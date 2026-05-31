"""Background scheduler for periodic data refresh.

Applies the spec:
  - odds snapshots refresh every 30 minutes
  - team context (stats, standings, h2h, injuries) refresh every 6 hours

Uses AsyncIOScheduler so jobs run inside the FastAPI event loop.
Gated by the SCHEDULER_ENABLED env var (default off in test environments).
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import data_ingestion

log = logging.getLogger("scheduler")

_scheduler: AsyncIOScheduler | None = None
_status: dict[str, Any] = {
    "enabled": False,
    "jobs": {},
    "last_run": {},
}


async def _job_refresh_upcoming(db):
    """Re-ingest top-league upcoming fixtures (football only, refresh odds aggressively).

    NBA/MLB are NOT auto-refreshed: they're opt-in via explicit user analysis runs to
    preserve the shared 10 req/min API-Sports quota. If users need automatic refresh
    for those sports, multiple per-sport jobs can be added here.
    """
    log.info("Scheduler: refresh_upcoming (football) starting")
    started = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient() as client:
            await db.cache_odds.delete_many({"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]})
            items = await data_ingestion.ingest_upcoming(client, db, sport="football", max_per_league=2, max_total=8)
        _status["last_run"]["upcoming"] = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "count": len(items),
            "ok": True,
        }
        log.info("Scheduler: refresh_upcoming finished, %d items", len(items))
    except Exception as exc:
        log.exception("Scheduler refresh_upcoming failed: %s", exc)
        _status["last_run"]["upcoming"] = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": str(exc),
        }


async def _job_refresh_live(db):
    """Re-ingest live matches across ALL sports (football, basketball, baseball).

    Previously this job only refreshed football, leaving basketball and
    baseball with stale `is_live=True` rows from the last manual ingest.
    """
    import asyncio
    log.info("Scheduler: refresh_live (multi-sport) starting")
    started = datetime.now(timezone.utc)
    results: dict[str, int] = {}
    try:
        async with httpx.AsyncClient() as client:
            async def _one(s: str):
                try:
                    items = await data_ingestion.ingest_live(client, db, sport=s, max_total=15)
                    results[s] = len(items or [])
                except Exception as exc:
                    log.warning("Scheduler refresh_live[%s] failed: %s", s, exc)
                    results[s] = -1
            await asyncio.gather(*[_one(s) for s in ("football", "basketball", "baseball")],
                                  return_exceptions=True)
        _status["last_run"]["live"] = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "by_sport":  results,
            "ok": True,
        }
    except Exception as exc:
        log.exception("Scheduler refresh_live failed: %s", exc)
        _status["last_run"]["live"] = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": str(exc),
            "by_sport": results,
        }


async def _job_sweep_stale_live(db):
    """Backup sweeper that flips stale `is_live=True` rows across all sports.

    Even though `/api/matches/live` runs the sweep on every request, this
    background job ensures we don't accumulate ghost-live rows when nobody
    queries the endpoint for a long time (off-hours, weekends, etc.).
    """
    from services import live_lifecycle as ll
    log.info("Scheduler: sweep_stale_live starting")
    started = datetime.now(timezone.utc)
    totals: dict[str, int] = {}
    try:
        for s in ("football", "basketball", "baseball"):
            try:
                flipped = await ll.sweep_expired_live(db, sport=s)
                totals[s] = int(flipped or 0)
            except Exception as exc:
                log.warning("Scheduler sweep_stale_live[%s] failed: %s", s, exc)
                totals[s] = -1
        _status["last_run"]["sweep_stale"] = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "by_sport":  totals,
            "ok": True,
        }
    except Exception as exc:
        log.exception("Scheduler sweep_stale_live failed: %s", exc)


async def _job_purge_context_cache(db):
    """Purge stale 6h-old context cache entries (lets next access re-fetch)."""
    log.info("Scheduler: purge_context_cache starting")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    for col in ("cache_team_stats", "cache_standings", "cache_h2h", "cache_injuries"):
        try:
            res = await db[col].delete_many({"_cached_at": {"$lt": cutoff}})
            log.info("Purged %d stale from %s", res.deleted_count, col)
        except Exception as exc:
            log.warning("Purge %s failed: %s", col, exc)
    _status["last_run"]["purge"] = {"finished_at": datetime.now(timezone.utc).isoformat()}


async def _job_settle_finished_baseball(db):
    """Persist final_score + bullpen pitch counts for recently finished
    baseball matches. Feeds the active-series analyzer and any future
    learning loop with reliable settlement data.
    """
    log.info("Scheduler: settle_finished_baseball starting")
    try:
        from .mlb_finished_game_settler import settle_recent_finished
        n = await settle_recent_finished(db, days_back=2)
        _status["last_run"]["settle_finished_baseball"] = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "settled":     int(n or 0),
        }
        log.info("Scheduler: settle_finished_baseball settled %d games", n or 0)
    except Exception as exc:
        log.warning("settle_finished_baseball failed: %s", exc)


def start_scheduler(db) -> None:
    """Start the background scheduler if SCHEDULER_ENABLED=true."""
    global _scheduler
    enabled = os.environ.get("SCHEDULER_ENABLED", "false").lower() == "true"
    _status["enabled"] = enabled
    if not enabled:
        log.info("Scheduler disabled via env (SCHEDULER_ENABLED=false)")
        return
    if _scheduler is not None:
        log.info("Scheduler already running")
        return
    sch = AsyncIOScheduler(timezone="UTC")
    # Odds refresh every 30 min (also refreshes everything since ingest_upcoming gets odds)
    sch.add_job(
        _job_refresh_upcoming, args=[db],
        trigger=IntervalTrigger(minutes=30),
        id="refresh_upcoming",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
        max_instances=1,
        coalesce=True,
    )
    # Live refresh every 3 minutes (multi-sport)
    sch.add_job(
        _job_refresh_live, args=[db],
        trigger=IntervalTrigger(minutes=3),
        id="refresh_live",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        max_instances=1,
        coalesce=True,
    )
    # Sweep stale live rows every 5 minutes — catches matches that finished
    # but were not flipped is_live=False because nobody queried /matches/live.
    sch.add_job(
        _job_sweep_stale_live, args=[db],
        trigger=IntervalTrigger(minutes=5),
        id="sweep_stale_live",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
        max_instances=1,
        coalesce=True,
    )
    # Context purge every 6h
    sch.add_job(
        _job_purge_context_cache, args=[db],
        trigger=IntervalTrigger(hours=6),
        id="purge_context",
        next_run_time=datetime.now(timezone.utc) + timedelta(hours=6),
        max_instances=1,
        coalesce=True,
    )
    # Finished-game settler every 15 min — writes final_score + bullpen
    # pitch counts onto matches that just closed. Feeds M1 (active
    # series) and any future learning loop with reliable settlement.
    sch.add_job(
        _job_settle_finished_baseball, args=[db],
        trigger=IntervalTrigger(minutes=15),
        id="settle_finished_baseball",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=3),
        max_instances=1,
        coalesce=True,
    )
    sch.start()
    _scheduler = sch
    _status["jobs"] = {
        j.id: {"next_run": j.next_run_time.isoformat() if j.next_run_time else None}
        for j in sch.get_jobs()
    }
    log.info("Scheduler started with jobs: %s", list(_status["jobs"].keys()))


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler shut down")


def status() -> dict:
    if _scheduler is not None:
        jobs = {}
        for j in _scheduler.get_jobs():
            jobs[j.id] = {
                "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            }
        _status["jobs"] = jobs
    return _status
