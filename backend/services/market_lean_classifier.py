"""Market Lean Classifier — SINGLE SOURCE OF TRUTH for the "Lean" badge
shown in the Historical Profile panel (Historial profundo).

User-reported bug (May 2026):

    > Historial Profundo decía "LEAN OVER CARRERAS"
    > pero el pick final era "UNDER 9.5" con Expected Runs 6.7
    > Caso real: LA Angels vs Tampa Bay → Angels 4-1 Rays en la 1ra
      (script roto inmediatamente)

Root cause: the historical layer computed its own lean from
``projected − league_avg_runs`` (a heuristic vs the SEASON average) while
the engine's pick was computed against the actual market line. The two
calculations disagreed when the market line itself diverged from the
season baseline.

Fix: every "lean" surfaced to the user MUST be computed by this module,
which receives ``expected_runs`` and ``market_line`` as inputs and
applies the user's rules verbatim::

    expected_runs <= market_line - 1.0   →   LEAN UNDER
    expected_runs >= market_line + 1.0   →   LEAN OVER
    abs(expected_runs - market_line) < 1.0 → SIN LEAN CLARO  (NONE)

Pure function module — no IO.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("market_lean_classifier")
mismatch_logger = logging.getLogger("MARKET_LEAN_MISMATCH")


# User-specified thresholds (DO NOT change without product sign-off).
LEAN_DELTA_THRESHOLD = 1.0     # runs

# Confidence bands derived from the magnitude of the edge.
def _confidence_from_delta(delta_abs: float) -> int:
    """Map |expected_runs − market_line| into a 0-100 confidence."""
    if delta_abs >= 3.0:
        return 90
    if delta_abs >= 2.0:
        return 84
    if delta_abs >= 1.5:
        return 78
    if delta_abs >= 1.0:
        return 71
    if delta_abs >= 0.5:
        return 60
    return 50


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════════════════════
# Core
# ════════════════════════════════════════════════════════════════════════════
def classify_market_lean(
    *,
    expected_runs: float,
    market_line: float,
    p_under: Optional[float] = None,
    p_over: Optional[float] = None,
) -> dict:
    """Classify the lean for a Totals/Runs market.

    Parameters
    ----------
    expected_runs : float
        Engine's projection (e.g. 6.7).
    market_line : float
        Bookmaker line (e.g. 9.5).
    p_under, p_over : optional float
        Engine probabilities (0..1). Used to enrich the confidence and
        the reason text, but the LEAN decision is driven exclusively by
        ``expected_runs`` vs ``market_line`` per the user's rule.

    Returns
    -------
    {
        "lean":       "OVER" | "UNDER" | "NONE",
        "confidence": int 0..100,
        "reason":     str (Spanish),
        "delta":      float (signed: expected − line),
        "expected_runs": float,
        "market_line":  float,
    }
    """
    expected_runs = _f(expected_runs)
    market_line   = _f(market_line)
    delta = round(expected_runs - market_line, 2)
    delta_abs = abs(delta)

    if delta <= -LEAN_DELTA_THRESHOLD:
        lean = "UNDER"
    elif delta >= LEAN_DELTA_THRESHOLD:
        lean = "OVER"
    else:
        lean = "NONE"

    confidence = _confidence_from_delta(delta_abs)

    # Enrich confidence with engine probabilities when available.
    if lean == "UNDER" and p_under is not None and 0 <= p_under <= 1:
        confidence = max(confidence, int(round(p_under * 100)))
    elif lean == "OVER" and p_over is not None and 0 <= p_over <= 1:
        confidence = max(confidence, int(round(p_over * 100)))
    confidence = max(0, min(100, int(confidence)))

    if lean == "UNDER":
        reason = (
            f"Expected runs {expected_runs:.1f} vs línea {market_line:.1f} "
            f"(delta {delta:+.1f}). El motor proyecta {delta_abs:.1f} carreras "
            "por debajo del mercado."
        )
    elif lean == "OVER":
        reason = (
            f"Expected runs {expected_runs:.1f} vs línea {market_line:.1f} "
            f"(delta {delta:+.1f}). El motor proyecta {delta_abs:.1f} carreras "
            "por encima del mercado."
        )
    else:
        reason = (
            f"Expected runs {expected_runs:.1f} vs línea {market_line:.1f} "
            f"(delta {delta:+.1f}). No se detecta edge accionable."
        )

    return {
        "lean":          lean,
        "confidence":    confidence,
        "reason":        reason,
        "delta":         delta,
        "expected_runs": expected_runs,
        "market_line":   market_line,
        "version":       1,
    }


# ════════════════════════════════════════════════════════════════════════════
# Consistency validation
# ════════════════════════════════════════════════════════════════════════════
def _market_polarity(recommended_market: Optional[str]) -> Optional[str]:
    """Return 'OVER' / 'UNDER' / None for a recommended market label."""
    if not recommended_market:
        return None
    s = str(recommended_market).lower()
    if "over" in s:
        return "OVER"
    if "under" in s or "menos de" in s:
        return "UNDER"
    return None


def validate_lean_consistency(
    *,
    lean_payload: dict,
    recommended_market: Optional[str],
    game_id: Optional[str] = None,
) -> dict:
    """Return a consistency-report dict::

        {
            "consistent":         bool,
            "mismatch":           bool,
            "lean":               "OVER"|"UNDER"|"NONE",
            "recommended_market": str | None,
            "pick_polarity":      "OVER"|"UNDER"|None,
            "warning":            str | None,
        }

    When ``lean`` is OVER but the recommended market contains UNDER (or
    vice-versa), a structured warning is logged via the
    ``MARKET_LEAN_MISMATCH`` logger and the report is flagged with
    ``mismatch=True``. The caller (UI / API) is then expected to render
    "⚠ Revisión requerida" instead of the contradictory badge.
    """
    lean = (lean_payload or {}).get("lean") or "NONE"
    polarity = _market_polarity(recommended_market)
    mismatch = False
    warning = None

    if lean == "OVER" and polarity == "UNDER":
        mismatch = True
        warning = (
            "El motor proyecta OVER pero el pick recomendado es UNDER. "
            "Revisar fuente de proyección o lógica de mercado protegido."
        )
    elif lean == "UNDER" and polarity == "OVER":
        mismatch = True
        warning = (
            "El motor proyecta UNDER pero el pick recomendado es OVER. "
            "Revisar fuente de proyección o lógica de mercado protegido."
        )

    if mismatch:
        mismatch_logger.warning(
            "MARKET_LEAN_MISMATCH | game_id=%s expected_runs=%s "
            "market_line=%s lean=%s recommended_market=%s",
            game_id,
            (lean_payload or {}).get("expected_runs"),
            (lean_payload or {}).get("market_line"),
            lean,
            recommended_market,
        )

    return {
        "consistent":         not mismatch,
        "mismatch":           mismatch,
        "lean":               lean,
        "recommended_market": recommended_market,
        "pick_polarity":      polarity,
        "warning":            warning,
    }


def classify_and_validate(
    *,
    expected_runs: float,
    market_line: float,
    recommended_market: Optional[str] = None,
    p_under: Optional[float] = None,
    p_over: Optional[float] = None,
    game_id: Optional[str] = None,
) -> dict:
    """One-shot helper: classify the lean AND validate it against the
    recommended market. Returns the merged payload::

        {
            **lean_payload,
            "consistency": validate_lean_consistency(...),
            "display_lean": "OVER" | "UNDER" | "NONE" | "REVIEW_REQUIRED",
        }

    The ``display_lean`` is what the UI must surface:
        • "REVIEW_REQUIRED" → render "⚠ Revisión requerida".
        • anything else     → render the matching badge.
    """
    payload = classify_market_lean(
        expected_runs=expected_runs,
        market_line=market_line,
        p_under=p_under,
        p_over=p_over,
    )
    cons = validate_lean_consistency(
        lean_payload=payload,
        recommended_market=recommended_market,
        game_id=game_id,
    )
    display = "REVIEW_REQUIRED" if cons["mismatch"] else payload["lean"]
    return {
        **payload,
        "consistency":  cons,
        "display_lean": display,
        "game_id":      game_id,
    }


__all__ = [
    "LEAN_DELTA_THRESHOLD",
    "classify_market_lean",
    "validate_lean_consistency",
    "classify_and_validate",
]
