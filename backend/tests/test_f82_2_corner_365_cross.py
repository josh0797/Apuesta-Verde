"""Phase F82.2 — Tests for the 365Scores corner-cross integration.

These are the 8 backend tests required by product:

1. ``test_thestatsapi_is_fast_corner_baseline`` — provider now picks
   TheStatsAPI BEFORE API-Sports when both are present.
2. ``test_scores24_not_called_for_corner_confirmation_by_default`` —
   the integrator never reaches ``scrape_scores24_match`` while the
   ``ENABLE_SCORES24_CORNERS_CONFIRMATION`` env flag is off (default).
3. ``test_365scores_confirms_under_corner_cross`` — UNDER profile +
   low avg/rate → ``external_confirmation=True``.
4. ``test_365scores_conflicts_with_under_corner_cross`` — UNDER profile
   + high avg/rate → ``external_conflict=True``.
5. ``test_365scores_confirms_over_corner_cross`` — OVER profile + high
   avg/rate → ``external_confirmation=True``.
6. ``test_corner_cross_persists_365_external_confirmation_in_analyst_runs``
   — after `/run-now` succeeds, the cross block written into
   analyst_runs carries ``external_source='365scores'`` plus the
   confirmation flags.
7. ``test_corner_enrichment_run_now_returns_cross_confirmation`` —
   the endpoint response payload includes the cross + audit blocks.
8. ``test_corner_enrichment_background_updates_cross_confirmation`` —
   after the background job completes, ``/status/{match_id}`` exposes
   the cross block in the result.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from services import football_corners_provider as cp
from services import football_corner_365_cross_integration as cross365


# ─────────────────────────────────────────────────────────────────────
# Test 1 — TheStatsAPI is the fast corner baseline
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_thestatsapi_is_fast_corner_baseline():
    """When BOTH TheStatsAPI and API-Sports stats are on the match doc,
    TheStatsAPI must win (Phase F82.2 reorder)."""
    match_doc = {
        "match_id":   "F82_2_BASELINE_001",
        "live_stats": {
            # API-Sports payload — present but should NOT be picked.
            "home_stats": {"Corner Kicks": "7"},
            "away_stats": {"Corner Kicks": "4"},
        },
        "_thestatsapi_enrichment": {
            "corners": {"home": 3, "away": 2, "total": 5},
        },
    }
    result = await cp.enrich_match_corners_fast(None, None, match_doc)
    assert result["available"] is True
    assert result["source"] == "thestatsapi", (
        "TheStatsAPI must take precedence over API-Sports as of F82.2; "
        f"got {result['source']}"
    )
    assert result["current_match"]["home"] == 3
    assert result["current_match"]["total"] == 5


# ─────────────────────────────────────────────────────────────────────
# Test 2 — Scores24 not called for corner confirmation by default
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scores24_not_called_for_corner_confirmation_by_default(monkeypatch):
    """``attach_football_corner_cross_to_payload`` MUST short-circuit
    before reaching the Scores24 scraper when the env flag is off."""
    monkeypatch.delenv("ENABLE_SCORES24_CORNERS_CONFIRMATION", raising=False)

    from services import football_corner_cross_integration as cci
    call_count = {"n": 0}

    async def _boom(*a, **k):
        call_count["n"] += 1
        raise AssertionError("scrape_scores24_match must not be called!")

    monkeypatch.setattr("services.scores24_scraper.scrape_scores24_match",
                         _boom, raising=False)

    pick = {"match_id": "F82_2_NO_SCORES24_001", "recommendation": {}}
    match = {
        "match_id":  "F82_2_NO_SCORES24_001",
        "home_team": {"name": "Alpha"},
        "away_team": {"name": "Beta"},
    }
    audit = await cci.attach_football_corner_cross_to_payload(pick, match)
    assert call_count["n"] == 0
    assert cci.RC_SCORES24_DISABLED in audit["reason_codes"]
    assert cci.RC_SCRAPER_SKIPPED in audit["reason_codes"]
    assert audit["scores24_attempted"] is False


# ─────────────────────────────────────────────────────────────────────
# Test 3 — 365Scores confirms UNDER cross profile
# ─────────────────────────────────────────────────────────────────────
def test_365scores_confirms_under_corner_cross():
    match_doc = {
        "match_id": "F82_2_UNDER_CONFIRM_001",
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_UNDER_CROSS",
            "supports":  "UNDER",
        },
        "corners_snapshot": {
            "available":        True,
            "source":           "365scores",
            "combined_avg_for": 7.9,
            "over_9_5_rate":    0.28,
        },
    }
    audit = cross365.attach_365_corner_confirmation(match_doc)
    assert audit["available"] is True
    assert audit["external_source"] == "365scores"
    assert audit["external_confirmation"] is True
    assert audit["external_conflict"]     is False
    assert cross365.RC_CONFIRMS_UNDER in audit["external_reason_codes"]
    # And the cross block on the match_doc must mirror it.
    cross = match_doc["combined_football_corner_profile_cross"]
    assert cross["external_confirmation"] is True
    assert cross["external_source"] == "365scores"


# ─────────────────────────────────────────────────────────────────────
# Test 4 — 365Scores conflicts with UNDER cross profile
# ─────────────────────────────────────────────────────────────────────
def test_365scores_conflicts_with_under_corner_cross():
    match_doc = {
        "match_id": "F82_2_UNDER_CONFLICT_001",
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_UNDER_CROSS",
            "supports":  "UNDER",
        },
        "corners_snapshot": {
            "available":        True,
            "source":           "365scores",
            "combined_avg_for": 11.2,
            "over_9_5_rate":    0.62,
        },
    }
    audit = cross365.attach_365_corner_confirmation(match_doc)
    assert audit["external_confirmation"] is False
    assert audit["external_conflict"]     is True
    assert cross365.RC_CONFLICTS_UNDER in audit["external_reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Test 5 — 365Scores confirms OVER cross profile
# ─────────────────────────────────────────────────────────────────────
def test_365scores_confirms_over_corner_cross():
    match_doc = {
        "match_id": "F82_2_OVER_CONFIRM_001",
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_OVER_CROSS",
            "supports":  "OVER",
        },
        "corners_snapshot": {
            "available":        True,
            "source":           "365scores",
            "combined_avg_for": 10.1,
            "over_9_5_rate":    0.58,
        },
    }
    audit = cross365.attach_365_corner_confirmation(match_doc)
    assert audit["external_confirmation"] is True
    assert audit["external_conflict"]     is False
    assert cross365.RC_CONFIRMS_OVER in audit["external_reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Test 6 — Cross persists 365 external confirmation in analyst_runs
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_corner_cross_persists_365_external_confirmation_in_analyst_runs(monkeypatch):
    """After `/run-now` succeeds, the persist helper must include the
    cross block + audit dict in its $set."""
    import server

    captured: dict = {"calls": []}

    class FakeUpdateResult:
        modified_count = 1

    class FakeColl:
        async def update_many(self, filt, update):
            captured["calls"].append({"filter": filt, "update": update})
            return FakeUpdateResult()

    class FakeDB:
        analyst_runs = FakeColl()

    monkeypatch.setattr(server, "db", FakeDB())

    cross_block = {
        "available": True,
        "profile":   "STRONG_CORNERS_UNDER_CROSS",
        "supports":  "UNDER",
        "external_source":       "365scores",
        "external_confirmation": True,
        "external_conflict":     False,
        "external_reason_codes": ["365SCORES_CONFIRMS_UNDER_PROFILE"],
    }
    cross_audit = {"engine_version": "test", "external_source": "365scores"}
    snapshot = {"available": True, "source": "365scores",
                 "combined_avg_for": 7.9, "over_9_5_rate": 0.28}

    await server._persist_corners_snapshot_to_run(
        "F82_2_PERSIST_001", snapshot,
        cross_block=cross_block, cross_audit=cross_audit,
    )

    assert captured["calls"], "persist must issue at least one update"
    set_block = captured["calls"][0]["update"]["$set"]
    # Must include both the corners snapshot AND the cross/audit blocks.
    assert "picks.$.corners_snapshot" in set_block
    assert "picks.$.combined_football_corner_profile_cross" in set_block
    assert "picks.$.football_corner_365_cross_applied"      in set_block
    written_cross = set_block["picks.$.combined_football_corner_profile_cross"]
    assert written_cross["external_source"]       == "365scores"
    assert written_cross["external_confirmation"] is True


# ─────────────────────────────────────────────────────────────────────
# Test 7 — /run-now returns the cross confirmation in its response
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_corner_enrichment_run_now_returns_cross_confirmation(monkeypatch):
    import server

    async def _fake_lookup(mid):
        return {
            "match_id": mid,
            "combined_football_corner_profile_cross": {
                "available": True,
                "profile":   "STRONG_CORNERS_UNDER_CROSS",
                "supports":  "UNDER",
            },
        }
    monkeypatch.setattr(server, "_load_match_doc_for_corners", _fake_lookup)

    async def _noop_persist(*a, **k): return None
    monkeypatch.setattr(server, "_persist_corners_snapshot_to_run", _noop_persist)

    async def _fake_external(client, db, match_doc):
        # Mutate the doc so attach_365_corner_confirmation has the
        # snapshot to evaluate.
        snap = {
            "available":        True,
            "source":           "365scores",
            "combined_avg_for": 7.9,
            "over_9_5_rate":    0.28,
            "current_match":    {"home": 4, "away": 4, "total": 8},
        }
        match_doc["corners_snapshot"] = snap
        return snap
    monkeypatch.setattr(cp, "enrich_match_corners_external", _fake_external)

    from server import corners_enrichment_run_now, CornersEnrichmentRequest
    result = await corners_enrichment_run_now(
        CornersEnrichmentRequest(match_id="F82_2_RUNNOW_001"),
    )

    assert result["status"] == "SUCCESS"
    assert result["available"] is True
    assert "combined_football_corner_profile_cross" in result
    cross = result["combined_football_corner_profile_cross"]
    assert cross["external_source"]       == "365scores"
    assert cross["external_confirmation"] is True
    # Audit dict must also surface.
    assert "football_corner_365_cross_applied" in result


# ─────────────────────────────────────────────────────────────────────
# Test 8 — /background → polling /status returns the cross confirmation
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_corner_enrichment_background_updates_cross_confirmation(monkeypatch):
    import server

    async def _fake_lookup(mid):
        return {
            "match_id": mid,
            "combined_football_corner_profile_cross": {
                "available": True,
                "profile":   "STRONG_CORNERS_OVER_CROSS",
                "supports":  "OVER",
            },
        }
    monkeypatch.setattr(server, "_load_match_doc_for_corners", _fake_lookup)

    async def _noop_persist(*a, **k): return None
    monkeypatch.setattr(server, "_persist_corners_snapshot_to_run", _noop_persist)

    async def _fake_external(client, db, match_doc):
        await asyncio.sleep(0.01)
        snap = {
            "available":        True,
            "source":           "365scores",
            "combined_avg_for": 10.5,
            "over_9_5_rate":    0.60,
        }
        match_doc["corners_snapshot"] = snap
        return snap
    monkeypatch.setattr(cp, "enrich_match_corners_external", _fake_external)

    mid = "F82_2_BG_001"
    server._CORNERS_BG_JOBS.pop(mid, None)

    from server import (
        corners_enrichment_background, corners_enrichment_status,
        CornersEnrichmentRequest,
    )
    queued = await corners_enrichment_background(
        CornersEnrichmentRequest(match_id=mid),
    )
    assert queued["status"] == "QUEUED"

    # Wait for the worker.
    status = None
    for _ in range(40):
        await asyncio.sleep(0.05)
        status = await corners_enrichment_status(mid)
        if status["status"] in ("SUCCESS", "FAILED"):
            break

    assert status["status"] == "SUCCESS"
    res = status["result"]
    assert "combined_football_corner_profile_cross" in res
    cross = res["combined_football_corner_profile_cross"]
    assert cross["external_source"]       == "365scores"
    assert cross["external_confirmation"] is True
    assert "365SCORES_CONFIRMS_OVER_PROFILE" in cross["external_reason_codes"]
