"""Baseball Savant — hitter advanced metrics (xwOBA, xSLG, Barrel%,
Hard Hit %, Exit Velocity).

Follows the same fail-soft + 24h cache architecture as
``baseball_savant.py`` for pitchers. The CSV endpoint differs slightly
(``player_type=batter``) and the field set targets hitting metrics.

Usage
-----
::

    from services.baseball_savant_batter import fetch_batter_savant
    data = await fetch_batter_savant(player_id=660271, season=2026, db=db)
    # data → {'xwoba': 0.395, 'xslg': 0.560, 'barrel_pct': 12.1, ...}

Fail-soft contract
------------------
* Returns ``None`` on any error (HTTP timeout, CSV invalid, rate-limit,
  empty result).
* Never raises.
* Cache TTL: 24h per ``(player_id, season)``.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger("baseball_savant_batter")

# Baseball Savant statcast_search CSV endpoint for batters.
_SAVANT_BATTER_URL = (
    "https://baseballsavant.mlb.com/statcast_search/csv?"
    "all=true&type=batter&player_type=batter&player_id={player_id}&"
    "year={season}&min_pas=10&group_by=name-year"
)

_DEFAULT_TIMEOUT  = 6.0     # short — never block the props pipeline
_CACHE_TTL_SECONDS = 24 * 3600

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/csv,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://baseballsavant.mlb.com/",
}

# Canonical → list of acceptable CSV column names (Savant alternates).
_FLOAT_FIELDS: list[tuple[str, list[str]]] = [
    ("xwoba",         ["xwoba", "est_woba", "xwoba_pa"]),
    ("xslg",          ["xslg", "est_slg"]),
    ("woba",          ["woba"]),
    ("slg",           ["slg", "slg_percent"]),
    ("barrel_pct",    ["barrel_batted_rate", "barrels_per_pa_percent", "barrel_pct"]),
    ("hard_hit_pct",  ["hard_hit_percent", "hard_hit_pct"]),
    ("exit_velocity", ["exit_velocity_avg", "avg_hit_speed", "launch_speed"]),
    ("launch_angle",  ["launch_angle_avg", "avg_hit_angle"]),
    ("k_pct",         ["k_percent", "strikeout_pct"]),
    ("bb_pct",        ["bb_percent", "walk_pct"]),
]


# ── In-memory fallback when no Mongo is available (tests / synthetic). ──
_MEM_CACHE: dict[str, tuple[float, dict]] = {}
_MEM_CACHE_MAX = 1000


def _mem_get(key: str) -> Optional[dict]:
    entry = _MEM_CACHE.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if (datetime.now(timezone.utc).timestamp()) >= expires_at:
        _MEM_CACHE.pop(key, None)
        return None
    return value


def _mem_put(key: str, value: dict, ttl: int) -> None:
    if len(_MEM_CACHE) >= _MEM_CACHE_MAX:
        try:
            oldest = min(_MEM_CACHE, key=lambda k: _MEM_CACHE[k][0])
            _MEM_CACHE.pop(oldest, None)
        except ValueError:
            return
    _MEM_CACHE[key] = (datetime.now(timezone.utc).timestamp() + ttl, value)


async def _cache_get(db: Any, key: str) -> Optional[dict]:
    if db is None:
        return _mem_get(key)
    try:
        doc = await db.savant_batter_cache.find_one({"_id": key})
        if not doc:
            return None
        expires_at = doc.get("expires_at")
        if not expires_at:
            return None
        if isinstance(expires_at, (int, float)):
            now = datetime.now(timezone.utc).timestamp()
            if expires_at <= now:
                return None
        return doc.get("data")
    except Exception as exc:
        log.debug("savant batter cache_get failed for %s: %s", key, exc)
        return None


async def _cache_put(db: Any, key: str, data: dict, ttl: int = _CACHE_TTL_SECONDS) -> None:
    _mem_put(key, data, ttl)
    if db is None:
        return
    try:
        expires_at = datetime.now(timezone.utc).timestamp() + ttl
        await db.savant_batter_cache.update_one(
            {"_id": key},
            {"$set": {"data": data, "expires_at": expires_at}},
            upsert=True,
        )
    except Exception as exc:
        log.debug("savant batter cache_put failed for %s: %s", key, exc)


def _parse_csv_row(row: dict) -> dict:
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


async def fetch_batter_savant(
    player_id: int,
    season: Optional[int] = None,
    *,
    db: Any = None,
    timeout_sec: float = _DEFAULT_TIMEOUT,
) -> Optional[dict]:
    """Fetch advanced batter metrics from Baseball Savant.

    Returns a dict with the canonical keys defined in ``_FLOAT_FIELDS``
    plus ``_savant_url`` for transparency. Returns ``None`` on any
    failure path (fail-soft).
    """
    if not player_id:
        return None
    season = season or datetime.now(timezone.utc).year
    key = f"savant:b:{player_id}:{season}"

    cached = await _cache_get(db, key)
    if cached is not None:
        return cached

    url = _SAVANT_BATTER_URL.format(player_id=player_id, season=season)
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            r = await client.get(url, headers=_HEADERS)
    except (httpx.HTTPError, asyncio.TimeoutError, Exception) as exc:
        log.debug("savant batter fetch failed for %s: %s", player_id, exc)
        return None
    if r.status_code >= 400 or not r.text:
        log.debug("savant batter http %s for %s", r.status_code, player_id)
        return None
    body = r.text
    if "<html" in body.lower()[:200] or not body.strip().startswith(("name", "player", "first", "last")):
        return None
    try:
        reader = csv.DictReader(io.StringIO(body))
        rows = list(reader)
    except Exception as exc:
        log.debug("savant batter csv parse failed for %s: %s", player_id, exc)
        return None
    if not rows:
        return None
    data = _parse_csv_row(rows[0])
    if not data:
        return None
    data["_savant_url"] = url
    await _cache_put(db, key, data)
    return data


async def enrich_batter_dict(p: dict, *, db: Any = None) -> dict:
    """Merge Savant batter fields into a hitter dict in place. Returns
    the same dict for chaining. If Savant fails the dict stays
    unchanged and a ``_savant_failed`` marker is set so the caller can
    tag ``data_quality`` accordingly."""
    pid = p.get("id") or p.get("player_id") or p.get("savant_id")
    if not pid:
        p["_savant_failed"] = True
        return p
    savant = await fetch_batter_savant(int(pid), db=db)
    if not savant:
        p["_savant_failed"] = True
        return p
    for k in ("xwoba", "xslg", "woba", "slg", "barrel_pct",
              "hard_hit_pct", "exit_velocity", "launch_angle",
              "k_pct", "bb_pct"):
        if k in savant and (k not in p or p.get(k) is None):
            p[k] = savant[k]
    p["_savant_url"] = savant.get("_savant_url")
    p["_savant_failed"] = False
    return p


__all__ = ["fetch_batter_savant", "enrich_batter_dict"]
