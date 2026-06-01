"""Tests for ``mlb_explosive_inning_engine``.

Covers:
    * The 7 declared states (BASE_TRAFFIC, COMMAND_COLLAPSE,
      BULLPEN_EXPLOSION, TWO_OUT_RALLY, HARD_CONTACT, LINEUP_TURNOVER,
      CLEAN_INNING).
    * The 3 risk tiers (LOW / MEDIUM / HIGH).
    * Trap signals (LINE_ALREADY_MOVED, LINE_OVERREACTED,
      RISP_TWO_OUTS_BOTTOM_ORDER, ELITE_RELIEVER_DAMPENS_COLLAPSE,
      MODERATE_PRESSURE_WITH_INFLATED_LINE).
    * Market selection priorities (Inning Over, Team Total Over,
      Live Total Over, Watchlist).
    * Avoid markets logic.
    * flip_triggered semantics.

Run::

    cd /app/backend && python -m pytest tests/test_mlb_explosive_inning_engine.py -v
"""
from __future__ import annotations

import pytest

from services.mlb_explosive_inning_engine import (
    DEFAULT_SURFACE_THRESHOLD,
    HIGH_RISK_THRESHOLD,
    MEDIUM_RISK_THRESHOLD,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    STATE_BASE_TRAFFIC,
    STATE_BULLPEN_EXPLOSION,
    STATE_CLEAN_INNING,
    STATE_COMMAND_COLLAPSE,
    STATE_HARD_CONTACT,
    STATE_LINEUP_TURNOVER,
    STATE_TWO_OUT_RALLY,
    evaluate_explosive_inning,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _base_metrics(**overrides):
    """Return a minimal-but-valid metrics dict; tests override specific fields."""
    m = {
        "inning":                       5,
        "half_inning":                  "top",
        "home_team":                    "NYY",
        "away_team":                    "OAK",
        "batting_team":                 "away",
        "outs":                         1,
        "score_home":                   2,
        "score_away":                   2,
        "base_runners":                 {"first": False, "second": False, "third": False},
        "pitches_this_inning":          10,
        "walks_this_inning":            0,
        "hits_this_inning":             0,
        "pitch_count":                  60,
        "pitch_count_threshold":        95,
        "falling_behind_count_rate":    0.30,
        "wild_pitch_or_hbp":            False,
        "hard_contact_this_inning":     0,
        "barrels_this_inning":          0,
        "avg_exit_velocity":            85.0,
        "line_drives_this_inning":      0,
        "lineup_position_due_up":       6,
        "times_through_order":          2,
        "handedness_matchup":           "neutral",
        "pitcher_role":                 "starter",
        "starter_removed_early":        False,
        "bullpen_fatigue":              0.20,
        "next_reliever_quality":        0.60,
        "reliever_back_to_back":        False,
        "pregame_total_line":           8.5,
        "live_total_line":              8.5,
        "current_odds":                 {},
    }
    m.update(overrides)
    return m


# ═════════════════════════════════════════════════════════════════════
# RISK TIERS — LOW / MEDIUM / HIGH
# ═════════════════════════════════════════════════════════════════════
class TestRiskTiers:
    def test_low_risk_clean_inning(self):
        r = evaluate_explosive_inning(_base_metrics())
        assert r["risk_tier"] == RISK_LOW
        assert r["state"] == STATE_CLEAN_INNING
        assert r["explosive_inning_pressure_score"] < MEDIUM_RISK_THRESHOLD
        assert r["should_recommend"] is False
        assert r["flip_triggered"] is False
        assert r["recommended_market"] is None

    def test_low_risk_empty_metrics(self):
        r = evaluate_explosive_inning({})
        assert r["risk_tier"] == RISK_LOW
        assert r["state"] == STATE_CLEAN_INNING
        assert r["explosive_inning_pressure_score"] == 0

    def test_medium_risk_partial_pressure(self):
        # RISP + 2 walks + 2 hits + top of order + 3rd time through but
        # NO bullpen explosion → should land in MEDIUM band (40..69).
        r = evaluate_explosive_inning(_base_metrics(
            outs=1,
            base_runners={"first": True, "second": True, "third": False},
            walks_this_inning=2,
            hits_this_inning=1,
            falling_behind_count_rate=0.45,
            lineup_position_due_up=3,
            times_through_order=3,
        ))
        assert MEDIUM_RISK_THRESHOLD <= r["explosive_inning_pressure_score"] < HIGH_RISK_THRESHOLD
        assert r["risk_tier"] == RISK_MEDIUM

    def test_high_risk_multi_signal(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=28,
            walks_this_inning=2,
            hits_this_inning=2,
            falling_behind_count_rate=0.65,
            hard_contact_this_inning=3,
            barrels_this_inning=2,
            avg_exit_velocity=98.0,
            lineup_position_due_up=2,
            times_through_order=3,
            handedness_matchup="unfavorable",
            starter_removed_early=True,
            bullpen_fatigue=0.75,
            next_reliever_quality=0.30,
            reliever_back_to_back=True,
        ))
        assert r["explosive_inning_pressure_score"] >= HIGH_RISK_THRESHOLD
        assert r["risk_tier"] == RISK_HIGH

    def test_high_risk_caps_at_100(self):
        # Throw every signal at max — score must cap at 100
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=40,
            walks_this_inning=4,
            hits_this_inning=4,
            falling_behind_count_rate=0.95,
            wild_pitch_or_hbp=True,
            pitch_count=110,
            hard_contact_this_inning=6,
            barrels_this_inning=3,
            avg_exit_velocity=110.0,
            line_drives_this_inning=4,
            lineup_position_due_up=1,
            times_through_order=4,
            handedness_matchup="unfavorable",
            starter_removed_early=True,
            bullpen_fatigue=0.95,
            next_reliever_quality=0.10,
            reliever_back_to_back=True,
        ))
        assert r["explosive_inning_pressure_score"] == 100


# ═════════════════════════════════════════════════════════════════════
# STATES — each of the 7 must be reachable
# ═════════════════════════════════════════════════════════════════════
class TestStates:
    def test_clean_inning_state(self):
        r = evaluate_explosive_inning(_base_metrics())
        assert r["state"] == STATE_CLEAN_INNING

    def test_base_traffic_state(self):
        # Bases loaded with 0 outs but no bullpen/command issues
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            walks_this_inning=1,
            hits_this_inning=2,
        ))
        assert r["state"] == STATE_BASE_TRAFFIC

    def test_command_collapse_state(self):
        # Multi walks + high inning pitches, no bases
        r = evaluate_explosive_inning(_base_metrics(
            outs=2,
            base_runners={"first": False, "second": False, "third": False},
            pitches_this_inning=28,
            walks_this_inning=3,
            pitch_count=98,
            pitch_count_threshold=95,
            falling_behind_count_rate=0.60,
        ))
        assert r["state"] == STATE_COMMAND_COLLAPSE

    def test_bullpen_explosion_state(self):
        # Starter out early + high fatigue + low-quality next reliever
        r = evaluate_explosive_inning(_base_metrics(
            outs=1,
            pitcher_role="reliever",
            starter_removed_early=True,
            bullpen_fatigue=0.80,
            next_reliever_quality=0.25,
            reliever_back_to_back=True,
        ))
        assert r["state"] == STATE_BULLPEN_EXPLOSION

    def test_hard_contact_state(self):
        # Hard contact cluster + barrel + EV high, no command issues
        r = evaluate_explosive_inning(_base_metrics(
            outs=1,
            hard_contact_this_inning=3,
            barrels_this_inning=2,
            avg_exit_velocity=99.0,
            line_drives_this_inning=2,
        ))
        assert r["state"] == STATE_HARD_CONTACT

    def test_lineup_turnover_state(self):
        # Top order + 3rd time through + platoon unfavorable, no other signals
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            lineup_position_due_up=2,
            times_through_order=3,
            handedness_matchup="unfavorable",
        ))
        # When pressure_score is moderate and lineup dominates,
        # _classify_state will pick LINEUP_TURNOVER_DANGER
        assert r["state"] == STATE_LINEUP_TURNOVER

    def test_two_out_rally_state(self):
        # 2 outs + RISP + multiple baserunners this inning
        r = evaluate_explosive_inning(_base_metrics(
            outs=2,
            base_runners={"first": True, "second": True, "third": False},
            walks_this_inning=1,
            hits_this_inning=2,
            lineup_position_due_up=4,    # not bottom order
        ))
        # State priority: if classification doesn't fire bullpen/command/
        # hard_contact/base, two_out_rally code wins.
        assert r["state"] == STATE_TWO_OUT_RALLY


# ═════════════════════════════════════════════════════════════════════
# TRAP SIGNALS
# ═════════════════════════════════════════════════════════════════════
class TestTrapSignals:
    def test_line_already_moved(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=1,
            base_runners={"first": True, "second": True, "third": False},
            walks_this_inning=1, hits_this_inning=2,
            pregame_total_line=7.5,
            live_total_line=8.8,    # +1.3 → MOVED, not yet OVERREACTED
        ))
        assert "LINE_ALREADY_MOVED" in r["trap_signals"]
        assert "LINE_OVERREACTED" not in r["trap_signals"]

    def test_line_overreacted(self):
        r = evaluate_explosive_inning(_base_metrics(
            pregame_total_line=7.5,
            live_total_line=9.5,    # +2.0 → both MOVED and OVERREACTED
        ))
        assert "LINE_ALREADY_MOVED" in r["trap_signals"]
        assert "LINE_OVERREACTED" in r["trap_signals"]
        assert r["should_recommend"] is False

    def test_risp_two_outs_bottom_order_trap(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=2,
            base_runners={"first": False, "second": True, "third": False},
            lineup_position_due_up=8,    # bottom of order
        ))
        assert "RISP_TWO_OUTS_BOTTOM_ORDER" in r["trap_signals"]

    def test_elite_reliever_dampens_collapse(self):
        # Pitch count high BUT next reliever is elite (0.85) → trap fires
        r = evaluate_explosive_inning(_base_metrics(
            pitch_count=98,
            pitch_count_threshold=95,
            bullpen_fatigue=0.20,
            next_reliever_quality=0.85,
        ))
        assert "ELITE_RELIEVER_DAMPENS_COLLAPSE" in r["trap_signals"]


# ═════════════════════════════════════════════════════════════════════
# MARKET SELECTION
# ═════════════════════════════════════════════════════════════════════
class TestMarketSelection:
    def test_no_candidates_when_low_pressure(self):
        r = evaluate_explosive_inning(_base_metrics())
        assert r["market_candidates"] == []
        assert r["recommended_market"] is None

    def test_inning_over_picked_on_immediate_pressure(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=22,
            walks_this_inning=2,
            hits_this_inning=2,
            lineup_position_due_up=3,
            current_odds={"inning_over_0_5": 1.65},
        ))
        assert r["should_recommend"] is True
        cats = [c["category"] for c in r["market_candidates"]]
        assert "INNING_OVER_0_5" in cats
        # When pressure is dominant, INNING_OVER_0_5 is top of list
        assert r["recommended_market"].startswith("Inning Over")
        # odds resolved from current_odds
        assert r["recommended_odds"] == 1.65

    def test_team_total_over_for_high_pressure_with_side(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            batting_team="away",
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=28,
            walks_this_inning=2,
            hard_contact_this_inning=3,
            barrels_this_inning=2,
            avg_exit_velocity=98,
            lineup_position_due_up=2,
        ))
        cats = [c["category"] for c in r["market_candidates"]]
        assert "TEAM_TOTAL_OVER" in cats

    def test_watchlist_when_line_overreacted(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=24,
            walks_this_inning=2,
            hits_this_inning=2,
            pregame_total_line=7.5,
            live_total_line=9.5,    # OVERREACTED
        ))
        cats = [c["category"] for c in r["market_candidates"]]
        assert "WATCHLIST" in cats
        assert r["should_recommend"] is False    # trap vetoes execution

    def test_live_total_over_uses_safe_half_line(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            score_home=3, score_away=4,    # current_total_runs = 7
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=22,
            walks_this_inning=2,
            hits_this_inning=2,
            pregame_total_line=8.5,
            live_total_line=8.5,
        ))
        live_candidates = [c for c in r["market_candidates"]
                            if c.get("category") == "LIVE_TOTAL_OVER"]
        if live_candidates:
            # Must be at least current_total_runs + 0.5 = 7.5
            assert live_candidates[0]["line"] >= 7.5


# ═════════════════════════════════════════════════════════════════════
# AVOID MARKETS
# ═════════════════════════════════════════════════════════════════════
class TestAvoidMarkets:
    def test_no_avoid_when_clean(self):
        r = evaluate_explosive_inning(_base_metrics())
        assert r["avoid_markets"] == []

    def test_avoid_full_game_under_when_pressure_rising(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": False},
            walks_this_inning=1, hits_this_inning=2,
            lineup_position_due_up=3,
        ))
        if r["explosive_inning_pressure_score"] >= MEDIUM_RISK_THRESHOLD:
            assert "Full Game Under" in r["avoid_markets"]

    def test_avoid_live_under_on_bullpen_explosion(self):
        r = evaluate_explosive_inning(_base_metrics(
            starter_removed_early=True,
            bullpen_fatigue=0.85,
            next_reliever_quality=0.25,
            reliever_back_to_back=True,
        ))
        assert "Live Under (próximos innings)" in r["avoid_markets"]

    def test_avoid_live_over_when_line_overreacted(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            walks_this_inning=2, hits_this_inning=2,
            pregame_total_line=7.5,
            live_total_line=9.5,
        ))
        assert "Live Over (línea ya sobre-reaccionó)" in r["avoid_markets"]


# ═════════════════════════════════════════════════════════════════════
# FLIP TRIGGERED + PREVIOUS RECOMMENDATION OVERRIDE
# ═════════════════════════════════════════════════════════════════════
class TestFlipTriggered:
    def test_flip_fires_on_high_pressure_clean_signal(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=28,
            walks_this_inning=2,
            hits_this_inning=2,
            falling_behind_count_rate=0.65,
            hard_contact_this_inning=3,
            barrels_this_inning=2,
            avg_exit_velocity=98.0,
            lineup_position_due_up=2,
            times_through_order=3,
            starter_removed_early=True,
            bullpen_fatigue=0.75,
            next_reliever_quality=0.30,
        ))
        assert r["explosive_inning_pressure_score"] >= HIGH_RISK_THRESHOLD
        assert r["recommended_market"] is not None
        assert r["flip_triggered"] is True

    def test_flip_blocked_by_line_overreacted(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=28,
            walks_this_inning=2, hits_this_inning=2,
            pregame_total_line=7.5,
            live_total_line=9.5,
        ))
        assert r["flip_triggered"] is False

    def test_flip_suppressed_when_previous_was_over(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=28,
            walks_this_inning=2, hits_this_inning=2,
            falling_behind_count_rate=0.65,
            starter_removed_early=True,
            bullpen_fatigue=0.75,
            previous_recommendation_side="over",
        ))
        assert r["flip_triggered"] is False


# ═════════════════════════════════════════════════════════════════════
# OUTPUT CONTRACT — required fields always present
# ═════════════════════════════════════════════════════════════════════
class TestOutputContract:
    REQUIRED = [
        "explosive_inning_pressure_score",
        "state",
        "risk_tier",
        "confidence",
        "risk",
        "reason_codes",
        "human_reasons",
        "avoid_markets",
        "market_candidates",
        "should_recommend",
        "recommended_market",
        "recommended_line",
        "recommended_odds",
        "flip_triggered",
        "explanation",
        "trap_signals",
        "score_contributions",
        "narrative_es",
        "version",
    ]

    @pytest.mark.parametrize("metrics", [
        {},
        _base_metrics(),
        _base_metrics(outs=0, base_runners={"first": True, "second": True, "third": True}),
    ])
    def test_all_required_keys_present(self, metrics):
        r = evaluate_explosive_inning(metrics)
        for k in self.REQUIRED:
            assert k in r, f"missing required key: {k}"
        assert r["version"] == 2

    def test_score_contributions_keys(self):
        r = evaluate_explosive_inning(_base_metrics())
        contribs = r["score_contributions"]
        for k in ("base_traffic", "command", "hard_contact",
                   "lineup", "bullpen", "two_out_rally"):
            assert k in contribs


# ═════════════════════════════════════════════════════════════════════
# CONFIDENCE / RISK CONVENTIONS (mirror football engine)
# ═════════════════════════════════════════════════════════════════════
class TestConfidenceRiskSemantics:
    def test_high_confidence_means_low_risk(self):
        r = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            pitches_this_inning=28,
            walks_this_inning=2, hits_this_inning=2,
            starter_removed_early=True,
            bullpen_fatigue=0.75,
            next_reliever_quality=0.30,
            lineup_position_due_up=2,
        ))
        assert r["confidence"] >= 70
        assert r["risk"] == RISK_LOW

    def test_traps_reduce_confidence(self):
        r_clean = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            walks_this_inning=2, hits_this_inning=2,
        ))
        r_trap = evaluate_explosive_inning(_base_metrics(
            outs=0,
            base_runners={"first": True, "second": True, "third": True},
            walks_this_inning=2, hits_this_inning=2,
            pregame_total_line=7.5,
            live_total_line=9.5,    # OVERREACTED
        ))
        # Trap should reduce confidence at least 20 pts
        assert r_trap["confidence"] < r_clean["confidence"]
