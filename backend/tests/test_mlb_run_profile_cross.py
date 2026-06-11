"""Tests for mlb_run_profile_cross — Phase 59 L5 vs L15 crossover engine.

Covers:
  * Per-team classification (cold / hot / prevention up / prevention down)
  * Five combined profiles required by the spec:
      STRONG_UNDER_CROSS, LOW_SCORING_CROSS,
      STRONG_OVER_CROSS, HIGH_SCORING_CROSS, MIXED_PROFILE
  * Fail-soft behaviour with missing inputs
  * Symmetric application helper (supports / contradicts pick)
  * Pattern-alignment visual entry (must NOT count toward
    supporting/contradicting counts of CAMBIO 4)
  * Clamps for confidence & fragility
"""
from __future__ import annotations

import pytest

from services.mlb_run_profile_cross import (
    PROFILE_STRONG_UNDER, PROFILE_LOW_SCORING,
    PROFILE_STRONG_OVER, PROFILE_HIGH_SCORING, PROFILE_MIXED,
    classify_team_run_profile,
    compute_combined_run_profile_cross,
    apply_run_profile_cross_to_pick,
    build_pattern_alignment_entry,
)


# ── Per-team classification ─────────────────────────────────────────


def test_team_offense_cooling():
    out = classify_team_run_profile(
        scored_l5=3.2, scored_l15=4.5, allowed_l5=4.0, allowed_l15=4.0,
    )
    assert "TEAM_OFFENSE_COOLING" in out["reason_codes"]
    assert out["is_offense_cold"] is True
    assert out["is_offense_hot"] is False


def test_team_offense_heating():
    out = classify_team_run_profile(
        scored_l5=5.4, scored_l15=4.0, allowed_l5=4.0, allowed_l15=4.0,
    )
    assert "TEAM_OFFENSE_HEATING" in out["reason_codes"]
    assert out["is_offense_hot"] is True
    assert out["is_offense_cold"] is False


def test_team_run_prevention_improving():
    out = classify_team_run_profile(
        scored_l5=4.0, scored_l15=4.0, allowed_l5=3.5, allowed_l15=4.4,
    )
    assert "TEAM_RUN_PREVENTION_IMPROVING" in out["reason_codes"]
    assert out["is_prevention_up"] is True
    assert out["is_prevention_down"] is False


def test_team_run_prevention_weakening():
    out = classify_team_run_profile(
        scored_l5=4.0, scored_l15=4.0, allowed_l5=5.6, allowed_l15=4.5,
    )
    assert "TEAM_RUN_PREVENTION_WEAKENING" in out["reason_codes"]
    assert out["is_prevention_down"] is True


def test_team_below_thresholds_no_codes():
    """Mild swings should NOT trigger team-level RCs."""
    out = classify_team_run_profile(
        scored_l5=4.3, scored_l15=4.5, allowed_l5=4.1, allowed_l15=4.3,
    )
    assert out["reason_codes"] == []


# ── Combined profiles (5 fixtures required by spec) ─────────────────


def test_combined_strong_under_cross():
    """Both offenses cold AND both defenses tight → STRONG_UNDER_CROSS."""
    out = compute_combined_run_profile_cross(
        home_scored_l5=3.2, home_scored_l15=4.1,
        home_allowed_l5=3.5, home_allowed_l15=4.2,
        away_scored_l5=3.4, away_scored_l15=4.5,
        away_allowed_l5=3.6, away_allowed_l15=3.8,
    )
    assert out["available"] is True
    assert out["profile"] == PROFILE_STRONG_UNDER
    assert out["supports"] == "UNDER"
    assert out["confidence_delta"] == 10
    assert out["fragility_delta"] == 6
    assert "STRONG_UNDER_CROSS" in out["reason_codes"]
    assert "BOTH_OFFENSES_LOW_L5" in out["reason_codes"]
    assert "BOTH_TEAMS_ALLOW_LOW_L5" in out["reason_codes"]


def test_combined_low_scoring_cross():
    """Both offenses cold + at least ONE tight defense (not both) → LOW_SCORING_CROSS."""
    out = compute_combined_run_profile_cross(
        home_scored_l5=3.5, home_scored_l15=4.0,
        home_allowed_l5=3.8, home_allowed_l15=4.0,
        away_scored_l5=3.7, away_scored_l15=4.0,
        away_allowed_l5=4.6, away_allowed_l15=4.6,   # away allows > 4.0
    )
    assert out["available"] is True
    assert out["profile"] == PROFILE_LOW_SCORING
    assert out["supports"] == "UNDER"
    assert out["confidence_delta"] == 6
    assert out["fragility_delta"] == 4
    assert "LOW_SCORING_CROSS_SUPPORTS_UNDER" in out["reason_codes"]


def test_combined_strong_over_cross():
    """Both offenses HOT AND both defenses leaky → STRONG_OVER_CROSS."""
    out = compute_combined_run_profile_cross(
        home_scored_l5=5.8, home_scored_l15=4.3,
        home_allowed_l5=5.4, home_allowed_l15=4.2,
        away_scored_l5=5.2, away_scored_l15=4.5,
        away_allowed_l5=5.7, away_allowed_l15=4.0,
    )
    assert out["available"] is True
    assert out["profile"] == PROFILE_STRONG_OVER
    assert out["supports"] == "OVER"
    assert out["confidence_delta"] == 12
    assert out["fragility_delta"] == 8
    assert "STRONG_OVER_CROSS" in out["reason_codes"]
    assert "BOTH_OFFENSES_HIGH_L5" in out["reason_codes"]
    assert "BOTH_TEAMS_ALLOW_HIGH_L5" in out["reason_codes"]


def test_combined_high_scoring_cross_offenses_only():
    """Both offenses HOT but defenses not unified leaky → HIGH_SCORING_CROSS."""
    out = compute_combined_run_profile_cross(
        home_scored_l5=5.4, home_scored_l15=4.5,
        home_allowed_l5=4.0, home_allowed_l15=4.0,
        away_scored_l5=5.6, away_scored_l15=4.6,
        away_allowed_l5=3.5, away_allowed_l15=4.0,
    )
    assert out["available"] is True
    assert out["profile"] == PROFILE_HIGH_SCORING
    assert out["supports"] == "OVER"
    assert out["confidence_delta"] == 8
    assert out["fragility_delta"] == 6
    assert "HIGH_SCORING_CROSS_SUPPORTS_OVER" in out["reason_codes"]


def test_combined_high_scoring_cross_defenses_only():
    """Both defenses leaky (≥5.0) but offenses not both hot → HIGH_SCORING_CROSS."""
    out = compute_combined_run_profile_cross(
        home_scored_l5=4.5, home_scored_l15=4.5,
        home_allowed_l5=5.5, home_allowed_l15=4.0,
        away_scored_l5=4.7, away_scored_l15=4.6,
        away_allowed_l5=5.8, away_allowed_l15=4.2,
    )
    assert out["profile"] == PROFILE_HIGH_SCORING
    assert out["supports"] == "OVER"


def test_combined_mixed_profile():
    """One team cold offense, other team leaky defense → MIXED_PROFILE."""
    out = compute_combined_run_profile_cross(
        home_scored_l5=3.5, home_scored_l15=4.5,   # home cold offense
        home_allowed_l5=4.0, home_allowed_l15=4.0,
        away_scored_l5=5.5, away_scored_l15=4.5,   # away hot offense
        away_allowed_l5=4.0, away_allowed_l15=4.0,
    )
    assert out["available"] is True
    assert out["profile"] == PROFILE_MIXED
    assert out["supports"] == "NEUTRAL"
    assert out["confidence_delta"] == 0
    assert out["fragility_delta"] == 2
    assert "MIXED_RUN_PROFILE_NO_CLEAR_EDGE" in out["reason_codes"]


# ── Fail-soft & edge cases ──────────────────────────────────────────


def test_failsoft_missing_inputs():
    out = compute_combined_run_profile_cross(
        home_scored_l5=None, home_scored_l15=4.1,
        home_allowed_l5=None, home_allowed_l15=4.2,
        away_scored_l5=3.4, away_scored_l15=4.5,
        away_allowed_l5=3.6, away_allowed_l15=3.8,
    )
    assert out["available"] is False
    assert out["confidence_delta"] == 0
    assert out["fragility_delta"] == 0
    assert out["profile"] is None
    assert out["_skipped_reason"] == "missing_l5_inputs"


def test_failsoft_partially_none_l15_still_classifies():
    """If L5 are present and L15 missing, profile should still be derivable
    (it uses L5 thresholds for the cross — L15 is only for per-team deltas)."""
    out = compute_combined_run_profile_cross(
        home_scored_l5=3.2, home_scored_l15=None,
        home_allowed_l5=3.5, home_allowed_l15=None,
        away_scored_l5=3.4, away_scored_l15=None,
        away_allowed_l5=3.6, away_allowed_l15=None,
    )
    assert out["available"] is True
    assert out["profile"] == PROFILE_STRONG_UNDER


def test_no_clear_cross_returns_neutral_no_deltas():
    """Benign game with everyone near 4.5 → no profile, no deltas, no codes."""
    out = compute_combined_run_profile_cross(
        home_scored_l5=4.5, home_scored_l15=4.5,
        home_allowed_l5=4.5, home_allowed_l15=4.5,
        away_scored_l5=4.5, away_scored_l15=4.5,
        away_allowed_l5=4.5, away_allowed_l15=4.5,
    )
    assert out["available"] is True
    assert out["profile"] is None
    assert out["supports"] == "NEUTRAL"
    assert out["confidence_delta"] == 0
    assert out["fragility_delta"] == 0


# ── Symmetric application ──────────────────────────────────────────


def test_apply_supports_under_pick():
    cross = compute_combined_run_profile_cross(
        home_scored_l5=3.2, home_scored_l15=4.1,
        home_allowed_l5=3.5, home_allowed_l15=4.2,
        away_scored_l5=3.4, away_scored_l15=4.5,
        away_allowed_l5=3.6, away_allowed_l15=3.8,
    )
    res = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="under",
        current_confidence=70, current_fragility=40,
    )
    assert res["applied"] is True
    assert res["interaction"] == "SUPPORTS_PICK"
    assert res["confidence_delta_signed"] == 8       # capped at +MAX_CONFIDENCE_BONUS (=8)
    assert res["new_confidence"] == 78.0
    assert res["fragility_delta_signed"] == -6
    assert res["new_fragility"] == 34.0


def test_apply_contradicts_under_pick():
    """STRONG_OVER cross + UNDER pick → contradicts → -12 conf cap, +8 frag."""
    cross = compute_combined_run_profile_cross(
        home_scored_l5=5.8, home_scored_l15=4.3,
        home_allowed_l5=5.4, home_allowed_l15=4.2,
        away_scored_l5=5.2, away_scored_l15=4.5,
        away_allowed_l5=5.7, away_allowed_l15=4.0,
    )
    res = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="under",
        current_confidence=70, current_fragility=40,
    )
    assert res["applied"] is True
    assert res["interaction"] == "CONTRADICTS_PICK"
    assert res["confidence_delta_signed"] == -12     # capped at -MAX_CONFIDENCE_PENALTY
    assert res["new_confidence"] == 58.0
    assert res["fragility_delta_signed"] == 8
    assert res["new_fragility"] == 48.0


def test_apply_supports_over_pick_symmetry():
    """STRONG_OVER + OVER pick should be the symmetric mirror of UNDER+STRONG_UNDER."""
    cross = compute_combined_run_profile_cross(
        home_scored_l5=5.8, home_scored_l15=4.3,
        home_allowed_l5=5.4, home_allowed_l15=4.2,
        away_scored_l5=5.2, away_scored_l15=4.5,
        away_allowed_l5=5.7, away_allowed_l15=4.0,
    )
    res = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="over",
        current_confidence=70, current_fragility=40,
    )
    assert res["interaction"] == "SUPPORTS_PICK"
    assert res["confidence_delta_signed"] == 8       # capped at +8 (bonus cap)
    assert res["fragility_delta_signed"] == -8
    assert res["new_confidence"] == 78.0
    assert res["new_fragility"] == 32.0


def test_apply_neutral_profile_noop():
    cross = compute_combined_run_profile_cross(
        home_scored_l5=4.5, home_scored_l15=4.5,
        home_allowed_l5=4.5, home_allowed_l15=4.5,
        away_scored_l5=4.5, away_scored_l15=4.5,
        away_allowed_l5=4.5, away_allowed_l15=4.5,
    )
    res = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="under",
        current_confidence=70, current_fragility=40,
    )
    assert res["applied"] is False
    assert res["interaction"] == "SKIPPED"
    assert res["new_confidence"] == 70
    assert res["new_fragility"] == 40


def test_apply_confidence_clamp_at_100():
    cross = compute_combined_run_profile_cross(
        home_scored_l5=3.2, home_scored_l15=4.1,
        home_allowed_l5=3.5, home_allowed_l15=4.2,
        away_scored_l5=3.4, away_scored_l15=4.5,
        away_allowed_l5=3.6, away_allowed_l15=3.8,
    )
    res = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="under",
        current_confidence=95, current_fragility=10,
    )
    assert res["new_confidence"] == 100.0      # clamped
    assert res["new_fragility"] == 4.0


def test_apply_fragility_clamp_at_0():
    cross = compute_combined_run_profile_cross(
        home_scored_l5=3.2, home_scored_l15=4.1,
        home_allowed_l5=3.5, home_allowed_l15=4.2,
        away_scored_l5=3.4, away_scored_l15=4.5,
        away_allowed_l5=3.6, away_allowed_l15=3.8,
    )
    res = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="under",
        current_confidence=70, current_fragility=4,
    )
    assert res["new_fragility"] == 0.0         # clamped from 4 - 6


# ── Pattern-alignment visual entry ─────────────────────────────────


def test_pattern_alignment_entry_visual_only_flag():
    cross = compute_combined_run_profile_cross(
        home_scored_l5=3.2, home_scored_l15=4.1,
        home_allowed_l5=3.5, home_allowed_l15=4.2,
        away_scored_l5=3.4, away_scored_l15=4.5,
        away_allowed_l5=3.6, away_allowed_l15=3.8,
    )
    entry = build_pattern_alignment_entry(cross, pick_side="under")
    assert entry is not None
    assert entry["visual_only"] is True            # critical: do NOT count to ratio.
    assert entry["pattern"] == "STRONG_UNDER_CROSS"
    assert entry["side"] == "UNDER"
    assert entry["supports_pick"] is True
    assert entry["source"] == "mlb_run_profile_cross"


def test_pattern_alignment_entry_contradicts_pick():
    cross = compute_combined_run_profile_cross(
        home_scored_l5=5.8, home_scored_l15=4.3,
        home_allowed_l5=5.4, home_allowed_l15=4.2,
        away_scored_l5=5.2, away_scored_l15=4.5,
        away_allowed_l5=5.7, away_allowed_l15=4.0,
    )
    entry = build_pattern_alignment_entry(cross, pick_side="under")
    assert entry["pattern"] == "STRONG_OVER_CROSS"
    assert entry["side"] == "OVER"
    assert entry["supports_pick"] is False
    assert entry["visual_only"] is True


def test_pattern_alignment_entry_none_when_unavailable():
    cross = compute_combined_run_profile_cross(
        home_scored_l5=None, home_scored_l15=None,
        home_allowed_l5=None, home_allowed_l15=None,
        away_scored_l5=None, away_scored_l15=None,
        away_allowed_l5=None, away_allowed_l15=None,
    )
    assert build_pattern_alignment_entry(cross, pick_side="under") is None


def test_pattern_alignment_entry_none_for_neutral_no_profile():
    cross = compute_combined_run_profile_cross(
        home_scored_l5=4.5, home_scored_l15=4.5,
        home_allowed_l5=4.5, home_allowed_l15=4.5,
        away_scored_l5=4.5, away_scored_l15=4.5,
        away_allowed_l5=4.5, away_allowed_l15=4.5,
    )
    assert build_pattern_alignment_entry(cross, pick_side="under") is None


# ── Polarity guard: cross NEVER flips polarity, only modulates confidence. ──


def test_cross_never_overrides_polarity():
    """A STRONG_OVER cross hitting an UNDER pick must NOT change the pick
    side anywhere — it only modulates confidence/fragility."""
    cross = compute_combined_run_profile_cross(
        home_scored_l5=5.8, home_scored_l15=4.3,
        home_allowed_l5=5.4, home_allowed_l15=4.2,
        away_scored_l5=5.2, away_scored_l15=4.5,
        away_allowed_l5=5.7, away_allowed_l15=4.0,
    )
    res = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="under",
        current_confidence=70, current_fragility=40,
    )
    # The helper does NOT return any side flip — only deltas.
    assert "new_side" not in res
    assert "pick_side_changed" not in res
    assert "polarity_flip" not in res


# ── Symmetry sanity: equal magnitude penalty on either side. ────────


@pytest.mark.parametrize("pick_side", ["under", "over"])
def test_symmetric_penalty_magnitude(pick_side):
    """For the SAME cross, bonus and penalty must have the same magnitude
    regardless of which side the pick is on (the only thing that flips is
    the sign of `confidence_delta_signed`). This is what 'simétrica' means
    in the spec: same cross → same |delta|, just sign-flipped depending
    on alignment with the pick."""
    # STRONG_OVER cross — confidence_delta = 12 per spec.
    cross = compute_combined_run_profile_cross(
        home_scored_l5=5.8, home_scored_l15=4.3,
        home_allowed_l5=5.4, home_allowed_l15=4.2,
        away_scored_l5=5.2, away_scored_l15=4.5,
        away_allowed_l5=5.7, away_allowed_l15=4.0,
    )
    res = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side=pick_side,
        current_confidence=70, current_fragility=40,
    )
    if pick_side == "over":
        # supports — bonus capped at MAX_CONFIDENCE_BONUS (+8)
        assert res["interaction"] == "SUPPORTS_PICK"
        assert res["confidence_delta_signed"] == 8
    else:
        # contradicts — penalty capped at MAX_CONFIDENCE_PENALTY (-12)
        assert res["interaction"] == "CONTRADICTS_PICK"
        assert res["confidence_delta_signed"] == -12


def test_same_cross_symmetric_sign_flip():
    """Sanity: the SAME cross applied to both sides should yield deltas
    with equal *absolute magnitude* on the capped side, since both caps
    are tight (bonus=8 cap, penalty=12 cap)."""
    cross = compute_combined_run_profile_cross(
        home_scored_l5=3.2, home_scored_l15=4.1,
        home_allowed_l5=3.5, home_allowed_l15=4.2,
        away_scored_l5=3.4, away_scored_l15=4.5,
        away_allowed_l5=3.6, away_allowed_l15=3.8,
    )
    # STRONG_UNDER cross — raw delta = 10
    res_under = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="under",
        current_confidence=70, current_fragility=40,
    )
    res_over = apply_run_profile_cross_to_pick(
        cross_payload=cross, pick_side="over",
        current_confidence=70, current_fragility=40,
    )
    # raw=10 → bonus capped at +8, penalty capped at -min(10,12)=-10
    assert res_under["confidence_delta_signed"] == 8
    assert res_over["confidence_delta_signed"] == -10
    # The fragility delta is symmetric in magnitude on either side.
    assert abs(res_under["fragility_delta_signed"]) == abs(res_over["fragility_delta_signed"])
