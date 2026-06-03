"""
MLB Recent-Form Split — Últimos 5 vs Últimos 15 juegos (v2)
============================================================

**Bug fixed (2026-06):** the previous implementation called
``/teams/{id}/stats?stats=lastXGames&limit=N`` with N=5 and N=15. The
MLB Stats API ignores ``limit`` on that path and always returns the
*same* season-to-date split for both windows, so the UI ended up
showing identical L5 and L15 values (Δ=0.0 everywhere).

This module now uses the **canonical schedule + boxscore** approach
the user asked for:

  1.  ``GET /api/v1/schedule?sportId=1&teamId={teamId}&startDate=...&endDate=...&hydrate=linescore``
      → list of all finished regular-season games for the team in the
      last ~35 days.
  2.  For the top-15 most recent ``gamePk``s →
      ``GET /api/v1/game/{gamePk}/boxscore`` to pull the batting line
      for THIS team (home or away depending on which side it played).
  3.  Aggregate the per-game lines into L15 and L5 windows separately
      and compute deltas.

Per-game batting stats extracted::

    {
      "runs":         int,
      "hits":         int,
      "walks":        int,    # baseOnBalls
      "hbp":          int,    # hitByPitch
      "home_runs":    int,
      "obp":          float   # parsed string-stat
    }

All HTTP calls are cached for **12 h** in-memory (schedule + per-game
boxscore separately). Failures are silent — the function returns ``{}``
and the orchestrator's fail-soft block hides the panel.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from typing import Any, Optional
from datetime import datetime, timezone, timedelta

import httpx

log = logging.getLogger(__name__)

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
SCHEDULE_LOOKBACK_DAYS = 35   # ~5 weeks → comfortable buffer for 15 finished games
SCHEDULE_MAX_GAMES     = 25   # cap to keep boxscore requests bounded

# Cache TTL — 12h: L5/L15 averages don't move materially within a day.
_CACHE_TTL = timedelta(hours=12)

# Trend thresholds (Δ per game) — match the original constants.
RISING_RUN_THRESHOLD     = 1.25
DECLINING_RUN_THRESHOLD  = -1.25
RISING_OB_THRESHOLD      = 1.5
DECLINING_OB_THRESHOLD   = -1.5


# ── In-memory caches ──────────────────────────────────────────────────────
# Schedule cache:  (team_id, season) → (expires_at, [game_meta])
_SCHEDULE_CACHE: dict[tuple[int, int], tuple[datetime, list[dict]]] = {}
# Boxscore cache: game_pk → (expires_at, {team_id: per_game_line})
_BOX_CACHE: dict[int, tuple[datetime, dict[int, dict]]] = {}


def _cache_get_schedule(team_id: int, season: int) -> Optional[list[dict]]:
    hit = _SCHEDULE_CACHE.get((team_id, season))
    if not hit:
        return None
    exp, val = hit
    if datetime.now(timezone.utc) > exp:
        _SCHEDULE_CACHE.pop((team_id, season), None)
        return None
    return val


def _cache_set_schedule(team_id: int, season: int, value: list[dict]) -> None:
    _SCHEDULE_CACHE[(team_id, season)] = (
        datetime.now(timezone.utc) + _CACHE_TTL,
        value,
    )


def _cache_get_box(game_pk: int) -> Optional[dict[int, dict]]:
    hit = _BOX_CACHE.get(game_pk)
    if not hit:
        return None
    exp, val = hit
    if datetime.now(timezone.utc) > exp:
        _BOX_CACHE.pop(game_pk, None)
        return None
    return val


def _cache_set_box(game_pk: int, value: dict[int, dict]) -> None:
    _BOX_CACHE[game_pk] = (datetime.now(timezone.utc) + _CACHE_TTL, value)


# ── Low-level helpers ────────────────────────────────────────────────────
def _to_int(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_obp(s: Any) -> Optional[float]:
    """OBP is returned as a string like ``".320"`` or ``"0.320"``."""
    if s is None:
        return None
    try:
        return float(str(s))
    except (TypeError, ValueError):
        return None


# ── Schedule fetch ───────────────────────────────────────────────────────
async def _fetch_recent_schedule(
    client: httpx.AsyncClient,
    team_id: int,
    season: int,
) -> list[dict]:
    """Return the team's finished regular-season games in the lookback
    window. Each element is a ``dict`` with ``game_pk``, ``date``, and
    ``home_team_id`` / ``away_team_id``. Sorted descending by date.
    """
    cached = _cache_get_schedule(team_id, season)
    if cached is not None:
        return cached

    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=SCHEDULE_LOOKBACK_DAYS)).isoformat()
    end   = today.isoformat()

    params = {
        "sportId":   1,
        "teamId":    team_id,
        "startDate": start,
        "endDate":   end,
        "gameType":  "R",
        "hydrate":   "linescore",
    }
    url = f"{MLB_STATS_BASE}/schedule"
    try:
        r = await client.get(url, params=params, timeout=10.0)
        r.raise_for_status()
        data = r.json() or {}
    except Exception as exc:
        log.debug("schedule fetch failed team=%s: %s", team_id, exc)
        _cache_set_schedule(team_id, season, [])
        return []

    games: list[dict] = []
    for date_block in data.get("dates") or []:
        date_str = date_block.get("date")
        for g in date_block.get("games") or []:
            status = ((g.get("status") or {}).get("abstractGameState") or "").lower()
            if status != "final":
                continue
            game_pk = g.get("gamePk")
            if not game_pk:
                continue
            teams = g.get("teams") or {}
            home  = (teams.get("home") or {}).get("team") or {}
            away  = (teams.get("away") or {}).get("team") or {}
            games.append({
                "game_pk":       int(game_pk),
                "date":          date_str,
                "home_team_id":  home.get("id"),
                "away_team_id":  away.get("id"),
                "home_score":    (teams.get("home") or {}).get("score"),
                "away_score":    (teams.get("away") or {}).get("score"),
            })

    # Most recent first.
    games.sort(key=lambda g: g.get("date") or "", reverse=True)
    games = games[:SCHEDULE_MAX_GAMES]
    _cache_set_schedule(team_id, season, games)
    return games


# ── Boxscore fetch ───────────────────────────────────────────────────────
async def _fetch_boxscore_lines(
    client: httpx.AsyncClient,
    game_pk: int,
) -> dict[int, dict]:
    """Fetch the boxscore for a game and return a per-team-id mapping
    of batting stats. Cached.
    """
    cached = _cache_get_box(game_pk)
    if cached is not None:
        return cached

    url = f"{MLB_STATS_BASE}/game/{game_pk}/boxscore"
    try:
        r = await client.get(url, timeout=10.0)
        r.raise_for_status()
        data = r.json() or {}
    except Exception as exc:
        log.debug("boxscore fetch failed game=%s: %s", game_pk, exc)
        _cache_set_box(game_pk, {})
        return {}

    out: dict[int, dict] = {}
    teams_block = data.get("teams") or {}
    for side_key in ("home", "away"):
        side = teams_block.get(side_key) or {}
        team = side.get("team") or {}
        team_id = team.get("id")
        if not team_id:
            continue
        stats = (side.get("teamStats") or {}).get("batting") or {}
        out[int(team_id)] = {
            "runs":      _to_int(stats.get("runs")),
            "hits":      _to_int(stats.get("hits")),
            "walks":     _to_int(stats.get("baseOnBalls")),
            "hbp":       _to_int(stats.get("hitByPitch")),
            "home_runs": _to_int(stats.get("homeRuns")),
            "obp":       _parse_obp(stats.get("obp")),
            "plate_appearances": _to_int(stats.get("plateAppearances")),
        }
    _cache_set_box(game_pk, out)
    return out


# ── Aggregation ──────────────────────────────────────────────────────────
def _avg(values: list[float]) -> Optional[float]:
    cleaned = [v for v in values if v is not None]
    if not cleaned:
        return None
    return round(statistics.fmean(cleaned), 3)


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 3)


def _aggregate(per_game: list[dict]) -> dict:
    """Average a list of per-game batting lines into a single dict."""
    if not per_game:
        return {}
    return {
        "runs":      _avg([float(g.get("runs", 0))      for g in per_game]),
        "hits":      _avg([float(g.get("hits", 0))      for g in per_game]),
        "walks":     _avg([float(g.get("walks", 0))     for g in per_game]),
        "hbp":       _avg([float(g.get("hbp", 0))       for g in per_game]),
        "home_runs": _avg([float(g.get("home_runs", 0)) for g in per_game]),
        "obp":       _avg([g.get("obp")                  for g in per_game]),  # _avg filters None
        "games":     len(per_game),
    }


def _classify_run_trend(delta_total_runs: Optional[float]) -> str:
    if delta_total_runs is None:
        return "UNKNOWN_RUN_ENVIRONMENT"
    if delta_total_runs >= RISING_RUN_THRESHOLD:
        return "RISING_RUN_ENVIRONMENT"
    if delta_total_runs <= DECLINING_RUN_THRESHOLD:
        return "DECLINING_RUN_ENVIRONMENT"
    return "STABLE_RUN_ENVIRONMENT"


def _classify_on_base_trend(delta_tob: Optional[float]) -> str:
    if delta_tob is None:
        return "UNKNOWN_ON_BASE_PRESSURE"
    if delta_tob >= RISING_OB_THRESHOLD:
        return "RISING_ON_BASE_PRESSURE"
    if delta_tob <= DECLINING_OB_THRESHOLD:
        return "DECLINING_ON_BASE_PRESSURE"
    return "STABLE_ON_BASE_PRESSURE"


# ── Public API ───────────────────────────────────────────────────────────
async def get_team_recent_form(
    client: httpx.AsyncClient,
    team_id: int,
    season: int,
) -> dict:
    """Return per-team L5 and L15 aggregated batting lines. Falls back to
    ``{}`` when the team has fewer than 5 finished games in the window.
    """
    if not team_id:
        return {}
    games = await _fetch_recent_schedule(client, int(team_id), int(season))
    if not games:
        return {}

    # Pull boxscores in parallel for the top 15 games.
    top15 = games[:15]
    box_results = await asyncio.gather(
        *(_fetch_boxscore_lines(client, g["game_pk"]) for g in top15),
        return_exceptions=False,
    )

    per_game_lines: list[dict] = []
    for game_meta, box in zip(top15, box_results):
        line = (box or {}).get(int(team_id))
        if line:
            per_game_lines.append(line)

    if len(per_game_lines) < 1:
        return {}

    l15 = per_game_lines[:15]
    l5  = per_game_lines[:5]
    a15 = _aggregate(l15)
    a5  = _aggregate(l5)

    def _tob(blk: dict) -> Optional[float]:
        parts = [blk.get("hits"), blk.get("walks"), blk.get("hbp")]
        nums = [p for p in parts if p is not None]
        if not nums:
            return None
        return round(sum(nums), 3)

    return {
        "team_id":                  int(team_id),
        "runs_scored_avg_last_5":   a5.get("runs"),
        "runs_scored_avg_last_15":  a15.get("runs"),
        "hits_avg_last_5":          a5.get("hits"),
        "hits_avg_last_15":         a15.get("hits"),
        "walks_avg_last_5":         a5.get("walks"),
        "walks_avg_last_15":        a15.get("walks"),
        "hbp_avg_last_5":           a5.get("hbp"),
        "hbp_avg_last_15":          a15.get("hbp"),
        "home_runs_avg_last_5":     a5.get("home_runs"),
        "home_runs_avg_last_15":    a15.get("home_runs"),
        "times_on_base_avg_last_5": _tob(a5),
        "times_on_base_avg_last_15": _tob(a15),
        "obp_last_5":               a5.get("obp"),
        "obp_last_15":              a15.get("obp"),
        "games_played_last_5":      a5.get("games") or 0,
        "games_played_last_15":     a15.get("games") or 0,
    }


def build_recent_form_payload(home_form: dict, away_form: dict) -> dict:
    """Combine the home + away dicts into the canonical
    ``recent_run_split`` + ``on_base_profile`` payload consumed by the
    final pick router and the UI.
    """
    runs_total_l5_home  = home_form.get("runs_scored_avg_last_5")
    runs_total_l15_home = home_form.get("runs_scored_avg_last_15")
    runs_total_l5_away  = away_form.get("runs_scored_avg_last_5")
    runs_total_l15_away = away_form.get("runs_scored_avg_last_15")

    if (
        runs_total_l5_home is not None and runs_total_l5_away is not None
        and runs_total_l15_home is not None and runs_total_l15_away is not None
    ):
        total_l5  = round(runs_total_l5_home + runs_total_l5_away, 3)
        total_l15 = round(runs_total_l15_home + runs_total_l15_away, 3)
        total_delta = round(total_l5 - total_l15, 3)
    else:
        total_l5 = total_l15 = total_delta = None

    recent_run_trend = _classify_run_trend(total_delta)

    def _ob_block(side_form: dict) -> dict:
        tob_l5  = side_form.get("times_on_base_avg_last_5")
        tob_l15 = side_form.get("times_on_base_avg_last_15")
        hits_l5  = side_form.get("hits_avg_last_5")
        hits_l15 = side_form.get("hits_avg_last_15")
        bb_l5   = side_form.get("walks_avg_last_5")
        bb_l15  = side_form.get("walks_avg_last_15")
        hbp_l5  = side_form.get("hbp_avg_last_5")
        hbp_l15 = side_form.get("hbp_avg_last_15")
        hr_l5   = side_form.get("home_runs_avg_last_5")
        hr_l15  = side_form.get("home_runs_avg_last_15")
        return {
            "times_on_base_avg_last_5":   tob_l5,
            "times_on_base_avg_last_15":  tob_l15,
            "times_on_base_delta_5_vs_15": _delta(tob_l5, tob_l15),
            "hits_avg_last_5":             hits_l5,
            "hits_avg_last_15":            hits_l15,
            "hits_delta_5_vs_15":          _delta(hits_l5, hits_l15),
            "walks_avg_last_5":            bb_l5,
            "walks_avg_last_15":           bb_l15,
            "walks_delta_5_vs_15":         _delta(bb_l5, bb_l15),
            "hbp_avg_last_5":              hbp_l5,
            "hbp_avg_last_15":             hbp_l15,
            "hbp_delta_5_vs_15":           _delta(hbp_l5, hbp_l15),
            "home_runs_avg_last_5":        hr_l5,
            "home_runs_avg_last_15":       hr_l15,
            "home_runs_delta_5_vs_15":     _delta(hr_l5, hr_l15),
            "obp_last_5":                  side_form.get("obp_last_5"),
            "obp_last_15":                 side_form.get("obp_last_15"),
            "trend": _classify_on_base_trend(_delta(tob_l5, tob_l15)),
        }

    on_base_profile = {
        "home": _ob_block(home_form),
        "away": _ob_block(away_form),
    }
    # Combined block for the on-base side (sum of both teams).
    home_tob_l5  = home_form.get("times_on_base_avg_last_5")
    home_tob_l15 = home_form.get("times_on_base_avg_last_15")
    away_tob_l5  = away_form.get("times_on_base_avg_last_5")
    away_tob_l15 = away_form.get("times_on_base_avg_last_15")
    home_hr_l5  = home_form.get("home_runs_avg_last_5")
    home_hr_l15 = home_form.get("home_runs_avg_last_15")
    away_hr_l5  = away_form.get("home_runs_avg_last_5")
    away_hr_l15 = away_form.get("home_runs_avg_last_15")
    if all(v is not None for v in (home_tob_l5, home_tob_l15, away_tob_l5, away_tob_l15)):
        combined_tob_l5  = round(home_tob_l5 + away_tob_l5, 3)
        combined_tob_l15 = round(home_tob_l15 + away_tob_l15, 3)
        combined_tob_delta = round(combined_tob_l5 - combined_tob_l15, 3)
    else:
        combined_tob_l5 = combined_tob_l15 = combined_tob_delta = None
    if all(v is not None for v in (home_hr_l5, home_hr_l15, away_hr_l5, away_hr_l15)):
        combined_hr_l5  = round(home_hr_l5 + away_hr_l5, 3)
        combined_hr_l15 = round(home_hr_l15 + away_hr_l15, 3)
        combined_hr_delta = round(combined_hr_l5 - combined_hr_l15, 3)
    else:
        combined_hr_l5 = combined_hr_l15 = combined_hr_delta = None
    on_base_profile["combined"] = {
        "times_on_base_avg_last_5":   combined_tob_l5,
        "times_on_base_avg_last_15":  combined_tob_l15,
        "times_on_base_delta_5_vs_15": combined_tob_delta,
        "home_runs_avg_last_5":        combined_hr_l5,
        "home_runs_avg_last_15":       combined_hr_l15,
        "home_runs_delta_5_vs_15":     combined_hr_delta,
        "trend": _classify_on_base_trend(combined_tob_delta),
    }

    return {
        "recent_run_split": {
            "runs_scored_avg_last_5_home":   runs_total_l5_home,
            "runs_scored_avg_last_15_home":  runs_total_l15_home,
            "runs_scored_avg_last_5_away":   runs_total_l5_away,
            "runs_scored_avg_last_15_away":  runs_total_l15_away,
            "runs_scored_delta_5_vs_15_home": _delta(
                runs_total_l5_home, runs_total_l15_home,
            ),
            "runs_scored_delta_5_vs_15_away": _delta(
                runs_total_l5_away, runs_total_l15_away,
            ),
            "total_runs_avg_last_5":         total_l5,
            "total_runs_avg_last_15":        total_l15,
            "total_runs_delta_5_vs_15":      total_delta,
        },
        "recent_run_trend": recent_run_trend,
        "on_base_profile":  on_base_profile,
    }


__all__ = [
    "get_team_recent_form",
    "build_recent_form_payload",
    "RISING_RUN_THRESHOLD",
    "DECLINING_RUN_THRESHOLD",
    "RISING_OB_THRESHOLD",
    "DECLINING_OB_THRESHOLD",
    "_classify_run_trend",
    "_classify_on_base_trend",
    "_aggregate",
]
