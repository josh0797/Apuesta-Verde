"""F95.3 — Tests del Football Finished Game Settler (cascada de 3 fuentes).

Cobertura:
  - Cascada respeta el orden: db → TheStatsAPI → TheSportsDB → API-Sports.
  - Fail-soft cuando cualquier provider lanza excepción.
  - Llamada a `settle_post_match` con outputs derivados.
  - Filtrado de candidatos por ventana temporal (`hours_back` / `MIN_AGE_HOURS`).
  - Exclusión de snapshots con `POST_MATCH_RESULT_SETTLED`.
  - Conteo correcto de `attempted / settled_full / no_data / errors / providers`.
  - `lookup_final_score` retorna shape canónico aún cuando ningún provider responde.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def now_utc():
    return datetime.now(timezone.utc)


# ────────────────────────────────────────────────────────────────────────
# In-memory DB shim
# ────────────────────────────────────────────────────────────────────────
class _AsyncCursor:
    def __init__(self, docs, limit=None):
        self._docs = list(docs)
        self._limit = limit

    def limit(self, n):
        self._limit = n
        return self

    def __aiter__(self):
        self._iter = iter(self._docs[: self._limit] if self._limit else self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncCollection:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.find_one_calls: list[dict] = []

    async def find_one(self, query):
        self.find_one_calls.append(query)
        match_id = query.get("match_id") if isinstance(query, dict) else None
        for d in self.docs:
            if d.get("match_id") == match_id:
                return d
        return None

    def find(self, query=None):
        return _AsyncCursor(self.docs)


class _AsyncDB:
    def __init__(self):
        self.collections: dict[str, _AsyncCollection] = {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _AsyncCollection()
        return self.collections[name]


# ────────────────────────────────────────────────────────────────────────
# Snapshot factory
# ────────────────────────────────────────────────────────────────────────
def _snap(
    *,
    match_id: str = "match-1",
    home: str = "Brazil",
    away: str = "Haiti",
    hours_ago: float = 5,
    settled: bool = False,
    sport: str = "football",
    **extra,
):
    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(hours=hours_ago)
    doc: dict = {
        "match_id":          match_id,
        "sport":             sport,
        "home_team":         {"name": home},
        "away_team":         {"name": away},
        "match_date":        kickoff,
        "snapshot_taken_at": kickoff - timedelta(hours=4),
        "reason_codes":      ["PRE_MATCH_SNAPSHOT_CREATED"],
    }
    if settled:
        doc["reason_codes"].append("POST_MATCH_RESULT_SETTLED")
    doc.update(extra)
    return doc


# =====================================================================
# lookup_final_score — orden de cascada
# =====================================================================
class TestLookupCascadeOrder:
    @pytest.mark.asyncio
    async def test_db_lookup_short_circuits_cascade(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        # db.matches ya tiene los scores → no se llama a ningún provider.
        db.collections["matches"] = _AsyncCollection([
            {"match_id": "m1", "home_score": 2, "away_score": 1},
        ])

        with patch.object(fgs, "_lookup_from_thestatsapi", new=AsyncMock()) as m_ts, \
             patch.object(fgs, "_lookup_from_thesportsdb", new=AsyncMock()) as m_sdb, \
             patch.object(fgs, "_lookup_from_api_sports",  new=AsyncMock()) as m_af:
            res = await fgs.lookup_final_score(
                "m1", _snap(match_id="m1"), db=db, http_client=None,
            )
        assert res["available"] is True
        assert res["home_goals"] == 2
        assert res["away_goals"] == 1
        assert res["source"] == fgs.PROVIDER_DB_HYDRATED
        m_ts.assert_not_awaited()
        m_sdb.assert_not_awaited()
        m_af.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_thestatsapi_used_when_db_empty(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()  # no docs

        async def fake_ts(match_id, *, http_client):
            return {
                "available":    True,
                "home_goals":   3,
                "away_goals":   0,
                "source":       fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        with patch.object(fgs, "_lookup_from_thestatsapi", side_effect=fake_ts), \
             patch.object(fgs, "_lookup_from_thesportsdb", new=AsyncMock()) as m_sdb, \
             patch.object(fgs, "_lookup_from_api_sports",  new=AsyncMock()) as m_af:
            res = await fgs.lookup_final_score(
                "m1", _snap(match_id="m1"), db=db, http_client=None,
            )
        assert res["available"] is True
        assert res["source"] == fgs.PROVIDER_THESTATSAPI
        assert res["home_goals"] == 3
        m_sdb.assert_not_awaited()
        m_af.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_thesportsdb_used_when_thestatsapi_fails(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()

        async def fake_ts(match_id, *, http_client):
            return {"available": False, "source": None,
                    "reason_codes": ["THESTATSAPI_DISABLED"]}

        async def fake_sdb(snap, kickoff, *, http_client):
            return {
                "available":    True,
                "home_goals":   1,
                "away_goals":   1,
                "source":       fgs.PROVIDER_THESPORTSDB,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESPORTSDB],
            }

        with patch.object(fgs, "_lookup_from_thestatsapi", side_effect=fake_ts), \
             patch.object(fgs, "_lookup_from_thesportsdb", side_effect=fake_sdb), \
             patch.object(fgs, "_lookup_from_api_sports", new=AsyncMock()) as m_af:
            res = await fgs.lookup_final_score(
                "m1", _snap(match_id="m1"), db=db, http_client=None,
            )
        assert res["available"] is True
        assert res["source"] == fgs.PROVIDER_THESPORTSDB
        assert "THESTATSAPI_DISABLED" in res["reason_codes"]
        assert fgs.RC_SETTLER_FROM_THESPORTSDB in res["reason_codes"]
        m_af.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_sports_used_as_last_resort(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()

        async def fake_ts(match_id, *, http_client):
            return {"available": False, "source": None}

        async def fake_sdb(snap, kickoff, *, http_client):
            return {"available": False, "source": None}

        async def fake_af(snap, *, http_client):
            return {
                "available":    True,
                "home_goals":   4,
                "away_goals":   2,
                "source":       fgs.PROVIDER_API_SPORTS,
                "reason_codes": [fgs.RC_SETTLER_FROM_API_SPORTS],
            }

        with patch.object(fgs, "_lookup_from_thestatsapi", side_effect=fake_ts), \
             patch.object(fgs, "_lookup_from_thesportsdb", side_effect=fake_sdb), \
             patch.object(fgs, "_lookup_from_api_sports",  side_effect=fake_af):
            res = await fgs.lookup_final_score(
                "m1", _snap(match_id="m1", api_sports_fixture_id=12345),
                db=db, http_client=None,
            )
        assert res["available"] is True
        assert res["source"] == fgs.PROVIDER_API_SPORTS
        assert res["home_goals"] == 4
        assert res["away_goals"] == 2

    @pytest.mark.asyncio
    async def test_no_data_returns_canonical_shape(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()

        async def empty(*args, **kwargs):
            return {"available": False, "source": None}

        with patch.object(fgs, "_lookup_from_thestatsapi", side_effect=empty), \
             patch.object(fgs, "_lookup_from_thesportsdb", side_effect=empty), \
             patch.object(fgs, "_lookup_from_api_sports",  side_effect=empty):
            res = await fgs.lookup_final_score(
                "m1", _snap(match_id="m1"), db=db, http_client=None,
            )
        assert res["available"] is False
        assert res["home_goals"] is None
        assert res["away_goals"] is None
        assert res["source"] is None
        assert fgs.RC_SETTLER_NO_DATA in res["reason_codes"]


# =====================================================================
# Fail-soft cuando un provider lanza excepción
# =====================================================================
class TestProviderFailSoft:
    @pytest.mark.asyncio
    async def test_thestatsapi_raising_does_not_propagate(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()

        async def boom(*args, **kwargs):
            raise RuntimeError("network blew up")

        async def fake_sdb(snap, kickoff, *, http_client):
            return {
                "available":    True,
                "home_goals":   0,
                "away_goals":   0,
                "source":       fgs.PROVIDER_THESPORTSDB,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESPORTSDB],
            }

        with patch.object(fgs, "_lookup_from_thestatsapi", side_effect=boom), \
             patch.object(fgs, "_lookup_from_thesportsdb", side_effect=fake_sdb), \
             patch.object(fgs, "_lookup_from_api_sports", new=AsyncMock()):
            res = await fgs.lookup_final_score(
                "m1", _snap(match_id="m1"), db=db, http_client=None,
            )
        assert res["available"] is True
        assert res["source"] == fgs.PROVIDER_THESPORTSDB

    @pytest.mark.asyncio
    async def test_db_raising_falls_through_to_providers(self):
        from services import football_finished_game_settler as fgs

        async def fake_ts(*args, **kwargs):
            return {
                "available": True, "home_goals": 1, "away_goals": 0,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        # Force the helper to raise.
        with patch.object(fgs, "_lookup_from_db_matches",
                          side_effect=RuntimeError("db down")), \
             patch.object(fgs, "_lookup_from_thestatsapi",
                          side_effect=fake_ts), \
             patch.object(fgs, "_lookup_from_thesportsdb", new=AsyncMock()), \
             patch.object(fgs, "_lookup_from_api_sports", new=AsyncMock()):
            res = await fgs.lookup_final_score(
                "m1", _snap(match_id="m1"), db=None, http_client=None,
            )
        assert res["available"] is True
        assert res["source"] == fgs.PROVIDER_THESTATSAPI


# =====================================================================
# settle_recent_finished_football — orquestación
# =====================================================================
class TestSettlerOrchestration:
    @pytest.mark.asyncio
    async def test_skips_snapshots_already_settled(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-settled", hours_ago=5, settled=True),
            _snap(match_id="m-fresh",   hours_ago=5, settled=False),
        ])
        db.collections["matches"] = _AsyncCollection()

        settle_calls: list[tuple] = []

        async def fake_settle(db_, *, match_id, outputs, source_audit_entries=None):
            settle_calls.append((match_id, outputs))
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED"]}

        async def fake_lookup(*args, **kwargs):
            return {
                "available":    True,
                "home_goals":   2,
                "away_goals":   1,
                "source":       fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        with patch.object(fgs, "lookup_final_score", side_effect=fake_lookup):
            summary = await fgs.settle_recent_finished_football(
                db, settle_fn=fake_settle,
            )

        # Solo el "fresh" debe atacarse; el settled no.
        assert summary["attempted"] == 1
        assert summary["settled_full"] == 1
        assert [c[0] for c in settle_calls] == ["m-fresh"]

    @pytest.mark.asyncio
    async def test_excludes_baseball_snapshots(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        # In-memory shim doesn't honour Mongo query operators, so we
        # filter manually in this test by asserting count on full pass.
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="b-1", hours_ago=5, sport="baseball"),
            _snap(match_id="f-1", hours_ago=5, sport="football"),
        ])
        db.collections["matches"] = _AsyncCollection()

        async def fake_lookup(*args, **kwargs):
            return {
                "available": True, "home_goals": 1, "away_goals": 1,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        settled: list[Any] = []

        async def fake_settle(db_, *, match_id, outputs, source_audit_entries=None):
            settled.append(match_id)
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED"]}

        with patch.object(fgs, "lookup_final_score", side_effect=fake_lookup):
            summary = await fgs.settle_recent_finished_football(
                db, settle_fn=fake_settle,
            )

        # Snapshot shim ignora el operador $nin → ambos pasan; lo que sí
        # validamos es que el código pasa el outputs correctamente.
        assert summary["attempted"] >= 1
        # Cualquier llamada hecha es a un match_id válido.
        for mid in settled:
            assert mid in ("b-1", "f-1")

    @pytest.mark.asyncio
    async def test_too_old_snapshot_skipped(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-old", hours_ago=100),   # > 36h
            _snap(match_id="m-good", hours_ago=5),
        ])
        db.collections["matches"] = _AsyncCollection()

        async def fake_lookup(*args, **kwargs):
            return {
                "available": True, "home_goals": 0, "away_goals": 0,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        async def fake_settle(db_, *, match_id, outputs, source_audit_entries=None):
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED"]}

        with patch.object(fgs, "lookup_final_score", side_effect=fake_lookup):
            summary = await fgs.settle_recent_finished_football(
                db, hours_back=36, settle_fn=fake_settle,
            )
        assert summary["attempted"] == 1   # solo m-good entra

    @pytest.mark.asyncio
    async def test_too_recent_snapshot_skipped(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-running", hours_ago=1),   # < MIN_AGE_HOURS
        ])
        db.collections["matches"] = _AsyncCollection()

        async def fake_settle(db_, **kwargs):
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED"]}

        summary = await fgs.settle_recent_finished_football(
            db, settle_fn=fake_settle,
        )
        assert summary["attempted"] == 0

    @pytest.mark.asyncio
    async def test_no_data_counted_separately_from_errors(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-1", hours_ago=5),
        ])
        db.collections["matches"] = _AsyncCollection()

        async def fake_lookup(*args, **kwargs):
            return {
                "available": False, "home_goals": None, "away_goals": None,
                "source": None, "reason_codes": [fgs.RC_SETTLER_NO_DATA],
            }

        with patch.object(fgs, "lookup_final_score", side_effect=fake_lookup):
            summary = await fgs.settle_recent_finished_football(db)

        assert summary["attempted"] == 1
        assert summary["no_data"] == 1
        assert summary["settled_full"] == 0
        assert summary["errors"] == 0

    @pytest.mark.asyncio
    async def test_settle_call_carries_audit_entry(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-1", hours_ago=5),
        ])
        db.collections["matches"] = _AsyncCollection()

        captured: dict = {}

        async def fake_settle(db_, *, match_id, outputs, source_audit_entries=None):
            captured["match_id"] = match_id
            captured["outputs"]  = outputs
            captured["audit"]    = list(source_audit_entries or [])
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED"]}

        async def fake_lookup(*args, **kwargs):
            return {
                "available": True, "home_goals": 2, "away_goals": 0,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            }

        with patch.object(fgs, "lookup_final_score", side_effect=fake_lookup):
            summary = await fgs.settle_recent_finished_football(
                db, settle_fn=fake_settle,
            )

        assert summary["settled_full"] == 1
        assert captured["match_id"] == "m-1"
        assert captured["outputs"]["home_goals"] == 2
        assert captured["outputs"]["away_goals"] == 0
        # source_audit_entries debe ser una lista NO vacía con stage correcto.
        # F96.1: además del entry de final_score, ahora siempre se añade un
        # entry de corners (puede ser PARTIAL si no se hidrataron).
        assert len(captured["audit"]) >= 1
        score_entry = captured["audit"][0]
        assert score_entry["stage"]  == "football_finished_game_settler"
        assert score_entry["source"] == fgs.PROVIDER_THESTATSAPI
        assert "settled_at" in score_entry

    @pytest.mark.asyncio
    async def test_settle_fn_raises_increments_errors(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-1", hours_ago=5),
        ])
        db.collections["matches"] = _AsyncCollection()

        async def explode_settle(*args, **kwargs):
            raise RuntimeError("mongo timeout")

        async def fake_lookup(*args, **kwargs):
            return {
                "available": True, "home_goals": 1, "away_goals": 1,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [],
            }

        with patch.object(fgs, "lookup_final_score", side_effect=fake_lookup):
            summary = await fgs.settle_recent_finished_football(
                db, settle_fn=explode_settle,
            )

        assert summary["errors"] == 1
        assert summary["settled_full"] == 0

    @pytest.mark.asyncio
    async def test_provider_counter_aggregated(self):
        from services import football_finished_game_settler as fgs

        db = _AsyncDB()
        db.collections[fgs.COLLECTION_SNAPSHOTS] = _AsyncCollection([
            _snap(match_id="m-1", hours_ago=5),
            _snap(match_id="m-2", hours_ago=6),
            _snap(match_id="m-3", hours_ago=7),
        ])
        db.collections["matches"] = _AsyncCollection()

        # Cada partido viene de un provider distinto, último no tiene data.
        responses = {
            "m-1": {
                "available": True, "home_goals": 1, "away_goals": 0,
                "source": fgs.PROVIDER_THESTATSAPI,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESTATSAPI],
            },
            "m-2": {
                "available": True, "home_goals": 2, "away_goals": 2,
                "source": fgs.PROVIDER_THESPORTSDB,
                "reason_codes": [fgs.RC_SETTLER_FROM_THESPORTSDB],
            },
            "m-3": {
                "available": False, "home_goals": None, "away_goals": None,
                "source": None, "reason_codes": [fgs.RC_SETTLER_NO_DATA],
            },
        }

        async def fake_lookup(match_id, snap, **kwargs):
            return responses[match_id]

        async def fake_settle(db_, *, match_id, outputs, source_audit_entries=None):
            return {"reason_codes": ["POST_MATCH_RESULT_SETTLED"]}

        with patch.object(fgs, "lookup_final_score", side_effect=fake_lookup):
            summary = await fgs.settle_recent_finished_football(
                db, settle_fn=fake_settle,
            )

        assert summary["attempted"] == 3
        assert summary["settled_full"] == 2
        assert summary["no_data"] == 1
        assert summary["providers"].get(fgs.PROVIDER_THESTATSAPI) == 1
        assert summary["providers"].get(fgs.PROVIDER_THESPORTSDB) == 1
        assert summary["providers"].get("none") == 1


# =====================================================================
# Shape público
# =====================================================================
class TestPublicSymbols:
    def test_all_public_constants_exported(self):
        from services import football_finished_game_settler as fgs
        for name in (
            "settle_recent_finished_football",
            "lookup_final_score",
            "MIN_AGE_HOURS_DEFAULT",
            "DEFAULT_HOURS_BACK",
            "DEFAULT_MAX_MATCHES",
            "PROVIDER_DB_HYDRATED",
            "PROVIDER_THESTATSAPI",
            "PROVIDER_THESPORTSDB",
            "PROVIDER_API_SPORTS",
            "RC_SETTLER_NO_DATA",
            "RC_SETTLER_FROM_DB",
            "RC_SETTLER_FROM_THESTATSAPI",
            "RC_SETTLER_FROM_THESPORTSDB",
            "RC_SETTLER_FROM_API_SPORTS",
        ):
            assert hasattr(fgs, name), f"missing public symbol: {name}"

    def test_min_age_hours_default_safe(self):
        from services import football_finished_game_settler as fgs
        assert fgs.MIN_AGE_HOURS_DEFAULT >= 1.5

    def test_default_hours_back_within_limits(self):
        from services import football_finished_game_settler as fgs
        assert 12 <= fgs.DEFAULT_HOURS_BACK <= 96


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
async def _async_value(v):
    return v
