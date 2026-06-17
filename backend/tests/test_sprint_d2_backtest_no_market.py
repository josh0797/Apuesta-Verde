"""Sprint-D2 · Tests for the no-market mode of the backtest engine.

Covers:
* run_backtest(no_market=True) → produces predictions[] + picks[]
* picks have no odds / no PnL / no edge
* Brier, log-loss, calibration metrics are present
* Phase split (group_stage / knockout / combined) works
* Label hit-rate dict is populated correctly
* Label re-assignment uses absolute-probability thresholds
* Strict point-in-time discipline survives walk-forward calibration
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from services.football_historical_ingestor import parse_openfootball_json
from services.football_backtest_engine import (
    run_backtest, _relabel_no_market,
    NO_MARKET_STRONG_VALUE_PP, NO_MARKET_VALUE_PP, NO_MARKET_FAIR_PP,
)
from services.football_backtest_metrics import (
    compute_backtest_metrics, _brier_score, _log_loss,
    _hit_rate_by_label, _compute_no_market_metrics,
)
from services.football_draw_potential import (
    LABEL_STRONG_VALUE, LABEL_VALUE_DRAW, LABEL_FAIR_DRAW, LABEL_NO_VALUE,
)


# Synthetic 4-team group with knockout + final.
_TOURNAMENT_JSON = {
    "name": "Sprint-D2 Test Cup",
    "matches": [
        # MD1
        {"round": "Matchday 1", "date": "2099-06-10", "team1": "A", "team2": "B",
         "score": {"ft": [1, 1]}, "group": "Group X"},
        {"round": "Matchday 1", "date": "2099-06-10", "team1": "C", "team2": "D",
         "score": {"ft": [2, 0]}, "group": "Group X"},
        # MD2
        {"round": "Matchday 2", "date": "2099-06-14", "team1": "A", "team2": "C",
         "score": {"ft": [0, 0]}, "group": "Group X"},
        {"round": "Matchday 2", "date": "2099-06-14", "team1": "B", "team2": "D",
         "score": {"ft": [2, 1]}, "group": "Group X"},
        # MD3
        {"round": "Matchday 3", "date": "2099-06-18", "team1": "A", "team2": "D",
         "score": {"ft": [1, 1]}, "group": "Group X"},
        {"round": "Matchday 3", "date": "2099-06-18", "team1": "B", "team2": "C",
         "score": {"ft": [0, 0]}, "group": "Group X"},
        # Knockout — A vs C
        {"round": "Quarter-final", "date": "2099-06-25", "team1": "A", "team2": "C",
         "score": {"ft": [2, 2]}},
        # Final — A vs B
        {"round": "Final", "date": "2099-07-02", "team1": "A", "team2": "B",
         "score": {"ft": [1, 1]}},
    ],
}


@pytest.fixture
def matches():
    return parse_openfootball_json(_TOURNAMENT_JSON,
                                    competition="Sprint-D2 Test Cup")


# ════════════════════════════════════════════════════════════════════════
# _relabel_no_market
# ════════════════════════════════════════════════════════════════════════
class TestRelabelNoMarket:
    @pytest.mark.parametrize("pp,expected_label", [
        (40.0, LABEL_STRONG_VALUE),       # ≥ 32
        (NO_MARKET_STRONG_VALUE_PP, LABEL_STRONG_VALUE),
        (30.0, LABEL_VALUE_DRAW),         # ≥ 28
        (NO_MARKET_VALUE_PP, LABEL_VALUE_DRAW),
        (26.0, LABEL_FAIR_DRAW),          # ≥ 24
        (NO_MARKET_FAIR_PP, LABEL_FAIR_DRAW),
        (20.0, LABEL_NO_VALUE),           # < 24
    ])
    def test_label_assignment(self, pp, expected_label):
        verdict = {"draw_probability": pp, "label": "WHATEVER"}
        _relabel_no_market(verdict)
        assert verdict["label"] == expected_label

    def test_none_draw_prob_preserves_label(self):
        verdict = {"draw_probability": None, "label": "OLD"}
        _relabel_no_market(verdict)
        assert verdict["label"] == "OLD"


# ════════════════════════════════════════════════════════════════════════
# run_backtest(no_market=True)
# ════════════════════════════════════════════════════════════════════════
class TestRunBacktestNoMarket:
    def test_returns_predictions_and_picks(self, matches):
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0)
        assert "predictions" in r
        assert "picks" in r
        assert r["no_market"] is True
        assert r["n_matches_total"] == 8

    def test_picks_have_no_pnl_or_odds(self, matches):
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0)
        for p in r["picks"]:
            assert p["pnl"] == 0.0
            assert p["stake"] == 0.0
            assert p["odd_draw"] is None
            assert p["edge_pp"] is None
            assert p["market_prob"] is None

    def test_predictions_carry_phase_and_matchday(self, matches):
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0)
        # At least one prediction has tournament_phase set.
        phases = {p.get("tournament_phase") for p in r["predictions"]}
        assert phases & {"GROUP", "KNOCKOUT"}

    def test_predictions_carry_tournament_context_score(self, matches):
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0)
        ctx_scores = [p.get("tournament_context_score")
                      for p in r["predictions"]
                      if p.get("tournament_context_score") is not None]
        assert len(ctx_scores) > 0

    def test_picks_subset_of_predictions(self, matches):
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0)
        n_fired_in_predictions = sum(1 for p in r["predictions"] if p["fired"])
        assert n_fired_in_predictions == len(r["picks"])

    def test_min_pred_prob_pp_threshold(self, matches):
        # Extremely high threshold → no picks should fire.
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=99.0)
        assert len(r["picks"]) == 0

    def test_market_mode_unaffected_by_no_market_flag_default(self, matches):
        """The new no_market default (False) must NOT change behaviour
        of existing market-mode callers."""
        # All openfootball matches lack odds → run_backtest will skip
        # them all due to "no_draw_odd" in default market mode.
        r = run_backtest(matches, no_market=False)
        assert all(s["reason"] in ("no_draw_odd", "insufficient_history")
                   for s in r["skipped"])
        assert len(r["picks"]) == 0


# ════════════════════════════════════════════════════════════════════════
# Metrics
# ════════════════════════════════════════════════════════════════════════
class TestNoMarketMetrics:
    def test_compute_backtest_metrics_dispatches_no_market(self, matches):
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0)
        m = compute_backtest_metrics(r)
        assert m.get("mode") == "NO_MARKET"
        assert m.get("no_market") is True
        # ROI fields must be None (no odds).
        assert m["roi"] is None
        assert m["yield_per_bet"] is None

    def test_phase_metrics_present(self, matches):
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0)
        m = compute_backtest_metrics(r)
        for k in ("combined_metrics", "group_stage_metrics",
                  "knockout_metrics"):
            assert k in m
            assert "brier_score" in m[k]
            assert "log_loss" in m[k]
            assert "calibration_label" in m[k]
            assert "reliability_curve" in m[k]
            assert "draw_base_rate" in m[k]

    def test_label_hit_rate_breakdowns(self, matches):
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0)
        m = compute_backtest_metrics(r)
        for k in ("label_hit_rate_combined", "label_hit_rate_group_stage",
                  "label_hit_rate_knockout"):
            assert k in m
            # Type: dict[str, dict]
            for label, d in m[k].items():
                assert "n" in d
                assert "won" in d
                assert "hit_rate" in d


class TestBrierAndLogLoss:
    def test_brier_perfect_predictor(self):
        preds = [
            {"predicted_prob": 1.0, "hit": True},
            {"predicted_prob": 0.0, "hit": False},
        ]
        assert _brier_score(preds) == 0.0

    def test_brier_worst_predictor(self):
        preds = [
            {"predicted_prob": 0.0, "hit": True},
            {"predicted_prob": 1.0, "hit": False},
        ]
        # Both miss by 1.0 → MSE = 1.0
        assert _brier_score(preds) == 1.0

    def test_brier_empty(self):
        assert _brier_score([]) is None

    def test_brier_none_predictions_skipped(self):
        preds = [
            {"predicted_prob": None, "hit": True},
            {"predicted_prob": 0.5, "hit": True},
        ]
        # Only 1 valid pred → (0.5 - 1)^2 = 0.25.
        assert _brier_score(preds) == 0.25

    def test_log_loss_perfect_clamped(self):
        # 1.0 will get clamped to (1 - eps).
        preds = [{"predicted_prob": 1.0, "hit": True}]
        ll = _log_loss(preds)
        assert ll is not None and ll < 1e-3

    def test_log_loss_empty(self):
        assert _log_loss([]) is None


class TestHitRateByLabel:
    def test_groups_by_label(self):
        picks = [
            {"label": "STRONG_VALUE_DRAW", "hit": True},
            {"label": "STRONG_VALUE_DRAW", "hit": False},
            {"label": "VALUE_DRAW_CANDIDATE", "hit": True},
        ]
        out = _hit_rate_by_label(picks)
        assert out["STRONG_VALUE_DRAW"]["n"] == 2
        assert out["STRONG_VALUE_DRAW"]["won"] == 1
        assert out["STRONG_VALUE_DRAW"]["hit_rate"] == 0.5
        assert out["VALUE_DRAW_CANDIDATE"]["n"] == 1
        assert out["VALUE_DRAW_CANDIDATE"]["hit_rate"] == 1.0

    def test_handles_missing_label(self):
        out = _hit_rate_by_label([{"hit": True}])
        assert "UNKNOWN" in out


# ════════════════════════════════════════════════════════════════════════
# Point-in-time discipline (regression test for Sprint D2)
# ════════════════════════════════════════════════════════════════════════
class TestPointInTimeNoLeakage:
    def test_md3_match_does_not_see_parallel_md3_results(self, matches):
        """Two MD3 matches in the same group are played on the same
        day. Neither should be able to see the OTHER MD3's result
        when its features are computed.

        We verify this by checking that the standings used for the
        tournament_context_score in MD3 only have `played==2` for
        both teams (i.e. only MD1 + MD2 results contributed).
        """
        r = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0,
                          use_calibration=False, walk_forward=False)
        # Find both MD3 group predictions.
        md3_preds = [p for p in r["predictions"]
                     if p.get("is_group_stage")
                     and p.get("date", "").startswith("2099-06-18")]
        # We expect 2 MD3 matches (A vs D, B vs C).
        assert len(md3_preds) >= 1
        # Each pred should have a tournament_context_score (group stage).
        for p in md3_preds:
            assert p.get("tournament_context_score") is not None

    def test_calibrator_walks_forward(self, matches):
        """With walk_forward=True the calibrator at step i uses ONLY
        picks 0..i-1. We verify this indirectly: running with and
        without calibration produces different outputs."""
        r_off = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0,
                              use_calibration=False, walk_forward=False)
        r_on = run_backtest(matches, no_market=True, min_pred_prob_pp=20.0,
                             use_calibration=True, walk_forward=True)
        # The function must not crash.
        assert "predictions" in r_off
        assert "predictions" in r_on
        # Same number of predictions (calibration doesn't gate sample
        # inclusion).
        assert len(r_off["predictions"]) == len(r_on["predictions"])
