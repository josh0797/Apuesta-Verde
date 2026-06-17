"""Sprint-D3 · Tests for football_over15_potential.py (Dixon-Coles bivariate).

Covers:
* Probability bounds [PROB_MIN, PROB_MAX]
* Monotonicity in λ (higher λ → higher P(O1.5))
* Identity: P(O1.5) = 1 − P(0,0) − P(0,1) − P(1,0)
* tau application at low-score cells
* Fail-soft: missing inputs route to fallbacks
* Symmetry-like sanity checks
"""
from __future__ import annotations

import math

import pytest

from services.football_over15_potential import (
    compute_over15_potential,
    _poisson_pmf, _tau_dixon_coles,
    RHO_DEFAULT, HOME_ADV_LAMBDA, MIN_LAMBDA, MAX_LAMBDA,
    LEAGUE_AVG_LAMBDA, PROB_MIN, PROB_MAX,
    RC_DIXON_COLES_OK, RC_USED_XG, RC_USED_GOAL_AVG_FALLBACK,
    RC_USED_LEAGUE_FALLBACK, RC_HOME_ADVANTAGE_APPLIED,
    RC_LAMBDA_FLOORED, RC_LAMBDA_CEILED, RC_INSUFFICIENT_INPUTS,
)


# ════════════════════════════════════════════════════════════════════════
# _poisson_pmf
# ════════════════════════════════════════════════════════════════════════
class TestPoissonPMF:
    @pytest.mark.parametrize("lam,k,expected", [
        (1.0, 0, math.exp(-1.0)),
        (1.0, 1, math.exp(-1.0)),
        (2.0, 2, math.exp(-2.0) * 4 / 2),
        (0.0, 0, 1.0),
        (0.0, 1, 0.0),
    ])
    def test_poisson_known_values(self, lam, k, expected):
        assert _poisson_pmf(k, lam) == pytest.approx(expected, rel=1e-6)

    def test_sums_to_one_over_first_20(self):
        # PMF should integrate to ≈1 over the first 20 values.
        total = sum(_poisson_pmf(k, 1.5) for k in range(20))
        assert total == pytest.approx(1.0, abs=1e-5)


# ════════════════════════════════════════════════════════════════════════
# _tau_dixon_coles
# ════════════════════════════════════════════════════════════════════════
class TestTau:
    def test_tau_identity_for_high_scores(self):
        for x in (2, 3, 5):
            for y in (2, 3, 5):
                assert _tau_dixon_coles(x, y, 1.5, 1.2, -0.13) == 1.0

    def test_tau_negative_rho_increases_00(self):
        """With ρ < 0, τ(0,0) > 1 (boosts low-score probability)."""
        t = _tau_dixon_coles(0, 0, 1.5, 1.2, -0.13)
        assert t > 1.0

    def test_tau_negative_rho_increases_11(self):
        """With ρ < 0, τ(1,1) = 1 - ρ > 1."""
        t = _tau_dixon_coles(1, 1, 1.5, 1.2, -0.13)
        assert t > 1.0

    def test_tau_negative_rho_decreases_01_and_10(self):
        """With ρ < 0, τ(0,1) and τ(1,0) become < 1."""
        t01 = _tau_dixon_coles(0, 1, 1.5, 1.2, -0.13)
        t10 = _tau_dixon_coles(1, 0, 1.5, 1.2, -0.13)
        assert t01 < 1.0
        assert t10 < 1.0


# ════════════════════════════════════════════════════════════════════════
# compute_over15_potential
# ════════════════════════════════════════════════════════════════════════
class TestComputeOver15:
    def test_basic_xg_inputs(self):
        r = compute_over15_potential(xg_home_l5=1.5, xg_away_l5=1.2)
        assert PROB_MIN <= r["over15_probability"] <= PROB_MAX
        assert RC_USED_XG in r["reason_codes"]
        assert RC_DIXON_COLES_OK in r["reason_codes"]

    def test_monotonicity_in_xg(self):
        r_low  = compute_over15_potential(xg_home_l5=0.6, xg_away_l5=0.5)
        r_mid  = compute_over15_potential(xg_home_l5=1.3, xg_away_l5=1.1)
        r_high = compute_over15_potential(xg_home_l5=2.5, xg_away_l5=2.0)
        assert r_low["over15_probability"] < r_mid["over15_probability"]
        assert r_mid["over15_probability"] < r_high["over15_probability"]

    def test_identity_grid_sums(self):
        r = compute_over15_potential(xg_home_l5=1.6, xg_away_l5=1.2)
        grid = r["p_score_grid"]
        p_under = grid["0-0"] + grid["0-1"] + grid["1-0"]
        # Recover P(O1.5) from the grid (within ±0.5pp due to clamping).
        recovered = (1.0 - p_under) * 100.0
        assert r["over15_probability"] == pytest.approx(recovered, abs=0.5)

    def test_home_advantage_applied(self):
        # Same xG but check λ_home includes the home advantage.
        r = compute_over15_potential(xg_home_l5=1.2, xg_away_l5=1.2)
        assert r["lambda_home"] == pytest.approx(1.2 + HOME_ADV_LAMBDA, abs=1e-6)
        assert r["lambda_away"] == pytest.approx(1.2, abs=1e-6)
        assert RC_HOME_ADVANTAGE_APPLIED in r["reason_codes"]

    def test_goal_avg_fallback(self):
        r = compute_over15_potential(goal_avg_for_home=1.5,
                                       goal_avg_for_away=1.0,
                                       goal_avg_against_home=1.2,
                                       goal_avg_against_away=1.4)
        assert RC_USED_GOAL_AVG_FALLBACK in r["reason_codes"]
        assert PROB_MIN <= r["over15_probability"] <= PROB_MAX

    def test_league_fallback_no_inputs(self):
        r = compute_over15_potential()
        assert RC_USED_LEAGUE_FALLBACK in r["reason_codes"]
        assert RC_INSUFFICIENT_INPUTS in r["reason_codes"]
        # λ ends up at LEAGUE_AVG_LAMBDA + HA (= 1.4 + 0.2 = 1.6) home,
        # LEAGUE_AVG_LAMBDA (= 1.4) away.
        assert r["lambda_home"] == pytest.approx(
            LEAGUE_AVG_LAMBDA + HOME_ADV_LAMBDA, abs=1e-6)
        assert r["lambda_away"] == pytest.approx(LEAGUE_AVG_LAMBDA, abs=1e-6)

    def test_lambda_floored(self):
        r = compute_over15_potential(xg_home_l5=0.0, xg_away_l5=0.0,
                                       home_advantage_lambda=0.0)
        # Both λ should be floored at MIN_LAMBDA.
        assert r["lambda_home"] == MIN_LAMBDA
        assert r["lambda_away"] == MIN_LAMBDA
        assert RC_LAMBDA_FLOORED in r["reason_codes"]

    def test_lambda_ceiled(self):
        r = compute_over15_potential(xg_home_l5=10.0, xg_away_l5=10.0)
        assert r["lambda_home"] == MAX_LAMBDA
        assert r["lambda_away"] == MAX_LAMBDA
        assert RC_LAMBDA_CEILED in r["reason_codes"]
        # With huge λ, P(O1.5) → ceiling.
        assert r["over15_probability"] == pytest.approx(PROB_MAX, abs=0.5)

    def test_rho_override(self):
        # ρ = 0 means independence; default ρ < 0 alters the low-score
        # cells. The NET direction on P(O1.5) depends on (λ_h, λ_a):
        # for equal λs near 1.5, the boost in P(0,0) + P(1,1) is more
        # than offset by the drop in P(0,1) + P(1,0), so default ρ < 0
        # gives a HIGHER P(O1.5). We just verify the two paths differ.
        r0 = compute_over15_potential(xg_home_l5=1.5, xg_away_l5=1.5, rho=0.0)
        rdef = compute_over15_potential(xg_home_l5=1.5, xg_away_l5=1.5)
        assert r0["over15_probability"] != rdef["over15_probability"]
        # Both must remain valid probabilities.
        assert PROB_MIN <= r0["over15_probability"] <= PROB_MAX
        assert PROB_MIN <= rdef["over15_probability"] <= PROB_MAX

    def test_nan_inputs_routed_to_fallback(self):
        r = compute_over15_potential(xg_home_l5=float("nan"),
                                       xg_away_l5=float("nan"))
        assert RC_USED_LEAGUE_FALLBACK in r["reason_codes"]

    def test_label_is_none_engine_assigns(self):
        r = compute_over15_potential(xg_home_l5=1.5, xg_away_l5=1.5)
        # Module is pure; labeling is delegated to the engine.
        assert r["label"] is None

    def test_audit_block_present(self):
        r = compute_over15_potential(xg_home_l5=1.5, xg_away_l5=1.2)
        assert "audit" in r
        assert r["audit"]["lambda_home"] == r["lambda_home"]
        assert r["audit"]["lambda_away"] == r["lambda_away"]
        assert r["audit"]["rho_used"] == r["rho_used"]
        assert "p_score_grid" in r["audit"]
