"""Adaptive Line Recommender — Feature 6 (Phase 43).

Given an engine projection (e.g. 7.8 total runs) and a list of
available bookmaker lines (8.5, 9.0, 9.5, 10.0, 10.5), score each line
on four dimensions and pick three canonical recommendations:

  * **Value Line**     — best edge vs the projection
  * **Protected Line** — best survival probability with acceptable edge
  * **Ultra Safe Line** — maximum survival, accept low/no edge

Scoring metrics per line:
  value_score      — implied edge against the projection (0..1)
  survival_score   — Pr(line survives) given projection ± uncertainty
  push_probability — Pr(line === final value) at half-step granularity
  fragility_score  — composite penalty for variance, half-step misses

All math is pure + fail-soft.  Returns ``available:false`` with a
``DATA_INSUFFICIENT_FALLBACK`` reason when inputs are unusable.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

log = logging.getLogger("adaptive_line_recommender")

ENGINE_VERSION = "adaptive_line_recommender.1"

# ─────────────────────────────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────────────────────────────
# Default uncertainty around the projection (used when the caller
# doesn't supply one). Calibrated for MLB totals (~2.5 runs σ); the
# caller should override with sport-specific stdev when available.
DEFAULT_SIGMA = 2.5

# Minimum acceptable value_score for the "Protected" recommendation.
# Below this we still emit the line but downgrade the recommendation to
# "INSUFFICIENT_EDGE".
PROTECTED_MIN_VALUE = 0.15
ULTRA_SAFE_MIN_SURVIVAL = 0.78

RC_DATA_INSUFFICIENT_FALLBACK = "DATA_INSUFFICIENT_FALLBACK"


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _phi(x: float) -> float:
    """Standard normal CDF — pure math, no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ─────────────────────────────────────────────────────────────────────
# Per-line scoring
# ─────────────────────────────────────────────────────────────────────
def score_line(
    *,
    projection: float,
    line: float,
    is_under: bool,
    sigma: float = DEFAULT_SIGMA,
) -> dict:
    """Compute the four scores for one (projection, line) combination.

    Uses a Normal(projection, sigma²) model for the *final value*. Push
    probability is estimated as the probability that final lands within
    ±0.25 of the line (a half-integer line has 0 push by definition,
    but we still return a positive number for integer lines).
    """
    p = _safe_float(projection)
    l = _safe_float(line)
    s = max(_safe_float(sigma) or DEFAULT_SIGMA, 0.5)

    if p is None or l is None:
        return {
            "value_score": 0.0, "survival_score": 0.5,
            "push_probability": 0.0, "fragility_score": 1.0,
            "edge": 0.0, "implied_prob": 0.5,
        }

    # P(final < line) for Under, P(final > line) for Over.
    z = (l - p) / s
    p_under = _phi(z)
    p_over  = 1.0 - p_under
    win_prob = p_under if is_under else p_over

    # Push (integer line) — probability mass within ±0.5 of the line
    # is interpreted as "the line is the bookmaker's posted value and
    # an integer total would land exactly on it". Approximation is fine.
    push_prob = abs(_phi((l + 0.5 - p) / s) - _phi((l - 0.5 - p) / s)) \
                if abs(l - round(l)) < 1e-6 else 0.0

    edge = win_prob - 0.5
    # Value score: scaled edge, clipped to [0, 1].
    value_score = max(0.0, min(1.0, edge * 2.0))
    survival_score = max(0.0, min(1.0, win_prob))

    # Fragility: how close are we to flipping outcome? A line at the
    # projection has |edge|=0 → fragility=1. Far from projection → 0.
    # Half-step proximity (|line - projection| < 0.5) bumps fragility.
    distance = abs(l - p)
    fragility_score = max(0.0, min(1.0,
        1.0 - (distance / (2.0 * s)) + push_prob * 0.5
    ))

    return {
        "value_score":      round(value_score, 4),
        "survival_score":   round(survival_score, 4),
        "push_probability": round(push_prob, 4),
        "fragility_score":  round(fragility_score, 4),
        "edge":             round(edge, 4),
        "implied_prob":     round(win_prob, 4),
    }


# ─────────────────────────────────────────────────────────────────────
# Public top-level: recommend 3 lines (Value / Protected / Ultra Safe)
# ─────────────────────────────────────────────────────────────────────
def recommend_adaptive_lines(
    *,
    projection: Optional[float],
    available_lines: list[float] | None,
    is_under: bool,
    sigma: float = DEFAULT_SIGMA,
    cohort_bias_line_shift: float = 0.0,
) -> dict:
    """Build the 3-tier recommendation. Pure + fail-soft.

    ``cohort_bias_line_shift`` is the optional bias hint coming from
    ``line_learning_engine.compute_weighted_recommendation_bias``. When
    > 0 we nudge the Protected/Ultra Safe choices toward more
    protected lines (and the Value choice keeps the raw best edge).
    """
    if not isinstance(available_lines, list) or projection is None or not available_lines:
        return _empty()

    proj = _safe_float(projection)
    if proj is None:
        return _empty()

    lines = sorted({_safe_float(x) for x in available_lines if _safe_float(x) is not None})
    if not lines:
        return _empty()

    scored = []
    for ln in lines:
        sc = score_line(projection=proj, line=ln, is_under=is_under, sigma=sigma)
        scored.append({"line": ln, **sc})

    # Sort the scoring tables once and reuse.
    by_value    = sorted(scored, key=lambda d: d["value_score"],    reverse=True)
    by_survival = sorted(scored, key=lambda d: d["survival_score"], reverse=True)
    by_fragility = sorted(scored, key=lambda d: d["fragility_score"])

    value_pick = by_value[0]
    # Apply cohort bias when picking protected: push toward more
    # survival-friendly lines.  Positive shift means "give up some value
    # for safety" → pick at index proportional to shift.
    shift_steps = max(0, int(round(cohort_bias_line_shift * 2)))  # 0.5 = 1 step
    protected_idx = min(len(by_survival) - 1, shift_steps)
    protected_pick = by_survival[protected_idx]
    # Ultra-safe: line with maximum survival regardless of edge.
    ultra_pick = by_survival[0]

    # Edge: when the same line wins both value and protected, downgrade
    # protected so it offers genuine differentiation.
    if protected_pick["line"] == value_pick["line"] and len(by_survival) > 1:
        protected_pick = by_survival[1]
    if ultra_pick["line"] == protected_pick["line"] and len(by_survival) > 2:
        ultra_pick = by_survival[2]

    reasons = []
    if value_pick["value_score"] < PROTECTED_MIN_VALUE:
        reasons.append("LOW_VALUE_AVAILABLE")
    if ultra_pick["survival_score"] < ULTRA_SAFE_MIN_SURVIVAL:
        reasons.append("ULTRA_SAFE_THIN")
    if abs(cohort_bias_line_shift) > 1e-3:
        reasons.append("COHORT_BIAS_APPLIED")

    return {
        "available":       True,
        "engine_version":  ENGINE_VERSION,
        "projection":      proj,
        "is_under":        is_under,
        "sigma":           sigma,
        "cohort_bias_line_shift": cohort_bias_line_shift,
        "lines_scored":    scored,
        "value_line":      _format_tier(value_pick, tier="VALUE"),
        "protected_line":  _format_tier(protected_pick, tier="PROTECTED"),
        "ultra_safe_line": _format_tier(ultra_pick,     tier="ULTRA_SAFE"),
        "fragility_ranked": by_fragility[:3],
        "reason_codes":    reasons,
        "summary_es":      _build_summary_es(value_pick, protected_pick, ultra_pick, is_under),
    }


def _format_tier(pick: dict, *, tier: str) -> dict:
    return {
        "tier":             tier,
        "line":             pick["line"],
        "value_score":      pick["value_score"],
        "survival_score":   pick["survival_score"],
        "push_probability": pick["push_probability"],
        "fragility_score":  pick["fragility_score"],
        "edge":             pick.get("edge"),
        "implied_prob":     pick.get("implied_prob"),
    }


def _empty() -> dict:
    return {
        "available":      False,
        "engine_version": ENGINE_VERSION,
        "reason_codes":   [RC_DATA_INSUFFICIENT_FALLBACK],
        "value_line":     None,
        "protected_line": None,
        "ultra_safe_line": None,
        "summary_es":     "Sin datos suficientes para recomendar líneas adaptativas.",
    }


def _build_summary_es(value: dict, protected: dict, ultra: dict, is_under: bool) -> str:
    side = "Under" if is_under else "Over"
    return (
        f"{side} Valor: {value['line']} ({value['value_score']:.0%} edge). "
        f"Protegido: {protected['line']} ({protected['survival_score']:.0%} survival). "
        f"Ultra-Safe: {ultra['line']} ({ultra['survival_score']:.0%})."
    )


__all__ = [
    "ENGINE_VERSION",
    "DEFAULT_SIGMA",
    "score_line",
    "recommend_adaptive_lines",
]
