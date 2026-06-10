"""MLB Stats API client — statsapi.mlb.com (free, no key, no rate-limit publicado).

Provides the granular MLB data that API-Sports' baseball v1 endpoint doesn't
expose: probable pitchers, pitcher season splits (ERA/WHIP/K/BB/HR/IP), team
batting form, and bullpen usage in the last 3 days.

Design:
  • Best-effort: every call returns None on failure rather than raising.
  • Mongo-cached: payloads cache for 6h (pitcher) / 30m (probables, batting form).
  • Strict sport scope: this client is ONLY used when `sport == "baseball"`.
  • DATE BASIS — MLB organises its schedule by US Eastern Time. "Today's
    games" in UTC can already be tomorrow on the East Coast (or vice-versa
    at the wrap-around). We pin every "today" reference to
    `America/New_York` so the engine never asks StatsAPI for the wrong day.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("mlb_stats_api")

BASE = "https://statsapi.mlb.com/api/v1"
TIMEOUT = httpx.Timeout(10.0, read=15.0)

# MLB's official day-roll happens at midnight Eastern. Anchoring on
# America/New_York avoids the off-by-one regression where the server's
# UTC clock has already rolled to "tomorrow" but MLB is still on
# "today" (or vice-versa).
EASTERN = ZoneInfo("America/New_York")
DEFAULT_SEASON = datetime.now(EASTERN).year

# Cache TTLs (seconds)
TTL_SCHEDULE = 30 * 60
TTL_PITCHER_SEASON = 6 * 3600
TTL_TEAM_FORM = 30 * 60

# Tracks the *last* schedule cache result so the orchestrator can report
# it in pipeline_meta without having to thread a parameter through every
# caller. Values: "hit_valid" | "hit_invalid_refetched" | "miss" | "error".
LAST_SCHEDULE_CACHE_STATUS: ContextVar[str] = ContextVar(
    "mlb_schedule_cache_status", default="unknown",
)


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
    for the given YYYY-MM-DD (Eastern-time date).

    Cache validation
    ----------------
    Even though the cache key includes the date, a stale or malformed
    cache entry could surface games from a different date. We re-validate
    every cached payload by checking that each game's `gameDate` matches
    the requested `date_str` (in Eastern time). If the cache is invalid,
    we refetch from the network and set `LAST_SCHEDULE_CACHE_STATUS`
    accordingly so the orchestrator can surface it in `pipeline_meta`.
    """
    key = f"schedule:{date_str}"
    cached = await _cache_get(db, key)
    if cached is not None:
        games = cached.get("games", []) or []
        # MLB Stats API returns `gameDate` as a UTC ISO timestamp, but the
        # schedule API groups games by Eastern day. Validate by converting
        # each game's UTC timestamp to Eastern and comparing to date_str.
        def _matches_eastern_date(g: dict) -> bool:
            gd = g.get("gameDate") or ""
            if not gd:
                return False
            try:
                # MLB always returns Zulu time → convert via fromisoformat
                ts = datetime.fromisoformat(gd.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts.astimezone(EASTERN).strftime("%Y-%m-%d") == date_str
            except Exception:
                return False

        valid = [g for g in games if _matches_eastern_date(g)]
        if valid and len(valid) == len(games):
            log.info(
                "MLB schedule cache hit válido para %s: %d juegos",
                date_str, len(valid),
            )
            LAST_SCHEDULE_CACHE_STATUS.set("hit_valid")
            return valid
        if valid:
            # Partial validity — refetch so we don't leak stale entries.
            log.warning(
                "MLB schedule cache parcialmente inválido para %s "
                "(%d/%d juegos en la fecha correcta); refetching",
                date_str, len(valid), len(games),
            )
        else:
            log.warning(
                "MLB schedule cache inválido para %s: ningún juego pertenece a esa fecha; refetching",
                date_str,
            )
        LAST_SCHEDULE_CACHE_STATUS.set("hit_invalid_refetched")
    else:
        LAST_SCHEDULE_CACHE_STATUS.set("miss")

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
        LAST_SCHEDULE_CACHE_STATUS.set("error")
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


# ── Injured List (basic IL check) ────────────────────────────────────────────
async def get_team_il_players(db, team_id: int) -> list[dict]:
    """Return the list of players currently on a team's Injured List.

    Uses the public endpoint:
        /teams/{teamId}/roster?rosterType=injuries

    Returns a list of dicts like:
        [{ "name": str, "position": str, "status": str, "expected_return": str | None }]

    Never raises. On any failure (no team_id, HTTP error, parse error)
    returns ``[]`` so callers can safely treat IL data as optional.
    """
    if not team_id:
        return []

    today_iso = datetime.now(EASTERN).date().isoformat()
    key = f"il:{team_id}:{today_iso}"
    cached = await _cache_get(db, key)
    if cached is not None:
        return cached.get("players", [])

    url = f"{BASE}/teams/{team_id}/roster"
    params = {"rosterType": "injuries"}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.debug("IL fetch failed for team %s: %s", team_id, exc)
        return []

    players: list[dict] = []
    for p in (data.get("roster") or []):
        person   = p.get("person") or {}
        position = p.get("position") or {}
        status   = p.get("status") or {}
        players.append({
            "name":            person.get("fullName"),
            "position":        position.get("abbreviation"),
            "status":          status.get("description"),
            "expected_return": None,
            "_source_url":     f"{url}?rosterType=injuries&teamId={team_id}",
        })

    await _cache_put(db, key, {"players": players, "_source_url": url}, ttl_seconds=3600)
    return players


# ── Active offensive roster with hitting stats ───────────────────────────────
async def hydrate_team_offensive_roster(
    db,
    team_id: int,
    season: Optional[int] = None,
) -> dict:
    """Return the team's active roster with **per-player season hitting
    stats**, ready to feed into
    ``mlb_offensive_injury_impact.compute_offensive_injury_impact_for_team``.

    The MLB Stats API supports hydrating a roster endpoint with stats
    via:
        /teams/{teamId}/roster?rosterType=active&hydrate=person(stats(group=[hitting],type=[season],season=YYYY))

    Strategy:
      • Fetch the active roster + hydrated season hitting splits.
      • For each player we extract OPS, OBP, runs, RBI, HR, plate
        appearances, games_played, position abbreviation.
      • Pitchers stay in the payload (with PA=0) so the consumer can
        filter them out via its own ``_is_offensive_role`` rule.

    **Fail-soft contract — NEVER raises.**
      • ``db=None``     → cache layer is bypassed (warm fetch only).
      • cache read err  → ignored, continue with warm fetch.
      • cache write err → ignored, payload still returned.
      • API/parse err   → returns ``{"available": False, "reason": ...}``.

    Cached for 6h per (team_id, season) on the standard ``mlb_cache``.
    """
    if not team_id:
        return {"available": False, "reason": "no_team_id"}

    season = season or DEFAULT_SEASON
    key = f"off_roster:{team_id}:{season}"

    # ── 1) Cache read (fail-soft) ──────────────────────────────────
    if db is not None:
        try:
            cached = await _cache_get(db, key)
            if cached is not None:
                return cached
        except Exception as exc:
            log.debug("offensive roster cache_get failed for team %s: %s",
                      team_id, exc)

    # ── 2) Warm fetch from MLB Stats API ───────────────────────────
    hydrate = (
        f"person(stats(group=[hitting],type=[season],season={season}))"
    )
    url = f"{BASE}/teams/{team_id}/roster"
    params = {"rosterType": "active", "hydrate": hydrate}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.debug("offensive roster fetch failed for team %s: %s", team_id, exc)
        return {
            "available":   False,
            "reason":      "http_error",
            "error":       str(exc),
            "team_id":     team_id,
            "season":      season,
            "players":     [],
        }

    # ── 3) Parse roster (fail-soft per player) ─────────────────────
    def _to_float(v) -> Optional[float]:
        try:
            if v is None or v == "":
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    def _to_int(v) -> Optional[int]:
        try:
            if v is None or v == "":
                return None
            return int(v)
        except (TypeError, ValueError):
            return None

    players: list[dict] = []
    try:
        roster_list = (data or {}).get("roster") or []
    except Exception:
        roster_list = []

    for p in roster_list:
        try:
            person   = p.get("person") or {}
            position = p.get("position") or {}
            pos_abbr = (position.get("abbreviation") or "").upper()

            # Pull the most recent season hitting split (there can be 0 or 1).
            stats_blocks = person.get("stats") or []
            season_split: dict = {}
            for block in stats_blocks:
                if (block.get("group") or {}).get("displayName") != "hitting":
                    continue
                for sp in (block.get("splits") or []):
                    if (sp.get("season") or "").strip() == str(season):
                        season_split = sp.get("stat") or {}
                        break
                if season_split:
                    break

            players.append({
                "id":             person.get("id"),
                "name":           person.get("fullName"),
                "position":       pos_abbr,
                "ops":            _to_float(season_split.get("ops")),
                "obp":            _to_float(season_split.get("obp")),
                "slg":            _to_float(season_split.get("slg")),
                "avg":            _to_float(season_split.get("avg")),
                "runs":           _to_int(season_split.get("runs")),
                "rbi":            _to_int(season_split.get("rbi")),
                "hr":             _to_int(season_split.get("homeRuns")),
                "xbh":            (
                    (_to_int(season_split.get("doubles")) or 0)
                    + (_to_int(season_split.get("triples")) or 0)
                    + (_to_int(season_split.get("homeRuns")) or 0)
                ) or None,
                "pa":             _to_int(season_split.get("plateAppearances")),
                "games_played":   _to_int(season_split.get("gamesPlayed")),
            })
        except Exception as exc:
            log.debug("offensive roster parse failed for one player (team %s): %s",
                      team_id, exc)
            continue

    payload = {
        "available":   True,
        "team_id":     team_id,
        "season":      season,
        "players":     players,
        "_source_url": f"{url}?rosterType=active",
    }

    # ── 4) Cache write (fail-soft) ─────────────────────────────────
    if db is not None:
        try:
            await _cache_put(db, key, payload, ttl_seconds=6 * 3600)
        except Exception as exc:
            log.debug("offensive roster cache_put failed for team %s: %s",
                      team_id, exc)

    return payload


# ── Bullpen usage estimate (best-effort) ─────────────────────────────────────
async def get_bullpen_recent_usage(db, team_id: int, days: int = 3) -> Optional[dict]:
    """Estimate bullpen workload over the last `days`. Heuristic — uses team
    schedule + line scores to approximate IP thrown by relievers. Returns
    None if data not available."""
    if not team_id:
        return None
    today = datetime.now(EASTERN).date()
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
    # ── Pitch-stress augmentation (M2) ─────────────────────────────────
    # Best-effort: query the real box-score for pitch counts over the
    # last 48h. Falls back to 0 stress if MLB Stats returns nothing — in
    # which case `fatigue_score` is computed with the legacy formula.
    pitch_stress = 0.0
    bullpen_pitches_48h = 0
    bullpen_innings_48h = 0.0
    starter_lasted_innings: Optional[float] = None
    try:
        from .mlb_bullpen_real_usage import (
            fetch_recent_bullpen_workload, compute_fatigue_score, derive_fatigue_label,
        )
        workload = await fetch_recent_bullpen_workload(team_id, days=min(days, 2))
        if workload:
            pitch_stress = workload.get("pitch_stress_index") or 0.0
            bullpen_pitches_48h = workload.get("bullpen_pitches_48h") or 0
            bullpen_innings_48h = workload.get("bullpen_innings_48h") or 0.0
            starter_lasted_innings = workload.get("starter_lasted_innings")
            fatigue_score = compute_fatigue_score(
                games_played, extra_inning_games, pitch_stress,
            )
            fatigue_label = derive_fatigue_label(fatigue_score)
        else:
            fatigue_score = min(100, games_played * 25 + extra_inning_games * 15)
            fatigue_label = (
                "fresh" if fatigue_score < 30 else
                "moderate" if fatigue_score < 60 else
                "high" if fatigue_score < 85 else
                "extreme"
            )
    except Exception as exc:
        log.debug("bullpen pitch_stress aug failed for %s: %s", team_id, exc)
        fatigue_score = min(100, games_played * 25 + extra_inning_games * 15)
        fatigue_label = (
            "fresh" if fatigue_score < 30 else
            "moderate" if fatigue_score < 60 else
            "high" if fatigue_score < 85 else
            "extreme"
        )

    payload = {
        "team_id": team_id,
        "days": days,
        "games_played_recent": games_played,
        "extra_inning_games_recent": extra_inning_games,
        "fatigue_score_0_100": fatigue_score,
        "fatigue_label": fatigue_label,
        # ── M2 extension fields (optional, may be 0 when API not available) ──
        "pitch_stress_index":      pitch_stress,
        "bullpen_pitches_48h":     bullpen_pitches_48h,
        "bullpen_innings_48h":     bullpen_innings_48h,
        "starter_lasted_innings":  starter_lasted_innings,
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
    date_str = (kickoff_iso[:10]) or datetime.now(EASTERN).date().isoformat()

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
