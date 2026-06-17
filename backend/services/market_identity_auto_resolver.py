"""Sprint E.1.1-d · Auto-resolver scheduler for market identity.

Periodically scans the latest pick_runs / picks for any match that
finished in ``state=REQUIRES_MARKET_IDENTIFICATION`` (i.e. the engine
detected a price but couldn't classify it as DNB / 1X2 / O/U / BTTS /
handicap / etc.) and dispatches the price to
``services.market_identity_resolver.resolve_market_identity`` to try
to map it via The Odds API.

Strict invariants
-----------------
* **observe_only**: pure proposal layer. We NEVER mutate the original
  pick / market_trace; we only persist resolutions in
  ``market_identity_resolutions`` so the UI can surface them on the
  next refresh.
* **Background-first / fail-soft**: every error becomes a log line.
* **No global polling**: only the **visible universe** from the latest
  pick_runs is touched (the same universe the live_odds_monitor uses).
* **Rate-limit aware**: hard cap per cycle (``IDENTITY_RESOLVER_MAX_PER_CYCLE``)
  + skip already-cached resolutions.

Environment flags
-----------------
* ``IDENTITY_RESOLVER_ENABLED``         (default ``false``) — kill switch.
* ``IDENTITY_RESOLVER_INTERVAL_SECONDS`` (default ``180``).
* ``IDENTITY_RESOLVER_MAX_PER_CYCLE``   (default ``20``) — hard cap.
* ``IDENTITY_RESOLVER_LOOKBACK_HOURS``  (default ``24``).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import live_odds_monitor as lom
from . import market_identity_resolver as mir

log = logging.getLogger("market_identity_auto_resolver")

DEFAULT_INTERVAL_SECONDS:  int = 180  # 3 min
DEFAULT_MAX_PER_CYCLE:     int = 20
DEFAULT_LOOKBACK_HOURS:    int = 24

_status: dict[str, Any] = {
    "enabled":            False,
    "last_cycle":         None,
    "last_error":         None,
    "candidates_seen":    0,
    "resolutions_run":    0,
    "resolutions_skipped":0,
}


def _env_bool(name: str, default: bool = False) -> bool:
    return (os.environ.get(name, "true" if default else "false") or "").lower() == "true"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def get_config() -> dict:
    return {
        "enabled":          _env_bool("IDENTITY_RESOLVER_ENABLED", False),
        "interval_seconds": _env_int("IDENTITY_RESOLVER_INTERVAL_SECONDS",
                                      DEFAULT_INTERVAL_SECONDS),
        "max_per_cycle":    _env_int("IDENTITY_RESOLVER_MAX_PER_CYCLE",
                                      DEFAULT_MAX_PER_CYCLE),
        "lookback_hours":   _env_int("IDENTITY_RESOLVER_LOOKBACK_HOURS",
                                      DEFAULT_LOOKBACK_HOURS),
    }


def get_status() -> dict:
    return dict(_status)


# ─── Pure: extract REQUIRES_MARKET_IDENTIFICATION items from run docs ──
def extract_pending_identities(
    *,
    run_docs: list[dict],
    sport_filter: str = "football",
    already_resolved_keys: Optional[set[tuple]] = None,
) -> list[dict]:
    """From a list of pick_run / analyst_run documents, return the
    set of *unique* (match_id, detected_price) pairs that the engine
    flagged as ``REQUIRES_MARKET_IDENTIFICATION`` and were NOT already
    resolved.

    Pure helper — no Mongo / HTTP.

    Each entry::

        {
            "match_id":       <str>,
            "home_team":      <str|None>,
            "away_team":      <str|None>,
            "league":         <str|None>,
            "commence_time":  <str|None>,
            "detected_price": <float>,
            "source_collection": <str>,
        }
    """
    seen: set[tuple] = set(already_resolved_keys or set())
    out: list[dict] = []

    def _consider(entry: dict, collection: str):
        if not isinstance(entry, dict):
            return
        # We accept either a top-level ``state`` or nested under
        # ``market_trace`` / ``classification``.
        state = (entry.get("state")
                  or (entry.get("market_trace") or {}).get("state")
                  or entry.get("classification")
                  or (entry.get("market_trace") or {}).get("classification"))
        if state != "REQUIRES_MARKET_IDENTIFICATION":
            return
        mid = (entry.get("match_id") or entry.get("fixture_id")
                or entry.get("id"))
        if mid is None:
            return
        # Detected price — pick the first available among common keys.
        price = (entry.get("odds_visible")
                  or entry.get("original_odds")
                  or entry.get("odds")
                  or entry.get("detected_odd")
                  or entry.get("detected_price")
                  or (entry.get("market_trace") or {}).get("odds_visible")
                  or (entry.get("market_trace") or {}).get("original_odds")
                  or (entry.get("market_trace") or {}).get("odds"))
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        if price is None or price <= 1.0:
            return
        key = (str(mid), round(price, 4))
        if key in seen:
            return
        seen.add(key)
        out.append({
            "match_id":       str(mid),
            "home_team":      entry.get("home_team") or entry.get("home"),
            "away_team":      entry.get("away_team") or entry.get("away"),
            "league":         entry.get("league") or entry.get("league_name"),
            "commence_time":  (entry.get("commence_time")
                                or entry.get("kickoff")),
            "sport_key_hint": entry.get("sport_key"),
            "detected_price": price,
            "source_collection": collection,
        })

    for run in run_docs or []:
        if not isinstance(run, dict):
            continue
        if sport_filter and run.get("sport") and run["sport"] != sport_filter:
            continue
        coll = run.get("_collection") or "pick_runs"
        payload = run.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        buckets = (
            (payload.get("picks")                          or []) +
            (payload.get("rescued_picks")                  or []) +
            (payload.get("rescued")                        or []) +
            (payload.get("watchlist_manual_odds")          or []) +
            (payload.get("structural_lean_requires_odds")  or []) +
            (payload.get("watchlist_odds_needed")          or []) +
            (payload.get("discarded_market")               or []) +
            (payload.get("matches")                        or [])
        )
        for entry in buckets:
            _consider(entry, coll)
    return out


# ─── Async: load latest run docs from Mongo ────────────────────────────
async def _load_recent_runs(
    db, *, lookback_hours: int, sport: str = "football",
) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    runs: list[dict] = []
    for coll_name in ("pick_runs", "picks"):
        coll = getattr(db, coll_name, None)
        if coll is None:
            continue
        try:
            cursor = coll.find(
                {"sport": sport, "generated_at": {"$gte": cutoff}},
                sort=[("generated_at", -1)],
                limit=50,
            )
            async for doc in cursor:
                doc["_collection"] = coll_name
                runs.append(doc)
        except Exception as exc:  # noqa: BLE001
            log.debug("auto-resolver: cannot read %s: %s", coll_name, exc)
    return runs


async def _already_resolved_keys(
    db, *, lookback_hours: int,
) -> set[tuple]:
    """Return (match_id, detected_price) tuples that already have a
    fresh resolution (within the configured cache TTL of the resolver).

    Avoids re-hitting The Odds API on the same pair every cycle.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=max(mir.CACHE_TTL_SECONDS, lookback_hours * 3600),
    )
    keys: set[tuple] = set()
    try:
        cursor = db.market_identity_resolutions.find(
            {"resolved_at": {"$gte": cutoff}},
            sort=[("resolved_at", -1)],
            limit=1000,
        )
        async for d in cursor:
            mid = d.get("match_id")
            price = d.get("detected_price")
            if mid is None or price is None:
                continue
            try:
                keys.add((str(mid), round(float(price), 4)))
            except (TypeError, ValueError):
                continue
    except Exception as exc:  # noqa: BLE001
        log.debug("auto-resolver: cannot read existing resolutions: %s", exc)
    return keys


# ─── Main cycle ────────────────────────────────────────────────────────
async def run_cycle(db) -> dict:
    """Run one auto-resolution cycle. Returns a small report.

    Fail-soft at every step. The scheduler keeps running even on a
    crash here.
    """
    cfg = get_config()
    started = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "enabled":           cfg["enabled"],
        "started_at":        started.isoformat(),
        "finished_at":       None,
        "candidates_seen":   0,
        "resolutions_run":   0,
        "resolutions_ok":    0,
        "resolutions_fail":  0,
        "skipped_cached":    0,
        "reasons":           [],
        "ok":                True,
    }
    _status["enabled"] = cfg["enabled"]

    if not cfg["enabled"]:
        log.info("market_identity_auto_resolver: disabled")
        report["reasons"].append("DISABLED")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _status["last_cycle"] = report
        return report

    try:
        runs = await _load_recent_runs(
            db, lookback_hours=cfg["lookback_hours"],
        )
        if not runs:
            report["reasons"].append("EMPTY_RUNS")
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            _status["last_cycle"] = report
            return report

        already = await _already_resolved_keys(
            db, lookback_hours=cfg["lookback_hours"],
        )
        pending = extract_pending_identities(
            run_docs=runs, already_resolved_keys=already,
        )
        report["candidates_seen"]  = len(pending) + len(already)
        report["skipped_cached"]   = len(already)

        if not pending:
            report["reasons"].append("NOTHING_PENDING")
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            _status["last_cycle"] = report
            return report

        # Cap.
        if len(pending) > cfg["max_per_cycle"]:
            pending = pending[: cfg["max_per_cycle"]]

        for item in pending:
            report["resolutions_run"] += 1
            try:
                match = {
                    "match_id":       item["match_id"],
                    "home_team":      item.get("home_team"),
                    "away_team":      item.get("away_team"),
                    "commence_time":  item.get("commence_time"),
                    "league":         item.get("league"),
                    "sport_key_hint": item.get("sport_key_hint"),
                }
                result = await mir.resolve_market_identity(
                    db, match=match,
                    detected_price=item["detected_price"],
                    use_cache=True,
                )
                status = (result or {}).get("resolution_status", "ERROR")
                if status in ("RESOLVED", "AMBIGUOUS", "CACHED"):
                    report["resolutions_ok"] += 1
                else:
                    report["resolutions_fail"] += 1
            except Exception as exc:  # noqa: BLE001
                report["resolutions_fail"] += 1
                log.warning("auto-resolver: resolution crashed for %s: %s",
                            item.get("match_id"), exc)

        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _status["last_cycle"]         = report
        _status["candidates_seen"]    = (_status.get("candidates_seen") or 0) + report["candidates_seen"]
        _status["resolutions_run"]    = (_status.get("resolutions_run") or 0) + report["resolutions_run"]
        _status["resolutions_skipped"] = (_status.get("resolutions_skipped") or 0) + report["skipped_cached"]
        log.info(
            "auto-resolver cycle: candidates=%d run=%d ok=%d fail=%d cached=%d",
            report["candidates_seen"], report["resolutions_run"],
            report["resolutions_ok"], report["resolutions_fail"],
            report["skipped_cached"],
        )
        return report
    except Exception as exc:  # noqa: BLE001
        log.exception("auto-resolver cycle crashed: %s", exc)
        report["ok"] = False
        report["reasons"].append(f"EXCEPTION:{exc}")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _status["last_error"] = str(exc)
        _status["last_cycle"] = report
        return report


# ─── Scheduler integration ─────────────────────────────────────────────
def register_jobs(scheduler: AsyncIOScheduler, db) -> bool:
    """Register the auto-resolver job. No-op when disabled."""
    cfg = get_config()
    _status["enabled"] = cfg["enabled"]
    if not cfg["enabled"]:
        log.info("market_identity_auto_resolver: not registering job (disabled)")
        return False
    scheduler.add_job(
        run_cycle, args=[db],
        trigger=IntervalTrigger(seconds=cfg["interval_seconds"]),
        id="market_identity_auto_resolver",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        max_instances=1,
        coalesce=True,
    )
    log.info(
        "market_identity_auto_resolver: job registered every %ds (max %d/cycle)",
        cfg["interval_seconds"], cfg["max_per_cycle"],
    )
    return True


__all__ = [
    "DEFAULT_INTERVAL_SECONDS", "DEFAULT_MAX_PER_CYCLE",
    "DEFAULT_LOOKBACK_HOURS",
    "get_config", "get_status",
    "extract_pending_identities",
    "run_cycle", "register_jobs",
]
