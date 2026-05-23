"""MLB Intelligence Engine — sport-specific weighting and structural matchup.

Strict rule: this module ONLY applies when sport == "baseball". Football and
basketball are not touched.

The traditional analyst engine over-weights narrative factors (motivation,
table position, "team needs to win") that are common in soccer but largely
irrelevant in MLB. This module:

  1. Defines an MLB-specific weighting (motivation capped at 10%, pitchers
     20%, bullpen 20%, offense 15%, splits 15%, base reach 10%, live 10%).
  2. Provides a structural scorer that synthesizes the MLB Stats API context
     (probable pitchers, batting form, bullpen usage) into an estimated
     edge per side, which the LLM then validates.
  3. Exposes a small "matchup card" payload the UI can render alongside the
     LLM pick (pitcher advantage, bullpen risk, offensive pressure).

It does NOT replace the LLM — it produces a structured signal that the LLM
must respect via prompt rules, and that the post-processing layer uses to
correct obviously soccer-biased reasoning.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("mlb_intel")


# ── Sport-specific weighting (per user spec) ─────────────────────────────────
MLB_WEIGHTS = {
    "starting_pitcher_matchup":   0.20,
    "bullpen_strength_fatigue":   0.20,
    "offensive_form_last_5_10":   0.15,
    "batter_vs_pitcher_splits":   0.15,
    "base_reach_probability":     0.10,
    "live_game_state":            0.10,
    "motivation_context":         0.10,   # capped per spec
}
assert abs(sum(MLB_WEIGHTS.values()) - 1.0) < 1e-6, "MLB weights must sum to 1.0"


# Forbidden markets / language for MLB (no draws, no double chance).
MLB_FORBIDDEN_MARKETS = {
    "doble oportunidad", "double chance",
    "draw no bet",  # has no meaning in MLB
    "1x2",          # baseball is binary
}
MLB_FORBIDDEN_SELECTION_TOKENS = {"empate", "draw", "x"}


# ── Pitcher quality scoring ──────────────────────────────────────────────────
def _pitcher_quality_score(p: Optional[dict]) -> Optional[float]:
    """0.0–1.0 score from ERA/WHIP/K-BB. None when stats missing."""
    if not p:
        return None
    era = p.get("era")
    whip = p.get("whip")
    k_per_bb = p.get("k_per_bb")
    if era is None and whip is None:
        return None
    score = 0.0
    components = 0
    if era is not None:
        # ERA: 2.50 → 1.0, 4.50 → 0.5, 6.00 → ~0.15
        s = max(0.0, min(1.0, 1.0 - (era - 2.5) / 4.0))
        score += s; components += 1
    if whip is not None:
        # WHIP: 1.00 → 1.0, 1.30 → 0.55, 1.60 → 0.10
        s = max(0.0, min(1.0, 1.0 - (whip - 1.0) / 0.7))
        score += s; components += 1
    if k_per_bb is not None and k_per_bb > 0:
        # K/BB: 4.5 → 1.0, 2.5 → 0.55, 1.0 → 0.10
        s = max(0.0, min(1.0, (k_per_bb - 1.0) / 4.0))
        score += s; components += 1
    return round(score / components, 3) if components else None


def _offense_quality_score(b: Optional[dict]) -> Optional[float]:
    """0.0–1.0 score from OBP/SLG/OPS/runs per game. None when stats missing."""
    if not b:
        return None
    ops = b.get("ops")
    rpg = b.get("runs_per_game")
    if ops is None and rpg is None:
        return None
    score = 0.0
    components = 0
    if ops is not None:
        s = max(0.0, min(1.0, (ops - 0.640) / 0.220))   # 0.640 league avg → 0; 0.860 elite → 1
        score += s; components += 1
    if rpg is not None:
        s = max(0.0, min(1.0, (rpg - 3.5) / 2.5))       # 3.5 → 0, 6.0 → 1
        score += s; components += 1
    return round(score / components, 3) if components else None


def _bullpen_risk_score(bp: Optional[dict]) -> Optional[float]:
    """0.0–1.0 — HIGHER = more risk (more fatigue). None when missing."""
    if not bp:
        return None
    fatigue = bp.get("fatigue_score_0_100")
    if fatigue is None:
        return None
    return round(min(1.0, max(0.0, fatigue / 100.0)), 3)


# ── Structural matchup score ────────────────────────────────────────────────
def score_mlb_matchup(mlb_context: dict) -> dict:
    """Synthesize the MLB Stats API context into a structural payload the LLM
    must respect.

    Returns a dict with:
        home_pitcher_score, away_pitcher_score, pitcher_advantage (home|away|even),
        home_offense_score, away_offense_score, offensive_pressure_side,
        home_bullpen_risk, away_bullpen_risk, bullpen_risk_side,
        structural_edge_side (home|away|even),
        structural_edge_strength (0.0-1.0),
        narrative (short sentence),
        data_quality (full|partial|missing)
    """
    if not mlb_context or not mlb_context.get("available"):
        return {
            "available": False,
            "data_quality": "missing",
            "narrative": "Sin datos estructurales — usar prompt LLM con prudencia.",
        }

    hp = mlb_context.get("home_pitcher") or {}
    ap = mlb_context.get("away_pitcher") or {}
    hb = mlb_context.get("home_batting") or {}
    ab = mlb_context.get("away_batting") or {}
    hbp = mlb_context.get("home_bullpen") or {}
    abp = mlb_context.get("away_bullpen") or {}

    hp_score = _pitcher_quality_score(hp)
    ap_score = _pitcher_quality_score(ap)
    hb_score = _offense_quality_score(hb)
    ab_score = _offense_quality_score(ab)
    hbp_risk = _bullpen_risk_score(hbp)
    abp_risk = _bullpen_risk_score(abp)

    def _side(a, b, threshold=0.05):
        if a is None or b is None:
            return None
        if a - b > threshold:
            return "home"
        if b - a > threshold:
            return "away"
        return "even"

    pitcher_adv = _side(hp_score, ap_score, threshold=0.07)
    offense_pressure = _side(hb_score, ab_score, threshold=0.05)
    # For bullpen — LOWER risk is better, so we invert.
    bullpen_adv = None
    if hbp_risk is not None and abp_risk is not None:
        bullpen_adv = "home" if abp_risk - hbp_risk > 0.15 else \
                      "away" if hbp_risk - abp_risk > 0.15 else "even"

    # Aggregate structural edge: weighted votes among the three dimensions.
    vote = 0.0
    if pitcher_adv == "home": vote += 0.50
    elif pitcher_adv == "away": vote -= 0.50
    if offense_pressure == "home": vote += 0.30
    elif offense_pressure == "away": vote -= 0.30
    if bullpen_adv == "home": vote += 0.20
    elif bullpen_adv == "away": vote -= 0.20

    if vote >= 0.25:
        edge_side = "home"
    elif vote <= -0.25:
        edge_side = "away"
    else:
        edge_side = "even"
    edge_strength = round(min(1.0, abs(vote)), 3)

    # Data quality
    have = sum(x is not None for x in [hp_score, ap_score, hb_score, ab_score, hbp_risk, abp_risk])
    data_quality = "full" if have >= 5 else "partial" if have >= 2 else "missing"

    narrative_parts: list[str] = []
    if pitcher_adv and pitcher_adv != "even":
        side_name = mlb_context.get(f"{pitcher_adv}_probable") or pitcher_adv
        narrative_parts.append(f"Ventaja de pitcher: {side_name}")
    if offense_pressure and offense_pressure != "even":
        narrative_parts.append(f"Más presión ofensiva: {offense_pressure}")
    if bullpen_adv and bullpen_adv != "even":
        narrative_parts.append(f"Mejor bullpen disponible: {bullpen_adv}")
    if not narrative_parts:
        narrative_parts.append("Matchup estructural parejo")
    narrative = ". ".join(narrative_parts) + "."

    return {
        "available": True,
        "data_quality": data_quality,
        "home_pitcher_score": hp_score,
        "away_pitcher_score": ap_score,
        "pitcher_advantage": pitcher_adv,
        "home_offense_score": hb_score,
        "away_offense_score": ab_score,
        "offensive_pressure_side": offense_pressure,
        "home_bullpen_risk": hbp_risk,
        "away_bullpen_risk": abp_risk,
        "bullpen_risk_side": bullpen_adv,
        "structural_edge_side": edge_side,
        "structural_edge_strength": edge_strength,
        "narrative": narrative,
        # Raw context (for prompt + UI rendering)
        "raw": {
            "home_probable":   mlb_context.get("home_probable"),
            "away_probable":   mlb_context.get("away_probable"),
            "venue":           mlb_context.get("venue"),
            "home_pitcher":    hp,
            "away_pitcher":    ap,
            "home_batting":    hb,
            "away_batting":    ab,
            "home_bullpen":    hbp,
            "away_bullpen":    abp,
        },
    }


# ── Post-LLM corrections: ban draws / double chance in MLB ──────────────────
def sanitize_mlb_picks(parsed: dict) -> dict:
    """Re-route MLB picks that use forbidden markets/selections (Doble
    Oportunidad, Draw No Bet, "o empate") to discarded_market with an
    explanatory reason.

    The Rangers vs Angels case the user reported was caused exactly by this
    bug: the engine produced "Texas Rangers o empate" — a Doble Oportunidad
    pick on MLB, where draws cannot happen.
    """
    if not parsed or not isinstance(parsed, dict):
        return parsed
    if (parsed.get("_sport") or "").lower() != "baseball":
        # _sport is set later, so we also check the per-pick context below.
        pass

    picks = list(parsed.get("picks") or [])
    summary = parsed.get("summary") or {}
    disc_mkt = list(summary.get("discarded_market") or [])

    kept: list[dict] = []
    sanitized = 0
    for p in picks:
        rec = p.get("recommendation") or {}
        market = str(rec.get("market") or "").lower()
        selection = str(rec.get("selection") or "").lower()
        is_forbidden_market = any(m in market for m in MLB_FORBIDDEN_MARKETS)
        is_forbidden_selection = any(tok in selection.split() for tok in MLB_FORBIDDEN_SELECTION_TOKENS)
        if is_forbidden_market or is_forbidden_selection:
            disc_mkt.append({
                "match_id": p.get("match_id"),
                "match_label": p.get("match_label"),
                "reason": (
                    "MLB no admite empates ni Doble Oportunidad: pick descartado "
                    "por mercado inválido para béisbol. Reevaluar como Moneyline "
                    "o Run Line si hay edge real."
                ),
                "_mlb_sanitization": {
                    "original_market": rec.get("market"),
                    "original_selection": rec.get("selection"),
                },
                "_market_guardrail_reroute": True,
            })
            sanitized += 1
        else:
            kept.append(p)

    parsed["picks"] = kept
    summary["discarded_market"] = disc_mkt
    parsed["summary"] = summary
    parsed.setdefault("_pipeline", {})
    parsed["_pipeline"]["mlb_sanitization"] = {"sanitized": sanitized}
    if sanitized:
        log.info("mlb_sanitization: rerouted %d picks with forbidden markets/selections", sanitized)
    return parsed


# ── Prompt fragment for the LLM (Stage 2) ───────────────────────────────────
MLB_INTELLIGENCE_RULES = """REGLAS ESPECÍFICAS PARA MLB (NO NEGOCIABLES — ESTE DEPORTE NO SE ANALIZA COMO FÚTBOL):

A) PONDERACIÓN DE FACTORES (MLB):
   - Matchup de pitchers abridores: 20%
   - Bullpen (fuerza + fatiga reciente): 20%
   - Forma ofensiva últimos 5–10 juegos: 15%
   - Splits bateador vs mano del pitcher: 15%
   - Probabilidad de embase (base reach): 10%
   - Estado de juego en vivo: 10%
   - Motivación / contexto: MÁXIMO 10% (señal secundaria)
   - Cuotas implícitas de mercado: OBLIGATORIO para validación final

   PROHIBIDO: usar "el equipo necesita ganar", "ya clasificado", "sin urgencia",
   "presión de eliminación" como factor PRIMARIO en MLB. Estos son señales
   secundarias con peso ≤10%. La decisión debe descansar en pitchers,
   bullpen, ofensiva real y mercado.

B) MERCADOS PROHIBIDOS EN MLB:
   - ❌ Doble Oportunidad — no aplica (no hay empate en béisbol)
   - ❌ Draw No Bet — no aplica (no hay empate)
   - ❌ Cualquier selección con "empate" / "draw" / "X"
   Si el modelo siente que el partido no tiene edge claro, usar discarded_market,
   NUNCA forzar un Doble Oportunidad.

C) MERCADOS PERMITIDOS EN MLB:
   - ✅ Moneyline (Home/Away) — solo cuando exista edge real (≥3% vs implícita)
   - ✅ Run Line (-1.5 / +1.5) — solo si el diferencial esperado lo justifica
   - ✅ Total Runs Over/Under — solo si pitcher + bullpen + ofensiva + estadio respaldan
   - ✅ Player props (hit, total bases, reach base) — cuando baseReachScore alto

D) USAR EL BLOQUE `mlb_context` cuando esté presente en el payload:
   - Contiene ERA/WHIP/K-BB/HR del pitcher abridor, OPS/runs por juego, y fatiga
     del bullpen (3 días recientes). Es la fuente PRIMARIA — úsala literalmente.
   - Si el bloque viene vacío o con data_quality="missing", admite el partido
     en incomplete_data o explica por qué hay edge sin esos datos.

E) LECCIÓN RANGERS vs ANGELS (caso real del usuario):
   "Una remontada parcial NO equivale a probabilidad alta de comeback. Si el
   mercado/cashout sigue descontando baja probabilidad, el engine debe
   respetar esa señal. Pasar de 6-0 a 6-3 mejora el ticket, pero NO convierte
   automáticamente al equipo en favorito real. El cash out bajo es señal de
   baja probabilidad real de remontada."
   Por tanto, en MLB live, JAMÁS recomiendes Moneyline del equipo perdedor
   solo porque está acercando el marcador. La probabilidad implícita del
   mercado en vivo es tu ground truth.
"""
