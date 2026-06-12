"""Phase F70 — External editorial context provider.

Orchestrates scraping + caching across the two whitelisted external
sources (Sportytrader + Forebet) and produces a normalised "external
editorial context" payload consumed by the internal editorial engine
and the discard cards in the UI.

Public API
----------
``async fetch_external_editorial_for_match(match) -> dict``
    Returns a normalised payload (fail-soft) containing data from both
    sources when available, plus a ``reason_codes`` audit trail.

``async fetch_forebet_index() -> dict``
    Cached Forebet fixtures index (24h TTL).

``async build_sportytrader_search_url(match) -> Optional[str]``
    Best-effort URL discovery using a deterministic slug pattern. When
    we don't know the numeric Sportytrader ID, we let the caller (UI)
    expose a generic search link instead.

Storage
-------
MongoDB collection ``external_editorial_cache`` with TTL 24h on
``cached_at``. One document per ``cache_key``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("external_editorial")

# Mongo handle is lazily resolved from the running app's globals when
# possible to avoid creating duplicate clients in tests.
_db_handle = None


def _get_db():
    global _db_handle
    if _db_handle is not None:
        return _db_handle
    try:
        from server import db  # type: ignore
        _db_handle = db
        return db
    except Exception:  # noqa: BLE001
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo_url = os.environ.get("MONGO_URL")
            if not mongo_url:
                return None
            client = AsyncIOMotorClient(mongo_url)
            _db_handle = client.get_default_database()
            return _db_handle
        except Exception:  # noqa: BLE001
            return None


def _strip_accents(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


def _team_slug(name: str) -> str:
    """Build a Sportytrader-compatible slug from a team name."""
    if not isinstance(name, str):
        return ""
    s = _strip_accents(name).strip()
    s = re.sub(r"[^a-z0-9\s\-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def build_sportytrader_search_url(home: str, away: str) -> Optional[str]:
    """Best-effort guess of the canonical Sportytrader match URL.

    Sportytrader's URLs follow the pattern:
        /es/pronosticos/<home>-<away>-<numeric_id>/
    where <numeric_id> is opaque (we don't know it without listing the
    parent index). We instead emit a SEARCH URL on the site so the UI
    can fall back to a deep link without scraping.
    """
    if not home or not away:
        return None
    hs, as_ = _team_slug(home), _team_slug(away)
    if not hs or not as_:
        return None
    # Public search-by-query URL — kept as a fallback the UI can open.
    return ("https://www.sportytrader.com/es/?s="
            + hs.replace("-", "+") + "+vs+" + as_.replace("-", "+"))


def build_sportytrader_match_url(home: str, away: str,
                                   numeric_id: str | int) -> Optional[str]:
    """When the upstream cache or H2H ingestor knows the numeric id."""
    if not numeric_id or not home or not away:
        return None
    return (f"https://www.sportytrader.com/es/pronosticos/"
            f"{_team_slug(home)}-{_team_slug(away)}-{numeric_id}/")


# ─────────────────────────────────────────────────────────────────────
# Cache layer
# ─────────────────────────────────────────────────────────────────────
async def _cache_lookup(cache_key: str) -> Optional[dict]:
    db = _get_db()
    if db is None:
        return None
    try:
        doc = await db.external_editorial_cache.find_one({"cache_key": cache_key})
        return doc
    except Exception as exc:  # noqa: BLE001
        log.debug("[F70_CACHE] lookup failed: %s", exc)
        return None


async def _cache_save(cache_key: str, payload: dict) -> None:
    db = _get_db()
    if db is None:
        return
    try:
        await db.external_editorial_cache.update_one(
            {"cache_key": cache_key},
            {"$set": {
                "cache_key": cache_key,
                "payload":   payload,
                "cached_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[F70_CACHE] save failed: %s", exc)


async def ensure_indexes() -> None:
    """Idempotent index ensure (TTL 24h)."""
    db = _get_db()
    if db is None:
        return
    try:
        await db.external_editorial_cache.create_index(
            "cached_at", expireAfterSeconds=86_400,
        )
        await db.external_editorial_cache.create_index(
            "cache_key", unique=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[F70_CACHE] ensure_indexes failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────
# Forebet — cached fixtures index
# ─────────────────────────────────────────────────────────────────────
async def fetch_forebet_index() -> dict:
    """Fetch + cache the Forebet predictions index (covers fixtures
    across the next ~2 days). 24h TTL.
    """
    cache_key = "forebet:fixtures-index"
    cached = await _cache_lookup(cache_key)
    if cached and isinstance(cached.get("payload"), dict):
        return cached["payload"]

    try:
        from services.scrape_do_client import fetch_via_scrapedo
        from services.forebet_scraper import parse_forebet_fixtures_page
        html = await fetch_via_scrapedo(
            "https://www.forebet.com/es/predicciones-de-futbol",
            timeout=45.0,
        )
        if not html:
            return {"available": False, "reason_codes": ["FOREBET_FETCH_FAILED"]}
        payload = parse_forebet_fixtures_page(html)
        await _cache_save(cache_key, payload)
        return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("[F70_FOREBET_INDEX] failed: %s", exc)
        return {"available": False, "reason_codes": ["FOREBET_INDEX_ERROR"]}


# ─────────────────────────────────────────────────────────────────────
# Sportytrader — per-match page (when URL known)
# ─────────────────────────────────────────────────────────────────────
async def fetch_sportytrader_match(url: str) -> dict:
    """Fetch + cache a Sportytrader match page. Cache key = the URL.
    24h TTL.
    """
    cache_key = f"sportytrader:{url}"
    cached = await _cache_lookup(cache_key)
    if cached and isinstance(cached.get("payload"), dict):
        return cached["payload"]
    try:
        from services.scrape_do_client import fetch_via_scrapedo
        from services.sportytrader_scraper import parse_sportytrader_match_page
        html = await fetch_via_scrapedo(url, timeout=60.0)
        if not html:
            payload = {"available": False, "reason_codes": ["SPORTYTRADER_FETCH_FAILED"]}
        else:
            payload = parse_sportytrader_match_page(html)
            payload["source_url"] = url
        await _cache_save(cache_key, payload)
        return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("[F70_SPORTYTRADER] fetch failed for %s: %s", url, exc)
        return {"available": False, "reason_codes": ["SPORTYTRADER_ERROR"],
                "source_url": url}


# ─────────────────────────────────────────────────────────────────────
# Top-level orchestrator
# ─────────────────────────────────────────────────────────────────────
async def fetch_external_editorial_for_match(match: dict) -> dict:
    """Fetch external editorial context for a single match.

    Strategy:
      1. Look up the Forebet index → grab the matching fixture (1X2,
         predicted score, goals_avg, pick_1x2).
      2. If the match dict carries a known ``sportytrader_url`` (from
         prior cache or the H2H ingestor), fetch the Sportytrader
         match page and parse stats + recent results + prediction.
      3. Otherwise, expose the Sportytrader SEARCH URL so the UI can
         link out.
    """
    home = _resolve_team_name(match, "home")
    away = _resolve_team_name(match, "away")
    if not home or not away:
        return {"available": False, "reason_codes": ["EXTERNAL_TEAMS_MISSING"]}

    forebet_index = await fetch_forebet_index()
    forebet_fixture = None
    if forebet_index.get("available"):
        try:
            from services.forebet_scraper import find_fixture
            forebet_fixture = find_fixture(forebet_index, home, away)
        except Exception:  # noqa: BLE001
            forebet_fixture = None

    sporty_payload: dict = {"available": False,
                             "reason_codes": ["SPORTYTRADER_URL_UNKNOWN"]}
    sporty_url = (match.get("sportytrader_url")
                  or (forebet_fixture or {}).get("sportytrader_url"))
    if sporty_url:
        sporty_payload = await fetch_sportytrader_match(sporty_url)
    else:
        sporty_payload["search_url"] = build_sportytrader_search_url(home, away)

    reason_codes: list[str] = ["EXTERNAL_EDITORIAL_ATTEMPTED"]
    if forebet_index.get("available"):
        reason_codes.append("FOREBET_INDEX_AVAILABLE")
    if forebet_fixture:
        reason_codes.append("FOREBET_FIXTURE_MATCHED")
    if sporty_payload.get("available"):
        reason_codes.append("SPORTYTRADER_AVAILABLE")
    for c in (sporty_payload.get("reason_codes") or []):
        if c not in reason_codes:
            reason_codes.append(c)

    return {
        "available":   bool(forebet_fixture) or bool(sporty_payload.get("available")),
        "home_team":   home,
        "away_team":   away,
        "forebet":     forebet_fixture or {"available": False},
        "sportytrader": sporty_payload,
        "reason_codes": reason_codes,
    }


def _resolve_team_name(match: dict, side: str) -> str:
    if not isinstance(match, dict):
        return ""
    key = "home_team" if side == "home" else "away_team"
    val = match.get(key)
    if isinstance(val, dict):
        return val.get("name") or val.get("label") or ""
    if isinstance(val, str):
        return val
    flat = match.get("home_team_name" if side == "home" else "away_team_name")
    if isinstance(flat, str):
        return flat
    label = match.get("match_label")
    if isinstance(label, str):
        for sep in (r"\s+vs\.?\s+", r"\s+v\s+", r"\s+-\s+",
                    r"\s+\u2013\s+", r"\s+\u2014\s+"):
            parts = re.split(sep, label, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                return parts[0].strip() if side == "home" else parts[1].strip()
    return ""


__all__ = [
    "ensure_indexes",
    "fetch_forebet_index",
    "fetch_sportytrader_match",
    "fetch_external_editorial_for_match",
    "build_sportytrader_search_url",
    "build_sportytrader_match_url",
]
