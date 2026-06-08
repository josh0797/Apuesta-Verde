"""Unit tests for the rewritten backtest_bullpen_under_hypothesis script.

Phase 44 — backtest v2: fetches from MLB Stats API, integrates the
``services.traffic_score`` composite score for sub-cohort assignment.
"""
from __future__ import annotations

import pytest

from scripts import backtest_bullpen_under_hypothesis as bb


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
DEFAULT_THRESHOLDS = {
    "bullpen_era_high":       5.50,
    "bullpen_era_normal_max": 4.50,
    "traffic_high":           12.0,
    "min_bullpen_innings_7d": 3.0,
    "min_offense_games_7d":   2,
}


def _team_window(
    *, bullpen_era=5.0, bullpen_ip=20.0, n_games=6,
    traffic_score=50, traffic_bucket="MEDIUM_TRAFFIC",
):
    return {
        "n_games":          n_games,
        "bullpen_ip_7d":    bullpen_ip,
        "bullpen_era_7d":   bullpen_era,
        "bullpen_whip_7d":  1.30,
        "offense_traffic_legacy_7d": 11.0,
        "traffic_score_obj": {
            "traffic_score":  traffic_score,
            "traffic_bucket": traffic_bucket,
            "components":     {},
        },
    }


# ─────────────────────────────────────────────────────────────────────
# assign_cohort — primary cohorts (A / B / gap)
# ─────────────────────────────────────────────────────────────────────
class TestAssignCohort:
    def test_cohort_A_when_max_era_above_high_threshold(self):
        home = _team_window(bullpen_era=6.0, traffic_bucket="MEDIUM_TRAFFIC", traffic_score=55)
        away = _team_window(bullpen_era=4.0, traffic_bucket="MEDIUM_TRAFFIC", traffic_score=50)
        primary, sub, signal = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary == "A"
        # Sub-cohort only assigned when bucket is HIGH or LOW; MEDIUM excluded.
        assert sub is None
        assert signal["bullpen_era_7d_max"] == 6.0
        assert signal["traffic_score"] == int(round((55 + 50) / 2))
        assert signal["traffic_bucket"] == "MEDIUM_TRAFFIC"

    def test_cohort_B_when_both_below_normal_threshold(self):
        home = _team_window(bullpen_era=3.5)
        away = _team_window(bullpen_era=4.0)
        primary, sub, _ = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary == "B"
        assert sub is None

    def test_no_cohort_in_gap_zone(self):
        # 4.5 ≤ max_era ≤ 5.5 → exclusion.
        home = _team_window(bullpen_era=5.0)
        away = _team_window(bullpen_era=4.7)
        primary, _, _ = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary is None

    def test_no_cohort_when_bullpen_ip_too_low(self):
        home = _team_window(bullpen_era=6.0, bullpen_ip=1.0)
        away = _team_window(bullpen_era=3.0, bullpen_ip=20.0)
        primary, _, signal = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary is None
        assert signal["reason"] == "low_bullpen_ip_home"

    def test_no_cohort_when_too_few_offensive_games(self):
        home = _team_window(bullpen_era=6.0, n_games=1)
        away = _team_window(bullpen_era=3.0, n_games=6)
        primary, _, signal = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary is None
        assert signal["reason"] == "low_offense_games_home"


# ─────────────────────────────────────────────────────────────────────
# assign_cohort — sub-cohorts driven by composite traffic bucket
# ─────────────────────────────────────────────────────────────────────
class TestAssignSubCohort:
    def test_A1_when_combined_bucket_high(self):
        home = _team_window(bullpen_era=6.5, traffic_score=85, traffic_bucket="HIGH_TRAFFIC")
        away = _team_window(bullpen_era=5.8, traffic_score=80, traffic_bucket="HIGH_TRAFFIC")
        primary, sub, signal = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary == "A"
        assert sub == "A1"
        assert signal["traffic_bucket"] == "HIGH_TRAFFIC"

    def test_A2_when_combined_bucket_low(self):
        home = _team_window(bullpen_era=6.0, traffic_score=20, traffic_bucket="LOW_TRAFFIC")
        away = _team_window(bullpen_era=6.0, traffic_score=15, traffic_bucket="LOW_TRAFFIC")
        primary, sub, signal = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary == "A"
        assert sub == "A2"
        assert signal["traffic_bucket"] == "LOW_TRAFFIC"

    def test_no_sub_when_combined_bucket_medium(self):
        # One HIGH + one LOW → mean lands in MEDIUM → no sub-cohort.
        home = _team_window(bullpen_era=6.5, traffic_score=85, traffic_bucket="HIGH_TRAFFIC")
        away = _team_window(bullpen_era=6.0, traffic_score=20, traffic_bucket="LOW_TRAFFIC")
        primary, sub, signal = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary == "A"
        assert sub is None
        assert signal["traffic_bucket"] == "MEDIUM_TRAFFIC"

    def test_no_sub_when_primary_is_B(self):
        home = _team_window(bullpen_era=3.0, traffic_score=85, traffic_bucket="HIGH_TRAFFIC")
        away = _team_window(bullpen_era=3.5, traffic_score=80, traffic_bucket="HIGH_TRAFFIC")
        primary, sub, _ = bb.assign_cohort(home, away, DEFAULT_THRESHOLDS)
        assert primary == "B"
        assert sub is None


# ─────────────────────────────────────────────────────────────────────
# settle_under
# ─────────────────────────────────────────────────────────────────────
class TestSettleUnder:
    def test_under_win_pays_minus110(self):
        outcome, pnl = bb.settle_under(9.5, 7)
        assert outcome == "won"
        assert pnl == pytest.approx(0.9091, abs=0.001)

    def test_over_loss_returns_minus_one_unit(self):
        outcome, pnl = bb.settle_under(9.5, 12)
        assert outcome == "lost"
        assert pnl == -1.0

    def test_push_when_equal(self):
        outcome, pnl = bb.settle_under(10.0, 10)
        assert outcome == "push"
        assert pnl == 0.0

    def test_void_on_missing_inputs(self):
        outcome, pnl = bb.settle_under(None, 9)
        assert outcome == "void"
        assert pnl == 0.0
        outcome, pnl = bb.settle_under(9.5, None)
        assert outcome == "void"


# ─────────────────────────────────────────────────────────────────────
# aggregate_rows
# ─────────────────────────────────────────────────────────────────────
class TestAggregateRows:
    def test_empty_returns_skeleton(self):
        out = bb.aggregate_rows([], [8.5, 9.5])
        assert out == {"sample_size": 0, "per_line": {}}

    def test_basic_metrics(self):
        rows = [
            {"final_total_runs": 6,  "bullpen_era_7d_max": 6.0, "traffic_score": 75},
            {"final_total_runs": 11, "bullpen_era_7d_max": 6.5, "traffic_score": 80},
            {"final_total_runs": 9,  "bullpen_era_7d_max": 5.8, "traffic_score": 70},
        ]
        out = bb.aggregate_rows(rows, [9.5])
        assert out["sample_size"] == 3
        assert out["avg_final_total_runs"] == pytest.approx((6 + 11 + 9) / 3, abs=0.01)
        per_line = out["per_line"]["9.5"]
        assert per_line["wins"] == 2     # 6 and 9 both < 9.5
        assert per_line["losses"] == 1   # 11 > 9.5
        assert per_line["pushes"] == 0
        assert per_line["hit_rate"] == pytest.approx(2 / 3, abs=0.01)
        # Avg traffic score surfaced for the cohort.
        assert out["avg_traffic_score"] == pytest.approx(75.0, abs=0.5)


# ─────────────────────────────────────────────────────────────────────
# Line distance histogram + parsing helpers
# ─────────────────────────────────────────────────────────────────────
class TestHelpers:
    def test_parse_ip_decimal_outs(self):
        assert bb._parse_ip("5.2") == pytest.approx(5 + 2 / 3, abs=0.001)
        assert bb._parse_ip("0.1") == pytest.approx(1 / 3, abs=0.001)
        assert bb._parse_ip("4.0") == 4.0
        assert bb._parse_ip(None) == 0.0
        assert bb._parse_ip("") == 0.0

    def test_line_distance_histogram(self):
        rows = [
            {"final_total_runs": 9},   # diff = -0.5 → int(-0.5) == 0
            {"final_total_runs": 11},  # diff = +1.5 → +1
            {"final_total_runs": 7},   # diff = -2.5 → -2
            {"final_total_runs": None},
        ]
        hist = bb._line_distance_histogram(rows, 9.5)
        assert hist == {"-2": 1, "+0": 1, "+1": 1}
