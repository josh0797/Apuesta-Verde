"""Sprint D12 · tests para módulos Nivel 2:
  * mlb_bullpen_stress
  * mlb_domino_risk
  * mlb_total_risk_overlay (aggregator final con verdict/dispersion/codes)
"""
from __future__ import annotations

import pytest

from services.mlb_bullpen_stress import (
    compute_bullpen_stress,
    assess_reliever_availability,
)
from services.mlb_domino_risk import compute_domino_risk
from services.mlb_total_risk_overlay import compute_total_risk_overlay


# ═════════════════════════════════════════════════════════════════════
# Reliever Availability
# ═════════════════════════════════════════════════════════════════════
def test_reliever_unavailable_when_back_to_back():
    out = assess_reliever_availability({
        "role": "CLOSER",
        "pitches_yesterday": 25,
        "pitches_2d_ago": 18,
    })
    assert out["availability"] == "UNAVAILABLE"
    assert out["back_to_back"] is True


def test_reliever_unavailable_when_heavy_yesterday():
    out = assess_reliever_availability({
        "role": "CLOSER",
        "pitches_yesterday": 38,
    })
    assert out["availability"] == "UNAVAILABLE"
    assert out["reason"] == "HEAVY_YESTERDAY"


def test_reliever_limited_when_moderate_use():
    out = assess_reliever_availability({
        "role": "SETUP",
        "pitches_yesterday": 25,
        "pitches_2d_ago": 0,
        "pitches_3d_ago": 15,
    })
    assert out["availability"] == "LIMITED"


def test_reliever_limited_when_l3_heavy():
    out = assess_reliever_availability({
        "role": "MIDDLE",
        "pitches_yesterday": 15,
        "pitches_2d_ago": 0,
        "pitches_3d_ago": 35,
    })
    assert out["availability"] == "LIMITED"
    assert out["last_3_days_pitches"] == 50


def test_reliever_available_when_rested():
    out = assess_reliever_availability({
        "role": "LONG",
        "pitches_yesterday": 0,
        "pitches_2d_ago": 10,
        "pitches_3d_ago": 0,
    })
    assert out["availability"] == "AVAILABLE"


def test_reliever_unknown_role_normalised():
    out = assess_reliever_availability({"role": "weirdo"})
    assert out["role"] == "UNKNOWN"


# ═════════════════════════════════════════════════════════════════════
# Bullpen Stress
# ═════════════════════════════════════════════════════════════════════
def test_bullpen_fresh_returns_low_score():
    out = compute_bullpen_stress({
        "usage_l3": {"pitches": 25, "relievers_used_yesterday": 1},
        "bullpen_era_l7": 2.8,
        "bullpen_whip_l7": 1.10,
        "closer": "AVAILABLE", "setup": "AVAILABLE", "long_relief": "AVAILABLE",
    })
    assert out["bullpen_stress_score"] <= 45
    assert out["bucket"] == "FRESH"
    assert "L3_BULLPEN_FRESH" in out["drivers"]


def test_bullpen_tired_via_l3_pitches():
    out = compute_bullpen_stress({
        "usage_l3": {"pitches": 130, "relievers_used_yesterday": 3},
    })
    assert out["bucket"] in ("TIRED", "EXHAUSTED")
    assert "L3_PITCHES_120" in out["tired_triggers"] or out["bucket"] == "EXHAUSTED"


def test_bullpen_exhausted_via_l3_extreme():
    out = compute_bullpen_stress({
        "usage_l3": {"pitches": 175, "relievers_used_yesterday": 5},
        "bullpen_era_l7": 5.5,
        "bullpen_whip_l7": 1.50,
        "closer": "UNAVAILABLE", "setup": "UNAVAILABLE",
    })
    assert out["bucket"] == "EXHAUSTED"
    assert "L3_PITCHES_160" in out["exhausted_triggers"]
    assert "CLOSER_AND_SETUP_OUT" in out["exhausted_triggers"]


def test_bullpen_exhausted_when_closer_and_setup_out():
    out = compute_bullpen_stress({
        "closer": "UNAVAILABLE", "setup": "UNAVAILABLE",
    })
    assert out["bucket"] == "EXHAUSTED"


def test_bullpen_availability_inferred_from_pitcher_dict():
    """Si pasas dict del pitcher en lugar de string, se llama
    assess_reliever_availability automáticamente."""
    out = compute_bullpen_stress({
        "closer": {"role": "CLOSER", "pitches_yesterday": 40},
    })
    assert out["availability"]["closer"] == "UNAVAILABLE"


def test_bullpen_high_leverage_back_to_back_drives_score_up():
    out = compute_bullpen_stress({
        "high_leverage_back_to_back": 2,
    })
    assert "HIGH_LEVERAGE_BACK_TO_BACK" in out["drivers"]
    assert out["bucket"] in ("TIRED", "EXHAUSTED", "NORMAL")


def test_bullpen_failsoft_empty_input():
    out = compute_bullpen_stress(None)
    assert 0 <= out["bullpen_stress_score"] <= 100
    assert out["confidence"] == 0.0


def test_bullpen_usage_block_passthrough():
    out = compute_bullpen_stress({
        "usage_l3": {"pitches": 80, "innings": 6.0, "appearances": 5},
        "usage_l5": {"pitches": 140},
        "usage_l7": {"pitches": 200},
    })
    assert out["usage"]["last_3_days"]["pitches"] == 80
    assert out["usage"]["last_5_days"]["pitches"] == 140
    assert out["usage"]["last_7_days"]["pitches"] == 200


# ═════════════════════════════════════════════════════════════════════
# Domino Risk
# ═════════════════════════════════════════════════════════════════════
def test_domino_high_when_starter_volatile_and_bullpen_tired():
    out = compute_domino_risk(
        starter_volatility_score=68,
        bullpen_stress_score=65,
        prob_short_exit=0.40,
        lineup_explosiveness_score=70,
    )
    assert out["bucket"] in ("HIGH", "EXTREME")


def test_domino_extreme_with_no_long_relief():
    out = compute_domino_risk(
        starter_volatility_score=80,
        bullpen_stress_score=75,
        long_relief_availability="UNAVAILABLE",
        lineup_explosiveness_score=82,
        prob_short_exit=0.50,
    )
    assert out["bucket"] == "EXTREME"
    assert "STARTER_VOLATILITY_EXTREME" in out["drivers"]
    assert "LONG_RELIEF_OUT" in out["drivers"]


def test_domino_low_when_everything_clean():
    out = compute_domino_risk(
        starter_volatility_score=25,
        bullpen_stress_score=30,
        long_relief_availability="AVAILABLE",
        prob_short_exit=0.10,
        lineup_explosiveness_score=40,
    )
    assert out["bucket"] == "LOW"


def test_domino_failsoft_no_inputs():
    out = compute_domino_risk()
    assert out["bucket"] in ("LOW", "MEDIUM")
    assert out["confidence"] == 0.0


# ═════════════════════════════════════════════════════════════════════
# Total Risk Overlay — verdict, dispersion, reason codes
# ═════════════════════════════════════════════════════════════════════
def _wrap(home_score: float, away_score: float, key: str, bucket_h="HIGH", bucket_a="HIGH") -> dict:
    return {
        "home": {key: home_score, "bucket": bucket_h},
        "away": {key: away_score, "bucket": bucket_a},
    }


def test_overlay_allow_when_no_under_risks():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.5},
        starter_volatility=_wrap(20, 25, "starter_volatility_score"),
        first_inning_collapse=_wrap(20, 30, "first_inning_collapse_score"),
        recent_offensive_quality={
            "home": {"recent_offense_score": 30, "bucket": "COLD"},
            "away": {"recent_offense_score": 30, "bucket": "COLD"},
        },
        lineup_explosiveness=_wrap(30, 25, "lineup_explosiveness_score"),
        bullpen_stress=_wrap(20, 25, "bullpen_stress_score"),
        domino_risk=_wrap(15, 20, "domino_risk_score"),
        base_fragility=10, base_survival=85, base_explosive_tail_risk="LOW",
    )
    assert out["verdict"] == "ALLOW"
    assert out["explosive_tail_risk"] == "LOW"
    assert out["dispersion_multiplier"] == 1.0


def test_overlay_volatile_starter_vs_explosive_lineup_emits_code():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.5},
        starter_volatility=_wrap(80, 30, "starter_volatility_score"),
        lineup_explosiveness=_wrap(75, 40, "lineup_explosiveness_score"),
        base_fragility=20, base_survival=75, base_explosive_tail_risk="LOW",
    )
    assert "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" in out["reason_codes"]
    assert out["fragility_score"] >= 35  # +15
    assert out["explosive_tail_risk"] in ("MEDIUM", "HIGH")


def test_overlay_first_inning_collapse_85_emits_extreme():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.5},
        first_inning_collapse=_wrap(88, 50, "first_inning_collapse_score"),
        bullpen_stress=_wrap(72, 50, "bullpen_stress_score"),
        base_fragility=15, base_survival=80,
    )
    assert "EXTREME_FIRST_INNING_COLLAPSE_RISK" in out["reason_codes"]
    assert "FIRST_INNING_COLLAPSE_RISK" in out["reason_codes"]
    assert out["verdict"] == "BLOCK"


def test_overlay_both_offenses_hot_adds_runs():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.0,
        pick={"selection": "UNDER", "line": 9.0},
        recent_offensive_quality={
            "home": {"recent_offense_score": 80, "bucket": "EXPLOSIVE"},
            "away": {"recent_offense_score": 78, "bucket": "EXPLOSIVE"},
        },
        base_fragility=10, base_survival=80,
    )
    assert "BOTH_OFFENSES_HOT" in out["reason_codes"]
    # Both EXPLOSIVE → +0.8.
    assert out["adjusted_expected_runs"] == pytest.approx(8.8, abs=1e-3)


def test_overlay_bullpen_exhaustion_risk():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.5},
        bullpen_stress=_wrap(75, 70, "bullpen_stress_score"),
        base_fragility=20, base_survival=80,
    )
    assert "BULLPEN_EXHAUSTION_RISK" in out["reason_codes"]
    assert out["fragility_score"] >= 32  # +12
    assert out["under_survival_score"] <= 72  # -8


def test_overlay_domino_risk_emits_code_and_bumps_tail():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.5},
        domino_risk=_wrap(75, 65, "domino_risk_score"),
        base_fragility=25, base_survival=75, base_explosive_tail_risk="LOW",
    )
    assert "DOMINO_RISK_STARTER_TO_BULLPEN" in out["reason_codes"]
    assert out["explosive_tail_risk"] in ("MEDIUM", "HIGH", "EXTREME")


def test_overlay_block_when_extreme_tail():
    """Multiple severe risks combine → tail=EXTREME → BLOCK."""
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.5},
        starter_volatility=_wrap(85, 40, "starter_volatility_score"),
        lineup_explosiveness=_wrap(82, 50, "lineup_explosiveness_score"),
        first_inning_collapse=_wrap(82, 50, "first_inning_collapse_score"),
        bullpen_stress=_wrap(75, 60, "bullpen_stress_score"),
        domino_risk=_wrap(82, 60, "domino_risk_score"),
        base_fragility=30, base_survival=70, base_explosive_tail_risk="MEDIUM",
    )
    assert out["verdict"] == "BLOCK"
    assert out["explosive_tail_risk"] == "EXTREME"
    assert "UNDER_TAIL_RISK_RECALIBRATED" in out["reason_codes"]
    assert out["dispersion_multiplier"] >= 1.65


def test_overlay_dispersion_high_when_tail_high():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.5},
        domino_risk=_wrap(75, 60, "domino_risk_score"),
        starter_volatility=_wrap(72, 40, "starter_volatility_score"),
        lineup_explosiveness=_wrap(72, 50, "lineup_explosiveness_score"),
        base_fragility=20, base_survival=80, base_explosive_tail_risk="MEDIUM",
    )
    assert out["explosive_tail_risk"] in ("HIGH", "EXTREME")
    assert out["dispersion_multiplier"] >= 1.35


def test_overlay_warn_when_moderate_fragility():
    """Single moderate risk + cushion → WARN."""
    out = compute_total_risk_overlay(
        baseline_expected_runs=7.0,
        pick={"selection": "UNDER", "line": 10.5},
        bullpen_stress=_wrap(72, 30, "bullpen_stress_score"),
        base_fragility=40, base_survival=70,
    )
    assert out["verdict"] == "WARN"


def test_overlay_does_not_alter_over_picks():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "OVER", "line": 7.5},
        starter_volatility=_wrap(85, 80, "starter_volatility_score"),
        domino_risk=_wrap(80, 70, "domino_risk_score"),
        base_fragility=30, base_survival=70,
    )
    # OVER no se degrada por la capa — fragility/survival no se modifican.
    assert out["verdict"] == "ALLOW"
    assert out["fragility_score"] == 30
    assert out["under_survival_score"] == 70


def test_overlay_block_when_domino_extreme_and_offense_explosive():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.0},
        domino_risk=_wrap(82, 70, "domino_risk_score"),
        recent_offensive_quality={
            "home": {"recent_offense_score": 80, "bucket": "EXPLOSIVE"},
            "away": {"recent_offense_score": 50, "bucket": "NEUTRAL"},
        },
        base_fragility=20, base_survival=75,
    )
    assert out["verdict"] == "BLOCK"


def test_overlay_editorial_summary_present_for_block():
    out = compute_total_risk_overlay(
        baseline_expected_runs=8.5,
        pick={"selection": "UNDER", "line": 9.5},
        first_inning_collapse=_wrap(88, 50, "first_inning_collapse_score"),
        bullpen_stress=_wrap(75, 60, "bullpen_stress_score"),
        base_fragility=15, base_survival=80,
    )
    assert out["editorial_summary"]
    assert out["verdict"] == "BLOCK"


def test_overlay_debug_block_present():
    out = compute_total_risk_overlay()
    assert "debug" in out
    for k in ("sv_max", "fi_max", "lineup_max", "bp_max", "domino_max"):
        assert k in out["debug"]
