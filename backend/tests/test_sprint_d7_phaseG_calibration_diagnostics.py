"""Sprint-D7-G · Tests for the calibration diagnostics module + endpoints.

Covers:
- Brier / log-loss math invariants.
- AUC monotonic in informativeness.
- Calibration regression slope==1 / intercept==0 on perfectly calibrated
  synthetic data; degraded on miscalibrated data.
- Reliability bucketization.
- CLV computation.
- classify_model_quality rubric applied to the user's 4 scenarios.
- Endpoint behaviour (index + per-report fetch).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from services.football_calibration_diagnostics import (
    compute_calibration_diagnostics, classify_model_quality,
)


# ════════════════════════════════════════════════════════════════════════
# Synthetic builders
# ════════════════════════════════════════════════════════════════════════
def _build_perfect_records(n_per_bucket: int = 100, seed: int = 0):
    """Synthetic dataset where ``p_pred == p_true`` exactly.

    For each bucket centre p ∈ {0.05, 0.15, ..., 0.95} we deterministically
    produce ``n_per_bucket`` outcomes that hit p·n times. This produces
    a perfectly calibrated dataset for slope=1, intercept=0 testing.
    """
    recs = []
    centres = [0.05 + 0.1 * i for i in range(10)]
    for p in centres:
        n_hit = int(round(p * n_per_bucket))
        for k in range(n_per_bucket):
            recs.append({
                "predicted_prob":         p,
                "market_implied_raw":     p,
                "market_implied_devig":   p,
                "hit":                    (k < n_hit),
                "odd_open":               1.0 / max(p, 1e-3),
                "odd_close":              1.0 / max(p, 1e-3),
                "market_implied_raw_close":   p,
                "market_implied_devig_close": p,
                "fired":                  False,
            })
    return recs


def _build_uninformative_records(n: int = 500):
    """Model emits a constant 0.5; outcomes are 50/50."""
    recs = []
    for i in range(n):
        recs.append({
            "predicted_prob":         0.5,
            "market_implied_raw":     0.5,
            "market_implied_devig":   0.5,
            "hit":                    (i % 2 == 0),
            "odd_open":               2.0,
            "odd_close":              2.0,
            "market_implied_raw_close":   0.5,
            "market_implied_devig_close": 0.5,
            "fired":                  False,
        })
    return recs


def _build_overconfident_records():
    """Model says 0.9 but realized rate is 0.5 — bad calibration but
    no discrimination (single bucket). slope should be near 0."""
    recs = []
    for i in range(200):
        recs.append({
            "predicted_prob":         0.9,
            "market_implied_raw":     0.5,
            "market_implied_devig":   0.5,
            "hit":                    (i % 2 == 0),
            "odd_open":               1.5,
            "odd_close":              1.5,
            "market_implied_raw_close":   0.5,
            "market_implied_devig_close": 0.5,
            "fired":                  False,
        })
    return recs


def _build_discriminative_but_miscalibrated():
    """Two buckets: model says 0.3 vs 0.7; realized 0.4 vs 0.55.
    AUC > 0.5 (since high preds → more hits), but slope < 1."""
    recs = []
    for i in range(200):                # bucket 0.3, hit_rate 0.4
        recs.append({"predicted_prob": 0.3, "market_implied_raw": 0.5,
                     "market_implied_devig": 0.5, "hit": (i < 80),
                     "odd_open": 3.0, "odd_close": 3.0,
                     "market_implied_raw_close": 0.5,
                     "market_implied_devig_close": 0.5, "fired": False})
    for i in range(200):                # bucket 0.7, hit_rate 0.55
        recs.append({"predicted_prob": 0.7, "market_implied_raw": 0.5,
                     "market_implied_devig": 0.5, "hit": (i < 110),
                     "odd_open": 1.45, "odd_close": 1.45,
                     "market_implied_raw_close": 0.5,
                     "market_implied_devig_close": 0.5, "fired": False})
    return recs


# ════════════════════════════════════════════════════════════════════════
# 1) Brier / log-loss math
# ════════════════════════════════════════════════════════════════════════
def test_brier_zero_for_perfect_prediction():
    """If p=1 when y=1 and p=0 when y=0, Brier = 0 (after clipping)."""
    recs = ([{"predicted_prob": 0.999999, "hit": True}] * 100
              + [{"predicted_prob": 0.000001, "hit": False}] * 100)
    rep = compute_calibration_diagnostics(recs)
    assert rep["model_vs_market"]["brier_model"] < 1e-6


def test_logloss_finite_for_clipped_probabilities():
    """Even with predictions at exact 0 or 1 the clip guarantees a
    finite log-loss instead of ``inf``."""
    recs = [{"predicted_prob": 1.0, "hit": False}] * 10
    rep = compute_calibration_diagnostics(recs)
    ll = rep["model_vs_market"]["logloss_model"]
    assert ll is not None and math.isfinite(ll)


# ════════════════════════════════════════════════════════════════════════
# 2) AUC sanity
# ════════════════════════════════════════════════════════════════════════
def test_auc_perfect_separator():
    recs = ([{"predicted_prob": 0.9, "hit": True}] * 50
              + [{"predicted_prob": 0.1, "hit": False}] * 50)
    rep = compute_calibration_diagnostics(recs)
    assert rep["discrimination"]["auc_model"] == 1.0


def test_auc_uninformative_around_0_5():
    rep = compute_calibration_diagnostics(_build_uninformative_records())
    assert 0.45 <= rep["discrimination"]["auc_model"] <= 0.55


# ════════════════════════════════════════════════════════════════════════
# 3) Calibration regression
# ════════════════════════════════════════════════════════════════════════
def test_calibration_perfect_data_slope_near_1():
    rep = compute_calibration_diagnostics(_build_perfect_records())
    slope = rep["calibration"]["slope"]
    intercept = rep["calibration"]["intercept"]
    assert slope is not None
    assert 0.95 <= slope <= 1.05
    assert -0.05 <= intercept <= 0.05


def test_calibration_overconfident_slope_near_zero():
    rep = compute_calibration_diagnostics(_build_overconfident_records())
    # All p_pred = 0.9 → no variation in x → slope undefined
    # (we documented that the module returns None in that case).
    assert rep["calibration"]["slope"] is None


def test_calibration_miscalibrated_but_discriminative_slope_lt_1():
    rep = compute_calibration_diagnostics(
        _build_discriminative_but_miscalibrated(),
    )
    slope = rep["calibration"]["slope"]
    # high preds increase hit rate, but slope = (0.55-0.4)/(0.7-0.3) = 0.375
    assert slope is not None and 0.3 <= slope <= 0.5


# ════════════════════════════════════════════════════════════════════════
# 4) Reliability buckets
# ════════════════════════════════════════════════════════════════════════
def test_reliability_buckets_count_and_population():
    rep = compute_calibration_diagnostics(_build_perfect_records())
    buckets = rep["reliability_curve"]
    assert len(buckets) == 10
    # Each bucket should have a population (100 records each).
    for b in buckets:
        assert b["n"] == 100
        # mean_p_pred ≈ centre of bucket.
        centre = (b["lo"] + b["hi"]) / 2.0
        assert abs(b["mean_p_pred"] - centre) < 0.02
        # hit_rate ≈ mean_p_pred (perfectly calibrated).
        assert abs(b["hit_rate"] - b["mean_p_pred"]) < 0.02


# ════════════════════════════════════════════════════════════════════════
# 5) CLV
# ════════════════════════════════════════════════════════════════════════
def test_clv_zero_when_open_equals_close():
    rep = compute_calibration_diagnostics(_build_perfect_records())
    clv = rep["clv"]["all_predictions"]
    assert clv["n_with_close"] > 0
    assert abs(clv["clv_pp_mean"]) < 1e-6


def test_clv_positive_when_closing_implies_higher_prob():
    """Closing odd < opening odd ⇒ market moves AGAINST our side ⇒
    clv_pp > 0 (line moved towards more probable; we got the worse number)."""
    recs = [{
        "predicted_prob": 0.6, "hit": True,
        "odd_open": 2.0, "odd_close": 1.8,
        "market_implied_devig":       0.50,
        "market_implied_devig_close": 0.555,
        "market_implied_raw":         0.50,
        "market_implied_raw_close":   0.555,
        "fired": True,
    }] * 50
    rep = compute_calibration_diagnostics(recs)
    clv = rep["clv"]["all_predictions"]
    assert clv["clv_pp_mean"] > 0


# ════════════════════════════════════════════════════════════════════════
# 6) classify_model_quality rubric
# ════════════════════════════════════════════════════════════════════════
def test_rubric_well_calibrated_and_beats_market():
    """Force a scenario where modelo es mejor que mercado y bien
    calibrado: model_devig == realized prob; market_devig is a worse
    constant (0.5) vs realized 0.7."""
    recs = ([{"predicted_prob": 0.7, "hit": True,
                "market_implied_devig": 0.5, "market_implied_raw": 0.5,
                "odd_open": 2.0, "odd_close": 2.0, "fired": False}] * 70
              + [{"predicted_prob": 0.7, "hit": False,
                  "market_implied_devig": 0.5, "market_implied_raw": 0.5,
                  "odd_open": 2.0, "odd_close": 2.0, "fired": False}] * 30
              + [{"predicted_prob": 0.3, "hit": True,
                  "market_implied_devig": 0.5, "market_implied_raw": 0.5,
                  "odd_open": 2.0, "odd_close": 2.0, "fired": False}] * 30
              + [{"predicted_prob": 0.3, "hit": False,
                  "market_implied_devig": 0.5, "market_implied_raw": 0.5,
                  "odd_open": 2.0, "odd_close": 2.0, "fired": False}] * 70)
    rep = compute_calibration_diagnostics(recs)
    verdict = classify_model_quality(rep)
    assert "WELL_CALIBRATED_AND_BEATS_MARKET" in verdict["tags"]


def test_rubric_low_discrimination_auc_near_0_50():
    rep = compute_calibration_diagnostics(_build_uninformative_records())
    verdict = classify_model_quality(rep)
    assert "LOW_DISCRIMINATION_AUC_NEAR_0_50" in verdict["tags"]


def test_rubric_miscalibrated_but_discriminative_flagged():
    """Need AUC>0.55 AND bad slope. Build a strongly discriminative but
    miscalibrated set: low-pred ⇒ low hit, high-pred ⇒ high hit, but
    overall slope deviates."""
    recs = ([{"predicted_prob": 0.2, "hit": False,
                "market_implied_devig": 0.5, "market_implied_raw": 0.5,
                "odd_open": 2.0, "odd_close": 2.0, "fired": False}] * 90
              + [{"predicted_prob": 0.2, "hit": True,
                  "market_implied_devig": 0.5, "market_implied_raw": 0.5,
                  "odd_open": 2.0, "odd_close": 2.0, "fired": False}] * 10
              + [{"predicted_prob": 0.8, "hit": True,
                  "market_implied_devig": 0.5, "market_implied_raw": 0.5,
                  "odd_open": 2.0, "odd_close": 2.0, "fired": False}] * 30
              + [{"predicted_prob": 0.8, "hit": False,
                  "market_implied_devig": 0.5, "market_implied_raw": 0.5,
                  "odd_open": 2.0, "odd_close": 2.0, "fired": False}] * 70)
    rep = compute_calibration_diagnostics(recs)
    auc = rep["discrimination"]["auc_model"]
    slope = rep["calibration"]["slope"]
    assert auc > 0.55
    # slope = (0.30-0.10)/(0.80-0.20) = 0.333 → far from 1.0
    assert slope < 0.5
    # Rubric must flag MIS_CALIBRATED_BUT_DISCRIMINATIVE.
    verdict = classify_model_quality(rep)
    assert "MIS_CALIBRATED_BUT_DISCRIMINATIVE" in verdict["tags"]


# ════════════════════════════════════════════════════════════════════════
# 7) Endpoint smoke tests
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from server import app
    return TestClient(app)


def test_endpoint_index_returns_pre_generated_reports(client):
    """Smoke test: el endpoint debe devolver la lista de reportes
    generada por `run_d7_calibration_diagnostics`."""
    idx_path = Path("/app/diagnostics/_index.json")
    if not idx_path.exists():
        pytest.skip("Diagnostics not pre-generated.")
    r = client.get("/api/football/diagnostics/calibration/index")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert "OVER_2_5" in data["markets"]
    assert "UNDER_2_5" in data["markets"]
    assert "DRAW" in data["markets"]


def test_endpoint_returns_concrete_report(client):
    if not Path("/app/diagnostics/_index.json").exists():
        pytest.skip("Diagnostics not pre-generated.")
    r = client.get(
        "/api/football/diagnostics/calibration",
        params={"market": "OVER_2_5", "scope": "premier_2425"},
    )
    assert r.status_code == 200
    rep = r.json()
    assert rep["market"] == "OVER_2_5"
    assert "reliability_curve" in rep
    assert "calibration" in rep
    assert "model_vs_market" in rep
    assert "discrimination" in rep
    assert "clv" in rep
    assert "verdict" in rep


def test_endpoint_returns_404_for_unknown_pair(client):
    r = client.get(
        "/api/football/diagnostics/calibration",
        params={"market": "OVER_2_5", "scope": "__does_not_exist__"},
    )
    assert r.status_code == 404
