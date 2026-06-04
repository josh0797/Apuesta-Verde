"""MLB bucket validation tracker.

Tracks the first day a whitelisted bucket reached the apply_eligible
threshold + the days elapsed since. After 14 days of continuous
observation in preview, the bucket auto-transitions from
``OBSERVING`` to ``VALIDATED`` and the orchestrator is allowed to
apply confidence/fragility adjustments per the tier ladder.

Mongo collection
----------------
``mlb_bucket_validation_tracker``::

    {
        "_id":               <ObjectId>,        # mongo-managed
        "bucket_key":        "pressure.HIGH_PRESSURE",
        "first_eligible_at": "2026-06-04T12:00:00+00:00",   # ISO
        "first_eligible_sample_size": 92,
        "latest_seen_at":    "2026-06-18T12:00:00+00:00",
        "latest_sample_size": 124,
        "status":            "OBSERVING" | "VALIDATED" | "RESET",
        "validated_at":      "2026-06-18T12:00:00+00:00",   # set on transition
        "reset_count":       0,                              # times sample dropped <90
    }

Public API
----------
* :func:`upsert_bucket_observation(db, *, bucket_key, sample_size,
    apply_eligible)` — call from the orchestrator whenever a summary
    pass shows the bucket. Idempotent per-day.
* :func:`get_bucket_validation_state(db, bucket_key)` — read helper
  used by the orchestrator to decide whether to apply caps.
* :func:`get_bucket_validation_overview(db)` — returns a dict for the UI.

Fail-soft: every function returns a sane default (``None`` or empty
dict) when the DB is unreachable / collection missing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("mlb_bucket_validation_tracker")

COLLECTION_NAME = "mlb_bucket_validation_tracker"

# Days of continuous OBSERVING before a bucket transitions to VALIDATED.
VALIDATION_WINDOW_DAYS = 14

# Status values
STATUS_OBSERVING = "OBSERVING"
STATUS_VALIDATED = "VALIDATED"
STATUS_RESET     = "RESET"

# Buckets whose validation we actively track. Mirrors
# ``BUCKETS_APPLY_WHITELIST`` in mlb_pregame_analytics_v2 + the
# in-summary ``_SUMMARY_APPLY_WHITELIST``. Kept here as a local copy
# to avoid an engine-import cycle.
TRACKED_BUCKETS: frozenset = frozenset({
    "pressure.HIGH_PRESSURE",
    "park.HITTER_FRIENDLY",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _days_between(start_iso: Any, end_iso: Optional[Any] = None) -> Optional[int]:
    """Whole UTC days between two ISO timestamps. Returns ``None`` when
    inputs cannot be parsed."""
    start = _parse_iso(start_iso)
    if start is None:
        return None
    end = _parse_iso(end_iso) or datetime.now(timezone.utc)
    delta = end - start
    return max(0, int(delta.total_seconds() // 86400))


async def upsert_bucket_observation(
    db,
    *,
    bucket_key: str,
    sample_size: int,
    apply_eligible: bool,
) -> Optional[dict]:
    """Upsert one observation for ``bucket_key``.

    Behaviour
    ---------
    * First time the bucket is seen apply-eligible → create the doc
      with ``status=OBSERVING`` + ``first_eligible_at = now``.
    * Subsequent eligible observations → bump ``latest_*`` fields. If
      ``days_since_first_eligible >= VALIDATION_WINDOW_DAYS`` AND the
      status is still ``OBSERVING``, transition to ``VALIDATED``.
    * Observation where ``apply_eligible == False`` after a doc exists
      → mark ``status=RESET`` and bump ``reset_count``. The tracker
      will require a fresh 14-day observation window when it climbs
      back over the threshold.

    Returns the latest doc snapshot or ``None`` on DB error.
    """
    if db is None or not bucket_key or bucket_key not in TRACKED_BUCKETS:
        return None
    try:
        coll = db[COLLECTION_NAME]
        existing = await coll.find_one({"bucket_key": bucket_key})
        now_iso = _now_iso()
        if not existing:
            if not apply_eligible:
                # Don't create the doc until the bucket first crosses
                # the threshold — keeps the tracker tidy.
                return None
            doc = {
                "bucket_key":                bucket_key,
                "first_eligible_at":         now_iso,
                "first_eligible_sample_size": int(sample_size),
                "latest_seen_at":            now_iso,
                "latest_sample_size":        int(sample_size),
                "status":                    STATUS_OBSERVING,
                "validated_at":              None,
                "reset_count":               0,
            }
            await coll.insert_one(doc)
            log.info("bucket %s first eligible (n=%d) → OBSERVING",
                      bucket_key, sample_size)
            return doc

        # An existing record — decide what to do based on apply_eligible.
        update_set: dict[str, Any] = {
            "latest_seen_at":     now_iso,
            "latest_sample_size": int(sample_size),
        }
        if not apply_eligible:
            # Bucket dropped below the threshold — reset.
            if existing.get("status") != STATUS_RESET:
                update_set["status"] = STATUS_RESET
                update_set["validated_at"] = None
                # Wipe the first_eligible_at so a future climb starts a
                # fresh 14-day window.
                update_set["first_eligible_at"] = None
                update_set["first_eligible_sample_size"] = 0
                log.info("bucket %s fell below threshold (n=%d) → RESET",
                          bucket_key, sample_size)
            update_inc = {"reset_count": 1}
            await coll.update_one(
                {"bucket_key": bucket_key},
                {"$set": update_set, "$inc": update_inc},
            )
        else:
            # Eligible. If the previous status was RESET, start a fresh
            # OBSERVING window NOW.
            if existing.get("status") == STATUS_RESET or not existing.get("first_eligible_at"):
                update_set["status"] = STATUS_OBSERVING
                update_set["first_eligible_at"] = now_iso
                update_set["first_eligible_sample_size"] = int(sample_size)
                update_set["validated_at"] = None
                log.info("bucket %s back over threshold (n=%d) → OBSERVING",
                          bucket_key, sample_size)
            else:
                # Check for auto-validation.
                days = _days_between(existing.get("first_eligible_at"), now_iso) or 0
                if (existing.get("status") == STATUS_OBSERVING
                        and days >= VALIDATION_WINDOW_DAYS):
                    update_set["status"] = STATUS_VALIDATED
                    update_set["validated_at"] = now_iso
                    log.info(
                        "bucket %s auto-validated after %d days (n=%d) → VALIDATED",
                        bucket_key, days, sample_size,
                    )
            await coll.update_one({"bucket_key": bucket_key}, {"$set": update_set})

        return await coll.find_one({"bucket_key": bucket_key})
    except Exception as exc:
        log.debug("upsert_bucket_observation failed for %s: %s", bucket_key, exc)
        return None


async def get_bucket_validation_state(db, bucket_key: str) -> dict:
    """Return ``{status, days_since_first_eligible, validated, ...}`` for
    one bucket. ``status`` defaults to ``OBSERVING`` when no doc exists
    yet (i.e. the bucket has never been apply-eligible)."""
    default = {
        "bucket_key":  bucket_key,
        "status":      STATUS_OBSERVING,
        "validated":   False,
        "days_since_first_eligible": None,
        "days_until_validated":      VALIDATION_WINDOW_DAYS,
        "validation_window_days":    VALIDATION_WINDOW_DAYS,
        "first_eligible_at":         None,
        "latest_sample_size":        0,
    }
    if db is None or not bucket_key:
        return default
    try:
        doc = await db[COLLECTION_NAME].find_one({"bucket_key": bucket_key}) or {}
    except Exception as exc:
        log.debug("get_bucket_validation_state failed: %s", exc)
        return default
    if not doc:
        return default
    days = _days_between(doc.get("first_eligible_at"))
    status = doc.get("status") or STATUS_OBSERVING
    return {
        "bucket_key":                bucket_key,
        "status":                    status,
        "validated":                 status == STATUS_VALIDATED,
        "days_since_first_eligible": days,
        "days_until_validated": (
            max(0, VALIDATION_WINDOW_DAYS - (days or 0))
            if status == STATUS_OBSERVING else 0
        ),
        "validation_window_days":    VALIDATION_WINDOW_DAYS,
        "first_eligible_at":         doc.get("first_eligible_at"),
        "latest_seen_at":            doc.get("latest_seen_at"),
        "latest_sample_size":        doc.get("latest_sample_size") or 0,
        "validated_at":              doc.get("validated_at"),
        "reset_count":               doc.get("reset_count", 0),
    }


async def get_bucket_validation_overview(db) -> dict:
    """Return validation state for ALL tracked buckets. Used by the UI
    panel to render a 'validation roadmap' alongside the calibration
    panel.

    Returns ``{<bucket_key>: <state dict>, ...}``.
    """
    if db is None:
        return {b: await get_bucket_validation_state(None, b) for b in TRACKED_BUCKETS}
    out: dict[str, dict] = {}
    for bkey in TRACKED_BUCKETS:
        out[bkey] = await get_bucket_validation_state(db, bkey)
    return out


__all__ = [
    "COLLECTION_NAME",
    "VALIDATION_WINDOW_DAYS",
    "TRACKED_BUCKETS",
    "STATUS_OBSERVING",
    "STATUS_VALIDATED",
    "STATUS_RESET",
    "upsert_bucket_observation",
    "get_bucket_validation_state",
    "get_bucket_validation_overview",
]
