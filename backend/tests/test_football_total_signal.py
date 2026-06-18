"""Sprint D10-A · tests del módulo puro football_total_signal.

Cubre las reglas críticas del spec:
  * Tres clamps: ±0.65 normal, ±0.30 muestra limitada, ±0.45 sin xG.
  * Redistribución proporcional de SOURCE_WEIGHTS cuando falta una
    fuente (sin reemplazar por 0.0).
  * `adjusted_lambda_home + adjusted_lambda_away ≈ adjusted_expected_goals`.
  * Distribución proporcional a las lambdas base (no 50/50 forzado).
  * La línea no modifica las proyecciones (anti-circularidad).
  * Status states (BASE_MODEL_ONLY, READY).
  * Variabilidad bandas en fútbol (STABLE/MODERATE/VOLATILE).
  * Score de influencia clamped a ±10 y confidence_delta a ±5.
  * H2H antiguo (>7 años) excluido.
  * Amistoso fuera de contexto → multiplier 0.45.
"""
from __future__ import annotations

import math
import pytest

from services.football_total_signal import (
    calculate_weighted_h2h_goals,
    calculate_weighted_recent_form,
    calculate_weighted_xg_total,
    calculate_contextual_goal_mean,
    calculate_context_reliability,
    calculate_goal_variability,
    calculate_football_total_influence_score,
    calculate_football_total_signal,
    CLAMP_NORMAL,
    CLAMP_LIMITED_SAMPLE,
    CLAMP_NO_XG,
    SOURCE_WEIGHTS,
    SHRINKAGE_K_FOOTBALL,
    INFLUENCE_SCORE_CAP,
    CONFIDENCE_DELTA_CAP,
)


# ─── Constantes spec-aligned ───────────────────────────────────────────
def test_source_weights_aligned_with_spec():
    assert SOURCE_WEIGHTS["H2H"]         == 0.25
    assert SOURCE_WEIGHTS["RECENT_FORM"] == 0.40
    assert SOURCE_WEIGHTS["XG"]          == 0.35
    assert abs(sum(SOURCE_WEIGHTS.values()) - 1.0) < 1e-9


def test_clamps_aligned_with_spec():
    assert CLAMP_NORMAL         == 0.65
    assert CLAMP_LIMITED_SAMPLE == 0.30
    assert CLAMP_NO_XG          == 0.45


def test_shrinkage_k_aligned_with_spec():
    assert SHRINKAGE_K_FOOTBALL == 5


# ─── calculate_weighted_h2h_goals ──────────────────────────────────────
def test_h2h_filters_non_final():
    out = calculate_weighted_h2h_goals([
        {"home_goals": 2, "away_goals": 1, "status": "POSTPONED"},
        {"home_goals": 3, "away_goals": 0, "status": "FINAL"},
    ])
    # Solo el FINAL.
    assert out["n_valid"] == 1
    assert out["value"] == 3.0
    assert out["samples_excluded"] == 1


def test_h2h_excludes_games_older_than_7_years():
    out = calculate_weighted_h2h_goals([
        {"home_goals": 5, "away_goals": 5, "age_days": 365 * 8},
        {"home_goals": 2, "away_goals": 1, "age_days": 30},
    ])
    assert out["n_valid"] == 1
    assert out["value"] == 3.0


def test_h2h_friendly_outside_context_gets_strong_penalty():
    """Amistoso H2H cuando el partido actual NO es amistoso → multiplier 0.45."""
    out = calculate_weighted_h2h_goals(
        [
            {"home_goals": 6, "away_goals": 4, "is_friendly": True},
            {"home_goals": 2, "away_goals": 1, "is_friendly": False},
        ],
        current_match_context={"is_friendly": False},
    )
    # Pesos: amistoso 1.0*0.45=0.45; oficial 0.75*1.0=0.75. Total denom=1.2.
    # Weighted = (10*0.45 + 3*0.75) / 1.2 = (4.5+2.25)/1.2 = 5.625
    expected = (10 * 0.45 + 3 * 0.75) / 1.2
    assert math.isclose(out["value"], expected, abs_tol=1e-3)


def test_h2h_uses_total_goals_when_present():
    out = calculate_weighted_h2h_goals([
        {"total_goals": 4, "status": "FINAL"},
        {"home_goals": 2, "away_goals": 1, "status": "FINAL"},  # → 3
    ])
    assert out["n_valid"] == 2


def test_h2h_negative_total_excluded():
    out = calculate_weighted_h2h_goals([
        {"total_goals": -2, "status": "FINAL"},
    ])
    assert out["n_valid"] == 0


def test_h2h_empty_input():
    out = calculate_weighted_h2h_goals(None)
    assert out["value"] is None
    assert "NO_H2H_INPUT" in out["reasons"]


# ─── calculate_weighted_recent_form ────────────────────────────────────
def test_recent_form_combines_both_sides_with_average():
    """No debe sumar las dos medias — debe promediarlas (sec 5 del spec)."""
    out = calculate_weighted_recent_form(
        home_recent_matches=[
            {"goals_scored": 2, "goals_conceded": 1, "status": "FINAL"},
            {"goals_scored": 1, "goals_conceded": 0, "status": "FINAL"},
        ],
        away_recent_matches=[
            {"goals_scored": 1, "goals_conceded": 2, "status": "FINAL"},
            {"goals_scored": 0, "goals_conceded": 0, "status": "FINAL"},
        ],
    )
    # home: ([(3)*1.0 + (1)*0.75]) / 1.75 = 3.75/1.75 ≈ 2.143
    # away: ([(3)*1.0 + (0)*0.75]) / 1.75 = 3/1.75 ≈ 1.714
    # average ≈ 1.929
    expected = ((3 * 1.0 + 1 * 0.75) / 1.75 + (3 * 1.0 + 0 * 0.75) / 1.75) / 2.0
    assert math.isclose(out["value"], expected, abs_tol=1e-3)


def test_recent_form_opponent_strength_adjustment():
    """Goleada contra rival muy débil debe atenuarse (×0.85)."""
    out_weak = calculate_weighted_recent_form(
        home_recent_matches=[
            {"goals_scored": 5, "goals_conceded": 0, "opponent_strength": "very_weak"},
        ],
        away_recent_matches=[
            {"goals_scored": 2, "goals_conceded": 1, "opponent_strength": "average"},
        ],
    )
    out_avg = calculate_weighted_recent_form(
        home_recent_matches=[
            {"goals_scored": 5, "goals_conceded": 0, "opponent_strength": "average"},
        ],
        away_recent_matches=[
            {"goals_scored": 2, "goals_conceded": 1, "opponent_strength": "average"},
        ],
    )
    # Con "very_weak" la home obs se atenúa (5*0.85=4.25), por tanto la media baja.
    assert out_weak["value"] < out_avg["value"]


def test_recent_form_one_side_only():
    """Si sólo hay un lado, devuelve ese lado con reason code."""
    out = calculate_weighted_recent_form(
        home_recent_matches=[
            {"goals_scored": 2, "goals_conceded": 1},
        ],
        away_recent_matches=None,
    )
    assert out["value"] == 3.0
    assert "RECENT_FORM_ONE_SIDE_ONLY" in out["reasons"]


# ─── calculate_weighted_xg_total ───────────────────────────────────────
def test_xg_total_combines_l5_and_l15():
    """L5 peso 0.65, L15 peso 0.35."""
    home = {"xg_for_l5": 1.4, "xg_against_l5": 0.9,
              "xg_for_l15": 1.3, "xg_against_l15": 1.0,
              "matches_available": 10}
    away = {"xg_for_l5": 1.0, "xg_against_l5": 1.2,
              "xg_for_l15": 1.1, "xg_against_l15": 1.3,
              "matches_available": 10}
    out = calculate_weighted_xg_total(home, away)
    # L5: home_exp = (1.4+1.2)/2 = 1.3; away_exp = (1.0+0.9)/2 = 0.95 → total 2.25
    # L15: home_exp = (1.3+1.3)/2 = 1.3; away_exp = (1.1+1.0)/2 = 1.05 → total 2.35
    # combined = 2.25*0.65 + 2.35*0.35 = 1.4625 + 0.8225 = 2.285
    expected = 2.25 * 0.65 + 2.35 * 0.35
    assert math.isclose(out["value"], expected, abs_tol=1e-3)


def test_xg_total_unavailable_returns_reason_code():
    out = calculate_weighted_xg_total(None, None)
    assert out["value"] is None
    assert "XG_CONTEXT_UNAVAILABLE" in out["reasons"]


# ─── calculate_contextual_goal_mean ────────────────────────────────────
def test_context_mean_renormalises_when_xg_missing():
    """Sin xG, los pesos 0.25 (H2H) y 0.40 (recent) deben renormalizarse
    a 0.25/0.65 y 0.40/0.65."""
    out = calculate_contextual_goal_mean(
        weighted_h2h=3.0,
        weighted_recent=2.0,
        weighted_xg=None,
    )
    assert out["available_sources"] == ["H2H", "RECENT_FORM"]
    expected_h2h = 0.25 / 0.65
    expected_recent = 0.40 / 0.65
    assert math.isclose(out["weights_applied"]["H2H"], expected_h2h, abs_tol=1e-4)
    assert math.isclose(out["weights_applied"]["RECENT_FORM"], expected_recent, abs_tol=1e-4)
    expected_val = 3.0 * expected_h2h + 2.0 * expected_recent
    assert math.isclose(out["value"], expected_val, abs_tol=1e-3)


def test_context_mean_no_sources_returns_none():
    out = calculate_contextual_goal_mean(None, None, None)
    assert out["value"] is None
    assert out["available_sources"] == []


def test_context_mean_does_not_replace_missing_with_zero():
    """Verifica que NO se sustituyen None por 0 (regla del spec sec 8)."""
    out_with_xg_zero = calculate_contextual_goal_mean(3.0, 2.0, 0.0)
    out_with_xg_none = calculate_contextual_goal_mean(3.0, 2.0, None)
    # Si se hubiera sustituido None por 0, los dos resultados serían iguales.
    # Con None se renormaliza a (H2H+RECENT) → valor más alto.
    assert out_with_xg_zero["value"] < out_with_xg_none["value"]


# ─── calculate_context_reliability ─────────────────────────────────────
def test_reliability_grows_with_sample():
    out_small = calculate_context_reliability(h2h_n=1, home_recent_n=1,
                                                  away_recent_n=1, xg_matches_available=2)
    out_large = calculate_context_reliability(h2h_n=4, home_recent_n=5,
                                                  away_recent_n=5, xg_matches_available=10)
    assert out_large["reliability"] > out_small["reliability"]
    assert out_large["quality"] == "STRONG"


def test_reliability_quality_very_limited():
    out = calculate_context_reliability(h2h_n=0, home_recent_n=1,
                                          away_recent_n=1, xg_matches_available=0)
    assert out["quality"] == "VERY_LIMITED"
    assert out["effective_n"] < 3


# ─── calculate_goal_variability ────────────────────────────────────────
def test_variability_stable_band():
    out = calculate_goal_variability([2, 2, 3, 2, 2])
    # cv = std/mean ≈ 0.39/2.2 ≈ 0.177 < 0.35 → STABLE
    assert out["class"] == "STABLE"


def test_variability_volatile_band():
    out = calculate_goal_variability([0, 5, 1, 6, 0])
    # cv muy alta → VOLATILE
    assert out["class"] == "VOLATILE"


def test_variability_insufficient_below_3_samples():
    out = calculate_goal_variability([3, 4])
    assert out["class"] == "INSUFFICIENT_SAMPLE"


# ─── calculate_football_total_influence_score ──────────────────────────
def test_influence_score_capped_to_plus_minus_10():
    out = calculate_football_total_influence_score(
        total_edge_goals=2.0,            # edge_comp clamp = +4
        reliability=1.0,                  # rel_comp = +2
        cv_class="STABLE",                # var_comp = clamp(2*0.8*4, ±2) = +2
        has_xg=True,                      # xg_comp = +1
        has_lineups=True,                 # lu_comp = +0.5
        competition_match=True,           # comp_comp = 0
    )
    # 4 + 2 + 2 + 1 + 0.5 = 9.5 → bajo cap ±10.
    assert abs(out["score"]) <= INFLUENCE_SCORE_CAP + 1e-9
    assert out["score"] == 9.5
    assert out["confidence_delta"] == 9.5 * 0.5


def test_influence_score_confidence_delta_capped_to_5():
    """confidence_delta = score * 0.5; capped at ±5 cuando score satura ±10."""
    # Caso normal: score 9.5 → confidence 4.75 (proporcional, no saturado).
    out = calculate_football_total_influence_score(
        total_edge_goals=5.0,
        reliability=1.0,
        cv_class="STABLE",
        has_xg=True,
        has_lineups=True,
    )
    assert out["confidence_delta"] == pytest.approx(out["score"] * 0.5, abs=1e-3)
    assert abs(out["confidence_delta"]) <= CONFIDENCE_DELTA_CAP + 1e-9


def test_influence_score_negative_edge_drives_negative_score():
    out = calculate_football_total_influence_score(
        total_edge_goals=-1.5,
        reliability=0.7,
        cv_class="STABLE",
        has_xg=True,
    )
    assert out["score"] < 0


# ═════════════════════════════════════════════════════════════════════
# calculate_football_total_signal — pruebas integradas
# ═════════════════════════════════════════════════════════════════════
def _full_context_inputs(*, line=2.5, has_xg=True, n_samples=5):
    """Helper: arma inputs reales con todas las fuentes disponibles."""
    h2h = [
        {"home_goals": 2, "away_goals": 1, "status": "FINAL"},
        {"home_goals": 1, "away_goals": 1, "status": "FINAL"},
        {"home_goals": 0, "away_goals": 2, "status": "FINAL"},
    ][:n_samples]
    home_recent = [
        {"goals_scored": 2, "goals_conceded": 1},
        {"goals_scored": 1, "goals_conceded": 0},
        {"goals_scored": 2, "goals_conceded": 2},
        {"goals_scored": 0, "goals_conceded": 1},
        {"goals_scored": 1, "goals_conceded": 1},
    ]
    away_recent = [
        {"goals_scored": 1, "goals_conceded": 2},
        {"goals_scored": 0, "goals_conceded": 1},
        {"goals_scored": 2, "goals_conceded": 1},
        {"goals_scored": 1, "goals_conceded": 1},
        {"goals_scored": 0, "goals_conceded": 0},
    ]
    home_xg = {
        "xg_for_l5": 1.4, "xg_against_l5": 0.9,
        "xg_for_l15": 1.3, "xg_against_l15": 1.0,
        "matches_available": 15,
    } if has_xg else None
    away_xg = {
        "xg_for_l5": 1.0, "xg_against_l5": 1.2,
        "xg_for_l15": 1.1, "xg_against_l15": 1.3,
        "matches_available": 15,
    } if has_xg else None
    return dict(
        base_expected_goals=2.5,
        base_lambda_home=1.55,
        base_lambda_away=0.95,
        market_total=line,
        recent_h2h_games=h2h,
        home_recent_matches=home_recent,
        away_recent_matches=away_recent,
        home_xg_recent=home_xg,
        away_xg_recent=away_xg,
    )


def test_signal_status_ready_with_full_context():
    out = calculate_football_total_signal(**_full_context_inputs())
    assert out["status"] == "FOOTBALL_TOTAL_SIGNAL_READY"
    assert out["adjustment"]["adjusted_expected_goals"] is not None


def test_signal_status_base_model_only_when_no_context():
    out = calculate_football_total_signal(
        base_expected_goals=2.4, base_lambda_home=1.5, base_lambda_away=0.9,
        market_total=2.5,
    )
    assert out["status"] == "BASE_MODEL_ONLY"
    assert "INSUFFICIENT_CONTEXT_SAMPLE" in out["reason_codes"]
    assert out["adjustment"]["applied_adjustment"] == 0.0
    # Las lambdas base deben quedar intactas.
    assert out["adjustment"]["adjusted_lambda_home"] == 1.5
    assert out["adjustment"]["adjusted_lambda_away"] == 0.9


# ── Anti-circularidad: línea NO modifica proyección ─────────────────────
def test_changing_only_the_line_does_not_change_projection():
    base = _full_context_inputs(line=2.5)
    out_a = calculate_football_total_signal(**base)
    base["market_total"] = 3.5
    out_b = calculate_football_total_signal(**base)
    # Las cuotas / proyecciones no cambian — sólo el edge.
    assert out_a["adjustment"]["adjusted_expected_goals"] == out_b["adjustment"]["adjusted_expected_goals"]
    assert out_a["adjustment"]["adjusted_lambda_home"] == out_b["adjustment"]["adjusted_lambda_home"]
    assert out_a["adjustment"]["adjusted_lambda_away"] == out_b["adjustment"]["adjusted_lambda_away"]
    assert out_a["adjustment"]["applied_adjustment"] == out_b["adjustment"]["applied_adjustment"]
    # Edge sí cambia.
    assert out_a["market_context"]["total_edge_goals"] != out_b["market_context"]["total_edge_goals"]


# ── Triple clamp ────────────────────────────────────────────────────────
def test_clamp_normal_applies_when_full_data():
    out = calculate_football_total_signal(**_full_context_inputs())
    assert out["adjustment"]["clamp_used"] == CLAMP_NORMAL


def test_clamp_no_xg_applies_when_xg_missing():
    inputs = _full_context_inputs(has_xg=False)
    out = calculate_football_total_signal(**inputs)
    # Sin xG el clamp debe ser 0.45.
    assert out["adjustment"]["clamp_used"] == CLAMP_NO_XG
    assert "XG_CONTEXT_UNAVAILABLE" in out["reason_codes"]


def test_clamp_limited_sample_applies_with_tiny_data():
    """Con muy pocos partidos debería entrar el clamp 0.30."""
    out = calculate_football_total_signal(
        base_expected_goals=2.5, base_lambda_home=1.5, base_lambda_away=1.0,
        market_total=2.5,
        recent_h2h_games=[{"home_goals": 4, "away_goals": 3, "status": "FINAL"}],
        home_recent_matches=[{"goals_scored": 4, "goals_conceded": 4}],
        away_recent_matches=[{"goals_scored": 5, "goals_conceded": 3}],
    )
    # h2h_n=1, home_recent_n=1, away_recent_n=1, sin xG.
    # effective_n = 1*0.5 + 1*1.0 + 0 = 1.5 → quality=VERY_LIMITED.
    assert out["sample"]["quality"] == "VERY_LIMITED"
    assert out["adjustment"]["clamp_used"] == CLAMP_LIMITED_SAMPLE


# ── Distribución lambdas ────────────────────────────────────────────────
def test_lambda_distribution_proportional_to_base_when_no_contextual_xg():
    """Sin contextual_home_xg/away_xg, el adjustment se reparte
    proporcionalmente a las lambdas base. NO 50/50."""
    out = calculate_football_total_signal(
        base_expected_goals=2.5, base_lambda_home=2.0, base_lambda_away=0.5,
        market_total=2.5,
        recent_h2h_games=[
            {"home_goals": 4, "away_goals": 4, "status": "FINAL"},
            {"home_goals": 3, "away_goals": 2, "status": "FINAL"},
            {"home_goals": 3, "away_goals": 3, "status": "FINAL"},
        ],
        home_recent_matches=[
            {"goals_scored": 3, "goals_conceded": 2},
            {"goals_scored": 4, "goals_conceded": 3},
            {"goals_scored": 2, "goals_conceded": 1},
        ],
        away_recent_matches=[
            {"goals_scored": 1, "goals_conceded": 2},
            {"goals_scored": 0, "goals_conceded": 3},
            {"goals_scored": 1, "goals_conceded": 2},
        ],
    )
    adj_h = out["adjustment"]["adjusted_lambda_home"]
    adj_a = out["adjustment"]["adjusted_lambda_away"]
    # adj_h debe estar más cerca de 2.0 (mayor share) que adj_a de 0.5.
    # Si el adjustment fue +X, home_share debería absorber 80% (2/2.5).
    delta_h = adj_h - 2.0
    delta_a = adj_a - 0.5
    # delta_h debe ser ≈ 0.8 * applied_adjustment; delta_a ≈ 0.2 * applied_adjustment
    applied = out["adjustment"]["applied_adjustment"]
    if abs(applied) > 0.01:
        ratio = delta_h / applied
        assert math.isclose(ratio, 0.80, abs_tol=0.05)


def test_lambdas_consistency_sums_to_adjusted_expected_goals():
    out = calculate_football_total_signal(**_full_context_inputs())
    adj_h = out["adjustment"]["adjusted_lambda_home"]
    adj_a = out["adjustment"]["adjusted_lambda_away"]
    adj_eg = out["adjustment"]["adjusted_expected_goals"]
    assert math.isclose(adj_h + adj_a, adj_eg, abs_tol=0.05)  # margen por max(0.05, …)


# ── Reason codes ────────────────────────────────────────────────────────
def test_reason_code_expected_goals_below_market():
    out = calculate_football_total_signal(
        base_expected_goals=2.0, base_lambda_home=1.2, base_lambda_away=0.8,
        market_total=3.5,
        recent_h2h_games=[
            {"home_goals": 1, "away_goals": 0, "status": "FINAL"},
            {"home_goals": 0, "away_goals": 1, "status": "FINAL"},
            {"home_goals": 1, "away_goals": 1, "status": "FINAL"},
        ],
        home_recent_matches=[
            {"goals_scored": 1, "goals_conceded": 0},
            {"goals_scored": 0, "goals_conceded": 1},
            {"goals_scored": 1, "goals_conceded": 1},
        ],
        away_recent_matches=[
            {"goals_scored": 0, "goals_conceded": 1},
            {"goals_scored": 1, "goals_conceded": 1},
            {"goals_scored": 0, "goals_conceded": 2},
        ],
        home_xg_recent={"xg_for_l5": 0.8, "xg_against_l5": 0.7,
                          "xg_for_l15": 0.9, "xg_against_l15": 0.8,
                          "matches_available": 15},
        away_xg_recent={"xg_for_l5": 0.7, "xg_against_l5": 0.9,
                          "xg_for_l15": 0.8, "xg_against_l15": 0.9,
                          "matches_available": 15},
    )
    assert "EXPECTED_GOALS_BELOW_MARKET" in out["reason_codes"]
    assert out["market_context"]["lean"] in ("STRONG_UNDER", "MODERATE_UNDER")


def test_observe_only_flag_present():
    out = calculate_football_total_signal(**_full_context_inputs())
    assert out["observe_only"] is True


# ── Influence score ≠ 0 cuando hay edge ─────────────────────────────────
def test_influence_score_in_range_minus_10_to_plus_10():
    out = calculate_football_total_signal(**_full_context_inputs())
    sc = out["market_context"]["influence_score"]
    assert -10.0 <= sc <= 10.0


def test_signal_does_not_depend_on_selection_string():
    """selection no debe alterar proyección/lambdas/score (es metadata)."""
    base = _full_context_inputs()
    out_over  = calculate_football_total_signal(selection="OVER", **base)
    out_under = calculate_football_total_signal(selection="UNDER", **base)
    assert out_over["adjustment"] == out_under["adjustment"]
    assert out_over["market_context"]["influence_score"] == out_under["market_context"]["influence_score"]
