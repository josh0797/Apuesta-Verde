"""Unit tests for services.football_siege_pressure_guard."""
from __future__ import annotations

from services.football_siege_pressure_guard import (
    RC_DELAYED_CONVERSION_RISK,
    RC_DOMINANT_TEAM_ASSEDIO,
    RC_LATE_GOAL_RISK_HIGH,
    RC_LOW_SCORE_MISLEADING,
    RC_LOW_SCORE_WITH_SIEGE,
    RC_OVER_0_5_LIVE_SUPPORTED,
    RC_SIEGE_PRESSURE_HIGH,
    RC_TWENTY_MINUTES_LEFT,
    RC_UNDER_BLOCKED_BY_PRESSURE,
    RC_UNDER_REJECTED_DESPITE_LOW_SCORE,
    VERDICT_ALLOW_UNDER,
    VERDICT_BLOCK_UNDER,
    VERDICT_DOWNGRADE_UNDER_3,
    evaluate_siege_pressure,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Flamengo vs Cusco profile — must trigger SIEGE + block Under
# ─────────────────────────────────────────────────────────────────────
class TestFlamengoProfile:
    def _flamengo_stats(self):
        # Use final-match snapshot scaled to minute 75 (proportional).
        return {
            "home_stats": {
                "possession": 68.0, "shots": 22, "shots_on_target": 11,
                "dangerous_attacks": 75, "corners": 9, "xg": 2.4,
            },
            "away_stats": {
                "possession": 32.0, "shots": 2, "shots_on_target": 0,
                "dangerous_attacks": 12, "corners": 1, "xg": 0.15,
            },
        }

    def test_late_game_low_score_blocks_under_2_5(self):
        stats = self._flamengo_stats()
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0,
            market="Under 2.5", **stats,
        )
        assert out["siege_pressure_high"] is True
        assert out["dominant_side"] == "home"
        assert "full_profile" in out["triggers"]
        assert "high_xg" in out["triggers"]
        assert out["verdict"] == VERDICT_BLOCK_UNDER
        assert out["is_late_game"] is True
        assert out["low_score"] is True
        assert RC_SIEGE_PRESSURE_HIGH in out["reason_codes"]
        assert RC_DOMINANT_TEAM_ASSEDIO in out["reason_codes"]
        assert RC_LATE_GOAL_RISK_HIGH in out["reason_codes"]
        assert RC_UNDER_BLOCKED_BY_PRESSURE in out["reason_codes"]
        assert any("over 0.5" in m.lower() for m in out["prefer_markets"])
        assert out["ui_message_es"] is not None
        assert "Over 0.5" in out["ui_message_es"]

    def test_late_game_one_zero_still_blocks(self):
        stats = self._flamengo_stats()
        out = evaluate_siege_pressure(
            minute=82, home_score=1, away_score=0,
            market="Under 2.5", **stats,
        )
        assert out["verdict"] == VERDICT_BLOCK_UNDER
        assert RC_UNDER_BLOCKED_BY_PRESSURE in out["reason_codes"]

    def test_under_3_5_downgraded_instead_of_blocked(self):
        stats = self._flamengo_stats()
        out = evaluate_siege_pressure(
            minute=75, home_score=1, away_score=0,
            market="Under 3.5", **stats,
        )
        assert out["verdict"] == VERDICT_DOWNGRADE_UNDER_3
        assert RC_SIEGE_PRESSURE_HIGH in out["reason_codes"]

    def test_low_score_at_minute_60_triggers_twenty_min_rule(self):
        stats = self._flamengo_stats()
        out = evaluate_siege_pressure(
            minute=60, home_score=0, away_score=0,
            market="Under 2.5", **stats,
        )
        assert out["siege_pressure_high"] is True
        assert out["has_20_min_left"] is True
        assert out["verdict"] == VERDICT_BLOCK_UNDER
        assert RC_LOW_SCORE_WITH_SIEGE in out["reason_codes"]
        assert RC_TWENTY_MINUTES_LEFT in out["reason_codes"]
        assert RC_OVER_0_5_LIVE_SUPPORTED in out["reason_codes"]
        assert RC_UNDER_REJECTED_DESPITE_LOW_SCORE in out["reason_codes"]
        assert "Over 0.5" in out["ui_message_es"]


# ─────────────────────────────────────────────────────────────────────
# 2. Tactical low-volume match — Under MUST be allowed
# ─────────────────────────────────────────────────────────────────────
class TestTacticalLowVolume:
    def test_six_total_shots_low_xg_under_allowed(self):
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0, market="Under 2.5",
            home_stats={"possession": 52, "shots": 4, "shots_on_target": 1,
                        "dangerous_attacks": 18, "xg": 0.3},
            away_stats={"possession": 48, "shots": 2, "shots_on_target": 0,
                        "dangerous_attacks": 14, "xg": 0.15},
        )
        assert out["siege_pressure_high"] is False
        assert out["verdict"] == VERDICT_ALLOW_UNDER
        assert RC_SIEGE_PRESSURE_HIGH not in out["reason_codes"]
        assert out["prefer_markets"] == []
        assert out["ui_message_es"] is None


# ─────────────────────────────────────────────────────────────────────
# 3. Possession-only — must NOT over-trigger siege guard
# ─────────────────────────────────────────────────────────────────────
class TestPossessionOnly:
    def test_high_possession_but_low_shots_no_siege(self):
        # 70% possession + only 5 shots, 1 on target → tiki-taka sterile.
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0, market="Under 2.5",
            home_stats={"possession": 70, "shots": 5, "shots_on_target": 1,
                        "dangerous_attacks": 30, "xg": 0.4},
            away_stats={"possession": 30, "shots": 2, "shots_on_target": 1,
                        "dangerous_attacks": 12, "xg": 0.2},
        )
        assert out["siege_pressure_high"] is False
        assert "full_profile"      not in out["triggers"]
        assert "high_xg"           not in out["triggers"]
        assert "dangerous_attacks" not in out["triggers"]
        assert out["verdict"] == VERDICT_ALLOW_UNDER

    def test_high_possession_high_dangerous_but_no_shots(self):
        # 70% possession, 40 dangerous attacks, but only 6 shots and SOT 1
        # → ratio in dangerous attacks high, but shots floor (10) protects us.
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0, market="Under 2.5",
            home_stats={"possession": 70, "shots": 6, "shots_on_target": 1,
                        "dangerous_attacks": 50, "xg": 0.5},
            away_stats={"possession": 30, "shots": 2, "shots_on_target": 1,
                        "dangerous_attacks": 14, "xg": 0.2},
        )
        assert out["siege_pressure_high"] is False
        assert out["verdict"] == VERDICT_ALLOW_UNDER


# ─────────────────────────────────────────────────────────────────────
# Trigger isolation tests
# ─────────────────────────────────────────────────────────────────────
class TestTriggerIsolation:
    def test_high_xg_alone_triggers_siege(self):
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0, market="Under 2.5",
            home_stats={"possession": 55, "shots": 12, "shots_on_target": 5,
                        "dangerous_attacks": 40, "xg": 2.0},
            away_stats={"possession": 45, "shots": 8, "shots_on_target": 3,
                        "dangerous_attacks": 28, "xg": 0.8},
        )
        assert out["siege_pressure_high"] is True
        assert "high_xg" in out["triggers"]
        assert "full_profile" not in out["triggers"]

    def test_dangerous_attacks_trigger_requires_shot_floor(self):
        # 5:1 dangerous-attacks ratio, possession 60%, shots 12 ⇒ trigger.
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0, market="Under 2.5",
            home_stats={"possession": 60, "shots": 12, "shots_on_target": 4,
                        "dangerous_attacks": 60, "xg": 1.1},
            away_stats={"possession": 40, "shots": 8, "shots_on_target": 2,
                        "dangerous_attacks": 12, "xg": 0.5},
        )
        assert out["siege_pressure_high"] is True
        assert "dangerous_attacks" in out["triggers"]


# ─────────────────────────────────────────────────────────────────────
# Score / minute gating
# ─────────────────────────────────────────────────────────────────────
class TestScoreMinuteGating:
    def _siege_stats(self):
        return {
            "home_stats": {"possession": 68, "shots": 22, "shots_on_target": 11,
                           "dangerous_attacks": 75, "xg": 2.4},
            "away_stats": {"possession": 32, "shots": 2, "shots_on_target": 0,
                           "dangerous_attacks": 12, "xg": 0.15},
        }

    def test_siege_but_score_high_does_not_block(self):
        # Siege profile but 3-1 already on the board → not a low-score case.
        out = evaluate_siege_pressure(
            minute=75, home_score=3, away_score=1,
            market="Under 2.5", **self._siege_stats(),
        )
        assert out["siege_pressure_high"] is True
        # The market itself is already dead (over 4 goals scored) but the
        # guard only governs the low-score interaction. Under 2.5 wouldn't
        # be blocked by THIS layer — the dead-line guard catches it
        # downstream.
        assert out["verdict"] == VERDICT_ALLOW_UNDER
        assert RC_LOW_SCORE_MISLEADING not in out["reason_codes"]

    def test_siege_early_minute_does_not_late_block(self):
        # Minute 30 with siege + low score → 20-min rule applies.
        out = evaluate_siege_pressure(
            minute=30, home_score=0, away_score=0,
            market="Under 2.5", **self._siege_stats(),
        )
        assert out["siege_pressure_high"] is True
        assert out["is_late_game"] is False
        assert out["has_20_min_left"] is True
        assert out["verdict"] == VERDICT_BLOCK_UNDER
        assert RC_TWENTY_MINUTES_LEFT in out["reason_codes"]

    def test_no_dominant_side_when_possession_balanced(self):
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0, market="Under 2.5",
            home_stats={"possession": 52, "shots": 15, "shots_on_target": 6,
                        "dangerous_attacks": 50, "xg": 1.5},
            away_stats={"possession": 48, "shots": 14, "shots_on_target": 5,
                        "dangerous_attacks": 48, "xg": 1.4},
        )
        # Possession within 5% → no dominant side → trigger 1 + 3 cannot fire.
        # But trigger 2 (high xG) could fire if dom_xg was set — we set
        # dominant=None so high_xg can't fire either. Good.
        assert out["dominant_side"] is None
        assert out["siege_pressure_high"] is False


# ─────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────
class TestEdgeCases:
    def test_missing_stats_returns_no_siege(self):
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0, market="Under 2.5",
            home_stats={}, away_stats={},
        )
        assert out["siege_pressure_high"] is False
        assert out["verdict"] == VERDICT_ALLOW_UNDER
        assert out["reason_codes"] == []

    def test_over_market_does_not_get_blocked(self):
        # Same siege scenario but the user picks Over 1.5 — no block applied.
        out = evaluate_siege_pressure(
            minute=75, home_score=0, away_score=0, market="Over 1.5",
            home_stats={"possession": 68, "shots": 22, "shots_on_target": 11,
                        "dangerous_attacks": 75, "xg": 2.4},
            away_stats={"possession": 32, "shots": 2, "shots_on_target": 0,
                        "dangerous_attacks": 12, "xg": 0.15},
        )
        assert out["siege_pressure_high"] is True
        # Verdict only governs Under markets; Over passes through unchanged.
        assert out["verdict"] == VERDICT_ALLOW_UNDER
        # Reason codes still surfaced so the UI can show the warning.
        assert RC_SIEGE_PRESSURE_HIGH in out["reason_codes"]

    def test_zero_minute_no_late_or_twenty_block(self):
        out = evaluate_siege_pressure(
            minute=None, home_score=0, away_score=0, market="Under 2.5",
            home_stats={"possession": 68, "shots": 22, "shots_on_target": 11,
                        "dangerous_attacks": 75, "xg": 2.4},
            away_stats={"possession": 32, "shots": 2, "shots_on_target": 0,
                        "dangerous_attacks": 12, "xg": 0.15},
        )
        assert out["siege_pressure_high"] is True
        assert out["is_late_game"] is False
        assert out["has_20_min_left"] is False
        # No minute info → can't trigger time-windowed block.
        assert out["verdict"] == VERDICT_ALLOW_UNDER
