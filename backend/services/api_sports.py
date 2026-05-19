"""Generic API-Sports client supporting football, basketball (NBA), baseball (MLB).

All three products share the same API key (same as football). Endpoints differ slightly.
We expose a small per-sport surface used by data_ingestion.
"""
from __future__ import annotations

import asyncio
import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger("api_sports")

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")

SPORT_CONFIG = {
    "football": {
        "base": "https://v3.football.api-sports.io",
        "season": 2024,
        "top_leagues": {39, 140, 135, 78, 61, 2, 3, 848, 88, 94, 71, 128, 253, 262, 13, 11, 144, 218, 197, 119, 207, 113, 103, 179},
    },
    "basketball": {
        "base": "https://v1.basketball.api-sports.io",
        "season": "2024-2025",
        "top_leagues": {12, 120, 117, 117, 194, 110},  # NBA(12), EuroLeague(120), Liga ACB(110), etc.
    },
    "baseball": {
        "base": "https://v1.baseball.api-sports.io",
        "season": 2024,
        "top_leagues": {1, 2, 3, 5},  # MLB(1), NPB(2), KBO(5), CPBL(3)
    },
}

# Sentence-friendly labels
SPORT_LABELS = {"football": "Fútbol", "basketball": "NBA / Basket", "baseball": "MLB / Béisbol"}

# Rate limiter shared across all sports (same API key quota)
class _RateLimiter:
    def __init__(self, max_calls: int = 8, period_sec: int = 60):
        self.max_calls = max_calls
        self.period = period_sec
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if now - t < self.period]
            if len(self._calls) >= self.max_calls:
                wait = self.period - (now - self._calls[0]) + 0.05
                if wait > 0:
                    log.info("Rate limit: sleeping %.2fs", wait)
                    await asyncio.sleep(wait)
                    now = time.monotonic()
                    self._calls = [t for t in self._calls if now - t < self.period]
            self._calls.append(time.monotonic())


_LIMITER = _RateLimiter(max_calls=8, period_sec=60)

ODDS_TTL_MIN = 30
CONTEXT_TTL_HOURS = 6


class APISportsError(Exception):
    pass


def get_base(sport: str) -> str:
    cfg = SPORT_CONFIG.get(sport)
    if not cfg:
        raise APISportsError(f"unknown sport {sport}")
    return cfg["base"]


def proxy_season(sport: str):
    return SPORT_CONFIG[sport]["season"]


def top_leagues(sport: str) -> set:
    return SPORT_CONFIG[sport]["top_leagues"]


async def _get(sport: str, client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    if not API_KEY:
        raise APISportsError("API_FOOTBALL_KEY not configured")
    await _LIMITER.acquire()
    url = f"{get_base(sport)}{path}"
    try:
        r = await client.get(url, headers={"x-apisports-key": API_KEY}, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise APISportsError(f"{path}: {exc}") from exc
    errs = data.get("errors")
    if errs and isinstance(errs, dict) and errs:
        if "rateLimit" in errs:
            log.warning("Hit hard rate limit, sleeping 60s and retrying %s", path)
            await asyncio.sleep(60)
            await _LIMITER.acquire()
            r = await client.get(url, headers={"x-apisports-key": API_KEY}, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        else:
            log.warning("API errors for %s [%s]: %s", path, sport, errs)
    return data


# ── Cache helpers ────────────────────────────────────────────────────────────
def _cache_fresh(doc: dict | None, ttl_minutes: int) -> bool:
    if not doc:
        return False
    ts = doc.get("_cached_at")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False
    return (datetime.now(timezone.utc) - cached) < timedelta(minutes=ttl_minutes)


async def _cache_get(db, collection: str, key: dict, ttl_minutes: int):
    if db is None:
        return None
    doc = await db[collection].find_one(key)
    if _cache_fresh(doc, ttl_minutes):
        return doc.get("data")
    return None


async def _cache_set(db, collection: str, key: dict, data: Any) -> None:
    if db is None:
        return
    doc = {**key, "data": data, "_cached_at": datetime.now(timezone.utc).isoformat()}
    await db[collection].update_one(key, {"$set": doc}, upsert=True)


# ── Generic endpoints (football | basketball | baseball) ─────────────────────
async def fixtures_by_date(sport: str, client: httpx.AsyncClient, date_iso: str) -> list[dict]:
    """Fixtures (or games) for a date. Football uses /fixtures, basketball/baseball use /games."""
    path = "/fixtures" if sport == "football" else "/games"
    data = await _get(sport, client, path, {"date": date_iso})
    return data.get("response", []) or []


async def fixtures_next_48h(sport: str, client: httpx.AsyncClient) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    out: list[dict] = []
    for d in (today, tomorrow):
        out.extend(await fixtures_by_date(sport, client, d.isoformat()))
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=48)
    res = []
    for f in out:
        try:
            if sport == "football":
                ts = f["fixture"]["timestamp"]
                status = f["fixture"]["status"]["short"]
                upcoming_statuses = ("NS", "TBD")
            else:
                ts = f.get("timestamp") or (f.get("fixture") or {}).get("timestamp")
                status_obj = f.get("status") or {}
                status = status_obj.get("short") if isinstance(status_obj, dict) else None
                upcoming_statuses = ("NS", "TBD", "SCHED", "AWAITING")
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if status in upcoming_statuses and now - timedelta(minutes=10) <= dt <= cutoff:
                res.append(f)
        except Exception:
            pass
    return res


async def fixtures_live(sport: str, client: httpx.AsyncClient) -> list[dict]:
    path = "/fixtures" if sport == "football" else "/games"
    data = await _get(sport, client, path, {"live": "all"})
    return data.get("response", []) or []


async def fixture_by_id(sport: str, client: httpx.AsyncClient, fixture_id: int) -> dict | None:
    path = "/fixtures" if sport == "football" else "/games"
    data = await _get(sport, client, path, {"id": fixture_id})
    resp = data.get("response", []) or []
    return resp[0] if resp else None


async def odds_for_fixture(sport: str, client: httpx.AsyncClient, fixture_id: int, db=None) -> list[dict]:
    key = {"sport": sport, "fixture_id": fixture_id}
    cached = await _cache_get(db, "cache_odds", key, ODDS_TTL_MIN)
    if cached is not None:
        return cached
    data = await _get(sport, client, "/odds", {"game" if sport != "football" else "fixture": fixture_id})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_odds", key, resp)
    return resp


async def standings(sport: str, client: httpx.AsyncClient, league_id: int, season=None, db=None) -> list[dict]:
    season = season or proxy_season(sport)
    key = {"sport": sport, "league_id": league_id, "season": season}
    cached = await _cache_get(db, "cache_standings", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    data = await _get(sport, client, "/standings", {"league": league_id, "season": season})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_standings", key, resp)
    return resp


async def team_statistics(sport: str, client: httpx.AsyncClient, team_id: int, league_id: int, season=None, db=None) -> dict:
    season = season or proxy_season(sport)
    key = {"sport": sport, "team_id": team_id, "league_id": league_id, "season": season}
    cached = await _cache_get(db, "cache_team_stats", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    # football has /teams/statistics; basketball/baseball use /teams/statistics too with same params
    data = await _get(sport, client, "/teams/statistics", {"team": team_id, "league": league_id, "season": season})
    resp = data.get("response") or {}
    await _cache_set(db, "cache_team_stats", key, resp)
    return resp


async def head_to_head(sport: str, client: httpx.AsyncClient, home_id: int, away_id: int, limit: int = 5, db=None) -> list[dict]:
    key = {"sport": sport, "h2h_key": f"{home_id}-{away_id}"}
    cached = await _cache_get(db, "cache_h2h", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached[:limit]
    if sport == "football":
        data = await _get(sport, client, "/fixtures/headtohead", {"h2h": f"{home_id}-{away_id}"})
    else:
        data = await _get(sport, client, "/games/h2h", {"h2h": f"{home_id}-{away_id}"})
    items = data.get("response", []) or []
    items.sort(key=lambda f: ((f.get("fixture") or {}).get("timestamp") or f.get("timestamp") or 0), reverse=True)
    await _cache_set(db, "cache_h2h", key, items)
    return items[:limit]
