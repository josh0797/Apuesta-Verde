"""
MLB Series Total Signal — Sprint D9.3-B (módulo puro).

Combina la serie activa + H2H recientes en una señal cuantitativa para
OVER/UNDER de carreras totales. **No** sustituye la proyección del
modelo base; aplica shrinkage y clamps estrictos para que en muestras
pequeñas el ajuste sea tenue y la señal nunca decida sola un pick.

Salida (siempre fail-soft, observe_only):
  - weighted_series_runs           (float | None)
  - series_reliability             ∈ [0, 1)
  - adjusted_expected_runs         (float | None)
  - series_adjustment              ∈ [-1.25, +1.25]
  - series_edge_runs               (float | None) = adjusted - market_total
  - edge_band                      ∈ STRONG_UNDER / MOD_UNDER / NEUTRAL / MOD_OVER / STRONG_OVER
  - variability {mean, median, std, min, max, cv, band}
  - series_slope                   (float | None) — None si n<3
  - series_context_score           ∈ [-10, +10]
  - score_breakdown { edge, slope, bullpen_fatigue, pitching_matchup, variance }
  - confidence_modifier            ∈ [-5, +5]   ← derivado del score
  - reason_codes[]

Componentes del score:
  - edge_component         = clamp(series_edge_runs * 2.5, -4, +4)
  - slope_component        = clamp(series_slope, -2, +2)        (0 si n<3)
  - bullpen_fatigue_comp   = (input opt-in, default 0)
  - pitching_matchup_comp  = (input opt-in, default 0)
  - variance_component     = atenuación: |edge|·(0..0.5) inverso a CV
  - score = clamp(suma, -10, +10)

Reglas:
  - active series weight = 1.0; H2H de series previas = 0.45
  - shrinkage = n / (n + 3)
  - cap de influencia sobre proyección = 30 % del expected runs base
  - clamp final de series_adjustment a ±1.25 carreras
  - confidence_modifier = clamp( score * 0.5 , -5, +5 )

Convención de game schema esperado:
  { "total_runs": int|float, "game_number": int (1-based; opcional) }

H2H recientes (de series PREVIAS al matchup actual) se aceptan en la
misma shape y reciben peso 0.45.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Optional


# ─── Constantes de diseño ──────────────────────────────────────────────
RECENCY_WEIGHTS: tuple[float, ...] = (1.00, 0.75, 0.55, 0.40, 0.30)
ACTIVE_SERIES_WEIGHT: float = 1.00
PREVIOUS_SERIES_H2H_WEIGHT: float = 0.45

SHRINKAGE_K: int = 3                    # series_reliability = n/(n+K)
MAX_INFLUENCE_PCT: float = 0.30         # tope 30 % de la proyección base
ADJUSTMENT_CLAMP: float = 1.25          # clamp final ±1.25 carreras
CONFIDENCE_MODIFIER_CAP: float = 5.0    # ±5 pts de confianza
SCORE_CAP: float = 10.0                 # ±10 score

EDGE_COMPONENT_CAP: float = 4.0
SLOPE_COMPONENT_CAP: float = 2.0
VARIANCE_COMPONENT_CAP: float = 2.0
BULLPEN_FATIGUE_CAP: float = 3.0
PITCHING_MATCHUP_CAP: float = 3.0

# Bandas del edge (carreras frente a la línea).
EDGE_BAND_BREAKS = (-1.25, -0.60, 0.60, 1.25)
EDGE_BAND_LABELS = (
    "STRONG_UNDER",   # edge ≤ -1.25
    "MODERATE_UNDER", # -1.25 < edge ≤ -0.60
    "NEUTRAL",        # -0.60 < edge < +0.60
    "MODERATE_OVER",  # +0.60 ≤ edge < +1.25
    "STRONG_OVER",    # edge ≥ +1.25
)

# Bandas del CV (coeficiente de variación).
CV_STABLE_MAX: float    = 0.20
CV_MEDIUM_MAX: float    = 0.35


# ─── Helpers ───────────────────────────────────────────────────────────
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


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


def _normalise_games(games: Optional[Iterable[dict]]) -> list[dict]:
    """Filtra a games con total_runs numérico finito. Ordena por
    game_number si está disponible (1-based, ascendente); en otro caso
    preserva el orden recibido."""
    out: list[dict] = []
    if not games:
        return out
    for g in games:
        if not isinstance(g, dict):
            continue
        tot = _safe_float(g.get("total_runs"))
        if tot is None or tot < 0:
            continue
        gn = g.get("game_number")
        if gn is None:
            gn_int: Optional[int] = None
        else:
            try:
                gn_int = int(gn)
            except (TypeError, ValueError):
                gn_int = None
        out.append({"total_runs": tot, "game_number": gn_int})
    # Si todos tienen game_number, ordenar ascendente; si no, mantener.
    if out and all(g["game_number"] is not None for g in out):
        out.sort(key=lambda g: g["game_number"])
    return out


def _weighted_mean(values: list[float], weights: list[float]) -> Optional[float]:
    if not values:
        return None
    s_w = sum(weights)
    if s_w <= 0:
        return None
    return sum(v * w for v, w in zip(values, weights)) / s_w


def _recency_weights_for(n: int) -> list[float]:
    """Da pesos por recencia: el más RECIENTE recibe 1.00. Se asume el
    último elemento de la lista como el más reciente (orden ascendente
    de game_number); el primero es el más antiguo. Si n > len(table),
    los más antiguos comparten el peso mínimo (0.30)."""
    if n <= 0:
        return []
    table = list(RECENCY_WEIGHTS)
    if n <= len(table):
        # Tomar los `n` primeros y reasignarlos al orden cronológico
        # (más recientes al final): invertir para que el último de la
        # lista de juegos coincida con weight 1.00.
        return list(reversed(table[:n]))
    # n > len(table): los antiguos extra reciben 0.30.
    extra = [table[-1]] * (n - len(table))
    base = list(reversed(table))
    return extra + base


def _linear_slope(values: list[float]) -> Optional[float]:
    """Pendiente de regresión lineal (least squares) de y vs x donde
    x = 1..n. Devuelve None si n<3."""
    n = len(values)
    if n < 3:
        return None
    xs = list(range(1, n + 1))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def _coefficient_of_variation(values: list[float]) -> Optional[float]:
    if not values:
        return None
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    std = statistics.pstdev(values)
    return std / mean


def _cv_band(cv: Optional[float]) -> str:
    if cv is None:
        return "UNKNOWN"
    if cv < CV_STABLE_MAX:
        return "STABLE"
    if cv < CV_MEDIUM_MAX:
        return "MEDIUM"
    return "VOLATILE"


def _edge_band(edge: Optional[float]) -> str:
    if edge is None:
        return "UNKNOWN"
    if edge <= EDGE_BAND_BREAKS[0]:
        return EDGE_BAND_LABELS[0]
    if edge <= EDGE_BAND_BREAKS[1]:
        return EDGE_BAND_LABELS[1]
    if edge < EDGE_BAND_BREAKS[2]:
        return EDGE_BAND_LABELS[2]
    if edge < EDGE_BAND_BREAKS[3]:
        return EDGE_BAND_LABELS[3]
    return EDGE_BAND_LABELS[4]


def _variance_component(edge: Optional[float], cv: Optional[float]) -> float:
    """Atenuación: en series volátiles (CV alto) y muestras chicas,
    bajar la contribución absoluta. Devuelve valor en [-2, +2].

    Heurística:
      - cv None / VOLATILE → magnitud reducida ~30 % del edge.
      - cv MEDIUM          → ~60 % del edge.
      - cv STABLE          → preserva ~80 % del edge.
    """
    if edge is None:
        return 0.0
    band = _cv_band(cv)
    if band == "STABLE":
        mult = 0.80
    elif band == "MEDIUM":
        mult = 0.60
    elif band == "VOLATILE":
        mult = 0.30
    else:  # UNKNOWN
        mult = 0.40
    raw = edge * mult
    return _clamp(raw, -VARIANCE_COMPONENT_CAP, VARIANCE_COMPONENT_CAP)


def _empty_payload(market_total: Optional[float], reason: str) -> dict:
    return {
        "available":              False,
        "reason_code":            reason,
        "n_active":               0,
        "n_h2h":                  0,
        "n_effective":            0.0,
        "weighted_series_runs":   None,
        "series_reliability":     0.0,
        "adjusted_expected_runs": None,
        "series_adjustment":      0.0,
        "series_edge_runs":       None,
        "edge_band":              "UNKNOWN",
        "variability":            {
            "mean":   None, "median": None, "std": None,
            "min":    None, "max":    None, "cv":  None, "band": "UNKNOWN",
        },
        "series_slope":           None,
        "slope_band":             "INSUFFICIENT_SAMPLE_FOR_SERIES_TREND",
        "series_context_score":   0.0,
        "score_breakdown":        {
            "edge_runs":         0.0,
            "slope":             0.0,
            "bullpen_fatigue":   0.0,
            "pitching_matchup":  0.0,
            "variance":          0.0,
        },
        "confidence_modifier":    0.0,
        "market_total":           market_total,
        "reason_codes":           [reason],
        "observe_only":           True,
    }


# ─── API principal ─────────────────────────────────────────────────────
def calculate_series_total_signal(
    current_expected_runs: Optional[float],
    market_total: Optional[float],
    active_series_games: Optional[Iterable[dict]] = None,
    recent_h2h_games: Optional[Iterable[dict]] = None,
    *,
    bullpen_fatigue_component: float = 0.0,
    pitching_matchup_component: float = 0.0,
) -> dict:
    """Calcula la señal cuantitativa de la serie activa.

    Args:
      current_expected_runs:  ER del modelo base (sin ajustar). Si None,
                              no se puede calcular `adjusted_expected_runs`
                              ni `series_edge_runs`; el módulo igual
                              expone media ponderada y CV.
      market_total:           línea del mercado (e.g. 9.5). Si None, el
                              edge queda en None.
      active_series_games:    iterable de dicts con `total_runs` (1..N).
      recent_h2h_games:       iterable adicional (series previas); peso
                              0.45 frente a 1.0 de active series.
      bullpen_fatigue_component:  contribución opt-in al score, en
                              unidades del score (-3..+3). Default 0.
      pitching_matchup_component: contribución opt-in al score, en
                              unidades del score (-3..+3). Default 0.

    Returns:
      dict siempre poblado (fail-soft).
    """
    base_er = _safe_float(current_expected_runs)
    mkt = _safe_float(market_total)

    active = _normalise_games(active_series_games)
    h2h = _normalise_games(recent_h2h_games)

    if not active and not h2h:
        return _empty_payload(mkt, "NO_SERIES_SAMPLE")

    # ── Combinar muestras con pesos de bloque + pesos de recencia ────
    n_active = len(active)
    n_h2h = len(h2h)
    # n_effective: número efectivo (active=1.0, h2h=0.45) — se usa en
    # shrinkage para que H2H de otras series no infle la reliability.
    n_effective = n_active * ACTIVE_SERIES_WEIGHT + n_h2h * PREVIOUS_SERIES_H2H_WEIGHT

    # Recency weights por bloque.
    active_rw = _recency_weights_for(n_active)
    h2h_rw = _recency_weights_for(n_h2h)
    # Pesos finales = recency * block_weight.
    active_w = [w * ACTIVE_SERIES_WEIGHT for w in active_rw]
    h2h_w = [w * PREVIOUS_SERIES_H2H_WEIGHT for w in h2h_rw]
    all_values = [g["total_runs"] for g in active] + [g["total_runs"] for g in h2h]
    all_weights = active_w + h2h_w
    weighted = _weighted_mean(all_values, all_weights)

    # ── Variability sobre la muestra COMBINADA ───────────────────────
    active_values = [g["total_runs"] for g in active]
    h2h_values = [g["total_runs"] for g in h2h]
    combined = active_values + h2h_values
    if combined:
        mean = sum(combined) / len(combined)
        median = statistics.median(combined)
        std = statistics.pstdev(combined) if len(combined) >= 2 else 0.0
        vmin = min(combined)
        vmax = max(combined)
        cv = _coefficient_of_variation(combined)
    else:
        mean = median = std = vmin = vmax = cv = None
    cv_band = _cv_band(cv)

    # ── Shrinkage + ajuste de ER ─────────────────────────────────────
    reliability = (
        n_effective / (n_effective + SHRINKAGE_K)
        if n_effective > 0 else 0.0
    )
    adjusted_er: Optional[float] = None
    series_adjustment = 0.0
    if base_er is not None and weighted is not None:
        # Cap de influencia: 30 % de la proyección base.
        weight_on_series = reliability * MAX_INFLUENCE_PCT
        raw_adjusted = (
            base_er * (1.0 - weight_on_series)
            + weighted * weight_on_series
        )
        # Clamp del ajuste absoluto.
        delta = _clamp(raw_adjusted - base_er, -ADJUSTMENT_CLAMP, ADJUSTMENT_CLAMP)
        adjusted_er = base_er + delta
        series_adjustment = delta

    # ── Edge vs línea ────────────────────────────────────────────────
    edge: Optional[float] = None
    if adjusted_er is not None and mkt is not None:
        edge = adjusted_er - mkt

    edge_band = _edge_band(edge)

    # ── Slope (sólo con la serie activa; necesita ≥3) ────────────────
    slope = _linear_slope(active_values)
    if slope is None:
        slope_band = "INSUFFICIENT_SAMPLE_FOR_SERIES_TREND"
    else:
        if slope > 1.0:
            slope_band = "EXPANSION_STRONG"
        elif slope > 0.30:
            slope_band = "EXPANSION_LIGHT"
        elif slope >= -0.29:
            slope_band = "STABLE"
        elif slope >= -1.0:
            slope_band = "CONTRACTION_LIGHT"
        else:
            slope_band = "CONTRACTION_STRONG"

    # ── Score de influencia ─────────────────────────────────────────
    edge_component = _clamp((edge or 0.0) * 2.5, -EDGE_COMPONENT_CAP, EDGE_COMPONENT_CAP)
    slope_component = (
        _clamp(slope, -SLOPE_COMPONENT_CAP, SLOPE_COMPONENT_CAP)
        if slope is not None else 0.0
    )
    bp_comp = _clamp(_safe_float(bullpen_fatigue_component) or 0.0,
                      -BULLPEN_FATIGUE_CAP, BULLPEN_FATIGUE_CAP)
    pm_comp = _clamp(_safe_float(pitching_matchup_component) or 0.0,
                      -PITCHING_MATCHUP_CAP, PITCHING_MATCHUP_CAP)
    var_comp = _variance_component(edge, cv)

    raw_score = edge_component + slope_component + bp_comp + pm_comp + var_comp
    score = _clamp(raw_score, -SCORE_CAP, SCORE_CAP)
    confidence_modifier = _clamp(score * 0.5,
                                   -CONFIDENCE_MODIFIER_CAP,
                                   CONFIDENCE_MODIFIER_CAP)

    reason_codes: list[str] = []
    if n_active + n_h2h < 3:
        reason_codes.append("LIMITED_SAMPLE_SERIES_SIGNAL")
    if n_active < 3:
        reason_codes.append("INSUFFICIENT_SAMPLE_FOR_SERIES_TREND")
    if base_er is None:
        reason_codes.append("NO_BASE_EXPECTED_RUNS")
    if mkt is None:
        reason_codes.append("NO_MARKET_TOTAL")

    return {
        "available":              True,
        "reason_code":            "OK",
        "n_active":               n_active,
        "n_h2h":                  n_h2h,
        "n_effective":            round(n_effective, 4),
        "weighted_series_runs":   (round(weighted, 4) if weighted is not None else None),
        "series_reliability":     round(reliability, 4),
        "adjusted_expected_runs": (round(adjusted_er, 4) if adjusted_er is not None else None),
        "series_adjustment":      round(series_adjustment, 4),
        "series_edge_runs":       (round(edge, 4) if edge is not None else None),
        "edge_band":              edge_band,
        "variability":            {
            "mean":   None if mean is None else round(mean, 4),
            "median": None if median is None else round(median, 4),
            "std":    None if std is None else round(std, 4),
            "min":    None if vmin is None else round(vmin, 4),
            "max":    None if vmax is None else round(vmax, 4),
            "cv":     None if cv is None else round(cv, 4),
            "band":   cv_band,
        },
        "series_slope":           None if slope is None else round(slope, 4),
        "slope_band":             slope_band,
        "series_context_score":   round(score, 4),
        "score_breakdown":        {
            "edge_runs":         round(edge_component, 4),
            "slope":             round(slope_component, 4),
            "bullpen_fatigue":   round(bp_comp, 4),
            "pitching_matchup":  round(pm_comp, 4),
            "variance":          round(var_comp, 4),
        },
        "confidence_modifier":    round(confidence_modifier, 4),
        "market_total":           mkt,
        "reason_codes":           reason_codes,
        "observe_only":           True,
    }


__all__ = [
    "calculate_series_total_signal",
    "ACTIVE_SERIES_WEIGHT",
    "PREVIOUS_SERIES_H2H_WEIGHT",
    "RECENCY_WEIGHTS",
    "MAX_INFLUENCE_PCT",
    "ADJUSTMENT_CLAMP",
    "CONFIDENCE_MODIFIER_CAP",
    "SCORE_CAP",
    "EDGE_BAND_LABELS",
]
