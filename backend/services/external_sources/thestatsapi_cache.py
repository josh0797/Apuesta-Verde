"""MongoDB cache wrapper for TheStatsAPI payloads.

Collection: ``external_source_cache``
Document shape::

    {
        "source":     "thestatsapi",
        "endpoint":   "competitions" | "live_matches" | "fixtures" | "match_stats",
        "key":        str   # request param signature
        "data":       any,  # JSON-serializable payload
        "_cached_at": ISO8601 (UTC)
    }

TTLs (per user spec, 2026-06-03):
    - competitions:  24h
    - live_matches:  40s
    - fixtures:      5min
    - match_stats:   3min

All methods are fail-soft: if `db is None` or any Mongo call raises,
they return ``None`` (cache miss) so the caller falls back to the live
fetch — no exceptions ever propagate from this module.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

COLLECTION = "external_source_cache"

# TTLs in seconds — easy to import for tests / monitoring.
TTL_SECONDS = {
    "competitions":  24 * 60 * 60,   # 24h
    "live_matches":  40,             # 40s
    "fixtures":      5 * 60,         # 5min
    "match_stats":   3 * 60,         # 3min
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _is_fresh(doc: dict | None, ttl_seconds: int) -> bool:
    if not isinstance(doc, dict):
        return False
    ts = _parse_iso(doc.get("_cached_at"))
    if ts is None:
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age < ttl_seconds


async def cache_get(db, endpoint: str, key: str = "_") -> Any | None:
    """Return cached payload if fresh; otherwise ``None``."""
    if db is None:
        return None
    ttl = TTL_SECONDS.get(endpoint)
    if ttl is None:
        return None
    try:
        doc = await db[COLLECTION].find_one(
            {"source": "thestatsapi", "endpoint": endpoint, "key": key}
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[thestatsapi_cache] read failed for %s/%s: %s", endpoint, key, exc)
        return None
    if not _is_fresh(doc, ttl):
        return None
    return doc.get("data")


async def cache_set(db, endpoint: str, key: str, data: Any) -> None:
    """Upsert payload. Silently no-op if db missing or write fails."""
    if db is None:
        return
    if endpoint not in TTL_SECONDS:
        return
    try:
        await db[COLLECTION].update_one(
            {"source": "thestatsapi", "endpoint": endpoint, "key": key},
            {"$set": {
                "source":     "thestatsapi",
                "endpoint":   endpoint,
                "key":        key,
                "data":       data,
                "_cached_at": _now_iso(),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[thestatsapi_cache] write failed for %s/%s: %s", endpoint, key, exc)


async def cache_clear(db, endpoint: str | None = None) -> int:
    """Purge cache (useful for tests / manual ops).

    Returns the number of documents removed (0 on error).
    """
    if db is None:
        return 0
    try:
        q: dict[str, Any] = {"source": "thestatsapi"}
        if endpoint is not None:
            q["endpoint"] = endpoint
        res = await db[COLLECTION].delete_many(q)
        return int(getattr(res, "deleted_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("[thestatsapi_cache] clear failed: %s", exc)
        return 0
