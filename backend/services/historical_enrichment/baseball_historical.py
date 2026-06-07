"""Baseball (MLB) Historical Detail Enrichment.

Mirrors the structure of `basketball_historical.py` but speaks the
language of innings, runs, OBP/SLG/OPS, bullpen usage and starting
pitcher ERA/WHIP.

Public output shape (matches user spec):

    baseballHistoricalProfile = {
        "available":      bool,
        "home":           {<batting + scoring block>},
        "away":           {<batting + scoring block>},
        "pitching":       {
            "homeStarter":          {name, id, era, whip, ip_per_start, last5_era},
            "awayStarter":          {name, id, era, whip, ip_per_start, last5_era},
            "pitcherAdvantage":     "home"|"away"|"none",
            "bullpenAdvantage":     "home"|"away"|"none",
            "homeBullpen":          {fatigue_score, fatigue_label, games_played_recent},
            "awayBullpen":          {fatigue_score, fatigue_label, games_played_recent},
        },
        "combined": {
            "projectedTotalRuns":   float,
            "h2hTotalRunsAvg":      float|None,
            "overUnderLean":        "OVER" | "UNDER" | "NEUTRAL",
            "f5Lean":               "OVER" | "UNDER" | "NEUTRAL",
            "marketFitScore":       0-100,
            "fragilityScore":       0-100,
            "trendSummary":         [<frase en español>, …],
        },
        "_reason":        str,
        "_engine_version": "baseball-hist.1",
    }

Data sources
------------
1. MLB Stats API (`services.mlb_stats_api`)
     • get_team_batting_form  → season OBP/SLG/OPS/runs-per-game
     • get_pitcher_season_stats → ERA/WHIP per probable starter
     • get_bullpen_recent_usage → fatigue last 3-5 days
     • get_schedule_with_probables → per-game linescores (used for the
       "last 15 games" detail)

2. API-Sports baseball (`services.api_sports`) — fallback when MLB Stats
   API doesn't have the league (NPB / KBO / CPBL) or for non-MLB games.

Fail-soft contract: every fetch is wrapped, returns
`empty_baseball_profile(reason)` when something goes wrong. The pipeline
keeps running.
"""
from __future__ import annotations

import asyncio
import logging
import os
import statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

log = logging.getLogger("historical.baseball")

ENGINE_VERSION = "baseball-hist.1"

DEFAULT_LOOKBACK_GAMES   = 15
MIN_GAMES_FOR_PROFILE    = 5
HISTORICAL_TTL_HOURS     = 6     # baseball moves faster — daily refresh

# League run baselines (used when sample is too thin)
DEFAULT_LEAGUE_RUNS = {
    "MLB":      9.0,
    "NPB":      8.0,
    "KBO":      10.5,
    "CPBL":     10.0,
    "default":  9.0,
}


def _league_runs_baseline(league_name: Optional[str]) -> float:
    if not league_name:
        return DEFAULT_LEAGUE_RUNS["default"]
    low = league_name.lower()
    if "mlb" in low or "major" in low:
        return DEFAULT_LEAGUE_RUNS["MLB"]
    if "npb" in low or "nippon" in low or "japan" in low:
        return DEFAULT_LEAGUE_RUNS["NPB"]
    if "kbo" in low or "korea" in low:
        return DEFAULT_LEAGUE_RUNS["KBO"]
    if "cpbl" in low or "taiwan" in low:
        return DEFAULT_LEAGUE_RUNS["CPBL"]
    return DEFAULT_LEAGUE_RUNS["default"]


def empty_baseball_profile(reason: str = "not_available") -> dict:
    return {
        "available":       False,
        "home":            {},
        "away":            {},
        "pitching":        {},
        "combined":        {
            "projectedTotalRuns": None,
            "h2hTotalRunsAvg":    None,
            "overUnderLean":      "NEUTRAL",
            "f5Lean":             "NEUTRAL",
            "marketFitScore":     0,
            "fragilityScore":     100,
            "trendSummary":       [],
        },
        "injuries":        {
            "home_il_count":   0,
            "away_il_count":   0,
            "home_il_players": [],
            "away_il_players": [],
            "_source":         "MLB Stats API (roster/injuries)",
        },
        "_reason":         reason,
        "_engine_version": ENGINE_VERSION,
    }


# ── Aggregation helpers ─────────────────────────────────────────────────
def _safe_mean(xs: list[float]) -> Optional[float]:
    nums = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.fmean(nums), 2) if nums else None


def _rate(xs: list[bool]) -> Optional[float]:
    if not xs:
        return None
    return round(sum(1 for x in xs if x) / len(xs), 3)


def _extract_runs_perspective(games: list[dict], team_id: int, *, lookback: int) -> list[dict]:
    """Re-key the MLB /schedule game list (with linescore hydrated) so each
    row is from the team's point of view.

    Output row:
        {
            "kickoff_utc": datetime,
            "is_home":     bool,
            "runs_for":    int,
            "runs_against":int,
            "total_runs":  int,
            "hits_for":    int,
            "hits_against":int,
            "errors_for":  int,
            "errors_against":int,
            "f5_runs_for":  int,  # runs in innings 1-5 (when innings hydrated)
            "f5_runs_against": int,
            "f5_total":     int,
            "result":      "W"|"L"|"T",
            "innings_played": int,
            "extra_innings": bool,
            "opponent_id": int,
            "opponent_name": str,
            "league_name": str,
        }
    """
    out: list[dict] = []
    for g in games or []:
        status = (g.get("status") or {})
        # MLB status format: detailedState / abstractGameState
        detailed = (status.get("detailedState") or status.get("long") or "").lower()
        if "final" not in detailed and (status.get("short") or "").upper() not in {"FT", "FIN"}:
            continue

        teams = g.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        # MLB linescore is the source of truth for runs/hits/errors
        ls = g.get("linescore") or {}
        ls_teams = ls.get("teams") or {}
        home_ls = ls_teams.get("home") or {}
        away_ls = ls_teams.get("away") or {}

        def _ls_int(side, key):
            v = side.get(key)
            if isinstance(v, int):
                return v
            return None

        home_runs = _ls_int(home_ls, "runs")
        away_runs = _ls_int(away_ls, "runs")
        # Fallback path: API-Sports baseball uses scores.{home,away}.total
        if home_runs is None or away_runs is None:
            scores = g.get("scores") or {}
            sh = scores.get("home") or {}
            sa = scores.get("away") or {}
            home_runs = sh.get("total") if isinstance(sh.get("total"), int) else None
            away_runs = sa.get("total") if isinstance(sa.get("total"), int) else None
        if home_runs is None or away_runs is None:
            continue

        home_id  = ((home.get("team") or {}).get("id")) or home.get("id")
        away_id  = ((away.get("team") or {}).get("id")) or away.get("id")

        if home_id == team_id:
            is_home = True
            rf, ra = home_runs, away_runs
            opp = away.get("team") or away
            hits_for     = _ls_int(home_ls, "hits")
            hits_against = _ls_int(away_ls, "hits")
            err_for      = _ls_int(home_ls, "errors")
            err_against  = _ls_int(away_ls, "errors")
        elif away_id == team_id:
            is_home = False
            rf, ra = away_runs, home_runs
            opp = home.get("team") or home
            hits_for     = _ls_int(away_ls, "hits")
            hits_against = _ls_int(home_ls, "hits")
            err_for      = _ls_int(away_ls, "errors")
            err_against  = _ls_int(home_ls, "errors")
        else:
            continue

        # F5: sum innings 1-5 from linescore.innings
        innings = ls.get("innings") or []
        f5_for, f5_against = None, None
        if innings:
            try:
                f5_home = sum(int((i.get("home") or {}).get("runs") or 0) for i in innings[:5])
                f5_away = sum(int((i.get("away") or {}).get("runs") or 0) for i in innings[:5])
                f5_for = f5_home if is_home else f5_away
                f5_against = f5_away if is_home else f5_home
            except Exception:
                pass

        # Game datetime
        kickoff: Optional[datetime] = None
        for k in ("gameDate", "officialDate", "date"):
            v = g.get(k)
            if isinstance(v, str):
                try:
                    kickoff = datetime.fromisoformat(v.replace("Z", "+00:00"))
                    break
                except Exception:
                    continue
        ts = g.get("timestamp")
        if kickoff is None and isinstance(ts, int):
            try:
                kickoff = datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass

        out.append({
            "kickoff_utc":       kickoff,
            "is_home":           is_home,
            "runs_for":          rf,
            "runs_against":      ra,
            "total_runs":        rf + ra,
            "hits_for":          hits_for,
            "hits_against":      hits_against,
            "errors_for":        err_for,
            "errors_against":    err_against,
            "f5_runs_for":       f5_for,
            "f5_runs_against":   f5_against,
            "f5_total":          (f5_for + f5_against) if (f5_for is not None and f5_against is not None) else None,
            "result":            "W" if rf > ra else "L" if rf < ra else "T",
            "innings_played":    len(innings) if innings else 9,
            "extra_innings":     len(innings) > 9,
            "opponent_id":       (opp or {}).get("id"),
            "opponent_name":     (opp or {}).get("name"),
            "league_name":       (g.get("league") or {}).get("name") or "MLB",
        })

    out.sort(
        key=lambda r: (r["kickoff_utc"] or datetime(1970, 1, 1, tzinfo=timezone.utc)),
        reverse=True,
    )
    return out[:lookback]


def _team_block(
    rows: list[dict],
    *,
    season_form: Optional[dict],
    league_avg_runs: float,
) -> dict:
    if not rows and not season_form:
        return {"gamesAnalyzed": 0, "missingData": True}

    n = len(rows)
    if n == 0:
        # Only season aggregate available
        rpg = (season_form or {}).get("runs_per_game") or league_avg_runs / 2.0
        return {
            "gamesAnalyzed":      0,
            "runsForAvg":         rpg,
            "runsAgainstAvg":     None,
            "hitsAvg":            (season_form or {}).get("hits_per_game"),
            "obpTrend":           (season_form or {}).get("obp"),
            "slgTrend":           (season_form or {}).get("slg"),
            "opsTrend":           (season_form or {}).get("ops"),
            "strikeoutRateTrend": (season_form or {}).get("strikeout_rate"),
            "walkRateTrend":      (season_form or {}).get("walk_rate"),
            "homeRuns":           (season_form or {}).get("home_runs"),
            "missingData":        True,
        }

    rf      = [r["runs_for"]        for r in rows]
    ra      = [r["runs_against"]    for r in rows]
    totals  = [r["total_runs"]      for r in rows]
    hits_f  = [r["hits_for"]        for r in rows if isinstance(r["hits_for"], int)]
    last5_for = rf[:5]
    over_flags  = [t > league_avg_runs       for t in totals]
    under_flags = [t < league_avg_runs       for t in totals]
    team_total_thr = league_avg_runs / 2.0   # default thr; can be overridden by book line later
    exceeded_tt = [r > team_total_thr for r in rf]
    failed_tt   = [r < team_total_thr for r in rf]

    home_rows = [r for r in rows if r["is_home"]]
    away_rows = [r for r in rows if not r["is_home"]]

    # F5 stats (when available)
    f5_for_vals = [r["f5_runs_for"] for r in rows if isinstance(r.get("f5_runs_for"), int)]
    f5_total_vals = [r["f5_total"] for r in rows if isinstance(r.get("f5_total"), int)]

    # Trend last5 vs older
    if len(rf) >= 8:
        recent = statistics.fmean(rf[:5])
        prior  = statistics.fmean(rf[5:10])
        if recent > prior + 0.7:
            scoring_trend = "RISING"
        elif recent < prior - 0.7:
            scoring_trend = "FALLING"
        else:
            scoring_trend = "STABLE"
    else:
        scoring_trend = "UNKNOWN"

    extra_inning_count = sum(1 for r in rows if r["extra_innings"])

    return {
        "gamesAnalyzed":             n,
        "runsForAvg":                _safe_mean(rf),
        "runsAgainstAvg":            _safe_mean(ra),
        "totalRunsAvg":              _safe_mean(totals),
        "hitsAvg":                   _safe_mean(hits_f) if hits_f else ((season_form or {}).get("hits_per_game")),
        "overRate":                  _rate(over_flags),
        "underRate":                 _rate(under_flags),
        "exceededTeamTotalRate":     _rate(exceeded_tt),
        "failedToReachTeamTotalRate":_rate(failed_tt),
        "teamTotalThreshold":        round(team_total_thr, 2),
        # Season-aggregate batting (these don't change fast)
        "obpTrend":                  (season_form or {}).get("obp"),
        "slgTrend":                  (season_form or {}).get("slg"),
        "opsTrend":                  (season_form or {}).get("ops"),
        "strikeoutRateTrend":        (season_form or {}).get("strikeout_rate"),
        "walkRateTrend":             (season_form or {}).get("walk_rate"),
        "homeRuns":                  (season_form or {}).get("home_runs"),
        # Splits & last5
        "homeAwaySplit": {
            "homeGames":             len(home_rows),
            "awayGames":             len(away_rows),
            "homeRunsForAvg":        _safe_mean([r["runs_for"] for r in home_rows]),
            "awayRunsForAvg":        _safe_mean([r["runs_for"] for r in away_rows]),
        },
        "last5ScoringTrend":         scoring_trend,
        "last5RunsForAvg":           _safe_mean(last5_for),
        "last5TotalRunsAvg":         _safe_mean(totals[:5]),
        "winRate":                   _rate([r["result"] == "W" for r in rows]),
        "extraInningGames":          extra_inning_count,
        # F5 metrics (per-team perspective)
        "f5RunsForAvg":              _safe_mean(f5_for_vals) if f5_for_vals else None,
        "f5TotalAvg":                _safe_mean(f5_total_vals) if f5_total_vals else None,
        "missingData":               False,
    }


def _pitcher_block(stats: Optional[dict], name: Optional[str]) -> dict:
    if not stats and not name:
        return {}
    return {
        "name":          name,
        "id":            (stats or {}).get("pitcher_id") or (stats or {}).get("id"),
        "era":           (stats or {}).get("era"),
        "whip":          (stats or {}).get("whip"),
        "ipPerStart":    (stats or {}).get("avg_innings_per_start") or (stats or {}).get("ip_per_start"),
        "strikeoutsPer9":(stats or {}).get("k9") or (stats or {}).get("strikeouts_per_9"),
        "walksPer9":     (stats or {}).get("bb9") or (stats or {}).get("walks_per_9"),
        "homeRunsAllowed":(stats or {}).get("home_runs_allowed") or (stats or {}).get("hr"),
        "starts":        (stats or {}).get("games_started") or (stats or {}).get("starts"),
    }


def _bullpen_block(usage: Optional[dict]) -> dict:
    if not usage:
        return {}
    return {
        "fatigueScore":         usage.get("fatigue_score_0_100"),
        "fatigueLabel":         usage.get("fatigue_label"),
        "gamesPlayedRecent":    usage.get("games_played_recent"),
        "extraInningGamesRecent":usage.get("extra_inning_games_recent"),
        "lookbackDays":         usage.get("days"),
    }


def _build_combined(
    home: dict, away: dict,
    *, pitching: dict,
    h2h_avg: Optional[float],
    league_avg_runs: float,
) -> dict:
    s_h = home.get("gamesAnalyzed") or 0
    s_a = away.get("gamesAnalyzed") or 0

    if not (s_h and s_a):
        return {
            "projectedTotalRuns":   league_avg_runs,
            "h2hTotalRunsAvg":      h2h_avg,
            "overUnderLean":        "NEUTRAL",
            "f5Lean":               "NEUTRAL",
            "marketFitScore":       15,
            "fragilityScore":       90,
            "trendSummary":         ["Muestra insuficiente: usando promedio de liga como referencia."],
        }

    # Projection
    p_h_for = home.get("runsForAvg")     or league_avg_runs / 2.0
    p_a_for = away.get("runsForAvg")     or league_avg_runs / 2.0
    p_h_ag  = home.get("runsAgainstAvg") or league_avg_runs / 2.0
    p_a_ag  = away.get("runsAgainstAvg") or league_avg_runs / 2.0
    proj_home = (p_h_for + p_a_ag) / 2.0
    proj_away = (p_a_for + p_h_ag) / 2.0
    projected = round(proj_home + proj_away, 2)

    # Pitcher adjustment: subtract a small amount when both starters elite
    era_pen = 0.0
    hs = pitching.get("homeStarter") or {}
    asts = pitching.get("awayStarter") or {}
    h_era = hs.get("era")
    a_era = asts.get("era")
    if isinstance(h_era, (int, float)) and h_era < 3.30:
        era_pen += 0.5
    if isinstance(a_era, (int, float)) and a_era < 3.30:
        era_pen += 0.5
    if isinstance(h_era, (int, float)) and h_era > 5.00:
        era_pen -= 0.4
    if isinstance(a_era, (int, float)) and a_era > 5.00:
        era_pen -= 0.4
    projected = round(projected - era_pen, 2)

    delta = projected - league_avg_runs
    lean = "OVER" if delta > 0.6 else ("UNDER" if delta < -0.6 else "NEUTRAL")

    # F5 lean
    f5_home = home.get("f5TotalAvg")
    f5_away = away.get("f5TotalAvg")
    f5_proj = None
    if isinstance(f5_home, (int, float)) and isinstance(f5_away, (int, float)):
        f5_proj = round((f5_home + f5_away) / 2.0, 2)
    f5_baseline = league_avg_runs * 0.55
    if f5_proj is None:
        f5_lean = "NEUTRAL"
    elif f5_proj - f5_baseline > 0.4:
        f5_lean = "OVER"
    elif f5_baseline - f5_proj > 0.4:
        f5_lean = "UNDER"
    else:
        f5_lean = "NEUTRAL"

    # Market fit / fragility
    fit = 0
    if s_h >= MIN_GAMES_FOR_PROFILE:
        fit += 25
    if s_a >= MIN_GAMES_FOR_PROFILE:
        fit += 25
    if abs(delta) >= 0.6:
        fit += 25
    if (pitching.get("homeStarter") or {}).get("era") and (pitching.get("awayStarter") or {}).get("era"):
        fit += 25
    fit = min(100, fit)

    frag = 0
    if s_h < MIN_GAMES_FOR_PROFILE:
        frag += 25
    if s_a < MIN_GAMES_FOR_PROFILE:
        frag += 25
    h_bp = (pitching.get("homeBullpen") or {}).get("fatigueScore") or 0
    a_bp = (pitching.get("awayBullpen") or {}).get("fatigueScore") or 0
    if h_bp >= 60:
        frag += 10
    if a_bp >= 60:
        frag += 10
    if home.get("extraInningGames", 0) >= 2:
        frag += 5
    if away.get("extraInningGames", 0) >= 2:
        frag += 5
    if not (pitching.get("homeStarter") and pitching.get("awayStarter")):
        frag += 20
    frag = min(100, frag)

    return {
        "projectedTotalRuns":  projected,
        "projectedHomeRuns":   round(proj_home, 2),
        "projectedAwayRuns":   round(proj_away, 2),
        "f5ProjectedRuns":     f5_proj,
        "h2hTotalRunsAvg":     round(h2h_avg, 2) if h2h_avg else None,
        "overUnderLean":       lean,
        "f5Lean":              f5_lean,
        "marketFitScore":      fit,
        "fragilityScore":      frag,
        "leagueAvgRunsUsed":   league_avg_runs,
        "trendSummary":        _build_trend_phrases(home, away, pitching, projected, league_avg_runs, h2h_avg, f5_lean),
    }


def _build_trend_phrases(
    home: dict, away: dict, pitching: dict,
    projected: float, league_avg: float,
    h2h_avg: Optional[float], f5_lean: str,
) -> list[str]:
    phrases: list[str] = []
    # Cold offense — "no superó 3 carreras en X de 15"
    n_h = home.get("gamesAnalyzed") or 0
    n_a = away.get("gamesAnalyzed") or 0
    failed_h = home.get("failedToReachTeamTotalRate")
    failed_a = away.get("failedToReachTeamTotalRate")
    if failed_h is not None and n_h and failed_h >= 0.5:
        n_failed = int(round(failed_h * n_h))
        phrases.append(
            f"El equipo local no superó {home.get('teamTotalThreshold', 4.5):.1f} carreras "
            f"en {n_failed} de sus últimos {n_h} partidos."
        )
    if failed_a is not None and n_a and failed_a >= 0.5:
        n_failed = int(round(failed_a * n_a))
        phrases.append(
            f"El equipo visitante no superó {away.get('teamTotalThreshold', 4.5):.1f} carreras "
            f"en {n_failed} de sus últimos {n_a} partidos."
        )
    # Hot offense — "anotó >X carreras en X de 15"
    exceeded_h = home.get("exceededTeamTotalRate")
    exceeded_a = away.get("exceededTeamTotalRate")
    if exceeded_h is not None and n_h and exceeded_h >= 0.6:
        n_hit = int(round(exceeded_h * n_h))
        phrases.append(
            f"El equipo local anotó más de {home.get('teamTotalThreshold', 4.5):.1f} carreras "
            f"en {n_hit} de sus últimos {n_h} partidos."
        )
    if exceeded_a is not None and n_a and exceeded_a >= 0.6:
        n_hit = int(round(exceeded_a * n_a))
        phrases.append(
            f"El equipo visitante anotó más de {away.get('teamTotalThreshold', 4.5):.1f} carreras "
            f"en {n_hit} de sus últimos {n_a} partidos."
        )
    # Bullpen fatigue
    h_bp = (pitching.get("homeBullpen") or {})
    a_bp = (pitching.get("awayBullpen") or {})
    if (h_bp.get("fatigueScore") or 0) >= 60:
        phrases.append(
            f"El bullpen local llega cargado tras {h_bp.get('gamesPlayedRecent', '?')} "
            f"juegos en los últimos {h_bp.get('lookbackDays', 3)} días."
        )
    if (a_bp.get("fatigueScore") or 0) >= 60:
        phrases.append(
            f"El bullpen visitante llega cargado tras {a_bp.get('gamesPlayedRecent', '?')} "
            f"juegos en los últimos {a_bp.get('lookbackDays', 3)} días."
        )
    # Starting pitcher hints
    hs = pitching.get("homeStarter") or {}
    asts = pitching.get("awayStarter") or {}
    if hs.get("era") and hs.get("era") <= 3.0:
        phrases.append(f"Abridor local con ERA elite ({hs['era']:.2f}) en la temporada.")
    if asts.get("era") and asts.get("era") <= 3.0:
        phrases.append(f"Abridor visitante con ERA elite ({asts['era']:.2f}) en la temporada.")
    # Projection vs league
    if projected and league_avg:
        diff = projected - league_avg
        if abs(diff) >= 0.7:
            direction = "por encima" if diff > 0 else "por debajo"
            phrases.append(
                f"La proyección del motor ({projected:.1f}) está {abs(diff):.1f} carreras "
                f"{direction} del promedio de la liga ({league_avg:.1f})."
            )
    # F5
    if f5_lean == "UNDER":
        phrases.append("Los primeros 5 innings históricamente cierran por debajo de la media (lean Under F5).")
    elif f5_lean == "OVER":
        phrases.append("Los primeros 5 innings históricamente cierran por encima de la media (lean Over F5).")
    # H2H
    if h2h_avg and projected:
        if h2h_avg - projected >= 1.5:
            phrases.append(f"Los enfrentamientos directos promediaron {h2h_avg:.1f} carreras — más altos que la proyección actual.")
        elif projected - h2h_avg >= 1.5:
            phrases.append(f"Los enfrentamientos directos promediaron {h2h_avg:.1f} carreras — más bajos que la proyección actual.")
    return phrases[:7]


# ── Public pure compute ─────────────────────────────────────────────────
def compute_baseball_profile(
    home_games_team_view: list[dict],
    away_games_team_view: list[dict],
    *,
    home_season_form:     Optional[dict] = None,
    away_season_form:     Optional[dict] = None,
    home_pitcher_stats:   Optional[dict] = None,
    away_pitcher_stats:   Optional[dict] = None,
    home_pitcher_name:    Optional[str]  = None,
    away_pitcher_name:    Optional[str]  = None,
    home_bullpen_usage:   Optional[dict] = None,
    away_bullpen_usage:   Optional[dict] = None,
    h2h_team_views_pairs: Optional[list[dict]] = None,
    league_name:          Optional[str]  = None,
) -> dict:
    league_avg = _league_runs_baseline(league_name)
    home_block = _team_block(home_games_team_view, season_form=home_season_form,
                              league_avg_runs=league_avg)
    away_block = _team_block(away_games_team_view, season_form=away_season_form,
                              league_avg_runs=league_avg)

    # Pitcher / bullpen advantage labels
    hs = _pitcher_block(home_pitcher_stats, home_pitcher_name)
    asts = _pitcher_block(away_pitcher_stats, away_pitcher_name)
    h_era = hs.get("era") if isinstance(hs.get("era"), (int, float)) else None
    a_era = asts.get("era") if isinstance(asts.get("era"), (int, float)) else None
    if h_era is not None and a_era is not None:
        if h_era + 0.5 < a_era:
            pitcher_adv = "home"
        elif a_era + 0.5 < h_era:
            pitcher_adv = "away"
        else:
            pitcher_adv = "none"
    else:
        pitcher_adv = "none"

    h_bp = _bullpen_block(home_bullpen_usage)
    a_bp = _bullpen_block(away_bullpen_usage)
    h_bp_score = h_bp.get("fatigueScore") or 0
    a_bp_score = a_bp.get("fatigueScore") or 0
    if abs(h_bp_score - a_bp_score) >= 20:
        bullpen_adv = "home" if h_bp_score < a_bp_score else "away"  # less fatigue = advantage
    else:
        bullpen_adv = "none"

    pitching = {
        "homeStarter":       hs,
        "awayStarter":       asts,
        "pitcherAdvantage":  pitcher_adv,
        "bullpenAdvantage":  bullpen_adv,
        "homeBullpen":       h_bp,
        "awayBullpen":       a_bp,
    }

    h2h_avg: Optional[float] = None
    if h2h_team_views_pairs:
        totals = [g.get("total_runs") for g in h2h_team_views_pairs
                  if isinstance(g.get("total_runs"), (int, float))]
        if totals:
            h2h_avg = round(statistics.fmean(totals), 2)

    combined = _build_combined(home_block, away_block, pitching=pitching,
                               h2h_avg=h2h_avg, league_avg_runs=league_avg)

    available = bool(
        (home_block.get("gamesAnalyzed") or 0) >= MIN_GAMES_FOR_PROFILE
        and (away_block.get("gamesAnalyzed") or 0) >= MIN_GAMES_FOR_PROFILE
    )
    return {
        "available":       available,
        "home":            home_block,
        "away":            away_block,
        "pitching":        pitching,
        "combined":        combined,
        "_reason":         "ok" if available else "low_sample",
        "_engine_version": ENGINE_VERSION,
    }


# ── Async I/O helpers (MLB Stats API + API-Sports fallback) ─────────────
async def _fetch_mlb_team_games(
    db: Any, team_id: int, *, days_back: int = 30,
) -> list[dict]:
    """Pull recent finished MLB games via /schedule with linescore hydrated."""
    from .. import mlb_stats_api as _mlb

    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=days_back)).isoformat()
    end   = today.isoformat()
    cache_key = f"mlb-team-games:{team_id}:{start}:{end}"
    if db is not None:
        cached = await _mlb._cache_get(db, cache_key)
        if cached is not None:
            return cached.get("games") or []
    url    = f"{_mlb.BASE}/schedule"
    params = {
        "teamId":    team_id,
        "startDate": start,
        "endDate":   end,
        "sportId":   1,
        "hydrate":   "linescore,team",
    }
    games: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=_mlb.TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.info("MLB team-games fetch failed for %s: %s", team_id, exc)
        return []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            games.append(g)
    if db is not None:
        await _mlb._cache_put(db, cache_key, {"games": games}, _mlb.TTL_TEAM_FORM)
    return games


async def _fetch_apisports_team_games(
    client: httpx.AsyncClient, team_id: int, *, season: Any, league_id: Optional[int], db: Any,
) -> list[dict]:
    from .. import api_sports as _aps

    key = {"sport": "baseball", "kind": "team_games", "team_id": team_id,
           "season": str(season), "league_id": league_id or 0}
    cached = await _aps._cache_get(db, "cache_team_games", key, HISTORICAL_TTL_HOURS * 60)
    if cached is not None:
        return cached
    params = {"team": team_id, "season": season}
    if league_id:
        params["league"] = league_id
    try:
        data = await _aps._get("baseball", client, "/games", params)
    except Exception as exc:
        log.info("[BASEBALL_HIST_API_FAIL] team=%s: %s", team_id, exc)
        return []
    games = data.get("response") or []
    await _aps._cache_set(db, "cache_team_games", key, games)
    return games


# ── Public enrichment ───────────────────────────────────────────────────
async def enrich_baseball_historical_profile(
    match: dict,
    *,
    db: Any = None,
    lookback: int = DEFAULT_LOOKBACK_GAMES,
    timeout_sec: float = 22.0,
) -> dict:
    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    home_id = home.get("id")
    away_id = away.get("id")
    league_name = (match.get("league") or {}).get("name") or "MLB"
    league_id   = (match.get("league") or {}).get("id")
    is_mlb = "mlb" in (league_name or "").lower() or league_id == 1

    if not home_id or not away_id:
        return empty_baseball_profile("missing_team_ids")

    try:
        from .. import api_sports as _aps
        from .. import mlb_stats_api as _mlb

        season = _aps.proxy_season("baseball")

        async with httpx.AsyncClient(timeout=12.0) as client:
            home_games_task: asyncio.Task
            away_games_task: asyncio.Task

            if is_mlb:
                home_games_task = asyncio.create_task(
                    _fetch_mlb_team_games(db, int(home_id), days_back=45)
                )
                away_games_task = asyncio.create_task(
                    _fetch_mlb_team_games(db, int(away_id), days_back=45)
                )
                home_form_task = asyncio.create_task(
                    _mlb.get_team_batting_form(db, int(home_id))
                )
                away_form_task = asyncio.create_task(
                    _mlb.get_team_batting_form(db, int(away_id))
                )
                home_bp_task = asyncio.create_task(
                    _mlb.get_bullpen_recent_usage(db, int(home_id), days=3)
                )
                away_bp_task = asyncio.create_task(
                    _mlb.get_bullpen_recent_usage(db, int(away_id), days=3)
                )
                # GAP #4 — Injured List (per-team), fail-soft.
                home_il_task = asyncio.create_task(
                    _mlb.get_team_il_players(db, int(home_id))
                )
                away_il_task = asyncio.create_task(
                    _mlb.get_team_il_players(db, int(away_id))
                )
                # Pitcher stats (if probable IDs on match)
                h_pid = (match.get("home_probable") or {}).get("id") or match.get("home_probable_id")
                a_pid = (match.get("away_probable") or {}).get("id") or match.get("away_probable_id")
                home_pitcher_task = asyncio.create_task(
                    _mlb.get_pitcher_season_stats(db, h_pid) if h_pid else asyncio.sleep(0, result=None)
                )
                away_pitcher_task = asyncio.create_task(
                    _mlb.get_pitcher_season_stats(db, a_pid) if a_pid else asyncio.sleep(0, result=None)
                )
                h2h_task = asyncio.create_task(
                    _aps.head_to_head("baseball", client, int(home_id), int(away_id), limit=5, db=db)
                )
            else:
                home_games_task = asyncio.create_task(
                    _fetch_apisports_team_games(client, int(home_id), season=season,
                                                 league_id=league_id, db=db)
                )
                away_games_task = asyncio.create_task(
                    _fetch_apisports_team_games(client, int(away_id), season=season,
                                                 league_id=league_id, db=db)
                )
                home_form_task = asyncio.sleep(0, result=None)
                away_form_task = asyncio.sleep(0, result=None)
                home_bp_task   = asyncio.sleep(0, result=None)
                away_bp_task   = asyncio.sleep(0, result=None)
                home_pitcher_task = asyncio.sleep(0, result=None)
                away_pitcher_task = asyncio.sleep(0, result=None)
                home_il_task   = asyncio.sleep(0, result=[])
                away_il_task   = asyncio.sleep(0, result=[])
                h2h_task       = asyncio.create_task(
                    _aps.head_to_head("baseball", client, int(home_id), int(away_id), limit=5, db=db)
                )

            try:
                (home_games, away_games, home_form, away_form,
                 home_bp, away_bp, home_pitcher, away_pitcher,
                 home_il, away_il, h2h_games) = await asyncio.wait_for(
                    asyncio.gather(
                        home_games_task, away_games_task,
                        home_form_task, away_form_task,
                        home_bp_task, away_bp_task,
                        home_pitcher_task, away_pitcher_task,
                        home_il_task, away_il_task,
                        h2h_task,
                        return_exceptions=False,
                    ),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                log.info("[BASEBALL_HIST_TIMEOUT] match=%s", match.get("match_id"))
                return empty_baseball_profile("timeout")
    except Exception as exc:
        log.info("[BASEBALL_HIST_FAILED] match=%s: %s", match.get("match_id"), exc)
        return empty_baseball_profile(f"fetch_error:{type(exc).__name__}")

    home_view = _extract_runs_perspective(home_games, int(home_id), lookback=lookback)
    away_view = _extract_runs_perspective(away_games, int(away_id), lookback=lookback)
    h2h_view  = _extract_runs_perspective(h2h_games or [], int(home_id), lookback=5)

    profile = compute_baseball_profile(
        home_view, away_view,
        home_season_form=home_form, away_season_form=away_form,
        home_pitcher_stats=home_pitcher, away_pitcher_stats=away_pitcher,
        home_pitcher_name=((match.get("home_probable") or {}).get("name")
                            or match.get("home_probable_name")),
        away_pitcher_name=((match.get("away_probable") or {}).get("name")
                            or match.get("away_probable_name")),
        home_bullpen_usage=home_bp, away_bullpen_usage=away_bp,
        h2h_team_views_pairs=h2h_view,
        league_name=league_name,
    )
    # GAP #4 — attach Injured List per team so the UI can render names.
    profile["injuries"] = {
        "home_il_count":   len(home_il or []),
        "away_il_count":   len(away_il or []),
        "home_il_players": list(home_il or []),
        "away_il_players": list(away_il or []),
        "_source":         "MLB Stats API (roster/injuries)",
    }
    return profile


async def prefetch_baseball_profiles(
    matches: list[dict],
    *,
    db: Any = None,
    timeout_sec: float = 30.0,
) -> int:
    real_matches = [
        m for m in matches
        if m and (m.get("sport") or "").lower() == "baseball"
        and (m.get("home_team") or {}).get("id")
        and (m.get("away_team") or {}).get("id")
    ]
    if not real_matches:
        return 0

    enriched = 0

    async def _one(m: dict) -> None:
        nonlocal enriched
        try:
            profile = await enrich_baseball_historical_profile(m, db=db, timeout_sec=22.0)
        except Exception as exc:
            log.debug("baseball hist enrichment crashed match %s: %s",
                      m.get("match_id"), exc)
            profile = empty_baseball_profile("exception")
        m["baseballHistoricalProfile"] = profile
        if profile.get("available"):
            enriched += 1

        # ── Phase 40 / Fix 1 — Box-score hydration (opt-in, default ON).
        # Pulls real per-game AB/H/BB/K/SB + OBP/SLG/ISO so downstream
        # baseball Moneyball layers can use REAL numbers instead of the
        # league-average proxy. Strict per-match timeout + fail-soft.
        # Disable with ``BASEBALL_BOX_SCORES_HYDRATE=0``.
        if os.environ.get("BASEBALL_BOX_SCORES_HYDRATE", "1") != "0":
            try:
                from services.box_score_providers import (
                    hydrate_match_with_box_scores,
                )
                await asyncio.wait_for(
                    hydrate_match_with_box_scores(m, last_n=10),
                    timeout=float(os.environ.get(
                        "BASEBALL_BOX_SCORES_HYDRATE_TIMEOUT_S", "5.0",
                    )),
                )
            except (asyncio.TimeoutError, Exception) as _exc_hydrate:
                log.debug(
                    "baseball box-score hydration skipped for match %s: %s",
                    m.get("match_id"), _exc_hydrate,
                )

    try:
        await asyncio.wait_for(
            asyncio.gather(*[_one(m) for m in real_matches], return_exceptions=True),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        log.info("[BASEBALL_HIST_BULK_TIMEOUT] enriched=%d/%d", enriched, len(real_matches))
    except Exception as exc:
        log.warning("[BASEBALL_HIST_BULK_ERROR] %s", exc)

    log.info("[BASEBALL_HIST_DONE] enriched=%d total=%d", enriched, len(real_matches))
    return enriched


__all__ = [
    "enrich_baseball_historical_profile",
    "compute_baseball_profile",
    "prefetch_baseball_profiles",
    "empty_baseball_profile",
    "ENGINE_VERSION",
    "DEFAULT_LOOKBACK_GAMES",
]
