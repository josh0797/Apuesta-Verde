"""Phase F83-update — Corners cascade with Scrape.do diagnostics.

Tests cover:
  * ``fetch_via_scrapedo_result`` reason codes (token missing, HTTP
    error, empty body, breaker open).
  * ``score365_scrapedo_client.extract_365scores_ids`` cascade.
  * ``score365_scrapedo_client.parse_365scores_corners_from_html``.
  * ``football_corners_provider.debug_corners_cascade`` with both
    cascade orders.
  * ``GET /api/football/corners/debug`` endpoint shape.

All tests are pure-Python: HTTP calls are stubbed by monkeypatching
the inner ``httpx.AsyncClient.get`` / the scrape.do helper directly.
No real network is exercised.
"""
from __future__ import annotations

import json
import os
import pytest

from services import scrape_do_client as sd
from services.external_sources import score365_scrapedo_client as s365sd
from services import football_corners_provider as cp


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    """Minimal async-context-manager mock for httpx.AsyncClient."""
    def __init__(self, *args, **kwargs):
        self._kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kw):
        return _FakeAsyncClient._RESP


def _set_fake_response(status_code: int, text: str) -> None:
    _FakeAsyncClient._RESP = _FakeResponse(status_code, text)


@pytest.fixture(autouse=True)
def _reset_breaker(monkeypatch):
    """Each test starts with a fresh circuit-breaker state and a
    deterministic env (no env contamination across tests)."""
    sd._CB_FAILS.clear()
    sd._CB_OPENED_AT.clear()
    monkeypatch.delenv("ENABLE_F83_CASCADE_ORDER", raising=False)
    monkeypatch.delenv("SCRAPEDO_TOKEN", raising=False)
    monkeypatch.delenv("SCRAPE_DO_TOKEN", raising=False)
    monkeypatch.delenv("SCRAPEDO_API_KEY", raising=False)
    yield


# =====================================================================
# PART 1 — fetch_via_scrapedo_result reason codes
# =====================================================================
class TestScrapedoResult:
    async def _run(self, url, **kw):
        return await sd.fetch_via_scrapedo_result(url, **kw)

    @pytest.mark.asyncio
    async def test_scrapedo_result_token_missing(self):
        # No token set in env (autouse fixture removed any).
        res = await self._run("https://example.com/page")
        assert res["ok"] is False
        assert res["reason_code"] == sd.RC_TOKEN_MISSING
        assert "token" in (res["message_debug"] or "").lower()
        assert res["html"] is None
        assert res["provider"] == "scrape_do"
        assert res["fetched_at"]  # ISO timestamp present

    @pytest.mark.asyncio
    async def test_scrapedo_result_http_error(self, monkeypatch):
        monkeypatch.setenv("SCRAPEDO_TOKEN", "fake_token_123")
        monkeypatch.setattr(sd.httpx, "AsyncClient", _FakeAsyncClient)
        _set_fake_response(403, "Forbidden")
        res = await self._run("https://www.365scores.com/x")
        assert res["ok"] is False
        assert res["status_code"] == 403
        assert res["reason_code"] == sd.RC_HTTP_ERROR
        assert "403" in res["message_debug"]

    @pytest.mark.asyncio
    async def test_scrapedo_result_empty_body(self, monkeypatch):
        monkeypatch.setenv("SCRAPEDO_TOKEN", "fake_token_123")
        monkeypatch.setattr(sd.httpx, "AsyncClient", _FakeAsyncClient)
        _set_fake_response(200, "")
        res = await self._run("https://www.365scores.com/x")
        assert res["ok"] is False
        assert res["status_code"] == 200
        assert res["reason_code"] == sd.RC_EMPTY_BODY

    @pytest.mark.asyncio
    async def test_scrapedo_result_breaker_open(self, monkeypatch):
        monkeypatch.setenv("SCRAPEDO_TOKEN", "fake_token_123")
        # Force the breaker open for the host.
        sd._CB_OPENED_AT["www.example.com"] = 9999999999.0  # far future
        res = await self._run("https://www.example.com/x")
        assert res["ok"] is False
        assert res["reason_code"] == sd.RC_BREAKER_OPEN

    @pytest.mark.asyncio
    async def test_scrapedo_result_success(self, monkeypatch):
        monkeypatch.setenv("SCRAPEDO_TOKEN", "fake_token_123")
        monkeypatch.setattr(sd.httpx, "AsyncClient", _FakeAsyncClient)
        _set_fake_response(200, "<html><body>OK</body></html>")
        res = await self._run("https://www.365scores.com/x")
        assert res["ok"] is True
        assert res["html"].startswith("<html>")
        assert res["status_code"] == 200
        assert res["reason_code"] is None

    @pytest.mark.asyncio
    async def test_legacy_fetch_returns_none_when_result_fails(self, monkeypatch):
        # Legacy fetch_via_scrapedo must keep returning None on failure
        # so existing callers don't observe the new dict shape.
        res_legacy = await sd.fetch_via_scrapedo("https://example.com/x")
        assert res_legacy is None


# =====================================================================
# PART 2 — score365 ID resolution + parser
# =====================================================================
class TestScore365IDs:
    def test_score365_missing_id_returns_specific_reason(self):
        out = s365sd.extract_365scores_ids({})
        assert out["available"] is False
        assert out["game_id"] is None
        assert out["match_url"] is None

    def test_resolves_from_external_ids(self):
        md = {"external_ids": {"365scores": {"game_id": "12345",
                                               "matchup_id": "111-222"}}}
        out = s365sd.extract_365scores_ids(md)
        assert out["available"] is True
        assert out["game_id"]    == "12345"
        assert out["matchup_id"] == "111-222"
        assert out["resolved_from"] == "external_ids.365scores.game_id"

    def test_resolves_from_match_url(self):
        md = {"match_url": "https://www.365scores.com/football/match/x#id=99887"}
        out = s365sd.extract_365scores_ids(md)
        assert out["available"]   is True
        assert out["game_id"]     == "99887"
        assert out["resolved_from"] == "match_url"

    def test_resolves_from_external_urls(self):
        md = {"external_urls": {
            "365scores": "https://www.365scores.com/match/abc-111-222-7777/"
        }}
        out = s365sd.extract_365scores_ids(md)
        assert out["available"]      is True
        assert out["game_id"]        == "7777"
        assert out["matchup_id"]     == "111-222"
        assert out["resolved_from"]  == "external_urls.365scores"

    def test_resolves_from_pick_match_url(self):
        md = {"pick": {"match_url": "https://example.com/m/?id=4242"}}
        out = s365sd.extract_365scores_ids(md)
        assert out["available"] is True
        assert out["game_id"]   == "4242"


class TestScore365Parser:
    def test_parser_extracts_corner_kicks_alias(self):
        html = """
        <html><head></head><body>
        <script id="__NEXT_DATA__" type="application/json">
        {
          "props": {
            "pageProps": {
              "game": {
                "competitors": [
                  {"id": 1, "name": "Netherlands"},
                  {"id": 2, "name": "Japan"}
                ],
                "statistics": [
                  {"name": "Possession", "home": "55", "away": "45"},
                  {"name": "Corner Kicks", "home": "6", "away": "3"}
                ]
              }
            }
          }
        }
        </script>
        </body></html>
        """
        out = s365sd.parse_365scores_corners_from_html(html)
        assert out["available"]      is True
        assert out["home"]["corners"] == 6
        assert out["away"]["corners"] == 3
        assert out["total_corners"]  == 9
        assert out["home"]["team"]   == "Netherlands"
        assert out["transport"]      == "scrape_do"
        assert s365sd.RC_CORNERS_FOUND in out["reason_codes"]

    def test_parser_extracts_corner_alias_in_spanish(self):
        html = """
        <script id="__NEXT_DATA__" type="application/json">
        {"game": {"competitors": [{"name": "Argentina"}, {"name": "Brasil"}],
                  "statistics": [{"name": "Tiros de esquina",
                                   "home": 8, "away": 4}]}}
        </script>
        """
        out = s365sd.parse_365scores_corners_from_html(html)
        assert out["available"]      is True
        assert out["total_corners"]  == 12

    def test_html_without_stats_returns_stats_empty(self):
        html = "<html><body>No embedded JSON here</body></html>"
        out = s365sd.parse_365scores_corners_from_html(html)
        assert out["available"] is False
        assert out["reason_code"] == s365sd.RC_STATS_EMPTY

    def test_stats_without_corners_returns_corners_not_found(self):
        # statistics is present but no corner alias matches.
        raw = {"statistics": [
            {"name": "Possession",  "home": 55, "away": 45},
            {"name": "Yellow Cards", "home": 2,  "away": 1},
        ]}
        out = s365sd.normalize_365scores_corners(raw)
        assert out["available"] is False
        assert out["reason_code"] == s365sd.RC_CORNERS_NOT_FOUND
        assert "Possession" in out["raw_stat_names"]


# =====================================================================
# PART 6 — football_corners_provider cascade
# =====================================================================
class TestF83CascadeOrder:
    def test_cascade_default_is_f82_2_order(self, monkeypatch):
        monkeypatch.delenv("ENABLE_F83_CASCADE_ORDER", raising=False)
        assert cp.is_f83_cascade_order_enabled() is False

    def test_cascade_flag_enabled_when_env_true(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F83_CASCADE_ORDER", "true")
        assert cp.is_f83_cascade_order_enabled() is True

    @pytest.mark.asyncio
    async def test_debug_cascade_default_order(self, monkeypatch):
        monkeypatch.delenv("ENABLE_F83_CASCADE_ORDER", raising=False)
        res = await cp.debug_corners_cascade({"match_id": "m1"})
        assert res["cascade_order_used"] == ["thestatsapi", "api_sports", "365scores"]
        assert res["flag_enabled"] is False

    @pytest.mark.asyncio
    async def test_debug_cascade_f83_order(self, monkeypatch):
        monkeypatch.setenv("ENABLE_F83_CASCADE_ORDER", "true")
        res = await cp.debug_corners_cascade({"match_id": "m1"})
        assert res["cascade_order_used"] == ["api_sports", "365scores", "thestatsapi"]
        assert res["flag_enabled"] is True


class TestF83CascadeDiagnostics:
    @pytest.mark.asyncio
    async def test_uses_scrapedo_transport_for_365scores(self):
        # Empty match_doc → 365scores stage reports ID_RESOLUTION.
        res = await cp.debug_corners_cascade({"match_id": "m_empty"})
        s365_entry = next(p for p in res["providers_checked"]
                          if p["provider"] == "365scores")
        assert s365_entry["transport"] == "scrape_do"
        assert s365_entry["available"] is False
        assert s365_entry["reason_code"] == s365sd.RC_ID_MISSING
        assert s365_entry["stage"] == "ID_RESOLUTION"

    @pytest.mark.asyncio
    async def test_corners_provider_falls_back_after_365scores_failure(self):
        # APS has data → winner = api_sports regardless of order.
        md = {
            "match_id": "m_aps",
            "home_team": {"name": "H"}, "away_team": {"name": "A"},
            "live_stats": {"home_stats": {"Corner Kicks": "6"},
                            "away_stats": {"Corner Kicks": "3"}},
        }
        res = await cp.debug_corners_cascade(md)
        # Default order: TSA → APS (winner).
        assert res["winner"] is not None
        assert res["winner"]["provider"] == "api_sports"
        # final.available is True with reason_code from APS.
        assert res["final"]["available"] is True

    @pytest.mark.asyncio
    async def test_thestatsapi_block_emits_specific_reason(self):
        # Only TSA has data.
        md = {
            "match_id": "m_tsa",
            "_thestatsapi_enrichment": {
                "corners": {"home": 5, "away": 4, "total": 9}
            },
        }
        res = await cp.debug_corners_cascade(md)
        # Default order, TSA wins first.
        assert res["winner"]["provider"] == "thestatsapi"
        assert res["final"]["available"] is True

    @pytest.mark.asyncio
    async def test_no_provider_available_emits_final_reason(self):
        res = await cp.debug_corners_cascade({"match_id": "void"})
        assert res["final"]["available"] is False
        assert res["final"]["reason_code"] == cp.RC_NO_PROVIDER_AVAILABLE
        # All 3 providers must be present in the audit.
        names = [p["provider"] for p in res["providers_checked"]]
        assert set(names) == {"thestatsapi", "api_sports", "365scores"}

    @pytest.mark.asyncio
    async def test_debug_endpoint_includes_scrapedo_status(self):
        res = await cp.debug_corners_cascade({"match_id": "m_x"})
        assert "scrapedo" in res
        assert "enabled" in res["scrapedo"]
        assert "breaker_status" in res["scrapedo"]
        assert "open_hosts"   in res["scrapedo"]["breaker_status"]
        assert "threshold"    in res["scrapedo"]["breaker_status"]


class TestF83EnrichWrapper:
    @pytest.mark.asyncio
    async def test_enrich_match_corners_f83_persists_winner(self):
        md = {
            "match_id": "m_e",
            "live_stats": {"home_stats": {"Corner Kicks": "7"},
                            "away_stats": {"Corner Kicks": "2"}},
        }
        payload = await cp.enrich_match_corners_f83(None, None, md)
        assert payload["available"] is True
        assert payload["source"]    == "api_sports"
        # Persisted to all 3 compat locations.
        assert md.get("corners_snapshot") == payload
        assert md["football_data_enrichment"]["corners"] == payload

    @pytest.mark.asyncio
    async def test_enrich_match_corners_f83_persists_failure(self):
        md = {"match_id": "m_f"}
        payload = await cp.enrich_match_corners_f83(None, None, md)
        assert payload["available"] is False
        assert payload["reason_code"] == cp.RC_NO_PROVIDER_AVAILABLE
        # Per-provider audit included for UI.
        assert any(p["provider"] == "365scores"
                   for p in payload["providers_checked"])


# =====================================================================
# PART 7 — endpoint smoke (in-process FastAPI)
# =====================================================================
class TestCornersDebugEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_returns_full_diagnostics_shape(self, monkeypatch):
        # Direct call into the route function to avoid spinning the app.
        from server import football_corners_debug
        # monkeypatch the match-doc loader to return a synthetic doc.
        import server as srv_mod
        async def _stub_loader(match_id):
            return {
                "match_id": match_id,
                "home_team": {"name": "H"}, "away_team": {"name": "A"},
                "live_stats": {"home_stats": {"Corner Kicks": "5"},
                                "away_stats": {"Corner Kicks": "4"}},
            }
        monkeypatch.setattr(srv_mod, "_load_match_doc_for_corners", _stub_loader)
        res = await football_corners_debug(match_id="abc123")
        assert res.get("ok") is True
        assert res.get("match_doc_found") is True
        assert "cascade_order_used" in res
        assert "flag_enabled" in res
        assert "providers_checked" in res
        assert res["winner"]["provider"] == "api_sports"

    @pytest.mark.asyncio
    async def test_endpoint_handles_missing_match_id(self):
        from server import football_corners_debug
        res = await football_corners_debug(match_id="")
        assert res["ok"] is False
        assert res["reason_code"] == "MATCH_ID_REQUIRED"

    @pytest.mark.asyncio
    async def test_endpoint_handles_match_not_in_db(self, monkeypatch):
        from server import football_corners_debug
        import server as srv_mod
        async def _stub_loader(match_id):
            return None
        monkeypatch.setattr(srv_mod, "_load_match_doc_for_corners", _stub_loader)
        res = await football_corners_debug(match_id="nonexistent")
        assert res.get("match_doc_found") is False
        # Cascade still runs against a stub match_doc.
        assert res.get("cascade_order_used")
        assert res["final"]["available"] is False
