"""Basketball Possession & Four Factors Layer (Phase 37).

This module turns raw per-game basketball box-score data into an
explainable, market-aware profile built around the **Dean Oliver** Four
Factors plus possession-based efficiency.

Goals:
  • Why does a team score / concede points? → eFG, TOV, ORB, FTr.
  • How fast does the game play? → possessions / 48 min.
  • How efficient is each side per possession? → ORtg / DRtg / NetRtg.
  • What does that imply for Moneyline / Spread / Total Points /
    Team Totals?

The module is **strictly fail-soft**:
  * if box-score data is missing → returns ``available:false`` and
    surfaces a single ``DATA_INSUFFICIENT_FALLBACK`` reason code.
  * everything is computed from in-memory dicts (no I/O). The caller
    is responsible for hydrating ``team_games`` from the existing
    historical pipeline (``basketball_historical`` /
    ``basketball_pace_layer``).

Output shape (stable contract for analyst_engine + UI):

    {
      "basketball_possession_profile": {
        "available": bool,
        "home":    {<per-team metrics>},
        "away":    {<per-team metrics>},
        "matchup": {
          "projected_possessions":  float,
          "projected_total_points": float,
          "projected_margin":       float,
          "pace_environment":       "LOW"|"MODERATE"|"HIGH",
          "efficiency_edge":        "home"|"away"|"neutral",
          "total_points_lean":      "OVER"|"UNDER"|"NEUTRAL",
          "spread_support":         "home"|"away"|"none",
          "fragility_score":        0..100,
          "reason_codes":           [str, ...],
          "summary":                str_es,
        },
        "_engine_version": "basketball-possession.1",
        "_reason":         str,   # only when available=false
      }
    }

Reason codes (constants below) mirror the user's spec.
"""
from __future__ import annotations

import logging
import math
import statistics
from typing import Any, Iterable, Optional

log = logging.getLogger("basketball.possession")

ENGINE_VERSION = "basketball-possession.1"

# ────────────────────────────────────────────────────────────────────
# Reason codes (the analyst_engine + UI rely on these literal strings)
# ────────────────────────────────────────────────────────────────────
RC_HIGH_PACE_ENVIRONMENT       = "HIGH_PACE_ENVIRONMENT"
RC_LOW_PACE_ENVIRONMENT        = "LOW_PACE_ENVIRONMENT"
RC_STRONG_OFFENSIVE_RATING     = "STRONG_OFFENSIVE_RATING_EDGE"
RC_STRONG_DEFENSIVE_RATING     = "STRONG_DEFENSIVE_RATING_EDGE"
RC_THREE_POINT_VARIANCE_RISK   = "THREE_POINT_VARIANCE_RISK"
RC_TURNOVER_RISK               = "TURNOVER_RISK"
RC_OFFENSIVE_REBOUND_EDGE      = "OFFENSIVE_REBOUND_EDGE"
RC_FREE_THROW_RATE_SUPPORT     = "FREE_THROW_RATE_SUPPORT"
RC_SPREAD_MARGIN_SUPPORTED     = "SPREAD_MARGIN_SUPPORTED"
RC_MONEYLINE_SAFER_THAN_SPREAD = "MONEYLINE_SAFER_THAN_SPREAD"
RC_TOTAL_OVER_SUPPORTED        = "TOTAL_OVER_SUPPORTED"
RC_TOTAL_UNDER_SUPPORTED       = "TOTAL_UNDER_SUPPORTED"
RC_DATA_INSUFFICIENT_FALLBACK  = "DATA_INSUFFICIENT_FALLBACK"

ALL_REASON_CODES = (
    RC_HIGH_PACE_ENVIRONMENT,
    RC_LOW_PACE_ENVIRONMENT,
    RC_STRONG_OFFENSIVE_RATING,
    RC_STRONG_DEFENSIVE_RATING,
    RC_THREE_POINT_VARIANCE_RISK,
    RC_TURNOVER_RISK,
    RC_OFFENSIVE_REBOUND_EDGE,
    RC_FREE_THROW_RATE_SUPPORT,
    RC_SPREAD_MARGIN_SUPPORTED,
    RC_MONEYLINE_SAFER_THAN_SPREAD,
    RC_TOTAL_OVER_SUPPORTED,
    RC_TOTAL_UNDER_SUPPORTED,
    RC_DATA_INSUFFICIENT_FALLBACK,
)

# ────────────────────────────────────────────────────────────────────
# Thresholds (calibrated for league-agnostic use; caller can override
# via ``league_baseline`` if needed)
# ────────────────────────────────────────────────────────────────────
MIN_GAMES_FULL_PROFILE       = 4    # below this we mark fragility high
MIN_GAMES_ANY_PROFILE        = 1
PACE_HIGH_THRESHOLD          = 102.0  # poss/48
PACE_LOW_THRESHOLD            = 94.0
NET_RATING_STRONG_EDGE        = 4.0   # |net_home - net_away| ≥ 4 → spread support
NET_RATING_DOMINANT_EDGE      = 8.0   # ≥8 → moneyline clearly safer than spread
EFG_LOW_THRESHOLD             = 0.490
EFG_HIGH_THRESHOLD            = 0.555
TOV_HIGH_THRESHOLD            = 0.155
ORB_HIGH_THRESHOLD            = 0.300
FTR_HIGH_THRESHOLD            = 0.275
THREE_PA_RATE_HIGH            = 0.42  # 42%+ of FGA from 3
THREE_P_PCT_VARIANCE_HIGH     = 0.06  # stdev of 3P% across sample
TOTAL_POINTS_STD_HIGH         = 12.0
MIN_TOTAL_POINTS_LEAN_DELTA   = 4.5
MIN_SPREAD_MARGIN_BUFFER      = 1.5   # projected margin must beat line by this

# Default league baseline (used when caller does not provide one).
DEFAULT_LEAGUE_BASELINE = {
    "pace":               99.0,
    "offensive_rating":  110.0,
    "league_total":      210.0,
}


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────
def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_mean(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (_safe_float(x) for x in values) if v is not None]
    return statistics.fmean(nums) if nums else None


def _safe_stdev(values: Iterable[Any]) -> Optional[float]:
    nums = [v for v in (_safe_float(x) for x in values) if v is not None]
    if len(nums) < 2:
        return 0.0 if nums else None
    return statistics.pstdev(nums)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def empty_profile(reason: str = "not_available") -> dict:
    """Shape returned when we can't build the profile (fail-soft)."""
    return {
        "available":       False,
        "home":            {},
        "away":            {},
        "matchup": {
            "projected_possessions":   None,
            "projected_total_points":  None,
            "projected_margin":        None,
            "pace_environment":        "MODERATE",
            "efficiency_edge":         "neutral",
            "total_points_lean":       "NEUTRAL",
            "spread_support":          "none",
            "fragility_score":         100,
            "reason_codes":            [RC_DATA_INSUFFICIENT_FALLBACK],
            "summary": (
                "Sin datos completos para perfilar posesiones; "
                "se usa el histórico básico como referencia."
            ),
        },
        "_engine_version": ENGINE_VERSION,
        "_reason":         reason,
    }


# ────────────────────────────────────────────────────────────────────
# 1) Possession estimation (Dean Oliver formula)
# ────────────────────────────────────────────────────────────────────
def estimate_possessions(game_stats: dict) -> Optional[float]:
    """Estimate possessions from a single-team box-score line.

    Formula (per Dean Oliver): ``FGA + 0.475*FTA - ORB + TOV``.
    Falls back to a symmetric estimate when opponent data is also
    provided (averaged with ``0.5 * (team + opp)``).

    Expected ``game_stats`` keys (all optional — missing keys safely
    fall back to ``None``):

        fga, fta, orb, tov,
        opp_fga, opp_fta, opp_orb, opp_tov

    Returns ``None`` when there isn't enough data to compute.
    """
    if not isinstance(game_stats, dict):
        return None

    fga = _safe_float(game_stats.get("fga"))
    fta = _safe_float(game_stats.get("fta"))
    orb = _safe_float(game_stats.get("orb"))
    tov = _safe_float(game_stats.get("tov"))
    if any(v is None for v in (fga, fta, orb, tov)):
        return None

    team_poss = fga + 0.475 * fta - orb + tov

    opp_fga = _safe_float(game_stats.get("opp_fga"))
    opp_fta = _safe_float(game_stats.get("opp_fta"))
    opp_orb = _safe_float(game_stats.get("opp_orb"))
    opp_tov = _safe_float(game_stats.get("opp_tov"))
    if all(v is not None for v in (opp_fga, opp_fta, opp_orb, opp_tov)):
        opp_poss = opp_fga + 0.475 * opp_fta - opp_orb + opp_tov
        return round(0.5 * (team_poss + opp_poss), 2)

    return round(team_poss, 2)


# ────────────────────────────────────────────────────────────────────
# 2) Four Factors
# ────────────────────────────────────────────────────────────────────
def calculate_four_factors(team_stats: dict) -> dict:
    """Compute Dean Oliver's Four Factors from a single team's totals.

    Inputs (all optional, fail-soft):
        fgm, fga, three_pm, fta, tov, orb, opp_drb

    Returns dict with: ``efg``, ``tov_rate``, ``orb_rate``, ``ft_rate``.
    Each metric returns ``None`` when its prerequisites are missing.
    """
    out = {"efg": None, "tov_rate": None, "orb_rate": None, "ft_rate": None}
    if not isinstance(team_stats, dict):
        return out

    fgm     = _safe_float(team_stats.get("fgm"))
    fga     = _safe_float(team_stats.get("fga"))
    three_pm = _safe_float(team_stats.get("three_pm"))
    fta     = _safe_float(team_stats.get("fta"))
    tov     = _safe_float(team_stats.get("tov"))
    orb     = _safe_float(team_stats.get("orb"))
    opp_drb = _safe_float(team_stats.get("opp_drb"))

    # eFG% = (FGM + 0.5 * 3PM) / FGA
    if fgm is not None and fga and fga > 0:
        tpm = three_pm or 0.0
        out["efg"] = round((fgm + 0.5 * tpm) / fga, 4)

    # TOV% = TOV / (FGA + 0.44*FTA + TOV)
    if tov is not None and fga is not None:
        ftaf = fta or 0.0
        denom = fga + 0.44 * ftaf + tov
        if denom > 0:
            out["tov_rate"] = round(tov / denom, 4)

    # ORB% = ORB / (ORB + Opp_DRB)
    if orb is not None and opp_drb is not None:
        denom = orb + opp_drb
        if denom > 0:
            out["orb_rate"] = round(orb / denom, 4)

    # FTr = FTA / FGA  (free-throw rate)
    if fta is not None and fga and fga > 0:
        out["ft_rate"] = round(fta / fga, 4)

    return out


# ────────────────────────────────────────────────────────────────────
# 3) Team efficiency profile (aggregate across games)
# ────────────────────────────────────────────────────────────────────
def calculate_team_efficiency_profile(team_games: list[dict]) -> dict:
    """Aggregate a list of game-level box-scores into a team profile.

    Each item in ``team_games`` should contain (all optional):

        pts_for, pts_against,
        fgm, fga, three_pm, three_pa, fta, tov, orb, drb,
        opp_fga, opp_fta, opp_orb, opp_tov, opp_drb,
        minutes (defaults to 48)

    Returns a dict with the aggregated metrics (see module docstring).
    Missing inputs degrade gracefully.
    """
    if not isinstance(team_games, list) or not team_games:
        return {"sample_size": 0, "missing_data": True}

    valid_games = [g for g in team_games if isinstance(g, dict)]
    sample = len(valid_games)
    if not sample:
        return {"sample_size": 0, "missing_data": True}

    # Per-game derived series
    poss_series:       list[float] = []
    pace_series:       list[float] = []
    ortg_series:       list[float] = []
    drtg_series:       list[float] = []
    efg_series:        list[float] = []
    tov_series:        list[float] = []
    orb_series:        list[float] = []
    ftr_series:        list[float] = []
    three_pa_series:   list[float] = []
    three_p_pct_series: list[float] = []
    total_pts_series:  list[float] = []
    pts_for_series:    list[float] = []
    pts_against_series: list[float] = []

    for g in valid_games:
        poss = estimate_possessions(g)
        minutes = _safe_float(g.get("minutes"), 48.0) or 48.0
        if poss is not None and minutes > 0:
            poss_series.append(poss)
            pace_series.append(poss * (48.0 / minutes))

        pts_for = _safe_float(g.get("pts_for"))
        pts_against = _safe_float(g.get("pts_against"))
        if pts_for is not None:
            pts_for_series.append(pts_for)
        if pts_against is not None:
            pts_against_series.append(pts_against)
        if pts_for is not None and pts_against is not None:
            total_pts_series.append(pts_for + pts_against)

        if poss and poss > 0:
            if pts_for is not None:
                ortg_series.append(100.0 * pts_for / poss)
            if pts_against is not None:
                drtg_series.append(100.0 * pts_against / poss)

        ff = calculate_four_factors(g)
        if ff["efg"] is not None:
            efg_series.append(ff["efg"])
        if ff["tov_rate"] is not None:
            tov_series.append(ff["tov_rate"])
        if ff["orb_rate"] is not None:
            orb_series.append(ff["orb_rate"])
        if ff["ft_rate"] is not None:
            ftr_series.append(ff["ft_rate"])

        fga = _safe_float(g.get("fga"))
        three_pa = _safe_float(g.get("three_pa"))
        three_pm = _safe_float(g.get("three_pm"))
        if fga and fga > 0 and three_pa is not None:
            three_pa_series.append(three_pa / fga)
        if three_pa and three_pa > 0 and three_pm is not None:
            three_p_pct_series.append(three_pm / three_pa)

    off_rating = _safe_mean(ortg_series)
    def_rating = _safe_mean(drtg_series)
    pace_mean = _safe_mean(pace_series)
    pace_vol  = _safe_stdev(pace_series)
    total_std = _safe_stdev(total_pts_series)
    three_pct_var = _safe_stdev(three_p_pct_series)

    net_rating = None
    if off_rating is not None and def_rating is not None:
        net_rating = round(off_rating - def_rating, 2)

    return {
        "sample_size":         sample,
        "possessions":         round(_safe_mean(poss_series), 2) if poss_series else None,
        "pace":                round(pace_mean, 2) if pace_mean is not None else None,
        "pace_volatility":     round(pace_vol, 2) if pace_vol is not None else None,
        "offensive_rating":    round(off_rating, 2) if off_rating is not None else None,
        "defensive_rating":    round(def_rating, 2) if def_rating is not None else None,
        "net_rating":          net_rating,
        "efg":                 round(_safe_mean(efg_series), 4) if efg_series else None,
        "tov_rate":            round(_safe_mean(tov_series), 4) if tov_series else None,
        "orb_rate":            round(_safe_mean(orb_series), 4) if orb_series else None,
        "ft_rate":             round(_safe_mean(ftr_series), 4) if ftr_series else None,
        "three_pa_rate":       round(_safe_mean(three_pa_series), 4) if three_pa_series else None,
        "three_p_pct_variance": round(three_pct_var, 4) if three_pct_var is not None else None,
        "avg_points_for":      round(_safe_mean(pts_for_series), 2) if pts_for_series else None,
        "avg_points_against":  round(_safe_mean(pts_against_series), 2) if pts_against_series else None,
        "total_points_std":    round(total_std, 2) if total_std is not None else None,
        "missing_data":        sample < MIN_GAMES_FULL_PROFILE,
    }


# ────────────────────────────────────────────────────────────────────
# 4) Matchup possession context
# ────────────────────────────────────────────────────────────────────
def calculate_matchup_possession_context(
    home_profile: dict,
    away_profile: dict,
    *,
    league_baseline: Optional[dict] = None,
) -> dict:
    """Combine two team profiles into a matchup-level projection.

    Returns a dict with possession + total-points + margin projections,
    plus pace environment classification. The output is the input that
    ``derive_basketball_market_adjustments`` consumes.
    """
    baseline = {**DEFAULT_LEAGUE_BASELINE, **(league_baseline or {})}

    if not isinstance(home_profile, dict) or not isinstance(away_profile, dict):
        return _empty_matchup(baseline)

    s_h = home_profile.get("sample_size") or 0
    s_a = away_profile.get("sample_size") or 0
    if s_h < MIN_GAMES_ANY_PROFILE or s_a < MIN_GAMES_ANY_PROFILE:
        return _empty_matchup(baseline)

    pace_h = home_profile.get("pace")     or baseline["pace"]
    pace_a = away_profile.get("pace")     or baseline["pace"]
    matchup_pace = round((pace_h + pace_a) / 2.0, 2)

    ortg_h = home_profile.get("offensive_rating") or baseline["offensive_rating"]
    ortg_a = away_profile.get("offensive_rating") or baseline["offensive_rating"]
    drtg_h = home_profile.get("defensive_rating") or baseline["offensive_rating"]
    drtg_a = away_profile.get("defensive_rating") or baseline["offensive_rating"]

    # Expected ORtg vs THIS opponent (regress to league average to avoid
    # blowing up when only one side has good data).
    league_avg_rtg = baseline["offensive_rating"]
    proj_ortg_h = (ortg_h + drtg_a + league_avg_rtg) / 3.0
    proj_ortg_a = (ortg_a + drtg_h + league_avg_rtg) / 3.0

    # Points per side = pace * (proj_ortg / 100)
    proj_pts_h = matchup_pace * (proj_ortg_h / 100.0)
    proj_pts_a = matchup_pace * (proj_ortg_a / 100.0)
    projected_total = round(proj_pts_h + proj_pts_a, 1)
    projected_margin = round(proj_pts_h - proj_pts_a, 1)

    # Pace environment classification
    if matchup_pace >= PACE_HIGH_THRESHOLD:
        pace_env = "HIGH"
    elif matchup_pace <= PACE_LOW_THRESHOLD:
        pace_env = "LOW"
    else:
        pace_env = "MODERATE"

    net_h = home_profile.get("net_rating") or 0.0
    net_a = away_profile.get("net_rating") or 0.0
    net_edge = net_h - net_a
    if net_edge >= NET_RATING_STRONG_EDGE:
        efficiency_edge = "home"
    elif net_edge <= -NET_RATING_STRONG_EDGE:
        efficiency_edge = "away"
    else:
        efficiency_edge = "neutral"

    return {
        "projected_possessions":  matchup_pace,
        "projected_total_points": projected_total,
        "projected_margin":       projected_margin,
        "pace_environment":       pace_env,
        "efficiency_edge":        efficiency_edge,
        "net_rating_edge":        round(net_edge, 2),
        "league_avg_total":       round(baseline["league_total"], 1),
        "home_projected_points":  round(proj_pts_h, 1),
        "away_projected_points":  round(proj_pts_a, 1),
        "sample_home":            s_h,
        "sample_away":            s_a,
    }


def _empty_matchup(baseline: dict) -> dict:
    return {
        "projected_possessions":  baseline["pace"],
        "projected_total_points": baseline["league_total"],
        "projected_margin":       0.0,
        "pace_environment":       "MODERATE",
        "efficiency_edge":        "neutral",
        "net_rating_edge":        0.0,
        "league_avg_total":       baseline["league_total"],
        "home_projected_points":  baseline["league_total"] / 2.0,
        "away_projected_points":  baseline["league_total"] / 2.0,
        "sample_home":            0,
        "sample_away":            0,
        "missing_data":           True,
    }


# ────────────────────────────────────────────────────────────────────
# 5) Market adjustments derived from matchup context
# ────────────────────────────────────────────────────────────────────
def derive_basketball_market_adjustments(
    matchup_context: dict,
    *,
    home_profile: Optional[dict] = None,
    away_profile: Optional[dict] = None,
    bookmaker_total_line: Optional[float] = None,
    bookmaker_spread_line: Optional[float] = None,
) -> dict:
    """Translate a matchup context into market leans + reason codes.

    Returns a dict with:
        total_points_lean   : OVER | UNDER | NEUTRAL
        spread_support      : home  | away  | none
        moneyline_lean      : home  | away  | none
        fragility_score     : 0..100
        reason_codes        : [str, ...]
        summary             : str_es
    """
    home = home_profile or {}
    away = away_profile or {}
    reasons: list[str] = []

    if not isinstance(matchup_context, dict) or matchup_context.get("missing_data"):
        return {
            "total_points_lean":  "NEUTRAL",
            "spread_support":     "none",
            "moneyline_lean":     "none",
            "fragility_score":    100,
            "reason_codes":       [RC_DATA_INSUFFICIENT_FALLBACK],
            "summary": "Datos insuficientes para derivar leans (fallback).",
        }

    pace_env = matchup_context.get("pace_environment", "MODERATE")
    if pace_env == "HIGH":
        reasons.append(RC_HIGH_PACE_ENVIRONMENT)
    elif pace_env == "LOW":
        reasons.append(RC_LOW_PACE_ENVIRONMENT)

    proj_total = _safe_float(matchup_context.get("projected_total_points"))
    league_total = _safe_float(matchup_context.get("league_avg_total")) or DEFAULT_LEAGUE_BASELINE["league_total"]
    reference_total = _safe_float(bookmaker_total_line) or league_total
    total_delta = (proj_total - reference_total) if proj_total is not None else 0.0

    # ── eFG / TOV / ORB / FTr signals (average of both teams) ────────
    efg_avg = _safe_mean([home.get("efg"), away.get("efg")])
    tov_avg = _safe_mean([home.get("tov_rate"), away.get("tov_rate")])
    orb_avg = _safe_mean([home.get("orb_rate"), away.get("orb_rate")])
    ftr_avg = _safe_mean([home.get("ft_rate"), away.get("ft_rate")])
    three_pa_avg = _safe_mean([home.get("three_pa_rate"), away.get("three_pa_rate")])
    three_var_avg = _safe_mean([home.get("three_p_pct_variance"), away.get("three_p_pct_variance")])
    total_std_avg = _safe_mean([home.get("total_points_std"), away.get("total_points_std")])

    if tov_avg is not None and tov_avg >= TOV_HIGH_THRESHOLD:
        reasons.append(RC_TURNOVER_RISK)
    if orb_avg is not None and orb_avg >= ORB_HIGH_THRESHOLD:
        reasons.append(RC_OFFENSIVE_REBOUND_EDGE)
    if ftr_avg is not None and ftr_avg >= FTR_HIGH_THRESHOLD:
        reasons.append(RC_FREE_THROW_RATE_SUPPORT)

    three_point_risk = False
    if (three_pa_avg is not None and three_pa_avg >= THREE_PA_RATE_HIGH) or \
       (three_var_avg is not None and three_var_avg >= THREE_P_PCT_VARIANCE_HIGH):
        three_point_risk = True
        reasons.append(RC_THREE_POINT_VARIANCE_RISK)

    # ── Total points lean ────────────────────────────────────────────
    total_lean = "NEUTRAL"
    if total_delta >= MIN_TOTAL_POINTS_LEAN_DELTA:
        # High pace + high eFG ⇒ Over. Penalize if TOV high + pace low.
        if (pace_env == "HIGH" and (efg_avg or 0) >= EFG_HIGH_THRESHOLD) \
           or RC_OFFENSIVE_REBOUND_EDGE in reasons \
           or RC_FREE_THROW_RATE_SUPPORT in reasons:
            total_lean = "OVER"
            reasons.append(RC_TOTAL_OVER_SUPPORTED)
        elif pace_env != "LOW":
            total_lean = "OVER"
            reasons.append(RC_TOTAL_OVER_SUPPORTED)
    elif total_delta <= -MIN_TOTAL_POINTS_LEAN_DELTA:
        if (pace_env == "LOW" and (efg_avg or 1) < EFG_LOW_THRESHOLD) \
           or RC_TURNOVER_RISK in reasons:
            total_lean = "UNDER"
            reasons.append(RC_TOTAL_UNDER_SUPPORTED)
        elif pace_env != "HIGH":
            total_lean = "UNDER"
            reasons.append(RC_TOTAL_UNDER_SUPPORTED)

    # ── Net rating / spread / moneyline ─────────────────────────────
    net_edge = _safe_float(matchup_context.get("net_rating_edge"), 0.0) or 0.0
    eff_edge = matchup_context.get("efficiency_edge", "neutral")
    proj_margin = _safe_float(matchup_context.get("projected_margin"), 0.0) or 0.0

    spread_support = "none"
    moneyline_lean = "none"

    if eff_edge in ("home", "away") and abs(net_edge) >= NET_RATING_STRONG_EDGE:
        reasons.append(
            RC_STRONG_OFFENSIVE_RATING if abs(net_edge) >= NET_RATING_DOMINANT_EDGE
            else RC_STRONG_DEFENSIVE_RATING
        )
        moneyline_lean = eff_edge

        # Spread support: only if projected margin clearly clears the
        # bookmaker line (or a generic 3.5 reference when missing).
        spread_ref = _safe_float(bookmaker_spread_line)
        # Convention: positive spread_ref = favorite-side handicap (e.g. -3.5
        # means favorite must win by >3.5). Use absolute value for buffer.
        ref_abs = abs(spread_ref) if spread_ref is not None else 3.5
        if abs(proj_margin) >= (ref_abs + MIN_SPREAD_MARGIN_BUFFER):
            spread_support = eff_edge
            reasons.append(RC_SPREAD_MARGIN_SUPPORTED)
        elif abs(net_edge) >= NET_RATING_DOMINANT_EDGE:
            # Strong edge but projection doesn't beat the line by enough →
            # prefer Moneyline over Spread.
            reasons.append(RC_MONEYLINE_SAFER_THAN_SPREAD)

    # ── Fragility ───────────────────────────────────────────────────
    fragility = 0
    s_h = home.get("sample_size") or 0
    s_a = away.get("sample_size") or 0
    if s_h < MIN_GAMES_FULL_PROFILE:
        fragility += 20
    if s_a < MIN_GAMES_FULL_PROFILE:
        fragility += 20
    if three_point_risk:
        fragility += 15
    if total_std_avg is not None and total_std_avg >= TOTAL_POINTS_STD_HIGH:
        fragility += 10
    if pace_env == "MODERATE" and abs(total_delta) < MIN_TOTAL_POINTS_LEAN_DELTA:
        fragility += 5
    fragility = int(_clamp(fragility, 0, 100))

    # ── Summary (Spanish, used by UI) ───────────────────────────────
    summary = _build_summary(
        pace_env=pace_env,
        proj_total=proj_total,
        reference_total=reference_total,
        total_lean=total_lean,
        eff_edge=eff_edge,
        proj_margin=proj_margin,
        three_point_risk=three_point_risk,
        fragility=fragility,
    )

    # De-dup reason codes preserving order.
    seen = set()
    dedup_reasons = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            dedup_reasons.append(r)

    return {
        "total_points_lean":  total_lean,
        "spread_support":     spread_support,
        "moneyline_lean":     moneyline_lean,
        "fragility_score":    fragility,
        "reason_codes":       dedup_reasons,
        "summary":            summary,
        "projected_total":    proj_total,
        "reference_total":    reference_total,
        "projected_margin":   proj_margin,
    }


def _build_summary(
    *,
    pace_env: str,
    proj_total: Optional[float],
    reference_total: Optional[float],
    total_lean: str,
    eff_edge: str,
    proj_margin: float,
    three_point_risk: bool,
    fragility: int,
) -> str:
    parts: list[str] = []
    if pace_env == "HIGH":
        parts.append("Ritmo alto proyectado")
    elif pace_env == "LOW":
        parts.append("Ritmo bajo proyectado")
    else:
        parts.append("Ritmo neutro proyectado")

    if proj_total is not None and reference_total:
        delta = proj_total - reference_total
        sign = "+" if delta >= 0 else ""
        parts.append(
            f"proyección {proj_total:.1f} pts vs referencia {reference_total:.1f} ({sign}{delta:.1f})"
        )

    if total_lean == "OVER":
        parts.append("apoya Over")
    elif total_lean == "UNDER":
        parts.append("apoya Under")
    else:
        parts.append("sin lean claro de total")

    if eff_edge != "neutral":
        side = "local" if eff_edge == "home" else "visitante"
        parts.append(f"ventaja de eficiencia para el {side} ({proj_margin:+.1f})")

    if three_point_risk:
        parts.append("varianza alta en tiros de 3 → fragilidad")

    parts.append(f"fragility {fragility}/100")
    return ". ".join(parts).capitalize() + "."


# ────────────────────────────────────────────────────────────────────
# 6) Public top-level builder
# ────────────────────────────────────────────────────────────────────
def build_basketball_possession_profile(
    home_games: Optional[list[dict]],
    away_games: Optional[list[dict]],
    *,
    league_baseline: Optional[dict] = None,
    bookmaker_total_line: Optional[float] = None,
    bookmaker_spread_line: Optional[float] = None,
    home_fallback: Optional[dict] = None,
    away_fallback: Optional[dict] = None,
) -> dict:
    """One-shot wrapper used by analyst_engine / live re-eval.

    ``home_games`` / ``away_games`` are lists of per-team game dicts
    (the same shape ``calculate_team_efficiency_profile`` consumes).

    ``home_fallback`` / ``away_fallback`` may carry a precomputed
    profile from ``basketball_historical`` (``paceProxy`` etc.) — used
    only when the Four-Factors path can't produce enough data.

    Always returns a dict matching the documented shape. NEVER raises.
    """
    try:
        home_profile = calculate_team_efficiency_profile(home_games or [])
        away_profile = calculate_team_efficiency_profile(away_games or [])
        s_h = home_profile.get("sample_size") or 0
        s_a = away_profile.get("sample_size") or 0

        # If we don't have enough box-score sample, splice in the
        # historical fallback (pace proxy + scoring averages) so the
        # caller still gets a usable matchup projection.
        if s_h < MIN_GAMES_ANY_PROFILE and isinstance(home_fallback, dict):
            home_profile = _profile_from_fallback(home_fallback)
            s_h = home_profile.get("sample_size") or 0
        if s_a < MIN_GAMES_ANY_PROFILE and isinstance(away_fallback, dict):
            away_profile = _profile_from_fallback(away_fallback)
            s_a = away_profile.get("sample_size") or 0

        if s_h < MIN_GAMES_ANY_PROFILE or s_a < MIN_GAMES_ANY_PROFILE:
            out = empty_profile("insufficient_sample")
            out["home"] = home_profile
            out["away"] = away_profile
            return {"basketball_possession_profile": out}

        matchup = calculate_matchup_possession_context(
            home_profile, away_profile, league_baseline=league_baseline,
        )
        adjustments = derive_basketball_market_adjustments(
            matchup,
            home_profile=home_profile,
            away_profile=away_profile,
            bookmaker_total_line=bookmaker_total_line,
            bookmaker_spread_line=bookmaker_spread_line,
        )

        matchup_block = {
            "projected_possessions":  matchup.get("projected_possessions"),
            "projected_total_points": matchup.get("projected_total_points"),
            "projected_margin":       matchup.get("projected_margin"),
            "pace_environment":       matchup.get("pace_environment"),
            "efficiency_edge":        matchup.get("efficiency_edge"),
            "total_points_lean":      adjustments.get("total_points_lean"),
            "spread_support":         adjustments.get("spread_support"),
            "moneyline_lean":         adjustments.get("moneyline_lean"),
            "fragility_score":        adjustments.get("fragility_score"),
            "reason_codes":           adjustments.get("reason_codes"),
            "summary":                adjustments.get("summary"),
            "net_rating_edge":        matchup.get("net_rating_edge"),
            "home_projected_points":  matchup.get("home_projected_points"),
            "away_projected_points":  matchup.get("away_projected_points"),
            "league_avg_total":       matchup.get("league_avg_total"),
        }

        return {
            "basketball_possession_profile": {
                "available":       True,
                "home":            home_profile,
                "away":            away_profile,
                "matchup":         matchup_block,
                "_engine_version": ENGINE_VERSION,
            }
        }

    except Exception as exc:  # pragma: no cover — fail-soft guard
        log.debug("basketball_possession_layer failed: %s", exc)
        return {"basketball_possession_profile": empty_profile(f"error:{exc.__class__.__name__}")}


def _profile_from_fallback(fb: dict) -> dict:
    """Map a basketball_historical team block onto the possession-profile shape.

    Only fields we can derive from the proxy block are populated; the
    rest stay ``None`` so the matchup context regresses to baseline.
    """
    return {
        "sample_size":         fb.get("gamesAnalyzed") or 0,
        "possessions":         None,
        "pace":                fb.get("paceProxy"),
        "pace_volatility":     None,
        "offensive_rating":    None,
        "defensive_rating":    None,
        "net_rating":          None,
        "efg":                 None,
        "tov_rate":            None,
        "orb_rate":            None,
        "ft_rate":             None,
        "three_pa_rate":       None,
        "three_p_pct_variance": None,
        "avg_points_for":      fb.get("pointsForAvg"),
        "avg_points_against":  fb.get("pointsAgainstAvg"),
        "total_points_std":    None,
        "missing_data":        True,
        "_source":             "historical_fallback",
    }


__all__ = [
    "ENGINE_VERSION",
    "estimate_possessions",
    "calculate_four_factors",
    "calculate_team_efficiency_profile",
    "calculate_matchup_possession_context",
    "derive_basketball_market_adjustments",
    "build_basketball_possession_profile",
    "empty_profile",
    # Reason code constants
    "RC_HIGH_PACE_ENVIRONMENT",
    "RC_LOW_PACE_ENVIRONMENT",
    "RC_STRONG_OFFENSIVE_RATING",
    "RC_STRONG_DEFENSIVE_RATING",
    "RC_THREE_POINT_VARIANCE_RISK",
    "RC_TURNOVER_RISK",
    "RC_OFFENSIVE_REBOUND_EDGE",
    "RC_FREE_THROW_RATE_SUPPORT",
    "RC_SPREAD_MARGIN_SUPPORTED",
    "RC_MONEYLINE_SAFER_THAN_SPREAD",
    "RC_TOTAL_OVER_SUPPORTED",
    "RC_TOTAL_UNDER_SUPPORTED",
    "RC_DATA_INSUFFICIENT_FALLBACK",
    "ALL_REASON_CODES",
]
