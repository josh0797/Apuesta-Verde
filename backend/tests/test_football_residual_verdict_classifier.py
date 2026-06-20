"""
Sprint D9.2 — Block C · Residual Verdict Classifier tests (Bonferroni).

Cubre:
  * compute_bonferroni_cutoff (alpha / m).
  * classify_residual_verdict en sus 4 ramas + tag de Bonferroni-not-sig.
  * Detección de overfit.
  * Fail-soft para diagnósticos incompletos.
  * Sanidad numérica (orden de cutoffs, monotonía con m).
"""

from __future__ import annotations

import pytest

from services.football_residual_verdict_classifier import (
    DEFAULT_ALPHA,
    TAG_BONFERRONI_NOT_SIG,
    TAG_CALIBRATION_ONLY,
    TAG_INSUFFICIENT_DIAG,
    TAG_NO_INCREMENTAL_SIGNAL,
    TAG_RESIDUAL_BEATS_MARKET,
    TAG_RESIDUAL_OVERFIT,
    classify_residual_verdict,
    compute_bonferroni_cutoff,
)


# ─────────────────────────────────────────────────────────────────────
# compute_bonferroni_cutoff
# ─────────────────────────────────────────────────────────────────────
class TestBonferroniCutoff:
    def test_m1_naive(self):
        out = compute_bonferroni_cutoff(alpha=0.05, m_tests=1)
        assert out["alpha"] == 0.05
        assert out["m_tests"] == 1
        assert out["alpha_adjusted"] == pytest.approx(0.05)
        assert out["cutoff"] == pytest.approx(0.95)

    def test_m2_halves_alpha(self):
        out = compute_bonferroni_cutoff(alpha=0.05, m_tests=2)
        assert out["alpha_adjusted"] == pytest.approx(0.025)
        assert out["cutoff"] == pytest.approx(0.975)

    def test_m_default_sweep_4(self):
        # 1 market × 2 scopes × 2 metrics = 4 tests
        out = compute_bonferroni_cutoff(alpha=0.05, m_tests=4)
        assert out["alpha_adjusted"] == pytest.approx(0.0125)
        assert out["cutoff"] == pytest.approx(0.9875)

    def test_m_large_sweep_20(self):
        # 5 markets × 2 scopes × 2 metrics = 20 tests
        out = compute_bonferroni_cutoff(alpha=0.05, m_tests=20)
        assert out["alpha_adjusted"] == pytest.approx(0.0025)
        assert out["cutoff"] == pytest.approx(0.9975)

    def test_m_zero_clamped_to_one(self):
        out = compute_bonferroni_cutoff(alpha=0.05, m_tests=0)
        assert out["m_tests"] == 1
        assert out["cutoff"] == pytest.approx(0.95)

    def test_m_monotonic_in_cutoff(self):
        # Larger m → stricter cutoff.
        c1 = compute_bonferroni_cutoff(alpha=0.05, m_tests=1)["cutoff"]
        c10 = compute_bonferroni_cutoff(alpha=0.05, m_tests=10)["cutoff"]
        c100 = compute_bonferroni_cutoff(alpha=0.05, m_tests=100)["cutoff"]
        assert c1 < c10 < c100

    def test_custom_alpha(self):
        out = compute_bonferroni_cutoff(alpha=0.01, m_tests=4)
        assert out["alpha_adjusted"] == pytest.approx(0.0025)
        assert out["cutoff"] == pytest.approx(0.9975)


# ─────────────────────────────────────────────────────────────────────
# classify_residual_verdict — helpers
# ─────────────────────────────────────────────────────────────────────
def _diag(brier_model, brier_market, ll_model, ll_market, slope=1.0, intercept=0.0):
    return {
        "model_vs_market": {
            "brier_model":          brier_model,
            "brier_market_devig":   brier_market,
            "logloss_model":        ll_model,
            "logloss_market_devig": ll_market,
        },
        "calibration": {"slope": slope, "intercept": intercept},
    }


def _boot(p_below_zero):
    return {"p_below_zero": p_below_zero, "n": 300, "n_boot": 1000,
            "ci_low": -0.01, "ci_high": 0.0, "median": -0.005}


def _train(delta_train_brier=0.0):
    return {"pooled": {"delta_train_brier": delta_train_brier}}


# ─────────────────────────────────────────────────────────────────────
# classify_residual_verdict — verdict branches
# ─────────────────────────────────────────────────────────────────────
class TestClassifyVerdict:
    def test_naive_significant_but_not_bonferroni(self):
        # p=0.96: passes naïve (≥0.95) but with m_tests=4 cutoff is
        # 0.9875 → does NOT pass Bonferroni.
        out = classify_residual_verdict(
            _diag(0.20, 0.21, 0.50, 0.51),   # holdout dominates
            _diag(0.21, 0.21, 0.51, 0.51),
            _diag(0.22, 0.21, 0.52, 0.51),
            _boot(0.96), _boot(0.96),
            _train(),
            alpha=0.05, m_tests=4,
        )
        # Beats market is NOT awarded.
        assert TAG_RESIDUAL_BEATS_MARKET not in out["tags"]
        # But the Bonferroni-not-sig flag IS raised.
        assert TAG_BONFERRONI_NOT_SIG in out["tags"]

    def test_passes_bonferroni_residual_beats_market(self):
        # p=0.995: passes both naïve and Bonferroni (m=4 → cutoff=0.9875).
        out = classify_residual_verdict(
            _diag(0.20, 0.22, 0.50, 0.52),
            _diag(0.22, 0.22, 0.52, 0.52),
            _diag(0.23, 0.22, 0.53, 0.52),
            _boot(0.995), _boot(0.995),
            _train(),
            alpha=0.05, m_tests=4,
        )
        assert TAG_RESIDUAL_BEATS_MARKET in out["tags"]
        assert TAG_BONFERRONI_NOT_SIG not in out["tags"]
        b = out["bonferroni"]
        assert b["brier_passes_bonferroni"] is True
        assert b["logloss_passes_bonferroni"] is True

    def test_m1_passes_naive_equivalent_to_bonferroni(self):
        # With m=1 Bonferroni == naïve.
        out = classify_residual_verdict(
            _diag(0.20, 0.22, 0.50, 0.52),
            _diag(0.22, 0.22, 0.52, 0.52),
            _diag(0.23, 0.22, 0.53, 0.52),
            _boot(0.96), _boot(0.96),
            _train(),
            alpha=0.05, m_tests=1,
        )
        assert TAG_RESIDUAL_BEATS_MARKET in out["tags"]
        assert TAG_BONFERRONI_NOT_SIG not in out["tags"]

    def test_calibration_only_branch(self):
        # Residual is well-calibrated but does NOT dominate the market.
        out = classify_residual_verdict(
            _diag(0.22, 0.21, 0.52, 0.51, slope=1.02, intercept=0.0),
            _diag(0.21, 0.21, 0.51, 0.51),
            _diag(0.23, 0.21, 0.53, 0.51),
            _boot(0.40), _boot(0.42),
            _train(),
            alpha=0.05, m_tests=4,
        )
        assert TAG_CALIBRATION_ONLY in out["tags"]
        assert TAG_RESIDUAL_BEATS_MARKET not in out["tags"]

    def test_no_incremental_signal_branch(self):
        # Slope way off + does not dominate.
        out = classify_residual_verdict(
            _diag(0.22, 0.20, 0.52, 0.50, slope=1.5, intercept=0.1),
            _diag(0.20, 0.20, 0.50, 0.50),
            _diag(0.21, 0.20, 0.51, 0.50),
            _boot(0.10), _boot(0.05),
            _train(),
            alpha=0.05, m_tests=4,
        )
        assert TAG_NO_INCREMENTAL_SIGNAL in out["tags"]

    def test_overfit_detection(self):
        # Residual MUCH better than market on train; worse on holdout.
        out = classify_residual_verdict(
            _diag(0.22, 0.20, 0.52, 0.50),    # holdout worse
            _diag(0.20, 0.20, 0.50, 0.50),
            _diag(0.23, 0.20, 0.53, 0.50),
            _boot(0.10), _boot(0.05),
            _train(delta_train_brier=-0.05),  # train much better
            alpha=0.05, m_tests=4,
        )
        assert TAG_RESIDUAL_OVERFIT in out["tags"]

    def test_brier_passes_but_logloss_does_not(self):
        # Only one metric passes Bonferroni → NOT residual beats market.
        out = classify_residual_verdict(
            _diag(0.20, 0.22, 0.50, 0.52),
            _diag(0.22, 0.22, 0.52, 0.52),
            _diag(0.23, 0.22, 0.53, 0.52),
            _boot(0.99), _boot(0.50),   # logloss not significant
            _train(),
            alpha=0.05, m_tests=4,
        )
        assert TAG_RESIDUAL_BEATS_MARKET not in out["tags"]

    def test_holdout_does_not_dominate_returns_other_tag(self):
        # Even with very significant Brier bootstrap, if holdout
        # doesn't dominate → no "beats market" tag.
        out = classify_residual_verdict(
            _diag(0.22, 0.20, 0.52, 0.50),  # residual WORSE on holdout
            _diag(0.20, 0.20, 0.50, 0.50),
            _diag(0.23, 0.20, 0.53, 0.50),
            _boot(0.999), _boot(0.999),
            _train(),
            alpha=0.05, m_tests=4,
        )
        assert TAG_RESIDUAL_BEATS_MARKET not in out["tags"]


# ─────────────────────────────────────────────────────────────────────
# classify_residual_verdict — fail-soft
# ─────────────────────────────────────────────────────────────────────
class TestFailSoft:
    def test_missing_diag_returns_insufficient_tag(self):
        out = classify_residual_verdict(
            _diag(None, None, None, None),
            _diag(0.20, 0.20, 0.50, 0.50),
            _diag(0.20, 0.20, 0.50, 0.50),
            _boot(0.99), _boot(0.99),
            _train(),
            alpha=0.05, m_tests=4,
        )
        assert TAG_INSUFFICIENT_DIAG in out["tags"]
        # Bonferroni block still emitted.
        assert "bonferroni" in out
        assert out["bonferroni"]["cutoff"] == pytest.approx(0.9875)

    def test_no_p_below_zero_does_not_pass(self):
        out = classify_residual_verdict(
            _diag(0.20, 0.22, 0.50, 0.52),
            _diag(0.22, 0.22, 0.52, 0.52),
            _diag(0.23, 0.22, 0.53, 0.52),
            {"p_below_zero": None}, {"p_below_zero": None},
            _train(),
            alpha=0.05, m_tests=4,
        )
        assert TAG_RESIDUAL_BEATS_MARKET not in out["tags"]
        assert out["bonferroni"]["brier_passes_bonferroni"] is False
        assert out["bonferroni"]["logloss_passes_bonferroni"] is False

    def test_empty_boot_dict_does_not_pass(self):
        out = classify_residual_verdict(
            _diag(0.20, 0.22, 0.50, 0.52),
            _diag(0.22, 0.22, 0.52, 0.52),
            _diag(0.23, 0.22, 0.53, 0.52),
            {}, {},
            _train(),
            alpha=0.05, m_tests=4,
        )
        assert TAG_RESIDUAL_BEATS_MARKET not in out["tags"]


# ─────────────────────────────────────────────────────────────────────
# Bonferroni block in output
# ─────────────────────────────────────────────────────────────────────
class TestBonferroniBlock:
    def test_block_contains_all_expected_keys(self):
        out = classify_residual_verdict(
            _diag(0.20, 0.22, 0.50, 0.52),
            _diag(0.22, 0.22, 0.52, 0.52),
            _diag(0.23, 0.22, 0.53, 0.52),
            _boot(0.99), _boot(0.99),
            _train(),
            alpha=0.05, m_tests=4,
        )
        b = out["bonferroni"]
        for key in (
            "alpha", "m_tests", "alpha_adjusted", "cutoff",
            "naive_cutoff", "brier_p", "logloss_p",
            "brier_passes_bonferroni", "logloss_passes_bonferroni",
            "brier_passes_naive", "logloss_passes_naive",
        ):
            assert key in b, f"missing key {key} in bonferroni block"

    def test_block_reflects_alpha_m(self):
        out = classify_residual_verdict(
            _diag(0.20, 0.22, 0.50, 0.52),
            _diag(0.22, 0.22, 0.52, 0.52),
            _diag(0.23, 0.22, 0.53, 0.52),
            _boot(0.99), _boot(0.99),
            _train(),
            alpha=0.01, m_tests=10,
        )
        b = out["bonferroni"]
        assert b["alpha"] == 0.01
        assert b["m_tests"] == 10
        assert b["alpha_adjusted"] == pytest.approx(0.001)
        assert b["cutoff"] == pytest.approx(0.999)

    def test_default_alpha_constant(self):
        assert DEFAULT_ALPHA == 0.05
