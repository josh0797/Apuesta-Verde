"""Basketball Historical Detail Enrichment.

Produces `basketballHistoricalProfile` for a given match by analysing the
last 10–15 completed games of each team plus their recent H2H. The shape
mirrors the user spec:

    basketballHistoricalProfile = {
      "home":     {<per-team metrics>},
      "away":     {<per-team metrics>},
      "combined": {
          "projectedTotalPoints":  …,
          "projectedPace":         …,
          "h2hTotalPointsAvg":     …,
          "overUnderLean":         "OVER" | "UNDER" | "NEUTRAL",
          "marketFitScore":        0–100,
          "fragilityScore":        0–100,
          "trendSummary":          [<frase humana>, …],
      },
      "available":     bool,
      "_reason":       str,
      "_engine_version": "basketball-hist.1",
    }

Data sources
------------
We use API-Sports basketball v1 endpoints already wired in
`services/api_sports.py`:

    GET /games?team={id}&season={season}                  → games list
    GET /games/statistics?game={id}                       → detailed stats
                                                           (optional, only
                                                            when quota allows)
    GET /games/h2h?h2h={home}-{away}                      → recent H2H

`team_statistics` is NOT used here — we re-derive from the per-game data
so the metrics stay consistent with how the user wants to see them
("últimos 10–15 partidos").

Caching
-------
All API calls go through the same cache helpers (`_cache_get` /
`_cache_set`) used by `corner_market_layer`. TTLs are intentionally
aggressive (12h) because historical data doesn't change.

Fail-soft contract
------------------
Anything that prevents us from building the profile (rate limit, missing
team IDs, API timeout) returns an `empty_basketball_profile(reason)`
dictionary so the caller can still proceed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

log = logging.getLogger("historical.basketball")

ENGINE_VERSION = "basketball-hist.1"

# Hard limits
DEFAULT_LOOKBACK_GAMES   = 15      # spec says 10–15
MIN_GAMES_FOR_PROFILE    = 4       # below this we mark `low_sample`
HISTORICAL_TTL_HOURS     = 12
H2H_TTL_HOURS            = 24


# ── League point baselines (used for shrinkage when sample is low) ──────
DEFAULT_LEAGUE_TOTAL = {
    "NBA":         225.0,
    "EuroLeague":  162.0,
    "FIBA":        158.0,
    "Liga ACB":    160.0,
    "default":     200.0,
}


def _league_baseline(league_name: Optional[str]) -> float:
    if not league_name:
        return DEFAULT_LEAGUE_TOTAL["default"]
    low = league_name.lower()
    if "nba" in low:
        return DEFAULT_LEAGUE_TOTAL["NBA"]
    if "euroleague" in low or "euro league" in low:
        return DEFAULT_LEAGUE_TOTAL["EuroLeague"]
    if "fiba" in low:
        return DEFAULT_LEAGUE_TOTAL["FIBA"]
    if "acb" in low:
        return DEFAULT_LEAGUE_TOTAL["Liga ACB"]
    return DEFAULT_LEAGUE_TOTAL["default"]


def empty_basketball_profile(reason: str = "not_available") -> dict:
    """Shape returned when we can't (or shouldn't) build a full profile."""
    return {
        "available":       False,
        "home":            {},
        "away":            {},
        "combined":        {
            "projectedTotalPoints": None,
            "projectedPace":        None,
            "h2hTotalPointsAvg":    None,
            "overUnderLean":        "NEUTRAL",
            "marketFitScore":       0,
            "fragilityScore":       100,
            "trendSummary":         [],
        },
        "_reason":         reason,
        "_engine_version": ENGINE_VERSION,
    }


# ── Game extraction (from API-Sports /games response) ───────────────────
def _score_total(side: dict) -> Optional[int]:
    """Pull total points scored by a side from an API-Sports basketball game.

    The API exposes `scores.{home,away}.total` for finished games.
    """
    if not isinstance(side, dict):
        return None
    v = side.get("total")
    if isinstance(v, int):
        return v
    # Fallback: sum quarters
    qs = [side.get(f"quarter_{i}") for i in range(1, 5)]
    qs.append(side.get("over_time"))
    nums = [q for q in qs if isinstance(q, int)]
    return sum(nums) if nums else None


def _game_is_finished(g: dict) -> bool:
    status = (g.get("status") or {}).get("short") or ""
    return status.upper() in {"FT", "AOT", "FT_OT", "AET", "FIN"}


def _game_datetime(g: dict) -> Optional[datetime]:
    ts = g.get("timestamp")
    if isinstance(ts, int):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    d = g.get("date")
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _had_overtime(g: dict) -> bool:
    home_ot = ((g.get("scores") or {}).get("home") or {}).get("over_time")
    away_ot = ((g.get("scores") or {}).get("away") or {}).get("over_time")
    return bool(
        (isinstance(home_ot, int) and home_ot > 0) or
        (isinstance(away_ot, int) and away_ot > 0)
    )


def _extract_team_perspective(
    games: list[dict],
    team_id: int,
    *,
    lookback: int,
) -> list[dict]:
    """Re-key each game from the perspective of `team_id`.

    Returns dicts like:
        {
            "kickoff_utc":   datetime,
            "is_home":       bool,
            "points_for":    int,
            "points_against":int,
            "total_points":  int,
            "result":        "W"|"L",
            "had_overtime":  bool,
            "opponent_id":   int,
            "opponent_name": str,
            "league_name":   str,
            "raw_game_id":   int,
        }
    Filters to FINISHED games only, sorted most-recent first, capped at
    `lookback`.
    """
    out: list[dict] = []
    for g in games or []:
        if not _game_is_finished(g):
            continue
        teams  = g.get("teams") or {}
        scores = g.get("scores") or {}
        home   = teams.get("home") or {}
        away   = teams.get("away") or {}
        home_pts = _score_total(scores.get("home") or {})
        away_pts = _score_total(scores.get("away") or {})
        if home_pts is None or away_pts is None:
            continue
        if home.get("id") == team_id:
            is_home = True
            pf, pa  = home_pts, away_pts
            opp     = away
        elif away.get("id") == team_id:
            is_home = False
            pf, pa  = away_pts, home_pts
            opp     = home
        else:
            continue
        kickoff = _game_datetime(g)
        out.append({
            "kickoff_utc":   kickoff,
            "is_home":       is_home,
            "points_for":    pf,
            "points_against":pa,
            "total_points":  pf + pa,
            "result":        "W" if pf > pa else "L" if pf < pa else "T",
            "had_overtime":  _had_overtime(g),
            "opponent_id":   opp.get("id"),
            "opponent_name": opp.get("name"),
            "league_name":   (g.get("league") or {}).get("name"),
            "raw_game_id":   g.get("id"),
        })
    out.sort(
        key=lambda r: (r["kickoff_utc"] or datetime(1970, 1, 1, tzinfo=timezone.utc)),
        reverse=True,
    )
    return out[:lookback]


# ── Per-team aggregator ─────────────────────────────────────────────────
def _safe_mean(xs: list[float]) -> Optional[float]:
    nums = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.fmean(nums), 2) if nums else None


def _rate(xs: list[bool]) -> Optional[float]:
    if not xs:
        return None
    return round(sum(1 for x in xs if x) / len(xs), 3)


def _team_block(
    rows: list[dict],
    *,
    team_total_threshold: float,
    league_avg_total: float,
) -> dict:
    """Build the per-team metrics block matching the user spec."""
    if not rows:
        return {
            "gamesAnalyzed": 0,
            "missingData":   True,
        }

    pts_for      = [r["points_for"]    for r in rows]
    pts_against  = [r["points_against"] for r in rows]
    totals       = [r["total_points"]  for r in rows]

    # Over/under against the LEAGUE average (we don't have closing lines
    # per historical game — using the league baseline keeps it honest).
    over_flags   = [t > league_avg_total for t in totals]
    under_flags  = [t < league_avg_total for t in totals]

    home_rows    = [r for r in rows if r["is_home"]]
    away_rows    = [r for r in rows if not r["is_home"]]

    last5_for    = pts_for[:5]
    last5_total  = totals[:5]

    # Team-total threshold = league_avg / 2 by default → "anotar más de la
    # mitad de la línea promedio". Caller can tighten with bookmaker data.
    exceeded_tt  = [p > team_total_threshold for p in pts_for]
    failed_tt    = [p < team_total_threshold for p in pts_for]

    # Trend last5 vs last10 → "scoring trend"
    if len(pts_for) >= 8:
        recent = statistics.fmean(pts_for[:5])
        prior  = statistics.fmean(pts_for[5:10])
        if recent > prior + 3:
            scoring_trend = "RISING"
        elif recent < prior - 3:
            scoring_trend = "FALLING"
        else:
            scoring_trend = "STABLE"
    else:
        scoring_trend = "UNKNOWN"

    # Pace placeholder: derive from total points (proxy). True pace
    # requires possessions from /games/statistics; we backfill there
    # opportunistically. Until then: pace ≈ total / 2.25 (NBA heuristic).
    pace_proxy   = round((_safe_mean(totals) or league_avg_total) / 2.25, 1)

    # Offensive/Defensive efficiency proxies (no possessions data yet)
    off_eff      = round(((_safe_mean(pts_for)     or 0) / 100.0) * 100.0, 1)
    def_res      = round(((_safe_mean(pts_against) or 0) / 100.0) * 100.0, 1)
    # These are kept as ratings on a 100-base for the UI, NOT true ORtg/DRtg.

    # b2b detection: any pair of consecutive games <= 26h apart
    b2b_count = 0
    last_rest = None
    for i in range(len(rows) - 1):
        a = rows[i]["kickoff_utc"]
        b = rows[i + 1]["kickoff_utc"]
        if a and b:
            gap = (a - b).total_seconds() / 3600.0
            if 0 < gap <= 26:
                b2b_count += 1
            if last_rest is None:
                last_rest = round(gap, 1)

    overtime_rate = _rate([r["had_overtime"] for r in rows])

    return {
        "gamesAnalyzed":                  len(rows),
        "pointsForAvg":                   _safe_mean(pts_for),
        "pointsAgainstAvg":               _safe_mean(pts_against),
        "totalPointsAvg":                 _safe_mean(totals),
        "overRate":                       _rate(over_flags),
        "underRate":                      _rate(under_flags),
        "paceProxy":                      pace_proxy,
        "offensiveEfficiencyTrend":       off_eff,    # rating proxy
        "defensiveResistanceTrend":       def_res,    # rating proxy
        "homeAwaySplit": {
            "homeGames":           len(home_rows),
            "awayGames":           len(away_rows),
            "homePointsForAvg":    _safe_mean([r["points_for"] for r in home_rows]),
            "awayPointsForAvg":    _safe_mean([r["points_for"] for r in away_rows]),
            "homeTotalPointsAvg":  _safe_mean([r["total_points"] for r in home_rows]),
            "awayTotalPointsAvg":  _safe_mean([r["total_points"] for r in away_rows]),
        },
        "last5ScoringTrend":              scoring_trend,
        "last5PointsForAvg":              _safe_mean(last5_for),
        "last5TotalPointsAvg":            _safe_mean(last5_total),
        "exceededTeamTotalRate":          _rate(exceeded_tt),
        "failedToReachTeamTotalRate":     _rate(failed_tt),
        "teamTotalThreshold":             round(team_total_threshold, 1),
        "winRate":                        _rate([r["result"] == "W" for r in rows]),
        "overtimeRate":                   overtime_rate,
        "backToBackCount":                b2b_count,
        "lastRestHours":                  last_rest,
        "missingData":                    False,
    }


# ── Combined block + trend phrases ──────────────────────────────────────
def _build_combined(
    home: dict,
    away: dict,
    h2h_avg: Optional[float],
    league_avg_total: float,
) -> dict:
    s_h = home.get("gamesAnalyzed") or 0
    s_a = away.get("gamesAnalyzed") or 0
    if not s_h or not s_a:
        return {
            "projectedTotalPoints":   league_avg_total,
            "projectedPace":          None,
            "h2hTotalPointsAvg":      h2h_avg,
            "overUnderLean":          "NEUTRAL",
            "marketFitScore":         15,
            "fragilityScore":         90,
            "trendSummary":           ["Muestra insuficiente: usando promedio de liga como referencia."],
        }

    # Projection: blend of (team A scores) + (team B concedes) + reverse
    p_h_for = home.get("pointsForAvg")    or league_avg_total / 2.0
    p_a_for = away.get("pointsForAvg")    or league_avg_total / 2.0
    p_h_ag  = home.get("pointsAgainstAvg") or league_avg_total / 2.0
    p_a_ag  = away.get("pointsAgainstAvg") or league_avg_total / 2.0
    proj_home = (p_h_for + p_a_ag) / 2.0
    proj_away = (p_a_for + p_h_ag) / 2.0
    projected = round(proj_home + proj_away, 1)

    pace_h = home.get("paceProxy") or 100.0
    pace_a = away.get("paceProxy") or 100.0
    proj_pace = round((pace_h + pace_a) / 2.0, 1)

    # Lean: project vs league baseline (caller can re-bias with bookmaker line later)
    delta = projected - league_avg_total
    if delta > 4:
        lean = "OVER"
    elif delta < -4:
        lean = "UNDER"
    else:
        lean = "NEUTRAL"

    # Market fit (0–100)
    fit = 0
    if s_h >= MIN_GAMES_FOR_PROFILE:
        fit += 25
    if s_a >= MIN_GAMES_FOR_PROFILE:
        fit += 25
    if abs(delta) >= 4:
        fit += 25
    if s_h >= 8 and s_a >= 8:
        fit += 25
    fit = min(100, fit)

    # Fragility (0–100, lower is healthier)
    frag = 0
    if s_h < MIN_GAMES_FOR_PROFILE:
        frag += 25
    if s_a < MIN_GAMES_FOR_PROFILE:
        frag += 25
    if (home.get("backToBackCount") or 0) >= 2:
        frag += 10
    if (away.get("backToBackCount") or 0) >= 2:
        frag += 10
    if (home.get("overtimeRate") or 0) >= 0.25:
        frag += 10
    if (away.get("overtimeRate") or 0) >= 0.25:
        frag += 10
    frag = min(100, frag)

    return {
        "projectedTotalPoints":  projected,
        "projectionHome":        round(proj_home, 1),
        "projectionAway":        round(proj_away, 1),
        "projectedPace":         proj_pace,
        "h2hTotalPointsAvg":     round(h2h_avg, 1) if h2h_avg else None,
        "overUnderLean":         lean,
        "marketFitScore":        fit,
        "fragilityScore":        frag,
        "leagueAvgTotalUsed":    league_avg_total,
        "trendSummary":          _build_trend_phrases(home, away, projected, league_avg_total, h2h_avg),
    }


def _build_trend_phrases(
    home: dict, away: dict,
    projected: float, league_avg: float,
    h2h_avg: Optional[float],
) -> list[str]:
    """Generate human-readable trend phrases per the spec."""
    phrases: list[str] = []
    # Team A exceeded team total rate
    tt_h = home.get("exceededTeamTotalRate")
    tt_h_n = home.get("gamesAnalyzed") or 0
    tt_thr = home.get("teamTotalThreshold")
    if tt_h is not None and tt_h_n and tt_thr:
        n_over = int(round(tt_h * tt_h_n))
        phrases.append(
            f"El equipo local superó {tt_thr:.0f} puntos en {n_over} de sus últimos {tt_h_n} partidos."
        )
    tt_a = away.get("exceededTeamTotalRate")
    tt_a_n = away.get("gamesAnalyzed") or 0
    tt_thr_a = away.get("teamTotalThreshold")
    if tt_a is not None and tt_a_n and tt_thr_a:
        n_over = int(round(tt_a * tt_a_n))
        phrases.append(
            f"El equipo visitante superó {tt_thr_a:.0f} puntos en {n_over} de sus últimos {tt_a_n} partidos."
        )
    # Defensive permissiveness
    pa_h = home.get("pointsAgainstAvg")
    if pa_h and pa_h >= 110:
        phrases.append(f"La defensa local permitió un promedio de {pa_h:.1f} puntos por partido en su histórico reciente.")
    pa_a = away.get("pointsAgainstAvg")
    if pa_a and pa_a >= 110:
        phrases.append(f"La defensa visitante permitió un promedio de {pa_a:.1f} puntos por partido en su histórico reciente.")
    # Lean note
    if projected and league_avg:
        diff = projected - league_avg
        if abs(diff) >= 4:
            direction = "por encima" if diff > 0 else "por debajo"
            phrases.append(
                f"La proyección del motor ({projected:.1f}) está {abs(diff):.1f} pts {direction} del promedio de la liga ({league_avg:.0f})."
            )
    # H2H
    if h2h_avg and projected:
        if h2h_avg - projected >= 5:
            phrases.append(f"Los enfrentamientos directos recientes promediaron {h2h_avg:.1f} pts — más altos que la proyección actual.")
        elif projected - h2h_avg >= 5:
            phrases.append(f"Los enfrentamientos directos recientes promediaron {h2h_avg:.1f} pts — más bajos que la proyección actual.")
    # B2B
    b2b_h = home.get("backToBackCount") or 0
    b2b_a = away.get("backToBackCount") or 0
    if b2b_h >= 2:
        phrases.append(f"El local acumuló {b2b_h} back-to-backs en los últimos partidos.")
    if b2b_a >= 2:
        phrases.append(f"El visitante acumuló {b2b_a} back-to-backs en los últimos partidos.")
    return phrases[:6]   # cap to avoid noise


# ── Public compute (pure function, easy to unit-test) ───────────────────
def compute_basketball_profile(
    home_games_team_view: list[dict],
    away_games_team_view: list[dict],
    *,
    h2h_team_views_pairs: Optional[list[dict]] = None,
    league_name: Optional[str] = None,
) -> dict:
    """Pure aggregation — no I/O.

    Args:
        home_games_team_view: list returned by `_extract_team_perspective(...)` for HOME.
        away_games_team_view: same for AWAY.
        h2h_team_views_pairs: optional list of dicts {"total_points": int}
                              representing H2H finished games.
        league_name: used to pick the right league baseline for shrinkage.
    """
    league_avg = _league_baseline(league_name)
    team_total_thr = league_avg / 2.0
    home_block = _team_block(
        home_games_team_view,
        team_total_threshold=team_total_thr,
        league_avg_total=league_avg,
    )
    away_block = _team_block(
        away_games_team_view,
        team_total_threshold=team_total_thr,
        league_avg_total=league_avg,
    )

    h2h_avg: Optional[float] = None
    if h2h_team_views_pairs:
        totals = [g.get("total_points") for g in h2h_team_views_pairs
                  if isinstance(g.get("total_points"), (int, float))]
        if totals:
            h2h_avg = round(statistics.fmean(totals), 2)

    combined = _build_combined(home_block, away_block, h2h_avg, league_avg)

    available = bool(
        (home_block.get("gamesAnalyzed") or 0) >= MIN_GAMES_FOR_PROFILE
        and (away_block.get("gamesAnalyzed") or 0) >= MIN_GAMES_FOR_PROFILE
    )
    return {
        "available":       available,
        "home":            home_block,
        "away":            away_block,
        "combined":        combined,
        "_reason":         "ok" if available else "low_sample",
        "_engine_version": ENGINE_VERSION,
    }


# ── Phase 2 enrichment helpers (espejo MLB) ─────────────────────────────
#
# These helpers mutate the existing `profile` dict in place to add three
# optional blocks the spec asks for:
#
#   profile["restAdvantage"] = {homeRestDays, awayRestDays, edge, advantageSide}
#   profile["paceFactor"]    = {leagueAvgPace, projectedPace, factor, code}
#   profile["keyPlayers"]    = {home:[{name,status,impact}], away:[…], _source}
#
# They live next to the public compute / enrich pair so callers don't have
# to import a second module. Each function MUST be fail-soft.
# ─────────────────────────────────────────────────────────────────────────

def _parse_iso_to_dt(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _attach_rest_advantage(
    profile: dict,
    home_games: list[dict],
    away_games: list[dict],
    match: dict,
) -> None:
    """Compute rest days for each side relative to the next match kickoff.

    Rest days = days between the latest finished game and the match
    kickoff (UTC). Edge = home_rest - away_rest (positive ⇒ home rests
    more). Falls back to None if either side has no recent completed
    games.
    """
    kickoff = _parse_iso_to_dt(match.get("kickoff_iso") or match.get("date"))
    if kickoff is None:
        return

    def _latest_kickoff(games: list[dict]) -> Optional[datetime]:
        latest: Optional[datetime] = None
        for g in games or []:
            date_str = (g.get("date") or {}).get("start") if isinstance(g.get("date"), dict) else g.get("date")
            d = _parse_iso_to_dt(date_str)
            if d is None or d >= kickoff:
                continue
            if latest is None or d > latest:
                latest = d
        return latest

    h_latest = _latest_kickoff(home_games)
    a_latest = _latest_kickoff(away_games)
    if h_latest is None or a_latest is None:
        return

    h_rest = round((kickoff - h_latest).total_seconds() / 86400.0, 1)
    a_rest = round((kickoff - a_latest).total_seconds() / 86400.0, 1)
    edge = round(h_rest - a_rest, 1)
    if edge >= 1.0:
        side = "home"
    elif edge <= -1.0:
        side = "away"
    else:
        side = "neutral"

    profile["restAdvantage"] = {
        "homeRestDays":   h_rest,
        "awayRestDays":   a_rest,
        "edge":           edge,
        "advantageSide":  side,
    }


def _attach_pace_factor(profile: dict, league_name: Optional[str]) -> None:
    """Surface a normalized pace factor (1.0 = league average).

    Uses `combined.projectedTotalPoints` and the league baseline already
    computed by `_build_combined`. Caller can read `paceFactor.factor`
    directly without re-deriving it on the UI side.
    """
    combined = profile.get("combined") or {}
    projected = combined.get("projectedTotalPoints")
    league_avg = combined.get("leagueAvgTotalUsed") or _league_baseline(league_name)
    if not projected or not league_avg:
        return
    try:
        factor = round(float(projected) / float(league_avg), 3)
    except (TypeError, ValueError, ZeroDivisionError):
        return
    if factor >= 1.05:
        code = "HIGH"
    elif factor <= 0.95:
        code = "LOW"
    else:
        code = "NEUTRAL"
    profile["paceFactor"] = {
        "leagueAvgPace":  round(float(league_avg), 1),
        "projectedPace":  round(float(projected), 1),
        "factor":         factor,
        "code":           code,
    }


def _attach_key_players(profile: dict, match: dict) -> None:
    """Surface key-player status when available on the match document.

    We DON'T scrape new sources — we read whatever the upstream
    `match.injuries` / `match.lineups` ingestion already put on the
    document. When nothing is available the block is omitted entirely
    so the UI can hide its section.
    """
    src = match.get("injuries") or {}
    home_il = src.get("home_il_players") or src.get("home") or []
    away_il = src.get("away_il_players") or src.get("away") or []

    def _norm_entry(p) -> Optional[dict]:
        if isinstance(p, str) and p.strip():
            return {"name": p.strip(), "status": "out", "impact": "unknown"}
        if isinstance(p, dict):
            name = p.get("name") or p.get("player_name") or p.get("player")
            if not name:
                return None
            return {
                "name":   name,
                "status": (p.get("status") or p.get("availability") or "out").lower(),
                "impact": (p.get("impact") or p.get("role") or "unknown").lower(),
            }
        return None

    home_norm = [e for e in (_norm_entry(p) for p in home_il) if e][:3]
    away_norm = [e for e in (_norm_entry(p) for p in away_il) if e][:3]
    if not home_norm and not away_norm:
        return
    profile["keyPlayers"] = {
        "home":     home_norm,
        "away":     away_norm,
        "_source":  "upstream_injuries_block",
    }


def _augment_trend_summary(profile: dict) -> None:
    """Add 2-3 deeper Spanish phrases derived from the enrichment blocks
    so the UI's "Patrones detectados" section gets a richer signal mix.

    We append (never overwrite) to keep backward-compatibility with any
    consumer that ordered phrases for display.
    """
    combined = profile.get("combined") or {}
    phrases = list(combined.get("trendSummary") or [])

    # Rest-advantage phrase.
    ra = profile.get("restAdvantage")
    if ra and ra.get("advantageSide") in ("home", "away"):
        side = "local" if ra["advantageSide"] == "home" else "visitante"
        edge = ra.get("edge") or 0
        phrases.append(
            f"Ventaja de descanso para el {side}: {abs(edge):.1f} días más de descanso."
        )

    # Pace-factor phrase.
    pf = profile.get("paceFactor")
    if pf:
        if pf["code"] == "HIGH":
            phrases.append(
                f"Pace proyectado elevado ({pf['projectedPace']:.1f} vs liga {pf['leagueAvgPace']:.0f}) — favorece partidos altos."
            )
        elif pf["code"] == "LOW":
            phrases.append(
                f"Pace proyectado bajo ({pf['projectedPace']:.1f} vs liga {pf['leagueAvgPace']:.0f}) — favorece partidos cerrados."
            )

    # Key-player phrase.
    kp = profile.get("keyPlayers") or {}
    high_impact_out = [
        p for side in ("home", "away") for p in (kp.get(side) or [])
        if (p.get("status") in ("out", "doubtful")) and (p.get("impact") in ("star", "high", "starter"))
    ]
    if high_impact_out:
        names = ", ".join(p["name"] for p in high_impact_out[:2])
        phrases.append(f"Bajas relevantes: {names} — impacto ofensivo/defensivo proyectado.")

    if len(phrases) > len(combined.get("trendSummary") or []):
        combined["trendSummary"] = phrases[:8]  # cap to avoid noise
        profile["combined"] = combined


# ── Async I/O wrappers (API-Sports backed) ──────────────────────────────
async def _fetch_team_games(
    client: httpx.AsyncClient,
    team_id: int,
    *,
    season: Any,
    league_id: Optional[int],
    db: Any = None,
) -> list[dict]:
    """Pull the season's games for a team and cache them."""
    from .. import api_sports as _aps

    key = {"sport": "basketball", "kind": "team_games", "team_id": team_id, "season": str(season),
           "league_id": league_id or 0}
    cached = await _aps._cache_get(db, "cache_team_games", key, HISTORICAL_TTL_HOURS * 60)
    if cached is not None:
        return cached

    params = {"team": team_id, "season": season}
    if league_id:
        params["league"] = league_id
    try:
        data = await _aps._get("basketball", client, "/games", params)
    except Exception as exc:
        log.info("[BASKETBALL_HIST_API_FAIL] team=%s season=%s: %s",
                 team_id, season, exc)
        return []
    games = data.get("response") or []
    await _aps._cache_set(db, "cache_team_games", key, games)
    return games


async def _fetch_h2h_games(
    client: httpx.AsyncClient,
    home_id: int,
    away_id: int,
    *,
    db: Any = None,
    limit: int = 5,
) -> list[dict]:
    from .. import api_sports as _aps
    try:
        return await _aps.head_to_head("basketball", client, home_id, away_id, limit=limit, db=db)
    except Exception as exc:
        log.info("[BASKETBALL_HIST_H2H_FAIL] %s vs %s: %s", home_id, away_id, exc)
        return []


# ── Public enrichment ───────────────────────────────────────────────────
async def enrich_basketball_historical_profile(
    match: dict,
    *,
    db: Any = None,
    lookback: int = DEFAULT_LOOKBACK_GAMES,
    timeout_sec: float = 18.0,
) -> dict:
    """Enrich a single basketball match. ALWAYS returns a dict (fail-soft).

    The shape matches the spec:
        {
            available, home, away, combined,
            _reason, _engine_version
        }
    """
    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    home_id = home.get("id")
    away_id = away.get("id")
    league_id = (match.get("league") or {}).get("id")
    league_name = (match.get("league") or {}).get("name")
    if not home_id or not away_id:
        return empty_basketball_profile("missing_team_ids")

    from .. import api_sports as _aps
    season = _aps.proxy_season("basketball")

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            home_games_task = asyncio.create_task(
                _fetch_team_games(client, int(home_id), season=season, league_id=league_id, db=db)
            )
            away_games_task = asyncio.create_task(
                _fetch_team_games(client, int(away_id), season=season, league_id=league_id, db=db)
            )
            h2h_task = asyncio.create_task(
                _fetch_h2h_games(client, int(home_id), int(away_id), db=db, limit=5)
            )
            try:
                home_games, away_games, h2h_games = await asyncio.wait_for(
                    asyncio.gather(home_games_task, away_games_task, h2h_task, return_exceptions=False),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                log.info("[BASKETBALL_HIST_TIMEOUT] match=%s",
                         match.get("match_id"))
                return empty_basketball_profile("timeout")
    except Exception as exc:
        log.info("[BASKETBALL_HIST_FAILED] match=%s: %s",
                 match.get("match_id"), exc)
        return empty_basketball_profile(f"fetch_error:{type(exc).__name__}")

    home_view = _extract_team_perspective(home_games, int(home_id), lookback=lookback)
    away_view = _extract_team_perspective(away_games, int(away_id), lookback=lookback)
    h2h_view  = _extract_team_perspective(h2h_games or [], int(home_id), lookback=5)

    profile = compute_basketball_profile(
        home_view, away_view,
        h2h_team_views_pairs=h2h_view,
        league_name=league_name,
    )

    # ── Phase 2 enrichment (espejo MLB) ──────────────────────────────
    # Add three optional blocks on top of the base profile. Each one is
    # fail-soft: if the data isn't there the block is omitted and the
    # rest of the profile is untouched.
    try:
        _attach_rest_advantage(profile, home_games, away_games, match)
    except Exception as exc:
        log.debug("rest_advantage attach failed: %s", exc)
    try:
        _attach_pace_factor(profile, league_name)
    except Exception as exc:
        log.debug("pace_factor attach failed: %s", exc)
    try:
        _attach_key_players(profile, match)
    except Exception as exc:
        log.debug("key_players attach failed: %s", exc)
    try:
        _augment_trend_summary(profile)
    except Exception as exc:
        log.debug("trend_summary augment failed: %s", exc)

    profile["_engine_version"] = ENGINE_VERSION + "+v2"
    return profile


async def prefetch_basketball_profiles(
    matches: list[dict],
    *,
    db: Any = None,
    timeout_sec: float = 25.0,
) -> int:
    """Bulk pre-fetch the historical profile for many basketball matches.

    Mutates each `match` dict by attaching:
        match["basketballHistoricalProfile"] = <profile>
        match["_basketball_pace_form"]       = <shape for basketball_pace_layer>

    Returns the number of matches that ended up with `available=True`.
    """
    real_matches = [
        m for m in matches
        if m and (m.get("sport") or "").lower() == "basketball"
        and (m.get("home_team") or {}).get("id")
        and (m.get("away_team") or {}).get("id")
    ]
    if not real_matches:
        return 0

    enriched = 0

    async def _one(m: dict) -> None:
        nonlocal enriched
        try:
            profile = await enrich_basketball_historical_profile(m, db=db, timeout_sec=18.0)
        except Exception as exc:
            log.debug("basketball hist enrichment crashed for match %s: %s",
                      m.get("match_id"), exc)
            profile = empty_basketball_profile("exception")

        m["basketballHistoricalProfile"] = profile

        # Also populate the legacy `_basketball_pace_form` so the existing
        # `basketball_pace_layer.find_basketball_pace_value(...)` rescue
        # path can fire without code changes.
        try:
            home_block    = profile.get("home") or {}
            away_block    = profile.get("away") or {}
            combined      = profile.get("combined") or {}
            league_avg    = combined.get("leagueAvgTotalUsed") or _league_baseline(
                (m.get("league") or {}).get("name")
            )
            m["_basketball_pace_form"] = {
                "home": {
                    "sample_size":         home_block.get("gamesAnalyzed") or 0,
                    "avg_points_for":      home_block.get("pointsForAvg"),
                    "avg_points_against":  home_block.get("pointsAgainstAvg"),
                    "pace":                home_block.get("paceProxy"),
                    "offensive_rating":    home_block.get("offensiveEfficiencyTrend"),
                    "defensive_rating":    home_block.get("defensiveResistanceTrend"),
                    "per_match":           [],
                    "missing_data":        bool(home_block.get("missingData")),
                },
                "away": {
                    "sample_size":         away_block.get("gamesAnalyzed") or 0,
                    "avg_points_for":      away_block.get("pointsForAvg"),
                    "avg_points_against":  away_block.get("pointsAgainstAvg"),
                    "pace":                away_block.get("paceProxy"),
                    "offensive_rating":    away_block.get("offensiveEfficiencyTrend"),
                    "defensive_rating":    away_block.get("defensiveResistanceTrend"),
                    "per_match":           [],
                    "missing_data":        bool(away_block.get("missingData")),
                },
                "league_avg_total": league_avg,
                "league_key":       (m.get("league") or {}).get("name") or "default",
                "h2h_avg_total":    (combined or {}).get("h2hTotalPointsAvg"),
            }
        except Exception as exc:
            log.debug("pace_form derivation failed for match %s: %s",
                      m.get("match_id"), exc)

        if profile.get("available"):
            enriched += 1

        # ── Phase 40 / Fix 1 — Box-score hydration (opt-in by default ON).
        # Attaches REAL per-game Four Factors so the basketball_possession
        # _layer can drop the historical fallback. Strict per-match timeout
        # + fail-soft: any failure → silently skip, downstream layers
        # degrade gracefully to the proxy path. Set ``BASKETBALL_BOX_SCORES
        # _HYDRATE=0`` to disable in production if latency budget is tight.
        if os.environ.get("BASKETBALL_BOX_SCORES_HYDRATE", "1") != "0":
            try:
                from services.box_score_providers import (
                    hydrate_match_with_box_scores,
                )
                await asyncio.wait_for(
                    hydrate_match_with_box_scores(m, last_n=8),
                    timeout=float(os.environ.get(
                        "BASKETBALL_BOX_SCORES_HYDRATE_TIMEOUT_S", "5.0",
                    )),
                )
            except (asyncio.TimeoutError, Exception) as _exc_hydrate:
                log.debug(
                    "basketball box-score hydration skipped for match %s: %s",
                    m.get("match_id"), _exc_hydrate,
                )

    try:
        await asyncio.wait_for(
            asyncio.gather(*[_one(m) for m in real_matches], return_exceptions=True),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        log.info("[BASKETBALL_HIST_BULK_TIMEOUT] enriched %d/%d so far",
                 enriched, len(real_matches))
    except Exception as exc:
        log.warning("[BASKETBALL_HIST_BULK_ERROR] %s", exc)

    log.info("[BASKETBALL_HIST_DONE] enriched=%d total=%d",
             enriched, len(real_matches))
    return enriched


__all__ = [
    "enrich_basketball_historical_profile",
    "compute_basketball_profile",
    "prefetch_basketball_profiles",
    "empty_basketball_profile",
    "ENGINE_VERSION",
    "DEFAULT_LOOKBACK_GAMES",
]
