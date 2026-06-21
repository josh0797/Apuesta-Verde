"""Sprint Corner-1 · paso 2 — Modelo **Most Corners** (clasificador binario).

Modela:
    P(home_most_corners), P(away_most_corners), P(tie_corners)

con calibración:

    p_home_most_no_tie = sigmoid(a * expected_corner_diff + b)
    p_tie_corners       = lookup_tie_prob_by_bucket(|expected_corner_diff|)
    p_home_most         = (1 - p_tie) * p_home_most_no_tie
    p_away_most         = (1 - p_tie) * (1 - p_home_most_no_tie)

Los coeficientes ``a`` y ``b`` se calibran por maximum likelihood
(walk-forward) en ``corner_backtest._calibrate_most_corners_sigmoid``.
Mientras no haya calibración, se usan defaults razonables.

Output sigue el contrato exacto del brief:
    home_most_corners_prob, away_most_corners_prob, tie_corners_prob,
    recommended_side ("HOME" | "AWAY" | "NO_BET"), edge_score,
    confidence, expected_corner_diff, reason_codes, drivers, debug.

Reglas NO_BET (del brief):
    * confidence < 55
    * prob del lado recomendado < 0.58
    * missing critical fields > umbral
    * data_quality == LOW
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .corner_diff_model import compute_expected_corner_diff

# Defaults de calibración (sigmoid: p = 1/(1+exp(-(a*x + b))))
# Derivados aproximadamente de los hallazgos de Fase 1.5
# (dominant favorite → diff +3.82, p_fav_no_tie ≈ 0.84 → x≈3.82, p≈0.84
#  → a*3.82 ≈ logit(0.84) = 1.66 → a ≈ 0.43)
DEFAULT_SIGMOID_A = 0.43
DEFAULT_SIGMOID_B = 0.0  # sin sesgo direccional por default

# Tie probabilities por bucket de |expected_corner_diff|
# Calibrado empíricamente: cuando hay ventaja clara, menos chance de tie.
# Estos defaults son aproximados — el backtest los reemplaza por
# frecuencias empíricas reales.
DEFAULT_TIE_BUCKETS = [
    # (max_abs_diff, p_tie)
    (0.5, 0.18),  # diff cercano a 0 → tie 18%
    (1.5, 0.14),
    (2.5, 0.11),
    (3.5, 0.09),
    (5.5, 0.07),
    (99.0, 0.05),
]

# Reason codes
REASON_DOMINANT_FAV          = "DOMINANT_FAVORITE_CORNER_EDGE"
REASON_LOW_CONFIDENCE        = "MOST_CORNERS_LOW_CONFIDENCE"
REASON_LOW_PROB              = "MOST_CORNERS_PROB_BELOW_THRESHOLD"
REASON_LOW_DATA_QUALITY      = "CORNER_DIFF_LOW_DATA_QUALITY"
REASON_TIE_HIGH              = "MOST_CORNERS_TIE_HIGH"

# Umbrales del brief
MIN_CONFIDENCE_FOR_BET = 55.0
MIN_PROB_FOR_BET       = 0.58


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _lookup_tie_prob(abs_diff: float, buckets: list[tuple[float, float]]) -> float:
    for max_abs, p in buckets:
        if abs_diff <= max_abs:
            return p
    return buckets[-1][1]


def predict_most_corners(
    context: dict[str, Any],
    *,
    sigmoid_a: float = DEFAULT_SIGMOID_A,
    sigmoid_b: float = DEFAULT_SIGMOID_B,
    tie_buckets: Optional[list[tuple[float, float]]] = None,
    diff_coefficients: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Predicción del mercado Most Corners.

    El context puede tener ya un `expected_corner_diff` precalculado
    (path rápido para el backtest) o las features crudas (path normal).
    """
    if tie_buckets is None:
        tie_buckets = DEFAULT_TIE_BUCKETS

    drivers: list[dict] = []
    reason_codes: list[str] = []

    # ----- 1) Obtener expected_corner_diff -----
    if "expected_corner_diff" in context and context.get("_skip_diff_calc"):
        edcd = float(context["expected_corner_diff"])
        diff_result = {
            "expected_corner_diff": edcd,
            "favored_corner_side": "HOME" if edcd > 0.5 else ("AWAY" if edcd < -0.5 else "NONE"),
            "confidence":     float(context.get("diff_confidence", 60.0)),
            "data_quality":   context.get("diff_data_quality", "MEDIUM"),
            "drivers":        context.get("diff_drivers", []),
            "missing_fields": context.get("diff_missing_fields", []),
            "reason_codes":   context.get("diff_reason_codes", []),
            "is_dominant_favorite": context.get("is_dominant_favorite", False),
            "dominant_favorite_side": context.get("dominant_favorite_side"),
        }
    else:
        diff_result = compute_expected_corner_diff(context, coefficients=diff_coefficients)
        edcd = float(diff_result["expected_corner_diff"])

    # ----- 2) Calcular P(home_most_corners | no tie) vía sigmoid -----
    p_home_no_tie = _sigmoid(sigmoid_a * edcd + sigmoid_b)

    # ----- 3) Tie probability por bucket -----
    p_tie = _lookup_tie_prob(abs(edcd), tie_buckets)

    # ----- 4) Reescalar para sumar 1 -----
    p_home = (1.0 - p_tie) * p_home_no_tie
    p_away = (1.0 - p_tie) * (1.0 - p_home_no_tie)
    # Normalizar (por si redondeo)
    s = p_home + p_away + p_tie
    if s > 0:
        p_home /= s
        p_away /= s
        p_tie  /= s

    # ----- 5) Lado recomendado -----
    if p_home > p_away:
        recommended_side = "HOME"
        recommended_prob = p_home
    else:
        recommended_side = "AWAY"
        recommended_prob = p_away

    # Edge score: 0-100, basado en cuanto se aleja del 50/50 corregido por tie
    # max_prob_excluding_tie = max(p_home, p_away) / (1 - p_tie) → ajusta a un escala 0.5-1.0
    if p_tie < 0.999:
        max_excl_tie = max(p_home, p_away) / (1.0 - p_tie)
        # 0.5 -> 0; 1.0 -> 100
        edge_score = round(max(0.0, min(100.0, 200.0 * (max_excl_tie - 0.5))), 2)
    else:
        edge_score = 0.0

    # ----- 6) Reason codes -----
    if diff_result.get("is_dominant_favorite"):
        reason_codes.append(REASON_DOMINANT_FAV)
    if p_tie >= 0.15:
        reason_codes.append(REASON_TIE_HIGH)
    for rc in diff_result.get("reason_codes", []):
        if rc not in reason_codes:
            reason_codes.append(rc)

    # ----- 7) Confidence (combina diff_confidence + edge_score) -----
    diff_conf = float(diff_result.get("confidence", 0.0))
    confidence = round(0.55 * diff_conf + 0.45 * edge_score, 2)
    if diff_result.get("data_quality") == "LOW":
        confidence = min(confidence, 40.0)

    # ----- 8) Decisión BET / NO_BET (reglas del brief) -----
    if diff_result.get("data_quality") == "LOW":
        recommended_side = "NO_BET"
        reason_codes.append(REASON_LOW_DATA_QUALITY)
    elif confidence < MIN_CONFIDENCE_FOR_BET:
        recommended_side = "NO_BET"
        reason_codes.append(REASON_LOW_CONFIDENCE)
    elif recommended_prob < MIN_PROB_FOR_BET:
        recommended_side = "NO_BET"
        reason_codes.append(REASON_LOW_PROB)

    # Dedupe reason codes preserving order
    seen = set()
    rc_clean: list[str] = []
    for rc in reason_codes:
        if rc not in seen:
            seen.add(rc)
            rc_clean.append(rc)

    drivers = list(diff_result.get("drivers", []))

    return {
        "home_most_corners_prob": round(p_home, 4),
        "away_most_corners_prob": round(p_away, 4),
        "tie_corners_prob":       round(p_tie, 4),
        "recommended_side":       recommended_side,
        "edge_score":             edge_score,
        "confidence":             confidence,
        "expected_corner_diff":   round(edcd, 4),
        "reason_codes":           rc_clean,
        "drivers":                drivers,
        "debug": {
            "sigmoid_a":              sigmoid_a,
            "sigmoid_b":              sigmoid_b,
            "p_home_no_tie":          round(p_home_no_tie, 4),
            "p_tie_bucket_input":     round(abs(edcd), 4),
            "diff_data_quality":      diff_result.get("data_quality"),
            "diff_confidence":        diff_conf,
            "favored_corner_side":    diff_result.get("favored_corner_side"),
            "is_dominant_favorite":   diff_result.get("is_dominant_favorite", False),
            "dominant_favorite_side": diff_result.get("dominant_favorite_side"),
        },
    }
