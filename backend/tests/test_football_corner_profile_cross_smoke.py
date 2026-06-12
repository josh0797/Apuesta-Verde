"""Phase F58 Fix 3 — Football Corner Profile Cross smoke tests."""
from __future__ import annotations

import pytest

from services.football_corner_profile_cross import (
    ENGINE_VERSION,
    PROFILE_ASYMMETRIC,
    PROFILE_HIGH,
    PROFILE_LOW,
    PROFILE_MIXED,
    PROFILE_STRONG_OVER,
    PROFILE_STRONG_UNDER,
    compute_football_corner_profile_cross,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _team(cf5, cf15, ca5, ca15):
    return {
        "corners_for_l5": cf5, "corners_for_l15": cf15,
        "corners_against_l5": ca5, "corners_against_l15": ca15,
    }


# ─────────────────────────────────────────────────────────────────────
# Profiles 1-6
# ─────────────────────────────────────────────────────────────────────
def test_strong_corners_under_cross():
    res = compute_football_corner_profile_cross(
        home=_team(3.8, 4.6, 3.5, 4.2),
        away=_team(3.2, 4.1, 3.6, 4.4),
    )
    assert res["available"] is True
    assert res["profile"] == PROFILE_STRONG_UNDER
    assert res["supports"] == "CORNERS_UNDER"
    assert res["recommended_market_family"] == "TOTAL_CORNERS_UNDER"
    assert res["confidence_delta"] >= 10
    assert "BOTH_TEAMS_LOW_CORNERS_FOR_L5" in res["reason_codes"]
    assert "BOTH_TEAMS_LOW_CORNERS_AGAINST_L5" in res["reason_codes"]
    assert "STRONG_CORNERS_UNDER_CROSS" in res["reason_codes"]
    assert res["engine_version"] == ENGINE_VERSION


def test_low_corners_cross_only_for():
    # Both teams generate few corners but concede normal volume.
    res = compute_football_corner_profile_cross(
        home=_team(3.8, 4.6, 5.5, 5.2),
        away=_team(3.2, 4.1, 5.0, 4.8),
    )
    # ambos generate ≤ 4.0 BUT both concede ≥ 5.0 → matches HIGH_CORNERS via against
    # We want LOW_CORNERS_CROSS specifically when defense is neutral, so adjust:
    res2 = compute_football_corner_profile_cross(
        home=_team(3.8, 4.6, 4.5, 4.2),
        away=_team(3.2, 4.1, 4.5, 4.4),
    )
    assert res2["profile"] == PROFILE_LOW
    assert res2["supports"] == "CORNERS_UNDER"
    assert res2["confidence_delta"] == 6


def test_strong_corners_over_cross():
    res = compute_football_corner_profile_cross(
        home=_team(6.0, 5.4, 5.5, 5.0),
        away=_team(5.8, 5.2, 5.3, 5.1),
    )
    assert res["profile"] == PROFILE_STRONG_OVER
    assert res["supports"] == "CORNERS_OVER"
    assert res["recommended_market_family"] == "TOTAL_CORNERS_OVER"
    assert "STRONG_CORNERS_OVER_CROSS" in res["reason_codes"]


def test_high_corners_cross_by_for():
    res = compute_football_corner_profile_cross(
        home=_team(5.2, 5.0, 4.0, 4.3),
        away=_team(5.4, 5.1, 4.2, 4.5),
    )
    assert res["profile"] == PROFILE_HIGH
    assert res["supports"] == "CORNERS_OVER"


def test_high_corners_cross_by_against():
    res = compute_football_corner_profile_cross(
        home=_team(4.5, 4.5, 5.4, 5.1),
        away=_team(4.7, 4.6, 5.3, 5.0),
    )
    assert res["profile"] == PROFILE_HIGH
    assert "BOTH_TEAMS_HIGH_CORNERS_AGAINST_L5" in res["reason_codes"]


def test_asymmetric_corners_profile():
    # Home generates a lot, away concedes a lot.
    res = compute_football_corner_profile_cross(
        home=_team(6.5, 6.0, 3.2, 3.8),
        away=_team(3.5, 3.8, 5.5, 5.2),
    )
    assert res["profile"] == PROFILE_ASYMMETRIC
    assert res["supports"] == "TEAM_CORNERS_OVER"
    assert res["recommended_market_family"] == "TEAM_CORNERS_OVER"
    assert res["dominant_side"] == "home"


def test_mixed_corners_profile():
    # Mixed signals: one team generates lots, other generates few.
    res = compute_football_corner_profile_cross(
        home=_team(6.2, 5.8, 3.5, 3.6),
        away=_team(3.0, 3.5, 3.5, 3.7),
    )
    assert res["profile"] == PROFILE_MIXED
    assert res["supports"] == "NEUTRAL"
    assert res["confidence_delta"] == 0
    assert "MIXED_CORNERS_PROFILE_NO_CLEAR_EDGE" in res["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Fail-soft
# ─────────────────────────────────────────────────────────────────────
def test_failsoft_when_missing_corners_inputs():
    res = compute_football_corner_profile_cross(
        home={"corners_for_l5": None, "corners_against_l5": None},
        away=_team(3.0, 3.0, 3.0, 3.0),
    )
    assert res["available"] is False
    assert res["profile"] is None
    assert res["confidence_delta"] == 0
    assert res["_skipped_reason"] == "missing_l5_corners"


def test_derives_from_recent_fixtures_array():
    # Pass `recent_fixtures` with arrays — derivation logic kicks in.
    home_recent = {
        "recent_fixtures": {
            "corners_for":     [3, 4, 3, 4, 3, 5, 6, 5, 6, 5, 4, 5, 6, 4, 5],
            "corners_against": [3, 4, 3, 4, 3, 5, 4, 5, 4, 5, 5, 4, 5, 4, 5],
        },
    }
    away_recent = {
        "recent_fixtures": {
            "corners_for":     [3, 3, 4, 4, 2, 5, 6, 5, 6, 4, 5, 6, 5, 4, 5],
            "corners_against": [4, 3, 4, 4, 3, 5, 5, 4, 5, 4, 5, 5, 4, 5, 5],
        },
    }
    res = compute_football_corner_profile_cross(home=home_recent, away=away_recent)
    assert res["available"] is True
    # corners_for_l5 home = mean(3,4,3,4,3) = 3.4 → STRONG_UNDER candidate
    assert res["home"]["corners_for_l5"] == 3.4
    # Both concede ≤4 in L5 → STRONG_CORNERS_UNDER_CROSS
    assert res["profile"] == PROFILE_STRONG_UNDER


# ─────────────────────────────────────────────────────────────────────
# Scores24 confirmation / conflict
# ─────────────────────────────────────────────────────────────────────
def test_scores24_confirms_engine_under_recommendation():
    res = compute_football_corner_profile_cross(
        home=_team(3.8, 4.6, 3.5, 4.2),
        away=_team(3.2, 4.1, 3.6, 4.4),
        scores24_payload={
            "available": True,
            "consensus": {
                "primary_market_type": "corners_total",
                "primary_section":     "corners_prediction",
                "primary_side":        "UNDER",
                "primary_line":        9.5,
                "primary_odds":        1.58,
            },
            "sections": [],
        },
    )
    assert res["profile"] == PROFILE_STRONG_UNDER
    assert res["external_confirmation"] is True
    assert res["external_conflict"] is False
    assert "SCORES24_CORNERS_CONTEXT_CONFIRMS_ENGINE" in res["reason_codes"]
    assert res["external_market"]["side"] == "UNDER"
    assert res["external_market"]["line"] == 9.5


def test_scores24_conflicts_with_engine_over_recommendation():
    # Engine says OVER but Scores24 says UNDER → conflict.
    res = compute_football_corner_profile_cross(
        home=_team(6.0, 5.4, 5.5, 5.0),
        away=_team(5.8, 5.2, 5.3, 5.1),
        scores24_payload={
            "available": True,
            "consensus": {
                "primary_market_type": "corners_total",
                "primary_side":        "UNDER",
                "primary_line":        10.5,
                "primary_odds":        1.85,
            },
            "sections": [],
        },
    )
    assert res["profile"] == PROFILE_STRONG_OVER
    assert res["external_confirmation"] is False
    assert res["external_conflict"] is True
    assert "SCORES24_CORNERS_CONTEXT_CONFLICT" in res["reason_codes"]


def test_scores24_unavailable_does_not_set_flags():
    res = compute_football_corner_profile_cross(
        home=_team(3.8, 4.6, 3.5, 4.2),
        away=_team(3.2, 4.1, 3.6, 4.4),
        scores24_payload={"available": False, "sections": [], "consensus": {}},
    )
    assert res["external_confirmation"] is False
    assert res["external_conflict"] is False
    assert "SCORES24_CORNERS_CONTEXT_CONFIRMS_ENGINE" not in res["reason_codes"]


def test_output_shape_matches_spec_example():
    res = compute_football_corner_profile_cross(
        home=_team(3.8, 4.6, 3.5, 4.2),
        away=_team(3.2, 4.1, 3.6, 4.4),
    )
    # All required keys per the spec output.
    for k in ("available", "engine_version", "home", "away",
              "profile", "supports", "recommended_market_family",
              "confidence_delta", "fragility_delta",
              "reason_codes", "narrative_es"):
        assert k in res
    # Home block has the deltas exposed.
    for k in ("corners_for_l5", "corners_for_l15", "corners_for_delta",
              "corners_against_l5", "corners_against_l15", "corners_against_delta",
              "total_corners_l5", "total_corners_l15", "total_corners_delta"):
        assert k in res["home"]
    # Confidence delta is positive when profile is non-mixed.
    assert isinstance(res["confidence_delta"], int)
    # Fragility delta NEGATIVE (subtracts from fragility) for STRONG_UNDER.
    assert res["fragility_delta"] <= 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
