"""
Football Total Signal — Sprint D10 (módulo matemático puro).

Construye una señal contextual de goles para mercados Over/Under en
fútbol. Adapta los principios de `mlb_series_total_signal` (D9.3-B) al
universo del fútbol con varianza más alta y muestras heterogéneas
(H2H, forma reciente, xG).

Contrato del módulo
-------------------
El módulo es PURO:
  * No accede a APIs ni a MongoDB.
  * No conoce componentes React ni variables de UI.
  * No realiza scraping.
  * No conoce cuotas — la cuota se procesa en
    `football_total_market_value` aparte.

Recibe estructuras ya normalizadas y devuelve un resultado determinista.

Reglas de diseño (anti-circularidad)
------------------------------------
* La línea (`market_total`) NUNCA modifica las proyecciones:
    - `weighted_h2h_goals`, `weighted_recent_goals`, `weighted_xg_total`
    - `contextual_goal_mean`, `context_adjustment`, `adjusted_expected_goals`
  Sólo se usa para `total_edge_goals`, `lean` y bandas.
* La cuota tampoco modifica las proyecciones — vive en otro módulo.
* `influence_score` depende de: distancia a la línea, confiabilidad,
  variabilidad, xG, alineaciones y contexto competitivo. No depende
  de EV ni de la cuota.

Pesos
-----
* Recencia (más reciente primero):
    1.00 · 0.75 · 0.55 · 0.40 · 0.30
* Bloques (cuando todas las fuentes están disponibles):
    H2H            : 0.25
    RECENT_FORM    : 0.40
    XG             : 0.35
* Si falta una fuente, los pesos se renormalizan entre las disponibles.

Shrinkage
---------
* `effective_n = h2h*0.5 + min(home_recent, away_recent)*1.0 + xg_factor`
* `reliability = effective_n / (effective_n + 5)`  (en fútbol la
  constante es 5, mayor que 3 de MLB, porque la varianza es alta).
* Clamp de ajuste según contexto:
    - normal                       → ±0.65
    - sample_quality == "VERY_LIMITED" or "LIMITED" → ±0.30
    - sin xG (pero con resto)      → ±0.45
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Optional

# ─── Constantes ──────────────────────────────────────────────────────
RECENCY_WEIGHTS: tuple[float, ...] = (1.00, 0.75, 0.55, 0.40, 0.30)

SOURCE_WEIGHTS: dict[str, float] = {
    "H2H":         0.25,
    "RECENT_FORM": 0.40,
    "XG":          0.35,
}

SHRINKAGE_K_FOOTBALL: int = 5
MAX_CONTEXT_INFLUENCE: float = 0.25

CLAMP_NORMAL: float        = 0.65
CLAMP_LIMITED_SAMPLE: float = 0.30
CLAMP_NO_XG: float          = 0.45

INFLUENCE_SCORE_CAP: float = 10.0
CONFIDENCE_DELTA_CAP: float = 5.0

# Bandas de variabilidad (CV) adaptadas a fútbol — umbrales más altos
# que MLB porque la varianza relativa por partido es mayor.
CV_STABLE_MAX_FB: float   = 0.35
CV_MODERATE_MAX_FB: float = 0.60

# Edge bands (en goles). Líneas medias en fútbol → bandas más
# pequeñas que MLB (donde eran ±0.60/1.25 carreras).
EDGE_BREAKS_FB = (-0.60, -0.30, 0.30, 0.60)
EDGE_LABELS_FB = (
    "STRONG_UNDER",
    "MODERATE_UNDER",
    "NEUTRAL",
    "MODERATE_OVER",
    "STRONG_OVER",
)

# Multiplier de strength de oponente (sección 6 del spec).
OPPONENT_STRENGTH_MULTIPLIERS = {
    "very_weak":  0.85,
    "weak":       0.93,
    "average":    1.00,
    "strong":     1.07,
    "elite":      1.15,
}


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


def _recency_weights_for(n: int) -> list[float]:
    """Pesos por recencia: el MÁS RECIENTE recibe 1.0. Se asume la
    lista de partidos ordenada del más reciente al más antiguo. Si
    n > len(table), los más antiguos comparten el peso mínimo."""
    if n <= 0:
        return []
    table = list(RECENCY_WEIGHTS)
    if n <= len(table):
        return table[:n]
    return table + [table[-1]] * (n - len(table))


def _weighted_mean_safe(values: list[float],
                          weights: list[float]) -> Optional[float]:
    if not values:
        return None
    s_w = sum(weights)
    if s_w <= 0:
        return None
    return sum(v * w for v, w in zip(values, weights)) / s_w


def _normalise_available_weights(available: dict[str, bool]) -> dict[str, float]:
    """Renormaliza SOURCE_WEIGHTS para las fuentes disponibles.
    Devuelve mapping {source_name: peso_normalizado} sumando 1.0."""
    avail = {s: SOURCE_WEIGHTS[s] for s, ok in available.items() if ok and s in SOURCE_WEIGHTS}
    total = sum(avail.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in avail.items()}


# ═════════════════════════════════════════════════════════════════════
# 1. Weighted H2H goals
# ═════════════════════════════════════════════════════════════════════
def calculate_weighted_h2h_goals(
    h2h_games: Optional[Iterable[dict]],
    current_match_context: Optional[dict] = None,
) -> dict:
    """Pondera los goles totales de partidos H2H recientes.

    Cada partido `{date, home_goals, away_goals, total_goals,
    competition_id, competition_type, venue_type, status,
    age_days?, is_friendly?}`. Excluye:
      - status != "FINAL"
      - total_goals missing/None
      - antigüedad > 7 años
      - amistosos cuando el partido actual NO es amistoso

    Aplica multiplicadores de contexto (sec 4 del spec) y pesos de
    recencia (1.00/0.75/0.55/0.40/0.30).

    Returns:
        {"value": float | None, "n_valid": int, "samples_excluded": int,
         "reasons": list[str]}
    """
    cur = current_match_context or {}
    cur_is_friendly = bool(cur.get("is_friendly"))
    cur_competition_type = cur.get("competition_type")

    valid: list[tuple[float, float]] = []  # (total_goals, multiplier)
    excluded = 0
    if not h2h_games:
        return {"value": None, "n_valid": 0, "samples_excluded": 0, "reasons": ["NO_H2H_INPUT"]}

    for g in h2h_games:
        if not isinstance(g, dict):
            excluded += 1
            continue
        status = (g.get("status") or "").upper()
        if status and status != "FINAL":
            excluded += 1
            continue
        tot = _safe_float(g.get("total_goals"))
        if tot is None:
            hg = _safe_float(g.get("home_goals"))
            ag = _safe_float(g.get("away_goals"))
            if hg is None or ag is None:
                excluded += 1
                continue
            tot = hg + ag
        if tot < 0:
            excluded += 1
            continue
        age_days = _safe_float(g.get("age_days"))
        # Exclusión dura por antigüedad >7 años (spec sec 3).
        if age_days is not None and age_days > 365 * 7:
            excluded += 1
            continue
        is_friendly = bool(g.get("is_friendly"))
        if is_friendly and not cur_is_friendly:
            # Amistoso fuera de contexto → soft-include con multiplier
            # fuerte de penalización (spec sec 4).
            pass  # cae al multiplier (0.45) abajo

        # Multiplicador contextual (sec 4 del spec).
        mult = 1.0
        if age_days is not None:
            if age_days > 730:
                mult *= 0.55
            if age_days > 365 * 5:
                mult *= 0.25
        if is_friendly and not cur_is_friendly:
            mult *= 0.45
        if cur_competition_type and g.get("competition_type") and \
            g["competition_type"] != cur_competition_type:
            mult *= 0.70
        if g.get("home_away_orientation_changed"):
            mult *= 0.90
        valid.append((tot, mult))

    if not valid:
        return {"value": None, "n_valid": 0,
                  "samples_excluded": excluded,
                  "reasons": ["NO_VALID_H2H_AFTER_FILTERS"]}

    # Aplicar pesos por recencia (lista ya ordenada del más reciente).
    rw = _recency_weights_for(len(valid))
    weights = [w_rec * mult for w_rec, (_, mult) in zip(rw, valid)]
    values = [tot for tot, _ in valid]
    val = _weighted_mean_safe(values, weights)
    return {
        "value":            None if val is None else round(val, 4),
        "n_valid":          len(valid),
        "samples_excluded": excluded,
        "reasons":          [],
    }


# ═════════════════════════════════════════════════════════════════════
# 2. Weighted recent team form
# ═════════════════════════════════════════════════════════════════════
def _weighted_team_total(team_matches: Optional[Iterable[dict]]) -> Optional[float]:
    """Promedio ponderado de (goles a favor + en contra) para un equipo.
    Cada match: `{goals_scored, goals_conceded, opponent_strength?,
    is_friendly?, status?}`.
    """
    if not team_matches:
        return None
    valid: list[tuple[float, float]] = []
    for m in team_matches:
        if not isinstance(m, dict):
            continue
        status = (m.get("status") or "").upper()
        if status and status != "FINAL":
            continue
        gs = _safe_float(m.get("goals_scored"))
        gc = _safe_float(m.get("goals_conceded"))
        if gs is None or gc is None or gs < 0 or gc < 0:
            continue
        observed_total = gs + gc
        # Ajuste por fuerza de rival (sec 6 del spec).
        opp = (m.get("opponent_strength") or "average")
        mult = OPPONENT_STRENGTH_MULTIPLIERS.get(opp, 1.0)
        adjusted = observed_total * mult
        valid.append((adjusted, 1.0))
        if len(valid) >= len(RECENCY_WEIGHTS):
            break  # respetamos top-5

    if not valid:
        return None
    rw = _recency_weights_for(len(valid))
    weights = [w * mult for w, (_, mult) in zip(rw, valid)]
    values = [v for v, _ in valid]
    return _weighted_mean_safe(values, weights)


def calculate_weighted_recent_form(
    home_recent_matches: Optional[Iterable[dict]],
    away_recent_matches: Optional[Iterable[dict]],
) -> dict:
    """Sec 5 del spec. NO sumar las dos medias directamente — eso
    duplicaría el total. Se promedia."""
    home_v = _weighted_team_total(home_recent_matches)
    away_v = _weighted_team_total(away_recent_matches)
    n_home = sum(1 for _ in (home_recent_matches or []))
    n_away = sum(1 for _ in (away_recent_matches or []))
    if home_v is None and away_v is None:
        return {"value": None, "n_home": n_home, "n_away": n_away,
                  "reasons": ["NO_RECENT_FORM"]}
    if home_v is None or away_v is None:
        # Sólo un lado disponible — usamos el lado conocido como
        # proxy (la otra mitad queda en None pero el módulo no
        # bloquea).
        avail = home_v if home_v is not None else away_v
        return {"value": round(avail, 4),
                  "n_home": n_home, "n_away": n_away,
                  "reasons": ["RECENT_FORM_ONE_SIDE_ONLY"]}
    return {"value": round((home_v + away_v) / 2.0, 4),
              "n_home": n_home, "n_away": n_away,
              "reasons": []}


# ═════════════════════════════════════════════════════════════════════
# 3. Weighted xG total
# ═════════════════════════════════════════════════════════════════════
def calculate_weighted_xg_total(
    home_xg_recent: Optional[dict],
    away_xg_recent: Optional[dict],
) -> dict:
    """Sec 7 del spec.

    Cada `xg_recent` es un dict con `xg_for_l5`, `xg_against_l5`,
    `xg_for_l15`, `xg_against_l15`, `matches_available`.

    Proyección:
      home_expected_from_xg = (home_xg_for + away_xg_against) / 2
      away_expected_from_xg = (away_xg_for + home_xg_against) / 2
      total                = home_expected + away_expected

    Combina L5 (peso 0.65) con L15 (peso 0.35).
    """
    if not home_xg_recent and not away_xg_recent:
        return {"value": None, "matches_used": 0, "reasons": ["XG_CONTEXT_UNAVAILABLE"]}
    h = home_xg_recent or {}
    a = away_xg_recent or {}

    def _proj(window: str) -> Optional[float]:
        h_for  = _safe_float(h.get(f"xg_for_{window}"))
        h_ag   = _safe_float(h.get(f"xg_against_{window}"))
        a_for  = _safe_float(a.get(f"xg_for_{window}"))
        a_ag   = _safe_float(a.get(f"xg_against_{window}"))
        if h_for is None or h_ag is None or a_for is None or a_ag is None:
            return None
        h_exp = (h_for + a_ag) / 2.0
        a_exp = (a_for + h_ag) / 2.0
        return h_exp + a_exp

    p_l5 = _proj("l5")
    p_l15 = _proj("l15")

    if p_l5 is None and p_l15 is None:
        return {"value": None, "matches_used": 0,
                  "reasons": ["XG_CONTEXT_UNAVAILABLE"]}

    matches = int(max(_safe_float(h.get("matches_available")) or 0,
                       _safe_float(a.get("matches_available")) or 0))

    if p_l5 is not None and p_l15 is not None:
        val = p_l5 * 0.65 + p_l15 * 0.35
        return {"value": round(val, 4), "matches_used": matches, "reasons": []}
    val = p_l5 if p_l5 is not None else p_l15
    return {"value": round(val, 4), "matches_used": matches,
              "reasons": ["XG_WINDOW_PARTIAL"]}


# ═════════════════════════════════════════════════════════════════════
# 4. Contextual mean (combinación de fuentes)
# ═════════════════════════════════════════════════════════════════════
def calculate_contextual_goal_mean(
    weighted_h2h: Optional[float],
    weighted_recent: Optional[float],
    weighted_xg: Optional[float],
) -> dict:
    """Combina las tres fuentes con SOURCE_WEIGHTS, renormalizando si
    faltan. Devuelve {value, available_sources, weights_applied}."""
    sources = {
        "H2H":         weighted_h2h,
        "RECENT_FORM": weighted_recent,
        "XG":          weighted_xg,
    }
    avail_flags = {k: (v is not None) for k, v in sources.items()}
    weights = _normalise_available_weights(avail_flags)
    if not weights:
        return {"value": None, "available_sources": [],
                  "weights_applied": {}}
    val = sum(sources[k] * w for k, w in weights.items())
    return {
        "value": round(val, 4),
        "available_sources": sorted(weights.keys()),
        "weights_applied":   {k: round(v, 4) for k, v in weights.items()},
    }


# ═════════════════════════════════════════════════════════════════════
# 5. Sample reliability
# ═════════════════════════════════════════════════════════════════════
def calculate_context_reliability(
    h2h_n: int,
    home_recent_n: int,
    away_recent_n: int,
    xg_matches_available: Optional[int],
) -> dict:
    """Sec 9 del spec."""
    xg_factor = 0.0
    if xg_matches_available is not None and xg_matches_available > 0:
        xg_factor = min(xg_matches_available / 5.0, 1.0) * 3.0
    effective_n = (
        max(0, int(h2h_n)) * 0.50
        + min(max(0, int(home_recent_n)), max(0, int(away_recent_n))) * 1.0
        + xg_factor
    )
    reliability = effective_n / (effective_n + SHRINKAGE_K_FOOTBALL) if effective_n > 0 else 0.0

    if effective_n < 3:
        quality = "VERY_LIMITED"
    elif reliability < 0.40:
        quality = "LIMITED"
    elif reliability < 0.65:
        quality = "USABLE"
    else:
        quality = "STRONG"

    return {
        "effective_n":  round(effective_n, 4),
        "reliability":  round(reliability, 4),
        "quality":      quality,
        "xg_factor":    round(xg_factor, 4),
    }


# ═════════════════════════════════════════════════════════════════════
# 6. Variability
# ═════════════════════════════════════════════════════════════════════
def calculate_goal_variability(totals: list[float]) -> dict:
    """Sec 11 del spec. Bandas adaptadas a fútbol."""
    vals = [v for v in totals if isinstance(v, (int, float)) and v == v]
    if not vals:
        return {"mean": None, "median": None, "std": None,
                  "min": None, "max": None, "cv": None,
                  "class": "INSUFFICIENT_SAMPLE"}
    n = len(vals)
    mean = sum(vals) / n
    median = statistics.median(vals)
    std = statistics.pstdev(vals) if n >= 2 else 0.0
    vmin = min(vals)
    vmax = max(vals)
    cv = (std / mean) if mean > 0 else None
    if n < 3:
        cls = "INSUFFICIENT_SAMPLE"
    elif cv is None:
        cls = "INSUFFICIENT_SAMPLE"
    elif cv < CV_STABLE_MAX_FB:
        cls = "STABLE"
    elif cv <= CV_MODERATE_MAX_FB:
        cls = "MODERATE"
    else:
        cls = "VOLATILE"
    return {
        "mean":   round(mean, 4),
        "median": round(median, 4),
        "std":    round(std, 4),
        "min":    round(vmin, 4),
        "max":    round(vmax, 4),
        "cv":     None if cv is None else round(cv, 4),
        "class":  cls,
    }


# ═════════════════════════════════════════════════════════════════════
# 7. Influence score (-10..+10) — depende de DATOS, no de la cuota
# ═════════════════════════════════════════════════════════════════════
def _edge_band_fb(edge: Optional[float]) -> str:
    if edge is None:
        return "UNKNOWN"
    if edge <= EDGE_BREAKS_FB[0]:
        return EDGE_LABELS_FB[0]
    if edge <= EDGE_BREAKS_FB[1]:
        return EDGE_LABELS_FB[1]
    if edge < EDGE_BREAKS_FB[2]:
        return EDGE_LABELS_FB[2]
    if edge < EDGE_BREAKS_FB[3]:
        return EDGE_LABELS_FB[3]
    return EDGE_LABELS_FB[4]


def _variance_attenuation_fb(edge: Optional[float], cv_class: str) -> float:
    """Atenuación del componente de edge por variabilidad. Igual
    espíritu que mlb_series_total_signal pero adaptado a fútbol."""
    if edge is None:
        return 0.0
    mult = {
        "STABLE":   0.80,
        "MODERATE": 0.55,
        "VOLATILE": 0.25,
        "INSUFFICIENT_SAMPLE": 0.40,
    }.get(cv_class, 0.40)
    raw = edge * mult * 4.0  # escala a unidades de score
    return _clamp(raw, -2.0, 2.0)


def calculate_football_total_influence_score(
    *,
    total_edge_goals: Optional[float],
    reliability: float,
    cv_class: str,
    has_xg: bool,
    has_lineups: bool = False,
    competition_match: bool = True,
) -> dict:
    """Score ∈ [-10, +10]. Componentes:
      * edge_component       — clamp(edge*8, -4, +4); fútbol usa
                                escala mayor (1 gol ≈ 8 puntos) porque
                                las bandas son más estrechas.
      * reliability_component — clamp(reliability*4 - 2, -2, +2): cae
                                negativo cuando la muestra es muy
                                débil; sólo añade signo si edge != 0.
      * variance_component   — atenuación de edge por CV.
      * xg_component         — +1.0 si xG está disponible (señal más
                                fiable), -1.0 si está ausente y el
                                edge es != 0 (penaliza confianza).
      * lineups_component    — +0.5 cuando hay datos de alineación
                                (D10-future).
      * competition_match    — -1.0 cuando el contexto NO coincide
                                (e.g. amistoso vs oficial).
    """
    score = 0.0
    edge_comp = 0.0
    if total_edge_goals is not None:
        edge_comp = _clamp(total_edge_goals * 8.0, -4.0, 4.0)
    score += edge_comp

    rel_comp = _clamp(reliability * 4.0 - 2.0, -2.0, 2.0)
    score += rel_comp

    var_comp = _variance_attenuation_fb(total_edge_goals, cv_class)
    score += var_comp

    xg_comp = 0.0
    if has_xg:
        xg_comp = 1.0 if (total_edge_goals or 0) >= 0 else -1.0
        # xG presente → +1.0 en magnitud, en la dirección del edge.
        xg_comp = math.copysign(1.0, edge_comp) if edge_comp != 0 else 0.0
    elif total_edge_goals not in (None, 0.0):
        xg_comp = -1.0 * math.copysign(1.0, edge_comp)
    score += xg_comp

    lu_comp = 0.5 if has_lineups else 0.0
    # Dirección del lineup component sigue al edge.
    if has_lineups and edge_comp != 0:
        lu_comp = math.copysign(0.5, edge_comp)
    score += lu_comp

    comp_comp = 0.0
    if not competition_match and edge_comp != 0:
        comp_comp = -1.0 * math.copysign(1.0, edge_comp)
    score += comp_comp

    score = _clamp(score, -INFLUENCE_SCORE_CAP, INFLUENCE_SCORE_CAP)
    confidence_delta = _clamp(score * 0.5, -CONFIDENCE_DELTA_CAP, CONFIDENCE_DELTA_CAP)

    return {
        "score":             round(score, 4),
        "confidence_delta":  round(confidence_delta, 4),
        "components": {
            "edge":             round(edge_comp, 4),
            "reliability":      round(rel_comp, 4),
            "variance":         round(var_comp, 4),
            "xg":               round(xg_comp, 4),
            "lineups":          round(lu_comp, 4),
            "competition_match": round(comp_comp, 4),
        },
    }


# ═════════════════════════════════════════════════════════════════════
# 8. Función orquestadora principal — calculate_football_total_signal
# ═════════════════════════════════════════════════════════════════════
def calculate_football_total_signal(
    *,
    base_expected_goals: Optional[float],
    base_lambda_home: Optional[float],
    base_lambda_away: Optional[float],
    market_total: Optional[float],
    selection: Optional[str] = None,
    recent_h2h_games: Optional[Iterable[dict]] = None,
    home_recent_matches: Optional[Iterable[dict]] = None,
    away_recent_matches: Optional[Iterable[dict]] = None,
    home_xg_recent: Optional[dict] = None,
    away_xg_recent: Optional[dict] = None,
    lineup_context: Optional[dict] = None,
    competition_context: Optional[dict] = None,
    game_state_context: Optional[dict] = None,
    current_match_context: Optional[dict] = None,
    contextual_home_xg: Optional[float] = None,
    contextual_away_xg: Optional[float] = None,
) -> dict:
    """Devuelve el contrato D10 (sec "Contrato del módulo" del spec).

    `base_expected_goals`, `base_lambda_home`, `base_lambda_away` se
    aceptan opcionalmente. Si no se aportan los lambdas individuales,
    se asume reparto 50/50. La función NO mutará lambdas si no hay
    señal aplicable; en ese caso devuelve `BASE_MODEL_ONLY`.

    La señal NUNCA usa `selection` ni `market_total` para modificar
    las proyecciones (anti-circularidad). `market_total` se usa
    SOLO para `total_edge_goals`, `lean` y `influence_score`. La
    cuota no aparece aquí — vive en `football_total_market_value`.
    """
    base_eg = _safe_float(base_expected_goals)
    lam_h = _safe_float(base_lambda_home)
    lam_a = _safe_float(base_lambda_away)

    # Inferir base_expected_goals de las lambdas si no se aportó.
    if base_eg is None and lam_h is not None and lam_a is not None:
        base_eg = lam_h + lam_a

    market = _safe_float(market_total)

    # Contar muestra disponible.
    h2h_list = list(recent_h2h_games or [])
    home_list = list(home_recent_matches or [])
    away_list = list(away_recent_matches or [])

    h2h_block = calculate_weighted_h2h_goals(h2h_list, current_match_context)
    form_block = calculate_weighted_recent_form(home_list, away_list)
    xg_block = calculate_weighted_xg_total(home_xg_recent, away_xg_recent)
    ctx_block = calculate_contextual_goal_mean(
        h2h_block.get("value"),
        form_block.get("value"),
        xg_block.get("value"),
    )

    has_xg = xg_block.get("value") is not None
    rel_block = calculate_context_reliability(
        h2h_n=h2h_block.get("n_valid") or 0,
        home_recent_n=form_block.get("n_home") or 0,
        away_recent_n=form_block.get("n_away") or 0,
        xg_matches_available=xg_block.get("matches_used") if has_xg else 0,
    )

    # Recolectar totales para variabilidad — combinamos h2h + forma
    # individual (cuando hay totales).
    totals_for_variability: list[float] = []
    for g in h2h_list:
        if isinstance(g, dict):
            t = _safe_float(g.get("total_goals"))
            if t is None:
                hg, ag = _safe_float(g.get("home_goals")), _safe_float(g.get("away_goals"))
                if hg is not None and ag is not None:
                    t = hg + ag
            if t is not None and t >= 0:
                totals_for_variability.append(t)
    for m in home_list + away_list:
        if isinstance(m, dict):
            gs = _safe_float(m.get("goals_scored"))
            gc = _safe_float(m.get("goals_conceded"))
            if gs is not None and gc is not None and gs >= 0 and gc >= 0:
                totals_for_variability.append(gs + gc)
    var_block = calculate_goal_variability(totals_for_variability)

    # ── Empty path: nada de contexto, pero podemos seguir con base ──
    contextual_mean = ctx_block.get("value")
    reliability = rel_block.get("reliability") or 0.0
    quality = rel_block.get("quality")

    reason_codes: list[str] = []
    status = "FOOTBALL_TOTAL_SIGNAL_READY"

    if contextual_mean is None or base_eg is None:
        # No hay forma de calcular ajuste contextual.
        status = "BASE_MODEL_ONLY" if base_eg is not None else "MARKET_LINE_MISSING_OR_BASE_MISSING"
        reason_codes.append("INSUFFICIENT_CONTEXT_SAMPLE")
        return _emit_payload(
            status=status,
            base_eg=base_eg, lam_h=lam_h, lam_a=lam_a,
            h2h_block=h2h_block, form_block=form_block, xg_block=xg_block,
            ctx_block=ctx_block, rel_block=rel_block, var_block=var_block,
            adjusted_eg=base_eg, applied_adjustment=0.0, raw_adjustment=0.0,
            actual_influence=0.0,
            adjusted_lam_home=lam_h, adjusted_lam_away=lam_a,
            market=market, has_xg=has_xg,
            lineup_context=lineup_context,
            competition_context=competition_context,
            reason_codes=reason_codes,
        )

    # ── Shrinkage + clamp triple ──
    # Clamp dinámico según calidad de muestra y presencia de xG.
    if quality in ("VERY_LIMITED", "LIMITED"):
        clamp_abs = CLAMP_LIMITED_SAMPLE
    elif not has_xg:
        clamp_abs = CLAMP_NO_XG
    else:
        clamp_abs = CLAMP_NORMAL

    actual_influence = reliability * MAX_CONTEXT_INFLUENCE
    raw_adjusted = (
        base_eg * (1.0 - actual_influence)
        + contextual_mean * actual_influence
    )
    raw_adjustment = raw_adjusted - base_eg
    applied_adjustment = _clamp(raw_adjustment, -clamp_abs, clamp_abs)
    adjusted_eg = base_eg + applied_adjustment

    # ── Distribución del ajuste entre lambdas ──
    # Por defecto, proporcional a las lambdas base. Si hay contextual_home_xg
    # y contextual_away_xg válidos, usar share contextual.
    adjusted_lam_home = lam_h
    adjusted_lam_away = lam_a
    if lam_h is not None and lam_a is not None:
        total_base = lam_h + lam_a
        if total_base > 0:
            c_home = _safe_float(contextual_home_xg)
            c_away = _safe_float(contextual_away_xg)
            if c_home is not None and c_away is not None and (c_home + c_away) > 0:
                home_share = c_home / (c_home + c_away)
            else:
                home_share = lam_h / total_base
        else:
            home_share = 0.5
        away_share = 1.0 - home_share
        adjusted_lam_home = max(0.05, lam_h + applied_adjustment * home_share)
        adjusted_lam_away = max(0.05, lam_a + applied_adjustment * away_share)

    # ── Edge vs línea ──
    edge = (adjusted_eg - market) if market is not None else None

    # ── Score de influencia ──
    score_block = calculate_football_total_influence_score(
        total_edge_goals=edge,
        reliability=reliability,
        cv_class=var_block.get("class") or "INSUFFICIENT_SAMPLE",
        has_xg=has_xg,
        has_lineups=bool(lineup_context),
        competition_match=bool((competition_context or {}).get("matches", True)),
    )

    # Reason codes
    if edge is not None:
        if edge <= EDGE_BREAKS_FB[0]:
            reason_codes.append("EXPECTED_GOALS_BELOW_MARKET")
        elif edge >= EDGE_BREAKS_FB[3]:
            reason_codes.append("EXPECTED_GOALS_ABOVE_MARKET")
    if (h2h_block.get("value") or 0) and market is not None:
        if h2h_block["value"] < market - 0.5:
            reason_codes.append("H2H_SUPPORTS_UNDER")
        elif h2h_block["value"] > market + 0.5:
            reason_codes.append("H2H_SUPPORTS_OVER")
    if (xg_block.get("value") or 0) and market is not None:
        if xg_block["value"] < market - 0.5:
            reason_codes.append("RECENT_XG_SUPPORTS_UNDER")
        elif xg_block["value"] > market + 0.5:
            reason_codes.append("RECENT_XG_SUPPORTS_OVER")
    if var_block.get("class") == "MODERATE":
        reason_codes.append("MODERATE_GOAL_VARIANCE")
    if var_block.get("class") == "VOLATILE":
        reason_codes.append("VOLATILE_GOAL_VARIANCE")
    if not has_xg:
        reason_codes.append("XG_CONTEXT_UNAVAILABLE")
    if quality in ("VERY_LIMITED", "LIMITED"):
        reason_codes.append("LIMITED_SAMPLE_CLAMP_APPLIED")
    if abs(raw_adjustment) > abs(applied_adjustment) + 1e-9:
        reason_codes.append("ADJUSTMENT_CLAMPED")

    return _emit_payload(
        status=status,
        base_eg=base_eg, lam_h=lam_h, lam_a=lam_a,
        h2h_block=h2h_block, form_block=form_block, xg_block=xg_block,
        ctx_block=ctx_block, rel_block=rel_block, var_block=var_block,
        adjusted_eg=adjusted_eg,
        applied_adjustment=applied_adjustment,
        raw_adjustment=raw_adjustment,
        actual_influence=actual_influence,
        adjusted_lam_home=adjusted_lam_home,
        adjusted_lam_away=adjusted_lam_away,
        market=market, has_xg=has_xg,
        lineup_context=lineup_context,
        competition_context=competition_context,
        clamp_used=clamp_abs,
        edge=edge,
        score_block=score_block,
        reason_codes=reason_codes,
        selection=selection,
    )


def _emit_payload(*, status, base_eg, lam_h, lam_a,
                    h2h_block, form_block, xg_block,
                    ctx_block, rel_block, var_block,
                    adjusted_eg, applied_adjustment, raw_adjustment,
                    actual_influence,
                    adjusted_lam_home, adjusted_lam_away,
                    market, has_xg,
                    lineup_context=None, competition_context=None,
                    clamp_used=CLAMP_NORMAL, edge=None,
                    score_block=None, reason_codes=None,
                    selection=None) -> dict:
    out = {
        "status": status,
        "base": {
            "lambda_home":   None if lam_h is None else round(lam_h, 4),
            "lambda_away":   None if lam_a is None else round(lam_a, 4),
            "expected_goals": None if base_eg is None else round(base_eg, 4),
        },
        "context_sources": {
            "weighted_h2h_goals":    h2h_block.get("value"),
            "weighted_recent_goals": form_block.get("value"),
            "weighted_xg_total":     xg_block.get("value"),
            "available_sources":     ctx_block.get("available_sources") or [],
            "weights_applied":       ctx_block.get("weights_applied") or {},
            "h2h_samples_excluded":  h2h_block.get("samples_excluded") or 0,
        },
        "sample": {
            "effective_n": rel_block.get("effective_n") or 0.0,
            "reliability": rel_block.get("reliability") or 0.0,
            "quality":     rel_block.get("quality") or "VERY_LIMITED",
            "xg_factor":   rel_block.get("xg_factor") or 0.0,
        },
        "adjustment": {
            "contextual_goal_mean":  ctx_block.get("value"),
            "maximum_influence":     MAX_CONTEXT_INFLUENCE,
            "actual_influence":      round(actual_influence, 4),
            "raw_adjustment":        round(raw_adjustment, 4),
            "applied_adjustment":    round(applied_adjustment, 4),
            "clamp_used":            round(clamp_used, 4),
            "adjusted_expected_goals": None if adjusted_eg is None else round(adjusted_eg, 4),
            "adjusted_lambda_home":  None if adjusted_lam_home is None else round(adjusted_lam_home, 4),
            "adjusted_lambda_away":  None if adjusted_lam_away is None else round(adjusted_lam_away, 4),
        },
        "variability": var_block,
        "market_context": {
            "line":               market,
            "total_edge_goals":   None if edge is None else round(edge, 4),
            "lean":               _edge_band_fb(edge) if edge is not None else "UNKNOWN",
            "influence_score":    (score_block or {}).get("score", 0.0),
            "confidence_delta":   (score_block or {}).get("confidence_delta", 0.0),
            "score_components":   (score_block or {}).get("components", {}),
            "selection":          selection,
        },
        "reason_codes": reason_codes or [],
        "observe_only": True,
    }
    return out


__all__ = [
    "calculate_weighted_h2h_goals",
    "calculate_weighted_recent_form",
    "calculate_weighted_xg_total",
    "calculate_contextual_goal_mean",
    "calculate_context_reliability",
    "calculate_goal_variability",
    "calculate_football_total_influence_score",
    "calculate_football_total_signal",
    "SOURCE_WEIGHTS",
    "RECENCY_WEIGHTS",
    "CLAMP_NORMAL",
    "CLAMP_LIMITED_SAMPLE",
    "CLAMP_NO_XG",
    "MAX_CONTEXT_INFLUENCE",
    "SHRINKAGE_K_FOOTBALL",
]
