"""Sprint-B · Fix 1 — APScheduler jobs that drive the learning-snapshot
collection lifecycle.

Two jobs:

* ``create_pre_match_snapshots`` — runs every 30 min. For each
  upcoming match in the **2-6h window** before kickoff, ensures a
  snapshot exists (idempotent via the manager) and fires the
  aggregator cascade to populate ``pre_match_inputs``.

* ``refresh_pre_match_snapshots`` — runs every 10 min. For matches
  in the **0-60min window** before kickoff that already have a
  snapshot, re-runs the aggregator to capture lineups / late odds.

Both jobs are **fail-soft**: any per-match exception is logged and
skipped so a single bad fixture cannot derail the rest of the batch.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger("football_learning_snapshot_jobs")

# Time windows (UTC, seconds).
PRE_MATCH_CREATE_MIN_SEC = 2 * 3600       # 2 hours
PRE_MATCH_CREATE_MAX_SEC = 6 * 3600       # 6 hours
PRE_MATCH_REFRESH_MAX_SEC = 60 * 60       # 60 minutes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _kickoff_in_window(match: dict, *, min_sec: int, max_sec: int) -> bool:
    """True iff ``match.kickoff_at`` falls between (now+min_sec, now+max_sec)."""
    if not isinstance(match, dict):
        return False
    ko = match.get("kickoff_at") or match.get("date") or match.get("start_time")
    if ko is None:
        return False
    try:
        if isinstance(ko, str):
            ko = datetime.fromisoformat(ko.replace("Z", "+00:00"))
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return False
    delta = (ko - _now()).total_seconds()
    return min_sec < delta <= max_sec


async def _iter_upcoming_football_matches(db, *, min_sec: int, max_sec: int):
    """Yield football matches whose kickoff is within the given window."""
    if db is None:
        return
    try:
        cursor = db["matches"].find({
            "sport": "football",
            "kickoff_at": {
                "$gte": _now() + timedelta(seconds=min_sec),
                "$lte": _now() + timedelta(seconds=max_sec),
            },
        }).limit(50)
        async for m in cursor:
            yield m
    except Exception as exc:  # noqa: BLE001
        log.debug("upcoming matches query failed: %s", exc)
        return


async def job_create_pre_match_snapshots(db) -> dict:
    """APScheduler entry — create snapshots for matches kicking off in
    the 2-6h window. Returns a small stats dict for /api/health probes.
    """
    from .football_learning_snapshot_manager import (
        create_pre_match_snapshot, refresh_pre_match_snapshot,
    )
    from .football_pre_match_data_aggregator import gather_pre_match_data

    created = 0
    refreshed = 0
    errors = 0
    async for match in _iter_upcoming_football_matches(
        db,
        min_sec=PRE_MATCH_CREATE_MIN_SEC,
        max_sec=PRE_MATCH_CREATE_MAX_SEC,
    ):
        try:
            mid       = match.get("match_id") or match.get("_id")
            home_name = ((match.get("home_team") or {}).get("name")
                          or match.get("home_name") or "")
            away_name = ((match.get("away_team") or {}).get("name")
                          or match.get("away_name") or "")
            kickoff   = match.get("kickoff_at")
            comp      = ((match.get("league") or {}).get("name")
                          or match.get("competition") or "")
            # 1. Run the cascade.
            agg = await gather_pre_match_data(
                home_team=home_name, away_team=away_name, match_id=mid,
                context={
                    "home_team_id": (match.get("home_team") or {}).get("id"),
                    "away_team_id": (match.get("away_team") or {}).get("id"),
                },
            )
            audit_entries = (agg.get("source_audit") or {}).get(
                "pre_match_sources"
            ) or []
            # 2. Persist (idempotent).
            snap_before = await db["football_match_learning_snapshots"]\
                .find_one({"match_id": mid})
            if snap_before is None:
                await create_pre_match_snapshot(
                    db,
                    match_id=mid,
                    home_team=home_name, away_team=away_name,
                    competition=comp,
                    match_date=kickoff,
                    initial_inputs=agg.get("inputs"),
                    source_audit_entries=audit_entries,
                )
                created += 1
            else:
                # Snapshot exists but maybe with sparser data — refresh
                # so newly-available fields are folded in.
                await refresh_pre_match_snapshot(
                    db,
                    match_id=mid,
                    refreshed_inputs=agg.get("inputs") or {},
                    source_audit_entries=audit_entries,
                )
                refreshed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.debug("create_snapshot job per-match failure: %s", exc)
    summary = {"created": created, "refreshed": refreshed, "errors": errors}
    if created or refreshed:
        log.info("[learning_snapshots.create] %s", summary)
    return summary


async def job_refresh_pre_match_snapshots(db) -> dict:
    """APScheduler entry — refresh existing snapshots that are within
    60 min of kickoff. Captures lineups + late odds.
    """
    from .football_learning_snapshot_manager import refresh_pre_match_snapshot
    from .football_pre_match_data_aggregator import gather_pre_match_data

    refreshed = 0
    errors    = 0
    async for match in _iter_upcoming_football_matches(
        db, min_sec=0, max_sec=PRE_MATCH_REFRESH_MAX_SEC
    ):
        try:
            mid = match.get("match_id") or match.get("_id")
            snap = await db["football_match_learning_snapshots"]\
                .find_one({"match_id": mid})
            if snap is None:
                continue   # nothing to refresh
            home_name = ((match.get("home_team") or {}).get("name")
                          or match.get("home_name") or "")
            away_name = ((match.get("away_team") or {}).get("name")
                          or match.get("away_name") or "")
            agg = await gather_pre_match_data(
                home_team=home_name, away_team=away_name, match_id=mid,
                context={
                    "home_team_id": (match.get("home_team") or {}).get("id"),
                    "away_team_id": (match.get("away_team") or {}).get("id"),
                },
            )
            audit_entries = (agg.get("source_audit") or {}).get(
                "pre_match_sources"
            ) or []
            await refresh_pre_match_snapshot(
                db,
                match_id=mid,
                refreshed_inputs=agg.get("inputs") or {},
                source_audit_entries=audit_entries,
            )
            refreshed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.debug("refresh_snapshot job per-match failure: %s", exc)
    summary = {"refreshed": refreshed, "errors": errors}
    if refreshed:
        log.info("[learning_snapshots.refresh] %s", summary)
    return summary


__all__ = [
    "job_create_pre_match_snapshots",
    "job_refresh_pre_match_snapshots",
    "_kickoff_in_window",
    "PRE_MATCH_CREATE_MIN_SEC",
    "PRE_MATCH_CREATE_MAX_SEC",
    "PRE_MATCH_REFRESH_MAX_SEC",
]
