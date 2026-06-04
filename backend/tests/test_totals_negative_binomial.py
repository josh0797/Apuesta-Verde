"""Tests for the Negative-Binomial totals model + dispersion calibration.

Covers:
  * `totals_probability` default model is NegativeBinomial.
  * NB returns LOWER Under probability than Poisson for half-lines
    where lambda is below the line (under was over-estimated by Poisson).
  * `model="Poisson"` reproduces the legacy behaviour exactly.
  * `dispersion_ratio=1.0` converges to Poisson.
  * `prob_under + prob_over == 1` (no leakage).
  * Integer-line push split (50/50) still works under NB.
  * `_compute_totals_dispersion_calibration` returns
    `available:false` with `<30` samples, and a valid suggested ratio
    when `>=30` samples are provided.
"""
from __future__ import annotations

import math

import pytest

from services.mlb_pregame_analytics_v2 import (
    totals_probability,
    MLB_TOTALS_DISPERSION_RATIO,
    _poisson_cdf,
    _nb_cdf,
    _nb_pmf,
    _nb_params_from_mean,
)
from services.mlb_run_evaluations_summary import (
    _compute_totals_dispersion_calibration,
)


# ─────────────────────────────────────────────────────────────────────
# totals_probability — model selection & telemetry
# ─────────────────────────────────────────────────────────────────────
def test_default_model_is_negative_binomial():
    r = totals_probability(6.1, 9.5)
    assert r["model"] == "NegativeBinomial"
    assert r["dispersion_ratio"] == MLB_TOTALS_DISPERSION_RATIO


def test_nb_corrects_poisson_overestimation_of_under():
    r = totals_probability(6.1, 9.5)
    # Poisson said this Under was ~91%; NB tells the honest ~87%.
    assert r["under_calibration_delta_pts"] > 0
    assert r["prob_under"] < r["poisson_prob_under"]


def test_nb_correction_grows_with_distance_below_line():
    """The further the expected_runs is BELOW the line, the more
    Poisson over-estimates the Under (because the tail at high totals
    is fat in real life). This test pins the property."""
    far_below = totals_probability(5.0, 9.5)   # lambda << line
    close_to  = totals_probability(8.5, 9.5)   # lambda close to line
    # Both must be positive (NB more conservative than Poisson).
    assert far_below["under_calibration_delta_pts"] > 0
    # But the correction is LARGER (or equal) when farther below.
    assert far_below["under_calibration_delta_pts"] >= \
           close_to["under_calibration_delta_pts"]


def test_force_poisson_reproduces_legacy_behaviour():
    r = totals_probability(6.1, 9.5, model="Poisson")
    assert r["model"] == "Poisson"
    # When forced to Poisson, prob_under must equal poisson_prob_under.
    assert abs(r["prob_under"] - r["poisson_prob_under"]) < 1e-9


def test_dispersion_ratio_1_converges_to_poisson():
    """When dispersion_ratio → 1, NB collapses to Poisson."""
    r = totals_probability(6.1, 9.5, dispersion_ratio=1.0)
    assert r["model"] == "NegativeBinomial"
    # Tolerance: 1 percentage point (the fallback uses k=1e6, a tiny
    # numerical residual is expected).
    assert abs(r["prob_under"] - r["poisson_prob_under"]) < 0.01


def test_probabilities_sum_to_one():
    for mu, line in [(4.0, 7.5), (6.1, 9.5), (8.5, 9.5), (12.0, 10.5)]:
        r = totals_probability(mu, line)
        assert abs(r["prob_under"] + r["prob_over"] - 1.0) < 1e-6


def test_integer_line_splits_push_50_50_in_nb():
    """Integer line (line=8.0) → push split 50/50 under NB too."""
    r = totals_probability(8.0, 8.0)
    # Push prob_eq is roughly the highest single point mass; the split
    # should yield prob_under and prob_over both close to 0.5.
    assert 0.40 < r["prob_under"] < 0.60
    assert abs(r["prob_under"] + r["prob_over"] - 1.0) < 1e-6


def test_extreme_low_lambda_does_not_blow_up():
    r = totals_probability(0.05, 4.5)
    assert 0.0 <= r["prob_under"] <= 1.0
    assert r["prob_under"] > 0.95  # Very low lambda → Under ≈ certain.


def test_extreme_high_lambda_clamped():
    r = totals_probability(30.0, 4.5)   # clamped to 25
    # At lambda=25 and line=4.5, Over is overwhelmingly likely.
    assert r["prob_under"] < 0.10
    assert r["lambda"] == 25.0


def test_invalid_inputs_fail_soft():
    r = totals_probability("not a number", "neither")
    assert r["lambda"] == 0.05
    assert r["line"] == 0.0
    assert 0.0 <= r["prob_under"] <= 1.0


# ─────────────────────────────────────────────────────────────────────
# Internal NB primitives — sanity checks
# ─────────────────────────────────────────────────────────────────────
def test_nb_params_recovers_poisson_for_ratio_one():
    k, _ = _nb_params_from_mean(8.0, 1.0)
    # Ratio≈1 → k is the huge fallback.
    assert k > 1e5


def test_nb_cdf_monotone():
    """NB CDF must be non-decreasing in x."""
    prev = -1.0
    for x in range(0, 15):
        v = _nb_cdf(x, 6.1, 1.5)
        assert v >= prev
        prev = v
    assert prev <= 1.0


def test_nb_pmf_sums_to_one_over_large_range():
    s = sum(_nb_pmf(n, 6.1, 1.5) for n in range(0, 60))
    assert abs(s - 1.0) < 1e-3   # truncation tolerance


# ─────────────────────────────────────────────────────────────────────
# Dispersion calibration feedback loop
# ─────────────────────────────────────────────────────────────────────
def test_dispersion_calibration_insufficient_samples():
    res = _compute_totals_dispersion_calibration([])
    assert res["available"] is False
    assert res["reason"] == "insufficient_samples"
    assert res["current_default"] == 1.5


def test_dispersion_calibration_returns_suggestion_with_samples():
    """Build 50 synthetic docs with known variance and check the
    calibration produces a plausible ratio."""
    import random
    random.seed(123)
    docs = []
    for _ in range(50):
        exp_total = 8.5
        # Force overdispersion: actual = exp + N(0, sigma=3) → var≈9, mean≈8.5
        actual = exp_total + random.gauss(0, 3)
        docs.append({"expected_total": exp_total, "actual_total": max(0, actual)})

    res = _compute_totals_dispersion_calibration(docs)
    assert res["available"] is True
    assert res["sample_size"] == 50
    assert res["mean_expected_total"] == pytest.approx(8.5, abs=0.01)
    # Suggested ratio must be inside the clamp range.
    assert 1.0 <= res["suggested_ratio"] <= 2.5
    assert res["confidence_tier"] == "LOW_SAMPLE"
    assert res["recommendation"] in (
        "default_ok", "tighten_dispersion_lower", "loosen_dispersion_higher",
    )


def test_dispersion_calibration_ignores_invalid_pairs():
    docs = [
        {"expected_total": "not a number", "actual_total": 8},
        {"expected_total": None,            "actual_total": 8},
        {"expected_total": -1,              "actual_total": 8},  # non-positive
        {"expected_total": 8.5,             "actual_total": -1}, # negative
    ]
    res = _compute_totals_dispersion_calibration(docs)
    # All pairs filtered out → not enough samples.
    assert res["available"] is False
    assert res["sample_size"] == 0
