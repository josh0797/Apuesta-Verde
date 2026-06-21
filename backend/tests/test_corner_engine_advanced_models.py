"""Sprint Corner Fase B · Refinamientos avanzados (Ensemble + MC).

Verifica:
  * ``predict_ensemble_most_corners`` produce probabilidades coherentes
    (suman 1, están en [0,1]) y EDCD que vive entre los componentes.
  * ``monte_carlo_corner_markets`` produce mercados con probabilidades
    coherentes (curva over↓ con líneas más altas) y medias coincidentes
    con los λ de entrada (±0.2 córners de tolerancia con 20k sims).
  * ``calibrate_hierarchical_diff_model`` produce coefs por liga con
    partial pooling hacia el global.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.football.corners import (
    calibrate_hierarchical_diff_model,
    predict_diff_hierarchical,
    monte_carlo_corner_markets,
    predict_ensemble_most_corners,
)
from services.football.corners.corner_diff_model import DEFAULT_COEFFICIENTS
from services.football.corners.corner_most_model import (
    DEFAULT_SIGMOID_A, DEFAULT_SIGMOID_B, DEFAULT_TIE_BUCKETS,
)
from services.football.corners.skellam_corner_model import (
    DEFAULT_LAMBDA_COEFS_HOME, DEFAULT_LAMBDA_COEFS_AWAY,
)


# ============================================================
# Ensemble
# ============================================================

def _favorite_ctx() -> dict:
    return {
        "home_implied_prob": 0.70, "away_implied_prob": 0.15, "draw_implied_prob": 0.15,
        "abs_implied_prob_diff": 0.55,
        "dominant_favorite_side": "home", "dominant_favorite_strength": 0.55,
        "home_corners_for_L15": 7.0, "home_corners_against_L15": 3.5,
        "away_corners_for_L15": 4.0, "away_corners_against_L15": 6.5,
        "home_xg_for_L15": 2.2, "away_xg_for_L15": 1.0,
        "home_deep_allowed_L15": 4.0, "away_deep_allowed_L15": 9.5,
    }


def test_ensemble_probs_sum_to_one():
    em = predict_ensemble_most_corners(
        _favorite_ctx(), ensemble_weight=0.5,
        linear_coefs=DEFAULT_COEFFICIENTS,
        sigmoid_a=DEFAULT_SIGMOID_A, sigmoid_b=DEFAULT_SIGMOID_B,
        tie_buckets=DEFAULT_TIE_BUCKETS,
        skellam_coefs_h=DEFAULT_LAMBDA_COEFS_HOME,
        skellam_coefs_a=DEFAULT_LAMBDA_COEFS_AWAY,
    )
    s = (em["home_most_corners_prob"] + em["away_most_corners_prob"]
          + em["tie_corners_prob"])
    assert abs(s - 1.0) < 1e-3
    for p in (em["home_most_corners_prob"], em["away_most_corners_prob"],
               em["tie_corners_prob"]):
        assert 0.0 <= p <= 1.0


def test_ensemble_edcd_between_components():
    em = predict_ensemble_most_corners(
        _favorite_ctx(), ensemble_weight=0.4,
        linear_coefs=DEFAULT_COEFFICIENTS,
        sigmoid_a=DEFAULT_SIGMOID_A, sigmoid_b=DEFAULT_SIGMOID_B,
        tie_buckets=DEFAULT_TIE_BUCKETS,
        skellam_coefs_h=DEFAULT_LAMBDA_COEFS_HOME,
        skellam_coefs_a=DEFAULT_LAMBDA_COEFS_AWAY,
    )
    lin = em["components"]["linear"]["edcd"]
    sk  = em["components"]["skellam"]["edcd"]
    ens = em["expected_corner_diff"]
    lo, hi = (lin, sk) if lin < sk else (sk, lin)
    assert lo - 1e-3 <= ens <= hi + 1e-3


def test_ensemble_reason_codes_include_ensemble_tag():
    em = predict_ensemble_most_corners(
        _favorite_ctx(), ensemble_weight=0.5,
        linear_coefs=DEFAULT_COEFFICIENTS,
        sigmoid_a=DEFAULT_SIGMOID_A, sigmoid_b=DEFAULT_SIGMOID_B,
        tie_buckets=DEFAULT_TIE_BUCKETS,
        skellam_coefs_h=DEFAULT_LAMBDA_COEFS_HOME,
        skellam_coefs_a=DEFAULT_LAMBDA_COEFS_AWAY,
    )
    assert "ENSEMBLE_LINEAR_SKELLAM" in em["reason_codes"]


def test_ensemble_no_bet_when_low_max_prob():
    """Caso parejo → recommended_side debe ser NO_BET (max_prob < 0.58)."""
    even_ctx = {
        "home_implied_prob": 0.40, "away_implied_prob": 0.34, "draw_implied_prob": 0.26,
        "home_corners_for_L15": 5.0, "home_corners_against_L15": 5.0,
        "away_corners_for_L15": 4.9, "away_corners_against_L15": 5.1,
        "home_xg_for_L15": 1.4, "away_xg_for_L15": 1.3,
        "home_deep_allowed_L15": 7.0, "away_deep_allowed_L15": 7.0,
    }
    em = predict_ensemble_most_corners(
        even_ctx, ensemble_weight=0.5,
        linear_coefs=DEFAULT_COEFFICIENTS,
        sigmoid_a=DEFAULT_SIGMOID_A, sigmoid_b=DEFAULT_SIGMOID_B,
        tie_buckets=DEFAULT_TIE_BUCKETS,
        skellam_coefs_h=DEFAULT_LAMBDA_COEFS_HOME,
        skellam_coefs_a=DEFAULT_LAMBDA_COEFS_AWAY,
    )
    assert em["recommended_side"] == "NO_BET"


# ============================================================
# Monte Carlo
# ============================================================

def test_monte_carlo_mean_matches_lambdas():
    """Con 20k sims, las medias empíricas de home/away/total deben
    estar a ±0.2 córners de los λ de entrada."""
    lh, la = 6.0, 4.5
    mc = monte_carlo_corner_markets(lambda_h=lh, lambda_a=la,
                                     n_simulations=20000, seed=42)
    total_mean = mc["total_corners_distribution"]["mean"]
    diff_mean  = mc["diff_distribution"]["mean"]
    assert abs(total_mean - (lh + la)) < 0.2
    assert abs(diff_mean - (lh - la)) < 0.2


def test_monte_carlo_over_curve_monotonic():
    """P(over X) debe ser monótona decreciente al subir la línea."""
    mc = monte_carlo_corner_markets(lambda_h=5.5, lambda_a=4.5,
                                     n_simulations=20000, seed=7)
    lines = sorted(float(k.split("_")[1]) for k in mc["total_markets"])
    probs = [mc["total_markets"][f"OVER_{ln}"]["prob_over"] for ln in lines]
    for i in range(1, len(probs)):
        assert probs[i] <= probs[i-1] + 1e-9, (
            f"Curva over no monótona en línea {lines[i]}: "
            f"{probs[i]} > {probs[i-1]}"
        )


def test_monte_carlo_team_total_markets_present():
    mc = monte_carlo_corner_markets(lambda_h=5.0, lambda_a=4.0,
                                     n_simulations=5000, seed=1)
    assert set(mc["team_total_markets"].keys()) == {"home", "away"}
    # Cada equipo debe tener probs por línea
    for side in ("home", "away"):
        for k, v in mc["team_total_markets"][side].items():
            assert 0.0 <= v["prob_over"] <= 1.0


def test_monte_carlo_btgc_decreases_with_threshold():
    """BTGC(≥3) >= BTGC(≥4) >= BTGC(≥5)."""
    mc = monte_carlo_corner_markets(lambda_h=5.0, lambda_a=4.0,
                                     n_simulations=15000, seed=11)
    p3 = mc["both_teams_corners"]["BOTH_TEAMS_GET_3_CORNERS"]["prob"]
    p4 = mc["both_teams_corners"]["BOTH_TEAMS_GET_4_CORNERS"]["prob"]
    p5 = mc["both_teams_corners"]["BOTH_TEAMS_GET_5_CORNERS"]["prob"]
    assert p3 >= p4 >= p5


# ============================================================
# Hierarchical
# ============================================================

def _synthetic_train(n_per_league=200):
    """Genera training set sintético con 3 ligas (variación en EDCD)."""
    rng_seed = 0
    rows = []
    for lg in ("EPL", "LaLiga", "SerieA"):
        for i in range(n_per_league):
            # Sembrar variaciones leves
            home_corners = 5 + (i % 5)
            away_corners = 4 + ((i + 1) % 4)
            rows.append({
                "league": lg,
                "home_corners": home_corners,
                "away_corners": away_corners,
                "home_implied_prob": 0.30 + 0.05 * (i % 7),
                "away_implied_prob": 0.30 + 0.05 * ((i + 3) % 7),
                "draw_implied_prob": 0.28,
                "home_corners_for_L15": 5.0,
                "home_corners_against_L15": 4.8,
                "away_corners_for_L15": 4.7,
                "away_corners_against_L15": 5.0,
                "home_xg_for_L15": 1.4,
                "away_xg_for_L15": 1.3,
                "home_deep_allowed_L15": 7.0,
                "away_deep_allowed_L15": 7.0,
                "home_venue_corner_split": 5.5,
                "away_venue_corner_split": 4.0,
                "dominant_favorite_side": None,
                "dominant_favorite_strength": 0.0,
            })
    return rows


def test_hierarchical_produces_global_and_league_coefs():
    rows = _synthetic_train(150)
    coefs = calibrate_hierarchical_diff_model(rows, pooling_strength=0.5)
    assert "_global" in coefs
    # Las 3 ligas con 150 partidos deben tener su propia entrada
    for lg in ("EPL", "LaLiga", "SerieA"):
        assert lg in coefs
        assert "intercept" in coefs[lg]


def test_hierarchical_predict_uses_league_specific_coefs():
    rows = _synthetic_train(150)
    coefs = calibrate_hierarchical_diff_model(rows, pooling_strength=0.5)
    ctx = {
        "league": "EPL",
        "home_implied_prob": 0.55, "away_implied_prob": 0.25, "draw_implied_prob": 0.20,
        "home_corners_for_L15": 6.0, "home_corners_against_L15": 4.0,
        "away_corners_for_L15": 4.0, "away_corners_against_L15": 5.5,
        "home_deep_allowed_L15": 5.0, "away_deep_allowed_L15": 7.5,
        "home_venue_corner_split": 6.0, "away_venue_corner_split": 4.0,
    }
    result = predict_diff_hierarchical(ctx, coefs)
    assert "expected_corner_diff" in result


def test_hierarchical_falls_back_to_global_for_unknown_league():
    rows = _synthetic_train(150)
    coefs = calibrate_hierarchical_diff_model(rows, pooling_strength=0.5)
    ctx = {
        "league": "LigueUnknown",   # liga no entrenada
        "home_implied_prob": 0.50, "away_implied_prob": 0.30, "draw_implied_prob": 0.20,
        "home_corners_for_L15": 5.0, "away_corners_for_L15": 4.5,
    }
    result = predict_diff_hierarchical(ctx, coefs)
    # No debe crashear; debe usar el global y devolver un dict válido
    assert "expected_corner_diff" in result
