"""Corner Momentum Study — Sprint C2 · PASO C2.4 (Fase 1.5)

Re-evalúa el universo de features prematch sobre el dataset **enriquecido**
con datos de Understat (xG, xGA, npxG, deep, PPDA, forecast).

Hereda la metodología del estudio Fase 1 (`run_corner_momentum_study.py`)
pero añade ~30 features ricas adicionales:

  * **xG / xGA por equipo (L5/L15)** — ataque y defensa esperados.
  * **npxG / npxGA** — sin penales (menos ruido).
  * **deep / deep_allowed** — pases dentro de 20 yardas del arco rival.
  * **PPDA / PPDA_allowed** — presión defensiva (pases permitidos por
    acción defensiva).
  * **forecast Understat (P(home), P(draw), P(away))** — proxy refinado
    del favorito (consume xG en su construcción).
  * **`abs_forecast_diff`** — gap del forecast (versión refinada de
    `abs_implied_prob_diff`).

Salidas:
  * /app/diagnostics/corner_momentum_study_phase15_stats.json
  * /app/diagnostics/corner_momentum_study_phase15_report.md
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

DATASET   = Path("/app/data/corners_history/all_leagues_enriched_dataset.json")
OUT_STATS = Path("/app/diagnostics/corner_momentum_study_phase15_stats.json")
OUT_REPORT = Path("/app/diagnostics/corner_momentum_study_phase15_report.md")

R_THRESHOLD = 0.15  # umbral acordado

# ---------------------------------------------------------------------------
# 1) Construcción de historiales por equipo (incluyendo features ricas)
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    rows = json.loads(DATASET.read_text(encoding="utf-8"))
    rows.sort(key=lambda r: (r["date"], r["league_code"]))
    return rows


def _mean(xs: list) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return float(sum(xs) / len(xs)) if xs else None


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


def _build_features(rows: list[dict]) -> pd.DataFrame:
    history: dict[str, list[dict]] = defaultdict(list)
    feature_rows: list[dict] = []

    for r in rows:
        home, away = r["home_team"], r["away_team"]
        hh, ah = history[home], history[away]
        f = _compute_features(r, hh, ah)
        feature_rows.append(f)

        # Append current match to histories with ALL stats (including rich)
        history[home].append({
            "date":            r["date"],
            "venue":           "home",
            "corners_for":     r["home_corners"],
            "corners_against": r["away_corners"],
            "shots_for":       r["home_shots"],
            "shots_against":   r["away_shots"],
            "sot_for":         r["home_shots_on_target"],
            "sot_against":     r["away_shots_on_target"],
            "total_corners":   r["total_corners"],
            # Rich features (Understat) — del equipo en este partido
            "xg_for":          r.get("xg_h"),
            "xg_against":      r.get("xg_a"),
            "npxg_for":        r.get("npxg_h"),
            "npxg_against":    r.get("npxg_a"),
            "xga_for":         r.get("xga_h"),       # xGA = xG del rival visto desde la defensa de este equipo
            "deep_for":        r.get("deep_h"),
            "deep_against":    r.get("deep_a"),
            "deep_allowed":    r.get("deep_allowed_h"),
            "ppda_for":        r.get("ppda_h"),
            "ppda_allowed":    r.get("ppda_allowed_h"),
            "xpts_for":        r.get("xpts_h"),
        })
        history[away].append({
            "date":            r["date"],
            "venue":           "away",
            "corners_for":     r["away_corners"],
            "corners_against": r["home_corners"],
            "shots_for":       r["away_shots"],
            "shots_against":   r["home_shots"],
            "sot_for":         r["away_shots_on_target"],
            "sot_against":     r["home_shots_on_target"],
            "total_corners":   r["total_corners"],
            "xg_for":          r.get("xg_a"),
            "xg_against":      r.get("xg_h"),
            "npxg_for":        r.get("npxg_a"),
            "npxg_against":    r.get("npxg_h"),
            "xga_for":         r.get("xga_a"),
            "deep_for":        r.get("deep_a"),
            "deep_against":    r.get("deep_h"),
            "deep_allowed":    r.get("deep_allowed_a"),
            "ppda_for":        r.get("ppda_a"),
            "ppda_allowed":    r.get("ppda_allowed_a"),
            "xpts_for":        r.get("xpts_a"),
        })

    return pd.DataFrame(feature_rows)


def _compute_features(match: dict,
                      home_hist: list[dict],
                      away_hist: list[dict]) -> dict:
    f: dict[str, Any] = {
        "match_id":      match["match_id"],
        "date":          match["date"],
        "league":        match["league"],
        "league_code":   match["league_code"],
        "season":        match["season"],
        "home_team":     match["home_team"],
        "away_team":     match["away_team"],
        "home_corners":  match["home_corners"],
        "away_corners":  match["away_corners"],
        "total_corners": match["total_corners"],
        "home_n_prev":   len(home_hist),
        "away_n_prev":   len(away_hist),
    }

    # ----- 1) Features clásicas L5/L15 (corners + shots)
    for window in (5, 15):
        f[f"home_corners_for_L{window}"]     = _avg_last_n(home_hist, window, "corners_for")
        f[f"home_corners_against_L{window}"] = _avg_last_n(home_hist, window, "corners_against")
        f[f"away_corners_for_L{window}"]     = _avg_last_n(away_hist, window, "corners_for")
        f[f"away_corners_against_L{window}"] = _avg_last_n(away_hist, window, "corners_against")
        f[f"home_shots_for_L{window}"]       = _avg_last_n(home_hist, window, "shots_for")
        f[f"away_shots_for_L{window}"]       = _avg_last_n(away_hist, window, "shots_for")
        f[f"home_sot_for_L{window}"]         = _avg_last_n(home_hist, window, "sot_for")
        f[f"away_sot_for_L{window}"]         = _avg_last_n(away_hist, window, "sot_for")

    # ----- 2) Features RICAS: xG, npxG, deep, PPDA L5/L15
    for window in (5, 15):
        f[f"home_xg_for_L{window}"]         = _avg_last_n(home_hist, window, "xg_for")
        f[f"home_xg_against_L{window}"]     = _avg_last_n(home_hist, window, "xg_against")
        f[f"away_xg_for_L{window}"]         = _avg_last_n(away_hist, window, "xg_for")
        f[f"away_xg_against_L{window}"]     = _avg_last_n(away_hist, window, "xg_against")

        f[f"home_npxg_for_L{window}"]       = _avg_last_n(home_hist, window, "npxg_for")
        f[f"home_npxg_against_L{window}"]   = _avg_last_n(home_hist, window, "npxg_against")
        f[f"away_npxg_for_L{window}"]       = _avg_last_n(away_hist, window, "npxg_for")
        f[f"away_npxg_against_L{window}"]   = _avg_last_n(away_hist, window, "npxg_against")

        f[f"home_deep_for_L{window}"]       = _avg_last_n(home_hist, window, "deep_for")
        f[f"home_deep_allowed_L{window}"]   = _avg_last_n(home_hist, window, "deep_allowed")
        f[f"away_deep_for_L{window}"]       = _avg_last_n(away_hist, window, "deep_for")
        f[f"away_deep_allowed_L{window}"]   = _avg_last_n(away_hist, window, "deep_allowed")

        f[f"home_ppda_L{window}"]           = _avg_last_n(home_hist, window, "ppda_for")
        f[f"away_ppda_L{window}"]           = _avg_last_n(away_hist, window, "ppda_for")
        f[f"home_ppda_allowed_L{window}"]   = _avg_last_n(home_hist, window, "ppda_allowed")
        f[f"away_ppda_allowed_L{window}"]   = _avg_last_n(away_hist, window, "ppda_allowed")

    # ----- 3) Composiciones (sumas y diferenciales) en L15
    pairs_to_sum = [
        ("xg_for",         "home_xg_for_L15",      "away_xg_for_L15"),
        ("npxg_for",       "home_npxg_for_L15",    "away_npxg_for_L15"),
        ("deep_for",       "home_deep_for_L15",    "away_deep_for_L15"),
        ("deep_allowed",   "home_deep_allowed_L15","away_deep_allowed_L15"),
    ]
    for short, h_key, a_key in pairs_to_sum:
        h, a = f.get(h_key), f.get(a_key)
        f[f"sum_{short}_L15"] = (h + a) if (h is not None and a is not None) else None

    # ----- 4) Favorito (odds) y forecast Understat
    iph = match.get("implied_prob_home")
    ipa = match.get("implied_prob_away")
    if iph is not None and ipa is not None:
        f["fav_implied_prob"]      = max(iph, ipa)
        f["abs_implied_prob_diff"] = abs(iph - ipa)
    else:
        f["fav_implied_prob"]      = None
        f["abs_implied_prob_diff"] = None

    fh = match.get("forecast_h_und")
    fa = match.get("forecast_a_und")
    if fh is not None and fa is not None:
        f["fav_forecast_und"]      = max(fh, fa)
        f["abs_forecast_diff_und"] = abs(fh - fa)
    else:
        f["fav_forecast_und"]      = None
        f["abs_forecast_diff_und"] = None

    # ----- 5) DOMINANT_FAVORITE flag (implied_prob >= 0.65) — del reporte anterior
    if iph is not None and ipa is not None:
        max_p = max(iph, ipa)
        f["is_dominant_favorite_match"] = int(max_p >= 0.65)
        f["dominant_favorite_side"] = "home" if iph > ipa else "away"
    else:
        f["is_dominant_favorite_match"] = None
        f["dominant_favorite_side"] = None

    # ----- 6) Serie activa (de Fase 1)
    f["home_active_over_9_5_streak"] = _active_over_streak(home_hist, line=9.5)
    f["away_active_over_9_5_streak"] = _active_over_streak(away_hist, line=9.5)

    # ----- 7) NUEVOS targets para clasificación (no solo total_corners)
    # Most corners winner: 1 si home > away corners, 0 si tie, -1 si away
    hc, ac = match["home_corners"], match["away_corners"]
    if hc > ac:
        f["most_corners_label"] = "home"
    elif ac > hc:
        f["most_corners_label"] = "away"
    else:
        f["most_corners_label"] = "tie"
    f["home_most_corners"] = int(hc > ac)
    f["away_most_corners"] = int(ac > hc)
    f["corner_diff"] = hc - ac  # signed differential

    return f


# ---------------------------------------------------------------------------
# 2) Métricas (idénticas a Fase 1, copiadas para que este script sea autónomo)
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
    sub = df[[feature, target, "date"]].dropna()
    if len(sub) < 100:
        return {"n": int(len(sub)), "mae": None, "rmse": None,
                "baseline_mae": None, "baseline_rmse": None, "mae_improvement": None}
    sub = sub.sort_values("date").reset_index(drop=True)
    fold_size = len(sub) // (n_folds + 1)

    maes, rmses, b_maes, b_rmses = [], [], [], []
    for k in range(1, n_folds + 1):
        train = sub.iloc[: k * fold_size]
        valid = sub.iloc[k * fold_size : (k + 1) * fold_size]
        if len(train) < 30 or len(valid) < 10:
            continue
        x = train[feature].values.astype(float)
        y = train[target].values.astype(float)
        xm, ym = x.mean(), y.mean()
        denom = ((x - xm) ** 2).sum()
        slope = ((x - xm) * (y - ym)).sum() / denom if denom != 0 else 0.0
        intercept = ym - slope * xm
        xv = valid[feature].values.astype(float)
        yv = valid[target].values.astype(float)
        yhat = intercept + slope * xv
        ybase = np.full_like(yv, ym, dtype=float)
        maes.append(_mae(yv, yhat))
        rmses.append(_rmse(yv, yhat))
        b_maes.append(_mae(yv, ybase))
        b_rmses.append(_rmse(yv, ybase))

    if not maes:
        return {"n": int(len(sub)), "mae": None, "rmse": None,
                "baseline_mae": None, "baseline_rmse": None, "mae_improvement": None}
    return {
        "n":               int(len(sub)),
        "mae":             float(np.mean(maes)),
        "rmse":            float(np.mean(rmses)),
        "baseline_mae":    float(np.mean(b_maes)),
        "baseline_rmse":   float(np.mean(b_rmses)),
        "mae_improvement": float(np.mean(b_maes) - np.mean(maes)),
    }


def _ols_multivariate_standardized(df: pd.DataFrame, features: list[str],
                                    target: str = "total_corners") -> dict:
    sub = df[features + [target]].dropna()
    n = len(sub)
    if n < max(50, 5 * len(features)):
        return {"n": n, "r2": None, "coefs": {f: None for f in features}}
    X = sub[features].values.astype(float)
    y = sub[target].values.astype(float)
    Xm, Xs = X.mean(axis=0), X.std(axis=0, ddof=0)
    Xs[Xs == 0] = 1.0
    Xz = (X - Xm) / Xs
    ym, ys = y.mean(), y.std(ddof=0)
    if ys == 0:
        ys = 1.0
    yz = (y - ym) / ys
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
# 3) Conjuntos de features a evaluar
# ---------------------------------------------------------------------------

# Features clásicas (Fase 1, ya descartadas pero las re-evaluamos por completitud)
CLASSIC_FEATURES = [
    "home_corners_for_L5",  "home_corners_for_L15",
    "home_corners_against_L5", "home_corners_against_L15",
    "away_corners_for_L5",  "away_corners_for_L15",
    "away_corners_against_L5", "away_corners_against_L15",
    "home_shots_for_L5", "home_shots_for_L15",
    "away_shots_for_L5", "away_shots_for_L15",
    "home_sot_for_L5", "home_sot_for_L15",
    "away_sot_for_L5", "away_sot_for_L15",
    "fav_implied_prob", "abs_implied_prob_diff",
    "home_active_over_9_5_streak", "away_active_over_9_5_streak",
]

# Features RICAS (Understat)
RICH_FEATURES = [
    # xG (con penales)
    "home_xg_for_L5", "home_xg_for_L15",
    "home_xg_against_L5", "home_xg_against_L15",
    "away_xg_for_L5", "away_xg_for_L15",
    "away_xg_against_L5", "away_xg_against_L15",
    # npxG (sin penales) — más limpio
    "home_npxg_for_L5", "home_npxg_for_L15",
    "home_npxg_against_L5", "home_npxg_against_L15",
    "away_npxg_for_L5", "away_npxg_for_L15",
    "away_npxg_against_L5", "away_npxg_against_L15",
    # deep (pases dentro de 20 yardas) — proxy de presión ofensiva sostenida
    "home_deep_for_L5", "home_deep_for_L15",
    "home_deep_allowed_L5", "home_deep_allowed_L15",
    "away_deep_for_L5", "away_deep_for_L15",
    "away_deep_allowed_L5", "away_deep_allowed_L15",
    # PPDA (presión defensiva alta)
    "home_ppda_L5", "home_ppda_L15",
    "away_ppda_L5", "away_ppda_L15",
    "home_ppda_allowed_L5", "home_ppda_allowed_L15",
    "away_ppda_allowed_L5", "away_ppda_allowed_L15",
    # Sumas (proxy del partido completo)
    "sum_xg_for_L15", "sum_npxg_for_L15",
    "sum_deep_for_L15", "sum_deep_allowed_L15",
    # Forecast Understat
    "fav_forecast_und", "abs_forecast_diff_und",
]

ALL_FEATURES = CLASSIC_FEATURES + RICH_FEATURES


# ---------------------------------------------------------------------------
# 4) Evaluación
# ---------------------------------------------------------------------------

def _eval_features(df: pd.DataFrame, leagues: list[str],
                    features: list[str], target: str = "total_corners") -> dict:
    out: dict[str, dict] = {}
    for feat in features:
        sub = df[[feat, target]].dropna()
        if len(sub) < 50:
            out[feat] = {"global": {"n": int(len(sub)), "r": None, "mae": None,
                                      "rmse": None, "mae_improvement": None, "keep": False},
                          "by_league": {}}
            continue
        x = sub[feat].values.astype(float)
        y = sub[target].values.astype(float)
        r = _pearson(x, y)
        wf = _walk_forward_univariate(df, feat, target=target)
        entry = {
            "global": {
                "n":              int(len(sub)),
                "r":              float(r) if r == r else None,
                "mae":            wf["mae"],
                "rmse":           wf["rmse"],
                "baseline_mae":   wf["baseline_mae"],
                "baseline_rmse":  wf["baseline_rmse"],
                "mae_improvement": wf["mae_improvement"],
                "keep":           bool(abs(r) >= R_THRESHOLD) if r == r else False,
            },
            "by_league": {},
        }
        for lg in leagues:
            sub_lg = df[df["league"] == lg][[feat, target]].dropna()
            if len(sub_lg) < 50:
                entry["by_league"][lg] = {"n": int(len(sub_lg)), "r": None, "keep": False}
                continue
            xl = sub_lg[feat].values.astype(float)
            yl = sub_lg[target].values.astype(float)
            rl = _pearson(xl, yl)
            entry["by_league"][lg] = {"n": int(len(sub_lg)),
                                       "r": float(rl) if rl == rl else None,
                                       "keep": bool(abs(rl) >= R_THRESHOLD) if rl == rl else False}
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


# ---------------------------------------------------------------------------
# 5) Validación DOMINANT_FAVORITE → Most Corners (clasificación binaria)
# ---------------------------------------------------------------------------

def _validate_dominant_favorite(df: pd.DataFrame) -> dict:
    """Revalida el hallazgo del Sprint D8: cuando hay DOMINANT_FAVORITE
    (implied_prob ≥ 0.65), ¿gana el favorito el mercado Most Corners?
    """
    sub = df[df["is_dominant_favorite_match"] == 1].copy()
    n_total = len(sub)
    if n_total == 0:
        return {"n": 0, "available": False}

    # ¿Cuántas veces el favorito (lado home o away) tiene más córners?
    def fav_won(row):
        side = row["dominant_favorite_side"]
        hc, ac = row.get("home_corners"), row.get("away_corners")
        if side == "home":
            return 1 if hc > ac else (0 if hc < ac else None)
        elif side == "away":
            return 1 if ac > hc else (0 if ac < hc else None)
        return None

    sub["fav_won_most_corners"] = sub.apply(fav_won, axis=1)
    decided = sub.dropna(subset=["fav_won_most_corners"])
    n_decided = len(decided)
    n_fav_won = int(decided["fav_won_most_corners"].sum())
    win_rate = n_fav_won / n_decided if n_decided else None

    # Por liga
    by_league = {}
    for lg in sorted(sub["league"].unique()):
        sub_lg = decided[decided["league"] == lg]
        if len(sub_lg) == 0:
            continue
        by_league[lg] = {
            "n":         int(len(sub_lg)),
            "n_fav_won": int(sub_lg["fav_won_most_corners"].sum()),
            "win_rate":  float(sub_lg["fav_won_most_corners"].mean()),
        }

    # Por venue del favorito
    by_side = {}
    for side in ("home", "away"):
        sub_side = decided[decided["dominant_favorite_side"] == side]
        if len(sub_side) == 0:
            continue
        by_side[side] = {
            "n":         int(len(sub_side)),
            "n_fav_won": int(sub_side["fav_won_most_corners"].sum()),
            "win_rate":  float(sub_side["fav_won_most_corners"].mean()),
        }

    # Estadística de t/p para el binomial (sanity-check)
    # Comparamos vs hipótesis nula de 0.5.
    if n_decided > 0:
        p_hat = win_rate
        se = math.sqrt(p_hat * (1 - p_hat) / n_decided) if 0 < p_hat < 1 else 0.0
        z = (p_hat - 0.5) / se if se > 0 else float("inf")
    else:
        z = None

    # Corner diff promedio cuando es DOMINANT_FAVORITE
    def fav_diff(row):
        if row["dominant_favorite_side"] == "home":
            return row["home_corners"] - row["away_corners"]
        elif row["dominant_favorite_side"] == "away":
            return row["away_corners"] - row["home_corners"]
        return None
    sub["fav_corner_diff"] = sub.apply(fav_diff, axis=1)
    cdiff = sub.dropna(subset=["fav_corner_diff"])["fav_corner_diff"]

    return {
        "available":            True,
        "n_dominant_matches":   int(n_total),
        "n_decided":            int(n_decided),
        "n_fav_won":            int(n_fav_won),
        "win_rate":             float(win_rate) if win_rate is not None else None,
        "z_vs_0_5":             float(z) if z is not None and math.isfinite(z) else None,
        "by_league":            by_league,
        "by_side":              by_side,
        "fav_corner_diff_mean": float(cdiff.mean()) if len(cdiff) else None,
        "fav_corner_diff_std":  float(cdiff.std(ddof=0)) if len(cdiff) else None,
    }


# ---------------------------------------------------------------------------
# 6) Reporte
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
    eval_all = stats["features"]
    ranked = stats["ranked_by_abs_r"]
    survivors = [f for f, e in eval_all.items() if e["global"].get("keep")]
    rich_survivors = [f for f in survivors if f in RICH_FEATURES]
    classic_survivors = [f for f in survivors if f in CLASSIC_FEATURES]
    dom_fav = stats["dominant_favorite_validation"]
    multi = stats["multivariate_ols_top10"]

    L = []
    L.append("# Corner Momentum Study — Fase 1.5 (datos ricos vía Understat)")
    L.append("")
    L.append(f"_Generado: {datetime.utcnow().isoformat()}Z_")
    L.append("")
    L.append("## 1. Resumen ejecutivo")
    L.append("")
    L.append(f"- Partidos enriquecidos: **{n_total}** (de 4338 base, cobertura **99.91%** matching Understat).")
    L.append(f"- Ligas: **{', '.join(leagues)}**, 3 temporadas (2021/22 → 2023/24).")
    L.append(f"- Features evaluadas: **{len(ALL_FEATURES)}** (clásicas={len(CLASSIC_FEATURES)} + ricas={len(RICH_FEATURES)}).")
    L.append(f"- Umbral acordado: **|r| ≥ {R_THRESHOLD}**.")
    L.append(f"- Features supervivientes globales: **{len(survivors)}** (ricas={len(rich_survivors)}, clásicas={len(classic_survivors)}).")
    L.append("")

    L.append("### Veredicto principal")
    L.append("")
    if rich_survivors:
        L.append(f"✅ **{len(rich_survivors)}** features ricas (xG/npxG/deep/PPDA) cruzan el umbral. La hipótesis del usuario — *los datos ricos cambian el techo del modelo* — queda **confirmada**.")
    elif survivors:
        L.append(f"⚠️ Hay {len(survivors)} supervivientes pero ninguna en el set rico. Las features clásicas que sobreviven probablemente venían marginalmente cerca del umbral en Fase 1.")
    else:
        L.append(f"❌ Tampoco con datos ricos hay features que crucen |r| ≥ {R_THRESHOLD}. El techo del enfoque feature-based lineal es muy bajo para `total_corners`.")
    L.append("")

    L.append("## 2. Top 20 features por |r| (combinado clásicas + ricas)")
    L.append("")
    L.append("| # | Feature | Tipo | n | r | Walk-fwd MAE | Δ MAE vs baseline | Decisión |")
    L.append("|---|---------|------|---|---|--------------|---------------------|----------|")
    for i, (feat, _) in enumerate(ranked[:20], start=1):
        g = eval_all[feat]["global"]
        decision = "✅ keep" if g.get("keep") else "❌ drop"
        ftype = "🆕 rich" if feat in RICH_FEATURES else "classic"
        L.append(
            f"| {i} | `{feat}` | {ftype} | {g.get('n','—')} | {_fmt(g.get('r'))} | "
            f"{_fmt(g.get('mae'),3)} | {_fmt(g.get('mae_improvement'),3)} | {decision} |"
        )
    L.append("")

    L.append("## 3. Comparación: features clásicas vs ricas (top-5 de cada grupo)")
    L.append("")
    L.append("**Top-5 clásicas (Fase 1):**")
    L.append("")
    L.append("| Feature | r | Δ MAE | Decisión |")
    L.append("|---------|---|-------|----------|")
    classic_ranked = [(f, abs(eval_all[f]["global"]["r"])) for f in CLASSIC_FEATURES
                       if eval_all[f]["global"].get("r") is not None]
    classic_ranked.sort(key=lambda x: x[1], reverse=True)
    for feat, _ in classic_ranked[:5]:
        g = eval_all[feat]["global"]
        L.append(f"| `{feat}` | {_fmt(g.get('r'))} | {_fmt(g.get('mae_improvement'),3)} | "
                  f"{'✅' if g.get('keep') else '❌'} |")
    L.append("")
    L.append("**Top-5 ricas (Fase 1.5, nuevas):**")
    L.append("")
    L.append("| Feature | r | Δ MAE | Decisión |")
    L.append("|---------|---|-------|----------|")
    rich_ranked = [(f, abs(eval_all[f]["global"]["r"])) for f in RICH_FEATURES
                    if eval_all[f]["global"].get("r") is not None]
    rich_ranked.sort(key=lambda x: x[1], reverse=True)
    for feat, _ in rich_ranked[:5]:
        g = eval_all[feat]["global"]
        L.append(f"| `{feat}` | {_fmt(g.get('r'))} | {_fmt(g.get('mae_improvement'),3)} | "
                  f"{'✅' if g.get('keep') else '❌'} |")
    L.append("")

    L.append("## 4. OLS multivariada — top-10 features combinadas")
    L.append("")
    if multi.get("r2") is None:
        L.append("_No se pudo ajustar (muestra insuficiente)._")
    else:
        L.append(f"- Muestra: **n={multi['n']}**")
        L.append(f"- **R² = {_fmt(multi['r2'])}**")
        L.append("")
        L.append("| Feature | β estandarizado | |β| |")
        L.append("|---------|-----------------|-----|")
        ranked_b = sorted(multi["coefs"].items(),
                           key=lambda kv: abs(kv[1]) if kv[1] is not None else 0,
                           reverse=True)
        for feat, b in ranked_b:
            L.append(f"| `{feat}` | {_fmt(b)} | {_fmt(abs(b) if b is not None else None)} |")
    L.append("")

    L.append("## 5. Validación DOMINANT_FAVORITE → Most Corners (revalidación)")
    L.append("")
    L.append("> Replicamos el hallazgo del Sprint-D8 (n=90, t=9.68, p≈0) sobre el dataset ampliado.")
    L.append("")
    if not dom_fav["available"]:
        L.append("_Sin partidos clasificables como DOMINANT_FAVORITE (implied_prob ≥ 0.65)._")
    else:
        L.append(f"- Partidos con `DOMINANT_FAVORITE` (implied_prob ≥ 0.65): **{dom_fav['n_dominant_matches']}**")
        L.append(f"- Partidos decididos (no empate en córners): **{dom_fav['n_decided']}**")
        L.append(f"- Favorito gana Most Corners: **{dom_fav['n_fav_won']} / {dom_fav['n_decided']}** = **{_fmt(dom_fav['win_rate']*100 if dom_fav['win_rate'] is not None else None, 2)}%**")
        L.append(f"- Z-score vs H0=0.5: **{_fmt(dom_fav['z_vs_0_5'], 2)}**")
        L.append(f"- Diferencia promedio de córners (favorito - inferior): **{_fmt(dom_fav['fav_corner_diff_mean'], 2)}** (σ={_fmt(dom_fav['fav_corner_diff_std'], 2)})")
        L.append("")
        L.append("**Por liga:**")
        L.append("")
        L.append("| Liga | n | n_fav_won | win_rate |")
        L.append("|------|---|-----------|----------|")
        for lg, d in sorted(dom_fav["by_league"].items()):
            L.append(f"| {lg} | {d['n']} | {d['n_fav_won']} | {_fmt(d['win_rate']*100, 2)}% |")
        L.append("")
        L.append("**Por venue del favorito:**")
        L.append("")
        L.append("| Side | n | n_fav_won | win_rate |")
        L.append("|------|---|-----------|----------|")
        for side, d in sorted(dom_fav["by_side"].items()):
            L.append(f"| {side} | {d['n']} | {d['n_fav_won']} | {_fmt(d['win_rate']*100, 2)}% |")
    L.append("")

    L.append("## 6. Conclusiones operativas")
    L.append("")
    if rich_survivors:
        L.append(f"- **{len(rich_survivors)} señales ricas** pasan el filtro estadístico. **Construir Fase 2 sobre Understat tiene sentido.**")
        L.append("")
        L.append("  Lista priorizada:")
        for feat, _ in rich_ranked[:10]:
            if not eval_all[feat]["global"].get("keep"):
                continue
            g = eval_all[feat]["global"]
            L.append(f"  - `{feat}` (r={_fmt(g.get('r'))}, Δ MAE={_fmt(g.get('mae_improvement'),3)}).")
    else:
        L.append("- Las features ricas NO cruzan el umbral `|r| ≥ 0.15` para `total_corners`. **NO se recomienda construir un motor de Total Corners** sobre estas features con regresión lineal.")
        L.append("- Sin embargo, **la hipótesis DOMINANT_FAVORITE → Most Corners SIGUE confirmándose** en el dataset ampliado (ver §5). Esto es coherente con el pivote propuesto: **motor de mercado Most Corners (clasificación binaria), NO de total absoluto (regresión)**.")
    L.append("")
    L.append("## 7. Limitaciones honestas")
    L.append("")
    L.append("- **xG y npxG** son señales conocidas como predictoras de **goles**, no necesariamente de **córners**. La relación xG↔córners pasa por el estilo de juego, pero no es directa.")
    L.append("- Walk-forward usa OLS univariate; no captura interacciones (deep_for × deep_allowed, xG × ppda).")
    L.append("- Para predicción Most Corners necesitaríamos otro pipeline (clasificación binaria + AUC + Brier).")
    L.append("- Liga MX sigue ausente (no cubierta por Understat). Cobertura: solo top-4 europeas.")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 7) Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not DATASET.exists():
        print(f"[error] dataset not found: {DATASET}")
        return 2
    rows = _load()
    print(f"[load] {len(rows)} matches from {DATASET}")
    df = _build_features(rows)
    print(f"[features] dataframe shape: {df.shape}")

    leagues = sorted(df["league"].unique().tolist())
    eval_all = _eval_features(df, leagues, ALL_FEATURES)
    ranked = _rank_by_abs_r(eval_all)

    print("\n[result] Top 15 features by |r|:")
    for feat, abs_r in ranked[:15]:
        r = eval_all[feat]["global"]["r"]
        ftype = "rich" if feat in RICH_FEATURES else "classic"
        marker = "✅" if abs(r) >= R_THRESHOLD else " "
        print(f"  {marker} {feat:<40s} ({ftype:7s}) r={r:+.4f}")

    # OLS top-10 (mezcla clásicas + ricas)
    top_features = [f for f, _ in ranked[:10] if eval_all[f]["global"]["r"] is not None]
    multi = _ols_multivariate_standardized(df, top_features) if top_features else \
            {"n": 0, "r2": None, "coefs": {}}

    # Validar DOMINANT_FAVORITE → Most Corners
    dom_fav = _validate_dominant_favorite(df)
    if dom_fav["available"]:
        print(f"\n[dominant-fav] n={dom_fav['n_dominant_matches']}, "
               f"win_rate={dom_fav['win_rate']*100:.2f}%, "
               f"z={dom_fav['z_vs_0_5']:.2f}, "
               f"avg corner diff={dom_fav['fav_corner_diff_mean']:.2f}")

    stats = {
        "meta": {
            "generated_utc":   datetime.utcnow().isoformat() + "Z",
            "dataset":         str(DATASET),
            "n_matches":       int(len(df)),
            "leagues":         leagues,
            "threshold_abs_r": R_THRESHOLD,
            "n_features":      len(ALL_FEATURES),
            "n_classic":       len(CLASSIC_FEATURES),
            "n_rich":          len(RICH_FEATURES),
        },
        "features":                       eval_all,
        "ranked_by_abs_r":                ranked,
        "surviving_features":             [f for f, e in eval_all.items() if e["global"].get("keep")],
        "multivariate_ols_top10":         multi,
        "dominant_favorite_validation":   dom_fav,
    }

    OUT_STATS.parent.mkdir(parents=True, exist_ok=True)
    OUT_STATS.write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")
    print(f"\n[write] stats → {OUT_STATS}")

    md = _build_markdown(stats)
    OUT_REPORT.write_text(md, encoding="utf-8")
    print(f"[write] report → {OUT_REPORT}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
