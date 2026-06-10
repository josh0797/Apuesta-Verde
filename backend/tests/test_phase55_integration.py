"""Integration tests for Phase 55 — Tail Fragility Engine.

Verifies:
  • compute_tail_fragility never raises (fail-soft)
  • explosive_tail_score formula correctness
  • bucket thresholds and base adjustments
  • interactions ONLY fire when bucket >= HIGH
  • cap at +20 with TAIL_FRAGILITY_CAP_HIT code
  • NO auto-flip (no 'pick'/'recommendation'/'side_flip'/'polarity' keys)
  • fragility_calibrator integration (tail_fragility vs legacy p_ge_12)
  • backward compatibility when tail_fragility not provided
"""
from __future__ import annotations

import pytest

from services.mlb_tail_fragility import (
    compute_tail_fragility,
    BUCKET_LOW, BUCKET_MEDIUM, BUCKET_HIGH, BUCKET_EXTREME,
    CAP_TOTAL_ADJUSTMENT,
    RC_TAIL_FRAGILITY_CAP_HIT,
)
from services.mlb_fragility_calibrator import calibrate_fragility


def _tail(p12=0.0, p14=0.0, p16=0.0, p18=0.0):
    """Helper to create tail_risk payload."""
    return {
        "available": True,
        "p_ge_12": p12, "p_ge_14": p14, "p_ge_16": p16, "p_ge_18": p18,
    }


class TestPhase55Integration:
    """Integration tests for Phase 55 Tail Fragility Engine."""

    def test_compute_tail_fragility_never_raises_on_none(self):
        """Verify fail-soft behavior with None payload."""
        result = compute_tail_fragility(tail_risk_payload=None)
        assert result["available"] is False
        assert result["total_adjustment"] == 0

    def test_compute_tail_fragility_never_raises_on_malformed(self):
        """Verify fail-soft behavior with malformed payload."""
        result = compute_tail_fragility(
            tail_risk_payload={"available": True, "p_ge_12": "bad_data"}
        )
        assert isinstance(result, dict)
        assert result["available"] is True
        assert result["tail_bucket"] == BUCKET_LOW

    def test_explosive_tail_score_formula(self):
        """Verify explosive_tail_score formula: p12*0.30 + p14*0.30 + p16*0.25 + p18*0.15."""
        # Test case: p12=0.40, p14=0.30, p16=0.20, p18=0.10
        # Expected: 0.40*0.30 + 0.30*0.30 + 0.20*0.25 + 0.10*0.15
        #         = 0.120 + 0.090 + 0.050 + 0.015 = 0.275 → 28
        result = compute_tail_fragility(tail_risk_payload=_tail(0.40, 0.30, 0.20, 0.10))
        assert result["explosive_tail_score"] == 28
        assert result["tail_bucket"] == BUCKET_MEDIUM

    def test_bucket_thresholds(self):
        """Verify bucket thresholds: LOW [0-24], MEDIUM [25-49], HIGH [50-74], EXTREME [75+]."""
        # LOW: score 0
        result = compute_tail_fragility(tail_risk_payload=_tail(0.0, 0.0, 0.0, 0.0))
        assert result["tail_bucket"] == BUCKET_LOW
        assert result["base_adjustment"] == 0

        # MEDIUM: score 28
        result = compute_tail_fragility(tail_risk_payload=_tail(0.40, 0.30, 0.20, 0.10))
        assert result["tail_bucket"] == BUCKET_MEDIUM
        assert result["base_adjustment"] == 5

        # HIGH: score ~60
        result = compute_tail_fragility(tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20))
        assert result["tail_bucket"] == BUCKET_HIGH
        assert result["base_adjustment"] == 10

        # EXTREME: score ~85
        result = compute_tail_fragility(tail_risk_payload=_tail(0.95, 0.85, 0.70, 0.50))
        assert result["tail_bucket"] == BUCKET_EXTREME
        assert result["base_adjustment"] == 15

    def test_interactions_only_fire_when_bucket_high_or_above(self):
        """Verify interactions ONLY fire when tail_bucket >= HIGH."""
        # LOW bucket: no interactions
        result = compute_tail_fragility(
            tail_risk_payload=_tail(0.05, 0.01, 0.0, 0.0),
            bullpen_fatigue_high=True,
            defensive_breakdown_bucket="HIGH",
            series_familiarity_bucket="HIGH",
            starter_era=5.50, starter_whip=1.60,
        )
        assert result["tail_bucket"] == BUCKET_LOW
        assert result["interactions"] == []
        assert result["interaction_total"] == 0

        # MEDIUM bucket: no interactions
        result = compute_tail_fragility(
            tail_risk_payload=_tail(0.40, 0.30, 0.20, 0.10),
            bullpen_fatigue_high=True,
            starter_era=5.0,
        )
        assert result["tail_bucket"] == BUCKET_MEDIUM
        assert result["interactions"] == []

        # HIGH bucket: interactions fire
        result = compute_tail_fragility(
            tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),
            bullpen_fatigue_high=True,
        )
        assert result["tail_bucket"] == BUCKET_HIGH
        assert len(result["interactions"]) > 0
        assert result["interaction_total"] == 5

    def test_cap_at_20_with_cap_hit_code(self):
        """Verify cap at +20 total adjustment with TAIL_FRAGILITY_CAP_HIT code."""
        # EXTREME (+15) + ALL interactions (+5+4+3+5 = +17) = +32 → clamp to +20
        result = compute_tail_fragility(
            tail_risk_payload=_tail(0.95, 0.85, 0.70, 0.50),
            bullpen_fatigue_high=True,
            defensive_breakdown_bucket="HIGH",
            series_familiarity_bucket="HIGH",
            starter_era=5.50, starter_whip=1.60,
        )
        assert result["base_adjustment"] == 15
        assert result["interaction_total"] == 17
        assert result["total_adjustment"] == CAP_TOTAL_ADJUSTMENT == 20
        assert result["cap_hit"] is True
        assert RC_TAIL_FRAGILITY_CAP_HIT in result["reason_codes"]

    def test_no_auto_flip_keys_in_payload(self):
        """Verify payload NEVER contains 'pick'/'recommendation'/'side_flip'/'polarity' keys."""
        result = compute_tail_fragility(
            tail_risk_payload=_tail(0.80, 0.50, 0.25, 0.10),
            market_side="under",
            bullpen_fatigue_high=True,
        )
        forbidden = {"pick", "selection", "recommendation", "side_flip", "polarity"}
        for key in forbidden:
            assert key not in result, f"forbidden key {key} present in payload"

    def test_fragility_calibrator_uses_tail_fragility_when_provided(self):
        """Verify calibrator uses tail_fragility when provided (no double counting)."""
        # Create a tail_fragility payload with +15 adjustment
        tail_frag = compute_tail_fragility(
            tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),
            bullpen_fatigue_high=True,
        )
        assert tail_frag["total_adjustment"] == 15

        # Call calibrator with tail_fragility
        result = calibrate_fragility(
            base_fragility=20,
            tail_fragility=tail_frag,
            tail_risk={"available": True, "p_ge_12": 0.25},  # This should be IGNORED
        )
        
        # Should use tail_fragility (+15), NOT legacy p_ge_12 (+10)
        assert result["delta"] == 15
        assert "tail_fragility" in result["component_deltas"]
        assert "tail_risk" not in result["component_deltas"]

    def test_fragility_calibrator_uses_legacy_when_tail_fragility_not_provided(self):
        """Verify calibrator uses legacy p_ge_12 logic when tail_fragility not provided."""
        # Call calibrator WITHOUT tail_fragility
        result = calibrate_fragility(
            base_fragility=20,
            tail_risk={"available": True, "p_ge_12": 0.25},  # >= 0.22 → +10
        )
        
        # Should use legacy p_ge_12 logic (+10)
        assert result["delta"] == 10
        assert "tail_risk" in result["component_deltas"]
        assert result["component_deltas"]["tail_risk"] == 10

    def test_fragility_calibrator_backward_compatibility(self):
        """Verify backward compatibility: legacy logic still works when tail_fragility unavailable."""
        # Unavailable tail_fragility should trigger legacy fallback
        result = calibrate_fragility(
            base_fragility=20,
            tail_fragility={"available": False},
            tail_risk={"available": True, "p_ge_12": 0.15},  # >= 0.12 → +5
        )
        
        # Should use legacy p_ge_12 logic (+5)
        assert result["delta"] == 5
        assert "tail_risk" in result["component_deltas"]
        assert result["component_deltas"]["tail_risk"] == 5

    def test_cole_vs_cecconi_spec_case(self):
        """Verify Cole vs Cecconi spec case hits +20 cap."""
        # Spec: HIGH bucket, bullpen fatigue, series familiarity, vulnerable starter
        # Expected: base +10 + bullpen +5 + series +3 + starter +5 = +23 → capped at +20
        result = compute_tail_fragility(
            tail_risk_payload=_tail(0.85, 0.60, 0.35, 0.18),  # HIGH (~55)
            bullpen_fatigue_high=True,
            series_familiarity_bucket="HIGH",
            starter_era=4.92, starter_whip=1.43,
        )
        assert result["tail_bucket"] == BUCKET_HIGH
        assert result["interaction_total"] == 13  # +5 +3 +5
        assert result["total_adjustment"] == 20  # capped
        assert result["cap_hit"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
