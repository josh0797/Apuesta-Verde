"""Mongo-backed TTL cache for injury_intelligence payloads.

Fail-soft — a missing DB or a write error never blocks the live fetch.
Collection: ``injury_intel_cache``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("injury_intelligence.cache")

COLLECTION = "injury_intel_cache"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_for(sport: str, is_game_day: bool) -> timedelta:
    if sport == "basketball":
        return timedelta(minutes=30) if is_game_day else timedelta(hours=2)
    return timedelta(hours=1) if is_game_day else timedelta(hours=4)


async def cache_get(
    db,
    *,
    cache_key: str,
    sport: str = "basketball",
    is_game_day: bool = False,
) -> Optional[dict]:
    if db is None:
        return None
    try:
        doc = await db[COLLECTION].find_one({"_id": cache_key})
        if not doc:
            return None
        fetched_at = doc.get("fetched_at")
        if isinstance(fetched_at, str):
            try:
                fetched_at = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            except Exception:
                fetched_at = None
        if fetched_at and _now() - fetched_at > _ttl_for(sport, is_game_day):
            return None
        payload = doc.get("payload")
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        log.debug("injury_intel cache_get failed: %s", exc)
        return None


async def cache_set(db, *, cache_key: str, payload: dict) -> None:
    if db is None:
        return
    try:
        await db[COLLECTION].replace_one(
            {"_id": cache_key},
            {
                "_id":         cache_key,
                "fetched_at":  _now().isoformat(),
                "payload":     payload,
            },
            upsert=True,
        )
    except Exception as exc:
        log.debug("injury_intel cache_set failed: %s", exc)


__all__ = ["cache_get", "cache_set", "COLLECTION"]
