"""MLB team stats + bullpen usage helpers.

Uses MLB Stats API directly. NO scraping. Fail-soft (returns empty
dicts when data is unavailable; caller falls back to league averages).

Public API
==========
    get_team_hand_splits(db, team_id, season=...) -> dict
        Returns {vs_lhp: {...}, vs_rhp: {...}} with AVG/OBP/SLG/OPS/RPG
        from MLB Stats API splits hydrate.

    get_team_bullpen_usage(db, team_id, *, days=7) -> dict
        Computes innings_last_48h / 3d / 7d + bullpen_era_7d from
        the team's recent gameLogs. Saves to mongo cache (30min).

Both functions use 30-min Mongo cache so repeated calls during the same
day are free.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger("mlb_team_stats")

_BASE = "https://statsapi.mlb.com/api/v1"
_CACHE_TTL_MIN = 30
_DEFAULT_SEASON = datetime.now(timezone.utc).year


async def _cache_get(db, key: str) -> Optional[dict]:
    if db is None:
        return None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=_CACHE_TTL_MIN)).isoformat()
        doc = await db.team_stats_cache.find_one(
            {"key": key, "fetched_at": {"$gte": cutoff}}, {"_id": 0, "data": 1},
        )
        if doc and isinstance(doc.get("data"), dict):
            return doc["data"]
    except Exception:
        pass
    return None


async def _cache_put(db, key: str, data: dict) -> None:
    if db is None:
        return
    try:
        await db.team_stats_cache.replace_one(
            {"key": key},
            {"key": key, "data": data, "fetched_at": datetime.now(timezone.utc).isoformat()},
            upsert=True,
        )
    except Exception:
        pass


async def _safe_get_json(url: str, *, timeout: float = 8.0) -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "MLB-Engine/1.0"})
    except Exception as exc:
        log.debug("statsapi fetch failed %s: %s", url, exc)
        return None
    if r.status_code >= 400:
        return None
    try:
        return r.json()
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────────────────
# Team hand splits (vs LHP / RHP)
# ────────────────────────────────────────────────────────────────────────────
async def get_team_hand_splits(
    db: Any, team_id: int, *, season: int = _DEFAULT_SEASON,
) -> dict:
    if not team_id:
        return {}
    key = f"team_hand:{team_id}:{season}"
    cached = await _cache_get(db, key)
    if cached:
        return cached

    url = (
        f"{_BASE}/teams/{team_id}/stats"
        f"?stats=statSplits&season={season}&sportIds=1&group=hitting"
        f"&sitCodes=vl,vr"  # vl = vs LHP, vr = vs RHP
    )
    data = await _safe_get_json(url)
    if not data:
        return {}
    splits = (data.get("stats") or [{}])[0].get("splits") or []
    out: dict = {"vs_lhp": {}, "vs_rhp": {}, "_source_url": url}
    for s in splits:
        sit = (s.get("split") or {}).get("code", "")
        bucket = "vs_lhp" if sit == "vl" else "vs_rhp" if sit == "vr" else None
        if not bucket:
            continue
        stat = s.get("stat") or {}
        try:
            avg = float(stat.get("avg") or 0)
            obp = float(stat.get("obp") or 0)
            slg = float(stat.get("slg") or 0)
            ops = float(stat.get("ops") or (obp + slg))
            ab  = int(stat.get("atBats") or 0)
            r   = int(stat.get("runs") or 0)
            games = int(stat.get("gamesPlayed") or 1)
            out[bucket] = {
                "avg":           avg,
                "obp":           obp,
                "slg":           slg,
                "ops":           ops,
                "runs_per_game": round(r / max(1, games), 2),
                "at_bats":       ab,
                "games":         games,
            }
        except (TypeError, ValueError):
            continue
    if out["vs_lhp"] or out["vs_rhp"]:
        await _cache_put(db, key, out)
    return out


# ────────────────────────────────────────────────────────────────────────────
# Bullpen usage
# ────────────────────────────────────────────────────────────────────────────
async def get_team_bullpen_usage(
    db: Any, team_id: int, *, days: int = 7,
) -> dict:
    """Compute innings_last_48h / 3d / 7d + bullpen_era_7d from gameLogs."""
    if not team_id:
        return {}
    key = f"bullpen_usage:{team_id}:{days}"
    cached = await _cache_get(db, key)
    if cached:
        return cached

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days)
    url = (
        f"{_BASE}/teams/{team_id}/stats"
        f"?stats=gameLog&group=pitching&startDate={start.isoformat()}"
        f"&endDate={today.isoformat()}&sportIds=1"
    )
    data = await _safe_get_json(url)
    if not data:
        return {}
    splits = (data.get("stats") or [{}])[0].get("splits") or []

    bp_innings_48h = 0.0
    bp_innings_3d  = 0.0
    bp_innings_7d  = 0.0
    bp_runs_7d     = 0
    games_in_7d    = 0
    runs_allowed_last_5 = 0
    games_seen = 0

    for s in sorted(splits, key=lambda x: x.get("date") or "", reverse=True):
        date_str = s.get("date")
        if not date_str:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            continue
        days_ago = (today - d).days
        stat = s.get("stat") or {}
        # In gameLog pitching, `inningsPitched` is the team total — we
        # approximate bullpen IP as max(0, team IP - 5.0). A typical
        # starter goes ~5 IP; everything else is relief.
        ip_str = stat.get("inningsPitched") or "0"
        try:
            team_ip = float(str(ip_str).replace(".1", ".33").replace(".2", ".66"))
        except Exception:
            team_ip = 0.0
        relief_ip = max(0.0, team_ip - 5.0)
        runs      = int(stat.get("runs") or 0)
        games_seen += 1
        if days_ago <= 2:
            bp_innings_48h += relief_ip
        if days_ago <= 3:
            bp_innings_3d  += relief_ip
        if days_ago <= 7:
            bp_innings_7d  += relief_ip
            bp_runs_7d     += runs
            games_in_7d    += 1
        if games_seen <= 5:
            runs_allowed_last_5 += runs

    bp_era_7d = (
        (bp_runs_7d * 9.0) / bp_innings_7d if bp_innings_7d > 0 else 4.00
    )
    out = {
        "innings_last_48h":      round(bp_innings_48h, 1),
        "innings_last_3d":       round(bp_innings_3d, 1),
        "innings_last_7d":       round(bp_innings_7d, 1),
        "bullpen_era_7d":        round(bp_era_7d, 2),
        "runs_allowed_last_5g":  runs_allowed_last_5,
        "games_in_7d":           games_in_7d,
        "_source_url":           url,
    }
    await _cache_put(db, key, out)
    return out


__all__ = ["get_team_hand_splits", "get_team_bullpen_usage"]
