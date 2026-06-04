"""Tests for Live Recommendation History."""

from __future__ import annotations

import pytest

from services.live_recommendation_history import (
    settle_live_event_from_score,
    persist_live_recommendation_event,
    record_manual_live_event,
    settle_live_recommendation_event,
    query_live_recommendation_events,
    ensure_live_recommendation_indexes,
    COLLECTION,
)

# Reuse the fake DB from existing tests.
from tests.test_football_moneyball import _FakeDB  # type: ignore


# Patch _FakeColl for indexes update_one + count_documents.
class _PatchedColl:
    def __init__(self, inner):
        self._inner = inner

    async def update_one(self, q, update):
        for d in self._inner.docs:
            if all(d.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                if "$set" in update:
                    d.update(update["$set"])
                return
        return None


@pytest.fixture
def fake_db():
    db = _FakeDB()
    # Inject update_one on each collection on access.
    return db


# Monkey-patch FakeColl.update_one if missing.
from tests.test_football_moneyball import _FakeColl  # type: ignore

if not hasattr(_FakeColl, "update_one"):
    async def _update_one(self, q, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                if "$set" in update:
                    d.update(update["$set"])
                return None
        return None
    _FakeColl.update_one = _update_one  # type: ignore


# ─────────────────────────────────────────────────────────────────────
# Auto-settlement (pure)
# ─────────────────────────────────────────────────────────────────────
def _ev(market, selection="x"):
    return {"recommendation": {"market": market, "selection": selection}}


def test_settle_btts_yes_hits_at_1_1():
    s = settle_live_event_from_score(
        _ev("BTTS YES"), {"home": 1, "away": 1}, minute=54,
    )
    assert s["result"] == "hit"
    assert s["settled_score"] == "1-1"
    assert s["settled_minute"] == 54


def test_settle_btts_yes_pending_at_1_0():
    s = settle_live_event_from_score(_ev("BTTS YES"), {"home": 1, "away": 0})
    assert s["result"] == "pending"


def test_settle_btts_yes_miss_at_full_time_1_0():
    s = settle_live_event_from_score(
        _ev("BTTS YES"), {"home": 1, "away": 0}, match_ended=True,
    )
    assert s["result"] == "miss"


def test_settle_btts_no_at_1_1_miss():
    s = settle_live_event_from_score(_ev("BTTS NO"), {"home": 1, "away": 1})
    assert s["result"] == "miss"


def test_settle_over_3_5_pending_at_1_1():
    s = settle_live_event_from_score(_ev("Over 3.5"), {"home": 1, "away": 1})
    assert s["result"] == "pending"


def test_settle_over_2_5_hit_when_total_3():
    s = settle_live_event_from_score(_ev("Over 2.5"), {"home": 2, "away": 1})
    assert s["result"] == "hit"


def test_settle_under_2_5_miss_when_total_3():
    s = settle_live_event_from_score(_ev("Under 2.5"), {"home": 3, "away": 0})
    assert s["result"] == "miss"


def test_settle_under_3_5_hit_at_full_time_total_3():
    s = settle_live_event_from_score(
        _ev("Under 3.5"), {"home": 2, "away": 1}, match_ended=True,
    )
    assert s["result"] == "hit"


def test_settle_unknown_market_pending():
    s = settle_live_event_from_score(_ev("Moneyline Home"), {"home": 1, "away": 0})
    assert s["result"] == "pending"


# ─────────────────────────────────────────────────────────────────────
# Engine persist
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_persist_engine_event_dedup(fake_db):
    match = {
        "match_id":  "m1",
        "home_team": {"name": "France"},
        "away_team": {"name": "Ivory Coast"},
        "league":    {"name": "Friendlies"},
        "live_stats": {
            "minute": 42,
            "score": {"home": 1, "away": 0},
        },
    }
    reeval = {
        "live_state": "LIVE_VALUE_WINDOW",
        "recommended_action": "LIVE_ENTRY",
        "market": "BTTS YES",
        "selection": "Ambos equipos marcan: Sí",
        "confidence": 72,
        "live_snapshot": {"minute": 42, "score": {"home": 1, "away": 0}},
    }
    doc1 = await persist_live_recommendation_event(
        fake_db, user_id="u1", match=match, reeval_result=reeval,
        interpreter={"narrative": "BTTS support"},
    )
    assert doc1 is not None
    assert doc1["status"] == "open"
    assert doc1["source"] == "engine"
    # Same minute/market/score → dedup returns None.
    doc2 = await persist_live_recommendation_event(
        fake_db, user_id="u1", match=match, reeval_result=reeval,
    )
    assert doc2 is None


@pytest.mark.asyncio
async def test_persist_engine_event_skips_pass_action(fake_db):
    match = {"match_id": "m1", "home_team": {"name": "A"}, "away_team": {"name": "B"}}
    res = {"recommended_action": "PASS"}
    assert await persist_live_recommendation_event(
        fake_db, user_id="u1", match=match, reeval_result=res,
    ) is None


@pytest.mark.asyncio
async def test_persist_engine_event_supersedes_previous(fake_db):
    match = {
        "match_id": "m1",
        "home_team": {"name": "A"}, "away_team": {"name": "B"},
        "live_stats": {"minute": 42, "score": {"home": 1, "away": 0}},
    }
    # First event: BTTS YES
    await persist_live_recommendation_event(
        fake_db, user_id="u1", match=match,
        reeval_result={
            "recommended_action": "LIVE_ENTRY",
            "market": "BTTS YES",
            "selection": "BTTS",
            "live_snapshot": {"minute": 42, "score": {"home": 1, "away": 0}},
        },
    )
    # Second event: Over 3.5, different minute/score → DIFFERENT dedup key
    match2 = {**match, "live_stats": {"minute": 54, "score": {"home": 1, "away": 1}}}
    new_doc = await persist_live_recommendation_event(
        fake_db, user_id="u1", match=match2,
        reeval_result={
            "recommended_action": "LIVE_ENTRY",
            "market": "Over 3.5",
            "selection": "Más de 3.5 goles",
            "live_snapshot": {"minute": 54, "score": {"home": 1, "away": 1}},
        },
    )
    assert new_doc is not None
    # Original event should now be marked superseded.
    original = await fake_db[COLLECTION].find_one({"recommendation.market": "BTTS YES"})
    # Note: fake_db doesn't dot-resolve; verify via list traversal.
    docs = fake_db[COLLECTION].docs
    btts_doc = next((d for d in docs if (d.get("recommendation") or {}).get("market") == "BTTS YES"), None)
    assert btts_doc is not None
    assert btts_doc["status"] == "superseded"
    assert btts_doc["superseded_by_event_id"] == new_doc["event_id"]


@pytest.mark.asyncio
async def test_record_manual_event_without_match_doc(fake_db):
    payload = {
        "sport": "football",
        "match_id": "France-Ivory-2026-06-04",
        "match_label": "France vs Ivory Coast",
        "league": "Friendlies",
        "minute": 42,
        "score": {"home": 1, "away": 0, "label": "1-0"},
        "recommendation": {
            "market": "BTTS YES",
            "selection": "Ambos equipos marcan: Sí",
            "confidence": 72,
            "risk_level": "MEDIUM",
            "recommended_action": "LIVE_ENTRY",
            "title": "Ambos equipos marcan",
        },
        "reason": "Engine recomendó BTTS antes del empate.",
        "reason_codes": ["MANUAL_BACKFILL", "BTTS_LIVE_SUPPORT"],
        "outcome": {
            "result": "hit",
            "settled_minute": 54,
            "settled_score": "1-1",
            "settlement_reason": "Ambos equipos marcaron al 54'.",
        },
    }
    doc = await record_manual_live_event(fake_db, user_id="u1", payload=payload)
    assert doc is not None
    assert doc["source"] == "manual"
    assert doc["event_type"] == "manual_event"
    assert doc["status"] == "hit"
    assert doc["outcome"]["settled_score"] == "1-1"


@pytest.mark.asyncio
async def test_record_manual_event_rejects_missing_fields(fake_db):
    bad = {"sport": "football", "match_id": "m1"}  # missing minute + market
    assert await record_manual_live_event(fake_db, user_id="u", payload=bad) is None


@pytest.mark.asyncio
async def test_query_timeline_returns_events(fake_db):
    payload = {
        "sport": "football", "match_id": "mt1", "minute": 30,
        "score": {"home": 0, "away": 0, "label": "0-0"},
        "recommendation": {"market": "BTTS YES", "selection": "BTTS"},
    }
    await record_manual_live_event(fake_db, user_id="u1", payload=payload)
    items = await query_live_recommendation_events(
        fake_db, user_id="u1", sport="football", match_id="mt1",
    )
    assert len(items) == 1
    assert items[0]["match_id"] == "mt1"


@pytest.mark.asyncio
async def test_settle_event_updates_status(fake_db):
    doc = await record_manual_live_event(
        fake_db, user_id="u1",
        payload={
            "sport": "football", "match_id": "ms", "minute": 30,
            "score": {"home": 0, "away": 0},
            "recommendation": {"market": "BTTS YES"},
            "outcome": {"result": "pending"},
        },
    )
    ok = await settle_live_recommendation_event(
        fake_db, event_id=doc["event_id"], result="hit",
        settled_score="1-1", settled_minute=60,
        settlement_reason="BTTS YES cumplido.",
    )
    assert ok is True
    found = next(
        (d for d in fake_db[COLLECTION].docs if d["event_id"] == doc["event_id"]),
        None,
    )
    assert found["status"] == "hit"
    assert found["outcome"]["settled_score"] == "1-1"


@pytest.mark.asyncio
async def test_db_none_failsoft():
    assert await persist_live_recommendation_event(None, user_id="u", match={}) is None
    assert await record_manual_live_event(None, user_id="u", payload={}) is None
    assert await settle_live_recommendation_event(None, event_id="x", result="hit") is False
    assert await query_live_recommendation_events(None, user_id="u") == []
    assert (await ensure_live_recommendation_indexes(None))["available"] is False


@pytest.mark.asyncio
async def test_ensure_indexes_creates_all(fake_db):
    res = await ensure_live_recommendation_indexes(fake_db)
    assert res["available"] is True
    assert len(res["created"]) >= 5
