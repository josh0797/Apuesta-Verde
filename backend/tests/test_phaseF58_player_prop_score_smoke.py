"""Phase F58 Fix 2 — Player Prop Score + Fragility smoke tests."""
from __future__ import annotations

import asyncio

import pytest

from services import football_player_props_discovery as disc


def _fetcher_factory(stats: dict, *, available: bool = True,
                     source: str = "statmuse", minutes_sample: int = 1800,
                     penalty: int = 0):
    async def _fetcher(*, player_name, team=None, league=None):  # noqa: ANN001
        return {
            "available":          available, "source":             source,
            "confidence_penalty": penalty, "minutes_sample":     minutes_sample,
            "stats":              stats, "raw":                {},
            "engine_version":     "test",
        }
    return _fetcher


# ─────────────────────────────────────────────────────────────────────
# compute_player_prop_score — direct unit tests
# ─────────────────────────────────────────────────────────────────────
def test_score_high_when_minutes_stable_volume_and_matchup():
    score, rcs, mult = disc.compute_player_prop_score(
        player={"projected_starter": True, "minutes_projection": 80,
                "started_last_5": 5, "position_fits_market": True},
        market=disc.MARKET_SOT_OVER,
        stat_p90=1.4,  # over volume threshold 0.8
        matchup_context={"opponent_allows_high_sot": True, "opponent_pace_mult": 1.1},
        game_script="UNILATERAL_DOMINANCE",
        line=0.5, prob=0.78, source="statmuse",
    )
    assert score >= 75
    assert "PLAYER_MINUTES_STABLE" in rcs
    assert "HIGH_SOT_VOLUME" in rcs
    assert "MATCHUP_ALLOWS_SOT" in rcs
    assert "SCRIPT_SUPPORTS_SHOTS" in rcs
    assert 0.75 <= mult <= 1.25


def test_score_low_when_rotation_risk_and_low_volume():
    score, rcs, _ = disc.compute_player_prop_score(
        player={"projected_starter": False, "minutes_projection": 45,
                "rotation_risk": True, "substitute_likely": True},
        market=disc.MARKET_SHOTS_OVER,
        stat_p90=0.6,  # below threshold 2.0
        matchup_context={},
        game_script=None,
        line=1.5, prob=0.45, source="understat",
    )
    assert score <= 45
    assert "PLAYER_ROTATION_RISK" in rcs


def test_score_blocked_by_low_event_script():
    score, rcs, _ = disc.compute_player_prop_score(
        player={"projected_starter": True, "minutes_projection": 80},
        market=disc.MARKET_SHOTS_OVER,
        stat_p90=2.5,
        matchup_context={"opponent_allows_high_shots": True},
        game_script=disc.SCRIPT_LOW_EVENT_UNDER,
        line=1.5, prob=0.65, source="statmuse",
    )
    assert "SCRIPT_HURTS_AGGRESSIVE_PROPS" in rcs
    assert score <= 80   # script downweights aggressive props


# ─────────────────────────────────────────────────────────────────────
# compute_player_prop_fragility
# ─────────────────────────────────────────────────────────────────────
def test_fragility_baseline_by_market():
    # SOT_OVER baseline = 42, with stable inputs should stay near baseline.
    f = disc.compute_player_prop_fragility(
        market=disc.MARKET_SOT_OVER, line=0.5, stat_p90=1.2,
        minutes_sample=2000, minutes_projection=85,
        matchup_context={}, game_script=None,
        confidence_penalty=0, rotation_risk=False,
    )
    # Baseline 42 minus line-bonus(5) = 37 expected.
    assert 30 <= f <= 45


def test_fragility_to_score_baseline_high():
    f = disc.compute_player_prop_fragility(
        market=disc.MARKET_TO_SCORE, line=0.5, stat_p90=0.6,
        minutes_sample=2000, minutes_projection=85,
        matchup_context={}, game_script=None,
        confidence_penalty=0, rotation_risk=False,
    )
    # TO_SCORE baseline = 65, with -5 line bonus = ~60, may dip below with stat_p90>=1.3x ⇒ ~54.
    assert f >= 50


def test_fragility_increases_with_rotation_and_low_minutes():
    f = disc.compute_player_prop_fragility(
        market=disc.MARKET_SHOTS_OVER, line=2.5, stat_p90=1.0,
        minutes_sample=200, minutes_projection=55,
        matchup_context={"opponent_suppresses_shots": True},
        game_script=disc.SCRIPT_LOW_EVENT_UNDER,
        confidence_penalty=8, rotation_risk=True,
    )
    assert f >= 60


# ─────────────────────────────────────────────────────────────────────
# Moneyball filter end-to-end
# ─────────────────────────────────────────────────────────────────────
def test_moneyball_filter_promotes_to_top_player_props():
    # Premium scenario: starter, high volume, matchup OK, low line.
    stats = {
        "shots_p90":   3.5, "sot_p90": 1.8, "passes_p90": 55.0,
        "tackles_p90": 2.5, "fouls_p90": 1.5, "cards_p90": 0.15,
        "xg_p90":      0.35, "minutes_p_game": 85.0,
    }
    players = [{
        "name": "Bukayo Saka", "team": "Arsenal",
        "projected_starter": True, "minutes_projection": 85,
        "started_last_5": 5, "position_fits_market": True,
    }]
    res = asyncio.run(disc.discover_player_props(
        players=players, matchup_context={"opponent_allows_high_sot": True,
                                          "opponent_allows_high_shots": True},
        game_script=disc.SCRIPT_BILATERAL_OPENNESS,
        stats_fetcher=_fetcher_factory(stats),
    ))
    assert res["available"] is True
    # SOT_OVER 0.5 should land in top_player_props.
    sot = next((p for p in res["top_player_props"] if p["market"] == disc.MARKET_SOT_OVER), None)
    assert sot is not None
    assert sot["player_prop_score"] >= disc.MONEYBALL_SCORE_GATE
    assert sot["player_prop_fragility"] <= disc.MONEYBALL_FRAGILITY_GATE
    assert sot["confidence_tier"] in {"VALUE", "PREMIUM"}
    assert sot["passes_moneyball_filter"] is True
    # Summary.top_count must reflect the filtered count.
    assert res["summary"]["top_count"] >= 1


def test_moneyball_filter_excludes_low_score_props():
    # Sub-par scenario: rotation risk + low volume → should NOT pass filter.
    stats = {
        "shots_p90":   2.5, "sot_p90": 1.0, "passes_p90": 30.0,
        "tackles_p90": 1.0, "fouls_p90": 1.2, "cards_p90": 0.10,
        "xg_p90":      0.25, "minutes_p_game": 60.0,
    }
    players = [{
        "name": "Sub Player", "team": "Test FC",
        "projected_starter": False, "minutes_projection": 45,
        "rotation_risk": True, "substitute_likely": True,
    }]
    res = asyncio.run(disc.discover_player_props(
        players=players, stats_fetcher=_fetcher_factory(stats),
    ))
    # Nothing should pass the Moneyball gate.
    assert res["summary"]["top_count"] == 0
    for p in res["props"]:
        assert p["passes_moneyball_filter"] is False
        assert p["confidence_tier"] in {"WATCHLIST", "AVOID"}


def test_tier3_to_score_only_promotes_with_elite_score_and_low_fragility():
    # Elite striker: high xG + perfect minutes + low fragility inputs.
    stats = {
        "shots_p90": 4.0, "sot_p90": 2.0, "passes_p90": 35.0,
        "tackles_p90": 0.5, "fouls_p90": 1.5, "cards_p90": 0.10,
        "xg_p90": 1.0, "minutes_p_game": 88.0,
    }
    players = [{
        "name": "Elite Striker", "team": "X",
        "projected_starter": True, "minutes_projection": 88,
        "started_last_5": 5, "position_fits_market": True,
        "expected_minutes": 88,
    }]
    res = asyncio.run(disc.discover_player_props(
        players=players,
        matchup_context={"opponent_allows_high_sot": True, "opponent_allows_high_shots": True,
                         "opponent_pace_mult": 1.15},
        game_script=disc.SCRIPT_UNILATERAL_DOMINANCE,
        stats_fetcher=_fetcher_factory(stats),
    ))
    to_score = next((p for p in res["top_player_props"] if p["market"] == disc.MARKET_TO_SCORE), None)
    # May or may not graduate depending on numerics, but if it does it
    # must meet the strict gates.
    if to_score is not None:
        assert to_score["player_prop_score"] >= disc.TIER3_SCORE_GATE
        assert to_score["player_prop_fragility"] <= disc.TIER3_FRAGILITY_GATE
        assert to_score["confidence_tier"] == "PREMIUM"


def test_output_shape_matches_spec():
    # Verify the exact output shape requested in the spec.
    stats = {
        "shots_p90": 3.0, "sot_p90": 1.5, "passes_p90": 55.0,
        "tackles_p90": 2.0, "fouls_p90": 1.8, "cards_p90": 0.15,
        "xg_p90": 0.35, "minutes_p_game": 80.0,
    }
    players = [{"name": "Bukayo Saka", "team": "Arsenal",
                "projected_starter": True, "minutes_projection": 85,
                "started_last_5": 5, "position_fits_market": True}]
    res = asyncio.run(disc.discover_player_props(
        players=players, matchup_context={"opponent_allows_high_sot": True},
        game_script=disc.SCRIPT_UNILATERAL_DOMINANCE,
        stats_fetcher=_fetcher_factory(stats),
    ))
    # Required top-level keys.
    for key in ("available", "engine_version", "top_player_props",
                "props", "summary", "skipped"):
        assert key in res
    assert res["engine_version"] == "football_player_props_discovery.v2"
    # Each prop must carry the new canonical fields.
    if res["top_player_props"]:
        p = res["top_player_props"][0]
        for k in ("player", "team", "market", "line", "selection",
                  "player_prop_score", "player_prop_fragility",
                  "confidence_tier", "reason_codes", "narrative_es"):
            assert k in p
        assert isinstance(p["player_prop_score"], int)
        assert isinstance(p["player_prop_fragility"], int)
        assert 0 <= p["player_prop_score"] <= 100
        assert 0 <= p["player_prop_fragility"] <= 100


def test_props_sorted_by_player_prop_score_desc():
    stats = {
        "shots_p90": 3.0, "sot_p90": 1.4, "passes_p90": 48.0,
        "tackles_p90": 2.2, "fouls_p90": 1.8, "cards_p90": 0.18,
        "xg_p90": 0.40, "minutes_p_game": 80.0,
    }
    players = [{"name": "Player A"}, {"name": "Player B"}, {"name": "Player C"}]
    res = asyncio.run(disc.discover_player_props(
        players=players, stats_fetcher=_fetcher_factory(stats),
    ))
    scores = [p["player_prop_score"] for p in res["props"]]
    assert scores == sorted(scores, reverse=True)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
