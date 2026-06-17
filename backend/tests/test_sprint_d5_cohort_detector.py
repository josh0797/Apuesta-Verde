"""Sprint-D5 · Tests for the football cohort detector.

Covers each of the four cohorts:
* DOMINANT_FAVORITE_DRAW_VALUE
* TOURNAMENT_GROUP_STAGE_DRAW_VALUE
* LOW_GOAL_UNDERDOG_BLOCK
* TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS

And the aggregator ``summarise_picks_by_cohort``.
"""
from __future__ import annotations

import pytest

from services.football_cohort_detector import (
    detect_cohorts, summarise_picks_by_cohort,
    is_dominant_favorite_draw_value,
    is_tournament_group_stage_draw_value,
    is_low_goal_underdog_block,
    is_tail_edge_overconfidence,
    ALL_COHORTS,
    COHORT_DOMINANT_FAVORITE, COHORT_TOURNAMENT_GROUP,
    COHORT_LOW_GOAL_UNDERDOG, COHORT_TAIL_EDGE,
    DOMINANT_FAVORITE_ELO_DELTA, DOMINANT_FAVORITE_EDGE_PP,
    TAIL_EDGE_PP_MIN, LOW_GOAL_TOTAL_XG_MAX,
)


def _pick(**kw) -> dict:
    base = {
        "predicted_prob": 0.30, "market_prob": 0.20, "edge_pp": 10.0,
        "hit": False, "label": "VALUE_DRAW_CANDIDATE",
        "is_group_stage": False, "tournament_context_score": None,
        "odd_draw": 5.0, "actual_score": "2-1",
        "home": "H", "away": "A", "date": "2024-09-01",
        "competition": "Test",
    }
    base.update(kw)
    return base


def _features(**kw) -> dict:
    base = {
        "elo_home": 1500, "elo_away": 1500,
        "xg_home_l5": 1.5, "xg_away_l5": 1.5,
        "goal_avg_against_home": 1.2, "goal_avg_against_away": 1.2,
        "tournament_context_score": None,
    }
    base.update(kw)
    return base


# ════════════════════════════════════════════════════════════════════════
# DOMINANT_FAVORITE_DRAW_VALUE
# ════════════════════════════════════════════════════════════════════════
class TestDominantFavorite:
    def test_spain_vs_cape_verde_archetype(self):
        """Spain (1900) vs Cape Verde (1450); edge +10pp → fires."""
        p = _pick(edge_pp=10.0)
        f = _features(elo_home=1900, elo_away=1450)
        assert is_dominant_favorite_draw_value(p, f) is True

    def test_no_fire_when_elo_balanced(self):
        p = _pick(edge_pp=10.0)
        f = _features(elo_home=1600, elo_away=1580)
        assert is_dominant_favorite_draw_value(p, f) is False

    def test_no_fire_below_edge_threshold(self):
        p = _pick(edge_pp=6.0)
        f = _features(elo_home=1900, elo_away=1450)
        assert is_dominant_favorite_draw_value(p, f) is False

    def test_no_features_returns_false(self):
        # Without features the detector cannot evaluate ELO.
        p = _pick(edge_pp=10.0)
        assert is_dominant_favorite_draw_value(p, None) is False

    def test_works_with_underdog_at_home(self):
        # Symmetry: home is weaker.
        p = _pick(edge_pp=10.0)
        f = _features(elo_home=1450, elo_away=1900)
        assert is_dominant_favorite_draw_value(p, f) is True


# ════════════════════════════════════════════════════════════════════════
# TOURNAMENT_GROUP_STAGE_DRAW_VALUE
# ════════════════════════════════════════════════════════════════════════
class TestTournamentGroupStage:
    def test_group_stage_with_high_context_score(self):
        p = _pick(is_group_stage=True, tournament_context_score=0.85,
                   edge_pp=5.0)
        assert is_tournament_group_stage_draw_value(p) is True

    def test_no_fire_outside_group_stage(self):
        p = _pick(is_group_stage=False, tournament_context_score=0.85,
                   edge_pp=10.0)
        assert is_tournament_group_stage_draw_value(p) is False

    def test_no_fire_below_edge_threshold(self):
        p = _pick(is_group_stage=True, tournament_context_score=0.85,
                   edge_pp=2.0)
        assert is_tournament_group_stage_draw_value(p) is False

    def test_no_fire_low_context_score(self):
        p = _pick(is_group_stage=True, tournament_context_score=0.15,
                   edge_pp=10.0)
        assert is_tournament_group_stage_draw_value(p) is False

    def test_no_market_uses_predicted_prob_proxy(self):
        """In no-market mode edge_pp is None; detector uses predicted_prob
        vs the 24% baseline as the edge proxy."""
        p = _pick(is_group_stage=True, tournament_context_score=0.70,
                   predicted_prob=0.32, market_prob=None, edge_pp=None)
        assert is_tournament_group_stage_draw_value(p) is True


# ════════════════════════════════════════════════════════════════════════
# LOW_GOAL_UNDERDOG_BLOCK
# ════════════════════════════════════════════════════════════════════════
class TestLowGoalUnderdogBlock:
    def test_low_tempo_defensive_underdog(self):
        """Total xG = 2.0 (≤ 2.4); underdog (away) GA = 0.8 (≤ 1.1)."""
        p = _pick()
        f = _features(elo_home=1800, elo_away=1500,
                       xg_home_l5=1.0, xg_away_l5=1.0,
                       goal_avg_against_away=0.8)
        assert is_low_goal_underdog_block(p, f) is True

    def test_no_fire_high_total_xg(self):
        f = _features(xg_home_l5=1.6, xg_away_l5=1.4,    # total = 3.0
                       goal_avg_against_away=0.8)
        assert is_low_goal_underdog_block(_pick(), f) is False

    def test_no_fire_porous_underdog(self):
        f = _features(elo_home=1800, elo_away=1500,
                       xg_home_l5=1.0, xg_away_l5=1.0,
                       goal_avg_against_away=1.8)  # too high
        assert is_low_goal_underdog_block(_pick(), f) is False

    def test_no_features_returns_false(self):
        assert is_low_goal_underdog_block(_pick(), None) is False


# ════════════════════════════════════════════════════════════════════════
# TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS
# ════════════════════════════════════════════════════════════════════════
class TestTailEdge:
    def test_fires_at_threshold(self):
        assert is_tail_edge_overconfidence(_pick(edge_pp=15.0)) is True
        assert is_tail_edge_overconfidence(_pick(edge_pp=42.6)) is True

    def test_does_not_fire_below_threshold(self):
        assert is_tail_edge_overconfidence(_pick(edge_pp=14.9)) is False
        assert is_tail_edge_overconfidence(_pick(edge_pp=10.0)) is False

    def test_reconstructs_edge_from_probs(self):
        p = _pick(edge_pp=None, predicted_prob=0.60, market_prob=0.20)
        # edge = (0.60 - 0.20) * 100 = 40 pp
        assert is_tail_edge_overconfidence(p) is True


# ════════════════════════════════════════════════════════════════════════
# detect_cohorts (top-level)
# ════════════════════════════════════════════════════════════════════════
class TestDetectCohorts:
    def test_multiple_cohorts_can_apply(self):
        """A dominant favourite WITH tail edge should hit both cohorts."""
        p = _pick(edge_pp=20.0)
        f = _features(elo_home=1900, elo_away=1450)
        out = detect_cohorts(p, f)
        assert COHORT_DOMINANT_FAVORITE in out["cohorts"]
        assert COHORT_TAIL_EDGE in out["cohorts"]

    def test_audit_block_present(self):
        out = detect_cohorts(_pick(), _features())
        assert "cohorts" in out
        assert "audit" in out
        assert "edge_pp_used" in out["audit"]

    def test_empty_features_safe(self):
        out = detect_cohorts(_pick(edge_pp=20.0), None)
        # Tail edge does not need features.
        assert COHORT_TAIL_EDGE in out["cohorts"]
        # Dominant-favorite requires features.
        assert COHORT_DOMINANT_FAVORITE not in out["cohorts"]


# ════════════════════════════════════════════════════════════════════════
# summarise_picks_by_cohort
# ════════════════════════════════════════════════════════════════════════
class TestSummariseByCohort:
    def test_aggregates_and_returns_examples(self):
        picks = [
            _pick(edge_pp=20.0, hit=True),
            _pick(edge_pp=20.0, hit=False),
        ]
        feats = [_features(elo_home=1900, elo_away=1450),
                  _features(elo_home=1900, elo_away=1450)]
        out = summarise_picks_by_cohort(picks, feats)
        assert out[COHORT_DOMINANT_FAVORITE]["n"] == 2
        assert out[COHORT_DOMINANT_FAVORITE]["won"] == 1
        assert out[COHORT_DOMINANT_FAVORITE]["hit_rate"] == 0.5
        assert out[COHORT_TAIL_EDGE]["n"] == 2

    def test_examples_capped_at_five(self):
        picks = [_pick(edge_pp=20.0, hit=True) for _ in range(20)]
        feats = [_features(elo_home=1900, elo_away=1450) for _ in range(20)]
        out = summarise_picks_by_cohort(picks, feats)
        for c in ALL_COHORTS:
            assert len(out[c]["examples"]) <= 5

    def test_no_features_still_works(self):
        picks = [_pick(edge_pp=20.0, hit=True)]
        out = summarise_picks_by_cohort(picks)
        # Tail edge fires; dominant favourite does not (needs features).
        assert out[COHORT_TAIL_EDGE]["n"] == 1
        assert out[COHORT_DOMINANT_FAVORITE]["n"] == 0
