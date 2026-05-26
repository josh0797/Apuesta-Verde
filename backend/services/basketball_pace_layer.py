"""Basketball Pace & Scoring Rescue Layer.

Diseñado en paralelo al `corner_market_layer` de fútbol. Cuando los mercados
directos (Moneyline, Spread principal) no entregan valor, intenta una pasada
sobre el mercado de Total Points proyectando el pace y la eficiencia ofensiva.

═══════════════════════════════════════════════════════════════════════════
ESTADO: STRUCTURED STUB — pre-fetch real pendiente
═══════════════════════════════════════════════════════════════════════════

La implementación completa requiere:
  1. Llamadas a API-Sports `/basketball/games/statistics?game={id}` para los
     últimos 5-10 partidos de cada equipo. Esto devuelve por equipo:
        - Total points scored / conceded
        - Field goal % / 3P% / FT%
        - Possessions (calculable: FGA + 0.475*FTA - OffReb + TOV)
        - Pace = (possessions / minutes) * 48
        - Offensive Rating = points / possessions * 100
        - Defensive Rating = points_against / possessions * 100
  2. Cache agresivo por (team_id, season) — corner_market_layer ya muestra
     el patrón usando `_cache_get` / `_cache_set` en api_sports.py.
  3. Pre-fetch async en analyst_engine.py Phase 10a (paralelo al de córners).

El layer actual ya define la arquitectura de métricas, scores y trap
signals para que la implementación de la capa de fetch sea quirúrgica.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from . import market_tolerance as mt

log = logging.getLogger("basketball_pace")


# ── Configuración ──────────────────────────────────────────────────────────
MIN_PROJECTION_MARGIN = 6.0    # ≥6 puntos de margen vs línea
MIN_EDGE_VALUE = 0.04          # +4%
MIN_EDGE_PROTECTED_ACC = -0.015

MIN_SAMPLE_SIZE = 3

# Promedios típicos por liga (puntos totales por partido)
DEFAULT_LEAGUE_TOTAL = {
    "NBA":      225.0,
    "EuroLeague": 162.0,
    "FIBA":     158.0,
    "default":  200.0,
}


def _shrink(value: Optional[float], sample: int, mean: float, k: int = 5) -> float:
    if value is None:
        return mean
    w = sample / (sample + k)
    return w * value + (1 - w) * mean


def compute_basketball_pace_metrics(
    home_form: dict,
    away_form: dict,
    *,
    league_avg_total: Optional[float] = None,
    league_key: str = "default",
) -> dict:
    """Computes pace/scoring metrics from per-team form dicts.

    Expected per-team form shape (mirrors what `_prefetch_basketball_pace_forms`
    will produce):
        {
          "sample_size":         int,
          "avg_points_for":      float,
          "avg_points_against":  float,
          "pace":                float,   # possessions/48min
          "offensive_rating":    float,   # points per 100 poss
          "defensive_rating":    float,
          "per_match":           list[dict],
          "missing_data":        bool,
        }

    Returns dict with combinedPointsProjection, paceProjection, etc.
    """
    league_avg = league_avg_total or DEFAULT_LEAGUE_TOTAL.get(league_key, DEFAULT_LEAGUE_TOTAL["default"])

    if not home_form or not away_form:
        return {
            "paceProjection":             None,
            "combinedPointsProjection":   league_avg,
            "offensiveEfficiencyScore":   50,
            "defensiveResistanceScore":   50,
            "basketballMarketFitScore":   25,
            "basketballFragilityScore":   85,
            "sampleSizeHome":             0,
            "sampleSizeAway":             0,
            "dataQuality":                "insufficient",
        }

    s_h = home_form.get("sample_size") or 0
    s_a = away_form.get("sample_size") or 0
    half_league = league_avg / 2.0

    pts_for_h     = _shrink(home_form.get("avg_points_for"),     s_h, half_league)
    pts_for_a     = _shrink(away_form.get("avg_points_for"),     s_a, half_league)
    pts_against_h = _shrink(home_form.get("avg_points_against"), s_h, half_league)
    pts_against_a = _shrink(away_form.get("avg_points_against"), s_a, half_league)

    # Proyección por equipo: media de (lo que anota) y (lo que el rival concede)
    proj_home = (pts_for_h + pts_against_a) / 2.0
    proj_away = (pts_for_a + pts_against_h) / 2.0
    combined  = proj_home + proj_away

    # Pace projection: promedio simple de paces individuales (mejor metric
    # real requiere modelo Dean Oliver — aproximación aquí)
    pace_h = home_form.get("pace") or 100.0
    pace_a = away_form.get("pace") or 100.0
    pace_proj = (pace_h + pace_a) / 2.0

    off_rtg_h = home_form.get("offensive_rating") or 110.0
    off_rtg_a = away_form.get("offensive_rating") or 110.0
    def_rtg_h = home_form.get("defensive_rating") or 110.0
    def_rtg_a = away_form.get("defensive_rating") or 110.0

    off_eff_score = max(0, min(100, int((off_rtg_h + off_rtg_a) / 2 - 90)))   # 90→0, 140→50
    def_res_score = max(0, min(100, int(140 - (def_rtg_h + def_rtg_a) / 2)))

    # Fit & fragility
    delta_vs_league = (combined - league_avg) / max(league_avg, 1.0)
    fit = 0
    if s_h >= MIN_SAMPLE_SIZE:
        fit += 25
    if s_a >= MIN_SAMPLE_SIZE:
        fit += 25
    if abs(delta_vs_league) >= 0.05:
        fit += 25
    if s_h >= 5 and s_a >= 5:
        fit += 25
    fit = min(100, fit)

    fragility = 0
    if s_h < MIN_SAMPLE_SIZE:
        fragility += 25
    if s_a < MIN_SAMPLE_SIZE:
        fragility += 25
    if home_form.get("missing_data"):
        fragility += 10
    if away_form.get("missing_data"):
        fragility += 10
    fragility = min(100, fragility)

    quality = (
        "good"  if (s_h >= 5 and s_a >= 5) else
        "ok"    if (s_h >= 3 and s_a >= 3) else
        "thin"  if (s_h >= 1 and s_a >= 1) else
        "insufficient"
    )

    return {
        "paceProjection":             round(pace_proj, 1),
        "combinedPointsProjection":   round(combined, 1),
        "projectionHome":             round(proj_home, 1),
        "projectionAway":             round(proj_away, 1),
        "offensiveEfficiencyScore":   off_eff_score,
        "defensiveResistanceScore":   def_res_score,
        "basketballMarketFitScore":   fit,
        "basketballFragilityScore":   fragility,
        "leagueAvgTotal":             round(league_avg, 1),
        "sampleSizeHome":             s_h,
        "sampleSizeAway":             s_a,
        "avgPointsForHome":           home_form.get("avg_points_for"),
        "avgPointsForAway":           away_form.get("avg_points_for"),
        "avgPointsAgainstHome":       home_form.get("avg_points_against"),
        "avgPointsAgainstAway":       away_form.get("avg_points_against"),
        "dataQuality":                quality,
    }


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Aproximación de CDF normal para Total Points (basket: ~normal dist)."""
    if sigma <= 0:
        return 0.5
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2))))


def find_basketball_pace_value(
    match: dict,
    *,
    why_direct_failed: Optional[str] = None,
) -> Optional[dict]:
    """Intenta encontrar valor en el mercado de Total Points basketball.

    Pre-requisitos:
      match["_basketball_pace_form"] = {"home": {...}, "away": {...}, "league_avg_total": float}

    Returns None si no hay valor o no hay datos.
    """
    form = match.get("_basketball_pace_form") or {}
    home_form = form.get("home") or {}
    away_form = form.get("away") or {}

    if not home_form or not away_form:
        return None
    if (home_form.get("sample_size") or 0) < 1:
        return None

    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None
    markets = (snaps[-1] or {}).get("markets") or {}

    # Buscar líneas de Total (Over/Under puntos)
    total_rows = markets.get("Total") or markets.get("Over/Under") or markets.get("Points") or []
    if not isinstance(total_rows, list) or not total_rows:
        return None

    # Extraer líneas {line: {over: odd, under: odd}}
    import re as _re
    buckets: dict[float, dict] = {}
    for r in total_rows:
        if not isinstance(r, dict):
            continue
        lines = r.get("lines") or {}
        if isinstance(lines, dict):
            for k, v in lines.items():
                if not isinstance(v, (int, float)) or v <= 1.01:
                    continue
                k_s = str(k).lower()
                m = _re.search(r"([0-9]+(?:\.[0-9]+)?)", k_s)
                if not m:
                    continue
                line_num = float(m.group(1))
                side = "over" if "over" in k_s else "under" if "under" in k_s else None
                if side is None:
                    continue
                b = buckets.setdefault(line_num, {"line": line_num})
                if side not in b or float(v) > b[side]:
                    b[side] = float(v)

    if not buckets:
        return None

    metrics = compute_basketball_pace_metrics(
        home_form, away_form,
        league_avg_total=form.get("league_avg_total"),
        league_key=form.get("league_key", "default"),
    )
    if metrics["basketballMarketFitScore"] < 40:
        return None

    combined = metrics["combinedPointsProjection"]
    fragility = metrics["basketballFragilityScore"]
    # En basket, sigma típica ~ 12-15 pts para Total games
    sigma = 13.0

    best_pick: Optional[dict] = None
    best_edge = float("-inf")

    for line, sides in sorted(buckets.items()):
        for side in ("over", "under"):
            if side not in sides:
                continue
            decimal_odds = sides[side]
            implied = 1.0 / decimal_odds
            if side == "over":
                est_prob = 1.0 - _normal_cdf(line, combined, sigma)
                margin   = combined - line
            else:
                est_prob = _normal_cdf(line, combined, sigma)
                margin   = line - combined
            edge = est_prob - implied
            if margin < MIN_PROJECTION_MARGIN:
                continue
            if edge < MIN_EDGE_PROTECTED_ACC:
                continue
            if fragility > 75:
                continue
            if edge <= best_edge:
                continue

            cls = (
                "VALUE_BET" if edge >= MIN_EDGE_VALUE else
                "PROTECTED_ACCEPTABLE"
            )
            best_edge = edge
            best_pick = {
                "line": line, "side": side, "decimal_odds": decimal_odds,
                "estimatedProbability": round(est_prob, 4),
                "impliedProbability":   round(implied, 4),
                "edge":                 round(edge, 4),
                "classification":       cls,
                "margin_vs_line":       round(margin, 2),
            }

    if not best_pick:
        return None

    side_label = "Over" if best_pick["side"] == "over" else "Under"
    selection = f"Más de {best_pick['line']} puntos" if best_pick["side"] == "over" \
                else f"Menos de {best_pick['line']} puntos"

    reasons = []
    avg_h_for = metrics.get("avgPointsForHome") or 0
    avg_a_for = metrics.get("avgPointsForAway") or 0
    if avg_h_for and avg_a_for:
        reasons.append(f"Promedio últimos partidos: local {avg_h_for:.1f} pts, visitante {avg_a_for:.1f} pts.")
    reasons.append(
        f"Proyección del motor: {combined:.1f} pts "
        f"({'+' if combined > best_pick['line'] else ''}{combined - best_pick['line']:.1f} vs línea {best_pick['line']})."
    )
    if metrics["paceProjection"]:
        reasons.append(f"Pace proyectado: {metrics['paceProjection']:.1f} posesiones/48min.")
    reasons.append("Mercado directo sin valor — Total Points refleja mejor la dinámica esperada.")

    risks = []
    if best_pick["side"] == "over":
        risks.append("Riesgo de garbage time o tiempo muerto al final si la diferencia es grande.")
    else:
        risks.append("Riesgo de prórroga: cualquier minuto extra añade ~10 puntos al total.")
    if metrics["sampleSizeHome"] < 5 or metrics["sampleSizeAway"] < 5:
        risks.append("Muestra de partidos recientes limitada.")

    return {
        "rescued":         True,
        "rescueType":      "BASKETBALL_PACE",
        "routed_to":       "rescued_picks",
        "market":          f"Total Points {side_label}",
        "selection":       selection,
        "decimal_odds":    best_pick["decimal_odds"],
        "edge":            best_pick["edge"],
        "estimatedProbability": best_pick["estimatedProbability"],
        "impliedProbability":   best_pick["impliedProbability"],
        "classification":  best_pick["classification"],
        "confidence":      max(55, min(80, metrics["basketballMarketFitScore"])),
        "basketballMarketFitScore":  metrics["basketballMarketFitScore"],
        "basketballFragilityScore":  metrics["basketballFragilityScore"],
        "metrics":          metrics,
        "reasons":          reasons,
        "risks":            risks,
        "whyDirectMarketsFailed": why_direct_failed or "Mercados directos sin edge claro.",
        "whyThisMarketIsSafer":   (
            f"Total Points captura el ritmo global del partido sin necesidad de "
            f"acertar al ganador. Con una proyección de {combined:.0f} pts y la "
            f"línea en {best_pick['line']}, el margen estadístico está claramente "
            f"a favor del {side_label}."
        ),
        "reason": (
            f"Proyección {combined:.1f} pts vs línea {best_pick['line']}, "
            f"edge {best_pick['edge']*100:+.1f}%, "
            f"fit {metrics['basketballMarketFitScore']}/100, "
            f"fragility {metrics['basketballFragilityScore']}/100."
        ),
        "market_category":  mt.CATEGORY_PROTECTED,
        "tolerance_used":   mt.CATEGORY_PROTECTED,
        "fragility_score":  metrics["basketballFragilityScore"],
        "trap_signals_structured": [],
        "_source":          "basketball_pace_layer_v1",
    }


__all__ = [
    "compute_basketball_pace_metrics",
    "find_basketball_pace_value",
    "MIN_PROJECTION_MARGIN",
]
