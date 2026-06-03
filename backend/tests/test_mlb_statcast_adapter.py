"""Tests for the MLB Statcast Adapter (Batch B).

Coverage:
  * Feature flags / env toggles
  * normalize_mlb_advanced_payload (data_quality auto-detection)
  * Cache read/write (Mongo fake)
  * fetch_with_pybaseball — skipped when not installed
  * fetch_with_brightdata — skipped without config
  * fetch_with_thestatsapi — skipped when disabled
  * merge_advanced_sources — priority-aware merge + field_sources tracking
  * get_mlb_advanced_profile — end-to-end with mocked sources
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services import mlb_statcast_adapter as adapter


# ──────────────────────────────────────────────────────────────────────
# Feature flags
# ──────────────────────────────────────────────────────────────────────
def test_is_adapter_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("ENABLE_MLB_STATCAST_ADAPTER", raising=False)
    assert adapter.is_adapter_enabled() is False


def test_is_adapter_enabled_when_true(monkeypatch):
    monkeypatch.setenv("ENABLE_MLB_STATCAST_ADAPTER", "true")
    assert adapter.is_adapter_enabled() is True


def test_is_pybaseball_enabled_defaults_on(monkeypatch):
    monkeypatch.delenv("ENABLE_PYBASEBALL_ENRICHMENT", raising=False)
    assert adapter.is_pybaseball_enabled() is True


def test_is_brightdata_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("ENABLE_BRIGHTDATA_STATCAST_FALLBACK", raising=False)
    assert adapter.is_brightdata_enabled() is False


def test_cache_ttl_per_role(monkeypatch):
    monkeypatch.setenv("MLB_ADVANCED_STATS_CACHE_HOURS", "24")
    # pitcher / batter (default) → 24h
    assert adapter.cache_ttl_seconds("pitcher") == 24 * 3600
    assert adapter.cache_ttl_seconds("batter") == 24 * 3600
    # team → half
    assert adapter.cache_ttl_seconds("team") == 12 * 3600
    # live → 5min
    assert adapter.cache_ttl_seconds("live") == 5 * 60


# ──────────────────────────────────────────────────────────────────────
# normalize_mlb_advanced_payload
# ──────────────────────────────────────────────────────────────────────
def test_normalize_defaults_all_fields_to_none_for_role():
    p = adapter.normalize_mlb_advanced_payload(role="pitcher", available=False)
    assert "pitcher" in p
    assert all(v is None for v in p["pitcher"].values())
    assert p["data_quality"] == "missing"
    assert p["available"] is False


def test_normalize_auto_strong_when_all_fields_present():
    p = adapter.normalize_mlb_advanced_payload(
        role="pitcher", available=True,
        pitcher={f: 1.0 for f in adapter._PITCHER_FIELDS},
    )
    assert p["data_quality"] == "strong"
    assert p["available"] is True


def test_normalize_auto_partial_with_some_fields():
    p = adapter.normalize_mlb_advanced_payload(
        role="pitcher", available=True,
        pitcher={"era": 3.42, "xera": 3.10},  # only 2 fields
    )
    assert p["data_quality"] == "partial"


def test_normalize_team_section():
    p = adapter.normalize_mlb_advanced_payload(
        role="team", available=True, team_id="t1", team_name="X",
        team={"team_ops": 0.745, "team_xwoba": 0.330},
    )
    assert p["team"]["team_ops"] == 0.745
    assert p["team"]["team_xwoba"] == 0.330
    # The remaining team fields exist but are None
    assert p["team"]["team_barrel_pct"] is None


# ──────────────────────────────────────────────────────────────────────
# is_pybaseball_available
# ──────────────────────────────────────────────────────────────────────
def test_is_pybaseball_available_is_bool():
    # Don't assert True/False — depends on env. Just shape.
    assert isinstance(adapter.is_pybaseball_available(), bool)


# ──────────────────────────────────────────────────────────────────────
# fetch_with_pybaseball — skip paths
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_pybaseball_skipped_when_flag_off(monkeypatch):
    monkeypatch.setenv("ENABLE_PYBASEBALL_ENRICHMENT", "false")
    out = await adapter.fetch_with_pybaseball(role="pitcher", player_id=1)
    assert out["source_status"] == "skipped"


@pytest.mark.asyncio
async def test_fetch_pybaseball_skipped_when_not_installed(monkeypatch):
    monkeypatch.setenv("ENABLE_PYBASEBALL_ENRICHMENT", "true")
    monkeypatch.setattr(adapter, "is_pybaseball_available", lambda: False)
    out = await adapter.fetch_with_pybaseball(role="pitcher", player_id=1)
    assert out["source_status"] == "skipped"
    assert "not_installed" in out["_reason"]


# ──────────────────────────────────────────────────────────────────────
# fetch_with_brightdata
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_brightdata_skipped_when_flag_off(monkeypatch):
    monkeypatch.setenv("ENABLE_BRIGHTDATA_STATCAST_FALLBACK", "false")
    out = await adapter.fetch_with_brightdata(role="pitcher")
    assert out["source_status"] == "skipped"


@pytest.mark.asyncio
async def test_fetch_brightdata_scaffold_returns_skipped_without_config(monkeypatch):
    """The Bright Data module is stub-only until parsers are wired in."""
    monkeypatch.setenv("ENABLE_BRIGHTDATA_STATCAST_FALLBACK", "true")
    monkeypatch.delenv("BRIGHTDATA_TOKEN", raising=False)
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)
    out = await adapter.fetch_with_brightdata(role="pitcher", player_name="X")
    assert out["source_status"] == "skipped"


# ──────────────────────────────────────────────────────────────────────
# fetch_with_thestatsapi (baseball)
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_thestatsapi_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_THE_STATS_API_BASEBALL", "false")
    out = await adapter.fetch_with_thestatsapi(role="team", team_id="t1")
    assert out["source_status"] == "skipped"


# ──────────────────────────────────────────────────────────────────────
# Cache helpers
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
async def test_cache_write_then_read_fresh():
    db = _FakeDB()
    payload = {"available": True, "data_quality": "strong", "team": {"team_ops": 0.8}}
    await adapter.write_mlb_advanced_cache(db, "mlb_advanced:team:2026:yankees", payload, ttl_seconds=3600)
    out = await adapter.read_mlb_advanced_cache(db, "mlb_advanced:team:2026:yankees")
    assert out == payload


@pytest.mark.asyncio
async def test_cache_read_returns_none_when_stale():
    db = _FakeDB()
    coll = db[adapter._CACHE_COLL]
    stale_at = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    coll._docs[(adapter._CACHE_SRC, "profile", "key1")] = {
        "data": {"x": 1}, "_cached_at": stale_at, "_ttl_seconds": 3600,
    }
    out = await adapter.read_mlb_advanced_cache(db, "key1")
    assert out is None


@pytest.mark.asyncio
async def test_cache_fail_soft_with_db_none():
    out = await adapter.read_mlb_advanced_cache(None, "any")
    assert out is None
    await adapter.write_mlb_advanced_cache(None, "any", {"x": 1}, ttl_seconds=60)


# ──────────────────────────────────────────────────────────────────────
# merge_advanced_sources
# ──────────────────────────────────────────────────────────────────────
def test_merge_field_sources_priority_for_statcast_metrics():
    """xERA / barrel / hard-hit must prefer pybaseball over the rest."""
    pyb = {"pitcher": {"xera": 3.10, "barrel_pct_allowed": 7.2}, "source_status": "success"}
    bd  = {"pitcher": {"xera": 999.0, "hard_hit_pct_allowed": 35.0}, "source_status": "success"}
    ts  = {"pitcher": {"era": 3.50}, "source_status": "success"}
    merged = adapter.merge_advanced_sources(pyb, bd, ts, role="pitcher")
    assert merged["pitcher"]["xera"] == 3.10
    assert merged["field_sources"]["pitcher.xera"] == "pybaseball"
    assert merged["pitcher"]["hard_hit_pct_allowed"] == 35.0
    assert merged["field_sources"]["pitcher.hard_hit_pct_allowed"] == "brightdata"
    assert merged["pitcher"]["era"] == 3.50
    assert merged["field_sources"]["pitcher.era"] == "thestatsapi"


def test_merge_conventional_metrics_prefer_thestatsapi():
    pyb = {"batting": {"ops": 0.700}, "source_status": "success"}
    ts  = {"batting": {"ops": 0.812}, "source_status": "success"}
    merged = adapter.merge_advanced_sources(pyb, None, ts, role="batter")
    assert merged["batting"]["ops"] == 0.812
    assert merged["field_sources"]["batting.ops"] == "thestatsapi"


def test_merge_marks_merged_when_multiple_sources_succeed():
    pyb = {"pitcher": {"xera": 3.0}, "source_status": "success"}
    ts  = {"pitcher": {"era": 4.2}, "source_status": "success"}
    merged = adapter.merge_advanced_sources(pyb, None, ts, role="pitcher")
    assert merged["source"] == "merged"
    assert set(merged["sources_consulted"]) >= {"pybaseball", "thestatsapi"}


def test_merge_single_source_label():
    pyb = {"pitcher": {"xera": 3.0}, "source_status": "success"}
    merged = adapter.merge_advanced_sources(pyb, None, None, role="pitcher")
    assert merged["source"] == "pybaseball"


def test_merge_data_quality_partial_when_some_missing():
    pyb = {"pitcher": {"xera": 3.0}, "source_status": "success"}   # only 1 field of 14
    merged = adapter.merge_advanced_sources(pyb, None, None, role="pitcher")
    assert merged["data_quality"] == "partial"


def test_merge_data_quality_missing_when_all_empty():
    merged = adapter.merge_advanced_sources({}, {}, {}, role="pitcher")
    assert merged["data_quality"] == "missing"
    assert merged["available"] is False
    assert merged["source"] == "unknown"


# ──────────────────────────────────────────────────────────────────────
# get_mlb_advanced_profile end-to-end (mocked sources)
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_mlb_advanced_profile_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("ENABLE_MLB_STATCAST_ADAPTER", "false")
    out = await adapter.get_mlb_advanced_profile(role="pitcher", player_id=1)
    assert out["available"] is False
    assert out["source"] == "disabled"


@pytest.mark.asyncio
async def test_get_mlb_advanced_profile_aggregates_and_caches(monkeypatch):
    monkeypatch.setenv("ENABLE_MLB_STATCAST_ADAPTER", "true")
    db = _FakeDB()

    async def fake_pyb(**kwargs):
        return {"pitcher": {"xera": 3.20, "k_pct": 28.5}, "source_status": "success"}

    async def fake_bd(**kwargs):
        return {"source_status": "skipped"}

    async def fake_ts(**kwargs):
        return {"pitcher": {"era": 3.55}, "source_status": "success"}

    monkeypatch.setattr(adapter, "fetch_with_pybaseball", fake_pyb)
    monkeypatch.setattr(adapter, "fetch_with_brightdata", fake_bd)
    monkeypatch.setattr(adapter, "fetch_with_thestatsapi", fake_ts)

    out1 = await adapter.get_mlb_advanced_profile(
        db=db, player_id=12345, player_name="Test Pitcher",
        season=2026, role="pitcher",
    )
    assert out1["available"] is True
    assert out1["source"] == "merged"
    assert out1["pitcher"]["xera"] == 3.20
    assert out1["pitcher"]["era"] == 3.55
    assert out1["source_status"]["cache"] == "miss"
    assert out1["player_id"] == 12345
    assert out1["season"] == 2026

    # Second call — must hit cache, no new provider calls
    calls = {"n": 0}

    async def fake_pyb_count(**kwargs):
        calls["n"] += 1
        return {"pitcher": {}, "source_status": "failed"}

    monkeypatch.setattr(adapter, "fetch_with_pybaseball", fake_pyb_count)
    out2 = await adapter.get_mlb_advanced_profile(
        db=db, player_id=12345, player_name="Test Pitcher",
        season=2026, role="pitcher",
    )
    assert out2["source_status"]["cache"] == "hit"
    assert out2["pitcher"]["xera"] == 3.20  # same as cached
    assert calls["n"] == 0     # provider was NOT called


@pytest.mark.asyncio
async def test_get_mlb_advanced_profile_all_sources_failed_returns_missing(monkeypatch):
    monkeypatch.setenv("ENABLE_MLB_STATCAST_ADAPTER", "true")

    async def empty(**kwargs):
        return {"source_status": "failed", "_reason": "X"}

    monkeypatch.setattr(adapter, "fetch_with_pybaseball", empty)
    monkeypatch.setattr(adapter, "fetch_with_brightdata", empty)
    monkeypatch.setattr(adapter, "fetch_with_thestatsapi", empty)

    out = await adapter.get_mlb_advanced_profile(role="pitcher", player_id=99)
    assert out["available"] is False
    assert out["data_quality"] == "missing"
    # The engine MUST still work — no exception, just a missing payload.


@pytest.mark.asyncio
async def test_get_mlb_advanced_profile_force_refresh_bypasses_cache(monkeypatch):
    monkeypatch.setenv("ENABLE_MLB_STATCAST_ADAPTER", "true")
    db = _FakeDB()

    # Pre-fill cache with one value
    coll = db[adapter._CACHE_COLL]
    coll._docs[(adapter._CACHE_SRC, "profile",
                "mlb_advanced:pitcher:2026:9999")] = {
        "data": {"available": True, "source": "cached", "pitcher": {"xera": 99}},
        "_cached_at": datetime.now(timezone.utc).isoformat(),
        "_ttl_seconds": 86400,
    }

    async def fresh_pyb(**kwargs):
        return {"pitcher": {"xera": 3.0}, "source_status": "success"}

    async def skip(**kwargs):
        return {"source_status": "skipped"}

    monkeypatch.setattr(adapter, "fetch_with_pybaseball", fresh_pyb)
    monkeypatch.setattr(adapter, "fetch_with_brightdata", skip)
    monkeypatch.setattr(adapter, "fetch_with_thestatsapi", skip)

    out = await adapter.get_mlb_advanced_profile(
        db=db, player_id=9999, season=2026, role="pitcher",
        force_refresh=True,
    )
    assert out["pitcher"]["xera"] == 3.0   # fresh, not 99
    assert out["source_status"]["cache"] == "miss"


# ──────────────────────────────────────────────────────────────────────
# normalisers
# ──────────────────────────────────────────────────────────────────────
def test_normalise_float_handles_nan_and_strings():
    assert adapter._normalise_float(None) is None
    assert adapter._normalise_float("nope") is None
    assert adapter._normalise_float("3.14") == 3.14
    assert adapter._normalise_float(float("nan")) is None
    assert adapter._normalise_float(7) == 7.0


def test_pct_from_ratio_converts_fractions():
    assert adapter._pct_from_ratio(0.55) == 55.0
    assert adapter._pct_from_ratio(0.085) == 8.5
    assert adapter._pct_from_ratio(35.2) == 35.2   # already percentage
    assert adapter._pct_from_ratio(None) is None
