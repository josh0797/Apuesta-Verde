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

# ── P4 Moneyball alignment (2026-Q2) ─────────────────────────────────
# Editorial context is now a SECONDARY confirmation layer. It must never
# modify ``confidence`` directly — it can only:
#   * confirm pitcher / lineup / injury context (raises reliability)
#   * surface narrative contradictions vs the Moneyball pipeline
#     (PUBLIC_NARRATIVE_RISK, EDITORIAL_CONTRADICTS_MONEYBALL)
#   * tag MLB-specific signal buckets (pitcher_news, bullpen_news, etc.)
EDITORIAL_CONTEXT_VERSION = "p4-moneyball-context.1"
CACHE_COLLECTION = "editorial_context_signals"
CACHE_TTL_HOURS  = 6
# Pitcher/lineup news goes stale much faster than long-form previews.
CACHE_TTL_FAST_STALE_HOURS = 1.5
MAX_MATCHES_PER_RUN = 8     # P3 spec: max 5–8 shortlisted matches per run
SUBPROCESS_TIMEOUT_SEC = 30  # overall scrapy timeout per analyst run

# MLB editorial tags — every signal returned for sport=baseball must
# carry exactly ONE primary ``mlb_tag``. The mapper produces the raw
# classification; this constant set is the union of supported tags so
# the consumer (UI / analyst_engine) can render them deterministically.
MLB_EDITORIAL_TAGS = (
    "public_narrative",
    "injury_or_lineup_note",
    "pitcher_news",
    "bullpen_news",
    "market_public_bias",
    "weather_or_park_note",
)

# Tags that warrant a faster cache (pitcher_news / lineup_note) — these
# items are time-sensitive and we must NOT serve them stale.
MLB_FAST_STALE_TAGS = ("pitcher_news", "injury_or_lineup_note", "bullpen_news")


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
        # P4 Moneyball metadata (always declared — fail-soft).
        "moneyball_interpretation":   None,
        "editorial_vs_model_alignment": None,
        "used_as_confirmation_only":  True,
        "mlb_tags":                   [],
        "_reason":               reason,
        "_engine_version":       EDITORIAL_CONTEXT_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────
# P4 Moneyball alignment — read-only annotation of an editorial payload
# ─────────────────────────────────────────────────────────────────────
def annotate_editorial_vs_moneyball(
    editorial_payload: dict,
    *,
    pick_payload: dict | None = None,
) -> dict:
    """Tag an editorial payload with Moneyball alignment metadata.

    Never modifies the engine's confidence. Adds:
      * ``moneyball_interpretation`` — short ES summary of how the
        editorial reads vs the Moneyball pipeline.
      * ``editorial_vs_model_alignment`` — ``aligned`` / ``contradicts``
        / ``neutral``.
      * ``contradiction_flags`` — appends ``PUBLIC_NARRATIVE_RISK`` and/or
        ``EDITORIAL_CONTRADICTS_MONEYBALL`` when the editorial pushes
        an Over that the engine's ghost-edge / fragility blocks.

    The annotation is **read-only and fail-soft**: a malformed input
    returns the same dict with the canonical fields stamped as None.
    """
    if not isinstance(editorial_payload, dict):
        return {
            "available":                  False,
            "moneyball_interpretation":   None,
            "editorial_vs_model_alignment": None,
            "used_as_confirmation_only":  True,
            "contradiction_flags":        [],
        }

    pick_payload = pick_payload if isinstance(pick_payload, dict) else {}
    editorial_dir = (editorial_payload.get("consensus_direction") or "").lower()
    editorial_market = (editorial_payload.get("consensus_market") or "")

    # Detect Moneyball "no Over" signals.
    ghost = pick_payload.get("ghost_edges") or {}
    ghost_flags = ghost.get("flags") or [] if isinstance(ghost, dict) else []
    ghost_blocked = bool(isinstance(ghost, dict) and ghost.get("blocked_pick"))

    fragility = pick_payload.get("fragility_score") or {}
    frag_tier = fragility.get("tier") if isinstance(fragility, dict) else None

    market_selection = pick_payload.get("market_selection") or {}
    engine_market = (market_selection.get("recommended_market") or
                     (pick_payload.get("recommendation") or {}).get("market") or "")

    contradiction_flags = list(editorial_payload.get("contradiction_flags") or [])
    alignment = "neutral"
    interpretation = None

    if editorial_dir and engine_market:
        ed_is_over = editorial_dir in ("over", "alta", "high") or "over" in editorial_market.lower()
        ed_is_under = editorial_dir in ("under", "baja", "low") or "under" in editorial_market.lower()
        eng_is_over = "over" in engine_market.lower() and "team total" not in engine_market.lower()
        eng_is_under = "under" in engine_market.lower() and "team total" not in engine_market.lower()
        if ed_is_over and eng_is_under:
            alignment = "contradicts"
            interpretation = (
                "Narrativa editorial sugiere Over; modelo Moneyball "
                "favorece Under (revisar antes de apostar)."
            )
        elif ed_is_under and eng_is_over:
            alignment = "contradicts"
            interpretation = (
                "Narrativa editorial sugiere Under; modelo Moneyball "
                "favorece Over."
            )
        elif (ed_is_over and eng_is_over) or (ed_is_under and eng_is_under):
            alignment = "aligned"
            interpretation = "Editorial confirma la dirección del modelo."

    # Ghost edges + Over narrative → public narrative risk.
    if ghost_blocked or ghost_flags:
        ed_over_like = editorial_dir == "over" or "over" in (editorial_market or "").lower()
        if ed_over_like:
            if "PUBLIC_NARRATIVE_RISK" not in contradiction_flags:
                contradiction_flags.append("PUBLIC_NARRATIVE_RISK")
            if "EDITORIAL_CONTRADICTS_MONEYBALL" not in contradiction_flags:
                contradiction_flags.append("EDITORIAL_CONTRADICTS_MONEYBALL")
            alignment = "contradicts"
            interpretation = interpretation or (
                "El consenso editorial empuja Over, pero Moneyball "
                "detecta ghost-edges / fragilidad alta — trampa pública."
            )

    if frag_tier == "HIGH" and editorial_dir in ("over", "alta"):
        if "PUBLIC_NARRATIVE_RISK" not in contradiction_flags:
            contradiction_flags.append("PUBLIC_NARRATIVE_RISK")

    editorial_payload["moneyball_interpretation"]   = interpretation
    editorial_payload["editorial_vs_model_alignment"] = alignment
    editorial_payload["used_as_confirmation_only"]  = True
    editorial_payload["contradiction_flags"]        = contradiction_flags
    editorial_payload.setdefault("mlb_tags", [])
    return editorial_payload


def extract_mlb_tag(signal: dict) -> str | None:
    """Classify a raw editorial signal into one of MLB_EDITORIAL_TAGS.

    Pure heuristic — never raises. Returns None for non-MLB signals.
    """
    if not isinstance(signal, dict):
        return None
    sport = (signal.get("sport") or "").lower()
    if sport not in ("baseball", "mlb"):
        return None

    text = (signal.get("text") or signal.get("body") or "").lower()
    sig_type = (signal.get("signal_type") or "").upper()

    if sig_type == "INJURY_NOTE" or any(
        kw in text for kw in ("lineup", "alineación", "alineacion",
                                 "scratched", "lesionado", "baja", "injur")
    ):
        return "injury_or_lineup_note"
    if any(kw in text for kw in ("bullpen", "relevo", "relief", "closer",
                                  "set up", "cerrador")):
        return "bullpen_news"
    if any(kw in text for kw in ("pitcher", "starter", "abridor",
                                  "opener", "probable", "rotación", "rotacion")):
        return "pitcher_news"
    if any(kw in text for kw in ("público", "publico", "narrativa",
                                  "consenso", "trampa pública", "mercado")):
        return "market_public_bias"
    if any(kw in text for kw in ("weather", "clima", "wind", "viento",
                                  "park", "coors", "estadio")):
        return "weather_or_park_note"
    return "public_narrative"


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

    # Supported sports (registry-driven). Any match whose sport is NOT in
    # this set returns an empty payload immediately. Football was the MVP
    # scope; basketball (NBA) and baseball (MLB) sources were added later
    # to the registry — gate them in here so the dispatcher actually runs
    # for those sports too. Previously this method hard-coded
    # `if sport != "football"` and silently dropped every NBA/MLB match.
    SUPPORTED_EDITORIAL_SPORTS = {"football", "basketball", "baseball"}
    for m in matches:
        home, away = _extract_pair(m)
        sport      = (m.get("sport") or "football").lower()
        if sport not in SUPPORTED_EDITORIAL_SPORTS:
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
    "annotate_editorial_vs_moneyball",
    "extract_mlb_tag",
    "EDITORIAL_CONTEXT_VERSION",
    "MLB_EDITORIAL_TAGS",
    "MLB_FAST_STALE_TAGS",
]
