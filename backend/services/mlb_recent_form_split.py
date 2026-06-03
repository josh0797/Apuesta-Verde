"""
MLB Recent-Form Split — Últimos 5 vs Últimos 15 juegos
========================================================

Pulls per-team team-batting averages over the last 5 and last 15 games
from MLB Stats API and surfaces two complementary trend signals that
feed the final pick router and the deep historical panel:

  1. **`recent_run_trend`**: rising / stable / declining run environment
     based on the delta between L5 and L15 average total runs.
  2. **`on_base_profile`**: hits + walks + HBP averages per team, with
     a trend label per side (rising / stable / declining on-base pressure).

Why two windows?
----------------
L15 captures the stable baseline ("this team has averaged 4.8 runs/g
all month") while L5 captures the very recent trend ("but in the last 5
games they're averaging 6.1"). The DELTA is the actionable signal: a
+1.3-run delta means the team's offense has heated up materially and an
Under recommendation derived from L15 alone would be a stale call.

Endpoint contract (MLB Stats API)
---------------------------------
We use the public endpoint::

    GET /api/v1/teams/{teamId}/stats?stats=lastXGames&group=hitting
                                    &gameType=R&season={season}&limit={N}

The response contains a single ``stats`` row with the cumulative
averages over the requested window. We hit it twice per team-side per
game (L5 + L15), with aggressive 12h caching to stay well within the
endpoint's soft rate-limit budget.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from datetime import datetime, timezone, timedelta

import httpx

log = logging.getLogger(__name__)

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"

# Cache TTL — 12h is the right tradeoff: the L5/L15 averages don't move
# more than ~0.1 runs/game between back-to-back game days, but we want
# to refresh after each game day to keep the trend signal fresh.
_CACHE_TTL = timedelta(hours=12)

# Run-trend thresholds (Δ runs/game).
RISING_RUN_THRESHOLD     = 1.25
DECLINING_RUN_THRESHOLD  = -1.25

# On-base-trend thresholds (Δ times-on-base/game).
RISING_OB_THRESHOLD      = 1.0
DECLINING_OB_THRESHOLD   = -1.0


# ── In-memory cache ────────────────────────────────────────────────────
_CACHE: dict[tuple[int, int, int], tuple[datetime, dict]] = {}


def _cache_key(team_id: int, last_n: int, season: int) -> tuple[int, int, int]:
    return (int(team_id), int(last_n), int(season))


def _cache_get(team_id: int, last_n: int, season: int) -> Optional[dict]:
    k = _cache_key(team_id, last_n, season)
    hit = _CACHE.get(k)
    if not hit:
        return None
    expires_at, value = hit
    if datetime.now(timezone.utc) > expires_at:
        _CACHE.pop(k, None)
        return None
    return value


def _cache_set(team_id: int, last_n: int, season: int, value: dict) -> None:
    _CACHE[_cache_key(team_id, last_n, season)] = (
        datetime.now(timezone.utc) + _CACHE_TTL,
        value,
    )


# ── Low-level fetch ────────────────────────────────────────────────────
async def _fetch_last_x_games(
    client: httpx.AsyncClient,
    team_id: int,
    last_n: int,
    season: int,
) -> dict:
    """Return the raw `splits[0].stat` dict from the MLB Stats API for
    a team over the last N games. Always returns a (possibly empty) dict
    — never raises. Cached.
    """
    cached = _cache_get(team_id, last_n, season)
    if cached is not None:
        return cached

    params = {
        "stats":    "lastXGames",
        "group":    "hitting",
        "gameType": "R",
        "season":   season,
        "limit":    last_n,
    }
    url = f"{MLB_STATS_BASE}/teams/{team_id}/stats"
    try:
        r = await client.get(url, params=params, timeout=10.0)
        r.raise_for_status()
        data = r.json() or {}
        splits = (data.get("stats") or [{}])[0].get("splits") or []
        stat = (splits[0].get("stat") if splits else {}) or {}
    except Exception as exc:
        log.debug("lastXGames fetch failed team=%s last=%s: %s", team_id, last_n, exc)
        stat = {}

    _cache_set(team_id, last_n, season, stat)
    return stat


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _per_game(stat: dict, *, numerator_field: str) -> Optional[float]:
    """Pull a counting stat (hits, runs, walks, hitByPitch) and divide
    by gamesPlayed to get the per-game average.
    """
    if not stat:
        return None
    g_played = _to_float(stat.get("gamesPlayed")) or _to_float(stat.get("gamesStarted"))
    if not g_played or g_played <= 0:
        return None
    raw = _to_float(stat.get(numerator_field))
    if raw is None:
        return None
    return round(raw / g_played, 3)


# ── Public API ─────────────────────────────────────────────────────────
async def get_team_recent_form(
    client: httpx.AsyncClient,
    team_id: int,
    season: int,
) -> dict:
    """Fetch L5 + L15 stats for a single team in parallel and return a
    normalized per-game profile. Returns ``{}`` if the team has no data.
    """
    if not team_id:
        return {}
    try:
        l5, l15 = await asyncio.gather(
            _fetch_last_x_games(client, team_id, 5, season),
            _fetch_last_x_games(client, team_id, 15, season),
            return_exceptions=False,
        )
    except Exception as exc:
        log.debug("get_team_recent_form parallel fetch failed: %s", exc)
        return {}

    # Per-game offensive metrics (per team — own runs scored).
    runs_scored_l5  = _per_game(l5,  numerator_field="runs")
    runs_scored_l15 = _per_game(l15, numerator_field="runs")

    hits_l5   = _per_game(l5,  numerator_field="hits")
    hits_l15  = _per_game(l15, numerator_field="hits")
    bb_l5     = _per_game(l5,  numerator_field="baseOnBalls")
    bb_l15    = _per_game(l15, numerator_field="baseOnBalls")
    hbp_l5    = _per_game(l5,  numerator_field="hitByPitch")
    hbp_l15   = _per_game(l15, numerator_field="hitByPitch")
    hr_l5     = _per_game(l5,  numerator_field="homeRuns")
    hr_l15    = _per_game(l15, numerator_field="homeRuns")

    def _tob(h: Optional[float], bb: Optional[float], hbp: Optional[float]) -> Optional[float]:
        parts = [x for x in (h, bb, hbp) if x is not None]
        if not parts:
            return None
        return round(sum(parts), 3)

    tob_l5  = _tob(hits_l5,  bb_l5,  hbp_l5)
    tob_l15 = _tob(hits_l15, bb_l15, hbp_l15)

    # OBP from raw fields if present, else None.
    obp_l5  = _to_float(l5.get("obp"))   or None
    obp_l15 = _to_float(l15.get("obp"))  or None

    return {
        "team_id":                team_id,
        "runs_scored_avg_last_5":  runs_scored_l5,
        "runs_scored_avg_last_15": runs_scored_l15,
        "hits_avg_last_5":         hits_l5,
        "hits_avg_last_15":        hits_l15,
        "walks_avg_last_5":        bb_l5,
        "walks_avg_last_15":       bb_l15,
        "hbp_avg_last_5":          hbp_l5,
        "hbp_avg_last_15":         hbp_l15,
        "home_runs_avg_last_5":    hr_l5,
        "home_runs_avg_last_15":   hr_l15,
        "times_on_base_avg_last_5":  tob_l5,
        "times_on_base_avg_last_15": tob_l15,
        "obp_last_5":              obp_l5,
        "obp_last_15":             obp_l15,
        "games_played_last_5":     int(_to_float(l5.get("gamesPlayed")) or 0),
        "games_played_last_15":    int(_to_float(l15.get("gamesPlayed")) or 0),
    }


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 3)


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


def build_recent_form_payload(home_form: dict, away_form: dict) -> dict:
    """Combine the home + away L5/L15 dicts into the canonical
    ``recent_run_split`` + ``on_base_profile`` payload consumed by the
    final pick router and the UI.

    The router uses ``recent_run_trend`` as a gate on Under picks:
    a ``RISING_RUN_ENVIRONMENT`` should weaken Under-side confidence.
    """
    # Run-environment delta: home_scored + away_scored = total runs scored
    # in the average game both teams have played. We use the SUM of both
    # sides' own offence as our proxy for total game runs.
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

    return {
        # ── Run-environment trend (used as gate) ──
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
        # ── On-base pressure (informational + Under-favor signal) ──
        "on_base_profile":  on_base_profile,
    }


__all__ = [
    "get_team_recent_form",
    "build_recent_form_payload",
    "RISING_RUN_THRESHOLD",
    "DECLINING_RUN_THRESHOLD",
    "RISING_OB_THRESHOLD",
    "DECLINING_OB_THRESHOLD",
    "_classify_run_trend",      # exposed for tests
    "_classify_on_base_trend",  # exposed for tests
]
