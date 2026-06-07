"""Tests for services/line_learning_engine.py (Phase 42, Entrega A).

Covers all 9-feature primitives the MVP exposes:
  * line_distance + is_user_more_protected (Features 3 + 5)
  * classify with all 6 buckets + reason codes (Feature 4)
  * build_learning_sample + observe_only mode (Features 1 + 2 + 9)
  * compute_weighted_recommendation_bias (Feature 9)
"""
import os

import pytest

from services import line_learning_engine as lle


# ─────────────────────────────────────────────────────────────────────
# line_distance + protection
# ─────────────────────────────────────────────────────────────────────
def test_compute_line_distance_basic():
    assert lle.compute_line_distance(engine_line=9.5, user_line=10.0) == 0.5
    assert lle.compute_line_distance(engine_line=8.5, user_line=10.0) == 1.5
    assert lle.compute_line_distance(engine_line=10.0, user_line=10.0) == 0.0


def test_compute_line_distance_missing_returns_none():
    assert lle.compute_line_distance(engine_line=None, user_line=10.0) is None
    assert lle.compute_line_distance(engine_line=9.5, user_line=None) is None


def test_is_user_more_protected_under():
    # Under: larger line is safer.
    assert lle.is_user_more_protected(line_distance=0.5, is_under=True) is True
    assert lle.is_user_more_protected(line_distance=-0.5, is_under=True) is False


def test_is_user_more_protected_over():
    # Over: smaller line is safer.
    assert lle.is_user_more_protected(line_distance=-0.5, is_under=False) is True
    assert lle.is_user_more_protected(line_distance=0.5, is_under=False) is False


def test_is_user_more_protected_same_line():
    assert lle.is_user_more_protected(line_distance=0.0, is_under=True) is False


# ─────────────────────────────────────────────────────────────────────
# Classification — the 6 buckets
# ─────────────────────────────────────────────────────────────────────
def test_classify_push_saved_user_more_protected():
    """The canonical case from the spec: engine Under 9.5 lost; user Under 10 pushed."""
    out = lle.classify(
        engine_line=9.5, user_line=10.0,
        engine_projection=7.8,
        final_value=10,
        is_under=True,
    )
    assert out["classification"] == lle.CLASS_PUSH_SAVED
    assert lle.RC_PUSH_SAVED_BY_LINE in out["reason_codes"]
    assert lle.RC_AGGRESSIVE_LINE_TOO_TIGHT in out["reason_codes"]
    assert out["line_distance"] == 0.5
    assert out["user_more_protected"] is True


def test_classify_near_miss_when_engine_lost_by_half():
    """Engine Under 9.5 lost when total=10 — half-run miss."""
    out = lle.classify(
        engine_line=9.5, user_line=9.5,
        engine_projection=7.8,
        final_value=10,
        is_under=True,
    )
    assert out["classification"] == lle.CLASS_NEAR_MISS
    assert lle.RC_LOST_BY_HALF_RUN in out["reason_codes"]


def test_classify_safe_line_hit_when_engine_won_comfortably():
    out = lle.classify(
        engine_line=10.5, user_line=10.5,
        engine_projection=7.8,
        final_value=7,
        is_under=True,
    )
    assert out["classification"] == lle.CLASS_SAFE_LINE_HIT
    assert lle.RC_SAFE_LINE_SURVIVED in out["reason_codes"]


def test_classify_exact_hit_when_within_half_step():
    out = lle.classify(
        engine_line=9.5, user_line=9.5,
        engine_projection=8.5,
        final_value=9,                     # within 0.5 of line, won
        is_under=True,
    )
    assert out["classification"] == lle.CLASS_EXACT_HIT


def test_classify_aggressive_line_miss_user_won():
    """Engine Under 9.5 lost; user Under 10.5 won → aggressive line bias."""
    out = lle.classify(
        engine_line=9.5, user_line=10.5,
        engine_projection=7.8,
        final_value=10,
        is_under=True,
    )
    assert out["classification"] == lle.CLASS_AGGRESSIVE_LINE_MISS
    assert lle.RC_AGGRESSIVE_LINE_TOO_TIGHT in out["reason_codes"]
    assert lle.RC_LINE_BIAS_AGGRESSIVE in out["reason_codes"]


def test_classify_profile_wrong_overwhelming_miss():
    """Engine projected 6 runs, actually had 13 → game-read was wrong."""
    out = lle.classify(
        engine_line=8.5, user_line=8.5,
        engine_projection=6.0,
        final_value=13,
        is_under=True,
    )
    assert out["classification"] == lle.CLASS_PROFILE_WRONG
    assert lle.RC_OVERWHELMING_PROJECTION_MISS in out["reason_codes"]


def test_classify_user_agreed_with_engine_zero_distance():
    out = lle.classify(
        engine_line=9.5, user_line=9.5,
        engine_projection=7.8,
        final_value=8,
        is_under=True,
    )
    assert lle.RC_USER_AGREED_WITH_ENGINE in out["reason_codes"]


def test_classify_never_raises_on_bad_input():
    out = lle.classify(
        engine_line=None, user_line=None,
        engine_projection=None, final_value=None,
        is_under=None,
    )
    assert out["classification"] == lle.CLASS_UNDEFINED


# ─────────────────────────────────────────────────────────────────────
# build_learning_sample
# ─────────────────────────────────────────────────────────────────────
def test_build_learning_sample_full_shape():
    sample = lle.build_learning_sample(
        user_id="u1", match_id="m1", sport="baseball", market_type="total_runs",
        engine_market="total_runs_under", engine_selection="Under 9.5",
        engine_line=9.5, engine_odds=1.85, engine_projection=7.8,
        engine_outcome="lost",
        user_market="total_runs_under", user_selection="Under 10.0",
        user_line=10.0, user_odds=1.26, user_outcome="push",
        final_value=10,
        cohort_sample_count=4,
    )
    assert sample["classification"] == lle.CLASS_PUSH_SAVED
    assert sample["line_distance"] == 0.5
    assert sample["engine"]["line"] == 9.5
    assert sample["user_actual"]["line"] == 10.0
    assert sample["model_weight"] == 0.7  # default
    assert 0.0 <= sample["feedback_weight"] <= 1.0
    assert sample["observe_only"] is True   # cohort=5 < threshold=30
    assert sample["min_samples_threshold"] == 30
    assert sample["summary_es"]
    assert sample["summary_en"]


def test_build_learning_sample_observe_only_flips_at_threshold():
    sample = lle.build_learning_sample(
        user_id="u1", match_id="m2", sport="baseball", market_type="total_runs",
        engine_market="total_runs_under", engine_selection="Under 9.5",
        engine_line=9.5, engine_odds=1.85, engine_projection=7.8,
        engine_outcome="lost",
        user_market="total_runs_under", user_selection="Under 10.0",
        user_line=10.0, user_odds=1.26, user_outcome="push",
        final_value=10,
        cohort_sample_count=29,   # this one will be #30
    )
    assert sample["observe_only"] is False
    assert sample["cohort_sample_count"] == 30


def test_build_learning_sample_respects_env_weights(monkeypatch):
    monkeypatch.setenv("LINE_LEARNING_MODEL_WEIGHT", "0.6")
    monkeypatch.setenv("LINE_LEARNING_FEEDBACK_WEIGHT", "0.4")
    sample = lle.build_learning_sample(
        user_id="u", match_id="x", sport="baseball", market_type="total_runs",
        engine_market="total_runs_under", engine_selection="Under 9.5",
        engine_line=9.5, engine_odds=1.85,
        final_value=8,
        engine_outcome="won",
        cohort_sample_count=0,
    )
    assert sample["model_weight"] == 0.6
    assert sample["feedback_weight"] == 0.4


# ─────────────────────────────────────────────────────────────────────
# compute_weighted_recommendation_bias
# ─────────────────────────────────────────────────────────────────────
def test_bias_inactive_below_threshold():
    out = lle.compute_weighted_recommendation_bias(cohort_stats={"sample_size": 10})
    assert out["active"] is False
    assert out["recommendation"] == "NEUTRAL"
    assert "observe-only" in out["summary_es"]


def test_bias_protected_when_aggressive_dominates():
    out = lle.compute_weighted_recommendation_bias(cohort_stats={
        "sample_size":                40,
        "aggressive_line_miss_rate":  0.40,
        "push_saved_rate":            0.15,
        "near_miss_rate":             0.10,
        "safe_line_hit_rate":         0.20,
        "average_line_adjustment":    0.5,
    })
    assert out["active"] is True
    assert out["recommendation"] == "PROTECTED"
    assert out["line_bias"] > 0


def test_bias_value_when_safe_dominates():
    out = lle.compute_weighted_recommendation_bias(cohort_stats={
        "sample_size":                40,
        "aggressive_line_miss_rate":  0.05,
        "push_saved_rate":            0.05,
        "near_miss_rate":             0.05,
        "safe_line_hit_rate":         0.60,
        "average_line_adjustment":    0.5,
    })
    assert out["active"] is True
    assert out["recommendation"] == "VALUE"
    assert out["line_bias"] < 0


def test_bias_never_raises_on_bad_input():
    out = lle.compute_weighted_recommendation_bias(cohort_stats=None)
    assert out["active"] is False


# ─────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────
def test_summary_spanish_includes_push_saved_marker():
    classification = lle.classify(
        engine_line=9.5, user_line=10.0,
        engine_projection=7.8, final_value=10, is_under=True,
    )
    s = lle.build_summary(classification, lang="es")
    assert "push" in s.lower() or "bankroll" in s.lower()


def test_summary_english_includes_line_distance():
    classification = lle.classify(
        engine_line=9.5, user_line=10.0,
        engine_projection=7.8, final_value=10, is_under=True,
    )
    s = lle.build_summary(classification, lang="en")
    assert "+0.5" in s or "line distance" in s.lower()
