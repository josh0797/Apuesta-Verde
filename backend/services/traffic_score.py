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

ALL_TRAFFIC_REASON_CODES = (
    RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC,
    RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH,
    RC_HIGH_TRAFFIC_UNDER_DANGER,
    RC_LOW_TRAFFIC_UNDER_SURVIVED,
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
) -> dict:
    """Return the reason-code set + a verdict for a (bullpen, traffic) pair.

    Verdicts:
        * ``"penalize_under"`` — bullpen vulnerable AND high traffic.
          For Under picks, the engine should consider penalizing the
          confidence or stepping up to a protected line.
        * ``"hold_under"`` — bullpen vulnerable BUT low traffic. The
          Under is statistically still rentable in this cohort.
        * ``"no_signal"`` — bullpen not vulnerable OR data insufficient.

    All callers should default to **observe_only** (record the
    reason codes but do NOT change the pick) until the engine has
    enough samples to enter active mode.
    """
    reason_codes: list[str] = []
    if bullpen_era_7d_max is None or traffic_bucket is None:
        return {
            "verdict":      "no_signal",
            "reason_codes": reason_codes,
            "observe_only": True,
            "engine_version": ENGINE_VERSION,
        }
    bullpen_vulnerable = bullpen_era_7d_max > bullpen_high_threshold
    if not bullpen_vulnerable:
        return {
            "verdict":      "no_signal",
            "reason_codes": reason_codes,
            "observe_only": True,
            "engine_version": ENGINE_VERSION,
        }

    if traffic_bucket == BUCKET_HIGH:
        reason_codes.append(RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC)
        if is_under_pick:
            reason_codes.append(RC_HIGH_TRAFFIC_UNDER_DANGER)
        return {
            "verdict":      "penalize_under" if is_under_pick else "no_signal",
            "reason_codes": reason_codes,
            "observe_only": True,
            "engine_version": ENGINE_VERSION,
        }
    if traffic_bucket == BUCKET_LOW:
        reason_codes.append(RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH)
        if is_under_pick:
            reason_codes.append(RC_LOW_TRAFFIC_UNDER_SURVIVED)
        return {
            "verdict":      "hold_under" if is_under_pick else "no_signal",
            "reason_codes": reason_codes,
            "observe_only": True,
            "engine_version": ENGINE_VERSION,
        }
    # MEDIUM_TRAFFIC — uncertain zone, no aggressive verdict.
    reason_codes.append(RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH)
    return {
        "verdict":      "no_signal",
        "reason_codes": reason_codes,
        "observe_only": True,
        "engine_version": ENGINE_VERSION,
    }


__all__ = [
    "ENGINE_VERSION",
    "COMPONENTS",
    "BUCKET_LOW",
    "BUCKET_MEDIUM",
    "BUCKET_HIGH",
    "RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC",
    "RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH",
    "RC_HIGH_TRAFFIC_UNDER_DANGER",
    "RC_LOW_TRAFFIC_UNDER_SURVIVED",
    "ALL_TRAFFIC_REASON_CODES",
    "compute_offense_window_metrics",
    "compute_traffic_score",
    "combine_team_traffic_scores",
    "classify_bullpen_traffic_interaction",
]
