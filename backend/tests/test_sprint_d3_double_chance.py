"""Sprint-D3 · Tests for football_double_chance_potential.py (ELO 1X2).

Covers:
* Probability bounds [0, 1]
* Mathematical identities:
    - p_home + p_draw + p_away == 1 (sum-to-one)
    - p_home_or_draw  = p_home + p_draw
    - p_away_or_draw  = p_away + p_draw
    - p_home_or_away  = p_home + p_away = 1 − p_draw
* ELO monotonicity (stronger home team → higher P(home))
* Home-advantage effect
* Renormalisation when P(home) or P(away) would go negative
* Fail-soft for missing inputs
"""
from __future__ import annotations

import pytest

from services.football_double_chance_potential import (
    compute_double_chance_potential,
    _elo_expected_score,
    HOME_ADV_ELO, DEFAULT_DRAW_RATE,
    RC_ELO_OK, RC_ELO_MISSING_SYMMETRIC,
    RC_DRAW_PROB_FALLBACK, RC_DRAW_PROB_CLAMPED,
    RC_NEGATIVE_HOME_RENORMALIZED, RC_NEGATIVE_AWAY_RENORMALIZED,
    RC_HOME_ADVANTAGE_APPLIED,
)


# ════════════════════════════════════════════════════════════════════════
# _elo_expected_score
# ════════════════════════════════════════════════════════════════════════
class TestEloExpectedScore:
    def test_equal_elo_with_home_advantage_above_half(self):
        e = _elo_expected_score(1500, 1500, home_adv=65)
        assert e > 0.5

    def test_equal_elo_no_home_advantage_equals_half(self):
        e = _elo_expected_score(1500, 1500, home_adv=0)
        assert e == pytest.approx(0.5, abs=1e-6)

    def test_higher_elo_yields_higher_expected(self):
        e_high = _elo_expected_score(1700, 1500)
        e_low  = _elo_expected_score(1500, 1700)
        assert e_high > 0.5 > e_low

    def test_400_point_diff_means_around_91_percent(self):
        # 400-point favourite has ~91% expected score (with home adv).
        e = _elo_expected_score(1900, 1500, home_adv=0)
        assert e == pytest.approx(10/11, abs=1e-3)


# ════════════════════════════════════════════════════════════════════════
# Sum-to-one identity
# ════════════════════════════════════════════════════════════════════════
class TestSumToOne:
    @pytest.mark.parametrize("eh,ea,pd", [
        (1500, 1500, 25.0),
        (1750, 1600, 22.0),
        (1500, 1900, 18.0),
        (1620, 1620, 30.0),
        (1800, 1500, 10.0),
    ])
    def test_sum_is_one(self, eh, ea, pd):
        r = compute_double_chance_potential(
            elo_home=eh, elo_away=ea, draw_probability_pct=pd,
        )
        assert r["p_home"] + r["p_draw"] + r["p_away"] == pytest.approx(1.0, abs=1e-4)


# ════════════════════════════════════════════════════════════════════════
# Double-Chance identities
# ════════════════════════════════════════════════════════════════════════
class TestDoubleChanceIdentities:
    def test_hd_is_home_plus_draw(self):
        r = compute_double_chance_potential(
            elo_home=1700, elo_away=1650, draw_probability_pct=25.0,
        )
        assert r["p_home_or_draw"] == pytest.approx(r["p_home"] + r["p_draw"],
                                                      abs=1e-5)

    def test_ad_is_away_plus_draw(self):
        r = compute_double_chance_potential(
            elo_home=1500, elo_away=1700, draw_probability_pct=22.0,
        )
        assert r["p_away_or_draw"] == pytest.approx(r["p_away"] + r["p_draw"],
                                                      abs=1e-5)

    def test_ha_equals_one_minus_draw(self):
        r = compute_double_chance_potential(
            elo_home=1620, elo_away=1620, draw_probability_pct=30.0,
        )
        assert r["p_home_or_away"] == pytest.approx(1.0 - r["p_draw"],
                                                      abs=1e-5)


# ════════════════════════════════════════════════════════════════════════
# ELO monotonicity
# ════════════════════════════════════════════════════════════════════════
class TestEloMonotonicity:
    def test_stronger_home_team_more_likely_to_win(self):
        r_strong = compute_double_chance_potential(
            elo_home=1800, elo_away=1500, draw_probability_pct=18.0,
        )
        r_weak   = compute_double_chance_potential(
            elo_home=1500, elo_away=1800, draw_probability_pct=18.0,
        )
        assert r_strong["p_home"] > r_weak["p_home"]
        assert r_strong["p_away"] < r_weak["p_away"]

    def test_home_advantage_helps_home(self):
        r_with_adv = compute_double_chance_potential(
            elo_home=1500, elo_away=1500, draw_probability_pct=25.0,
        )
        r_no_adv = compute_double_chance_potential(
            elo_home=1500, elo_away=1500, draw_probability_pct=25.0,
            home_advantage_elo=0,
        )
        assert r_with_adv["p_home"] > r_no_adv["p_home"]


# ════════════════════════════════════════════════════════════════════════
# Probability bounds
# ════════════════════════════════════════════════════════════════════════
class TestBounds:
    @pytest.mark.parametrize("eh,ea,pd", [
        (1500, 1500, 25.0),
        (1900, 1500, 10.0),
        (1500, 1900, 10.0),
        (1500, 1500, 50.0),    # high draw rate
        (1500, 1500, 0.0),     # no draws
    ])
    def test_all_probabilities_in_zero_one(self, eh, ea, pd):
        r = compute_double_chance_potential(
            elo_home=eh, elo_away=ea, draw_probability_pct=pd,
        )
        for key in ("p_home", "p_draw", "p_away",
                    "p_home_or_draw", "p_away_or_draw", "p_home_or_away"):
            assert 0.0 <= r[key] <= 1.0


# ════════════════════════════════════════════════════════════════════════
# Edge cases / renormalisation
# ════════════════════════════════════════════════════════════════════════
class TestRenormalisation:
    def test_negative_p_home_renormalised(self):
        """Very strong away team + very high draw prob → P(home) would
        go negative; module must renormalise."""
        r = compute_double_chance_potential(
            elo_home=1300, elo_away=2000, draw_probability_pct=50.0,
            home_advantage_elo=0,
        )
        assert r["p_home"] >= 0.0
        assert r["p_away"] >= 0.0
        # Sum invariant still holds.
        assert r["p_home"] + r["p_draw"] + r["p_away"] == pytest.approx(1.0, abs=1e-4)
        # We may not require the exact reason code (it depends on which
        # branch fired); just check the math is sane.

    def test_negative_p_away_renormalised(self):
        """Very strong home team + very high draw prob → P(away)
        could approach 0 or negative."""
        r = compute_double_chance_potential(
            elo_home=2000, elo_away=1300, draw_probability_pct=60.0,
            home_advantage_elo=0,
        )
        assert r["p_home"] >= 0.0
        assert r["p_away"] >= 0.0


# ════════════════════════════════════════════════════════════════════════
# Fail-soft
# ════════════════════════════════════════════════════════════════════════
class TestFailSoft:
    def test_missing_elo_uses_symmetric(self):
        r = compute_double_chance_potential(draw_probability_pct=25.0)
        assert RC_ELO_MISSING_SYMMETRIC in r["reason_codes"]

    def test_missing_draw_prob_uses_default(self):
        r = compute_double_chance_potential(elo_home=1700, elo_away=1500)
        assert RC_DRAW_PROB_FALLBACK in r["reason_codes"]
        assert r["p_draw"] == pytest.approx(DEFAULT_DRAW_RATE, abs=1e-4)

    def test_no_inputs_at_all(self):
        r = compute_double_chance_potential()
        for key in ("p_home", "p_draw", "p_away",
                    "p_home_or_draw", "p_away_or_draw", "p_home_or_away"):
            assert 0.0 <= r[key] <= 1.0
        # With symmetric ELO + home adv + default draw rate of 0.24,
        # P(home) > P(away).
        assert r["p_home"] > r["p_away"]

    def test_draw_prob_clamped(self):
        r = compute_double_chance_potential(
            elo_home=1500, elo_away=1500, draw_probability_pct=150.0,
        )
        assert RC_DRAW_PROB_CLAMPED in r["reason_codes"]
        assert r["p_draw"] <= 1.0


# ════════════════════════════════════════════════════════════════════════
# Audit
# ════════════════════════════════════════════════════════════════════════
class TestAudit:
    def test_audit_block_present(self):
        r = compute_double_chance_potential(
            elo_home=1700, elo_away=1500, draw_probability_pct=25.0,
        )
        assert "audit" in r
        assert "elo_delta" in r
        assert "expected_score_home" in r
        assert RC_ELO_OK in r["reason_codes"]
