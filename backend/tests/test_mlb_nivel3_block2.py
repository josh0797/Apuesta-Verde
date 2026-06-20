"""
NIVEL 3 — Bloque 2 §2/§3/§4 tests.

Cubre:
  * mlb_tail_calibration: redistribución de masa, P90 recalibration,
    bucket selection, conservación de probabilidades.
  * mlb_threshold_over_model: contract, monotonía, confidence ≤ 60.
  * mlb_distribution_threshold_blender: blending por confidence,
    divergence flags, high-variance boost, partial-data cut.
"""

from __future__ import annotations

import pytest

from services.mlb_tail_calibration import (
    BUCKET_EXTREME, BUCKET_HIGH, BUCKET_LOW, BUCKET_MEDIUM,
    calibrate_tail_probabilities,
)
from services.mlb_threshold_over_model import (
    MAX_HEURISTIC_CONFIDENCE,
    MODEL_VERSION,
    SUPPORTED_LINES as TM_LINES,
    predict_threshold_probabilities,
)
from services.mlb_distribution_threshold_blender import (
    CONFIDENCE_HIGH, CONFIDENCE_MED,
    combine_distribution_and_threshold_model,
)


# ─────────────────────────────────────────────────────────────────────
# §2 — Tail Calibration
# ─────────────────────────────────────────────────────────────────────
def _base_distribution(mu=8.5):
    """Hand-crafted distribution payload mimicking mixer output."""
    return {
        "lambda": mu,
        "distribution_family": "POISSON",
        "probabilities": {
            "over_6_5":  0.85, "under_6_5":  0.15,
            "over_7_5":  0.72, "under_7_5":  0.28,
            "over_8_5":  0.55, "under_8_5":  0.45,
            "over_9_5":  0.38, "under_9_5":  0.62,
            "over_10_5": 0.24, "under_10_5": 0.76,
            "over_11_5": 0.14, "under_11_5": 0.86,
            "over_12_5": 0.07, "under_12_5": 0.93,
            "over_13_5": 0.04, "under_13_5": 0.96,
            "over_14_5": 0.02, "under_14_5": 0.98,
        },
        "percentiles": {"p10": 4, "p25": 6, "p50": 8, "p75": 11,
                        "p90": 13, "p95": 15, "p99": 18},
    }


class TestTailCalibration:
    def test_no_risk_signals_no_calibration(self):
        out = calibrate_tail_probabilities(_base_distribution(), {})
        assert out["tail_calibration_applied"] is False
        assert out["tail_multiplier"] == 1.0
        assert out["tail_risk_bucket"] == BUCKET_LOW

    def test_one_signal_medium_bucket(self):
        ctx = {
            "starter_volatility": {
                "home": {"starter_volatility_score": 75},
                "away": {"starter_volatility_score": 40},
            },
        }
        out = calibrate_tail_probabilities(_base_distribution(), ctx)
        assert out["tail_calibration_applied"] is True
        assert out["tail_risk_bucket"] == BUCKET_MEDIUM
        assert 1.10 <= out["tail_multiplier"] <= 1.20

    def test_three_signals_high_bucket(self):
        ctx = {
            "starter_volatility": {
                "home": {"starter_volatility_score": 80},
                "away": {"starter_volatility_score": 75},
            },
            "first_inning_collapse": {
                "home": {"first_inning_collapse_score": 70},
                "away": {"first_inning_collapse_score": 30},
            },
            "lineup_explosiveness": {
                "home": {"lineup_explosiveness_score": 75},
                "away": {"lineup_explosiveness_score": 60},
            },
        }
        out = calibrate_tail_probabilities(_base_distribution(), ctx)
        assert out["tail_calibration_applied"] is True
        assert out["tail_risk_bucket"] == BUCKET_HIGH
        assert "HIGH_VARIANCE_TAIL_EXPANSION" in out["reason_codes"]

    def test_six_signals_extreme_bucket(self):
        ctx = {
            "starter_volatility": {"home": {"starter_volatility_score": 90}, "away": {}},
            "first_inning_collapse": {"home": {"first_inning_collapse_score": 80}, "away": {}},
            "lineup_explosiveness": {"home": {"lineup_explosiveness_score": 85}, "away": {}},
            "bullpen_stress": {"home": {"bullpen_stress_score": 80}, "away": {}},
            "domino_risk": {"home": {"domino_risk_score": 75}, "away": {}},
            "recent_offense_home": {"bucket": "EXPLOSIVE"},
        }
        out = calibrate_tail_probabilities(_base_distribution(), ctx)
        assert out["tail_risk_bucket"] == BUCKET_EXTREME
        assert "EXTREME_TAIL_EXPANSION" in out["reason_codes"]

    def test_tail_probs_increase_after_calibration(self):
        ctx = {
            "starter_volatility": {"home": {"starter_volatility_score": 90}, "away": {}},
            "first_inning_collapse": {"home": {"first_inning_collapse_score": 80}, "away": {}},
            "lineup_explosiveness": {"home": {"lineup_explosiveness_score": 85}, "away": {}},
        }
        out = calibrate_tail_probabilities(_base_distribution(), ctx)
        before = out["before"]["probabilities"]
        after  = out["after"]["probabilities"]
        # P(Over 11.5) and P(Over 12.5) must increase.
        assert after["over_11_5"] >= before["over_11_5"]
        assert after["over_12_5"] >= before["over_12_5"]

    def test_probabilities_conserved_per_line(self):
        ctx = {
            "starter_volatility": {"home": {"starter_volatility_score": 90}, "away": {}},
            "lineup_explosiveness": {"home": {"lineup_explosiveness_score": 85}, "away": {}},
            "bullpen_stress": {"home": {"bullpen_stress_score": 80}, "away": {}},
        }
        out = calibrate_tail_probabilities(_base_distribution(), ctx)
        after = out["after"]["probabilities"]
        for ln in (6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5):
            kO = f"over_{str(ln).replace('.', '_')}"
            kU = f"under_{str(ln).replace('.', '_')}"
            assert after[kO] + after[kU] == pytest.approx(1.0, abs=0.01)

    def test_p90_too_compressed_recalibrated(self):
        dist = _base_distribution()
        dist["percentiles"]["p90"] = 10  # compressed
        ctx = {
            "starter_volatility": {"home": {"starter_volatility_score": 85}, "away": {}},
            "lineup_explosiveness": {"home": {"lineup_explosiveness_score": 80}, "away": {}},
            "bullpen_stress": {"home": {"bullpen_stress_score": 70}, "away": {}},
        }
        out = calibrate_tail_probabilities(dist, ctx)
        assert "P90_TOO_COMPRESSED_FOR_CONTEXT" in out["reason_codes"]
        assert "P90_RECALIBRATED" in out["reason_codes"]
        assert out["after"]["percentiles"]["p90"] > 10

    def test_park_factor_signal(self):
        ctx = {"park_factor": {"dynamic": 1.10}}
        out = calibrate_tail_probabilities(_base_distribution(), ctx)
        assert "HITTER_FRIENDLY_PARK" in out["drivers"]

    def test_invalid_distribution_fails_soft(self):
        out = calibrate_tail_probabilities(None, {})
        assert out["tail_calibration_applied"] is False
        assert "INVALID_DISTRIBUTION_INPUT" in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# §3 — Threshold Over Model (heuristic fallback)
# ─────────────────────────────────────────────────────────────────────
class TestThresholdOverModel:
    def test_all_lines_present(self):
        out = predict_threshold_probabilities({"baseline_expected_runs": 8.5})
        for ln in TM_LINES:
            kO = f"over_{str(ln).replace('.', '_')}"
            kU = f"under_{str(ln).replace('.', '_')}"
            assert kO in out["threshold_probabilities"]
            assert kU in out["under_probabilities"]

    def test_over_under_sum_one(self):
        out = predict_threshold_probabilities({"baseline_expected_runs": 8.5})
        for ln in TM_LINES:
            kO = f"over_{str(ln).replace('.', '_')}"
            kU = f"under_{str(ln).replace('.', '_')}"
            assert (out["threshold_probabilities"][kO]
                     + out["under_probabilities"][kU]) == pytest.approx(1.0, abs=0.01)

    def test_over_monotone_decreasing(self):
        out = predict_threshold_probabilities({"baseline_expected_runs": 8.5})
        probs = out["threshold_probabilities"]
        prev = 1.01
        for ln in TM_LINES:
            kO = f"over_{str(ln).replace('.', '_')}"
            assert probs[kO] <= prev + 1e-6
            prev = probs[kO]

    def test_higher_mu_higher_over_prob(self):
        out_low  = predict_threshold_probabilities({"baseline_expected_runs": 7.0})
        out_high = predict_threshold_probabilities({"baseline_expected_runs": 10.0})
        for ln in TM_LINES:
            kO = f"over_{str(ln).replace('.', '_')}"
            assert out_high["threshold_probabilities"][kO] >= out_low["threshold_probabilities"][kO] - 1e-6

    def test_confidence_capped_at_60(self):
        out = predict_threshold_probabilities({
            "baseline_expected_runs": 8.5,
            "market_total": 8.5,
            "starter_volatility_home": 70, "starter_volatility_away": 60,
            "lineup_explosiveness_home": 80, "lineup_explosiveness_away": 75,
            "bullpen_stress_home": 60, "bullpen_stress_away": 70,
            "domino_risk_home": 65, "domino_risk_away": 50,
            "first_inning_collapse_home": 50, "first_inning_collapse_away": 40,
            "recent_offense_home": {"bucket": "HOT"},
            "recent_offense_away": {"bucket": "NEUTRAL"},
            "home_iso_l7": 0.190, "away_iso_l7": 0.170,
        })
        assert out["confidence"] <= MAX_HEURISTIC_CONFIDENCE + 0.01

    def test_no_features_low_confidence(self):
        out = predict_threshold_probabilities({"baseline_expected_runs": 8.5})
        assert out["confidence"] < 50

    def test_model_version_present(self):
        out = predict_threshold_probabilities({"baseline_expected_runs": 8.5})
        assert out["model_version"] == MODEL_VERSION

    def test_invalid_input_fails_soft(self):
        out = predict_threshold_probabilities(None)
        assert out["confidence"] == 0.0
        assert "INVALID_FEATURES_TYPE" in out["missing_fields"]

    def test_volatility_boosts_tail_probabilities(self):
        ctx_low = {"baseline_expected_runs": 8.5}
        ctx_high = {
            "baseline_expected_runs": 8.5,
            "starter_volatility_home": 85, "starter_volatility_away": 80,
            "lineup_explosiveness_home": 90, "lineup_explosiveness_away": 85,
            "bullpen_stress_home": 80, "bullpen_stress_away": 75,
            "recent_offense_home": {"bucket": "EXPLOSIVE"},
            "recent_offense_away": {"bucket": "EXPLOSIVE"},
        }
        out_low  = predict_threshold_probabilities(ctx_low)
        out_high = predict_threshold_probabilities(ctx_high)
        # P(Over 11.5) should rise with high volatility.
        assert (out_high["threshold_probabilities"]["over_11_5"]
                 > out_low["threshold_probabilities"]["over_11_5"])


# ─────────────────────────────────────────────────────────────────────
# §4 — Distribution × Threshold-Model Blender
# ─────────────────────────────────────────────────────────────────────
class TestBlender:
    def _dist_probs(self):
        return {"probabilities": _base_distribution()["probabilities"]}

    def _tm_probs(self, conf=80.0):
        tm = predict_threshold_probabilities({
            "baseline_expected_runs": 8.5,
            "starter_volatility_home": 70, "starter_volatility_away": 60,
            "lineup_explosiveness_home": 75, "lineup_explosiveness_away": 70,
        })
        tm["confidence"] = conf
        return tm

    def test_high_confidence_blend_55_45(self):
        out = combine_distribution_and_threshold_model(
            self._dist_probs(), self._tm_probs(conf=75.0), {},
        )
        assert out["blend_weights"]["threshold_model"] == pytest.approx(0.55, abs=0.001)
        assert out["blend_weights"]["distribution"] == pytest.approx(0.45, abs=0.001)
        assert "THRESHOLD_MODEL_USED" in out["reason_codes"]

    def test_medium_confidence_blend_40_60(self):
        out = combine_distribution_and_threshold_model(
            self._dist_probs(), self._tm_probs(conf=50.0), {},
        )
        assert out["blend_weights"]["threshold_model"] == pytest.approx(0.40, abs=0.001)
        assert out["blend_weights"]["distribution"] == pytest.approx(0.60, abs=0.001)

    def test_low_confidence_skips_threshold(self):
        out = combine_distribution_and_threshold_model(
            self._dist_probs(), self._tm_probs(conf=30.0), {},
        )
        assert out["blend_weights"]["threshold_model"] == 0.0
        assert "THRESHOLD_MODEL_LOW_CONFIDENCE" in out["reason_codes"]

    def test_high_variance_boost(self):
        out = combine_distribution_and_threshold_model(
            self._dist_probs(),
            self._tm_probs(conf=50.0),
            {"tail_risk_bucket": "HIGH"},
        )
        # Medium baseline 0.40 + 0.05 boost = 0.45.
        assert out["blend_weights"]["threshold_model"] == pytest.approx(0.45, abs=0.001)

    def test_partial_data_cuts_threshold_weight(self):
        out = combine_distribution_and_threshold_model(
            self._dist_probs(),
            self._tm_probs(conf=75.0),
            {"partial_data": True},
        )
        # High baseline 0.55 * 0.5 = 0.275.
        assert out["blend_weights"]["threshold_model"] == pytest.approx(0.275, abs=0.001)

    def test_final_sum_one(self):
        out = combine_distribution_and_threshold_model(
            self._dist_probs(), self._tm_probs(conf=75.0), {},
        )
        for ln in TM_LINES:
            kO = f"over_{str(ln).replace('.', '_')}"
            kU = f"under_{str(ln).replace('.', '_')}"
            assert (out["final_over_probabilities"][kO]
                     + out["final_under_probabilities"][kU]) == pytest.approx(1.0, abs=0.01)

    def test_divergence_flag_on_tail(self):
        # Build divergent probabilities for over_11_5.
        dist = self._dist_probs()
        dist["probabilities"]["over_11_5"] = 0.05
        tm = self._tm_probs(conf=75.0)
        tm["threshold_probabilities"]["over_11_5"] = 0.30  # diff 0.25
        out = combine_distribution_and_threshold_model(dist, tm, {})
        assert "DISTRIBUTION_THRESHOLD_DIVERGENCE" in out["reason_codes"]
        assert any("over_11_5" in f for f in out["divergence_flags"])

    def test_sources_block_present(self):
        out = combine_distribution_and_threshold_model(
            self._dist_probs(), self._tm_probs(conf=75.0), {},
        )
        assert "distribution" in out["probability_sources"]
        assert "threshold_model" in out["probability_sources"]
        assert "final" in out["probability_sources"]

    def test_fails_soft_on_bad_inputs(self):
        out = combine_distribution_and_threshold_model(None, None, None)
        # Should not raise; final maps empty.
        assert "final_over_probabilities" in out
