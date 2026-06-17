"""Sprint-D4 · Tests for ROI honesty + bootstrap CI + significance +
sample_status + warnings.

Covers the contract requested by the user:
    * ROI from flat-stake bets is computed correctly
    * ROI is None when no odds available
    * Bootstrap CI excludes 0 for a strongly positive edge
    * Bootstrap CI INCLUDES 0 for coin-flip data (not significant)
    * Small samples flagged with INSUFFICIENT_SAMPLE_DO_NOT_TRUST
    * Closing-odds backtest carries the optimism warning
    * Max-drawdown matches a hand-computed sequence
    * sample_status taxonomy (INSUFFICIENT / SMALL_SAMPLE_CAUTION /
      ADEQUATE_SAMPLE)
    * football-data.co.uk parser sets odds_type and per-row warnings
"""
from __future__ import annotations

import math
import random

import pytest

from services.football_backtest_metrics import (
    compute_backtest_metrics, _max_drawdown, _ci_bootstrap,
    SMALL_SAMPLE_THRESHOLD, SMALL_SAMPLE_CAUTION_THRESHOLD,
    SAMPLE_STATUS_INSUFFICIENT, SAMPLE_STATUS_CAUTION,
    SAMPLE_STATUS_ADEQUATE, _resolve_sample_status,
    W_INSUFFICIENT_SAMPLE, W_SMALL_SAMPLE_CAUTION,
    W_CLOSING_ODDS_OPTIMISTIC, W_NO_ODDS_HITRATE_ONLY,
    W_NOT_SIGNIFICANT, W_ROI_SIGNIFICANTLY_NEGATIVE,
)
from services.football_historical_ingestor import (
    parse_footballdata_csv, ODDS_TYPE_OPENING, ODDS_TYPE_CLOSING,
    ODDS_TYPE_MIXED, ODDS_TYPE_NONE,
)


# ════════════════════════════════════════════════════════════════════════
# Helpers — build synthetic picks for the metrics calculator
# ════════════════════════════════════════════════════════════════════════
def _bet(stake: float, odd: float, hit: bool, **extra) -> dict:
    pnl = (odd - 1.0) * stake if hit else -stake
    row = {
        "date":           extra.get("date", "2024-01-01"),
        "competition":    extra.get("competition", "Test"),
        "home":           "H",
        "away":           "A",
        "odd_draw":       odd,
        "predicted_prob": 0.35,
        "market_prob":    1.0 / odd,
        "edge_pp":        5.0,
        "label":          "VALUE_DRAW_CANDIDATE",
        "stake":          stake,
        "hit":            hit,
        "pnl":            pnl,
        "actual_score":   "1-1" if hit else "1-0",
    }
    row.update(extra)
    return row


def _bt_result(picks: list[dict], **kwargs) -> dict:
    return {
        "market":           "DRAW",
        "min_edge_pp":      4.0,
        "stake_mode":       "flat",
        "use_calibration":  False,
        "walk_forward":     False,
        "picks":            picks,
        "skipped":          [],
        "n_matches_total":  len(picks) + 10,
        **kwargs,
    }


# ════════════════════════════════════════════════════════════════════════
# ROI computation
# ════════════════════════════════════════════════════════════════════════
class TestROIComputation:
    def test_roi_computation_flat_stake(self):
        picks = [
            _bet(stake=100, odd=2.0, hit=True),    # +100
            _bet(stake=100, odd=2.0, hit=False),   # -100
            _bet(stake=100, odd=3.0, hit=True),    # +200
        ]
        m = compute_backtest_metrics(_bt_result(picks))
        assert m["n_bets"] == 3
        assert m["n_won"]  == 2
        assert m["net_pnl"]      == pytest.approx(200.0, abs=1e-4)
        assert m["total_staked"] == pytest.approx(300.0, abs=1e-4)
        assert m["roi"]          == pytest.approx(200.0 / 300.0, abs=1e-4)

    def test_yield_per_bet_average(self):
        picks = [
            _bet(stake=100, odd=2.0, hit=True),    # yield = +1
            _bet(stake=100, odd=2.0, hit=False),   # yield = -1
            _bet(stake=100, odd=3.0, hit=True),    # yield = +2
        ]
        m = compute_backtest_metrics(_bt_result(picks))
        # mean of [1, -1, 2] = 2/3
        assert m["yield_per_bet"] == pytest.approx(2 / 3, abs=1e-4)


# ════════════════════════════════════════════════════════════════════════
# Bootstrap CI + significance
# ════════════════════════════════════════════════════════════════════════
class TestBootstrapCISignificance:
    def test_bootstrap_ci_excludes_zero_for_strong_edge(self):
        """50 bets all winning at 2.0 → ROI = 100%, CI strictly > 0."""
        picks = [_bet(100, 2.0, True) for _ in range(50)]
        m = compute_backtest_metrics(_bt_result(picks))
        assert m["roi"] == pytest.approx(1.0, abs=1e-4)
        assert m["roi_ci_low"] > 0
        assert m["roi_ci_high"] > 0
        assert m["is_roi_significant"] is True

    def test_bootstrap_ci_includes_zero_for_coin_flip(self):
        """40 bets, 50/50, at 2.0 — ROI ≈ 0, CI includes 0."""
        rng = random.Random(7)
        picks = [_bet(100, 2.0, rng.random() < 0.5) for _ in range(40)]
        m = compute_backtest_metrics(_bt_result(picks))
        # CI must straddle 0 (or sit near 0) → not strictly significant.
        assert m["roi_ci_low"] <= 0 or m["is_roi_significant"] is False

    def test_significantly_negative_warning(self):
        """All losers — ROI = -1, CI should be all negative; warn that
        ROI is significantly negative."""
        picks = [_bet(100, 2.0, False) for _ in range(50)]
        m = compute_backtest_metrics(_bt_result(picks))
        assert m["roi"] == pytest.approx(-1.0, abs=1e-4)
        assert m["roi_ci_high"] < 0
        assert W_ROI_SIGNIFICANTLY_NEGATIVE in m["warnings"]
        assert m["is_roi_significant"] is False


# ════════════════════════════════════════════════════════════════════════
# Sample status
# ════════════════════════════════════════════════════════════════════════
class TestSampleStatus:
    @pytest.mark.parametrize("n,expected", [
        (0, SAMPLE_STATUS_INSUFFICIENT),
        (10, SAMPLE_STATUS_INSUFFICIENT),
        (49, SAMPLE_STATUS_INSUFFICIENT),
        (50, SAMPLE_STATUS_CAUTION),
        (100, SAMPLE_STATUS_CAUTION),
        (199, SAMPLE_STATUS_CAUTION),
        (200, SAMPLE_STATUS_ADEQUATE),
        (500, SAMPLE_STATUS_ADEQUATE),
    ])
    def test_resolver(self, n, expected):
        assert _resolve_sample_status(n) == expected

    def test_small_sample_flag_in_metrics(self):
        picks = [_bet(100, 2.0, True) for _ in range(20)]
        m = compute_backtest_metrics(_bt_result(picks))
        assert m["sample_status"] == SAMPLE_STATUS_INSUFFICIENT
        assert W_INSUFFICIENT_SAMPLE in m["warnings"]

    def test_caution_sample(self):
        picks = [_bet(100, 2.0, i % 2 == 0) for i in range(100)]
        m = compute_backtest_metrics(_bt_result(picks))
        assert m["sample_status"] == SAMPLE_STATUS_CAUTION
        assert W_SMALL_SAMPLE_CAUTION in m["warnings"]

    def test_adequate_sample(self):
        picks = [_bet(100, 2.0, i % 2 == 0) for i in range(250)]
        m = compute_backtest_metrics(_bt_result(picks))
        assert m["sample_status"] == SAMPLE_STATUS_ADEQUATE
        assert W_INSUFFICIENT_SAMPLE not in m["warnings"]
        assert W_SMALL_SAMPLE_CAUTION not in m["warnings"]


# ════════════════════════════════════════════════════════════════════════
# Warnings propagation
# ════════════════════════════════════════════════════════════════════════
class TestWarnings:
    def test_closing_odds_warning_propagates(self):
        picks = [_bet(100, 2.0, True, warnings=[W_CLOSING_ODDS_OPTIMISTIC])
                  for _ in range(60)]
        m = compute_backtest_metrics(_bt_result(picks))
        assert W_CLOSING_ODDS_OPTIMISTIC in m["warnings"]

    def test_no_odds_hit_rate_only(self):
        """Build a no-market-style result (predictions populated, no
        odds): ``W_NO_ODDS_HITRATE_ONLY`` must appear."""
        bt = {
            "market":           "DRAW",
            "no_market":        True,
            "min_edge_pp":      None,
            "min_pred_prob_pp": 28.0,
            "stake_mode":       "flat",
            "use_calibration":  True,
            "walk_forward":     True,
            "predictions": [
                {"predicted_prob": 0.3, "hit": True, "fired": True,
                 "label": "VALUE_DRAW_CANDIDATE",
                 "tournament_phase": "GROUP", "is_group_stage": True,
                 "matchday": 1, "group_label": "Group A",
                 "date": "2024-01-01", "home": "H", "away": "A",
                 "actual_score": "1-1"},
            ],
            "picks": [
                {"label": "VALUE_DRAW_CANDIDATE", "hit": True,
                 "tournament_phase": "GROUP", "is_group_stage": True,
                 "predicted_prob": 0.3,
                 "date": "2024-01-01", "home": "H", "away": "A",
                 "actual_score": "1-1"},
            ],
            "skipped": [],
            "n_matches_total": 1,
        }
        m = compute_backtest_metrics(bt)
        assert m["mode"] == "NO_MARKET"
        assert m["roi"] is None
        assert W_NO_ODDS_HITRATE_ONLY in m["warnings"]


# ════════════════════════════════════════════════════════════════════════
# Max drawdown
# ════════════════════════════════════════════════════════════════════════
class TestMaxDrawdown:
    def test_known_sequence(self):
        # cum: +100, 0, +100, -100, +100  → peak=100, lowest after peak=-100
        # max drawdown should be 200.
        pnls = [100, -100, 100, -200, 200]
        # cumulative: 100, 0, 100, -100, 100
        # peak: 100; dd at -100 = 200.
        assert _max_drawdown(pnls) == 200.0

    def test_no_drawdown_when_only_gains(self):
        assert _max_drawdown([10, 20, 30]) == 0.0


# ════════════════════════════════════════════════════════════════════════
# football-data.co.uk parser — odds detection + warnings
# ════════════════════════════════════════════════════════════════════════
class TestFootballDataParser:
    def test_opening_odds_no_warning(self):
        csv_text = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n"
            "17/08/2024,Liverpool,Ipswich,2,0,H,1.40,4.50,8.00\n"
        )
        rows = parse_footballdata_csv(csv_text, competition="EPL 24/25")
        assert len(rows) == 1
        r = rows[0]
        assert r["odds_type"] == ODDS_TYPE_OPENING
        assert r["odd_home"] == 1.40
        assert r["odd_draw"] == 4.50
        assert r["odd_away"] == 8.00
        assert W_CLOSING_ODDS_OPTIMISTIC not in r["warnings"]

    def test_closing_odds_warning_present(self):
        csv_text = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365CH,B365CD,B365CA\n"
            "17/08/2024,Liverpool,Ipswich,2,0,H,1.45,4.40,7.50\n"
        )
        rows = parse_footballdata_csv(csv_text, competition="EPL 24/25")
        r = rows[0]
        assert r["odds_type"] == ODDS_TYPE_CLOSING
        assert r["odd_draw"] == 4.40
        assert W_CLOSING_ODDS_OPTIMISTIC in r["warnings"]

    def test_mixed_odds_default_opening(self):
        csv_text = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
            "B365H,B365D,B365A,B365CH,B365CD,B365CA\n"
            "17/08/2024,Liverpool,Ipswich,2,0,H,1.40,4.50,8.00,1.45,4.40,7.50\n"
        )
        rows = parse_footballdata_csv(csv_text)
        r = rows[0]
        assert r["odds_type"] == ODDS_TYPE_MIXED
        assert r["odd_draw"] == 4.50            # default = opening
        assert r["odd_draw_close"] == 4.40

    def test_prefer_closing_flips_default(self):
        csv_text = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
            "B365H,B365D,B365A,B365CH,B365CD,B365CA\n"
            "17/08/2024,Liverpool,Ipswich,2,0,H,1.40,4.50,8.00,1.45,4.40,7.50\n"
        )
        rows = parse_footballdata_csv(csv_text, prefer_closing=True)
        r = rows[0]
        assert r["odds_type"] == ODDS_TYPE_CLOSING
        assert r["odd_draw"] == 4.40
        assert W_CLOSING_ODDS_OPTIMISTIC in r["warnings"]

    def test_no_odds_does_not_drop_row(self):
        csv_text = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
            "17/08/2024,Liverpool,Ipswich,2,0,H\n"
        )
        rows = parse_footballdata_csv(csv_text)
        assert len(rows) == 1
        r = rows[0]
        assert r["odds_type"] == ODDS_TYPE_NONE
        assert r["odd_draw"] is None
