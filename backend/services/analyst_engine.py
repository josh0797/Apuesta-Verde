"""LLM Analyst Engine — two-stage hybrid pipeline.

Architecture:
  Stage 1 (PRE-FILTER) — fast/cheap model (default: gpt-4o-mini)
    • Normalizes raw match payloads
    • Classifies accessory signals (motivation context, market viability)
    • Returns a shortlist of candidate match_ids to deeply analyze

  Stage 2 (FINAL ANALYSIS) — strong reasoning model (default: gpt-4o)
    • Receives ONLY the shortlisted matches
    • Produces the strict-JSON picks output the rest of the app consumes

Motivation logic upgrade (v2):
  • Motivation is CONTEXTUAL and STANDINGS-AWARE (relegation, playoffs,
    European spots, title race, seeding, survival all bump 4-5).
  • Low table position does NOT auto-imply low motivation.
  • LOW_BOTH is the ONLY motivation state that may trigger automatic discard
    (and only when no other edge exists).
  • ASYMMETRIC_HIGH_LOW is treated as a SIGNAL, often creating value in
    protected markets — never as a kill switch.

Backwards compatibility:
  • `analyze_matches(payload, sport)` keeps the same signature and returns the
    same top-level JSON shape (with new optional fields `motivation_state` per
    pick and `_pipeline` metadata at the root).
"""
from __future__ import annotations

import json
import os
import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger("analyst")

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Two-stage model selection (both configurable). Defaults:
#   MINI  → gpt-4o-mini   (pre-filter, normalization, accessory classification)
#   FULL  → gpt-4o        (final deep analysis on candidates only)
# Legacy var `OPENAI_MODEL` still respected as MINI default for backward compat.
OPENAI_MODEL_MINI = os.environ.get("OPENAI_MODEL_MINI") or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_MODEL_FULL = os.environ.get("OPENAI_MODEL_FULL", "gpt-4o")

# How aggressively to shortlist: at most this many matches reach Stage 2.
# Tunable via env so prod can lower it if costs spike.
TWO_STAGE_MAX_CANDIDATES = int(os.environ.get("TWO_STAGE_MAX_CANDIDATES", "6"))
# Below this batch size the pre-filter adds latency without saving cost,
# so we skip Stage 1 and go straight to Stage 2.
TWO_STAGE_MIN_INPUT = int(os.environ.get("TWO_STAGE_MIN_INPUT", "3"))


SPORT_RULES = {
    "football": """REGLAS DEL DEPORTE (Fútbol):
- Mercados PERMITIDOS: 1X2, Doble Oportunidad, Under 2.5, Under 3.5, Hándicap Asiático conservador (-0.5/-1.0), Draw No Bet, DO 1er Tiempo.
- Mercados PROHIBIDOS: Over 2.5/3.5 como principal, BTTS, Hándicap -1.5+, Goleador, Resultado exacto, Corners, Tarjetas.""",
    "basketball": """REGLAS DEL DEPORTE (NBA/Basket):
- Mercados PERMITIDOS: Moneyline (favorito claro), Total Points UNDER (en línea cercana al promedio histórico), Spread conservador (-3.5/-4.5 máximo para favorito sólido).
- Mercados PROHIBIDOS: Spreads >7 puntos como principal, Player Props con dependencia individual, Over Total Points como principal, parlay/combinadas.
- En vivo: Si el favorito gana por <8 con cuarto final >5min restantes y el equipo perdedor tiene momentum (recientes 2-3 canastas), evitar Moneyline del favorito.""",
    "baseball": """REGLAS DEL DEPORTE (MLB/Béisbol):
- Mercados PERMITIDOS: Moneyline del favorito (cuota 1.30-1.85), Run Line +1.5 del underdog claro, Total Runs UNDER 8.5/9.5 cuando ambos pitchers son de élite.
- Mercados PROHIBIDOS: Run Line -1.5 del favorito como principal (alta varianza), F5 Spread, props de jugador.
- En vivo: Si entrada >=7 y diferencia <=2 carreras, EVITAR moneyline del que va arriba; evaluar Under runs restantes.""",
}


# ───────────────────────────────────────────────────────────────────────────
# Motivation v2 — shared instruction block reused by BOTH prompts so the
# pre-filter and the final analysis classify motivation identically.
# ───────────────────────────────────────────────────────────────────────────
MOTIVATION_RULES_V2 = """REGLAS DE MOTIVACIÓN v2 (CONTEXTUAL Y STANDINGS-AWARE):

══════ COMPETITION STAGE OVERRIDE — NO NEGOCIABLE (evaluar PRIMERO) ══════

Antes de mirar standings, posición de tabla, forma reciente, tamaño del
club o contexto genérico, evalúa SIEMPRE el campo `competition_stage` /
`is_final` / `pressure_state` que viene en el payload.

Si `is_final == true` o `competition_stage == "final"`:
  - home.level = 5  AND  away.level = 5
  - motivation_state = HIGH_BOTH
  - pressure_state = FINAL
  - PROHIBIDO clasificar como NORMAL motivation.
  - PROHIBIDO listarlo en `summary.discarded_motivation`.
  - PROHIBIDO usar la palabra "normal" en `motivation.home.label/away.label`.
  - El riesgo correcto a destacar NO es baja motivación; es "volatilidad de
    final" (presión, decisiones inestables, sustituciones tácticas).
  - Si decides descartarlo, va a `discarded_market` o `incomplete_data`,
    NUNCA a `discarded_motivation`.

Si `competition_stage == "semifinal"` o `pressure_state == KNOCKOUT_HIGH_PRESSURE`:
  - Ambos equipos normalmente motivation = 5.
  - motivation_state = HIGH_BOTH.
  - SOLO baja a 4 si hay evidencia explícita de:
      a) rotación masiva confirmada, o
      b) eliminatoria ya decidida en el global, o
      c) un lado matemáticamente clasificado y el otro no.

Si `competition_stage in {"quarterfinal", "round_of_16", "playoff"}`:
  - Ambos equipos motivation = 4–5 según decisividad.
  - Si `is_two_legged_tie == true` y hay `aggregate_score`:
      • equipo perdiendo en el global → motivation = 5
      • equipo ganando estrechamente (≤1) → motivation = 4–5
      • equipo ganando por margen amplio (≥2) → puede ser 3–4 (riesgo
        rotación), nunca 1–2 salvo evidencia clara.
  - NO clasifiques estos partidos como LOW_BOTH.

Si `competition_stage` es `"unknown"` pero el nombre de liga/torneo o `round`
contiene "final", "semifinal", "playoff", "knockout", "eliminatoria", "ida",
"vuelta", "octavos", "cuartos", "liguilla", o "repechaje", INFIERE el stage
apropiado en lugar de caer en NORMAL.

══════ ESCALA 1-5 (solo se aplica DESPUÉS del override anterior) ══════

La motivación NO se deduce solo del nombre del equipo ni de la posición de tabla.
Debe inferirse del CONTEXTO COMPETITIVO real (qué se juega cada equipo HOY).

Clasifica cada equipo 1–5 según escenario real:
  5 — Urgencia máxima: lucha directa por descenso/permanencia matemática,
      partido decisivo de playoff, final/semifinal eliminatoria, definición
      de título, partido a vida o muerte por clasificación europea/copas,
      o seeding crítico en cierre de temporada NBA/MLB.
  4 — Alta motivación: zona de playoffs / clasificación europea / wildcard
      / playoffs MLB, racha de pelea por puesto, derbi o rivalidad fuerte,
      necesidad de puntos para alcanzar objetivo aún vivo.
  3 — Normal: temporada normal sin urgencia particular, mediación de
      tabla sin objetivos ni amenazas inmediatas.
  2 — Baja: objetivo ya prácticamente asegurado o ya eliminado pero
      jugando por dignidad/rachas; equipo desconectado del objetivo.
  1 — Sin motivación REAL: campeón ya confirmado, eliminado matemáticamente,
      tanking deliberado en NBA, equipo ya descendido sin nada por jugar,
      o rotación masiva confirmada (descanso de titulares pre-copa).

REGLAS NEGATIVAS CRÍTICAS (no negociables):
- Posición baja en tabla NO IMPLICA motivación baja. Si está en zona de descenso
  y la temporada aún no terminó, motivación = 4–5 (lucha por supervivencia).
- "Equipo grande" o "equipo pequeño" NO determina la motivación. El contexto sí.
- Equipo fuera de Champions/playoffs pero peleando el último puesto = 4–5.
- Equipo cómodo en zona media sin nada por jugar = 2–3 según rotación.
- Si NO tienes información de standings/contexto suficiente PERO conoces el
  stage (final/semi/playoff), aplica el OVERRIDE. Si tampoco tienes stage,
  asigna 3 (Normal) y reflejalo en el campo `reason`. NUNCA asumas motivación
  baja por defecto.

CLASIFICA TAMBIÉN motivation_state DEL PARTIDO:
  HIGH_BOTH         — ambos equipos en 4–5 (ambos tienen mucho por jugar)
  ASYMMETRIC_HIGH_LOW — uno en 4–5, otro en 1–2 (asimetría motivacional)
  LOW_BOTH          — ambos equipos en 1–2 (ninguno tiene algo real por jugar)
  NORMAL            — el resto (incluye combinaciones con 3)

Y CLASIFICA pressure_state:
  FINAL                   — final de cualquier competición
  KNOCKOUT_HIGH_PRESSURE  — semi/cuartos/octavos/playoff eliminatorio
  LEAGUE_URGENCY          — partido de liga con lucha por descenso/título/Europa
  NORMAL_LEAGUE           — partido de liga sin urgencia particular
  LOW_STAKES              — partido sin nada por jugar

POLÍTICA DE DESCARTE POR MOTIVACIÓN:
- motivation_state = LOW_BOTH → solo descarta si NO hay ningún otro edge
  (mercado protegido viable, asimetría de talento, valor en cuota, etc.).
- motivation_state = ASYMMETRIC_HIGH_LOW → NO descartes. Considera el lado
  con motivación 4–5 como favorito psicológico/táctico. Puede generar VALOR
  real en mercados protegidos (Doble Oportunidad, Draw No Bet, 1X2 del lado
  motivado, Under si el lado desmotivado defenderá replegado).
- motivation_state = HIGH_BOTH → el partido puede ser caótico; usa Under
  si hay defensa fuerte o evita Over por intensidad inestable.
- motivation_state = NORMAL → trata el partido con los criterios estándar.
- FINAL / KNOCKOUT_HIGH_PRESSURE → NUNCA en discarded_motivation.

La motivación es una SEÑAL ponderada, NO un kill switch. Si crees que vale
la pena recomendar un pick con LOW_BOTH apoyado en un mercado protegido +
otra evidencia, hazlo y lista el rationale en `reasoning`."""


# ───────────────────────────────────────────────────────────────────────────
# Stage 2 system prompt — full analysis (gpt-4o by default)
# ───────────────────────────────────────────────────────────────────────────
def _build_system_prompt(sport: str) -> str:
    sport_rules = SPORT_RULES.get(sport, SPORT_RULES["football"])
    return f"""Eres un analista deportivo profesional especializado en apuestas de VALOR con gestión de riesgo. Tu objetivo es identificar apuestas de alta probabilidad y baja volatilidad en eventos deportivos (próximas 48h o en vivo).

DEPORTE A ANALIZAR: {sport.upper()}

{sport_rules}

{MOTIVATION_RULES_V2}

REGLAS GENERALES (todos los deportes):
1. Análisis MOTIVACIONAL OBLIGATORIO antes de cualquier análisis técnico, siguiendo el bloque de REGLAS DE MOTIVACIÓN v2 anterior.

2. SCORE DE CONFIANZA (0-100), pesos:
   - Diferencia nivel 20% + Motivación 25% + Forma reciente 15% + H2H 10% + Local/Visitante 10% + Bajas 10% + Estabilidad mercado 10%.
   - Mínimo para recomendar: 60 (modo MODERADO). Media: 60-69. Alta: 70-79. Máxima: >=80.
   - Penalizaciones: contexto ausente/>12h: -10; odds ausentes/>1h: -5; solo 1 snapshot: -5; oponente motivacion=5: -5.
   - BONIFICACIÓN por ASYMMETRIC_HIGH_LOW: +3 a +6 si el pick favorece al lado motivado en mercado protegido.

3. ANTI-TRAMPA cuotas:
   - Cuota <1.15 DESCARTAR. Cuota >2.20 para favorito sospechoso, investigar.
   - Rango óptimo favorito: 1.25-1.85.
   - Divergencia entre casas >15% "Divergencia sospechosa".

4. MÁXIMO 8 picks recomendados. ORDENADOS DE MAYOR A MENOR confianza (más confiable primero). Si NADA cumple devuelve verdict=no_value.

5. SIEMPRE devuelve JSON ESTRICTO con la estructura del template (sin comentarios, sin markdown):
{{
  "verdict": "value_found" | "no_value",
  "no_value_message": "Hoy no hay valor. No apostar es la mejor apuesta." (solo si no_value),
  "picks": [
    {{
      "match_id": (int o string),
      "match_label": "Equipo A vs Equipo B",
      "league": "string",
      "kickoff_iso": "ISO datetime",
      "is_live": bool,
      "live_minute": (int|null),
      "live_score": ("X-Y"|null),
      "motivation": {{
        "home": {{"level": 1-5, "label": "string", "reason": "string", "context": "string breve del escenario competitivo"}},
        "away": {{"level": 1-5, "label": "string", "reason": "string", "context": "string breve del escenario competitivo"}}
      }},
      "motivation_state": "HIGH_BOTH" | "ASYMMETRIC_HIGH_LOW" | "LOW_BOTH" | "NORMAL",
      "pressure_state": "FINAL" | "KNOCKOUT_HIGH_PRESSURE" | "LEAGUE_URGENCY" | "NORMAL_LEAGUE" | "LOW_STAKES",
      "key_data": {{
        "form_home": "WDWLW",
        "form_away": "WDWLW",
        "position_home": (int|null), "position_away": (int|null),
        "odds_moneyline": {{"home": (float|null), "draw": (float|null), "away": (float|null), "bookmaker": "string"}},
        "line_movement": "estable"|"subiendo"|"bajando"|"desconocido"
      }},
      "live_stats": (object|null),
      "recommendation": {{
        "market": "Moneyline"|"Doble Oportunidad"|"Total Under"|"Spread"|"Run Line"|"Draw No Bet",
        "selection": "string específica",
        "odds_range": "1.25-1.45",
        "confidence_score": int 0-100,
        "confidence_level": "Maxima"|"Alta"|"Media"
      }},
      "reasoning": "2-3 oraciones explicando por qué tiene valor",
      "risks": ["riesgo 1", "riesgo 2"],
      "cash_out": "viable y recomendado en min X"|"no viable"|"evaluar en vivo",
      "data_freshness": {{"odds": "fresh"|"stale", "context": "fresh"|"stale"}}
    }}
  ],
  "summary": {{
    "high_confidence": [{{"match_id": (int|string), "match_label": "string", "market": "string", "confidence": int}}],
    "medium_confidence": [{{"match_id": (int|string), "match_label": "string", "market": "string", "confidence": int}}],
    "discarded_motivation": [{{"match_id": (int|string), "match_label": "string", "reason": "string", "motivation_state": "LOW_BOTH"}}],
    "discarded_market": [{{"match_id": (int|string), "match_label": "string", "reason": "string"}}],
    "incomplete_data": [{{"match_id": (int|string), "match_label": "string", "missing": "string"}}],
    "total_analyzed": int,
    "total_recommended": int,
    "total_discarded": int,
    "data_freshness": {{"odds": "fresh"|"stale", "context": "fresh"|"stale", "live_active": int}}
  }}
}}

POLÍTICA DE DESCARTE POR MOTIVACIÓN (recordatorio crítico):
- SOLO listar un partido en `summary.discarded_motivation` cuando motivation_state = LOW_BOTH Y no encontraste ningún otro edge razonable.
- NUNCA listes un ASYMMETRIC_HIGH_LOW en discarded_motivation. Si lo descartas, debe ir en discarded_market (mercado no viable) o incomplete_data (datos insuficientes).
- NUNCA listes un HIGH_BOTH en discarded_motivation.
- NUNCA listes un partido con pressure_state = FINAL o KNOCKOUT_HIGH_PRESSURE en discarded_motivation. Si una final no tiene valor, va en discarded_market con razón basada en MERCADO (cuotas pobres, alta volatilidad, falta de mercado protegido, datos faltantes).

REGLAS DE MERCADOS PROTEGIDOS PARA FINALES Y KNOCKOUTS:
- Las finales NO son automáticamente buenas apuestas, pero SÍ son automáticamente máxima motivación.
- Mercados PREFERIDOS en finales/knockouts:
    • Doble Oportunidad del lado superior técnicamente
    • Draw No Bet
    • Under 3.5 si el partido es táctico/controlado (sin lluvia de goles esperada)
- Mercados a EVITAR en finales/knockouts:
    • Over 2.5 / BTTS agresivos salvo que el partido sea claramente caótico/abierto
    • Hándicaps frágiles (>-1.0) salvo brecha de talento muy clara
    • Goleador, exact score, props individuales
- Razones VÁLIDAS para descartar una final (van a discarded_market):
    • Cuotas pobres / sin valor (anti-trampa)
    • Alta volatilidad / signal contradictorio
    • No hay mercado protegido viable
    • Datos faltantes / team news incierto (entonces → incomplete_data)
- Razones INVÁLIDAS para descartar una final:
    • "Ambos equipos tienen motivación normal" (CONTRADICE el override; PROHIBIDO)
    • "Sin urgencia" / "Sin nada por jugar"

NOTAS IMPORTANTES SOBRE LOS DATOS DISPONIBLES:
- `data_source_season` puede ser "2024 (proxy)" porque el plan API no permite season actual. Esto es ESPERADO. Trata estos datos (form_last_5, position, wins/losses) como indicadores SÓLIDOS. Marca context como "stale" pero NO descartes por esto.
- Si tienes odds + position + h2h, TIENES SUFICIENTE para hacer un análisis razonable.
- Si recibes un campo `prefilter_hint` con motivation_state precomputado, úsalo como punto de partida pero recalcula tú mismo si la evidencia lo contradice.

REGLA CRÍTICA DE CATEGORIZACIÓN (NO NEGOCIABLE):
TODO partido analizado DEBE aparecer en EXACTAMENTE UNA de estas listas:
  - `picks` (si lo recomiendas)
  - `summary.discarded_motivation` (solo si LOW_BOTH sin otro edge)
  - `summary.discarded_market`
  - `summary.incomplete_data`

VALIDACIÓN: len(picks) + len(discarded_motivation) + len(discarded_market) + len(incomplete_data) === total_analyzed.

NUNCA dejes las listas de descarte vacías cuando total_discarded > 0. Cada partido descartado debe explicarse con su razón concreta.

ÚNICAMENTE responde JSON válido. NO uses markdown, NO uses bloques de código, NO añadas explicaciones fuera del JSON."""


# Backward-compat alias used by older imports. Always rebuild per-sport at runtime.
ANALYST_SYSTEM_PROMPT = _build_system_prompt("football")


# ───────────────────────────────────────────────────────────────────────────
# Stage 1 system prompt — pre-filter (gpt-4o-mini)
# ───────────────────────────────────────────────────────────────────────────
def _build_prefilter_prompt(sport: str) -> str:
    sport_rules = SPORT_RULES.get(sport, SPORT_RULES["football"])
    return f"""Eres un PRE-FILTRO rápido para un sistema de apuestas de valor. Tu trabajo NO es recomendar picks — es preseleccionar candidatos y normalizar contexto.

DEPORTE: {sport.upper()}

{sport_rules}

{MOTIVATION_RULES_V2}

TU TRABAJO (en 3 pasos):

1) Para CADA partido recibido, clasifica motivation_state aplicando las reglas anteriores.

2) Marca cada partido con un `viability_tag`:
   - "STRONG"   — claramente apto: motivation_state != LOW_BOTH, odds presentes, mercado protegido viable.
   - "BORDERLINE" — apto pero con dudas: motivación parcial, datos incompletos pero contexto suficiente.
   - "DISCARD" — descartable sin necesidad de análisis profundo: LOW_BOTH sin edge, odds ausentes Y forma ausente, mercado fragil sin alternativa.

3) Devuelve JSON ESTRICTO:
{{
  "candidates": [
    {{
      "match_id": (int|string),
      "match_label": "Equipo A vs Equipo B",
      "motivation_state": "HIGH_BOTH" | "ASYMMETRIC_HIGH_LOW" | "LOW_BOTH" | "NORMAL",
      "pressure_state": "FINAL" | "KNOCKOUT_HIGH_PRESSURE" | "LEAGUE_URGENCY" | "NORMAL_LEAGUE" | "LOW_STAKES",
      "motivation_home_level": 1-5,
      "motivation_away_level": 1-5,
      "motivation_summary": "1 oración explicando el contexto motivacional",
      "viability_tag": "STRONG" | "BORDERLINE" | "DISCARD",
      "viability_reason": "string corto explicando la clasificación",
      "preliminary_market_hint": "Doble Oportunidad" | "Under 2.5" | "Moneyline" | "Draw No Bet" | "Total Under" | "Spread" | "Run Line" | "ninguno",
      "skip_deep_analysis": bool
    }}
  ]
}}

REGLAS DEL PRE-FILTRO:
- skip_deep_analysis = true SOLO si viability_tag = "DISCARD" Y motivation_state = LOW_BOTH.
- Si motivation_state = ASYMMETRIC_HIGH_LOW: viability_tag SIEMPRE es "STRONG" o "BORDERLINE", NUNCA "DISCARD" por motivo motivacional.
- Si el payload tiene `is_final == true` o `competition_stage in ("final", "semifinal", "quarterfinal", "round_of_16", "playoff")`:
    • viability_tag NUNCA puede ser DISCARD por motivación.
    • motivation_state DEBE ser HIGH_BOTH (a menos que tengas evidencia explícita de rotación masiva o eliminatoria ya cerrada).
    • PROHIBIDO escribir "ambos equipos tienen motivación normal" para una final/knockout.
    • Si las cuotas no son atractivas, marca "BORDERLINE" y el motor principal lo categorizará como discarded_market después.
- Si tienes posiciones/standings que indican lucha por descenso, playoffs, copa, título → motivación 4–5, viability_tag "STRONG".
- Sé ESTRICTO con las cuotas: cuota <1.15 o >2.20 sospechosa → viability_tag "BORDERLINE" o "DISCARD".
- Devuelve TODOS los partidos recibidos (no omitas ninguno). El sistema decide qué hacer.

ÚNICAMENTE JSON válido. SIN markdown, SIN explicaciones fuera del JSON."""


def _strip_to_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("no JSON object found")
    return t[s : e + 1]


async def _call_openai_with_model(
    user_text: str, session_id: str, system_prompt: str, model: str
) -> str:
    """Call OpenAI Chat Completions for a specific model with JSON-mode."""
    from openai import AsyncOpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        temperature=0.2,
        max_tokens=4096,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


async def _call_emergent(user_text: str, session_id: str, system_prompt: str) -> str:
    """Fallback provider: Claude Sonnet 4.5 via Emergent Universal Key."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    if not EMERGENT_LLM_KEY:
        raise RuntimeError("EMERGENT_LLM_KEY not configured")
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=system_prompt,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    return await chat.send_message(UserMessage(text=user_text))


async def _run_prefilter(
    matches_payload: list[dict], sport: str, session_id: str
) -> dict[str, dict]:
    """Stage 1: cheap model classifies viability + motivation_state.

    Returns a dict keyed by str(match_id) with the prefilter signal per match.
    On any failure returns {} so the caller falls through to full analysis on
    all input matches.
    """
    if not OPENAI_API_KEY:
        return {}
    system_prompt = _build_prefilter_prompt(sport)
    user_text = (
        f"Pre-filtra los siguientes partidos de {sport.upper()}. "
        f"Devuelve JSON con todos los partidos clasificados.\n\n"
        f"FECHA ACTUAL: {datetime.now(timezone.utc).isoformat()}\n"
        f"TOTAL PARTIDOS: {len(matches_payload)}\n\n"
        f"PARTIDOS:\n{json.dumps(matches_payload, ensure_ascii=False, default=str)}"
    )
    try:
        raw = await _call_openai_with_model(
            user_text, session_id, system_prompt, OPENAI_MODEL_MINI
        )
        parsed = json.loads(_strip_to_json(raw))
        candidates = parsed.get("candidates") or []
        index: dict[str, dict] = {}
        for c in candidates:
            mid = c.get("match_id")
            if mid is None:
                continue
            index[str(mid)] = c
        return index
    except Exception as exc:
        log.warning("Pre-filter failed (%s) — falling back to single-stage", exc)
        return {}


def _select_candidates(
    matches_payload: list[dict], prefilter: dict[str, dict]
) -> tuple[list[dict], list[dict]]:
    """Split input matches into (to_analyze_deeply, auto_discarded).

    auto_discarded carries enough info to populate
    `summary.discarded_motivation` directly without a second LLM call.
    Only matches the pre-filter explicitly tags as `skip_deep_analysis=true`
    AND motivation_state == 'LOW_BOTH' get auto-discarded.
    """
    if not prefilter:
        return list(matches_payload), []

    to_analyze: list[dict] = []
    auto_discarded: list[dict] = []

    # Score function for ranking when we exceed TWO_STAGE_MAX_CANDIDATES
    def viability_score(c: dict) -> int:
        return {"STRONG": 3, "BORDERLINE": 2, "DISCARD": 1}.get(c.get("viability_tag", "BORDERLINE"), 2)

    annotated: list[tuple[int, dict, dict]] = []  # (score, match_payload, prefilter_hint)
    for m in matches_payload:
        mid = str(m.get("match_id"))
        hint = prefilter.get(mid) or {}
        # ── Stage-aware guard: NEVER auto-discard finals/knockouts ──
        # Even if the pre-filter said skip_deep_analysis=true and LOW_BOTH,
        # a final / semifinal / playoff must always reach the deep analyst.
        is_final = bool(m.get("is_final"))
        pressure = m.get("pressure_state")
        is_high_pressure = is_final or pressure in ("FINAL", "KNOCKOUT_HIGH_PRESSURE")
        if hint.get("skip_deep_analysis") and hint.get("motivation_state") == "LOW_BOTH" and not is_high_pressure:
            home = (m.get("home_team") or {}).get("name", "?")
            away = (m.get("away_team") or {}).get("name", "?")
            auto_discarded.append({
                "match_id": m.get("match_id"),
                "match_label": f"{home} vs {away}",
                "reason": hint.get("viability_reason") or "LOW_BOTH sin edge alternativo",
                "motivation_state": "LOW_BOTH",
            })
            continue
        # Attach hint so Stage 2 sees the prefilter classification
        enriched = {**m, "prefilter_hint": hint} if hint else m
        # Boost viability score for high-pressure matches so they survive
        # the TWO_STAGE_MAX_CANDIDATES cap.
        score = viability_score(hint)
        if is_high_pressure:
            score += 5
        annotated.append((score, enriched, hint))

    # If we have more candidates than the cap, keep top-scored
    annotated.sort(key=lambda t: -t[0])
    selected = annotated[:TWO_STAGE_MAX_CANDIDATES]
    overflow = annotated[TWO_STAGE_MAX_CANDIDATES:]

    to_analyze = [t[1] for t in selected]
    for _score, m, hint in overflow:
        home = (m.get("home_team") or {}).get("name", "?")
        away = (m.get("away_team") or {}).get("name", "?")
        auto_discarded.append({
            "match_id": m.get("match_id"),
            "match_label": f"{home} vs {away}",
            "reason": (hint.get("viability_reason") if hint else None) or "Overflow del pre-filtro (capacidad)",
            "motivation_state": hint.get("motivation_state") if hint else None,
            "_overflow": True,
        })
    return to_analyze, auto_discarded


async def _hydrate_team_news(matches_payload: list[dict]) -> int:
    """Best-effort: enrich the LLM payload with team-news snippets from
    rotowire / sportsgambler / promiedos for Tier-1 + high-pressure matches.

    Disabled by default (env INJURY_SOURCES_ENABLED). When disabled, returns
    0 and leaves the payload untouched. Always fail-soft.

    Returns the number of matches successfully enriched.
    """
    from . import injury_sources as ij  # local import to keep optional
    if not ij.INJURY_SOURCES_ENABLED:
        return 0
    enriched = 0
    for m in matches_payload:
        tier = m.get("competition_tier")
        pressure = m.get("pressure_state")
        # Only spend latency on Tier-1 OR high-pressure matches.
        if tier != "tier_1" and pressure not in ("FINAL", "KNOCKOUT_HIGH_PRESSURE"):
            continue
        home = (m.get("home_team") or {}).get("name")
        away = (m.get("away_team") or {}).get("name")
        comp = m.get("competition_canonical_name") or m.get("league") or ""
        if not home or not away:
            continue
        try:
            news = await ij.fetch_team_news(home, away, comp, timeout=10)
        except Exception as exc:
            log.info("injury_sources hydration failed for %s vs %s: %s", home, away, exc)
            continue
        if news and not news.get("_disabled"):
            # Compact down to short bullet lists per side so the LLM payload
            # stays small. Cap at 3 snippets per source per side.
            def _compact(side: dict) -> dict:
                return {src: snips[:3] for src, snips in (side or {}).items() if snips}
            m["team_news_snippets"] = {
                "home": _compact(news.get("home", {})),
                "away": _compact(news.get("away", {})),
                "sources": news.get("sources_attempted", []),
                "errors": list((news.get("errors") or {}).keys()),
            }
            enriched += 1
    if enriched:
        log.info("injury_sources: enriched %d matches with external team news", enriched)
    return enriched


# ── Post-LLM correction guard ──────────────────────────────────────────────
# After the LLM returns its JSON we apply a deterministic correction layer:
#   • Finals must NEVER live in discarded_motivation.
#   • Finals must have motivation_state = HIGH_BOTH + pressure_state = FINAL.
#   • Any "motivación normal" reason on a final gets rewritten.
#   • Knockout matches cannot be LOW_BOTH unless aggregate evidence proves it.
#
# This is the safety net for prompt drift: even if the LLM forgets the
# override, the engine still emits correct output.
def _apply_stage_correction(parsed: dict, input_payload: list[dict]) -> dict:
    """Mutate the parsed LLM response so finals/knockouts are stage-correct."""
    if not parsed or not isinstance(parsed, dict):
        return parsed

    # Build a {match_id: stage_info} lookup from the INPUT (authoritative).
    stage_by_id: dict[str, dict] = {}
    for m in input_payload:
        mid = m.get("match_id")
        if mid is None:
            continue
        # Re-detect from the raw input rather than trusting the LLM echo
        from . import match_stage_detector as msd
        stage_by_id[str(mid)] = msd.detect_match_stage(m)

    summary = parsed.get("summary") or {}
    disc_mot = list(summary.get("discarded_motivation") or [])
    disc_mkt = list(summary.get("discarded_market") or [])
    moved = 0
    fixed_reasons = 0
    fixed_state = 0

    # 1) Re-route finals/knockouts wrongly listed in discarded_motivation
    new_disc_mot: list[dict] = []
    for entry in disc_mot:
        sid = str(entry.get("match_id"))
        info = stage_by_id.get(sid)
        if info and (info["is_final"] or info["pressure_state"] == "KNOCKOUT_HIGH_PRESSURE"):
            new_reason = (
                "Final/eliminatoria: motivación máxima en ambos equipos; "
                "descartado por mercado/cuotas/volatilidad, no por motivación."
            ) if info["is_final"] else (
                "Eliminatoria de alta presión: motivación alta en ambos "
                "equipos; descartado por mercado/cuotas/volatilidad, no por motivación."
            )
            disc_mkt.append({
                "match_id": entry.get("match_id"),
                "match_label": entry.get("match_label"),
                "reason": new_reason,
                "pressure_state": info["pressure_state"],
                "_stage_corrected": True,
            })
            moved += 1
        else:
            new_disc_mot.append(entry)
    summary["discarded_motivation"] = new_disc_mot

    # 2) Sanitize reasons in discarded_market too (the bug we saw on screen)
    NORMAL_RE = re.compile(r"motivaci(?:o|ó)n\s+normal|normal\s+motivation", re.IGNORECASE)
    for entry in disc_mkt:
        sid = str(entry.get("match_id"))
        info = stage_by_id.get(sid)
        if info and (info["is_final"] or info["pressure_state"] == "KNOCKOUT_HIGH_PRESSURE"):
            reason = str(entry.get("reason") or "")
            if NORMAL_RE.search(reason):
                if info["is_final"]:
                    entry["reason"] = (
                        "Final: motivación máxima en ambos equipos. Descartado "
                        "por mercado frágil / cuotas no atractivas / volatilidad."
                    )
                else:
                    entry["reason"] = (
                        "Eliminatoria de alta presión: motivación alta en ambos "
                        "equipos. Descartado por mercado frágil / cuotas / volatilidad."
                    )
                entry["pressure_state"] = info["pressure_state"]
                entry["_stage_corrected"] = True
                fixed_reasons += 1
    summary["discarded_market"] = disc_mkt

    # 3) Fix motivation_state + pressure_state on every pick listed
    picks = parsed.get("picks") or []
    for p in picks:
        sid = str(p.get("match_id"))
        info = stage_by_id.get(sid)
        if not info:
            continue
        if info["is_final"]:
            if p.get("motivation_state") != "HIGH_BOTH":
                p["motivation_state"] = "HIGH_BOTH"
                fixed_state += 1
            p.setdefault("pressure_state", "FINAL")
            p["pressure_state"] = "FINAL"
            mot = p.get("motivation") or {}
            for side in ("home", "away"):
                s = mot.get(side) or {}
                if (s.get("level") or 0) < 5:
                    s["level"] = 5
                    s["label"] = s.get("label") or "Final: motivación máxima"
                    s["reason"] = "Final del torneo: ambos equipos juegan con motivación máxima."
                    mot[side] = s
            p["motivation"] = mot
        elif info["pressure_state"] == "KNOCKOUT_HIGH_PRESSURE":
            if p.get("motivation_state") not in ("HIGH_BOTH", "ASYMMETRIC_HIGH_LOW"):
                # Two-leg ties may legitimately be asymmetric; never NORMAL here.
                p["motivation_state"] = "HIGH_BOTH"
                fixed_state += 1
            p["pressure_state"] = "KNOCKOUT_HIGH_PRESSURE"

    parsed["summary"] = summary
    parsed.setdefault("_pipeline", {})
    parsed["_pipeline"]["stage_corrections"] = {
        "moved_finals_to_market": moved,
        "rewrote_normal_reasons": fixed_reasons,
        "forced_motivation_state": fixed_state,
    }
    if moved or fixed_reasons or fixed_state:
        log.info(
            "stage_correction: moved %d finals→market, fixed %d reasons, "
            "forced %d motivation_states",
            moved, fixed_reasons, fixed_state,
        )
    return parsed


async def analyze_matches(matches_payload: list[dict], sport: str = "football") -> dict:
    """Two-stage hybrid analyst.

    Args:
      matches_payload: compact match dicts (output of normalizer.summarize_match_for_llm)
      sport: 'football' | 'basketball' | 'baseball'

    Pipeline:
      Stage 1 (when input >= TWO_STAGE_MIN_INPUT and OPENAI_API_KEY present):
        OPENAI_MODEL_MINI normalizes context + classifies motivation_state +
        viability_tag, returning a shortlist of candidates.
      Stage 2:
        OPENAI_MODEL_FULL produces the strict-JSON picks output on the
        shortlist (or on the full payload if Stage 1 was skipped / failed).
      Fallback:
        If OpenAI is unavailable, Claude Sonnet 4.5 via Emergent runs Stage 2.
    """
    sport = (sport or "football").lower()
    if sport not in SPORT_RULES:
        sport = "football"
    system_prompt = _build_system_prompt(sport)
    session_id = f"analyst-{sport}-{uuid.uuid4().hex[:12]}"

    # ── Stage 1 ── pre-filter (skipped for very small batches)
    prefilter: dict[str, dict] = {}
    pipeline_meta: dict[str, Any] = {
        "stage1_model": None,
        "stage2_model": None,
        "stage1_skipped_reason": None,
        "stage1_candidates": None,
        "stage1_auto_discarded": 0,
    }
    if len(matches_payload) >= TWO_STAGE_MIN_INPUT and OPENAI_API_KEY:
        log.info("Analyst[%s]: Stage 1 pre-filter via %s on %d matches",
                 sport, OPENAI_MODEL_MINI, len(matches_payload))
        prefilter = await _run_prefilter(matches_payload, sport, session_id)
        if prefilter:
            pipeline_meta["stage1_model"] = OPENAI_MODEL_MINI
            pipeline_meta["stage1_candidates"] = len(prefilter)
        else:
            pipeline_meta["stage1_skipped_reason"] = "prefilter_failed_or_empty"
    elif len(matches_payload) < TWO_STAGE_MIN_INPUT:
        pipeline_meta["stage1_skipped_reason"] = f"input_below_threshold_{TWO_STAGE_MIN_INPUT}"
    else:
        pipeline_meta["stage1_skipped_reason"] = "openai_unavailable"

    to_analyze, auto_discarded = _select_candidates(matches_payload, prefilter)
    pipeline_meta["stage1_auto_discarded"] = len(auto_discarded)

    if not to_analyze:
        # Pre-filter classified everything as LOW_BOTH-with-no-edge.
        # Emit a no_value verdict directly, no Stage 2 needed.
        log.info("Analyst[%s]: pre-filter discarded all %d matches", sport, len(matches_payload))
        return _emit_no_value_response(
            matches_payload, auto_discarded, sport, session_id, pipeline_meta
        )

    # ── Optional Stage 1.5 ── external team-news hydration (opt-in)
    # Adds rotowire/sportsgambler/promiedos snippets to high-pressure / Tier-1
    # matches when INJURY_SOURCES_ENABLED=true. Disabled by default so it
    # never adds latency unless explicitly turned on.
    enriched_count = await _hydrate_team_news(to_analyze)
    if enriched_count:
        pipeline_meta["stage1_5_team_news_enriched"] = enriched_count

    # ── Stage 2 ── full analysis on shortlist
    user_text = (
        f"Analiza los siguientes partidos de {sport.upper()} según las reglas. Devuelve JSON estricto.\n\n"
        f"FECHA ACTUAL: {datetime.now(timezone.utc).isoformat()}\n"
        f"DEPORTE: {sport}\n"
        f"TOTAL PARTIDOS: {len(to_analyze)}\n"
        f"PRE-FILTRO APLICADO: {'sí' if prefilter else 'no'}\n\n"
        f"PARTIDOS:\n{json.dumps(to_analyze, ensure_ascii=False, default=str)}"
    )

    response: str = ""
    provider_used: str = ""
    last_error: Exception | None = None

    if OPENAI_API_KEY:
        try:
            log.info(
                "Analyst[%s]: Stage 2 deep analysis via %s on %d candidates",
                sport, OPENAI_MODEL_FULL, len(to_analyze),
            )
            response = await _call_openai_with_model(
                user_text, session_id, system_prompt, OPENAI_MODEL_FULL
            )
            provider_used = f"openai:{OPENAI_MODEL_FULL}"
            pipeline_meta["stage2_model"] = OPENAI_MODEL_FULL
        except Exception as exc:
            log.warning("OpenAI Stage 2 (%s) failed: %s — trying mini", OPENAI_MODEL_FULL, exc)
            last_error = exc
            # Cost-aware retry on mini (still better than nothing)
            if OPENAI_MODEL_FULL != OPENAI_MODEL_MINI:
                try:
                    response = await _call_openai_with_model(
                        user_text, session_id, system_prompt, OPENAI_MODEL_MINI
                    )
                    provider_used = f"openai:{OPENAI_MODEL_MINI} (fallback from {OPENAI_MODEL_FULL})"
                    pipeline_meta["stage2_model"] = OPENAI_MODEL_MINI
                    pipeline_meta["stage2_degraded"] = True
                except Exception as exc2:
                    last_error = exc2

    if not response and EMERGENT_LLM_KEY:
        try:
            log.info("Analyst[%s]: Stage 2 via Emergent Claude Sonnet 4.5", sport)
            response = await _call_emergent(user_text, session_id, system_prompt)
            provider_used = "emergent:claude-sonnet-4-5"
            pipeline_meta["stage2_model"] = "claude-sonnet-4-5"
        except Exception as exc:
            log.error("Emergent fallback also failed: %s", exc)
            last_error = exc

    if not response:
        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    raw = _strip_to_json(response)
    parsed = json.loads(raw)

    # ── Post-LLM deterministic correction layer ──
    # Even if the LLM forgets the COMPETITION_STAGE_OVERRIDE, we re-detect
    # the stage from the original input and fix:
    #   • finals incorrectly listed in discarded_motivation
    #   • "motivación normal" reasons attached to finals/knockouts
    #   • picks whose motivation_state is not HIGH_BOTH on a final
    parsed = _apply_stage_correction(parsed, matches_payload)

    # Merge auto_discarded from pre-filter into the summary so the
    # categorization invariant len(picks)+lists == total_analyzed still holds
    # against the ORIGINAL input size (not just the shortlist).
    summary = parsed.get("summary") or {}
    picks = parsed.get("picks") or []
    disc_mot = summary.get("discarded_motivation") or []
    disc_mkt = summary.get("discarded_market") or []
    incomp = summary.get("incomplete_data") or []

    if auto_discarded:
        for d in auto_discarded:
            if d.get("_overflow"):
                # Overflow → categorize as market (no deep analysis run on it)
                disc_mkt.append({
                    "match_id": d["match_id"],
                    "match_label": d["match_label"],
                    "reason": d.get("reason") or "No analizado por capacidad — viability baja",
                })
            else:
                # Motivation auto-discard (LOW_BOTH no-edge)
                disc_mot.append({
                    "match_id": d["match_id"],
                    "match_label": d["match_label"],
                    "reason": d.get("reason") or "LOW_BOTH sin edge alternativo",
                    "motivation_state": "LOW_BOTH",
                })
        summary["discarded_motivation"] = disc_mot
        summary["discarded_market"] = disc_mkt

    # Reconciliation against the FULL input set
    picked_ids = {p.get("match_id") for p in picks}
    listed_ids = picked_ids | {x.get("match_id") for x in (disc_mot + disc_mkt + incomp)}
    expected_total = len(matches_payload)
    if (len(picks) + len(disc_mot) + len(disc_mkt) + len(incomp)) < expected_total:
        for m in matches_payload:
            mid = m.get("match_id")
            if mid in listed_ids:
                continue
            label = f"{(m.get('home_team') or {}).get('name','?')} vs {(m.get('away_team') or {}).get('name','?')}"
            home_ctx = (m.get('home_team') or {}).get('context') or {}
            away_ctx = (m.get('away_team') or {}).get('context') or {}
            has_odds = bool(m.get('odds_snapshots'))
            has_form = bool(home_ctx.get('form_last_5')) or bool(away_ctx.get('form_last_5'))
            if not has_odds:
                incomp.append({"match_id": mid, "match_label": label, "missing": "Sin cuotas disponibles"})
            elif not has_form:
                incomp.append({"match_id": mid, "match_label": label, "missing": "Sin forma reciente ni posición"})
            else:
                disc_mkt.append({
                    "match_id": mid, "match_label": label,
                    "reason": "No cumple criterios de valor (cuotas/mercados protegidos insuficientes)",
                })
        summary["incomplete_data"] = incomp
        summary["discarded_market"] = disc_mkt

    summary["total_analyzed"] = expected_total
    summary["total_recommended"] = len(picks)
    summary["total_discarded"] = len(disc_mot) + len(disc_mkt) + len(incomp)
    parsed["summary"] = summary

    parsed["_generated_at"] = datetime.now(timezone.utc).isoformat()
    parsed["_session_id"] = session_id
    parsed["_provider"] = provider_used
    parsed["_sport"] = sport
    parsed["_pipeline"] = pipeline_meta
    return parsed


def _emit_no_value_response(
    matches_payload: list[dict],
    auto_discarded: list[dict],
    sport: str,
    session_id: str,
    pipeline_meta: dict,
) -> dict:
    """Build a synthetic no_value response when Stage 1 discards everything.

    Saves a full Stage 2 LLM call when the pre-filter has already determined
    no candidate matches deserve deep analysis.
    """
    disc_mot = [
        {
            "match_id": d["match_id"],
            "match_label": d["match_label"],
            "reason": d.get("reason") or "LOW_BOTH sin edge alternativo",
            "motivation_state": d.get("motivation_state") or "LOW_BOTH",
        }
        for d in auto_discarded
        if not d.get("_overflow")
    ]
    disc_mkt = [
        {
            "match_id": d["match_id"],
            "match_label": d["match_label"],
            "reason": d.get("reason") or "Pre-filtro: sin candidatos viables",
        }
        for d in auto_discarded
        if d.get("_overflow")
    ]
    total = len(matches_payload)
    return {
        "verdict": "no_value",
        "no_value_message": "Hoy no hay valor. No apostar es la mejor apuesta.",
        "picks": [],
        "summary": {
            "high_confidence": [],
            "medium_confidence": [],
            "discarded_motivation": disc_mot,
            "discarded_market": disc_mkt,
            "incomplete_data": [],
            "total_analyzed": total,
            "total_recommended": 0,
            "total_discarded": len(disc_mot) + len(disc_mkt),
            "data_freshness": {"odds": "fresh", "context": "fresh", "live_active": 0},
        },
        "_generated_at": datetime.now(timezone.utc).isoformat(),
        "_session_id": session_id,
        "_provider": f"prefilter-only:{OPENAI_MODEL_MINI}",
        "_sport": sport,
        "_pipeline": pipeline_meta,
    }
