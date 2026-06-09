"""
Focused test for Priority 4 Tail Risk + Fragility Calibrator scenarios.
Validates the specific cases mentioned in features_or_bugs_to_test.
"""
import pytest
from services.mlb_expected_runs_distribution import (
    compute_expected_runs_distribution,
    compute_tail_risk,
    interpret_market_profile,
    TAIL_BUCKET_LOW,
    TAIL_BUCKET_MEDIUM,
    TAIL_BUCKET_HIGH,
    TAIL_BUCKET_EXTREME,
    UQ_MEAN_AND_TAIL_SUPPORTED,
    UQ_MEAN_SUPPORTED_TAIL_FRAGILE,
    UQ_TAIL_DOMINATES,
    UQ_NOT_SUPPORTED,
    RC_TAIL_RISK_PANEL_USED,
    RC_PURE_PYTHON_PMF_CDF_USED,
    RC_CLEAN_UNDER_PROFILE,
    RC_MEAN_SUPPORTED_FRAGILE_UNDER,
    RC_OVER_LIVES_THROUGH_TAIL,
    RC_PROTECTED_LINE_PREFERRED_DUE_TO_TAIL,
)
from services.mlb_fragility_calibrator import (
    calibrate_fragility,
    MAX_DELTA,
    MAX_CEILING,
    HOR_BOTH_BULLPENS_TIRED,
    HOR_MEDIOCRE_VOLATILE_STARTER,
    HOR_SERIES_FAMILIARITY_ACTIVE,
    HOR_LATE_LAMBDA_ELEVATED,
    HOR_TAIL_RISK_PRESENT,
    HOR_TRAFFIC_RISK,
    HOR_DEFENSIVE_RISK,
    RC_FRAGILITY_CALIBRATED,
    RC_HIDDEN_OVER_ROUTES_DETECTED,
)


class TestFeature1TailRiskPanel:
    """Feature 1 — compute_tail_risk contract validation."""
    
    def test_contract_output_fields(self):
        """Verify all required output fields are present."""
        d = compute_expected_runs_distribution(
            expected_runs=8.0,
            market="total_runs_under",
            market_line=10.5,
            nb_dispersion_ratio=1.35,
        )
        t = compute_tail_risk(
            distribution_payload=d,
            market_line=10.5,
            market_side="under",
        )
        
        # Required fields
        assert "available" in t
        assert t["available"] is True
        assert "market_line" in t
        assert "mean" in t
        assert "under_probability" in t
        assert "over_probability" in t
        assert "p_ge_12" in t
        assert "p_ge_14" in t
        assert "p_ge_16" in t
        assert "tail_bucket" in t
        assert "tail_risk_score" in t
        assert "under_quality" in t
        assert "interpretation_es" in t
        assert "reason_codes" in t
        
    def test_probability_sum_constraint(self):
        """under_probability + over_probability ≈ 1.0"""
        d = compute_expected_runs_distribution(
            expected_runs=8.0,
            market="total_runs_under",
            market_line=10.5,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        
        prob_sum = t["under_probability"] + t["over_probability"]
        assert abs(prob_sum - 1.0) < 0.001, f"Probability sum {prob_sum} != 1.0"
        
    def test_monotonic_tail_probabilities(self):
        """p_ge_12 >= p_ge_14 >= p_ge_16 (monotonic)"""
        d = compute_expected_runs_distribution(
            expected_runs=8.0,
            market="total_runs_under",
            market_line=10.5,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        
        assert t["p_ge_12"] >= t["p_ge_14"], f"p_ge_12 {t['p_ge_12']} < p_ge_14 {t['p_ge_14']}"
        assert t["p_ge_14"] >= t["p_ge_16"], f"p_ge_14 {t['p_ge_14']} < p_ge_16 {t['p_ge_16']}"
        
    def test_tail_bucket_classification(self):
        """Verify bucket thresholds: LOW <12%, MEDIUM 12-22%, HIGH >22%, EXTREME p_ge_14>15% OR p_ge_16>8%"""
        # LOW case
        d_low = compute_expected_runs_distribution(
            expected_runs=6.5,
            market="total_runs_under",
            market_line=10.5,
            nb_dispersion_ratio=1.0,
        )
        t_low = compute_tail_risk(distribution_payload=d_low, market_line=10.5, market_side="under")
        assert t_low["tail_bucket"] == TAIL_BUCKET_LOW
        assert t_low["p_ge_12"] < 0.12
        
        # EXTREME case
        d_extreme = compute_expected_runs_distribution(
            expected_runs=11.5,
            market="total_runs_under",
            market_line=10.5,
            nb_dispersion_ratio=1.8,
            fragility_score=85,
            traffic_score=80,
        )
        t_extreme = compute_tail_risk(distribution_payload=d_extreme, market_line=10.5, market_side="under")
        # Should be EXTREME if p_ge_14 > 15% OR p_ge_16 > 8%
        if t_extreme["p_ge_14"] > 0.15 or t_extreme["p_ge_16"] > 0.08:
            assert t_extreme["tail_bucket"] == TAIL_BUCKET_EXTREME
            
    def test_mandatory_reason_codes(self):
        """TAIL_RISK_PANEL_USED and PURE_PYTHON_PMF_CDF_USED must be present."""
        d = compute_expected_runs_distribution(
            expected_runs=8.0,
            market="total_runs_under",
            market_line=10.5,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        
        assert RC_TAIL_RISK_PANEL_USED in t["reason_codes"]
        assert RC_PURE_PYTHON_PMF_CDF_USED in t["reason_codes"]
        
    def test_fail_soft_unavailable_distribution(self):
        """Fail-soft when distribution_payload not available."""
        t = compute_tail_risk(
            distribution_payload={"available": False},
            market_line=10.5,
            market_side="under",
        )
        assert t["available"] is False
        assert "reason" in t


class TestFeature1YankeesGuardiansCase:
    """Feature 1 — Yankees @ Guardians style case validation."""
    
    def test_yankees_guardians_under_quality(self):
        """
        Case: mean ER 8.0, line 10.5, nb_ratio 1.35, traffic 55, series 50
        Expected: under_probability >= 0.55, under_quality = MEAN_SUPPORTED_BUT_TAIL_FRAGILE or TAIL_DOMINATES
        (NOT MEAN_AND_TAIL_SUPPORTED)
        """
        d = compute_expected_runs_distribution(
            expected_runs=8.0,
            market="total_runs_under",
            market_line=10.5,
            nb_dispersion_ratio=1.35,
            fragility_score=20,
            traffic_score=55,
            series_familiarity_score=50,
        )
        t = compute_tail_risk(
            distribution_payload=d,
            market_line=10.5,
            market_side="under",
        )
        
        # Validate under_probability >= 0.55
        assert t["under_probability"] >= 0.55, \
            f"under_probability {t['under_probability']} < 0.55"
        
        # Validate under_quality is NOT clean
        assert t["under_quality"] in (UQ_MEAN_SUPPORTED_TAIL_FRAGILE, UQ_TAIL_DOMINATES), \
            f"under_quality {t['under_quality']} should be MEAN_SUPPORTED_BUT_TAIL_FRAGILE or TAIL_DOMINATES"
        
        # Should NOT be clean
        assert t["under_quality"] != UQ_MEAN_AND_TAIL_SUPPORTED, \
            "under_quality should NOT be MEAN_AND_TAIL_SUPPORTED for this case"


class TestFeature3FragilityCalibrator:
    """Feature 3 — calibrate_fragility validation."""
    
    def test_no_hidden_routes_no_change(self):
        """When NO hidden routes → delta=0, adjusted_fragility = base."""
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            expected_runs=8.0,
            inning_lambda_projection={"lambda_1_3": 3.0, "lambda_4_6": 3.0, "lambda_7_9": 2.0},
            home_pitcher={"era": 3.2, "whip": 1.10},
            away_pitcher={"era": 3.4, "whip": 1.15},
            bullpen_home={"bullpen_usage_3d": 0.30},
            bullpen_away={"bullpen_usage_3d": 0.25},
            series_familiarity={"series_familiarity_score": 10},
            traffic_score=20,
            defensive_breakdown_score=20,
            tail_risk={"p_ge_12": 0.05},
        )
        
        assert out["delta"] == 0
        assert out["adjusted_fragility"] == 20
        assert out["hidden_over_routes"] == []
        
    def test_both_bullpens_tired(self):
        """Both bullpens tired (usage_3d>=0.55 both) → HOR_BOTH_BULLPENS_TIRED, delta >= 8."""
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            bullpen_home={"bullpen_usage_3d": 0.65},
            bullpen_away={"bullpen_usage_3d": 0.62},
        )
        
        assert HOR_BOTH_BULLPENS_TIRED in out["hidden_over_routes"]
        assert out["delta"] >= 8
        assert out["adjusted_fragility"] > 20
        
    def test_volatile_starter(self):
        """Volatile starter (era>=4.50 OR whip>=1.35) → HOR_MEDIOCRE_OR_VOLATILE_STARTER, delta >= 5."""
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            home_pitcher={"era": 4.92, "whip": 1.43},
            away_pitcher={"era": 3.2, "whip": 1.1},
        )
        
        assert HOR_MEDIOCRE_VOLATILE_STARTER in out["hidden_over_routes"]
        assert out["delta"] >= 5
        
    def test_series_familiarity(self):
        """series_familiarity_score>=40 → HOR_SERIES_FAMILIARITY_ACTIVE, delta=5."""
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            series_familiarity={"series_familiarity_score": 45},
        )
        
        assert HOR_SERIES_FAMILIARITY_ACTIVE in out["hidden_over_routes"]
        assert out["delta"] == 5
        
    def test_late_lambda_elevated(self):
        """λ_7_9 > 35% of total OR is highest → HOR_LATE_LAMBDA_ELEVATED, delta 5-8."""
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            inning_lambda_projection={
                "lambda_1_3": 2.0,
                "lambda_4_6": 2.0,
                "lambda_7_9": 3.5,
            },
        )
        
        assert HOR_LATE_LAMBDA_ELEVATED in out["hidden_over_routes"]
        assert 5 <= out["delta"] <= 8
        
    def test_tail_risk_present(self):
        """tail_risk.p_ge_12 >= 0.12 → HOR_TAIL_RISK_PRESENT, delta=5 (medium) or 10 (>=0.22)."""
        # Medium tail risk
        out_medium = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            tail_risk={"p_ge_12": 0.16},
        )
        assert HOR_TAIL_RISK_PRESENT in out_medium["hidden_over_routes"]
        assert out_medium["delta"] == 5
        
        # High tail risk
        out_high = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            tail_risk={"p_ge_12": 0.25},
        )
        assert HOR_TAIL_RISK_PRESENT in out_high["hidden_over_routes"]
        assert out_high["delta"] == 10
        
    def test_traffic_and_defense_risk(self):
        """traffic_score>=55 or defensive_breakdown>=55 → routes correspondientes."""
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            traffic_score=60,
            defensive_breakdown_score=58,
        )
        
        assert HOR_TRAFFIC_RISK in out["hidden_over_routes"]
        assert HOR_DEFENSIVE_RISK in out["hidden_over_routes"]
        
    def test_caps_respected(self):
        """Caps: delta total max 20, adjusted_fragility max 85, never decrementa."""
        # Test max delta cap
        out_delta = calibrate_fragility(
            base_fragility=50,
            market_side="under",
            home_pitcher={"era": 6.0, "whip": 1.6},
            away_pitcher={"era": 6.0, "whip": 1.6},
            bullpen_home={"bullpen_usage_3d": 0.95},
            bullpen_away={"bullpen_usage_3d": 0.95},
            series_familiarity={"series_familiarity_score": 90},
            inning_lambda_projection={"lambda_1_3": 1.0, "lambda_4_6": 1.0, "lambda_7_9": 5.0},
            traffic_score=85,
            defensive_breakdown_score=85,
            tail_risk={"p_ge_12": 0.45},
        )
        assert out_delta["delta"] <= MAX_DELTA
        
        # Test ceiling cap
        out_ceiling = calibrate_fragility(
            base_fragility=80,
            market_side="under",
            bullpen_home={"bullpen_usage_3d": 0.95},
            bullpen_away={"bullpen_usage_3d": 0.95},
            home_pitcher={"era": 6.0, "whip": 1.7},
        )
        assert out_ceiling["adjusted_fragility"] <= MAX_CEILING
        
        # Test never decreases
        out_clean = calibrate_fragility(
            base_fragility=50,
            market_side="under",
            home_pitcher={"era": 2.0, "whip": 0.9},
            away_pitcher={"era": 2.0, "whip": 0.9},
        )
        assert out_clean["delta"] >= 0
        assert out_clean["adjusted_fragility"] >= out_clean["base_fragility"]
        
    def test_market_side_preserved(self):
        """market_side se preserva (nunca flip)."""
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            bullpen_home={"bullpen_usage_3d": 0.80},
            bullpen_away={"bullpen_usage_3d": 0.80},
        )
        assert out["market_side"] == "under"


class TestFeature3YankeesGuardiansCombined:
    """Feature 3 — Yankees @ Guardians combined case."""
    
    def test_combined_case_fragility_range(self):
        """
        Case: base_fragility=20 + both_bullpens (usage 0.65/0.62) + volatile starter (era 4.92, whip 1.43)
        + series 45 + traffic 40 + defense 40
        Expected: adjusted_fragility in range [28, 38] (delta ≈ 18: 8+5+5)
        """
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            expected_runs=8.0,
            market_line=10.5,
            # λ_4_6 highest so late lambda DOESN'T add to fragility
            inning_lambda_projection={
                "lambda_1_3": 2.5,
                "lambda_4_6": 3.0,
                "lambda_7_9": 2.5,
            },
            home_pitcher={"era": 3.2, "whip": 1.10},  # solid home starter
            away_pitcher={"era": 4.92, "whip": 1.43},  # Cecconi-like
            bullpen_home={"bullpen_usage_3d": 0.65},
            bullpen_away={"bullpen_usage_3d": 0.62},
            series_familiarity={"series_familiarity_score": 45},
            traffic_score=40,
            defensive_breakdown_score=40,
            tail_risk={"p_ge_12": 0.08},
        )
        
        # Verify expected hidden routes
        assert HOR_BOTH_BULLPENS_TIRED in out["hidden_over_routes"]
        assert HOR_MEDIOCRE_VOLATILE_STARTER in out["hidden_over_routes"]
        assert HOR_SERIES_FAMILIARITY_ACTIVE in out["hidden_over_routes"]
        
        # Verify adjusted_fragility in target range [28, 38]
        assert 28 <= out["adjusted_fragility"] <= 38, \
            f"adjusted_fragility {out['adjusted_fragility']} not in range [28, 38]"
        
        # Verify RC_HIDDEN_OVER_ROUTES_DETECTED is present
        assert RC_HIDDEN_OVER_ROUTES_DETECTED in out["reason_codes"]


class TestFeature4MarketProfile:
    """Feature 4 — interpret_market_profile validation."""
    
    def test_clean_under_profile(self):
        """tail bucket LOW + under_quality MEAN_AND_TAIL_SUPPORTED → profile='CLEAN_UNDER' + RC_CLEAN_UNDER_PROFILE."""
        d = compute_expected_runs_distribution(
            expected_runs=6.5,
            market="total_runs_under",
            market_line=10.5,
            nb_dispersion_ratio=1.0,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        i = interpret_market_profile(distribution_payload=d, tail_risk_payload=t)
        
        assert i["profile"] == "CLEAN_UNDER"
        assert RC_CLEAN_UNDER_PROFILE in i["reason_codes"]
        
    def test_mean_supported_fragile_under(self):
        """under_quality MEAN_SUPPORTED_BUT_TAIL_FRAGILE → profile='MEAN_SUPPORTED_FRAGILE_UNDER' + RC_PROTECTED_LINE_PREFERRED_DUE_TO_TAIL."""
        d = compute_expected_runs_distribution(
            expected_runs=8.0,
            market="total_runs_under",
            market_line=10.5,
            nb_dispersion_ratio=1.4,
            fragility_score=40,
            traffic_score=65,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        i = interpret_market_profile(distribution_payload=d, tail_risk_payload=t)
        
        assert i["profile"] == "MEAN_SUPPORTED_FRAGILE_UNDER"
        assert RC_PROTECTED_LINE_PREFERRED_DUE_TO_TAIL in i["reason_codes"]
        
    def test_over_lives_through_tail(self):
        """side=over + tail HIGH/EXTREME → profile='OVER_LIVES_THROUGH_TAIL'."""
        d = compute_expected_runs_distribution(
            expected_runs=10.5,
            market="total_runs_over",
            market_line=9.5,
            nb_dispersion_ratio=1.7,
            fragility_score=70,
            traffic_score=75,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=9.5, market_side="over")
        i = interpret_market_profile(distribution_payload=d, tail_risk_payload=t)
        
        if t["tail_bucket"] in (TAIL_BUCKET_HIGH, TAIL_BUCKET_EXTREME):
            assert i["profile"] == "OVER_LIVES_THROUGH_TAIL"
            assert RC_OVER_LIVES_THROUGH_TAIL in i["reason_codes"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
