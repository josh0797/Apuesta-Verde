"""Phase F58 smoke tests — Football Team Profile Cross.

Cubren los 7 perfiles, fail-soft, deltas simétricos, override gating y
el visual entry.
"""
from __future__ import annotations

import pytest

from services.football_team_profile_cross import (
    ENGINE_VERSION,
    MAX_CONFIDENCE_BONUS,
    MAX_CONFIDENCE_PENALTY,
    PROFILE_STRONG_UNDER,
    PROFILE_LOW_EVENT_UNDER,
    PROFILE_STRONG_OVER,
    PROFILE_BTTS,
    PROFILE_DOMINANCE,
    PROFILE_CORNERS_OVER,
    PROFILE_MIXED,
    STRONG_OVERRIDE_PROFILES,
    STRONG_OVERRIDE_THRESHOLD,
    classify_team_football_profile,
    compute_combined_football_profile_cross,
    apply_profile_cross_to_pick,
    build_pattern_alignment_entry,
)


# ─────────────────────────────────────────────────────────────────────
# classify_team_football_profile
# ─────────────────────────────────────────────────────────────────────
def test_classify_team_offense_cooling():
    out = classify_team_football_profile(
        goals_for_l5=0.6, goals_for_l15=1.4,
        goals_against_l5=1.0, goals_against_l15=1.0,
    )
    assert out["is_offense_cold"] is True
    assert "TEAM_OFFENSE_COOLING" in out["reason_codes"]


def test_classify_team_defense_leaking_and_offense_heating():
    out = classify_team_football_profile(
        goals_for_l5=2.2, goals_for_l15=1.4,
        goals_against_l5=2.0, goals_against_l15=1.2,
    )
    assert out["is_offense_hot"] is True
    assert out["is_defense_leaky"] is True


def test_classify_low_event_volume_team():
    out = classify_team_football_profile(
        goals_for_l5=1.0, goals_for_l15=1.0,
        goals_against_l5=1.0, goals_against_l15=1.0,
        shots_l5=7.0, sot_l5=2.0,
    )
    assert out["is_low_event"] is True
    assert "TEAM_LOW_EVENT_VOLUME" in out["reason_codes"]


def test_classify_missing_inputs_returns_safely():
    out = classify_team_football_profile(
        goals_for_l5=None, goals_for_l15=None,
        goals_against_l5=None, goals_against_l15=None,
    )
    # All flags must default to False; reason_codes empty
    assert out["is_offense_cold"] is False
    assert out["is_offense_hot"] is False
    assert out["reason_codes"] == []


# ─────────────────────────────────────────────────────────────────────
# compute_combined_football_profile_cross — 7 profiles
# ─────────────────────────────────────────────────────────────────────
def _home_cold_def_tight():
    return {
        "goals_for_l5": 0.6, "goals_for_l15": 1.4,
        "goals_against_l5": 0.6, "goals_against_l15": 1.3,
    }


def _away_cold_def_tight():
    return {
        "goals_for_l5": 0.4, "goals_for_l15": 1.2,
        "goals_against_l5": 0.7, "goals_against_l15": 1.3,
    }


def test_strong_under_cross_profile():
    res = compute_combined_football_profile_cross(
        home=_home_cold_def_tight(),
        away=_away_cold_def_tight(),
    )
    assert res["available"] is True
    assert res["profile"] == PROFILE_STRONG_UNDER
    assert res["supports"] == "UNDER"
    assert res["confidence_delta"] > 0
    assert res["fragility_delta"] > 0
    assert "STRONG_UNDER_CROSS" in res["reason_codes"]
    assert res["engine_version"] == ENGINE_VERSION


def test_strong_over_cross_profile():
    home = {
        "goals_for_l5": 2.4, "goals_for_l15": 1.4,
        "goals_against_l5": 2.0, "goals_against_l15": 1.2,
    }
    away = {
        "goals_for_l5": 2.0, "goals_for_l15": 1.4,
        "goals_against_l5": 1.8, "goals_against_l15": 1.1,
    }
    res = compute_combined_football_profile_cross(home=home, away=away)
    assert res["profile"] == PROFILE_STRONG_OVER
    assert res["supports"] == "OVER"
    assert res["confidence_delta"] >= 10
    assert "STRONG_OVER_CROSS" in res["reason_codes"]


def test_low_event_under_cross_profile():
    # Both teams have low event volume but goals are moderate (no STRONG_UNDER).
    home = {
        "goals_for_l5": 1.0, "goals_for_l15": 1.0,
        "goals_against_l5": 1.2, "goals_against_l15": 1.2,
        "shots_l5": 7.0, "sot_l5": 2.0,
    }
    away = {
        "goals_for_l5": 1.0, "goals_for_l15": 1.0,
        "goals_against_l5": 1.0, "goals_against_l15": 1.0,
        "shots_l5": 8.0, "sot_l5": 2.5,
    }
    res = compute_combined_football_profile_cross(home=home, away=away)
    assert res["profile"] == PROFILE_LOW_EVENT_UNDER
    assert res["supports"] == "UNDER"


def test_bilateral_btts_cross_profile():
    home = {
        "goals_for_l5": 1.6, "goals_for_l15": 1.4,
        "goals_against_l5": 1.4, "goals_against_l15": 1.2,
    }
    away = {
        "goals_for_l5": 1.4, "goals_for_l15": 1.3,
        "goals_against_l5": 1.3, "goals_against_l15": 1.2,
    }
    res = compute_combined_football_profile_cross(home=home, away=away)
    assert res["profile"] == PROFILE_BTTS
    assert res["supports"] == "BTTS"


def test_corners_over_cross_profile():
    home = {
        "goals_for_l5": 1.4, "goals_for_l15": 1.4,
        "goals_against_l5": 1.0, "goals_against_l15": 1.0,
        "corners_l5": 7.5, "corners_l15": 5.5,
    }
    away = {
        "goals_for_l5": 1.4, "goals_for_l15": 1.4,
        "goals_against_l5": 1.0, "goals_against_l15": 1.0,
        "corners_l5": 7.0, "corners_l15": 5.8,
    }
    res = compute_combined_football_profile_cross(home=home, away=away)
    assert res["profile"] == PROFILE_CORNERS_OVER
    assert res["supports"] == "CORNERS"


def test_mixed_profile_when_signals_diverge():
    home = {
        "goals_for_l5": 0.6, "goals_for_l15": 1.4,    # cooling
        "goals_against_l5": 1.0, "goals_against_l15": 1.0,
    }
    away = {
        "goals_for_l5": 2.2, "goals_for_l15": 1.4,    # heating
        "goals_against_l5": 1.0, "goals_against_l15": 1.0,
    }
    res = compute_combined_football_profile_cross(home=home, away=away)
    # MIXED or DOMINANCE depending on xg/gf diff — both are non-STRONG
    assert res["profile"] in (PROFILE_MIXED, PROFILE_DOMINANCE)
    assert res["supports"] == "NEUTRAL"


def test_unilateral_dominance_cross():
    home = {
        "goals_for_l5": 1.5, "goals_for_l15": 1.4,
        "goals_against_l5": 0.4, "goals_against_l15": 0.8,
        "xg_l5": 2.0, "xg_l15": 1.5,
        "xga_l5": 0.6, "xga_l15": 0.9,
    }
    away = {
        "goals_for_l5": 0.6, "goals_for_l15": 0.9,
        "goals_against_l5": 1.5, "goals_against_l15": 1.4,
        "xg_l5": 0.5, "xg_l15": 0.9,
        "xga_l5": 1.9, "xga_l15": 1.4,
    }
    res = compute_combined_football_profile_cross(home=home, away=away)
    assert res["profile"] == PROFILE_DOMINANCE
    # Dominance unilateral supports NEUTRAL for total markets
    assert res["supports"] == "NEUTRAL"


def test_failsoft_when_required_inputs_missing():
    res = compute_combined_football_profile_cross(
        home={"goals_for_l5": None, "goals_against_l5": 1.0},
        away={"goals_for_l5": 1.0, "goals_against_l5": 1.0},
    )
    assert res["available"] is False
    assert res["profile"] is None
    assert res["confidence_delta"] == 0
    assert res["fragility_delta"] == 0
    assert res["_skipped_reason"] == "missing_l5_core_inputs"


def test_failsoft_with_non_dict_inputs():
    res = compute_combined_football_profile_cross(home=None, away="garbage")  # type: ignore[arg-type]
    assert res["available"] is False


# ─────────────────────────────────────────────────────────────────────
# apply_profile_cross_to_pick — symmetric deltas + override
# ─────────────────────────────────────────────────────────────────────
def test_apply_supports_pick_bonus():
    cross = {
        "available": True, "profile": PROFILE_STRONG_UNDER,
        "supports": "UNDER", "confidence_delta": 11, "fragility_delta": 7,
    }
    out = apply_profile_cross_to_pick(
        cross_payload=cross,
        pick_side="under",
        current_confidence=60.0, current_fragility=40.0,
    )
    assert out["applied"] is True
    assert out["interaction"] == "SUPPORTS_PICK"
    # Bonus capped at MAX_CONFIDENCE_BONUS=8
    assert out["confidence_delta_signed"] == MAX_CONFIDENCE_BONUS
    assert out["new_confidence"] == 68.0
    assert out["fragility_delta_signed"] == -7
    assert out["new_fragility"] == 33.0


def test_apply_contradicts_pick_penalty_and_override():
    cross = {
        "available": True, "profile": PROFILE_STRONG_OVER,
        "supports": "OVER", "confidence_delta": 12, "fragility_delta": 8,
    }
    out = apply_profile_cross_to_pick(
        cross_payload=cross,
        pick_side="under",
        pick_market="UNDER_2_5",
        current_confidence=60.0, current_fragility=40.0,
        allow_override=True,
    )
    assert out["applied"] is True
    assert out["interaction"] == "CONTRADICTS_PICK"
    # Penalty capped at MAX_CONFIDENCE_PENALTY=12
    assert out["confidence_delta_signed"] == -MAX_CONFIDENCE_PENALTY
    assert out["new_confidence"] == 48.0
    # Override should fire (STRONG_OVER + delta >= threshold + contradicts)
    assert out["override"] is not None
    assert out["override"]["enabled"] is True
    assert out["override"]["recommended_market"] == "OVER_2_5"
    assert out["override"]["recommended_side"] == "OVER"


def test_apply_override_not_triggered_for_soft_profile():
    cross = {
        "available": True, "profile": PROFILE_BTTS,
        "supports": "BTTS", "confidence_delta": 7, "fragility_delta": 5,
    }
    out = apply_profile_cross_to_pick(
        cross_payload=cross,
        pick_side="under",
        pick_market="UNDER_2_5",
        current_confidence=60.0, current_fragility=40.0,
        allow_override=True,
    )
    # BTTS not in STRONG_OVERRIDE_PROFILES → no override even though contradicts
    assert PROFILE_BTTS not in STRONG_OVERRIDE_PROFILES
    assert out["override"] is None


def test_apply_skipped_when_unavailable():
    out = apply_profile_cross_to_pick(
        cross_payload={"available": False},
        pick_side="under",
        current_confidence=60.0, current_fragility=40.0,
    )
    assert out["applied"] is False
    assert out["interaction"] == "SKIPPED"


def test_apply_skipped_when_no_base_confidence():
    cross = {
        "available": True, "profile": PROFILE_STRONG_UNDER,
        "supports": "UNDER", "confidence_delta": 11, "fragility_delta": 7,
    }
    out = apply_profile_cross_to_pick(
        cross_payload=cross,
        pick_side="under",
        current_confidence=None, current_fragility=None,
    )
    assert out["applied"] is False
    assert out["interaction"] == "SKIPPED"


def test_apply_allow_override_false_does_not_emit_override():
    cross = {
        "available": True, "profile": PROFILE_STRONG_OVER,
        "supports": "OVER", "confidence_delta": 12, "fragility_delta": 8,
    }
    out = apply_profile_cross_to_pick(
        cross_payload=cross,
        pick_side="under",
        pick_market="UNDER_2_5",
        current_confidence=60.0, current_fragility=40.0,
        allow_override=False,
    )
    assert out["applied"] is True
    assert out["override"] is None  # explicitly suppressed


def test_apply_override_threshold_constants_sane():
    assert STRONG_OVERRIDE_THRESHOLD >= MAX_CONFIDENCE_BONUS
    assert PROFILE_STRONG_UNDER in STRONG_OVERRIDE_PROFILES
    assert PROFILE_STRONG_OVER in STRONG_OVERRIDE_PROFILES
    assert PROFILE_CORNERS_OVER in STRONG_OVERRIDE_PROFILES


# ─────────────────────────────────────────────────────────────────────
# build_pattern_alignment_entry — visual_only
# ─────────────────────────────────────────────────────────────────────
def test_build_pattern_alignment_entry_visual_only_flag():
    cross = {
        "available": True, "profile": PROFILE_STRONG_UNDER,
        "supports": "UNDER", "narrative_es": "Cruce favorable a UNDER",
    }
    entry = build_pattern_alignment_entry(cross, "under")
    assert entry is not None
    assert entry["visual_only"] is True
    assert entry["source"] == "football_team_profile_cross"
    assert entry["pattern"] == PROFILE_STRONG_UNDER
    assert entry["supports_pick"] is True


def test_build_pattern_alignment_entry_skipped_when_unavailable():
    assert build_pattern_alignment_entry({"available": False}, "under") is None
    assert build_pattern_alignment_entry({"available": True, "supports": "NEUTRAL", "profile": None}, "over") is None


def test_combined_cross_carries_engine_version():
    res = compute_combined_football_profile_cross(
        home=_home_cold_def_tight(), away=_away_cold_def_tight(),
    )
    assert res["engine_version"].startswith("football_team_profile_cross")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
