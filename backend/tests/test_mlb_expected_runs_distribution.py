"""Tests for MLB Expected Runs Distribution (Priority 4)."""
from __future__ import annotations

import pytest

from services.mlb_expected_runs_distribution import (
    compute_expected_runs_distribution,
    BUCKET_LOW,
    BUCKET_MEDIUM,
    BUCKET_HIGH,
    RC_DISTRIBUTION_USED,
    RC_NEGATIVE_BINOMIAL_USED,
    RC_POISSON_FALLBACK,
    RC_HIGH_UNCERTAINTY_BULLPEN_TRAFFIC,
    RC_HIGH_UNCERTAINTY_DEFENSIVE_BREAKDOWN,
    RC_HIGH_UNCERTAINTY_SERIES_FAMILIARITY,
    RC_HIGH_UNCERTAINTY_FRAGILITY,
    RC_LOW_UNCERTAINTY_STABLE_SCRIPT,
    RC_PROTECTED_LINE_RECOMMENDED,
    RC_ULTRA_SAFE_LINE_AVAILABLE,
)


def _base(**over):
    base = dict(
        expected_runs=8.5,
        market="total_runs_under",
        market_line=9.5,
    )
    base.update(over)
    return base


# =====================================================================
# Distribution selection
# =====================================================================
class TestDistributionSelection:
    def test_poisson_fallback_when_no_nb_ratio(self):
        out = compute_expected_runs_distribution(**_base())
        assert out["available"] is True
        assert out["distribution"] == "poisson"
        assert RC_POISSON_FALLBACK in out["reason_codes"]
        assert RC_NEGATIVE_BINOMIAL_USED not in out["reason_codes"]

    def test_negative_binomial_when_ratio_above_one(self):
        out = compute_expected_runs_distribution(**_base(
            nb_dispersion_ratio=1.5,
        ))
        assert out["distribution"] == "negative_binomial"
        assert RC_NEGATIVE_BINOMIAL_USED in out["reason_codes"]
        assert out["effective_dispersion_ratio"] >= 1.5 - 0.001

    def test_ratio_below_one_falls_back_to_poisson(self):
        out = compute_expected_runs_distribution(**_base(
            nb_dispersion_ratio=0.5,
        ))
        # ratio clamped at 0.9; still > 1.001? No → Poisson fallback.
        assert out["distribution"] == "poisson"

    def test_fail_soft_on_missing_mean(self):
        out = compute_expected_runs_distribution(expected_runs=None)
        assert out["available"] is False
        assert RC_POISSON_FALLBACK in out["reason_codes"]


# =====================================================================
# Quantiles + probabilities
# =====================================================================
class TestQuantiles:
    def test_quantiles_increase_monotonically(self):
        out = compute_expected_runs_distribution(**_base())
        assert out["p10"] <= out["p25"] <= out["median"] <= out["p75"] <= out["p90"]

    def test_probabilities_sum_under_plus_over_equals_one(self):
        out = compute_expected_runs_distribution(**_base())
        probs = out["probabilities"]
        for k in ("8_5", "9_5"):
            assert abs(probs[f"under_{k}"] + probs[f"over_{k}"] - 1.0) < 0.001

    def test_high_uncertainty_widens_range(self):
        """High fragility / traffic / defense → wider p10–p90 spread."""
        low = compute_expected_runs_distribution(**_base())
        high = compute_expected_runs_distribution(**_base(
            nb_dispersion_ratio=1.4,
            fragility_score=80,
            traffic_score=75,
            defensive_breakdown_score=70,
            bullpen_fatigue=0.75,
            series_familiarity_score=70,
        ))
        spread_low  = low["p90"] - low["p10"]
        spread_high = high["p90"] - high["p10"]
        assert spread_high > spread_low
        assert high["uncertainty_bucket"] == BUCKET_HIGH

    def test_low_uncertainty_keeps_narrow_range_and_bucket(self):
        out = compute_expected_runs_distribution(**_base(
            nb_dispersion_ratio=1.0,
            script_survival=0.85,
        ))
        assert out["uncertainty_bucket"] in (BUCKET_LOW, BUCKET_MEDIUM)
        assert RC_LOW_UNCERTAINTY_STABLE_SCRIPT in out["reason_codes"]

    def test_high_uncertainty_reason_codes(self):
        out = compute_expected_runs_distribution(**_base(
            fragility_score=80,
            traffic_score=80,
            defensive_breakdown_score=80,
            series_familiarity_score=80,
        ))
        assert RC_HIGH_UNCERTAINTY_FRAGILITY            in out["reason_codes"]
        assert RC_HIGH_UNCERTAINTY_BULLPEN_TRAFFIC      in out["reason_codes"]
        assert RC_HIGH_UNCERTAINTY_DEFENSIVE_BREAKDOWN  in out["reason_codes"]
        assert RC_HIGH_UNCERTAINTY_SERIES_FAMILIARITY   in out["reason_codes"]


# =====================================================================
# Protected lines + polarity
# =====================================================================
class TestProtectedLines:
    def test_under_protected_line_higher_than_value(self):
        out = compute_expected_runs_distribution(**_base(
            market="total_runs_under", market_line=9.5,
        ))
        pl = out["protected_lines"]
        # value_line = "Under 9.5", protected_line = "Under 10.5"
        assert pl["value_line"] == "Under 9.5"
        assert pl["protected_line"] == "Under 10.5"
        assert pl["ultra_safe_line"] == "Under 11.5"
        assert out["side"] == "under"

    def test_over_protected_line_lower_than_value(self):
        out = compute_expected_runs_distribution(**_base(
            market="total_runs_over", market_line=8.5,
        ))
        pl = out["protected_lines"]
        assert pl["value_line"] == "Over 8.5"
        assert pl["protected_line"] == "Over 7.5"
        assert pl["ultra_safe_line"] == "Over 6.5"
        assert out["side"] == "over"

    def test_protected_line_reason_codes_emitted(self):
        out = compute_expected_runs_distribution(**_base())
        assert RC_PROTECTED_LINE_RECOMMENDED in out["reason_codes"]
        assert RC_ULTRA_SAFE_LINE_AVAILABLE  in out["reason_codes"]

    def test_explanation_es_built_for_under(self):
        out = compute_expected_runs_distribution(**_base(
            market="total_runs_under", market_line=9.5,
        ))
        assert out["explanation_es"] is not None
        assert "Under" in out["explanation_es"]
        assert "%" in out["explanation_es"]


# =====================================================================
# Polarity guarantee
# =====================================================================
class TestPolarityGuarantee:
    def test_under_market_never_returns_over_value_line(self):
        out = compute_expected_runs_distribution(**_base(
            market="total_runs_under", market_line=9.5,
        ))
        # No protected_line / ultra_safe_line ever starts with "Over".
        for k in ("value_line", "protected_line", "ultra_safe_line"):
            v = out["protected_lines"].get(k) or ""
            assert not v.startswith("Over")

    def test_over_market_never_returns_under_value_line(self):
        out = compute_expected_runs_distribution(**_base(
            market="total_runs_over", market_line=8.5,
        ))
        for k in ("value_line", "protected_line", "ultra_safe_line"):
            v = out["protected_lines"].get(k) or ""
            assert not v.startswith("Under")


# =====================================================================
# Bucket boundaries
# =====================================================================
class TestBuckets:
    def test_bucket_high_when_extreme_dispersion(self):
        out = compute_expected_runs_distribution(**_base(
            nb_dispersion_ratio=2.0, fragility_score=80,
        ))
        assert out["uncertainty_bucket"] == BUCKET_HIGH

    def test_bucket_low_when_clean_inputs(self):
        out = compute_expected_runs_distribution(**_base(
            nb_dispersion_ratio=1.0, script_survival=0.9,
        ))
        assert out["uncertainty_bucket"] == BUCKET_LOW
