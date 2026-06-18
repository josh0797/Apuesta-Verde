"""
Football Total Market Value — Sprint D10-B (módulo matemático puro).

Calcula la valoración de mercado (fair odds, edge, EV, value class)
para selecciones Over/Under en mercados de goles de fútbol, usando la
distribución Dixon-Coles existente (`football_dc_nb_calibration`).

Reglas de diseño
----------------
* PURO: sin I/O, sin Mongo, sin APIs. Recibe lambdas ajustadas y la
  cuota; devuelve un contrato determinista.
* La cuota NO modifica `adjusted_lambda_*` (anti-circularidad — eso es
  responsabilidad de `football_total_signal`).
* Reutiliza:
    - `prob_total_goals_over(line, lam_h, lam_a, rho)` para P(total > L).
    - `_dc_joint_pmf(h, a, lam_h, lam_a, rho)` para P(total == K) (push).
* Líneas soportadas (todas las del spec D10):
    - `.5`   → sin push.        Over win=P(total>L);  loss=1-win.
    - `.0`   → con push.        Over win=P(total>L); push=P(total==L);
                                  loss=P(total<L).
    - `.25`  → split 50/50 entre (`.0` y `.5`).
    - `.75`  → split 50/50 entre (`.5` y `.0`).
* EV asiático = suma ponderada de los dos legs (no probabilidad
  promedio simplificada).
* `value_class` clasifica el EV en bandas auditables.

Contrato del repricing
----------------------
{
    "status":  "REPRICED" | "MARKET_LINE_MISSING" | "INVALID_INPUTS" |
                "BASE_MODEL_ONLY",
    "market":  {"selection", "line", "decimal_odds"},
    "probabilities": {"win", "push", "loss", "over", "under"},
    "valuation": {
        "implied_probability", "fair_odds", "edge_percentage_points",
        "ev_percentage", "value_class"
    },
    "asian_split": [...]  (sólo para .25/.75)
}
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .football_dc_nb_calibration import (
    DEFAULT_DC_RHO,
    _dc_joint_pmf,
    prob_total_goals_over,
)


# ── Bandas de value_class ──────────────────────────────────────────────
VALUE_CLASS_BREAKS = (
    (-float("inf"), -10.0, "STRONG_NEGATIVE_VALUE"),
    (-10.0,         -3.0,  "NEGATIVE_VALUE"),
    ( -3.0,          3.0,  "FAIR"),
    (  3.0,          8.0,  "MILD_VALUE"),
    (  8.0,         15.0,  "GOOD_VALUE"),
    ( 15.0,  float("inf"), "HIGH_EDGE_REVIEW_REQUIRED"),
)


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _classify_value(ev_pct: Optional[float]) -> str:
    if ev_pct is None:
        return "UNKNOWN"
    for lo, hi, label in VALUE_CLASS_BREAKS:
        if lo <= ev_pct < hi:
            return label
    return "FAIR"


def _normalise_selection(sel: Optional[str]) -> Optional[str]:
    if not sel:
        return None
    s = sel.strip().upper()
    if s in ("OVER", "UNDER"):
        return s
    if s.startswith("OVER"):
        return "OVER"
    if s.startswith("UNDER"):
        return "UNDER"
    return None


def _classify_line(line: float) -> str:
    """Devuelve uno de '.5', '.0', '.25', '.75' según la parte
    decimal de la línea."""
    frac = round(line - math.floor(line), 4)
    if abs(frac - 0.5) < 1e-6:
        return ".5"
    if abs(frac - 0.0) < 1e-6:
        return ".0"
    if abs(frac - 0.25) < 1e-6:
        return ".25"
    if abs(frac - 0.75) < 1e-6:
        return ".75"
    return ".other"


def _prob_total_equals(k: int,
                          lam_home: float, lam_away: float,
                          rho: float = DEFAULT_DC_RHO,
                          max_goals: int = 10) -> float:
    """P(home + away = k) bajo Dixon-Coles. Suma todas las celdas (h,a)
    de la matriz tal que h+a=k.

    Nota: la matriz DC tiene una micro no-normalización por τ. Para
    line .0 (cálculo de push) usamos la suma cruda — es la convención
    histórica del proyecto y mantiene consistencia con
    `prob_total_goals_over` (también suma cruda)."""
    if k < 0:
        return 0.0
    p = 0.0
    for h in range(max(0, k - max_goals), min(k, max_goals) + 1):
        a = k - h
        if 0 <= a <= max_goals:
            p += _dc_joint_pmf(h, a, lam_home, lam_away, rho)
    return max(0.0, p)


def _leg_probs(
    selection: str,
    line_value: float,
    lam_home: float,
    lam_away: float,
    rho: float,
    *,
    is_integer_line: bool,
) -> dict:
    """Calcula win/push/loss para una pierna (sub-línea) específica.

    * `selection`: "OVER" | "UNDER".
    * `line_value`: línea numérica (e.g. 2.5, 3.0).
    * `is_integer_line`: True si la línea termina en .0 (admite push).

    Convención:
      Línea .0 (e.g. 3.0):
        Over  → win = P(total > 3); push = P(total == 3); loss = P(total < 3).
        Under → win = P(total < 3); push = P(total == 3); loss = P(total > 3).
      Línea .5 (e.g. 2.5):
        Over  → win = P(total >= 3) = P(total > 2.5); loss = P(total <= 2).
        Under → win = P(total <= 2) = 1 - P(total > 2.5); loss = P(total >= 3).
    """
    over_prob = prob_total_goals_over(line_value, lam_home, lam_away, rho=rho)
    if over_prob is None:
        return {"win": None, "push": None, "loss": None}
    if not is_integer_line:
        if selection == "OVER":
            return {"win": round(over_prob, 6), "push": 0.0,
                      "loss": round(1.0 - over_prob, 6)}
        else:  # UNDER
            return {"win": round(1.0 - over_prob, 6), "push": 0.0,
                      "loss": round(over_prob, 6)}
    # Integer line — push = P(total == L).
    push = _prob_total_equals(int(round(line_value)), lam_home, lam_away, rho=rho)
    # prob_total_goals_over usa P(total > L). Para L entero:
    #   P(total > L)  = over_prob (>3 si L=3)
    #   P(total < L)  = 1 - over_prob - push
    p_strict_over = over_prob
    p_strict_under = max(0.0, 1.0 - over_prob - push)
    if selection == "OVER":
        return {"win": round(p_strict_over, 6),
                  "push": round(push, 6),
                  "loss": round(p_strict_under, 6)}
    else:
        return {"win": round(p_strict_under, 6),
                  "push": round(push, 6),
                  "loss": round(p_strict_over, 6)}


def _leg_ev(win: float, loss: float, decimal_odds: float) -> float:
    """EV = p_win * (odds - 1) - p_loss. El push contribuye 0."""
    return win * (decimal_odds - 1.0) - loss


def _fair_odds(win_prob: float, push_prob: float) -> Optional[float]:
    """Fair decimal odds — cuota neutra con el push (que devuelve stake).

    Convención: cuando push >0, una apuesta con stake=1 recupera 1 en
    caso de push. Por tanto, win_prob * (X - 1) - loss = 0 — donde
    loss = 1 - win_prob - push_prob.
    Resolviendo: X = (1 - push_prob) / win_prob, siempre que
    win_prob > 0. Cuando push_prob = 0, esto colapsa a 1/win_prob.
    """
    if win_prob <= 0:
        return None
    return (1.0 - push_prob) / win_prob


# ═════════════════════════════════════════════════════════════════════
# API pública
# ═════════════════════════════════════════════════════════════════════
def calculate_football_total_market_value(
    *,
    selection: Optional[str],
    line: Optional[float],
    decimal_odds: Optional[float],
    adjusted_lambda_home: Optional[float],
    adjusted_lambda_away: Optional[float],
    rho: float = DEFAULT_DC_RHO,
    influence_score: Optional[float] = None,
    confidence_delta: Optional[float] = None,
) -> dict:
    """Calcula la valoración de mercado para una selección Over/Under.

    Devuelve el contrato del spec D10 sec "Contrato del repricing
    manual". Para .25 / .75 incluye `asian_split[]` con dos sub-legs.

    Estados:
      * `INVALID_INPUTS`        — selection / line / odds inválidos.
      * `MARKET_LINE_MISSING`   — falta línea pero hay lambdas.
      * `BASE_MODEL_ONLY`       — falta odds (sólo probabilidades).
      * `REPRICED`              — todo presente, EV calculado.

    Reglas:
      * NO modifica las lambdas.
      * NO depende de `selection` para las probabilidades del modelo
        (Over y Under son complementarios bajo el mismo modelo).
      * EV asiático = 0.5 * leg1_ev + 0.5 * leg2_ev.
    """
    sel_norm = _normalise_selection(selection)
    L = _safe_float(line)
    odds = _safe_float(decimal_odds)
    lam_h = _safe_float(adjusted_lambda_home)
    lam_a = _safe_float(adjusted_lambda_away)

    # ── Validaciones ────────────────────────────────────────────────
    if lam_h is None or lam_a is None or lam_h < 0 or lam_a < 0:
        return _empty_repriced("INVALID_INPUTS", sel_norm, L, odds, "missing_lambdas")
    if sel_norm is None:
        return _empty_repriced("INVALID_INPUTS", None, L, odds, "missing_selection")
    if L is None:
        return _empty_repriced("MARKET_LINE_MISSING", sel_norm, None, odds, "no_line")
    if odds is not None and odds <= 1.0:
        return _empty_repriced("INVALID_INPUTS", sel_norm, L, odds, "odds_must_be_gt_1")

    # ── Clasificar línea ────────────────────────────────────────────
    line_class = _classify_line(L)
    asian_split: Optional[list[dict]] = None

    if line_class == ".25":
        # 0.25 → mitad va a .0 (línea entera abajo) + mitad va a .5
        lo = math.floor(L)               # ej. 2.0
        hi = math.floor(L) + 0.5         # ej. 2.5
        legs = _build_asian_legs(sel_norm, lo, hi, lam_h, lam_a, rho, odds)
        asian_split = legs
        probs, val = _combine_asian(legs, odds)
    elif line_class == ".75":
        # 0.75 → mitad va a .5 (debajo) + mitad va a .0 (arriba)
        lo = math.floor(L) + 0.5         # ej. 2.5
        hi = math.floor(L) + 1.0         # ej. 3.0
        legs = _build_asian_legs(sel_norm, lo, hi, lam_h, lam_a, rho, odds)
        asian_split = legs
        probs, val = _combine_asian(legs, odds)
    else:
        # Línea simple (.5 o .0)
        is_int = (line_class == ".0")
        probs = _leg_probs(sel_norm, L, lam_h, lam_a, rho, is_integer_line=is_int)
        if probs.get("win") is None:
            return _empty_repriced("INVALID_INPUTS", sel_norm, L, odds, "matrix_failed")
        # Over/Under canónicos para el panel UI (independiente de la
        # selection — refleja la dirección de los strict-comparisons).
        if sel_norm == "OVER":
            probs["over"] = probs["win"]
            probs["under"] = probs["loss"]
        else:
            probs["under"] = probs["win"]
            probs["over"] = probs["loss"]
        val = _valuation_block(probs["win"], probs["push"], probs["loss"], odds)

    # ── Estado final ────────────────────────────────────────────────
    status = "REPRICED" if odds is not None else "BASE_MODEL_ONLY"

    # ── Context block (informativo, no afecta valoración) ──────────
    influence = _safe_float(influence_score) or 0.0
    conf_delta = _safe_float(confidence_delta) or 0.0
    # directional_support: positivo cuando el signo del score coincide
    # con la selection (Over apoya OVER si score > 0).
    if sel_norm == "OVER":
        directional_support = influence
    else:
        directional_support = -influence
    if directional_support >= 3.0:
        support_class = "STRONG_SUPPORT"
    elif directional_support >= 1.0:
        support_class = "MILD_SUPPORT"
    elif directional_support <= -3.0:
        support_class = "STRONG_CONFLICT"
    elif directional_support <= -1.0:
        support_class = "MILD_CONFLICT"
    else:
        support_class = "NEUTRAL"

    # Decision tag — combina valuation + context.
    decision = _make_decision_tag(val.get("value_class"), support_class)

    out = {
        "status": status,
        "market": {
            "selection":     sel_norm,
            "line":          L,
            "line_class":    line_class,
            "decimal_odds":  odds,
        },
        "probabilities": probs,
        "valuation":     val,
        "context": {
            "influence_score":      round(influence, 4),
            "directional_support":  round(directional_support, 4),
            "support_class":        support_class,
            "confidence_delta":     round(conf_delta, 4),
        },
        "decision": decision,
        "observe_only": True,
    }
    if asian_split is not None:
        out["asian_split"] = asian_split
    return out


def _empty_repriced(status: str, sel: Optional[str],
                       line: Optional[float], odds: Optional[float],
                       reason: str) -> dict:
    return {
        "status": status,
        "market": {
            "selection":    sel,
            "line":         line,
            "line_class":   _classify_line(line) if line is not None else "UNKNOWN",
            "decimal_odds": odds,
        },
        "probabilities": {"win": None, "push": None, "loss": None,
                            "over": None, "under": None},
        "valuation": {
            "implied_probability":      None,
            "fair_odds":                None,
            "edge_percentage_points":   None,
            "ev_percentage":            None,
            "value_class":              "UNKNOWN",
        },
        "context": {
            "influence_score":     0.0,
            "directional_support": 0.0,
            "support_class":       "NEUTRAL",
            "confidence_delta":    0.0,
        },
        "reason_code": reason,
        "decision":    "UNKNOWN",
        "observe_only": True,
    }


def _valuation_block(win: float, push: float, loss: float,
                       odds: Optional[float]) -> dict:
    fair = _fair_odds(win, push)
    if odds is None:
        return {
            "implied_probability":     None,
            "fair_odds":               None if fair is None else round(fair, 4),
            "edge_percentage_points":  None,
            "ev_percentage":           None,
            "value_class":             "UNKNOWN",
        }
    implied = 1.0 / odds
    ev = _leg_ev(win, loss, odds)  # neto: stake=1
    ev_pct = ev * 100.0
    # Edge percentage points = (win_prob - implied) * 100, ajustado por push.
    # Cuando hay push, el modelo win efectivo = win + push (recupera stake),
    # pero el comparativo más limpio es win directo vs implied.
    edge_pp = (win - implied) * 100.0
    return {
        "implied_probability":     round(implied, 4),
        "fair_odds":               None if fair is None else round(fair, 4),
        "edge_percentage_points":  round(edge_pp, 4),
        "ev_percentage":           round(ev_pct, 4),
        "value_class":             _classify_value(ev_pct),
    }


def _build_asian_legs(selection: str, line_lo: float, line_hi: float,
                       lam_home: float, lam_away: float, rho: float,
                       decimal_odds: Optional[float]) -> list[dict]:
    """Construye dos sub-legs (stake_fraction = 0.5 cada uno).
    `line_lo` < `line_hi`. Para .25/.75 los legs tienen .0 y .5."""
    legs = []
    for L in (line_lo, line_hi):
        line_class = _classify_line(L)
        is_int = (line_class == ".0")
        probs = _leg_probs(selection, L, lam_home, lam_away, rho,
                              is_integer_line=is_int)
        if probs.get("win") is None:
            continue
        ev_pct = None
        edge_pp = None
        fair = _fair_odds(probs["win"], probs["push"])
        if decimal_odds is not None:
            ev = _leg_ev(probs["win"], probs["loss"], decimal_odds)
            ev_pct = round(ev * 100.0, 4)
            edge_pp = round((probs["win"] - (1.0 / decimal_odds)) * 100.0, 4)
        legs.append({
            "line":           L,
            "stake_fraction": 0.5,
            "win":            probs["win"],
            "push":           probs["push"],
            "loss":           probs["loss"],
            "fair_odds":      None if fair is None else round(fair, 4),
            "ev_percentage":  ev_pct,
            "edge_percentage_points": edge_pp,
        })
    return legs


def _combine_asian(legs: list[dict], decimal_odds: Optional[float]) -> tuple[dict, dict]:
    """Combina dos legs en probabilidades ponderadas + valoración.

    El EV asiático = 0.5*EV(leg1) + 0.5*EV(leg2). Las probabilidades
    win/push/loss se ponderan también."""
    if not legs:
        return ({"win": None, "push": None, "loss": None,
                  "over": None, "under": None},
                {"implied_probability": None, "fair_odds": None,
                  "edge_percentage_points": None, "ev_percentage": None,
                  "value_class": "UNKNOWN"})
    win = sum(leg["win"] * leg["stake_fraction"] for leg in legs)
    push = sum(leg["push"] * leg["stake_fraction"] for leg in legs)
    loss = sum(leg["loss"] * leg["stake_fraction"] for leg in legs)
    probs = {
        "win":  round(win, 6),
        "push": round(push, 6),
        "loss": round(loss, 6),
        # 'over' y 'under' canónicos no aplican bien a la mezcla; se
        # exponen como win-side y loss-side para mantener el contrato.
        "over":  None,
        "under": None,
    }
    if decimal_odds is None:
        return (probs, _valuation_block(win, push, loss, None))
    ev_pct = sum((leg.get("ev_percentage") or 0.0) * leg["stake_fraction"] for leg in legs)
    implied = 1.0 / decimal_odds
    edge_pp = (win - implied) * 100.0
    fair = _fair_odds(win, push)
    val = {
        "implied_probability":     round(implied, 4),
        "fair_odds":               None if fair is None else round(fair, 4),
        "edge_percentage_points":  round(edge_pp, 4),
        "ev_percentage":           round(ev_pct, 4),
        "value_class":             _classify_value(ev_pct),
    }
    return (probs, val)


def _make_decision_tag(value_class: Optional[str], support_class: str) -> str:
    """Combina value_class + support_class en un tag accionable."""
    if not value_class or value_class in ("UNKNOWN", "FAIR"):
        return "NO_ACTIONABLE_VALUE"
    if value_class.endswith("NEGATIVE_VALUE") or value_class == "STRONG_NEGATIVE_VALUE":
        return "AVOID_NEGATIVE_VALUE"
    if value_class in ("MILD_VALUE", "GOOD_VALUE", "HIGH_EDGE_REVIEW_REQUIRED"):
        if support_class in ("STRONG_SUPPORT", "MILD_SUPPORT"):
            return "VALUE_SUPPORTED_BY_CONTEXT"
        if support_class in ("STRONG_CONFLICT", "MILD_CONFLICT"):
            return "VALUE_CONFLICTS_WITH_CONTEXT"
        return "VALUE_NEUTRAL_CONTEXT"
    return "UNKNOWN"


__all__ = [
    "calculate_football_total_market_value",
    "VALUE_CLASS_BREAKS",
]
