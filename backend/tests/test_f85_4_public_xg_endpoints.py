"""Phase F85.4 — Tests for the /api/football/public-xg-enrichment endpoints.

Smoke-tests the request validation + the run-now / background / status
state machine. The actual orchestrator (`enrich_public_xg_context`) is
covered by `test_f85_3_public_xg.py` so here we monkey-patch it to
isolate the HTTP layer.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest


# =====================================================================
# Pydantic request validation
# =====================================================================
class TestPublicXGEnrichmentRequest:
    def test_numeric_match_id_coerced_to_string(self):
        from server import PublicXGEnrichmentRequest
        req = PublicXGEnrichmentRequest(match_id=12345)
        assert isinstance(req.match_id, str)
        assert req.match_id == "12345"

    def test_blank_match_id_rejected(self):
        from pydantic import ValidationError
        from server import PublicXGEnrichmentRequest
        with pytest.raises(ValidationError):
            PublicXGEnrichmentRequest(match_id="   ")

    def test_null_match_id_rejected(self):
        from pydantic import ValidationError
        from server import PublicXGEnrichmentRequest
        with pytest.raises(ValidationError):
            PublicXGEnrichmentRequest(match_id=None)

    def test_undefined_string_rejected(self):
        from pydantic import ValidationError
        from server import PublicXGEnrichmentRequest
        with pytest.raises(ValidationError):
            PublicXGEnrichmentRequest(match_id="undefined")

    def test_forebet_url_optional(self):
        from server import PublicXGEnrichmentRequest
        req = PublicXGEnrichmentRequest(match_id="m1")
        assert req.forebet_url is None
        assert req.sources is None
        assert req.force is False

    def test_sources_filter_accepted(self):
        from server import PublicXGEnrichmentRequest
        req = PublicXGEnrichmentRequest(
            match_id="m1",
            sources=["fbref", "forebet"],
            forebet_url="https://www.forebet.com/es/x",
            force=True,
        )
        assert req.sources == ["fbref", "forebet"]
        assert req.forebet_url.startswith("https://www.forebet.com")
        assert req.force is True


# =====================================================================
# _do_public_xg_fetch — happy paths
# =====================================================================
class TestDoPublicXGFetch:
    @pytest.mark.asyncio
    async def test_match_not_found_returns_clean_error(self, monkeypatch):
        import server
        async def _none_loader(mid):
            return None
        monkeypatch.setattr(server, "_load_match_doc_for_corners", _none_loader)
        result = await server._do_public_xg_fetch("UNKNOWN")
        assert result["available"] is False
        assert result["status"] == "MATCH_NOT_FOUND"
        assert "MATCH_NOT_FOUND" in result["reason_codes"]

    @pytest.mark.asyncio
    async def test_timeout_payload_is_passed_through(self, monkeypatch):
        import server
        async def _loader(mid):
            return {"match_id": mid,
                     "home_team": {"name": "United States"},
                     "away_team": {"name": "Paraguay"}}
        async def _enrich(*a, **kw):
            return {
                "available":   False,
                "status":      "TIMEOUT",
                "reason_codes": ["PUBLIC_XG_SCRAPER_TIMEOUT"],
                "message":     "no fue afectado",
            }
        monkeypatch.setattr(server, "_load_match_doc_for_corners", _loader)
        from services import football_xg_public_ingestor as ingmod
        monkeypatch.setattr(ingmod, "enrich_public_xg_context", _enrich)

        out = await server._do_public_xg_fetch("m1")
        assert out["status"] == "TIMEOUT"
        assert "PUBLIC_XG_SCRAPER_TIMEOUT" in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_success_payload_includes_xg_public_enrichment(self, monkeypatch):
        import server
        async def _loader(mid):
            return {"match_id": mid,
                     "home_team": {"name": "United States"},
                     "away_team": {"name": "Paraguay"}}
        async def _enrich(*a, **kw):
            return {
                "available":   True,
                "source_priority": ["thestatsapi", "fbref", "forebet"],
                "xg_recent_averages": {"available": True, "source": "fbref"},
                "forebet_context":    {"available": True},
                "data_quality": "USABLE",
                "reason_codes": ["FBREF_XG_RECENT_AVERAGES_AVAILABLE",
                                  "FOREBET_CONTEXT_AVAILABLE"],
            }
        async def _persist(mid, payload):
            return None
        monkeypatch.setattr(server, "_load_match_doc_for_corners", _loader)
        monkeypatch.setattr(server, "_persist_public_xg_to_run", _persist)
        from services import football_xg_public_ingestor as ingmod
        monkeypatch.setattr(ingmod, "enrich_public_xg_context", _enrich)

        out = await server._do_public_xg_fetch(
            "m1",
            forebet_url="https://www.forebet.com/es/football/matches/x-y-1",
        )
        assert out["status"] == "SUCCESS"
        assert out["available"] is True
        assert "xg_public_enrichment" in out
        assert out["match_id"] == "m1"

    @pytest.mark.asyncio
    async def test_sources_filter_drops_forebet_url(self, monkeypatch):
        """When sources=['fbref'], the forebet_url MUST be dropped before
        invoking the orchestrator."""
        import server
        captured: dict[str, Any] = {}
        async def _loader(mid):
            return {"match_id": mid, "home_team": {"name": "USA"},
                     "away_team": {"name": "Paraguay"}}
        async def _enrich(client, db, match_doc, *, forebet_url=None, timeout_s=8):
            captured["forebet_url"] = forebet_url
            return {"available": False, "data_quality": "UNAVAILABLE",
                     "reason_codes": []}
        monkeypatch.setattr(server, "_load_match_doc_for_corners", _loader)
        from services import football_xg_public_ingestor as ingmod
        monkeypatch.setattr(ingmod, "enrich_public_xg_context", _enrich)

        await server._do_public_xg_fetch(
            "m1",
            forebet_url="https://www.forebet.com/es/football/matches/x-y-1",
            sources=["fbref"],   # forebet NOT in sources
        )
        assert captured["forebet_url"] is None


# =====================================================================
# Background job state machine
# =====================================================================
class TestBackgroundQueueing:
    @pytest.mark.asyncio
    async def test_background_endpoint_returns_queued(self, monkeypatch):
        from server import (
            PublicXGEnrichmentRequest, public_xg_background,
            _PUBLIC_XG_BG_JOBS,
        )
        _PUBLIC_XG_BG_JOBS.clear()

        # Replace the worker so it doesn't try real fetches.
        import server as srv
        async def _noop(*a, **kw):
            return None
        monkeypatch.setattr(srv, "_run_background_public_xg_job", _noop)

        out = await public_xg_background(
            PublicXGEnrichmentRequest(match_id="m_bg_1"),
        )
        assert out["status"] == "QUEUED"
        assert out["match_id"] == "m_bg_1"
        assert "m_bg_1" in _PUBLIC_XG_BG_JOBS

    @pytest.mark.asyncio
    async def test_already_queued_returns_idempotent(self, monkeypatch):
        from server import (
            PublicXGEnrichmentRequest, public_xg_background,
            _PUBLIC_XG_BG_JOBS,
        )
        _PUBLIC_XG_BG_JOBS.clear()
        _PUBLIC_XG_BG_JOBS["m_dup"] = {
            "status": "RUNNING", "started_at": "2026-01-01T00:00:00",
            "finished_at": None, "result": None,
        }
        import server as srv
        async def _noop(*a, **kw):
            return None
        monkeypatch.setattr(srv, "_run_background_public_xg_job", _noop)

        out = await public_xg_background(
            PublicXGEnrichmentRequest(match_id="m_dup"),
        )
        assert out["status"] == "ALREADY_QUEUED"
        assert out["started_at"] == "2026-01-01T00:00:00"

    @pytest.mark.asyncio
    async def test_status_endpoint_not_found(self):
        from server import public_xg_status, _PUBLIC_XG_BG_JOBS
        _PUBLIC_XG_BG_JOBS.pop("m_missing", None)
        out = await public_xg_status("m_missing")
        assert out["status"] == "NOT_FOUND"
        assert out["match_id"] == "m_missing"

    @pytest.mark.asyncio
    async def test_status_endpoint_returns_job_snapshot(self):
        from server import public_xg_status, _PUBLIC_XG_BG_JOBS
        _PUBLIC_XG_BG_JOBS["m_status_1"] = {
            "status":      "DONE",
            "started_at":  "2026-01-01T00:00:00",
            "finished_at": "2026-01-01T00:00:05",
            "result":      {"available": True, "status": "SUCCESS"},
        }
        out = await public_xg_status("m_status_1")
        assert out["status"]       == "DONE"
        assert out["match_id"]     == "m_status_1"
        assert out["finished_at"]  == "2026-01-01T00:00:05"
        assert out["result"]["available"] is True

    @pytest.mark.asyncio
    async def test_background_worker_marks_done(self, monkeypatch):
        from server import _PUBLIC_XG_BG_JOBS, _run_background_public_xg_job
        import server as srv
        _PUBLIC_XG_BG_JOBS["m_done"] = {
            "status": "QUEUED", "started_at": "2026-01-01T00:00:00",
            "finished_at": None, "result": None,
        }
        async def _fake_fetch(mid, *, forebet_url=None, sources=None):
            return {"status": "SUCCESS", "available": True,
                     "match_id": mid}
        monkeypatch.setattr(srv, "_do_public_xg_fetch", _fake_fetch)
        await _run_background_public_xg_job(
            "m_done", forebet_url=None, sources=None,
        )
        job = _PUBLIC_XG_BG_JOBS["m_done"]
        assert job["status"] == "SUCCESS"
        assert job["result"]["available"] is True
        assert job["finished_at"] is not None

    @pytest.mark.asyncio
    async def test_background_worker_marks_error_on_crash(self, monkeypatch):
        from server import _PUBLIC_XG_BG_JOBS, _run_background_public_xg_job
        import server as srv
        _PUBLIC_XG_BG_JOBS["m_crash"] = {
            "status": "QUEUED", "started_at": "2026-01-01T00:00:00",
            "finished_at": None, "result": None,
        }
        async def _crash(mid, *, forebet_url=None, sources=None):
            raise RuntimeError("simulated crash")
        monkeypatch.setattr(srv, "_do_public_xg_fetch", _crash)
        await _run_background_public_xg_job(
            "m_crash", forebet_url=None, sources=None,
        )
        job = _PUBLIC_XG_BG_JOBS["m_crash"]
        assert job["status"] == "ERROR"
        assert "BACKGROUND_JOB_CRASHED" in (job["result"] or {}).get("reason_codes", [])
