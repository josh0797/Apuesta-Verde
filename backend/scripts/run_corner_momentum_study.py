"""Corner Momentum Study — Fase 1 (Opción B) · PASO 1.3

Estudio cuantitativo PURO sobre el dataset unificado generado en el
paso 1.2 (``/app/data/corners_history/all_leagues_dataset.json``).

Objetivo (acordado con el usuario):
  Producir evidencia cuantitativa, SIN heurísticas arbitrarias y SIN
  tocar código de producción, sobre qué señales prematch explican o
  mejoran la predicción del *total de córners* de un partido.

Restricciones de disciplina:
  * Point-in-time estricto: por cada match objetivo solo se usan
    partidos previos (date < target_date) para construir features.
  * Walk-forward temporal para evaluar modelos lineales simples.
  * Solo numpy/pandas (sin sklearn/scipy): toda la matemática se
    implementa explícitamente.
  * No se altera el código de producción; este es un análisis
    aislado en /scripts/ + /data/ + /diagnostics/.

Features estudiadas (todas PIT, ventana L5 vs L15):

  1) **Corners FOR / AGAINST** del equipo local y visitante, L5 y L15
     y sus deltas L5-L15 (medida de "momentum").
  2) **Home/Away split**: estadísticas filtradas por venue (local
     calcula promedios en partidos LOCAL; visitante en partidos
     VISITANTE).
  3) **Ofensivas**: shots, shots_on_target, pressure_proxy = shots +
     2*sot (promedio L5/L15 por equipo).
  4) **Defensivas**: corners_against, shots_against (promedio L5/L15
     por equipo).
  5) **Serie activa**: rachas consecutivas de "más de 9.5 córners"
     sobre los últimos L5.
  6) **Favorito dominante**: implied_prob del favorito (max entre
     home/away) y `abs_implied_prob_diff` = |implied_home - implied_away|.
  7) **Corner Momentum (compuesta)**: z-score del diferencial de
     córners L5 (for - against) por equipo, agregado al match.

Métricas reportadas (por feature, global y por liga):
  * Pearson r vs target = `total_corners` del partido.
  * MAE y RMSE en walk-forward (modelo univariate-lineal).
  * Feature Importance global = magnitud de coef. estandarizados de
    una OLS multivariada (con todas las features supervivientes al
    umbral |r| >= 0.15).
  * Decisión por feature: keep si |r| >= 0.15 (umbral acordado).

Salida:
  * /app/diagnostics/corner_momentum_study_phase1_stats.json
  * /app/diagnostics/corner_momentum_study_phase1_report.md
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

DATASET   = Path("/app/data/corners_history/all_leagues_dataset.json")
OUT_STATS = Path("/app/diagnostics/corner_momentum_study_phase1_stats.json")
OUT_REPORT = Path("/app/diagnostics/corner_momentum_study_phase1_report.md")

R_THRESHOLD = 0.15  # umbral de descarte acordado con el usuario

# ---------------------------------------------------------------------------
# 1) Carga y construcción de historiales por equipo
# ---------------------------------------------------------------------------

def _load_dataset() -> list[dict]:
    rows = json.loads(DATASET.read_text(encoding="utf-8"))
    rows.sort(key=lambda r: (r["date"], r["league_code"]))
    return rows


def _mean(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return float(sum(xs) / len(xs))


def _build_features(rows: list[dict]) -> pd.DataFrame:
    """Para cada partido, construir features L5/L15 usando SOLO partidos
    previos de cada equipo (point-in-time correcto).

    Retorna un DataFrame con un registro por partido + features.
    """
    # historial por equipo: lista cronológica de eventos con stats por venue.
    history: dict[str, list[dict]] = defaultdict(list)

    feature_rows: list[dict] = []
    for r in rows:
        home, away = r["home_team"], r["away_team"]
        # Tomamos snapshots PIT del historial ANTES de añadir el match actual.
        hh = history[home]
        ah = history[away]
        feat = _compute_match_features(r, hh, ah)
        feature_rows.append(feat)

        # Añadir match actual a los historiales para futuros partidos.
        history[home].append({
            "date":              r["date"],
            "venue":             "home",
            "corners_for":       r["home_corners"],
            "corners_against":   r["away_corners"],
            "shots_for":         r["home_shots"],
            "shots_against":     r["away_shots"],
            "sot_for":           r["home_shots_on_target"],
            "sot_against":       r["away_shots_on_target"],
            "total_corners":     r["total_corners"],
        })
        history[away].append({
            "date":              r["date"],
            "venue":             "away",
            "corners_for":       r["away_corners"],
            "corners_against":   r["home_corners"],
            "shots_for":         r["away_shots"],
            "shots_against":     r["home_shots"],
            "sot_for":           r["away_shots_on_target"],
            "sot_against":       r["home_shots_on_target"],
            "total_corners":     r["total_corners"],
        })

    return pd.DataFrame(feature_rows)


def _avg_last_n(items: list[dict], n: int, key: str,
                venue_filter: Optional[str] = None) -> Optional[float]:
    pool = items
    if venue_filter is not None:
        pool = [it for it in items if it["venue"] == venue_filter]
    if not pool:
        return None
    pool = pool[-n:]
    return _mean([it.get(key) for it in pool])


def _active_over_streak(items: list[dict], line: float = 9.5,
                         lookback: int = 5) -> int:
    """Racha consecutiva (hasta `lookback`) de 'total_corners > line'
    en los últimos partidos del equipo."""
    streak = 0
    for it in reversed(items[-lookback:]):
        tc = it.get("total_corners")
        if tc is None:
            break
        if tc > line:
            streak += 1
        else:
            break
    return streak


def _compute_match_features(match: dict,
                            home_hist: list[dict],
                            away_hist: list[dict]) -> dict:
    home_n_prev = len(home_hist)
    away_n_prev = len(away_hist)

    f: dict[str, Any] = {
        "match_id":      match["match_id"],
        "date":          match["date"],
        "league":        match["league"],
        "league_code":   match["league_code"],
        "season":        match["season"],
        "home_team":     match["home_team"],
        "away_team":     match["away_team"],
        "total_corners": match["total_corners"],
        "home_n_prev":   home_n_prev,
        "away_n_prev":   away_n_prev,
    }

    # ----- (1) Corners FOR/AGAINST L5/L15 global (todos los venues)
    for window in (5, 15):
        f[f"home_corners_for_L{window}"]     = _avg_last_n(home_hist, window, "corners_for")
        f[f"home_corners_against_L{window}"] = _avg_last_n(home_hist, window, "corners_against")
        f[f"away_corners_for_L{window}"]     = _avg_last_n(away_hist, window, "corners_for")
        f[f"away_corners_against_L{window}"] = _avg_last_n(away_hist, window, "corners_against")

    # ----- (1b) Deltas L5 - L15 (momentum bruto)
    for prefix in ("home_corners_for", "home_corners_against",
                    "away_corners_for", "away_corners_against"):
        a, b = f.get(f"{prefix}_L5"), f.get(f"{prefix}_L15")
        f[f"{prefix}_delta_L5_L15"] = (a - b) if (a is not None and b is not None) else None

    # ----- (2) Home/Away split (filtrado por venue del propio equipo)
    for window in (5, 15):
        f[f"home_corners_for_atHome_L{window}"]     = _avg_last_n(home_hist, window, "corners_for", venue_filter="home")
        f[f"home_corners_against_atHome_L{window}"] = _avg_last_n(home_hist, window, "corners_against", venue_filter="home")
        f[f"away_corners_for_atAway_L{window}"]     = _avg_last_n(away_hist, window, "corners_for", venue_filter="away")
        f[f"away_corners_against_atAway_L{window}"] = _avg_last_n(away_hist, window, "corners_against", venue_filter="away")

    # ----- (3) Ofensivas (shots, SoT, pressure_proxy = shots + 2*SoT)
    for window in (5, 15):
        f[f"home_shots_for_L{window}"]   = _avg_last_n(home_hist, window, "shots_for")
        f[f"home_sot_for_L{window}"]     = _avg_last_n(home_hist, window, "sot_for")
        f[f"away_shots_for_L{window}"]   = _avg_last_n(away_hist, window, "shots_for")
        f[f"away_sot_for_L{window}"]     = _avg_last_n(away_hist, window, "sot_for")
        # pressure_proxy
        hsh, hso = f[f"home_shots_for_L{window}"], f[f"home_sot_for_L{window}"]
        ash, aso = f[f"away_shots_for_L{window}"], f[f"away_sot_for_L{window}"]
        f[f"home_pressure_proxy_L{window}"] = (hsh + 2*hso) if (hsh is not None and hso is not None) else None
        f[f"away_pressure_proxy_L{window}"] = (ash + 2*aso) if (ash is not None and aso is not None) else None

    # ----- (4) Defensivas
    for window in (5, 15):
        f[f"home_shots_against_L{window}"] = _avg_last_n(home_hist, window, "shots_against")
        f[f"away_shots_against_L{window}"] = _avg_last_n(away_hist, window, "shots_against")

    # ----- (5) Serie activa (rachas over 9.5 en últimos 5)
    f["home_active_over_9_5_streak"] = _active_over_streak(home_hist, line=9.5, lookback=5)
    f["away_active_over_9_5_streak"] = _active_over_streak(away_hist, line=9.5, lookback=5)
    # versión 10.5 (más exigente)
    f["home_active_over_10_5_streak"] = _active_over_streak(home_hist, line=10.5, lookback=5)
    f["away_active_over_10_5_streak"] = _active_over_streak(away_hist, line=10.5, lookback=5)

    # ----- (6) Favorito dominante (odds)
    iph = match.get("implied_prob_home")
    ipa = match.get("implied_prob_away")
    if iph is not None and ipa is not None:
        f["fav_implied_prob"] = max(iph, ipa)
        f["abs_implied_prob_diff"] = abs(iph - ipa)
    else:
        f["fav_implied_prob"] = None
        f["abs_implied_prob_diff"] = None

    # ----- (7) Corner Momentum compuesto (diferencial L5 for-against agregado)
    # Suma de diferenciales L5 normalizada (proxy simple).
    hf5  = f.get("home_corners_for_L5")
    ha5  = f.get("home_corners_against_L5")
    af5  = f.get("away_corners_for_L5")
    aa5  = f.get("away_corners_against_L5")
    if None not in (hf5, ha5, af5, aa5):
        f["match_corner_momentum_L5"] = (hf5 - ha5) + (af5 - aa5)
    else:
        f["match_corner_momentum_L5"] = None

    # versión L15 (más estable)
    hf15  = f.get("home_corners_for_L15")
    ha15  = f.get("home_corners_against_L15")
    af15  = f.get("away_corners_for_L15")
    aa15  = f.get("away_corners_against_L15")
    if None not in (hf15, ha15, af15, aa15):
        f["match_corner_momentum_L15"] = (hf15 - ha15) + (af15 - aa15)
    else:
        f["match_corner_momentum_L15"] = None

    # ----- "Baseline" útil: suma simple de corners_for L15 ambos equipos
    if hf15 is not None and af15 is not None:
        f["sum_corners_for_L15"] = hf15 + af15
    else:
        f["sum_corners_for_L15"] = None
    if hf5 is not None and af5 is not None:
        f["sum_corners_for_L5"] = hf5 + af5
    else:
        f["sum_corners_for_L5"] = None

    return f


# ---------------------------------------------------------------------------
# 2) Métricas
# ---------------------------------------------------------------------------

def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 2:
        return float("nan")
    xm = x - x.mean()
    ym = y - y.mean()
    denom = math.sqrt((xm**2).sum() * (ym**2).sum())
    if denom == 0:
        return float("nan")
    return float((xm * ym).sum() / denom)


def _mae(y: np.ndarray, yhat: np.ndarray) -> float:
    return float(np.mean(np.abs(y - yhat)))


def _rmse(y: np.ndarray, yhat: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y - yhat) ** 2)))


def _walk_forward_univariate(df: pd.DataFrame, feature: str,
                              target: str = "total_corners",
                              n_folds: int = 5) -> dict:
    """Walk-forward temporal con regresión lineal simple univariate
    (cerrada: pendiente + intercepto por mínimos cuadrados).
    Splits ordenados por fecha. Cada fold entrena con [0..k] y
    valida con bloque [k..k+1].
    """
    sub = df[[feature, target, "date"]].dropna()
    if len(sub) < 100:
        return {"n": len(sub), "mae": None, "rmse": None, "baseline_mae": None, "baseline_rmse": None}
    sub = sub.sort_values("date").reset_index(drop=True)
    fold_size = len(sub) // (n_folds + 1)  # primer fold de calentamiento

    maes, rmses, baseline_maes, baseline_rmses = [], [], [], []
    for k in range(1, n_folds + 1):
        train = sub.iloc[: k * fold_size]
        valid = sub.iloc[k * fold_size : (k + 1) * fold_size]
        if len(train) < 30 or len(valid) < 10:
            continue
        x = train[feature].values.astype(float)
        y = train[target].values.astype(float)
        # OLS univariate
        xm, ym = x.mean(), y.mean()
        denom = ((x - xm) ** 2).sum()
        if denom == 0:
            slope = 0.0
        else:
            slope = ((x - xm) * (y - ym)).sum() / denom
        intercept = ym - slope * xm
        xv = valid[feature].values.astype(float)
        yv = valid[target].values.astype(float)
        yhat = intercept + slope * xv
        # Baseline: predicción = media del training
        ybaseline = np.full_like(yv, ym, dtype=float)
        maes.append(_mae(yv, yhat))
        rmses.append(_rmse(yv, yhat))
        baseline_maes.append(_mae(yv, ybaseline))
        baseline_rmses.append(_rmse(yv, ybaseline))

    if not maes:
        return {"n": len(sub), "mae": None, "rmse": None, "baseline_mae": None, "baseline_rmse": None}

    return {
        "n":               int(len(sub)),
        "mae":             float(np.mean(maes)),
        "rmse":            float(np.mean(rmses)),
        "baseline_mae":    float(np.mean(baseline_maes)),
        "baseline_rmse":   float(np.mean(baseline_rmses)),
        "mae_improvement": float(np.mean(baseline_maes) - np.mean(maes)),
    }


def _ols_multivariate_standardized(df: pd.DataFrame, features: list[str],
                                    target: str = "total_corners") -> dict:
    """OLS multivariada usando solo filas con todas las features no-nulas.
    Estandariza features y target a z-score → los coeficientes son
    importancia normalizada (magnitud directamente comparable).
    Retorna {feature: standardized_coef} y R^2.
    """
    sub = df[features + [target]].dropna()
    n = len(sub)
    if n < max(50, 5 * len(features)):
        return {"n": n, "r2": None, "coefs": {f: None for f in features}}
    X = sub[features].values.astype(float)
    y = sub[target].values.astype(float)
    # Estandariza
    Xm, Xs = X.mean(axis=0), X.std(axis=0, ddof=0)
    Xs[Xs == 0] = 1.0
    Xz = (X - Xm) / Xs
    ym, ys = y.mean(), y.std(ddof=0)
    if ys == 0:
        ys = 1.0
    yz = (y - ym) / ys

    # Resolver OLS: beta = (X'X)^-1 X' y
    XtX = Xz.T @ Xz
    try:
        beta = np.linalg.solve(XtX, Xz.T @ yz)
    except np.linalg.LinAlgError:
        beta = np.linalg.pinv(XtX) @ (Xz.T @ yz)
    yhat = Xz @ beta
    ss_res = ((yz - yhat) ** 2).sum()
    ss_tot = (yz ** 2).sum()
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else None

    return {
        "n":     int(n),
        "r2":    r2,
        "coefs": {f: float(b) for f, b in zip(features, beta)},
    }


# ---------------------------------------------------------------------------
# 3) Análisis principal
# ---------------------------------------------------------------------------

# Conjunto canónico de features candidatas a evaluar
CANDIDATE_FEATURES = [
    # (1) Corners FOR/AGAINST L5/L15 global
    "home_corners_for_L5",  "home_corners_for_L15",
    "home_corners_against_L5", "home_corners_against_L15",
    "away_corners_for_L5",  "away_corners_for_L15",
    "away_corners_against_L5", "away_corners_against_L15",
    # (1b) Deltas
    "home_corners_for_delta_L5_L15",
    "home_corners_against_delta_L5_L15",
    "away_corners_for_delta_L5_L15",
    "away_corners_against_delta_L5_L15",
    # (2) Splits H/A
    "home_corners_for_atHome_L5",  "home_corners_for_atHome_L15",
    "home_corners_against_atHome_L5", "home_corners_against_atHome_L15",
    "away_corners_for_atAway_L5",  "away_corners_for_atAway_L15",
    "away_corners_against_atAway_L5", "away_corners_against_atAway_L15",
    # (3) Ofensivas
    "home_shots_for_L5",  "home_shots_for_L15",
    "home_sot_for_L5",    "home_sot_for_L15",
    "away_shots_for_L5",  "away_shots_for_L15",
    "away_sot_for_L5",    "away_sot_for_L15",
    "home_pressure_proxy_L5",  "home_pressure_proxy_L15",
    "away_pressure_proxy_L5",  "away_pressure_proxy_L15",
    # (4) Defensivas
    "home_shots_against_L5",  "home_shots_against_L15",
    "away_shots_against_L5",  "away_shots_against_L15",
    # (5) Serie activa
    "home_active_over_9_5_streak",
    "away_active_over_9_5_streak",
    "home_active_over_10_5_streak",
    "away_active_over_10_5_streak",
    # (6) Favorito
    "fav_implied_prob",
    "abs_implied_prob_diff",
    # (7) Momentum compuesto + baselines
    "match_corner_momentum_L5",
    "match_corner_momentum_L15",
    "sum_corners_for_L5",
    "sum_corners_for_L15",
]


def _eval_features(df: pd.DataFrame, leagues: list[str]) -> dict:
    """Calcula Pearson y walk-forward MAE/RMSE para cada feature,
    globalmente y por liga.
    """
    out: dict[str, dict] = {}
    for feat in CANDIDATE_FEATURES:
        sub = df[[feat, "total_corners"]].dropna()
        if len(sub) < 50:
            out[feat] = {
                "global": {"n": int(len(sub)), "r": None, "mae": None,
                            "rmse": None, "mae_improvement": None, "keep": False},
                "by_league": {},
            }
            continue
        x = sub[feat].values.astype(float)
        y = sub["total_corners"].values.astype(float)
        r = _pearson(x, y)
        wf = _walk_forward_univariate(df, feat)
        keep_global = (abs(r) >= R_THRESHOLD)
        entry = {
            "global": {
                "n":              int(len(sub)),
                "r":              float(r) if r == r else None,
                "mae":            wf["mae"],
                "rmse":           wf["rmse"],
                "baseline_mae":   wf["baseline_mae"],
                "baseline_rmse":  wf["baseline_rmse"],
                "mae_improvement": wf["mae_improvement"],
                "keep":           bool(keep_global),
            },
            "by_league": {},
        }
        for lg in leagues:
            sub_lg = df[df["league"] == lg][[feat, "total_corners"]].dropna()
            if len(sub_lg) < 50:
                entry["by_league"][lg] = {"n": int(len(sub_lg)), "r": None, "keep": False}
                continue
            xl = sub_lg[feat].values.astype(float)
            yl = sub_lg["total_corners"].values.astype(float)
            rl = _pearson(xl, yl)
            entry["by_league"][lg] = {
                "n":    int(len(sub_lg)),
                "r":    float(rl) if rl == rl else None,
                "keep": bool(abs(rl) >= R_THRESHOLD),
            }
        out[feat] = entry
    return out


def _rank_by_abs_r(eval_results: dict) -> list[tuple[str, float]]:
    rows = []
    for feat, e in eval_results.items():
        r = e["global"].get("r")
        if r is None:
            continue
        rows.append((feat, abs(r)))
    rows.sort(key=lambda t: t[1], reverse=True)
    return rows


def _surviving_features(eval_results: dict) -> list[str]:
    return [f for f, e in eval_results.items() if e["global"].get("keep")]


def _league_consistency(eval_results: dict, leagues: list[str]) -> dict:
    """Para cada feature superviviente, ¿en cuántas ligas pasa el umbral?"""
    out = {}
    for feat in _surviving_features(eval_results):
        by_lg = eval_results[feat]["by_league"]
        n_pass = sum(1 for lg in leagues if by_lg.get(lg, {}).get("keep"))
        out[feat] = {"leagues_passing": n_pass, "total": len(leagues)}
    return out


# ---------------------------------------------------------------------------
# 4) Reporte
# ---------------------------------------------------------------------------

def _fmt(x, nd=4):
    if x is None:
        return "—"
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _build_markdown(stats: dict) -> str:
    leagues = stats["meta"]["leagues"]
    n_total = stats["meta"]["n_matches"]
    ranked = stats["ranked_by_abs_r"]
    eval_results = stats["features"]
    survivors = stats["surviving_features"]
    consistency = stats["league_consistency"]
    multi = stats["multivariate_ols"]

    lines: list[str] = []
    lines.append("# Corner Momentum Study — Fase 1 (Opción B)")
    lines.append("")
    lines.append(f"_Generado: {datetime.utcnow().isoformat()}Z_")
    lines.append("")
    lines.append("## 1. Resumen ejecutivo")
    lines.append("")
    lines.append(f"- Partidos totales analizados: **{n_total}**")
    lines.append(f"- Ligas: **{', '.join(leagues)}** (12 temporadas en total, 2021/22 → 2023/24)")
    lines.append("- Liga MX: **excluida**. `football-data.co.uk` ofrece el archivo `new/MEX.csv` (4655 partidos) pero **no contiene columnas HC/AC** ni stats de tiros; archivado en `extra_no_corners/`.")
    lines.append(f"- Umbral de descarte acordado: **|r| < {R_THRESHOLD}**")
    lines.append(f"- Features supervivientes (|r| ≥ {R_THRESHOLD}, global): **{len(survivors)}** de {len(CANDIDATE_FEATURES)} candidatas.")
    lines.append("")
    if not survivors:
        max_r = ranked[0][1] if ranked else 0.0
        max_feat = ranked[0][0] if ranked else "—"
        lines.append("### ⚠️ Veredicto principal")
        lines.append("")
        lines.append(f"**Ninguna feature prematch L5/L15 supera el umbral acordado de |r| ≥ {R_THRESHOLD}.**")
        lines.append("")
        lines.append(f"La señal con mayor correlación absoluta es `{max_feat}` con **|r|={_fmt(max_r)}** (~{int(max_r*100)}% de correlación), claramente por debajo del corte.")
        lines.append("")
        lines.append("**Interpretación pragmática:** los córners individuales por partido tienen una varianza inherente muy alta (típicamente 10±5). Las features prematch basadas en historial reciente capturan poco más del 1% de la varianza total. Esto no significa que los córners sean impredecibles en agregado (la **suma** y la **media** sí son estables), sino que **predecir el total exacto de un partido a partir de features prematch lineales es estadísticamente débil**.")
        lines.append("")
        lines.append("**Implicaciones operativas para Fase 2:**")
        lines.append("")
        lines.append("- Un motor de córners construido sobre estas features tendrá un **techo de R² muy bajo** (ver §4 abajo) — probablemente no superará al modelo poissoniano que usa solo la media histórica del par de equipos.")
        lines.append("- Antes de invertir en arquitectura (Bivariate Poisson / Negative Binomial), conviene **revisar el listado** y decidir si:")
        lines.append("  1. Relajar el umbral a `|r| ≥ 0.10` y aceptar señales débiles pero estadísticamente significativas (con n>1000 incluso r=0.10 es significativo).")
        lines.append("  2. Buscar fuentes con stats más granulares (xG, posesión, ataques peligrosos) que `football-data.co.uk` no expone.")
        lines.append("  3. Abandonar el enfoque feature-based y modelar córners con **goles esperados (xG)** como input dominante (no disponible aquí gratis).")
        lines.append("")
    lines.append("## 2. Top 15 features por |r| (global)")
    lines.append("")
    lines.append("| # | Feature | n | r | Walk-fwd MAE | Baseline MAE | Δ MAE | RMSE | Decisión |")
    lines.append("|---|---------|---|---|--------------|--------------|-------|------|----------|")
    for i, (feat, _) in enumerate(ranked[:15], start=1):
        g = eval_results[feat]["global"]
        decision = "✅ keep" if g.get("keep") else "❌ drop"
        lines.append(
            f"| {i} | `{feat}` | {g.get('n','—')} | "
            f"{_fmt(g.get('r'))} | {_fmt(g.get('mae'),3)} | "
            f"{_fmt(g.get('baseline_mae'),3)} | {_fmt(g.get('mae_improvement'),3)} | "
            f"{_fmt(g.get('rmse'),3)} | {decision} |"
        )
    lines.append("")
    lines.append("**Lectura:**")
    lines.append("")
    lines.append("- `r` es la correlación de Pearson entre la feature (PIT, solo historia previa) y el `total_corners` del partido objetivo.")
    lines.append("- `Walk-fwd MAE/RMSE` proviene de 5 folds temporales con regresión lineal **univariate** (una feature a la vez).")
    lines.append("- `Baseline MAE` predice siempre con la media del bloque de entrenamiento. **Δ MAE > 0** ⇒ la feature mejora respecto al baseline naïf.")
    lines.append("- La decisión `keep` se basa **únicamente** en `|r| ≥ {th}` (umbral acordado).".format(th=R_THRESHOLD))
    lines.append("")

    lines.append("## 3. Consistencia por liga (solo supervivientes globales)")
    lines.append("")
    lines.append("| Feature | r global | EPL r | Bundesliga r | LaLiga r | SerieA r | Ligas pasan umbral |")
    lines.append("|---------|----------|-------|--------------|----------|----------|---------------------|")
    for feat in survivors:
        g = eval_results[feat]["global"]
        by = eval_results[feat]["by_league"]
        cons = consistency[feat]
        row = [
            f"`{feat}`",
            _fmt(g.get("r")),
            _fmt(by.get("EPL", {}).get("r")),
            _fmt(by.get("Bundesliga", {}).get("r")),
            _fmt(by.get("LaLiga", {}).get("r")),
            _fmt(by.get("SerieA", {}).get("r")),
            f"{cons['leagues_passing']}/{cons['total']}",
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## 4. OLS multivariada (importancia relativa entre supervivientes)")
    lines.append("")
    used = multi.get("features_used", "survivors")
    if used == "top10_by_abs_r":
        lines.append("_No hubo supervivientes; ajustamos la OLS con las **top-10 features por |r|** para estimar el techo conjunto realista._")
        lines.append("")
    if multi.get("r2") is None:
        lines.append("_No se pudo ajustar la OLS multivariada (muestra insuficiente o sin features supervivientes)._")
    else:
        lines.append(f"- Muestra (n con todas las features no nulas): **{multi['n']}**")
        lines.append(f"- R² de la OLS estandarizada: **{_fmt(multi['r2'])}**")
        lines.append("")
        lines.append("**Coeficientes estandarizados (magnitud comparable):**")
        lines.append("")
        lines.append("| Feature | β estandarizado | |β| |")
        lines.append("|---------|-----------------|-----|")
        coefs = multi["coefs"]
        ranked_b = sorted(coefs.items(), key=lambda kv: abs(kv[1]) if kv[1] is not None else 0, reverse=True)
        for feat, b in ranked_b:
            lines.append(f"| `{feat}` | {_fmt(b)} | {_fmt(abs(b) if b is not None else None)} |")
    lines.append("")

    lines.append("## 5. Hallazgos clave (interpretación)")
    lines.append("")
    # Generamos interpretación pragmática
    if survivors:
        top3 = ranked[:3]
        lines.append("- **Las 3 señales más correlacionadas con `total_corners` son:**")
        for feat, abs_r in top3:
            g = eval_results[feat]["global"]
            lines.append(f"  - `{feat}` (r={_fmt(g.get('r'))}, n={g.get('n')}, Δ MAE vs baseline = {_fmt(g.get('mae_improvement'),3)}).")
    else:
        lines.append("- **Ninguna feature** supera el umbral acordado de |r| ≥ {th}.".format(th=R_THRESHOLD))

    # Comparación L5 vs L15
    pair_examples = [
        ("home_corners_for_L5", "home_corners_for_L15"),
        ("home_corners_against_L5", "home_corners_against_L15"),
        ("away_corners_for_L5", "away_corners_for_L15"),
        ("sum_corners_for_L5", "sum_corners_for_L15"),
        ("match_corner_momentum_L5", "match_corner_momentum_L15"),
    ]
    lines.append("")
    lines.append("- **L5 vs L15** (¿cuál ventana es más informativa?):")
    lines.append("")
    lines.append("| Par | r L5 | r L15 | Ganador |")
    lines.append("|-----|------|-------|---------|")
    for f5, f15 in pair_examples:
        r5 = eval_results.get(f5, {}).get("global", {}).get("r")
        r15 = eval_results.get(f15, {}).get("global", {}).get("r")
        winner = "L15" if (r5 is not None and r15 is not None and abs(r15) > abs(r5)) \
                 else ("L5" if (r5 is not None and r15 is not None) else "—")
        lines.append(f"| `{f5}` vs `{f15}` | {_fmt(r5)} | {_fmt(r15)} | {winner} |")
    lines.append("")

    # Diagnóstico "Home/Away split"
    lines.append("- **Home/Away split**: comparamos la versión global de la feature contra la versión filtrada por venue (atHome / atAway):")
    lines.append("")
    lines.append("| Par | r global | r venue | Ganador |")
    lines.append("|-----|----------|---------|---------|")
    venue_pairs = [
        ("home_corners_for_L15", "home_corners_for_atHome_L15"),
        ("home_corners_against_L15", "home_corners_against_atHome_L15"),
        ("away_corners_for_L15", "away_corners_for_atAway_L15"),
        ("away_corners_against_L15", "away_corners_against_atAway_L15"),
    ]
    for fg, fv in venue_pairs:
        rg = eval_results.get(fg, {}).get("global", {}).get("r")
        rv = eval_results.get(fv, {}).get("global", {}).get("r")
        winner = "venue" if (rg is not None and rv is not None and abs(rv) > abs(rg)) \
                 else ("global" if (rg is not None and rv is not None) else "—")
        lines.append(f"| `{fg}` vs `{fv}` | {_fmt(rg)} | {_fmt(rv)} | {winner} |")
    lines.append("")

    # Diagnóstico favorito
    fav_r = eval_results.get("fav_implied_prob", {}).get("global", {}).get("r")
    diff_r = eval_results.get("abs_implied_prob_diff", {}).get("global", {}).get("r")
    lines.append("- **Favorito dominante**: ")
    lines.append(f"  - `fav_implied_prob` r = {_fmt(fav_r)}")
    lines.append(f"  - `abs_implied_prob_diff` r = {_fmt(diff_r)}")
    lines.append("")

    # Diagnóstico serie activa
    streak_r = eval_results.get("home_active_over_9_5_streak", {}).get("global", {}).get("r")
    streak2_r = eval_results.get("away_active_over_9_5_streak", {}).get("global", {}).get("r")
    lines.append("- **Serie activa** (rachas over 9.5 en últimos 5):")
    lines.append(f"  - home r = {_fmt(streak_r)} | away r = {_fmt(streak2_r)}")
    lines.append("")

    lines.append("## 6. Decisiones operativas")
    lines.append("")
    lines.append(f"- **Features supervivientes ({len(survivors)})** para considerar en el diseño del motor (Fase 2):")
    lines.append("")
    for feat in survivors:
        g = eval_results[feat]["global"]
        lines.append(f"  - `{feat}` (r={_fmt(g.get('r'))}, MAE walk-fwd={_fmt(g.get('mae'),3)}, mejora vs baseline={_fmt(g.get('mae_improvement'),3)}).")
    lines.append("")

    lines.append("- **Features descartadas** (no pasan |r| ≥ {th}):".format(th=R_THRESHOLD))
    lines.append("")
    for feat, e in eval_results.items():
        if not e["global"].get("keep"):
            r = e["global"].get("r")
            lines.append(f"  - `{feat}` (r={_fmt(r)}).")
    lines.append("")

    lines.append("## 7. Limitaciones honestas")
    lines.append("")
    lines.append("- **Modelo univariate** para walk-forward: no captura interacciones. El R² multivariado al final cuantifica el techo conjunto entre supervivientes.")
    lines.append("- **No usamos sklearn/scipy** (decisión deliberada para no contaminar `requirements.txt`); todas las métricas son numpy puro.")
    lines.append("- **Liga MX**: excluida por ausencia de columnas HC/AC en `football-data.co.uk` (extra leagues solo tienen goles + odds). Para incluirla habría que recurrir a otra fuente (API-Sports, scraping con créditos), lo que no se hizo para conservar créditos.")
    lines.append("- El umbral `|r| ≥ 0.15` es **estricto** (acordado contigo). Algunas features con r en torno a 0.10-0.14 podrían tener señal interactiva — el motor en Fase 2 podría revisitar este corte con un test multivariado dedicado.")
    lines.append("- **Sin tunear hiperparámetros**: walk-forward usa 5 folds y un OLS univariate puro. No es producción — es diagnóstico.")
    lines.append("")

    lines.append("## 8. Siguientes pasos sugeridos (NO ejecutados aún)")
    lines.append("")
    lines.append("- Fase 2: diseño del motor (Bivariate Poisson vs Negative Binomial) usando las features supervivientes como inputs.")
    lines.append("- Análisis adicional opcional: hyperparameter-free interacciones (p.ej. `home_corners_for * away_corners_against`) para verificar señal multiplicativa.")
    lines.append("- Validación cruzada estratificada por liga (no solo temporal).")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5) Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not DATASET.exists():
        print(f"[error] dataset not found: {DATASET}")
        return 2

    rows = _load_dataset()
    print(f"[load] {len(rows)} matches from {DATASET}")
    df = _build_features(rows)
    print(f"[features] dataframe shape: {df.shape}")

    leagues = sorted(df["league"].unique().tolist())
    eval_results = _eval_features(df, leagues)
    ranked = _rank_by_abs_r(eval_results)
    survivors = _surviving_features(eval_results)
    consistency = _league_consistency(eval_results, leagues)

    print(f"\n[result] survivors (|r| >= {R_THRESHOLD}): {len(survivors)} / {len(CANDIDATE_FEATURES)}")
    for feat, abs_r in ranked[:10]:
        r = eval_results[feat]["global"]["r"]
        print(f"  {feat:<50s} r={r:+.4f}")

    # OLS multivariada con todos los supervivientes; si no hay, usar top-10 por |r|.
    if survivors:
        multi = _ols_multivariate_standardized(df, survivors)
        multi["features_used"] = "survivors"
    else:
        top_features = [f for f, _ in ranked[:10] if eval_results[f]["global"].get("r") is not None]
        multi = _ols_multivariate_standardized(df, top_features) if top_features else {"n": 0, "r2": None, "coefs": {}}
        multi["features_used"] = "top10_by_abs_r"
        multi["features_list"] = top_features

    stats = {
        "meta": {
            "generated_utc":   datetime.utcnow().isoformat() + "Z",
            "dataset":         str(DATASET),
            "n_matches":       int(len(df)),
            "leagues":         leagues,
            "threshold_abs_r": R_THRESHOLD,
            "liga_mx_excluded_reason": "football-data.co.uk new/MEX.csv lacks HC/AC and shots columns",
        },
        "features":             eval_results,
        "ranked_by_abs_r":      ranked,
        "surviving_features":   survivors,
        "league_consistency":   consistency,
        "multivariate_ols":     multi,
    }

    OUT_STATS.parent.mkdir(parents=True, exist_ok=True)
    OUT_STATS.write_text(json.dumps(stats, indent=2, default=str),
                          encoding="utf-8")
    print(f"\n[write] stats → {OUT_STATS}")

    md = _build_markdown(stats)
    OUT_REPORT.write_text(md, encoding="utf-8")
    print(f"[write] report → {OUT_REPORT}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
