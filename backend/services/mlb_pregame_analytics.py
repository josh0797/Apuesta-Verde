"""MLB Pre-game Analytics Engine — repeatable-edge focused.

Philosophy
==========
The engine no longer hunts for attractive odds. It looks for repeatable
edges in *protected* MLB markets (Team Totals, F5, NRFI/YRFI, Run Line
+1.5) using:
  1. Starting Pitcher edge (ERA / xERA / FIP / WHIP / K/BB / hard contact)
  2. Offense vs pitcher type (LHP/RHP, season + last5/10)
  3. Bullpen fatigue (innings last 48h / 7d + late-relief ERA)
  4. Park + weather impact (Coors/Oracle/etc.)
  5. Historical 1st-inning patterns for NRFI/YRFI
  6. Market fragility (composite 0-100)
  7. Alternative-market rescue when direct markets show no value

Every signal it emits carries the literal SOURCE_URL that confirmed the
pitcher / stat, so the UI can show "Confirmed by MLB Stats API:
https://statsapi.mlb.com/...". This is the user's transparency request.

Public API
----------
    analyze_mlb_day(date_str, *, db) -> dict
        Top-level orchestration. Returns:
            {
                picks, rescued_picks, discarded_picks,
                fragility_scores, editorial_context_signals,
                pipeline_meta,
            }

    starting_pitcher_edge(home_p, away_p, ctx) -> dict
    pitcher_quality_score(p) -> dict
    bullpen_fatigue_score(usage) -> dict
    offense_vs_pitcher_type(team_stats, hand) -> dict
    park_factor_analyzer(venue, weather) -> dict
    mlb_fragility_score(match_ctx) -> dict
    run_line_predictor(ctx) -> dict
    over_under_predictor(ctx, book_line) -> dict
    nrfi_yrfi_analyzer(ctx) -> dict

All scoring functions are *pure*: they take dicts and return dicts. The
orchestrator wires them together using `mlb_stats_api` + an optional
pitcher-confirmation scraper.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("mlb_pregame_analytics")

# ════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════════════════
MLB_STATSAPI_BASE = "https://statsapi.mlb.com/api/v1"
LEAGUE_AVG_ERA    = 4.30
LEAGUE_AVG_FIP    = 4.30
LEAGUE_AVG_WHIP   = 1.30
LEAGUE_AVG_K9     = 8.7
LEAGUE_AVG_BB9    = 3.2
LEAGUE_AVG_RUNS_PER_GAME = 4.5

# Park factors (per-park run multiplier — > 1.0 means hitter-friendly).
# Source: Baseball Savant 3-year rolling averages.
PARK_FACTORS: dict[str, dict] = {
    "Coors Field":            {"runs": 1.15, "hr": 1.20, "tilt": "OVER"},
    "Great American Ball Park":{"runs": 1.10, "hr": 1.18, "tilt": "OVER"},
    "Citizens Bank Park":     {"runs": 1.08, "hr": 1.14, "tilt": "OVER"},
    "Yankee Stadium":         {"runs": 1.06, "hr": 1.18, "tilt": "OVER"},
    "Fenway Park":            {"runs": 1.05, "hr": 1.02, "tilt": "OVER"},
    "Oracle Park":            {"runs": 0.92, "hr": 0.80, "tilt": "UNDER"},
    "T-Mobile Park":          {"runs": 0.93, "hr": 0.88, "tilt": "UNDER"},
    "loanDepot park":         {"runs": 0.93, "hr": 0.90, "tilt": "UNDER"},
    "Petco Park":             {"runs": 0.95, "hr": 0.93, "tilt": "UNDER"},
    "Tropicana Field":        {"runs": 0.95, "hr": 0.96, "tilt": "UNDER"},
}


# ════════════════════════════════════════════════════════════════════════════
# 1. STARTING PITCHER EDGE
# ════════════════════════════════════════════════════════════════════════════
def starting_pitcher_edge(home_p: dict, away_p: dict, ctx: Optional[dict] = None) -> dict:
    """Compare two starting pitchers side-by-side.

    Each `p` dict expects {era, xera, fip, xfip, whip, k9, bb9, hard_hit,
    barrel, exit_velocity, last_3_starts (list of ERA), vs_team_era,
    park_era}. Missing fields fall back to league averages so the
    function never raises.
    """
    if not home_p or not away_p:
        return {"edge_type": "UNKNOWN", "score": 50,
                "explanation": "Pitcher missing — cannot compute edge."}

    h_quality = pitcher_quality_score(home_p)["score"]
    a_quality = pitcher_quality_score(away_p)["score"]
    diff = h_quality - a_quality
    # Map diff [-50,+50] → score [0,100]. 50 = equal pitchers; >65 means
    # home advantage; <35 means away advantage.
    score = max(0, min(100, 50 + diff))

    abs_diff = abs(diff)
    if abs_diff >= 22:
        edge_type = "STRONG"
    elif abs_diff >= 12:
        edge_type = "MODERATE"
    elif abs_diff >= 5:
        edge_type = "NEUTRAL"
    else:
        edge_type = "NEUTRAL"
    if diff < -20:
        edge_type = "NEGATIVE"

    better = home_p.get("name", "Home") if diff >= 0 else away_p.get("name", "Away")
    weaker = away_p.get("name", "Away") if diff >= 0 else home_p.get("name", "Home")
    explanation = (
        f"{better} (Q={int(max(h_quality, a_quality))}) vs "
        f"{weaker} (Q={int(min(h_quality, a_quality))}); "
        f"diff={diff:+d}, edge={edge_type}."
    )
    return {"edge_type": edge_type, "score": int(score), "explanation": explanation,
            "home_quality": int(h_quality), "away_quality": int(a_quality)}


# ════════════════════════════════════════════════════════════════════════════
# 2. PITCHER QUALITY SCORE (with regression detection)
# ════════════════════════════════════════════════════════════════════════════
def pitcher_quality_score(p: dict) -> dict:
    """Score a single pitcher 0-100. Detects xERA/ERA divergence to flag
    OVERPERFORMING / UNDERVALUED regression tags."""
    if not p:
        return {"score": 50, "tags": [], "explanation": "no pitcher data"}

    era    = float(p.get("era")   or LEAGUE_AVG_ERA)
    xera   = float(p.get("xera")  or era)
    fip    = float(p.get("fip")   or LEAGUE_AVG_FIP)
    xfip   = float(p.get("xfip")  or fip)
    whip   = float(p.get("whip")  or LEAGUE_AVG_WHIP)
    k9     = float(p.get("k9")    or LEAGUE_AVG_K9)
    bb9    = float(p.get("bb9")   or LEAGUE_AVG_BB9)
    hard   = float(p.get("hard_hit") or 0.0)  # 0-100 %
    barrel = float(p.get("barrel")   or 0.0)

    # Base score: how many sigmas below league average each rate is.
    # Lower is better for ERA/FIP/WHIP/BB9; higher for K9.
    def _to_pts(val, baseline, *, lower_is_better: bool, weight: float):
        ratio = (baseline - val) / max(0.1, baseline) if lower_is_better else (val - baseline) / max(0.1, baseline)
        return max(-1.0, min(1.0, ratio)) * weight

    pts = 50.0
    pts += _to_pts(era,  LEAGUE_AVG_ERA, lower_is_better=True,  weight=12)
    pts += _to_pts(fip,  LEAGUE_AVG_FIP, lower_is_better=True,  weight=10)
    pts += _to_pts(whip, LEAGUE_AVG_WHIP,lower_is_better=True,  weight=10)
    pts += _to_pts(k9,   LEAGUE_AVG_K9,  lower_is_better=False, weight=8)
    pts += _to_pts(bb9,  LEAGUE_AVG_BB9, lower_is_better=True,  weight=6)
    # Penalty for hard contact > 38% / barrel > 8%
    if hard:    pts -= max(0, (hard - 35.0)) * 0.4
    if barrel:  pts -= max(0, (barrel - 7.0)) * 0.5

    score = max(0, min(100, int(round(pts))))

    tags: list[str] = []
    # Regression detection
    if xera and era and (xera - era) >= 1.20:
        tags.append("PITCHER_OVERPERFORMING")
        score -= 12
    if xera and era and (era - xera) >= 1.20:
        tags.append("PITCHER_UNDERVALUED")
        score += 10
    if xfip and fip and (xfip - fip) >= 0.90 and "PITCHER_OVERPERFORMING" not in tags:
        tags.append("PITCHER_OVERPERFORMING")
        score -= 8

    score = max(0, min(100, score))
    explanation = (
        f"ERA {era:.2f} (xERA {xera:.2f}) · FIP {fip:.2f} · WHIP {whip:.2f} · "
        f"K/9 {k9:.1f} · BB/9 {bb9:.1f}"
    )
    return {"score": score, "tags": tags, "explanation": explanation}


# ════════════════════════════════════════════════════════════════════════════
# 3. BULLPEN FATIGUE SCORE
# ════════════════════════════════════════════════════════════════════════════
def bullpen_fatigue_score(usage: dict) -> dict:
    """Compute 0-100 (HIGHER = FRESHER). Hard caps when fatigue is real."""
    if not usage:
        return {"score": 60, "tags": [], "explanation": "no bullpen data"}
    ip_48h     = float(usage.get("innings_last_48h") or 0)
    ip_3d      = float(usage.get("innings_last_3d")  or 0)
    era_7d     = float(usage.get("bullpen_era_7d")   or 4.00)
    save_pct   = float(usage.get("save_conversion_pct") or 0.75)
    runs_5g    = float(usage.get("runs_allowed_last_5g") or 0)

    score = 80.0
    if ip_48h >= 8:  score -= 30
    if ip_48h >= 10: score = min(score, 40)
    if ip_3d >= 12:  score -= 10
    score -= max(0, era_7d - 3.80) * 4
    score += max(0, (save_pct - 0.70)) * 30
    score -= max(0, runs_5g - 12) * 1.5
    score = max(0, min(100, int(round(score))))

    tags: list[str] = []
    if ip_48h >= 8 or ip_3d >= 12:
        tags.append("BULLPEN_FATIGUE")
    if era_7d >= 4.75:
        tags.append("BULLPEN_FATIGUE")

    explanation = (
        f"IP 48h {ip_48h:.1f} · IP 3d {ip_3d:.1f} · ERA 7d {era_7d:.2f} · "
        f"save% {save_pct*100:.0f} · R last5 {int(runs_5g)}"
    )
    return {"score": score, "tags": list(set(tags)), "explanation": explanation}


# ════════════════════════════════════════════════════════════════════════════
# 4. OFFENSE vs PITCHER TYPE
# ════════════════════════════════════════════════════════════════════════════
def offense_vs_pitcher_type(team_stats: dict, hand: str) -> dict:
    """Score the team's offensive index vs LHP/RHP. 100 = elite, 50 = avg."""
    if not team_stats or not hand:
        return {"score": 50, "tags": [], "explanation": "no offense data"}
    key = "vs_lhp" if hand.upper().startswith("L") else "vs_rhp"
    split = team_stats.get(key) or {}
    avg     = float(split.get("avg")   or 0.245)
    obp     = float(split.get("obp")   or 0.310)
    slg     = float(split.get("slg")   or 0.400)
    ops     = float(split.get("ops")   or (obp + slg))
    rpg     = float(split.get("runs_per_game") or LEAGUE_AVG_RUNS_PER_GAME)
    risp    = float(split.get("risp_avg") or avg)
    k_rate  = float(split.get("k_rate") or 0.22)
    hr_rate = float(split.get("hr_rate")or 0.030)

    score = 50.0
    score += (ops    - 0.730) * 60      # OPS centered on 0.730
    score += (rpg    - LEAGUE_AVG_RUNS_PER_GAME) * 4
    score += (risp   - avg) * 50
    score -= (k_rate - 0.22) * 80
    score += (hr_rate- 0.030) * 250
    score = max(0, min(100, int(round(score))))

    tags: list[str] = []
    if ops >= 0.800: tags.append("OFFENSE_HOT_VS_" + key.upper())
    if k_rate >= 0.27: tags.append("HIGH_K_RATE_VS_" + key.upper())

    explanation = (
        f"vs{key[-3:].upper()}: AVG {avg:.3f} · OBP {obp:.3f} · SLG {slg:.3f} · "
        f"OPS {ops:.3f} · RPG {rpg:.2f}"
    )
    return {"score": score, "tags": tags, "explanation": explanation}


# ════════════════════════════════════════════════════════════════════════════
# 5. PARK FACTOR + WEATHER
# ════════════════════════════════════════════════════════════════════════════
def park_factor_analyzer(venue: Optional[str], weather: Optional[dict] = None) -> dict:
    """Return park run multiplier + weather impact + tilt OVER/UNDER."""
    pf = PARK_FACTORS.get((venue or "").strip(), {"runs": 1.00, "hr": 1.00, "tilt": "NEUTRAL"})
    weather = weather or {}
    temp_f      = weather.get("temperature_f")
    wind_mph    = weather.get("wind_mph") or 0
    wind_dir    = (weather.get("wind_direction") or "").lower()  # "out_to_left", "in_from_cf", "cross"
    humidity    = weather.get("humidity_pct") or 50

    weather_score = 50.0
    tags: list[str] = []

    if temp_f is not None:
        if temp_f >= 80:
            weather_score += 8
            tags.append("HOT_WEATHER_OVER")
        elif temp_f <= 45:
            weather_score -= 8
            tags.append("COLD_WEATHER_UNDER")
    if wind_mph >= 10:
        if "out" in wind_dir:
            weather_score += 10
            tags.append("WIND_OUT_OVER")
        elif "in" in wind_dir:
            weather_score -= 10
            tags.append("WIND_IN_UNDER")
    if humidity >= 70:
        weather_score += 3

    tilt = pf["tilt"]
    if tilt == "OVER" and weather_score > 55:
        tags.append("PARK_OVER_SIGNAL")
    if tilt == "UNDER" and weather_score < 45:
        tags.append("PARK_UNDER_SIGNAL")

    return {
        "venue":           venue,
        "park_runs_mult":  pf["runs"],
        "park_hr_mult":    pf["hr"],
        "park_tilt":       tilt,
        "weather_score":   int(weather_score),
        "tags":            tags,
        "explanation":     f"{venue or 'unknown park'}: runs×{pf['runs']:.2f}, HR×{pf['hr']:.2f}, tilt {tilt}, weather {int(weather_score)}",
    }


# ════════════════════════════════════════════════════════════════════════════
# 6. FRAGILITY SCORE (composite)
# ════════════════════════════════════════════════════════════════════════════
def mlb_fragility_score(ctx: dict) -> dict:
    """Composite 0-100. HIGHER = FRAGILE. Reads tags from the other scoring
    blocks already computed.
    """
    bullpen_tags  = (ctx.get("bullpen") or {}).get("tags") or []
    pitcher_tags  = []
    for p in ("home_pitcher_quality", "away_pitcher_quality"):
        pitcher_tags.extend((ctx.get(p) or {}).get("tags") or [])
    park          = ctx.get("park") or {}
    inexp_pitcher = ctx.get("inexperienced_pitcher", False)
    incomplete    = ctx.get("incomplete_lineup", False)
    extreme_w     = ctx.get("extreme_weather", False)

    score = 20  # base — most MLB games are moderately protected
    if "BULLPEN_FATIGUE" in bullpen_tags:    score += 25
    if "PITCHER_OVERPERFORMING" in pitcher_tags: score += 12
    if park.get("park_runs_mult", 1.0) >= 1.10:  score += 8
    if inexp_pitcher:                        score += 18
    if incomplete:                           score += 10
    if extreme_w:                            score += 8
    if (ctx.get("home_pitcher_quality") or {}).get("score", 50) < 35 \
       or (ctx.get("away_pitcher_quality") or {}).get("score", 50) < 35:
        score += 8
    score = max(0, min(100, score))

    if score <= 20:   label = "MUY_PROTEGIDO"
    elif score <= 40: label = "PROTEGIDO"
    elif score <= 60: label = "RIESGO_MEDIO"
    else:             label = "FRAGIL"

    return {"score": score, "label": label,
            "explanation": f"fragility={score}/100 ({label})"}


# ════════════════════════════════════════════════════════════════════════════
# 7. RUN LINE PREDICTOR + RUN_LINE_TRAP guardrail
# ════════════════════════════════════════════════════════════════════════════
def run_line_predictor(ctx: dict) -> dict:
    """Score Run Line (favorite -1.5 or underdog +1.5) 0-100 and detect traps."""
    edge        = (ctx.get("pitcher_edge") or {}).get("score", 50)
    bullpen     = (ctx.get("bullpen")      or {}).get("score", 60)
    offense_h   = (ctx.get("offense_home") or {}).get("score", 50)
    offense_a   = (ctx.get("offense_away") or {}).get("score", 50)
    park_mult   = (ctx.get("park")         or {}).get("park_runs_mult", 1.0)
    momentum    = ctx.get("momentum_score") or 50

    score = (edge * 0.35 + bullpen * 0.25 + max(offense_h, offense_a) * 0.20
             + (park_mult - 1.0) * 100 * 0.05 + momentum * 0.15)
    score = max(0, min(100, int(round(score))))

    tags: list[str] = []
    # RUN_LINE_TRAP guardrail
    favorite_bp_era_7d = float((ctx.get("favorite_bullpen_era_7d") or 0))
    favorite_bp_ip_48h = float((ctx.get("favorite_bullpen_ip_48h") or 0))
    one_run_win_pct    = float((ctx.get("favorite_one_run_win_pct") or 0))
    if favorite_bp_era_7d > 4.75 or favorite_bp_ip_48h > 8 or one_run_win_pct > 0.40:
        tags.append("RUN_LINE_TRAP")
        score = min(score, 45)  # cap — never recommend at high confidence

    return {"score": score, "tags": tags,
            "explanation": f"Run Line score={score}; "
                          f"bp_era_7d={favorite_bp_era_7d:.2f} ip_48h={favorite_bp_ip_48h:.1f} 1R_win%={one_run_win_pct:.0%}"}


# ════════════════════════════════════════════════════════════════════════════
# 8. OVER/UNDER PREDICTOR (expected_runs model)
# ════════════════════════════════════════════════════════════════════════════
def over_under_predictor(ctx: dict, book_line: Optional[float] = None) -> dict:
    """Estimate expected_runs and compare against book line (if provided)."""
    h_q = (ctx.get("home_pitcher_quality") or {}).get("score", 50)
    a_q = (ctx.get("away_pitcher_quality") or {}).get("score", 50)
    bp  = (ctx.get("bullpen")              or {}).get("score", 60)
    off_h = (ctx.get("offense_home") or {}).get("score", 50)
    off_a = (ctx.get("offense_away") or {}).get("score", 50)
    park  = ctx.get("park")           or {}
    park_mult     = park.get("park_runs_mult", 1.0)
    weather_score = park.get("weather_score", 50)

    # Translate the scoring blocks into an expected runs estimate.
    # Baseline 4.5 RPG per team; pitchers/offenses tweak it ±2 RPG.
    base = LEAGUE_AVG_RUNS_PER_GAME * 2  # 9 runs combined baseline
    pitcher_factor = (100 - (h_q + a_q) / 2) / 50.0   # 0..2 (worse pitchers = more runs)
    offense_factor = ((off_h + off_a) / 2 - 50) / 50.0  # -1..1
    bullpen_factor = (60 - bp) / 60.0 * 0.6

    expected_runs = base * (
        0.55 + pitcher_factor * 0.30 + offense_factor * 0.20 + bullpen_factor * 0.05
    ) * park_mult * (0.95 + (weather_score - 50) / 200.0)
    expected_runs = round(max(4.0, min(14.0, expected_runs)), 1)

    out: dict = {
        "expected_runs": expected_runs,
        "tags":          [],
        "explanation":   f"expected_runs={expected_runs:.1f} (park×{park_mult:.2f})",
        "verdict":       "NO_BET",
        "score":         50,
    }
    if book_line is not None:
        diff = expected_runs - book_line
        if diff >= 0.8:
            out["verdict"] = "OVER"
            out["score"]   = min(100, 65 + int(diff * 10))
            out["tags"].append("OVER_VALUE")
        elif diff <= -0.8:
            out["verdict"] = "UNDER"
            out["score"]   = min(100, 65 + int(abs(diff) * 10))
            out["tags"].append("UNDER_VALUE")
        out["explanation"] += f" vs line {book_line:.1f} (diff {diff:+.1f})"
    return out


# ════════════════════════════════════════════════════════════════════════════
# 9. NRFI / YRFI ANALYZER
# ════════════════════════════════════════════════════════════════════════════
def nrfi_yrfi_analyzer(ctx: dict) -> dict:
    """Compute nrfi_score and yrfi_score using 1st-inning specific stats."""
    def _p_first_inning(p: dict) -> float:
        if not p:
            return 50.0
        first_pitch_strike = float(p.get("first_pitch_strike_pct") or 0.58)
        era_1st = float(p.get("first_inning_era") or p.get("era") or LEAGUE_AVG_ERA)
        whip_1st = float(p.get("first_inning_whip") or p.get("whip") or LEAGUE_AVG_WHIP)
        bb_1st  = float(p.get("first_inning_walk_rate") or 0.10)
        hard_1st= float(p.get("first_inning_hard_contact") or 0.30)
        s = 50.0
        s += (first_pitch_strike - 0.58) * 120
        s -= max(0, era_1st - 3.50) * 6
        s -= max(0, whip_1st - 1.20) * 18
        s -= max(0, bb_1st  - 0.08) * 80
        s -= max(0, hard_1st - 0.30) * 60
        return max(0, min(100, s))

    def _top3_offense(team: dict) -> float:
        t3 = (team or {}).get("top3_lineup") or {}
        obp = float(t3.get("obp") or 0.330)
        slg = float(t3.get("slg") or 0.420)
        hr  = float(t3.get("hr_rate") or 0.035)
        k   = float(t3.get("k_rate") or 0.22)
        s = 50.0
        s += (obp - 0.330) * 110
        s += (slg - 0.420) * 70
        s += (hr  - 0.035) * 200
        s -= (k   - 0.22)  * 50
        return max(0, min(100, s))

    p_home = _p_first_inning(ctx.get("home_pitcher_stats") or {})
    p_away = _p_first_inning(ctx.get("away_pitcher_stats") or {})
    off_h  = _top3_offense(ctx.get("home_team") or {})
    off_a  = _top3_offense(ctx.get("away_team") or {})

    # Team historicals (1st-inning rates last 10 games)
    home_team = ctx.get("home_team") or {}
    away_team = ctx.get("away_team") or {}
    h_nrfi_rate = float(home_team.get("nrfi_rate_10g") or 0.55)
    a_nrfi_rate = float(away_team.get("nrfi_rate_10g") or 0.55)

    park_mult = (ctx.get("park") or {}).get("park_runs_mult", 1.0)

    # NRFI favored when both pitchers are sharp in 1st AND top3 are weak.
    nrfi_score = (
        0.30 * p_home + 0.30 * p_away + 0.15 * (100 - off_h) + 0.15 * (100 - off_a)
        + 0.10 * ((h_nrfi_rate + a_nrfi_rate) * 100 / 2)
    ) - (park_mult - 1.0) * 30
    nrfi_score = max(0, min(100, int(round(nrfi_score))))
    yrfi_score = max(0, min(100, 100 - nrfi_score + 8))  # slight upward bias

    tags: list[str] = []
    if nrfi_score >= 72 and yrfi_score <= 55:
        tags.append("NRFI_SIGNAL")
    elif yrfi_score >= 70 and nrfi_score <= 50:
        tags.append("YRFI_SIGNAL")

    return {
        "nrfi_score":  nrfi_score,
        "yrfi_score":  yrfi_score,
        "tags":        tags,
        "explanation": (
            f"NRFI={nrfi_score}/100 (p_home={int(p_home)}, p_away={int(p_away)}, "
            f"top3_h={int(off_h)}, top3_a={int(off_a)})"
        ),
    }


# ════════════════════════════════════════════════════════════════════════════
# 10. ALTERNATIVE MARKET RESCUE
# ════════════════════════════════════════════════════════════════════════════
def mlb_alternative_rescue(ctx: dict, run_line: dict, over_under: dict, nrfi: dict) -> dict:
    """Try to rescue a match when Run Line isn't viable."""
    candidates: list[dict] = []
    # Team total under for the side facing the better pitcher
    h_q = (ctx.get("home_pitcher_quality") or {}).get("score", 50)
    a_q = (ctx.get("away_pitcher_quality") or {}).get("score", 50)
    if h_q - a_q >= 15:
        candidates.append({"market": "Team Total Under (away)",
                            "score": 70, "rationale": "Visitante enfrenta mejor pitcher local."})
    if a_q - h_q >= 15:
        candidates.append({"market": "Team Total Under (home)",
                            "score": 70, "rationale": "Local enfrenta mejor pitcher visitante."})
    # F5 Total Runs Under when both pitchers are top-tier
    if h_q >= 65 and a_q >= 65:
        candidates.append({"market": "F5 Total Runs Under",
                            "score": 75, "rationale": "Duelo de pitchers — F5 Under es repetible."})
    # NRFI rescue
    if "NRFI_SIGNAL" in nrfi.get("tags", []):
        candidates.append({"market": "NRFI", "score": int(nrfi.get("nrfi_score") or 0),
                            "rationale": nrfi.get("explanation", "")})
    # Run Line +1.5 underdog when run-line trap on favorite
    if "RUN_LINE_TRAP" in run_line.get("tags", []):
        candidates.append({"market": "Run Line +1.5 (underdog)",
                            "score": 68, "rationale": "Favorito gana por 1 carrera con frecuencia."})
    # Over/Under from over_under_predictor
    if over_under.get("verdict") in ("OVER", "UNDER") and over_under.get("score", 0) >= 70:
        candidates.append({"market": f"Total Runs {over_under['verdict'].title()}",
                            "score": over_under["score"],
                            "rationale": over_under.get("explanation", "")})

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"candidates": candidates[:3]}


# ════════════════════════════════════════════════════════════════════════════
# 11. SIGNAL EMISSION (with source_url for transparency)
# ════════════════════════════════════════════════════════════════════════════
def emit_signals(ctx: dict, parts: dict, *, source_url: Optional[str] = None) -> list[dict]:
    """Aggregate the tags from every scoring block into the canonical
    editorial_context_signals shape, embedding source_url when given.
    """
    from .signal_catalog import make_signal

    bag: list[dict] = []
    seen: set[str] = set()
    tag_to_code = {
        "PITCHER_OVERPERFORMING":  "PITCHER_OVERPERFORMING",
        "PITCHER_UNDERVALUED":     "PITCHER_UNDERVALUED",
        "BULLPEN_FATIGUE":         "BULLPEN_FATIGUE_SIGNAL",
        "RUN_LINE_TRAP":           "RUN_LINE_TRAP",
        "PARK_OVER_SIGNAL":        "PARK_OVER_SIGNAL",
        "PARK_UNDER_SIGNAL":       "PARK_UNDER_SIGNAL",
        "NRFI_SIGNAL":             "NRFI_SIGNAL",
        "YRFI_SIGNAL":             "YRFI_SIGNAL",
    }
    # Collect tags from all the parts
    raw_tags: list[str] = []
    for key in ("home_pitcher_quality", "away_pitcher_quality",
                "bullpen", "park", "run_line", "over_under", "nrfi"):
        raw_tags.extend((parts.get(key) or {}).get("tags") or [])

    if (parts.get("pitcher_edge") or {}).get("edge_type") == "STRONG":
        raw_tags.append("STRONG_PITCHER_EDGE")
    frag = parts.get("fragility") or {}
    if frag.get("score", 100) <= 20:
        raw_tags.append("LOW_FRAGILITY_MARKET")
    if parts.get("rescued_candidates"):
        raw_tags.append("RESCUED_MARKET")

    for t in raw_tags:
        code = tag_to_code.get(t, t)
        if code in seen:
            continue
        sig = make_signal(code, sport="baseball")
        if sig is None:
            continue
        if source_url:
            sig["source_url"] = source_url
            sig["source"] = (sig.get("source") or "MLB Stats API")
        seen.add(code)
        bag.append(sig)
    return bag


__all__ = [
    "starting_pitcher_edge",
    "pitcher_quality_score",
    "bullpen_fatigue_score",
    "offense_vs_pitcher_type",
    "park_factor_analyzer",
    "mlb_fragility_score",
    "run_line_predictor",
    "over_under_predictor",
    "nrfi_yrfi_analyzer",
    "mlb_alternative_rescue",
    "emit_signals",
    "PARK_FACTORS",
    "LEAGUE_AVG_ERA",
    "LEAGUE_AVG_FIP",
    "LEAGUE_AVG_RUNS_PER_GAME",
]
