"""Phase F58 smoke tests — Football Player Stats Ingestor.

Cubre el contrato fail-soft, parsing, cache y fallback Understat.
Todos los tests inyectan stubs vía monkeypatch para no salir a internet.
"""
from __future__ import annotations

import asyncio

import pytest

from services import football_player_stats_ingestor as ing


@pytest.fixture(autouse=True)
def _clean_cache():
    ing.cache_clear()
    yield
    ing.cache_clear()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if not asyncio.iscoroutine(coro) else asyncio.run(coro)


def test_ingestor_invalid_player_name_failsoft():
    res = asyncio.run(ing.hydrate_player_stats(player_name=""))
    assert res["available"] is False
    assert res["source"] == "unavailable"
    assert res["stats"] == ing._EMPTY_STATS


def test_ingestor_returns_empty_when_all_sources_fail(monkeypatch):
    async def _fail_primary(_name):  # noqa: ANN001
        return None

    async def _fail_fallback(_name, _league):  # noqa: ANN001
        return None

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _fail_primary)
    monkeypatch.setattr(ing, "_fetch_understat_player", _fail_fallback)

    res = asyncio.run(ing.hydrate_player_stats(player_name="Test Player"))
    assert res["available"] is False
    assert res["source"] == "unavailable"
    assert res["confidence_penalty"] == 0
    assert res["raw"]["_reason"] == "all_sources_failed"


def test_ingestor_uses_primary_statmuse(monkeypatch):
    async def _ok_primary(_name):  # noqa: ANN001
        return {
            "source": "statmuse",
            "minutes_sample": 1800,
            "stats": {
                **ing._EMPTY_STATS,
                "shots_p90":   2.5,
                "sot_p90":     1.0,
                "passes_p90":  42.0,
                "tackles_p90": 1.8,
                "xg_p90":      0.35,
                "minutes_p_game": 80.0,
            },
            "raw": {"totals": {"shots": 50}},
        }

    async def _never_call(_name, _league):  # noqa: ANN001
        raise AssertionError("fallback should not be invoked when primary OK")

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _ok_primary)
    monkeypatch.setattr(ing, "_fetch_understat_player", _never_call)

    res = asyncio.run(ing.hydrate_player_stats(player_name="Bruno Fernandes"))
    assert res["available"] is True
    assert res["source"] == "statmuse"
    assert res["confidence_penalty"] == 0
    assert res["stats"]["shots_p90"] == 2.5
    assert res["minutes_sample"] == 1800


def test_ingestor_falls_back_to_understat(monkeypatch):
    async def _fail_primary(_name):  # noqa: ANN001
        return None

    async def _ok_fallback(_name, _league):  # noqa: ANN001
        return {
            "source": "understat",
            "minutes_sample": 1200,
            "stats": {**ing._EMPTY_STATS, "xg_p90": 0.45, "shots_p90": 3.1},
            "raw": {"understat": {"xg": 6.0}},
        }

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _fail_primary)
    monkeypatch.setattr(ing, "_fetch_understat_player", _ok_fallback)

    res = asyncio.run(ing.hydrate_player_stats(player_name="Marcus Rashford"))
    assert res["available"] is True
    assert res["source"] == "understat"
    # Fallback debe tener penalty >= PENALTY_FALLBACK
    assert res["confidence_penalty"] >= ing.PENALTY_FALLBACK
    assert res["stats"]["xg_p90"] == 0.45


def test_ingestor_cache_hits(monkeypatch):
    call_count = {"n": 0}

    async def _ok_primary(_name):  # noqa: ANN001
        call_count["n"] += 1
        return {
            "source": "statmuse",
            "minutes_sample": 1800,
            "stats": {**ing._EMPTY_STATS, "shots_p90": 2.0},
            "raw": {},
        }

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _ok_primary)

    # Primer call → fetch real
    r1 = asyncio.run(ing.hydrate_player_stats(player_name="Player X", league="EPL"))
    # Segundo call → debe venir de cache
    r2 = asyncio.run(ing.hydrate_player_stats(player_name="Player X", league="EPL"))
    assert call_count["n"] == 1
    assert r1["stats"]["shots_p90"] == r2["stats"]["shots_p90"]


def test_to_per90_scaling_with_minutes():
    # 50 shots en 1800 min → 50 * (90/1800) = 2.5
    out = ing._to_per90({"shots": 50, "minutes": 1800, "matches": 20})
    assert out["stats"]["shots_p90"] == 2.5
    assert out["minutes_sample"] == 1800
    assert out["stats"]["minutes_p_game"] == 90.0


def test_to_per90_when_already_per90_values():
    # minutes muy bajos ⇒ asumimos que ya son tasas per-90
    out = ing._to_per90({"shots": 2.3, "minutes": 0})
    assert out["stats"]["shots_p90"] == 2.3


def test_parse_statmuse_player_html_extracts_table():
    html = """
    <html><body><table>
      <tr><th>Shots</th><th>SoT</th><th>Min</th><th>MP</th></tr>
      <tr><td>40</td><td>15</td><td>1800</td><td>20</td></tr>
    </table></body></html>
    """
    totals = ing._parse_statmuse_player_html(html)
    assert totals.get("shots") == 40
    assert totals.get("sot") == 15
    assert totals.get("minutes") == 1800
    assert totals.get("matches") == 20


def test_parse_statmuse_player_html_empty_when_no_table():
    assert ing._parse_statmuse_player_html("<html></html>") == {}
    assert ing._parse_statmuse_player_html("") == {}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
