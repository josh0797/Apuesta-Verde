"""F94.2 — FIFA World Cup live visibility + TheStatsAPI fallback tests.

Mandatory tests (per user spec):
  1. World Cup live fixture (Iran vs New Zealand) is ANALYZABLE and
     never DISCARDED, even when the provider does not ship a league_id.
  2. Live debug counters surface world_cup_live_count >= 1,
     world_cup_live_detected=True, world_cup_hidden_by_filter=0.
  3. WORLD_CUP_ALIASES detector matches every locale variant the user
     supplied (ES/EN/PT/FR), rejects qualifying / women / U-XX / club WC.
  4. TheStatsAPI fallback is invoked ONLY when API-Football does NOT
     return a senior WC fixture, and contributes a normalised fixture
     tagged ``_is_world_cup=True``.
  5. When World Cup is detected without odds (no league_id), the fixture
     carries ``VISIBLE_PENDING_MARKET`` as a *secondary* reason, with
     ``analysis_status == "ANALYZABLE"`` (never DISCARDED).
  6. Diagnostics module surfaces the structured TheStatsAPI probe block
     with endpoint, http_status, raw_count, reason, sample_payload_keys.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.football_world_cup_aliases import (
    WORLD_CUP_ALIASES, is_world_cup, normalize_world_cup_league_name,
)
from services.football_live_visibility import (
    classify_live_fixture,
    compute_football_live_visibility,
    _thestatsapi_world_cup_fallback,
)


# =====================================================================
# Helpers
# =====================================================================

def _wc_fixture(*, league_id=None, league_name="FIFA World Cup",
                 country="World", home="Iran", away="New Zealand",
                 status="1H", elapsed=24, fid="fx-wc-1"):
    """Build the user-reported real-world fixture (Iran vs New Zealand)."""
    return {
        "fixture": {"id": fid, "status": {"short": status},
                    "elapsed": elapsed,
                    "date":      "2026-06-16T01:00:00+00:00",
                    "timestamp": 1781568000},
        "league":  {"id": league_id, "name": league_name, "country": country},
        "teams":   {"home": {"id": 1, "name": home},
                    "away": {"id": 2, "name": away}},
    }


# =====================================================================
# 1. World Cup detector — alias coverage
# =====================================================================

@pytest.mark.parametrize("name,country", [
    ("FIFA World Cup", None),
    ("World Cup", None),
    ("FIFA World Cup 2026", None),
    ("World Cup 2026", None),
    ("Copa Mundial", None),
    ("Copa Mundial de Fútbol", None),
    ("Copa do Mundo FIFA", None),
    ("Copa do Mundo", "World"),
    ("Coupe du Monde", None),
    ("Mundial 2026", "World"),
    ("FIFA WC", None),
])
def test_world_cup_aliases_positive(name, country):
    assert is_world_cup(name, country) is True
    assert normalize_world_cup_league_name(name, country) == "FIFA World Cup"


@pytest.mark.parametrize("name,country", [
    ("World Cup Qualifying CONMEBOL", None),
    ("FIFA World Cup Women", None),
    ("Women's World Cup", None),
    ("U-20 World Cup", None),
    ("FIFA U-17 World Cup", None),
    ("FIFA Club World Cup", None),
    ("Eliminatorias Mundial", None),
    ("Premier League", None),
    ("", None),
    (None, None),
])
def test_world_cup_aliases_negative(name, country):
    assert is_world_cup(name, country) is False
    assert normalize_world_cup_league_name(name, country) is None


def test_world_cup_aliases_set_contains_all_canonical_forms():
    """The frozenset must include the documented canonical aliases."""
    must_have = {"fifa world cup", "world cup", "copa mundial",
                  "copa do mundo", "coupe du monde"}
    assert must_have.issubset(WORLD_CUP_ALIASES)


# =====================================================================
# 2. classify_live_fixture — World Cup bypass
# =====================================================================

def test_world_cup_live_fixture_is_always_analyzable():
    """Iran vs New Zealand / FIFA World Cup → never DISCARDED."""
    fx = _wc_fixture()
    c = classify_live_fixture(fx)
    assert c["visibility_status"] == "VISIBLE"
    assert c["analysis_status"]   == "ANALYZABLE"
    assert c["discard_reason"] is None
    assert c["_is_world_cup"] is True
    # Either real meta from football_competitions OR synthetic tier_1 meta.
    assert c["competition_meta"] is not None
    assert c["competition_meta"]["tier"] == "tier_1"


def test_world_cup_live_fixture_without_league_id_marks_pending_market():
    """No league_id → VISIBLE_PENDING_MARKET on secondary_reasons but
    analysis_status stays ANALYZABLE (never DISCARDED)."""
    fx = _wc_fixture(league_id=None)
    c = classify_live_fixture(fx)
    assert c["analysis_status"] == "ANALYZABLE"
    assert "VISIBLE_PENDING_MARKET" in c["secondary_reasons"]
    assert "SPORTYTRADER_NOT_FOUND" in c["secondary_reasons"]


def test_world_cup_with_qualifying_name_does_NOT_trigger_bypass():
    """Qualifiers must NOT get the senior-WC bypass."""
    fx = _wc_fixture(league_name="World Cup Qualifying CONMEBOL")
    c = classify_live_fixture(fx)
    assert c["_is_world_cup"] is False


# =====================================================================
# 3. compute_football_live_visibility — debug counters + payload
# =====================================================================

@pytest.mark.asyncio
async def test_compute_live_visibility_surfaces_world_cup_counters():
    """The full pipeline must surface world_cup_* counters and include
    the user-reported fixture as ANALYZABLE."""
    wc_fx = _wc_fixture()
    other_fx = {
        "fixture": {"id": "fx-other", "status": {"short": "1H"},
                    "elapsed": 60, "date": "2026-06-16T02:00:00+00:00",
                    "timestamp": 1781571600},
        "league":  {"id": 71,  "name": "Serie B", "country": "Brazil"},
        "teams":   {"home": {"id": 9,  "name": "Avai"},
                    "away": {"id": 10, "name": "Sport Recife"}},
    }
    fake_db = MagicMock()

    with patch(
        "services.football_live_aggregator.fetch_live_football_fixtures",
        new=AsyncMock(return_value=([wc_fx, other_fx], {"primary_count": 2})),
    ):
        out = await compute_football_live_visibility(client=None, db=fake_db)

    assert out["ok"] is True
    debug = out["live_debug"]
    assert debug["world_cup_live_detected"] is True
    assert debug["world_cup_live_count"]   >= 1
    assert debug["world_cup_hidden_by_filter"] == 0
    assert "Iran vs New Zealand" in debug["world_cup_examples"]
    # visible_live_count includes both fixtures.
    assert debug["visible_live_count"] == 2
    # Hidden by priority filter must ALWAYS be 0 per F94 contract.
    assert debug["hidden_by_priority_filter"] == 0


# =====================================================================
# 4. TheStatsAPI fallback — only fires when primary lacks WC
# =====================================================================

@pytest.mark.asyncio
async def test_thestatsapi_fallback_fires_when_primary_has_no_world_cup():
    """When API-Football returns no World Cup, the fallback is invoked
    and contributes the missing fixture (tagged _is_world_cup=True)."""
    primary_no_wc = [{
        "fixture": {"id": "fx-mlb-1", "status": {"short": "1H"}, "elapsed": 10,
                    "date": "2026-06-16T00:00:00+00:00", "timestamp": 1781568000},
        "league":  {"id": 71, "name": "Serie B", "country": "Brazil"},
        "teams":   {"home": {"id": 1, "name": "Avai"},
                    "away": {"id": 2, "name": "Sport Recife"}},
    }]
    ts_wc_fixture = {
        "fixture": {"id": "fx-ts-wc-1", "status": {"short": "1H"}, "elapsed": 24,
                    "date": "2026-06-16T01:00:00+00:00", "timestamp": 1781568000},
        "league":  {"id": None, "name": "FIFA World Cup 2026", "country": "World"},
        "teams":   {"home": {"id": None, "name": "Iran"},
                    "away": {"id": None, "name": "New Zealand"}},
    }

    fake_db = MagicMock()

    with patch(
        "services.football_live_aggregator.fetch_live_football_fixtures",
        new=AsyncMock(return_value=(primary_no_wc, {"primary_count": 1})),
    ), patch(
        "services.football_live_visibility._thestatsapi_world_cup_fallback",
        new=AsyncMock(return_value=(
            [{**ts_wc_fixture, "_is_world_cup": True,
              "_external_source": "thestatsapi"}],
            {"provider": "thestatsapi", "status": "OK",
             "raw_count": 5, "reason": "WORLD_CUP_FOUND",
             "endpoint": "/football/matches?status=live",
             "http_status": 200, "sample_payload_keys": ["matches"]},
        )),
    ):
        out = await compute_football_live_visibility(client=None, db=fake_db)

    debug = out["live_debug"]
    assert debug["world_cup_fallback_used"] is True
    assert debug["world_cup_live_count"] >= 1
    assert debug["thestatsapi_diag"]["status"] == "OK"
    # World Cup fixture must be present in the visible items.
    wc_items = [it for it in out["items"] if it.get("_is_world_cup")]
    assert len(wc_items) >= 1
    assert wc_items[0]["analysis_status"] == "ANALYZABLE"


@pytest.mark.asyncio
async def test_thestatsapi_fallback_SKIPPED_when_primary_already_has_wc():
    """If API-Football already returned a World Cup match, the
    expensive TheStatsAPI fallback must NOT fire."""
    primary_with_wc = [_wc_fixture()]
    fake_db = MagicMock()

    fallback_mock = AsyncMock()

    with patch(
        "services.football_live_aggregator.fetch_live_football_fixtures",
        new=AsyncMock(return_value=(primary_with_wc, {"primary_count": 1})),
    ), patch(
        "services.football_live_visibility._thestatsapi_world_cup_fallback",
        new=fallback_mock,
    ):
        out = await compute_football_live_visibility(client=None, db=fake_db)

    fallback_mock.assert_not_called()
    debug = out["live_debug"]
    assert debug["world_cup_fallback_used"] is False
    assert debug["thestatsapi_diag"]["status"] == "SKIPPED_PRIMARY_HAS_WC"


# =====================================================================
# 5. Standalone fallback contract (the _thestatsapi_world_cup_fallback
#    helper itself must be fail-soft and return a structured diag).
# =====================================================================

@pytest.mark.asyncio
async def test_world_cup_fallback_returns_diag_when_thestatsapi_disabled(monkeypatch):
    """Fail-soft: when TheStatsAPI is disabled, the fallback returns an
    empty list and a diagnostic with status='DISABLED'."""
    monkeypatch.setenv("ENABLE_THE_STATS_API", "false")
    monkeypatch.delenv("THESTATSAPI_KEY", raising=False)
    fake_db = MagicMock()
    fixtures, diag = await _thestatsapi_world_cup_fallback(client=None, db=fake_db)
    assert fixtures == []
    assert diag["status"] == "DISABLED"
    assert diag["provider"] == "thestatsapi"
    assert diag["raw_count"] == 0


# =====================================================================
# 6. Diagnostics module — structured probe envelope
# =====================================================================

@pytest.mark.asyncio
async def test_diagnostics_probe_returns_full_envelope(monkeypatch):
    """probe_fixtures_endpoint must return the documented contract,
    including endpoint, http_status, raw_count, sample_payload_keys."""
    from services.external_sources import thestatsapi_diagnostics as diag

    monkeypatch.setenv("ENABLE_THE_STATS_API", "true")
    monkeypatch.setenv("THESTATSAPI_KEY", "test-key")

    sample_payload = {"matches": [
        {"id": "m1"}, {"id": "m2"}, {"id": "m3"},
    ]}

    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=sample_payload,
                              headers={"x-request-id": "req-abc"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        out = await diag.probe_fixtures_endpoint(client)

    assert out["provider"] == "thestatsapi"
    assert out["status"] == "OK"
    assert out["http_status"] == 200
    assert out["raw_count"] == 3
    assert out["request_id"] == "req-abc"
    assert "/football/matches" in out["endpoint"]
    assert "matches" in out["sample_payload_keys"]


@pytest.mark.asyncio
async def test_diagnostics_probe_reports_empty_payload(monkeypatch):
    """When the provider returns an empty body, status=EMPTY with
    reason=ADAPTER_RETURNED_EMPTY (matching the user-reported bug)."""
    from services.external_sources import thestatsapi_diagnostics as diag

    monkeypatch.setenv("ENABLE_THE_STATS_API", "true")
    monkeypatch.setenv("THESTATSAPI_KEY", "test-key")

    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        out = await diag.probe_fixtures_endpoint(client)

    assert out["status"] == "EMPTY"
    assert out["reason"] == "ADAPTER_RETURNED_EMPTY"
    assert out["raw_count"] == 0


@pytest.mark.asyncio
async def test_diagnostics_probe_reports_auth_error(monkeypatch):
    """HTTP 401/403 must surface as status=AUTH_ERROR."""
    from services.external_sources import thestatsapi_diagnostics as diag

    monkeypatch.setenv("ENABLE_THE_STATS_API", "true")
    monkeypatch.setenv("THESTATSAPI_KEY", "test-key")

    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        out = await diag.probe_fixtures_endpoint(client)

    assert out["status"] == "AUTH_ERROR"
    assert out["http_status"] == 401
    assert "AUTH" in out["reason"]
