"""Sprint-D9-HOTFIX2 · Validación post-deploy iteration 5:
API-Football desactivada definitivamente.

Verificaciones:
  1. ``discover_priority_fixtures`` con ENABLE_API_FOOTBALL_FALLBACK=false
     NO llama a API-Football → no hay timeout cuando la cuenta está
     suspendida.
  2. Cuando `matched_by_id_count == 0` Y `matched_by_name > 0` Y
     API-Football off, los matches by-name se MANTIENEN como
     best-effort (mejor que devolver 0).
  3. Cuando `matched_by_id_count == 0` Y `matched_by_name == 0` Y
     API-Football off, ``discovered`` queda vacío sin levantar.
"""
from __future__ import annotations

import pytest

from services import data_ingestion as di


# ─────────────────────────────────────────────────────────────────────
# Helpers (reusa shape de tests previos)
# ─────────────────────────────────────────────────────────────────────
def _fake_apifootball_fixture(home, away, *, fid, league_id, league_name,
                                  date_iso="2026-06-23T15:00:00Z",
                                  status_short="NS"):
    return {
        "fixture": {
            "id":        fid,
            "date":      date_iso,
            "timestamp": 1782255600,
            "status":    {"short": status_short},
        },
        "teams": {
            "home": {"id": 1, "name": home},
            "away": {"id": 2, "name": away},
        },
        "league": {"id": league_id, "name": league_name, "season": 2025},
    }


class _FakeClient:
    pass


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_priority_discovery_skips_api_football_when_flag_off(monkeypatch):
    """Con ENABLE_API_FOOTBALL_FALLBACK=false:
    * `af.fixtures_by_date` NO se invoca (cero timeouts).
    * Los matches by-name se conservan como best-effort.
    """
    monkeypatch.setenv("ENABLE_API_FOOTBALL_FALLBACK", "false")

    # Cascada: TheSportsDB devuelve FIFA World Cup (matcheable por nombre
    # pero no por ID canónico de API-Football).
    tsdb_fixtures = [
        _fake_apifootball_fixture(
            "Uruguay", "Cape Verde", fid=2463160,
            league_id=4429,                # ID exótico TheSportsDB
            league_name="FIFA World Cup",
        ),
        _fake_apifootball_fixture(
            "New Zealand", "Egypt", fid=2463161,
            league_id=4429, league_name="FIFA World Cup",
        ),
    ]
    async def _tsdb(client):
        return tsdb_fixtures, []
    monkeypatch.setattr(
        "services.external_sources.thesportsdb_fixtures_adapter.fetch_fixtures_next_48h",
        _tsdb,
    )

    # Otras fuentes: vacías para forzar el path matched_by_id_count==0.
    async def _empty(*args, **kwargs):
        return [], []
    monkeypatch.setattr(
        "services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
        _empty,
    )
    async def _empty_espn(client):
        return []
    monkeypatch.setattr(
        "services.fallback_scraper.espn_soccer_scoreboard", _empty_espn,
    )
    async def _empty_sofa():
        return []
    monkeypatch.setattr(
        "services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
        _empty_sofa,
    )

    # API-Football: si se llamara, marcamos falla del test.
    af_called = {"count": 0}
    async def _af_should_not_be_called(client, date):
        af_called["count"] += 1
        return []
    monkeypatch.setattr(
        "services.api_football.fixtures_by_date", _af_should_not_be_called,
    )
    async def _af48_should_not_be_called(client):
        af_called["count"] += 1
        return []
    monkeypatch.setattr(
        "services.api_football.fixtures_next_48h", _af48_should_not_be_called,
    )

    # Bypass status/window filtering — el shape tiene status="NS" pero el
    # kickoff podría caer fuera de los próximos 48h en el test runner.
    # Trampita: usamos una ventana muy amplia.
    fixtures = await di.discover_priority_fixtures(
        _FakeClient(), None, window_hours=24 * 365,
    )

    # API-Football NO debe haber sido invocada en NINGÚN momento.
    assert af_called["count"] == 0, (
        f"API-Football was called {af_called['count']} times but should "
        "be skipped when ENABLE_API_FOOTBALL_FALLBACK=false"
    )
    # Los matches by-name (FIFA World Cup) deben conservarse como
    # best-effort.
    assert len(fixtures) >= 1


@pytest.mark.asyncio
async def test_priority_discovery_returns_empty_when_no_matches_at_all(monkeypatch):
    """Cuando ninguna fuente devuelve fixtures, ``discovered=[]``
    sin levantar exception y sin llamar a API-Football."""
    monkeypatch.setenv("ENABLE_API_FOOTBALL_FALLBACK", "false")

    async def _empty(*args, **kwargs):
        # Algunas fuentes devuelven (list, audit), otras solo list.
        return [], []
    async def _empty_list(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "services.external_sources.thesportsdb_fixtures_adapter.fetch_fixtures_next_48h",
        _empty,
    )
    monkeypatch.setattr(
        "services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
        _empty,
    )
    monkeypatch.setattr(
        "services.fallback_scraper.espn_soccer_scoreboard", _empty_list,
    )
    monkeypatch.setattr(
        "services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
        _empty_list,
    )

    af_called = {"count": 0}
    async def _af_spy(client, date):
        af_called["count"] += 1
        return []
    monkeypatch.setattr(
        "services.api_football.fixtures_by_date", _af_spy,
    )

    fixtures = await di.discover_priority_fixtures(
        _FakeClient(), None, window_hours=48,
    )
    assert fixtures == []
    assert af_called["count"] == 0


def test_env_flag_default_now_disables_api_football_via_dotenv(monkeypatch):
    """Como el .env tiene ENABLE_API_FOOTBALL_FALLBACK=false, la helper
    interna debe devolver False sin necesidad de monkey-patch."""
    # El autouse conftest fixture habilita el flag por default para no
    # romper los ~1000 tests legacy. Aquí desactivamos el override para
    # leer el valor REAL del .env de producción.
    import os
    monkeypatch.delenv("ENABLE_API_FOOTBALL_FALLBACK", raising=False)
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env", override=True)
    raw = os.environ.get("ENABLE_API_FOOTBALL_FALLBACK")
    assert raw is not None, (
        "ENABLE_API_FOOTBALL_FALLBACK debe estar definida en .env "
        "(decisión usuario: API-Football desactivada definitivamente)"
    )
    assert raw.strip().lower() in ("false", "0", "no", "off")
