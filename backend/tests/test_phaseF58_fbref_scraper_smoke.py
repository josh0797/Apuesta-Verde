"""Phase F58 — FBref scraper + smart-merge smoke tests.

Cubre:
  * Parser del enlace de búsqueda de FBref.
  * Parser de la tabla "Standard Stats" con ``data-stat``.
  * Smart-merge StatMuse parcial + FBref enriquecimiento.
  * Fallback FBref cuando StatMuse falla totalmente.
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


# ─────────────────────────────────────────────────────────────────────
# Search-result link parser
# ─────────────────────────────────────────────────────────────────────
def test_fbref_link_parser_extracts_first_player():
    html = """
    <html><body>
      <div class="search-item-name">
        <a href="/en/players/1f44ac21/Erling-Haaland">Erling Haaland</a>
      </div>
      <div class="search-item-name">
        <a href="/en/players/abc12345/Other-Player">Other Player</a>
      </div>
    </body></html>
    """
    href = ing._parse_fbref_player_link(html)
    assert href == "/en/players/1f44ac21/Erling-Haaland"


def test_fbref_link_parser_skips_scout_and_squad_urls():
    html = """
    <a href="/en/players/1f44ac21/scout/365_m1/Haaland-Scouting-Report">scout</a>
    <a href="/en/squads/b8fd03ef/Manchester-City-Stats">squad</a>
    <a href="/en/players/1f44ac21/Erling-Haaland">player</a>
    """
    href = ing._parse_fbref_player_link(html)
    assert href == "/en/players/1f44ac21/Erling-Haaland"


def test_fbref_link_parser_empty_when_no_match():
    assert ing._parse_fbref_player_link("") is None
    assert ing._parse_fbref_player_link("<html><body>no links</body></html>") is None


# ─────────────────────────────────────────────────────────────────────
# Standard-stats table parser
# ─────────────────────────────────────────────────────────────────────
def _fbref_table_html():
    return """
    <html><body>
      <table id="stats_standard_dom_lg">
        <thead>
          <tr><th>Season</th><th>Comp</th><th>Min</th></tr>
        </thead>
        <tbody>
          <tr>
            <th data-stat="season">2024-25</th>
            <td data-stat="comp_level">Premier League</td>
            <td data-stat="games">28</td>
            <td data-stat="minutes">2500</td>
            <td data-stat="goals">22</td>
            <td data-stat="shots">110</td>
            <td data-stat="shots_on_target">52</td>
            <td data-stat="passes_completed">680</td>
            <td data-stat="tackles">35</td>
            <td data-stat="fouls">28</td>
            <td data-stat="cards_yellow">4</td>
            <td data-stat="xg">20.5</td>
          </tr>
          <tr>
            <th data-stat="season">2023-24</th>
            <td data-stat="comp_level">Champions Lg</td>
            <td data-stat="games">10</td>
            <td data-stat="minutes">900</td>
            <td data-stat="goals">8</td>
            <td data-stat="shots">35</td>
            <td data-stat="shots_on_target">15</td>
            <td data-stat="passes_completed">220</td>
            <td data-stat="tackles">7</td>
            <td data-stat="fouls">6</td>
            <td data-stat="cards_yellow">1</td>
            <td data-stat="xg">7.0</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """


def test_fbref_standard_stats_parser_picks_domestic_with_more_minutes():
    out = ing._parse_fbref_standard_stats(_fbref_table_html())
    assert out is not None
    # Premier League row has more minutes → picked
    assert out["minutes"] == 2500
    assert out["shots"] == 110
    assert out["sot"] == 52
    assert out["passes"] == 680
    assert out["tackles"] == 35
    assert out["fouls"] == 28
    assert out["cards"] == 4
    assert out["xg"] == 20.5
    assert out["matches"] == 28
    assert "Premier League" in (out["_comp"] or "")


def test_fbref_standard_stats_parser_empty_when_no_table():
    assert ing._parse_fbref_standard_stats("") is None
    assert ing._parse_fbref_standard_stats("<html><body><p>no table</p></body></html>") is None


def test_fbref_standard_stats_parser_handles_thousands_separator():
    html = """
    <table id="stats_standard_dom_lg">
      <tbody>
        <tr>
          <td data-stat="comp_level">La Liga</td>
          <td data-stat="games">30</td>
          <td data-stat="minutes">2,650</td>
          <td data-stat="shots">95</td>
          <td data-stat="shots_on_target">38</td>
          <td data-stat="passes_completed">1,250</td>
        </tr>
      </tbody>
    </table>
    """
    out = ing._parse_fbref_standard_stats(html)
    assert out["minutes"] == 2650
    assert out["passes"] == 1250
    assert out["shots"] == 95


# ─────────────────────────────────────────────────────────────────────
# Hydrator chain: StatMuse partial + FBref enrichment (smart merge)
# ─────────────────────────────────────────────────────────────────────
def test_hydrator_merges_statmuse_partial_with_fbref(monkeypatch):
    async def _statmuse(_name):  # noqa: ANN001
        return {
            "source":         "statmuse",
            "minutes_sample": 2200,
            "stats": {
                **ing._EMPTY_STATS,
                "shots_p90":  2.5,
                "sot_p90":    1.0,
                # passes / tackles / fouls / cards / xg all None
            },
            "raw": {"sm": True},
        }

    async def _fbref(_name):  # noqa: ANN001
        return {
            "source":         "fbref",
            "minutes_sample": 2500,
            "stats": {
                **ing._EMPTY_STATS,
                "shots_p90":   3.9,        # would lose to StatMuse (already present)
                "sot_p90":     1.8,        # would lose
                "passes_p90":  24.5,
                "tackles_p90": 1.3,
                "fouls_p90":   1.0,
                "cards_p90":   0.15,
                "xg_p90":      0.74,
            },
            "raw": {"fb": True},
        }

    async def _understat_never(_name, _league):  # noqa: ANN001
        raise AssertionError("understat must not be called when statmuse+fbref ok")

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _statmuse)
    monkeypatch.setattr(ing, "_fetch_fbref_player", _fbref)
    monkeypatch.setattr(ing, "_fetch_understat_player", _understat_never)

    res = asyncio.run(ing.hydrate_player_stats(player_name="Test"))
    assert res["available"] is True
    assert res["source"] == "statmuse+fbref"
    # StatMuse values must be preserved (NOT overwritten by FBref).
    assert res["stats"]["shots_p90"] == 2.5
    assert res["stats"]["sot_p90"]   == 1.0
    # FBref fills in the missing fields.
    assert res["stats"]["passes_p90"]  == 24.5
    assert res["stats"]["tackles_p90"] == 1.3
    assert res["stats"]["xg_p90"]      == 0.74
    # Minutes_sample = max(statmuse, fbref).
    assert res["minutes_sample"] == 2500
    # Raw block carries both sources.
    assert "statmuse" in res["raw"] and "fbref" in res["raw"]


def test_hydrator_uses_fbref_when_statmuse_fails(monkeypatch):
    async def _statmuse(_name):  # noqa: ANN001
        return None

    async def _fbref(_name):  # noqa: ANN001
        return {
            "source":         "fbref",
            "minutes_sample": 2100,
            "stats": {
                **ing._EMPTY_STATS,
                "shots_p90":   3.2,
                "passes_p90":  22.0,
                "tackles_p90": 1.6,
            },
            "raw": {},
        }

    async def _us(_name, _league):  # noqa: ANN001
        raise AssertionError("understat must not be called when fbref ok")

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _statmuse)
    monkeypatch.setattr(ing, "_fetch_fbref_player", _fbref)
    monkeypatch.setattr(ing, "_fetch_understat_player", _us)

    res = asyncio.run(ing.hydrate_player_stats(player_name="Test"))
    assert res["available"] is True
    assert res["source"] == "fbref"
    assert res["stats"]["shots_p90"] == 3.2
    # Penalty should be at least PENALTY_FALLBACK.
    assert res["confidence_penalty"] >= ing.PENALTY_FALLBACK


def test_hydrator_falls_to_understat_when_first_two_fail(monkeypatch):
    async def _statmuse(_name):  # noqa: ANN001
        return None

    async def _fbref(_name):  # noqa: ANN001
        return None

    async def _us(_name, _league):  # noqa: ANN001
        return {
            "source":         "understat",
            "minutes_sample": 1800,
            "stats":          {**ing._EMPTY_STATS, "xg_p90": 0.55, "shots_p90": 3.0},
            "raw":            {"us": True},
        }

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _statmuse)
    monkeypatch.setattr(ing, "_fetch_fbref_player", _fbref)
    monkeypatch.setattr(ing, "_fetch_understat_player", _us)

    res = asyncio.run(ing.hydrate_player_stats(player_name="Test", league="EPL"))
    assert res["available"] is True
    assert res["source"] == "understat"
    assert res["stats"]["xg_p90"] == 0.55


def test_hydrator_all_three_fail_returns_unavailable(monkeypatch):
    async def _none(_a, *args, **kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _none)
    monkeypatch.setattr(ing, "_fetch_fbref_player", _none)
    monkeypatch.setattr(ing, "_fetch_understat_player", _none)

    res = asyncio.run(ing.hydrate_player_stats(player_name="Test"))
    assert res["available"] is False
    assert res["source"] == "unavailable"
    assert res["raw"]["_reason"] == "all_sources_failed"


def test_hydrator_does_not_call_fbref_when_statmuse_complete(monkeypatch):
    # When StatMuse already returns a fully-populated stats dict,
    # FBref should NOT be queried (saves bandwidth + politeness).
    complete_stats = {
        "shots_p90":      3.0,
        "sot_p90":        1.2,
        "passes_p90":     30.0,
        "tackles_p90":    1.5,
        "fouls_p90":      1.0,
        "cards_p90":      0.2,
        "xg_p90":         0.5,
        "minutes_p_game": 80.0,
    }

    async def _statmuse(_name):  # noqa: ANN001
        return {
            "source":         "statmuse",
            "minutes_sample": 2200,
            "stats":          complete_stats,
            "raw":            {},
        }

    called = {"fbref": False}

    async def _fbref(_name):  # noqa: ANN001
        called["fbref"] = True
        return None

    monkeypatch.setattr(ing, "_fetch_statmuse_player", _statmuse)
    monkeypatch.setattr(ing, "_fetch_fbref_player", _fbref)

    res = asyncio.run(ing.hydrate_player_stats(player_name="Complete Player"))
    assert res["available"] is True
    assert res["source"] == "statmuse"
    assert called["fbref"] is False  # ← key assertion


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
