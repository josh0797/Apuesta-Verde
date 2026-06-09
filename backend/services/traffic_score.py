"""Traffic Score Engine — Composite 0-100 offensive-pressure score.

Phase 44 — Bullpen-vs-Traffic interaction model.

Background:
    The bullpen-vulnerable backtest revealed that a vulnerable bullpen
    *alone* (high ERA 7d) does NOT automatically degrade Under bets.
    What DOES degrade them is the combination of a vulnerable bullpen
    AND a high-traffic opposing offense. This module produces the
    "traffic score" used to decide which side of that interaction we
    are on.

Design philosophy:
    * Pure functions — no I/O, no DB access, no HTTP. The caller (the
      backtest script or the live engine) is responsible for hydrating
      the offensive stats from whichever provider it trusts.
    * Composite score 0-100 built from 8 weighted components. Each
      component is **linearly interpolated** between a "low" and a
      "high" threshold so the score is smooth (no cliff effects).
    * The score is bucketed into LOW / MEDIUM / HIGH so callers can
      branch on a categorical without re-reading the score.

Component weights (total = 100):
    1. OPS (last 7d)                       20
    2. Runs/Game (last 7d)                 15
    3. OBP (last 7d)                       15
    4. HR rate (HR / PA, last 7d)          15
    5. XBH rate ((2B+3B+HR) / PA, last 7d) 10
    6. Hard contact (Statcast or SLG)      10
    7. Recent form (last 5g R/G)            5
    8. Implied team total (or proxy)       10

Buckets:
    0-39   → LOW_TRAFFIC
    40-69  → MEDIUM_TRAFFIC
    70-100 → HIGH_TRAFFIC

Public API:
    compute_offense_window_metrics(raw_window: dict) -> dict
    compute_traffic_score(team_window: dict, ..., implied_team_total: float | None) -> dict
    combine_team_traffic_scores(home: dict, away: dict) -> dict
    classify_bullpen_traffic_interaction(bullpen_era_7d, traffic_bucket, is_under_pick) -> dict
"""
from __future__ import annotations

from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────
# Component definitions
# ─────────────────────────────────────────────────────────────────────
#  Each component:
#     'weight'    — points the component contributes at full strength.
#     'low'       — threshold at or below which the component scores 0.
#     'high'      — threshold at or above which it scores full points.
#     'higher_is_more_traffic' — True for all our metrics (more offense
#                   ⇒ more traffic). Kept explicit for future flexibility.
COMPONENTS = {
    "ops":           {"weight": 20, "low": 0.680,  "high": 0.760},
    "runs_per_game": {"weight": 15, "low": 3.8,    "high": 5.0},
    "obp":           {"weight": 15, "low": 0.300,  "high": 0.330},
    "hr_rate":       {"weight": 15, "low": 0.025,  "high": 0.035},
    "xbh_rate":      {"weight": 10, "low": 0.060,  "high": 0.085},
    "hard_contact":  {"weight": 10, "low": 0.380,  "high": 0.430},
    "recent_form":   {"weight":  5, "low": 4.0,    "high": 5.0},
    "team_total":    {"weight": 10, "low": 3.8,    "high": 4.6},
}

BUCKET_LOW_MAX    = 39
BUCKET_MEDIUM_MAX = 69
BUCKET_HIGH       = "HIGH_TRAFFIC"
BUCKET_MEDIUM     = "MEDIUM_TRAFFIC"
BUCKET_LOW        = "LOW_TRAFFIC"

ENGINE_VERSION = "traffic_score.1"


# ─────────────────────────────────────────────────────────────────────
# Reason codes — exported so callers can use these constants without
# stringly-typed mistakes.
# ─────────────────────────────────────────────────────────────────────
RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC = "BULLPEN_RISK_CONFIRMED_BY_TRAFFIC"
RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH  = "BULLPEN_RISK_ISOLATED_NOT_ENOUGH"
RC_HIGH_TRAFFIC_UNDER_DANGER         = "HIGH_TRAFFIC_UNDER_DANGER"
RC_LOW_TRAFFIC_UNDER_SURVIVED        = "LOW_TRAFFIC_UNDER_SURVIVED"

# Phase 46 — Live traffic score reason codes.
RC_LIVE_TRAFFIC_RISING              = "LIVE_TRAFFIC_RISING"
RC_LIVE_TRAFFIC_COLLAPSING          = "LIVE_TRAFFIC_COLLAPSING"
RC_LIVE_HIGH_TRAFFIC_UNDER_DANGER   = "LIVE_HIGH_TRAFFIC_UNDER_DANGER"
RC_LIVE_LOW_TRAFFIC_UNDER_SURVIVING = "LIVE_LOW_TRAFFIC_UNDER_SURVIVING"
RC_BULLPEN_FATIGUE_LATE_INNINGS     = "BULLPEN_FATIGUE_LATE_INNINGS"
RC_HIGH_RISP_PRESSURE               = "HIGH_RISP_PRESSURE"
RC_LOB_DRAIN                        = "LOB_DRAIN"
# Phase 49 — spec-aligned live components.
RC_WALK_TRAFFIC_HIGH                = "WALK_TRAFFIC_HIGH"
RC_PITCH_COUNT_PRESSURE             = "PITCH_COUNT_PRESSURE"
RC_BULLPEN_ENTRY_TRAFFIC_RISK       = "BULLPEN_ENTRY_TRAFFIC_RISK"
RC_LOB_PRESSURE                     = "LOB_PRESSURE"  # spec alias for LOB_DRAIN

ALL_TRAFFIC_REASON_CODES = (
    RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC,
    RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH,
    RC_HIGH_TRAFFIC_UNDER_DANGER,
    RC_LOW_TRAFFIC_UNDER_SURVIVED,
    RC_LIVE_TRAFFIC_RISING,
    RC_LIVE_TRAFFIC_COLLAPSING,
    RC_LIVE_HIGH_TRAFFIC_UNDER_DANGER,
    RC_LIVE_LOW_TRAFFIC_UNDER_SURVIVING,
    RC_BULLPEN_FATIGUE_LATE_INNINGS,
    RC_HIGH_RISP_PRESSURE,
    RC_LOB_DRAIN,
)


# ─────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────
def _safe(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _score_component(value: Optional[float], spec: dict) -> int:
    """Linear interpolation between `low` and `high` thresholds."""
    if value is None:
        return 0
    low  = spec["low"]
    high = spec["high"]
    w    = spec["weight"]
    if high <= low:
        return w if value >= high else 0
    if value <= low:
        return 0
    if value >= high:
        return w
    return int(round(((value - low) / (high - low)) * w))


def _bucket_from_score(score: int) -> str:
    if score <= BUCKET_LOW_MAX:
        return BUCKET_LOW
    if score <= BUCKET_MEDIUM_MAX:
        return BUCKET_MEDIUM
    return BUCKET_HIGH


# ─────────────────────────────────────────────────────────────────────
# Stats hydration
# ─────────────────────────────────────────────────────────────────────
def compute_offense_window_metrics(raw_window: dict) -> dict:
    """Derive (OPS, OBP, SLG, HR/PA, XBH/PA, R/G) from a raw stat bag.

    Accepts the shape produced by the backtest's MLB Stats API
    extractor — fields default to 0 when missing.

    Returns a dict with only the float metrics (None when the
    underlying division is undefined).
    """
    ab  = _safe(raw_window.get("ab"))  or 0.0
    h   = _safe(raw_window.get("h"))   or 0.0
    db  = _safe(raw_window.get("doubles"))  or 0.0
    tr  = _safe(raw_window.get("triples"))  or 0.0
    hr  = _safe(raw_window.get("hr"))  or 0.0
    bb  = _safe(raw_window.get("bb"))  or 0.0
    hbp = _safe(raw_window.get("hbp")) or 0.0
    sf  = _safe(raw_window.get("sf"))  or 0.0
    runs = _safe(raw_window.get("runs")) or _safe(raw_window.get("r")) or 0.0
    n_games = _safe(raw_window.get("n_games")) or 0.0

    singles = max(0.0, h - db - tr - hr)
    tb      = singles + 2 * db + 3 * tr + 4 * hr
    pa_div  = ab + bb + hbp + sf
    obp     = (h + bb + hbp) / pa_div if pa_div > 0 else None
    slg     = tb / ab if ab > 0 else None
    ops     = (obp + slg) if (obp is not None and slg is not None) else None
    pa      = pa_div  # close enough for HR/PA, XBH/PA — sacrifices are negligible
    hr_rate  = (hr / pa) if pa > 0 else None
    xbh_rate = ((db + tr + hr) / pa) if pa > 0 else None
    rpg      = (runs / n_games) if n_games > 0 else None

    return {
        "ops":      round(ops, 4)      if ops      is not None else None,
        "obp":      round(obp, 4)      if obp      is not None else None,
        "slg":      round(slg, 4)      if slg      is not None else None,
        "hr_rate":  round(hr_rate, 4)  if hr_rate  is not None else None,
        "xbh_rate": round(xbh_rate, 4) if xbh_rate is not None else None,
        "runs_per_game": round(rpg, 3) if rpg      is not None else None,
        "n_games":  int(n_games),
    }


# ─────────────────────────────────────────────────────────────────────
# Score per team
# ─────────────────────────────────────────────────────────────────────
def compute_traffic_score(
    *,
    metrics: dict,
    recent_form_rpg: Optional[float] = None,
    implied_team_total: Optional[float] = None,
    hard_contact_proxy: Optional[float] = None,
) -> dict:
    """Build the composite 0-100 traffic score for a single team.

    Args:
        metrics: dict from ``compute_offense_window_metrics``.
        recent_form_rpg: optional runs/game over the last 5 games.
            Falls back to the 7d ``runs_per_game`` if absent.
        implied_team_total: optional pre-match implied team total (from
            book lines). Falls back to ``runs_per_game`` if absent.
        hard_contact_proxy: optional Statcast hard-hit % or barrel %.
            Falls back to ``slg`` if absent (still a useful proxy of
            quality of contact in the absence of Statcast).

    Returns:
        {
          "traffic_score":          int (0-100),
          "traffic_bucket":         "LOW_TRAFFIC" | "MEDIUM_TRAFFIC" | "HIGH_TRAFFIC",
          "components":             { name: int_score, ... },
          "raw":                    { name: float_input_value, ... },
          "engine_version":         "traffic_score.1",
        }
    """
    ops_val      = metrics.get("ops")
    obp_val      = metrics.get("obp")
    slg_val      = metrics.get("slg")
    hr_rate      = metrics.get("hr_rate")
    xbh_rate     = metrics.get("xbh_rate")
    rpg          = metrics.get("runs_per_game")
    recent_val   = recent_form_rpg if recent_form_rpg is not None else rpg
    hc_val       = hard_contact_proxy if hard_contact_proxy is not None else slg_val
    team_total   = implied_team_total if implied_team_total is not None else rpg

    components = {
        "ops":           _score_component(ops_val,    COMPONENTS["ops"]),
        "runs_per_game": _score_component(rpg,        COMPONENTS["runs_per_game"]),
        "obp":           _score_component(obp_val,    COMPONENTS["obp"]),
        "hr_rate":       _score_component(hr_rate,    COMPONENTS["hr_rate"]),
        "xbh_rate":      _score_component(xbh_rate,   COMPONENTS["xbh_rate"]),
        "hard_contact":  _score_component(hc_val,     COMPONENTS["hard_contact"]),
        "recent_form":   _score_component(recent_val, COMPONENTS["recent_form"]),
        "team_total":    _score_component(team_total, COMPONENTS["team_total"]),
    }
    score = sum(components.values())
    score = max(0, min(100, score))
    bucket = _bucket_from_score(score)
    return {
        "traffic_score":  score,
        "traffic_bucket": bucket,
        "components":     components,
        "raw": {
            "ops":      ops_val,
            "obp":      obp_val,
            "slg":      slg_val,
            "hr_rate":  hr_rate,
            "xbh_rate": xbh_rate,
            "runs_per_game": rpg,
            "recent_form":   recent_val,
            "hard_contact":  hc_val,
            "team_total":    team_total,
        },
        "engine_version": ENGINE_VERSION,
    }


def combine_team_traffic_scores(home: dict, away: dict) -> dict:
    """Combine two per-team traffic-score dicts into a game-level score.

    The combined score is the **mean** of the two team scores. The
    bucket re-classifies from that mean (NOT a max of the two buckets,
    so a single elite offense vs a punchless opponent doesn't auto-fire
    HIGH_TRAFFIC). The full breakdown is preserved for both teams.
    """
    h = home.get("traffic_score") or 0
    a = away.get("traffic_score") or 0
    combined = int(round((h + a) / 2.0))
    return {
        "traffic_score":  combined,
        "traffic_bucket": _bucket_from_score(combined),
        "home":           home,
        "away":           away,
        "engine_version": ENGINE_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────
# Interaction rule — observe-only by default
# ─────────────────────────────────────────────────────────────────────
def classify_bullpen_traffic_interaction(
    *,
    bullpen_era_7d_max: Optional[float],
    traffic_bucket:     Optional[str],
    is_under_pick:      bool,
    bullpen_high_threshold: float = 5.50,
    observe_only:       bool = False,
) -> dict:
    """Return the reason-code set + a verdict for a (bullpen, traffic) pair.

    Phase 46 — Promoted from observe-only to **active** by default. When
    ``observe_only=True`` is explicitly passed, the verdict is still
    produced but ``observe_only=True`` is set on the response so callers
    know NOT to change picks (preserved for backtest replay use).

    Verdicts:
        * ``"penalize_under"`` — bullpen vulnerable AND high traffic.
          For Under picks the engine should cap confidence, raise risk
          to HIGH and recommend PASS (or a protected line).
        * ``"hold_under"`` — bullpen vulnerable BUT low traffic. The
          Under is statistically still rentable in this cohort.
        * ``"no_signal"`` — bullpen not vulnerable OR data insufficient.
    """
    reason_codes: list[str] = []
    if bullpen_era_7d_max is None or traffic_bucket is None:
        return {
            "verdict":      "no_signal",
            "reason_codes": reason_codes,
            "observe_only": observe_only,
            "engine_version": ENGINE_VERSION,
        }
    bullpen_vulnerable = bullpen_era_7d_max > bullpen_high_threshold
    if not bullpen_vulnerable:
        return {
            "verdict":      "no_signal",
            "reason_codes": reason_codes,
            "observe_only": observe_only,
            "engine_version": ENGINE_VERSION,
        }

    if traffic_bucket == BUCKET_HIGH:
        reason_codes.append(RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC)
        if is_under_pick:
            reason_codes.append(RC_HIGH_TRAFFIC_UNDER_DANGER)
        return {
            "verdict":      "penalize_under" if is_under_pick else "no_signal",
            "reason_codes": reason_codes,
            "observe_only": observe_only,
            "engine_version": ENGINE_VERSION,
        }
    if traffic_bucket == BUCKET_LOW:
        reason_codes.append(RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH)
        if is_under_pick:
            reason_codes.append(RC_LOW_TRAFFIC_UNDER_SURVIVED)
        return {
            "verdict":      "hold_under" if is_under_pick else "no_signal",
            "reason_codes": reason_codes,
            "observe_only": observe_only,
            "engine_version": ENGINE_VERSION,
        }
    # MEDIUM_TRAFFIC — uncertain zone, no aggressive verdict.
    reason_codes.append(RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH)
    return {
        "verdict":      "no_signal",
        "reason_codes": reason_codes,
        "observe_only": observe_only,
        "engine_version": ENGINE_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────
# Phase 46 / Phase 49 — Live Traffic Score (spec-aligned components)
# ─────────────────────────────────────────────────────────────────────
# Inning-based pitch-count expectation. A starter at typical pace throws
# ~15-18 pitches per inning. Below the lower bound the bullpen is fresh;
# above the upper bound the staff is gassed and traffic is more dangerous.
PITCH_COUNT_PER_INNING_EXPECTED = 16
PITCH_COUNT_FATIGUE_THRESHOLD   = 1.20  # actual / expected >= 1.20 ⇒ fatigue
REG_INNINGS                     = 9.0

# Phase 49 — Spec-aligned component schema. Sum = 100, no single
# component exceeds the 25% safety cap. Each component is expressed as a
# direct "points scored" out of its weight rather than a continuous
# interpolation, so the output keys mirror the Phase-49 product spec.
LIVE_COMPONENT_WEIGHTS = {
    "hits":               15,
    "walks":              15,   # walks create hidden traffic — high weight
    "risp":               18,   # RISP > raw hits per the spec
    "lob":                10,
    "pitch_count":        12,
    "bullpen_entry":      10,
    "home_runs":          12,   # HR matters but cannot dominate (≤ 25%)
    "defensive_pressure": 8,    # errors + wild pitches + passed balls + SB allowed
}
LIVE_COMPONENT_CAP_PCT = 25  # no single component contributes > 25% of score


def _live_offense_summary(team_live: dict, innings_played: float) -> dict:
    """Aggregate a single team's live offensive pressure stats.

    Accepts a flexible field map so different providers can be ingested
    without renaming. All numeric fields default to 0 / None.
    """
    pa  = _safe(team_live.get("plate_appearances")) or _safe(team_live.get("pa")) or 0.0
    ab  = _safe(team_live.get("at_bats"))           or _safe(team_live.get("ab")) or 0.0
    h   = _safe(team_live.get("hits"))              or _safe(team_live.get("h"))  or 0.0
    bb  = _safe(team_live.get("walks"))             or _safe(team_live.get("bb")) or 0.0
    hbp = _safe(team_live.get("hbp"))               or 0.0
    hr  = _safe(team_live.get("home_runs"))         or _safe(team_live.get("hr")) or 0.0
    runs = _safe(team_live.get("runs"))             or _safe(team_live.get("r")) or 0.0
    lob = _safe(team_live.get("left_on_base"))      or _safe(team_live.get("lob")) or 0.0
    runners_on_base = _safe(team_live.get("runners_on_base")) or 0.0
    risp_opps = (
        _safe(team_live.get("risp_opportunities"))
        or _safe(team_live.get("at_bats_with_risp"))
        or 0.0
    )
    risp_hits = (
        _safe(team_live.get("risp_hits"))
        or _safe(team_live.get("hits_with_risp"))
        or 0.0
    )
    # Defensive-pressure raw events from the OPPOSING defense — the
    # caller passes the offense bag, and these fields capture pressure
    # the offense generated against the defense.
    errors_forced = _safe(team_live.get("errors_forced")) or 0.0
    wild_pitches  = _safe(team_live.get("wild_pitches"))  or 0.0
    passed_balls  = _safe(team_live.get("passed_balls"))  or 0.0
    stolen_bases  = (_safe(team_live.get("stolen_bases"))
                     or _safe(team_live.get("sb"))) or 0.0
    # Indicator: did the starter come out (i.e. bullpen entered)?
    bullpen_entered = bool(team_live.get("bullpen_entered")
                           or team_live.get("starter_removed"))
    pitch_count = _safe(team_live.get("pitch_count")) \
                  or _safe(team_live.get("pitches_thrown"))

    # Derived rates.
    pa_eff = pa or (ab + bb + hbp)
    runners_reached = h + bb + hbp
    return {
        "pa":               pa_eff,
        "h":                h,
        "bb":               bb,
        "hbp":              hbp,
        "hr":               hr,
        "runs":             runs,
        "lob":              lob,
        "runners_on_base":  runners_on_base,
        "runners_reached":  runners_reached,
        "risp_opportunities": risp_opps,
        "risp_hits":        risp_hits,
        "errors_forced":    errors_forced,
        "wild_pitches":     wild_pitches,
        "passed_balls":     passed_balls,
        "stolen_bases":     stolen_bases,
        "bullpen_entered":  bullpen_entered,
        "pitch_count":      pitch_count,
        "innings_played":   innings_played,
    }


def _score_capped(points: float, weight: int) -> int:
    """Round + clamp a component to its weight (and to LIVE_COMPONENT_CAP_PCT)."""
    cap = min(weight, LIVE_COMPONENT_CAP_PCT)
    return int(round(max(0.0, min(cap, points))))


def _score_hits(s: dict) -> int:
    """Combined hits / inning. League average ≈ 1.0 H/inning.
    1.5+ H/inning ⇒ full weight."""
    n = s.get("innings_played") or 0.5
    h_per_inning = (s.get("h") or 0) / max(0.5, n)
    pts = (h_per_inning - 0.7) / (1.5 - 0.7) * LIVE_COMPONENT_WEIGHTS["hits"]
    return _score_capped(pts, LIVE_COMPONENT_WEIGHTS["hits"])


def _score_walks(s: dict) -> int:
    """Walks per inning. 0.6+/inning ⇒ full weight (hidden traffic high)."""
    n = s.get("innings_played") or 0.5
    bb_per_inning = (s.get("bb") or 0) / max(0.5, n)
    pts = bb_per_inning / 0.60 * LIVE_COMPONENT_WEIGHTS["walks"]
    return _score_capped(pts, LIVE_COMPONENT_WEIGHTS["walks"])


def _score_risp(s: dict) -> int:
    """RISP hit rate. > 35% ⇒ full weight. Falls back to RISP volume
    (opportunities/inning) when ``risp_opportunities`` is 0 so we still
    reward teams generating threats even if they haven't converted yet.
    """
    opps = s.get("risp_opportunities") or 0
    if opps > 0:
        rate = (s.get("risp_hits") or 0) / opps
        pts = (rate - 0.10) / (0.35 - 0.10) * LIVE_COMPONENT_WEIGHTS["risp"]
    else:
        # Fallback: rough proxy from runners on base ÷ innings.
        n = s.get("innings_played") or 0.5
        opp_proxy = (s.get("runners_reached") or 0) / max(0.5, n)
        pts = (opp_proxy - 0.5) / 1.5 * LIVE_COMPONENT_WEIGHTS["risp"]
    return _score_capped(pts, LIVE_COMPONENT_WEIGHTS["risp"])


def _score_lob(s: dict) -> int:
    """LOB indicates traffic existed even if runs didn't convert."""
    n = s.get("innings_played") or 0.5
    lob_per_inning = (s.get("lob") or 0) / max(0.5, n)
    pts = lob_per_inning / 1.0 * LIVE_COMPONENT_WEIGHTS["lob"]  # 1+ LOB/inn ⇒ full
    return _score_capped(pts, LIVE_COMPONENT_WEIGHTS["lob"])


def _score_pitch_count(home_pc: Optional[float], away_pc: Optional[float],
                       innings_played: float) -> int:
    """Late-game pitch-count pressure. Combines both sides."""
    if home_pc is None and away_pc is None:
        return 0
    total = (home_pc or 0) + (away_pc or 0)
    expected = innings_played * 2 * PITCH_COUNT_PER_INNING_EXPECTED
    ratio = total / max(1.0, expected)
    # 1.0 ratio neutral; 1.30+ full weight.
    pts = (ratio - 1.0) / 0.30 * LIVE_COMPONENT_WEIGHTS["pitch_count"]
    return _score_capped(pts, LIVE_COMPONENT_WEIGHTS["pitch_count"])


def _score_bullpen_entry(home_bp: bool, away_bp: bool, inning: Optional[float]) -> int:
    """Award up to full weight when a starter has been removed already.

    Earlier removal ⇒ more pressure (starter usually goes 5+ innings).
    """
    if not (home_bp or away_bp):
        return 0
    full = LIVE_COMPONENT_WEIGHTS["bullpen_entry"]
    if inning is None:
        return full // 2
    # Removed by inning 4 ⇒ full; by inning 6 ⇒ half; by inning 7+ ⇒ quarter.
    if inning <= 4:
        return full
    if inning <= 6:
        return int(round(full * 0.65))
    return int(round(full * 0.35))


def _score_home_runs(s: dict) -> int:
    """HR matters but is hard-capped at 25% by the global cap."""
    hr = s.get("hr") or 0
    # 0 HR → 0; 1 HR → ~½ weight; 2+ HR → full weight (capped).
    pts = hr / 2.0 * LIVE_COMPONENT_WEIGHTS["home_runs"]
    return _score_capped(pts, LIVE_COMPONENT_WEIGHTS["home_runs"])


def _score_defensive_pressure(s: dict) -> int:
    """Errors / wild pitches / passed balls / SB allowed against the
    defense. Each event ≈ 1 pt up to the component weight."""
    events = (
        (s.get("errors_forced") or 0)
        + (s.get("wild_pitches") or 0)
        + (s.get("passed_balls") or 0)
        + (s.get("stolen_bases") or 0)
    )
    return _score_capped(float(events) * 1.5, LIVE_COMPONENT_WEIGHTS["defensive_pressure"])


def compute_live_traffic_score(
    *,
    inning:               Optional[float] = None,
    innings_played:       Optional[float] = None,
    home_live:            Optional[dict]  = None,
    away_live:            Optional[dict]  = None,
    pitch_count_home:     Optional[float] = None,
    pitch_count_away:     Optional[float] = None,
    pregame_traffic_score: Optional[int] = None,
    pregame_traffic_bucket: Optional[str] = None,
    is_under_pick:        bool            = True,
    # Phase 50 forward-compat — Defensive Breakdown Score feeds the
    # defensive_pressure component when explicit raw events aren't
    # available from the provider. None ⇒ falls back to event sum.
    defensive_breakdown_score: Optional[float] = None,
) -> dict:
    """Composite 0-100 LIVE traffic score for an in-progress MLB game.

    Phase-49 spec components (each capped at 25% of total score):
        ``hits, walks, risp, lob, pitch_count, bullpen_entry,
        home_runs, defensive_pressure``.

    Emits ``LIVE_TRAFFIC_RISING`` / ``LIVE_TRAFFIC_COLLAPSING`` against
    ``pregame_traffic_score`` when the delta crosses ±15.
    """
    home_live = home_live or {}
    away_live = away_live or {}
    if innings_played is None:
        innings_played = max(0.5, (inning or 5.0) - 0.5)
    innings_played = max(0.5, float(innings_played))

    home_summary = _live_offense_summary(home_live, innings_played)
    away_summary = _live_offense_summary(away_live, innings_played)
    # Combine both sides for game-level scoring (each component averaged).
    combined = {
        k: ((home_summary.get(k) or 0) + (away_summary.get(k) or 0)) / 2.0
        if isinstance(home_summary.get(k), (int, float))
        else (home_summary.get(k) or away_summary.get(k))
        for k in home_summary
    }
    combined["innings_played"] = innings_played

    # Per-component scoring (Phase-49 schema).
    pc_home = pitch_count_home or home_summary.get("pitch_count")
    pc_away = pitch_count_away or away_summary.get("pitch_count")
    bullpen_entered_any = bool(home_summary.get("bullpen_entered")
                               or away_summary.get("bullpen_entered"))

    components: dict[str, int] = {
        "hits":          _score_hits(combined),
        "walks":         _score_walks(combined),
        "risp":          _score_risp(combined),
        "lob":           _score_lob(combined),
        "pitch_count":   _score_pitch_count(pc_home, pc_away, innings_played),
        "bullpen_entry": _score_bullpen_entry(
            bool(home_summary.get("bullpen_entered")),
            bool(away_summary.get("bullpen_entered")),
            inning,
        ),
        "home_runs":     _score_home_runs(combined),
        "defensive_pressure": (
            _score_capped(float(defensive_breakdown_score) / 100
                          * LIVE_COMPONENT_WEIGHTS["defensive_pressure"],
                          LIVE_COMPONENT_WEIGHTS["defensive_pressure"])
            if defensive_breakdown_score is not None
            else _score_defensive_pressure(combined)
        ),
    }
    score = max(0, min(100, sum(components.values())))
    bucket = _bucket_from_score(score)

    # ── Reason codes ─────────────────────────────────────────────────
    reason_codes: list[str] = []
    if bucket == BUCKET_HIGH:
        if is_under_pick:
            reason_codes.append(RC_LIVE_HIGH_TRAFFIC_UNDER_DANGER)
        reason_codes.append(RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC)
    elif bucket == BUCKET_LOW:
        if is_under_pick:
            reason_codes.append(RC_LIVE_LOW_TRAFFIC_UNDER_SURVIVING)

    # Per-component flags fire regardless of bucket — independent signal.
    if components["risp"] >= LIVE_COMPONENT_WEIGHTS["risp"] * 0.70:
        reason_codes.append(RC_HIGH_RISP_PRESSURE)
    if components["lob"] >= LIVE_COMPONENT_WEIGHTS["lob"] * 0.70:
        reason_codes.append(RC_LOB_DRAIN)
    if components["walks"] >= LIVE_COMPONENT_WEIGHTS["walks"] * 0.60:
        reason_codes.append(RC_WALK_TRAFFIC_HIGH)
    if components["pitch_count"] >= LIVE_COMPONENT_WEIGHTS["pitch_count"] * 0.60:
        reason_codes.append(RC_PITCH_COUNT_PRESSURE)
    if components["bullpen_entry"] >= LIVE_COMPONENT_WEIGHTS["bullpen_entry"] * 0.60:
        reason_codes.append(RC_BULLPEN_ENTRY_TRAFFIC_RISK)
    # Bullpen fatigue: combined pitch count exceeds expected by ≥20% AND
    # we're in the late innings (≥6) — actionable signal.
    _total_pc = (pc_home or 0) + (pc_away or 0)
    if (_total_pc > 0
            and _total_pc >= innings_played * 2 * PITCH_COUNT_PER_INNING_EXPECTED * 1.20
            and (inning or 0) >= 6):
        reason_codes.append(RC_BULLPEN_FATIGUE_LATE_INNINGS)

    # ── Pregame delta ────────────────────────────────────────────────
    pregame_delta: Optional[int] = None
    if pregame_traffic_score is not None:
        pregame_delta = score - int(pregame_traffic_score)
        if pregame_delta >= 15:
            reason_codes.append(RC_LIVE_TRAFFIC_RISING)
        elif pregame_delta <= -15:
            reason_codes.append(RC_LIVE_TRAFFIC_COLLAPSING)

    return {
        "engine_version":        ENGINE_VERSION,
        "live_traffic_score":    score,
        "live_traffic_bucket":   bucket,
        "components":            components,
        "raw": {
            "innings_played":   innings_played,
            "inning":           inning,
            "home_summary":     home_summary,
            "away_summary":     away_summary,
            "pitch_count_home": pc_home,
            "pitch_count_away": pc_away,
            "bullpen_entered_any": bullpen_entered_any,
            "defensive_breakdown_score": defensive_breakdown_score,
        },
        "home":                  home_summary,
        "away":                  away_summary,
        "pregame_traffic_score": pregame_traffic_score,
        "pregame_traffic_bucket": pregame_traffic_bucket,
        "pregame_delta":         pregame_delta,
        "reason_codes":          list(dict.fromkeys(reason_codes)),  # dedupe
        "is_live":               True,
    }


def classify_live_bullpen_traffic_interaction(
    *,
    bullpen_era_7d_max:    Optional[float],
    live_traffic_bucket:   Optional[str],
    live_traffic_score:    Optional[int]  = None,
    pregame_delta:         Optional[int]  = None,
    is_under_pick:         bool           = True,
    bullpen_high_threshold: float         = 5.50,
) -> dict:
    """LIVE counterpart of ``classify_bullpen_traffic_interaction``.

    Uses the LIVE traffic bucket instead of the pregame composite, so
    the verdict reflects what's actually happening on the field. When
    a ``pregame_delta`` is supplied, the rising/collapsing tags are
    surfaced too — the engine can soften the verdict when traffic is
    visibly cooling off in live.
    """
    base = classify_bullpen_traffic_interaction(
        bullpen_era_7d_max=bullpen_era_7d_max,
        traffic_bucket=live_traffic_bucket,
        is_under_pick=is_under_pick,
        bullpen_high_threshold=bullpen_high_threshold,
    )
    extra: list[str] = []
    if pregame_delta is not None:
        if pregame_delta >= 15:
            extra.append(RC_LIVE_TRAFFIC_RISING)
        elif pregame_delta <= -15:
            extra.append(RC_LIVE_TRAFFIC_COLLAPSING)
    if live_traffic_bucket == BUCKET_HIGH and is_under_pick:
        extra.append(RC_LIVE_HIGH_TRAFFIC_UNDER_DANGER)
    elif live_traffic_bucket == BUCKET_LOW and is_under_pick:
        extra.append(RC_LIVE_LOW_TRAFFIC_UNDER_SURVIVING)
    base["reason_codes"] = list(dict.fromkeys((base.get("reason_codes") or []) + extra))
    base["live_traffic_score"]  = live_traffic_score
    base["live_traffic_bucket"] = live_traffic_bucket
    # When live traffic is collapsing AND verdict was "penalize_under",
    # soften to "monitor_under": the in-game evidence contradicts the
    # pregame setup.
    if base.get("verdict") == "penalize_under" and pregame_delta is not None and pregame_delta <= -15:
        base["verdict"] = "monitor_under"
        base["softened_by_live"] = True
    return base


__all__ = [
    "ENGINE_VERSION",
    "COMPONENTS",
    "LIVE_COMPONENT_WEIGHTS",
    "BUCKET_LOW",
    "BUCKET_MEDIUM",
    "BUCKET_HIGH",
    "RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC",
    "RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH",
    "RC_HIGH_TRAFFIC_UNDER_DANGER",
    "RC_LOW_TRAFFIC_UNDER_SURVIVED",
    "RC_LIVE_TRAFFIC_RISING",
    "RC_LIVE_TRAFFIC_COLLAPSING",
    "RC_LIVE_HIGH_TRAFFIC_UNDER_DANGER",
    "RC_LIVE_LOW_TRAFFIC_UNDER_SURVIVING",
    "RC_BULLPEN_FATIGUE_LATE_INNINGS",
    "RC_HIGH_RISP_PRESSURE",
    "RC_LOB_DRAIN",
    "RC_LOB_PRESSURE",
    "RC_WALK_TRAFFIC_HIGH",
    "RC_PITCH_COUNT_PRESSURE",
    "RC_BULLPEN_ENTRY_TRAFFIC_RISK",
    "ALL_TRAFFIC_REASON_CODES",
    "compute_offense_window_metrics",
    "compute_traffic_score",
    "combine_team_traffic_scores",
    "classify_bullpen_traffic_interaction",
    "compute_live_traffic_score",
    "classify_live_bullpen_traffic_interaction",
]
