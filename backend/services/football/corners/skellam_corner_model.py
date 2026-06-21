"""Sprint Corner-1 · Refinamiento — Modelo **Skellam** (distribución del
diferencial de córners derivada de dos Poisson independientes).

Idea:
  * Cada equipo tiene un *rate* de córners λ por partido.
  * home_corners ~ Poisson(λ_h),  away_corners ~ Poisson(λ_a).
  * corner_diff = home_corners - away_corners ~ Skellam(λ_h, λ_a).

Calibración:
  λ_h = exp(α0 + α1·home_corners_for_L15 + α2·away_corners_against_L15
            + α3·home_xg_for_L15 + α4·(away_deep_allowed_L15 / 100)
            + α5·home_implied_prob
            + α6·home_xg_for_L15 × (away_deep_allowed_L15 / 100))  # interacción

  λ_a = exp(β0 + β1·away_corners_for_L15 + β2·home_corners_against_L15
            + β3·away_xg_for_L15 + β4·(home_deep_allowed_L15 / 100)
            + β5·away_implied_prob
            + β6·away_xg_for_L15 × (home_deep_allowed_L15 / 100))

Distribución del diferencial:
  Calculamos P(corner_diff = k) por **convolución directa** de las dos
  Poisson — evita scipy/Bessel:

    P(diff = k) = Σ_{h=max(0,k)}^{K_max} P(home=h) · P(away=h-k)

  donde K_max ≈ 25 córners (cobertura > 99.99% para λ típicos).

Outputs principales:
  * predict_skellam_corner_diff(context, coefs_home, coefs_away) →
        {lambda_h, lambda_a, expected_corner_diff, P(diff=k)}.
  * skellam_to_asian_corners(distribution, lines, book_odds=None) →
        markets idénticos en formato a `build_asian_corner_markets`.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

K_MAX = 25  # córners máximos por equipo a considerar en la convolución

# Defaults: calibrados aproximadamente desde Fase 1.5 (corners promedio ≈ 5.2 por equipo
# y dominant favorite → +3.82 córners diff). Los caller pueden sobreescribir.
DEFAULT_LAMBDA_COEFS_HOME = {
    "intercept":          math.log(5.2),  # baseline 5.2 córners
    "corners_for_L15":    0.06,
    "corners_against_L15": 0.04,
    "xg_for_L15":         0.04,
    "deep_allowed_L15":   0.005,   # /100 ya aplicado
    "implied_prob":       0.50,
    "xg_deep_interaction": 0.01,
}
DEFAULT_LAMBDA_COEFS_AWAY = dict(DEFAULT_LAMBDA_COEFS_HOME)
# Caps razonables para λ (evita explosiones numéricas).
# Histórico: corners por equipo p99 ≈ 12, máx observado ≈ 20. Un λ Poisson
# > 14 implica una distribución con cola en 25+ córners, irrealista en ligas top.
# LAMBDA_MAX se mantiene en 18 como hard-cap; el guard defensivo dispara
# warning a partir de LAMBDA_WARNING_THRESHOLD para alertar antes de saturar.
LAMBDA_MIN = 1.0
LAMBDA_MAX = 18.0
LAMBDA_WARNING_THRESHOLD = 12.0   # > este valor → warning en reason_codes
DRIVER_DOMINANT_ABS = 2.0          # |aporte de un driver| > 2.0 al exponente z
COEF_LARGE_ABS = 2.0               # |coef|>2.0 (excl. intercept) → coef sospechoso


REASON_XG_DEEP_INTERACTION = "XG_DEEP_INTERACTION_USED"
REASON_LAMBDA_SATURATED    = "LAMBDA_SATURATED"
REASON_LAMBDA_HIGH         = "LAMBDA_HIGH_WARNING"
REASON_DRIVER_DOMINANT     = "DRIVER_DOMINANT"  # con sufijo "_FEATURENAME"
REASON_COEFS_SUSPICIOUS    = "SKELLAM_COEFS_SUSPICIOUS"


# ============================================================
# Lambda computation
# ============================================================

def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def _compute_lambda(
    *,
    corners_for_L15: Optional[float],
    corners_against_opp_L15: Optional[float],
    xg_for_L15: Optional[float],
    deep_allowed_opp_L15: Optional[float],
    implied_prob: Optional[float],
    coefs: dict[str, float],
    use_interaction: bool = False,
) -> tuple[float, dict[str, float], list[str]]:
    """Calcula λ via exp(linear combo). Maneja valores faltantes con
    fallback al intercept. Retorna (λ, drivers_used, warnings).

    use_interaction: si True, añade xG × (deep_allowed/100). Por defecto
    False para evitar multicolinealidad (los coefs in-sample no
    generalizan bien out-of-sample sin regularización fuerte).

    Guards defensivos:
      * Si |contribución de un driver| > DRIVER_DOMINANT_ABS al exponente z,
        se emite ``DRIVER_DOMINANT_<feature>`` en warnings.
      * Si λ pre-clamp > LAMBDA_MAX, se emite ``LAMBDA_SATURATED``.
      * Si λ pre-clamp en [LAMBDA_WARNING_THRESHOLD, LAMBDA_MAX], se emite
        ``LAMBDA_HIGH_WARNING``.
    Estos warnings nunca rompen el flujo: el caller decide qué hacer.
    """
    drivers: dict[str, float] = {}
    warnings: list[str] = []
    z = coefs["intercept"]
    drivers["intercept"] = round(coefs["intercept"], 4)

    def _add(name: str, contribution: float) -> None:
        nonlocal z
        z += contribution
        drivers[name] = round(contribution, 4)
        if abs(contribution) > DRIVER_DOMINANT_ABS:
            warnings.append(f"{REASON_DRIVER_DOMINANT}_{name.upper()}")

    if corners_for_L15 is not None:
        _add("corners_for_L15", coefs["corners_for_L15"] * float(corners_for_L15))
    if corners_against_opp_L15 is not None:
        _add("corners_against_opp_L15",
             coefs["corners_against_L15"] * float(corners_against_opp_L15))
    if xg_for_L15 is not None:
        _add("xg_for_L15", coefs["xg_for_L15"] * float(xg_for_L15))
    deep_scaled = None
    if deep_allowed_opp_L15 is not None:
        deep_scaled = float(deep_allowed_opp_L15) / 100.0
        _add("deep_allowed_opp_L15", coefs["deep_allowed_L15"] * deep_scaled)
    if implied_prob is not None:
        _add("implied_prob", coefs["implied_prob"] * float(implied_prob))
    # Interacción xG × deep_allowed/100 (solo si caller pidió explícitamente)
    if use_interaction and xg_for_L15 is not None and deep_scaled is not None:
        _add("xg_deep_interaction",
             coefs.get("xg_deep_interaction", 0.0)
             * float(xg_for_L15) * deep_scaled)

    # exp + clamp con detección de saturación
    try:
        lam = math.exp(z)
    except OverflowError:
        lam = LAMBDA_MAX + 1.0   # forzar saturated warning abajo

    if lam >= LAMBDA_MAX:
        warnings.append(REASON_LAMBDA_SATURATED)
    elif lam >= LAMBDA_WARNING_THRESHOLD:
        warnings.append(REASON_LAMBDA_HIGH)

    lam_clamped = _clamp(lam, LAMBDA_MIN, LAMBDA_MAX)
    return lam_clamped, drivers, warnings


def validate_skellam_coefs(
    coefs_home: dict[str, float],
    coefs_away: dict[str, float],
) -> list[str]:
    """Valida coeficientes Skellam y devuelve lista de warnings.

    Comprueba:
      * Coeficientes individuales con |β| > COEF_LARGE_ABS (excl. intercept).
      * Signos opuestos para la misma feature entre home y away (síntoma
        clásico de multicolinealidad mal regularizada).

    Esta función NO modifica nada — solo reporta. Caller decide si recalibra,
    aumenta ridge, o continúa con warning explícito en la respuesta.
    """
    issues: list[str] = []
    for k, v in coefs_home.items():
        if k == "intercept":
            continue
        if abs(v) > COEF_LARGE_ABS:
            issues.append(f"{REASON_COEFS_SUSPICIOUS}_HOME_{k.upper()}_ABS_{abs(v):.2f}")
    for k, v in coefs_away.items():
        if k == "intercept":
            continue
        if abs(v) > COEF_LARGE_ABS:
            issues.append(f"{REASON_COEFS_SUSPICIOUS}_AWAY_{k.upper()}_ABS_{abs(v):.2f}")
    # Signos opuestos no-triviales (ambos en magnitud > 0.05)
    common_keys = set(coefs_home) & set(coefs_away)
    for k in common_keys:
        if k == "intercept":
            continue
        vh, va = coefs_home[k], coefs_away[k]
        if (vh * va < 0) and min(abs(vh), abs(va)) > 0.05:
            issues.append(
                f"{REASON_COEFS_SUSPICIOUS}_OPPOSITE_SIGNS_{k.upper()}"
                f"(H={vh:+.3f},A={va:+.3f})"
            )
    return issues


def predict_skellam_corner_diff(
    context: dict[str, Any],
    *,
    coefs_home: Optional[dict[str, float]] = None,
    coefs_away: Optional[dict[str, float]] = None,
    use_interaction: bool = False,
) -> dict[str, Any]:
    """Calcula λ_h, λ_a, expected_corner_diff y la PMF completa del
    diferencial (corner_diff = home_corners - away_corners).

    use_interaction: incluir xG × deep_allowed/100. Por defecto False
    (la interacción tiene fuerte multicolinealidad con deep_allowed y los
    coefs in-sample no generalizan bien sin regularización fuerte).
    """
    if coefs_home is None:
        coefs_home = DEFAULT_LAMBDA_COEFS_HOME
    if coefs_away is None:
        coefs_away = DEFAULT_LAMBDA_COEFS_AWAY

    lam_h, drivers_h, warns_h = _compute_lambda(
        corners_for_L15        = _safe_float(context.get("home_corners_for_L15")),
        corners_against_opp_L15= _safe_float(context.get("away_corners_against_L15")),
        xg_for_L15             = _safe_float(context.get("home_xg_for_L15")),
        deep_allowed_opp_L15   = _safe_float(context.get("away_deep_allowed_L15")),
        implied_prob           = _safe_float(context.get("home_implied_prob")),
        coefs                  = coefs_home,
        use_interaction        = use_interaction,
    )
    lam_a, drivers_a, warns_a = _compute_lambda(
        corners_for_L15        = _safe_float(context.get("away_corners_for_L15")),
        corners_against_opp_L15= _safe_float(context.get("home_corners_against_L15")),
        xg_for_L15             = _safe_float(context.get("away_xg_for_L15")),
        deep_allowed_opp_L15   = _safe_float(context.get("home_deep_allowed_L15")),
        implied_prob           = _safe_float(context.get("away_implied_prob")),
        coefs                  = coefs_away,
        use_interaction        = use_interaction,
    )

    pmf_diff, pmf_h, pmf_a = _skellam_pmf_by_convolution(lam_h, lam_a)
    ed = float(np.sum(np.arange(-K_MAX, K_MAX + 1) * pmf_diff))

    reason_codes: list[str] = []
    if use_interaction:
        reason_codes.append(REASON_XG_DEEP_INTERACTION)
    # Warnings de saturación / drivers dominantes (prefijo HOME_/AWAY_)
    for w in warns_h:
        reason_codes.append(f"HOME_{w}")
    for w in warns_a:
        reason_codes.append(f"AWAY_{w}")
    # Validar coefs (multicolinealidad, magnitudes raras)
    coef_issues = validate_skellam_coefs(coefs_home, coefs_away)
    reason_codes.extend(coef_issues)

    return {
        "lambda_h":              round(lam_h, 4),
        "lambda_a":              round(lam_a, 4),
        "expected_corner_diff":  round(ed, 4),
        "expected_total_corners": round(lam_h + lam_a, 4),
        "diff_pmf":              {int(k): float(p) for k, p in
                                    zip(range(-K_MAX, K_MAX + 1), pmf_diff)},
        "home_pmf":              {int(k): float(p) for k, p in enumerate(pmf_h)},
        "away_pmf":              {int(k): float(p) for k, p in enumerate(pmf_a)},
        "drivers_home":          drivers_h,
        "drivers_away":          drivers_a,
        "reason_codes":          reason_codes,
    }


def _poisson_pmf(lam: float, k_max: int = K_MAX) -> np.ndarray:
    """PMF de Poisson(λ) para k=0..k_max."""
    pmf = np.zeros(k_max + 1)
    if lam <= 0:
        pmf[0] = 1.0
        return pmf
    # Iterativo para estabilidad numérica
    log_lam = math.log(lam)
    log_pmf = np.zeros(k_max + 1)
    log_pmf[0] = -lam  # P(0) = exp(-λ)
    log_fact = 0.0
    for k in range(1, k_max + 1):
        log_fact += math.log(k)
        log_pmf[k] = k * log_lam - lam - log_fact
    pmf = np.exp(log_pmf)
    s = pmf.sum()
    if s > 0:
        pmf /= s  # normalizar (recortamos en K_MAX)
    return pmf


def _skellam_pmf_by_convolution(lam_h: float, lam_a: float
                                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calcula la PMF de la diferencia Skellam por convolución directa.

    Retorna (pmf_diff, pmf_h, pmf_a) con pmf_diff indexada de -K_MAX a +K_MAX.
    """
    pmf_h = _poisson_pmf(lam_h)
    pmf_a = _poisson_pmf(lam_a)
    # P(diff = k) = Σ_{h: h-a=k, h,a >= 0} P(home=h) P(away=a)
    # Donde h ∈ [0, K_MAX] y a = h - k.
    pmf_diff = np.zeros(2 * K_MAX + 1)
    for h in range(K_MAX + 1):
        ph = pmf_h[h]
        if ph == 0:
            continue
        for a in range(K_MAX + 1):
            k = h - a
            idx = k + K_MAX  # k=-K_MAX → idx=0; k=0 → idx=K_MAX
            pmf_diff[idx] += ph * pmf_a[a]
    # No es necesario normalizar (pmf_h y pmf_a ya están normalizados, suma=1×1=1)
    return pmf_diff, pmf_h, pmf_a


# ============================================================
# Calibration (Poisson regression via simple iterative weighted OLS)
# ============================================================

def calibrate_skellam_lambdas(
    rows: list[dict],
    *,
    n_iter: int = 25,
    lr: float = 0.0,  # no usado (IRLS no necesita LR)
    use_interaction: bool = False,
    ridge_strength: float = 0.5,
) -> tuple[dict[str, float], dict[str, float]]:
    """Calibra los coeficientes λ_h y λ_a por IRLS.

    use_interaction: si True, incluye xG×deep_allowed/100 como feature en
    la matriz X. Por defecto False — la interacción genera fuerte
    multicolinealidad con `deep_allowed_L15` y los coefs in-sample no
    generalizan bien sin regularización fuerte.

    ridge_strength: penalización L2 sobre features estandarizadas (no
    aplica al intercepto). Por defecto 0.5 — suficiente para reducir
    multicolinealidad entre `corners_for_L15`, `xg_for_L15` y
    `implied_prob` (correlación natural por calidad del equipo) sin
    sesgar mucho la magnitud de los coeficientes. Subir a 1.0–2.0 si
    aparecen signos opuestos entre coefs home/away tras calibrar.
    """
    """Calibra los coeficientes λ_h y λ_a por descenso de gradiente sobre
    la *log-likelihood Poisson* (numpy puro). Cada equipo se aprende por
    separado: target home_corners ~ Poisson(λ_h(features_home)).

    Devuelve (coefs_home, coefs_away).
    """
    Xh, yh = [], []
    Xa, ya = [], []
    for r in rows:
        hc = r.get("home_corners")
        ac = r.get("away_corners")
        if hc is None or ac is None:
            continue
        # Features para λ_h
        cf = _safe_float(r.get("home_corners_for_L15"))
        ca = _safe_float(r.get("away_corners_against_L15"))
        xg = _safe_float(r.get("home_xg_for_L15"))
        da = _safe_float(r.get("away_deep_allowed_L15"))
        ip = _safe_float(r.get("home_implied_prob"))
        if None in (cf, ca, xg, da, ip):
            continue
        da_scaled = da / 100.0
        row_h = [1.0, cf, ca, xg, da_scaled, ip]
        if use_interaction:
            row_h.append(xg * da_scaled)
        Xh.append(row_h)
        yh.append(hc)
        # Features para λ_a
        cf2 = _safe_float(r.get("away_corners_for_L15"))
        ca2 = _safe_float(r.get("home_corners_against_L15"))
        xg2 = _safe_float(r.get("away_xg_for_L15"))
        da2 = _safe_float(r.get("home_deep_allowed_L15"))
        ip2 = _safe_float(r.get("away_implied_prob"))
        if None in (cf2, ca2, xg2, da2, ip2):
            continue
        da2_scaled = da2 / 100.0
        row_a = [1.0, cf2, ca2, xg2, da2_scaled, ip2]
        if use_interaction:
            row_a.append(xg2 * da2_scaled)
        Xa.append(row_a)
        ya.append(ac)

    if len(Xh) < 50 or len(Xa) < 50:
        return dict(DEFAULT_LAMBDA_COEFS_HOME), dict(DEFAULT_LAMBDA_COEFS_AWAY)

    coefs_h = _poisson_mle(np.array(Xh), np.array(yh, dtype=float),
                            n_iter, lr, ridge_strength=ridge_strength)
    coefs_a = _poisson_mle(np.array(Xa), np.array(ya, dtype=float),
                            n_iter, lr, ridge_strength=ridge_strength)

    base_keys = ("intercept", "corners_for_L15", "corners_against_L15",
                  "xg_for_L15", "deep_allowed_L15", "implied_prob")
    keys = base_keys + (("xg_deep_interaction",) if use_interaction else ())
    out_h = {k: float(v) for k, v in zip(keys, coefs_h)}
    out_a = {k: float(v) for k, v in zip(keys, coefs_a)}
    if not use_interaction:
        out_h["xg_deep_interaction"] = 0.0
        out_a["xg_deep_interaction"] = 0.0
    return out_h, out_a


def _poisson_mle(X: np.ndarray, y: np.ndarray, n_iter: int, lr: float,
                  *, ridge_strength: float = 0.5) -> np.ndarray:
    """Maximum likelihood Poisson regression via **IRLS** (Iteratively
    Reweighted Least Squares) sobre features ESTANDARIZADAS.

    Standardización (z-score) estabiliza IRLS cuando las features tienen
    escalas muy distintas (corners ~5, xG ~1.5, deep_scaled ~3, ip ~0.5).
    Los coeficientes finales se desnormalizan para que sean aplicables a
    los inputs crudos del predictor.

    ridge_strength: λ_ridge en la matriz XtWX + λI (excepto intercepto).
    Por defecto 0.5 — suficiente para domar multicolinealidad típica
    sin sesgar coeficientes en exceso.
    """
    n, p = X.shape
    # Standardize all features EXCEPT the intercept column (idx=0).
    mu_x = X.mean(axis=0)
    sd_x = X.std(axis=0)
    sd_x_safe = np.where(sd_x > 1e-9, sd_x, 1.0)
    Xs = X.copy()
    Xs[:, 1:] = (X[:, 1:] - mu_x[1:]) / sd_x_safe[1:]

    # init μ
    mu = np.maximum(y.astype(float) + 0.5, 0.5)
    beta_s = np.zeros(p)
    for _ in range(n_iter):
        eta = np.log(mu)
        z = eta + (y - mu) / mu
        W = mu
        WX = Xs * W[:, None]
        XtWX = Xs.T @ WX
        XtWz = Xs.T @ (W * z)
        # Ridge (sobre features estandarizadas, no sobre intercepto)
        ridge = ridge_strength * np.eye(p)
        ridge[0, 0] = 0.0
        XtWX_reg = XtWX + ridge
        try:
            beta_new = np.linalg.solve(XtWX_reg, XtWz)
        except np.linalg.LinAlgError:
            beta_new = np.linalg.pinv(XtWX_reg) @ XtWz
        eta_new = Xs @ beta_new
        eta_new = np.clip(eta_new, -5.0, 5.0)
        mu_new = np.exp(eta_new)
        if np.linalg.norm(beta_new - beta_s) < 1e-6:
            beta_s = beta_new
            mu = mu_new
            break
        beta_s = beta_new
        mu = mu_new

    # Convert standardized coefficients back to "raw" scale
    # If X_raw column j had mean μ_j and std σ_j, the standardized variable
    # was (X_raw - μ_j) / σ_j. So:
    #   η = β0_s + Σ β_j_s * (X_j - μ_j) / σ_j
    #     = (β0_s - Σ β_j_s * μ_j / σ_j) + Σ (β_j_s / σ_j) * X_j
    # → β0_raw = β0_s - Σ β_j_s * μ_j / σ_j
    # → β_j_raw = β_j_s / σ_j  (j > 0)
    beta_raw = np.zeros(p)
    beta_raw[0] = beta_s[0]
    for j in range(1, p):
        beta_raw[j] = beta_s[j] / sd_x_safe[j]
        beta_raw[0] -= beta_s[j] * mu_x[j] / sd_x_safe[j]
    return beta_raw


# ============================================================
# Skellam → Asian Corners markets
# ============================================================

ASIAN_LINES = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]

REASON_REAL_ODDS_NA   = "ASIAN_CORNERS_REAL_ODDS_NOT_AVAILABLE"
REASON_LOW_CONFIDENCE = "ASIAN_CORNERS_LOW_CONFIDENCE"
BET_EV_THRESHOLD = 0.03
WATCH_EV_LOW     = 0.0
MIN_CONFIDENCE_FOR_BET = 60.0


def skellam_to_asian_corners(
    skellam_result: dict[str, Any],
    *,
    book_odds: Optional[dict[str, float]] = None,
    real_odds_available: bool = False,
    confidence: float = 70.0,
) -> list[dict[str, Any]]:
    """Genera 14 mercados Asian Corners (HOME/AWAY × 7 líneas) desde
    la PMF Skellam. Para líneas .5 no hay push; para enteras sí.
    """
    pmf_diff: dict[int, float] = skellam_result["diff_pmf"]
    # Convertir keys str→int por si vienen serializadas
    pmf_diff = {int(k): float(v) for k, v in pmf_diff.items()}

    book_odds = book_odds or {}
    markets: list[dict[str, Any]] = []
    base_reasons = list(skellam_result.get("reason_codes", []))
    if not real_odds_available:
        base_reasons.append(REASON_REAL_ODDS_NA)

    for side in ("HOME", "AWAY"):
        for line in ASIAN_LINES:
            market_id = f"{side}_-{line}"
            reasons = list(base_reasons)

            # Definir win condition
            #   HOME -L : win si home_diff > L; push si home_diff == L y L es entero
            #   AWAY -L : win si home_diff < -L; push si home_diff == -L y L es entero
            if side == "HOME":
                p_win = sum(p for k, p in pmf_diff.items() if k > line)
                p_push = (sum(p for k, p in pmf_diff.items() if k == line)
                           if line == int(line) else 0.0)
            else:
                p_win = sum(p for k, p in pmf_diff.items() if k < -line)
                p_push = (sum(p for k, p in pmf_diff.items() if k == -line)
                           if line == int(line) else 0.0)
            p_lose = max(0.0, 1.0 - p_win - p_push)

            fair_odds = None
            if p_win > 1e-6:
                if p_push > 0:
                    fair_odds = (1.0 - p_push) / p_win
                else:
                    fair_odds = 1.0 / p_win

            book_price = book_odds.get(market_id)
            ev = None
            if book_price is not None and book_price > 1.0:
                ev = round(p_win * (book_price - 1.0) - p_lose, 4)

            # Recommendation
            recommendation = "NO_BET"
            if book_price is None:
                recommendation = "WATCH" if real_odds_available else "NO_BET"
            elif confidence < MIN_CONFIDENCE_FOR_BET:
                recommendation = "NO_BET"
                reasons.append(REASON_LOW_CONFIDENCE)
            elif ev is not None and ev >= BET_EV_THRESHOLD:
                recommendation = "BET"
            elif ev is not None and ev > WATCH_EV_LOW:
                recommendation = "WATCH"

            # Dedupe reasons
            seen = set()
            rc_clean = []
            for rc in reasons:
                if rc not in seen:
                    seen.add(rc)
                    rc_clean.append(rc)

            markets.append({
                "market":         f"{side}_CORNERS_-{line}",
                "side":           side,
                "line":           line,
                "prob_win":       round(p_win, 4),
                "prob_push":      round(p_push, 4),
                "prob_lose":      round(p_lose, 4),
                "fair_odds":      round(fair_odds, 4) if fair_odds is not None else None,
                "book_odds":      book_price,
                "ev":             ev,
                "recommendation": recommendation,
                "confidence":     round(confidence, 2),
                "reason_codes":   rc_clean,
            })

    return markets


# ============================================================
# Most Corners derived from Skellam
# ============================================================

def skellam_most_corners(skellam_result: dict[str, Any]) -> dict[str, Any]:
    """Deriva probabilidades Most Corners (home / away / tie) desde la
    PMF Skellam directamente."""
    pmf = {int(k): float(v) for k, v in skellam_result["diff_pmf"].items()}
    p_home = sum(p for k, p in pmf.items() if k > 0)
    p_away = sum(p for k, p in pmf.items() if k < 0)
    p_tie  = pmf.get(0, 0.0)
    s = p_home + p_away + p_tie
    if s > 0:
        p_home /= s
        p_away /= s
        p_tie  /= s
    return {
        "home_most_corners_prob": round(p_home, 4),
        "away_most_corners_prob": round(p_away, 4),
        "tie_corners_prob":       round(p_tie, 4),
        "expected_corner_diff":   round(skellam_result["expected_corner_diff"], 4),
    }
