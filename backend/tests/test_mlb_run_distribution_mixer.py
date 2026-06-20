"""
NIVEL 3 — Bloque 1 · Dynamic Run Distribution Mixer tests.

Cubre:
  * Fail-soft / contratos defensivos.
  * Computación de lambda (baseline + park + weather).
  * Volatility scoring + selección de familia (POISSON / NB / MIXTURE).
  * Detección de datos parciales → MIXTURE forzado.
  * Probabilidades por línea (monotónicas, suma over+under ≈ 1).
  * Percentiles (orden p10 ≤ p25 ≤ … ≤ p99).
  * Tail risk (score, buckets, drivers).
  * Mezcla convexa (PMF híbrida válida).
  * Dispersión clampada [1.0, 3.0].
"""

from __future__ import annotations

import pytest

from services.mlb_run_distribution_mixer import (
    FAMILY_MIXTURE,
    FAMILY_NB,
    FAMILY_POISSON,
    MAX_DISPERSION,
    MAX_RUNS,
    MIN_DISPERSION,
    PERCENTILE_LEVELS,
    SUPPORTED_LINES,
    TAIL_EXTREME,
    TAIL_HIGH,
    TAIL_LOW,
    TAIL_MEDIUM,
    VOLATILITY_NB_MIN,
    VOLATILITY_POISSON_MAX,
    build_dynamic_run_distribution,
)


def _ctx_full(volatility_high=False, lam=8.5):
    """Build a full context with all sub-blocks present."""
    if volatility_high:
        return {
            "baseline_expected_runs": lam,
            "starter_volatility": {
                "home": {"starter_volatility_score": 88, "bucket": "EXTREME"},
                "away": {"starter_volatility_score": 75, "bucket": "HIGH"},
            },
            "first_inning_collapse": {
                "home": {"first_inning_collapse_score": 90},
                "away": {"first_inning_collapse_score": 80},
            },
            "lineup_explosiveness": {
                "home": {"lineup_explosiveness_score": 85, "bucket": "EXPLOSIVE"},
                "away": {"lineup_explosiveness_score": 80, "bucket": "EXPLOSIVE"},
            },
            "bullpen_stress": {
                "home": {"bullpen_stress_score": 75},
                "away": {"bullpen_stress_score": 60},
            },
            "domino_risk": {
                "home": {"domino_risk_score": 80},
                "away": {"domino_risk_score": 70},
            },
            "recent_offense_home": {"bucket": "EXPLOSIVE"},
            "recent_offense_away": {"bucket": "HOT"},
        }
    return {
        "baseline_expected_runs": lam,
        "starter_volatility": {
            "home": {"starter_volatility_score": 20, "bucket": "LOW"},
            "away": {"starter_volatility_score": 25, "bucket": "LOW"},
        },
        "first_inning_collapse": {
            "home": {"first_inning_collapse_score": 20},
            "away": {"first_inning_collapse_score": 15},
        },
        "lineup_explosiveness": {
            "home": {"lineup_explosiveness_score": 35, "bucket": "AVG"},
            "away": {"lineup_explosiveness_score": 30, "bucket": "AVG"},
        },
        "bullpen_stress": {
            "home": {"bullpen_stress_score": 20},
            "away": {"bullpen_stress_score": 15},
        },
        "domino_risk": {
            "home": {"domino_risk_score": 20},
            "away": {"domino_risk_score": 25},
        },
        "recent_offense_home": {"bucket": "COLD"},
        "recent_offense_away": {"bucket": "NEUTRAL"},
    }


# ─────────────────────────────────────────────────────────────────────
# Fail-soft contract
# ─────────────────────────────────────────────────────────────────────
class TestFailSoft:
    def test_none_context_returns_neutral(self):
        out = build_dynamic_run_distribution(None)
        assert out["available"] is False
        assert "NEUTRAL_FALLBACK" in out["reason_codes"]
        assert out["distribution_family"] == FAMILY_POISSON

    def test_non_dict_context(self):
        out = build_dynamic_run_distribution("garbage")
        assert out["available"] is False

    def test_empty_context_returns_industry_avg(self):
        out = build_dynamic_run_distribution({})
        # Lambda falls back to 8.5.
        assert out["lambda"] == 8.5
        assert "LAMBDA_FALLBACK_INDUSTRY_AVG" in out["reason_codes"]

    def test_invalid_baseline_zero(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 0})
        # Falls back to 8.5.
        assert out["lambda"] == 8.5


# ─────────────────────────────────────────────────────────────────────
# Lambda computation
# ─────────────────────────────────────────────────────────────────────
class TestLambda:
    def test_baseline_runs_used(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 9.0})
        assert out["lambda"] == 9.0

    def test_park_factor_dynamic_applied(self):
        out = build_dynamic_run_distribution({
            "baseline_expected_runs": 8.0,
            "park_factor": {"dynamic": 1.10},
        })
        assert out["lambda"] == pytest.approx(8.8, abs=0.01)
        assert "PARK_FACTOR_APPLIED" in out["reason_codes"]

    def test_park_factor_runs_mult_applied(self):
        out = build_dynamic_run_distribution({
            "baseline_expected_runs": 8.0,
            "park_factor": {"park_runs_mult": 0.92},
        })
        assert out["lambda"] == pytest.approx(7.36, abs=0.01)

    def test_park_factor_neutral_skipped(self):
        out = build_dynamic_run_distribution({
            "baseline_expected_runs": 8.0,
            "park_factor": {"dynamic": 1.02},  # within ±0.04
        })
        assert "PARK_FACTOR_APPLIED" not in out["reason_codes"]

    def test_weather_multiplier(self):
        out = build_dynamic_run_distribution({
            "baseline_expected_runs": 8.0,
            "weather": {"runs_multiplier": 1.10},
        })
        assert out["lambda"] == pytest.approx(8.8, abs=0.01)
        assert "WEATHER_APPLIED" in out["reason_codes"]

    def test_lambda_clamped(self):
        # Absurd baseline; lambda clamped to 22.
        out = build_dynamic_run_distribution({"baseline_expected_runs": 50.0})
        assert out["lambda"] == 22.0

    def test_uses_baseline_distribution_mean(self):
        out = build_dynamic_run_distribution({
            "baseline_distribution": {"mean": 7.5},
        })
        assert out["lambda"] == 7.5


# ─────────────────────────────────────────────────────────────────────
# Family selection
# ─────────────────────────────────────────────────────────────────────
class TestFamilySelection:
    def test_low_volatility_selects_poisson(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=False))
        assert out["distribution_family"] == FAMILY_POISSON
        assert out["mixture_weights"]["poisson"] == 1.0
        assert out["mixture_weights"]["negative_binomial"] == 0.0

    def test_high_volatility_selects_nb(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=True))
        assert out["distribution_family"] == FAMILY_NB
        assert out["mixture_weights"]["negative_binomial"] == 1.0

    def test_mid_volatility_selects_mixture(self):
        # Hand-craft a mid-range score.
        ctx = {
            "baseline_expected_runs": 8.5,
            "starter_volatility": {
                "home": {"starter_volatility_score": 55, "bucket": "MEDIUM"},
                "away": {"starter_volatility_score": 50, "bucket": "MEDIUM"},
            },
            "first_inning_collapse": {
                "home": {"first_inning_collapse_score": 50},
                "away": {"first_inning_collapse_score": 50},
            },
            "lineup_explosiveness": {
                "home": {"lineup_explosiveness_score": 50, "bucket": "AVG"},
                "away": {"lineup_explosiveness_score": 50, "bucket": "AVG"},
            },
            "bullpen_stress": {
                "home": {"bullpen_stress_score": 50},
                "away": {"bullpen_stress_score": 50},
            },
            "domino_risk": {
                "home": {"domino_risk_score": 50},
                "away": {"domino_risk_score": 50},
            },
            "recent_offense_home": {"bucket": "NEUTRAL"},
            "recent_offense_away": {"bucket": "NEUTRAL"},
        }
        out = build_dynamic_run_distribution(ctx)
        assert out["distribution_family"] == FAMILY_MIXTURE
        assert 0 < out["mixture_weights"]["negative_binomial"] < 1

    def test_partial_data_forces_mixture(self):
        # No starter_volatility → critical missing → MIXTURE.
        ctx = {
            "baseline_expected_runs": 8.0,
            "lineup_explosiveness": {
                "home": {"lineup_explosiveness_score": 50},
                "away": {"lineup_explosiveness_score": 50},
            },
            "bullpen_stress": {
                "home": {"bullpen_stress_score": 50},
                "away": {"bullpen_stress_score": 50},
            },
        }
        out = build_dynamic_run_distribution(ctx)
        assert out["distribution_family"] == FAMILY_MIXTURE
        assert "MIXTURE_DUE_TO_PARTIAL_DATA" in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Probabilities by line
# ─────────────────────────────────────────────────────────────────────
class TestProbabilities:
    def test_all_supported_lines_present(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 8.5})
        probs = out["probabilities"]
        for ln in SUPPORTED_LINES:
            key_o = f"over_{str(ln).replace('.', '_')}"
            key_u = f"under_{str(ln).replace('.', '_')}"
            assert key_o in probs
            assert key_u in probs

    def test_over_plus_under_sums_to_one(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 8.5})
        probs = out["probabilities"]
        for ln in SUPPORTED_LINES:
            key_o = f"over_{str(ln).replace('.', '_')}"
            key_u = f"under_{str(ln).replace('.', '_')}"
            assert probs[key_o] + probs[key_u] == pytest.approx(1.0, abs=0.005)

    def test_over_probability_monotone_decreasing(self):
        # Higher line → lower P(over).
        out = build_dynamic_run_distribution({"baseline_expected_runs": 8.5})
        probs = out["probabilities"]
        prev = 1.01  # over_6_5 should be ≤ 1
        for ln in SUPPORTED_LINES:
            key_o = f"over_{str(ln).replace('.', '_')}"
            curr = probs[key_o]
            assert curr <= prev + 1e-6
            prev = curr

    def test_under_probability_monotone_increasing(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 8.5})
        probs = out["probabilities"]
        prev = -0.01
        for ln in SUPPORTED_LINES:
            key_u = f"under_{str(ln).replace('.', '_')}"
            curr = probs[key_u]
            assert curr >= prev - 1e-6
            prev = curr

    def test_high_volatility_widens_tails(self):
        # NB with high volatility should put more mass on extremes
        # than pure Poisson with same mean.
        out_low  = build_dynamic_run_distribution(_ctx_full(volatility_high=False, lam=8.5))
        out_high = build_dynamic_run_distribution(_ctx_full(volatility_high=True, lam=8.5))
        # P(over 12.5) should be higher in the volatile regime.
        assert out_high["probabilities"]["over_12_5"] >= out_low["probabilities"]["over_12_5"]
        # And P(under 5.5) doesn't apply (not supported), but tail risk score should be higher.
        assert out_high["tail_risk"]["score"] >= out_low["tail_risk"]["score"]


# ─────────────────────────────────────────────────────────────────────
# Percentiles
# ─────────────────────────────────────────────────────────────────────
class TestPercentiles:
    def test_all_percentiles_present(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 8.5})
        p = out["percentiles"]
        for q in PERCENTILE_LEVELS:
            assert f"p{int(q * 100)}" in p

    def test_percentile_order(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 8.5})
        p = out["percentiles"]
        keys = sorted(p.keys(), key=lambda k: int(k[1:]))
        prev = -1
        for k in keys:
            assert p[k] >= prev
            prev = p[k]

    def test_p50_near_lambda(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 8.0})
        # For Poisson with lambda=8, p50 should be 8 (or close).
        assert abs(out["percentiles"]["p50"] - 8) <= 1


# ─────────────────────────────────────────────────────────────────────
# Tail risk
# ─────────────────────────────────────────────────────────────────────
class TestTailRisk:
    def test_low_vol_has_low_tail_risk(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=False, lam=8.0))
        tr = out["tail_risk"]
        assert tr["bucket"] in (TAIL_LOW, TAIL_MEDIUM)
        assert 0 <= tr["score"] <= 100

    def test_high_vol_has_higher_tail_risk(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=True, lam=9.5))
        tr = out["tail_risk"]
        assert tr["bucket"] in (TAIL_HIGH, TAIL_EXTREME, TAIL_MEDIUM)
        assert tr["score"] > 0

    def test_tail_drivers_promote_explosive(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=True, lam=9.5))
        if out["tail_risk"]["bucket"] in (TAIL_HIGH, TAIL_EXTREME):
            assert "EXPLOSIVE_TAIL_RISK" in out["tail_risk"]["drivers"]


# ─────────────────────────────────────────────────────────────────────
# Dispersion clamping
# ─────────────────────────────────────────────────────────────────────
class TestDispersion:
    def test_dispersion_clamped_within_bounds(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=True))
        assert MIN_DISPERSION <= out["dispersion"] <= MAX_DISPERSION

    def test_poisson_regime_dispersion_is_close_to_1(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=False))
        assert out["dispersion"] <= 1.4

    def test_nb_regime_dispersion_above_baseline(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=True))
        assert out["dispersion"] > 1.0


# ─────────────────────────────────────────────────────────────────────
# Mixture PMF validity
# ─────────────────────────────────────────────────────────────────────
class TestMixturePMF:
    def test_mixture_weights_sum_to_one(self):
        out = build_dynamic_run_distribution(_ctx_full(volatility_high=False))
        w = out["mixture_weights"]
        assert w["poisson"] + w["negative_binomial"] == pytest.approx(1.0, abs=0.01)

    def test_probabilities_valid_for_all_families(self):
        for v in (False, True):
            out = build_dynamic_run_distribution(_ctx_full(volatility_high=v))
            probs = out["probabilities"]
            # All values in [0, 1].
            for k, v_p in probs.items():
                assert 0 <= v_p <= 1, f"bad prob {k}={v_p}"


# ─────────────────────────────────────────────────────────────────────
# Debug
# ─────────────────────────────────────────────────────────────────────
class TestDebug:
    def test_debug_flag_off_returns_empty_debug(self):
        out = build_dynamic_run_distribution({"baseline_expected_runs": 8.5})
        assert out["debug"] == {}

    def test_debug_flag_on_returns_debug_block(self):
        out = build_dynamic_run_distribution({
            "baseline_expected_runs": 8.5, "debug": True,
        })
        assert "pmf_head" in out["debug"]
        assert "vol_score" in out["debug"]
        assert "dispersion" in out["debug"]
