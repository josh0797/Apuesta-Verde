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
from apscheduler.triggers.cron import CronTrigger

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


async def _job_settle_finished_football(db):
    """F95.4 — Persist final_score for recently finished football matches.

    Iterates ``football_match_learning_snapshots`` candidates whose
    kickoff is in the last 36h and which do NOT yet carry the
    ``POST_MATCH_RESULT_SETTLED`` reason code, and hydrates the final
    score via the provider cascade:
      TheStatsAPI → TheSportsDB → API-Sports.

    The job NEVER raises (any error is logged + counted as `errors`),
    so the rest of the scheduler is unaffected.
    """
    log.info("Scheduler: settle_finished_football starting")
    started = datetime.now(timezone.utc)
    try:
        from .football_finished_game_settler import (
            settle_recent_finished_football,
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            summary = await settle_recent_finished_football(
                db,
                hours_back=36,
                max_matches=50,
                http_client=client,
            )
        _status["last_run"]["settle_finished_football"] = {
            "started_at":  started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **(summary or {}),
        }
        log.info(
            "Scheduler: settle_finished_football done — attempted=%d "
            "full=%d partial=%d no_data=%d errors=%d",
            (summary or {}).get("attempted", 0),
            (summary or {}).get("settled_full", 0),
            (summary or {}).get("settled_partial", 0),
            (summary or {}).get("no_data", 0),
            (summary or {}).get("errors", 0),
        )
    except Exception as exc:
        log.warning("settle_finished_football failed: %s", exc)
        _status["last_run"]["settle_finished_football"] = {
            "started_at":  started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok":          False,
            "error":       str(exc),
        }


async def _job_recompute_feedback_weights(db):
    """Tick the MLB feedback loop. Fires `recompute_weights_if_due` which
    no-ops unless there are ≥ FEEDBACK_BATCH_SIZE (40) unconsumed settled
    picks. Cheap when there's nothing to do — safe to run every 30 min.
    """
    log.info("Scheduler: recompute_feedback_weights starting")
    try:
        from .mlb_feedback_loop import recompute_weights_if_due
        result = await recompute_weights_if_due(db)
        if result:
            _status["last_run"]["recompute_feedback_weights"] = {
                "finished_at":    datetime.now(timezone.utc).isoformat(),
                "recalibrated":   True,
                "rows_consumed":  len(result.get("accuracy_by_category") or {}),
            }
            log.info("Scheduler: recompute_feedback_weights → recalibrated")
        else:
            _status["last_run"]["recompute_feedback_weights"] = {
                "finished_at":  datetime.now(timezone.utc).isoformat(),
                "recalibrated": False,
            }
    except Exception as exc:
        log.warning("recompute_feedback_weights failed: %s", exc)


async def _job_auto_settle_mlb_evaluations(db):
    """F6C auto-settle: resuelve documentos `pending` de
    ``mlb_run_evaluations`` contra ``db.matches.final_score`` (que ya
    fue escrito por ``settle_finished_baseball``).

    Cada 20 min: lee evaluaciones pending de los últimos 3 días, busca
    el final_score correspondiente y aplica ``_resolve_result`` →
    ``update_run_evaluation_result``. Cierra el feedback loop sin
    intervención manual del usuario.
    """
    log.info("Scheduler: auto_settle_mlb_evaluations starting")
    started = datetime.now(timezone.utc)
    try:
        from .mlb_results_settler import auto_settle_pending_evaluations
        stats = await auto_settle_pending_evaluations(db, days_back=3, max_docs=200)
        _status["last_run"]["auto_settle_mlb_evaluations"] = {
            "started_at":  started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **stats,
        }
    except Exception as exc:
        log.warning("auto_settle_mlb_evaluations failed: %s", exc)
        _status["last_run"]["auto_settle_mlb_evaluations"] = {
            "started_at":  started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": str(exc),
        }


# ── Monthly StatBunker comp_id auto-discovery ───────────────────────────
async def _job_discover_statbunker_comp_ids(db):
    """Scan StatBunker for the active comp_id per league code, persist
    to MongoDB and patch `_COMP_IDS` in-process.

    Runs on the 1st of every month at 03:00 UTC. Cheap: ~10 fetches via
    Bright Data, finishes in <60s.
    """
    log.info("Scheduler: discover_statbunker_comp_ids starting")
    started = datetime.now(timezone.utc)
    try:
        from .external_sources.statbunker import discover_comp_ids
        result = await discover_comp_ids(db)
        _status["last_run"]["discover_statbunker_comp_ids"] = {
            "ts":         started.isoformat(),
            "duration_s": (datetime.now(timezone.utc) - started).total_seconds(),
            "discovered": len(result.get("discovered") or []),
            "failures":   len(result.get("failures") or []),
        }
        log.info(
            "discover_statbunker_comp_ids: %d discovered, %d failures",
            len(result.get("discovered") or []),
            len(result.get("failures") or []),
        )
    except Exception as exc:
        log.warning("discover_statbunker_comp_ids failed: %s", exc)


# ── One-shot startup: load previously-discovered comp_ids from MongoDB ──
async def _job_warm_statbunker_comp_ids(db):
    """Pre-load previously-discovered comp_ids from MongoDB into the
    in-process `_COMP_IDS` table. Runs once shortly after boot so all
    subsequent fetches benefit from past discovery runs.
    """
    try:
        from .external_sources.statbunker import load_discovered_comp_ids
        loaded = await load_discovered_comp_ids(db)
        log.info("warm_statbunker_comp_ids: %d entries loaded from MongoDB", loaded)
    except Exception as exc:
        log.warning("warm_statbunker_comp_ids failed: %s", exc)


# ── Phase F65 — hourly snapshot of odds for picks in watchlist_odds_needed ─
async def _job_snapshot_watchlist_odds(db):
    """Capture an hourly snapshot of the latest odds for every pick
    currently in the ``watchlist_odds_needed`` bucket.

    The pick lives in the most recent analyst_runs document of every
    user, so we walk runs younger than 48h. For each pick we read the
    latest odds from ``odds_snapshots`` (the existing odds-refresh
    pipeline writes them every 30 min) and persist:

        watchlist_odds_snapshots = {
          match_id, market_family, captured_at,
          odds, estimated_prob, edge_pct, ...
        }

    Fail-soft: any error logs + continues, never raises.
    """
    started = datetime.now(timezone.utc)
    written = 0
    try:
        cutoff = started - timedelta(hours=48)
        cursor = db.analyst_runs.find(
            {"created_at": {"$gte": cutoff}},
            {"summary.watchlist_odds_needed": 1, "created_at": 1},
        )
        seen_ids: set[str] = set()
        async for run in cursor:
            bucket = (run.get("summary") or {}).get("watchlist_odds_needed") or []
            for entry in bucket:
                if not isinstance(entry, dict):
                    continue
                mid = entry.get("match_id")
                if not mid or mid in seen_ids:
                    continue
                seen_ids.add(mid)
                # Latest odds snapshot for this match.
                odds_doc = await db.odds_snapshots.find_one(
                    {"match_id": mid},
                    sort=[("snapshot_at", -1)],
                )
                odds = None
                if odds_doc:
                    odds = (
                        odds_doc.get("odds")
                        or (odds_doc.get("markets") or {}).get("main_odds")
                        or entry.get("odds")
                    )
                est = entry.get("estimated_prob")
                # Compute edge from current odds + model probability.
                edge = None
                try:
                    if odds and est is not None and float(odds) > 1.0:
                        edge = round((float(est) - 1.0 / float(odds)) * 100.0, 2)
                except (TypeError, ValueError):
                    pass
                await db.watchlist_odds_snapshots.insert_one({
                    "match_id":        mid,
                    "match_label":     entry.get("match_label"),
                    "league":          entry.get("league"),
                    "captured_at":     started,
                    "odds":            odds,
                    "estimated_prob":  est,
                    "edge_pct":        edge,
                    "rescued_market":  entry.get("rescued_market"),
                    "source":          "scheduler.hourly",
                })
                written += 1
        _status["last_run"]["snapshot_watchlist_odds"] = {
            "ts":         started.isoformat(),
            "duration_s": (datetime.now(timezone.utc) - started).total_seconds(),
            "written":    written,
            "matches":    len(seen_ids),
        }
        log.info("snapshot_watchlist_odds: %d snapshots written (%d matches)",
                 written, len(seen_ids))
    except Exception as exc:
        log.warning("snapshot_watchlist_odds failed: %s", exc)


# ── Phase F67 — daily H2H refresh from upcoming fixtures ─────────────
async def _job_refresh_h2h(db):
    """Once-per-day refresh of the head_to_head_matches collection.

    Walks the unique (home, away) pairs from the last 7 days of analyst
    runs and asks API-Sports for the last-5 H2H rows. Fail-soft."""
    try:
        from .head_to_head_ingestor import refresh_h2h_for_upcoming_fixtures
        res = await refresh_h2h_for_upcoming_fixtures(db)
        _status["last_run"]["refresh_h2h"] = res
        log.info("refresh_h2h: pairs=%d rows=%d in %.1fs",
                 res.get("pairs_checked", 0),
                 res.get("rows_written", 0),
                 res.get("duration_s", 0))
    except Exception as exc:
        log.warning("refresh_h2h failed: %s", exc)


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
    # F95.4 — Football finished-game settler every 20 min — hydrates
    # final_score onto football_match_learning_snapshots via the
    # cascade TheStatsAPI → TheSportsDB → API-Sports. Closes the
    # POST_MATCH_RESULT_SETTLED gap that left finished matches lingering
    # in "Generar picks del día".
    sch.add_job(
        _job_settle_finished_football, args=[db],
        trigger=IntervalTrigger(minutes=20),
        id="settle_finished_football",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=4),
        max_instances=1,
        coalesce=True,
    )
    # Feedback-loop recalibration every 30 min — auto-recomputes engine
    # weights when ≥ 40 unconsumed settled picks accumulate. Cheap when
    # nothing's due (a single count_documents). Aligned with the odds
    # refresh cadence so we don't fragment scheduler ticks.
    sch.add_job(
        _job_recompute_feedback_weights, args=[db],
        trigger=IntervalTrigger(minutes=30),
        id="recompute_feedback_weights",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        max_instances=1,
        coalesce=True,
    )
    # F6C auto-settle every 20 min — resuelve mlb_run_evaluations pending
    # contra el final_score escrito por settle_finished_baseball (15 min).
    # Offset = +5 min para garantizar que el score ya esté persistido.
    sch.add_job(
        _job_auto_settle_mlb_evaluations, args=[db],
        trigger=IntervalTrigger(minutes=20),
        id="auto_settle_mlb_evaluations",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=8),
        max_instances=1,
        coalesce=True,
    )
    # Warm StatBunker comp_ids from MongoDB shortly after boot.
    sch.add_job(
        _job_warm_statbunker_comp_ids, args=[db],
        trigger=IntervalTrigger(days=365),  # one-shot via next_run_time
        id="warm_statbunker_comp_ids",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        max_instances=1,
        coalesce=True,
    )
    # Monthly: discover the active comp_id for every StatBunker league code
    # (1st of each month at 03:00 UTC). New seasons auto-go-live the day
    # StatBunker indexes them — no code deploy needed.
    sch.add_job(
        _job_discover_statbunker_comp_ids, args=[db],
        trigger=CronTrigger(day=1, hour=3, minute=0, timezone="UTC"),
        id="discover_statbunker_comp_ids",
        max_instances=1,
        coalesce=True,
    )
    # Phase F65 — hourly snapshot of watchlist_odds_needed prices.
    # Starts +10 min after boot so the first analyst_run has a chance
    # to populate the bucket. Aligned with the 30-min odds refresh so
    # the latest snapshot is at most 30 min stale.
    sch.add_job(
        _job_snapshot_watchlist_odds, args=[db],
        trigger=IntervalTrigger(hours=1),
        id="snapshot_watchlist_odds",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=10),
        max_instances=1,
        coalesce=True,
    )
    # Phase F67 — daily H2H refresh from upcoming fixtures @ 02:30 UTC.
    sch.add_job(
        _job_refresh_h2h, args=[db],
        trigger=CronTrigger(hour=2, minute=30, timezone="UTC"),
        id="refresh_h2h",
        max_instances=1,
        coalesce=True,
    )
    # Sprint-B Fix 1 — Learning-snapshot lifecycle jobs.
    # CREATE: every 30 min, scans matches kicking off in the 2-6h
    # window and ensures a pre_match snapshot exists in
    # ``football_match_learning_snapshots``.
    # REFRESH: every 10 min, re-runs the aggregator for matches in
    # the 0-60min window so we capture late lineups and odds shifts.
    try:
        from .football_learning_snapshot_jobs import (
            job_create_pre_match_snapshots,
            job_refresh_pre_match_snapshots,
        )
        sch.add_job(
            job_create_pre_match_snapshots, args=[db],
            trigger=IntervalTrigger(minutes=30),
            id="create_pre_match_snapshots",
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=7),
            max_instances=1,
            coalesce=True,
        )
        sch.add_job(
            job_refresh_pre_match_snapshots, args=[db],
            trigger=IntervalTrigger(minutes=10),
            id="refresh_pre_match_snapshots",
            next_run_time=datetime.now(timezone.utc) + timedelta(minutes=12),
            max_instances=1,
            coalesce=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Sprint-B learning snapshot jobs not wired: %s", exc)
    # Sprint E.1 — Live Odds Monitor.
    # Polls The Odds API every ``LIVE_ODDS_REFRESH_SECONDS`` for the
    # current odds of every fixture visible in the latest pick_run.
    # No-op when ``LIVE_ODDS_ENABLED!=true`` (kill switch).
    try:
        from . import live_odds_monitor
        live_odds_monitor.register_jobs(sch, db)
    except Exception as exc:  # noqa: BLE001
        log.warning("Sprint E.1 live odds monitor not wired: %s", exc)
    # Sprint E.1.1-d — Market Identity Auto-Resolver.
    # Every ``IDENTITY_RESOLVER_INTERVAL_SECONDS`` scans the visible
    # universe for matches stuck in ``REQUIRES_MARKET_IDENTIFICATION``
    # and dispatches them to ``market_identity_resolver`` via The Odds
    # API. observe_only / fail-soft.
    try:
        from . import market_identity_auto_resolver as _mir_auto
        _mir_auto.register_jobs(sch, db)
    except Exception as exc:  # noqa: BLE001
        log.warning("Sprint E.1.1-d auto-resolver not wired: %s", exc)
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
