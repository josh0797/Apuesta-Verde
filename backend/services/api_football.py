"""API-Football v3 async client with strict rate limiting + Mongo cache.

Free plan = 10 requests per minute (very low). We:
  - Limit to ~8 req/min via a sliding-window token bucket.
  - Cache team_statistics / standings / h2h / injuries in Mongo (6h TTL).
  - Cache odds in Mongo (30 min TTL).
"""
from __future__ import annotations

import asyncio
import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger("api_football")

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

PROXY_SEASON = 2024  # Free plan limit

# Cache TTLs
ODDS_TTL_MIN = 30
CONTEXT_TTL_HOURS = 6

# Global rate limiter ~8 req/min (safe under 10 req/min hard cap)
class _RateLimiter:
    def __init__(self, max_calls: int = 8, period_sec: int = 60):
        self.max_calls = max_calls
        self.period = period_sec
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # drop calls outside window
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


class APIFootballError(Exception):
    pass


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    if not API_KEY:
        raise APIFootballError("API_FOOTBALL_KEY not configured")
    await _LIMITER.acquire()
    try:
        r = await client.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise APIFootballError(f"{path}: {exc}") from exc
    errs = data.get("errors")
    if errs and isinstance(errs, dict) and errs:
        # rateLimit error → wait and retry once
        if "rateLimit" in errs:
            log.warning("Hit hard rate limit, sleeping 60s and retrying %s", path)
            await asyncio.sleep(60)
            await _LIMITER.acquire()
            r = await client.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            errs2 = data.get("errors")
            if errs2 and isinstance(errs2, dict) and errs2:
                log.warning("API-Football errors after retry %s: %s", path, errs2)
        else:
            log.warning("API-Football errors for %s: %s", path, errs)
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


async def _cache_get(db, collection: str, key: dict, ttl_minutes: int) -> dict | None:
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


# ── Public endpoints (with optional db cache) ────────────────────────────────
async def fixtures_by_date(client: httpx.AsyncClient, date_iso: str) -> list[dict]:
    data = await _get(client, "/fixtures", {"date": date_iso})
    return data.get("response", []) or []


async def fixtures_next_48h(client: httpx.AsyncClient) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    out: list[dict] = []
    for d in (today, tomorrow):
        out.extend(await fixtures_by_date(client, d.isoformat()))
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=48)
    res = []
    for f in out:
        try:
            ts = f["fixture"]["timestamp"]
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            status = f["fixture"]["status"]["short"]
            if status in ("NS", "TBD") and now - timedelta(minutes=10) <= dt <= cutoff:
                res.append(f)
        except Exception:
            pass
    return res


async def fixtures_live(client: httpx.AsyncClient) -> list[dict]:
    data = await _get(client, "/fixtures", {"live": "all"})
    return data.get("response", []) or []


async def fixtures_by_league_window(
    client: httpx.AsyncClient,
    league_id: int,
    season: int,
    *,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """Fetch fixtures for a single league_id over a date window.

    Used by `discover_priority_fixtures` to surgically ask API-Sports for
    just the Tier 1/2 leagues that matter, instead of pulling the global
    /fixtures?date=… firehose (which is what historically caused
    Côte d'Ivoire U17 / Botswana / Belarus to flood the candidate list).
    """
    data = await _get(client, "/fixtures", {
        "league": league_id,
        "season": season,
        "from": from_date,
        "to": to_date,
    })
    return data.get("response", []) or []


async def fixture_by_id(client: httpx.AsyncClient, fixture_id: int) -> dict | None:
    data = await _get(client, "/fixtures", {"id": fixture_id})
    resp = data.get("response", []) or []
    return resp[0] if resp else None


async def odds_for_fixture(client: httpx.AsyncClient, fixture_id: int, db=None) -> list[dict]:
    key = {"fixture_id": fixture_id}
    cached = await _cache_get(db, "cache_odds", key, ODDS_TTL_MIN)
    if cached is not None:
        return cached
    data = await _get(client, "/odds", {"fixture": fixture_id})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_odds", key, resp)
    return resp


async def team_statistics(client: httpx.AsyncClient, team_id: int, league_id: int, season: int = PROXY_SEASON, db=None) -> dict:
    key = {"team_id": team_id, "league_id": league_id, "season": season}
    cached = await _cache_get(db, "cache_team_stats", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    data = await _get(client, "/teams/statistics", {"team": team_id, "league": league_id, "season": season})
    resp = data.get("response") or {}
    await _cache_set(db, "cache_team_stats", key, resp)
    return resp


async def standings(client: httpx.AsyncClient, league_id: int, season: int = PROXY_SEASON, db=None) -> list[dict]:
    key = {"league_id": league_id, "season": season}
    cached = await _cache_get(db, "cache_standings", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    data = await _get(client, "/standings", {"league": league_id, "season": season})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_standings", key, resp)
    return resp


async def head_to_head(client: httpx.AsyncClient, home_id: int, away_id: int, limit: int = 5, db=None) -> list[dict]:
    key = {"h2h_key": f"{home_id}-{away_id}"}
    cached = await _cache_get(db, "cache_h2h", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached[:limit]
    data = await _get(client, "/fixtures/headtohead", {"h2h": f"{home_id}-{away_id}"})
    items = data.get("response", []) or []
    items.sort(key=lambda f: f.get("fixture", {}).get("timestamp", 0), reverse=True)
    await _cache_set(db, "cache_h2h", key, items)
    return items[:limit]


async def injuries(client: httpx.AsyncClient, team_id: int, season: int = PROXY_SEASON, db=None) -> list[dict]:
    key = {"team_id": team_id, "season": season}
    cached = await _cache_get(db, "cache_injuries", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    data = await _get(client, "/injuries", {"team": team_id, "season": season})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_injuries", key, resp)
    return resp


async def fixture_statistics(client: httpx.AsyncClient, fixture_id: int, db=None) -> list[dict]:
    """Per-team statistics for a finished fixture.

    Includes Corner Kicks, Shots, Possession, Cards, etc. Cached aggressively
    (7 days) because stats of a finished match never change.
    """
    key = {"fixture_id": int(fixture_id)}
    cached = await _cache_get(db, "cache_fixture_stats", key, 7 * 24 * 60)
    if cached is not None:
        return cached
    data = await _get(client, "/fixtures/statistics", {"fixture": fixture_id})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_fixture_stats", key, resp)
    return resp


def _corners_from_fixture_stats(stats_resp: list[dict], team_id: int) -> tuple[int, int] | None:
    """Extract (corners_for, corners_against) from a /fixtures/statistics response.

    Returns None if the corner statistic is missing for either team.
    Stats response shape:
      [{"team":{"id":X,"name":...}, "statistics":[{"type":"Corner Kicks","value":7}, ...]},
       {"team":{"id":Y,"name":...}, "statistics":[...]}]
    """
    if not stats_resp or not isinstance(stats_resp, list) or len(stats_resp) < 2:
        return None
    team_id = int(team_id)
    my_corners: Optional[int] = None
    opp_corners: Optional[int] = None
    for block in stats_resp:
        t_id = ((block.get("team") or {}).get("id"))
        stats_arr = block.get("statistics") or []
        cv = None
        for s in stats_arr:
            if (s.get("type") or "").strip().lower() in ("corner kicks", "corners"):
                v = s.get("value")
                if v is None:
                    continue
                try:
                    cv = int(v)
                except (TypeError, ValueError):
                    try:
                        cv = int(float(v))
                    except (TypeError, ValueError):
                        cv = None
                break
        if cv is None:
            continue
        if t_id == team_id:
            my_corners = cv
        else:
            opp_corners = cv
    if my_corners is None or opp_corners is None:
        return None
    return (my_corners, opp_corners)


async def team_corner_form(
    client: httpx.AsyncClient,
    team_id: int,
    *,
    n: int = 5,
    season: int = PROXY_SEASON,
    db=None,
) -> dict:
    """Corner kicks form over the team's last N completed fixtures.

    Cached by `(team_id, season)` for 12h (corners only change after each new
    finished match). Internal per-fixture stats are cached 7 days.

    Returns:
        {
          "team_id":         int,
          "sample_size":     int,        # actual fixtures with corner data
          "avg_for":         float,      # corners scored per game
          "avg_against":     float,      # corners conceded per game
          "avg_total":       float,      # avg_for + avg_against
          "per_match":       list[dict], # [{fixture_id, date, corners_for, corners_against}]
          "missing_data":    bool,       # True if sample_size < 3
        }

    Returns an empty-form dict (sample_size=0) when no data is available.
    """
    n = max(1, min(int(n or 5), 10))
    key = {"team_id": int(team_id), "season": season, "n": n, "kind": "corner_form"}
    cached = await _cache_get(db, "cache_team_corner_form", key, 12 * 60)
    if cached is not None:
        return cached

    fixtures = await fixtures_last_n(client, team_id, n=n, season=season, db=db)

    per_match: list[dict] = []
    for fx in (fixtures or [])[:n]:
        fx_id = (fx.get("fixture") or {}).get("id")
        # Skip non-finished games (no corner stats available yet)
        short = ((fx.get("fixture") or {}).get("status") or {}).get("short")
        if short not in ("FT", "AET", "PEN"):
            continue
        if not fx_id:
            continue
        try:
            stats = await fixture_statistics(client, fx_id, db=db)
        except Exception:
            continue
        corners = _corners_from_fixture_stats(stats, team_id)
        if not corners:
            continue
        c_for, c_against = corners
        per_match.append({
            "fixture_id":      int(fx_id),
            "date":             (fx.get("fixture") or {}).get("date"),
            "corners_for":      c_for,
            "corners_against":  c_against,
        })

    sample = len(per_match)
    if sample == 0:
        result = {
            "team_id":      int(team_id),
            "sample_size":  0,
            "avg_for":      None,
            "avg_against":  None,
            "avg_total":    None,
            "per_match":    [],
            "missing_data": True,
        }
    else:
        avg_for     = sum(m["corners_for"]     for m in per_match) / sample
        avg_against = sum(m["corners_against"] for m in per_match) / sample
        result = {
            "team_id":      int(team_id),
            "sample_size":  sample,
            "avg_for":      round(avg_for,     2),
            "avg_against":  round(avg_against, 2),
            "avg_total":    round(avg_for + avg_against, 2),
            "per_match":    per_match,
            "missing_data": sample < 3,
        }
    await _cache_set(db, "cache_team_corner_form", key, result)
    return result


async def fixtures_last_n(
    client: httpx.AsyncClient,
    team_id: int,
    *,
    n: int = 10,
    season: int | None = PROXY_SEASON,
    db=None,
    include_all_competitions: bool = False,
) -> list[dict]:
    """Return the team's last N fixtures (most recent first) WITH final score.

    Used by `services/statsbomb_features.py` to build a goal-distribution
    profile per team — feeds the Poisson model that powers the Under 3.5 /
    Under 2.5 protected scan (Phase 9 + P2A enhancement). Also used by
    ``football_corners_history`` for the L1/L5/L15 corner window.

    Sprint-D9.2 Block A — Cross-tournament window
    ---------------------------------------------
    For national-team analysis (World Cup, Euros, Copa América) the
    legacy contract ``season=PROXY_SEASON`` over-filters: API-Sports
    files friendlies and qualifiers under different ``season`` values
    (league_id=10 "Friendlies International" lives in a season equal to
    the calendar year, not the tournament year). To compute a real
    L1/L5/L15 corner window for a national team you need ALL competitions
    glued together.

    Two new entry-points:

    * ``include_all_competitions=True`` — drops the ``season`` filter and
      asks API-Sports for "last N globally". This is the recommended
      mode for national-team windows. Cache key is versioned
      ``kind: "last_n_global"`` so it does not collide with the legacy
      cache.
    * ``season=None`` — same effect (drops the filter) but keeps the
      legacy cache key, primarily for ad-hoc scripts.

    Leagues stay on the original contract by default
    (``include_all_competitions=False``, ``season=PROXY_SEASON``) so we
    don't introduce a regression in club-football flows.
    """
    n = max(1, min(int(n or 10), 20))
    # Sprint-D9.2 Block A — cross-tournament fan-in for national teams.
    use_global = bool(include_all_competitions) or season is None
    kind = "last_n_global" if use_global else "last_n"
    key = {"team_id": team_id, "season": (None if use_global else season),
            "kind": kind}
    cached = await _cache_get(db, "cache_team_recent_fixtures", key, 12 * 60)
    if cached is not None:
        return cached[:n]
    params: dict[str, Any] = {"team": team_id, "last": n}
    if not use_global:
        params["season"] = season
    data = await _get(client, "/fixtures", params)
    resp = data.get("response", []) or []
    # API-Sports returns chronological; keep newest-first.
    resp.sort(key=lambda f: ((f.get("fixture") or {}).get("timestamp") or 0), reverse=True)
    await _cache_set(db, "cache_team_recent_fixtures", key, resp)
    return resp[:n]
