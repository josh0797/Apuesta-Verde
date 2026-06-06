"""Basketball box-score provider — API-Sports primary + Balldontlie fallback.

The public ``fetch_basketball_team_games(team_id, last_n=...)`` returns a
list of per-game dicts matching the schema
``basketball_possession_layer.calculate_team_efficiency_profile`` consumes.

Both providers are tried in order. Fail-soft: returns ``[]`` on any
unrecoverable failure (no key, both providers down, etc).

Provider field maps (from integration playbook):

  API-Sports                       │ internal
  ─────────────────────────────────┼─────────────────────────────────
  field_goals.attempts/total       │ fga
  field_goals.made                 │ fgm
  threepoint_goals.attempts        │ three_pa
  threepoint_goals.made            │ three_pm
  freethrows_goals.attempts        │ fta
  freethrows_goals.made            │ ftm
  rebounds.offence / offensive     │ orb
  rebounds.defense / defensive     │ drb
  turnovers.total                  │ tov
  game.scores.{home,away}.total    │ pts_for / pts_against
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from .common import (
    API_SPORTS_BASKETBALL_BASE,
    API_SPORTS_HEADERS,
    API_SPORTS_KEY,
    BALLDONTLIE_BASE,
    BALLDONTLIE_KEY,
    DEFAULT_TIMEOUT_S,
    keys_lower,
    robust_get,
    safe_float,
    safe_int,
)

log = logging.getLogger("box_score_providers.basketball")


# ─────────────────────────────────────────────────────────────────────
# Normalizers
# ─────────────────────────────────────────────────────────────────────
def _stat_value(block: dict, *candidates: str) -> Optional[int]:
    """Read the first matching candidate key from a stats sub-block.

    API-Sports has slightly inconsistent naming across versions (e.g.
    ``attempts`` vs ``total``). Try several aliases before giving up.
    """
    bl = keys_lower(block or {})
    for c in candidates:
        v = bl.get(c.lower())
        if v is not None:
            return safe_int(v)
    return None


def normalize_api_sports_basketball(
    payload: dict,
    *,
    team_id: int | str | None,
) -> list[dict]:
    """Translate an API-Sports ``/games/statistics`` response into our schema.

    ``payload`` is the full JSON; ``team_id`` filters to a single team's
    rows. When ``team_id`` is None, returns ALL teams.
    """
    out: list[dict] = []
    if not isinstance(payload, dict):
        return out
    response = payload.get("response") or []
    if not isinstance(response, list):
        return out

    for item in response:
        if not isinstance(item, dict):
            continue
        # Each `item` is a per-team-per-game row.
        team = item.get("team") or {}
        tid = team.get("id")
        if team_id is not None and str(tid) != str(team_id):
            continue

        game = item.get("game") or {}
        date = (
            (game.get("date") or {}).get("date")
            if isinstance(game.get("date"), dict)
            else game.get("date")
        )
        scores = game.get("scores") or {}
        # Determine who is "self" vs "opp" within scores.
        is_home = (game.get("teams") or {}).get("home", {}).get("id") == tid \
                  if isinstance(game.get("teams"), dict) else None
        team_score = None
        opp_score  = None
        if isinstance(scores, dict):
            home_total = (scores.get("home") or {}).get("total") if isinstance(scores.get("home"), dict) else None
            away_total = (scores.get("away") or {}).get("total") if isinstance(scores.get("away"), dict) else None
            if is_home is True:
                team_score, opp_score = home_total, away_total
            elif is_home is False:
                team_score, opp_score = away_total, home_total

        fg   = item.get("field_goals") or {}
        tp   = item.get("threepoint_goals") or item.get("three_point_goals") or item.get("threes") or {}
        ft   = item.get("freethrows_goals") or item.get("free_throws") or item.get("freethrows") or {}
        rb   = item.get("rebounds") or {}
        tov  = item.get("turnovers") or {}

        fga = _stat_value(fg,  "attempts", "total")
        fgm = _stat_value(fg,  "made")
        three_pa = _stat_value(tp, "attempts", "total")
        three_pm = _stat_value(tp, "made")
        fta = _stat_value(ft,  "attempts", "total")
        ftm = _stat_value(ft,  "made")
        orb = _stat_value(rb,  "offence", "offensive", "off")
        drb = _stat_value(rb,  "defense", "defensive", "def")
        tov_val = _stat_value(tov, "total")

        out.append({
            "_provider":   "api_sports",
            "game_id":     game.get("id"),
            "date":        date,
            "team_id":     tid,
            "fga":         fga,
            "fgm":         fgm,
            "three_pa":    three_pa,
            "three_pm":    three_pm,
            "fta":         fta,
            "ftm":         ftm,
            "orb":         orb,
            "drb":         drb,
            "tov":         tov_val,
            "minutes":     48,
            "pts_for":     safe_int(team_score),
            "pts_against": safe_int(opp_score),
        })
    return out


def normalize_balldontlie(
    payload: dict,
    *,
    team_id: int | str | None,
) -> list[dict]:
    """Aggregate per-player Balldontlie ``/v1/stats`` rows to team totals.

    Balldontlie returns one row per player per game. We group by
    ``(game.id, team.id)`` and sum the counting stats.
    """
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return []

    grouped: dict[tuple, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        team = r.get("team") or {}
        tid = team.get("id")
        if team_id is not None and str(tid) != str(team_id):
            continue
        game = r.get("game") or {}
        gid  = game.get("id")
        key  = (gid, tid)
        bucket = grouped.setdefault(key, {
            "_provider":   "balldontlie",
            "game_id":     gid,
            "date":        game.get("date"),
            "team_id":     tid,
            "fga":   0, "fgm": 0, "three_pa": 0, "three_pm": 0,
            "fta":   0, "ftm": 0, "orb":      0, "drb":      0,
            "tov":   0, "minutes": 0,
            "pts_for": 0, "pts_against": 0,
        })
        bucket["fga"]      += safe_int(r.get("fga"))
        bucket["fgm"]      += safe_int(r.get("fgm"))
        bucket["three_pa"] += safe_int(r.get("fg3a"))
        bucket["three_pm"] += safe_int(r.get("fg3m"))
        bucket["fta"]      += safe_int(r.get("fta"))
        bucket["ftm"]      += safe_int(r.get("ftm"))
        bucket["orb"]      += safe_int(r.get("oreb"))
        bucket["drb"]      += safe_int(r.get("dreb"))
        bucket["tov"]      += safe_int(r.get("turnover"))
        # min field is a string like "32:14" → reduce to total minutes
        mn = r.get("min")
        if isinstance(mn, str) and ":" in mn:
            try:
                m, s = mn.split(":", 1)
                bucket["minutes"] += int(m)
            except (TypeError, ValueError):
                pass
        else:
            bucket["minutes"] += safe_int(mn)
        bucket["pts_for"] += safe_int(r.get("pts"))

    # Opponent score: Balldontlie game block carries home/visitor score.
    for r in rows:
        if not isinstance(r, dict):
            continue
        team = r.get("team") or {}
        tid = team.get("id")
        if team_id is not None and str(tid) != str(team_id):
            continue
        game = r.get("game") or {}
        key = (game.get("id"), tid)
        bucket = grouped.get(key)
        if bucket is None or bucket.get("pts_against"):
            continue
        # Determine if THIS team is home or away in the game.
        home_team_id = game.get("home_team_id") or (game.get("home_team") or {}).get("id")
        if home_team_id == tid:
            bucket["pts_against"] = safe_int(game.get("visitor_team_score"))
        else:
            bucket["pts_against"] = safe_int(game.get("home_team_score"))

    return list(grouped.values())


# ─────────────────────────────────────────────────────────────────────
# Public top-level fetcher (fail-soft, async)
# ─────────────────────────────────────────────────────────────────────
async def _fetch_api_sports_basketball_games(
    client: httpx.AsyncClient,
    team_id: int | str,
    *,
    last_n: int = 10,
) -> list[dict]:
    """API-Sports primary path. Fail-soft on errors."""
    if not API_SPORTS_KEY:
        return []
    try:
        # First: list recent games for the team. Endpoint:
        #   /games?team=<id>&last=<n>
        url = f"{API_SPORTS_BASKETBALL_BASE}/games"
        resp = await robust_get(
            client, url,
            headers=API_SPORTS_HEADERS,
            params={"team": team_id, "last": last_n},
        )
        if resp is None or resp.status_code != 200:
            return []
        data = resp.json() or {}
        games = data.get("response") or []
        if not isinstance(games, list) or not games:
            return []

        # For each game, hit /games/statistics?game=<id>
        out: list[dict] = []
        for g in games[:last_n]:
            gid = (g or {}).get("id")
            if not gid:
                continue
            stats_resp = await robust_get(
                client, f"{API_SPORTS_BASKETBALL_BASE}/games/statistics",
                headers=API_SPORTS_HEADERS,
                params={"game": gid},
            )
            if stats_resp is None or stats_resp.status_code != 200:
                continue
            stats_payload = stats_resp.json() or {}
            out.extend(normalize_api_sports_basketball(stats_payload, team_id=team_id))
        return out
    except Exception as exc:
        log.debug("api_sports basketball fetch failed: %s", exc)
        return []


async def _fetch_balldontlie_games(
    client: httpx.AsyncClient,
    team_id: int | str,
    *,
    last_n: int = 10,
    seasons: Optional[list[int]] = None,
) -> list[dict]:
    if not BALLDONTLIE_KEY:
        return []
    try:
        params: dict[str, Any] = {
            "team_ids[]": team_id,
            "per_page":   max(100, last_n * 14),  # ~14 players/team/game
        }
        if seasons:
            params["seasons[]"] = seasons
        resp = await robust_get(
            client, f"{BALLDONTLIE_BASE}/v1/stats",
            headers={"Authorization": BALLDONTLIE_KEY, "accept": "application/json"},
            params=params,
        )
        if resp is None or resp.status_code != 200:
            return []
        payload = resp.json() or {}
        games = normalize_balldontlie(payload, team_id=team_id)
        # Keep only the most recent last_n entries (sort by date desc).
        games.sort(key=lambda d: str(d.get("date") or ""), reverse=True)
        return games[:last_n]
    except Exception as exc:
        log.debug("balldontlie fetch failed: %s", exc)
        return []


async def fetch_basketball_team_games(
    team_id: int | str,
    *,
    last_n: int = 10,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    seasons: Optional[list[int]] = None,
) -> list[dict]:
    """Fetch recent box-scores for a basketball team.

    Tries API-Sports first, falls back to Balldontlie when primary
    returns nothing usable. Always returns a list (possibly empty).
    NEVER raises.
    """
    if not team_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            games = await _fetch_api_sports_basketball_games(
                client, team_id, last_n=last_n,
            )
            if games:
                return games
            # Fallback path — Balldontlie.
            return await _fetch_balldontlie_games(
                client, team_id, last_n=last_n, seasons=seasons,
            )
    except Exception as exc:
        log.debug("fetch_basketball_team_games top-level failure: %s", exc)
        return []
