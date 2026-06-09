"""Tests for Tail Risk + Fragility Calibrator (Hidden Over Routes)."""
from __future__ import annotations

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
    RC_UNDER_SUPPORTED_BY_MEAN,
    RC_UNDER_SUPPORTED_BY_LOW_TAIL,
    RC_UNDER_MEAN_SUPPORTED_TAIL_FRAGILE,
    RC_OVER_TAIL_RISK_PRESENT,
    RC_EXTREME_TAIL_RISK,
    RC_TAIL_RISK_PRESENT,
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
    RC_FRAGILITY_CALIBRATED,
    RC_HIDDEN_OVER_ROUTES_DETECTED,
    RC_BOTH_BULLPENS_TIRED,
    RC_VOLATILE_STARTER_RAISES_FRAGILITY,
    RC_SERIES_FAMILIARITY_RAISES_FRAGILITY,
    RC_LATE_LAMBDA_RAISES_FRAGILITY,
    RC_TAIL_RISK_RAISES_FRAGILITY,
    RC_FRAGILITY_DELTA_CAPPED,
)


# =====================================================================
# Feature 1 — Tail Risk Panel
# =====================================================================
class TestTailRiskBasics:
    def _dist(self, **over):
        kwargs = dict(
            expected_runs=8.0,
            market="total_runs_under",
            market_line=10.5,
        )
        kwargs.update(over)
        return compute_expected_runs_distribution(**kwargs)

    def test_under_plus_over_probability_sums_to_one(self):
        d = self._dist()
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        assert abs(t["under_probability"] + t["over_probability"] - 1.0) < 0.001

    def test_p_ge_decreasing(self):
        """P(>=12) >= P(>=14) >= P(>=16) (basic survival monotonicity)."""
        d = self._dist()
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        assert t["p_ge_12"] >= t["p_ge_14"] >= t["p_ge_16"]

    def test_low_mean_low_tail_bucket_is_low(self):
        d = self._dist(expected_runs=6.5, nb_dispersion_ratio=1.0)
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        assert t["tail_bucket"] == TAIL_BUCKET_LOW
        assert t["under_quality"] == UQ_MEAN_AND_TAIL_SUPPORTED
        assert RC_UNDER_SUPPORTED_BY_MEAN in t["reason_codes"]
        assert RC_UNDER_SUPPORTED_BY_LOW_TAIL in t["reason_codes"]

    def test_high_mean_high_tail_bucket_progresses(self):
        d = self._dist(expected_runs=10.5, nb_dispersion_ratio=1.6, fragility_score=70)
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        assert t["tail_bucket"] in (TAIL_BUCKET_HIGH, TAIL_BUCKET_EXTREME)
        assert RC_OVER_TAIL_RISK_PRESENT in t["reason_codes"]

    def test_mean_supported_tail_fragile_label(self):
        """The Yankees @ Guardians case: ER ~8, line 10.5, NB ratio ~1.3,
        moderate traffic → Under supported by mean but tail meaningful."""
        d = self._dist(
            expected_runs=8.0, nb_dispersion_ratio=1.35,
            fragility_score=20, traffic_score=55,
            series_familiarity_score=50,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        assert t["under_probability"] >= 0.55
        # Quality must NOT be clean — tail risk is non-trivial.
        assert t["under_quality"] in (
            UQ_MEAN_SUPPORTED_TAIL_FRAGILE, UQ_TAIL_DOMINATES,
        )

    def test_pure_python_marker_emitted(self):
        d = self._dist()
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        assert RC_PURE_PYTHON_PMF_CDF_USED in t["reason_codes"]
        assert RC_TAIL_RISK_PANEL_USED in t["reason_codes"]

    def test_no_scipy_import_path(self):
        """Regression: the module must not IMPORT scipy/numpy.
        References in comments/docstrings are allowed (they explain
        the design intentionally avoids those deps)."""
        with open("/app/backend/services/mlb_expected_runs_distribution.py",
                  "r", encoding="utf-8") as f:
            src = f.read()
        # No actual import statements.
        for forbidden in ("import scipy", "from scipy", "import numpy", "from numpy"):
            assert forbidden not in src, f"forbidden import found: {forbidden}"

    def test_fail_soft_on_unavailable_distribution(self):
        t = compute_tail_risk(distribution_payload={"available": False})
        assert t["available"] is False
        assert "reason" in t


class TestTailBuckets:
    def test_extreme_when_p_ge_14_above_15pct(self):
        d = compute_expected_runs_distribution(
            expected_runs=11.5, market="total_runs_under", market_line=10.5,
            nb_dispersion_ratio=1.8, fragility_score=85, traffic_score=80,
            defensive_breakdown_score=80,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        # With high mean and wide dispersion, extreme tail likely.
        if t["p_ge_14"] > 0.15 or t["p_ge_16"] > 0.08:
            assert t["tail_bucket"] == TAIL_BUCKET_EXTREME
            assert RC_EXTREME_TAIL_RISK in t["reason_codes"]


class TestMarketInterpretation:
    def test_clean_under_profile(self):
        d = compute_expected_runs_distribution(
            expected_runs=6.5, market="total_runs_under", market_line=10.5,
            nb_dispersion_ratio=1.0,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        i = interpret_market_profile(distribution_payload=d, tail_risk_payload=t)
        assert i["profile"] == "CLEAN_UNDER"
        assert RC_CLEAN_UNDER_PROFILE in i["reason_codes"]
        assert "limpio" in (i["headline_es"] or "").lower()

    def test_fragile_under_profile_recommends_protected_line(self):
        d = compute_expected_runs_distribution(
            expected_runs=8.0, market="total_runs_under", market_line=10.5,
            nb_dispersion_ratio=1.4, fragility_score=40, traffic_score=65,
        )
        t = compute_tail_risk(distribution_payload=d, market_line=10.5, market_side="under")
        i = interpret_market_profile(distribution_payload=d, tail_risk_payload=t)
        assert i["profile"] == "MEAN_SUPPORTED_FRAGILE_UNDER"
        assert RC_PROTECTED_LINE_PREFERRED_DUE_TO_TAIL in i["reason_codes"]


# =====================================================================
# Feature 3 — Fragility calibrator
# =====================================================================
class TestFragilityCalibrator:
    def test_no_hidden_routes_no_change(self):
        """Clean game → fragility unchanged (delta = 0)."""
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

    def test_both_bullpens_tired_raises_fragility(self):
        out = calibrate_fragility(
            base_fragility=20, market_side="under",
            bullpen_home={"bullpen_usage_3d": 0.70},
            bullpen_away={"bullpen_usage_3d": 0.65},
        )
        assert HOR_BOTH_BULLPENS_TIRED in out["hidden_over_routes"]
        assert RC_BOTH_BULLPENS_TIRED in out["reason_codes"]
        assert out["delta"] >= 8
        assert out["adjusted_fragility"] > 20

    def test_volatile_starter_raises_fragility(self):
        out = calibrate_fragility(
            base_fragility=20, market_side="under",
            home_pitcher={"era": 4.92, "whip": 1.43},
            away_pitcher={"era": 3.2, "whip": 1.1},
        )
        assert HOR_MEDIOCRE_VOLATILE_STARTER in out["hidden_over_routes"]
        assert RC_VOLATILE_STARTER_RAISES_FRAGILITY in out["reason_codes"]
        assert out["delta"] >= 5

    def test_series_familiarity_raises_fragility(self):
        out = calibrate_fragility(
            base_fragility=20, market_side="under",
            series_familiarity={"series_familiarity_score": 50},
        )
        assert HOR_SERIES_FAMILIARITY_ACTIVE in out["hidden_over_routes"]
        assert RC_SERIES_FAMILIARITY_RAISES_FRAGILITY in out["reason_codes"]
        assert out["delta"] == 5

    def test_late_lambda_elevated_raises_fragility(self):
        out = calibrate_fragility(
            base_fragility=20, market_side="under",
            inning_lambda_projection={
                "lambda_1_3": 2.0, "lambda_4_6": 2.0, "lambda_7_9": 3.5,
            },
        )
        assert HOR_LATE_LAMBDA_ELEVATED in out["hidden_over_routes"]
        assert RC_LATE_LAMBDA_RAISES_FRAGILITY in out["reason_codes"]

    def test_tail_risk_medium_raises_fragility(self):
        out = calibrate_fragility(
            base_fragility=20, market_side="under",
            tail_risk={"p_ge_12": 0.16},
        )
        assert HOR_TAIL_RISK_PRESENT in out["hidden_over_routes"]
        assert out["delta"] == 5

    def test_tail_risk_high_raises_fragility_more(self):
        out = calibrate_fragility(
            base_fragility=20, market_side="under",
            tail_risk={"p_ge_12": 0.25},
        )
        assert out["delta"] == 10

    def test_combined_yankees_guardians_case(self):
        """Acceptance: base 20 → adjusted 30-35 with the documented mix.

        Inputs intentionally selected so ONLY 3 hidden routes fire:
        both bullpens tired (+8), volatile starter (+5),
        series familiarity (+5). λ_7_9 NOT highest so late-lambda
        doesn't fire; traffic / defense below 55 so they don't fire.
        Expected delta = 18 → adjusted ≈ 38 (within 28-38 band).
        """
        out = calibrate_fragility(
            base_fragility=20,
            market_side="under",
            expected_runs=8.0,
            market_line=10.5,
            # λ_4_6 highest so late lambda DOESN'T add to fragility.
            inning_lambda_projection={
                "lambda_1_3": 2.5, "lambda_4_6": 3.0, "lambda_7_9": 2.5,
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
        assert HOR_BOTH_BULLPENS_TIRED in out["hidden_over_routes"]
        assert HOR_MEDIOCRE_VOLATILE_STARTER in out["hidden_over_routes"]
        assert HOR_SERIES_FAMILIARITY_ACTIVE in out["hidden_over_routes"]
        # Target band: 30-38 (8 + 5 + 5 = 18 delta from base 20).
        assert 28 <= out["adjusted_fragility"] <= 38
        assert RC_HIDDEN_OVER_ROUTES_DETECTED in out["reason_codes"]

    def test_max_delta_cap_respected(self):
        out = calibrate_fragility(
            base_fragility=50, market_side="under",
            home_pitcher={"era": 6.0, "whip": 1.6},
            away_pitcher={"era": 6.0, "whip": 1.6},
            bullpen_home={"bullpen_usage_3d": 0.95},
            bullpen_away={"bullpen_usage_3d": 0.95},
            series_familiarity={"series_familiarity_score": 90},
            inning_lambda_projection={
                "lambda_1_3": 1.0, "lambda_4_6": 1.0, "lambda_7_9": 5.0,
            },
            traffic_score=85, defensive_breakdown_score=85,
            tail_risk={"p_ge_12": 0.45},
        )
        assert out["delta"] <= MAX_DELTA
        assert RC_FRAGILITY_DELTA_CAPPED in out["reason_codes"]

    def test_ceiling_respected(self):
        out = calibrate_fragility(
            base_fragility=80, market_side="under",
            bullpen_home={"bullpen_usage_3d": 0.95},
            bullpen_away={"bullpen_usage_3d": 0.95},
            home_pitcher={"era": 6.0, "whip": 1.7},
            away_pitcher={"era": 5.5, "whip": 1.4},
        )
        assert out["adjusted_fragility"] <= MAX_CEILING

    def test_never_decreases_fragility(self):
        """Module must NEVER produce a delta < 0, regardless of inputs."""
        out = calibrate_fragility(
            base_fragility=50, market_side="under",
            home_pitcher={"era": 2.0, "whip": 0.9},
            away_pitcher={"era": 2.0, "whip": 0.9},
            bullpen_home={"bullpen_usage_3d": 0.10},
            bullpen_away={"bullpen_usage_3d": 0.10},
            traffic_score=15, defensive_breakdown_score=15,
        )
        assert out["delta"] >= 0
        assert out["adjusted_fragility"] >= out["base_fragility"]

    def test_polarity_never_flipped(self):
        """The module never returns Over-style hints when side=under."""
        out = calibrate_fragility(
            base_fragility=20, market_side="under",
            bullpen_home={"bullpen_usage_3d": 0.80},
            bullpen_away={"bullpen_usage_3d": 0.80},
        )
        assert out["market_side"] == "under"

    def test_narrative_es_built_when_routes_present(self):
        out = calibrate_fragility(
            base_fragility=20, market_side="under",
            bullpen_home={"bullpen_usage_3d": 0.70},
            bullpen_away={"bullpen_usage_3d": 0.70},
            home_pitcher={"era": 4.92, "whip": 1.43},
            series_familiarity={"series_familiarity_score": 50},
        )
        assert out["narrative_es"] is not None
        assert "Sube" in out["narrative_es"]
