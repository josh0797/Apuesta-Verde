"""Sprint-B · B1.b — Football Match Learning Snapshot Manager
============================================================

Thin async CRUD layer over the ``football_match_learning_snapshots``
Mongo collection. All persistence goes through this module so the
schema invariants (UUID ids, UTC datetimes, reason codes) are enforced
in one place.

Public API
----------
* ``create_pre_match_snapshot(db, match_id, home_team, away_team, ...)``
* ``refresh_pre_match_snapshot(db, match_id, refreshed_inputs)``
* ``settle_post_match(db, match_id, outputs)``
* ``get_snapshot(db, match_id)``

Each function is **fail-soft** (returns ``{"available": False, ...}``
on error) and **idempotent** (subsequent calls update the existing
document rather than creating duplicates).

Collection indexes (created lazily on first call):
* ``match_id`` ascending (UNIQUE)
* ``snapshot_taken_at`` descending (for time-range queries)
* ``post_match_outputs.over25_hit`` (for learning loop aggregations)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .football_learning_snapshot_schema import (
    SNAPSHOT_PRE_MATCH,
    SCRAPE_COMPLETE,
    SCRAPE_PARTIAL,
    RC_PRE_MATCH_SNAPSHOT_CREATED,
    RC_PRE_MATCH_SNAPSHOT_REFRESHED,
    RC_PRE_MATCH_DATA_PARTIAL,
    RC_POST_MATCH_RESULT_SETTLED,
    RC_POST_MATCH_STATS_PARTIAL,
    RC_POST_MATCH_CORNERS_MISSING,
    new_snapshot_doc,
    validate_pre_match_completeness,
    validate_post_match_completeness,
)

log = logging.getLogger("football_learning_snapshot_manager")

COLLECTION = "football_match_learning_snapshots"

_INDEX_INSTALLED: dict[str, bool] = {}


async def _ensure_indexes(db) -> None:
    """Create indexes once per db instance."""
    if not db:
        return
    key = id(db)
    if _INDEX_INSTALLED.get(key):
        return
    try:
        col = db[COLLECTION]
        await col.create_index("match_id", unique=True, name="uq_match_id")
        await col.create_index([("snapshot_taken_at", -1)], name="snapshot_taken_at_desc")
        await col.create_index("post_match_outputs.over25_hit", sparse=True,
                                name="over25_hit_sparse")
        await col.create_index("post_match_outputs.btts_hit", sparse=True,
                                name="btts_hit_sparse")
        await col.create_index("post_match_outputs.draw_hit", sparse=True,
                                name="draw_hit_sparse")
        _INDEX_INSTALLED[key] = True
    except Exception as exc:  # noqa: BLE001
        log.debug("index install skipped: %s", exc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_snapshot(db, match_id) -> Optional[dict]:
    """Return the learning snapshot for ``match_id`` (or None)."""
    if db is None:
        return None
    try:
        await _ensure_indexes(db)
        return await db[COLLECTION].find_one({"match_id": match_id})
    except Exception as exc:  # noqa: BLE001
        log.debug("get_snapshot failed: %s", exc)
        return None


async def create_pre_match_snapshot(
    db,
    *,
    match_id: int | str,
    home_team: str,
    away_team: str,
    competition: str = "",
    match_date: Optional[datetime] = None,
    initial_inputs: Optional[dict] = None,
    source_audit_entries: Optional[list[dict]] = None,
) -> dict:
    """Create OR return the existing pre-match snapshot for ``match_id``.

    Idempotent: when a snapshot already exists, the existing document
    is returned untouched (call ``refresh_pre_match_snapshot`` to
    refresh).

    Returns the snapshot dict on success, or
    ``{"available": False, "error": ...}`` on failure.
    """
    if db is None:
        return {"available": False, "error": "db_unavailable"}
    await _ensure_indexes(db)

    existing = await get_snapshot(db, match_id)
    if existing:
        return existing

    doc = new_snapshot_doc(
        match_id=match_id,
        home_team=home_team,
        away_team=away_team,
        competition=competition,
        match_date=match_date,
        snapshot_type=SNAPSHOT_PRE_MATCH,
    )

    if isinstance(initial_inputs, dict):
        # Merge only the keys already present in the template (so we
        # don't accidentally store arbitrary garbage).
        for k, v in initial_inputs.items():
            if k in doc["pre_match_inputs"]:
                doc["pre_match_inputs"][k] = v
            elif k == "market_odds" and isinstance(v, dict):
                for ok, ov in v.items():
                    if ok in doc["pre_match_inputs"]["market_odds"]:
                        doc["pre_match_inputs"]["market_odds"][ok] = ov

    # Fold any source-audit entries the caller already collected.
    if source_audit_entries:
        doc["source_audit"]["pre_match_sources"].extend(source_audit_entries)
        # Aggregate scrape_status.
        statuses = {e.get("status") for e in source_audit_entries
                    if isinstance(e, dict)}
        if SCRAPE_COMPLETE in statuses and SCRAPE_PARTIAL not in statuses:
            doc["source_audit"]["scrape_status"] = SCRAPE_COMPLETE
        elif statuses:
            doc["source_audit"]["scrape_status"] = SCRAPE_PARTIAL

    # Reason code based on completeness.
    is_complete, _missing = validate_pre_match_completeness(doc["pre_match_inputs"])
    if not is_complete:
        doc["reason_codes"].append(RC_PRE_MATCH_DATA_PARTIAL)

    try:
        await db[COLLECTION].insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        log.debug("insert_one failed (race?): %s", exc)
        # Race: another worker just created it. Return whatever's in DB.
        existing = await get_snapshot(db, match_id)
        if existing:
            return existing
        return {"available": False, "error": str(exc)}

    return doc


async def refresh_pre_match_snapshot(
    db,
    *,
    match_id: int | str,
    refreshed_inputs: dict,
    source_audit_entries: Optional[list[dict]] = None,
) -> dict:
    """Update an existing pre-match snapshot with refreshed inputs
    (typically 30-60min before kickoff when lineups are out).

    The function performs a key-by-key merge so it never wipes
    previously-collected data.
    """
    if db is None:
        return {"available": False, "error": "db_unavailable"}
    existing = await get_snapshot(db, match_id)
    if not existing:
        return {"available": False, "error": "snapshot_not_found",
                "match_id": match_id}

    set_ops: dict[str, Any] = {
        "snapshot_refreshed_at": _utcnow(),
        "updated_at":            _utcnow(),
    }
    push_audit: list[dict] = list(source_audit_entries or [])

    inputs = existing.get("pre_match_inputs") or {}
    if isinstance(refreshed_inputs, dict):
        for k, v in refreshed_inputs.items():
            if v is None:
                continue
            if k == "market_odds" and isinstance(v, dict):
                cur_odds = inputs.get("market_odds") or {}
                for ok, ov in v.items():
                    if ok in cur_odds and ov is not None:
                        set_ops[f"pre_match_inputs.market_odds.{ok}"] = ov
            elif k in inputs:
                set_ops[f"pre_match_inputs.{k}"] = v

    update: dict[str, Any] = {"$set": set_ops}
    rc_to_add = {RC_PRE_MATCH_SNAPSHOT_REFRESHED}
    if push_audit:
        update["$push"] = {"source_audit.pre_match_sources": {"$each": push_audit}}
    update["$addToSet"] = {"reason_codes": {"$each": sorted(rc_to_add)}}

    try:
        await db[COLLECTION].update_one({"match_id": match_id}, update)
    except Exception as exc:  # noqa: BLE001
        log.debug("refresh failed: %s", exc)
        return {"available": False, "error": str(exc)}

    return await get_snapshot(db, match_id) or {"available": False}


async def settle_post_match(
    db,
    *,
    match_id: int | str,
    outputs: dict,
    source_audit_entries: Optional[list[dict]] = None,
) -> dict:
    """Persist the post-match outcome.

    Computes the *_hit booleans automatically from the score/stats so
    learning loops can aggregate without re-deriving.
    """
    if db is None:
        return {"available": False, "error": "db_unavailable"}
    existing = await get_snapshot(db, match_id)
    if not existing:
        return {"available": False, "error": "snapshot_not_found",
                "match_id": match_id}

    out = dict(existing.get("post_match_outputs") or {})
    if isinstance(outputs, dict):
        for k, v in outputs.items():
            if k in out and v is not None:
                out[k] = v

    # Auto-derive *_hit booleans whenever the scoring data is available.
    hg = out.get("home_goals")
    ag = out.get("away_goals")
    if hg is not None and ag is not None:
        try:
            hg_i = int(hg); ag_i = int(ag)
            total = hg_i + ag_i
            out["home_goals"]  = hg_i
            out["away_goals"]  = ag_i
            out["total_goals"] = total
            if out.get("final_score") is None:
                out["final_score"] = f"{hg_i}-{ag_i}"
            out["draw_hit"]   = (hg_i == ag_i)
            out["btts_hit"]   = (hg_i > 0 and ag_i > 0)
            out["over25_hit"] = (total >= 3)
        except (TypeError, ValueError):
            pass

    # Corners over 8.5 hit derivation
    tc = out.get("total_corners")
    if tc is not None:
        try:
            out["total_corners"]     = int(tc)
            out["over85_corners_hit"] = (int(tc) >= 9)
        except (TypeError, ValueError):
            pass

    set_ops: dict[str, Any] = {
        "post_match_outputs": out,
        "updated_at":         _utcnow(),
    }
    update: dict[str, Any] = {"$set": set_ops}
    if source_audit_entries:
        update["$push"] = {"source_audit.post_match_sources":
                            {"$each": list(source_audit_entries)}}

    # Reason codes
    rc_to_add: set[str] = set()
    is_complete, _missing = validate_post_match_completeness(out)
    if is_complete:
        rc_to_add.add(RC_POST_MATCH_RESULT_SETTLED)
    else:
        rc_to_add.add(RC_POST_MATCH_STATS_PARTIAL)
    if out.get("total_corners") is None:
        rc_to_add.add(RC_POST_MATCH_CORNERS_MISSING)
    update["$addToSet"] = {"reason_codes": {"$each": sorted(rc_to_add)}}

    try:
        await db[COLLECTION].update_one({"match_id": match_id}, update)
    except Exception as exc:  # noqa: BLE001
        log.debug("settle_post_match failed: %s", exc)
        return {"available": False, "error": str(exc)}

    return await get_snapshot(db, match_id) or {"available": False}


__all__ = [
    "COLLECTION",
    "get_snapshot",
    "create_pre_match_snapshot",
    "refresh_pre_match_snapshot",
    "settle_post_match",
]
