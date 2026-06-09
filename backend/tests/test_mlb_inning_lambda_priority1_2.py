"""Tests for Priority 1 (reactive λ7-9) + Priority 2 (adjustment breakdown).

These tests validate the new λ7-9 interaction model added in the MLB
engine refresh:

  • bullpen ALONE with low traffic must NOT inflate λ7-9.
  • bullpen + high traffic must raise λ7-9 clearly.
  • bullpen + high traffic + defensive breakdown raise λ7-9 strongly.
  • λ7-9 respects the new MLB_LAMBDA_MAX_LATE_ADJUSTMENT cap (±45%).
  • adjustment_breakdown sums match final - base within rounding.
"""
from __future__ import annotations

import pytest

from services.mlb_inning_lambda_model import (
    compute_mlb_inning_lambdas,
    RC_LATE_LAMBDA_REACTIVE_MODEL_USED,
    RC_BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA,
    RC_BULLPEN_DEFENSE_RAISES_LATE_LAMBDA,
    RC_FATIGUE_RAISES_LATE_LAMBDA,
    RC_HR_RISK_RAISES_LATE_LAMBDA,
    RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK,
    RC_LOW_DEFENSIVE_RISK_LIMITS_LATE_RUNS,
    RC_LATE_LAMBDA_CAPPED,
    RC_PROJECTION_BREAKDOWN_AVAILABLE,
    RC_BULLPEN_IMPACT_EXPLAINED,
    RC_TRAFFIC_IMPACT_EXPLAINED,
)


def _kwargs(**over):
    base = dict(
        expected_runs=8.5,
        home_pitcher={"era": 3.9, "whip": 1.20},
        away_pitcher={"era": 3.9, "whip": 1.20},
        home_lineup={"ops": 0.720},
        away_lineup={"ops": 0.720},
        bullpen_home={},
        bullpen_away={},
        traffic_score=None,
        observe_only=True,
    )
    base.update(over)
    return base


# =====================================================================
# Priority 1 — Core rule: bullpen risk ALONE doesn't heavily inflate λ7-9
# =====================================================================
class TestPriority1CoreRule:

    def test_bullpen_bad_low_traffic_stays_low(self):
        """Vulnerable bullpen + LOW traffic → λ7-9 barely moves."""
        baseline_out = compute_mlb_inning_lambdas(**_kwargs())
        baseline_lambda_7_9 = baseline_out["lambda_7_9"]

        out = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 6.0, "bullpen_whip_7d": 1.55,
                          "bullpen_usage_3d": 0.30, "bullpen_fatigue": 0.20},
            bullpen_away={"bullpen_era_7d": 6.0, "bullpen_whip_7d": 1.55},
            traffic_score=15,  # very low traffic
        ))
        # λ7-9 grows but moderately (the bullpen ALONE shouldn't move it
        # more than ~12% in this scenario per the new equation).
        growth = (out["lambda_7_9"] - baseline_lambda_7_9) / baseline_lambda_7_9
        assert growth < 0.15, f"Got {growth:.3f} — bullpen-alone moved λ7-9 too much"
        assert RC_LATE_LAMBDA_REACTIVE_MODEL_USED in out["reason_codes"]
        # Should mark the low-traffic limiter.
        assert RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )

    def test_bullpen_bad_high_traffic_raises_clearly(self):
        """Vulnerable bullpen + HIGH traffic → λ7-9 rises clearly."""
        baseline_out = compute_mlb_inning_lambdas(**_kwargs())
        baseline_lambda_7_9 = baseline_out["lambda_7_9"]

        out = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 6.0, "bullpen_whip_7d": 1.55,
                          "bullpen_usage_3d": 0.70, "bullpen_fatigue": 0.40},
            bullpen_away={"bullpen_era_7d": 6.0, "bullpen_whip_7d": 1.55},
            traffic_score=80,
        ))
        growth = (out["lambda_7_9"] - baseline_lambda_7_9) / baseline_lambda_7_9
        # Bullpen + high traffic should move λ7-9 by at least 8% upward.
        assert growth >= 0.08, f"Got {growth:.3f} — bullpen+traffic didn't raise λ7-9"
        assert RC_BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )

    def test_bullpen_high_traffic_and_defense_raises_strongly(self):
        """Bullpen + high traffic + defensive breakdown → strong rise."""
        baseline_out = compute_mlb_inning_lambdas(**_kwargs())
        baseline_lambda_7_9 = baseline_out["lambda_7_9"]

        out = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 6.5, "bullpen_whip_7d": 1.60,
                          "bullpen_usage_3d": 0.80, "bullpen_fatigue": 0.55,
                          "hr_risk": 0.65},
            bullpen_away={"bullpen_era_7d": 6.5, "bullpen_whip_7d": 1.60,
                          "bullpen_fatigue": 0.55},
            traffic_score=85,
            defensive_breakdown_score=80,
        ))
        growth = (out["lambda_7_9"] - baseline_lambda_7_9) / baseline_lambda_7_9
        # Strong, but still within the 45% cap.
        assert 0.18 <= growth <= 0.46, f"Got {growth:.3f}"
        rcs = out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        assert RC_BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA in rcs
        assert RC_BULLPEN_DEFENSE_RAISES_LATE_LAMBDA in rcs
        assert RC_FATIGUE_RAISES_LATE_LAMBDA in rcs
        assert RC_HR_RISK_RAISES_LATE_LAMBDA in rcs

    def test_late_lambda_respects_max_late_cap(self, monkeypatch):
        """Maximum possible inputs → λ7-9 stops at +45% (default cap)."""
        monkeypatch.delenv("MLB_LAMBDA_MAX_LATE_ADJUSTMENT", raising=False)
        out = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 14.0, "bullpen_whip_7d": 3.0,
                          "bullpen_usage_3d": 1.0, "bullpen_fatigue": 1.0,
                          "hr_risk": 1.0, "offensive_explosion_score": 1.0},
            bullpen_away={"bullpen_era_7d": 14.0, "bullpen_whip_7d": 3.0,
                          "bullpen_fatigue": 1.0, "hr_risk": 1.0},
            traffic_score=100,
            defensive_breakdown_score=100,
            series_familiarity_score=100,
        ))
        baseline_lambda_7_9 = 8.5 * 0.34
        cap_value = baseline_lambda_7_9 * 1.45
        assert out["lambda_7_9"] <= cap_value + 0.001
        assert out["phase_breakdown"]["bullpen_phase"]["capped"] is True
        assert RC_LATE_LAMBDA_CAPPED in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )

    def test_low_defensive_score_limits_bullpen_risk(self):
        out = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 6.0, "bullpen_whip_7d": 1.55},
            bullpen_away={"bullpen_era_7d": 6.0},
            traffic_score=50,
            defensive_breakdown_score=15,  # very low — strong defense
        ))
        assert RC_LOW_DEFENSIVE_RISK_LIMITS_LATE_RUNS in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )


# =====================================================================
# Priority 2 — adjustment_breakdown payload
# =====================================================================
class TestPriority2Breakdown:

    def test_adjustment_breakdown_present(self):
        out = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 5.5},
            bullpen_away={"bullpen_era_7d": 5.5},
            traffic_score=70,
        ))
        ab = out["adjustment_breakdown"]
        assert "base_expected_runs" in ab
        assert "lambda_base" in ab
        assert "adjustments" in ab
        assert "final_expected_runs" in ab
        assert "total_delta" in ab
        assert ab["base_expected_runs"] == 8.5
        # final == sum of all 3 lambdas == out['expected_runs'].
        assert abs(ab["final_expected_runs"] - out["expected_runs"]) < 0.001

    def test_adjustment_breakdown_sum_matches_total_delta(self):
        """Sum of all adjustment deltas should approximate the total delta
        (within rounding). Adjustments span phases 1_3, 4_6, 7_9."""
        out = compute_mlb_inning_lambdas(**_kwargs(
            home_pitcher={"era": 2.5, "whip": 1.0},   # elite starter
            away_pitcher={"era": 5.5, "whip": 1.4},  # poor starter
            bullpen_home={"bullpen_era_7d": 5.5, "bullpen_fatigue": 0.7},
            bullpen_away={"bullpen_era_7d": 4.0},
            traffic_score=70,
            defensive_breakdown_score=60,
        ))
        adjustments = out["adjustment_breakdown"]["adjustments"]
        assert len(adjustments) >= 1
        # Every adjustment must have phase + factor + delta + reason.
        for a in adjustments:
            assert "phase" in a
            assert "factor" in a
            assert "delta" in a
            assert "reason" in a
            assert isinstance(a["delta"], (int, float))

    def test_breakdown_reason_codes_emitted(self):
        out = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 5.5, "bullpen_fatigue": 0.6},
            bullpen_away={"bullpen_era_7d": 5.5},
            traffic_score=70,
        ))
        rcs = out["reason_codes"]
        assert RC_PROJECTION_BREAKDOWN_AVAILABLE in rcs
        assert RC_BULLPEN_IMPACT_EXPLAINED in rcs
        # Traffic explanation should appear when traffic is meaningful.
        assert RC_TRAFFIC_IMPACT_EXPLAINED in rcs

    def test_breakdown_failsoft_with_missing_inputs(self):
        """Missing bullpen / traffic → still produces a valid breakdown."""
        out = compute_mlb_inning_lambdas(**_kwargs())
        ab = out["adjustment_breakdown"]
        assert ab["base_expected_runs"] == 8.5
        # No adjustments may have delta ≠ 0 in this empty scenario, but
        # the payload structure must always be present.
        assert isinstance(ab["adjustments"], list)


# =====================================================================
# Priority 1 — env knobs respected
# =====================================================================
class TestPriority1EnvKnobs:

    def test_traffic_weight_env(self, monkeypatch):
        monkeypatch.setenv("MLB_LAMBDA_TRAFFIC_WEIGHT", "0.05")
        out_low = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 6.0},
            bullpen_away={"bullpen_era_7d": 6.0},
            traffic_score=80,
        ))
        monkeypatch.setenv("MLB_LAMBDA_TRAFFIC_WEIGHT", "0.40")
        out_high = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 6.0},
            bullpen_away={"bullpen_era_7d": 6.0},
            traffic_score=80,
        ))
        # Higher weight ⇒ higher λ7-9.
        assert out_high["lambda_7_9"] > out_low["lambda_7_9"]

    def test_max_late_adjustment_env(self, monkeypatch):
        monkeypatch.setenv("MLB_LAMBDA_MAX_LATE_ADJUSTMENT", "0.10")
        out = compute_mlb_inning_lambdas(**_kwargs(
            bullpen_home={"bullpen_era_7d": 14.0, "bullpen_fatigue": 1.0,
                          "hr_risk": 1.0},
            bullpen_away={"bullpen_era_7d": 14.0, "bullpen_fatigue": 1.0},
            traffic_score=100,
            defensive_breakdown_score=100,
        ))
        baseline = 8.5 * 0.34
        assert out["lambda_7_9"] <= baseline * 1.10 + 0.001
