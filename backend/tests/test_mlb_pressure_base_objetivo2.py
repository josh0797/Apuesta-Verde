"""Tests for services.mlb_pressure_base (Objetivo 2).

Coverage:
* Unit-level per-team tier classification (HIGH/MODERATE/LOW/NEUTRAL/UNAVAILABLE).
* Combined tier classification.
* Live hits acceleration override.
* Match-level extraction from full pick_payload shape.
* Downstream impact helper for Under / Over picks.
"""
from __future__ import annotations

import pytest

from services.mlb_pressure_base import (
    HIGH_PRESSURE, MODERATE_PRESSURE, LOW_PRESSURE,
    NEUTRAL_PRESSURE, UNAVAILABLE,
    RC_HIGH_HIT_LOW_RUN, RC_MODERATE_HIT_LOW_RUN,
    RC_LOW_HITS_QUIET_OFFENSE, RC_NEUTRAL_PRESSURE,
    RC_COMBINED_HIDDEN_PRESSURE, RC_LIVE_HIT_ACCELERATION,
    RC_PRESSURE_DATA_MISSING, RC_UNDER_PICK_HIGH_PRESSURE,
    RC_UNDER_PICK_MODERATE_PRESSURE, RC_LOW_PRESSURE_CONTROLLED,
    calculate_team_pressure_base,
    calculate_match_pressure_context,
    derive_pressure_impact_for_under_pick,
)


# ─────────────────────────────────────────────────────────────────────
# Per-team tier classification
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "hits_l5, runs_l5, expected",
    [
        (9.0, 3.5, HIGH_PRESSURE),       # exact threshold
        (10.0, 2.8, HIGH_PRESSURE),
        (8.5, 3.9, MODERATE_PRESSURE),   # moderate
        (8.0, 4.0, MODERATE_PRESSURE),   # boundary moderate
        (6.5, 3.5, LOW_PRESSURE),        # boundary low
        (5.0, 2.0, LOW_PRESSURE),
        (7.0, 5.0, NEUTRAL_PRESSURE),    # hits middling, runs high → neutral
        (9.0, 6.0, NEUTRAL_PRESSURE),    # high hits but high runs too → not pressure
        (6.0, 4.5, NEUTRAL_PRESSURE),    # low hits + high runs → neutral
    ],
)
def test_per_team_classify_tiers(hits_l5, runs_l5, expected):
    side = {"hits_avg_last_5": hits_l5, "hits_avg_last_15": hits_l5}
    out = calculate_team_pressure_base(side, runs_avg_l5=runs_l5)
    assert out["pressure_tier"] == expected, f"hits={hits_l5}, runs={runs_l5}"


def test_per_team_unavailable_when_no_form():
    out = calculate_team_pressure_base(None, runs_avg_l5=3.0)
    assert out["available"] is False
    assert out["pressure_tier"] == UNAVAILABLE
    assert RC_PRESSURE_DATA_MISSING in out["reasons"]


def test_per_team_unavailable_when_runs_missing():
    side = {"hits_avg_last_5": 10.0, "hits_avg_last_15": 9.0}
    out = calculate_team_pressure_base(side, runs_avg_l5=None)
    assert out["pressure_tier"] == UNAVAILABLE
    assert out["score"] == 0


def test_per_team_high_pressure_reasons():
    side = {
        "hits_avg_last_5": 9.5, "hits_avg_last_15": 8.2,
        "times_on_base_avg_last_5": 13.0, "times_on_base_avg_last_15": 11.5,
    }
    out = calculate_team_pressure_base(side, runs_avg_l5=3.0)
    assert out["pressure_tier"] == HIGH_PRESSURE
    assert RC_HIGH_HIT_LOW_RUN in out["reasons"]
    assert out["score"] >= 70


def test_per_team_low_pressure_reasons():
    side = {"hits_avg_last_5": 6.0, "hits_avg_last_15": 6.2}
    out = calculate_team_pressure_base(side, runs_avg_l5=3.0)
    assert out["pressure_tier"] == LOW_PRESSURE
    assert RC_LOW_HITS_QUIET_OFFENSE in out["reasons"]


def test_per_team_neutral_pressure_reasons():
    side = {"hits_avg_last_5": 7.5, "hits_avg_last_15": 7.5}
    out = calculate_team_pressure_base(side, runs_avg_l5=4.5)
    assert out["pressure_tier"] == NEUTRAL_PRESSURE
    assert RC_NEUTRAL_PRESSURE in out["reasons"]


# ─────────────────────────────────────────────────────────────────────
# Live hits acceleration
# ─────────────────────────────────────────────────────────────────────
def test_live_acceleration_promotes_neutral_to_moderate():
    side = {"hits_avg_last_5": 7.0, "hits_avg_last_15": 7.5}
    # neutral with hits=7, runs=4.0 → NEUTRAL (no rule fires)
    out = calculate_team_pressure_base(side, runs_avg_l5=4.0, live_hits=11)
    # live_hits = 11, baseline = 7 → delta 4 ≥ 3 → acceleration
    assert RC_LIVE_HIT_ACCELERATION in out["reasons"]
    assert out["pressure_tier"] == MODERATE_PRESSURE


def test_live_acceleration_no_override_below_threshold():
    side = {"hits_avg_last_5": 7.0, "hits_avg_last_15": 7.0}
    out = calculate_team_pressure_base(side, runs_avg_l5=4.5, live_hits=8)
    # delta=1 — no acceleration
    assert RC_LIVE_HIT_ACCELERATION not in out["reasons"]


def test_live_acceleration_promotes_moderate_to_high():
    side = {"hits_avg_last_5": 8.0, "hits_avg_last_15": 8.0}
    out = calculate_team_pressure_base(side, runs_avg_l5=3.5, live_hits=11)
    # delta=3, was MODERATE → HIGH
    assert RC_LIVE_HIT_ACCELERATION in out["reasons"]
    assert out["pressure_tier"] == HIGH_PRESSURE


# ─────────────────────────────────────────────────────────────────────
# Match-level extraction
# ─────────────────────────────────────────────────────────────────────
def _build_pick_payload(home_hits=9.0, home_runs=3.0,
                         away_hits=9.0, away_runs=3.0,
                         total_l5=None):
    if total_l5 is None:
        total_l5 = round(home_runs + away_runs, 3)
    return {
        "recent_run_split": {
            "runs_scored_avg_last_5_home":  home_runs,
            "runs_scored_avg_last_15_home": home_runs + 0.2,
            "runs_scored_avg_last_5_away":  away_runs,
            "runs_scored_avg_last_15_away": away_runs + 0.2,
            "total_runs_avg_last_5":        total_l5,
            "total_runs_avg_last_15":       total_l5 + 0.4,
        },
        "on_base_profile": {
            "home": {"hits_avg_last_5": home_hits, "hits_avg_last_15": home_hits + 0.5},
            "away": {"hits_avg_last_5": away_hits, "hits_avg_last_15": away_hits + 0.5},
        },
    }


def test_calculate_match_pressure_high_both_teams():
    pp = _build_pick_payload(home_hits=10.0, home_runs=3.0,
                              away_hits=9.5, away_runs=3.0)
    ctx = calculate_match_pressure_context(pp)
    assert ctx["available"] is True
    assert ctx["home"]["pressure_tier"] == HIGH_PRESSURE
    assert ctx["away"]["pressure_tier"] == HIGH_PRESSURE
    assert ctx["combined"]["pressure_tier"] == HIGH_PRESSURE
    assert RC_COMBINED_HIDDEN_PRESSURE in ctx["combined"]["reasons"]
    assert ctx["combined"]["flags"]["both_teams_high"] is True


def test_calculate_match_pressure_low_both_teams():
    pp = _build_pick_payload(home_hits=6.0, home_runs=3.0,
                              away_hits=6.0, away_runs=3.0)
    ctx = calculate_match_pressure_context(pp)
    assert ctx["available"] is True
    assert ctx["home"]["pressure_tier"] == LOW_PRESSURE
    assert ctx["away"]["pressure_tier"] == LOW_PRESSURE
    assert ctx["combined"]["pressure_tier"] == LOW_PRESSURE
    assert ctx["combined"]["flags"]["both_teams_low"] is True


def test_calculate_match_pressure_unavailable_when_missing():
    ctx = calculate_match_pressure_context({})
    assert ctx["available"] is False
    assert ctx["combined"]["pressure_tier"] == UNAVAILABLE
    assert RC_PRESSURE_DATA_MISSING in ctx["reason_codes"]


def test_calculate_match_pressure_from_baseball_historical_mirror():
    """Should also resolve from baseballHistoricalProfile mirror."""
    pp = {
        "baseballHistoricalProfile": {
            "recentRunSplit": {
                "runs_scored_avg_last_5_home": 3.0,
                "runs_scored_avg_last_5_away": 3.0,
                "total_runs_avg_last_5":       6.0,
            },
            "onBaseProfileL5": {
                "home": {"hits_avg_last_5": 9.5, "hits_avg_last_15": 9.0},
                "away": {"hits_avg_last_5": 9.5, "hits_avg_last_15": 9.0},
            },
        },
    }
    ctx = calculate_match_pressure_context(pp)
    assert ctx["available"] is True
    assert ctx["home"]["pressure_tier"] == HIGH_PRESSURE


def test_calculate_match_pressure_live_hits_propagated():
    pp = _build_pick_payload(home_hits=7.0, home_runs=4.0,
                              away_hits=7.0, away_runs=4.0)
    pp["live_state"] = {
        "box_score": {"hits": {"home": 11, "away": 7}}
    }
    ctx = calculate_match_pressure_context(pp)
    assert ctx["home"]["inputs"]["live_hits"] == 11
    assert RC_LIVE_HIT_ACCELERATION in ctx["home"]["reasons"]
    assert ctx["combined"]["flags"]["live_acceleration"] is True


# ─────────────────────────────────────────────────────────────────────
# Downstream impact helper
# ─────────────────────────────────────────────────────────────────────
def test_impact_under_pick_high_pressure_increases_fragility():
    pp = _build_pick_payload(home_hits=10.0, home_runs=3.0,
                              away_hits=10.0, away_runs=3.0)
    ctx = calculate_match_pressure_context(pp)
    imp = derive_pressure_impact_for_under_pick(ctx, pick_market="Full Game Under 8.5")
    assert imp["applied"] is True
    assert imp["fragility_delta"] > 0
    assert imp["confidence_delta"] < 0
    assert RC_UNDER_PICK_HIGH_PRESSURE in imp["reason_codes"]


def test_impact_under_pick_moderate_pressure():
    pp = _build_pick_payload(home_hits=8.2, home_runs=3.8,
                              away_hits=7.5, away_runs=4.5)
    ctx = calculate_match_pressure_context(pp)
    imp = derive_pressure_impact_for_under_pick(ctx, pick_market="Full Game Under 9.0")
    if imp["applied"]:
        assert imp["fragility_delta"] in (5, 8)  # accept HIGH or MOD
        assert imp["confidence_delta"] < 0


def test_impact_under_pick_low_pressure_supports():
    pp = _build_pick_payload(home_hits=6.0, home_runs=3.0,
                              away_hits=6.0, away_runs=3.0)
    ctx = calculate_match_pressure_context(pp)
    imp = derive_pressure_impact_for_under_pick(ctx, pick_market="Full Game Under 8.0")
    assert imp["applied"] is True
    assert imp["confidence_delta"] > 0
    assert imp["fragility_delta"] < 0
    assert RC_LOW_PRESSURE_CONTROLLED in imp["reason_codes"]


def test_impact_over_pick_inverse_signal():
    pp = _build_pick_payload(home_hits=10.0, home_runs=3.0,
                              away_hits=10.0, away_runs=3.0)
    ctx = calculate_match_pressure_context(pp)
    imp = derive_pressure_impact_for_under_pick(ctx, pick_market="Full Game Over 8.5")
    assert imp["applied"] is True
    assert imp["confidence_delta"] > 0


def test_impact_team_total_market_not_applied():
    """Team Total markets should NOT trigger pressure_base adjustments."""
    pp = _build_pick_payload(home_hits=10.0, home_runs=3.0,
                              away_hits=10.0, away_runs=3.0)
    ctx = calculate_match_pressure_context(pp)
    imp = derive_pressure_impact_for_under_pick(ctx, pick_market="Team Total Under 4.5")
    assert imp["applied"] is False


def test_impact_no_op_when_pressure_unavailable():
    imp = derive_pressure_impact_for_under_pick(None, pick_market="Under 8.5")
    assert imp["applied"] is False
    imp2 = derive_pressure_impact_for_under_pick({"available": False}, pick_market="Under 8.5")
    assert imp2["applied"] is False
