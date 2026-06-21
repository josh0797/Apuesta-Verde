"""Sprint Corner-2 · Refinamientos avanzados.

Módulos:
    * **Efectos jerárquicos por liga** — calibra un β global + correcciones
      por liga (partial pooling). Cuando una liga tiene poca muestra,
      se "encoge" hacia el global.
    * **Ensemble Lineal + Skellam** — combina ambas predicciones con
      peso w calibrado por minimización de Brier en validation.
    * **Monte Carlo simulator** — capa FINAL (no motor principal) que
      simula N partidos usando las λ_h, λ_a calibradas, y deriva
      probabilidades empíricas para mercados extras:
        · Total Corners O/U (líneas comunes)
        · Team Total Corners Over X (por equipo)
        · Both Teams Get ≥ X Corners (BTGC)
        · First Half Corners (proxy ~ 40% del total)

Diseño:
    * Puramente numpy (sin scipy/sklearn).
    * NO toca el motor principal — son refinamientos opcionales
      activables vía feature flag.
    * Compatible con el endpoint existente (`/corner-engine/predict`)
      que recibe ``use_skellam`` y se podrá extender con
      ``use_ensemble`` y ``include_monte_carlo``.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

import numpy as np

from .corner_backtest import calibrate_diff_model, calibrate_most_corners_sigmoid
from .corner_diff_model import compute_expected_corner_diff
from .corner_most_model import predict_most_corners
from .skellam_corner_model import (
    calibrate_skellam_lambdas,
    predict_skellam_corner_diff,
    skellam_most_corners,
)


# ============================================================
# A) Efectos jerárquicos por liga (partial pooling)
# ============================================================

def calibrate_hierarchical_diff_model(
    train_rows: list[dict],
    *,
    pooling_strength: float = 0.5,
) -> dict[str, dict[str, float]]:
    """Calibra coeficientes β del corner_diff_model por liga, con
    **partial pooling** hacia el β global.

    Algoritmo:
      1. Calibrar β_global con TODO el train set.
      2. Para cada liga, calibrar β_local con su subset (si tiene >= 50
         partidos; si no, devolver el global).
      3. Mezclar: β_liga = (1-w)*β_local + w*β_global, donde w depende
         del tamaño relativo de la liga.

    Cuanto mayor `pooling_strength`, más se encoge β_liga hacia β_global.
    Por defecto 0.5 → mezcla 50/50, segura para ligas con muestra
    moderada (~600 partidos / temporada).

    Returns:
        dict {liga: coeficientes}, incluyendo una entrada "_global"
        con el β global para uso como fallback.
    """
    beta_global = calibrate_diff_model(train_rows)
    out = {"_global": beta_global}

    rows_by_league: dict[str, list[dict]] = defaultdict(list)
    for r in train_rows:
        lg = r.get("league")
        if lg:
            rows_by_league[lg].append(r)

    for lg, rows in rows_by_league.items():
        if len(rows) < 50:
            out[lg] = dict(beta_global)
            continue
        beta_local = calibrate_diff_model(rows)
        # Partial pooling
        beta_mix = {}
        for k, v_global in beta_global.items():
            v_local = beta_local.get(k, v_global)
            # weight global más cuando la muestra es pequeña
            n_eff = len(rows)
            # cuanto más datos, menos pooling (w_global pequeño)
            w_global = pooling_strength * (1.0 / (1.0 + n_eff / 500.0))
            w_global = min(1.0, max(0.0, w_global))
            beta_mix[k] = (1.0 - w_global) * v_local + w_global * v_global
        out[lg] = beta_mix
    return out


def predict_diff_hierarchical(
    context: dict[str, Any],
    hierarchical_coefs: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Predice expected_corner_diff usando el β específico de la liga
    del partido. Si la liga no está calibrada, usa β_global."""
    lg = context.get("league")
    coefs = hierarchical_coefs.get(lg) if lg else None
    if coefs is None:
        coefs = hierarchical_coefs.get("_global")
    return compute_expected_corner_diff(context, coefficients=coefs)


# ============================================================
# B) Ensemble Lineal + Skellam con pesos calibrados
# ============================================================

def calibrate_ensemble_weight(
    val_rows: list[dict],
    linear_coefs: dict[str, float],
    sigmoid_a: float,
    sigmoid_b: float,
    tie_buckets: list[tuple[float, float]],
    skellam_coefs_h: dict[str, float],
    skellam_coefs_a: dict[str, float],
) -> float:
    """Calibra el peso `w` del ensemble:

        P_final(home_most) = w * P_lineal(home_most) + (1-w) * P_skellam(home_most)
        P_final(tie)       = w * P_lineal(tie) + (1-w) * P_skellam(tie)

    Minimiza Brier sobre val_rows haciendo grid search en [0, 1] con
    paso 0.05. Suficiente para esta combinación 2-modelo.

    Returns:
        Peso óptimo w ∈ [0, 1].
    """
    if not val_rows:
        return 0.5

    # Pre-compute predicciones de ambos modelos en val
    lin_probs, sk_probs, actuals = [], [], []
    for r in val_rows:
        hc, ac = r.get("home_corners"), r.get("away_corners")
        if hc is None or ac is None:
            continue
        actual = "home" if hc > ac else ("away" if ac > hc else "tie")
        lp = predict_most_corners(r,
                                    sigmoid_a=sigmoid_a, sigmoid_b=sigmoid_b,
                                    tie_buckets=tie_buckets,
                                    diff_coefficients=linear_coefs)
        sk = predict_skellam_corner_diff(r,
                                            coefs_home=skellam_coefs_h,
                                            coefs_away=skellam_coefs_a)
        sk_most = skellam_most_corners(sk)
        lin_probs.append([lp["home_most_corners_prob"],
                            lp["away_most_corners_prob"],
                            lp["tie_corners_prob"]])
        sk_probs.append([sk_most["home_most_corners_prob"],
                           sk_most["away_most_corners_prob"],
                           sk_most["tie_corners_prob"]])
        actuals.append(actual)

    if not actuals:
        return 0.5
    lin_arr = np.array(lin_probs)
    sk_arr  = np.array(sk_probs)
    Y = np.zeros_like(lin_arr)
    for i, a in enumerate(actuals):
        if a == "home":
            Y[i, 0] = 1.0
        elif a == "away":
            Y[i, 1] = 1.0
        else:
            Y[i, 2] = 1.0

    best_w, best_brier = 0.5, float("inf")
    for w in np.arange(0.0, 1.01, 0.05):
        mix = w * lin_arr + (1.0 - w) * sk_arr
        brier = float(np.mean(np.sum((mix - Y) ** 2, axis=1)))
        if brier < best_brier:
            best_brier = brier
            best_w = float(w)
    return best_w


def predict_ensemble_most_corners(
    context: dict[str, Any],
    *,
    ensemble_weight: float,
    linear_coefs: dict[str, float],
    sigmoid_a: float,
    sigmoid_b: float,
    tie_buckets: list[tuple[float, float]],
    skellam_coefs_h: dict[str, float],
    skellam_coefs_a: dict[str, float],
) -> dict[str, Any]:
    """Predice Most Corners combinando lineal y Skellam con peso fijado."""
    lp = predict_most_corners(context,
                                 sigmoid_a=sigmoid_a, sigmoid_b=sigmoid_b,
                                 tie_buckets=tie_buckets,
                                 diff_coefficients=linear_coefs)
    sk = predict_skellam_corner_diff(context,
                                        coefs_home=skellam_coefs_h,
                                        coefs_away=skellam_coefs_a)
    skm = skellam_most_corners(sk)

    w = float(ensemble_weight)
    p_home = w * lp["home_most_corners_prob"] + (1.0 - w) * skm["home_most_corners_prob"]
    p_away = w * lp["away_most_corners_prob"] + (1.0 - w) * skm["away_most_corners_prob"]
    p_tie  = w * lp["tie_corners_prob"]       + (1.0 - w) * skm["tie_corners_prob"]
    s = p_home + p_away + p_tie
    if s > 0:
        p_home, p_away, p_tie = p_home / s, p_away / s, p_tie / s

    # Reglas NO_BET
    max_side = "home" if p_home > p_away else "away"
    max_prob = max(p_home, p_away)
    recommendation = max_side.upper()
    if max_prob < 0.58:
        recommendation = "NO_BET"

    # Combinar reason codes
    reason_codes = list(lp.get("reason_codes", []))
    for rc in sk.get("reason_codes", []):
        if rc not in reason_codes:
            reason_codes.append(rc)
    reason_codes.append("ENSEMBLE_LINEAR_SKELLAM")

    expected_diff = (w * lp["expected_corner_diff"]
                      + (1.0 - w) * sk["expected_corner_diff"])

    return {
        "home_most_corners_prob": round(p_home, 4),
        "away_most_corners_prob": round(p_away, 4),
        "tie_corners_prob":       round(p_tie, 4),
        "expected_corner_diff":   round(expected_diff, 4),
        "recommended_side":       recommendation,
        "ensemble_weight":        round(w, 4),
        "lambda_h":               sk["lambda_h"],
        "lambda_a":               sk["lambda_a"],
        "reason_codes":           reason_codes,
        "components": {
            "linear":  {
                "home": lp["home_most_corners_prob"],
                "away": lp["away_most_corners_prob"],
                "tie":  lp["tie_corners_prob"],
                "edcd": lp["expected_corner_diff"],
            },
            "skellam": {
                "home": skm["home_most_corners_prob"],
                "away": skm["away_most_corners_prob"],
                "tie":  skm["tie_corners_prob"],
                "edcd": sk["expected_corner_diff"],
            },
        },
    }


# ============================================================
# C) Monte Carlo (capa final para mercados extras)
# ============================================================

def monte_carlo_corner_markets(
    *,
    lambda_h: float,
    lambda_a: float,
    n_simulations: int = 10000,
    seed: Optional[int] = 42,
    total_lines: tuple = (7.5, 8.5, 9.5, 10.5, 11.5, 12.5),
    team_total_lines: tuple = (2.5, 3.5, 4.5, 5.5),
) -> dict[str, Any]:
    """Simula N partidos usando λ_h, λ_a calibradas y deriva
    probabilidades empíricas para mercados extras:

      * Total Corners O/U (líneas comunes)
      * Team Total Corners (Home y Away)
      * Both Teams Get ≥ X corners
      * First Half Corners (proxy: 38% del total, ratio aprendido)

    Uso recomendado: como **capa final** después del modelo Skellam o
    Ensemble — NO como motor principal. Las probabilidades de Most Corners
    deben venir del motor principal (que ya está calibrado y validado);
    aquí solo añadimos mercados extras que serían complicados de derivar
    analíticamente.

    Args:
        lambda_h, lambda_a: rates Poisson calibrados.
        n_simulations: número de partidos a simular.
        seed: semilla numpy RNG.
        total_lines, team_total_lines: líneas a evaluar.

    Returns:
        dict con probabilidades empíricas para cada mercado +
        distribuciones (mean, std, percentiles) del total y diferencial.
    """
    rng = np.random.default_rng(seed)
    home_sim = rng.poisson(lambda_h, size=n_simulations)
    away_sim = rng.poisson(lambda_a, size=n_simulations)
    total_sim = home_sim + away_sim
    diff_sim  = home_sim - away_sim

    # Total Corners O/U
    total_markets = {}
    for line in total_lines:
        p_over = float(np.mean(total_sim > line))
        # En total entero, push se incluye; en .5 no.
        p_push = float(np.mean(total_sim == line)) if line == int(line) else 0.0
        total_markets[f"OVER_{line}"] = {
            "prob_over":  round(p_over, 4),
            "prob_push":  round(p_push, 4),
            "prob_under": round(1.0 - p_over - p_push, 4),
            "fair_odds_over":  round(1.0 / p_over, 4) if p_over > 1e-6 else None,
            "fair_odds_under": round(1.0 / (1.0 - p_over - p_push), 4) if (1.0 - p_over - p_push) > 1e-6 else None,
        }

    # Team Total Corners (Home y Away)
    team_total_markets = {"home": {}, "away": {}}
    for line in team_total_lines:
        ph_over = float(np.mean(home_sim > line))
        pa_over = float(np.mean(away_sim > line))
        team_total_markets["home"][f"OVER_{line}"] = {
            "prob_over":      round(ph_over, 4),
            "fair_odds_over": round(1.0 / ph_over, 4) if ph_over > 1e-6 else None,
        }
        team_total_markets["away"][f"OVER_{line}"] = {
            "prob_over":      round(pa_over, 4),
            "fair_odds_over": round(1.0 / pa_over, 4) if pa_over > 1e-6 else None,
        }

    # Both Teams Get ≥ X corners (BTGC)
    btgc_markets = {}
    for x in (3, 4, 5):
        p = float(np.mean((home_sim >= x) & (away_sim >= x)))
        btgc_markets[f"BOTH_TEAMS_GET_{x}_CORNERS"] = {
            "prob":      round(p, 4),
            "fair_odds": round(1.0 / p, 4) if p > 1e-6 else None,
        }

    # First Half corners (proxy: ~38% del total, distribuído Poisson con λ_FH = λ * 0.38)
    fh_ratio = 0.38
    fh_sim = rng.poisson((lambda_h + lambda_a) * fh_ratio, size=n_simulations)
    first_half_markets = {}
    for line in (3.5, 4.5, 5.5):
        p_over = float(np.mean(fh_sim > line))
        first_half_markets[f"FH_OVER_{line}"] = {
            "prob_over":      round(p_over, 4),
            "fair_odds_over": round(1.0 / p_over, 4) if p_over > 1e-6 else None,
        }

    return {
        "n_simulations": n_simulations,
        "lambda_h": round(lambda_h, 4),
        "lambda_a": round(lambda_a, 4),
        "total_corners_distribution": {
            "mean": float(round(total_sim.mean(), 3)),
            "std":  float(round(total_sim.std(), 3)),
            "p10":  float(np.percentile(total_sim, 10)),
            "p50":  float(np.percentile(total_sim, 50)),
            "p90":  float(np.percentile(total_sim, 90)),
        },
        "diff_distribution": {
            "mean": float(round(diff_sim.mean(), 3)),
            "std":  float(round(diff_sim.std(), 3)),
        },
        "total_markets":       total_markets,
        "team_total_markets":  team_total_markets,
        "both_teams_corners":  btgc_markets,
        "first_half_markets":  first_half_markets,
    }
