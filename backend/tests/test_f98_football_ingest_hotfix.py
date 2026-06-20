"""F98 — Tests para los hotfixes del ingestador de football.

Cobertura:
  * `thestatsapi_fixtures_adapter._normalise_fixture` ahora respeta
    ``utc_date`` (regresión del 2026-05 que dejaba "vacíos" todos los
    fixtures de TheStatsAPI).
  * `scheduler._job_purge_stale_upcoming_matches`:
    - archiva fixtures con `kickoff_ts < now-24h` y status no terminal.
    - archiva fixtures sin `kickoff_ts` pero con `kickoff_iso` viejo.
    - NO archiva fixtures terminales (FT/AET/PEN/CANC/PST).
    - hard-deletea fixtures >14 días.
    - es idempotente (segunda corrida no archiva nada nuevo).
  * `server._filter_upcoming_candidates` filtra `is_archived_stale=True`
    antes del fixture gate (defense-in-depth UI).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


# ════════════════════════════════════════════════════════════════════════
# F98 — TheStatsAPI fixtures adapter: utc_date mapping
# ════════════════════════════════════════════════════════════════════════
class TestUtcDateMapping:
    def test_utc_date_with_milliseconds_is_parsed(self):
        from services.external_sources.thestatsapi_fixtures_adapter import (
            _normalise_fixture,
        )
        raw = {
            "id":         "mt_153637559",
            "status":     "scheduled",
            "utc_date":   "2026-06-21T22:00:00.000Z",
            "home_team":  {"id": "tm_41769", "name": "Uruguay"},
            "away_team":  {"id": "tm_53237", "name": "Cape Verde"},
        }
        out = _normalise_fixture(raw)
        assert out is not None, "F98 regression: utc_date must be parsed"
        assert out["fixture"]["timestamp"] is not None
        assert out["fixture"]["status"]["short"] == "NS"
        assert out["teams"]["home"]["name"] == "Uruguay"
        assert out["teams"]["away"]["name"] == "Cape Verde"

    def test_utc_date_without_milliseconds_also_works(self):
        from services.external_sources.thestatsapi_fixtures_adapter import (
            _normalise_fixture,
        )
        raw = {
            "id":         "mt_X",
            "status":     "live",
            "utc_date":   "2026-06-20T18:00:00Z",
            "home_team":  {"id": "h1", "name": "A"},
            "away_team":  {"id": "a1", "name": "B"},
        }
        out = _normalise_fixture(raw)
        assert out is not None
        assert out["fixture"]["status"]["short"] == "1H"

    def test_legacy_date_key_still_works(self):
        """Back-compat: rows using ``date`` instead of ``utc_date`` must
        keep parsing OK."""
        from services.external_sources.thestatsapi_fixtures_adapter import (
            _normalise_fixture,
        )
        raw = {
            "id":         "mt_legacy",
            "status":     "scheduled",
            "date":       "2026-06-21T22:00:00+00:00",
            "home_team":  {"id": "h1", "name": "A"},
            "away_team":  {"id": "a1", "name": "B"},
        }
        out = _normalise_fixture(raw)
        assert out is not None

    def test_no_timestamp_at_all_drops_row(self):
        from services.external_sources.thestatsapi_fixtures_adapter import (
            _normalise_fixture,
        )
        raw = {
            "id":         "mt_no_ts",
            "status":     "scheduled",
            "home_team":  {"id": "h1", "name": "A"},
            "away_team":  {"id": "a1", "name": "B"},
        }
        assert _normalise_fixture(raw) is None


# ════════════════════════════════════════════════════════════════════════
# F98 — Purge job
# ════════════════════════════════════════════════════════════════════════
class _Counter:
    def __init__(self):
        self.modified_count = 0
        self.deleted_count  = 0


class _AsyncCursor:
    def __init__(self, docs):
        self._d = list(docs)

    def __aiter__(self):
        self._i = iter(self._d)
        return self

    async def __anext__(self):
        try:
            return next(self._i)
        except StopIteration:
            raise StopAsyncIteration


def _matches_query(doc: dict, q: dict) -> bool:
    """Tiny Mongo-like predicate evaluator (only the operators the
    purge query needs)."""
    for k, v in q.items():
        if k == "$or":
            if not any(_matches_query(doc, sub) for sub in v):
                return False
            continue
        if k == "$and":
            if not all(_matches_query(doc, sub) for sub in v):
                return False
            continue
        actual = doc.get(k, _MISSING)
        if isinstance(v, dict):
            for op, val in v.items():
                if op == "$ne":
                    if actual == val:
                        return False
                    if val is None and actual is _MISSING:
                        return False
                elif op == "$nin":
                    if actual in val:
                        return False
                elif op == "$lt":
                    if actual is _MISSING or actual is None:
                        return False
                    if not (actual < val):
                        return False
                elif op == "$exists":
                    has = actual is not _MISSING
                    if has != val:
                        return False
                else:
                    raise NotImplementedError(f"op {op!r} not implemented")
        else:
            if actual is _MISSING or actual != v:
                return False
    return True


_MISSING = object()


class _AsyncCollection:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def count_documents(self, q):
        return sum(1 for d in self.docs if _matches_query(d, q))

    async def update_many(self, q, update):
        c = _Counter()
        set_ops = update.get("$set", {})
        for d in self.docs:
            if _matches_query(d, q):
                d.update(set_ops)
                c.modified_count += 1
        return c

    async def delete_many(self, q):
        c = _Counter()
        keep = []
        for d in self.docs:
            if _matches_query(d, q):
                c.deleted_count += 1
            else:
                keep.append(d)
        self.docs = keep
        return c


class _AsyncDB:
    def __init__(self):
        self.collections: dict[str, _AsyncCollection] = {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _AsyncCollection()
        return self.collections[name]

    def __getattr__(self, name):
        return self[name]


def _make_fixture(*, match_id="m", hours_ago=48, status="NS",
                   has_ts=True, has_iso=True, **extra):
    now = datetime.now(timezone.utc)
    kt = now - timedelta(hours=hours_ago)
    doc = {
        "match_id":     match_id,
        "sport":        "football",
        "status_short": status,
        "home_team":    {"name": "H"},
        "away_team":    {"name": "A"},
    }
    if has_ts:
        doc["kickoff_ts"] = kt.timestamp()
    if has_iso:
        doc["kickoff_iso"] = kt.isoformat()
    doc.update(extra)
    return doc


class TestPurgeStaleUpcoming:
    @pytest.mark.asyncio
    async def test_archives_stale_ns_fixtures(self):
        from services.scheduler import _job_purge_stale_upcoming_matches
        db = _AsyncDB()
        db["matches"].docs = [
            _make_fixture(match_id="m-old-1", hours_ago=48, status="NS"),
            _make_fixture(match_id="m-old-2", hours_ago=72, status="NS"),
            _make_fixture(match_id="m-fresh", hours_ago=2,  status="NS"),
        ]
        await _job_purge_stale_upcoming_matches(db)
        archived = [d for d in db["matches"].docs if d.get("is_archived_stale")]
        assert {d["match_id"] for d in archived} == {"m-old-1", "m-old-2"}
        # m-fresh remains untouched.
        fresh = next(d for d in db["matches"].docs if d["match_id"] == "m-fresh")
        assert not fresh.get("is_archived_stale")

    @pytest.mark.asyncio
    async def test_does_not_archive_terminal_statuses(self):
        from services.scheduler import _job_purge_stale_upcoming_matches
        db = _AsyncDB()
        db["matches"].docs = [
            _make_fixture(match_id="m-ft",   hours_ago=48, status="FT"),
            _make_fixture(match_id="m-pst",  hours_ago=72, status="PST"),
            _make_fixture(match_id="m-canc", hours_ago=48, status="CANC"),
        ]
        await _job_purge_stale_upcoming_matches(db)
        for d in db["matches"].docs:
            assert not d.get("is_archived_stale"), \
                f"{d['match_id']} (status={d['status_short']}) should NOT be archived"

    @pytest.mark.asyncio
    async def test_archives_fixtures_without_kickoff_ts(self):
        """ESPN-sourced rows have ``kickoff_iso`` but no ``kickoff_ts``."""
        from services.scheduler import _job_purge_stale_upcoming_matches
        db = _AsyncDB()
        db["matches"].docs = [
            _make_fixture(
                match_id="espn-old", hours_ago=48, status="NS",
                has_ts=False,  # only kickoff_iso, no numeric ts
            ),
        ]
        await _job_purge_stale_upcoming_matches(db)
        archived = [d for d in db["matches"].docs if d.get("is_archived_stale")]
        assert len(archived) == 1
        assert archived[0]["match_id"] == "espn-old"

    @pytest.mark.asyncio
    async def test_hard_deletes_after_14_days(self):
        from services.scheduler import _job_purge_stale_upcoming_matches
        db = _AsyncDB()
        db["matches"].docs = [
            _make_fixture(match_id="m-very-old", hours_ago=24 * 30, status="NS"),
            _make_fixture(match_id="m-recent-stale", hours_ago=48,    status="NS"),
        ]
        await _job_purge_stale_upcoming_matches(db)
        ids = {d["match_id"] for d in db["matches"].docs}
        assert "m-very-old"      not in ids, "should be hard-deleted"
        assert "m-recent-stale"  in ids,     "should only be soft-archived"

    @pytest.mark.asyncio
    async def test_idempotent_second_run(self):
        from services.scheduler import _job_purge_stale_upcoming_matches
        db = _AsyncDB()
        db["matches"].docs = [_make_fixture(match_id="m-old", hours_ago=48)]
        await _job_purge_stale_upcoming_matches(db)
        snap1 = [d.get("is_archived_stale") for d in db["matches"].docs]
        await _job_purge_stale_upcoming_matches(db)
        snap2 = [d.get("is_archived_stale") for d in db["matches"].docs]
        assert snap1 == snap2  # no new archival on the 2nd run

    @pytest.mark.asyncio
    async def test_fails_soft_when_db_raises(self):
        from services.scheduler import _job_purge_stale_upcoming_matches
        db = _AsyncDB()
        # Force update_many to blow up.
        async def boom(*a, **kw):
            raise RuntimeError("mongo unavailable")
        db["matches"].docs = [_make_fixture(match_id="m-old", hours_ago=48)]
        db["matches"].update_many = boom
        # MUST NOT raise.
        await _job_purge_stale_upcoming_matches(db)


# ════════════════════════════════════════════════════════════════════════
# F98 — Server filter: is_archived_stale dropped pre-gate
# ════════════════════════════════════════════════════════════════════════
class TestServerFilterDropsArchivedStale:
    def test_archived_stale_dropped_before_gate(self):
        from server import _filter_upcoming_candidates
        archived = {
            "match_id":           "m-old",
            "sport":              "football",
            "is_archived_stale":  True,
            "status_short":       "NS",
            "kickoff_iso":        "2026-05-19T18:30:00+00:00",
            "kickoff_ts":         1779215400,
            "home_team":          {"name": "Bournemouth"},
            "away_team":          {"name": "Manchester City"},
        }
        fresh = {
            "match_id":      "m-fresh",
            "sport":         "football",
            "status_short":  "NS",
            "kickoff_iso":   (datetime.now(timezone.utc)
                              + timedelta(hours=3)).isoformat(),
            "kickoff_ts":    (datetime.now(timezone.utc)
                              + timedelta(hours=3)).timestamp(),
            "home_team":     {"name": "Uruguay"},
            "away_team":     {"name": "Cape Verde"},
        }
        kept = _filter_upcoming_candidates([archived, fresh])
        ids = {m["match_id"] for m in kept}
        assert "m-old"   not in ids
        assert "m-fresh" in ids
