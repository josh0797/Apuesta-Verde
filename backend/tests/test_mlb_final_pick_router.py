"""Tests for the final-pick router refinements (Round 3):
   * detect_total_script_conflict — 4 conflict patterns + no-conflict.
   * parse_manual_odds — accepts comma decimal + bounds.
   * calculate_manual_edge — VALUE / FAIR_VALUE / NO_VALUE classification.
   * mlb_recent_form_split._classify_run_trend + _classify_on_base_trend.
   * build_recent_form_payload — delta + trend integration.
"""
from __future__ import annotations

import pytest

from services.mlb_script_conflict import (
    detect_total_script_conflict,
    parse_manual_odds,
    calculate_manual_edge,
)
from services.mlb_recent_form_split import (
    _classify_run_trend,
    _classify_on_base_trend,
    build_recent_form_payload,
)


# ────────────────────────────────────────────────────────────────────
# Script Conflict Detector
# ────────────────────────────────────────────────────────────────────
class TestScriptConflictDetector:
    def test_under_pick_vs_over_lean_high_conflict(self):
        out = detect_total_script_conflict(
            chosen_market={"market": "Under 9.5"},
            deep_script={"overUnderLean": "LEAN OVER", "projected_runs": 9.8},
        )
        assert out["has_conflict"] is True
        assert out["severity"] == "high"
        assert "UNDER_PICK_CONFLICTS_WITH_OVER_SCRIPT" in out["code"]

    def test_over_pick_vs_under_lean_high_conflict(self):
        out = detect_total_script_conflict(
            chosen_market={"market": "Over 8.5"},
            deep_script={"overUnderLean": "LEAN UNDER"},
        )
        assert out["has_conflict"] is True
        assert out["severity"] == "high"

    def test_under_below_projected_runs(self):
        # Under 8.5 but projection = 9.8 → projection > line → conflict.
        out = detect_total_script_conflict(
            chosen_market={"market": "Under 8.5", "recommended_line": 8.5},
            deep_script={"projected_runs": 9.8},
        )
        assert out["has_conflict"] is True
        assert out["severity"] == "high"
        assert out["code"] == "UNDER_BELOW_PROJECTED_RUNS"
        assert out["details"]["projected_runs"] == 9.8
        assert out["details"]["selected_line"] == 8.5
        assert out["details"]["gap"] == 1.3

    def test_under_close_to_projection_medium(self):
        # Under 9.5, projection 9.2 → gap 0.3 → medium.
        out = detect_total_script_conflict(
            chosen_market={"market": "Under 9.5", "recommended_line": 9.5},
            deep_script={"projected_runs": 9.2},
        )
        assert out["has_conflict"] is True
        assert out["severity"] == "medium"
        assert out["code"] == "UNDER_CLOSE_TO_PROJECTED_RUNS"

    def test_no_conflict_safe_under(self):
        out = detect_total_script_conflict(
            chosen_market={"market": "Under 9.5", "recommended_line": 9.5},
            deep_script={"projected_runs": 7.5, "overUnderLean": "LEAN UNDER"},
        )
        assert out["has_conflict"] is False

    def test_over_above_projection_high_conflict(self):
        out = detect_total_script_conflict(
            chosen_market={"market": "Over 9.5", "recommended_line": 9.5},
            deep_script={"projected_runs": 7.2},  # gap = -2.3
        )
        assert out["has_conflict"] is True
        assert out["severity"] == "high"
        assert out["code"] == "OVER_ABOVE_PROJECTED_RUNS"

    def test_missing_inputs_no_crash(self):
        assert detect_total_script_conflict(None, None)["has_conflict"] is False
        assert detect_total_script_conflict({}, {})["has_conflict"] is False
        assert detect_total_script_conflict({"market": "Under 8.5"}, None)["has_conflict"] is False

    def test_line_extracted_from_market_text(self):
        # No recommended_line key — must extract "9.5" from "Under 9.5".
        out = detect_total_script_conflict(
            chosen_market={"market": "Under 9.5"},
            deep_script={"projected_runs": 10.5},
        )
        assert out["has_conflict"] is True
        assert out["code"] == "UNDER_BELOW_PROJECTED_RUNS"


# ────────────────────────────────────────────────────────────────────
# parse_manual_odds
# ────────────────────────────────────────────────────────────────────
class TestParseManualOdds:
    @pytest.mark.parametrize("raw,expected", [
        (1.85,        1.85),
        ("1.85",      1.85),
        ("1,85",      1.85),     # Spanish comma decimal
        ("  2,15  ",  2.15),
        (3,           3.0),
        ("1.01",      1.01),
        (1000,        1000.0),
    ])
    def test_valid_odds(self, raw, expected):
        assert parse_manual_odds(raw) == expected

    @pytest.mark.parametrize("raw", [
        None, "", "abc", "0.85", "1.00", 0, -1, 1001, "garbage",
    ])
    def test_invalid_odds_return_none(self, raw):
        assert parse_manual_odds(raw) is None


# ────────────────────────────────────────────────────────────────────
# calculate_manual_edge
# ────────────────────────────────────────────────────────────────────
class TestCalculateManualEdge:
    def test_value_when_edge_positive(self):
        # Estimated 60%, odds 1.90 → implied 52.6% → edge +7.4%.
        r = calculate_manual_edge(
            estimated_probability=0.60,
            manual_odds=1.90,
        )
        assert r["value_status"] == "VALUE"
        assert r["can_recommend"] is True
        assert r["manual_edge"] == pytest.approx(0.0737, abs=0.01)
        assert r["manual_edge_pct"] > 0

    def test_no_value_when_edge_negative(self):
        # Estimated 40%, odds 2.0 → implied 50% → edge -10%.
        r = calculate_manual_edge(
            estimated_probability=0.40,
            manual_odds=2.0,
        )
        assert r["value_status"] == "NO_VALUE"
        assert r["can_recommend"] is False

    def test_fair_value_in_neutral_band(self):
        # Estimated 50%, odds 2.0 → implied 50% → edge 0.
        r = calculate_manual_edge(
            estimated_probability=0.50,
            manual_odds=2.0,
        )
        assert r["value_status"] == "FAIR_VALUE"
        assert r["can_recommend"] is False

    def test_handles_percentage_input(self):
        # estimated_probability passed as percentage (60.0) instead of 0.60.
        r = calculate_manual_edge(
            estimated_probability=60.0,
            manual_odds=1.90,
        )
        assert r["value_status"] == "VALUE"

    def test_invalid_odds(self):
        r = calculate_manual_edge(
            estimated_probability=0.60,
            manual_odds=0.5,
        )
        assert r["value_status"] == "INVALID"

    def test_no_estimated_probability(self):
        r = calculate_manual_edge(
            estimated_probability=None,
            manual_odds=2.0,
        )
        assert r["value_status"] == "UNKNOWN"
        assert r["manual_implied_probability"] == 0.5


# ────────────────────────────────────────────────────────────────────
# Recent-form split trends
# ────────────────────────────────────────────────────────────────────
class TestRecentFormTrends:
    def test_run_trend_rising(self):
        assert _classify_run_trend(1.30) == "RISING_RUN_ENVIRONMENT"
        assert _classify_run_trend(1.25) == "RISING_RUN_ENVIRONMENT"

    def test_run_trend_declining(self):
        assert _classify_run_trend(-1.30) == "DECLINING_RUN_ENVIRONMENT"
        assert _classify_run_trend(-1.25) == "DECLINING_RUN_ENVIRONMENT"

    def test_run_trend_stable(self):
        assert _classify_run_trend(0.5)   == "STABLE_RUN_ENVIRONMENT"
        assert _classify_run_trend(-0.5)  == "STABLE_RUN_ENVIRONMENT"
        assert _classify_run_trend(0)     == "STABLE_RUN_ENVIRONMENT"

    def test_run_trend_unknown(self):
        assert _classify_run_trend(None) == "UNKNOWN_RUN_ENVIRONMENT"

    def test_on_base_trend_rising(self):
        assert _classify_on_base_trend(1.5) == "RISING_ON_BASE_PRESSURE"

    def test_on_base_trend_declining(self):
        assert _classify_on_base_trend(-1.2) == "DECLINING_ON_BASE_PRESSURE"

    def test_on_base_trend_stable(self):
        assert _classify_on_base_trend(0.4) == "STABLE_ON_BASE_PRESSURE"


class TestBuildRecentFormPayload:
    def _form(self, *, runs_5, runs_15, hits_5=8, hits_15=8, bb_5=3, bb_15=3, hbp_5=0.5, hbp_15=0.5):
        return {
            "runs_scored_avg_last_5":   runs_5,
            "runs_scored_avg_last_15":  runs_15,
            "hits_avg_last_5":          hits_5,
            "hits_avg_last_15":         hits_15,
            "walks_avg_last_5":         bb_5,
            "walks_avg_last_15":        bb_15,
            "hbp_avg_last_5":           hbp_5,
            "hbp_avg_last_15":          hbp_15,
            "times_on_base_avg_last_5":  hits_5 + bb_5 + hbp_5,
            "times_on_base_avg_last_15": hits_15 + bb_15 + hbp_15,
            "obp_last_5":  None,
            "obp_last_15": None,
        }

    def test_rising_environment(self):
        # Home heats up massively, away neutral → total delta > 1.25
        home = self._form(runs_5=6.5, runs_15=4.5)   # +2
        away = self._form(runs_5=5.0, runs_15=4.5)   # +0.5
        payload = build_recent_form_payload(home, away)
        # total_l5 = 11.5, total_l15 = 9.0 → delta = 2.5
        assert payload["recent_run_split"]["total_runs_avg_last_5"]  == 11.5
        assert payload["recent_run_split"]["total_runs_avg_last_15"] == 9.0
        assert payload["recent_run_split"]["total_runs_delta_5_vs_15"] == 2.5
        assert payload["recent_run_trend"] == "RISING_RUN_ENVIRONMENT"

    def test_declining_environment(self):
        home = self._form(runs_5=3.0, runs_15=5.0)
        away = self._form(runs_5=2.5, runs_15=4.5)
        payload = build_recent_form_payload(home, away)
        # total_l5 = 5.5, total_l15 = 9.5 → delta = -4.0
        assert payload["recent_run_trend"] == "DECLINING_RUN_ENVIRONMENT"

    def test_stable_environment(self):
        home = self._form(runs_5=4.8, runs_15=4.5)
        away = self._form(runs_5=4.7, runs_15=4.6)
        payload = build_recent_form_payload(home, away)
        assert payload["recent_run_trend"] == "STABLE_RUN_ENVIRONMENT"

    def test_unknown_when_missing_data(self):
        payload = build_recent_form_payload({}, {})
        assert payload["recent_run_trend"] == "UNKNOWN_RUN_ENVIRONMENT"
        assert payload["recent_run_split"]["total_runs_avg_last_5"] is None

    def test_on_base_block_per_side(self):
        # Home rising OB pressure, away stable.
        home = self._form(runs_5=5, runs_15=5, hits_5=10, hits_15=8, bb_5=4, bb_15=3)
        away = self._form(runs_5=5, runs_15=5, hits_5=8, hits_15=8)
        payload = build_recent_form_payload(home, away)
        ob = payload["on_base_profile"]
        # home TOB_5 = 10+4+0.5 = 14.5; TOB_15 = 8+3+0.5 = 11.5; delta = 3.0
        assert ob["home"]["times_on_base_delta_5_vs_15"] == 3.0
        assert ob["home"]["trend"] == "RISING_ON_BASE_PRESSURE"
        assert ob["away"]["trend"] == "STABLE_ON_BASE_PRESSURE"
