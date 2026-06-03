"""Tests for TheStatsAPI Batch 3 — pre-match enrichment + sources surfacing.

Covers:
  * `thestatsapi_enrichment.enrich_pre_match` fan-out and result shape
  * Cache integration (calls cache_get/set, returns cached values)
  * Fail-soft when integration disabled / partial provider failure
  * Client surface for team_stats / player_stats / match_details
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from services.external_sources import thestatsapi_client as ts_client
from services.external_sources import thestatsapi_enrichment as ts_enrich


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _enable_env(monkeypatch):
    monkeypatch.setenv("THESTATSAPI_KEY", "test-fake-key")
    monkeypatch.setenv("THESTATSAPI_BASE_URL", "https://api.thestatsapi.com/api")
    monkeypatch.setenv("ENABLE_THE_STATS_API", "true")
    yield


# ──────────────────────────────────────────────────────────────────────
# Client — new endpoints
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_match_details_unwraps_data_wrapper():
    def handler(req):
        assert req.url.path.endswith("/football/matches/mt_42")
        return httpx.Response(200, json={"data": {
            "id": "mt_42",
            "home_lineup": [{"id": "pl_1", "name": "Player A"}],
            "venue": {"name": "Stadium X"},
        }})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_match_details(c, "mt_42")
    assert out["id"] == "mt_42"
    assert isinstance(out["home_lineup"], list)


@pytest.mark.asyncio
async def test_fetch_team_stats_passes_season_and_competition():
    captured = {}

    def handler(req):
        captured["path"] = req.url.path
        captured["params"] = dict(req.url.params)
        return httpx.Response(200, json={"data": {"goals_for_avg": 1.6, "xg_avg": 1.42}})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_team_stats(c, "tm_99", season=2026, competition_id="comp_5")
    assert out == {"goals_for_avg": 1.6, "xg_avg": 1.42}
    assert "/football/teams/tm_99/stats" in captured["path"]
    assert captured["params"].get("season") == "2026"
    assert captured["params"].get("competition_id") == "comp_5"


@pytest.mark.asyncio
async def test_fetch_player_stats_returns_empty_on_404():
    def handler(req):
        return httpx.Response(404, json={"error": "no player"})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_player_stats(c, "pl_x")
    assert out == {}


@pytest.mark.asyncio
async def test_fetch_match_details_basketball_path():
    def handler(req):
        assert req.url.path.endswith("/basketball/matches/mt_b1")
        return httpx.Response(200, json={"data": {"id": "mt_b1"}})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_match_details(c, "mt_b1", sport="basketball")
    assert out["id"] == "mt_b1"


@pytest.mark.asyncio
async def test_fetch_match_details_baseball_path_uses_games_endpoint():
    captured = {}

    def handler(req):
        captured["path"] = req.url.path
        return httpx.Response(200, json={"data": {"id": "mt_bb1"}})

    async with _mock_client(handler) as c:
        await ts_client.fetch_match_details(c, "mt_bb1", sport="baseball")
    assert "/baseball/games/mt_bb1" in captured["path"]


# ──────────────────────────────────────────────────────────────────────
# enrich_pre_match — happy path
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_enrich_pre_match_aggregates_three_calls():
    calls: list[str] = []

    def handler(req):
        path = req.url.path
        calls.append(path)
        if "/matches/mt_777" in path:
            return httpx.Response(200, json={"data": {"id": "mt_777", "venue": {"name": "Wembley"}}})
        if "/teams/tm_h/stats" in path:
            return httpx.Response(200, json={"data": {"xg_avg": 1.6}})
        if "/teams/tm_a/stats" in path:
            return httpx.Response(200, json={"data": {"xg_avg": 1.1}})
        return httpx.Response(404, json={})

    async with _mock_client(handler) as c:
        out = await ts_enrich.enrich_pre_match(
            c, db=None,
            sport="football",
            match_raw_id="mt_777",
            home_team_id="tm_h", away_team_id="tm_a",
            season=2026, competition_id="comp_5",
        )

    assert out["source"] == "thestatsapi"
    assert out["sport"] == "football"
    assert out["match"]["venue"]["name"] == "Wembley"
    assert out["team_stats"]["home"] == {"xg_avg": 1.6}
    assert out["team_stats"]["away"] == {"xg_avg": 1.1}
    # 3 parallel requests
    assert sum(1 for p in calls if "matches/mt_777" in p) == 1
    assert sum(1 for p in calls if "teams/tm_h" in p) == 1
    assert sum(1 for p in calls if "teams/tm_a" in p) == 1


@pytest.mark.asyncio
async def test_enrich_pre_match_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_THE_STATS_API", "false")

    async with _mock_client(lambda r: httpx.Response(200)) as c:
        out = await ts_enrich.enrich_pre_match(
            c, db=None, sport="football",
            match_raw_id="mt_1", home_team_id="tm_h", away_team_id="tm_a",
        )
    assert out == {}


@pytest.mark.asyncio
async def test_enrich_pre_match_drops_block_when_all_empty():
    """If every call returns 404, the payload is collapsed to {} so the
    UI can branch on `if match_doc.get('_thestatsapi_enrichment')`."""
    def handler(req):
        return httpx.Response(404, json={"error": "missing"})

    async with _mock_client(handler) as c:
        out = await ts_enrich.enrich_pre_match(
            c, db=None, sport="football",
            match_raw_id="mt_1", home_team_id="tm_h", away_team_id="tm_a",
        )
    assert out == {}


@pytest.mark.asyncio
async def test_enrich_pre_match_partial_team_stats():
    """One team's stats fail → the partial result still surfaces."""
    def handler(req):
        path = req.url.path
        if "teams/tm_h" in path:
            return httpx.Response(200, json={"data": {"xg_avg": 2.0}})
        if "teams/tm_a" in path:
            return httpx.Response(500, json={"error": "boom"})
        if "matches/mt_1" in path:
            return httpx.Response(200, json={"data": {"id": "mt_1"}})
        return httpx.Response(404)

    async with _mock_client(handler) as c:
        out = await ts_enrich.enrich_pre_match(
            c, db=None, sport="football",
            match_raw_id="mt_1", home_team_id="tm_h", away_team_id="tm_a",
        )
    assert out["team_stats"]["home"] == {"xg_avg": 2.0}
    assert out["team_stats"]["away"] == {}


# ──────────────────────────────────────────────────────────────────────
# Cache integration
# ──────────────────────────────────────────────────────────────────────
class _FakeColl:
    def __init__(self):
        self._docs: dict = {}

    async def find_one(self, q):
        key = (q.get("source"), q.get("endpoint"), q.get("key"))
        return self._docs.get(key)

    async def update_one(self, q, update, upsert=False):
        key = (q.get("source"), q.get("endpoint"), q.get("key"))
        if key in self._docs or upsert:
            self._docs[key] = {**self._docs.get(key, {}), **update["$set"]}


class _FakeDB:
    def __init__(self):
        self._colls: dict = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())


@pytest.mark.asyncio
async def test_enrich_pre_match_uses_cache_on_second_call():
    db = _FakeDB()
    call_count = {"n": 0}

    def handler(req):
        call_count["n"] += 1
        path = req.url.path
        if "/matches/" in path:
            return httpx.Response(200, json={"data": {"id": "mt_1"}})
        if "/teams/" in path:
            return httpx.Response(200, json={"data": {"xg_avg": 1.0}})
        return httpx.Response(404)

    async with _mock_client(handler) as c:
        out1 = await ts_enrich.enrich_pre_match(
            c, db=db, sport="football",
            match_raw_id="mt_1", home_team_id="tm_h", away_team_id="tm_a",
            season=2026, competition_id="comp_1",
        )
        first_count = call_count["n"]
        # Second call with same inputs — all 3 should hit the cache.
        out2 = await ts_enrich.enrich_pre_match(
            c, db=db, sport="football",
            match_raw_id="mt_1", home_team_id="tm_h", away_team_id="tm_a",
            season=2026, competition_id="comp_1",
        )

    assert out1["match"]["id"] == "mt_1"
    # Everything except the per-call `fetched_at` timestamp should be identical.
    for k in ("source", "sport", "match", "team_stats"):
        assert out2.get(k) == out1.get(k), f"mismatch on {k}"
    # 3 calls for the first run, 0 additional for the second
    assert call_count["n"] == first_count == 3


# ──────────────────────────────────────────────────────────────────────
# Sources-consulted propagation (summary shape)
# ──────────────────────────────────────────────────────────────────────
def test_external_sources_propagation_shape():
    """Smoke test for the field contract the frontend EmptyState reads."""
    summary = {}
    sources_seen = [
        {"source": "api_sports", "label": "API-Sports"},
        {"source": "thestatsapi", "label": "TheStatsAPI"},
        {"source": "mlb_stats_api", "label": "MLB Stats API"},
    ]
    # Mimic the server.py block
    summary["external_sources_consulted"] = sources_seen
    summary["external_sources_labels"] = sorted({s.get("label") or s.get("source") for s in sources_seen})

    assert summary["external_sources_labels"] == ["API-Sports", "MLB Stats API", "TheStatsAPI"]
    assert len(summary["external_sources_consulted"]) == 3
