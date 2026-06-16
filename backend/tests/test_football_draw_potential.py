"""Sprint-A · Unit tests for ``services.football_draw_potential``.

These tests pin down the heuristic so future calibration (Sprint B with
the learning loops) cannot silently drift the behaviour.
"""
from __future__ import annotations

import pytest

from services.football_draw_potential import (
    compute_draw_potential,
    implied_probability_from_american_odds,
    BASE_DRAW_PROBABILITY,
    DRAW_PROB_FLOOR,
    DRAW_PROB_CEILING,
    LABEL_VALUE_DRAW,
    LABEL_STRONG_VALUE,
    LABEL_FAIR_DRAW,
    LABEL_NO_VALUE,
    LABEL_INSUFFICIENT,
    RC_EVEN_MATCHUP,
    RC_DOMINANT_FAVOURITE,
    RC_GROUP_STAGE_CONSERVATIVE,
    RC_BOTH_NEED_POINTS,
    RC_LOW_GOAL_ENV,
    RC_CONSERVATIVE_STYLE_BOTH,
    RC_NEGATIVE_EDGE,
    RC_INSUFFICIENT_INPUTS,
    RC_MARKET_IMPLIED_UNAVAILABLE,
)


# ─────────────────────────────────────────────────────────────────────
# 1. American-odds helper
# ─────────────────────────────────────────────────────────────────────
class TestImpliedProbabilityFromAmericanOdds:
    def test_underdog_plus_900(self):
        # +900 -> 100 / (900+100) = 0.10
        assert implied_probability_from_american_odds("+900") == pytest.approx(0.10)
        assert implied_probability_from_american_odds(900) == pytest.approx(0.10)

    def test_underdog_plus_280(self):
        # +280 -> 100 / 380 ≈ 0.2632
        assert implied_probability_from_american_odds("+280") == pytest.approx(0.2632, abs=1e-3)

    def test_underdog_plus_300(self):
        # +300 -> 100 / 400 = 0.25
        assert implied_probability_from_american_odds("+300") == pytest.approx(0.25)

    def test_favourite_minus_150(self):
        # -150 -> 150 / 250 = 0.60
        assert implied_probability_from_american_odds("-150") == pytest.approx(0.60)

    def test_zero_returns_none(self):
        assert implied_probability_from_american_odds(0) is None

    def test_malformed_input_returns_none(self):
        assert implied_probability_from_american_odds("abc") is None
        assert implied_probability_from_american_odds(None) is None


# ─────────────────────────────────────────────────────────────────────
# 2. Insufficient-data paths
# ─────────────────────────────────────────────────────────────────────
class TestInsufficientData:
    def test_no_inputs_at_all_returns_insufficient(self):
        out = compute_draw_potential()
        assert out["label"] == LABEL_INSUFFICIENT
        assert RC_INSUFFICIENT_INPUTS in out["reason_codes"]
        assert out["draw_probability"] is None
        assert out["edge"] is None

    def test_only_one_elo_value_is_insufficient(self):
        out = compute_draw_potential(elo_home=1800)  # no elo_away
        assert out["label"] == LABEL_INSUFFICIENT

    def test_context_flags_alone_are_enough_to_attempt_verdict(self):
        # No quantitative inputs but several contextual flags -> we DO
        # try to produce a verdict (fail-soft principle).
        out = compute_draw_potential(
            is_group_stage=True, both_need_points=True,
            low_goal_environment=True,
            conservative_style_home=True, conservative_style_away=True,
            market_implied_draw_prob=0.20,
        )
        assert out["label"] != LABEL_INSUFFICIENT
        assert out["draw_probability"] is not None


# ─────────────────────────────────────────────────────────────────────
# 3. Strength balance — even matchup boosts probability
# ─────────────────────────────────────────────────────────────────────
class TestStrengthBalance:
    def test_identical_teams_max_balance_boost(self):
        out = compute_draw_potential(
            home_team="A", away_team="B",
            elo_home=1700, elo_away=1700,
            xg_home_l5=1.4, xg_away_l5=1.4,
            market_implied_draw_prob=0.20,
        )
        assert RC_EVEN_MATCHUP in out["reason_codes"]
        # base 24% + full 10pp balance boost ~ 34%
        assert 33.0 <= out["draw_probability"] <= 35.0

    def test_huge_favourite_lowers_probability(self):
        out = compute_draw_potential(
            elo_home=1900, elo_away=1500,        # 400 ELO gap
            xg_home_l5=2.2, xg_away_l5=0.6,      # 1.6 xG gap
            market_implied_draw_prob=0.20,
        )
        assert RC_DOMINANT_FAVOURITE in out["reason_codes"]
        # base 24% + nearly-zero balance contribution - 6pp penalty
        assert out["draw_probability"] < BASE_DRAW_PROBABILITY * 100.0

    def test_balance_never_exceeds_ceiling(self):
        out = compute_draw_potential(
            elo_home=1700, elo_away=1700,
            xg_home_l5=1.0, xg_away_l5=1.0,
            is_group_stage=True, both_need_points=True,
            low_goal_environment=True,
            conservative_style_home=True, conservative_style_away=True,
            market_implied_draw_prob=0.20,
        )
        assert out["draw_probability"] <= DRAW_PROB_CEILING * 100.0 + 1e-6


# ─────────────────────────────────────────────────────────────────────
# 4. Edge labelling thresholds
# ─────────────────────────────────────────────────────────────────────
class TestEdgeLabelling:
    def _balanced(self, **kwargs):
        # Helper: a balanced match returning ~34% draw probability.
        base = dict(
            elo_home=1700, elo_away=1700,
            xg_home_l5=1.4, xg_away_l5=1.4,
        )
        base.update(kwargs)
        return compute_draw_potential(**base)

    def test_strong_value_when_edge_above_8pp(self):
        # 34% model vs 20% market => +14pp edge -> STRONG_VALUE
        out = self._balanced(market_implied_draw_prob=0.20)
        assert out["edge"] >= 8.0
        assert out["label"] == LABEL_STRONG_VALUE

    def test_value_when_edge_between_4_and_8pp(self):
        # 34% model vs 28% market => +6pp -> VALUE_DRAW
        out = self._balanced(market_implied_draw_prob=0.28)
        assert 4.0 <= out["edge"] < 8.0
        assert out["label"] == LABEL_VALUE_DRAW

    def test_fair_draw_when_edge_below_4pp(self):
        # 34% model vs 32% market => +2pp -> FAIR
        out = self._balanced(market_implied_draw_prob=0.32)
        assert 0 <= out["edge"] < 4.0
        assert out["label"] == LABEL_FAIR_DRAW

    def test_no_value_when_edge_is_negative(self):
        # 34% model vs 45% market => -11pp -> NO_VALUE
        out = self._balanced(market_implied_draw_prob=0.45)
        assert out["edge"] < 0
        assert out["label"] == LABEL_NO_VALUE
        assert RC_NEGATIVE_EDGE in out["reason_codes"]

    def test_market_missing_returns_fair_draw_no_edge(self):
        out = self._balanced(market_implied_draw_prob=None)
        assert out["edge"] is None
        assert out["market_implied"] is None
        assert out["label"] == LABEL_FAIR_DRAW
        assert RC_MARKET_IMPLIED_UNAVAILABLE in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 5. Modifier reason codes propagate correctly
# ─────────────────────────────────────────────────────────────────────
class TestReasonCodes:
    def test_group_stage_both_need_points_emits_two_codes(self):
        out = compute_draw_potential(
            elo_home=1700, elo_away=1700,
            xg_home_l5=1.4, xg_away_l5=1.4,
            is_group_stage=True, both_need_points=True,
            market_implied_draw_prob=0.20,
        )
        assert RC_GROUP_STAGE_CONSERVATIVE in out["reason_codes"]
        assert RC_BOTH_NEED_POINTS in out["reason_codes"]

    def test_low_goal_env_increases_probability(self):
        out_base = compute_draw_potential(
            elo_home=1700, elo_away=1700,
            xg_home_l5=1.4, xg_away_l5=1.4,
            market_implied_draw_prob=0.20,
        )
        out_low = compute_draw_potential(
            elo_home=1700, elo_away=1700,
            xg_home_l5=1.4, xg_away_l5=1.4,
            low_goal_environment=True,
            market_implied_draw_prob=0.20,
        )
        assert out_low["draw_probability"] > out_base["draw_probability"]
        assert RC_LOW_GOAL_ENV in out_low["reason_codes"]

    def test_conservative_style_both_sides(self):
        out = compute_draw_potential(
            elo_home=1700, elo_away=1700,
            xg_home_l5=1.4, xg_away_l5=1.4,
            conservative_style_home=True, conservative_style_away=True,
            market_implied_draw_prob=0.20,
        )
        assert RC_CONSERVATIVE_STYLE_BOTH in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 6. Robustness — type coercion and weird inputs
# ─────────────────────────────────────────────────────────────────────
class TestRobustness:
    def test_string_inputs_coerced(self):
        out = compute_draw_potential(
            elo_home="1700", elo_away="1700",
            xg_home_l5="1.4", xg_away_l5="1.4",
            market_implied_draw_prob=0.20,
        )
        assert out["draw_probability"] is not None

    def test_garbage_inputs_do_not_raise(self):
        # Garbage numeric inputs silently degrade to None.
        out = compute_draw_potential(
            elo_home="abc", elo_away=None,
            xg_home_l5=None, xg_away_l5="xyz",
            is_group_stage=True, both_need_points=True,
            market_implied_draw_prob=0.20,
        )
        # The contextual flags allow a verdict despite garbage numerics.
        assert out["label"] != LABEL_INSUFFICIENT

    def test_output_always_has_required_keys(self):
        out = compute_draw_potential(
            elo_home=1700, elo_away=1700,
            xg_home_l5=1.4, xg_away_l5=1.4,
            market_implied_draw_prob=0.20,
        )
        for k in ("home_team", "away_team", "draw_probability",
                  "market_implied", "edge", "label",
                  "reason_codes", "debug", "available"):
            assert k in out, f"missing key: {k}"
        assert out["available"] is True


# ─────────────────────────────────────────────────────────────────────
# 7. Probability bounds invariants
# ─────────────────────────────────────────────────────────────────────
class TestProbabilityBounds:
    @pytest.mark.parametrize("args", [
        dict(elo_home=2000, elo_away=1200,
             xg_home_l5=3.0, xg_away_l5=0.2,
             is_group_stage=False, market_implied_draw_prob=0.10),
        dict(elo_home=1500, elo_away=1500,
             xg_home_l5=1.2, xg_away_l5=1.2,
             is_group_stage=True, both_need_points=True,
             low_goal_environment=True,
             conservative_style_home=True, conservative_style_away=True,
             market_implied_draw_prob=0.20),
        dict(elo_home=1700, elo_away=1700,
             xg_home_l5=1.4, xg_away_l5=1.4,
             market_implied_draw_prob=None),
    ])
    def test_probability_within_bounds(self, args):
        out = compute_draw_potential(**args)
        if out["draw_probability"] is None:
            return
        assert DRAW_PROB_FLOOR * 100.0 <= out["draw_probability"] <= DRAW_PROB_CEILING * 100.0 + 1e-6
