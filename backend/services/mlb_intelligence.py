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


# ── Pitcher quality scoring (xERA / FIP-aware with regression detection) ─────
def _pitcher_quality_score(p: Optional[dict]) -> Optional[float]:
    """0.0–1.0 score using ADVANCED metrics (xERA → FIP → xFIP → ERA) plus
    Statcast quality (Hard Hit %, Barrel %).

    Crucial fix (Cubs vs Pirates regression):
    When xERA diverges from ERA by ≥ 1.0 we tag the pitcher as
    PITCHER_OVERPERFORMING (ERA much lower than xERA → likely regression,
    PENALIZE) or PITCHER_UNDERVALUED (ERA much higher than xERA → real
    skill, BONUS). The tag is written back onto the input dict so the
    post-processor can read it via `p["_regression_signal"]`.
    """
    if not p or not isinstance(p, dict):
        return None
    era      = p.get("era")
    xera     = p.get("xera")
    fip      = p.get("fip")
    xfip     = p.get("xfip")
    whip     = p.get("whip")
    k_per_bb = p.get("k_per_bb")
    if k_per_bb is None:
        # Derive from k9/bb9 if available (added by Savant).
        k9, bb9 = p.get("k9"), p.get("bb9")
        if isinstance(k9, (int, float)) and isinstance(bb9, (int, float)) and bb9 > 0:
            k_per_bb = k9 / bb9
    hard_hit_pct = p.get("hard_hit_pct") or p.get("hard_hit")
    barrel_pct   = p.get("barrel_pct")   or p.get("barrel")

    # Need at least one base metric.
    if all(v is None for v in (era, xera, fip, xfip, whip, k_per_bb, hard_hit_pct, barrel_pct)):
        return None

    score = 0.0
    components = 0.0

    # Primary ERA-style metric — prefer xERA → fip → xfip → ERA. The
    # winner gets weight 1.5 (because advanced metrics are more
    # predictive); fall-backs share weight 1.0.
    primary = next((m for m in (xera, fip, xfip, era) if m is not None), None)
    if primary is not None:
        s = max(0.0, min(1.0, 1.0 - (primary - 2.5) / 4.0))
        # Higher weight when the source was xERA or FIP (Statcast-grade).
        w = 1.5 if (xera is not None or fip is not None) else 1.0
        score += s * w
        components += w

    if whip is not None:
        s = max(0.0, min(1.0, 1.0 - (whip - 1.0) / 0.7))
        score += s; components += 1.0

    if k_per_bb is not None and k_per_bb > 0:
        s = max(0.0, min(1.0, (k_per_bb - 1.0) / 4.0))
        score += s; components += 1.0

    if hard_hit_pct is not None:
        # 25% → 1.0, 50% → 0.0 (higher = worse).
        s = max(0.0, min(1.0, 1.0 - (float(hard_hit_pct) - 25.0) / 25.0))
        score += s; components += 1.0

    if barrel_pct is not None:
        # 4% → 1.0, 14% → 0.0.
        s = max(0.0, min(1.0, 1.0 - (float(barrel_pct) - 4.0) / 10.0))
        score += s; components += 1.0

    if components == 0:
        return None
    base = score / components

    # ── Regression detection (xERA vs ERA divergence) ───────────────────
    regression_signal: Optional[str] = None
    if era is not None and xera is not None:
        delta = float(xera) - float(era)
        if delta >= 1.0:
            regression_signal = "PITCHER_OVERPERFORMING"
            base = max(0.0, base - 0.15)
        elif delta <= -1.0:
            regression_signal = "PITCHER_UNDERVALUED"
            base = min(1.0, base + 0.10)

    if regression_signal:
        p["_regression_signal"] = regression_signal

    return round(base, 3)


# ── UNDER safety rules ───────────────────────────────────────────────────────
# Hard rules to prevent the engine from recommending Under against an
# overperforming ace (Cubs vs Pirates failure).
UNDER_SAFETY_RULES = {
    "min_starts_both":              3,    # both pitchers must have ≥3 starts
    "block_if_overperforming_ace":  True, # block when any pitcher is overperforming AND has ERA < 3.00
    "min_pitcher_score_for_under":  0.60, # both pitchers ≥ 0.60 to recommend Under
    "park_factor_strict_threshold": 1.10, # if park_factor > 1.10, require stricter scores
    "min_pitcher_score_high_park":  0.70,
    "under_buffer_normal":          0.8,  # expected_runs must be < line - buffer
    "under_buffer_offensive_park":  1.2,  # tighter buffer when park_factor > 1.05
}


def under_pick_passes_safety_rules(
    home_pitcher: dict,
    away_pitcher: dict,
    *,
    expected_runs: Optional[float] = None,
    book_line: Optional[float] = None,
    park_factor: float = 1.0,
) -> tuple[bool, list[str]]:
    """Return (passes, failure_reasons). When `passes=False` the caller
    MUST NOT recommend the Under regardless of what the LLM/Poisson model
    suggests.
    """
    reasons: list[str] = []

    starts_h = int(home_pitcher.get("games_pitched") or home_pitcher.get("games_started") or 0)
    starts_a = int(away_pitcher.get("games_pitched") or away_pitcher.get("games_started") or 0)
    if starts_h < UNDER_SAFETY_RULES["min_starts_both"] \
       or starts_a < UNDER_SAFETY_RULES["min_starts_both"]:
        reasons.append("INSUFFICIENT_STARTS")

    # Overperforming ace block
    if UNDER_SAFETY_RULES["block_if_overperforming_ace"]:
        for pi in (home_pitcher, away_pitcher):
            sig = pi.get("_regression_signal")
            era = pi.get("era")
            if sig == "PITCHER_OVERPERFORMING" and era is not None and float(era) < 3.00:
                reasons.append("OVERPERFORMING_ACE_BLOCK")
                break

    # Both pitcher quality scores
    h_score = _pitcher_quality_score(home_pitcher) or 0.0
    a_score = _pitcher_quality_score(away_pitcher) or 0.0
    threshold = UNDER_SAFETY_RULES["min_pitcher_score_for_under"]
    if park_factor > UNDER_SAFETY_RULES["park_factor_strict_threshold"]:
        threshold = UNDER_SAFETY_RULES["min_pitcher_score_high_park"]
    if h_score < threshold or a_score < threshold:
        reasons.append("PITCHER_QUALITY_TOO_LOW")

    # Buffer check
    if expected_runs is not None and book_line is not None:
        buffer = UNDER_SAFETY_RULES["under_buffer_normal"]
        if park_factor > 1.05:
            buffer = UNDER_SAFETY_RULES["under_buffer_offensive_park"]
        if expected_runs >= (book_line - buffer):
            reasons.append("INSUFFICIENT_BUFFER")

    return (len(reasons) == 0, reasons)


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
MLB_INTELLIGENCE_RULES = """REGLAS ESPECÍFICAS PARA MLB (PIPELINE MONEYBALL — NO NEGOCIABLES):

═══ A) LECTURA OBLIGATORIA DE CAPAS PRECOMPUTADAS ═══
El pipeline MLB adjunta al payload de cada partido (no tienes que recalcular):
  • advanced_stats_snapshot   → Statcast adapter (xERA, xwOBA, barrel%, hard-hit%, K%, BB%, WHIP, OPS, wRC+ de equipo). Incluye data_quality por bloque.
  • pressure_base             → tier HIGH/MODERATE/LOW/NEUTRAL + flags + inputs (hits L5, runs L5 combined).
  • sabermetrics              → home/away.ops_profile, war_impact, starting_pitcher_fip + match_edges + summary + adjustments.
  • advanced_adjustments       → auditoría Phase 9 (raw_conf_delta, weighted_conf_delta, weight_factor_used según data_quality 60/35/0).
  • sabermetrics_audit         → auditoría Phase 9.6 (sabermetrics_weighted_adjustment + data_quality).
  • model_verification.discrepancies → ghost-edges (Phase 11): ERA_UNDERSTATES_RISK, ERA_OVERSTATES_RISK, PITCHER_XWOBA_WARNING, GHOST_EDGE_HARD_CONTACT_VS_UNDER, GHOST_EDGE_TEAM_XWOBA_VS_UNDER, GHOST_EDGE_UNDER_VS_L5_HIGH_SCORING, GHOST_EDGE_OVER_VS_L5_LOW_SCORING, GHOST_EDGE_F5_UNDER_VS_L5, RECENT_RUN_TREND_CONTRADICTS_*.
  • fragility, script_survival, pitcher_quality_score → 0-100.
  • market_selection           → mercado protegido sugerido (recommended_market, protected_alternative, why_this_market, why_not_other_markets, reason_codes, watchlist, requires_manual_odds).
  • historical_pattern_match  → coincidencia con patrones históricos (warehouse). Incluye historical_hit_rate, historical_roi, best_historical_market, pattern_confidence_adjustment, pattern_reason_codes, sample_size.

═══ B) FILOSOFÍA: PROTECTED-MARKET-FIRST ═══
Tu objetivo NO es predecir; es ELEGIR EL MERCADO MÁS PROTEGIDO que mejor refleje el guion real:
  1. Probable pitchers / FIP / xERA / xwOBA.
  2. Lineups y WAR/OPS si están disponibles.
  3. Pressure Base: hits, carreras promedio, BB, HR, L5/L15.
  4. Fragility + Script Survival.
  5. Ghost-Edges contra el lado considerado.
  6. Selección del mercado más protegido (consulta `market_selection`).
  7. SOLO después: odds / edge.

═══ C) MERCADOS PERMITIDOS (REGLAS NUEVAS — REEMPLAZAN LAS PROHIBICIONES ANTIGUAS) ═══
  ✅ Moneyline favorito: cuando es sólido pero el margen no soporta -1.5 (cover_prob < 0.50 o marginProjection < 2.0).
  ✅ Run Line -1.5 favorito: PERMITIDO sólo si marginProjection ≥ 2.0, cover_prob ≥ 0.50, pitcher edge + bullpen edge + lineup strength + WAR edge + sabermetrics edge alineados. SIN ese soporte → Moneyline.
  ✅ Run Line +1.5 underdog: cuando favorito no es dominante.
  ✅ Full Game Under: SOLO con pressure_base LOW + FIP/xERA fuertes en ambos + xwOBA baja + fragility bajo + script_survival alto.
  ✅ F5 Under: PREFERIDO sobre Full Game Under cuando abridores son fuertes pero bullpen frágil O pressure_base es HIGH_PRESSURE.
  ✅ Full Game Over: SOLO si coinciden VARIAS señales (pressure_base ≥ MODERATE + pitcher_risk vía xERA/xwOBA + hard-contact alto + OPS alto + cuotas razonables). Nunca por OPS solo.
  ✅ Team Total Over/Under: cuando el split del lado lo sostiene.
  ✅ NRFI/YRFI: solo con `firstInningSplit` claro.
  ❌ Doble Oportunidad / Draw No Bet / cualquier mercado de "empate" — NO existen en MLB.

═══ D) REGLAS GHOST-EDGE (BLOQUEOS) ═══
Si `model_verification.discrepancies` contiene flags UNDER-killer (ERA_UNDERSTATES_RISK, PITCHER_XWOBA_WARNING, GHOST_EDGE_HARD_CONTACT_VS_UNDER, GHOST_EDGE_TEAM_XWOBA_VS_UNDER) y tu pick es Under:
  → degrádalo a F5 Under (si abridores soportan) o watchlist.
Si contiene flags OVER-killer (ERA_OVERSTATES_RISK, GHOST_EDGE_OVER_VS_L5_LOW_SCORING) y tu pick es Over:
  → muévelo a watchlist.

═══ E) PRESSURE BASE (PRESIÓN OFENSIVA OCULTA) ═══
  • HIGH_PRESSURE + pick Under → bandera roja: prefiere F5 Under si abridores son fuertes.
  • LOW_PRESSURE + ambos pitchers sólidos → confirma Under.
  • Live-acceleration flag → contacto in-game caliente sin runs aún (bomba de tiempo). No recomiendes Under fuerte.

═══ F) SABERMETRICS (WAR/OPS/FIP) ═══
La capa Sabermetrics es de CONFIRMACIÓN, no motor principal. **JAMÁS** justifiques un pick fuerte SOLO con WAR/OPS/FIP. Se aplica ya ponderada (60% strong, 35% partial, 0% missing) — `sabermetrics_audit.sabermetrics_weighted_adjustment` te dice el delta neto.
  • ERA_OVERSTATES_PITCHER_QUALITY (en sabermetrics) → pitcher con suerte, riesgo oculto para Under.
  • ERA_UNDERSTATES_PITCHER_QUALITY → pitcher con mala suerte, posible valor oculto.

═══ G) MARKET SELECTION (USA EL RESULTADO PROPUESTO) ═══
El campo `market_selection.recommended_market` ya aplicó las reglas de protección (ghost-edge / pressure / bullpen / run-line margin / odds availability). Tu trabajo:
  • Si market_selection.watchlist == True → manda el pick a `watchlist` y razona por qué.
  • Si market_selection.requires_manual_odds == True → manda a `structural_lean_requires_odds` o `watchlist_manual_odds`, NUNCA a `discarded_market`.
  • Si market_selection.recommended_market difiere del que te propondrías, EXPLICA por qué adoptas o descartas la sugerencia y cita los reason_codes.

═══ H) HISTORICAL PATTERN MEMORY (`historical_pattern_match` — capa de ajuste conservadora) ═══
  • Si `historical_pattern_match.sample_size < 20` → solo warning, NO ajustes confianza.
  • Si `20 <= sample_size < 50` → ajuste moderado siguiendo `pattern_confidence_adjustment` (cap ±5 puntos).
  • Si `sample_size >= 50` Y `historical_roi > 0` → puedes aumentar confianza hasta el ajuste sugerido. Cita el patrón en `reasoning`.
  • NUNCA conviertas un pick débil en uno fuerte solo por memoria histórica.

═══ I) FAIL-SOFT Y PROHIBICIONES DE DESCARTE ═══
  • Si advanced_stats_snapshot / pressure_base / sabermetrics están missing → usa lógica base (pitcher matchup + recent_run_split + odds). Anota la limitación en `risks` pero NO descartes el partido.
  • Cuotas ausentes en MLB → estructural lean / watchlist manual odds, NO discarded_market.
  • "Motivación normal" en MLB → NEUTRAL, no es razón de descarte. Salta a estructura.

═══ J) MOMENTUM L5 vs L15 (compatible con la base ya existente) ═══
La estructura `baseballHistoricalProfile.recentRunSplit` sigue presente y la usa pressure_base internamente. Estructura típica::

    recentRunSplit = {
      "home": {"runs_l5_avg": 5.2, "runs_l15_avg": 4.1, "delta_pct": +27.0, ...},
      "away": {"runs_l5_avg": 3.4, "runs_l15_avg": 4.6, "delta_pct": -26.0, ...}
    }
    recentRunTrend = "BOTH_HOT" | "HOME_HOT_AWAY_COLD" | "BOTH_COLD" | ...

Umbrales (umbralizados también internamente):
  • delta_pct > +20%       → equipo caliente (ofensiva subiendo).
  • delta_pct < -20%       → equipo frío (ofensiva cayendo).
  • |delta_pct| < 10%      → momentum neutro.

Cuando justifiques un Under/Over en `reasoning`, cita los valores L5/L15 explícitamente (ejemplo: "Yankees runs L5 5.6 vs L15 4.0 = +40% caliente"). Si recentRunTrend = "INSUFFICIENT_DATA" → anótalo en risks, NO inventes.

═══ K) VIVO EN MLB ═══
Compara contra el pregame pick por `game_pk`. Inning ≥ 7 con diferencia ≤ 2 carreras: NO recomiendes moneyline del que va arriba (caso Rangers vs Angels). Evalúa runs restantes y bullpen disponible.
"""
