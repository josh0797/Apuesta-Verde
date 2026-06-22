"""
test_d9_odds_cascade_ingest_wiring
==================================

Sprint-D9 follow-up — validar que el cascade de odds (TheOddsAPI + OddsPortal)
está **realmente wireado** en el path de ingestión real
(``_ingestion_helpers/football_odds_cascade.fetch_football_odds_with_fallback``).

Antes del fix Jun-2026, el cascade D9 estaba implementado en módulos puros
y testeado, pero nunca se invocaba desde el pipeline real → las cuotas no
llegaban al motor → todos los partidos caían a ``MARKET_IDENTITY_MISSING``
y la UI mostraba "Watchlist descartado por mercado no identificado".

Reglas validadas:

- Si TheStatsAPI primary no devuelve odds y api_sports stub tampoco
  (post-F99.2 siempre), el cascade D9 se invoca como paso 4.
- Cuando D9 devuelve ``available=True``, se transforma al shape API-Sports
  y se stampa ``_odds_source`` con el ganador real
  (``odds_cascade_theoddsapi`` o ``odds_cascade_oddsportal``).
- El cascade D9 puede desactivarse con
  ``ENABLE_D9_ODDS_CASCADE_IN_INGEST=false``.
- El wiring es fail-soft (excepciones internas no rompen la ingesta).
"""

from __future__ import annotations

import pytest

from services._ingestion_helpers import football_odds_cascade as foc


# ── Helpers puros ───────────────────────────────────────────────────────────


def test_d9_cascade_enabled_flag_default_true(monkeypatch):
    monkeypatch.delenv("ENABLE_D9_ODDS_CASCADE_IN_INGEST", raising=False)
    assert foc._d9_cascade_enabled() is True


def test_d9_cascade_enabled_flag_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_D9_ODDS_CASCADE_IN_INGEST", "false")
    assert foc._d9_cascade_enabled() is False


def test_wrap_d9_result_as_api_sports_shape_produces_normalised_payload():
    """El helper debe producir un shape consumible por ``normalize_odds``."""
    d9_payload = {
        "available": True,
        "source": "the_odds_api",
        "home_odds": 2.10,
        "draw_odds": 3.30,
        "away_odds": 3.50,
        "implied_probs": {"home": 0.476, "draw": 0.303, "away": 0.286},
        "fetched_at": "2026-06-22T12:00:00Z",
        "reason_codes": ["D9_THEODDSAPI_HIT"],
    }
    odds_resp, norm = foc._wrap_d9_result_as_api_sports_shape(
        d9_payload, fid=42, home_name="Real Madrid", away_name="Barcelona"
    )

    assert isinstance(odds_resp, list) and len(odds_resp) == 1
    # bookmaker_name debe reflejar el proveedor real
    bk = odds_resp[0]["bookmakers"][0]
    assert "TheOddsAPI" in bk["name"]
    # norm_odds tiene available=True y trazas D9
    assert norm.get("available") is True
    assert norm.get("_odds_provider") == "the_odds_api"
    assert norm.get("_odds_cascade_used") == "sprint_d9"
    assert norm.get("_d9_reason_codes") == ["D9_THEODDSAPI_HIT"]
    assert norm.get("_d9_home_name_used") == "Real Madrid"
    assert norm.get("_d9_away_name_used") == "Barcelona"


def test_wrap_d9_result_oddsportal_label():
    d9_payload = {
        "available": True, "source": "oddsportal",
        "home_odds": 1.95, "draw_odds": 3.45, "away_odds": 3.80,
        "implied_probs": {}, "fetched_at": "x", "reason_codes": [],
    }
    odds_resp, norm = foc._wrap_d9_result_as_api_sports_shape(
        d9_payload, fid=1, home_name="A", away_name="B"
    )
    assert "OddsPortal" in odds_resp[0]["bookmakers"][0]["name"]
    assert norm["_odds_provider"] == "oddsportal"


# ── Integración con fetch_football_odds_with_fallback (mocked) ──────────────


@pytest.mark.asyncio
async def test_fetch_football_odds_with_fallback_invokes_d9_when_others_fail(monkeypatch):
    """
    Cuando TheStatsAPI y API-Sports stub devuelven sin odds, la cascade
    debe invocar fetch_direct_match_odds_cascade y stampar el source.
    """
    monkeypatch.setenv("ENABLE_D9_ODDS_CASCADE_IN_INGEST", "true")
    # Disable api_sports fallback path so we go straight to D9 fallback.
    monkeypatch.setenv("ENABLE_API_SPORTS_FALLBACK", "false")

    # Mock TheStatsAPI to return empty
    from services.external_sources import thestatsapi_odds_adapter as _ts_odds
    async def _ts_empty(*args, **kwargs):
        return (None, None, None)
    monkeypatch.setattr(_ts_odds, "fetch_odds_api_sports_shape", _ts_empty)

    # Mock D9 cascade to return a successful TheOddsAPI hit
    from services.external_sources import odds_cascade as _d9
    async def _d9_hit(home, away, **kwargs):
        return {
            "available": True,
            "source": "the_odds_api",
            "home_odds": 2.0, "draw_odds": 3.4, "away_odds": 3.5,
            "implied_probs": {}, "fetched_at": "x", "reason_codes": ["D9_HIT"],
        }
    monkeypatch.setattr(_d9, "fetch_direct_match_odds_cascade", _d9_hit)

    odds_resp, norm, source = await foc.fetch_football_odds_with_fallback(
        client=None,  # not used by mocks
        db=None,
        fx_raw={"fixture": {"id": 42}},
        fid=42,
        home={"name": "Argentina"},
        away={"name": "Austria"},
        kickoff="2026-06-22T20:00:00Z",
        league_name="Friendlies",
    )

    assert source == "odds_cascade_theoddsapi"
    assert norm.get("available") is True
    assert norm.get("_odds_source") == "odds_cascade_theoddsapi"
    assert norm.get("_odds_cascade_used") == "sprint_d9"


@pytest.mark.asyncio
async def test_fetch_football_odds_with_fallback_d9_disabled_returns_no_odds(monkeypatch):
    """Si ENABLE_D9_ODDS_CASCADE_IN_INGEST=false, NO se invoca D9."""
    monkeypatch.setenv("ENABLE_D9_ODDS_CASCADE_IN_INGEST", "false")
    monkeypatch.setenv("ENABLE_API_SPORTS_FALLBACK", "false")

    from services.external_sources import thestatsapi_odds_adapter as _ts_odds
    async def _ts_empty(*args, **kwargs):
        return (None, None, None)
    monkeypatch.setattr(_ts_odds, "fetch_odds_api_sports_shape", _ts_empty)

    # Espía D9 para verificar que NO se llama
    from services.external_sources import odds_cascade as _d9
    called = {"n": 0}
    async def _d9_spy(*args, **kwargs):
        called["n"] += 1
        return {"available": False, "reason_codes": []}
    monkeypatch.setattr(_d9, "fetch_direct_match_odds_cascade", _d9_spy)

    _, norm, source = await foc.fetch_football_odds_with_fallback(
        client=None, db=None, fx_raw={"fixture": {"id": 1}},
        fid=1, home={"name": "X"}, away={"name": "Y"},
        kickoff=None, league_name=None,
    )

    assert called["n"] == 0
    assert source == "no_odds"


@pytest.mark.asyncio
async def test_fetch_football_odds_with_fallback_d9_failure_is_fail_soft(monkeypatch):
    """Una excepción en el cascade D9 NO debe romper la ingesta."""
    monkeypatch.setenv("ENABLE_D9_ODDS_CASCADE_IN_INGEST", "true")
    monkeypatch.setenv("ENABLE_API_SPORTS_FALLBACK", "false")

    from services.external_sources import thestatsapi_odds_adapter as _ts_odds
    async def _ts_empty(*args, **kwargs):
        return (None, None, None)
    monkeypatch.setattr(_ts_odds, "fetch_odds_api_sports_shape", _ts_empty)

    from services.external_sources import odds_cascade as _d9
    async def _d9_boom(*args, **kwargs):
        raise RuntimeError("simulated network failure")
    monkeypatch.setattr(_d9, "fetch_direct_match_odds_cascade", _d9_boom)

    # No debe propagar la excepción
    _, norm, source = await foc.fetch_football_odds_with_fallback(
        client=None, db=None, fx_raw={"fixture": {"id": 1}},
        fid=1, home={"name": "X"}, away={"name": "Y"},
        kickoff=None, league_name=None,
    )
    assert source == "no_odds"
    assert norm.get("_odds_source") == "no_odds"


@pytest.mark.asyncio
async def test_fetch_football_odds_with_fallback_thestatsapi_wins_skips_d9(monkeypatch):
    """Cuando TheStatsAPI tiene odds, D9 NO se debe invocar (parity)."""
    monkeypatch.setenv("ENABLE_D9_ODDS_CASCADE_IN_INGEST", "true")

    from services.external_sources import thestatsapi_odds_adapter as _ts_odds
    async def _ts_hit(*args, **kwargs):
        # Devolver un norm_odds con available=True
        norm = {"available": True, "bookmakers": [{"name": "TS"}]}
        return ({"raw": "shape"}, norm, "match_123")
    monkeypatch.setattr(_ts_odds, "fetch_odds_api_sports_shape", _ts_hit)

    from services.external_sources import odds_cascade as _d9
    called = {"n": 0}
    async def _d9_spy(*args, **kwargs):
        called["n"] += 1
        return {"available": True, "source": "the_odds_api"}
    monkeypatch.setattr(_d9, "fetch_direct_match_odds_cascade", _d9_spy)

    _, norm, source = await foc.fetch_football_odds_with_fallback(
        client=None, db=None, fx_raw={"fixture": {"id": 99}},
        fid=99, home={"name": "X"}, away={"name": "Y"},
        kickoff=None, league_name=None,
    )

    assert source == "thestatsapi"
    assert called["n"] == 0  # D9 no debe haberse llamado
