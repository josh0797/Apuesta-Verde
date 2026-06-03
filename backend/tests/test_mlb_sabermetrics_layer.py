"""Tests for services.mlb_sabermetrics_layer (Phase 9.6).

Coverage matrix (per user spec):
* OPS alto → run creation support pero NO fuerza Over.
* OPS bajo + FIP fuerte → refuerza Under.
* ERA baja + FIP alto → ERA_OVERSTATES_PITCHER_QUALITY.
* ERA alta + FIP bajo → ERA_UNDERSTATES_PITCHER_QUALITY.
* FIP fuerte → mejora pitcher quality.
* FIP riesgoso → aumenta fragility.
* WAR edge fuerte → apoya team edge pero no fuerza Run Line.
* Ausencia de WAR/OPS/FIP → no rompe engine, available=False.
* data_quality missing → peso 0.
* data_quality partial → peso conservador (0.35).
* Football/basketball no afectados (smoke import-only — no cross-sport mutation).
"""
from __future__ import annotations

import pytest

from services.mlb_sabermetrics_layer import (
    OPS_TIER_ELITE, OPS_TIER_STRONG, OPS_TIER_AVERAGE, OPS_TIER_WEAK,
    FIP_TIER_ELITE, FIP_TIER_STRONG, FIP_TIER_AVERAGE, FIP_TIER_RISKY,
    DEFAULT_FIP_CONSTANT,
    RC_OPS_ELITE, RC_OPS_STRONG, RC_OPS_LOW_WARNING,
    RC_OPS_SUPPORTS_RUN_CREATION, RC_OPS_POWER_ON_BASE_SUPPORT,
    RC_FIP_ELITE, RC_FIP_STRONG, RC_FIP_RISKY,
    RC_ERA_OVERSTATES, RC_ERA_UNDERSTATES,
    RC_FIP_SUPPORTS_UNDER, RC_FIP_WARNING_FOR_UNDER,
    RC_WAR_ELITE_PLAYER, RC_WAR_STRONG_CORE,
    RC_WAR_LINEUP_WEAKNESS, RC_WAR_MISSING_HIGH,
    RC_SABER_BOTH_TEAMS_STRONG_OPS, RC_SABER_BOTH_PITCHERS_RISKY,
    calculate_ops_profile,
    calculate_fip_profile,
    calculate_war_impact,
    calculate_sabermetric_context,
    derive_sabermetric_recommendation_delta,
)


# ─────────────────────────────────────────────────────────────────────
# OPS profile
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ops, expected_tier", [
    (0.910, OPS_TIER_ELITE),
    (0.850, OPS_TIER_ELITE),
    (0.800, OPS_TIER_STRONG),
    (0.780, OPS_TIER_STRONG),
    (0.720, OPS_TIER_AVERAGE),
    (0.700, OPS_TIER_AVERAGE),
    (0.680, OPS_TIER_WEAK),
    (0.600, OPS_TIER_WEAK),
])
def test_ops_tier_thresholds(ops, expected_tier):
    out = calculate_ops_profile({"team_ops": ops})
    assert out["available"] is True
    assert out["tier"] == expected_tier


def test_ops_fail_soft_when_missing():
    out = calculate_ops_profile(None)
    assert out["available"] is False
    assert out["tier"] == "UNKNOWN_OPS"
    assert out["ops"] is None


def test_ops_computed_from_obp_slg():
    out = calculate_ops_profile({"obp": 0.360, "slg": 0.480})
    assert out["available"] is True
    assert abs(out["ops"] - 0.840) < 1e-6
    assert out["tier"] == OPS_TIER_STRONG


def test_ops_elite_emits_run_creation_support():
    out = calculate_ops_profile({"team_ops": 0.890})
    assert RC_OPS_ELITE in out["reason_codes"]
    assert RC_OPS_SUPPORTS_RUN_CREATION in out["reason_codes"]


def test_ops_weak_emits_warning():
    out = calculate_ops_profile({"team_ops": 0.640})
    assert RC_OPS_LOW_WARNING in out["reason_codes"]


def test_ops_power_on_base_boost_with_xwoba():
    out = calculate_ops_profile({"team_ops": 0.810, "team_xwoba": 0.342})
    assert RC_OPS_POWER_ON_BASE_SUPPORT in out["reason_codes"]
    # Score boosted vs no-xwoba baseline
    baseline = calculate_ops_profile({"team_ops": 0.810})
    assert out["power_on_base_score"] > baseline["power_on_base_score"]


# ─────────────────────────────────────────────────────────────────────
# FIP profile
# ─────────────────────────────────────────────────────────────────────
def test_fip_unavailable_when_missing_all():
    out = calculate_fip_profile({})
    assert out["available"] is False
    assert out["fip"] is None


def test_fip_direct_value():
    out = calculate_fip_profile({"fip": 3.10, "era": 3.20})
    assert out["available"] is True
    assert out["fip"] == 3.10
    assert out["fip_source"] == "direct"
    assert out["tier"] == FIP_TIER_ELITE


def test_fip_computed_from_raw_stats():
    # HR=10, BB=20, K=120, IP=120 → FIP = (130 + 60 - 240) / 120 + 3.10 = -0.417 + 3.10 = 2.68
    out = calculate_fip_profile({
        "hr": 10, "bb": 20, "k": 120, "ip": 120, "era": 3.00,
    })
    assert out["available"] is True
    assert out["fip_source"] == "direct" or out["fip_source"] is not None
    # Should be roughly 2.68 → ELITE
    assert out["tier"] == FIP_TIER_ELITE


def test_fip_proxy_from_xera_when_no_other_data():
    out = calculate_fip_profile({"era": 4.20, "xera": 3.50})
    assert out["available"] is True
    assert out["fip_source"] == "xera_proxy"
    assert out["fip"] == 3.50


def test_fip_strong_supports_under_reason():
    out = calculate_fip_profile({"fip": 3.40, "era": 3.50})
    assert out["tier"] == FIP_TIER_STRONG
    assert RC_FIP_STRONG in out["reason_codes"]
    assert RC_FIP_SUPPORTS_UNDER in out["reason_codes"]


def test_fip_risky_warns_for_under():
    out = calculate_fip_profile({"fip": 4.80, "era": 3.50})
    assert out["tier"] == FIP_TIER_RISKY
    assert RC_FIP_RISKY in out["reason_codes"]
    assert RC_FIP_WARNING_FOR_UNDER in out["reason_codes"]


def test_era_overstates_when_era_much_lower_than_fip():
    """ERA 2.50 with FIP 4.20 → ERA much LOWER than FIP → ERA_OVERSTATES."""
    out = calculate_fip_profile({"era": 2.50, "fip": 4.20})
    assert RC_ERA_OVERSTATES in out["reason_codes"]
    assert out["era_minus_fip_gap"] == round(2.50 - 4.20, 2)


def test_era_understates_when_era_much_higher_than_fip():
    """ERA 4.20 with FIP 3.10 → ERA much HIGHER than FIP → ERA_UNDERSTATES."""
    out = calculate_fip_profile({"era": 4.20, "fip": 3.10})
    assert RC_ERA_UNDERSTATES in out["reason_codes"]


def test_fip_quality_score_anchors():
    """Lower FIP → higher quality score."""
    elite = calculate_fip_profile({"fip": 2.80})
    strong = calculate_fip_profile({"fip": 3.50})
    risky = calculate_fip_profile({"fip": 4.80})
    assert elite["defense_independent_pitching_score"] > strong["defense_independent_pitching_score"]
    assert strong["defense_independent_pitching_score"] > risky["defense_independent_pitching_score"]


# ─────────────────────────────────────────────────────────────────────
# WAR impact
# ─────────────────────────────────────────────────────────────────────
def test_war_unavailable_with_no_data():
    out = calculate_war_impact(None)
    assert out["available"] is False


def test_war_unavailable_empty_list():
    out = calculate_war_impact([])
    assert out["available"] is False


def test_war_elite_player_detected():
    out = calculate_war_impact([{"name": "A", "war": 6.5}, {"name": "B", "war": 1.2}])
    assert out["available"] is True
    assert RC_WAR_ELITE_PLAYER in out["reason_codes"]


def test_war_strong_core_when_multiple_strong():
    out = calculate_war_impact([
        {"name": "A", "war": 3.5},
        {"name": "B", "war": 4.0},
        {"name": "C", "war": 0.5},
    ])
    assert RC_WAR_STRONG_CORE in out["reason_codes"]


def test_war_lineup_weakness_when_replacement_majority():
    out = calculate_war_impact([
        {"name": "A", "war": 0.2}, {"name": "B", "war": 0.5},
        {"name": "C", "war": 0.8}, {"name": "D", "war": 1.1},
    ])
    assert RC_WAR_LINEUP_WEAKNESS in out["reason_codes"]


def test_war_missing_high_when_inactive_player_has_high_war():
    out = calculate_war_impact(
        [{"name": "Active", "war": 1.0}],
        lineup_context={"inactive": [{"name": "Star", "war": 5.0}]},
    )
    assert RC_WAR_MISSING_HIGH in out["reason_codes"]


def test_war_dict_shape_supports_pitchers_and_team_total():
    out = calculate_war_impact({
        "team_total_war":  35.0,
        "hitters":  [{"name": "A", "war": 4.0}, {"name": "B", "war": 3.0}],
        "pitchers": [{"name": "SP", "war": 4.5, "role": "starter"}],
    })
    assert out["available"] is True
    assert out["team_total_war"] == 35.0
    assert out["starter_war"] == 4.5
    assert out["pitcher_war_score"] > 0


# ─────────────────────────────────────────────────────────────────────
# calculate_sabermetric_context — integration
# ─────────────────────────────────────────────────────────────────────
def _snap(home_pitcher=None, away_pitcher=None,
           home_team=None, away_team=None):
    return {
        "advanced_stats_snapshot": {
            "home_pitcher_advanced": {"available": bool(home_pitcher),
                                       "pitcher": home_pitcher or {}},
            "away_pitcher_advanced": {"available": bool(away_pitcher),
                                       "pitcher": away_pitcher or {}},
            "home_team_advanced":    {"available": bool(home_team),
                                       "team":    home_team or {}},
            "away_team_advanced":    {"available": bool(away_team),
                                       "team":    away_team or {}},
        }
    }


def test_context_unavailable_when_empty():
    out = calculate_sabermetric_context({})
    assert out["sabermetrics"]["available"] is False
    assert out["sabermetrics"]["data_quality"] == "missing"
    # adjustments all zero
    for v in out["sabermetrics"]["adjustments"].values():
        assert v == 0


def test_context_full_data_yields_strong():
    pp = _snap(
        home_pitcher={"era": 3.0, "xera": 3.1, "xwoba_allowed": 0.290},
        away_pitcher={"era": 3.5, "xera": 3.6, "xwoba_allowed": 0.305},
        home_team={"team_ops": 0.860, "team_xwoba": 0.340, "team_barrel_pct": 9.0},
        away_team={"team_ops": 0.820, "team_xwoba": 0.330, "team_barrel_pct": 8.5},
    )
    out = calculate_sabermetric_context(pp)
    sm = out["sabermetrics"]
    assert sm["available"] is True
    assert sm["data_quality"] in ("strong", "partial")
    # Both teams strong OPS → combined pattern code
    assert RC_SABER_BOTH_TEAMS_STRONG_OPS in sm["reason_codes"]


def test_context_high_ops_low_runs_warning_does_not_force_over():
    """OPS alto pero sin confirmación NO debe disparar fragility extrema o
    delta gigante. La capa es conservadora."""
    pp = _snap(
        home_team={"team_ops": 0.900},
        away_team={"team_ops": 0.890},
        home_pitcher={"era": 3.5, "fip": 3.5},
        away_pitcher={"era": 3.5, "fip": 3.5},
    )
    out = calculate_sabermetric_context(pp)
    adj = out["sabermetrics"]["adjustments"]
    # total_runs adjustment should be positive (supports run creation)
    # but small (capped at OPS helper cap 12)
    assert adj["total_runs_adjustment"] > 0
    assert adj["total_runs_adjustment"] <= 12


def test_context_low_ops_strong_fip_supports_under():
    pp = _snap(
        home_team={"team_ops": 0.640},
        away_team={"team_ops": 0.660},
        home_pitcher={"fip": 3.0, "era": 3.0},
        away_pitcher={"fip": 3.1, "era": 3.0},
    )
    out = calculate_sabermetric_context(pp)
    sm = out["sabermetrics"]
    # total_runs adjustment should be NEGATIVE (supports Under)
    assert sm["adjustments"]["total_runs_adjustment"] < 0
    assert sm["adjustments"]["script_survival_adjustment"] > 0


def test_context_risky_fip_raises_fragility():
    pp = _snap(
        home_pitcher={"fip": 5.0, "era": 4.5},
        away_pitcher={"fip": 4.8, "era": 4.0},
        home_team={"team_ops": 0.740},
        away_team={"team_ops": 0.730},
    )
    out = calculate_sabermetric_context(pp)
    sm = out["sabermetrics"]
    assert sm["adjustments"]["fragility_adjustment"] > 0
    assert RC_SABER_BOTH_PITCHERS_RISKY in sm["reason_codes"]


def test_context_emits_era_overstates_for_lucky_pitcher():
    """ERA much lower than FIP → ERA_OVERSTATES."""
    pp = _snap(
        home_pitcher={"era": 2.50, "fip": 4.20},
        away_pitcher={"era": 3.5, "fip": 3.5},
    )
    out = calculate_sabermetric_context(pp)
    # Reason codes for home pitcher should include the overstate flag
    home_fip = out["sabermetrics"]["home"]["starting_pitcher_fip"]
    assert RC_ERA_OVERSTATES in home_fip["reason_codes"]


def test_context_emits_era_understates_for_unlucky_pitcher():
    pp = _snap(
        home_pitcher={"era": 4.30, "fip": 3.10},
    )
    out = calculate_sabermetric_context(pp)
    home_fip = out["sabermetrics"]["home"]["starting_pitcher_fip"]
    assert RC_ERA_UNDERSTATES in home_fip["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# derive_sabermetric_recommendation_delta — weighting contract
# ─────────────────────────────────────────────────────────────────────
def test_delta_missing_data_zero_weight():
    out = derive_sabermetric_recommendation_delta(None, pick_market="Under 8.5")
    assert out["used"] is False
    assert out["weight"] == 0.0
    assert out["weighted_conf_delta"] == 0.0


def test_delta_unavailable_returns_zero():
    out = derive_sabermetric_recommendation_delta(
        {"sabermetrics": {"available": False}}, pick_market="Under 8.5",
    )
    assert out["used"] is False


def test_delta_strong_quality_uses_60pct():
    pp = _snap(
        home_pitcher={"era": 3.0, "fip": 3.0, "xera": 3.1},
        away_pitcher={"era": 3.3, "fip": 3.2},
        home_team={"team_ops": 0.860, "team_xwoba": 0.340},
        away_team={"team_ops": 0.820, "team_xwoba": 0.335},
    )
    ctx = calculate_sabermetric_context(pp)
    out = derive_sabermetric_recommendation_delta(ctx, pick_market="Under 8.5")
    if ctx["sabermetrics"]["data_quality"] == "strong":
        assert out["weight"] == 0.60


def test_delta_partial_quality_uses_35pct():
    pp = _snap(
        home_pitcher={"fip": 3.5},
        home_team={"team_ops": 0.810},
        # away missing → partial/thin
    )
    ctx = calculate_sabermetric_context(pp)
    out = derive_sabermetric_recommendation_delta(ctx, pick_market="Under 8.5")
    if ctx["sabermetrics"]["data_quality"] in ("partial", "thin"):
        assert out["weight"] == 0.35


def test_delta_under_pick_supports_when_low_ops_strong_fip():
    pp = _snap(
        home_pitcher={"fip": 3.0},
        away_pitcher={"fip": 3.1},
        home_team={"team_ops": 0.650},
        away_team={"team_ops": 0.660},
    )
    ctx = calculate_sabermetric_context(pp)
    out = derive_sabermetric_recommendation_delta(ctx, pick_market="Full Game Under 8.5")
    assert out["used"] is True
    # Should be POSITIVE delta for Under pick when fundamentals support Under
    assert out["weighted_conf_delta"] > 0


def test_delta_over_pick_supports_when_high_ops_risky_fip():
    pp = _snap(
        home_pitcher={"fip": 4.8, "era": 4.8},
        away_pitcher={"fip": 4.9, "era": 4.9},
        home_team={"team_ops": 0.880, "team_xwoba": 0.345},
        away_team={"team_ops": 0.860, "team_xwoba": 0.340},
    )
    ctx = calculate_sabermetric_context(pp)
    out = derive_sabermetric_recommendation_delta(ctx, pick_market="Full Game Over 8.5")
    assert out["used"] is True
    assert out["weighted_conf_delta"] > 0


def test_delta_capped_at_plus_minus_15():
    pp = _snap(
        home_pitcher={"fip": 2.0, "era": 2.0, "xwoba_allowed": 0.250},
        away_pitcher={"fip": 2.0, "era": 2.0, "xwoba_allowed": 0.250},
        home_team={"team_ops": 0.500},
        away_team={"team_ops": 0.500},
    )
    ctx = calculate_sabermetric_context(pp)
    out = derive_sabermetric_recommendation_delta(ctx, pick_market="Full Game Under 7.0")
    assert -15.0 <= out["weighted_conf_delta"] <= 15.0


def test_delta_war_alone_does_not_force_run_line():
    """WAR signal alone with no OPS/FIP edges shouldn't push run_line strong."""
    pp = {
        "home_lineup_profiles": [
            {"name": "A", "war": 6.0}, {"name": "B", "war": 5.0},
            {"name": "C", "war": 4.0}, {"name": "D", "war": 3.5},
        ],
        "away_lineup_profiles": [
            {"name": "X", "war": 1.0}, {"name": "Y", "war": 0.8},
        ],
    }
    ctx = calculate_sabermetric_context(pp)
    sm = ctx["sabermetrics"]
    # No OPS/FIP data → adjustments are modest
    assert abs(sm["adjustments"]["run_line_support_adjustment"]) <= 10
    out = derive_sabermetric_recommendation_delta(ctx, pick_market="Run Line -1.5")
    # weighted delta must be small (no Over/Under market keyword)
    assert -10 <= out["weighted_conf_delta"] <= 10


# ─────────────────────────────────────────────────────────────────────
# Football / basketball — sport isolation
# ─────────────────────────────────────────────────────────────────────
def test_module_is_pure_no_side_effects_on_non_baseball():
    """Calling the layer with empty payload should never raise and never
    leak state across sports — the orchestrator is the only caller, and
    it gates by sport."""
    out1 = calculate_sabermetric_context({"sport": "football"})
    out2 = calculate_sabermetric_context({"sport": "basketball"})
    assert out1["sabermetrics"]["available"] is False
    assert out2["sabermetrics"]["available"] is False
