"""Dispatcher — runs every applicable external_sources scraper in parallel
for a list of matches, caches via MongoDB, and returns standardized
EvidenceItem lists keyed by match_id.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import (
    fotmob,
    sofascore,
    flashscore,
    fbref,
    basketball_reference,
    nba_stats,
)
from .base import brightdata_available
from .schema import skipped_evidence

log = logging.getLogger("external_sources.dispatcher")

# Registry — each entry must expose .NAME, .APPLICABLE_SPORTS,
# .REQUIRES_UNLOCKER, and async def fetch(home, away, league, sport).
_SCRAPERS = [
    fotmob,                 # football, requires unlocker
    sofascore,              # all sports, requires unlocker
    flashscore,             # all sports, requires unlocker
    fbref,                  # football, free
    basketball_reference,   # basketball, free
    nba_stats,              # basketball, free
]

CACHE_COLLECTION = "external_source_evidence"
CACHE_TTL_HOURS  = 6
PER_MATCH_TIMEOUT_SEC = 22.0  # whole match (all sources) hard cap


def _cache_key(match_id: str, sport: str) -> str:
    h = hashlib.sha1(f"{sport}|{match_id}".encode("utf-8")).hexdigest()
    return f"{sport}:{match_id}:{h[:8]}"


async def _read_cache(db, key: str, now: datetime) -> Optional[list[dict]]:
    if db is None:
        return None
    try:
        cutoff = (now - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
        doc = await db[CACHE_COLLECTION].find_one(
            {"key": key, "fetched_at": {"$gte": cutoff}}, {"_id": 0, "evidence": 1},
        )
        if doc and isinstance(doc.get("evidence"), list):
            return doc["evidence"]
    except Exception as exc:
        log.debug("ext_src cache read failed: %s", exc)
    return None


async def _write_cache(db, key: str, sport: str, match_id: str, evidence: list[dict]) -> None:
    if db is None:
        return
    try:
        await db[CACHE_COLLECTION].replace_one(
            {"key": key},
            {
                "key":         key,
                "sport":       sport,
                "match_id":    match_id,
                "evidence":    evidence,
                "fetched_at":  datetime.now(timezone.utc).isoformat(),
            },
            upsert=True,
        )
    except Exception as exc:
        log.debug("ext_src cache write failed: %s", exc)


async def _ensure_indexes(db) -> None:
    if db is None:
        return
    try:
        await db[CACHE_COLLECTION].create_index("key", unique=True)
        await db[CACHE_COLLECTION].create_index("match_id")
        await db[CACHE_COLLECTION].create_index(
            "fetched_at", expireAfterSeconds=int(CACHE_TTL_HOURS * 3600 * 4),
        )
    except Exception:
        pass


def _match_meta(m: dict) -> tuple[str, str, str, str]:
    home = (m.get("home_team") or {}).get("name") if isinstance(m.get("home_team"), dict) else m.get("home") or ""
    away = (m.get("away_team") or {}).get("name") if isinstance(m.get("away_team"), dict) else m.get("away") or ""
    league = m.get("league") if isinstance(m.get("league"), str) else (m.get("league") or {}).get("name") or ""
    mid = str(m.get("match_id") or m.get("id") or m.get("fixture_id") or "")
    return mid, str(home), str(away), str(league)


async def _fetch_one_match(m: dict, sport: str) -> list[dict]:
    mid, home, away, league = _match_meta(m)
    bd_ok = brightdata_available()
    # Sprint-D9-HOTFIX3: scrape.do disponible para scrapers que migraron
    # del unlocker BrightData a Scrape.do (Sofascore, etc.).
    try:
        from services.scrape_do_client import is_enabled as _scrapedo_is_enabled
        sd_ok = bool(_scrapedo_is_enabled())
    except Exception:  # noqa: BLE001
        sd_ok = False

    def _unlocker_ok(scraper) -> bool:
        if not getattr(scraper, "REQUIRES_UNLOCKER", False):
            return True
        provider = (getattr(scraper, "UNLOCKER_PROVIDER", "brightdata") or "").lower()
        if provider == "scrapedo":
            return sd_ok
        # default: brightdata
        return bd_ok

    # Pick the scrapers applicable to this sport.
    chosen = [
        s for s in _SCRAPERS
        if sport in getattr(s, "APPLICABLE_SPORTS", set()) and _unlocker_ok(s)
    ]
    if not chosen:
        return []

    async def _safe(scraper):
        try:
            return await asyncio.wait_for(
                scraper.fetch(home, away, league=league, sport=sport),
                timeout=PER_MATCH_TIMEOUT_SEC / max(1, len(chosen)),
            )
        except asyncio.TimeoutError:
            return skipped_evidence(getattr(scraper, "NAME", "unknown"), reason="timeout")
        except Exception as exc:
            return skipped_evidence(getattr(scraper, "NAME", "unknown"), reason=f"crash:{exc}"[:80])

    results = await asyncio.gather(*(_safe(s) for s in chosen), return_exceptions=False)
    # Normalize Nones (shouldn't happen since scrapers always return dict).
    return [r for r in results if isinstance(r, dict)]


async def collect_external_evidence(
    matches: list[dict],
    sport: str,
    *,
    db: Any = None,
    force_refresh: bool = False,
    timeout_sec: float = 60.0,
) -> dict[str, list[dict]]:
    """Collect external evidence for many matches in parallel.

    Returns
    -------
    dict[str, list[EvidenceItem]]
        Keyed by str(match_id). Every requested match WILL be in the dict
        (empty list if nothing could be obtained).
    """
    sport = (sport or "football").lower()
    if not matches:
        return {}
    out: dict[str, list[dict]] = {}
    await _ensure_indexes(db)
    now = datetime.now(timezone.utc)

    async def _process(m: dict):
        mid = str(m.get("match_id") or m.get("id") or "")
        if not mid:
            return
        key = _cache_key(mid, sport)
        if not force_refresh:
            cached = await _read_cache(db, key, now)
            if cached is not None:
                out[mid] = cached
                return
        evidence = await _fetch_one_match(m, sport)
        out[mid] = evidence
        if evidence:
            await _write_cache(db, key, sport, mid, evidence)

    try:
        await asyncio.wait_for(
            asyncio.gather(*(_process(m) for m in matches), return_exceptions=True),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        log.warning("[EXT_SRC_BULK_TIMEOUT] partial results only")

    # Ensure every match has a key (even if empty).
    for m in matches:
        mid = str(m.get("match_id") or m.get("id") or "")
        if mid and mid not in out:
            out[mid] = []
    return out


__all__ = ["collect_external_evidence"]
