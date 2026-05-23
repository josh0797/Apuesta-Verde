"""Moneyball Betting Layer — Universal Value Engine.

Sits on top of every sport-specific engine. Receives the LLM pick + the
sport-specific structural signals (motivation, mlb_matchup, etc.) and decides
whether the price the market is paying is actually worth taking.

Pipeline (universal):
    Sport Engine     → estimated_probability (from confidence × per-sport calibration)
    Market Odds      → implied_probability  = 1 / decimal_odds
    Moneyball Layer  → edge, EV, ROI, fragility, overreaction, traps,
                       undervalued signals, final classification

Classification space (9 final verdicts):
    STRONG_VALUE_BET   — edge ≥ 5% AND high confidence AND low fragility
    VALUE_BET          — edge ≥ threshold (3% simple / 5% live / 7% parlay)
    UNDERVALUED_EDGE   — value + ≥1 undervalued signal (alternative line, prop, etc.)
    LIVE_VALUE_WINDOW  — is_live AND edge ≥ 5% AND volatile state
    FRAGILE_EDGE       — value present but fragility_score > 65 → reduce stake
    WAIT_FOR_BETTER_LINE — edge slightly below threshold AND line movement favourable
    PUBLIC_OVERREACTION — public_overreaction_index ≥ 70 AND no strong undervalued
    MARKET_TRAP        — ≥2 trap signals OR (negative edge + traps)
    NO_BET_VALUE       — edge < threshold and no other redeeming signal

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
# Market Trap detection
# ────────────────────────────────────────────────────────────────────────────
def detect_market_traps(pick: dict, sport: str, edge: Optional[float], implied: Optional[float], overreaction_score: int) -> list[str]:
    signals: list[str] = []
    rec = pick.get("recommendation") or {}
    market = (rec.get("market") or "").lower()
    selection = (rec.get("selection") or "").lower()
    conf = rec.get("confidence_score") or 0
    odds_used = parse_midpoint_odds(rec.get("odds_range")) or 0

    # 1) Popular favorite at very short price + high confidence
    if 0 < odds_used <= 1.35 and conf >= 75 and (edge is None or edge < 0.02):
        signals.append("Favorito popular a cuota corta sin edge real")

    # 2) Doble Op / Draw No Bet "safety" trap (football)
    if sport == "football" and ("doble" in market or "draw no bet" in market) and 0 < odds_used <= 1.25:
        signals.append("Doble Op a cuota mínima: falsa sensación de seguridad")

    # 3) Reputation / narrative-driven pick
    if overreaction_score >= 45:
        signals.append("Reasoning apoyado en narrativa pública")

    # 4) Negative edge — paying less than the model says
    if edge is not None and edge < 0:
        signals.append(f"Edge negativo: cuota paga {(implied or 0)*100:.0f}% pero modelo estima menos")

    # 5) High confidence claim but the implied probability already covers it
    if conf >= 80 and implied is not None and implied >= 0.70 and (edge is None or edge < 0.02):
        signals.append("Confianza alta pero ya descontada por el mercado")

    # 6) Line movement against our read
    line_mv = (pick.get("key_data") or {}).get("line_movement")
    if line_mv:
        direction = str(line_mv.get("direction", "")).lower() if isinstance(line_mv, dict) else ""
        if direction in ("drifting", "lengthening", "out", "drift"):
            signals.append("Línea se está alargando: el mercado pierde confianza")

    # 7) Live cash-out signal explicitly low while we're holding
    cash_out = (pick.get("cash_out") or "").lower()
    if cash_out and "bajo" in cash_out and edge is not None and edge < 0.03:
        signals.append("Cash-out muy bajo: mercado descuenta probabilidad real baja")

    return signals


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
# Final classification (the 9-state space)
# ────────────────────────────────────────────────────────────────────────────
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
) -> dict:
    if edge is None:
        return {"classification": "NO_BET_VALUE", "reason": "Datos insuficientes para validar valor."}

    # Hard rejections
    if edge < 0 and len(trap_signals) >= 2:
        return {"classification": "MARKET_TRAP",
                "reason": f"Edge negativo + {len(trap_signals)} señales de trampa."}
    if edge < 0:
        return {"classification": "NO_BET_VALUE",
                "reason": f"Edge negativo ({edge*100:+.1f}%): el mercado paga menos de lo justo."}
    if edge < threshold:
        if line_movement_favourable and edge >= threshold - 0.015:
            return {"classification": "WAIT_FOR_BETTER_LINE",
                    "reason": f"Edge {edge*100:.1f}% justo bajo el umbral {threshold*100:.0f}%, "
                              f"pero la línea se mueve a favor. Esperar mejor precio."}
        return {"classification": "NO_BET_VALUE",
                "reason": f"Edge {edge*100:.1f}% < umbral {threshold*100:.0f}%."}

    # Edge ≥ threshold. Check for overrides.
    has_strong_undervalued = len(undervalued_signals) >= 1 and edge >= 0.04
    is_high_conf = confidence >= 75
    is_live = bet_type == "live"

    # 1) Trap dominance — even with edge, ≥2 traps and no undervalued = trap
    if len(trap_signals) >= 2 and not has_strong_undervalued:
        return {"classification": "MARKET_TRAP",
                "reason": f"Pese a edge {edge*100:+.1f}%, hay {len(trap_signals)} señales de trampa de mercado."}

    # 2) Public overreaction — narrative-driven without undervalued backing
    if overreaction >= 70 and not has_strong_undervalued:
        return {"classification": "PUBLIC_OVERREACTION",
                "reason": f"Reasoning con narrativa pública alta ({overreaction}/100) sin métricas que la respalden."}

    # 3) Fragility override — value exists but the ticket is fragile
    if fragility > 65:
        return {"classification": "FRAGILE_EDGE",
                "reason": f"Edge {edge*100:+.1f}% real pero fragilidad alta ({fragility}/100). Reducir stake o evitar parlay."}

    # 4) Live value window
    if is_live and edge >= 0.05:
        return {"classification": "LIVE_VALUE_WINDOW",
                "reason": f"Live bet con edge {edge*100:+.1f}% sobre línea volátil."}

    # 5) Undervalued edge (alternative lines, props, etc.)
    if has_strong_undervalued and edge >= 0.04:
        return {"classification": "UNDERVALUED_EDGE",
                "reason": f"Edge {edge*100:+.1f}% en mercado infravalorado: {undervalued_signals[0]}."}

    # 6) Strong value bet — edge >= 5% + high confidence + low fragility
    if edge >= 0.05 and is_high_conf and fragility <= 60:
        return {"classification": "STRONG_VALUE_BET",
                "reason": f"Edge fuerte {edge*100:+.1f}% con confianza alta y baja fragilidad."}

    # 7) Default value bet
    return {"classification": "VALUE_BET",
            "reason": f"Edge {edge*100:+.1f}% ≥ umbral {threshold*100:.0f}%."}


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

    # Fragility + overreaction + traps + undervalued
    frag = compute_fragility_score(pick, sport)
    over = compute_public_overreaction_index(pick)
    matchup_signal = pick.get("mlb_matchup") or {}
    traps = detect_market_traps(pick, sport, edge, imp, over["score"])
    undervalued = detect_undervalued_edges(pick, sport, edge, matchup_signal)

    # Line-movement read
    line_mv = (pick.get("key_data") or {}).get("line_movement") or {}
    direction = str((line_mv.get("direction") if isinstance(line_mv, dict) else "") or "").lower()
    line_favourable = direction in ("shortening", "in", "drift_in", "tightening", "favourable", "favorable")

    cls = classify_pick(
        edge=edge, threshold=threshold, bet_type=bet_type,
        confidence=int(conf or 0),
        fragility=frag["score"], overreaction=over["score"],
        trap_signals=traps, undervalued_signals=undervalued,
        line_movement_favourable=line_favourable,
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
        "expected_value": ev_payload.get("expected_value"),
        "roi_projection_pct": ev_payload.get("roi_projection_pct"),
        "stake_used": ev_payload.get("stake"),
        "net_profit_if_win": ev_payload.get("net_profit_if_win"),
        "fragility": frag,
        "public_overreaction": over,
        "market_trap_signals": traps,
        "undervalued_reasons": undervalued,
        "why_this_can_fail": why_can_fail,
        "learning_tags": learning_tags,
        "line_movement_favourable": line_favourable,
    }

    # Back-compat: keep `_market_edge` populated so the legacy panel keeps
    # working until it's fully replaced. Verdict here mirrors classification.
    verdict_legacy = (
        "VALUE_FOUND" if cls["classification"] in {"VALUE_BET", "STRONG_VALUE_BET", "UNDERVALUED_EDGE", "LIVE_VALUE_WINDOW", "FRAGILE_EDGE"}
        else "NO_BET_VALUE" if cls["classification"] in {"NO_BET_VALUE", "MARKET_TRAP", "PUBLIC_OVERREACTION", "WAIT_FOR_BETTER_LINE"}
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

# Classes kept in `picks` (these are the actionable ones):
KEEP_CLASSIFICATIONS = {
    "VALUE_BET", "STRONG_VALUE_BET", "UNDERVALUED_EDGE",
    "LIVE_VALUE_WINDOW", "FRAGILE_EDGE", "WAIT_FOR_BETTER_LINE",
}


def apply_moneyball_layer(parsed: dict, sport: str = "football", stake: float = 10.0) -> dict:
    """Mutates `parsed`:
        • For each pick, attaches `_market_edge` (back-compat) AND `_moneyball`.
        • Reroutes picks classified as NO_BET_VALUE / MARKET_TRAP / PUBLIC_OVERREACTION
          to summary.discarded_market.
        • Updates `_pipeline.moneyball` with summary counters.
    """
    if not parsed or not isinstance(parsed, dict):
        return parsed

    picks = list(parsed.get("picks") or [])
    summary = parsed.get("summary") or {}
    disc_mkt = list(summary.get("discarded_market") or [])

    kept: list[dict] = []
    counts: dict[str, int] = {}
    rerouted = 0

    for p in picks:
        result = analyze_pick(p, sport=sport, stake=stake)
        p["_market_edge"] = result["_market_edge"]
        p["_moneyball"]   = result["_moneyball"]
        cls = result["_moneyball"]["classification"]
        counts[cls] = counts.get(cls, 0) + 1

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
        else:
            kept.append(p)

    parsed["picks"] = kept
    summary["discarded_market"] = disc_mkt
    parsed["summary"] = summary
    parsed.setdefault("_pipeline", {})
    parsed["_pipeline"]["moneyball"] = {
        "evaluated": len(picks),
        "kept": len(kept),
        "rerouted": rerouted,
        "by_classification": counts,
        "stake_used": stake,
        "thresholds": EDGE_THRESHOLDS,
    }
    if picks:
        log.info(
            "moneyball[%s]: %d picks → %d kept / %d rerouted | %s",
            sport, len(picks), len(kept), rerouted, counts,
        )
    return parsed
