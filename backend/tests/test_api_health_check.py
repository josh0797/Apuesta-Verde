"""API-health check probes.

Validates the canonical health-snapshot shape used by the new
``/api/diagnostics/api-health`` endpoint. The aggregator MUST:

  * Return ``status: DISABLED`` when env keys are missing (no requests).
  * Surface ``status: OK`` + ``fixtures_returned`` when a probe responds.
  * Catch every kind of failure (timeout, HTTP error, exception) and
    convert it into ``status: DOWN`` with a populated ``error`` field.
  * Aggregate concurrent probes with a summary breakdown.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import api_health_check as ahc


# =====================================================================
# Per-provider probes — disabled when env keys missing
# =====================================================================
class TestProbesDisabledWhenKeyMissing:
    @pytest.mark.asyncio
    async def test_api_sports_disabled(self, monkeypatch):
        monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
        monkeypatch.delenv("API_SPORTS_KEY", raising=False)
        r = await ahc._probe_api_sports(timeout_s=2.0)
        assert r["status"] == "DISABLED"
        assert r["request_sent"] is False
        assert "missing" in (r["error"] or "").lower()

    @pytest.mark.asyncio
    async def test_thestatsapi_disabled(self, monkeypatch):
        monkeypatch.delenv("THESTATSAPI_KEY", raising=False)
        monkeypatch.delenv("THE_STATS_API_KEY", raising=False)
        r = await ahc._probe_thestatsapi(timeout_s=2.0)
        assert r["status"] == "DISABLED"
        assert r["request_sent"] is False

    @pytest.mark.asyncio
    async def test_scrapedo_disabled(self, monkeypatch):
        monkeypatch.delenv("SCRAPEDO_TOKEN", raising=False)
        monkeypatch.delenv("SCRAPEDO_API_KEY", raising=False)
        for fn in (ahc._probe_sportytrader, ahc._probe_totalcorner, ahc._probe_footystats):
            r = await fn(timeout_s=2.0)
            assert r["status"] == "DISABLED"


# =====================================================================
# Per-provider probes — populated response
# =====================================================================
class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, *_, **__):
        self._resp = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *_args, **_kwargs):  # noqa: D401
        return self._resp

    def set_response(self, resp):
        self._resp = resp


class TestProbesWithMockedTransport:
    @pytest.mark.asyncio
    async def test_api_sports_ok(self, monkeypatch):
        monkeypatch.setenv("API_FOOTBALL_KEY", "secret")
        client = _FakeAsyncClient()
        client.set_response(_FakeResponse(200, {"response": [{}] * 25}))
        with patch("services.api_health_check.httpx.AsyncClient",
                    return_value=client):
            r = await ahc._probe_api_sports(timeout_s=2.0)
        assert r["status"] == "OK"
        assert r["fixtures_returned"] == 25
        assert r["http_status"] == 200

    @pytest.mark.asyncio
    async def test_api_sports_degraded_when_zero_fixtures(self, monkeypatch):
        monkeypatch.setenv("API_FOOTBALL_KEY", "secret")
        client = _FakeAsyncClient()
        client.set_response(_FakeResponse(200, {"response": []}))
        with patch("services.api_health_check.httpx.AsyncClient",
                    return_value=client):
            r = await ahc._probe_api_sports(timeout_s=2.0)
        assert r["status"] == "DEGRADED"
        assert r["fixtures_returned"] == 0
        assert r["http_status"] == 200

    @pytest.mark.asyncio
    async def test_api_sports_http_error_is_down(self, monkeypatch):
        monkeypatch.setenv("API_FOOTBALL_KEY", "secret")
        client = _FakeAsyncClient()
        client.set_response(_FakeResponse(500, {}))
        with patch("services.api_health_check.httpx.AsyncClient",
                    return_value=client):
            r = await ahc._probe_api_sports(timeout_s=2.0)
        assert r["status"] == "DOWN"
        assert r["http_status"] == 500
        assert "500" in (r["error"] or "")

    @pytest.mark.asyncio
    async def test_api_sports_transport_exception_is_down(self, monkeypatch):
        monkeypatch.setenv("API_FOOTBALL_KEY", "secret")

        class _ExplodingClient:
            def __init__(self, *_, **__):
                pass
            async def __aenter__(self):
                raise RuntimeError("connect refused")
            async def __aexit__(self, *exc):
                return False
            async def get(self, *_args, **_kwargs):
                raise RuntimeError("never reached")

        with patch("services.api_health_check.httpx.AsyncClient",
                    _ExplodingClient):
            r = await ahc._probe_api_sports(timeout_s=2.0)
        assert r["status"] == "DOWN"
        assert "connect refused" in (r["error"] or "")


# =====================================================================
# Aggregator — check_all_providers
# =====================================================================
class TestCheckAllProviders:
    @pytest.mark.asyncio
    async def test_runs_every_probe(self, monkeypatch):
        # Disable every external key so probes return DISABLED quickly
        # without doing any real HTTP work.
        for env in ("API_FOOTBALL_KEY", "API_SPORTS_KEY",
                     "THESTATSAPI_KEY", "THE_STATS_API_KEY",
                     "SCRAPEDO_TOKEN", "SCRAPEDO_API_KEY"):
            monkeypatch.delenv(env, raising=False)
        r = await ahc.check_all_providers(timeout_s=2.0)
        assert set(r["api_health"].keys()) == {
            "api_sports", "thestatsapi",
            "sportytrader", "totalcorner", "footystats",
        }
        assert r["summary"]["total"] == 5
        assert r["summary"]["disabled"] == 5

    @pytest.mark.asyncio
    async def test_only_filter_runs_subset(self, monkeypatch):
        for env in ("API_FOOTBALL_KEY", "API_SPORTS_KEY",
                     "THESTATSAPI_KEY", "THE_STATS_API_KEY",
                     "SCRAPEDO_TOKEN", "SCRAPEDO_API_KEY"):
            monkeypatch.delenv(env, raising=False)
        r = await ahc.check_all_providers(
            timeout_s=2.0, only=["api_sports", "thestatsapi"],
        )
        assert set(r["api_health"].keys()) == {"api_sports", "thestatsapi"}
        assert r["summary"]["total"] == 2

    @pytest.mark.asyncio
    async def test_aggregator_never_raises_on_probe_failure(self, monkeypatch):
        # Inject an exploding probe to make sure the gather() wrapper
        # converts the failure into a DOWN entry rather than propagating.
        async def boom(*_, **__):
            raise RuntimeError("simulated failure")
        custom = {"api_sports": boom, "thestatsapi": boom}
        r = await ahc.check_all_providers(timeout_s=2.0, probes=custom)
        assert r["summary"]["down"] == 2
        for det in r["api_health"].values():
            assert det["status"] == "DOWN"
            assert "simulated failure" in (det["error"] or "")

    @pytest.mark.asyncio
    async def test_aggregator_response_shape(self, monkeypatch):
        for env in ("API_FOOTBALL_KEY", "API_SPORTS_KEY",
                     "THESTATSAPI_KEY", "THE_STATS_API_KEY",
                     "SCRAPEDO_TOKEN"):
            monkeypatch.delenv(env, raising=False)
        r = await ahc.check_all_providers(timeout_s=2.0)
        # Required top-level keys.
        assert "checked_at" in r
        assert "timeout_s" in r
        assert "api_health" in r
        assert "summary" in r
        # Summary keys.
        for k in ("ok", "degraded", "down", "disabled", "skipped", "total"):
            assert k in r["summary"]

    @pytest.mark.asyncio
    async def test_aggregator_serialises_to_json(self):
        import json
        r = await ahc.check_all_providers(timeout_s=2.0)
        # No exception → safe to round-trip through JSON.
        encoded = json.dumps(r)
        assert "api_health" in encoded
        decoded = json.loads(encoded)
        assert "checked_at" in decoded


# =====================================================================
# Timeout handling
# =====================================================================
class TestTimeoutHandling:
    @pytest.mark.asyncio
    async def test_per_probe_timeout_returns_down(self):
        import asyncio

        async def slow_probe(*_, **__):
            await asyncio.sleep(5)
            return {"provider": "slow", "status": "OK"}

        custom = {"slow": slow_probe}
        r = await ahc.check_all_providers(timeout_s=0.1, probes=custom)
        assert r["api_health"]["slow"]["status"] == "DOWN"
        assert "timed out" in (r["api_health"]["slow"]["error"] or "").lower()
