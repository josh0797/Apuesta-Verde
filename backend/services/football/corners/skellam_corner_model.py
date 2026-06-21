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
# Caps razonables para λ (evita explosiones numéricas)
LAMBDA_MIN = 1.0
LAMBDA_MAX = 18.0


REASON_XG_DEEP_INTERACTION = "XG_DEEP_INTERACTION_USED"


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
) -> tuple[float, dict[str, float]]:
    """Calcula λ via exp(linear combo). Maneja valores faltantes con
    fallback al intercept. Retorna (λ, drivers_used)."""
    drivers = {}
    z = coefs["intercept"]
    drivers["intercept"] = coefs["intercept"]

    if corners_for_L15 is not None:
        c = coefs["corners_for_L15"] * float(corners_for_L15)
        z += c
        drivers["corners_for_L15"] = round(c, 4)
    if corners_against_opp_L15 is not None:
        c = coefs["corners_against_L15"] * float(corners_against_opp_L15)
        z += c
        drivers["corners_against_opp_L15"] = round(c, 4)
    if xg_for_L15 is not None:
        c = coefs["xg_for_L15"] * float(xg_for_L15)
        z += c
        drivers["xg_for_L15"] = round(c, 4)
    deep_scaled = None
    if deep_allowed_opp_L15 is not None:
        deep_scaled = float(deep_allowed_opp_L15) / 100.0
        c = coefs["deep_allowed_L15"] * deep_scaled
        z += c
        drivers["deep_allowed_opp_L15"] = round(c, 4)
    if implied_prob is not None:
        c = coefs["implied_prob"] * float(implied_prob)
        z += c
        drivers["implied_prob"] = round(c, 4)
    # Interacción xG × deep_allowed/100 (solo si ambas disponibles)
    if xg_for_L15 is not None and deep_scaled is not None:
        c = coefs["xg_deep_interaction"] * float(xg_for_L15) * deep_scaled
        z += c
        drivers["xg_deep_interaction"] = round(c, 4)

    # exp + clamp
    try:
        lam = math.exp(z)
    except OverflowError:
        lam = LAMBDA_MAX
    return _clamp(lam, LAMBDA_MIN, LAMBDA_MAX), drivers


def predict_skellam_corner_diff(
    context: dict[str, Any],
    *,
    coefs_home: Optional[dict[str, float]] = None,
    coefs_away: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Calcula λ_h, λ_a, expected_corner_diff y la PMF completa del
    diferencial (corner_diff = home_corners - away_corners)."""
    if coefs_home is None:
        coefs_home = DEFAULT_LAMBDA_COEFS_HOME
    if coefs_away is None:
        coefs_away = DEFAULT_LAMBDA_COEFS_AWAY

    lam_h, drivers_h = _compute_lambda(
        corners_for_L15        = _safe_float(context.get("home_corners_for_L15")),
        corners_against_opp_L15= _safe_float(context.get("away_corners_against_L15")),
        xg_for_L15             = _safe_float(context.get("home_xg_for_L15")),
        deep_allowed_opp_L15   = _safe_float(context.get("away_deep_allowed_L15")),
        implied_prob           = _safe_float(context.get("home_implied_prob")),
        coefs                  = coefs_home,
    )
    lam_a, drivers_a = _compute_lambda(
        corners_for_L15        = _safe_float(context.get("away_corners_for_L15")),
        corners_against_opp_L15= _safe_float(context.get("home_corners_against_L15")),
        xg_for_L15             = _safe_float(context.get("away_xg_for_L15")),
        deep_allowed_opp_L15   = _safe_float(context.get("home_deep_allowed_L15")),
        implied_prob           = _safe_float(context.get("away_implied_prob")),
        coefs                  = coefs_away,
    )

    pmf_diff, pmf_h, pmf_a = _skellam_pmf_by_convolution(lam_h, lam_a)
    ed = float(np.sum(np.arange(-K_MAX, K_MAX + 1) * pmf_diff))

    reason_codes: list[str] = []
    if "xg_deep_interaction" in drivers_h or "xg_deep_interaction" in drivers_a:
        reason_codes.append(REASON_XG_DEEP_INTERACTION)

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
) -> tuple[dict[str, float], dict[str, float]]:
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
        Xh.append([1.0, cf, ca, xg, da_scaled, ip, xg * da_scaled])
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
        Xa.append([1.0, cf2, ca2, xg2, da2_scaled, ip2, xg2 * da2_scaled])
        ya.append(ac)

    if len(Xh) < 50 or len(Xa) < 50:
        return dict(DEFAULT_LAMBDA_COEFS_HOME), dict(DEFAULT_LAMBDA_COEFS_AWAY)

    coefs_h = _poisson_mle(np.array(Xh), np.array(yh, dtype=float), n_iter, lr)
    coefs_a = _poisson_mle(np.array(Xa), np.array(ya, dtype=float), n_iter, lr)

    keys = ("intercept", "corners_for_L15", "corners_against_L15",
            "xg_for_L15", "deep_allowed_L15", "implied_prob",
            "xg_deep_interaction")
    return (
        {k: float(v) for k, v in zip(keys, coefs_h)},
        {k: float(v) for k, v in zip(keys, coefs_a)},
    )


def _poisson_mle(X: np.ndarray, y: np.ndarray, n_iter: int, lr: float) -> np.ndarray:
    """Maximum likelihood Poisson regression via **IRLS** (Iteratively
    Reweighted Least Squares) sobre features ESTANDARIZADAS.

    Standardización (z-score) estabiliza IRLS cuando las features tienen
    escalas muy distintas (corners ~5, xG ~1.5, deep_scaled ~3, ip ~0.5).
    Los coeficientes finales se desnormalizan para que sean aplicables a
    los inputs crudos del predictor.
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
        # Add small ridge for numerical stability (1e-4 on diagonal except intercept)
        ridge = 1e-4 * np.eye(p)
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
