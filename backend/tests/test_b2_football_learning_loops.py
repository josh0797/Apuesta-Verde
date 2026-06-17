"""Sprint-B · B2 — Tests for the 4 football learning loops.

Validates:
* The base loop computes hit_rate / ROI / calibration correctly.
* Each market wrapper (draw, corners, btts, totals) reads ONLY its
  own keys — no cross-market data leak.
* Failsoft behaviour when snapshots are partial/empty.
* The specific corners-vs-goals separation the user reported.
"""
from __future__ import annotations

import pytest

from services.football_learning_loop_base import (
    compute_learning_loop_metrics,
    _implied_prob_from_odd,
)
from services.football_draw_learning_loop    import run_draw_learning_loop
from services.football_corners_learning_loop import run_corners_learning_loop
from services.football_btts_learning_loop    import run_btts_learning_loop
from services.football_totals_learning_loop  import run_totals_learning_loop


# Helper to build a settled snapshot.
def _snap(*, draw_prob=None, draw_odd=None, draw_hit=None,
          btts_prob=None, btts_odd=None, btts_hit=None,
          over25_prob=None, over25_odd=None, over25_hit=None,
          corners_prob=None, corners_odd=None, corners_hit=None,
          ):
    return {
        "pre_match_inputs": {
            "draw_probability":              draw_prob,
            "btts_probability":              btts_prob,
            "over25_probability":            over25_prob,
            "corners_over85_probability":    corners_prob,
            "market_odds": {
                "draw":           draw_odd,
                "btts_yes":       btts_odd,
                "over25":         over25_odd,
                "over85_corners": corners_odd,
            },
        },
        "post_match_outputs": {
            "draw_hit":            draw_hit,
            "btts_hit":            btts_hit,
            "over25_hit":          over25_hit,
            "over85_corners_hit":  corners_hit,
        },
    }


# ═══════════════════════════════════════════════════════════════════
# 1. Base utilities
# ═══════════════════════════════════════════════════════════════════
class TestImpliedProb:
    def test_decimal_odd(self):
        assert _implied_prob_from_odd(2.0) == 0.5
        assert _implied_prob_from_odd(1.85) == pytest.approx(0.5405, abs=1e-3)

    def test_invalid_returns_none(self):
        assert _implied_prob_from_odd(None) is None
        assert _implied_prob_from_odd(1.0)  is None  # impossible odd
        assert _implied_prob_from_odd("x")  is None


# ═══════════════════════════════════════════════════════════════════
# 2. Base aggregator
# ═══════════════════════════════════════════════════════════════════
class TestBaseAggregator:
    def test_empty_returns_unavailable(self):
        out = compute_learning_loop_metrics(
            snapshots=[], probability_key="draw_probability",
            market_odd_key="draw", hit_key="draw_hit",
            market_label="DRAW",
        )
        assert out["sample_size"] == 0
        assert out["available"] is False

    def test_skips_snapshots_without_hit_key(self):
        snaps = [_snap(draw_hit=None)]
        out = compute_learning_loop_metrics(
            snapshots=snaps, probability_key="draw_probability",
            market_odd_key="draw", hit_key="draw_hit",
            market_label="DRAW",
        )
        assert out["sample_size"] == 0
        assert out["skipped"] == 1

    def test_hit_rate_basic(self):
        snaps = [
            _snap(draw_hit=True),  _snap(draw_hit=True),
            _snap(draw_hit=False), _snap(draw_hit=False),
        ]
        out = run_draw_learning_loop(snaps)
        assert out["sample_size"] == 4
        assert out["hit_rate"] == 0.5

    def test_roi_calculation(self):
        # 2 hits at odd 3.0 → profit +2 each. 2 misses → -1 each.
        # Total profit = +4 - 2 = +2. ROI = 2/4 = 0.5 (50%).
        snaps = [
            _snap(draw_hit=True,  draw_odd=3.0),
            _snap(draw_hit=True,  draw_odd=3.0),
            _snap(draw_hit=False, draw_odd=3.0),
            _snap(draw_hit=False, draw_odd=3.0),
        ]
        out = run_draw_learning_loop(snaps)
        assert out["roi_if_bet"] == 0.5

    def test_negative_roi(self):
        # 1 hit at 1.5 (profit +0.5) + 3 misses (-3). Total = -2.5.
        # ROI = -2.5/4 = -0.625.
        snaps = [
            _snap(draw_hit=True,  draw_odd=1.5),
            _snap(draw_hit=False, draw_odd=1.5),
            _snap(draw_hit=False, draw_odd=1.5),
            _snap(draw_hit=False, draw_odd=1.5),
        ]
        out = run_draw_learning_loop(snaps)
        assert out["roi_if_bet"] == -0.625

    def test_calibration_gap(self):
        # Model said 50% on each, true hit rate was 50% → gap=0.
        snaps = [
            _snap(draw_prob=0.5, draw_hit=True),
            _snap(draw_prob=0.5, draw_hit=False),
        ]
        out = run_draw_learning_loop(snaps)
        assert out["calibration_gap"] == 0.0
        # Brier score for 0.5 with 0/1 outcomes = 0.25.
        assert out["brier_score"] == 0.25

    def test_percent_inputs_are_normalised(self):
        """When pre_match_inputs.draw_probability is e.g. 25 (percent
        scale), the loop must normalise to 0.25 before computing brier
        and calibration."""
        snaps = [
            _snap(draw_prob=25, draw_hit=True),
            _snap(draw_prob=25, draw_hit=False),
        ]
        out = run_draw_learning_loop(snaps)
        assert out["mean_estimated_prob"] == pytest.approx(0.25)

    def test_edge_predicted_vs_realised(self):
        # Model 0.40 vs market implied 0.25 (odd 4.0) → edge_pred = +0.15.
        # Hit rate 0.5 vs market implied 0.25 → edge_real = +0.25.
        snaps = [
            _snap(draw_prob=0.40, draw_odd=4.0, draw_hit=True),
            _snap(draw_prob=0.40, draw_odd=4.0, draw_hit=False),
        ]
        out = run_draw_learning_loop(snaps)
        assert out["mean_edge_predicted"] == pytest.approx(0.15, abs=1e-3)
        assert out["mean_edge_realised"]  == pytest.approx(0.25, abs=1e-3)


# ═══════════════════════════════════════════════════════════════════
# 3. Anti-leak invariant — corners loop MUST NOT read goals fields
# ═══════════════════════════════════════════════════════════════════
class TestCornersDoesNotConfuseGoals:
    """Pins down the user-reported concern: the corners loop reads
    EXCLUSIVELY from corners keys; goals data is invisible to it."""

    def test_corners_loop_ignores_goals_fields(self):
        # Snapshot has rich goals data but NO corners data.
        snaps = [{
            "pre_match_inputs": {
                "over25_probability": 0.7,
                "btts_probability":   0.6,
                "draw_probability":   0.25,
                # Corners explicitly None.
                "corners_over85_probability": None,
                "market_odds": {
                    "over25":          1.85,
                    "btts_yes":        1.95,
                    "draw":            3.50,
                    "over85_corners":  None,  # No corners odd available.
                },
            },
            "post_match_outputs": {
                "over25_hit":         True,
                "btts_hit":           True,
                "draw_hit":           False,
                "over85_corners_hit": True,
            },
        }]
        out = run_corners_learning_loop(snaps)
        # Hit registered (1/1 = 100%) but probability/odd metrics MUST
        # be None because no corners-specific data was available.
        assert out["sample_size"] == 1
        assert out["hit_rate"]    == 1.0
        assert out["mean_estimated_prob"] is None    # No corners prob.
        assert out["mean_market_implied"] is None    # No corners odd.
        assert out["roi_if_bet"]          is None    # No corners odd.

    def test_corners_loop_reads_only_corners_fields(self):
        # Snapshot has BOTH goals and corners data. Corners loop must
        # only see the corners-tagged fields.
        snaps = [{
            "pre_match_inputs": {
                "over25_probability":          0.99,   # decoy
                "corners_over85_probability":  0.55,   # real corners prob
                "market_odds": {
                    "over25":          1.20,            # decoy goals odd
                    "over85_corners":  1.85,            # real corners odd
                },
            },
            "post_match_outputs": {
                "over25_hit":          True,            # decoy
                "over85_corners_hit":  False,           # corners outcome
            },
        }]
        out = run_corners_learning_loop(snaps)
        assert out["hit_rate"] == 0.0                  # corners DID NOT hit
        assert out["mean_estimated_prob"] == 0.55
        assert out["mean_market_implied"] == pytest.approx(1 / 1.85, abs=1e-3)
        # Loss of 1 unit at odd 1.85 → ROI = -1.0.
        assert out["roi_if_bet"] == -1.0

    def test_totals_loop_reads_only_goals_fields(self):
        """Mirror invariant: the goals/over25 loop ignores corners."""
        snaps = [{
            "pre_match_inputs": {
                "over25_probability":         0.65,
                "corners_over85_probability": 0.99,  # decoy
                "market_odds": {
                    "over25":          1.90,
                    "over85_corners":  1.10,         # decoy
                },
            },
            "post_match_outputs": {
                "over25_hit":         True,
                "over85_corners_hit": False,         # decoy
            },
        }]
        out = run_totals_learning_loop(snaps)
        assert out["hit_rate"] == 1.0
        assert out["mean_estimated_prob"] == 0.65
        assert out["mean_market_implied"] == pytest.approx(1 / 1.90, abs=1e-3)


# ═══════════════════════════════════════════════════════════════════
# 4. Each wrapper returns the right market label
# ═══════════════════════════════════════════════════════════════════
class TestMarketLabels:
    def test_each_wrapper_emits_its_own_label(self):
        snaps = [_snap(draw_hit=True, btts_hit=True,
                       over25_hit=True, corners_hit=True)]
        assert run_draw_learning_loop(snaps)["market"]    == "DRAW"
        assert run_btts_learning_loop(snaps)["market"]    == "BTTS_YES"
        assert run_totals_learning_loop(snaps)["market"]  == "OVER_2_5_GOALS"
        assert run_corners_learning_loop(snaps)["market"] == "CORNERS_OVER_8_5"


# ═══════════════════════════════════════════════════════════════════
# 5. Robustness — never raises
# ═══════════════════════════════════════════════════════════════════
class TestRobustness:
    def test_garbage_inputs_are_silently_skipped(self):
        snaps = [None, "string", 42, {}, _snap(draw_hit=True, draw_odd=2.0)]
        out = run_draw_learning_loop(snaps)
        assert out["sample_size"] == 1
        assert out["skipped"]     >= 3  # None, string, 42 → all skipped

    def test_handles_invalid_odd(self):
        snaps = [_snap(draw_prob=0.3, draw_odd="not-a-number", draw_hit=True)]
        out = run_draw_learning_loop(snaps)
        assert out["sample_size"] == 1
        assert out["roi_if_bet"]  is None
        assert out["mean_market_implied"] is None
