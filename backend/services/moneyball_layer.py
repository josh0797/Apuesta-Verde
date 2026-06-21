"""Moneyball Betting Layer — Universal Value Engine.

Sits on top of every sport-specific engine. Receives the LLM pick + the
sport-specific structural signals (motivation, mlb_matchup, etc.) and decides
whether the price the market is paying is actually worth taking.

Pipeline (universal):
    Sport Engine     → estimated_probability (from confidence × per-sport calibration)
    Market Odds      → implied_probability  = 1 / decimal_odds
    Market Tolerance → category (aggressive | balanced | protected)
    Moneyball Layer  → edge, EV, ROI, fragility, overreaction, traps,
                       undervalued signals, final classification

Classification space (11 final verdicts):
    STRONG_VALUE_BET     — edge ≥ 5% AND high confidence AND low fragility
    VALUE_BET            — edge ≥ threshold (3% simple / 5% live / 7% parlay)
    UNDERVALUED_EDGE     — value + ≥1 undervalued signal (alternative line, prop, etc.)
    LIVE_VALUE_WINDOW    — is_live AND edge ≥ 5% AND volatile state
    FRAGILE_EDGE         — value present but fragility_score > 65 → reduce stake
    WAIT_FOR_BETTER_LINE — edge slightly below threshold AND line movement favourable
    PROTECTED_ACCEPTABLE — edge slightly negative on protected market with low fragility (NEW)
    WATCHLIST            — edge marginal/negative but market reads OK; monitor only (NEW)
    PUBLIC_OVERREACTION  — public_overreaction_index ≥ 70 AND no strong undervalued
    MARKET_TRAP          — ≥3 structured trap signals (or ≥2 of high severity)
    NO_BET_VALUE         — edge < threshold and no other redeeming signal

Subsumes the prior `services.market_guardrail`:
    • implied_probability  → exposed as `_market_edge.implied_probability`
    • estimated_probability (calibrated) → `_market_edge.estimated_probability`
    • edge / edge_threshold / verdict   → `_market_edge.*`  (back-compat for UI/CSV)
    • full Moneyball verdict + factors  → `_moneyball.*`   (new)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from . import market_tolerance as mt

log = logging.getLogger("moneyball")


# ────────────────────────────────────────────────────────────────────────────
# Calibration (same constants the previous market_guardrail used — kept here
# so we can deprecate that module).
# ────────────────────────────────────────────────────────────────────────────
EDGE_THRESHOLDS = {
    "simple": 0.03,
    "live":   0.05,
    "parlay": 0.07,
}

DEFAULT_CALIBRATION = {
    "football":   float(os.environ.get("LLM_CONFIDENCE_CALIBRATION_FOOTBALL",   "0.85")),
    "basketball": float(os.environ.get("LLM_CONFIDENCE_CALIBRATION_BASKETBALL", "0.82")),
    "baseball":   float(os.environ.get("LLM_CONFIDENCE_CALIBRATION_BASEBALL",   "0.78")),
}


# ── Probability + EV math ───────────────────────────────────────────────────
def implied_probability(decimal_odds: Optional[float]) -> Optional[float]:
    try:
        o = float(decimal_odds)
    except (TypeError, ValueError):
        return None
    if o <= 1.0:
        return None
    return round(1.0 / o, 4)


def parse_midpoint_odds(odds_range: Optional[str]) -> Optional[float]:
    if not odds_range:
        return None
    nums = re.findall(r"\d+\.?\d*", str(odds_range))
    parsed: list[float] = []
    for n in nums:
        try:
            v = float(n)
            if 1.01 <= v <= 30.0:
                parsed.append(v)
        except ValueError:
            continue
    if not parsed:
        return None
    if len(parsed) >= 2:
        return round((parsed[0] + parsed[1]) / 2.0, 3)
    return parsed[0]


def estimated_probability_from_confidence(
    confidence_score: Optional[int],
    sport: str = "football",
) -> Optional[float]:
    """Apply per-sport calibration to counteract LLM over-confidence."""
    try:
        c = float(confidence_score)
    except (TypeError, ValueError):
        return None
    if c < 0 or c > 100:
        return None
    cal = DEFAULT_CALIBRATION.get(sport, 0.85)
    return round((c / 100.0) * cal, 4)


def compute_expected_value(
    estimated_probability: float,
    decimal_odds: float,
    stake: float = 10.0,
) -> dict:
    """EV = (p × net_profit_if_win) − ((1−p) × stake)."""
    net_if_win = stake * (decimal_odds - 1.0)
    ev = (estimated_probability * net_if_win) - ((1 - estimated_probability) * stake)
    roi_pct = (ev / stake) * 100 if stake > 0 else 0.0
    return {
        "stake": round(stake, 2),
        "net_profit_if_win": round(net_if_win, 2),
        "expected_value": round(ev, 3),
        "roi_projection_pct": round(roi_pct, 2),
    }


def detect_bet_type(pick: dict) -> str:
    if pick.get("is_parlay") is True:
        return "parlay"
    if pick.get("is_live") is True:
        return "live"
    return "simple"


# ────────────────────────────────────────────────────────────────────────────
# Fragility Score (0–100)
# ────────────────────────────────────────────────────────────────────────────
# Higher = more dependent on multiple things going right.

def compute_fragility_score(pick: dict, sport: str) -> dict:
    score = 0
    factors: list[str] = []

    rec = pick.get("recommendation") or {}
    market = (rec.get("market") or "").lower()
    selection = (rec.get("selection") or "").lower()
    odds_used = parse_midpoint_odds(rec.get("odds_range")) or 0
    risks = pick.get("risks") or []

    # 1) Very short odds — implies the market sees almost no risk; tiny EV cushion
    if 0 < odds_used < 1.20:
        score += 25
        factors.append("Cuota muy baja (<1.20) deja margen de EV mínimo")
    elif 0 < odds_used < 1.40:
        score += 12
        factors.append("Cuota baja (<1.40) reduce el colchón de error")

    # 2) Specific markets compound risk
    if "exact" in market or "exacto" in market or "correct score" in market:
        score += 30; factors.append("Mercado de resultado exacto: alta varianza")
    elif "scorer" in market or "goleador" in market or "anotador" in market:
        score += 15; factors.append("Mercado de anotador: depende de evento puntual")
    elif "both teams" in market or "ambos" in market:
        score += 8

    # 3) Parlay legs — inherently fragile, probabilities compound brutally
    legs = pick.get("parlay_legs")
    if isinstance(legs, list) and len(legs) >= 2:
        # 2 legs → 45 (moderate), 3 legs → 70 (alta), 4+ → 85+ (extrema)
        leg_penalty = min(85, 25 + 22 * (len(legs) - 1))
        score += leg_penalty
        factors.append(f"Parlay de {len(legs)} piernas: probabilidad compuesta brutal")

    # 4) Per-sport volatility
    if sport == "baseball":
        # MLB: bullpen + variance per game
        ctx = pick.get("mlb_context") or {}
        hbp = (ctx.get("home_bullpen") or {}).get("fatigue_score_0_100")
        abp = (ctx.get("away_bullpen") or {}).get("fatigue_score_0_100")
        max_fatigue = max(filter(None, [hbp, abp]), default=0) or 0
        if max_fatigue > 70:
            score += 20; factors.append("Bullpen muy cansado: riesgo de explosión tardía")
        elif max_fatigue > 50:
            score += 10
        # Run Line bets are inherently more fragile
        if "run line" in market or "spread" in market:
            score += 10; factors.append("Run Line / spread: depende de margen exacto")

    if sport == "basketball":
        # NBA: back-to-back, rest, pace
        if "back-to-back" in str(pick.get("reasoning") or "").lower():
            score += 15; factors.append("Equipo en back-to-back: fatiga elevada")
        if "spread" in market:
            score += 6

    if sport == "football":
        # Cards / sendings-off swing fútbol unpredictably
        if "tarjet" in str(pick.get("risks") or "").lower() or "card" in str(risks).lower():
            score += 8; factors.append("Riesgo de tarjeta/expulsión señalado")
        if "exactly" in market or "ht/ft" in market or "media tiempo" in market:
            score += 15

    # 5) Live game state — markets move fast
    if pick.get("is_live"):
        score += 12; factors.append("Apuesta live: condiciones inestables")

    # 6) Stack of risks from the analyst itself
    if len(risks) >= 4:
        score += 15; factors.append(f"Múltiples banderas de riesgo señaladas ({len(risks)})")
    elif len(risks) >= 2:
        score += 6

    # 7) Stale data freshness — odds may already have moved
    fresh = pick.get("data_freshness") or {}
    if fresh.get("odds") == "stale" or fresh.get("context") == "stale":
        score += 10; factors.append("Datos no completamente frescos")

    score = max(0, min(100, score))
    label = "baja" if score <= 30 else "moderada" if score <= 60 else "alta" if score <= 80 else "extrema"
    return {
        "score": score,
        "label": label,
        "factors": factors,
    }


# ────────────────────────────────────────────────────────────────────────────
# Public Overreaction Index (0–100)
# ────────────────────────────────────────────────────────────────────────────
# Detects narrative phrases in the LLM reasoning / motivation context.

# Spanish + English narrative tokens, each weighted by how strongly they
# typically correlate with hype rather than EV.
NARRATIVE_WEIGHTS: list[tuple[str, int]] = [
    # Spanish high-signal
    (r"necesitan? ganar",                        15),
    (r"no puede(n)? perder",                     18),
    (r"ya remontar(on|on?)",                     15),
    (r"ya clasificad",                           10),
    (r"clasificad[oa]s? sin presi[oó]n",         12),
    (r"jugar[áa]n? relajad",                     12),
    (r"vienen de golear",                        15),
    (r"siempre aparece",                         10),
    (r"favorito (claro|absoluto)",               12),
    (r"equipo grande",                           10),
    (r"jugador estrella",                        10),
    (r"forma estelar",                            8),
    (r"momento ideal",                            8),
    (r"presi[óo]n m[áa]xima",                     8),
    (r"está[n]? obligados?",                     12),
    # English equivalents (in case LLM ever switches)
    (r"need(s)? to win",                         15),
    (r"can't lose",                              18),
    (r"just (came back|comeback)",               15),
    (r"already qualified",                       10),
    (r"big club|big team",                        8),
    (r"star player always",                      10),
]


def compute_public_overreaction_index(pick: dict) -> dict:
    score = 0
    matched: list[str] = []

    texts: list[str] = []
    texts.append(str(pick.get("reasoning") or ""))
    texts.append(str((pick.get("motivation_state") or "")))
    texts.append(str((pick.get("pressure_state") or "")))
    mot = pick.get("motivation") or {}
    for side in ("home", "away"):
        s = (mot.get(side) or {}).get("reason") or ""
        texts.append(str(s))
    haystack = " | ".join(t for t in texts if t).lower()

    if not haystack:
        return {"score": 0, "matched": [], "label": "ninguna"}

    for pattern, weight in NARRATIVE_WEIGHTS:
        if re.search(pattern, haystack, flags=re.IGNORECASE):
            score += weight
            matched.append(pattern)

    score = min(100, score)
    label = "ninguna" if score < 20 else "leve" if score < 45 else "moderada" if score < 70 else "alta"
    return {"score": score, "matched": matched, "label": label}


# ────────────────────────────────────────────────────────────────────────────
# Market Trap detection — Structured (códigos canónicos + severidad)
# ────────────────────────────────────────────────────────────────────────────
# Cada señal devuelta tiene la forma:
#   {
#     "code":        "FAVORITE_NAME_BIAS",
#     "label":       "Favorito sobrevalorado por nombre",
#     "severity":    "low" | "medium" | "high",
#     "explanation": "El mercado paga menos por la reputación...",
#   }
#
# Esto reemplaza la lista plana de strings (`detect_market_traps` legacy)
# que la UI mostraba como "Edge negativo + N señales de trampa".
# La función legacy se mantiene como wrapper para back-compat.

TRAP_CATALOG: dict[str, dict] = {
    "FAVORITE_NAME_BIAS": {
        "label":       "Favorito sobrevalorado por nombre",
        "severity":    "high",
        "explanation": "El mercado paga menos por la reputación del equipo que por dominio estadístico real.",
    },
    "LOW_ODDS_NO_VALUE": {
        "label":       "Cuota baja sin valor real",
        "severity":    "high",
        "explanation": "Cuota corta sin colchón de EV: el mercado ya descuenta toda la probabilidad y no queda margen.",
    },
    "SCOREBOARD_TRAP": {
        "label":       "Marcador engañoso",
        "severity":    "medium",
        "explanation": "El marcador no refleja el balance de juego — el equipo con ventaja puede estar siendo dominado.",
    },
    "NO_STATISTICAL_DOMINANCE": {
        "label":       "Sin dominio estadístico real",
        "severity":    "high",
        "explanation": "El pick depende de la confianza del modelo, no de un dominio estadístico claro en xG/posesión/tiros.",
    },
    "PUBLIC_NARRATIVE_OVERREACTION": {
        "label":       "Sobre-reacción de narrativa pública",
        "severity":    "medium",
        "explanation": "El reasoning apoya el pick en frases hechas ('necesitan ganar', 'equipo grande') sin métricas que las respalden.",
    },
    "MOTIVATION_OVERPRICED": {
        "label":       "Motivación ya descontada por el mercado",
        "severity":    "medium",
        "explanation": "La presión / motivación está reflejada en la cuota; el upside ya se pagó hace días.",
    },
    "H2H_MISLEADING": {
        "label":       "Histórico H2H engañoso",
        "severity":    "low",
        "explanation": "El H2H reciente sesga al pick, pero las condiciones de los equipos cambiaron desde entonces.",
    },
    "LINE_MOVEMENT_AGAINST_PICK": {
        "label":       "Línea se mueve en contra",
        "severity":    "high",
        "explanation": "La cuota se ha alargado/movido contra el pick: el dinero inteligente está en el otro lado.",
    },
    "LOW_LIQUIDITY_MARKET": {
        "label":       "Mercado con baja liquidez",
        "severity":    "low",
        "explanation": "Pocos books cotizan este mercado — la línea puede no reflejar consenso.",
    },
    "LIVE_MOMENTUM_OPPOSITE": {
        "label":       "Momentum live contrario",
        "severity":    "high",
        "explanation": "El partido live tiene momentum claramente en contra del pick — riesgo de cambio de marcador.",
    },
    "RED_CARD_CONTEXT": {
        "label":       "Contexto de tarjeta roja",
        "severity":    "high",
        "explanation": "Hay inferioridad numérica que altera la dinámica esperada del partido.",
    },
    "LATE_GAME_VOLATILITY": {
        "label":       "Volatilidad de fin de partido",
        "severity":    "medium",
        "explanation": "El partido entra en fase volátil (último cuarto / final / extra) donde un evento decide todo.",
    },
    "WEAK_DEFENSIVE_PROFILE": {
        "label":       "Perfil defensivo débil",
        "severity":    "medium",
        "explanation": "Las defensas no soportan el pick: equipos concedieron muchos goles/runs/puntos recientes.",
    },
    "OVERDEPENDENT_ON_ONE_EVENT": {
        "label":       "Depende de un solo evento",
        "severity":    "medium",
        "explanation": "El pick necesita un evento puntual (gol exacto, scorer, primer corner) para resolverse positivamente.",
    },
    "CONFIDENCE_ALREADY_PRICED": {
        "label":       "Confianza ya descontada",
        "severity":    "medium",
        "explanation": "Confianza alta pero la implied probability ya cubre el escenario — no hay margen de edge.",
    },
    "CASH_OUT_LOW": {
        "label":       "Cash-out muy bajo",
        "severity":    "low",
        "explanation": "El cash-out ofrecido es bajo, indicando que el mercado descuenta probabilidad real baja.",
    },
}


def _make_trap(code: str, *, extra_explanation: str = "") -> dict:
    """Construye un trap signal estructurado a partir del catálogo."""
    base = TRAP_CATALOG.get(code, {})
    return {
        "code":        code,
        "label":       base.get("label", code),
        "severity":    base.get("severity", "medium"),
        "explanation": (base.get("explanation", "") + (" " + extra_explanation if extra_explanation else "")).strip(),
    }


def detect_trap_signals_structured(
    pick: dict,
    sport: str,
    edge: Optional[float],
    implied: Optional[float],
    overreaction_score: int,
    *,
    market_category: Optional[str] = None,
) -> list[dict]:
    """Devuelve lista de señales trampa estructuradas (códigos canónicos)."""
    signals: list[dict] = []
    rec = pick.get("recommendation") or {}
    market = (rec.get("market") or "").lower()
    conf = rec.get("confidence_score") or 0
    odds_used = parse_midpoint_odds(rec.get("odds_range")) or 0
    risks = pick.get("risks") or []

    # 1) Favorito popular a cuota corta + alta confianza + sin edge real
    if 0 < odds_used <= 1.35 and conf >= 75 and (edge is None or edge < 0.02):
        signals.append(_make_trap("FAVORITE_NAME_BIAS",
                                  extra_explanation=f"Cuota {odds_used:.2f} con confianza {conf}."))

    # 2) Cuota baja sin valor real
    if 0 < odds_used < 1.25 and (edge is None or edge < 0.01):
        signals.append(_make_trap("LOW_ODDS_NO_VALUE",
                                  extra_explanation=f"Cuota {odds_used:.2f}."))

    # 3) Doble Op / DNB a cuota mínima (caso especial — riesgo sin retorno)
    if sport == "football" and ("doble" in market or "draw no bet" in market or "dnb" in market) \
            and 0 < odds_used <= 1.25:
        signals.append(_make_trap("LOW_ODDS_NO_VALUE",
                                  extra_explanation="Doble Op/DNB a cuota mínima sin colchón."))

    # 4) Sobre-reacción narrativa pública
    if overreaction_score >= 45:
        signals.append(_make_trap("PUBLIC_NARRATIVE_OVERREACTION",
                                  extra_explanation=f"Índice de narrativa: {overreaction_score}/100."))

    # 5) Confianza alta ya descontada por el mercado
    if conf >= 80 and implied is not None and implied >= 0.70 and (edge is None or edge < 0.02):
        signals.append(_make_trap("CONFIDENCE_ALREADY_PRICED",
                                  extra_explanation=f"Implied {implied*100:.0f}% ≈ confianza {conf}."))

    # 6) Línea se mueve en contra
    line_mv = (pick.get("key_data") or {}).get("line_movement")
    if isinstance(line_mv, dict):
        direction = str(line_mv.get("direction", "")).lower()
        if direction in ("drifting", "lengthening", "out", "drift"):
            signals.append(_make_trap("LINE_MOVEMENT_AGAINST_PICK"))

    # 7) Cash-out bajo en live
    cash_out = (pick.get("cash_out") or "").lower()
    if cash_out and "bajo" in cash_out and edge is not None and edge < 0.03:
        signals.append(_make_trap("CASH_OUT_LOW"))

    # 8) Mercado depende de un solo evento (scorer / exact / first goal)
    if any(tok in market for tok in (
        "scorer", "anotador", "goleador", "exact", "exacto",
        "primer gol", "first goal", "primer corner",
    )):
        signals.append(_make_trap("OVERDEPENDENT_ON_ONE_EVENT"))

    # 9) Riesgo de tarjeta roja señalado en risks o en live_stats
    risk_blob = " ".join(str(r).lower() for r in risks) if risks else ""
    if "tarjet" in risk_blob or "red card" in risk_blob or "expuls" in risk_blob:
        signals.append(_make_trap("RED_CARD_CONTEXT"))

    # 10) Live momentum opposite (señal explícita en live narrative)
    live_state = (pick.get("live_state") or "")
    if pick.get("is_live") and isinstance(live_state, str) and live_state.lower() in (
        "momentum_against", "scoreboard_against", "trap_late_lead",
    ):
        signals.append(_make_trap("LIVE_MOMENTUM_OPPOSITE"))

    # 11) Volatilidad de fin de partido
    if pick.get("is_live"):
        minute = (pick.get("live_stats") or {}).get("minute")
        try:
            m = int(minute) if minute is not None else None
        except (TypeError, ValueError):
            m = None
        if m is not None and m >= 80:
            signals.append(_make_trap("LATE_GAME_VOLATILITY"))

    # 12) Perfil defensivo débil — señal proveniente del LLM context
    key_data = pick.get("key_data") or {}
    weak_def = key_data.get("weak_defensive_profile") or key_data.get("defensive_weakness")
    if weak_def:
        signals.append(_make_trap("WEAK_DEFENSIVE_PROFILE"))

    # 13) Sin dominio estadístico — flag explícito o muchos risks
    if (key_data.get("no_statistical_dominance") is True
            or (isinstance(risks, list) and len(risks) >= 4)):
        signals.append(_make_trap("NO_STATISTICAL_DOMINANCE"))

    # 14) Liquidez baja
    if (key_data.get("low_liquidity") is True
            or (pick.get("_market_meta") or {}).get("bookmaker_count", 99) < 3):
        signals.append(_make_trap("LOW_LIQUIDITY_MARKET"))

    # 15) Motivation already priced — señal del stage_detector
    pressure = (pick.get("pressure_state") or "").lower()
    if pressure in ("priced_in", "already_priced", "overpriced"):
        signals.append(_make_trap("MOTIVATION_OVERPRICED"))

    # Dedup por código
    seen = set()
    deduped: list[dict] = []
    for s in signals:
        if s["code"] not in seen:
            deduped.append(s)
            seen.add(s["code"])
    return deduped


def detect_market_traps(
    pick: dict,
    sport: str,
    edge: Optional[float],
    implied: Optional[float],
    overreaction_score: int,
) -> list[str]:
    """Wrapper legacy: devuelve lista de strings (back-compat).

    Mantiene la firma original que esperaban learning_cases.py y otros
    callers para no romper la API existente. Internamente usa la nueva
    detección estructurada y aplana a labels.
    """
    structured = detect_trap_signals_structured(
        pick, sport, edge, implied, overreaction_score,
    )
    return [s["label"] for s in structured]


# ────────────────────────────────────────────────────────────────────────────
# Undervalued edge signals
# ────────────────────────────────────────────────────────────────────────────
def detect_undervalued_edges(pick: dict, sport: str, edge: Optional[float], matchup_signal: dict) -> list[str]:
    signals: list[str] = []
    rec = pick.get("recommendation") or {}
    market = (rec.get("market") or "").lower()
    odds_used = parse_midpoint_odds(rec.get("odds_range")) or 0

    if edge is None or edge < 0.03:
        return signals

    # 1) Underdog with edge (odds ≥ 2.00)
    if odds_used >= 2.00 and edge >= 0.05:
        signals.append("Underdog con respaldo estadístico")

    # 2) Alternative line / spread variant
    if "spread" in market or "run line" in market or "hándicap" in market or "handicap" in market or "alt" in market:
        signals.append("Línea alternativa más eficiente que mercado principal")

    # 3) Player prop with metric support
    if "prop" in market or "anotador" in market or "scorer" in market or "hits" in market or "puntos jugador" in market:
        signals.append("Player prop respaldado por métricas")

    # 4) MLB: pitcher matchup decisively in our favour
    if sport == "baseball" and matchup_signal:
        edge_side = matchup_signal.get("structural_edge_side")
        strength = matchup_signal.get("structural_edge_strength") or 0
        if edge_side and edge_side != "even" and strength >= 0.4:
            signals.append(f"Edge estructural MLB ({edge_side}, {int(strength*100)}%)")

    # 5) Football: protected market chosen over direct result
    if sport == "football" and ("doble" in market or "draw no bet" in market) and edge >= 0.05:
        signals.append("Mercado protegido con edge real")

    # 6) Live bet where score looks bad but stats remain strong
    if pick.get("is_live") and edge >= 0.05:
        signals.append("Live: marcador engañoso vs métricas reales")

    return signals


# ────────────────────────────────────────────────────────────────────────────
# Final classification (the 11-state space)
# ────────────────────────────────────────────────────────────────────────────
def _high_severity_count(structured_traps: list[dict]) -> int:
    """Cuenta señales trampa de severidad 'high'."""
    return sum(1 for t in structured_traps if (t.get("severity") or "").lower() == "high")


def classify_pick(
    *,
    edge: Optional[float],
    threshold: float,
    bet_type: str,
    confidence: int,
    fragility: int,
    overreaction: int,
    trap_signals: list[str],
    undervalued_signals: list[str],
    line_movement_favourable: bool,
    market_category: str = mt.CATEGORY_UNKNOWN,
    structured_traps: Optional[list[dict]] = None,
    market: Optional[str] = None,
    selection: Optional[str] = None,
    market_identity: Optional[dict] = None,
) -> dict:
    """Decisión contextual por categoría de mercado.

    Nuevos verdicts (vs versión previa):
      - PROTECTED_ACCEPTABLE: edge ligeramente negativo en mercado protegido
        con baja fragilidad, alta confianza y ≤1 trap signal.
      - WATCHLIST: edge marginal/negativo pero la lectura del partido es
        coherente; no se apuesta, se monitorea.
      - MARKET_IDENTITY_MISSING (Phase F73/F74): cuando ``market_identity``
        es UNKNOWN/inválida, **bloqueamos** todo cálculo derivado
        (MARKET_TRAP, PROTECTED_BELOW_FLOOR, EDGE_INSUFFICIENT, etc.) y
        ruteamos a ``REQUIRES_MARKET_IDENTIFICATION``.

    Phase F74 — Floors granulares
    ------------------------------
    Cuando el mercado es PROTECTED, los floors de edge negativo y
    watchlist se resuelven granularmente vía
    ``market_tolerance.resolve_edge_floors``:
      - DOUBLE_CHANCE → -4%
      - DNB           → -3%
      - Under 3.5/4.5 → -3%
      - Over 1.5      → -2%
      - Resto         → -1.5% (default)
    """
    structured_traps = structured_traps or []

    # ── Phase F73/F74 — Market identity guard ───────────────────────────
    # Si el caller pasa una market_identity explícita y es UNKNOWN,
    # NO podemos calcular edge ni etiquetar trampas. Ruteamos a
    # REQUIRES_MARKET_IDENTIFICATION.
    if market_identity is not None:
        try:
            from . import market_identity_guards as _mig
            if not _mig.has_valid_market_identity(market_identity):
                return {
                    "classification": "MARKET_IDENTITY_MISSING",
                    "reason": (
                        "Identidad de mercado desconocida: no se puede "
                        "calcular edge ni clasificar trampa. Se requiere "
                        "identificación manual del mercado."
                    ),
                    "tolerance_used": market_category,
                    "state":          "REQUIRES_MARKET_IDENTIFICATION",
                    "reason_codes":   [
                        "MARKET_IDENTITY_MISSING",
                        "EDGE_CALCULATION_BLOCKED_UNKNOWN_MARKET",
                    ],
                }
        except Exception:  # noqa: BLE001
            # Fail-soft: si el guard no puede cargarse, seguimos el flujo
            # normal sin bloquear (mantiene compatibilidad).
            pass

    if edge is None:
        return {"classification": "NO_BET_VALUE",
                "reason": "Datos insuficientes para validar valor.",
                "tolerance_used": market_category}

    # Parámetros de tolerancia para esta categoría de mercado
    params = mt.tolerance_params(market_category)
    # Phase F74 — resolución granular de floors (solo activa si protected
    # y hay info de market/selection/market_identity).
    floors = mt.resolve_edge_floors(
        market_category,
        market=market, selection=selection,
        market_identity=market_identity,
    )
    n_traps_total = len(trap_signals)
    n_traps_high  = _high_severity_count(structured_traps)

    # ── Hard rejections universales ─────────────────────────────────────
    # Trampa fuerte: ≥3 señales OR ≥2 high-severity
    if n_traps_total >= 3 or n_traps_high >= 2:
        # Excepción: mercado protegido con muy baja fragilidad puede
        # sobrevivir 2 traps si edge no es demasiado negativo.
        if (mt.is_protected(market_category)
                and fragility < 30
                and (edge or 0) >= floors["negative_edge_floor"]
                and n_traps_high <= 1):
            pass  # caer al flujo de protegido
        else:
            return {"classification": "MARKET_TRAP",
                    "reason": f"Señales de trampa significativas: {n_traps_total} totales, "
                              f"{n_traps_high} de severidad alta.",
                    "tolerance_used": market_category}

    # ── Branch por categoría de mercado ─────────────────────────────────
    if mt.is_aggressive(market_category):
        # AGGRESSIVE: cero tolerancia a edge negativo
        if edge < 0:
            return {"classification": "NO_BET_VALUE",
                    "reason": f"Mercado agresivo con edge {edge*100:+.1f}%: "
                              f"no se acepta edge negativo en este tipo de mercado.",
                    "tolerance_used": market_category}
        if edge < params["min_edge"]:
            if line_movement_favourable and edge >= params["min_edge"] - 0.015:
                return {"classification": "WAIT_FOR_BETTER_LINE",
                        "reason": f"Edge {edge*100:.1f}% bajo el umbral agresivo "
                                  f"{params['min_edge']*100:.0f}%, pero línea favorable. "
                                  f"Esperar mejor precio.",
                        "tolerance_used": market_category}
            return {"classification": "NO_BET_VALUE",
                    "reason": f"Mercado agresivo requiere edge ≥{params['min_edge']*100:.0f}%; "
                              f"actual {edge*100:+.1f}%.",
                    "tolerance_used": market_category}
        # edge >= min_edge en agresivo → sigue al flujo final de "positive edge"

    elif mt.is_protected(market_category):
        # PROTECTED: el flujo más flexible (este es el core de la spec)
        # Phase F74 — floors granulares por familia/línea de mercado.
        floor    = floors["negative_edge_floor"]    # DC -4%, DNB -3%, U3.5 -3%, O1.5 -2%, default -1.5%
        wl_floor = floors["watchlist_floor"]        # un escalón por debajo del floor
        sub_label = floors.get("protected_subfamily")
        ok_frag  = fragility <= params["max_fragility_acceptable"]
        ok_conf  = confidence >= params["min_confidence"]
        ok_traps = n_traps_total <= params["max_trap_signals"]

        if edge >= 0:
            # Edge positivo en protegido → directamente VALUE_BET (la
            # validación de fragility/traps se hace abajo en el flujo común)
            pass
        elif edge >= floor and ok_frag and ok_conf and ok_traps:
            reason_extra = f" ({sub_label})" if sub_label else ""
            return {"classification": "PROTECTED_ACCEPTABLE",
                    "reason": (
                        f"Mercado protegido{reason_extra} con edge {edge*100:+.1f}% "
                        f"(dentro de tolerancia {floor*100:+.1f}%). "
                        f"Confianza {confidence}, fragilidad {fragility}, "
                        f"{n_traps_total} señales trampa: lectura coherente del partido."
                    ),
                    "tolerance_used": market_category}
        elif edge >= wl_floor and ok_frag:
            return {"classification": "WATCHLIST",
                    "reason": (
                        f"Edge {edge*100:+.1f}% bajo tolerancia aceptable pero el mercado "
                        f"protegido sigue siendo razonable. Monitorear, no apostar."
                    ),
                    "tolerance_used": market_category}
        else:
            return {"classification": "NO_BET_VALUE",
                    "reason": (
                        f"Mercado protegido con edge {edge*100:+.1f}% bajo el piso "
                        f"de tolerancia ({wl_floor*100:.1f}%) o fragilidad/confianza "
                        f"fuera de rango."
                    ),
                    "tolerance_used": market_category}

    else:
        # BALANCED / UNKNOWN
        if edge < params["negative_edge_floor"]:
            return {"classification": "NO_BET_VALUE",
                    "reason": f"Edge {edge*100:+.1f}% < piso aceptable "
                              f"({params['negative_edge_floor']*100:.1f}%) para mercado balanceado.",
                    "tolerance_used": market_category}
        if edge < params["min_edge"]:
            # Entre piso y umbral → WATCHLIST si fragility OK
            if fragility <= params["max_fragility_acceptable"]:
                return {"classification": "WATCHLIST",
                        "reason": (
                            f"Edge {edge*100:+.1f}% en zona de monitoreo "
                            f"({params['negative_edge_floor']*100:.1f}% a "
                            f"{params['min_edge']*100:.1f}%). Sin apuesta pero seguir."
                        ),
                        "tolerance_used": market_category}
            return {"classification": "NO_BET_VALUE",
                    "reason": f"Edge {edge*100:.1f}% < umbral {params['min_edge']*100:.0f}% "
                              f"+ fragilidad {fragility} alta.",
                    "tolerance_used": market_category}
        # edge >= min_edge → sigue al flujo final

    # ── Flujo común para "edge positivo y suficiente" ───────────────────
    has_strong_undervalued = len(undervalued_signals) >= 1 and edge >= 0.04
    is_high_conf = confidence >= 75
    is_live = bet_type == "live"

    # Fragility override
    if fragility > 75:
        return {"classification": "NO_BET_VALUE",
                "reason": f"Edge {edge*100:+.1f}% real pero fragilidad muy alta ({fragility}/100). Riesgo no aceptable.",
                "tolerance_used": market_category}
    if fragility > 65:
        return {"classification": "FRAGILE_EDGE",
                "reason": f"Edge {edge*100:+.1f}% real pero fragilidad alta ({fragility}/100). Reducir stake o evitar parlay.",
                "tolerance_used": market_category}

    # Public overreaction
    if overreaction >= 70 and not has_strong_undervalued:
        return {"classification": "PUBLIC_OVERREACTION",
                "reason": f"Reasoning con narrativa pública alta ({overreaction}/100) sin métricas que la respalden.",
                "tolerance_used": market_category}

    # Confidence demasiado baja para considerar valor
    if confidence < 60 and edge < 0.05:
        return {"classification": "WATCHLIST",
                "reason": f"Edge {edge*100:+.1f}% pero confianza baja ({confidence}/100). Monitorear.",
                "tolerance_used": market_category}

    # Live value window
    if is_live and edge >= 0.05:
        return {"classification": "LIVE_VALUE_WINDOW",
                "reason": f"Live bet con edge {edge*100:+.1f}% sobre línea volátil.",
                "tolerance_used": market_category}

    # Undervalued edge
    if has_strong_undervalued and edge >= 0.04:
        return {"classification": "UNDERVALUED_EDGE",
                "reason": f"Edge {edge*100:+.1f}% en mercado infravalorado: {undervalued_signals[0]}.",
                "tolerance_used": market_category}

    # Strong value bet
    if edge >= 0.05 and is_high_conf and fragility <= 60:
        return {"classification": "STRONG_VALUE_BET",
                "reason": f"Edge fuerte {edge*100:+.1f}% con confianza alta y baja fragilidad.",
                "tolerance_used": market_category}

    # Default value bet
    return {"classification": "VALUE_BET",
            "reason": f"Edge {edge*100:+.1f}% ≥ umbral {params['min_edge']*100:.1f}%.",
            "tolerance_used": market_category}


# ────────────────────────────────────────────────────────────────────────────
# Single-pick analyzer + pipeline integration
# ────────────────────────────────────────────────────────────────────────────
def analyze_pick(pick: dict, sport: str, stake: float = 10.0) -> dict:
    """Analyze one LLM pick. Returns a `_moneyball` payload AND a `_market_edge`
    payload (for back-compat with the prior MarketEdgePanel UI)."""
    rec = pick.get("recommendation") or {}
    conf = rec.get("confidence_score") or 0
    odds_used = parse_midpoint_odds(rec.get("odds_range"))
    bet_type = detect_bet_type(pick)
    threshold = EDGE_THRESHOLDS[bet_type]

    est = estimated_probability_from_confidence(conf, sport=sport)
    imp = implied_probability(odds_used) if odds_used else None
    edge = round(est - imp, 4) if (est is not None and imp is not None) else None

    # EV / ROI (only if we have valid odds & estimate)
    ev_payload: dict = {}
    if est is not None and odds_used:
        ev_payload = compute_expected_value(est, odds_used, stake=stake)

    # Market category (tolerance model) — drives the contextual decision
    market_category = mt.classify_market_tolerance(
        rec.get("market"),
        rec.get("selection"),
        decimal_odds=odds_used,
    )

    # Fragility + overreaction + traps (structured + legacy) + undervalued
    frag = compute_fragility_score(pick, sport)
    over = compute_public_overreaction_index(pick)
    matchup_signal = pick.get("mlb_matchup") or {}
    structured_traps = detect_trap_signals_structured(
        pick, sport, edge, imp, over["score"],
        market_category=market_category,
    )
    traps = [s["label"] for s in structured_traps]  # legacy flat list
    undervalued = detect_undervalued_edges(pick, sport, edge, matchup_signal)

    # Line-movement read
    line_mv = (pick.get("key_data") or {}).get("line_movement") or {}
    direction = str((line_mv.get("direction") if isinstance(line_mv, dict) else "") or "").lower()
    line_favourable = direction in ("shortening", "in", "drift_in", "tightening", "favourable", "favorable")

    # ── Sport + market confidence floor ────────────────────────────────
    # Baseball Under: calibración 0.78 (la más baja) + alta varianza de
    # bullpen implican que conf < 75 no es suficiente para apostar.
    # Si hay edge calculable y conf está bajo el floor, degradar a WATCHLIST
    # antes de entrar al flujo de classify_pick.
    # Cuando edge=None el downstream ya retorna NO_BET_VALUE — no interferir.
    MLB_UNDER_CONF_FLOOR = int(
        os.environ.get("MLB_UNDER_CONFIDENCE_FLOOR", "75")
    )
    if (
        sport == "baseball"
        and edge is not None
        and int(conf or 0) < MLB_UNDER_CONF_FLOOR
    ):
        _market_lower = (rec.get("market") or "").lower()
        _is_under_market = (
            "under" in _market_lower
            and "team total" not in _market_lower
            and "nrfi" not in _market_lower
        )
        if _is_under_market:
            cls = {
                "classification": "WATCHLIST",
                "reason": (
                    f"MLB Under con confianza {int(conf or 0)}/100 "
                    f"< floor {MLB_UNDER_CONF_FLOOR} para baseball "
                    f"(calibración 0.78). Monitorear, no apostar hasta "
                    f"conf ≥ {MLB_UNDER_CONF_FLOOR}."
                ),
                "tolerance_used": market_category,
            }
            # Saltar classify_pick y construir el payload directamente.
            # El resto del flujo de analyze_pick sigue igual (traps, fragility, etc.)
            # porque son informativos y útiles aunque no se recomiende.
            why_can_fail: list[str] = []
            why_can_fail.extend(frag["factors"])
            why_can_fail.extend(traps)
            why_can_fail.extend(list(pick.get("risks") or [])[:3])
            seen_f: set = set()
            why_can_fail = [x for x in why_can_fail if not (x in seen_f or seen_f.add(x))]
            learning_tags = [
                cls["classification"], f"sport:{sport}", f"bet_type:{bet_type}",
                f"fragility:{frag['label']}", "mlb_under_conf_floor_triggered",
            ]
            moneyball = {
                "classification":         cls["classification"],
                "classification_reason":  cls["reason"],
                "tolerance_used":         market_category,
                "market_category":        market_category,
                "expected_value":         ev_payload.get("expected_value"),
                "roi_projection_pct":     ev_payload.get("roi_projection_pct"),
                "stake_used":             ev_payload.get("stake"),
                "net_profit_if_win":      ev_payload.get("net_profit_if_win"),
                "fragility":              frag,
                "public_overreaction":    over,
                "market_trap_signals":    traps,
                "trap_signals_structured": structured_traps,
                "undervalued_reasons":    undervalued,
                "why_this_can_fail":      why_can_fail,
                "learning_tags":          learning_tags,
                "line_movement_favourable": line_favourable,
            }
            market_edge = {
                "verdict":               "NO_BET_VALUE",
                "estimated_probability": est,
                "implied_probability":   imp,
                "edge":                  edge,
                "edge_threshold":        threshold,
                "bet_type":              bet_type,
                "odds_used":             odds_used,
                "calibration":           DEFAULT_CALIBRATION.get(sport, 0.85),
                "reason":                cls["reason"],
            }
            # Flag explícito en el pick para que el orquestador / summary
            # puedan exponer el bucket conf_floor_demoted sin re-evaluar.
            pick["_conf_floor_demoted"] = True
            return {"_market_edge": market_edge, "_moneyball": moneyball}

    cls = classify_pick(
        edge=edge, threshold=threshold, bet_type=bet_type,
        confidence=int(conf or 0),
        fragility=frag["score"], overreaction=over["score"],
        trap_signals=traps, undervalued_signals=undervalued,
        line_movement_favourable=line_favourable,
        market_category=market_category,
        structured_traps=structured_traps,
        market=rec.get("market"),
        selection=rec.get("selection"),
        market_identity=(pick.get("market_identity")
                         or rec.get("market_identity")),
    )

    # Compose "why this can fail" — frag factors + traps + risks
    why_can_fail: list[str] = []
    why_can_fail.extend(frag["factors"])
    why_can_fail.extend(traps)
    why_can_fail.extend(list(pick.get("risks") or [])[:3])
    # Dedup while keeping order
    seen = set(); why_can_fail = [x for x in why_can_fail if not (x in seen or seen.add(x))]

    learning_tags = [
        cls["classification"],
        f"sport:{sport}",
        f"bet_type:{bet_type}",
        f"fragility:{frag['label']}",
    ]
    if over["label"] != "ninguna":
        learning_tags.append(f"overreaction:{over['label']}")
    if traps:
        learning_tags.append("trap_signals")
    if undervalued:
        learning_tags.append("undervalued_signals")

    moneyball = {
        "classification": cls["classification"],
        "classification_reason": cls["reason"],
        "tolerance_used": cls.get("tolerance_used", market_category),
        "market_category": market_category,
        "expected_value": ev_payload.get("expected_value"),
        "roi_projection_pct": ev_payload.get("roi_projection_pct"),
        "stake_used": ev_payload.get("stake"),
        "net_profit_if_win": ev_payload.get("net_profit_if_win"),
        "fragility": frag,
        "public_overreaction": over,
        "market_trap_signals": traps,            # legacy strings (back-compat)
        "trap_signals_structured": structured_traps,  # NEW structured payload
        "undervalued_reasons": undervalued,
        "why_this_can_fail": why_can_fail,
        "learning_tags": learning_tags,
        "line_movement_favourable": line_favourable,
    }
    # Phase F74 — propagate state + reason_codes (used by REQUIRES_MARKET_IDENTITY bucket)
    if "state" in cls:
        moneyball["state"] = cls["state"]
    if "reason_codes" in cls:
        moneyball["reason_codes"] = cls["reason_codes"]

    # Back-compat: keep `_market_edge` populated so the legacy panel keeps
    # working until it's fully replaced. Verdict here mirrors classification.
    verdict_legacy = (
        "VALUE_FOUND" if cls["classification"] in {
            "VALUE_BET", "STRONG_VALUE_BET", "UNDERVALUED_EDGE",
            "LIVE_VALUE_WINDOW", "FRAGILE_EDGE", "PROTECTED_ACCEPTABLE",
        }
        else "NO_BET_VALUE" if cls["classification"] in {
            "NO_BET_VALUE", "MARKET_TRAP", "PUBLIC_OVERREACTION",
            "WAIT_FOR_BETTER_LINE", "WATCHLIST",
        }
        else "INSUFFICIENT_DATA"
    )
    market_edge = {
        "verdict": verdict_legacy,
        "estimated_probability": est,
        "implied_probability": imp,
        "edge": edge,
        "edge_threshold": threshold,
        "bet_type": bet_type,
        "odds_used": odds_used,
        "calibration": DEFAULT_CALIBRATION.get(sport, 0.85),
        "reason": cls["reason"],
    }
    return {"_market_edge": market_edge, "_moneyball": moneyball}


# ── Pipeline integration ────────────────────────────────────────────────────
# Classes that get REROUTED to summary.discarded_market (no real bet value):
REROUTE_CLASSIFICATIONS = {"NO_BET_VALUE", "MARKET_TRAP", "PUBLIC_OVERREACTION"}

# Classes kept in `picks` (actionable, displayed as recommendations):
KEEP_CLASSIFICATIONS = {
    "VALUE_BET", "STRONG_VALUE_BET", "UNDERVALUED_EDGE",
    "LIVE_VALUE_WINDOW", "FRAGILE_EDGE", "WAIT_FOR_BETTER_LINE",
}

# Classes routed to `protected_acceptable` bucket — recommendation kept
# but flagged as "accepted via market protection override".
PROTECTED_ACCEPTABLE_CLASSIFICATIONS = {"PROTECTED_ACCEPTABLE"}

# Classes routed to `watchlist` bucket — not actionable, monitor only.
WATCHLIST_CLASSIFICATIONS = {"WATCHLIST"}

# Phase F74 — Classes routed to `requires_market_identity` bucket: when the
# market identity is UNKNOWN/missing we MUST NOT classify as trap, discard,
# or below-floor. Instead the pick lands here so the operator can map the
# market manually. Never terminal.
REQUIRES_MARKET_IDENTITY_CLASSIFICATIONS = {"MARKET_IDENTITY_MISSING"}


def apply_moneyball_layer(parsed: dict, sport: str = "football", stake: float = 10.0) -> dict:
    """Mutates `parsed`:
        • For each pick, attaches `_market_edge` (back-compat) AND `_moneyball`.
        • Buckets picks by classification:
            - `picks` (actionable): VALUE_BET, STRONG_VALUE_BET, UNDERVALUED_EDGE,
              LIVE_VALUE_WINDOW, FRAGILE_EDGE, WAIT_FOR_BETTER_LINE
            - `summary.protected_acceptable`: PROTECTED_ACCEPTABLE (NEW)
            - `summary.watchlist`: WATCHLIST (NEW)
            - `summary.discarded_market`: NO_BET_VALUE, MARKET_TRAP, PUBLIC_OVERREACTION
        • Updates `_pipeline.moneyball` with summary counters.
    """
    if not parsed or not isinstance(parsed, dict):
        return parsed

    picks = list(parsed.get("picks") or [])
    summary = parsed.get("summary") or {}
    disc_mkt          = list(summary.get("discarded_market") or [])
    protected_accept  = list(summary.get("protected_acceptable") or [])
    watchlist         = list(summary.get("watchlist") or [])
    # Phase F74 — new bucket for picks blocked by missing market identity.
    requires_mi       = list(summary.get("requires_market_identity") or [])

    kept: list[dict] = []
    counts: dict[str, int] = {}
    rerouted = 0
    accepted_protected = 0
    watchlisted = 0
    requires_mi_count = 0

    for p in picks:
        result = analyze_pick(p, sport=sport, stake=stake)
        p["_market_edge"] = result["_market_edge"]
        p["_moneyball"]   = result["_moneyball"]
        cls = result["_moneyball"]["classification"]
        counts[cls] = counts.get(cls, 0) + 1

        # ─────────────────────────────────────────────────────────────
        # Sprint-D9-CornerAutoFallback (decisión usuario, edge ≥ 8%):
        # Si el mercado directo no ofrece edge real, intentamos promover
        # el pick al motor de córners cuando hay book_odds REALES en el
        # contexto. Solo aplica a football y solo cuando el pick está
        # en una clase NO-VALUE.
        # ─────────────────────────────────────────────────────────────
        if sport == "football":
            try:
                from . import football_corner_auto_fallback as _caf
                promoted = _caf.maybe_promote_corner_pick(p, sport=sport)
                if promoted is not None:
                    # Re-evaluar el pick promovido con moneyball para
                    # que el bucket final tenga _market_edge correcto.
                    new_result = analyze_pick(promoted, sport=sport, stake=stake)
                    promoted["_market_edge"] = new_result["_market_edge"]
                    promoted["_moneyball"]   = new_result["_moneyball"]
                    new_cls = new_result["_moneyball"]["classification"]
                    counts[new_cls] = counts.get(new_cls, 0) + 1
                    log.info(
                        "[corner_auto_fallback] promoted pick %s: %s → %s (edge %.2f%%)",
                        promoted.get("match_id"),
                        (promoted.get("_corner_auto_fallback") or {})
                            .get("promoted_from_market"),
                        (promoted.get("_corner_auto_fallback") or {})
                            .get("promoted_market"),
                        (promoted.get("_corner_auto_fallback") or {})
                            .get("edge_pct", 0.0),
                    )
                    # Reemplazar p por el pick promovido para que el
                    # resto del flujo de bucketing trabaje con él.
                    p = promoted
                    cls = new_cls
            except Exception as exc:  # noqa: BLE001 — fail-soft
                log.debug("[corner_auto_fallback] skipped (%s)", exc)

        if cls in REQUIRES_MARKET_IDENTITY_CLASSIFICATIONS:
            # Phase F74 — UNKNOWN market identity. NUNCA descartar, NUNCA
            # marcar como trampa: rutear a bucket dedicado para que la UI
            # solicite identificación manual del mercado.
            # Phase F74-post — antes de rutear, intentar **resolver** la
            # identity desde odds_snapshots o pistas en la entry. Si lo
            # logramos, re-clasificamos el pick con la nueva identity.
            resolved_mi = None
            resolution_state = None
            candidate_markets: list[dict] = []
            try:
                from . import football_market_identity_resolver as _mir
                resolution = _mir.resolve_market_identity_for_discarded_entry(
                    p, p,
                )
                resolution_state = resolution.get("state")
                if resolution_state == _mir.STATE_RESOLVED:
                    resolved_mi = resolution.get("market_identity")
                elif resolution_state == _mir.STATE_REQUIRES_MANUAL:
                    candidate_markets = resolution.get("candidate_markets") or []
            except Exception as exc:  # noqa: BLE001
                log.debug("[F74_POST_RESOLVER_FAIL] %s", exc)

            if resolved_mi:
                # Re-evaluar con la identity recién resuelta.
                p["market_identity"] = resolved_mi
                rec_block = p.get("recommendation") or {}
                rec_block["market_identity"] = resolved_mi
                p["recommendation"] = rec_block
                result = analyze_pick(p, sport=sport, stake=stake)
                p["_market_edge"] = result["_market_edge"]
                p["_moneyball"]   = result["_moneyball"]
                cls = result["_moneyball"]["classification"]
                counts[cls] = counts.get(cls, 0) + 1
                # NO continue — caer en el flujo normal de buckets abajo.
            else:
                p["_bucket"] = "requires_market_identity"
                mb_block = result.get("_moneyball") or {}
                rec_block = p.get("recommendation") or {}
                entry = {
                    "match_id":    p.get("match_id"),
                    "match_label": p.get("match_label"),
                    "market_raw":  rec_block.get("market"),
                    "selection_raw": rec_block.get("selection"),
                    "odds_range":  rec_block.get("odds_range"),
                    "reason":      mb_block.get("classification_reason"),
                    "state":       (resolution_state
                                     if resolution_state else
                                     mb_block.get("state")
                                     or "REQUIRES_MARKET_IDENTIFICATION"),
                    "reason_codes": mb_block.get("reason_codes") or [
                        "MARKET_IDENTITY_MISSING",
                        "EDGE_CALCULATION_BLOCKED_UNKNOWN_MARKET",
                    ],
                    "_moneyball_classification": cls,
                    "_market_edge":  result["_market_edge"],
                    "_moneyball":    result["_moneyball"],
                }
                if candidate_markets:
                    # Phase F74-post — bucket separado AMBIGUOUS con
                    # candidatos para que la UI permita selección manual.
                    entry["candidate_markets"] = candidate_markets
                    entry["state"] = "REQUIRES_MANUAL_MARKET_SELECTION"
                requires_mi.append(entry)
                requires_mi_count += 1
                continue

        if cls in REROUTE_CLASSIFICATIONS:
            disc_mkt.append({
                "match_id": p.get("match_id"),
                "match_label": p.get("match_label"),
                "reason": result["_moneyball"]["classification_reason"],
                "_moneyball_classification": cls,
                "_market_guardrail_reroute": True,
                "_market_edge": result["_market_edge"],
                "_moneyball": result["_moneyball"],
            })
            rerouted += 1
        elif cls in PROTECTED_ACCEPTABLE_CLASSIFICATIONS:
            # Mantener en summary.protected_acceptable Y en picks (con flag)
            p["_bucket"] = "protected_acceptable"
            protected_accept.append(p)
            kept.append(p)
            accepted_protected += 1
        elif cls in WATCHLIST_CLASSIFICATIONS:
            # Watchlist: NO ir a picks recomendados, solo al bucket de observación
            p["_bucket"] = "watchlist"
            watchlist.append(p)
            watchlisted += 1
        else:
            p["_bucket"] = "picks"
            kept.append(p)

    parsed["picks"] = kept
    summary["discarded_market"]    = disc_mkt
    summary["protected_acceptable"] = protected_accept
    summary["watchlist"]            = watchlist
    if requires_mi:
        summary["requires_market_identity"] = requires_mi
    parsed["summary"] = summary
    parsed.setdefault("_pipeline", {})
    parsed["_pipeline"]["moneyball"] = {
        "evaluated":          len(picks),
        "kept":               len(kept),
        "rerouted":           rerouted,
        "protected_accepted": accepted_protected,
        "watchlisted":        watchlisted,
        "requires_market_identity": requires_mi_count,
        "by_classification":  counts,
        "stake_used":         stake,
        "thresholds":         EDGE_THRESHOLDS,
    }
    if picks:
        log.info(
            "moneyball[%s]: %d picks → %d kept (%d protected_accept) / %d watchlist / %d rerouted / %d requires_market_identity | %s",
            sport, len(picks), len(kept), accepted_protected,
            watchlisted, rerouted, requires_mi_count, counts,
        )
    return parsed
