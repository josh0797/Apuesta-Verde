"""Tests for the MLB post-settle feedback loop (Fix 1) and Statcast
adapter warehouse-first behaviour (Fix 2).

We don't run the heavy Statcast network code — we patch out
``fetch_with_pybaseball/brightdata/thestatsapi`` and rely on the
warehouse helpers being a thin Mongo wrapper.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.mlb_results_settler import _feed_pattern_memory_from_eval


class _AsyncIter:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _make_db_with_pick(pick_doc):
    db = MagicMock()
    picks_coll = MagicMock()
    # find() must return an async-iterable that also supports sort/limit
    picks_coll.find = MagicMock(return_value=_AsyncIter([pick_doc]))
    # generic for all other collections (warehouse writes)
    other_coll = MagicMock()
    other_coll.find_one = AsyncMock(return_value=None)
    other_coll.replace_one = AsyncMock(return_value=None)
    other_coll.insert_one = AsyncMock(return_value=None)

    def _getitem(name):
        if name == "picks":
            return picks_coll
        return other_coll

    db.__getitem__.side_effect = _getitem
    # Settler uses both attribute access (db.picks) and item access (db["picks"]).
    db.picks = picks_coll
    return db, other_coll


@pytest.mark.asyncio
async def test_feedback_loop_returns_false_when_db_missing():
    ok = await _feed_pattern_memory_from_eval(
        db=None,
        evaluation={"match_id": "x", "recommended_market": "Under"},
        final_runs_home=3,
        final_runs_away=4,
        outcome={"result": "won"},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_feedback_loop_returns_false_when_result_not_settled():
    ok = await _feed_pattern_memory_from_eval(
        db=MagicMock(),
        evaluation={"match_id": "x"},
        final_runs_home=0, final_runs_away=0,
        outcome={"result": "pending"},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_feedback_loop_persists_when_won_with_patterns():
    pick = {
        "generated_at": "2026-06-03T00:00:00+00:00",
        "payload": {
            "picks": [
                {
                    "match_id": "100",
                    "game_pk":  "100",
                    "pressure_base": {"combined": {"pressure_tier": "LOW_PRESSURE"}},
                    "sabermetrics": {
                        "home": {"starting_pitcher_fip": {"tier": "ELITE_FIP"}},
                        "away": {"starting_pitcher_fip": {"tier": "STRONG_FIP"}},
                    },
                    "recommendation": {"market": "Full Game Under 8.5",
                                         "odds_decimal": 1.95},
                }
            ]
        },
    }
    db, generic_coll = _make_db_with_pick(pick)
    ok = await _feed_pattern_memory_from_eval(
        db=db,
        evaluation={
            "match_id": "100", "game_pk": "100",
            "recommended_market": "Full Game Under",
            "recommended_line":   "8.5",
            "recommended_odds":   1.95,
        },
        final_runs_home=3, final_runs_away=4,
        outcome={"result": "won"},
    )
    assert ok is True
    # market_results insert_one was called
    assert generic_coll.insert_one.await_count >= 1
    # pattern_memory replace_one was called at least once per pattern
    assert generic_coll.replace_one.await_count >= 1


@pytest.mark.asyncio
async def test_feedback_loop_handles_pick_lookup_failure_silently():
    db = MagicMock()
    bad_coll = MagicMock()
    bad_coll.find = MagicMock(side_effect=Exception("boom"))
    other_coll = MagicMock()
    other_coll.find_one = AsyncMock(return_value=None)
    other_coll.replace_one = AsyncMock(return_value=None)
    other_coll.insert_one = AsyncMock(return_value=None)
    db.__getitem__.side_effect = lambda n: bad_coll if n == "picks" else other_coll
    db.picks = bad_coll

    ok = await _feed_pattern_memory_from_eval(
        db=db,
        evaluation={"match_id": "x", "recommended_market": "Over 8.5",
                    "recommended_odds": 2.0},
        final_runs_home=5, final_runs_away=4,
        outcome={"result": "won"},
    )
    # No pattern_keys → still persists market_result (no-pattern bucket).
    assert ok is True


# ─────────────────────────────────────────────────────────────────────
# Statcast warehouse-first caching (Fix 2)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_statcast_returns_warehouse_hit_when_fresh():
    """When a fresh pitcher_profile exists in the warehouse, the
    Statcast adapter must short-circuit and skip the fetch."""
    from services import mlb_statcast_adapter as adapter

    fake_profile = {
        "available": True,
        "data_quality": "strong",
        "pitcher": {"era": 3.10, "xera": 3.05, "xwoba_allowed": 0.295},
        "source_status": {"sources": ["pybaseball"]},
    }

    async def fake_load_pitcher(db, pid, **kw):
        return {
            "profile":    fake_profile,
            "updated_at": "2099-12-31T00:00:00+00:00",  # very fresh
            "day":        "2099-12-31",
        }

    async def fake_load_team(db, tid, **kw):
        return None

    with patch.object(adapter, "is_adapter_enabled", return_value=True):
        # Patch the warehouse module the adapter imports lazily.
        import services.mlb_intelligence_warehouse as wh
        with patch.object(wh, "load_pitcher_profile", side_effect=fake_load_pitcher), \
             patch.object(wh, "load_team_profile", side_effect=fake_load_team):
            db = MagicMock()
            # cache lookups must NEVER be reached:
            db.__getitem__.side_effect = AssertionError(
                "must not touch external_source_cache when warehouse hits"
            )
            out = await adapter.get_mlb_advanced_profile(
                db=db, player_id="P1", role="pitcher",
                force_refresh=False,
            )
            assert out.get("available") is True
            assert out["source_status"]["warehouse"] == "hit"


@pytest.mark.asyncio
async def test_statcast_skips_warehouse_when_force_refresh():
    """force_refresh=True must NOT consult the warehouse."""
    from services import mlb_statcast_adapter as adapter
    import services.mlb_intelligence_warehouse as wh

    load_called = {"pitcher": 0, "team": 0}

    async def load_pitcher(db, pid, **kw):
        load_called["pitcher"] += 1
        return None

    async def load_team(db, tid, **kw):
        load_called["team"] += 1
        return None

    async def stub_fetch(*a, **k):
        return {}

    with patch.object(adapter, "is_adapter_enabled", return_value=True), \
         patch.object(wh, "load_pitcher_profile", side_effect=load_pitcher), \
         patch.object(wh, "load_team_profile", side_effect=load_team), \
         patch.object(adapter, "fetch_with_pybaseball", side_effect=stub_fetch), \
         patch.object(adapter, "fetch_with_brightdata",  side_effect=stub_fetch), \
         patch.object(adapter, "fetch_with_thestatsapi", side_effect=stub_fetch), \
         patch.object(adapter, "read_mlb_advanced_cache", new=AsyncMock(return_value=None)), \
         patch.object(adapter, "write_mlb_advanced_cache", new=AsyncMock(return_value=None)):
        out = await adapter.get_mlb_advanced_profile(
            db=MagicMock(), player_id="P1", role="pitcher",
            force_refresh=True,
        )
        # warehouse must not have been consulted
        assert load_called["pitcher"] == 0
        assert load_called["team"] == 0
        assert "warehouse" not in (out.get("source_status") or {})
