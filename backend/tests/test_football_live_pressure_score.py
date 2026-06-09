"""Tests for the continuous 0-100 Football Live Pressure Score.

Validates the deterministic scoring + verdict thresholds added in Fix 2.
"""
from __future__ import annotations

import pytest

from services.football_live_pressure_score import (
    compute_pressure_score,
    evaluate_pressure_verdict,
    PRESSURE_SCORE_BLANKET_BLOCK,
    PRESSURE_SCORE_CONTEXT_BLOCK,
    PRESSURE_SCORE_DOWNGRADE,
    VERDICT_ALLOW_UNDER,
    VERDICT_BLOCK_UNDER,
    VERDICT_DOWNGRADE_UNDER_3,
    RC_PRESSURE_SCORE_HIGH,
    RC_PRESSURE_BLOCKS_UNDER,
)


class TestPressureScoreScale:
    """Score must be in [0, 100] and deterministic."""

    def test_empty_inputs_score_zero(self):
        """Empty stats → structured 'unavailable' fail-soft response."""
        out = compute_pressure_score(home_stats={}, away_stats={})
        assert out["available"] is False
        assert out["pressure_score"] is None
        assert out["pressure_bucket"] == "UNKNOWN"
        assert out["verdict"] == "ALLOW_UNDER"
        assert "FOOTBALL_PRESSURE_SCORE_UNAVAILABLE" in out["reason_codes"]
        assert out["dominant_side"] is None

    def test_none_inputs_do_not_raise(self):
        """Defensive: None / non-dict inputs return unavailable, no exception."""
        out = compute_pressure_score(home_stats=None, away_stats=None)
        assert out["available"] is False
        out2 = compute_pressure_score(home_stats="bad", away_stats=42)
        assert out2["available"] is False

    def test_full_siege_scenario_scores_near_100(self):
        """Flamengo-like late siege: 27 shots vs 5, 14 SOT vs 2, xG 2.6,
        possession 68%, dangerous attacks 90 vs 18, corners 12 vs 1."""
        home = {
            "shots": 27, "shots_on_target": 14, "possession": 68.0,
            "dangerous_attacks": 90, "corners": 12, "xg": 2.6,
            "big_chances": 6,
        }
        away = {
            "shots": 5, "shots_on_target": 2, "possession": 32.0,
            "dangerous_attacks": 18, "corners": 1, "xg": 0.3,
            "big_chances": 0,
        }
        out = compute_pressure_score(home_stats=home, away_stats=away)
        assert out["pressure_score"] >= 80.0, f"Got {out['pressure_score']}"
        assert out["dominant_side"] == "home"

    def test_balanced_match_scores_low(self):
        home = {"shots": 8, "shots_on_target": 3, "possession": 52, "xg": 0.9, "dangerous_attacks": 40, "corners": 4}
        away = {"shots": 7, "shots_on_target": 3, "possession": 48, "xg": 0.8, "dangerous_attacks": 38, "corners": 3}
        out = compute_pressure_score(home_stats=home, away_stats=away)
        # No clear dominant side → very low score (likely 0).
        assert out["pressure_score"] < 25.0

    def test_score_capped_at_100(self):
        home = {"shots": 100, "shots_on_target": 50, "possession": 95, "xg": 10.0, "dangerous_attacks": 500, "corners": 30, "big_chances": 20}
        away = {"shots": 1, "shots_on_target": 0, "possession": 5, "xg": 0.0, "dangerous_attacks": 1, "corners": 0, "big_chances": 0}
        out = compute_pressure_score(home_stats=home, away_stats=away)
        assert out["pressure_score"] <= 100.0
        assert out["pressure_score"] >= 90.0


class TestComponentBreakdown:
    """Each component must contribute independently and respect its weight."""

    def test_components_sum_matches_score(self):
        home = {"shots": 20, "shots_on_target": 10, "possession": 65, "xg": 2.0, "dangerous_attacks": 80, "corners": 8, "big_chances": 4}
        away = {"shots": 4, "shots_on_target": 2, "possession": 35, "xg": 0.4, "dangerous_attacks": 20, "corners": 2, "big_chances": 1}
        out = compute_pressure_score(home_stats=home, away_stats=away)
        comps = out["components"]
        total_pts = sum(c["points"] for c in comps.values())
        # Allow small rounding tolerance.
        assert abs(total_pts - out["pressure_score"]) < 0.5

    def test_xg_alone_drives_partial_score(self):
        # Only xG present — should contribute up to ~20 pts max.
        home = {"xg": 2.5, "possession": 60, "shots": 1}
        away = {"xg": 0.1, "possession": 40, "shots": 0}
        out = compute_pressure_score(home_stats=home, away_stats=away)
        assert out["components"]["xg"]["points"] >= 18.0
        assert out["pressure_score"] >= 18.0


class TestVerdictMapping:
    def test_score_above_75_blocks_under(self):
        v = evaluate_pressure_verdict(
            pressure_score=80, market="Under 2.5",
            minute=85, home_score=0, away_score=0,
        )
        assert v["verdict"] == VERDICT_BLOCK_UNDER
        assert RC_PRESSURE_SCORE_HIGH in v["reason_codes"]
        assert RC_PRESSURE_BLOCKS_UNDER in v["reason_codes"]
        assert v["ui_message_es"] is not None

    def test_score_above_60_late_low_blocks_under(self):
        v = evaluate_pressure_verdict(
            pressure_score=65, market="Under 1.5",
            minute=80, home_score=0, away_score=0,
        )
        assert v["verdict"] == VERDICT_BLOCK_UNDER

    def test_score_above_60_early_does_not_block_unless_20min_left(self):
        # Min 30, 60 min left → satisfies 20_min_left rule → block.
        v = evaluate_pressure_verdict(
            pressure_score=65, market="Under 2.5",
            minute=30, home_score=0, away_score=0,
        )
        assert v["verdict"] == VERDICT_BLOCK_UNDER

    def test_score_above_60_no_low_score_allows_under(self):
        v = evaluate_pressure_verdict(
            pressure_score=65, market="Under 3.5",
            minute=80, home_score=2, away_score=1,
        )
        # current_total=3 > low_score_max, so block-context not triggered.
        assert v["verdict"] != VERDICT_BLOCK_UNDER

    def test_score_above_45_low_score_downgrades(self):
        v = evaluate_pressure_verdict(
            pressure_score=50, market="Under 3.5",
            minute=70, home_score=1, away_score=0,
        )
        assert v["verdict"] == VERDICT_DOWNGRADE_UNDER_3

    def test_score_below_45_allows_under(self):
        v = evaluate_pressure_verdict(
            pressure_score=30, market="Under 2.5",
            minute=85, home_score=0, away_score=0,
        )
        assert v["verdict"] == VERDICT_ALLOW_UNDER

    def test_score_blanket_block_applies_at_any_minute(self):
        v = evaluate_pressure_verdict(
            pressure_score=85, market="Under 2.5",
            minute=10, home_score=0, away_score=0,
        )
        # Score >= 75 blocks regardless of minute.
        assert v["verdict"] == VERDICT_BLOCK_UNDER

    def test_over_market_not_blocked_by_pressure(self):
        v = evaluate_pressure_verdict(
            pressure_score=85, market="Over 1.5",
            minute=80, home_score=0, away_score=0,
        )
        assert v["verdict"] != VERDICT_BLOCK_UNDER


class TestBackCompat:
    """The legacy guard must still expose pressure_score in its output."""

    def test_legacy_evaluate_siege_pressure_includes_score(self):
        from services.football_siege_pressure_guard import evaluate_siege_pressure
        out = evaluate_siege_pressure(
            minute=85, home_score=0, away_score=0,
            home_stats={"shots": 20, "shots_on_target": 10, "possession": 68, "xg": 2.2, "dangerous_attacks": 80, "corners": 8},
            away_stats={"shots": 4,  "shots_on_target": 2,  "possession": 32, "xg": 0.3, "dangerous_attacks": 20, "corners": 2},
            market="Under 2.5",
        )
        # Legacy fields preserved.
        assert "siege_pressure_high" in out
        assert "triggers" in out
        # New fields appended.
        assert "pressure_score" in out
        assert "pressure_components" in out
        assert "pressure_verdict" in out
        assert isinstance(out["pressure_score"], (int, float))
        assert 0.0 <= out["pressure_score"] <= 100.0

    def test_legacy_evaluate_siege_pressure_with_empty_stats_does_not_raise(self):
        """Regression: pressure_score fail-soft must NOT break the legacy
        siege guard when live stats are missing (e.g. pregame ingestion
        flow). Previously a bug here was suspected to cause the
        'football generation stuck at 5%' issue."""
        from services.football_siege_pressure_guard import evaluate_siege_pressure
        # Empty stats — simulates pregame / no-live-stats path.
        out = evaluate_siege_pressure(
            minute=None, home_score=0, away_score=0,
            home_stats={}, away_stats={}, market=None,
        )
        # Legacy + new fields both present and consistent.
        assert out["siege_pressure_high"] is False
        assert out["verdict"] == "ALLOW_UNDER"
        # New: unavailable signalling.
        assert out.get("pressure_available") is False
        assert out["pressure_score"] is None
        assert out["pressure_verdict"] == "ALLOW_UNDER"
        assert "FOOTBALL_PRESSURE_SCORE_UNAVAILABLE" in (out.get("pressure_reason_codes") or [])

    def test_legacy_evaluate_siege_pressure_with_garbage_stats_does_not_raise(self):
        """Defensive: pathological inputs must NEVER raise. The pregame
        ingestion fetches stats from external providers — any garbage
        in the live_stats payload (e.g. None, lists, strings) must
        gracefully degrade to 'unavailable' instead of raising."""
        from services.football_siege_pressure_guard import evaluate_siege_pressure
        out = evaluate_siege_pressure(
            minute=10, home_score=1, away_score=0,
            home_stats={"shots": "n/a", "possession": None},
            away_stats={"shots": [], "possession": "?"},
            market="Under 2.5",
        )
        # Legacy verdict still computed (graceful degradation).
        assert out["verdict"] in ("ALLOW_UNDER", "BLOCK_UNDER", "DOWNGRADE_UNDER_3_5")
        # New: still returns a structured payload, never raises.
        assert "pressure_score" in out
