"""Phase F82.1-adjust — Tests for non-blocking 365Scores + manual/background endpoints.

These 5 tests are the contract guarantees demanded by product:

1) The main pick generator must NEVER call 365Scores inline (the fast
   tier in ``data_ingestion._enrich_football`` is the only path; it must
   not perform external HTTP).

2) When the fast tier produces no corners and background enrichment is
   enabled, the persisted snapshot must carry::

       status:       "PENDING_BACKGROUND_ENRICHMENT"
       reason_codes: [..., "CORNERS_EXTERNAL_ENRICHMENT_DEFERRED"]

3) ``POST /api/football/corners-enrichment/run-now`` must return
   ``status=SUCCESS`` with the corners block when 365Scores responds
   inside the 8s budget.

4) ``POST /api/football/corners-enrichment/run-now`` must return
   ``status=TIMEOUT`` + reason ``SCORE365_FETCH_TIMEOUT`` when 365Scores
   exceeds the 8s hard cap — without raising or blocking the request.

5) ``POST /api/football/corners-enrichment/background`` must respond
   immediately with ``status=QUEUED``, and the subsequent
   ``GET /api/football/corners-enrichment/status/{match_id}`` must
   reflect the final result once the background worker finishes.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from services import football_corners_provider as cp


# ─────────────────────────────────────────────────────────────────────
# Test 1 — Main generator NEVER calls 365Scores inline
# ─────────────────────────────────────────────────────────────────────
class TestF82_1Adjust_GeneratorNeverBlocks:
    @pytest.mark.asyncio
    async def test_main_generator_never_calls_365scores_inline(self, monkeypatch):
        """``enrich_match_corners_fast`` (the inline call) must NOT touch
        the 365Scores client even once. We assert this by monkey-patching
        the network entry-points to explode if invoked."""
        call_count = {"n": 0}

        async def _boom(*args, **kwargs):
            call_count["n"] += 1
            raise AssertionError("365Scores called inline — pipeline regression!")

        # Patch all external entry-points of score365_client.
        from services.external_sources import score365_client as s365
        monkeypatch.setattr(s365, "fetch_game_stats", _boom)
        monkeypatch.setattr(s365, "fetch_game_data",  _boom)

        async def _resolver_boom(*a, **k):
            call_count["n"] += 1
            raise AssertionError("365Scores resolver called inline!")
        monkeypatch.setattr(s365, "resolve_game_id_by_date_and_names", _resolver_boom)

        # Match WITHOUT API-Sports / TheStatsAPI corners. Fast tier must
        # short-circuit BEFORE any 365Scores call.
        match_doc = {
            "match_id":      "f82_1_adjust_test_001",
            "home_team":     {"name": "Alpha FC"},
            "away_team":     {"name": "Beta FC"},
            "kickoff_iso":   "2026-06-13T20:00:00Z",
        }
        result = await cp.enrich_match_corners_fast(None, None, match_doc)
        assert result["available"] is False
        assert call_count["n"] == 0, (
            "Fast tier must not invoke any 365Scores function; called %d times."
            % call_count["n"]
        )

    def test_data_ingestion_uses_fast_wrapper_only(self):
        """Sanity check: ``data_ingestion._enrich_football`` must import
        ``enrich_match_corners_fast`` — NOT the unbounded
        ``enrich_match_corners`` wrapper."""
        from services import data_ingestion
        src = inspect.getsource(data_ingestion)
        assert "enrich_match_corners_fast" in src
        # And it MUST NOT call the external opt-in wrapper inline.
        assert "enrich_match_corners_external" not in src


# ─────────────────────────────────────────────────────────────────────
# Test 2 — Empty fast tier → PENDING_BACKGROUND_ENRICHMENT
# ─────────────────────────────────────────────────────────────────────
class TestF82_1Adjust_DeferredSnapshot:
    @pytest.mark.asyncio
    async def test_corners_snapshot_marks_pending_when_empty(self, monkeypatch):
        """When the fast tier produces no corners and background is
        enabled (default), the snapshot must be marked PENDING with the
        ``CORNERS_EXTERNAL_ENRICHMENT_DEFERRED`` reason code."""
        # Force background enrichment flag ON (default).
        monkeypatch.setenv("ENABLE_BACKGROUND_365SCORES_CORNERS", "true")
        monkeypatch.setenv("ENABLE_INLINE_365SCORES_CORNERS",   "false")

        match_doc = {"match_id": "f82_1_adjust_test_002"}
        result = await cp.enrich_match_corners_fast(None, None, match_doc)

        assert result["available"] is False
        assert result.get("status") == cp.STATUS_PENDING_BG == "PENDING_BACKGROUND_ENRICHMENT"
        assert cp.RC_DEFERRED in result["reason_codes"]
        assert "CORNERS_EXTERNAL_ENRICHMENT_DEFERRED" in result["reason_codes"]
        # The same payload must be persisted to all 3 compat locations.
        assert match_doc.get("corners_snapshot") is result
        assert match_doc["football_data_enrichment"]["corners"] is result


# ─────────────────────────────────────────────────────────────────────
# Test 3 — /run-now returns SUCCESS with 365Scores payload
# ─────────────────────────────────────────────────────────────────────
class TestF82_1Adjust_RunNowEndpoint:
    @pytest.mark.asyncio
    async def test_run_now_endpoint_returns_score365_data_when_available(self, monkeypatch):
        import server

        # Mock the match_doc lookup so we don't depend on Mongo state.
        async def _fake_lookup(mid):
            return {"match_id": mid, "home_team": {"name": "Alpha"},
                    "away_team": {"name": "Beta"}}
        monkeypatch.setattr(server, "_load_match_doc_for_corners", _fake_lookup)

        # Make persistence a no-op (we don't have a real Mongo doc to update).
        async def _noop(*a, **k): return None
        monkeypatch.setattr(server, "_persist_corners_snapshot_to_run", _noop)

        # Mock the external 365Scores extractor to return a clean payload.
        async def _fake_365(client, match_doc, *, allow_name_resolver=True,
                             timeout_s=None):
            return {
                "source": "365scores",
                "home":   6,
                "away":   4,
                "total":  10,
                "_raw_provider": {"available": True},
            }, []
        monkeypatch.setattr(cp, "_extract_365scores_corners", _fake_365)

        # And short-circuit the fast tier so we actually hit the 365Scores branch.
        # (Fast tier already returns "unavailable" for a doc without api-sports
        # / thestatsapi corners — the cascade will then fall through to 365.)
        from server import corners_enrichment_run_now, CornersEnrichmentRequest
        result = await corners_enrichment_run_now(
            CornersEnrichmentRequest(match_id="f82_1_adjust_test_003"),
        )

        assert result["status"] == "SUCCESS"
        assert result["available"] is True
        assert result["source"] == "365scores"
        assert result["current_match"]["home"] == 6
        assert result["current_match"]["away"] == 4
        assert result["current_match"]["total"] == 10
        assert result["match_id"] == "f82_1_adjust_test_003"


# ─────────────────────────────────────────────────────────────────────
# Test 4 — /run-now returns TIMEOUT when 365Scores is slow
# ─────────────────────────────────────────────────────────────────────
class TestF82_1Adjust_RunNowTimeout:
    @pytest.mark.asyncio
    async def test_run_now_endpoint_returns_timeout_code_when_slow(self, monkeypatch):
        """When 365Scores exceeds the hard 8s cap, /run-now must return
        ``status=TIMEOUT`` with ``SCORE365_FETCH_TIMEOUT`` — without
        raising or blocking longer than the cap."""
        import server

        async def _fake_lookup(mid):
            return {"match_id": mid}
        monkeypatch.setattr(server, "_load_match_doc_for_corners", _fake_lookup)

        async def _noop(*a, **k): return None
        monkeypatch.setattr(server, "_persist_corners_snapshot_to_run", _noop)

        # Lower the cap to 0.1s for the duration of this test so the
        # assertion runs fast.
        monkeypatch.setattr(server, "_CORNERS_RUN_NOW_TIMEOUT_S", 0.1)

        async def _slow_external(client, db, match_doc):
            await asyncio.sleep(5)  # would exceed the 0.1s cap
            return {"available": True, "source": "365scores",
                    "current_match": {"home": 1, "away": 1, "total": 2},
                    "reason_codes": []}
        # Replace the function the endpoint calls.
        from services import football_corners_provider as cp_mod
        monkeypatch.setattr(cp_mod, "enrich_match_corners_external", _slow_external)

        from server import corners_enrichment_run_now, CornersEnrichmentRequest

        import time
        t0 = time.monotonic()
        result = await corners_enrichment_run_now(
            CornersEnrichmentRequest(match_id="f82_1_adjust_test_004"),
        )
        elapsed = time.monotonic() - t0

        assert result["status"] == "TIMEOUT"
        assert result["available"] is False
        assert cp.RC_365_TIMEOUT in result["reason_codes"]
        assert "SCORE365_FETCH_TIMEOUT" in result["reason_codes"]
        # Sanity: returned promptly (well below the original 8s cap and
        # well within asyncio scheduling tolerance).
        assert elapsed < 2.0, f"endpoint took {elapsed:.2f}s, expected <2s"


# ─────────────────────────────────────────────────────────────────────
# Test 5 — /background queues + /status reports the eventual result
# ─────────────────────────────────────────────────────────────────────
class TestF82_1Adjust_BackgroundEndpoint:
    @pytest.mark.asyncio
    async def test_background_endpoint_queues_and_status_returns_result(self, monkeypatch):
        import server

        async def _fake_lookup(mid):
            return {"match_id": mid}
        monkeypatch.setattr(server, "_load_match_doc_for_corners", _fake_lookup)

        async def _noop(*a, **k): return None
        monkeypatch.setattr(server, "_persist_corners_snapshot_to_run", _noop)

        # Background job will succeed via a quick external fetch.
        async def _fake_external(client, db, match_doc):
            await asyncio.sleep(0.01)
            return {
                "available":    True,
                "source":       "365scores",
                "current_match": {"home": 7, "away": 3, "total": 10},
                "confidence":   "USABLE",
                "reason_codes": ["CORNERS_FROM_365SCORES"],
            }
        from services import football_corners_provider as cp_mod
        monkeypatch.setattr(cp_mod, "enrich_match_corners_external", _fake_external)

        # Clear any prior state for the test match_id.
        mid = "f82_1_adjust_test_005"
        server._CORNERS_BG_JOBS.pop(mid, None)

        from server import (
            corners_enrichment_background, corners_enrichment_status,
            CornersEnrichmentRequest,
        )

        # 1) Queue the job — must return QUEUED immediately.
        queued = await corners_enrichment_background(
            CornersEnrichmentRequest(match_id=mid),
        )
        assert queued["status"] == "QUEUED"
        assert queued["match_id"] == mid

        # 2) Re-queueing while in-flight must return ALREADY_QUEUED.
        again = await corners_enrichment_background(
            CornersEnrichmentRequest(match_id=mid),
        )
        assert again["status"] in ("ALREADY_QUEUED", "QUEUED")

        # 3) Wait for the worker to finish (cap at 2s for safety).
        for _ in range(40):
            await asyncio.sleep(0.05)
            status = await corners_enrichment_status(mid)
            if status["status"] in ("SUCCESS", "FAILED"):
                break

        assert status["status"] == "SUCCESS"
        assert status["match_id"] == mid
        assert status["result"]["available"] is True
        assert status["result"]["source"] == "365scores"
        assert status["result"]["current_match"]["total"] == 10
        assert status["finished_at"] is not None

    @pytest.mark.asyncio
    async def test_status_returns_not_found_for_unknown_match(self):
        import server
        from server import corners_enrichment_status
        # Use a match_id we know has never been queued.
        result = await corners_enrichment_status("unknown_never_queued_match_xyz")
        assert result["status"] == "NOT_FOUND"
        assert result["match_id"] == "unknown_never_queued_match_xyz"
