"""MLB-F93 / F93.1 — Manual Odds Reprice (pure module).

Single source of truth for what happens when a user enters a manual odd
on an MLB pick / match card. Decoupled from HTTP/Mongo so it can be
called from:

  * The POST /api/mlb/picks/{pick_id}/manual-odds endpoint after the
    multi-key pick lookup (and after the optional `pick_context` payload
    fallback introduced in F93.1).
  * Any future bulk reprice tool.

It computes implied probability, edge, fair odds, EV and a decision
label, never raising on missing/invalid inputs.

F93.1 — Expanded probability extraction
---------------------------------------
:func:`_extract_model_probability` now probes 15+ locations across the
pick payload (engine direct, `key_data`, `recommendation`, modern
`_mlb_script_v2`, `margin_v2`, `expected_runs_distribution`, `tail_risk`)
and selects the *side-aware* probability (probability_under vs
probability_over) based on the supplied market / selection. It accepts
fractions (0..1), percentages (0..100), and strings with ``%`` suffix.

When no real probability is available but a `confidence_score` is, the
caller stays at ``MANUAL_ODDS_ONLY`` and surfaces the reason code
``CONFIDENCE_USED_AS_WEAK_PROBABILITY_PROXY``.

F93.1 — Line inference
----------------------
:func:`infer_total_line` extracts the total line from the market or
selection string when ``line`` is not provided. Surfaces
``LINE_INFERRED_FROM_MARKET`` / ``LINE_INFERRED_FROM_SELECTION``.

Env::

    MLB_MANUAL_VALUE_EDGE_THRESHOLD   default 0.03
    MLB_MANUAL_WATCHLIST_TOLERANCE    default 0.02 (edge >= -0.02 is "close")
"""
from __future__ import annotations

import logging
import os
import re
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
# F93.1
RC_PICK_CONTEXT_FROM_REQUEST  = "PICK_CONTEXT_FROM_REQUEST_PAYLOAD"
RC_CONFIDENCE_WEAK_PROXY      = "CONFIDENCE_USED_AS_WEAK_PROBABILITY_PROXY"
RC_LINE_INFERRED_FROM_MARKET    = "LINE_INFERRED_FROM_MARKET"
RC_LINE_INFERRED_FROM_SELECTION = "LINE_INFERRED_FROM_SELECTION"


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
        try:
            f = float(v)
            return f if (f == f and f not in (float("inf"), float("-inf"))) else None
        except (TypeError, ValueError):
            return None
    s = str(v).strip()
    if not s:
        return None
    # Strip trailing % and locale comma.
    s = s.replace("%", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_prob(p: Any) -> Optional[float]:
    """Accept percentages (0..100), fractions (0..1), or strings with %.

    Clamps to ``(0.01, 0.99)``; values outside that band are ignored to
    avoid degenerate edges (e.g. a ``0.0`` shotmap returns).
    """
    v = _safe_float(p)
    if v is None:
        return None
    if v > 1.0:
        v = v / 100.0
    if v < 0.01 or v > 0.99:
        return None
    return v


# ─────────────────────────────────────────────────────────────────────
# F93.1 — Market-side detection (Under vs Over) for probability picking.
# ─────────────────────────────────────────────────────────────────────
_UNDER_RX = re.compile(
    r"(?i)\bUNDER\b|\bMENOS DE\b|\bMENOS_\d|\bU\b\s*\d|TOTAL[_ ]?UNDER|\bUND\b",
)
_OVER_RX  = re.compile(
    r"(?i)\bOVER\b|\b(?:MAS|MÁS|M[áa]s) DE\b|TOTAL[_ ]?OVER|\bOVR\b|\bO\b\s*\d",
)


def _market_side(market: Any, selection: Any) -> Optional[str]:
    """Return ``"under" | "over" | None`` based on market / selection."""
    blob = " ".join(str(x) for x in (market, selection) if x).strip()
    if not blob:
        return None
    if _UNDER_RX.search(blob):
        return "under"
    if _OVER_RX.search(blob):
        return "over"
    return None


# ─────────────────────────────────────────────────────────────────────
# F93.1 — Total-line inference from market / selection text.
# ─────────────────────────────────────────────────────────────────────
_LINE_RX = re.compile(r"(\d+(?:[.,]\d+)?)")


def infer_total_line(
    market: Optional[str] = None,
    selection: Optional[str] = None,
) -> tuple[Optional[float], list[str]]:
    """Best-effort total line extraction. Returns ``(line_or_None,
    reason_codes)``.

    Prefers matches inside the market string (cleaner) and falls back
    to the selection string. Numbers must look like a credible MLB
    totals line (≥ 4.5 and ≤ 20.5).
    """
    rcs: list[str] = []
    for source, code in (
        (market,    RC_LINE_INFERRED_FROM_MARKET),
        (selection, RC_LINE_INFERRED_FROM_SELECTION),
    ):
        if not source:
            continue
        for m in _LINE_RX.finditer(str(source)):
            try:
                v = float(m.group(1).replace(",", "."))
            except ValueError:
                continue
            if 4.5 <= v <= 20.5:
                rcs.append(code)
                return v, rcs
    return None, rcs


# ─────────────────────────────────────────────────────────────────────
# F93.1 — Expanded probability cascade with side awareness.
# ─────────────────────────────────────────────────────────────────────
def _extract_model_probability(
    pick: Optional[dict],
    *,
    market: Optional[str] = None,
    selection: Optional[str] = None,
) -> tuple[Optional[float], list[str]]:
    """Resolve a model probability from the pick context, in priority order.

    Returns ``(prob_in_0_1_range, source_notes)``. The ``source_notes``
    list documents the *first* probe that yielded a value so the audit
    payload can trace which field was used.
    """
    if not isinstance(pick, dict):
        return None, []

    notes: list[str] = []
    side = _market_side(market, selection)

    def _try(path: str, value: Any) -> Optional[float]:
        v = _normalize_prob(value)
        if v is not None:
            notes.append(path)
        return v

    # 1) Top-level probabilities exposed by the engine.
    for key in ("model_probability", "cover_probability", "probability"):
        p = _try(f"pick.{key}", pick.get(key))
        if p is not None:
            return p, notes

    # 2) key_data block (modern engine).
    kd = pick.get("key_data") or {}
    if isinstance(kd, dict):
        for key in ("model_probability", "cover_probability", "probability"):
            p = _try(f"pick.key_data.{key}", kd.get(key))
            if p is not None:
                return p, notes

    # 3) Recommendation block (frontend payload mirror).
    rec = pick.get("recommendation") or {}
    if isinstance(rec, dict):
        for key in ("model_probability", "cover_probability", "probability"):
            p = _try(f"pick.recommendation.{key}", rec.get(key))
            if p is not None:
                return p, notes

    # 4) ``_mlb_script_v2`` — generic cover/estimated probabilities first.
    v2 = pick.get("_mlb_script_v2") or {}
    if isinstance(v2, dict):
        for key in ("coverProbability", "estimatedProbability",
                    "modelProbability", "probability"):
            p = _try(f"pick._mlb_script_v2.{key}", v2.get(key))
            if p is not None:
                return p, notes

        # Side-aware: probabilityUnder / probabilityOver.
        if side == "under":
            p = _try("pick._mlb_script_v2.probabilityUnder",
                     v2.get("probabilityUnder"))
            if p is not None:
                return p, notes
        elif side == "over":
            p = _try("pick._mlb_script_v2.probabilityOver",
                     v2.get("probabilityOver"))
            if p is not None:
                return p, notes

    # 5) margin_v2 block.
    m2 = pick.get("margin_v2") or {}
    if isinstance(m2, dict):
        for key in ("coverProbability", "cover_probability",
                    "modelProbability", "probability"):
            p = _try(f"pick.margin_v2.{key}", m2.get(key))
            if p is not None:
                return p, notes

    # 6) Expected runs distribution — side aware.
    erd = pick.get("expected_runs_distribution") or {}
    if isinstance(erd, dict):
        if side == "under":
            for key in ("probability_under", "probabilityUnder",
                        "under_probability"):
                p = _try(f"pick.expected_runs_distribution.{key}", erd.get(key))
                if p is not None:
                    return p, notes
        elif side == "over":
            for key in ("probability_over", "probabilityOver",
                        "over_probability"):
                p = _try(f"pick.expected_runs_distribution.{key}", erd.get(key))
                if p is not None:
                    return p, notes

    # 7) Tail-risk — side aware.
    tr = pick.get("tail_risk") or {}
    if isinstance(tr, dict):
        if side == "under":
            for key in ("under_probability", "probability_under",
                        "probabilityUnder"):
                p = _try(f"pick.tail_risk.{key}", tr.get(key))
                if p is not None:
                    return p, notes
        elif side == "over":
            for key in ("over_probability", "probability_over",
                        "probabilityOver"):
                p = _try(f"pick.tail_risk.{key}", tr.get(key))
                if p is not None:
                    return p, notes

    # 8) Estimated probability mirror.
    p = _try("pick.estimated_probability", pick.get("estimated_probability"))
    if p is not None:
        return p, notes

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

    if ev > 0:
        codes.append(RC_EV_POSITIVE)
    else:
        codes.append(RC_EV_NEGATIVE)

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
    selection: Optional[str] = None,
    line: Optional[float] = None,
    context_reconstructed: bool = False,
    context_from_request: bool = False,
    confidence_before: Optional[float] = None,
    extra_reason_codes: Optional[list[str]] = None,
) -> dict:
    """Compute the F93 reprice payload (F93.1-aware).

    Parameters
    ----------
    pick : dict | None
        Pick / minimal context (may be partial). When ``None`` the result
        is ``available=False`` with ``PICK_CONTEXT_NOT_FOUND``.
    manual_odd : float | str
        User-provided odd. Accepts ``1.85`` or ``"1,85"``.
    market, selection, line : optional
        Used for market-side detection (Under vs Over), audit, and
        downstream UI. ``line`` is inferred from market / selection when
        ``None`` and added to the reason codes.
    context_reconstructed : bool
        True when ``pick`` came from ``matches`` collection rebuild.
    context_from_request : bool
        True when ``pick`` came from the API request payload
        (``pick_context`` field).
    confidence_before : float | None
        Caller's confidence score (0..100). Mirrored to
        ``confidence_after``.
    extra_reason_codes : list[str] | None
        Caller-supplied reason codes to merge into the final output.

    Never raises.
    """
    threshold = _threshold()
    extra_reason_codes = list(extra_reason_codes or [])

    # F93.1 — infer line when missing.
    if line is None:
        inferred_line, line_rcs = infer_total_line(market, selection)
        if inferred_line is not None:
            line = inferred_line
            extra_reason_codes.extend(line_rcs)

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
        "selection":            selection,
        "line":                 line,
        "reason_codes":         list(extra_reason_codes),
        "rationale":            "",
        "threshold":            threshold,
    }

    odd = _safe_float(manual_odd)
    if odd is None or odd < 1.01:
        base["decision"]      = "INVALID"
        base["reason_codes"]  = list({*extra_reason_codes, RC_INVALID_ODDS})
        base["rationale"]     = "Cuota manual inválida (debe ser ≥ 1.01)."
        return base

    base["manual_odd"]           = round(odd, 3)
    implied                      = round(1.0 / odd, 6)
    base["implied_probability"]  = implied

    if not isinstance(pick, dict):
        codes = [*extra_reason_codes, RC_OVERRIDE_USED,
                 RC_PICK_CONTEXT_NOT_FOUND, RC_MODEL_PROB_MISSING]
        base["reason_codes"] = list(dict.fromkeys(codes))
        base["rationale"]    = (
            "Cuota guardada como override; no se encontró el contexto del "
            "pick para recalcular value/EV."
        )
        return base

    # confidence_before from the pick when caller didn't pass one.
    if confidence_before is None:
        for k in ("confidence_score", "confidence", "score"):
            v = _safe_float(pick.get(k))
            if v is not None:
                base["confidence_before"] = v
                base["confidence_after"]  = v
                break
        # Also check inside `recommendation`.
        if base["confidence_before"] is None:
            rec = pick.get("recommendation") or {}
            if isinstance(rec, dict):
                v = _safe_float(rec.get("confidence_score")
                                or rec.get("confidence"))
                if v is not None:
                    base["confidence_before"] = v
                    base["confidence_after"]  = v

    model_prob, _src_notes = _extract_model_probability(
        pick, market=market, selection=selection,
    )
    base["model_probability"] = model_prob

    if model_prob is None:
        codes = [*extra_reason_codes, RC_OVERRIDE_USED, RC_MODEL_PROB_MISSING]
        if context_from_request:
            codes.append(RC_PICK_CONTEXT_FROM_REQUEST)
        elif context_reconstructed:
            codes.append(RC_PICK_CONTEXT_RECONSTRUCTED)
        # If a confidence_score is available, flag it explicitly as a
        # *weak* proxy — but DO NOT promote the decision out of
        # MANUAL_ODDS_ONLY.
        if base["confidence_before"] is not None:
            codes.append(RC_CONFIDENCE_WEAK_PROXY)
        base["reason_codes"]  = list(dict.fromkeys(codes))
        base["rationale"]     = (
            f"Cuota {odd:.2f} aplicada, pero el engine no expone una "
            "probabilidad estimada — solo informativo."
        )
        return base

    fair_odds = round(1.0 / model_prob, 3)
    edge      = round(model_prob - implied, 6)
    edge_pct  = round(edge * 100.0, 2)
    ev        = round(model_prob * (odd - 1.0) - (1.0 - model_prob), 6)

    decision, decide_codes = _decide(edge=edge, ev=ev, model_prob=model_prob)

    codes = [*extra_reason_codes, RC_REPRICE_APPLIED, RC_OVERRIDE_USED, *decide_codes]
    if context_from_request:
        codes.append(RC_PICK_CONTEXT_FROM_REQUEST)
    elif context_reconstructed:
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
        "reason_codes":         list(dict.fromkeys(codes)),
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
    document. See module docstring."""
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
    for k in ("cover_probability", "model_probability", "probability",
              "estimated_probability"):
        if match_doc.get(k) is not None:
            ctx[k] = match_doc[k]
    if isinstance(match_doc.get("key_data"), dict):
        ctx["key_data"] = match_doc["key_data"]
    if isinstance(match_doc.get("_mlb_script_v2"), dict):
        ctx["_mlb_script_v2"] = match_doc["_mlb_script_v2"]
    if isinstance(match_doc.get("expected_runs_distribution"), dict):
        ctx["expected_runs_distribution"] = match_doc["expected_runs_distribution"]
    if isinstance(match_doc.get("tail_risk"), dict):
        ctx["tail_risk"] = match_doc["tail_risk"]
    cs = _safe_float(match_doc.get("confidence_score")
                     or match_doc.get("confidence"))
    if cs is not None:
        ctx["confidence_score"] = cs
    return ctx


def build_pick_context_from_request_payload(
    payload: Optional[dict],
) -> Optional[dict]:
    """F93.1 — Trust the `pick_context` blob sent by the frontend.

    The frontend already mirrors the React `pick` object so the backend
    can simply forward the dict to :func:`reprice_mlb_pick_with_manual_odds`.
    A shallow validation strips obviously-empty payloads.
    """
    if not isinstance(payload, dict) or not payload:
        return None
    # If absolutely no probability-bearing field is present, return as-is
    # (the reprice function will route to MANUAL_ODDS_ONLY).
    return dict(payload)


__all__ = [
    # Reason codes.
    "RC_REPRICE_APPLIED", "RC_OVERRIDE_USED",
    "RC_EDGE_ABOVE_THRESHOLD", "RC_EDGE_BELOW_THRESHOLD",
    "RC_EV_POSITIVE", "RC_EV_NEGATIVE",
    "RC_MODEL_PROB_MISSING",
    "RC_PICK_CONTEXT_RECONSTRUCTED", "RC_PICK_CONTEXT_NOT_FOUND",
    "RC_CLOSE_TO_VALUE", "RC_INVALID_ODDS",
    # F93.1
    "RC_PICK_CONTEXT_FROM_REQUEST",
    "RC_CONFIDENCE_WEAK_PROXY",
    "RC_LINE_INFERRED_FROM_MARKET", "RC_LINE_INFERRED_FROM_SELECTION",
    # API.
    "reprice_mlb_pick_with_manual_odds",
    "build_minimal_pick_context_from_match_doc",
    "build_pick_context_from_request_payload",
    "infer_total_line",
]
