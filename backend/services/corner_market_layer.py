"""Corner Market Rescue Layer — Football.

Cuando los mercados directos (1X2, Doble Op, DNB) y los mercados protegidos
de goles (Under 3.5, Over 1.5) no entregan valor, este módulo intenta una
última pasada por el mercado de córners totales.

Filosofía:
  - No recomendar Over córners solo porque el promedio es alto.
  - Aplicar Moneyball guardrail (edge = est_prob - implied_prob).
  - Detectar trap signals específicos de córners (favorito que controla
    sin presionar, baja liquidez, H2H contradictorio, etc.).

Fuente de datos:
  match["_corner_form"] = {
    "home": {avg_for, avg_against, sample_size, per_match, missing_data, ...},
    "away": {avg_for, avg_against, sample_size, per_match, missing_data, ...},
    "h2h":  optional list of past corner totals (or None),
    "league_avg": optional float (e.g. 10.2 corners/game para Ligue 1),
  }

Pre-fetch: el caller (analyst_engine Phase 10) debe poblar `_corner_form`
antes de invocar `find_corner_value()`. Hacerlo dentro de este módulo lo
ataría a httpx/async; preferimos un módulo sync que solo lee datos.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from . import market_tolerance as mt

log = logging.getLogger("corner_market")


# ── Configuración ──────────────────────────────────────────────────────────
# Mínima diferencia (proyección - línea) para considerar Over.
# Spec: 1.0 corner mínimo de margen sobre la línea.
MIN_PROJECTION_MARGIN_OVER = 1.0

# Edge mínimo (estimatedProb - impliedProb) para recomendar como VALUE_BET.
MIN_EDGE_VALUE = 0.04          # +4%
MIN_EDGE_PROTECTED_ACC = -0.015 # -1.5% (mismo piso que protected markets)

# Tamaño mínimo de muestra para confiar en el promedio.
MIN_SAMPLE_SIZE = 3

# League averages típicas (corners totales por partido). Si no se provee
# `league_avg` en el match, usamos 10.0 como neutral fallback.
DEFAULT_LEAGUE_AVG_CORNERS = 10.0


# ── Modelo de proyección de córners ────────────────────────────────────────
def _shrink_to_mean(value: float, sample: int, mean: float, k: int = 5) -> float:
    """Bayesian shrinkage: con pocos datos, regresar a la media.

    `k` controla la fuerza del prior — 5 partidos es razonable.
    Resultado: cuando sample < k el valor gravita hacia la media de liga;
    cuando sample >> k el valor casi no cambia.
    """
    if value is None:
        return mean
    weight = sample / (sample + k)
    return weight * value + (1 - weight) * mean


def compute_corner_metrics(
    home_form: dict,
    away_form: dict,
    *,
    h2h_avg_total: Optional[float] = None,
    league_avg_total: Optional[float] = None,
) -> dict:
    """Calcula las métricas de proyección de córners para un partido.

    Returns dict con:
      cornerForAvgHomeLast5:     float | None
      cornerForAvgAwayLast5:     float | None
      cornerAgainstAvgHomeLast5: float | None
      cornerAgainstAvgAwayLast5: float | None
      h2hCornerAvg:              float | None
      leagueAvgCorners:          float
      combinedCornerProjection:  float  (proyección "justa" del partido)
      cornerPressureScore:       int 0-100 (cuanta presión ofensiva esperar)
      cornerFitScore:            int 0-100 (qué tan adecuado es el mercado)
      cornerFragilityScore:      int 0-100 (volatilidad/riesgo)
      sampleSizeHome / sampleSizeAway / dataQuality: meta
    """
    league_avg = league_avg_total or DEFAULT_LEAGUE_AVG_CORNERS

    # Si no hay datos en absoluto, devolver métricas vacías
    if not home_form or not away_form:
        return {
            "cornerForAvgHomeLast5":     None,
            "cornerForAvgAwayLast5":     None,
            "cornerAgainstAvgHomeLast5": None,
            "cornerAgainstAvgAwayLast5": None,
            "h2hCornerAvg":              h2h_avg_total,
            "leagueAvgCorners":          league_avg,
            "combinedCornerProjection":  league_avg,
            "cornerPressureScore":       50,
            "cornerFitScore":            30,
            "cornerFragilityScore":      80,
            "sampleSizeHome":            0,
            "sampleSizeAway":            0,
            "dataQuality":               "insufficient",
        }

    s_h = home_form.get("sample_size") or 0
    s_a = away_form.get("sample_size") or 0

    # Bayesian shrinkage de cada métrica
    half_league = league_avg / 2.0  # media "por equipo" = mitad del total

    for_h     = _shrink_to_mean(home_form.get("avg_for"),     s_h, half_league)
    for_a     = _shrink_to_mean(away_form.get("avg_for"),     s_a, half_league)
    against_h = _shrink_to_mean(home_form.get("avg_against"), s_h, half_league)
    against_a = _shrink_to_mean(away_form.get("avg_against"), s_a, half_league)

    # Proyección combinada: cada equipo aporta sus tiros + lo que concede el rival
    # se promedia para evitar doble-conteo.
    proj_home = (for_h + against_a) / 2.0
    proj_away = (for_a + against_h) / 2.0
    combined  = proj_home + proj_away

    # Si tenemos H2H, hacer mix 80/20 hacia la proyección (corners varían más
    # por matchup específico que por baseline; H2H se usa como adjuste menor)
    if h2h_avg_total is not None and h2h_avg_total > 0:
        combined = 0.80 * combined + 0.20 * h2h_avg_total

    # Pressure score (0-100): qué tan "ofensivo" es el matchup
    # Basado en cuán por encima de la media de liga proyecta el combined.
    delta_vs_league = (combined - league_avg) / max(league_avg, 1.0)
    pressure_score = max(0, min(100, int(50 + delta_vs_league * 100)))

    # Fit score (0-100): qué tan adecuado es analizar este mercado
    #  - sample size suficiente
    #  - proyección no neutral (vale la pena buscar valor)
    #  - corner data quality
    fit = 0
    if s_h >= MIN_SAMPLE_SIZE:
        fit += 25
    if s_a >= MIN_SAMPLE_SIZE:
        fit += 25
    if abs(delta_vs_league) >= 0.10:
        fit += 20  # al menos 10% off-baseline
    if h2h_avg_total is not None and h2h_avg_total > 0:
        fit += 15
    if s_h >= 5 and s_a >= 5:
        fit += 15
    fit = min(100, fit)

    # Fragility (0-100): volatility del mercado
    # Penalizamos:
    #  - sample bajo
    #  - matchup balanceado (proj home ≈ proj away pero combined alto = un
    #    equipo puede frenar al otro)
    #  - missing data
    fragility = 0
    if s_h < MIN_SAMPLE_SIZE:
        fragility += 25
    if s_a < MIN_SAMPLE_SIZE:
        fragility += 25
    if home_form.get("missing_data"):
        fragility += 10
    if away_form.get("missing_data"):
        fragility += 10
    # Si los promedios son extremadamente desbalanceados → riesgo
    if proj_home > 0 and proj_away > 0:
        imbalance = abs(proj_home - proj_away) / max(proj_home + proj_away, 1.0)
        if imbalance > 0.45:
            fragility += 15
    fragility = min(100, fragility)

    quality = (
        "good" if (s_h >= 5 and s_a >= 5) else
        "ok"   if (s_h >= 3 and s_a >= 3) else
        "thin" if (s_h >= 1 and s_a >= 1) else
        "insufficient"
    )

    return {
        "cornerForAvgHomeLast5":     home_form.get("avg_for"),
        "cornerForAvgAwayLast5":     away_form.get("avg_for"),
        "cornerAgainstAvgHomeLast5": home_form.get("avg_against"),
        "cornerAgainstAvgAwayLast5": away_form.get("avg_against"),
        "h2hCornerAvg":              h2h_avg_total,
        "leagueAvgCorners":          round(league_avg, 2),
        "combinedCornerProjection":  round(combined, 2),
        "projectionHome":            round(proj_home, 2),
        "projectionAway":            round(proj_away, 2),
        "cornerPressureScore":       pressure_score,
        "cornerFitScore":            fit,
        "cornerFragilityScore":      fragility,
        "sampleSizeHome":            s_h,
        "sampleSizeAway":            s_a,
        "dataQuality":               quality,
    }


# ── Probabilidad estimada Over (modelo Poisson simple) ──────────────────────
def _poisson_p_over(line: float, lam: float) -> float:
    """P(X > line) donde X ~ Poisson(lam). Implementación clásica.

    Usamos line.5 como umbral típico (Over 8.5 → X >= 9).
    Asumimos line es half-int (8.5, 9.5) — el caller redondea si hace falta.
    """
    if lam <= 0:
        return 0.0
    # Discrete threshold: P(X >= ceil(line))
    threshold = math.ceil(line)
    # CDF complement: 1 - P(X < threshold)
    cum = 0.0
    p = math.exp(-lam)
    cum += p
    for k in range(1, threshold):
        p *= lam / k
        cum += p
    return max(0.0, min(1.0, 1.0 - cum))


# ── Extracción de líneas Total Corners de odds_snapshots ────────────────────
def _extract_corner_lines(markets: dict) -> list[dict]:
    """De los markets, extrae las líneas de Total Corners disponibles.

    API-Sports nombra este mercado de varias formas: "Total Corners",
    "Corners", "Corners Over/Under". Devuelve lista normalizada:
      [{"line": 8.5, "over": 1.85, "under": 1.95}, ...]
    """
    candidates_keys = (
        "Total Corners", "Corners Over/Under", "Corners",
        "Total - Corners", "Corners Total", "Total tiros de esquina",
        "Tiros de esquina",
    )
    rows: list[dict] = []
    for k in candidates_keys:
        if k in markets:
            rows = markets[k] or []
            break
    if not rows:
        # Last resort: cualquier llave que contenga "corner" o "esquina"
        for k, v in markets.items():
            if "corner" in k.lower() or "esquina" in k.lower():
                rows = v or []
                break

    out: list[dict] = []
    if not isinstance(rows, list):
        return out

    for r in rows:
        if not isinstance(r, dict):
            continue
        lines = r.get("lines") or {}
        # Formato A: {"Over 8.5": 1.85, "Under 8.5": 1.95}
        if isinstance(lines, dict):
            # Agrupar por línea
            buckets: dict[float, dict] = {}
            for key, val in lines.items():
                if not isinstance(val, (int, float)) or val <= 1.01:
                    continue
                key_s = str(key).strip().lower()
                # Extract numeric line
                import re as _re
                m = _re.search(r"([0-9]+(?:\.[0-9]+)?)", key_s)
                if not m:
                    continue
                try:
                    line_num = float(m.group(1))
                except ValueError:
                    continue
                side = "over" if "over" in key_s or "más" in key_s or "mas" in key_s else \
                       "under" if "under" in key_s or "menos" in key_s else None
                if side is None:
                    continue
                b = buckets.setdefault(line_num, {"line": line_num})
                if side not in b or float(val) > b[side]:
                    b[side] = float(val)
            out.extend(buckets.values())
        # Formato B: lista de {value, odd}
        elif isinstance(lines, list):
            buckets: dict[float, dict] = {}
            for entry in lines:
                if not isinstance(entry, dict):
                    continue
                val_str = str(entry.get("value", "")).lower()
                odd = entry.get("odd")
                if not isinstance(odd, (int, float)) or odd <= 1.01:
                    continue
                import re as _re
                m = _re.search(r"([0-9]+(?:\.[0-9]+)?)", val_str)
                if not m:
                    continue
                line_num = float(m.group(1))
                side = "over" if "over" in val_str else "under" if "under" in val_str else None
                if side is None:
                    continue
                b = buckets.setdefault(line_num, {"line": line_num})
                if side not in b or float(odd) > b[side]:
                    b[side] = float(odd)
            out.extend(buckets.values())

    # Filtrar líneas con al menos un side cotizado
    out = [b for b in out if "over" in b or "under" in b]
    return sorted(out, key=lambda b: b["line"])


# ── Trap signals específicos de córners ─────────────────────────────────────
def _detect_corner_trap_signals(
    metrics: dict,
    line: float,
    side: str,
    decimal_odds: float,
    match_meta: dict,
) -> list[dict]:
    """Detecta señales trampa específicas del mercado de córners."""
    signals: list[dict] = []

    # 1) Promedio alto pero rival concede pocos córners
    if side == "over":
        proj_home = metrics.get("projectionHome") or 0
        proj_away = metrics.get("projectionAway") or 0
        if proj_home > 0 and proj_away > 0:
            imbalance = abs(proj_home - proj_away) / max(proj_home + proj_away, 1.0)
            if imbalance > 0.40:
                signals.append({
                    "code": "CORNER_IMBALANCED_PROJECTION",
                    "label": "Proyección desbalanceada entre equipos",
                    "severity": "medium",
                    "explanation": (
                        f"Un equipo proyecta {max(proj_home, proj_away):.1f} y el otro "
                        f"{min(proj_home, proj_away):.1f} córners. Si el dominio es asimétrico "
                        f"el partido puede no generar suficientes córners totales."
                    ),
                })

    # 2) Línea demasiado alta vs proyección
    proj = metrics.get("combinedCornerProjection") or 0
    if side == "over" and proj > 0 and line > proj + 2.5:
        signals.append({
            "code": "CORNER_LINE_TOO_HIGH",
            "label": "Línea muy por encima de la proyección",
            "severity": "high",
            "explanation": (
                f"La línea Over {line} está {line - proj:.1f} córners por encima de la "
                f"proyección combinada ({proj:.1f}). El mercado descuenta más volumen del "
                f"que respalda la data."
            ),
        })

    # 3) Datos incompletos
    if metrics.get("dataQuality") in ("thin", "insufficient"):
        signals.append({
            "code": "CORNER_DATA_THIN",
            "label": "Datos de córners insuficientes",
            "severity": "medium",
            "explanation": (
                f"Muestra: {metrics.get('sampleSizeHome')} (local) y "
                f"{metrics.get('sampleSizeAway')} (visitante) partidos con córners "
                f"registrados. Confianza estadística limitada."
            ),
        })

    # 4) H2H contradictorio
    h2h = metrics.get("h2hCornerAvg")
    if h2h is not None and proj > 0:
        if side == "over" and h2h < line - 2.0:
            signals.append({
                "code": "CORNER_H2H_CONTRADICTS",
                "label": "H2H corners contradice el Over",
                "severity": "medium",
                "explanation": (
                    f"H2H promedia {h2h:.1f} córners totales, pero apostamos Over {line}. "
                    f"El historial directo sugiere ritmo bajo."
                ),
            })

    # 5) Favorito que puede controlar sin atacar (señal desde match_meta)
    if match_meta.get("strong_favorite_low_pressure"):
        signals.append({
            "code": "FAVORITE_CONTROLS_TEMPO",
            "label": "Favorito puede controlar sin presión",
            "severity": "medium",
            "explanation": (
                "Cuota muy corta a un equipo: si abre el marcador, suele administrar "
                "el partido sin generar más córners."
            ),
        })

    # 6) Mercado de baja liquidez (pocos books cotizando)
    if match_meta.get("low_liquidity_corners"):
        signals.append({
            "code": "CORNER_LOW_LIQUIDITY",
            "label": "Baja liquidez en mercado de córners",
            "severity": "low",
            "explanation": "Pocos books ofrecen este mercado — la línea puede no reflejar consenso.",
        })

    return signals


# ── Entry point ─────────────────────────────────────────────────────────────
def find_corner_value(
    match: dict,
    *,
    why_direct_failed: Optional[str] = None,
) -> Optional[dict]:
    """Intenta encontrar valor en el mercado de córners para `match`.

    Pre-requisitos:
      - match["_corner_form"]["home"]["sample_size"] >= 1
      - match["_corner_form"]["away"]["sample_size"] >= 1
      - match["odds_snapshots"][-1]["markets"] contiene líneas de Total Corners

    Returns:
        None si no hay valor o no hay datos suficientes,
        o un dict con la recomendación lista para incluir en summary.rescued_picks.
    """
    # ── Sport guardrail (defense-in-depth) ──
    # Este módulo es FOOTBALL-ONLY. Si por error llega un match de otro
    # deporte, salir limpio sin generar texto contaminado.
    sport = match.get("sport") or "football"
    if sport != "football":
        log.debug("corner_market_layer skipped: sport=%s is not football", sport)
        return None

    corner_form = match.get("_corner_form") or {}
    home_form = corner_form.get("home") or {}
    away_form = corner_form.get("away") or {}

    if not home_form or not away_form:
        return None
    if (home_form.get("sample_size") or 0) < 1 and (away_form.get("sample_size") or 0) < 1:
        return None

    # Extraer líneas de córners disponibles
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None
    markets = (snaps[-1] or {}).get("markets") or {}
    corner_lines = _extract_corner_lines(markets)
    if not corner_lines:
        return None

    # Métricas del partido
    h2h_avg = corner_form.get("h2h_avg_total") or corner_form.get("h2hCornerAvg")
    league_avg = corner_form.get("league_avg_total") or DEFAULT_LEAGUE_AVG_CORNERS

    metrics = compute_corner_metrics(
        home_form, away_form,
        h2h_avg_total=h2h_avg,
        league_avg_total=league_avg,
    )

    if metrics["cornerFitScore"] < 40:
        # No vale la pena buscar valor — datos pobres o matchup neutral
        return None

    combined = metrics["combinedCornerProjection"]
    fragility = metrics["cornerFragilityScore"]

    # Match metadata para trap signals
    match_meta = {
        "strong_favorite_low_pressure": False,
        "low_liquidity_corners":        len(corner_lines) <= 1,
    }
    # Detectar favorito muy corto (puede controlar)
    odds_1x2 = markets.get("1X2") or markets.get("Moneyline") or []
    if odds_1x2:
        first = odds_1x2[0] if isinstance(odds_1x2, list) else {}
        h_odd = first.get("home")
        a_odd = first.get("away")
        if (h_odd and h_odd <= 1.35) or (a_odd and a_odd <= 1.35):
            match_meta["strong_favorite_low_pressure"] = True

    # Buscar la mejor oportunidad entre las líneas
    best_pick: Optional[dict] = None
    best_edge = float("-inf")

    for line_data in corner_lines:
        line = line_data["line"]
        # Evaluar Over
        if "over" in line_data:
            decimal_odds = line_data["over"]
            implied = 1.0 / decimal_odds
            est_prob = _poisson_p_over(line, combined)
            edge = est_prob - implied
            margin = combined - line

            # Reglas: Over requiere margen >= MIN_PROJECTION_MARGIN_OVER
            # Y edge >= -1.5% (tolerance protected)
            if margin >= MIN_PROJECTION_MARGIN_OVER and edge > best_edge:
                # Classification
                if edge >= MIN_EDGE_VALUE:
                    cls = "VALUE_BET"
                elif edge >= 0:
                    cls = "PROTECTED_ACCEPTABLE"
                elif edge >= MIN_EDGE_PROTECTED_ACC and fragility <= 50:
                    cls = "PROTECTED_ACCEPTABLE"
                else:
                    continue  # no aceptable

                if fragility > 75:
                    continue  # demasiado frágil

                trap_signals = _detect_corner_trap_signals(
                    metrics, line, "over", decimal_odds, match_meta,
                )
                # Si trap signals high >= 2 → descartar
                high_severity = sum(1 for t in trap_signals
                                    if t.get("severity") == "high")
                if high_severity >= 2:
                    continue

                best_edge = edge
                best_pick = {
                    "line":             line,
                    "side":             "over",
                    "decimal_odds":     decimal_odds,
                    "estimatedProbability": round(est_prob, 4),
                    "impliedProbability":   round(implied, 4),
                    "edge":             round(edge, 4),
                    "classification":   cls,
                    "margin_vs_line":   round(margin, 2),
                    "trap_signals":     trap_signals,
                }

        # Evaluar Under (más conservador: solo si la proyección es claramente baja)
        if "under" in line_data:
            decimal_odds = line_data["under"]
            implied = 1.0 / decimal_odds
            # P(Under N.5) = 1 - P(Over N.5)
            est_prob = 1.0 - _poisson_p_over(line, combined)
            edge = est_prob - implied
            margin = line - combined  # cuánto está la línea por encima

            # Under requiere que la proyección esté CLARAMENTE por debajo
            if margin >= MIN_PROJECTION_MARGIN_OVER and edge > best_edge and edge >= 0:
                if fragility > 65:
                    continue
                trap_signals = _detect_corner_trap_signals(
                    metrics, line, "under", decimal_odds, match_meta,
                )
                best_edge = edge
                best_pick = {
                    "line":             line,
                    "side":             "under",
                    "decimal_odds":     decimal_odds,
                    "estimatedProbability": round(est_prob, 4),
                    "impliedProbability":   round(implied, 4),
                    "edge":             round(edge, 4),
                    "classification":   "VALUE_BET" if edge >= MIN_EDGE_VALUE else "PROTECTED_ACCEPTABLE",
                    "margin_vs_line":   round(margin, 2),
                    "trap_signals":     trap_signals,
                }

    if not best_pick:
        return None

    # ── Build human-readable reasons ────────────────────────────────────
    side_label = "Over" if best_pick["side"] == "over" else "Under"
    market_label = f"Total Corners {side_label}"
    selection_label = f"Más de {best_pick['line']} córners" if best_pick["side"] == "over" \
        else f"Menos de {best_pick['line']} córners"

    reasons: list[str] = []
    avg_home_for = metrics.get("cornerForAvgHomeLast5") or 0
    avg_away_for = metrics.get("cornerForAvgAwayLast5") or 0
    if avg_home_for and avg_away_for:
        reasons.append(
            f"Promedio últimos {min(metrics['sampleSizeHome'], 5)} partidos: "
            f"local {avg_home_for:.1f} córners, visitante {avg_away_for:.1f} córners."
        )
    reasons.append(
        f"Proyección combinada del motor: {combined:.1f} córners "
        f"({'+' if combined > best_pick['line'] else ''}{combined - best_pick['line']:.1f} vs línea {best_pick['line']})."
    )
    if h2h_avg:
        reasons.append(f"H2H promedio: {h2h_avg:.1f} córners totales.")
    if metrics["cornerPressureScore"] >= 60:
        reasons.append("Ambos equipos tienen perfil ofensivo elevado.")
    reasons.append("Mercado directo sin valor — córners ofrece mejor lectura del partido.")

    # Risks
    risks: list[str] = []
    if best_pick["side"] == "over":
        risks.append("Si un equipo anota temprano puede bajar el ritmo del partido.")
    risks.append("El mercado de córners suele ser más volátil que el de goles.")
    if metrics["sampleSizeHome"] < 5 or metrics["sampleSizeAway"] < 5:
        risks.append("Muestra de últimos partidos limitada.")

    why_safer = (
        f"Los córners son un proxy del volumen ofensivo, no del marcador final. "
        f"Cuando dos equipos generan córners de forma consistente "
        f"({avg_home_for:.1f} + {avg_away_for:.1f} en sus últimos partidos), la línea "
        f"Over {best_pick['line']} se vuelve estadísticamente más alcanzable que un "
        f"ganador específico."
    ) if avg_home_for and avg_away_for else (
        "Mercado alternativo basado en volumen de córners — independiente del resultado."
    )

    return {
        "rescued":         True,
        "rescueType":      "CORNER_MARKET",
        "routed_to":       "rescued_picks",
        "market":          market_label,
        "selection":       selection_label,
        "decimal_odds":    best_pick["decimal_odds"],
        "edge":            best_pick["edge"],
        "estimatedProbability": best_pick["estimatedProbability"],
        "impliedProbability":   best_pick["impliedProbability"],
        "classification":  best_pick["classification"],
        "confidence":      max(55, min(85, metrics["cornerFitScore"])),
        "cornerMarketFitScore":  metrics["cornerFitScore"],
        "cornerFragilityScore":  metrics["cornerFragilityScore"],
        "cornerPressureScore":   metrics["cornerPressureScore"],
        "metrics":               metrics,
        "trap_signals_structured": best_pick["trap_signals"],
        "reasons":               reasons,
        "risks":                 risks,
        "whyDirectMarketsFailed": (
            why_direct_failed
            or "Mercados directos (1X2 / Doble Op / Under goles) sin edge real."
        ),
        "whyThisMarketIsSafer":  why_safer,
        "reason": (
            f"Mercado de córners: proyección {combined:.1f} vs línea {best_pick['line']}, "
            f"edge {best_pick['edge']*100:+.1f}%, fit {metrics['cornerFitScore']}/100, "
            f"fragility {metrics['cornerFragilityScore']}/100."
        ),
        "market_category":  mt.CATEGORY_PROTECTED,
        "tolerance_used":   mt.CATEGORY_PROTECTED,
        "fragility_score":  metrics["cornerFragilityScore"],
        "_source":          "corner_market_layer_v1",
    }


__all__ = [
    "compute_corner_metrics",
    "find_corner_value",
    "MIN_PROJECTION_MARGIN_OVER",
]
