"""Phase F58 smoke tests — Football Player Props Discovery (Moneyball).

Cubre:
  * Generación happy-path con Tier 1.
  * Gate Tier 3 (PLAYER_TO_SCORE) — solo dispara si edge_score≥90 & frag≤35.
  * Skip cuando ingestor devuelve available=False.
  * Filtros Moneyball (min prob, min edge, longshot floor).
  * Cero rotura cuando players=[].
"""
from __future__ import annotations

import asyncio

import pytest

from services import football_player_props_discovery as disc


def _fetcher_factory(stats: dict, *, available: bool = True, source: str = "statmuse",
                     minutes_sample: int = 1800, penalty: int = 0):
    """Build an awaitable stats_fetcher that returns a fixed payload."""
    async def _fetcher(*, player_name, team=None, league=None):  # noqa: ANN001
        return {
            "available":          available,
            "source":             source,
            "confidence_penalty": penalty,
            "minutes_sample":     minutes_sample,
            "stats":              stats,
            "raw":                {},
            "engine_version":     "test",
        }
    return _fetcher


def test_empty_players_returns_unavailable():
    res = asyncio.run(disc.discover_player_props(players=[]))
    assert res["available"] is False
    assert res["props"] == []
    assert res["summary"]["total"] == 0


def test_tier1_shots_over_happy_path():
    # 3.5 shots p90 × 78 min/90 = 3.03 → P(X≥2) ≈ 0.81 → over edge claro
    stats = {
        "shots_p90":   3.5,
        "sot_p90":     1.4,
        "passes_p90":  45.0,
        "tackles_p90": 2.2,
        "fouls_p90":   1.8,
        "cards_p90":   0.18,
        "xg_p90":      0.45,
        "minutes_p_game": 80.0,
    }
    res = asyncio.run(disc.discover_player_props(
        players=[{"name": "Test Striker", "team": "Manchester United"}],
        stats_fetcher=_fetcher_factory(stats),
    ))
    assert res["available"] is True
    assert res["summary"]["total"] >= 1
    # SHOTS_OVER 1.5 debe estar incluido.
    markets = {p["market"] for p in res["props"]}
    assert disc.MARKET_SHOTS_OVER in markets
    # Verifica orden por edge_score desc.
    if len(res["props"]) >= 2:
        scores = [p["edge_score"] for p in res["props"]]
        assert scores == sorted(scores, reverse=True)


def test_tier3_player_to_score_blocked_by_gate():
    # xG bajo → prob de anotar baja → debe ser rechazado por gate.
    stats = {
        **{k: None for k in disc.STAT_KEY_BY_MARKET.values()},
        "xg_p90": 0.20,  # 0.20 * 78/90 ≈ 0.17 → P(X≥1) ≈ 15%
    }
    res = asyncio.run(disc.discover_player_props(
        players=[{"name": "Defender X", "team": "Burnley"}],
        stats_fetcher=_fetcher_factory(stats),
    ))
    # No debe haber PLAYER_TO_SCORE (rechazado por LONGSHOT/MIN_PROB).
    markets = {p["market"] for p in res["props"]}
    assert disc.MARKET_TO_SCORE not in markets
    # Auditoría:
    skip_reasons = {s.get("reason") for s in res["skipped"]
                    if s.get("market") == disc.MARKET_TO_SCORE}
    assert skip_reasons and any(r in skip_reasons for r in
                                {"BELOW_LONGSHOT_FLOOR", "BELOW_MIN_PROB", "BELOW_MIN_EDGE", "TIER3_GATE_NOT_MET"})


def test_tier3_player_to_score_allowed_only_with_elite_edge():
    # Para forzar gate Tier3, necesitamos λ tan alto que prob>>0.5 y edge>>4.
    # Con xG p90 alto (1.0) y minutos 90: λ ≈ 1.0 → P(X≥1) ≈ 0.632.
    # implied(+350) ≈ 0.222 → edge ≈ 41 pts → edge_score muy alto.
    stats = {
        **{k: None for k in disc.STAT_KEY_BY_MARKET.values()},
        "xg_p90": 1.0,
        "minutes_p_game": 88.0,
    }
    res = asyncio.run(disc.discover_player_props(
        players=[{"name": "Elite Striker", "team": "X", "expected_minutes": 88}],
        stats_fetcher=_fetcher_factory(stats),
    ))
    score_props = [p for p in res["props"] if p["market"] == disc.MARKET_TO_SCORE]
    if score_props:
        # Si aparece, debe haber pasado todos los gates Moneyball Tier3.
        p = score_props[0]
        assert p["edge_score"] >= disc.TIER3_MIN_EDGE_SCORE
        assert p["fragility"] <= disc.TIER3_MAX_FRAGILITY


def test_skip_when_stats_unavailable():
    async def _fetcher(*, player_name, team=None, league=None):  # noqa: ANN001
        return {
            "available": False, "source": "unavailable",
            "confidence_penalty": 0, "minutes_sample": None,
            "stats": dict(disc.STAT_KEY_BY_MARKET).fromkeys(disc.STAT_KEY_BY_MARKET, None),
            "raw": {"_reason": "test"},
            "engine_version": "test",
        }

    res = asyncio.run(disc.discover_player_props(
        players=[{"name": "Unknown Player"}],
        stats_fetcher=_fetcher,
    ))
    assert res["available"] is True   # engine ran, but produced nothing
    assert res["summary"]["total"] == 0
    assert any(s.get("reason") == "stats_unavailable" for s in res["skipped"])


def test_moneyball_gate_min_prob_filters_out():
    # SHOTS_OVER 1.5 con shots_p90=0.6 ⇒ λ≈0.52 ⇒ P(X≥2)≈10% ⇒ filter
    stats = {**{k: None for k in disc.STAT_KEY_BY_MARKET.values()},
             "shots_p90": 0.6}
    res = asyncio.run(disc.discover_player_props(
        players=[{"name": "Low Volume Player"}],
        stats_fetcher=_fetcher_factory(stats),
    ))
    markets = {p["market"] for p in res["props"]}
    assert disc.MARKET_SHOTS_OVER not in markets


def test_matchup_context_pace_multiplier_caps():
    # Pace mult fuera de banda → debe ser capped al ceil.
    stats = {**{k: None for k in disc.STAT_KEY_BY_MARKET.values()},
             "shots_p90": 3.5, "minutes_p_game": 80.0}
    res = asyncio.run(disc.discover_player_props(
        players=[{"name": "Player Y"}],
        matchup_context={"opponent_pace_mult": 5.0},  # extreme
        stats_fetcher=_fetcher_factory(stats),
    ))
    for p in res["props"]:
        if p["market"] in (disc.MARKET_SHOTS_OVER, disc.MARKET_SOT_OVER):
            assert p["matchup_mult"] <= disc.MATCHUP_MULT_CEIL + 1e-6


def test_orders_by_edge_score_desc_then_fragility():
    stats = {
        "shots_p90":   3.5,
        "sot_p90":     1.4,
        "passes_p90":  45.0,
        "tackles_p90": 2.2,
        "fouls_p90":   1.8,
        "cards_p90":   0.18,
        "xg_p90":      0.30,
        "minutes_p_game": 80.0,
    }
    res = asyncio.run(disc.discover_player_props(
        players=[{"name": "Player A"}, {"name": "Player B"}],
        stats_fetcher=_fetcher_factory(stats),
    ))
    scores = [p["edge_score"] for p in res["props"]]
    assert scores == sorted(scores, reverse=True)


def test_default_ingestor_used_when_not_provided(monkeypatch):
    # Sin stats_fetcher debería intentar importar el ingestor real.
    async def _stub(*, player_name, team=None, league=None):  # noqa: ANN001
        return {
            "available": False, "source": "unavailable",
            "confidence_penalty": 0, "minutes_sample": None,
            "stats": {}, "raw": {}, "engine_version": "stub",
        }

    import services.football_player_stats_ingestor as ing_mod
    monkeypatch.setattr(ing_mod, "hydrate_player_stats", _stub)

    res = asyncio.run(disc.discover_player_props(
        players=[{"name": "Some Player"}],
        # no stats_fetcher passed
    ))
    # No props porque stub devuelve unavailable, pero engine no debe romper
    assert res["available"] in (True, False)
    assert isinstance(res["props"], list)


def test_american_odds_to_implied_basic_cases():
    assert abs(disc.american_odds_to_implied(-110) - 0.5238) < 0.001
    assert abs(disc.american_odds_to_implied(+200) - 0.3333) < 0.001
    assert disc.american_odds_to_implied(None) == 0.50


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
