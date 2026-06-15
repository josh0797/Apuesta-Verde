"""MLB-F93 — Manual Odds Reprice (pure module).

Single source of truth for what happens when a user enters a manual odd
on an MLB pick / match card. Decoupled from HTTP/Mongo so it can be
called from:

  * The POST /api/mlb/picks/{pick_id}/manual-odds endpoint after
    multi-key pick lookup.
  * Any future bulk reprice tool.

It computes implied probability, edge, fair odds, EV and a decision
label, never raising on missing/invalid inputs.

Return contract::

    {
      "available": bool,
      "decision":  "VALUE" | "NO_VALUE" | "WATCHLIST" |
                   "MANUAL_ODDS_ONLY" | "INVALID",
      "edge":                 float | None,         # model_prob - implied
      "edge_pct":             float | None,         # × 100
      "fair_odds":            float | None,
      "implied_probability":  float | None,         # 0..1
      "model_probability":    float | None,         # 0..1
      "ev":                   float | None,         # per-1-unit EV
      "confidence_before":    float | None,
      "confidence_after":     float | None,
      "manual_odd":           float | None,
      "market":               str   | None,
      "line":                 float | None,
      "reason_codes":         list[str],
      "rationale":            str,
      "threshold":            float,
    }

Env::

    MLB_MANUAL_VALUE_EDGE_THRESHOLD   default 0.03
    MLB_MANUAL_WATCHLIST_TOLERANCE    default 0.02 (edge >= -0.02 is "close")
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

log = logging.getLogger("services.mlb_manual_odds_reprice")

# Reason codes — exported for the endpoint and test suites.
RC_REPRICE_APPLIED            = "MANUAL_ODDS_REPRICE_APPLIED"
RC_OVERRIDE_USED              = "MANUAL_ODDS_OVERRIDE_USED"
RC_EDGE_ABOVE_THRESHOLD       = "EDGE_ABOVE_VALUE_THRESHOLD"
RC_EDGE_BELOW_THRESHOLD       = "EDGE_BELOW_VALUE_THRESHOLD"
RC_EV_POSITIVE                = "EV_POSITIVE"
RC_EV_NEGATIVE                = "EV_NEGATIVE"
RC_MODEL_PROB_MISSING         = "MODEL_PROBABILITY_MISSING"
RC_PICK_CONTEXT_RECONSTRUCTED = "PICK_CONTEXT_RECONSTRUCTED"
RC_PICK_CONTEXT_NOT_FOUND     = "PICK_CONTEXT_NOT_FOUND"
RC_CLOSE_TO_VALUE             = "CLOSE_TO_VALUE_WATCHLIST"
RC_INVALID_ODDS               = "INVALID_MANUAL_ODDS"


def _threshold() -> float:
    try:
        return float(os.environ.get("MLB_MANUAL_VALUE_EDGE_THRESHOLD", "0.03"))
    except (TypeError, ValueError):
        return 0.03


def _watchlist_tol() -> float:
    try:
        return float(os.environ.get("MLB_MANUAL_WATCHLIST_TOLERANCE", "0.02"))
    except (TypeError, ValueError):
        return 0.02


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _normalize_prob(p: Any) -> Optional[float]:
    """Accept percentages (0..100) or fractions (0..1) and clamp to (0,1)."""
    v = _safe_float(p)
    if v is None:
        return None
    if v > 1.0:                 # caller passed 0-100
        v = v / 100.0
    if v <= 0.0 or v >= 1.0:    # ignore degenerate values
        return None
    return v


def _extract_model_probability(pick: Optional[dict]) -> tuple[Optional[float], list[str]]:
    """Resolve a model probability from the pick context, in priority order.

    Returns ``(prob_in_0_1_range, source_notes)``.
    """
    if not isinstance(pick, dict):
        return None, []

    notes: list[str] = []

    def _try(path: str, value: Any) -> Optional[float]:
        v = _normalize_prob(value)
        if v is not None:
            notes.append(path)
        return v

    # 1) Direct probabilities exposed by the engine.
    for key in ("probability", "cover_probability", "model_probability"):
        prob = _try(f"pick.{key}", pick.get(key))
        if prob is not None:
            return prob, notes
    # 2) Nested key_data block (modern engine).
    kd = pick.get("key_data") or {}
    if isinstance(kd, dict):
        for key in ("model_probability", "cover_probability", "probability"):
            prob = _try(f"pick.key_data.{key}", kd.get(key))
            if prob is not None:
                return prob, notes
    # 3) Legacy ``_mlb_script_v2`` block.
    v2 = pick.get("_mlb_script_v2") or {}
    if isinstance(v2, dict):
        for key in ("coverProbability", "estimatedProbability",
                    "modelProbability", "probability"):
            prob = _try(f"pick._mlb_script_v2.{key}", v2.get(key))
            if prob is not None:
                return prob, notes
    # 4) Estimated probability mirror.
    prob = _try("pick.estimated_probability", pick.get("estimated_probability"))
    if prob is not None:
        return prob, notes
    # 5) Last resort — derive from confidence_score (0..100) ONLY when no
    # real probability is available. Treat it as a *soft* signal and flag
    # it via MODEL_PROBABILITY_MISSING so the decision stays MANUAL_ODDS_ONLY.
    cs = _safe_float(pick.get("confidence_score"))
    if cs is not None and 0.0 <= cs <= 100.0:
        notes.append("pick.confidence_score (fallback, soft)")
        # We DO NOT return this as model_probability — it's not a real
        # probability. Caller marks the decision as MANUAL_ODDS_ONLY.
    return None, notes


def _decide(*, edge: Optional[float], ev: Optional[float],
            model_prob: Optional[float]) -> tuple[str, list[str]]:
    """Map edge/EV/model_prob into a decision label + reason codes."""
    codes: list[str] = []
    th  = _threshold()
    tol = _watchlist_tol()

    if model_prob is None:
        codes.append(RC_MODEL_PROB_MISSING)
        return "MANUAL_ODDS_ONLY", codes

    if edge is None or ev is None:
        codes.append(RC_MODEL_PROB_MISSING)
        return "MANUAL_ODDS_ONLY", codes

    if edge >= th and ev > 0:
        codes.append(RC_EDGE_ABOVE_THRESHOLD)
        codes.append(RC_EV_POSITIVE)
        return "VALUE", codes

    # Mark EV polarity for downstream UI.
    if ev > 0:
        codes.append(RC_EV_POSITIVE)
    else:
        codes.append(RC_EV_NEGATIVE)

    # Watchlist: edge is within `tol` *below* threshold (close to value).
    if edge >= (th - tol) - 1e-9:
        codes.append(RC_CLOSE_TO_VALUE)
        return "WATCHLIST", codes

    codes.append(RC_EDGE_BELOW_THRESHOLD)
    return "NO_VALUE", codes


def reprice_mlb_pick_with_manual_odds(
    pick: Optional[dict],
    manual_odd: Any,
    *,
    market: Optional[str] = None,
    line: Optional[float] = None,
    context_reconstructed: bool = False,
    confidence_before: Optional[float] = None,
) -> dict:
    """Compute the F93 reprice payload.

    Parameters
    ----------
    pick : dict | None
        Pick / minimal context (may be partial). When ``None`` the result
        is ``available=False`` with ``PICK_CONTEXT_NOT_FOUND``.
    manual_odd : float | str
        User-provided odd. Accepts ``1.85`` or ``"1,85"``.
    market, line : optional
        For audit only.
    context_reconstructed : bool
        Set to True when the pick context was reconstructed from
        ``matches`` (or any fallback) rather than read from ``pick_runs``.
    confidence_before : float | None
        Caller's confidence score (0..100). Mirrored to
        ``confidence_after`` (manual odds never decay confidence).

    Never raises.
    """
    threshold = _threshold()

    base: dict = {
        "available":            False,
        "decision":             "MANUAL_ODDS_ONLY",
        "edge":                 None,
        "edge_pct":             None,
        "fair_odds":            None,
        "implied_probability":  None,
        "model_probability":    None,
        "ev":                   None,
        "confidence_before":    confidence_before,
        "confidence_after":     confidence_before,
        "manual_odd":           None,
        "market":               market,
        "line":                 line,
        "reason_codes":         [],
        "rationale":            "",
        "threshold":            threshold,
    }

    # Validate odds.
    odd = _safe_float(manual_odd)
    if odd is None or odd < 1.01:
        base["decision"]      = "INVALID"
        base["reason_codes"]  = [RC_INVALID_ODDS]
        base["rationale"]     = "Cuota manual inválida (debe ser ≥ 1.01)."
        return base

    base["manual_odd"]           = round(odd, 3)
    implied                      = round(1.0 / odd, 6)
    base["implied_probability"]  = implied

    if not isinstance(pick, dict):
        base["reason_codes"] = [RC_OVERRIDE_USED, RC_PICK_CONTEXT_NOT_FOUND,
                                RC_MODEL_PROB_MISSING]
        base["rationale"]    = (
            "Cuota guardada como override; no se encontró el contexto del "
            "pick para recalcular value/EV."
        )
        return base

    # Try to surface confidence_before from the pick when caller didn't.
    if confidence_before is None:
        for k in ("confidence_score", "confidence", "score"):
            v = _safe_float(pick.get(k))
            if v is not None:
                base["confidence_before"] = v
                base["confidence_after"]  = v
                break

    model_prob, src_notes = _extract_model_probability(pick)
    base["model_probability"] = model_prob

    if model_prob is None:
        codes = [RC_OVERRIDE_USED, RC_MODEL_PROB_MISSING]
        if context_reconstructed:
            codes.append(RC_PICK_CONTEXT_RECONSTRUCTED)
        base["reason_codes"]  = codes
        base["rationale"]     = (
            f"Cuota {odd:.2f} aplicada, pero el engine no expone una "
            f"probabilidad estimada — solo informativo."
        )
        return base

    fair_odds = round(1.0 / model_prob, 3)
    edge      = round(model_prob - implied, 6)
    edge_pct  = round(edge * 100.0, 2)
    ev        = round(model_prob * (odd - 1.0) - (1.0 - model_prob), 6)

    decision, decide_codes = _decide(edge=edge, ev=ev, model_prob=model_prob)

    codes = [RC_REPRICE_APPLIED, RC_OVERRIDE_USED] + decide_codes
    if context_reconstructed:
        codes.append(RC_PICK_CONTEXT_RECONSTRUCTED)

    if decision == "VALUE":
        rationale = (
            f"Edge +{edge_pct:.1f}% con cuota {odd:.2f}: el engine estima "
            f"{model_prob*100:.1f}% vs implícita {implied*100:.1f}% "
            f"(EV {ev:+.3f})."
        )
    elif decision == "NO_VALUE":
        rationale = (
            f"Edge {edge_pct:+.1f}% con cuota {odd:.2f}: la cuota implica "
            f"{implied*100:.1f}% pero el engine estima sólo "
            f"{model_prob*100:.1f}% (fair {fair_odds:.2f})."
        )
    elif decision == "WATCHLIST":
        rationale = (
            f"Edge {edge_pct:+.1f}% con cuota {odd:.2f}: cerca de valor "
            f"(fair {fair_odds:.2f})."
        )
    else:
        rationale = (
            f"Cuota {odd:.2f} aplicada (informativo); el engine no entrega "
            "probabilidad confiable para decidir VALUE/NO_VALUE."
        )

    base.update({
        "available":            True,
        "decision":             decision,
        "edge":                 edge,
        "edge_pct":             edge_pct,
        "fair_odds":            fair_odds,
        "ev":                   ev,
        "reason_codes":         codes,
        "rationale":            rationale,
    })
    return base


def build_minimal_pick_context_from_match_doc(
    match_doc: Optional[dict],
    *,
    market: Optional[str] = None,
    line: Optional[float] = None,
) -> Optional[dict]:
    """Best-effort reconstruction of a pick-shaped dict from a `matches`
    document, so :func:`reprice_mlb_pick_with_manual_odds` can be called
    even when the pick is not in any recent ``pick_runs``.

    Surface fields the reprice cares about: probabilities, confidence,
    teams, kickoff, and the market/line passed in.
    """
    if not isinstance(match_doc, dict):
        return None

    ctx: dict = {
        "id":              match_doc.get("pick_id") or match_doc.get("id"),
        "match_id":        match_doc.get("match_id") or match_doc.get("id"),
        "game_pk":         match_doc.get("game_pk") or match_doc.get("gamePk"),
        "home_team":       match_doc.get("home_team") or match_doc.get("homeTeam"),
        "away_team":       match_doc.get("away_team") or match_doc.get("awayTeam"),
        "commence_date":   (match_doc.get("commence_date")
                            or (match_doc.get("commence_time") or "")[:10]
                            or None),
        "market":          market,
        "line":            line,
        "_reconstructed":  True,
    }

    # Pull probabilities from likely locations.
    for k in ("cover_probability", "model_probability", "probability",
              "estimated_probability"):
        if match_doc.get(k) is not None:
            ctx[k] = match_doc[k]

    # Surface key_data / _mlb_script_v2 sub-blocks unchanged.
    if isinstance(match_doc.get("key_data"), dict):
        ctx["key_data"] = match_doc["key_data"]
    if isinstance(match_doc.get("_mlb_script_v2"), dict):
        ctx["_mlb_script_v2"] = match_doc["_mlb_script_v2"]

    # Confidence fallback.
    cs = _safe_float(match_doc.get("confidence_score")
                     or match_doc.get("confidence"))
    if cs is not None:
        ctx["confidence_score"] = cs

    return ctx


__all__ = [
    # Reason codes.
    "RC_REPRICE_APPLIED", "RC_OVERRIDE_USED",
    "RC_EDGE_ABOVE_THRESHOLD", "RC_EDGE_BELOW_THRESHOLD",
    "RC_EV_POSITIVE", "RC_EV_NEGATIVE",
    "RC_MODEL_PROB_MISSING",
    "RC_PICK_CONTEXT_RECONSTRUCTED", "RC_PICK_CONTEXT_NOT_FOUND",
    "RC_CLOSE_TO_VALUE", "RC_INVALID_ODDS",
    # API.
    "reprice_mlb_pick_with_manual_odds",
    "build_minimal_pick_context_from_match_doc",
]
