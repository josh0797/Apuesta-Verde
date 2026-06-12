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
# 6. dc_nb_delta is telemetry-only (does NOT inflate score)
# ─────────────────────────────────────────────────────────────────────
def test_dc_nb_delta_is_telemetry_only():
    base_match = _full_low_scoring_signals()

    r_no_dc = calculate_football_under_support(base_match)
    score_no_dc = r_no_dc["football_under_support"]["score"]

    match_with_dc = dict(base_match)
    match_with_dc["statsbomb_features"] = {
        "dc_nb_delta_2_5_pts": 5.5,
        "dc_nb_delta_3_5_pts": 3.2,
    }
    r_with_dc = calculate_football_under_support(match_with_dc)
    us = r_with_dc["football_under_support"]
    score_with_dc = us["score"]

    # Score must be identical — telemetry adds 0 points.
    assert score_with_dc == score_no_dc
    # Telemetry is exposed though.
    assert "dc_nb_telemetry" in us
    assert us["dc_nb_telemetry"]["dc_nb_delta_2_5_pts"] == 5.5
    assert us["dc_nb_telemetry"]["dc_nb_delta_3_5_pts"] == 3.2
    assert us["dc_nb_telemetry"]["_policy"].startswith("telemetry_only")
    assert "DC_NB_DELTA_TELEMETRY_ONLY" in us["reason_codes"]


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
