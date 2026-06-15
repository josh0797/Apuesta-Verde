"""MLB-F93 — Manual Odds Override Reprice + UI Refresh.

Eleven obligatory tests covering:
  1. reprice when pick found by pick_id
  2. reprice when pick found by game_pk
  3. reprice when pick found in watchlist bucket
  4. reprice reconstructs context from matches doc
  5. saved-only when pick context missing
  6. response NEVER only says "saved"
  7. NO_VALUE when edge below threshold
  8. VALUE when edge above threshold
  9. updates analyst run entry (pick_runs.update_one called with proper fields)
 10. debug endpoint reports lookup_attempts
 11. override survives recent_run miss
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.mlb_manual_odds_reprice import (
    RC_EDGE_ABOVE_THRESHOLD, RC_EDGE_BELOW_THRESHOLD,
    RC_MODEL_PROB_MISSING, RC_OVERRIDE_USED,
    RC_PICK_CONTEXT_NOT_FOUND, RC_PICK_CONTEXT_RECONSTRUCTED,
    RC_REPRICE_APPLIED,
    build_minimal_pick_context_from_match_doc,
    reprice_mlb_pick_with_manual_odds,
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _pick(*, pid="pk-1", cover_prob=0.60, **extra):
    return {
        "id": pid,
        "home_team": "Houston Astros",
        "away_team": "Detroit Tigers",
        "commence_date": "2026-06-15",
        "cover_probability": cover_prob,
        "confidence_score": 80,
        "key_data": {},
        **extra,
    }


def _run(*, bucket="picks", pick=None, run_id="r1"):
    pick = pick or _pick()
    return {
        "_id": run_id,
        "user_id": "u1",
        "sport": "baseball",
        "payload": {bucket: [pick]},
        "generated_at": "2026-06-15T18:00:00+00:00",
    }


class _FakeUpdateResult:
    matched_count = 1
    modified_count = 1


async def _call_endpoint(payload_dict, **patches):
    """Call the FastAPI endpoint coroutine directly with `db.*` patched."""
    from server import MlbManualOddsIn, mlb_pick_manual_odds

    payload = MlbManualOddsIn(**payload_dict)
    pick_id = payload_dict.get("_pick_id_url", payload_dict.get("pick_id", "pk-1"))

    pick_runs = patches.get("pick_runs")
    matches   = patches.get("matches")
    overrides = patches.get("overrides")

    fake_db = MagicMock()
    fake_db.pick_runs = pick_runs or MagicMock(
        find_one=AsyncMock(return_value=None),
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
    )
    if matches is not None:
        fake_db.matches = matches
    else:
        fake_db.matches = MagicMock(
            find_one=AsyncMock(return_value=None),
        )
    fake_db.mlb_manual_odds_overrides = overrides or MagicMock(
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
        find_one=AsyncMock(return_value=None),
    )

    with patch("server.db", fake_db):
        return await mlb_pick_manual_odds(
            pick_id=pick_id, payload=payload, user={"id": "u1"},
        )


# ---------------------------------------------------------------------
# 1. Reprice when pick found by pick_id
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_reprices_when_pick_found_by_pick_id():
    run = _run(pick=_pick(pid="pk-99", cover_prob=0.62))
    pick_runs = MagicMock(
        find_one=AsyncMock(return_value=run),
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
    )
    resp = await _call_endpoint(
        {"manual_odds": 1.90, "_pick_id_url": "pk-99"},
        pick_runs=pick_runs,
    )
    assert resp["ok"] is True
    assert resp["status"] == "REPRICED"
    assert resp["reprice"]["available"] is True
    assert resp["reprice"]["decision"] in ("VALUE", "NO_VALUE", "WATCHLIST")
    assert resp["attached_to_pick"] is True
    assert resp["fallback_override_created"] is False
    # update_one was called against pick_runs to patch the entry.
    pick_runs.update_one.assert_called_once()


# ---------------------------------------------------------------------
# 2. Reprice when pick found by game_pk (legacy lookup path)
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_reprices_when_pick_found_by_game_pk():
    pick = _pick(pid="pk-A", cover_prob=0.58)
    pick["game_pk"] = "777111"
    run = _run(pick=pick)

    # _locate_pick_multikey: 1st call (pick_id) → None, 2nd (alt ids) → run.
    pick_runs = MagicMock(
        find_one=AsyncMock(side_effect=[None, run]),
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
    )
    resp = await _call_endpoint(
        {"manual_odds": 1.95, "game_pk": "777111", "_pick_id_url": "missing-pk"},
        pick_runs=pick_runs,
    )
    assert resp["status"] == "REPRICED"
    assert resp["attached_to_pick"] is True
    assert resp["reprice"]["available"] is True


# ---------------------------------------------------------------------
# 3. Reprice when pick found in watchlist_manual_odds bucket
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_reprices_when_pick_found_in_watchlist_bucket():
    pick = _pick(pid="pk-WL", cover_prob=0.65)
    run = _run(bucket="watchlist_manual_odds", pick=pick)

    pick_runs = MagicMock(
        find_one=AsyncMock(return_value=run),
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
    )
    resp = await _call_endpoint(
        {"manual_odds": 1.80, "_pick_id_url": "pk-WL"},
        pick_runs=pick_runs,
    )
    assert resp["status"] == "REPRICED"
    assert resp["bucket"] == "watchlist_manual_odds"
    # The update_one call used the watchlist bucket in its $set keys.
    args, kwargs = pick_runs.update_one.call_args
    sets = args[1]["$set"]
    assert any("watchlist_manual_odds" in k for k in sets.keys())


# ---------------------------------------------------------------------
# 4. Reconstruct context from matches collection
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_reconstructs_context_from_match_doc():
    pick_runs = MagicMock(find_one=AsyncMock(return_value=None),
                          update_one=AsyncMock(return_value=_FakeUpdateResult()))
    match_doc = {
        "match_id":      "m-12345",
        "game_pk":       "555000",
        "home_team":     "Houston Astros",
        "away_team":     "Detroit Tigers",
        "commence_date": "2026-06-15",
        "cover_probability": 0.61,
        "confidence_score":  78,
        "sport":         "baseball",
    }
    matches = MagicMock(find_one=AsyncMock(return_value=match_doc))
    resp = await _call_endpoint(
        {"manual_odds": 1.90, "match_id": "m-12345",
         "_pick_id_url": "pk-not-in-runs"},
        pick_runs=pick_runs,
        matches=matches,
    )
    assert resp["status"] == "REPRICED"
    assert resp["reprice"]["available"] is True
    # Pick was NOT inside pick_runs → attached_to_pick is False but reprice OK.
    assert resp["attached_to_pick"] is False
    assert resp["fallback_override_created"] is True
    assert RC_PICK_CONTEXT_RECONSTRUCTED in resp["reprice"]["reason_codes"]


# ---------------------------------------------------------------------
# 5. Saved-only when pick context cannot be found anywhere
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_saved_only_when_pick_context_missing():
    pick_runs = MagicMock(find_one=AsyncMock(return_value=None),
                          update_one=AsyncMock(return_value=_FakeUpdateResult()))
    matches   = MagicMock(find_one=AsyncMock(return_value=None))
    resp = await _call_endpoint(
        {"manual_odds": 1.85, "match_id": "m-unknown",
         "_pick_id_url": "pk-unknown"},
        pick_runs=pick_runs, matches=matches,
    )
    assert resp["status"] == "OVERRIDE_SAVED_ONLY"
    assert resp["reprice"]["available"] is False
    assert resp["next_action"] == "REFRESH_OR_REGENERATE_REQUIRED"
    assert RC_PICK_CONTEXT_NOT_FOUND in resp["reprice"]["reason_codes"]
    assert resp["attached_to_pick"] is False
    assert resp["fallback_override_created"] is True


# ---------------------------------------------------------------------
# 6. Response NEVER only says "guardada"
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_response_never_only_says_saved():
    """Whatever the path, the response must include status + reprice."""
    pick_runs = MagicMock(find_one=AsyncMock(return_value=None),
                          update_one=AsyncMock(return_value=_FakeUpdateResult()))
    matches   = MagicMock(find_one=AsyncMock(return_value=None))
    resp = await _call_endpoint(
        {"manual_odds": 1.64, "_pick_id_url": "pk-x"},
        pick_runs=pick_runs, matches=matches,
    )
    # Both contracts always present.
    assert "status"        in resp
    assert "reprice"       in resp
    assert "message_user"  in resp
    assert "message_debug" in resp
    # And the user-visible message is informative (NOT a bare "saved").
    msg = (resp["message_user"] or "").lower()
    assert "guardad" in msg or "cuota" in msg
    assert resp["status"] in {
        "REPRICED", "OVERRIDE_SAVED_ONLY", "PICK_NOT_FOUND", "ERROR",
    }


# ---------------------------------------------------------------------
# 7. NO_VALUE when edge below threshold
# ---------------------------------------------------------------------
def test_manual_odds_no_value_when_edge_below_threshold(monkeypatch):
    """If model_prob=0.57 vs implied=0.61 (odd 1.64) edge=-0.04 → NO_VALUE."""
    monkeypatch.setenv("MLB_MANUAL_VALUE_EDGE_THRESHOLD", "0.03")
    monkeypatch.setenv("MLB_MANUAL_WATCHLIST_TOLERANCE", "0.02")
    pick = _pick(cover_prob=0.57)
    out = reprice_mlb_pick_with_manual_odds(pick, 1.64)
    assert out["available"] is True
    assert out["decision"] == "NO_VALUE"
    assert RC_EDGE_BELOW_THRESHOLD in out["reason_codes"]
    assert RC_REPRICE_APPLIED      in out["reason_codes"]
    assert out["edge"] is not None and out["edge"] < 0.03
    assert out["ev"]   is not None and out["ev"]   <= 0
    assert out["fair_odds"] is not None
    assert out["implied_probability"] is not None


# ---------------------------------------------------------------------
# 8. VALUE when edge above threshold
# ---------------------------------------------------------------------
def test_manual_odds_value_when_edge_above_threshold(monkeypatch):
    monkeypatch.setenv("MLB_MANUAL_VALUE_EDGE_THRESHOLD", "0.03")
    pick = _pick(cover_prob=0.60)
    # implied at 1.92 = 0.5208 → edge = 0.60 - 0.5208 = 0.079
    out = reprice_mlb_pick_with_manual_odds(pick, 1.92)
    assert out["decision"] == "VALUE"
    assert RC_EDGE_ABOVE_THRESHOLD in out["reason_codes"]
    assert out["edge"] >= 0.03
    assert out["ev"]   > 0


# ---------------------------------------------------------------------
# 9. Updates analyst_run entry with the new F93 reprice fields
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_updates_analyst_run_entry():
    run = _run(pick=_pick(pid="pk-up", cover_prob=0.60))
    pick_runs = MagicMock(
        find_one=AsyncMock(return_value=run),
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
    )
    resp = await _call_endpoint(
        {"manual_odds": 1.92, "_pick_id_url": "pk-up"},
        pick_runs=pick_runs,
    )
    assert resp["status"] == "REPRICED"
    pick_runs.update_one.assert_called_once()
    args, _ = pick_runs.update_one.call_args
    sets = args[1]["$set"]
    # F93 fields are persisted.
    assert any(k.endswith(".odds_source") for k in sets.keys())
    assert any(k.endswith(".odds_status") for k in sets.keys())
    assert any(k.endswith(".reprice")      for k in sets.keys())
    assert any(k.endswith(".value_status") for k in sets.keys())
    assert any(k.endswith(".edge")         for k in sets.keys())
    assert any(k.endswith(".ev")           for k in sets.keys())
    assert any(k.endswith(".fair_odds")    for k in sets.keys())
    assert any(k.endswith(".manual_odds_updated_at") for k in sets.keys())
    # And the legacy fields kept for back-compat.
    assert any(k.endswith(".manual_edge_pct") for k in sets.keys())
    assert any(k.endswith(".manual_value_status") for k in sets.keys())


# ---------------------------------------------------------------------
# 10. Debug endpoint reports lookup_attempts
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_debug_reports_lookup_attempts():
    from server import mlb_manual_odds_debug

    pick = _pick(pid="dbg-1", cover_prob=0.60)
    pick["game_pk"] = "g-99"
    run = _run(pick=pick)
    # 1st call by pick_id → not found. 2nd by alt ids → found.
    pick_runs = MagicMock(find_one=AsyncMock(side_effect=[None, run]))
    matches   = MagicMock(find_one=AsyncMock(return_value=None))
    overrides = MagicMock(find_one=AsyncMock(return_value={
        "user_id": "u1", "sport": "baseball", "pick_id": "dbg-1",
        "manual_odds": 1.90,
    }))
    fake_db = MagicMock()
    fake_db.pick_runs = pick_runs
    fake_db.matches   = matches
    fake_db.mlb_manual_odds_overrides = overrides

    with patch("server.db", fake_db):
        out = await mlb_manual_odds_debug(
            user={"id": "u1"}, match_id=None, pick_id="dbg-1", game_pk="g-99",
        )
    assert out["ok"] is True
    assert isinstance(out["lookup_attempts"], list)
    methods = [a["method"] for a in out["lookup_attempts"]]
    assert "pick_id" in methods
    assert "game_pk_or_match_id" in methods
    assert out["override_found"] is True
    assert out["final_status"] in {"REPRICED", "OVERRIDE_SAVED_ONLY",
                                    "PICK_NOT_FOUND"}


# ---------------------------------------------------------------------
# 11. Override survives a recent_run miss (always persisted)
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_odds_override_survives_recent_run_miss():
    pick_runs = MagicMock(find_one=AsyncMock(return_value=None),
                          update_one=AsyncMock(return_value=_FakeUpdateResult()))
    matches   = MagicMock(find_one=AsyncMock(return_value=None))
    overrides_collection = MagicMock(
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
        find_one=AsyncMock(return_value=None),
    )

    resp = await _call_endpoint(
        {"manual_odds": 1.64, "match_id": "m-X", "game_pk": "g-X",
         "_pick_id_url": "missing-pk"},
        pick_runs=pick_runs, matches=matches, overrides=overrides_collection,
    )
    assert resp["fallback_override_created"] is True
    # Override doc was upserted with the manual odds + tried keys.
    overrides_collection.update_one.assert_called()
    _, upsert_call = overrides_collection.update_one.call_args
    # The `$set` dict carries manual_odds + f93_reprice + f93_status.
    args = overrides_collection.update_one.call_args[0]
    persisted = args[1]["$set"]
    assert persisted["manual_odds"] == 1.64
    assert "f93_reprice" in persisted
    assert persisted["f93_status"] in {"OVERRIDE_SAVED_ONLY", "PICK_NOT_FOUND"}


# ---------------------------------------------------------------------
# Extra — pure module sanity (not part of the obligatory 11, but cheap).
# ---------------------------------------------------------------------
def test_reprice_pure_module_returns_invalid_for_bad_odds():
    out = reprice_mlb_pick_with_manual_odds({"cover_probability": 0.5}, 0.5)
    assert out["decision"] == "INVALID"


def test_reprice_pure_module_manual_odds_only_when_no_prob():
    out = reprice_mlb_pick_with_manual_odds({"confidence_score": 50}, 1.90)
    assert out["decision"] == "MANUAL_ODDS_ONLY"
    assert RC_MODEL_PROB_MISSING in out["reason_codes"]
    assert RC_OVERRIDE_USED       in out["reason_codes"]


def test_build_minimal_pick_context_from_match_doc_keeps_probability():
    ctx = build_minimal_pick_context_from_match_doc({
        "match_id": "m1", "game_pk": "g1",
        "home_team": "A", "away_team": "B",
        "commence_date": "2026-06-15",
        "cover_probability": 0.55,
    })
    assert ctx is not None
    assert ctx["cover_probability"] == 0.55
    assert ctx["_reconstructed"] is True
