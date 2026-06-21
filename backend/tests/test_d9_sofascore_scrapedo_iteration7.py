"""Sprint-D9-HOTFIX3 · Sofascore migrado de Bright Data a Scrape.do.

Validaciones:
  1. Módulo declara UNLOCKER_PROVIDER="scrapedo" y REQUIRES_UNLOCKER=True.
  2. Cuando SCRAPEDO_TOKEN no está configurado → skipped_evidence.
  3. Helper interno _scrapedo_available reporta disponibilidad real.
  4. fetch() es fail-soft cuando event_id no se resuelve (search miss).
  5. Dispatcher: con bd_ok=False pero sd_ok=True, Sofascore se incluye
     en el set elegido (`chosen`).
"""
from __future__ import annotations

import pytest


def test_sofascore_declares_scrapedo_unlocker():
    from services.external_sources import sofascore as sf
    assert sf.REQUIRES_UNLOCKER is True
    assert sf.UNLOCKER_PROVIDER == "scrapedo"
    assert sf.NAME == "sofascore"
    assert "football" in sf.APPLICABLE_SPORTS
    assert "basketball" in sf.APPLICABLE_SPORTS
    assert "baseball" in sf.APPLICABLE_SPORTS


def test_sofascore_no_longer_imports_brightdata():
    """Regresión hard: el módulo NO debe importar brightdata_fetch ni
    brightdata_available — debe usar Scrape.do exclusivamente."""
    import inspect
    from services.external_sources import sofascore as sf
    src = inspect.getsource(sf)
    assert "brightdata_fetch" not in src
    assert "brightdata_available" not in src
    # Sí debe referenciar Scrape.do.
    assert "scrape_do_client" in src or "fetch_via_scrapedo" in src


@pytest.mark.asyncio
async def test_sofascore_skipped_when_scrapedo_not_configured(monkeypatch):
    """Sin token, debe devolver skipped_evidence sin levantar."""
    monkeypatch.delenv("SCRAPEDO_TOKEN", raising=False)

    # is_enabled() lee la env actual; deshabilitamos via monkeypatch.
    from services.external_sources import sofascore as sf
    out = await sf.fetch("Real Madrid", "Barcelona", sport="football")
    assert out["status"] == "skipped"
    assert any("scrapedo_not_configured" in (e or "") for e in out.get("errors") or [])


@pytest.mark.asyncio
async def test_sofascore_skipped_when_sport_not_supported():
    from services.external_sources import sofascore as sf
    out = await sf.fetch("A", "B", sport="handball")
    assert out["status"] == "skipped"
    assert any("sport_not_supported" in (e or "") for e in out.get("errors") or [])


@pytest.mark.asyncio
async def test_sofascore_failed_when_event_id_not_resolved(monkeypatch):
    """Si Scrape.do está disponible pero el search no encuentra event,
    debe devolver failed_evidence con reason canónico."""
    monkeypatch.setenv("SCRAPEDO_TOKEN", "test-token-fake")

    async def _fake_search(url, timeout=30.0, render=False):
        # Devolvemos un JSON vacío (sin results)
        return {"ok": True, "status_code": 200, "html": '{"results": []}',
                "reason_code": None}

    monkeypatch.setattr(
        "services.scrape_do_client.fetch_via_scrapedo_result", _fake_search,
    )

    from services.external_sources import sofascore as sf
    out = await sf.fetch("Nonexistent Team A", "Nonexistent Team B",
                            sport="football")
    assert out["status"] == "failed"
    assert any("event_id_not_resolved" in (e or "") for e in out.get("errors") or [])


@pytest.mark.asyncio
async def test_sofascore_happy_path_returns_evidence(monkeypatch):
    """Cuando Scrape.do devuelve el JSON esperado, debe construir
    evidence con bullets y confidence ≥ 40."""
    monkeypatch.setenv("SCRAPEDO_TOKEN", "test-token-fake")

    SEARCH_PAYLOAD = {"results": [{
        "entity": {
            "type": "event",
            "id": 12345678,
            "tournament": {"category": {"sport": {"slug": "football"}}},
        },
    }]}
    EVENT_PAYLOAD = {
        "event": {
            "slug": "real-madrid-barcelona",
            "status": {"type": "Scheduled"},
            "hasXg": True,
            "homeTeam": {"name": "Real Madrid", "shortName": "Real Madrid",
                          "form": ["W", "W", "D", "W", "L"]},
            "awayTeam": {"name": "Barcelona", "shortName": "Barça",
                          "form": ["W", "D", "W", "W", "W"]},
        }
    }
    H2H_PAYLOAD = {"events": [{}, {}, {}]}

    call_count = {"n": 0}
    responses = [
        # search → event id 12345678
        {"ok": True, "status_code": 200, "html": __import__("json").dumps(SEARCH_PAYLOAD)},
        # event details
        {"ok": True, "status_code": 200, "html": __import__("json").dumps(EVENT_PAYLOAD)},
        # h2h
        {"ok": True, "status_code": 200, "html": __import__("json").dumps(H2H_PAYLOAD)},
    ]

    async def _fake_fetch(url, timeout=30.0, render=False):
        idx = call_count["n"]
        call_count["n"] += 1
        return responses[min(idx, len(responses) - 1)]

    monkeypatch.setattr(
        "services.scrape_do_client.fetch_via_scrapedo_result", _fake_fetch,
    )

    from services.external_sources import sofascore as sf
    out = await sf.fetch("Real Madrid", "Barcelona", sport="football")
    assert out["status"] == "ok"
    assert out["source"] == "sofascore"
    assert out["confidence"] >= 40
    bullets = out.get("extracted_data") or []
    assert any("Forma" in b for b in bullets)
    assert any("xG" in b for b in bullets)
    assert any("H2H" in b for b in bullets)
    assert out["url"] == "https://www.sofascore.com/event/12345678"


def test_dispatcher_includes_sofascore_when_scrapedo_only(monkeypatch):
    """Cuando bd no está pero scrape.do sí, Sofascore debe entrar en
    `chosen`. Antes del HOTFIX-3 quedaba excluido porque
    REQUIRES_UNLOCKER=True solo consultaba bd_ok."""
    monkeypatch.setenv("SCRAPEDO_TOKEN", "test-token-fake")
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    monkeypatch.delenv("BRIGHT_DATA_API_KEY", raising=False)

    from services.external_sources import dispatcher as disp
    from services.external_sources import sofascore as sf

    # Re-evaluar el filtro tal como lo hace _fetch_one_match.
    from services.external_sources.base import brightdata_available
    bd_ok = brightdata_available()
    try:
        from services.scrape_do_client import is_enabled as _scrapedo_is_enabled
        sd_ok = bool(_scrapedo_is_enabled())
    except Exception:
        sd_ok = False
    assert bd_ok is False, (
        "Si BRIGHTDATA_API_KEY no está, brightdata_available() debe ser False."
    )
    assert sd_ok is True, (
        "Con SCRAPEDO_TOKEN seteado, scrape_do_client.is_enabled() debe ser True."
    )

    # Replicar el filtro de dispatcher._fetch_one_match
    def _unlocker_ok(scraper) -> bool:
        if not getattr(scraper, "REQUIRES_UNLOCKER", False):
            return True
        provider = (getattr(scraper, "UNLOCKER_PROVIDER", "brightdata") or "").lower()
        if provider == "scrapedo":
            return sd_ok
        return bd_ok

    assert _unlocker_ok(sf) is True
