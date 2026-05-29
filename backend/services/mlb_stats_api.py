"""MLB Stats API client — statsapi.mlb.com (free, no key, no rate-limit publicado).

Provides the granular MLB data that API-Sports' baseball v1 endpoint doesn't
expose: probable pitchers, pitcher season splits (ERA/WHIP/K/BB/HR/IP), team
batting form, and bullpen usage in the last 3 days.

Design:
  • Best-effort: every call returns None on failure rather than raising.
  • Mongo-cached: payloads cache for 6h (pitcher) / 30m (probables, batting form).
  • Strict sport scope: this client is ONLY used when `sport == "baseball"`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx

log = logging.getLogger("mlb_stats_api")

BASE = "https://statsapi.mlb.com/api/v1"
TIMEOUT = httpx.Timeout(10.0, read=15.0)
DEFAULT_SEASON = datetime.now(timezone.utc).year

# Cache TTLs (seconds)
TTL_SCHEDULE = 30 * 60
TTL_PITCHER_SEASON = 6 * 3600
TTL_TEAM_FORM = 30 * 60


# ── Cache helpers ────────────────────────────────────────────────────────────
async def _cache_get(db, key: str) -> Optional[dict]:
    doc = await db.mlb_cache.find_one({"_id": key})
    if not doc:
        return None
    if doc.get("expires_at", 0) < datetime.now(timezone.utc).timestamp():
        return None
    return doc.get("payload")


async def _cache_put(db, key: str, payload: dict, ttl_seconds: int) -> None:
    await db.mlb_cache.update_one(
        {"_id": key},
        {"$set": {
            "payload": payload,
            "expires_at": datetime.now(timezone.utc).timestamp() + ttl_seconds,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


# ── Schedule + probable pitchers ─────────────────────────────────────────────
async def get_schedule_with_probables(db, date_str: str) -> list[dict]:
    """Returns list of {gamePk, away, home, away_probable, home_probable, status, venue}
    for the given YYYY-MM-DD (UTC date)."""
    key = f"schedule:{date_str}"
    cached = await _cache_get(db, key)
    if cached is not None:
        return cached.get("games", [])

    url = f"{BASE}/schedule"
    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "probablePitcher,linescore,team,venue",
    }
    games: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("MLB schedule fetch failed for %s: %s", date_str, exc)
        return []

    for date_obj in data.get("dates", []):
        for g in date_obj.get("games", []):
            away = g.get("teams", {}).get("away", {}) or {}
            home = g.get("teams", {}).get("home", {}) or {}
            games.append({
                "gamePk": g.get("gamePk"),
                "gameDate": g.get("gameDate"),
                "status": (g.get("status") or {}).get("detailedState"),
                "abstractGameState": (g.get("status") or {}).get("abstractGameState"),
                "venue": (g.get("venue") or {}).get("name"),
                "away_team": (away.get("team") or {}).get("name"),
                "away_team_id": (away.get("team") or {}).get("id"),
                "home_team": (home.get("team") or {}).get("name"),
                "home_team_id": (home.get("team") or {}).get("id"),
                "away_probable_id": (away.get("probablePitcher") or {}).get("id"),
                "away_probable_name": (away.get("probablePitcher") or {}).get("fullName"),
                "home_probable_id": (home.get("probablePitcher") or {}).get("id"),
                "home_probable_name": (home.get("probablePitcher") or {}).get("fullName"),
                "linescore": g.get("linescore"),
            })
    await _cache_put(db, key, {"games": games}, TTL_SCHEDULE)
    return games


# ── Pitcher season stats ─────────────────────────────────────────────────────
async def get_pitcher_season_stats(db, pitcher_id: int, season: int = DEFAULT_SEASON) -> Optional[dict]:
    """Fetch ERA/WHIP/K/BB/HR/IP for a pitcher. Returns None if missing."""
    if not pitcher_id:
        return None
    key = f"pitcher:{pitcher_id}:{season}"
    cached = await _cache_get(db, key)
    if cached is not None:
        return cached

    url = f"{BASE}/people/{pitcher_id}/stats"
    params = {"stats": "season", "group": "pitching", "season": season}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("MLB pitcher stats fetch failed for id %s: %s", pitcher_id, exc)
        return None

    splits = (data.get("stats") or [{}])[0].get("splits") or []
    if not splits:
        # Fallback: try previous season
        if season == DEFAULT_SEASON:
            return await get_pitcher_season_stats(db, pitcher_id, season - 1)
        return None
    s = splits[0].get("stat") or {}
    try:
        ip = float(s.get("inningsPitched") or 0)
    except (TypeError, ValueError):
        ip = 0.0
    games = int(s.get("gamesPitched") or 0) or 1

    payload = {
        "pitcher_id": pitcher_id,
        "season": season,
        "era": _float(s.get("era")),
        "whip": _float(s.get("whip")),
        "strike_outs": _int(s.get("strikeOuts")),
        "base_on_balls": _int(s.get("baseOnBalls")),
        "home_runs": _int(s.get("homeRuns")),
        "innings_pitched": ip,
        "games_pitched": games,
        "ip_per_appearance": round(ip / games, 2) if games else None,
        "k_per_bb": round((s.get("strikeOuts") or 0) / max(1, (s.get("baseOnBalls") or 0)), 2)
                    if (s.get("baseOnBalls") or 0) > 0 else None,
        "hr_per_9": round(((s.get("homeRuns") or 0) * 9) / ip, 2) if ip else None,
        "hand": (s.get("pitchHand") or {}).get("code"),  # 'L' | 'R' | None
    }
    await _cache_put(db, key, payload, TTL_PITCHER_SEASON)
    return payload


async def get_pitcher_person(db, pitcher_id: int) -> Optional[dict]:
    """Returns {id, fullName, pitchHand} — used to know L/R when not in stats."""
    if not pitcher_id:
        return None
    key = f"person:{pitcher_id}"
    cached = await _cache_get(db, key)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{BASE}/people/{pitcher_id}")
            r.raise_for_status()
            people = r.json().get("people") or []
            if not people:
                return None
            p = people[0]
            payload = {
                "id": p.get("id"),
                "fullName": p.get("fullName"),
                "pitchHand": (p.get("pitchHand") or {}).get("code"),
                "batSide": (p.get("batSide") or {}).get("code"),
            }
            await _cache_put(db, key, payload, TTL_PITCHER_SEASON)
            return payload
    except Exception as exc:
        log.debug("MLB person fetch failed for id %s: %s", pitcher_id, exc)
        return None


# ── Team batting form (last N games) ─────────────────────────────────────────
async def get_team_batting_form(db, team_id: int, season: int = DEFAULT_SEASON) -> Optional[dict]:
    """Aggregate runs/G, hits/G, walk rate, K rate, OBP, SLG from recent games."""
    if not team_id:
        return None
    key = f"team-batting:{team_id}:{season}"
    cached = await _cache_get(db, key)
    if cached is not None:
        return cached

    url = f"{BASE}/teams/{team_id}/stats"
    params = {"stats": "season", "group": "hitting", "season": season}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.warning("MLB team batting fetch failed for %s: %s", team_id, exc)
        return None
    splits = (data.get("stats") or [{}])[0].get("splits") or []
    if not splits:
        if season == DEFAULT_SEASON:
            return await get_team_batting_form(db, team_id, season - 1)
        return None
    s = splits[0].get("stat") or {}
    games = int(s.get("gamesPlayed") or 0) or 1
    runs = int(s.get("runs") or 0)
    hits = int(s.get("hits") or 0)
    bb = int(s.get("baseOnBalls") or 0)
    so = int(s.get("strikeOuts") or 0)
    pa = int(s.get("plateAppearances") or 0) or 1

    payload = {
        "team_id": team_id,
        "season": season,
        "games_played": games,
        "runs_per_game": round(runs / games, 2),
        "hits_per_game": round(hits / games, 2),
        "walk_rate": round(bb / pa, 3),
        "strikeout_rate": round(so / pa, 3),
        "obp": _float(s.get("obp")),
        "slg": _float(s.get("slg")),
        "ops": _float(s.get("ops")),
        "avg": _float(s.get("avg")),
        "home_runs": int(s.get("homeRuns") or 0),
    }
    await _cache_put(db, key, payload, TTL_TEAM_FORM)
    return payload


# ── Bullpen usage estimate (best-effort) ─────────────────────────────────────
async def get_bullpen_recent_usage(db, team_id: int, days: int = 3) -> Optional[dict]:
    """Estimate bullpen workload over the last `days`. Heuristic — uses team
    schedule + line scores to approximate IP thrown by relievers. Returns
    None if data not available."""
    if not team_id:
        return None
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    key = f"bullpen:{team_id}:{start}:{end}"
    cached = await _cache_get(db, key)
    if cached is not None:
        return cached

    url = f"{BASE}/schedule"
    params = {
        "teamId": team_id,
        "startDate": start,
        "endDate": end,
        "sportId": 1,
        "hydrate": "linescore",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.debug("MLB bullpen fetch failed for %s: %s", team_id, exc)
        return None

    games_played = 0
    extra_inning_games = 0
    for d in data.get("dates", []):
        for g in d.get("games", []):
            ls = g.get("linescore") or {}
            innings = ls.get("innings") or []
            if not innings or (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            games_played += 1
            if len(innings) > 9:
                extra_inning_games += 1
    fatigue_score = min(100, games_played * 25 + extra_inning_games * 15)
    payload = {
        "team_id": team_id,
        "days": days,
        "games_played_recent": games_played,
        "extra_inning_games_recent": extra_inning_games,
        "fatigue_score_0_100": fatigue_score,
        "fatigue_label": (
            "fresh" if fatigue_score < 30 else
            "moderate" if fatigue_score < 60 else
            "high" if fatigue_score < 85 else
            "extreme"
        ),
    }
    await _cache_put(db, key, payload, TTL_TEAM_FORM)
    return payload


# ── Composite hydrator used by the analyst engine ────────────────────────────
async def hydrate_mlb_match_context(db, match_doc: dict) -> dict:
    """Enrich an ingested MLB match doc with probable pitcher stats, batting
    form and bullpen usage. Returns a dict ready to be passed to the LLM
    payload under `mlb_context`. Never raises — missing pieces are simply
    omitted."""
    home_name = (match_doc.get("home_team") or {}).get("name") or ""
    away_name = (match_doc.get("away_team") or {}).get("name") or ""
    kickoff_iso = match_doc.get("kickoff_iso") or ""
    date_str = (kickoff_iso[:10]) or datetime.now(timezone.utc).date().isoformat()

    schedule = await get_schedule_with_probables(db, date_str)
    if not schedule:
        return {"available": False, "reason": "schedule_empty"}

    # Resolve the game by fuzzy team-name matching
    norm = lambda s: (s or "").lower().replace(".", "").replace("’", "").strip()  # noqa: E731
    target = None
    for g in schedule:
        if (norm(g["home_team"]) in norm(home_name) or norm(home_name) in norm(g["home_team"])) \
                and (norm(g["away_team"]) in norm(away_name) or norm(away_name) in norm(g["away_team"])):
            target = g
            break
    if not target:
        return {"available": False, "reason": "no_match_in_schedule"}

    ctx: dict[str, Any] = {
        "available": True,
        "gamePk": target["gamePk"],
        "status": target["status"],
        "venue": target["venue"],
        "home_probable": target.get("home_probable_name"),
        "away_probable": target.get("away_probable_name"),
    }

    # Pitchers
    if target.get("home_probable_id"):
        ctx["home_pitcher"] = await get_pitcher_season_stats(db, target["home_probable_id"]) or {}
        if not ctx["home_pitcher"].get("hand"):
            person = await get_pitcher_person(db, target["home_probable_id"])
            if person:
                ctx["home_pitcher"]["hand"] = person.get("pitchHand")
    if target.get("away_probable_id"):
        ctx["away_pitcher"] = await get_pitcher_season_stats(db, target["away_probable_id"]) or {}
        if not ctx["away_pitcher"].get("hand"):
            person = await get_pitcher_person(db, target["away_probable_id"])
            if person:
                ctx["away_pitcher"]["hand"] = person.get("pitchHand")

    # Team batting form
    if target.get("home_team_id"):
        ctx["home_batting"] = await get_team_batting_form(db, target["home_team_id"])
    if target.get("away_team_id"):
        ctx["away_batting"] = await get_team_batting_form(db, target["away_team_id"])

    # Bullpen usage (last 3 days)
    if target.get("home_team_id"):
        ctx["home_bullpen"] = await get_bullpen_recent_usage(db, target["home_team_id"], days=3)
    if target.get("away_team_id"):
        ctx["away_bullpen"] = await get_bullpen_recent_usage(db, target["away_team_id"], days=3)

    return ctx


# ── Small utilities ──────────────────────────────────────────────────────────
def _float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
