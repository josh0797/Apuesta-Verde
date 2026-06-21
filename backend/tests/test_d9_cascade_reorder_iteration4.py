"""Sprint-D9-cascade-reorder · Tests para el nuevo orden de la cascada
de discovery de fútbol.

Nuevo orden (decisión del usuario):
    TheSportsDB → TheStatsAPI → ESPN → Sofascore → API-Football

Objetivos:
  * Validar que ESPN es invocada y puede ganar la cascada ANTES que
    API-Football cuando TheSportsDB/TheStatsAPI no devuelven viables.
  * Validar que Sofascore se invoca ANTES que API-Football.
  * Validar que API-Football sigue siendo el último fallback de pago.
  * Validar que el merge priority quede en el nuevo orden.

Los tests usan monkey-patching para no pegarle a ningún servicio externo.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services import data_ingestion as di


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _fake_apifootball_fixture(home: str, away: str, *, fid: int = 1,
                                date_iso: str = "2026-01-15T15:00:00Z",
                                status_short: str = "NS",
                                league_name: str = "Test League",
                                league_id: int = 999) -> dict:
    """Construye un fixture con el shape canónico API-Football."""
    return {
        "fixture": {
            "id":        fid,
            "date":      date_iso,
            "timestamp": 1736953200,
            "status":    {"short": status_short, "long": "Not Started"},
        },
        "teams": {
            "home": {"id": 1, "name": home},
            "away": {"id": 2, "name": away},
        },
        "league": {"id": league_id, "name": league_name, "season": 2025},
    }


class _FakeClient:
    """Dummy httpx.AsyncClient stand-in. No method is invoked because
    we monkey-patch all source adapters."""
    pass


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────
def test_merge_priority_reflects_new_order():
    """El nuevo orden debe poner ESPN y Sofascore ANTES que API-Football."""
    priority = di._F87_MERGE_PRIORITY
    # Posiciones esperadas:
    assert priority[0] == "thesportsdb"
    assert priority[1] == "thestatsapi"
    assert priority[2] == "espn"
    assert priority[3] == "sofascore_pw"
    assert priority[4] == "api_football"
    # ESPN debe venir ANTES que api_football
    assert priority.index("espn") < priority.index("api_football")
    # Sofascore debe venir ANTES que api_football
    assert priority.index("sofascore_pw") < priority.index("api_football")


@pytest.mark.asyncio
async def test_espn_short_circuits_when_viable_and_thesportsdb_empty(monkeypatch):
    """Cuando TheSportsDB y TheStatsAPI no devuelven viables, ESPN debe
    ser invocada Y cortocircuitar la cascada SIN llegar a API-Football."""

    # TheSportsDB: vacío
    async def _empty_tsdb(client):
        return [], []
    monkeypatch.setattr(
        "services.external_sources.thesportsdb_fixtures_adapter.fetch_fixtures_next_48h",
        _empty_tsdb,
    )
    # TheStatsAPI: vacío
    async def _empty_tsa(client):
        return [], []
    monkeypatch.setattr(
        "services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
        _empty_tsa,
    )

    # ESPN: viable (≥ 5 fixtures upcoming)
    # Para evitar dependencia del converter ESPN→APIFootball, devolvemos
    # ya el shape final y monkey-patcheamos _espn_to_apifootball_shape
    # como identidad.
    espn_payload = [
        _fake_apifootball_fixture(f"Home{i}", f"Away{i}", fid=200 + i)
        for i in range(6)
    ]
    async def _espn_ok(client):
        return espn_payload
    monkeypatch.setattr(
        "services.fallback_scraper.espn_soccer_scoreboard", _espn_ok,
    )
    monkeypatch.setattr(
        "services.data_ingestion._espn_to_apifootball_shape",
        lambda e: e,
    )

    # API-Football: si llegara aquí, marcaríamos como falla del test.
    af_called = {"count": 0}
    async def _af_should_not_be_called(client):
        af_called["count"] += 1
        return []
    monkeypatch.setattr(
        "services.api_football.fixtures_next_48h", _af_should_not_be_called,
    )

    client = _FakeClient()
    fixtures, audit = await di._discover_football_fixtures(client)

    # Sources llamadas: debe incluir thesportsdb, thestatsapi y espn.
    assert "thesportsdb" in audit["sources_called"]
    assert "thestatsapi" in audit["sources_called"]
    assert "espn" in audit["sources_called"]
    # API-Football NO debe haber sido invocada.
    assert af_called["count"] == 0
    assert "api_football" not in audit["sources_called"]
    # Primary winner debe ser ESPN.
    assert audit.get("primary_winner") == "espn"


@pytest.mark.asyncio
async def test_api_football_is_last_resort(monkeypatch):
    """Cuando ESPN y Sofascore también fallan/vacían, API-Football
    finalmente entra como último recurso."""
    # Sprint-D9-HOTFIX2: ENABLE_API_FOOTBALL_FALLBACK ahora viene en
    # false por default (.env). Para validar la pertenencia al ORDEN
    # de la cascada lo activamos explícitamente aquí.
    monkeypatch.setenv("ENABLE_API_FOOTBALL_FALLBACK", "true")

    async def _empty_tsdb(client):
        return [], []
    async def _empty_tsa(client):
        return [], []
    monkeypatch.setattr(
        "services.external_sources.thesportsdb_fixtures_adapter.fetch_fixtures_next_48h",
        _empty_tsdb,
    )
    monkeypatch.setattr(
        "services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
        _empty_tsa,
    )
    # ESPN: vacío
    async def _empty_espn(client):
        return []
    monkeypatch.setattr(
        "services.fallback_scraper.espn_soccer_scoreboard", _empty_espn,
    )
    # Sofascore PW: vacío
    async def _empty_sofa():
        return []
    monkeypatch.setattr(
        "services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
        _empty_sofa,
    )

    # API-Football: viable (raw API-Football shape)
    af_fixtures = [
        _fake_apifootball_fixture(f"Home{i}", f"Away{i}", fid=100 + i)
        for i in range(6)
    ]
    af_called = {"count": 0}
    async def _af_ok(client):
        af_called["count"] += 1
        return af_fixtures
    monkeypatch.setattr(
        "services.api_football.fixtures_next_48h", _af_ok,
    )
    # Sofascore scrape.do: cero (no debe ser viable cuando AF sí lo es)
    async def _empty_scrape():
        return []
    monkeypatch.setattr(
        "services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
        _empty_scrape,
    )

    client = _FakeClient()
    fixtures, audit = await di._discover_football_fixtures(client)

    # API-Football debe ser primary winner.
    assert af_called["count"] == 1
    assert audit.get("primary_winner") == "api_football"
    # Y se llamó al ORDEN nuevo: thesportsdb → thestatsapi → espn →
    # sofascore_pw → api_football
    sources = audit["sources_called"]
    assert sources.index("espn") < sources.index("api_football")
    assert sources.index("sofascore_pw") < sources.index("api_football")
