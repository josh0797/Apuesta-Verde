"""Sprint E.1 · Tests for ``services.live_odds_monitor``.

Covers the contract described in ``plan.md``:

* Kill-switch (``LIVE_ODDS_ENABLED!=true``) → no writes, no fetches.
* Visible universe extraction (pure function) reads only buckets that
  belong to the latest pick_run payload.
* ``match_id → event_id`` resolution:
    - cache hit short-circuits the HTTP fetch,
    - cache miss → fetch_events → fuzzy match → upsert mapping,
    - no match → ``ODDS_EVENT_ID_MISSING`` (no crash, no snapshot).
* ``event_payload_to_snapshots`` (pure) explodes a single event into
  one snapshot per (bookmaker, market).
* End-to-end ``run_cycle`` honours the universe, persists snapshots
  with ``source="live_odds_monitor_v1"``, and tolerates fetch failures.
* The ``GET /api/odds/snapshots/{match_id}`` filter ignores legacy
  ``odds_snapshots`` documents lacking ``source``.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from services import live_odds_monitor as lom


# ════════════════════════════════════════════════════════════════════════
# Fake Mongo (motor-compatible interface used by the module).
# ════════════════════════════════════════════════════════════════════════
class _AsyncCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *args, **kwargs):
        # We accept either positional or keyword sort; behaviour ignored
        # for the tests (data is supplied pre-sorted by the fixture).
        return self

    def limit(self, n):
        self.docs = self.docs[: int(n)]
        return self

    async def to_list(self, length=None):
        if length is not None:
            return list(self.docs[: int(length)])
        return list(self.docs)

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])
        self.inserts: list[dict] = []
        self.upserts: list[dict] = []
        self.indexes: list[Any] = []

    def find(self, query=None, projection=None, sort=None, limit=None):
        q = query or {}

        def _match(d: dict) -> bool:
            for k, v in q.items():
                if k == "$or":
                    if not any(all(d.get(kk) == vv for kk, vv in cond.items())
                                for cond in v):
                        return False
                    continue
                if isinstance(v, dict):
                    if "$gte" in v and d.get(k) is not None and d[k] < v["$gte"]:
                        return False
                    if "$in" in v and d.get(k) not in v["$in"]:
                        return False
                else:
                    if d.get(k) != v:
                        return False
            return True

        matched = [d for d in self.docs if _match(d)]
        cur = _AsyncCursor(matched)
        if limit is not None:
            cur.limit(limit)
        return cur

    async def find_one(self, query=None, projection=None, sort=None):
        cur = self.find(query or {})
        async for d in cur:
            return d
        return None

    async def insert_many(self, docs, ordered=False):
        self.inserts.extend(list(docs))
        self.docs.extend(list(docs))

        class _R:
            inserted_ids = [object() for _ in docs]
        return _R()

    async def insert_one(self, doc):
        self.inserts.append(doc)
        self.docs.append(doc)

    async def update_one(self, query, update, upsert=False):
        # Crude upsert: find first matching doc, $set fields; else insert.
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set") or {})
                return type("R", (), {"matched_count": 1,
                                       "modified_count": 1,
                                       "upserted_id": None})()
        if upsert:
            new_doc = {**query, **(update.get("$set") or {})}
            self.docs.append(new_doc)
            self.upserts.append(new_doc)
            return type("R", (), {"matched_count": 0,
                                   "modified_count": 0,
                                   "upserted_id": object()})()
        return type("R", (), {"matched_count": 0,
                               "modified_count": 0,
                               "upserted_id": None})()

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))


class FakeDB:
    def __init__(self):
        self.pick_runs = FakeCollection()
        self.picks = FakeCollection()
        self.odds_snapshots = FakeCollection()
        self.odds_event_id_mappings = FakeCollection()


# ════════════════════════════════════════════════════════════════════════
# Pure helpers
# ════════════════════════════════════════════════════════════════════════
class TestPureHelpers:
    def test_normalise_team_lowercases_and_strips(self):
        assert lom.normalise_team("Manchester City") == "manchestercity"
        assert lom.normalise_team("Real Madrid C.F.") == "realmadridcf"
        assert lom.normalise_team(None) == ""
        assert lom.normalise_team("") == ""

    def test_find_event_in_list_exact_match(self):
        events = [
            {"id": "e1", "home_team": "Arsenal",  "away_team": "Chelsea"},
            {"id": "e2", "home_team": "Liverpool", "away_team": "Everton"},
        ]
        ev = lom.find_event_in_list(
            home_team="Arsenal", away_team="Chelsea", events=events,
        )
        assert ev and ev["id"] == "e1"

    def test_find_event_in_list_substring_match(self):
        # Realistic scenario: picks store "Real Madrid C.F." while the
        # Odds API returns "Real Madrid" — token-overlap (≥4-char tokens
        # ``real`` and ``madrid``) drives the match.
        events = [
            {"id": "e1", "home_team": "Real Madrid",
             "away_team": "Barcelona"},
        ]
        ev = lom.find_event_in_list(
            home_team="Real Madrid C.F.", away_team="FC Barcelona",
            events=events,
        )
        assert ev and ev["id"] == "e1"

    def test_find_event_in_list_token_overlap(self):
        # "Manchester City" vs "Manchester City FC" — shared tokens.
        events = [
            {"id": "e1", "home_team": "Manchester City FC",
             "away_team": "Tottenham Hotspur FC"},
        ]
        ev = lom.find_event_in_list(
            home_team="Manchester City", away_team="Tottenham Hotspur",
            events=events,
        )
        assert ev and ev["id"] == "e1"

    def test_find_event_in_list_no_match(self):
        events = [
            {"id": "e1", "home_team": "Arsenal", "away_team": "Chelsea"},
        ]
        ev = lom.find_event_in_list(
            home_team="Atletico Madrid", away_team="Real Sociedad",
            events=events,
        )
        assert ev is None

    def test_find_event_in_list_handles_malformed(self):
        # Non-dict entries are skipped; never raises.
        events = [None, "string", {"id": "e1", "home_team": "Arsenal",
                                    "away_team": "Chelsea"}]
        ev = lom.find_event_in_list(
            home_team="Arsenal", away_team="Chelsea", events=events,
        )
        assert ev and ev["id"] == "e1"


class TestExtractVisibleUniverse:
    def test_extracts_from_all_buckets(self):
        runs = [
            {
                "sport": "football",
                "payload": {
                    "picks": [
                        {"match_id": "m1", "home_team": "Arsenal",
                         "away_team": "Chelsea", "league": "EPL"},
                    ],
                    "rescued_picks": [
                        {"match_id": "m2", "home_team": "Real Madrid",
                         "away_team": "Barcelona"},
                    ],
                    "watchlist_manual_odds": [
                        {"match_id": "m3", "home_team": "PSG",
                         "away_team": "Marseille"},
                    ],
                    "structural_lean_requires_odds": [
                        {"match_id": "m4", "home_team": "Juventus",
                         "away_team": "Inter"},
                    ],
                },
            },
        ]
        out = lom.extract_visible_universe(run_docs=runs)
        ids = sorted([m["match_id"] for m in out])
        assert ids == ["m1", "m2", "m3", "m4"]

    def test_dedupes_across_runs(self):
        runs = [
            {"sport": "football",
             "payload": {"picks": [{"match_id": "m1", "home_team": "A",
                                     "away_team": "B"}]}},
            {"sport": "football",
             "payload": {"picks": [{"match_id": "m1", "home_team": "A",
                                     "away_team": "B"}]}},
        ]
        out = lom.extract_visible_universe(run_docs=runs)
        assert len(out) == 1

    def test_filters_by_sport(self):
        runs = [
            {"sport": "baseball",
             "payload": {"picks": [{"match_id": "b1", "home_team": "X",
                                     "away_team": "Y"}]}},
            {"sport": "football",
             "payload": {"picks": [{"match_id": "f1", "home_team": "A",
                                     "away_team": "B"}]}},
        ]
        out = lom.extract_visible_universe(run_docs=runs,
                                            sport_filter="football")
        assert [m["match_id"] for m in out] == ["f1"]

    def test_ignores_malformed_entries(self):
        runs = [
            {"sport": "football",
             "payload": {"picks": [None, "garbage",
                                    {"home_team": "no_match_id"},
                                    {"match_id": "ok", "home_team": "A",
                                     "away_team": "B"}]}},
        ]
        out = lom.extract_visible_universe(run_docs=runs)
        assert [m["match_id"] for m in out] == ["ok"]


class TestEventPayloadToSnapshots:
    def test_explodes_per_bookmaker_and_market(self):
        ev = {
            "id": "evt-1",
            "bookmakers": [
                {"key": "pinnacle", "title": "Pinnacle",
                 "markets": [
                     {"key": "h2h", "outcomes": [
                         {"name": "Arsenal", "price": 2.10},
                         {"name": "Chelsea", "price": 3.50},
                         {"name": "Draw",    "price": 3.20},
                     ], "last_update": "2026-01-15T12:00:00Z"},
                     {"key": "totals", "outcomes": [
                         {"name": "Over",  "price": 1.85, "point": 2.5},
                         {"name": "Under", "price": 1.95, "point": 2.5},
                     ]},
                 ]},
                {"key": "betfair", "title": "Betfair",
                 "markets": [
                     {"key": "h2h", "outcomes": [
                         {"name": "Arsenal", "price": 2.12},
                         {"name": "Chelsea", "price": 3.55},
                         {"name": "Draw",    "price": 3.18},
                     ]},
                 ]},
            ],
        }
        ts = datetime.now(timezone.utc)
        snaps = lom.event_payload_to_snapshots(
            match_id="m1", sport_key="soccer_epl", event_id="evt-1",
            event_payload=ev, fetched_at=ts, quota_remaining=400,
        )
        # 2 bookmakers × markets: pinnacle has 2 (h2h+totals), betfair 1
        assert len(snaps) == 3
        assert all(s["source"] == lom.SOURCE_NAME for s in snaps)
        assert all(s["match_id"] == "m1" for s in snaps)
        assert all(s["event_id"] == "evt-1" for s in snaps)
        assert all(s["sport_key"] == "soccer_epl" for s in snaps)
        assert all(s["fetched_at"] == ts for s in snaps)
        # snapshot_at alias exists for legacy index compatibility.
        assert all(s["snapshot_at"] == ts for s in snaps)
        # Quota propagated.
        assert all(s["quota_remaining"] == 400 for s in snaps)
        # Totals retain `point`.
        totals = [s for s in snaps if s["market"] == "totals"]
        assert totals and totals[0]["outcomes"][0]["point"] == 2.5

    def test_malformed_returns_empty(self):
        assert lom.event_payload_to_snapshots(
            match_id="m", sport_key="x", event_id="e",
            event_payload=None, fetched_at=datetime.now(timezone.utc),
        ) == []


# ════════════════════════════════════════════════════════════════════════
# Async behaviour
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_collect_visible_universe_reads_both_collections():
    db = FakeDB()
    now = datetime.now(timezone.utc)
    db.pick_runs.docs.append({
        "sport": "football", "generated_at": now,
        "payload": {"picks": [
            {"match_id": "from_pick_runs", "home_team": "A",
             "away_team": "B"}
        ]},
    })
    db.picks.docs.append({
        "sport": "football", "generated_at": now,
        "payload": {"picks": [
            {"match_id": "from_picks", "home_team": "C", "away_team": "D"}
        ]},
    })
    out = await lom.collect_visible_universe(db, sport="football")
    ids = sorted(m["match_id"] for m in out)
    assert ids == ["from_pick_runs", "from_picks"]


@pytest.mark.asyncio
async def test_resolve_event_id_uses_cache():
    db = FakeDB()
    db.odds_event_id_mappings.docs.append({
        "match_id": "m1", "event_id": "evt-cached",
        "sport_key": "soccer_epl",
    })

    async def _fail_fetch(*args, **kwargs):
        raise AssertionError("fetch_events should NOT be called on cache hit")

    out = await lom.resolve_event_id(
        db,
        match={"match_id": "m1", "home_team": "A", "away_team": "B"},
        sport_keys=["soccer_epl"],
        events_cache={},
        fetch_events=_fail_fetch,
    )
    assert out and out["event_id"] == "evt-cached"
    assert out["from_cache"] is True


@pytest.mark.asyncio
async def test_resolve_event_id_persists_new_mapping():
    db = FakeDB()

    async def _fake_fetch(*, sport, **kwargs):
        if sport == "soccer_epl":
            return {"events": [
                {"id": "evt-99", "sport_key": "soccer_epl",
                 "home_team": "Arsenal", "away_team": "Chelsea",
                 "commence_time": "2026-01-15T15:00:00Z"},
            ], "quota": {"remaining": 300, "used": 5}}
        return {"events": [], "quota": {"remaining": 300, "used": 5}}

    out = await lom.resolve_event_id(
        db,
        match={"match_id": "m_new", "home_team": "Arsenal",
                "away_team": "Chelsea"},
        sport_keys=["soccer_epl", "soccer_spain_la_liga"],
        events_cache={},
        fetch_events=_fake_fetch,
    )
    assert out and out["event_id"] == "evt-99"
    assert out["sport_key"] == "soccer_epl"
    assert out["from_cache"] is False
    # Mapping persisted (upserted).
    assert any(d.get("match_id") == "m_new" and d.get("event_id") == "evt-99"
                for d in db.odds_event_id_mappings.docs)


@pytest.mark.asyncio
async def test_resolve_event_id_returns_none_when_no_match():
    db = FakeDB()

    async def _fake_fetch(*, sport, **kwargs):
        return {"events": [
            {"id": "evt-1", "home_team": "Some Other",
             "away_team": "Random Team"},
        ], "quota": {}}

    out = await lom.resolve_event_id(
        db,
        match={"match_id": "m_x", "home_team": "Arsenal",
                "away_team": "Chelsea"},
        sport_keys=["soccer_epl"],
        events_cache={},
        fetch_events=_fake_fetch,
    )
    assert out is None
    # No mapping was persisted.
    assert all(d.get("match_id") != "m_x" for d in db.odds_event_id_mappings.docs)


@pytest.mark.asyncio
async def test_run_cycle_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("LIVE_ODDS_ENABLED", "false")
    db = FakeDB()

    async def _explode(*args, **kwargs):
        raise AssertionError("fetch must not run when disabled")

    report = await lom.run_cycle(
        db,
        fetch_events=_explode,
        fetch_current_odds=_explode,
    )
    assert report["enabled"] is False
    assert "DISABLED" in report["reasons"]
    assert report["snapshots_written"] == 0
    assert db.odds_snapshots.inserts == []


@pytest.mark.asyncio
async def test_run_cycle_empty_universe(monkeypatch):
    monkeypatch.setenv("LIVE_ODDS_ENABLED", "true")
    db = FakeDB()
    # No pick_runs / picks docs at all.

    async def _no_fetch_events(*args, **kwargs):
        raise AssertionError("must not fetch when universe is empty")

    report = await lom.run_cycle(
        db,
        fetch_events=_no_fetch_events,
        fetch_current_odds=_no_fetch_events,
    )
    assert report["enabled"] is True
    assert report["matches_total"] == 0
    assert "EMPTY_UNIVERSE" in report["reasons"]
    assert db.odds_snapshots.inserts == []


@pytest.mark.asyncio
async def test_run_cycle_persists_snapshots_end_to_end(monkeypatch):
    monkeypatch.setenv("LIVE_ODDS_ENABLED", "true")
    monkeypatch.setenv("LIVE_ODDS_SPORTS", "soccer_epl")
    db = FakeDB()
    now = datetime.now(timezone.utc)
    db.pick_runs.docs.append({
        "sport": "football", "generated_at": now,
        "payload": {"picks": [
            {"match_id": "m1", "home_team": "Arsenal",
             "away_team": "Chelsea"}
        ]},
    })

    async def _fake_events(*, sport, **kwargs):
        return {"events": [
            {"id": "evt-1", "home_team": "Arsenal", "away_team": "Chelsea"}
        ], "quota": {"remaining": 500, "used": 1, "last_cost": 1}}

    async def _fake_current(*, sport, event_ids, **kwargs):
        assert event_ids == ["evt-1"], "must filter by resolved event ids"
        return {
            "events": [{
                "id": "evt-1",
                "home_team": "Arsenal", "away_team": "Chelsea",
                "bookmakers": [{
                    "key": "pinnacle", "title": "Pinnacle",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Arsenal", "price": 2.10},
                            {"name": "Chelsea", "price": 3.50},
                            {"name": "Draw",    "price": 3.20},
                        ],
                        "last_update": "2026-01-15T12:00:00Z",
                    }],
                }],
            }],
            "quota": {"remaining": 499, "used": 2, "last_cost": 1},
        }

    report = await lom.run_cycle(
        db,
        fetch_events=_fake_events,
        fetch_current_odds=_fake_current,
    )
    assert report["ok"] is True
    assert report["matches_total"] == 1
    assert report["matches_with_event"] == 1
    assert report["missing_event_ids"] == 0
    assert report["snapshots_written"] == 1
    assert report["quota_remaining"] == 499

    # Persisted snapshot carries the discriminator.
    assert len(db.odds_snapshots.inserts) == 1
    snap = db.odds_snapshots.inserts[0]
    assert snap["source"] == lom.SOURCE_NAME
    assert snap["match_id"] == "m1"
    assert snap["event_id"] == "evt-1"
    assert snap["sport_key"] == "soccer_epl"
    assert snap["market"] == "h2h"


@pytest.mark.asyncio
async def test_run_cycle_handles_missing_event_id(monkeypatch, caplog):
    """``ODDS_EVENT_ID_MISSING`` must be a soft auditable failure."""
    monkeypatch.setenv("LIVE_ODDS_ENABLED", "true")
    db = FakeDB()
    now = datetime.now(timezone.utc)
    db.pick_runs.docs.append({
        "sport": "football", "generated_at": now,
        "payload": {"picks": [
            {"match_id": "m_unknown", "home_team": "Some Team A",
             "away_team": "Some Team B"}
        ]},
    })

    async def _fake_events(*, sport, **kwargs):
        # The Odds API doesn't list this fixture (e.g. lower-tier match).
        return {"events": [], "quota": {"remaining": 100}}

    async def _fail_current(*args, **kwargs):
        raise AssertionError(
            "fetch_current_odds must NOT run when no event_ids are resolved")

    with caplog.at_level("INFO"):
        report = await lom.run_cycle(
            db,
            fetch_events=_fake_events,
            fetch_current_odds=_fail_current,
        )
    assert report["ok"] is True
    assert report["matches_total"] == 1
    assert report["matches_with_event"] == 0
    assert report["missing_event_ids"] == 1
    assert report["snapshots_written"] == 0
    assert "NO_RESOLVABLE_EVENT_IDS" in report["reasons"]
    assert any("ODDS_EVENT_ID_MISSING" in rec.message
                for rec in caplog.records)


@pytest.mark.asyncio
async def test_run_cycle_fail_soft_when_fetch_current_returns_none(monkeypatch):
    monkeypatch.setenv("LIVE_ODDS_ENABLED", "true")
    monkeypatch.setenv("LIVE_ODDS_SPORTS", "soccer_epl")
    db = FakeDB()
    now = datetime.now(timezone.utc)
    db.pick_runs.docs.append({
        "sport": "football", "generated_at": now,
        "payload": {"picks": [
            {"match_id": "m1", "home_team": "Arsenal",
             "away_team": "Chelsea"}
        ]},
    })

    async def _fake_events(*, sport, **kwargs):
        return {"events": [{"id": "evt-1", "home_team": "Arsenal",
                              "away_team": "Chelsea"}],
                "quota": {"remaining": 50}}

    async def _broken_current(*args, **kwargs):
        return None  # simulate non-2xx / network drop

    report = await lom.run_cycle(
        db,
        fetch_events=_fake_events,
        fetch_current_odds=_broken_current,
    )
    # Cycle still succeeds (no exception), with a clear reason code.
    assert report["ok"] is True
    assert report["snapshots_written"] == 0
    assert any(r.startswith("FETCH_FAILED") for r in report["reasons"])


# ════════════════════════════════════════════════════════════════════════
# Config / status surface
# ════════════════════════════════════════════════════════════════════════
class TestConfig:
    def test_defaults_when_no_env(self, monkeypatch):
        for k in ("LIVE_ODDS_ENABLED", "LIVE_ODDS_REFRESH_SECONDS",
                  "LIVE_ODDS_SPORTS", "LIVE_ODDS_MARKETS",
                  "LIVE_ODDS_REGIONS", "LIVE_ODDS_LOOKBACK_HOURS",
                  "LIVE_ODDS_MAX_MATCHES", "LIVE_ODDS_QUOTA_MIN"):
            monkeypatch.delenv(k, raising=False)
        cfg = lom.get_config()
        assert cfg["enabled"] is False
        assert cfg["refresh_seconds"] == lom.DEFAULT_REFRESH_SECONDS
        assert "soccer_epl" in cfg["sports"]
        assert cfg["markets"] == lom.DEFAULT_MARKETS
        assert cfg["regions"] == lom.DEFAULT_REGIONS
        assert cfg["max_matches"] == lom.DEFAULT_MAX_MATCHES

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("LIVE_ODDS_ENABLED", "true")
        monkeypatch.setenv("LIVE_ODDS_REFRESH_SECONDS", "600")
        monkeypatch.setenv("LIVE_ODDS_SPORTS", "soccer_epl, soccer_uefa_champs_league")
        monkeypatch.setenv("LIVE_ODDS_MARKETS", "h2h")
        cfg = lom.get_config()
        assert cfg["enabled"] is True
        assert cfg["refresh_seconds"] == 600
        assert cfg["sports"] == ["soccer_epl", "soccer_uefa_champs_league"]
        assert cfg["markets"] == "h2h"


class TestRegisterJobs:
    def test_register_jobs_noop_when_disabled(self, monkeypatch):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        monkeypatch.setenv("LIVE_ODDS_ENABLED", "false")
        sch = AsyncIOScheduler(timezone="UTC")
        assert lom.register_jobs(sch, db=object()) is False
        # No job got added.
        assert not [j for j in sch.get_jobs() if j.id == "live_odds_monitor"]

    def test_register_jobs_adds_when_enabled(self, monkeypatch):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        monkeypatch.setenv("LIVE_ODDS_ENABLED", "true")
        monkeypatch.setenv("LIVE_ODDS_REFRESH_SECONDS", "120")
        sch = AsyncIOScheduler(timezone="UTC")
        try:
            ok = lom.register_jobs(sch, db=object())
            assert ok is True
            assert any(j.id == "live_odds_monitor" for j in sch.get_jobs())
        finally:
            try:
                sch.shutdown(wait=False)
            except Exception:
                pass


# ════════════════════════════════════════════════════════════════════════
# Sprint-E.1 · Client smoke (fetch_events / fetch_current_odds)
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_client_fetch_events_returns_none_without_api_key(monkeypatch):
    from services.external_sources import the_odds_api_client as cli
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    out = await cli.fetch_events(sport="soccer_epl")
    assert out is None


@pytest.mark.asyncio
async def test_client_fetch_current_odds_returns_none_without_api_key(monkeypatch):
    from services.external_sources import the_odds_api_client as cli
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    out = await cli.fetch_current_odds(sport="soccer_epl")
    assert out is None


def test_quota_headers_parse_robust():
    from services.external_sources import the_odds_api_client as cli

    class _H(dict):
        pass
    h = _H({"x-requests-remaining": "412", "x-requests-used": "88",
             "x-requests-last": "1"})
    q = cli._extract_quota_headers(h)
    assert q == {"remaining": 412, "used": 88, "last_cost": 1}

    # Missing headers → None values, no exception.
    q2 = cli._extract_quota_headers({})
    assert q2 == {"remaining": None, "used": None, "last_cost": None}
