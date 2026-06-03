"""Tests for the TheStatsAPI integration (MLB-TS1).

All tests use mocked `httpx.AsyncClient` transports — no real network
calls. CI must pass even when `THESTATSAPI_KEY` is unset.

Coverage:
  * client.is_enabled() honours env flags
  * client.fetch_competitions parses 3 different wrapper shapes
  * client returns [] / {} on 4xx, 5xx, timeouts (fail-soft)
  * normalizer maps TheStatsAPI shape → API-Sports shape
  * normalizer drops malformed payloads
  * cache get/set with TTL freshness (in-memory fake db)
  * aggregator dedupe (same teams + same date window)
  * aggregator does NOT touch TheStatsAPI when disabled
  * aggregator survives one provider failing
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from services.external_sources import thestatsapi_cache as ts_cache
from services.external_sources import thestatsapi_client as ts_client
from services.external_sources import thestatsapi_normalizer as ts_norm
from services import football_live_aggregator as agg


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _mock_client(handler):
    """Build a httpx.AsyncClient backed by a MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.fixture(autouse=True)
def _enable_env(monkeypatch):
    monkeypatch.setenv("THESTATSAPI_KEY", "test-fake-key")
    monkeypatch.setenv("THESTATSAPI_BASE_URL", "https://api.thestatsapi.com/api")
    monkeypatch.setenv("ENABLE_THE_STATS_API", "true")
    yield


# ──────────────────────────────────────────────────────────────────────
# Client — env / enabled
# ──────────────────────────────────────────────────────────────────────
def test_client_is_enabled_true_when_flag_and_key(monkeypatch):
    assert ts_client.is_enabled() is True


def test_client_disabled_when_flag_off(monkeypatch):
    monkeypatch.setenv("ENABLE_THE_STATS_API", "false")
    assert ts_client.is_enabled() is False


def test_client_disabled_when_key_missing(monkeypatch):
    monkeypatch.delenv("THESTATSAPI_KEY", raising=False)
    assert ts_client.is_enabled() is False


def test_get_base_url_default(monkeypatch):
    monkeypatch.delenv("THESTATSAPI_BASE_URL", raising=False)
    assert ts_client.get_base_url() == "https://api.thestatsapi.com/api"


# ──────────────────────────────────────────────────────────────────────
# Client — fetch_competitions parses wrapper variants
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_competitions_wrapped_in_data():
    payload = {"data": [{"id": 1, "name": "World Cup"}, {"id": 2, "name": "Euro"}]}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/football/competitions")
        assert req.headers.get("Authorization") == "Bearer test-fake-key"
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_competitions(c)
    assert len(out) == 2
    assert out[0]["name"] == "World Cup"


@pytest.mark.asyncio
async def test_fetch_competitions_wrapped_in_competitions_key():
    payload = {"competitions": [{"id": 3, "name": "Copa America"}]}

    def handler(req):
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_competitions(c)
    assert out == [{"id": 3, "name": "Copa America"}]


@pytest.mark.asyncio
async def test_fetch_competitions_empty_on_404():
    def handler(req):
        return httpx.Response(404, json={"error": "not found"})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_competitions(c)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_competitions_empty_on_500_after_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500, json={"error": "server"})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_competitions(c)
    assert out == []
    # one initial + one retry = 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_fetch_competitions_empty_on_401_no_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"error": "auth"})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_competitions(c)
    assert out == []
    assert len(calls) == 1  # no retry on auth error


@pytest.mark.asyncio
async def test_fetch_live_matches_parses_matches_key():
    payload = {
        "matches": [
            {
                "id": 7777,
                "competition": {"id": 1, "name": "FIFA World Cup", "type": "international"},
                "teams": {
                    "home": {"id": 100, "name": "Argentina", "is_national_team": True},
                    "away": {"id": 101, "name": "Brazil", "is_national_team": True},
                },
                "date": "2026-06-15T18:00:00Z",
                "status": "live",
                "score": {"home": 1, "away": 0},
            }
        ]
    }

    def handler(req):
        assert "status=live" in str(req.url)
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_live_matches(c)
    assert len(out) == 1
    assert out[0]["id"] == 7777


@pytest.mark.asyncio
async def test_client_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("ENABLE_THE_STATS_API", "false")
    called = []

    def handler(req):
        called.append(req)
        return httpx.Response(200, json={"data": []})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_competitions(c)
    assert out == []
    assert called == []  # never reached the transport


@pytest.mark.asyncio
async def test_health_check_enabled_path():
    def handler(req):
        return httpx.Response(200, json={"data": [{"id": 1, "name": "X"}]})

    async with _mock_client(handler) as c:
        out = await ts_client.health_check(c)
    assert out["enabled"] is True
    assert out["reachable"] is True
    assert out["competitions_count"] == 1


@pytest.mark.asyncio
async def test_health_check_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_THE_STATS_API", "false")
    async with _mock_client(lambda r: httpx.Response(200, json={})) as c:
        out = await ts_client.health_check(c)
    assert out["enabled"] is False
    assert out["reachable"] is False


# ──────────────────────────────────────────────────────────────────────
# Normalizer
# ──────────────────────────────────────────────────────────────────────
def test_normalize_match_full_shape():
    raw = {
        "id": 12345,
        "competition": {
            "id": 1,
            "name": "FIFA World Cup",
            "type": "international",
            "country": "World",
            "season": 2026,
            "logo": "https://x/wc.png",
            "round": "Quarter-finals",
        },
        "teams": {
            "home": {"id": 50, "name": "Argentina", "logo": "h.png", "is_national_team": True},
            "away": {"id": 51, "name": "Brazil",    "logo": "a.png", "is_national_team": True},
        },
        "date": "2026-06-15T18:00:00Z",
        "status": "first_half",
        "score": {"home": 1, "away": 0},
        "venue": {"name": "Estadio Maracaná"},
    }
    n = ts_norm.normalize_match(raw)
    assert n is not None
    assert n["fixture"]["id"] == 900_000_000 + 12345
    assert n["fixture"]["status"]["short"] == "1H"
    assert n["fixture"]["venue"]["name"] == "Estadio Maracaná"
    assert n["league"]["name"] == "FIFA World Cup"
    assert n["teams"]["home"]["name"] == "Argentina"
    assert n["teams"]["away"]["name"] == "Brazil"
    assert n["goals"]["home"] == 1
    assert n["_external_source"] == "thestatsapi"
    assert n["_is_national_team"] is True
    assert n["_is_international"] is True
    # timestamp must be int seconds, UTC
    assert isinstance(n["fixture"]["timestamp"], int)
    expected_ts = int(datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc).timestamp())
    assert n["fixture"]["timestamp"] == expected_ts


def test_normalize_match_missing_team_returns_none():
    raw = {"id": 1, "competition": {"id": 1, "name": "X"}, "date": "2026-01-01T00:00:00Z",
           "teams": {"home": {"id": 1, "name": "H"}, "away": {"id": 2}}}  # away has no name
    assert ts_norm.normalize_match(raw) is None


def test_normalize_match_missing_id_returns_none():
    raw = {"competition": {"id": 1, "name": "X"}, "date": "2026-01-01T00:00:00Z",
           "teams": {"home": {"id": 1, "name": "H"}, "away": {"id": 2, "name": "A"}}}
    assert ts_norm.normalize_match(raw) is None


def test_normalize_match_missing_kickoff_returns_none():
    raw = {"id": 1, "competition": {"id": 1, "name": "X"},
           "teams": {"home": {"id": 1, "name": "H"}, "away": {"id": 2, "name": "A"}}}
    assert ts_norm.normalize_match(raw) is None


def test_normalize_match_with_unix_timestamp():
    ts = 1_700_000_000
    raw = {
        "id": 9, "competition": {"id": 1, "name": "L"},
        "teams": {"home": {"id": 1, "name": "H"}, "away": {"id": 2, "name": "A"}},
        "date": ts, "status": "scheduled",
    }
    n = ts_norm.normalize_match(raw)
    assert n["fixture"]["timestamp"] == ts
    assert n["fixture"]["status"]["short"] == "NS"


def test_normalize_matches_bulk_skips_invalid():
    raws = [
        # valid
        {"id": 1, "competition": {"id": 1, "name": "L"}, "date": "2026-01-01T00:00:00Z",
         "teams": {"home": {"id": 1, "name": "H"}, "away": {"id": 2, "name": "A"}}, "status": "ns"},
        # invalid (no away team)
        {"id": 2, "competition": {"id": 1, "name": "L"}, "date": "2026-01-01T00:00:00Z",
         "teams": {"home": {"id": 3, "name": "H"}}},
        None,
        "not a dict",
    ]
    out = ts_norm.normalize_matches(raws)
    assert len(out) == 1
    assert out[0]["teams"]["home"]["name"] == "H"


def test_normalize_competition_detects_international():
    raw = {"id": 1, "name": "UEFA Nations League", "type": "international", "country": "Europe"}
    n = ts_norm.normalize_competition(raw)
    assert n is not None
    assert n["is_international"] is True
    assert n["id"] == 900_000_000 + 1


# ──────────────────────────────────────────────────────────────────────
# _ns_id (string IDs — real TheStatsAPI format)
# ──────────────────────────────────────────────────────────────────────
def test_ns_id_int():
    assert ts_norm._ns_id(123) == 900_000_000 + 123


def test_ns_id_numeric_string():
    assert ts_norm._ns_id("456") == 900_000_000 + 456


def test_ns_id_prefixed_string():
    # mt_370102627 → strip "mt_" → parse 370102627
    assert ts_norm._ns_id("mt_370102627") == 900_000_000 + 370_102_627
    assert ts_norm._ns_id("tm_28025")     == 900_000_000 + 28_025
    assert ts_norm._ns_id("comp_6107")    == 900_000_000 + 6_107


def test_ns_id_is_deterministic_for_non_numeric():
    a = ts_norm._ns_id("abc_xyz")
    b = ts_norm._ns_id("abc_xyz")
    c = ts_norm._ns_id("abc_xyz2")
    assert a == b
    assert a != c
    assert isinstance(a, int)
    assert a >= 900_000_000


def test_ns_id_none_and_empty():
    assert ts_norm._ns_id(None) is None
    assert ts_norm._ns_id("") is None
    assert ts_norm._ns_id("   ") is None


# ──────────────────────────────────────────────────────────────────────
# Real TheStatsAPI payload shapes
# ──────────────────────────────────────────────────────────────────────
def test_normalize_match_real_shape_with_string_ids():
    """Verbatim shape captured from a real GET /football/matches call."""
    raw = {
        "id": "mt_370102627",
        "competition_id": "comp_6107",
        "season_id": "sn_118868",
        "matchday": 3,
        "status": "scheduled",
        "utc_date": "2026-06-28T02:00:00.000Z",
        "home_team": {"id": "tm_28025", "name": "Algeria"},
        "away_team": {"id": "tm_86533", "name": "Austria"},
        "score": {"home": None, "away": None},
    }
    n = ts_norm.normalize_match(raw)
    assert n is not None
    assert n["fixture"]["id"] == 900_000_000 + 370_102_627
    assert n["fixture"]["status"]["short"] == "NS"
    # milliseconds in the date string should be parsed cleanly
    assert n["fixture"]["timestamp"] == int(datetime(2026, 6, 28, 2, 0, tzinfo=timezone.utc).timestamp())
    assert n["teams"]["home"]["name"] == "Algeria"
    assert n["teams"]["away"]["name"] == "Austria"
    assert n["teams"]["home"]["id"] == 900_000_000 + 28_025
    # league must carry the namespaced comp id even though we only had `competition_id`
    assert n["league"]["id"] == 900_000_000 + 6_107
    assert n["_external_source"] == "thestatsapi"
    assert n["_external_source_id"] == "mt_370102627"


def test_normalize_match_with_competitions_index_enriches_league():
    """With a competitions index, a bare competition_id resolves to full
    metadata (name + is_international flag)."""
    comps_raw = [
        {"id": "comp_6107", "name": "FIFA World Cup", "type": "international", "country": "World"},
        {"id": "comp_0406", "name": "2. Bundesliga", "type": "league", "country": "Germany"},
    ]
    index = ts_norm.build_competitions_index(comps_raw)
    assert "comp_6107" in index
    assert index["comp_6107"]["is_international"] is True

    match_raw = {
        "id": "mt_1", "competition_id": "comp_6107",
        "status": "live", "utc_date": "2026-06-28T02:00:00.000Z",
        "home_team": {"id": "tm_1", "name": "Argentina"},
        "away_team": {"id": "tm_2", "name": "Brazil"},
    }
    n = ts_norm.normalize_match(match_raw, competitions_index=index)
    assert n is not None
    assert n["league"]["name"] == "FIFA World Cup"
    assert n["_is_international"] is True


def test_build_competitions_index_skips_invalid():
    raws = [
        {"id": "comp_1", "name": "X", "type": "league"},
        {"id": "comp_2"},                   # no name → skipped
        {"name": "Z", "type": "league"},    # no id → skipped
        None,
    ]
    index = ts_norm.build_competitions_index(raws)
    assert list(index.keys()) == ["comp_1"]


# ──────────────────────────────────────────────────────────────────────
# Cache — in-memory fake DB
# ──────────────────────────────────────────────────────────────────────
class _FakeColl:
    def __init__(self):
        self._docs: dict[tuple, dict] = {}

    async def find_one(self, q):
        key = (q.get("source"), q.get("endpoint"), q.get("key"))
        return self._docs.get(key)

    async def update_one(self, q, update, upsert=False):
        key = (q.get("source"), q.get("endpoint"), q.get("key"))
        if key in self._docs or upsert:
            self._docs[key] = {**self._docs.get(key, {}), **update["$set"]}

    async def delete_many(self, q):
        keys = [k for k in self._docs if (q.get("source") is None or k[0] == q["source"])
                and (q.get("endpoint") is None or k[1] == q["endpoint"])]
        for k in keys:
            del self._docs[k]

        class _R:
            deleted_count = len(keys)
        return _R()


class _FakeDB:
    def __init__(self):
        self._colls: dict[str, _FakeColl] = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())


@pytest.mark.asyncio
async def test_cache_set_then_get_fresh():
    db = _FakeDB()
    await ts_cache.cache_set(db, "competitions", "all", [{"id": 1}])
    out = await ts_cache.cache_get(db, "competitions", "all")
    assert out == [{"id": 1}]


@pytest.mark.asyncio
async def test_cache_get_returns_none_when_stale():
    db = _FakeDB()
    coll = db[ts_cache.COLLECTION]
    # Insert a doc with a timestamp older than the live_matches TTL (40s)
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    coll._docs[("thestatsapi", "live_matches", "all")] = {
        "source": "thestatsapi", "endpoint": "live_matches", "key": "all",
        "data": [{"id": 1}], "_cached_at": stale_ts,
    }
    out = await ts_cache.cache_get(db, "live_matches", "all")
    assert out is None


@pytest.mark.asyncio
async def test_cache_unknown_endpoint_returns_none():
    db = _FakeDB()
    out = await ts_cache.cache_get(db, "nonexistent_endpoint", "k")
    assert out is None


@pytest.mark.asyncio
async def test_cache_db_none_is_fail_soft():
    out = await ts_cache.cache_get(None, "competitions", "all")
    assert out is None
    await ts_cache.cache_set(None, "competitions", "all", [{"id": 1}])  # no-op, must not raise
    cleared = await ts_cache.cache_clear(None)
    assert cleared == 0


@pytest.mark.asyncio
async def test_cache_clear_endpoint_scoped():
    db = _FakeDB()
    await ts_cache.cache_set(db, "competitions", "all", [1])
    await ts_cache.cache_set(db, "live_matches", "all", [2])
    n = await ts_cache.cache_clear(db, endpoint="competitions")
    assert n == 1
    assert await ts_cache.cache_get(db, "competitions", "all") is None
    assert await ts_cache.cache_get(db, "live_matches", "all") == [2]


# ──────────────────────────────────────────────────────────────────────
# Aggregator — dedupe & merge
# ──────────────────────────────────────────────────────────────────────
def _af_fixture(home: str, away: str, ts: int, fid: int = 1) -> dict:
    return {
        "fixture": {"id": fid, "date": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "timestamp": ts, "status": {"short": "1H"}, "venue": {"name": None}},
        "league": {"id": 1, "name": "World Cup", "season": 2026, "logo": None, "country": "World", "round": None},
        "teams": {"home": {"id": 10, "name": home, "logo": None},
                  "away": {"id": 20, "name": away, "logo": None}},
        "goals": {"home": 0, "away": 0},
    }


def _ts_fixture(home: str, away: str, ts: int) -> dict:
    return ts_norm.normalize_match({
        "id": 99,
        "competition": {"id": 1, "name": "World Cup", "type": "international", "season": 2026},
        "teams": {"home": {"id": 10, "name": home, "is_national_team": True},
                  "away": {"id": 20, "name": away, "is_national_team": True}},
        "date": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "status": "live",
    })


def test_merge_dedupe_same_teams_and_close_ts():
    ts = 1_700_000_000
    primary = [_af_fixture("Argentina", "Brazil", ts)]
    secondary = [_ts_fixture("Argentina", "Brazil", ts + 600)]  # +10min
    merged, meta = agg.merge_and_deduplicate(primary, secondary)
    assert meta["primary_count"] == 1
    assert meta["secondary_count"] == 1
    assert meta["duplicates_dropped"] == 1
    assert meta["secondary_added"] == 0
    assert meta["total"] == 1
    # the primary should be tagged as covered by both
    assert "thestatsapi" in merged[0]["_external_sources_covered"]
    # and inherit national-team flag from secondary
    assert merged[0]["_is_national_team"] is True


def test_merge_keeps_distinct_matches():
    ts = 1_700_000_000
    primary = [_af_fixture("Real Madrid", "Barcelona", ts)]
    secondary = [_ts_fixture("Argentina", "Brazil", ts)]
    merged, meta = agg.merge_and_deduplicate(primary, secondary)
    assert meta["total"] == 2
    assert meta["secondary_added"] == 1
    # secondary entry retained
    names = sorted([(m["teams"]["home"]["name"], m["teams"]["away"]["name"]) for m in merged])
    assert names == [("Argentina", "Brazil"), ("Real Madrid", "Barcelona")]


def test_merge_normalizes_team_names_for_dedup():
    """Accents / suffixes / casing should not produce duplicates."""
    ts = 1_700_000_000
    primary = [_af_fixture("Atlético Madrid", "FC Barcelona", ts)]
    secondary = [_ts_fixture("Atletico Madrid", "Barcelona", ts + 30)]
    merged, meta = agg.merge_and_deduplicate(primary, secondary)
    assert meta["duplicates_dropped"] == 1
    assert meta["total"] == 1


def test_merge_treats_far_kickoffs_as_different():
    """Same teams but kickoffs > 1h apart → two distinct fixtures."""
    ts = 1_700_000_000
    primary = [_af_fixture("Argentina", "Brazil", ts)]
    secondary = [_ts_fixture("Argentina", "Brazil", ts + 7200)]  # +2h
    merged, meta = agg.merge_and_deduplicate(primary, secondary)
    assert meta["total"] == 2


def test_merge_empty_inputs():
    merged, meta = agg.merge_and_deduplicate([], [])
    assert merged == []
    assert meta["total"] == 0


def test_merge_only_secondary():
    ts = 1_700_000_000
    secondary = [_ts_fixture("England", "France", ts)]
    merged, meta = agg.merge_and_deduplicate([], secondary)
    assert meta["total"] == 1
    assert meta["secondary_added"] == 1
    assert merged[0]["_external_source"] == "thestatsapi"


# ──────────────────────────────────────────────────────────────────────
# Aggregator — fetch_live_football_fixtures end-to-end
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_live_football_disabled_skips_secondary(monkeypatch):
    monkeypatch.setenv("ENABLE_THE_STATS_API", "false")

    async def fake_af_live(client):
        return [_af_fixture("PSG", "Marseille", 1_700_000_000, fid=1)]

    monkeypatch.setattr(agg.af, "fixtures_live", fake_af_live)
    async with _mock_client(lambda r: httpx.Response(500)) as c:
        merged, meta = await agg.fetch_live_football_fixtures(c, db=None)
    assert meta["thestatsapi_enabled"] is False
    assert meta["secondary_count"] == 0
    assert meta["total"] == 1


@pytest.mark.asyncio
async def test_fetch_live_football_secondary_failure_is_fail_soft(monkeypatch):
    async def fake_af_live(client):
        return [_af_fixture("X", "Y", 1_700_000_000)]

    monkeypatch.setattr(agg.af, "fixtures_live", fake_af_live)

    def handler(req):
        return httpx.Response(500, json={"error": "bad"})

    async with _mock_client(handler) as c:
        merged, meta = await agg.fetch_live_football_fixtures(c, db=None)
    # Primary survives; secondary contributes 0
    assert meta["primary_count"] == 1
    assert meta["secondary_count"] == 0
    assert meta["total"] == 1


@pytest.mark.asyncio
async def test_fetch_live_football_primary_failure_returns_secondary(monkeypatch):
    async def fake_af_live(client):
        raise RuntimeError("api-sports down")

    monkeypatch.setattr(agg.af, "fixtures_live", fake_af_live)

    payload = {"matches": [{
        "id": 555,
        "competition": {"id": 1, "name": "Copa America", "type": "international"},
        "teams": {"home": {"id": 1, "name": "Argentina", "is_national_team": True},
                  "away": {"id": 2, "name": "Chile",     "is_national_team": True}},
        "date": "2026-07-01T20:00:00Z",
        "status": "live",
    }]}

    def handler(req):
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as c:
        merged, meta = await agg.fetch_live_football_fixtures(c, db=None)
    assert meta["primary_count"] == 0
    assert meta["secondary_count"] == 1
    assert meta["total"] == 1
    assert merged[0]["teams"]["home"]["name"] == "Argentina"
    assert merged[0]["_is_national_team"] is True


@pytest.mark.asyncio
async def test_fetch_live_football_merges_both(monkeypatch):
    """Primary returns a club match; secondary returns a national-team match.
    Both should be in the merged output with proper provenance tags.
    """
    async def fake_af_live(client):
        return [_af_fixture("Real Madrid", "Barcelona", 1_700_000_000, fid=1)]

    monkeypatch.setattr(agg.af, "fixtures_live", fake_af_live)

    payload = {"matches": [{
        "id": 700,
        "competition": {"id": 5, "name": "UEFA Nations League", "type": "international"},
        "teams": {"home": {"id": 10, "name": "Spain", "is_national_team": True},
                  "away": {"id": 11, "name": "Germany", "is_national_team": True}},
        "date": "2026-06-15T19:00:00Z",
        "status": "first_half",
    }]}

    def handler(req):
        return httpx.Response(200, json=payload)

    async with _mock_client(handler) as c:
        merged, meta = await agg.fetch_live_football_fixtures(c, db=None)

    assert meta["primary_count"] == 1
    assert meta["secondary_count"] == 1
    assert meta["total"] == 2
    sources = {m.get("_external_source") for m in merged}
    assert sources == {"api_sports", "thestatsapi"}
