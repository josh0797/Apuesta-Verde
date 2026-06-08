"""Unit tests for Line Learning Feedback Loop — Phase 48.

Covers ``derive_line_bias`` + ``apply_line_bias_to_projection`` and the
Over/Under polarity safety + cap enforcement guarantees.
"""
from __future__ import annotations

import pytest

from services.line_learning_engine import (
    RC_LINE_BIAS_APPLIED,
    RC_LINE_BIAS_DERIVED,
    RC_LINE_LEARNING_AGGRESSIVE_UNDERS,
    RC_LINE_LEARNING_ANALYTICS_LOADED,
    RC_LINE_LEARNING_FAIL_SOFT,
    RC_LINE_LEARNING_LOW_SAMPLE_WEIGHTED,
    RC_LINE_LEARNING_NEAR_MISS_CLUSTER,
    RC_LINE_LEARNING_PUSH_CLUSTER,
    RC_LINE_LEARNING_SAFE_LINES_WORKING,
    RC_LINE_LEARNING_USEFUL_WEIGHTED,
    RC_LINE_LEARNING_VALIDATED_WEIGHTED,
    TIER_LOW_SAMPLE,
    TIER_USEFUL,
    TIER_VALIDATED,
    apply_line_bias_to_projection,
    derive_line_bias,
)


def _analytics(n: int, *, agg=0.30, push=0.10, near=0.15, safe=0.20):
    return {
        "metrics": {
            "sample_size":               n,
            "aggressive_line_miss_rate": agg,
            "push_rate":                 push,
            "near_miss_rate":            near,
            "safe_line_hit_rate":        safe,
        }
    }


# ─────────────────────────────────────────────────────────────────────
# derive_line_bias
# ─────────────────────────────────────────────────────────────────────
class TestDeriveLineBias:
    def test_empty_payload_returns_unavailable(self):
        out = derive_line_bias({})
        assert out["available"] is False
        assert out["sample_size"] == 0

    def test_zero_samples_returns_unavailable(self):
        out = derive_line_bias(_analytics(0))
        assert out["available"] is False

    def test_low_sample_tier(self):
        out = derive_line_bias(_analytics(8))
        assert out["available"] is True
        assert out["confidence_tier"] == TIER_LOW_SAMPLE
        assert out["applied_weight"] == 0.25
        assert out["cap_runs"] == 0.15
        assert RC_LINE_LEARNING_LOW_SAMPLE_WEIGHTED in out["reason_codes"]
        assert RC_LINE_LEARNING_ANALYTICS_LOADED in out["reason_codes"]
        assert RC_LINE_BIAS_DERIVED in out["reason_codes"]

    def test_useful_tier(self):
        out = derive_line_bias(_analytics(25))
        assert out["confidence_tier"] == TIER_USEFUL
        assert out["applied_weight"] == 0.60
        assert out["cap_runs"] == 0.35
        assert RC_LINE_LEARNING_USEFUL_WEIGHTED in out["reason_codes"]

    def test_validated_tier(self):
        out = derive_line_bias(_analytics(75))
        assert out["confidence_tier"] == TIER_VALIDATED
        assert out["applied_weight"] == 1.00
        assert out["cap_runs"] == 0.50
        assert RC_LINE_LEARNING_VALIDATED_WEIGHTED in out["reason_codes"]

    def test_aggressive_unders_self_learning_code(self):
        out = derive_line_bias(_analytics(30, agg=0.20))
        assert RC_LINE_LEARNING_AGGRESSIVE_UNDERS in out["self_learning_reason_codes"]
        assert RC_LINE_LEARNING_AGGRESSIVE_UNDERS in out["reason_codes"]

    def test_push_cluster_self_learning_code(self):
        out = derive_line_bias(_analytics(30, push=0.15))
        assert RC_LINE_LEARNING_PUSH_CLUSTER in out["self_learning_reason_codes"]

    def test_near_miss_cluster_self_learning_code(self):
        out = derive_line_bias(_analytics(30, near=0.20))
        assert RC_LINE_LEARNING_NEAR_MISS_CLUSTER in out["self_learning_reason_codes"]

    def test_safe_lines_working_self_learning_code(self):
        out = derive_line_bias(_analytics(30, safe=0.70))
        assert RC_LINE_LEARNING_SAFE_LINES_WORKING in out["self_learning_reason_codes"]

    def test_accepts_flat_metrics_dict_too(self):
        # The helper should accept either {"metrics": {...}} or the metrics dict directly.
        flat = {"sample_size": 30, "aggressive_line_miss_rate": 0.30,
                "push_rate": 0.10, "near_miss_rate": 0.15, "safe_line_hit_rate": 0.20}
        out = derive_line_bias(flat)
        assert out["available"] is True
        assert out["sample_size"] == 30


# ─────────────────────────────────────────────────────────────────────
# apply_line_bias_to_projection
# ─────────────────────────────────────────────────────────────────────
class TestApplyLineBiasToProjection:
    def test_unavailable_bias_does_not_modify(self):
        out = apply_line_bias_to_projection(8.9, {"available": False})
        assert out["applied"] is False
        assert out["adjusted_expected_runs"] == 8.9

    def test_none_expected_runs_returns_unchanged(self):
        bias = derive_line_bias(_analytics(60))
        out = apply_line_bias_to_projection(None, bias)
        assert out["applied"] is False
        assert out["adjusted_expected_runs"] is None

    def test_useful_tier_applies_weighted_bias(self):
        bias = derive_line_bias(_analytics(30, agg=0.40))  # USEFUL
        out = apply_line_bias_to_projection(8.9, bias)
        # weight 0.60, cap 0.35 → effective is derived × 0.6 (no cap hit
        # because derived is small).
        assert out["applied"] is True
        assert out["confidence_tier"] == TIER_USEFUL
        assert out["applied_weight"] == 0.60
        # Always upward when agg > safe.
        assert out["bias_direction"] == "UP"
        assert out["adjusted_expected_runs"] > 8.9
        assert RC_LINE_BIAS_APPLIED in out["reason_codes"]

    def test_validated_tier_caps_at_0_50(self):
        bias = derive_line_bias(_analytics(75, agg=0.80, push=0.30,
                                            near=0.30, safe=0.05))
        out = apply_line_bias_to_projection(8.9, bias)
        # Even with extreme metrics, projection adjustment is capped at ±0.50.
        assert abs(out["bias_amount"]) <= 0.50 + 1e-9
        assert abs(out["adjusted_expected_runs"] - 8.9) <= 0.50 + 1e-9

    def test_low_sample_tier_caps_at_0_15(self):
        # Force a large derived bias but tier is LOW_SAMPLE → cap 0.15.
        bias = derive_line_bias(_analytics(10, agg=0.80, safe=0.05))
        out = apply_line_bias_to_projection(8.9, bias)
        assert abs(out["bias_amount"]) <= 0.15 + 1e-9

    def test_polarity_never_flips_over_to_under(self):
        # market_side='UNDER' should NOT flip the bias direction.
        bias = derive_line_bias(_analytics(75, agg=0.50, safe=0.10))
        out = apply_line_bias_to_projection(8.9, bias, market_side="UNDER")
        original_dir = out["bias_direction"]
        out_over = apply_line_bias_to_projection(8.9, bias, market_side="OVER")
        # Same direction regardless of market side — caller can choose to
        # interpret but the math doesn't flip.
        assert out_over["bias_direction"] == original_dir

    def test_fail_soft_on_bad_input(self):
        # Pass a non-dict bias payload — should not raise, return unchanged.
        out = apply_line_bias_to_projection(8.9, "not-a-dict")
        assert out["applied"] is False
        assert out["adjusted_expected_runs"] == 8.9

    def test_zero_bias_yields_not_applied(self):
        # Bias kernel returns 0.0 (LOW_SAMPLE clamps it) → applied=False
        # because the effective change is below the 0.01 threshold.
        bias = derive_line_bias(_analytics(5))
        out = apply_line_bias_to_projection(8.9, bias)
        assert out["applied"] is False
        assert out["adjusted_expected_runs"] == 8.9


# ─────────────────────────────────────────────────────────────────────
# Bounded behavior — projection cap respected even with extreme inputs.
# ─────────────────────────────────────────────────────────────────────
class TestBoundedBehavior:
    @pytest.mark.parametrize("sample_size,max_cap", [
        (10, 0.15),
        (30, 0.35),
        (80, 0.50),
    ])
    def test_max_cap_per_tier(self, sample_size, max_cap):
        bias = derive_line_bias(_analytics(sample_size, agg=0.90, push=0.40,
                                            near=0.40, safe=0.05))
        out = apply_line_bias_to_projection(8.9, bias)
        assert abs(out["bias_amount"]) <= max_cap + 1e-9

    def test_bias_does_not_invert_projection_sign(self):
        # No matter what, adjusted_expected_runs stays positive.
        bias = derive_line_bias(_analytics(75, agg=0.95, safe=0.05))
        out = apply_line_bias_to_projection(0.5, bias)
        assert out["adjusted_expected_runs"] > 0


# ─────────────────────────────────────────────────────────────────────
# Reason code surface contract
# ─────────────────────────────────────────────────────────────────────
class TestReasonCodeContract:
    def test_useful_with_no_self_codes_only_has_tier_codes(self):
        # Mild metrics — none of the self-learning triggers fire.
        out = derive_line_bias(_analytics(20, agg=0.05, push=0.02,
                                            near=0.05, safe=0.50))
        assert out["self_learning_reason_codes"] == []
        # Tier code still present.
        assert RC_LINE_LEARNING_USEFUL_WEIGHTED in out["reason_codes"]

    def test_fail_soft_reason_code_constant_exposed(self):
        # Ensure RC_LINE_LEARNING_FAIL_SOFT is reachable as a constant.
        assert RC_LINE_LEARNING_FAIL_SOFT == "LINE_LEARNING_FAIL_SOFT"
