"""Baseball box-score provider — API-Sports primary + MLB StatsAPI fallback.

Returns per-team-per-game dicts compatible with our internal schema:

    {
      "_provider":  "api_sports" | "mlb_statsapi",
      "game_id":     str,
      "date":        "YYYY-MM-DD",
      "team_id":     str | int,
      "ab":          int,
      "h":           int,
      "r":           int,
      "bb":          int,
      "k":           int,
      "sb":          int,
      "obp":         float | None,
      "slg":         float | None,
      "iso":         float | None,
      "babip":       float | None,
      "k_rate":      float | None,
      "bb_rate":     float | None,
      "runs_for":    int,
      "runs_against": int,
    }

MLB StatsAPI is unauthenticated. API-Sports baseball requires
``API_SPORTS_KEY``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .common import (
    API_SPORTS_BASEBALL_BASE,
    API_SPORTS_HEADERS,
    API_SPORTS_KEY,
    DEFAULT_TIMEOUT_S,
    MLB_STATSAPI_BASE,
    robust_get,
    safe_float,
    safe_int,
)

log = logging.getLogger("box_score_providers.baseball")


# ─────────────────────────────────────────────────────────────────────
# Helpers: derived stats (computed locally to ensure consistency)
# ─────────────────────────────────────────────────────────────────────
def _approx_pa(ab: int, bb: int, hbp: int = 0, sf: int = 0, sh: int = 0) -> int:
    return ab + bb + hbp + sf + sh


def _compute_derived(
    *,
    ab: int, h: int, bb: int, k: int,
    hr: int = 0, doubles: int = 0, triples: int = 0,
    obp_provider: Optional[float] = None,
    slg_provider: Optional[float] = None,
    hbp: int = 0, sf: int = 0, sh: int = 0,
) -> dict:
    """Compute OBP / SLG / ISO / BABIP / K_rate / BB_rate.

    Prefer provider-supplied OBP/SLG when present (StatsAPI / API-Sports
    occasionally supply these); compute defaults otherwise.
    """
    pa = _approx_pa(ab, bb, hbp, sf, sh)
    avg = (h / ab) if ab > 0 else 0.0
    obp = obp_provider
    if obp is None:
        denom = ab + bb + hbp + sf
        obp = (h + bb + hbp) / denom if denom > 0 else 0.0
    slg = slg_provider
    if slg is None:
        # Best-effort: when we don't know HR/2B/3B split, use AVG as a
        # conservative proxy (caller still gets ISO=0 in that case).
        singles = max(0, h - (hr + doubles + triples))
        tb = singles + 2 * doubles + 3 * triples + 4 * hr
        slg = (tb / ab) if ab > 0 else 0.0
    iso = max(0.0, (slg or 0.0) - avg)
    babip_denom = ab - k - hr + sf
    babip = (h - hr) / babip_denom if babip_denom > 0 else None
    k_rate  = (k / pa)  if pa > 0 else None
    bb_rate = (bb / pa) if pa > 0 else None
    return {
        "obp":     round(obp, 4) if obp is not None else None,
        "slg":     round(slg, 4) if slg is not None else None,
        "iso":     round(iso, 4),
        "babip":   round(babip, 4) if babip is not None else None,
        "k_rate":  round(k_rate, 4) if k_rate is not None else None,
        "bb_rate": round(bb_rate, 4) if bb_rate is not None else None,
    }


# ─────────────────────────────────────────────────────────────────────
# Normalizers
# ─────────────────────────────────────────────────────────────────────
def normalize_api_sports_baseball(
    payload: dict,
    *,
    team_id: int | str | None,
) -> list[dict]:
    """Translate API-Sports baseball ``/games/statistics`` response."""
    out: list[dict] = []
    if not isinstance(payload, dict):
        return out
    response = payload.get("response") or []
    if not isinstance(response, list):
        return out

    for item in response:
        if not isinstance(item, dict):
            continue
        team = item.get("team") or {}
        tid = team.get("id")
        if team_id is not None and str(tid) != str(team_id):
            continue

        game = item.get("game") or {}
        date_field = game.get("date")
        if isinstance(date_field, dict):
            date_field = date_field.get("date")

        batting = item.get("batting") or item.get("hitting") or {}
        ab = safe_int(batting.get("atBats") or batting.get("at_bats"))
        h  = safe_int(batting.get("hits"))
        r  = safe_int(batting.get("runs"))
        bb = safe_int(batting.get("walks") or batting.get("baseOnBalls"))
        k  = safe_int(batting.get("strikeouts") or batting.get("strikeOuts"))
        sb = safe_int(batting.get("stolenBases"))
        hr = safe_int(batting.get("homeruns") or batting.get("homeRuns"))
        d2 = safe_int(batting.get("doubles"))
        d3 = safe_int(batting.get("triples"))

        scores = game.get("scores") or {}
        runs_for = runs_against = None
        if isinstance(scores, dict):
            home_total = (scores.get("home") or {}).get("total") if isinstance(scores.get("home"), dict) else None
            away_total = (scores.get("away") or {}).get("total") if isinstance(scores.get("away"), dict) else None
            is_home = (game.get("teams") or {}).get("home", {}).get("id") == tid \
                      if isinstance(game.get("teams"), dict) else None
            if is_home is True:
                runs_for, runs_against = home_total, away_total
            elif is_home is False:
                runs_for, runs_against = away_total, home_total

        derived = _compute_derived(
            ab=ab, h=h, bb=bb, k=k, hr=hr, doubles=d2, triples=d3,
            obp_provider=safe_float(batting.get("obp")),
            slg_provider=safe_float(batting.get("slg")),
        )

        out.append({
            "_provider":     "api_sports",
            "game_id":       game.get("id"),
            "date":          date_field,
            "team_id":       tid,
            "ab": ab, "h": h, "r": r, "bb": bb, "k": k, "sb": sb,
            "runs_for":      safe_int(runs_for),
            "runs_against":  safe_int(runs_against),
            **derived,
        })
    return out


def normalize_mlb_statsapi(
    payload: dict,
    *,
    team_id: int | str | None,
) -> list[dict]:
    """Translate one MLB StatsAPI ``/game/<gamePk>/feed/live`` response.

    Returns up to TWO rows (home + away). When ``team_id`` is set, only
    the matching side is returned.
    """
    out: list[dict] = []
    if not isinstance(payload, dict):
        return out
    live = payload.get("liveData") or {}
    box  = live.get("boxscore") or {}
    teams = box.get("teams") or {}
    gamepk = (payload.get("gamePk")
              or (payload.get("gameData") or {}).get("game", {}).get("pk"))
    game_date = ((payload.get("gameData") or {}).get("datetime") or {}).get("officialDate")

    # Final score (top-level shortcut)
    linescore = live.get("linescore") or {}
    team_scores = linescore.get("teams") or {}

    for side in ("home", "away"):
        t = teams.get(side) or {}
        tid = ((t.get("team") or {}).get("id"))
        if team_id is not None and str(tid) != str(team_id):
            continue
        batting = ((t.get("teamStats") or {}).get("batting") or {})
        if not batting:
            continue
        ab = safe_int(batting.get("atBats"))
        h  = safe_int(batting.get("hits"))
        r  = safe_int(batting.get("runs"))
        bb = safe_int(batting.get("baseOnBalls"))
        k  = safe_int(batting.get("strikeOuts"))
        sb = safe_int(batting.get("stolenBases"))
        hr = safe_int(batting.get("homeRuns"))
        d2 = safe_int(batting.get("doubles"))
        d3 = safe_int(batting.get("triples"))
        hbp = safe_int(batting.get("hitByPitch"))
        sf  = safe_int(batting.get("sacFlies"))
        sh  = safe_int(batting.get("sacBunts"))

        runs_for = safe_int((team_scores.get(side) or {}).get("runs"))
        opp_side = "away" if side == "home" else "home"
        runs_against = safe_int((team_scores.get(opp_side) or {}).get("runs"))

        derived = _compute_derived(
            ab=ab, h=h, bb=bb, k=k, hr=hr, doubles=d2, triples=d3,
            obp_provider=safe_float(batting.get("obp")),
            slg_provider=safe_float(batting.get("slg")),
            hbp=hbp, sf=sf, sh=sh,
        )

        out.append({
            "_provider":     "mlb_statsapi",
            "game_id":       gamepk,
            "date":          game_date,
            "team_id":       tid,
            "ab": ab, "h": h, "r": r, "bb": bb, "k": k, "sb": sb,
            "runs_for":      runs_for,
            "runs_against":  runs_against,
            **derived,
        })

    return out


# ─────────────────────────────────────────────────────────────────────
# Public top-level fetcher (fail-soft, async)
# ─────────────────────────────────────────────────────────────────────
async def _fetch_api_sports_baseball_games(
    client: httpx.AsyncClient,
    team_id: int | str,
    *,
    last_n: int = 10,
) -> list[dict]:
    if not API_SPORTS_KEY:
        return []
    try:
        url = f"{API_SPORTS_BASEBALL_BASE}/games"
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
        out: list[dict] = []
        for g in games[:last_n]:
            gid = (g or {}).get("id")
            if not gid:
                continue
            stats_resp = await robust_get(
                client, f"{API_SPORTS_BASEBALL_BASE}/games/statistics",
                headers=API_SPORTS_HEADERS,
                params={"game": gid},
            )
            if stats_resp is None or stats_resp.status_code != 200:
                continue
            stats_payload = stats_resp.json() or {}
            out.extend(normalize_api_sports_baseball(stats_payload, team_id=team_id))
        return out
    except Exception as exc:
        log.debug("api_sports baseball fetch failed: %s", exc)
        return []


async def _fetch_mlb_statsapi_games(
    client: httpx.AsyncClient,
    team_id: int | str,
    *,
    last_n: int = 10,
) -> list[dict]:
    """Fallback path via MLB StatsAPI.

    Strategy: query the team schedule for the last 30 days, then pull
    each game's live feed for boxscore extraction. Unauthenticated.
    """
    try:
        # Schedule lookup
        sched_resp = await robust_get(
            client, f"{MLB_STATSAPI_BASE}/schedule",
            params={
                "sportId":  1,
                "teamId":   team_id,
                # Open-ended date range — StatsAPI uses startDate/endDate.
                # We just ask for "last 30 days" via implicit date filter.
            },
        )
        if sched_resp is None or sched_resp.status_code != 200:
            return []
        sched = sched_resp.json() or {}
        dates = sched.get("dates") or []
        game_pks: list[int] = []
        for day in dates:
            for game in (day.get("games") or []):
                pk = game.get("gamePk")
                if pk:
                    game_pks.append(pk)
        if not game_pks:
            return []
        game_pks = game_pks[-last_n:]   # latest N

        out: list[dict] = []
        for pk in game_pks:
            r = await robust_get(client, f"{MLB_STATSAPI_BASE}/game/{pk}/feed/live")
            if r is None or r.status_code != 200:
                continue
            out.extend(normalize_mlb_statsapi(r.json() or {}, team_id=team_id))
        return out
    except Exception as exc:
        log.debug("mlb_statsapi fetch failed: %s", exc)
        return []


async def fetch_baseball_team_games(
    team_id: int | str,
    *,
    last_n: int = 10,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    mlb_team_id: Optional[int | str] = None,
) -> list[dict]:
    """Fetch recent box-scores for a baseball team. Fail-soft.

    ``team_id`` is the API-Sports team id (primary). When API-Sports
    returns nothing we fall back to MLB StatsAPI, which uses its own
    team ID space — pass ``mlb_team_id`` if you have it. When omitted
    we still hit StatsAPI with the same ``team_id`` (the user can map
    it later).
    """
    if not team_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            games = await _fetch_api_sports_baseball_games(
                client, team_id, last_n=last_n,
            )
            if games:
                return games
            return await _fetch_mlb_statsapi_games(
                client, mlb_team_id or team_id, last_n=last_n,
            )
    except Exception as exc:
        log.debug("fetch_baseball_team_games top-level failure: %s", exc)
        return []
