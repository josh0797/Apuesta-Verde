"""Regression tests for football pattern-memory void handling.

Voids/pushes/refunds are financially neutral outcomes and must not count as
valid hit-rate attempts. They should increment `voids` while leaving
`sample_size`/market-ledger `samples` unchanged.
"""
from __future__ import annotations

import pytest

from services.football_moneyball import football_intelligence_warehouse as wh


class _FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query, *args, **kwargs):
        pk = query.get("pattern_key")
        doc = self.docs.get(pk)
        return dict(doc) if doc else None

    async def replace_one(self, query, payload, upsert=False):
        self.docs[query["pattern_key"]] = dict(payload)
        return True


class _FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _FakeCollection()
        return dict.__getitem__(self, name)


def _db_with_doc(doc):
    db = _FakeDB()
    db[wh.COLL_PATTERN_MEMORY].docs[doc["pattern_key"]] = dict(doc)
    return db


@pytest.mark.asyncio
async def test_void_does_not_count_in_sample_size():
    db = _db_with_doc({
        "pattern_key": "P1", "sample_size": 10, "wins": 6,
        "hit_rate": 0.6, "total_stake": 10.0, "total_payout": 12.0,
    })

    ok = await wh.update_pattern_memory_from_result(
        db, pattern_keys=["P1"], market="Over 1.5", stake=1.0,
        won=False, payout=1.0, outcome="void",
    )

    assert ok is True
    doc = db[wh.COLL_PATTERN_MEMORY].docs["P1"]
    assert doc["sample_size"] == 10
    assert doc["wins"] == 6
    assert doc["voids"] == 1
    assert doc["hit_rate"] == 0.6


@pytest.mark.asyncio
async def test_void_does_not_count_in_market_ledger_samples():
    db = _db_with_doc({
        "pattern_key": "P1", "sample_size": 10, "wins": 6,
        "total_stake": 10.0, "total_payout": 12.0,
        "market_ledger": {
            "Over 1.5": {"samples": 5, "wins": 3, "voids": 0, "stake": 5.0, "payout": 6.0}
        },
    })

    await wh.update_pattern_memory_from_result(
        db, pattern_keys=["P1"], market="Over 1.5", stake=1.0,
        won=False, payout=1.0, outcome="push",
    )

    ml = db[wh.COLL_PATTERN_MEMORY].docs["P1"]["market_ledger"]["Over 1.5"]
    assert ml["samples"] == 5
    assert ml["wins"] == 3
    assert ml["voids"] == 1
    assert ml["stake"] == 6.0
    assert ml["payout"] == 7.0


@pytest.mark.asyncio
async def test_void_roi_is_neutral():
    db = _db_with_doc({
        "pattern_key": "P1", "sample_size": 10, "wins": 6,
        "total_stake": 100.0, "total_payout": 110.0,
    })

    await wh.update_pattern_memory_from_result(
        db, pattern_keys=["P1"], market="Under 3.5", stake=10.0,
        won=False, payout=10.0, outcome="refund",
    )

    doc = db[wh.COLL_PATTERN_MEMORY].docs["P1"]
    assert doc["total_stake"] == 110.0
    assert doc["total_payout"] == 120.0
    assert doc["roi"] == round((120.0 - 110.0) / 110.0, 4)


@pytest.mark.asyncio
async def test_lost_outcome_unchanged():
    db = _db_with_doc({"pattern_key": "P1", "sample_size": 10, "wins": 6})

    await wh.update_pattern_memory_from_result(
        db, pattern_keys=["P1"], market="Over 2.5", stake=1.0,
        won=False, payout=0.0, outcome="lost",
    )

    doc = db[wh.COLL_PATTERN_MEMORY].docs["P1"]
    assert doc["sample_size"] == 11
    assert doc["wins"] == 6
    assert doc["hit_rate"] == round(6 / 11, 4)


@pytest.mark.asyncio
async def test_won_outcome_unchanged():
    db = _db_with_doc({"pattern_key": "P1", "sample_size": 10, "wins": 6})

    await wh.update_pattern_memory_from_result(
        db, pattern_keys=["P1"], market="Over 2.5", stake=1.0,
        won=True, payout=1.9, outcome="won",
    )

    doc = db[wh.COLL_PATTERN_MEMORY].docs["P1"]
    assert doc["sample_size"] == 11
    assert doc["wins"] == 7
    assert doc["hit_rate"] == round(7 / 11, 4)


@pytest.mark.asyncio
async def test_legacy_doc_without_voids_field():
    db = _db_with_doc({
        "pattern_key": "P1", "sample_size": 10, "wins": 6,
        "market_ledger": {"Over 1.5": {"samples": 5, "wins": 3, "stake": 5.0, "payout": 6.0}},
    })

    await wh.update_pattern_memory_from_result(
        db, pattern_keys=["P1"], market="Over 1.5", stake=1.0,
        won=False, payout=1.0, outcome="cancelled",
    )

    doc = db[wh.COLL_PATTERN_MEMORY].docs["P1"]
    assert doc["voids"] == 1
    assert doc["market_ledger"]["Over 1.5"]["voids"] == 1


@pytest.mark.asyncio
async def test_best_market_skips_all_void_market():
    db = _db_with_doc({
        "pattern_key": "P1", "sample_size": 6, "wins": 4,
        "market_ledger": {
            "Over 3.5": {"samples": 0, "wins": 0, "voids": 3, "stake": 3.0, "payout": 3.0},
            "Under 3.5": {"samples": 5, "wins": 3, "voids": 0, "stake": 5.0, "payout": 6.0},
        },
    })

    await wh.update_pattern_memory_from_result(
        db, pattern_keys=["P1"], market="Over 3.5", stake=1.0,
        won=False, payout=1.0, outcome="void",
    )

    doc = db[wh.COLL_PATTERN_MEMORY].docs["P1"]
    assert doc["market_ledger"]["Over 3.5"]["samples"] == 0
    assert doc["best_market"] == "Under 3.5"
