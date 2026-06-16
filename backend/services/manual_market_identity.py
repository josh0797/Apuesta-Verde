"""Phase F83 — Manual Market Identity + Manual Odds Injection.

When the engine detects a price but cannot identify the market family
(``REQUIRES_MARKET_IDENTIFICATION``), this module lets the operator
assign the market identity manually and inject a manual odd to compute
the edge.

Public:
    * ``MANUAL_MARKET_TYPES``      — whitelisted market families.
    * ``MARKET_OPTIONS``           — selections + lines per market.
    * ``validate_manual_payload()``— validates the request.
    * ``recalculate_with_manual_market()`` — produces the recalculated
      pick payload (edge, fragility, confidence, verdict).

The original detected odd is **never overwritten** — it is preserved
in ``manual_market_identity.detected_odd``; the manual one lives in
``manual_market_identity.manual_odd``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

MANUAL_MARKET_TYPES = [
    "DOUBLE_CHANCE", "DNB", "MATCH_WINNER", "TOTAL_GOALS",
    "BTTS", "CORNERS_TOTAL", "HANDICAP", "ASIAN_HANDICAP",
]

# selection options + valid lines per market.
MARKET_OPTIONS: dict[str, dict[str, Any]] = {
    "DOUBLE_CHANCE": {
        "selections": ["1X", "X2", "12"],
        "requires_line": False,
        "allowed_lines": [],
    },
    "DNB": {
        "selections": ["HOME", "AWAY"],
        "requires_line": False,
        "allowed_lines": [],
    },
    "MATCH_WINNER": {
        "selections": ["HOME", "DRAW", "AWAY"],
        "requires_line": False,
        "allowed_lines": [],
    },
    "TOTAL_GOALS": {
        "selections":    ["OVER", "UNDER"],
        "requires_line": True,
        "allowed_lines": [0.5, 1.5, 2.5, 3.5, 4.5],
    },
    "BTTS": {
        "selections": ["YES", "NO"],
        "requires_line": False,
        "allowed_lines": [],
    },
    "CORNERS_TOTAL": {
        "selections":    ["OVER", "UNDER"],
        "requires_line": True,
        "allowed_lines": [7.5, 8.5, 9.5, 10.5, 11.5],
    },
    "HANDICAP": {
        "selections":    ["HOME", "AWAY"],
        "requires_line": True,
        "allowed_lines": [-2.5, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.5],
    },
    "ASIAN_HANDICAP": {
        "selections":    ["HOME", "AWAY"],
        "requires_line": True,
        "allowed_lines": [-2.5, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.5],
    },
}


# ─────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────
def validate_manual_payload(payload: dict) -> tuple[bool, Optional[str]]:
    """Returns (ok, error_message)."""
    if not isinstance(payload, dict):
        return False, "Payload inválido."

    market_type = payload.get("market_type")
    if market_type not in MANUAL_MARKET_TYPES:
        return False, f"market_type debe ser uno de: {MANUAL_MARKET_TYPES}"

    opts = MARKET_OPTIONS[market_type]
    selection = (payload.get("selection") or "").upper()
    if selection not in opts["selections"]:
        return False, (
            f"selection '{selection}' inválido para {market_type}. "
            f"Permitidos: {opts['selections']}"
        )

    line = payload.get("line")
    if opts["requires_line"]:
        try:
            line_f = float(line) if line is not None else None
        except (TypeError, ValueError):
            return False, f"line debe ser numérico para {market_type}."
        if line_f is None:
            return False, f"line es requerido para {market_type}."
        if opts["allowed_lines"] and line_f not in opts["allowed_lines"]:
            return False, (
                f"line {line_f} no permitido para {market_type}. "
                f"Permitidos: {opts['allowed_lines']}"
            )

    manual_odd = payload.get("manual_odd")
    if manual_odd is None:
        return False, "manual_odd es requerido para recalcular."
    try:
        odd_f = float(manual_odd)
    except (TypeError, ValueError):
        return False, "manual_odd debe ser numérico."
    if odd_f < 1.01:
        return False, "manual_odd debe ser >= 1.01."

    return True, None


# ─────────────────────────────────────────────────────────────────────
# Identity builder
# ─────────────────────────────────────────────────────────────────────
def _identity_key(market_type: str, selection: str, line: Optional[float]) -> str:
    if market_type == "TOTAL_GOALS":
        return f"TOTAL_GOALS:{selection}:{line}"
    if market_type == "CORNERS_TOTAL":
        return f"CORNERS_TOTAL:{selection}:{line}"
    if market_type in ("HANDICAP", "ASIAN_HANDICAP"):
        return f"{market_type}:{selection}:{line}"
    return f"{market_type}:{selection}"


# ─────────────────────────────────────────────────────────────────────
# Recalculate
# ─────────────────────────────────────────────────────────────────────
def recalculate_with_manual_market(payload: dict,
                                    *, base_pick: Optional[dict] = None) -> dict:
    """Compute a recalculated pick payload from manual market data.

    The output uses the canonical pick payload shape so the UI can
    render edge / fragility / confidence / verdict without surprises.
    """
    market_type = payload["market_type"]
    selection   = (payload.get("selection") or "").upper()
    line        = payload.get("line")
    line_f      = float(line) if line is not None else None
    manual_odd  = float(payload["manual_odd"])

    # Phase F74 — protected floor lookup.
    try:
        from . import market_tolerance as _mt
        market_label = {
            "DOUBLE_CHANCE":  "Doble Oportunidad",
            "DNB":            "Draw No Bet",
            "MATCH_WINNER":   "Match Winner",
            "TOTAL_GOALS":    f"Under {line_f}" if selection == "UNDER" else f"Over {line_f}",
            "BTTS":           "Both Teams Score",
            "CORNERS_TOTAL":  f"Corners {selection} {line_f}",
            "HANDICAP":       f"Handicap {selection} {line_f}",
            "ASIAN_HANDICAP": f"Asian Handicap {selection} {line_f}",
        }.get(market_type, market_type)
        tolerance_category = _mt.classify_market_tolerance(market_label, selection)
    except Exception:  # noqa: BLE001
        tolerance_category = "unknown"

    # Edge estimate (manual): if base_pick provides a model probability,
    # use it; otherwise we MUST NOT fabricate one — doing so caused the
    # historical "every manual odd shows favorable edge" bug (even 1.01
    # got a +5% fake edge because the prior heuristic was
    # ``implied_prob * 1.05``, which is always positive by construction).
    implied_prob = round(1.0 / manual_odd, 4) if manual_odd > 0 else 0.0
    base_model_prob = None
    model_prob_source: str = "missing"
    if isinstance(base_pick, dict):
        base_model_prob = (
            (base_pick.get("_market_edge") or {}).get("estimated_probability")
            or (base_pick.get("model_probability"))
        )
        if base_model_prob is not None:
            model_prob_source = "base_pick"

    # Soft proxy: use confidence/100 ONLY when explicitly available and
    # only as a *weak* signal (clamped to a sane range to avoid silly
    # results when confidence is on a 0-100 scale).
    if base_model_prob is None and isinstance(base_pick, dict):
        mb = base_pick.get("_moneyball") or {}
        conf_raw = (mb.get("confidence")
                    if isinstance(mb.get("confidence"), (int, float))
                    else None)
        if conf_raw is not None and 0 < conf_raw <= 100:
            base_model_prob = round(conf_raw / 100.0, 4)
            base_model_prob = max(0.05, min(base_model_prob, 0.95))
            model_prob_source = "confidence_weak_proxy"

    # If we still have nothing, DO NOT fabricate a model. Return a
    # neutral payload that says so clearly.
    if base_model_prob is None:
        manual_edge_pct = None
        model_prob_pct = None
        status = "MODEL_PROBABILITY_UNAVAILABLE"
        verdict = (
            "Cuota guardada, pero no se puede calcular edge sin "
            "probabilidad del modelo. Se requiere análisis previo del pick."
        )
    else:
        manual_edge_pct = round((base_model_prob - implied_prob) * 100, 2)
        model_prob_pct  = round(base_model_prob * 100, 2)
        status = None  # Will be set by verdict block below.
        verdict = None

    # Fragility & confidence: take from base_pick if available, else
    # neutral.
    fragility = 25
    confidence = 65
    if isinstance(base_pick, dict):
        mb = base_pick.get("_moneyball") or {}
        if isinstance(mb.get("fragility"), dict):
            fragility = mb["fragility"].get("score", 25)
        elif isinstance(mb.get("fragility"), int):
            fragility = mb["fragility"]
        if isinstance(mb.get("confidence"), int):
            confidence = mb["confidence"]

    # Verdict — only computed when we have a valid model probability.
    if manual_edge_pct is not None:
        if manual_edge_pct >= 3.0 and fragility <= 30:
            verdict = "Apta para revisión manual conservadora"
            status  = "MANUAL_VALUE_REVIEW"
        elif manual_edge_pct >= 0:
            verdict = "Margen ajustado; revisar contexto antes de apostar"
            status  = "MANUAL_THIN_VALUE"
        else:
            verdict = "Sin valor con la cuota manual; no recomendado"
            status  = "MANUAL_NO_VALUE"
        # When using a soft proxy, downgrade the verdict honesty.
        if model_prob_source == "confidence_weak_proxy":
            verdict = (
                "Edge estimado usando confianza del modelo como proxy "
                f"débil — interpretar con cautela ({verdict.lower()})."
            )
            status = "MANUAL_WEAK_PROXY"

    recommended_market = {
        "DOUBLE_CHANCE":  f"Doble Oportunidad {selection}",
        "DNB":            f"DNB {selection}",
        "MATCH_WINNER":   f"1X2 {selection}",
        "TOTAL_GOALS":    (f"Under {line_f}" if selection == "UNDER" else f"Over {line_f}"),
        "BTTS":           f"BTTS {selection}",
        "CORNERS_TOTAL":  f"Corners {selection} {line_f}",
        "HANDICAP":       f"Handicap {selection} {line_f}",
        "ASIAN_HANDICAP": f"Asian Handicap {selection} {line_f}",
    }.get(market_type, market_type)

    return {
        "manual_market_identity": {
            "market_type":  market_type,
            "selection":    selection,
            "line":         line_f,
            "odd":          manual_odd,
            "identity_key": _identity_key(market_type, selection, line_f),
            "source":       payload.get("source") or "USER_MANUAL_INPUT",
        },
        "recalculated_pick": {
            "recommended_market":   recommended_market,
            "manual_edge":          manual_edge_pct,
            "implied_probability":  round(implied_prob * 100, 2),
            "model_probability":    model_prob_pct,
            "model_prob_source":    model_prob_source,
            "fragility_score":      fragility,
            "confidence":           confidence,
            "tolerance_category":   tolerance_category,
            "status":               status,
            "verdict":              verdict,
        },
        "warnings": [
            "Cuota ingresada manualmente: validar que corresponda al mercado seleccionado.",
            (
                "El edge fue calculado usando identidad de mercado manual."
                if model_prob_source == "base_pick"
                else "Edge basado en confianza del modelo como proxy débil — interpretar con cautela."
                if model_prob_source == "confidence_weak_proxy"
                else "No fue posible calcular edge: el engine no expone probabilidad estimada para este pick."
            ),
        ],
    }


__all__ = [
    "MANUAL_MARKET_TYPES", "MARKET_OPTIONS",
    "validate_manual_payload",
    "recalculate_with_manual_market",
]
