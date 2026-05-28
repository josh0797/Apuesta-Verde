"""High-level Editorial Context Service — entry point used by analyst_engine.

Responsibilities:
  • Decide whether P3 enrichment is enabled (env flag).
  • Build the canonical match key.
  • Check MongoDB cache (TTL 6h) before launching Scrapy.
  • Launch Scrapy via `scrapy_runner.run_scrapy` (fail-soft, timeout).
  • Normalise raw items → EditorialContextSignal documents.
  • Persist signals to `editorial_context_signals` collection.
  • Build per-match consensus dict.
  • Return `editorial_context` payload ready to attach to the match.

The service is import-safe even when Scrapy is missing: every public coro is
guarded so the analyst engine can call it unconditionally.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from .editorial_source_registry import (
    enabled_sources,
    server_rendered_sources,
    js_rendered_sources,
)
from .match_key import canonical_match_key
from .editorial_normalizer import (
    build_editorial_context_signal,
    build_consensus,
)

log = logging.getLogger("editorial.service")

EDITORIAL_CONTEXT_VERSION = "p3-mvp.1"
CACHE_COLLECTION = "editorial_context_signals"
CACHE_TTL_HOURS  = 6
MAX_MATCHES_PER_RUN = 8     # P3 spec: max 5–8 shortlisted matches per run
SUBPROCESS_TIMEOUT_SEC = 30  # overall scrapy timeout per analyst run


def _enabled() -> bool:
    return os.environ.get("EDITORIAL_CONTEXT_ENABLED", "true").lower() in ("1", "true", "yes")


def _empty_payload(reason: str = "not_available") -> dict:
    return {
        "available":             False,
        "sources_count":         0,
        "sources":               [],
        "signals":               [],
        "consensus_market":      None,
        "consensus_direction":   None,
        "motivation_notes":      [],
        "risks":                 [],
        "injury_notes":          [],
        "factual_notes":         [],
        "contradiction_flags":   [],
        "freshness_score":       0,
        "reliability_score":     0,
        "narrative_bias_score":  0,
        "_reason":               reason,
        "_engine_version":       EDITORIAL_CONTEXT_VERSION,
    }


async def _read_cached_signals(db, match_key: str, *, now: datetime) -> list[dict]:
    if db is None:
        return []
    cutoff = (now - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    try:
        cursor = db[CACHE_COLLECTION].find(
            {
                "match_key":  match_key,
                "scraped_at": {"$gte": cutoff},
            },
            {"_id": 0},
        ).limit(20)
        return [doc async for doc in cursor]
    except Exception as exc:
        log.debug("editorial cache read failed: %s", exc)
        return []


async def _persist_signals(db, signals: list[dict]) -> None:
    if db is None or not signals:
        return
    try:
        ops = []
        for s in signals:
            ops.append({
                "replaceOne": {
                    "filter":      {"hash": s["hash"]},
                    "replacement": s,
                    "upsert":      True,
                }
            })
        if ops:
            # Use bulk_write via raw command for motor compatibility.
            from pymongo import ReplaceOne
            requests = [ReplaceOne({"hash": s["hash"]}, s, upsert=True) for s in signals]
            await db[CACHE_COLLECTION].bulk_write(requests, ordered=False)
    except Exception as exc:
        log.debug("editorial cache write failed: %s", exc)


async def _ensure_indexes(db) -> None:
    if db is None:
        return
    try:
        await db[CACHE_COLLECTION].create_index("hash", unique=True)
        await db[CACHE_COLLECTION].create_index("match_key")
        await db[CACHE_COLLECTION].create_index("sport")
        await db[CACHE_COLLECTION].create_index("source")
        await db[CACHE_COLLECTION].create_index(
            "scraped_at",
            expireAfterSeconds=int(CACHE_TTL_HOURS * 3600 * 4),
        )
    except Exception:
        pass    # idempotent


async def fetch_editorial_context(
    match: dict,
    *,
    db=None,
    force_refresh: bool = False,
    timeout_sec: float = SUBPROCESS_TIMEOUT_SEC,
) -> dict:
    """Fetch editorial context for ONE match.

    See fetch_editorial_context_bulk for an N-match-at-once variant that
    shares a single Scrapy subprocess (much faster for analyst runs).

    Returns the consensus payload (always present, with `available=False`
    when no signals could be obtained).
    """
    return (await fetch_editorial_context_bulk(
        [match], db=db, force_refresh=force_refresh, timeout_sec=timeout_sec,
    )).get(_safe_match_id(match), _empty_payload("single_fetch_failed"))


def _safe_match_id(match: dict) -> str:
    return str(match.get("match_id") or match.get("id") or canonical_match_key(
        match.get("sport"),
        (match.get("home_team") or {}).get("name") if isinstance(match.get("home_team"), dict) else match.get("home"),
        (match.get("away_team") or {}).get("name") if isinstance(match.get("away_team"), dict) else match.get("away"),
        match.get("kickoff_iso"),
    ))


def _extract_pair(match: dict) -> tuple[Optional[str], Optional[str]]:
    home = match.get("home") or (match.get("home_team") or {}).get("name")
    away = match.get("away") or (match.get("away_team") or {}).get("name")
    return home, away


async def fetch_editorial_context_bulk(
    matches: list[dict],
    *,
    db=None,
    force_refresh: bool = False,
    timeout_sec: float = SUBPROCESS_TIMEOUT_SEC,
) -> dict[str, dict]:
    """Fetch editorial context for many matches in a SINGLE Scrapy invocation.

    Returns a dict keyed by `_safe_match_id(match)` whose value is the
    consensus payload (always present, never raises).
    """
    out: dict[str, dict] = {}
    if not matches:
        return out

    if not _enabled():
        for m in matches:
            out[_safe_match_id(m)] = _empty_payload("disabled_via_env")
        return out

    matches = matches[:MAX_MATCHES_PER_RUN]
    await _ensure_indexes(db)

    now = datetime.now(timezone.utc)
    fresh_signals_by_key: dict[str, list[dict]] = {}
    matches_needing_scrape: list[dict] = []
    key_to_match_id: dict[str, str] = {}

    for m in matches:
        home, away = _extract_pair(m)
        sport      = (m.get("sport") or "football").lower()
        if sport != "football":
            # P3 MVP scope: football only.
            out[_safe_match_id(m)] = _empty_payload("sport_not_supported")
            continue
        match_key  = canonical_match_key(sport, home, away, m.get("kickoff_iso"))
        key_to_match_id[match_key] = _safe_match_id(m)
        if not force_refresh:
            cached = await _read_cached_signals(db, match_key, now=now)
            if cached:
                fresh_signals_by_key[match_key] = cached
                continue
        matches_needing_scrape.append({
            "sport":       sport,
            "home":        home,
            "away":        away,
            "league":      m.get("league") or (m.get("league") if isinstance(m.get("league"), str) else None),
            "kickoff_iso": m.get("kickoff_iso"),
            "match_id":    _safe_match_id(m),
            "_match_key":  match_key,
        })

    # Run THREE backends in parallel (Scrapy + Playwright + Bright Data) over
    # the remaining matches. Each source is dispatched to its native backend
    # based on the registry flags:
    #     • requires_unlocker=True  → Bright Data Web Unlocker API
    #     • requires_js=True        → Playwright (chromium)
    #     • else                    → Scrapy
    # `requires_unlocker` takes precedence over `requires_js` (a source can
    # legitimately need both; the unlocker is the safer bet). Either backend
    # can return empty (or fail) without affecting the others — the union of
    # items is what we normalise downstream.
    new_raws: list[dict] = []
    if matches_needing_scrape:
        try:
            from .scrapy_runner     import run_scrapy
            from .playwright_runner import run_playwright
            from .brightdata_fetcher import run_brightdata

            # P4.1 (2026-05-28): the registry now contains NBA + MLB
            # sources. Build the union of enabled sources from EVERY
            # sport that appears in the matches batch — the dispatcher
            # used to hard-code "football" which silently ignored
            # basketball / baseball editorial coverage.
            sports_in_batch = {
                (m.get("sport") or "football").lower()
                for m in matches_needing_scrape
            } or {"football"}

            def _union(fn):
                seen = set()
                out  = []
                for sp in sports_in_batch:
                    for s in fn(sp):
                        if s["name"] not in seen:
                            seen.add(s["name"])
                            out.append(s)
                return out

            all_enabled         = _union(enabled_sources)
            unlocker_sources    = [s for s in all_enabled if s.get("requires_unlocker")]
            unlocker_names      = {s["name"] for s in unlocker_sources}
            scrapy_sources      = [s for s in _union(server_rendered_sources)
                                   if s["name"] not in unlocker_names]
            playwright_sources  = [s for s in _union(js_rendered_sources)
                                   if s["name"] not in unlocker_names]

            tasks: list = []
            if scrapy_sources:
                tasks.append(asyncio.create_task(
                    run_scrapy(matches_needing_scrape, scrapy_sources, timeout_sec=timeout_sec),
                ))
            else:
                tasks.append(asyncio.sleep(0, result=[]))
            if playwright_sources:
                tasks.append(asyncio.create_task(
                    run_playwright(matches_needing_scrape, playwright_sources, timeout_sec=timeout_sec),
                ))
            else:
                tasks.append(asyncio.sleep(0, result=[]))
            if unlocker_sources:
                tasks.append(asyncio.create_task(
                    run_brightdata(matches_needing_scrape, unlocker_sources, timeout_sec=timeout_sec),
                ))
            else:
                tasks.append(asyncio.sleep(0, result=[]))

            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=timeout_sec + 10.0,
                )
            except asyncio.TimeoutError:
                log.warning("[EDITORIAL_FETCH_TIMEOUT] outer wait_for timed out — partial results only")
                results = []

            for r in results or []:
                if isinstance(r, list):
                    new_raws.extend(r)
                elif isinstance(r, Exception):
                    log.warning("[EDITORIAL_FETCH_ERROR] backend raised: %s", r)
            log.info(
                "[EDITORIAL_FETCH_DONE] scrapy_src=%d playwright_src=%d unlocker_src=%d raw_items=%d",
                len(scrapy_sources), len(playwright_sources), len(unlocker_sources),
                len(new_raws),
            )
        except asyncio.TimeoutError:
            log.warning("[SCRAPY_EDITORIAL_SOURCE_FAILED] outer timeout, returning soft-empty")
            new_raws = []
        except Exception as exc:
            log.warning("[SCRAPY_EDITORIAL_SOURCE_FAILED] outer exception: %s", exc)
            new_raws = []

    # Convert raws to signals and route to per-match buckets.
    by_match_key: dict[str, list[dict]] = {}
    for raw in new_raws or []:
        match_info = raw.get("_match_payload") or {}
        sport      = match_info.get("sport") or "football"
        home       = match_info.get("home")
        away       = match_info.get("away")
        league     = match_info.get("league")
        kickoff    = match_info.get("kickoff_iso")
        try:
            signal = build_editorial_context_signal(
                raw=raw,
                sport=sport,
                home_team=home,
                away_team=away,
                league=league,
                kickoff_iso=kickoff,
            )
            by_match_key.setdefault(signal["match_key"], []).append(signal)
        except Exception as exc:
            log.debug("editorial signal build failed: %s", exc)
            continue

    # Persist newly-built signals (best-effort).
    flat: list[dict] = []
    for sigs in by_match_key.values():
        flat.extend(sigs)
    if flat:
        await _persist_signals(db, flat)

    # Merge cached + freshly scraped per match key.
    for key in set(fresh_signals_by_key) | set(by_match_key):
        merged = (fresh_signals_by_key.get(key) or []) + (by_match_key.get(key) or [])
        dedup: dict[str, dict] = {s.get("hash"): s for s in merged if s.get("hash")}
        consensus = build_consensus(list(dedup.values()))
        consensus["_engine_version"] = EDITORIAL_CONTEXT_VERSION
        consensus["_match_key"]      = key
        match_id = key_to_match_id.get(key)
        if match_id:
            out[match_id] = consensus

    # For matches that yielded nothing, return an explicit empty payload.
    for m in matches:
        mid = _safe_match_id(m)
        if mid not in out:
            out[mid] = _empty_payload("no_signals")
    return out


__all__ = [
    "fetch_editorial_context",
    "fetch_editorial_context_bulk",
    "EDITORIAL_CONTEXT_VERSION",
]
