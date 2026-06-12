"""Phase F61 — Football Under Support smoke tests.

Mirrors the 8 verification scenarios spec'd in the implementation
request. Validates:
  1. Insufficient signals → available=False (no misleading 50).
  2. Thin payload → also rejected.
  3. Full signals → score >= 60 with floor passed.
  4. Motivation WITHOUT corroboration → 0 points, NOT_CORROBORATED RC.
  5. Motivation WITH corroboration → +3 points, LOW_MOTIVATION_CONTEXT_MILD.
  6. dc_nb_delta is telemetry-only (does NOT inflate score).
  7. Cross-signal check: Over support >= 75 penalises Under profile -15.
  8. Cross-signal check: Under support >= 70 boosts Under profile +5.
"""
from __future__ import annotations

import pytest

from services.football_moneyball.football_under_support import (
    ENGINE_VERSION,
    MIN_SIGNALS_FLOOR,
    calculate_football_under_support,
)
from services.under_market_scan import compute_under_profile_score


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _full_low_scoring_signals():
    """Match with rich pro-Under signals (defensive + cold offenses + low xG)."""
    return {
        "home_team_signals": {
            "xga":                  0.7,
            "goals_against_avg":    0.9,
            "recent_gf_per_match":  0.9,
            "xg":                   1.0,
            "clean_sheet_rate":     0.5,
        },
        "away_team_signals": {
            "xga":                  0.8,
            "goals_against_avg":    1.0,
            "recent_gf_per_match":  1.0,
            "xg":                   1.2,
            "clean_sheet_rate":     0.45,
        },
    }


def _full_high_scoring_signals():
    """Match with NO pro-Under signals (leaky defenses + hot offenses)."""
    return {
        "home_team_signals": {
            "xga":                  1.5,
            "goals_against_avg":    1.8,
            "recent_gf_per_match":  2.0,
            "xg":                   2.1,
            "clean_sheet_rate":     0.10,
        },
        "away_team_signals": {
            "xga":                  1.4,
            "goals_against_avg":    1.7,
            "recent_gf_per_match":  2.1,
            "xg":                   2.0,
            "clean_sheet_rate":     0.12,
        },
    }


def _minimal_match_for_profile_score():
    """Build a match dict that compute_under_profile_score will accept."""
    return {
        "home_team": {"name": "A", "context": {}},
        "away_team": {"name": "B", "context": {}},
        "h2h_recent": [
            {"home_goals": 1, "away_goals": 0},
            {"home_goals": 0, "away_goals": 1},
            {"home_goals": 1, "away_goals": 1},
            {"home_goals": 0, "away_goals": 0},
            {"home_goals": 1, "away_goals": 0},
        ],
        "odds_snapshots": [],
    }


# ─────────────────────────────────────────────────────────────────────
# 1. Empty / invalid input → available=False, no misleading 50
# ─────────────────────────────────────────────────────────────────────
def test_no_match_doc_returns_unavailable():
    r = calculate_football_under_support({})
    us = r["football_under_support"]
    assert us["available"] is False
    assert us["score"] == 0
    assert us.get("_skipped") in ("insufficient_signals", "no_match_doc")
    assert us["version"] == ENGINE_VERSION


def test_none_input_returns_unavailable():
    r = calculate_football_under_support(None)
    us = r["football_under_support"]
    assert us["available"] is False
    assert us["_skipped"] == "no_match_doc"


# ─────────────────────────────────────────────────────────────────────
# 2. Thin payload (insufficient signals) → rejected
# ─────────────────────────────────────────────────────────────────────
def test_insufficient_signals_below_floor():
    match_thin = {
        "home_team_signals": {"xga": 0.8},  # only 1 signal
        "away_team_signals": {},
    }
    r = calculate_football_under_support(match_thin)
    us = r["football_under_support"]
    assert us["available"] is False
    assert us["_skipped"] == "insufficient_signals"
    assert us["signals_available"] < MIN_SIGNALS_FLOOR
    assert "SIGNAL_MISSING" in us["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 3. Full signals → valid score >= 60
# ─────────────────────────────────────────────────────────────────────
def test_full_signals_produces_high_under_score():
    r = calculate_football_under_support(_full_low_scoring_signals())
    us = r["football_under_support"]
    assert us["available"] is True
    assert us["score"] >= 60
    assert us["signals_available"] >= MIN_SIGNALS_FLOOR
    assert "BOTH_OFFENSES_COLD" in us["reason_codes"]
    assert "LOW_COMBINED_XG" in us["reason_codes"]
    assert "HIGH_CLEAN_SHEET_RATE" in us["reason_codes"]
    assert us["narrative_es"].startswith("Contexto apoya Under")


def test_high_scoring_match_produces_low_under_score():
    r = calculate_football_under_support(_full_high_scoring_signals())
    us = r["football_under_support"]
    assert us["available"] is True
    # Hot offenses + leaky defenses → low support for Under.
    assert us["score"] <= 50
    assert "BOTH_OFFENSES_COLD" not in us["reason_codes"]
    assert "LOW_COMBINED_XG" not in us["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 4. Motivation WITHOUT corroboration → 0 points + NOT_CORROBORATED RC
# ─────────────────────────────────────────────────────────────────────
def test_motivation_without_corroboration_adds_zero():
    match_only_motivation = {
        "home_team_signals": {
            "xga": 1.5, "goals_against_avg": 1.8,
            "recent_gf_per_match": 2.0,
        },
        "away_team_signals": {
            "xga": 1.4, "goals_against_avg": 1.7,
            "recent_gf_per_match": 2.1,
        },
        "motivation_context": {"dead_rubber": True},
    }
    r = calculate_football_under_support(match_only_motivation)
    us = r["football_under_support"]
    assert us["available"] is True
    assert us["bonuses"]["low_motivation"] == 0
    assert "LOW_MOTIVATION_CONTEXT_MILD_NOT_CORROBORATED" in us["reason_codes"]
    assert "LOW_MOTIVATION_CONTEXT_MILD" not in us["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 5. Motivation WITH corroboration → +3 points
# ─────────────────────────────────────────────────────────────────────
def test_motivation_with_corroboration_adds_three():
    match = _full_low_scoring_signals()
    match["motivation_context"] = {"dead_rubber": True}
    r = calculate_football_under_support(match)
    us = r["football_under_support"]
    assert us["available"] is True
    assert us["bonuses"]["low_motivation"] == 3
    assert "LOW_MOTIVATION_CONTEXT_MILD" in us["reason_codes"]


def test_low_stakes_flag_also_triggers_motivation():
    match = _full_low_scoring_signals()
    match["motivation_context"] = {"low_stakes": True}
    r = calculate_football_under_support(match)
    us = r["football_under_support"]
    assert us["bonuses"]["low_motivation"] == 3


# ─────────────────────────────────────────────────────────────────────
# 6. dc_nb_delta — PROMOTED to scoring in Phase F61.1.
#    Sign validated against statsbomb_features.py:447-453.
#    Thresholds: >=3.0 → +8, >=5.0 → +12.
# ─────────────────────────────────────────────────────────────────────
def test_dc_nb_delta_below_threshold_does_not_score():
    """Delta present but below +3.0 → 0 points, but telemetry remains
    and the legacy DC_NB_DELTA_TELEMETRY_ONLY RC is emitted for backward
    compat."""
    base_match = _full_low_scoring_signals()
    score_no_dc = calculate_football_under_support(base_match)["football_under_support"]["score"]

    match_low_delta = dict(base_match)
    match_low_delta["statsbomb_features"] = {
        "dc_nb_delta_2_5_pts": 1.2,   # below +3.0
        "dc_nb_delta_3_5_pts": 0.8,
    }
    r = calculate_football_under_support(match_low_delta)
    us = r["football_under_support"]
    # Score must be identical to the no-delta case (threshold not hit).
    assert us["score"] == score_no_dc
    assert us["bonuses"]["dc_nb_delta"] == 0
    assert us["dc_nb_telemetry"]["dc_nb_delta_2_5_pts"] == 1.2
    assert us["dc_nb_telemetry"]["tier"] == "below_threshold"
    assert us["dc_nb_telemetry"]["_policy"] == "validated_and_promoted_phase_F61_signoff"
    assert "DC_NB_DELTA_TELEMETRY_ONLY" in us["reason_codes"]
    # The promotion codes must NOT appear.
    assert "DC_NB_DELTA_FAVORS_UNDER" not in us["reason_codes"]
    assert "DC_NB_DELTA_STRONGLY_FAVORS_UNDER" not in us["reason_codes"]


def test_dc_nb_delta_mild_tier_adds_eight():
    """Delta >= +3.0 (but < 5.0) → +8 points + DC_NB_DELTA_FAVORS_UNDER."""
    base_match = _full_low_scoring_signals()
    score_no_dc = calculate_football_under_support(base_match)["football_under_support"]["score"]

    match_mild = dict(base_match)
    match_mild["statsbomb_features"] = {
        "dc_nb_delta_2_5_pts": 3.5,   # mild tier
        "dc_nb_delta_3_5_pts": 2.1,
    }
    r = calculate_football_under_support(match_mild)
    us = r["football_under_support"]
    # +8 points added (capped at 100).
    assert us["score"] == min(100, score_no_dc + 8)
    assert us["bonuses"]["dc_nb_delta"] == 8
    assert us["dc_nb_telemetry"]["tier"] == "mild"
    assert "DC_NB_DELTA_FAVORS_UNDER" in us["reason_codes"]
    assert "DC_NB_DELTA_STRONGLY_FAVORS_UNDER" not in us["reason_codes"]
    # Narrative should mention DC/NB.
    assert "DC/NB favorece Under" in us["narrative_es"]


def test_dc_nb_delta_strong_tier_adds_twelve():
    """Delta >= +5.0 → +12 points + STRONGLY_FAVORS_UNDER (+ mild RC too)."""
    base_match = _full_low_scoring_signals()
    score_no_dc = calculate_football_under_support(base_match)["football_under_support"]["score"]

    match_strong = dict(base_match)
    match_strong["statsbomb_features"] = {
        "dc_nb_delta_2_5_pts": 6.8,   # strong tier
        "dc_nb_delta_3_5_pts": 4.5,
    }
    r = calculate_football_under_support(match_strong)
    us = r["football_under_support"]
    assert us["score"] == min(100, score_no_dc + 12)
    assert us["bonuses"]["dc_nb_delta"] == 12
    assert us["dc_nb_telemetry"]["tier"] == "strong"
    assert "DC_NB_DELTA_STRONGLY_FAVORS_UNDER" in us["reason_codes"]
    # Both RCs appear so consumers filtering on the mild code keep working.
    assert "DC_NB_DELTA_FAVORS_UNDER" in us["reason_codes"]
    assert "DC/NB favorece fuertemente Under" in us["narrative_es"]


def test_dc_nb_delta_negative_does_not_score():
    """Negative delta means DC/NB favours OVER → 0 points to Under."""
    base_match = _full_low_scoring_signals()
    score_no_dc = calculate_football_under_support(base_match)["football_under_support"]["score"]

    match_neg = dict(base_match)
    match_neg["statsbomb_features"] = {
        "dc_nb_delta_2_5_pts": -4.0,
        "dc_nb_delta_3_5_pts": -3.0,
    }
    r = calculate_football_under_support(match_neg)
    us = r["football_under_support"]
    assert us["score"] == score_no_dc
    assert us["bonuses"]["dc_nb_delta"] == 0
    assert "DC_NB_DELTA_FAVORS_UNDER" not in us["reason_codes"]
    # Negative delta is still surfaced in telemetry for audit.
    assert us["dc_nb_telemetry"]["dc_nb_delta_2_5_pts"] == -4.0


def test_dc_nb_delta_at_threshold_boundary_mild():
    """Exactly 3.0 must trigger the mild tier."""
    base_match = _full_low_scoring_signals()
    match = dict(base_match)
    match["statsbomb_features"] = {"dc_nb_delta_2_5_pts": 3.0}
    us = calculate_football_under_support(match)["football_under_support"]
    assert us["bonuses"]["dc_nb_delta"] == 8


def test_dc_nb_delta_at_threshold_boundary_strong():
    """Exactly 5.0 must trigger the strong tier."""
    base_match = _full_low_scoring_signals()
    match = dict(base_match)
    match["statsbomb_features"] = {"dc_nb_delta_2_5_pts": 5.0}
    us = calculate_football_under_support(match)["football_under_support"]
    assert us["bonuses"]["dc_nb_delta"] == 12


def test_dc_nb_delta_missing_2_5_value_only_telemetry():
    """If only the 3.5 delta is present (no 2.5), no score is added."""
    base_match = _full_low_scoring_signals()
    match = dict(base_match)
    match["statsbomb_features"] = {"dc_nb_delta_3_5_pts": 7.0}
    us = calculate_football_under_support(match)["football_under_support"]
    assert us["bonuses"]["dc_nb_delta"] == 0
    assert us["dc_nb_telemetry"]["dc_nb_delta_3_5_pts"] == 7.0
    assert us["dc_nb_telemetry"]["dc_nb_delta_2_5_pts"] is None


# ─────────────────────────────────────────────────────────────────────
# 7. Cross-signal check: Over support >= 75 → penalty -15
# ─────────────────────────────────────────────────────────────────────
def test_cross_check_over_support_strong_penalty():
    match = _minimal_match_for_profile_score()
    match["football_over_support"] = {"available": True, "score": 80}
    prof = compute_under_profile_score(match, line=2.5)
    assert "cross_signal_check" in prof
    csc = prof["cross_signal_check"]
    assert csc["over_support_score"] == 80
    assert csc["penalty"] == -15
    assert csc["bonus"] == 0
    assert "OVER_SUPPORT_CONTRADICTS_UNDER_PROFILE" in csc["reason_codes"]
    assert "OVER_SUPPORT_STRONG_PENALTY_APPLIED" in csc["reason_codes"]
    assert csc["score_after_cross_check"] == csc["score_before_cross_check"] - 15


def test_cross_check_over_support_medium_penalty():
    """Over support in [60, 75) → -8 (no STRONG_PENALTY_APPLIED RC)."""
    match = _minimal_match_for_profile_score()
    match["football_over_support"] = {"available": True, "score": 65}
    prof = compute_under_profile_score(match, line=2.5)
    csc = prof["cross_signal_check"]
    assert csc["penalty"] == -8
    assert "OVER_SUPPORT_CONTRADICTS_UNDER_PROFILE" in csc["reason_codes"]
    assert "OVER_SUPPORT_STRONG_PENALTY_APPLIED" not in csc["reason_codes"]


def test_cross_check_over_support_below_threshold_no_penalty():
    match = _minimal_match_for_profile_score()
    match["football_over_support"] = {"available": True, "score": 55}
    prof = compute_under_profile_score(match, line=2.5)
    csc = prof["cross_signal_check"]
    assert csc["penalty"] == 0
    assert csc["bonus"] == 0


def test_cross_check_over_support_unavailable_does_nothing():
    match = _minimal_match_for_profile_score()
    match["football_over_support"] = {"available": False, "score": 80}
    prof = compute_under_profile_score(match, line=2.5)
    csc = prof["cross_signal_check"]
    assert csc["over_support_score"] is None
    assert csc["penalty"] == 0


# ─────────────────────────────────────────────────────────────────────
# 8. Cross-signal check: Under support >= 70 → bonus +5
# ─────────────────────────────────────────────────────────────────────
def test_cross_check_under_support_confirms():
    match = _minimal_match_for_profile_score()
    match["football_under_support"] = {"available": True, "score": 75}
    prof = compute_under_profile_score(match, line=2.5)
    csc = prof["cross_signal_check"]
    assert csc["under_support_score"] == 75
    assert csc["bonus"] == 5
    assert "UNDER_SUPPORT_CONFIRMS_UNDER_PROFILE" in csc["reason_codes"]
    assert csc["score_after_cross_check"] == min(100, csc["score_before_cross_check"] + 5)


def test_cross_check_under_support_below_threshold_no_bonus():
    match = _minimal_match_for_profile_score()
    match["football_under_support"] = {"available": True, "score": 65}
    prof = compute_under_profile_score(match, line=2.5)
    csc = prof["cross_signal_check"]
    assert csc["bonus"] == 0


def test_cross_check_both_supports_present_apply_separately():
    """Over=80 (penalty -15) + Under=72 (bonus +5) → net -10."""
    match = _minimal_match_for_profile_score()
    match["football_over_support"]  = {"available": True, "score": 80}
    match["football_under_support"] = {"available": True, "score": 72}
    prof = compute_under_profile_score(match, line=2.5)
    csc = prof["cross_signal_check"]
    assert csc["penalty"] == -15
    assert csc["bonus"] == 5
    # Both codes present.
    assert "OVER_SUPPORT_CONTRADICTS_UNDER_PROFILE" in csc["reason_codes"]
    assert "UNDER_SUPPORT_CONFIRMS_UNDER_PROFILE" in csc["reason_codes"]
    # Net change: -15 +5 = -10 (clamped at 0 if applicable).
    expected = max(0, csc["score_before_cross_check"] - 15)
    expected = min(100, expected + 5)
    assert csc["score_after_cross_check"] == expected


# ─────────────────────────────────────────────────────────────────────
# Sanity: backward compatibility — cross_signal_check always present.
# ─────────────────────────────────────────────────────────────────────
def test_cross_signal_check_always_present():
    """Even when neither support block exists, the cross_signal_check
    block must be in the output (with zero penalty/bonus)."""
    match = _minimal_match_for_profile_score()
    prof = compute_under_profile_score(match, line=2.5)
    assert "cross_signal_check" in prof
    csc = prof["cross_signal_check"]
    assert csc["penalty"] == 0
    assert csc["bonus"] == 0
    assert csc["over_support_score"] is None
    assert csc["under_support_score"] is None
    assert csc["reason_codes"] == []
    assert csc["score_after_cross_check"] == csc["score_before_cross_check"]


# ─────────────────────────────────────────────────────────────────────
# Sanity: Bonus structure
# ─────────────────────────────────────────────────────────────────────
def test_bonuses_structure_present():
    r = calculate_football_under_support(_full_low_scoring_signals())
    us = r["football_under_support"]
    bonuses = us["bonuses"]
    for k in ("both_offenses_cold", "low_combined_xg", "high_clean_sheet",
              "attacking_injuries", "cold_weather", "low_motivation"):
        assert k in bonuses


def test_cold_weather_bonus_triggers():
    match = _full_low_scoring_signals()
    match["weather"] = {"temp_c": 2.0, "condition": "clear"}
    r = calculate_football_under_support(match)
    us = r["football_under_support"]
    assert us["bonuses"]["cold_weather"] == 5
    assert "COLD_WEATHER_BONUS" in us["reason_codes"]


def test_attacking_injuries_bonus_triggers():
    match = _full_low_scoring_signals()
    match["injuries"] = {"attackers_out_top3": 2}
    r = calculate_football_under_support(match)
    us = r["football_under_support"]
    assert us["bonuses"]["attacking_injuries"] == 10
    assert "ATTACKING_INJURIES_BONUS" in us["reason_codes"]
