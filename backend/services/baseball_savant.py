"""Baseball Savant client — fetches advanced pitching metrics (xERA, FIP,
xFIP, Hard Hit %, Barrel %, Exit Velocity allowed) via Savant's CSV
search endpoint.

Why CSV?
========
Baseball Savant doesn't publish a documented JSON API but their
"statcast_search" CSV endpoint (`baseballsavant.mlb.com/statcast_search/csv`)
returns a structured CSV for any player/season query. Each pitcher
season aggregate has computed xERA / FIP / xFIP / hard-hit% / barrel% /
EV that we can lift directly.

Failure modes
=============
* Savant blocks aggressive scrapers; we use a single conservative
  request per pitcher per cache window (24h).
* If the CSV is empty or malformed → fall back to the MLB Stats API
  stats already in `mlb_stats_api.py` (ERA / WHIP / K9 / BB9 only).

This module is **fail-soft**: every error path returns ``None`` so the
caller can continue with whatever it already has.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger("baseball_savant")

# Baseball Savant's public statcast_search CSV endpoint. We query for
# season aggregate stats by player_id.
_SAVANT_PITCHER_URL = (
    "https://baseballsavant.mlb.com/statcast_search/csv?"
    "all=true&type=pitcher&player_type=pitcher&player_id={player_id}&"
    "year={season}&min_pitches=100&min_results=0&group_by=name-year"
)

_DEFAULT_TIMEOUT = 10.0
_CACHE_TTL_HOURS = 24


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/csv,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://baseballsavant.mlb.com/",
}


# ────────────────────────────────────────────────────────────────────────────
# Cache helpers (Mongo)
# ────────────────────────────────────────────────────────────────────────────
async def _cache_get(db: Any, key: str) -> Optional[dict]:
    if db is None:
        return None
    try:
        doc = await db.savant_cache.find_one(
            {"key": key, "expires_at": {"$gt": datetime.now(timezone.utc).isoformat()}},
            {"_id": 0, "data": 1},
        )
        if doc and isinstance(doc.get("data"), dict):
            return doc["data"]
    except Exception as exc:
        log.debug("savant cache read failed: %s", exc)
    return None


async def _cache_put(db: Any, key: str, data: dict) -> None:
    if db is None:
        return
    try:
        expires = datetime.now(timezone.utc).timestamp() + _CACHE_TTL_HOURS * 3600
        await db.savant_cache.replace_one(
            {"key": key},
            {
                "key":        key,
                "data":       data,
                "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
            },
            upsert=True,
        )
    except Exception as exc:
        log.debug("savant cache write failed: %s", exc)


# ────────────────────────────────────────────────────────────────────────────
# CSV parsing
# ────────────────────────────────────────────────────────────────────────────
_FLOAT_FIELDS = (
    ("xera",       ("xera", "x_era")),
    ("fip",        ("fip",)),
    ("xfip",       ("xfip", "x_fip")),
    ("hard_hit",   ("hard_hit_percent", "hard_hit_pct", "hard_hit")),
    ("barrel",     ("barrel_batted_rate", "barrel_pct", "brl_percent")),
    ("exit_velocity", ("avg_exit_velocity", "ev", "exit_velocity")),
    ("k9",         ("k_9", "k_per_9")),
    ("bb9",        ("bb_9", "bb_per_9")),
)


def _parse_csv_row(row: dict) -> dict:
    """Translate Savant CSV columns to our canonical pitcher dict."""
    norm = {k.strip().lower().replace(" ", "_"): v for k, v in row.items()}
    out: dict[str, Any] = {}
    for canonical, candidates in _FLOAT_FIELDS:
        for c in candidates:
            val = norm.get(c)
            if val is None or val == "" or val == "--":
                continue
            try:
                out[canonical] = float(val)
                break
            except (TypeError, ValueError):
                continue
    return out


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────
async def fetch_pitcher_savant(
    player_id: int,
    season: Optional[int] = None,
    *,
    db: Any = None,
    timeout_sec: float = _DEFAULT_TIMEOUT,
) -> Optional[dict]:
    """Fetch Savant advanced pitching stats for one pitcher. Returns
    a dict like {xera, fip, xfip, hard_hit, barrel, exit_velocity, k9, bb9}
    or ``None`` on failure.
    """
    if not player_id:
        return None
    season = season or datetime.now(timezone.utc).year
    key = f"savant:p:{player_id}:{season}"
    cached = await _cache_get(db, key)
    if cached is not None:
        return cached

    url = _SAVANT_PITCHER_URL.format(player_id=player_id, season=season)
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            r = await client.get(url, headers=_HEADERS)
    except (httpx.HTTPError, asyncio.TimeoutError, Exception) as exc:
        log.debug("savant fetch failed for %s: %s", player_id, exc)
        return None
    if r.status_code >= 400 or not r.text:
        log.debug("savant http %s for %s", r.status_code, player_id)
        return None
    body = r.text
    if "<html" in body.lower()[:200] or not body.strip().startswith(("name", "player", "first")):
        # Sometimes Savant returns the HTML search page instead of CSV
        # when the rate limit triggers.
        return None
    try:
        reader = csv.DictReader(io.StringIO(body))
        rows = list(reader)
    except Exception as exc:
        log.debug("savant csv parse failed for %s: %s", player_id, exc)
        return None
    if not rows:
        return None
    data = _parse_csv_row(rows[0])
    if not data:
        return None
    data["_savant_url"] = url
    await _cache_put(db, key, data)
    return data


async def enrich_pitcher_dict(p: dict, *, db: Any = None) -> dict:
    """Merge Savant fields into a pitcher dict in place. Returns the same
    dict for chaining. If the Savant fetch fails we leave the dict
    untouched and the caller's fallbacks (LEAGUE_AVG_*) kick in."""
    pid = p.get("pitcher_id") or p.get("id") or p.get("savant_id")
    if not pid:
        return p
    savant = await fetch_pitcher_savant(int(pid), db=db)
    if not savant:
        return p
    for k in ("xera", "fip", "xfip", "hard_hit", "barrel", "exit_velocity", "k9", "bb9"):
        if k in savant and (k not in p or not p.get(k)):
            p[k] = savant[k]
    p["_savant_url"] = savant.get("_savant_url")
    return p


__all__ = ["fetch_pitcher_savant", "enrich_pitcher_dict"]
