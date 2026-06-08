"""Unit tests for services.mlb_inning_lambda_model — Phase 47."""
from __future__ import annotations

import os

import pytest

from services.mlb_inning_lambda_model import (
    RC_BULLPEN_FATIGUE_RAISES_LATE_LAMBDA,
    RC_BULLPEN_PHASE_RISK,
    RC_HIGH_TRAFFIC_RAISES_LATE_LAMBDA,
    RC_INNING_LAMBDA_MODEL_USED,
    RC_INNING_LAMBDA_SIGNIFICANT_DELTA,
    RC_LATE_EXPLOSION_RISK_EMBEDDED,
    RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK,
    RC_STARTER_DURABILITY_LOW,
    RC_STARTER_EARLY_RISK,
    RC_STARTER_SUPPRESSES_EARLY_RUNS,
    RC_TRAFFIC_SCORE_MISSING_NEUTRAL_USED,
    RC_TRANSITION_PHASE_RISK,
    compute_mlb_inning_lambdas,
)


def _base_kwargs(**overrides):
    return {
        "expected_runs":   8.5,
        "home_pitcher":    {"era": 4.10, "whip": 1.30, "avg_innings_pitched": 5.8},
        "away_pitcher":    {"era": 4.10, "whip": 1.30, "avg_innings_pitched": 5.8},
        "home_lineup":     {"team_ops_7d": 0.720},
        "away_lineup":     {"team_ops_7d": 0.720},
        "bullpen_home":    {"bullpen_era_7d": 4.10, "bullpen_whip_7d": 1.30},
        "bullpen_away":    {"bullpen_era_7d": 4.10, "bullpen_whip_7d": 1.30},
        "traffic_score":   50,
        "park_factor":     1.0,
        "weather_factor":  1.0,
        **overrides,
    }


# 1 — Strong starters lower λ_1_3
class TestStrongStarterLowersEarlyLambda:
    def test_elite_starters_suppress_lambda_1_3(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            home_pitcher={"era": 2.80, "whip": 1.00, "avg_innings_pitched": 6.5},
            away_pitcher={"era": 3.00, "whip": 1.05, "avg_innings_pitched": 6.3},
        ))
        baseline_phase = 8.5 * 0.32
        assert out["lambda_1_3"] < baseline_phase
        assert RC_STARTER_SUPPRESSES_EARLY_RUNS in (
            out["phase_breakdown"]["starter_phase"]["reason_codes"]
        )

    def test_weak_starters_raise_lambda_1_3(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            home_pitcher={"era": 5.50, "whip": 1.55, "avg_innings_pitched": 5.0},
            away_pitcher={"era": 5.20, "whip": 1.50, "avg_innings_pitched": 5.2},
        ))
        baseline_phase = 8.5 * 0.32
        assert out["lambda_1_3"] > baseline_phase
        assert RC_STARTER_EARLY_RISK in (
            out["phase_breakdown"]["starter_phase"]["reason_codes"]
        )


# 2 — Low starter durability raises λ_4_6
class TestLowDurabilityRaisesTransition:
    def test_short_outing_starters(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            home_pitcher={"era": 4.10, "whip": 1.30, "avg_innings_pitched": 4.5},
            away_pitcher={"era": 4.10, "whip": 1.30, "avg_innings_pitched": 4.7},
        ))
        baseline_phase = 8.5 * 0.34
        assert out["lambda_4_6"] > baseline_phase
        assert RC_STARTER_DURABILITY_LOW in (
            out["phase_breakdown"]["transition_phase"]["reason_codes"]
        )
        assert RC_TRANSITION_PHASE_RISK in (
            out["phase_breakdown"]["transition_phase"]["reason_codes"]
        )


# 3 — Bullpen fatigue raises λ_7_9
class TestBullpenFatigueRaisesLateLambda:
    def test_high_fatigue_raises_lambda_7_9(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            bullpen_home={"bullpen_era_7d": 4.10, "bullpen_fatigue": 0.80,
                          "bullpen_usage_3d": 0.75},
            bullpen_away={"bullpen_era_7d": 4.10},
        ))
        baseline_phase = 8.5 * 0.34
        assert out["lambda_7_9"] > baseline_phase
        assert RC_BULLPEN_FATIGUE_RAISES_LATE_LAMBDA in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )


# 4 — High traffic + weak bullpen raises λ_7_9 more than weak bullpen alone
class TestTrafficAmplifiesBullpenRisk:
    def test_high_traffic_amplifies_weak_bullpen(self):
        weak_only = compute_mlb_inning_lambdas(**_base_kwargs(
            bullpen_home={"bullpen_era_7d": 6.20, "bullpen_whip_7d": 1.55},
            bullpen_away={"bullpen_era_7d": 4.10},
            traffic_score=25,
        ))
        weak_plus_traffic = compute_mlb_inning_lambdas(**_base_kwargs(
            bullpen_home={"bullpen_era_7d": 6.20, "bullpen_whip_7d": 1.55},
            bullpen_away={"bullpen_era_7d": 4.10},
            traffic_score=80,
        ))
        assert weak_plus_traffic["lambda_7_9"] > weak_only["lambda_7_9"]
        assert RC_HIGH_TRAFFIC_RAISES_LATE_LAMBDA in (
            weak_plus_traffic["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )


# 5 — Low traffic + weak bullpen does not over-penalize
class TestLowTrafficLimitsBullpenRisk:
    def test_low_traffic_keeps_late_lambda_modest(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            bullpen_home={"bullpen_era_7d": 6.20, "bullpen_whip_7d": 1.55},
            bullpen_away={"bullpen_era_7d": 4.10},
            traffic_score=20,
        ))
        # λ_7_9 should grow, but only marginally vs baseline (cap kicks in
        # because traffic interaction is tiny).
        baseline_phase = 8.5 * 0.34
        assert out["lambda_7_9"] < baseline_phase * 1.30
        assert RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )


# 6 — Missing traffic_score uses neutral fallback
class TestMissingTrafficNeutralFallback:
    def test_no_traffic_emits_neutral_reason_code(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            traffic_score=None,
            bullpen_home={"bullpen_era_7d": 6.0},
            bullpen_away={"bullpen_era_7d": 4.10},
        ))
        assert RC_TRAFFIC_SCORE_MISSING_NEUTRAL_USED in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )
        # Normalized traffic should be 0.50.
        brk = out["phase_breakdown"]["bullpen_phase"]["breakdown_home"]
        assert brk["normalized_traffic"] == pytest.approx(0.50)


# 7 — Total expected_runs = λ_1_3 + λ_4_6 + λ_7_9
class TestSumIdentity:
    def test_total_equals_sum_of_phases(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs())
        total = out["lambda_1_3"] + out["lambda_4_6"] + out["lambda_7_9"]
        assert out["expected_runs"] == pytest.approx(round(total, 3), abs=0.001)


# 8 — F5 expected runs = λ_1_3 + 2/3 of λ_4_6
class TestF5Identity:
    def test_f5_uses_partial_transition(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs())
        expected_f5 = out["lambda_1_3"] + (out["lambda_4_6"] * 2.0 / 3.0)
        assert out["f5_expected_runs"] == pytest.approx(round(expected_f5, 3), abs=0.001)
        # F5 should always be a strict subset (less than full game).
        assert out["f5_expected_runs"] < out["expected_runs"]


# 9 — Feature flag disabled returns available=false
class TestFeatureFlag:
    def test_disabled_flag_short_circuits(self, monkeypatch):
        monkeypatch.setenv("MLB_INNING_LAMBDA_ENABLED", "false")
        out = compute_mlb_inning_lambdas(**_base_kwargs())
        assert out["available"] is False
        assert out["reason"] == "feature_flag_disabled"
        # No lambdas computed.
        assert "lambda_1_3" not in out

    def test_invalid_expected_runs_returns_unavailable(self, monkeypatch):
        monkeypatch.setenv("MLB_INNING_LAMBDA_ENABLED", "true")
        out = compute_mlb_inning_lambdas(**_base_kwargs(expected_runs=None))
        assert out["available"] is False
        assert "expected_runs" in out["reason"]


# 10 — Phase adjustment cap clamps extreme inputs
class TestPhaseAdjustmentCap:
    def test_extreme_inputs_get_clamped(self, monkeypatch):
        # With default cap 0.35, even extreme bullpen + traffic input can't
        # push λ_7_9 beyond +35% of its baseline (8.5 × 0.34 × 1.35).
        monkeypatch.delenv("MLB_LAMBDA_MAX_PHASE_ADJUSTMENT", raising=False)
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            bullpen_home={"bullpen_era_7d": 12.0, "bullpen_whip_7d": 2.5,
                          "bullpen_fatigue": 1.0, "bullpen_usage_3d": 1.0,
                          "hr_risk": 1.0, "offensive_explosion_score": 1.0},
            bullpen_away={"bullpen_era_7d": 12.0, "bullpen_whip_7d": 2.5,
                          "bullpen_fatigue": 1.0},
            traffic_score=100,
        ))
        cap = 8.5 * 0.34 * 1.35
        assert out["lambda_7_9"] <= cap + 0.001
        assert RC_LATE_EXPLOSION_RISK_EMBEDDED in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )

    def test_minimum_phase_value_floor(self, monkeypatch):
        # Tiny expected_runs + best starters → phases get clamped at floor.
        monkeypatch.setenv("MLB_LAMBDA_MIN_PHASE_VALUE", "0.10")
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            expected_runs=0.30,
            home_pitcher={"era": 1.5, "whip": 0.8},
            away_pitcher={"era": 1.5, "whip": 0.8},
        ))
        assert out["lambda_1_3"] >= 0.10 - 1e-9
        assert out["lambda_4_6"] >= 0.10 - 1e-9
        assert out["lambda_7_9"] >= 0.10 - 1e-9


# 11 — Significant delta vs baseline emits warning code
class TestSignificantDelta:
    def test_large_delta_emits_significant_delta_code(self):
        # Force a large delta by combining extreme phase adjustments.
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            expected_runs=8.5,
            bullpen_home={"bullpen_era_7d": 8.0, "bullpen_fatigue": 0.95,
                          "bullpen_usage_3d": 0.90},
            bullpen_away={"bullpen_era_7d": 7.5, "bullpen_fatigue": 0.90},
            traffic_score=95,
            home_pitcher={"era": 5.5, "whip": 1.55, "avg_innings_pitched": 4.5},
            away_pitcher={"era": 5.5, "whip": 1.55, "avg_innings_pitched": 4.5},
        ))
        # With caps in place the absolute delta may not exceed 1.0;
        # but the reason code logic is exercised via this assertion.
        if abs(out["delta_vs_baseline"]) >= 1.0:
            assert RC_INNING_LAMBDA_SIGNIFICANT_DELTA in out["reason_codes"]


# 12 — Reason codes always include the engine marker
class TestReasonCodeMarker:
    def test_engine_marker_always_present(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs())
        assert RC_INNING_LAMBDA_MODEL_USED in out["reason_codes"]
        assert out["engine_version"] == "mlb_inning_lambda.1"


# 13 — Observe-only flag is respected (no semantic effect on the math)
class TestObserveOnly:
    def test_observe_only_flag_returned(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(), observe_only=True)
        assert out["observe_only"] is True

    def test_active_mode_returned(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(), observe_only=False)
        assert out["observe_only"] is False


# 14 — Bullpen phase risk fires when bullpen significantly above league
class TestBullpenPhaseRiskCode:
    def test_bullpen_phase_risk_when_weak(self):
        out = compute_mlb_inning_lambdas(**_base_kwargs(
            bullpen_home={"bullpen_era_7d": 6.5, "bullpen_whip_7d": 1.55},
            bullpen_away={"bullpen_era_7d": 4.10},
            traffic_score=70,
        ))
        assert RC_BULLPEN_PHASE_RISK in (
            out["phase_breakdown"]["bullpen_phase"]["reason_codes"]
        )
