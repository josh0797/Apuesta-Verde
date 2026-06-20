"""
Sprint D13 — MLB Matchup Familiarity Overlay (pure module).

Overlay contextual que NO toma picks por sí solo. Suma/resta puntos al
modelo principal cuando los equipos se han enfrentado recientemente,
especialmente dentro de los últimos 15 días.

Contract: pure function, no DB, no HTTP, fully deterministic, fail-soft.

Entry-point
-----------
    calculate_matchup_familiarity_overlay(context: dict) -> dict

Input (`context`) — campos esperados:
    home_team               : str
    away_team               : str
    game_date               : "YYYY-MM-DD"  (date of the upcoming game)
    current_pick_market     : "TOTAL" | "MONEYLINE" | "RUNLINE"
    current_pick_side       : "OVER" | "UNDER" | "HOME" | "AWAY"
                              | "HOME_RL" | "AWAY_RL"
    current_line            : float | None    (e.g. 8.5 for totals)
    recent_h2h_games        : list[dict]      (games where these two
                                               teams faced each other,
                                               oldest→newest preferred)
    team_recent_games       : dict | None     (optional context)
    bullpen_usage           : dict | None     (optional context)
    starter_info            : dict | None     (optional context)
    lineups                 : dict | None     (optional context)
    debug                   : bool            (verbose audit; default False)

Each H2H game in `recent_h2h_games` should carry at least:
    {
      "date"        : "YYYY-MM-DD"  (or "kickoff" ISO),
      "home_team"   : str,
      "away_team"   : str,
      "home_score"  : int,                 (or "home" — runs scored home)
      "away_score"  : int,                 (or "away" — runs scored away)
      # Optional enrichment:
      "innings_breakdown"          : list[int] | None,
      "bullpen_pitch_count_home"   : int  | None,
      "bullpen_pitch_count_away"   : int  | None,
      "starter_home"               : str  | None,
      "starter_away"               : str  | None,
    }

Output (always a dict; never raises):
    {
      "available"        : bool,
      "recent_h2h_found" : bool,
      "h2h_window"       : "LAST_3_DAYS" | "LAST_5_DAYS" | "LAST_15_DAYS"
                           | "CURRENT_SEASON" | "LAST_2_YEARS" | "NONE",
      "games_count"      : int,
      "games"            : [<normalized game dicts>],
      "metrics"          : {<H2H metrics — see _compute_h2h_metrics>},
      "familiarity_score": float 0..100,
      "bucket"           : "NONE" | "LOW" | "MEDIUM" | "HIGH",
      "confidence"       : float 0..100,
      "drivers"          : [str],         (positive contributors)
      "missing_fields"   : [str],         (degraded inputs)
      "totals_overlay"   : {
          "lean"         : "OVER" | "UNDER" | "NEUTRAL",
          "points"       : float in [-5.0, +5.0],
          "reason_codes" : [str],
          "summary"      : str,
      },
      "reason_codes"     : [str],         (flat list of all reasons),
    }

Design rules
------------
  * Pure module: no I/O, no globals, no time.now() side effects.
  * Fail-soft: any unexpected input is logged into `missing_fields` and
    the overlay degrades to NEUTRAL (0 points) — never raises.
  * **Observe-only overlay** in this delivery: TOTALS only. ML and RL
    are explicitly handled later (D13.2). This module ALWAYS returns
    a `totals_overlay` block; ML/RL overlays are out of scope for D13.
  * The overlay can never tilt a pick by more than ±5 points. Net
    points are clamped to [-5, +5] AFTER aggregation.
  * Games > 16 days old DO NOT contribute to any window calculation
    (they may still appear in the `LAST_2_YEARS` fallback flag, but
    they ARE NOT used to compute metrics or score points).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Constants ─────────────────────────────────────────────────────────
WINDOW_3_DAYS  = "LAST_3_DAYS"
WINDOW_5_DAYS  = "LAST_5_DAYS"
WINDOW_15_DAYS = "LAST_15_DAYS"
WINDOW_SEASON  = "CURRENT_SEASON"
WINDOW_2_YEARS = "LAST_2_YEARS"
WINDOW_NONE    = "NONE"

BUCKET_NONE   = "NONE"
BUCKET_LOW    = "LOW"
BUCKET_MEDIUM = "MEDIUM"
BUCKET_HIGH   = "HIGH"

LEAN_OVER    = "OVER"
LEAN_UNDER   = "UNDER"
LEAN_NEUTRAL = "NEUTRAL"

# Hard cap on overlay impact (per spec — Section 4 safety rules).
MAX_OVERLAY_POINTS = 5.0

# Hard date cap: games older than this from `game_date` are EXCLUDED
# from all metric computations (per spec — "No implementar partidos
# mayores a 16 días").
HARD_DATE_CAP_DAYS = 16

# Window thresholds (inclusive on lower bound, exclusive on upper).
WIN_3_DAYS_THRESHOLD  = 3
WIN_5_DAYS_THRESHOLD  = 5
WIN_15_DAYS_THRESHOLD = 15


# ─────────────────────────────────────────────────────────────────────
# Helpers — parsing & normalization
# ─────────────────────────────────────────────────────────────────────
def _parse_date(value: Any) -> Optional[datetime]:
    """Parse a date-ish value into a tz-aware UTC datetime. Returns None
    on failure (never raises).
    """
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Common formats: "YYYY-MM-DD", ISO with T, ISO with timezone.
        # Try ISO first.
        try:
            # Allow trailing 'Z'.
            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass
        # YYYY-MM-DD only.
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def _normalize_game(
    g: Dict[str, Any],
    ctx_home: str,
    ctx_away: str,
) -> Optional[Dict[str, Any]]:
    """Normalize a single H2H game dict into canonical shape.

    Returns None when the game is unusable (missing essentials).
    Accepts multiple input shapes:
      * games_detail from mlb_active_series_analyzer
        ({home, away, home_team, away_team, total_runs, kickoff, …})
      * spec-shaped dicts
        ({home_score, away_score, date, …})
    """
    if not isinstance(g, dict):
        return None

    # Date
    dt = _parse_date(g.get("date") or g.get("kickoff") or g.get("game_date"))
    if dt is None:
        return None

    # Teams (best-effort match against context teams — but we don't
    # reject games where team names are swapped; we just record them
    # as-is and align scoring below).
    home_team = g.get("home_team") or g.get("homeTeam") or g.get("home_name") or ctx_home
    away_team = g.get("away_team") or g.get("awayTeam") or g.get("away_name") or ctx_away

    # Scores — try multiple keys.
    home_score = (
        _safe_int(g.get("home_score"))
        if g.get("home_score") is not None
        else _safe_int(g.get("home"))
        if g.get("home") is not None
        else _safe_int(g.get("homeRuns"))
    )
    away_score = (
        _safe_int(g.get("away_score"))
        if g.get("away_score") is not None
        else _safe_int(g.get("away"))
        if g.get("away") is not None
        else _safe_int(g.get("awayRuns"))
    )
    if home_score is None or away_score is None:
        return None
    if home_score < 0 or away_score < 0:
        return None

    total_runs = home_score + away_score
    if g.get("total_runs") is not None:
        # Trust explicit total if present and consistent.
        t = _safe_int(g["total_runs"])
        if t is not None and t == total_runs:
            total_runs = t

    margin = home_score - away_score
    if margin > 0:
        winner = home_team
    elif margin < 0:
        winner = away_team
    else:
        winner = "TIE"

    return {
        "date":                       dt.strftime("%Y-%m-%d"),
        "kickoff":                    dt.isoformat(),
        "home_team":                  home_team,
        "away_team":                  away_team,
        "home_score":                 home_score,
        "away_score":                 away_score,
        "total_runs":                 total_runs,
        "winner":                     winner,
        "run_margin":                 abs(margin),
        "innings_breakdown":          g.get("innings_breakdown") or g.get("innings"),
        "bullpen_pitch_count_home":   _safe_int(g.get("bullpen_pitch_count_home")),
        "bullpen_pitch_count_away":   _safe_int(g.get("bullpen_pitch_count_away")),
        "starter_home":               g.get("starter_home") or g.get("home_starter"),
        "starter_away":               g.get("starter_away") or g.get("away_starter"),
    }


def _classify_window(
    games: List[Dict[str, Any]],
    ref_date: datetime,
) -> str:
    """Given a list of normalized games (all within ≤16 days of ref),
    plus the reference date, return the tightest window where at least
    1 game exists.
    """
    if not games:
        return WINDOW_NONE
    deltas = []
    for g in games:
        dt = _parse_date(g.get("kickoff") or g.get("date"))
        if dt is None:
            continue
        # ref_date is the upcoming game's date; we only count games
        # STRICTLY BEFORE ref_date (no self-counting).
        delta_days = (ref_date - dt).total_seconds() / 86400.0
        if delta_days <= 0:
            continue
        deltas.append(delta_days)
    if not deltas:
        return WINDOW_NONE
    min_delta = min(deltas)
    if min_delta < WIN_3_DAYS_THRESHOLD:
        return WINDOW_3_DAYS
    if min_delta < WIN_5_DAYS_THRESHOLD:
        return WINDOW_5_DAYS
    if min_delta < WIN_15_DAYS_THRESHOLD:
        return WINDOW_15_DAYS
    if min_delta < HARD_DATE_CAP_DAYS:
        # Within 15-16 day grace — still treated as "current season"
        # window for classification purposes.
        return WINDOW_SEASON
    return WINDOW_SEASON


def _filter_relevant_games(
    games: List[Dict[str, Any]],
    ref_date: datetime,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split games into:
      * `relevant`: kickoff strictly before ref_date AND within
                    HARD_DATE_CAP_DAYS (≤16 days back).
      * `older`:    kickoff before ref_date but older than the cap.

    Games with future/same-day kickoff are dropped (would be the same
    upcoming matchup or a future game — not H2H history).
    """
    relevant: List[Dict[str, Any]] = []
    older: List[Dict[str, Any]] = []
    for g in games:
        dt = _parse_date(g.get("kickoff") or g.get("date"))
        if dt is None:
            continue
        delta_days = (ref_date - dt).total_seconds() / 86400.0
        if delta_days <= 0:
            # Future or same-day game — skip.
            continue
        if delta_days < HARD_DATE_CAP_DAYS:
            relevant.append(g)
        else:
            older.append(g)
    return relevant, older


# ─────────────────────────────────────────────────────────────────────
# Section 2 — H2H metrics
# ─────────────────────────────────────────────────────────────────────
def _over_under_rates(
    totals: List[int],
    lines: Tuple[float, ...] = (7.5, 8.5, 9.5, 10.5, 11.5),
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Compute over_rate / under_rate by line. PUSH (rare in MLB but
    possible with whole-number lines) doesn't apply for .5 lines.
    """
    over_map: Dict[str, float] = {}
    under_map: Dict[str, float] = {}
    if not totals:
        return over_map, under_map
    n = len(totals)
    for ln in lines:
        ovr = sum(1 for t in totals if t > ln)
        und = sum(1 for t in totals if t < ln)
        key_o = f"over_{str(ln).replace('.', '_')}"
        key_u = f"under_{str(ln).replace('.', '_')}"
        over_map[key_o] = round(ovr / n, 4)
        under_map[key_u] = round(und / n, 4)
    return over_map, under_map


def _compute_h2h_metrics(
    games: List[Dict[str, Any]],
    ctx_home: str,
    ctx_away: str,
) -> Dict[str, Any]:
    """Compute the Section-2 metrics over the normalized, relevant games.

    Per-team runs are aligned to the CURRENT matchup's home/away
    (so if a past game had the teams flipped, scoring is reattributed).
    """
    if not games:
        return {
            "avg_total_runs_h2h":   None,
            "median_total_runs_h2h": None,
            "max_total_runs_h2h":   None,
            "min_total_runs_h2h":   None,
            "over_rate_by_line":    {},
            "under_rate_by_line":   {},
            "avg_home_runs_scored": None,
            "avg_away_runs_scored": None,
            "avg_margin":           None,
            "home_win_rate_h2h":    None,
            "away_win_rate_h2h":    None,
            "runline_cover_rate_home_minus_1_5": None,
            "runline_cover_rate_away_minus_1_5": None,
        }

    totals = [g["total_runs"] for g in games]
    n = len(totals)
    # Median
    s_sorted = sorted(totals)
    if n % 2 == 1:
        median = float(s_sorted[n // 2])
    else:
        median = (s_sorted[n // 2 - 1] + s_sorted[n // 2]) / 2.0

    # Per-team runs aligned to CURRENT home/away.
    ctx_home_l = (ctx_home or "").lower().strip()
    ctx_away_l = (ctx_away or "").lower().strip()
    home_runs_scored: List[int] = []
    away_runs_scored: List[int] = []
    home_wins = 0
    away_wins = 0
    home_rl_cover = 0
    away_rl_cover = 0
    margins: List[int] = []
    for g in games:
        gh_l = (g["home_team"] or "").lower().strip()
        if gh_l == ctx_home_l:
            # Past game had same orientation.
            r_for_home = g["home_score"]
            r_for_away = g["away_score"]
        elif gh_l == ctx_away_l:
            # Past game had teams swapped.
            r_for_home = g["away_score"]
            r_for_away = g["home_score"]
        else:
            # Unknown alignment — best-effort: trust the original.
            r_for_home = g["home_score"]
            r_for_away = g["away_score"]
        home_runs_scored.append(r_for_home)
        away_runs_scored.append(r_for_away)
        margin = r_for_home - r_for_away
        margins.append(margin)
        if margin > 0:
            home_wins += 1
            # Home -1.5 cover: home wins by ≥2.
            if margin >= 2:
                home_rl_cover += 1
        elif margin < 0:
            away_wins += 1
            if -margin >= 2:
                away_rl_cover += 1
        # Ties don't count for win rates.

    over_map, under_map = _over_under_rates(totals)

    return {
        "avg_total_runs_h2h":               round(sum(totals) / n, 2),
        "median_total_runs_h2h":            round(median, 2),
        "max_total_runs_h2h":               max(totals),
        "min_total_runs_h2h":               min(totals),
        "over_rate_by_line":                over_map,
        "under_rate_by_line":               under_map,
        "avg_home_runs_scored":             round(sum(home_runs_scored) / n, 2),
        "avg_away_runs_scored":             round(sum(away_runs_scored) / n, 2),
        "avg_margin":                       round(sum(margins) / n, 2),
        "home_win_rate_h2h":                round(home_wins / n, 4),
        "away_win_rate_h2h":                round(away_wins / n, 4),
        "runline_cover_rate_home_minus_1_5": round(home_rl_cover / n, 4),
        "runline_cover_rate_away_minus_1_5": round(away_rl_cover / n, 4),
    }


# ─────────────────────────────────────────────────────────────────────
# Section 3 — Familiarity score
# ─────────────────────────────────────────────────────────────────────
def _count_in_window(
    games: List[Dict[str, Any]],
    ref_date: datetime,
    max_days: float,
) -> int:
    n = 0
    for g in games:
        dt = _parse_date(g.get("kickoff") or g.get("date"))
        if dt is None:
            continue
        delta_days = (ref_date - dt).total_seconds() / 86400.0
        if 0 < delta_days < max_days:
            n += 1
    return n


def _consecutive_days(
    games: List[Dict[str, Any]],
    ref_date: datetime,
) -> bool:
    """True if there is at least one pair of games on consecutive days
    within the 5-day window (signals an active multi-game series).
    """
    days = set()
    for g in games:
        dt = _parse_date(g.get("kickoff") or g.get("date"))
        if dt is None:
            continue
        delta_days = (ref_date - dt).total_seconds() / 86400.0
        if 0 < delta_days < 5:
            days.add(dt.strftime("%Y-%m-%d"))
    if len(days) < 2:
        return False
    sorted_days = sorted(days)
    for i in range(len(sorted_days) - 1):
        d1 = datetime.strptime(sorted_days[i], "%Y-%m-%d")
        d2 = datetime.strptime(sorted_days[i + 1], "%Y-%m-%d")
        if (d2 - d1).days == 1:
            return True
    return False


def _compute_familiarity_score(
    games: List[Dict[str, Any]],
    ref_date: datetime,
    older_games: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Section-3 score 0..100 + bucket + drivers + confidence."""
    n_15 = _count_in_window(games, ref_date, 15)
    n_5  = _count_in_window(games, ref_date, 5)
    n_3  = _count_in_window(games, ref_date, 3)

    drivers: List[str] = []
    score = 0.0

    # Base score by count in 15-day window.
    if n_15 == 0:
        # No relevant games — fall back to older signal if any.
        if older_games:
            score += 5.0
            drivers.append("OLDER_H2H_ONLY")
            bucket = BUCKET_LOW
        else:
            bucket = BUCKET_NONE
        # Early return — minimal score, no boosts.
        return {
            "familiarity_score": round(min(score, 100.0), 1),
            "bucket":            bucket,
            "drivers":           drivers,
            "n_recent":          {"d3": n_3, "d5": n_5, "d15": n_15},
        }

    # 1+ games in 15-day window — apply scaled base + boosts.
    if n_15 >= 3:
        score += 70.0
        drivers.append("THREE_PLUS_RECENT_15D")
    elif n_15 == 2:
        score += 50.0
        drivers.append("TWO_RECENT_15D")
    elif n_15 == 1:
        score += 30.0
        drivers.append("ONE_RECENT_15D")

    # Consecutive-day boost (active series).
    if _consecutive_days(games, ref_date):
        score += 12.0
        drivers.append("ACTIVE_SERIES_CONSECUTIVE")

    # Yesterday/day-before boost.
    if n_3 >= 1:
        score += 10.0
        drivers.append("PLAYED_LAST_3_DAYS")

    # Same-series boost (heuristic): if 2+ games within last 5 days
    # AND the dates form a contiguous block.
    if n_5 >= 2 and _consecutive_days(games, ref_date):
        score += 5.0
        drivers.append("SAME_ACTIVE_SERIES")

    # Bullpen exposure boost (any recent game with bullpen pitch count
    # ≥ 60 from at least one side).
    for g in games:
        bph = g.get("bullpen_pitch_count_home") or 0
        bpa = g.get("bullpen_pitch_count_away") or 0
        if bph >= 60 or bpa >= 60:
            score += 4.0
            drivers.append("BULLPEN_EXPOSED_RECENT")
            break

    # Same starter recently boost.
    starter_info = context.get("starter_info") or {}
    home_starter = (starter_info.get("home") or {}).get("name") if isinstance(starter_info.get("home"), dict) else starter_info.get("home_starter")
    away_starter = (starter_info.get("away") or {}).get("name") if isinstance(starter_info.get("away"), dict) else starter_info.get("away_starter")
    for g in games:
        gh_s = (g.get("starter_home") or "").strip().lower()
        ga_s = (g.get("starter_away") or "").strip().lower()
        if home_starter and (gh_s == str(home_starter).strip().lower()
                              or ga_s == str(home_starter).strip().lower()):
            score += 4.0
            drivers.append("SAME_STARTER_RECENT_HOME")
            break
    for g in games:
        gh_s = (g.get("starter_home") or "").strip().lower()
        ga_s = (g.get("starter_away") or "").strip().lower()
        if away_starter and (gh_s == str(away_starter).strip().lower()
                              or ga_s == str(away_starter).strip().lower()):
            score += 4.0
            drivers.append("SAME_STARTER_RECENT_AWAY")
            break

    # Lineup similarity boost (heuristic — only when lineups provided).
    lineups = context.get("lineups") or {}
    h_lineup = (lineups.get("home") or {}).get("batters") if isinstance(lineups.get("home"), dict) else None
    a_lineup = (lineups.get("away") or {}).get("batters") if isinstance(lineups.get("away"), dict) else None
    if h_lineup and a_lineup and isinstance(h_lineup, list) and isinstance(a_lineup, list):
        # Heuristic: presence of lineup data alone is a small signal of
        # familiarity (because we have something to compare). The deeper
        # similarity comparison is intentionally out of scope here.
        score += 2.0
        drivers.append("LINEUPS_AVAILABLE")

    # High-traffic boost: any recent game with total ≥ 10 runs.
    for g in games:
        if g.get("total_runs", 0) >= 10:
            score += 3.0
            drivers.append("HIGH_TRAFFIC_RECENT_GAME")
            break

    score = max(0.0, min(score, 100.0))

    # Bucket from score (HIGH only with 3+ games; MEDIUM with 1-2; LOW
    # otherwise).
    if n_15 >= 3 or score >= 80.0:
        bucket = BUCKET_HIGH
    elif 1 <= n_15 <= 2:
        bucket = BUCKET_MEDIUM
    else:
        bucket = BUCKET_LOW

    return {
        "familiarity_score": round(score, 1),
        "bucket":            bucket,
        "drivers":           drivers,
        "n_recent":          {"d3": n_3, "d5": n_5, "d15": n_15},
    }


# ─────────────────────────────────────────────────────────────────────
# Section 4 — Totals overlay (OVER/UNDER points)
# ─────────────────────────────────────────────────────────────────────
def _line_key(line: float, prefix: str) -> Optional[str]:
    """Return e.g. 'over_8_5' / 'under_8_5'. Only standard .5 lines
    map to keys; otherwise None.
    """
    if line is None:
        return None
    try:
        f = float(line)
    except (TypeError, ValueError):
        return None
    # Find closest .5 increment in our supported set.
    for ln in (7.5, 8.5, 9.5, 10.5, 11.5):
        if abs(f - ln) < 0.05:
            return f"{prefix}_{str(ln).replace('.', '_')}"
    return None


def _bullpen_exposed_in_h2h(games: List[Dict[str, Any]]) -> bool:
    """A recent H2H game shows bullpen exposure from at least one side
    (pitch count ≥ 50 for HOME or AWAY bullpen)."""
    for g in games:
        bph = g.get("bullpen_pitch_count_home")
        bpa = g.get("bullpen_pitch_count_away")
        if (bph or 0) >= 50 or (bpa or 0) >= 50:
            return True
    return False


def _both_scored_4plus_in_n_games(
    games: List[Dict[str, Any]],
    n: int = 2,
) -> bool:
    """True if at least `n` recent games saw BOTH sides score ≥4."""
    cnt = 0
    for g in games:
        if (g.get("home_score") or 0) >= 4 and (g.get("away_score") or 0) >= 4:
            cnt += 1
    return cnt >= n


def _compute_totals_overlay(
    games: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    current_pick_market: str,
    current_pick_side: str,
    current_line: Optional[float],
    bullpen_usage: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute the OVER/UNDER overlay block.

    Returns ALWAYS a dict — even when the pick is not a TOTAL (in that
    case the overlay is NEUTRAL with explanation).
    """
    if (current_pick_market or "").upper() != "TOTAL":
        return {
            "lean":         LEAN_NEUTRAL,
            "points":       0.0,
            "reason_codes": [],
            "summary":      "Overlay solo activo en mercados Totales.",
        }

    if not games or metrics.get("avg_total_runs_h2h") is None:
        return {
            "lean":         LEAN_NEUTRAL,
            "points":       0.0,
            "reason_codes": [],
            "summary":      "Sin enfrentamientos recientes — overlay neutral.",
        }

    avg_runs = float(metrics["avg_total_runs_h2h"])
    line = _safe_float(current_line)

    over_points = 0.0
    under_points = 0.0
    reasons: List[str] = []

    # ── OVER triggers ────────────────────────────────────────────────
    if line is not None and avg_runs >= line + 1.0:
        over_points += 2.0
        reasons.append("H2H_AVG_RUNS_SUPPORTS_OVER")

    over_key = _line_key(line, "over") if line is not None else None
    if over_key and over_key in metrics.get("over_rate_by_line", {}):
        rate = metrics["over_rate_by_line"][over_key]
        if rate >= 0.65:
            over_points += 2.0
            reasons.append("RECENT_H2H_OVER_RATE_HIGH")

    # 2+ recent games with 10+ runs.
    high_runs_games = sum(1 for g in games if g.get("total_runs", 0) >= 10)
    if high_runs_games >= 2:
        over_points += 2.0
        reasons.append("MULTIPLE_HIGH_RUN_GAMES_RECENT")

    # Bullpen exposed in recent series — escalating bonus 1..3.
    bp_exposed = _bullpen_exposed_in_h2h(games)
    if bp_exposed:
        bp_pts = 1.0
        # If we also have current bullpen_usage flagging fatigue, scale up.
        if isinstance(bullpen_usage, dict):
            fat_h = (bullpen_usage.get("home") or {}).get("bullpen_fatigue") if isinstance(bullpen_usage.get("home"), dict) else None
            fat_a = (bullpen_usage.get("away") or {}).get("bullpen_fatigue") if isinstance(bullpen_usage.get("away"), dict) else None
            try:
                fat_max = max(float(fat_h or 0), float(fat_a or 0))
            except (TypeError, ValueError):
                fat_max = 0.0
            if fat_max >= 0.70:
                bp_pts = 3.0
            elif fat_max >= 0.40:
                bp_pts = 2.0
        over_points += bp_pts
        reasons.append("BULLPEN_EXPOSED_IN_RECENT_SERIES")

    # Both teams scored ≥4 in 2+ recent games → "offensive adaptation".
    if _both_scored_4plus_in_n_games(games, n=2):
        over_points += 1.0
        reasons.append("SERIES_OFFENSIVE_ADAPTATION_OVER")

    # ── UNDER triggers ───────────────────────────────────────────────
    if line is not None and avg_runs <= line - 1.0:
        under_points += 2.0
        reasons.append("H2H_AVG_RUNS_SUPPORTS_UNDER")

    under_key = _line_key(line, "under") if line is not None else None
    if under_key and under_key in metrics.get("under_rate_by_line", {}):
        rate = metrics["under_rate_by_line"][under_key]
        if rate >= 0.65:
            under_points += 2.0
            reasons.append("RECENT_H2H_UNDER_RATE_HIGH")

    # 2+ recent games with 7 or fewer total runs.
    low_runs_games = sum(1 for g in games if g.get("total_runs", 0) <= 7)
    if low_runs_games >= 2:
        under_points += 2.0
        reasons.append("MULTIPLE_LOW_RUN_GAMES_RECENT")

    # Low traffic — neither team scored more than 3 in 2+ recent games.
    low_traffic_games = sum(
        1 for g in games
        if (g.get("home_score") or 0) <= 3 and (g.get("away_score") or 0) <= 3
    )
    if low_traffic_games >= 2:
        under_points += 1.0
        reasons.append("SERIES_LOW_TRAFFIC_UNDER")

    # Familiarity suppresses offense (heuristic): avg margin tight AND
    # avg runs ≤ line - 0.5 (close, low-scoring affairs).
    if line is not None and metrics.get("avg_margin") is not None:
        if abs(metrics["avg_margin"]) <= 2.0 and avg_runs <= line - 0.5:
            under_points += 1.0
            reasons.append("FAMILIARITY_SUPPRESSES_OFFENSE")

    # ── Aggregate ────────────────────────────────────────────────────
    net = over_points - under_points
    # Clamp.
    net = max(-MAX_OVERLAY_POINTS, min(MAX_OVERLAY_POINTS, net))

    if net > 0.5:
        lean = LEAN_OVER
    elif net < -0.5:
        lean = LEAN_UNDER
    else:
        lean = LEAN_NEUTRAL

    # Summary string in Spanish.
    if lean == LEAN_OVER:
        summary = (
            f"Familiaridad reciente favorece OVER: avg total H2H = "
            f"{avg_runs:.2f}"
            + (f" vs línea {line}." if line is not None else ".")
        )
    elif lean == LEAN_UNDER:
        summary = (
            f"Familiaridad reciente favorece UNDER: avg total H2H = "
            f"{avg_runs:.2f}"
            + (f" vs línea {line}." if line is not None else ".")
        )
    else:
        summary = "Familiaridad reciente: señal neutral en totales."

    # If current pick side doesn't align with lean → the overlay still
    # publishes its lean but the net points are kept (consumers decide
    # whether to add or subtract relative to their pick polarity).
    return {
        "lean":         lean,
        "points":       round(net, 2),
        "over_points":  round(over_points, 2),
        "under_points": round(under_points, 2),
        "reason_codes": reasons,
        "summary":      summary,
    }


# ─────────────────────────────────────────────────────────────────────
# Confidence
# ─────────────────────────────────────────────────────────────────────
def _compute_confidence(
    n_relevant: int,
    missing_fields: List[str],
) -> float:
    """Confidence 0..100 based on sample size and data completeness."""
    if n_relevant == 0:
        return 0.0
    # Base by sample.
    if n_relevant >= 3:
        base = 70.0
    elif n_relevant == 2:
        base = 50.0
    else:
        base = 35.0
    # Each missing field reduces confidence by 7 points (cap at 5 fields).
    penalty = min(5, len(missing_fields)) * 7.0
    return round(max(0.0, base - penalty), 1)


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def _neutral_payload(
    missing_fields: List[str],
    window: str = WINDOW_NONE,
    reason_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "available":         True,
        "recent_h2h_found":  False,
        "h2h_window":        window,
        "games_count":       0,
        "games":             [],
        "metrics":           _compute_h2h_metrics([], "", ""),
        "familiarity_score": 0.0,
        "bucket":            BUCKET_NONE,
        "confidence":        0.0,
        "drivers":           [],
        "missing_fields":    list(missing_fields or []),
        "totals_overlay": {
            "lean":         LEAN_NEUTRAL,
            "points":       0.0,
            "reason_codes": [],
            "summary":      "Sin enfrentamientos recientes — overlay neutral.",
        },
        "reason_codes":      list(reason_codes or []),
    }


def calculate_matchup_familiarity_overlay(
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Public entry-point. Always returns a dict, never raises."""
    try:
        if not isinstance(context, dict):
            return _neutral_payload(["INVALID_CONTEXT_TYPE"])

        home_team = (context.get("home_team") or "").strip()
        away_team = (context.get("away_team") or "").strip()
        if not home_team or not away_team:
            return _neutral_payload(["MISSING_TEAM_NAMES"])

        game_date_raw = context.get("game_date")
        ref_date = _parse_date(game_date_raw)
        if ref_date is None:
            # Fallback — caller did not pass a parseable date; we still
            # compute but with current UTC midnight.
            ref_date = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )

        current_pick_market = (context.get("current_pick_market") or "").upper()
        current_pick_side   = (context.get("current_pick_side") or "").upper()
        current_line        = _safe_float(context.get("current_line"))

        raw_h2h = context.get("recent_h2h_games") or []
        if not isinstance(raw_h2h, list):
            return _neutral_payload(["RECENT_H2H_NOT_LIST"])

        # Normalize.
        normalized: List[Dict[str, Any]] = []
        skipped = 0
        for g in raw_h2h:
            ng = _normalize_game(g, home_team, away_team)
            if ng is None:
                skipped += 1
            else:
                normalized.append(ng)

        # Filter by date window.
        relevant, older = _filter_relevant_games(normalized, ref_date)
        relevant.sort(key=lambda g: g.get("kickoff") or "")

        missing_fields: List[str] = []
        if skipped > 0:
            missing_fields.append(f"H2H_SKIPPED_INVALID_{skipped}")
        if not (context.get("bullpen_usage") or {}):
            missing_fields.append("BULLPEN_USAGE_MISSING")
        if not (context.get("starter_info") or {}):
            missing_fields.append("STARTER_INFO_MISSING")
        if not (context.get("lineups") or {}):
            missing_fields.append("LINEUPS_MISSING")

        if not relevant and not older:
            payload = _neutral_payload(missing_fields)
            payload["games_count"] = 0
            return payload

        # Window classification (uses only `relevant` ≤16d set).
        window = _classify_window(relevant, ref_date) if relevant else (
            WINDOW_2_YEARS if older else WINDOW_NONE
        )

        # Section 2 — metrics on relevant only.
        metrics = _compute_h2h_metrics(relevant, home_team, away_team)

        # Section 3 — familiarity score.
        score_block = _compute_familiarity_score(
            relevant, ref_date, older, context,
        )

        # Section 4 — totals overlay.
        totals_overlay = _compute_totals_overlay(
            relevant,
            metrics,
            current_pick_market,
            current_pick_side,
            current_line,
            context.get("bullpen_usage"),
        )

        # Confidence.
        confidence = _compute_confidence(len(relevant), missing_fields)

        # Reason codes aggregation.
        flat_reasons: List[str] = []
        for rc in score_block.get("drivers", []):
            if rc not in flat_reasons:
                flat_reasons.append(rc)
        for rc in totals_overlay.get("reason_codes", []):
            if rc not in flat_reasons:
                flat_reasons.append(rc)

        return {
            "available":         True,
            "recent_h2h_found":  bool(relevant),
            "h2h_window":        window,
            "games_count":       len(relevant),
            "games":             relevant,
            "metrics":           metrics,
            "familiarity_score": score_block.get("familiarity_score", 0.0),
            "bucket":            score_block.get("bucket", BUCKET_NONE),
            "confidence":        confidence,
            "drivers":           score_block.get("drivers", []),
            "missing_fields":    missing_fields,
            "totals_overlay":    totals_overlay,
            "reason_codes":      flat_reasons,
        }

    except Exception as exc:  # noqa: BLE001 — fail-soft contract
        return _neutral_payload([f"EXCEPTION:{type(exc).__name__}:{exc}"])


__all__ = [
    "calculate_matchup_familiarity_overlay",
    "WINDOW_3_DAYS",
    "WINDOW_5_DAYS",
    "WINDOW_15_DAYS",
    "WINDOW_SEASON",
    "WINDOW_2_YEARS",
    "WINDOW_NONE",
    "BUCKET_NONE",
    "BUCKET_LOW",
    "BUCKET_MEDIUM",
    "BUCKET_HIGH",
    "LEAN_OVER",
    "LEAN_UNDER",
    "LEAN_NEUTRAL",
    "MAX_OVERLAY_POINTS",
    "HARD_DATE_CAP_DAYS",
]
