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
# Phase 46 — Live Traffic Score
# ─────────────────────────────────────────────────────────────────────
# Inning-based pitch-count expectation. A starter at typical pace throws
# ~15-18 pitches per inning. Below the lower bound the bullpen is fresh;
# above the upper bound the staff is gassed and traffic is more dangerous.
PITCH_COUNT_PER_INNING_EXPECTED = 16
PITCH_COUNT_FATIGUE_THRESHOLD   = 1.20  # actual / expected >= 1.20 ⇒ fatigue
REG_INNINGS                     = 9.0

# Live component weights — different mix than pregame because we have
# direct observation of pressure (RISP, LOB, contact quality).
LIVE_COMPONENTS = {
    "live_obp":          {"weight": 18, "low": 0.290, "high": 0.350},
    "live_runs_per_inning": {"weight": 14, "low": 0.40,  "high": 0.65},
    "live_hr_rate":      {"weight": 12, "low": 0.02,   "high": 0.05},
    "risp_pressure":     {"weight": 15, "low": 0.10,   "high": 0.40},
    "lob_drain":         {"weight": 10, "low": 0.50,   "high": 0.85},
    "hard_contact":      {"weight": 12, "low": 0.32,   "high": 0.45},
    "exit_velocity":     {"weight":  9, "low": 87.0,   "high": 91.0},
    "bullpen_fatigue":   {"weight": 10, "low": 1.00,   "high": 1.30},
}


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
    hard_contact = (
        _safe(team_live.get("hard_contact_rate"))
        or _safe(team_live.get("hard_hit_pct"))
    )
    exit_velocity = (
        _safe(team_live.get("exit_velocity_avg"))
        or _safe(team_live.get("avg_exit_velocity"))
    )

    # Derived metrics.
    pa_eff = pa or (ab + bb + hbp)
    obp = (h + bb + hbp) / pa_eff if pa_eff > 0 else None
    runners_reached = h + bb + hbp
    lob_rate = (lob / runners_reached) if runners_reached > 0 else None
    risp_rate = (risp_hits / risp_opps) if risp_opps > 0 else None
    hr_rate = (hr / pa_eff) if pa_eff > 0 else None
    runs_per_inning = (runs / innings_played) if innings_played > 0 else None
    return {
        "pa":                pa_eff,
        "obp":               obp,
        "lob":               lob,
        "lob_rate":          lob_rate,
        "risp_opportunities": risp_opps,
        "risp_hits":         risp_hits,
        "risp_rate":         risp_rate,
        "runs":              runs,
        "runs_per_inning":   runs_per_inning,
        "hr_rate":           hr_rate,
        "hard_contact":      hard_contact,
        "exit_velocity":     exit_velocity,
    }


def _pitch_count_fatigue_ratio(pitch_count: Optional[float], innings_played: float) -> Optional[float]:
    """Return actual / expected pitch count. > 1.0 means staff is over-extended."""
    if pitch_count is None or innings_played <= 0:
        return None
    expected = innings_played * PITCH_COUNT_PER_INNING_EXPECTED
    return round(float(pitch_count) / expected, 3) if expected > 0 else None


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
) -> dict:
    """Composite 0-100 LIVE traffic score for an in-progress MLB game.

    Surfaces in-game offensive pressure (RISP, LOB, contact quality,
    HR rate live, bullpen fatigue via pitch count) and compares against
    the pregame score when one is provided. Emits ``LIVE_TRAFFIC_RISING``
    or ``LIVE_TRAFFIC_COLLAPSING`` when the live score diverges by ≥ 15.

    Args:
        inning:           Current inning (1-9+). Used for late-innings tag.
        innings_played:   Innings completed by the relevant pitching side.
                          Falls back to ``inning - 0.5`` for half-inning ambiguity.
        home_live / away_live: live stat bags for each team (see
                          ``_live_offense_summary`` for accepted fields).
        pitch_count_home / pitch_count_away: cumulative team pitch counts.
        pregame_traffic_score / pregame_traffic_bucket: optional pregame
                          baselines for delta-based rising/collapsing tags.
        is_under_pick:    when True, surfaces under-specific reason codes.

    Returns:
        dict with ``live_traffic_score`` (0-100), ``live_traffic_bucket``,
        ``components`` (per-component points), ``home`` / ``away`` summaries,
        ``reason_codes``, ``pregame_delta``, ``engine_version``.
    """
    home_live = home_live or {}
    away_live = away_live or {}
    if innings_played is None:
        innings_played = max(0.5, (inning or 5.0) - 0.5)
    innings_played = max(0.5, float(innings_played))

    home_summary = _live_offense_summary(home_live, innings_played)
    away_summary = _live_offense_summary(away_live, innings_played)

    # Combined metrics (game-level pressure, not per side).
    def _avg(field: str) -> Optional[float]:
        vals = [home_summary.get(field), away_summary.get(field)]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else None

    live_obp        = _avg("obp")
    live_rpi        = _avg("runs_per_inning")
    live_hr_rate    = _avg("hr_rate")
    risp_pressure   = _avg("risp_rate")
    lob_drain       = _avg("lob_rate")
    hard_contact    = _avg("hard_contact")
    exit_velocity   = _avg("exit_velocity")

    # Bullpen-fatigue ratio uses the SUM of pitch counts vs both staffs'
    # expected pitches. A combined ratio > 1.20 means both staffs are
    # gassed and late traffic risk is elevated.
    total_pc = (pitch_count_home or 0) + (pitch_count_away or 0)
    if pitch_count_home is None and pitch_count_away is None:
        bullpen_fatigue: Optional[float] = None
    else:
        bullpen_fatigue = _pitch_count_fatigue_ratio(total_pc, innings_played * 2)

    component_inputs = {
        "live_obp":             live_obp,
        "live_runs_per_inning": live_rpi,
        "live_hr_rate":         live_hr_rate,
        "risp_pressure":        risp_pressure,
        "lob_drain":            lob_drain,
        "hard_contact":         hard_contact,
        "exit_velocity":        exit_velocity,
        "bullpen_fatigue":      bullpen_fatigue,
    }
    components = {
        name: _score_component(val, LIVE_COMPONENTS[name])
        for name, val in component_inputs.items()
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

    # Per-component flags fire regardless of overall bucket because they
    # carry independent informational value.
    if risp_pressure is not None and risp_pressure >= LIVE_COMPONENTS["risp_pressure"]["high"]:
        reason_codes.append(RC_HIGH_RISP_PRESSURE)
    if lob_drain is not None and lob_drain >= LIVE_COMPONENTS["lob_drain"]["high"]:
        reason_codes.append(RC_LOB_DRAIN)

    if bullpen_fatigue is not None and bullpen_fatigue >= PITCH_COUNT_FATIGUE_THRESHOLD:
        # Only fires when we're in late innings; otherwise it's not actionable.
        if inning is not None and inning >= 6:
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
            "live_obp":             live_obp,
            "live_runs_per_inning": live_rpi,
            "live_hr_rate":         live_hr_rate,
            "risp_pressure":        risp_pressure,
            "lob_drain":            lob_drain,
            "hard_contact":         hard_contact,
            "exit_velocity":        exit_velocity,
            "bullpen_fatigue":      bullpen_fatigue,
        },
        "home":                  home_summary,
        "away":                  away_summary,
        "pregame_traffic_score": pregame_traffic_score,
        "pregame_traffic_bucket": pregame_traffic_bucket,
        "pregame_delta":         pregame_delta,
        "reason_codes":          reason_codes,
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
    "LIVE_COMPONENTS",
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
    "ALL_TRAFFIC_REASON_CODES",
    "compute_offense_window_metrics",
    "compute_traffic_score",
    "combine_team_traffic_scores",
    "classify_bullpen_traffic_interaction",
    "compute_live_traffic_score",
    "classify_live_bullpen_traffic_interaction",
]
