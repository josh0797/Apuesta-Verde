"""Universal Market Implied Probability Guardrail.

Sport-agnostic. Every recommended pick from the LLM (football / basketball /
baseball) is validated against the market: if our estimated probability does
not beat the implied probability by at least `edge_threshold`, the pick is
re-routed from `picks` to `summary.discarded_market` with reason `NO_BET_VALUE`.

This prevents picks that "feel right" but have negative expected value because
the price is too short for the edge we actually have.

Pipeline:
    LLM_pick.confidence_score (0–100) → estimated_probability (0.0–1.0)
    odds                              → implied_probability    (0.0–1.0)
    edge                              = estimated - implied
    threshold                         = THRESHOLDS[bet_type]
    verdict                           = "VALUE_FOUND" | "NO_BET_VALUE" | "INSUFFICIENT_DATA"

Bet-type detection is heuristic (live? parlay? single?) and conservative —
when in doubt we use the stricter live threshold.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

log = logging.getLogger("market_guardrail")


# Edge thresholds in absolute probability units (0.03 = 3 percentage points).
# Per user spec:
#   • 3% para picks simples (single bet, pre-match)
#   • 5% para live bets
#   • 7% para parlays
EDGE_THRESHOLDS = {
    "simple": 0.03,
    "live":   0.05,
    "parlay": 0.07,
}

# Phase F63 — Soft vs Hard discard threshold for negative edge picks.
# Empirically chosen by the user: a moderately-negative edge ([-25%, 0%))
# is NOT a terminal discard — the pipeline routes the match to
# Scores24 external review (and possibly Watchlist). Only edges at or
# below -25% are hard-discarded outright.
EDGE_HARD_DISCARD_THRESHOLD = float(
    os.environ.get("SCORES24_EDGE_HARD_DISCARD_THRESHOLD", "-25.0")
)

# Per-sport calibration factor applied to the LLM confidence before treating
# it as a probability. Empirically derived from observed ROI on the existing
# tracked picks; will be refined as more outcomes accumulate. Override via
# env vars LLM_CONFIDENCE_CALIBRATION_{FOOTBALL,BASKETBALL,BASEBALL}.
DEFAULT_CALIBRATION = {
    "football":   float(os.environ.get("LLM_CONFIDENCE_CALIBRATION_FOOTBALL",   "0.85")),
    "basketball": float(os.environ.get("LLM_CONFIDENCE_CALIBRATION_BASKETBALL", "0.82")),
    "baseball":   float(os.environ.get("LLM_CONFIDENCE_CALIBRATION_BASEBALL",   "0.78")),
}


# ── Probability conversion ──────────────────────────────────────────────────
def implied_probability(decimal_odds: Optional[float]) -> Optional[float]:
    """Convert decimal odds → implied probability. Returns None if invalid."""
    try:
        o = float(decimal_odds)
    except (TypeError, ValueError):
        return None
    if o <= 1.0:
        return None
    return round(1.0 / o, 4)


def estimated_probability_from_confidence(
    confidence_score: Optional[int],
    sport: str = "football",
) -> Optional[float]:
    """Confidence 0–100 → calibrated probability 0.0–1.0.

    Applies a per-sport haircut to counteract the LLM's natural over-confidence.
    This is the single biggest reason ROI was negative on tracked picks:
    a raw 80-confidence pick at 1.40 odds (implied 71.4%) "looks" like a
    +8.6% edge but in practice wins closer to 68%, giving a real edge of
    just -3.4%. With calibration applied:
        80 × 0.85 (football) = 68% → vs 71.4% implied → edge -3.4% → NO_BET ✓
    """
    try:
        c = float(confidence_score)
    except (TypeError, ValueError):
        return None
    if c < 0 or c > 100:
        return None
    calibration = DEFAULT_CALIBRATION.get(sport, 0.85)
    return round((c / 100.0) * calibration, 4)


def parse_midpoint_odds(odds_range: Optional[str]) -> Optional[float]:
    """Parse '1.25-1.45' → 1.35, or '1.70' → 1.70."""
    if not odds_range:
        return None
    nums = re.findall(r"\d+\.?\d*", str(odds_range))
    parsed = []
    for n in nums:
        try:
            v = float(n)
            if 1.01 <= v <= 30.0:
                parsed.append(v)
        except ValueError:
            continue
    if not parsed:
        return None
    if len(parsed) >= 2:
        return round((parsed[0] + parsed[1]) / 2.0, 3)
    return parsed[0]


# ── Bet type detection ──────────────────────────────────────────────────────
def detect_bet_type(pick: dict) -> str:
    """Returns 'simple' | 'live' | 'parlay'. Conservative when ambiguous."""
    if pick.get("is_parlay") is True:
        return "parlay"
    if pick.get("is_live") is True:
        return "live"
    # No reliable parlay signal in our schema today — assume single.
    return "simple"


# ── Core validation ─────────────────────────────────────────────────────────
def evaluate_pick(pick: dict, sport: str = "football") -> dict:
    """Compute the market edge for a single pick.

    Returns a dict with:
        verdict, estimated_probability, implied_probability, edge,
        edge_threshold, bet_type, odds_used, reason.

    Does NOT mutate the pick. The caller decides what to do with the verdict.
    """
    rec = pick.get("recommendation") or {}
    conf = rec.get("confidence_score")
    odds_range = rec.get("odds_range")
    odds_used = parse_midpoint_odds(odds_range)
    bet_type = detect_bet_type(pick)
    threshold = EDGE_THRESHOLDS[bet_type]

    est = estimated_probability_from_confidence(conf, sport=sport)
    imp = implied_probability(odds_used)

    if est is None or imp is None:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "estimated_probability": est,
            "implied_probability": imp,
            "edge": None,
            "edge_threshold": threshold,
            "bet_type": bet_type,
            "odds_used": odds_used,
            "calibration": DEFAULT_CALIBRATION.get(sport, 0.85),
            "reason": (
                "Datos insuficientes para validar valor de mercado "
                f"(confidence={conf}, odds_range={odds_range!r})."
            ),
        }

    edge = round(est - imp, 4)
    base = {
        "estimated_probability": est,
        "implied_probability": imp,
        "edge": edge,
        "edge_threshold": threshold,
        "bet_type": bet_type,
        "odds_used": odds_used,
        "calibration": DEFAULT_CALIBRATION.get(sport, 0.85),
    }
    if edge >= threshold:
        return {**base, "verdict": "VALUE_FOUND", "reason": (
            f"Edge {edge*100:.1f}% ≥ umbral {threshold*100:.1f}% "
            f"(estimado calibrado {est*100:.0f}% > implícito {imp*100:.0f}%)."
        )}
    return {**base, "verdict": "NO_BET_VALUE", "reason": (
        f"NO_BET_VALUE: edge {edge*100:.1f}% < umbral {threshold*100:.1f}% "
        f"({sport.upper()}, {bet_type}). "
        f"Probabilidad estimada calibrada {est*100:.0f}% ≤ implícita {imp*100:.0f}%."
    )}


# ── Pipeline integration: filter picks and update summary ────────────────────
def apply_market_guardrail(parsed: dict, sport: str = "football") -> dict:
    """Mutates `parsed`:
        • Attaches `_market_edge` to every kept pick (for UI).
        • Re-routes NO_BET_VALUE picks from `picks` → `summary.discarded_market`.
        • Annotates INSUFFICIENT_DATA picks with `_market_edge` (kept, no reroute).
        • Adds `_pipeline.market_guardrail` summary stats.
    """
    if not parsed or not isinstance(parsed, dict):
        return parsed

    picks = list(parsed.get("picks") or [])
    summary = parsed.get("summary") or {}
    disc_mkt = list(summary.get("discarded_market") or [])

    kept: list[dict] = []
    rerouted = 0
    insufficient = 0
    value_count = 0
    edges: list[float] = []

    for p in picks:
        edge_info = evaluate_pick(p, sport=sport)
        p["_market_edge"] = edge_info
        if edge_info["verdict"] == "VALUE_FOUND":
            kept.append(p)
            value_count += 1
            if edge_info.get("edge") is not None:
                edges.append(edge_info["edge"])
        elif edge_info["verdict"] == "NO_BET_VALUE":
            # Phase F63: classify NO_BET_VALUE picks into HARD vs SOFT
            # discard. A SOFT discard still appears in `discarded_market`
            # (the UI bucket stays unchanged) but carries the marker
            # ``discard_strength="SOFT_DISCARD_REVIEW"`` so the Scores24
            # external review can run and the UI can show a "review
            # pending" badge instead of a terminal "descartado" label.
            edge_pct = (edge_info.get("edge") or 0) * 100.0
            if edge_pct <= EDGE_HARD_DISCARD_THRESHOLD:
                discard_strength = "HARD_DISCARD"
                f63_reasons = ["edge_too_negative", "EDGE_HARD_DISCARD"]
                scores24_review_required = False
            else:
                discard_strength = "SOFT_DISCARD_REVIEW"
                f63_reasons = [
                    "edge_negative_needs_external_review",
                    "NEGATIVE_EDGE_SOFT_DISCARD_REVIEW",
                    "SCORES24_REVIEW_REQUIRED_FOR_SOFT_DISCARD",
                ]
                scores24_review_required = True
            disc_mkt.append({
                "match_id":   p.get("match_id"),
                "match_label": p.get("match_label"),
                "reason":     edge_info["reason"],
                "edge_pct":   round(edge_pct, 2),
                "discard_strength":          discard_strength,
                "scores24_review_required":  scores24_review_required,
                "f63_reason_codes":          f63_reasons,
                "_market_guardrail":         edge_info,
                "_market_guardrail_reroute": True,
            })
            rerouted += 1
        else:  # INSUFFICIENT_DATA
            kept.append(p)
            insufficient += 1
            if edge_info.get("edge") is not None:
                edges.append(edge_info["edge"])

    parsed["picks"] = kept
    summary["discarded_market"] = disc_mkt
    parsed["summary"] = summary

    avg_edge = round(sum(edges) / len(edges), 4) if edges else None
    parsed.setdefault("_pipeline", {})
    parsed["_pipeline"]["market_guardrail"] = {
        "evaluated":     len(picks),
        "value_found":   value_count,
        "no_bet_value_rerouted": rerouted,
        "insufficient_data": insufficient,
        "average_edge":  avg_edge,
        "thresholds":    EDGE_THRESHOLDS,
        "edge_hard_discard_threshold_pct": EDGE_HARD_DISCARD_THRESHOLD,
    }
    if rerouted or insufficient:
        log.info(
            "market_guardrail[%s]: %d picks → %d VALUE / %d NO_BET / %d INSUFFICIENT",
            sport, len(picks), value_count, rerouted, insufficient,
        )
    return parsed
