"""
test_d9_followup2_fetch_football_odds
=====================================

Sprint-D9-followup-2 (Jun-2026) — validar la nueva fachada
``fetch_football_odds(match, source_ids, *, client, db=None)``.

Contrato (binding):

- Cascade: Cuotasahora → TheStatsAPI → SofaScore → OddsPortal → manual.
- Avanza ante: request failed, empty response, empty bookmakers, empty
  markets, schema not recognized, invalid odds.
- Success output::
    {available: True, source: <str>, markets: {...}, snapshot_at: iso8601,
     reason_codes: [...]}
- No-odds output::
    {available: False, state: "NO_ODDS_AVAILABLE",
     reason_codes: ["NO_ODDS_AVAILABLE_FROM_ALL_SOURCES",
                    "MANUAL_ODDS_REQUIRED", ...]}
"""

from __future__ import annotations

import asyncio

import pytest

from services import football_odds_aggregator as agg


def _match():
    return {
        "teams": {"home": {"name": "Argentina"}, "away": {"name": "Austria"}},
        "league": {"name": "Friendlies"},
        "kickoff_iso": "2026-06-22T20:00:00Z",
        "match_id": "test-42",
    }


# ── Contrato del no-odds envelope ───────────────────────────────────────────


def test_all_sources_exhausted_returns_canonical_no_odds_envelope():
    async def _run():
        return await agg.fetch_football_odds(_match(), {}, client=None, db=None)

    out = asyncio.run(_run())

    # Estructura exacta del contrato
    assert out.get("available") is False
    assert out.get("state") == agg.STATE_NO_ODDS
    rc = out.get("reason_codes") or []
    assert agg.RC_NO_ODDS_ALL in rc
    assert agg.RC_MANUAL_REQUIRED_USER in rc
    # Trail de fuentes intentadas (auditabilidad)
    assert "CUOTASAHORA_TRIED" in rc
    assert "THESTATSAPI_TRIED" in rc
    assert "SOFASCORE_TRIED" in rc
    assert "ODDSPORTAL_TRIED" in rc
    assert "MANUAL_ODDS_TRIED" in rc


# ── Cuotasahora primaria ────────────────────────────────────────────────────


def test_cuotasahora_primary_short_circuits_cascade(monkeypatch):
    async def _ca_hit(home, away, **kwargs):
        return {
            "available": True,
            "source": "cuotasahora",
            "markets": {"h2h": {"home": 2.10, "draw": 3.30, "away": 3.50}},
            "snapshot_at": "2026-06-22T12:00:00Z",
            "reason_codes": ["CUOTASAHORA_HIT"],
        }

    # Inyectar el mock del scraper antes de la llamada
    import services.external_sources.cuotasahora_scraper as _ca
    monkeypatch.setattr(_ca, "fetch_match_odds", _ca_hit)

    # Espías para verificar que el resto del cascade NO se invoca
    called = {"ts": 0, "sofa": 0, "op": 0, "manual": 0}

    async def _ts_called(*a, **kw):
        called["ts"] += 1
        return None

    monkeypatch.setattr(agg, "_try_thestatsapi", _ts_called)
    monkeypatch.setattr(agg, "_try_sofascore",   lambda *a, **kw: (called.__setitem__("sofa", called["sofa"]+1), None)[1])
    monkeypatch.setattr(agg, "_try_oddsportal",  lambda *a, **kw: (called.__setitem__("op", called["op"]+1), None)[1])
    monkeypatch.setattr(agg, "_try_manual_odds", lambda *a, **kw: (called.__setitem__("manual", called["manual"]+1), None)[1])

    async def _run():
        return await agg.fetch_football_odds(_match(), {}, client=None, db=None)

    out = asyncio.run(_run())

    assert out.get("available") is True
    assert out.get("source") == "cuotasahora"
    assert out["markets"]["h2h"]["home"] == 2.10
    assert "CUOTASAHORA_HIT" in (out.get("reason_codes") or [])
    assert called["ts"] == 0  # cascade no debe continuar
    assert called["sofa"] == 0
    assert called["op"] == 0
    assert called["manual"] == 0


# ── Cuotasahora falla → cae a TheStatsAPI ───────────────────────────────────


def test_thestatsapi_used_when_cuotasahora_empty(monkeypatch):
    async def _ca_empty(home, away, **kwargs):
        return {
            "available": False, "source": "cuotasahora",
            "snapshot_at": "x",
            "reason_codes": ["CUOTASAHORA_NO_MATCH"],
        }

    async def _ts_hit(match, source_ids, client, db):
        return {
            "available": True, "source": "thestatsapi",
            "markets": {"h2h": {"home": 1.95, "draw": 3.40, "away": 3.80}},
            "snapshot_at": "2026-06-22T12:00:00Z",
            "reason_codes": ["THESTATSAPI_ODDS_USED"],
        }

    import services.external_sources.cuotasahora_scraper as _ca
    monkeypatch.setattr(_ca, "fetch_match_odds", _ca_empty)
    monkeypatch.setattr(agg, "_try_thestatsapi", _ts_hit)

    out = asyncio.run(agg.fetch_football_odds(_match(), {}, client=None, db=None))

    assert out["source"] == "thestatsapi"
    assert "THESTATSAPI_ODDS_USED" in out["reason_codes"]
    # Trail debe llevar TODOS los anteriores que se intentaron
    assert "CUOTASAHORA_TRIED" in out["reason_codes"]


# ── Excepciones internas no rompen el cascade ───────────────────────────────


def test_inner_exception_does_not_break_cascade(monkeypatch):
    """Si una fuente lanza, se loguea y se continúa con la siguiente."""
    async def _ca_boom(*args, **kwargs):
        raise RuntimeError("simulated network failure")

    async def _ts_hit(match, source_ids, client, db):
        return {
            "available": True, "source": "thestatsapi",
            "markets": {"h2h": {"home": 2, "draw": 3, "away": 4}},
            "snapshot_at": "x", "reason_codes": ["THESTATSAPI_ODDS_USED"],
        }

    import services.external_sources.cuotasahora_scraper as _ca
    monkeypatch.setattr(_ca, "fetch_match_odds", _ca_boom)
    monkeypatch.setattr(agg, "_try_thestatsapi", _ts_hit)

    out = asyncio.run(agg.fetch_football_odds(_match(), {}, client=None, db=None))
    assert out["available"] is True
    assert out["source"] == "thestatsapi"


# ── Validación: la firma respeta el contrato ───────────────────────────────


def test_signature_is_binding():
    """Validar la firma exacta requerida por el usuario."""
    import inspect
    sig = inspect.signature(agg.fetch_football_odds)
    params = sig.parameters

    assert "match" in params
    assert "source_ids" in params
    assert params["match"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["source_ids"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["client"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["db"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["db"].default is None


# ── Override: NO_ODDS_AVAILABLE → rejection_code se sobreescribe ────────────


def test_no_odds_available_overrides_market_identity_missing():
    """
    Sprint-D9-followup-2: cuando el motor sabe que NO HAY CUOTAS en
    ninguna fuente, el rejection_code NO debe ser MARKET_IDENTITY_MISSING.
    Debe ser ``NO_ODDS_AVAILABLE`` (problema operacional, no de modelo).
    """
    from services.football_market_trace import build_market_trace, build_discarded_header

    trace = build_market_trace({
        "match_label": "Argentina vs Austria",
        "_moneyball": {"classification": "", "classification_reason": ""},
        "_market_edge": {},
        "recommendation": {"market": "Watchlist", "selection": None},
        "market_selection": {"recommended_market": "Watchlist", "reason_codes": []},
        # ↓ Este es el flag clave
        "odds_status": "NO_ODDS_AVAILABLE",
        "reason": "",
    })

    assert trace["rejection_code"] == "NO_ODDS_AVAILABLE"
    header = build_discarded_header(trace)
    assert "sin cuotas disponibles" in header.lower()
    assert "mercado no identificado" not in header.lower()
    assert "motivo no clasificado" not in header.lower()


def test_no_odds_available_overrides_unknown():
    from services.football_market_trace import build_market_trace

    trace = build_market_trace({
        "match_label": "X vs Y",
        "_moneyball": {"classification": "", "classification_reason": ""},
        "_market_edge": {},
        "recommendation": {"market": "Doble Oportunidad", "selection": "1X"},
        "_odds_status": "NO_ODDS_AVAILABLE",
        "reason": "",
    })
    assert trace["rejection_code"] == "NO_ODDS_AVAILABLE"


def test_no_odds_available_via_snapshot_state():
    """
    El override también debe activarse cuando odds_status viene en
    ``odds_snapshot.state`` (path real de propagación desde el aggregator).
    """
    from services.football_market_trace import build_market_trace

    trace = build_market_trace({
        "match_label": "X vs Y",
        "_moneyball": {"classification": "NO_BET_VALUE", "classification_reason": "Cuotas no atractivas y contexto competitivo normal."},
        "_market_edge": {},
        "recommendation": {"market": "Doble Oportunidad", "selection": "1X"},
        "odds_snapshot": {"state": "NO_ODDS_AVAILABLE"},
        "reason": "Cuotas no atractivas y contexto competitivo normal.",
    })
    assert trace["rejection_code"] == "NO_ODDS_AVAILABLE"


def test_existing_odds_do_NOT_get_overridden():
    """Cuando hay cuotas (odds_status ausente/USABLE), el rejection_code original se respeta."""
    from services.football_market_trace import build_market_trace

    trace = build_market_trace({
        "match_label": "Madrid vs Barcelona",
        "_moneyball": {"classification": "NO_BET_VALUE",
                       "classification_reason": "Cuotas no atractivas y contexto competitivo normal."},
        "_market_edge": {},
        "recommendation": {"market": "Doble Oportunidad", "selection": "1X"},
        "reason": "Cuotas no atractivas y contexto competitivo normal.",
        # Sin odds_status → no se debe overridear
    })
    # El código original es ODDS_NOT_ATTRACTIVE (regex match)
    assert trace["rejection_code"] == "ODDS_NOT_ATTRACTIVE"
