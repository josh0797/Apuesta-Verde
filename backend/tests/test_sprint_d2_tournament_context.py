"""Sprint-D2 · Tests for the tournament_context_score module.

Covers:
* score range [0,1]
* booster clamp [0, 3] pp
* activation threshold (0.60)
* matchday-1 / matchday-2 / matchday-3 branches
* knockout cap
* fail-soft behaviour with missing inputs
* integration into compute_draw_potential
"""
from __future__ import annotations

import pytest

from services.football_tournament_context import (
    compute_tournament_context_score,
    BOOST_ACTIVATION_THRESHOLD, BOOST_MIN_PP, BOOST_MAX_PP, BOOST_CAP_PP,
    MATCHDAY_1_BASE, MATCHDAY_2_BASE,
    MATCHDAY_3_BOTH_QUALIFIED, MATCHDAY_3_BOTH_ELIMINATED,
    MATCHDAY_3_BOTH_NEED_DRAW, MATCHDAY_3_ASYMMETRIC,
    KNOCKOUT_MAX_SCORE,
    RC_MATCHDAY_1, RC_MATCHDAY_2,
    RC_MATCHDAY_3_BOTH_QUALIFIED, RC_MATCHDAY_3_BOTH_ELIMINATED,
    RC_MATCHDAY_3_BOTH_NEED_DRAW, RC_MATCHDAY_3_ASYMMETRIC,
    RC_BOOSTER_APPLIED, RC_BOOSTER_BELOW_THRESHOLD,
    RC_KNOCKOUT_CAPPED, RC_INSUFFICIENT_GROUP_DATA,
)
from services.football_draw_potential import compute_draw_potential


def _row(team="X", played=0, won=0, drawn=0, lost=0,
         gf=0, ga=0, points=0, gd=0):
    if gd == 0:
        gd = gf - ga
    return {
        "team": team, "played": played, "won": won, "drawn": drawn,
        "lost": lost, "gf": gf, "ga": ga, "points": points, "gd": gd,
    }


# ════════════════════════════════════════════════════════════════════════
# Score bounds & basic invariants
# ════════════════════════════════════════════════════════════════════════
class TestScoreBounds:
    def test_score_is_within_0_1(self):
        for played in (0, 1, 2, 3):
            for matchday in (1, 2, 3, None):
                out = compute_tournament_context_score(
                    standings_home=_row(played=played),
                    standings_away=_row(played=played),
                    match_meta={"matchday": matchday,
                                 "tournament_phase": "GROUP",
                                 "is_group_stage": True},
                )
                assert 0.0 <= out["score_0_1"] <= 1.0

    def test_boost_is_within_0_3(self):
        for score_target in (0.0, 0.5, 0.6, 0.7, 1.0):
            # Force a score by feeding the right standings/matchday.
            # We use MD3 with both qualified for ~0.85, etc.
            out = compute_tournament_context_score(
                standings_home=_row(played=2, points=6, gd=4),
                standings_away=_row(played=2, points=6, gd=4),
                match_meta={"matchday": 3, "tournament_phase": "GROUP",
                             "is_group_stage": True},
            )
            assert 0.0 <= out["boost_pp"] <= BOOST_CAP_PP


# ════════════════════════════════════════════════════════════════════════
# Matchday branches
# ════════════════════════════════════════════════════════════════════════
class TestMatchdayBranches:
    def test_matchday_1_baseline(self):
        out = compute_tournament_context_score(
            standings_home=_row(played=0),
            standings_away=_row(played=0),
            match_meta={"matchday": 1, "tournament_phase": "GROUP",
                         "is_group_stage": True},
        )
        assert out["score_0_1"] == round(MATCHDAY_1_BASE, 3)
        assert RC_MATCHDAY_1 in out["reason_codes"]
        # No booster at MD1.
        assert out["boost_pp"] == 0.0
        assert RC_BOOSTER_BELOW_THRESHOLD in out["reason_codes"]

    def test_matchday_2_baseline(self):
        out = compute_tournament_context_score(
            standings_home=_row(played=1, points=3),
            standings_away=_row(played=1, points=0),
            match_meta={"matchday": 2, "tournament_phase": "GROUP",
                         "is_group_stage": True},
        )
        assert out["score_0_1"] == round(MATCHDAY_2_BASE, 3)
        assert RC_MATCHDAY_2 in out["reason_codes"]
        assert out["boost_pp"] == 0.0

    def test_matchday_3_both_qualified_triggers_booster(self):
        out = compute_tournament_context_score(
            standings_home=_row(played=2, points=6, gd=4),
            standings_away=_row(played=2, points=6, gd=3),
            match_meta={"matchday": 3, "tournament_phase": "GROUP",
                         "is_group_stage": True},
        )
        assert out["score_0_1"] == round(MATCHDAY_3_BOTH_QUALIFIED, 3)
        assert RC_MATCHDAY_3_BOTH_QUALIFIED in out["reason_codes"]
        assert RC_BOOSTER_APPLIED in out["reason_codes"]
        # 0.85 → ramp value between BOOST_MIN_PP and BOOST_MAX_PP.
        assert BOOST_MIN_PP <= out["boost_pp"] <= BOOST_CAP_PP

    def test_matchday_3_both_eliminated(self):
        out = compute_tournament_context_score(
            standings_home=_row(played=2, points=0, gd=-3),
            standings_away=_row(played=2, points=0, gd=-2),
            match_meta={"matchday": 3, "tournament_phase": "GROUP",
                         "is_group_stage": True},
        )
        assert out["score_0_1"] == round(MATCHDAY_3_BOTH_ELIMINATED, 3)
        assert RC_MATCHDAY_3_BOTH_ELIMINATED in out["reason_codes"]
        # 0.70 ≥ 0.60 → booster applied (small).
        assert out["boost_pp"] > 0

    def test_matchday_3_both_need_draw_max_score(self):
        # Both on 4 pts (1W 1D) → classic "both go through with a draw".
        out = compute_tournament_context_score(
            standings_home=_row(played=2, points=4, gd=1),
            standings_away=_row(played=2, points=4, gd=1),
            match_meta={"matchday": 3, "tournament_phase": "GROUP",
                         "is_group_stage": True},
        )
        assert out["score_0_1"] == round(MATCHDAY_3_BOTH_NEED_DRAW, 3)
        assert RC_MATCHDAY_3_BOTH_NEED_DRAW in out["reason_codes"]
        # Score = 1.0 → boost = BOOST_MAX_PP (≈3.0).
        assert out["boost_pp"] == pytest.approx(BOOST_MAX_PP, abs=1e-3)
        assert out["both_need_points_inferred"] is True

    def test_matchday_3_asymmetric_no_booster(self):
        # One on 6 pts, other on 3 pts → asymmetric (0.55 < 0.60).
        out = compute_tournament_context_score(
            standings_home=_row(played=2, points=6),
            standings_away=_row(played=2, points=3),
            match_meta={"matchday": 3, "tournament_phase": "GROUP",
                         "is_group_stage": True},
        )
        assert out["score_0_1"] == round(MATCHDAY_3_ASYMMETRIC, 3)
        assert RC_MATCHDAY_3_ASYMMETRIC in out["reason_codes"]
        assert out["boost_pp"] == 0.0

    def test_matchday_inferred_from_played(self):
        """When matchday is None but both teams have played 2 games,
        treat as MD3."""
        out = compute_tournament_context_score(
            standings_home=_row(played=2, points=6),
            standings_away=_row(played=2, points=6),
            match_meta={"matchday": None, "tournament_phase": "GROUP",
                         "is_group_stage": True},
        )
        assert out["score_0_1"] > 0.6


# ════════════════════════════════════════════════════════════════════════
# Knockout
# ════════════════════════════════════════════════════════════════════════
class TestKnockout:
    def test_knockout_is_capped(self):
        out = compute_tournament_context_score(
            standings_home=_row(played=0),
            standings_away=_row(played=0),
            match_meta={"matchday": None, "tournament_phase": "KNOCKOUT",
                         "is_group_stage": False},
        )
        assert out["score_0_1"] == round(KNOCKOUT_MAX_SCORE, 3)
        assert RC_KNOCKOUT_CAPPED in out["reason_codes"]
        # Cap (0.25) is BELOW activation threshold (0.60) → no booster.
        assert out["boost_pp"] == 0.0


# ════════════════════════════════════════════════════════════════════════
# Fail-soft
# ════════════════════════════════════════════════════════════════════════
class TestFailSoft:
    def test_missing_standings_returns_zero(self):
        out = compute_tournament_context_score(
            standings_home=None,
            standings_away=None,
            match_meta={"matchday": 1, "tournament_phase": "GROUP",
                         "is_group_stage": True},
        )
        assert out["score_0_1"] == 0.0
        assert out["boost_pp"] == 0.0
        assert RC_INSUFFICIENT_GROUP_DATA in out["reason_codes"]

    def test_empty_meta(self):
        out = compute_tournament_context_score(
            standings_home=_row(played=1),
            standings_away=_row(played=1),
            match_meta={},
        )
        assert "score_0_1" in out
        assert out["score_0_1"] >= 0.0

    def test_meta_none(self):
        out = compute_tournament_context_score(
            standings_home=_row(played=1),
            standings_away=_row(played=1),
            match_meta=None,
        )
        assert out["score_0_1"] >= 0.0


# ════════════════════════════════════════════════════════════════════════
# Integration with compute_draw_potential
# ════════════════════════════════════════════════════════════════════════
class TestDrawPotentialIntegration:
    def test_score_above_threshold_boosts_draw_prob(self):
        """A high tournament_context_score should bump draw_probability
        by 2..3 pp."""
        # Baseline (no context).
        v_base = compute_draw_potential(
            elo_home=1500, elo_away=1500,
            xg_home_l5=1.2, xg_away_l5=1.2,
            is_group_stage=True,
        )
        # With high context score (1.0 → +3pp).
        v_boosted = compute_draw_potential(
            elo_home=1500, elo_away=1500,
            xg_home_l5=1.2, xg_away_l5=1.2,
            is_group_stage=True,
            tournament_context_score=1.0,
        )
        # Boost should be positive but capped at 3pp.
        diff = v_boosted["draw_probability"] - v_base["draw_probability"]
        assert 1.5 <= diff <= 3.5, (
            f"Expected diff in [1.5,3.5], got {diff}. "
            f"base={v_base['draw_probability']} boosted={v_boosted['draw_probability']}"
        )

    def test_score_below_threshold_no_boost(self):
        v_base = compute_draw_potential(
            elo_home=1500, elo_away=1500,
            xg_home_l5=1.2, xg_away_l5=1.2,
            is_group_stage=True,
        )
        v_low = compute_draw_potential(
            elo_home=1500, elo_away=1500,
            xg_home_l5=1.2, xg_away_l5=1.2,
            is_group_stage=True,
            tournament_context_score=0.30,   # below 0.60 threshold
        )
        assert v_low["draw_probability"] == v_base["draw_probability"]

    def test_score_none_no_boost(self):
        """None must be safe (default behaviour without context)."""
        v_base = compute_draw_potential(
            elo_home=1500, elo_away=1500,
            xg_home_l5=1.2, xg_away_l5=1.2,
            is_group_stage=True,
        )
        v_none = compute_draw_potential(
            elo_home=1500, elo_away=1500,
            xg_home_l5=1.2, xg_away_l5=1.2,
            is_group_stage=True,
            tournament_context_score=None,
        )
        assert v_none["draw_probability"] == v_base["draw_probability"]

    def test_boost_clamped_when_at_ceiling(self):
        """If prob is already near the DRAW_PROB_CEILING (0.42), the
        booster must not push past the ceiling."""
        # Force a scenario with many boosters active.
        v = compute_draw_potential(
            elo_home=1500, elo_away=1500,
            xg_home_l5=0.6, xg_away_l5=0.6,
            is_group_stage=True,
            both_need_points=True,
            low_goal_environment=True,
            conservative_style_home=True, conservative_style_away=True,
            tournament_context_score=1.0,
        )
        # Ceiling is 42.0% → must be ≤ 42.0.
        assert v["draw_probability"] <= 42.1
