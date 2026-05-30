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

import asyncio
import json
import os
import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Optional

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
TWO_STAGE_MAX_CANDIDATES = int(os.environ.get("TWO_STAGE_MAX_CANDIDATES", "10"))
# Below this batch size the pre-filter adds latency without saving cost,
# so we skip Stage 1 and go straight to Stage 2.
TWO_STAGE_MIN_INPUT = int(os.environ.get("TWO_STAGE_MIN_INPUT", "3"))


SPORT_RULES = {
    "football": """REGLAS DEL DEPORTE (Fútbol):
- Mercados PERMITIDOS: 1X2, Doble Oportunidad, Under 2.5, Under 3.5, Hándicap Asiático conservador (-0.5/-1.0), Draw No Bet, DO 1er Tiempo.
- Mercados PROHIBIDOS: Over 2.5/3.5 como principal, BTTS, Hándicap -1.5+, Goleador, Resultado exacto, Corners, Tarjetas.

══════ PROTECTED ALTERNATIVE MARKET SCAN — NO NEGOCIABLE ══════
ANTES de descartar un partido a `discarded_market` por mercado frágil, edge
negativo o falta de valor en 1X2 / Doble Oportunidad / Draw No Bet, evalúa
OBLIGATORIAMENTE Under 3.5 como mercado alternativo protegido. Especialmente
cuando el H2H, ritmo táctico, xG, forma reciente o perfil defensivo indiquen
partido cerrado.

Under 3.5 NO es lo mismo que Under 2.5:
- Under 2.5 = rentable pero frágil (un solo gol extra rompe el ticket).
- Under 3.5 = protege escenarios 2-1 y goles tardíos; menor cuota, mejor
  para perfiles de baja volatilidad.

Reglas de selección:
- Si el modelo espera 0-0 / 1-0 / 1-1 / 2-0 → evaluar ambos (Under 2.5 y 3.5).
- Si el modelo espera posible 2-1 → recomendar Under 3.5 (NO Under 2.5).
- Si gol tardío posible pero no goleada → Under 3.5.
- Si caos / transiciones rápidas / defensas rotas → NO Under (ni 2.5 ni 3.5).

Ejemplo del Knowledge Base (Alavés vs Rayo Vallecano):
H2H reciente con marcadores 1-0, 2-0, 0-1, 0-2 → Under 3.5 fue lectura correcta
mientras 1X2 no tenía edge real. El analista debe identificar este patrón
ANTES de mandar el partido a discarded_market.

Cuando recomiendes Under 3.5 / Under 2.5 como mercado alternativo:
- Incluye en `risks` la nota "mercado alternativo protegido — direct market sin edge"
- Asigna confidence 60-72 (no inflar a "Alta").
- En `reasoning` cita H2H Under-rate, marcadores frecuentes y por qué Under 3.5 protege mejor que Under 2.5 si aplica.""",
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

══════ REGLAS DE FORMA RECIENTE — NO RECOMENDAR CONTRA RACHAS NEGATIVAS ══════

ANTES de recomendar a un equipo como ganador (1X2, Moneyline, Run Line del
favorito, Spread del favorito, lado fuerte en Doble Oportunidad), evalúa
SIEMPRE su `form_last_5`. Reglas no negociables:

- Si el equipo recomendado tiene racha de 3 derrotas consecutivas (las 3
  más recientes son L), NO lo recomiendes como GANADOR puro (Moneyline/1X2)
  salvo que JUSTIFIQUES explícitamente en `reasoning` una razón fuerte
  (bajas críticas del rival confirmadas, motivación 5 vs rival sin nada
  por jugar, mercado protegido alternativo claramente seguro, etc.).
- Si NO puedes justificarlo, NO lo pongas en picks. Pasa a Doble Oportunidad
  con el otro lado, o descártalo a discarded_market con razón clara:
  "Forma reciente desfavorable: <equipo> con racha de N derrotas; sin edge
  alternativo suficiente para superar el signal de forma."
- Para picks de Doble Oportunidad que incluyan al lado en mala racha, baja
  la confidence al menos 5 puntos y menciona la racha negativa en `risks`.
- form_last_5 = "LLLLL" (5 derrotas) sobre cualquier equipo = NO recomendar
  como ganador NI como Doble Oportunidad sin un edge masivo. Mejor pasar al
  rival o descartar.
- Si AMBOS equipos vienen en racha negativa (3+ L cada uno), evalúa Under
  o Doble Oportunidad del más motivado/contextual, NUNCA Moneyline.
- Toda racha de derrotas ≥3 del lado recomendado DEBE figurar en `risks`.


══════ COMPETITION STAGE OVERRIDE — NO NEGOCIABLE ══════

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
    # MLB gets a strict sport-specific block prepended to override soccer-style
    # motivational reasoning. Football/basketball use the existing prompt as-is.
    mlb_block = ""
    if sport == "baseball":
        from .mlb_intelligence import MLB_INTELLIGENCE_RULES
        mlb_block = "\n" + MLB_INTELLIGENCE_RULES + "\n"
    return f"""Eres un analista deportivo profesional especializado en apuestas de VALOR con gestión de riesgo. Tu objetivo es identificar apuestas de alta probabilidad y baja volatilidad en eventos deportivos (próximas 48h o en vivo).

DEPORTE A ANALIZAR: {sport.upper()}

{sport_rules}
{mlb_block}
{MOTIVATION_RULES_V2}

REGLAS GENERALES (todos los deportes):
1. Análisis MOTIVACIONAL OBLIGATORIO antes de cualquier análisis técnico, siguiendo el bloque de REGLAS DE MOTIVACIÓN v2 anterior.

2. SCORE DE CONFIANZA (0-100), pesos:
   - Diferencia nivel 20% + Motivación 25% + Forma reciente 15% + H2H 10% + Local/Visitante 10% + Bajas 10% + Estabilidad mercado 10%.
   - Mínimo para recomendar: 60 (modo MODERADO). Media: 60-69. Alta: 70-79. Máxima: >=80.
   - EXCEPCIÓN mercado alternativo protegido (_alternative_market=True): mínimo 55.
   - Penalizaciones: contexto ausente/>12h: -10; odds ausentes/>1h: -5; solo 1 snapshot: -5; oponente motivacion=5: -5.
   - BONIFICACIÓN por ASYMMETRIC_HIGH_LOW: +3 a +6 si el pick favorece al lado motivado en mercado protegido.

3. ANTI-TRAMPA cuotas:
   - Cuota <1.15 DESCARTAR. Cuota >2.20 para favorito sospechoso en 1X2/Moneyline, investigar. EXCEPCIÓN: Draw No Bet con cuota hasta 2.50 es válido (el mercado elimina el empate).
   - Rango óptimo favorito: 1.25-1.85.
   - Divergencia entre casas >15% "Divergencia sospechosa".

4. MÁXIMO 10 picks recomendados. ORDENADOS DE MAYOR A MENOR confianza (más confiable primero). Si NADA cumple devuelve verdict=no_value.

4b. SI EL CAMPO `understat` ESTÁ PRESENTE EN EL PAYLOAD DEL MATCH: úsalo como fuente PREFERIDA de xG/PPDA sobre cualquier proxy interno. Entre otros (`understat.xg`, `understat.ppda`, `understat.shots`, `understat.deep_completions`) trátalos como evidencia validada externamente. Si los valores Understat contradicen marcadamente a las cuotas (xG desbalanceado >40% pero la cuota implica equilibrio), eso es una señal fuerte de valor de mercado — anótalo en `risks` o `motivation` según corresponda.

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
        "selection": "string con NOMBRE EXPLÍCITO del equipo cuando aplique (ver REGLAS DE SELECTION)",
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

REGLAS DE `recommendation.selection` (NO NEGOCIABLE — CLARIDAD PARA EL USUARIO):
La `selection` SIEMPRE debe ser legible y específica. NUNCA uses códigos opacos
ni placeholders tipo "Home", "Away", "Local", "Visitante", "1", "X", "2", "1X",
"X2", "12", "Home/Draw", "Draw or Away". El usuario debe entender QUIÉN está
siendo apostado sin abrir el detalle.

FORMATO REQUERIDO POR TIPO DE MERCADO (usa el `home_team.name`/`away_team.name`
recibidos en el payload — no inventes nombres):

  • Moneyline / 1X2 / Draw No Bet (un solo lado):
      ✅ "Bayern Munich gana"           (favorito local)
      ✅ "Bayer Leverkusen gana"        (favorito visitante)
      ✅ "Knicks gana"                  (NBA)
      ❌ "Home" / "Away" / "1" / "2" / "Local" / "Visitante"

  • Doble Oportunidad / Double Chance:
      ✅ "Bayern Munich o empate"       (1X con nombre)
      ✅ "Empate o Bremen"              (X2 con nombre)
      ✅ "Bayern Munich o Bremen"       (12 con nombre)
      ❌ "Home/Draw" / "1X" / "X2" / "12" / "Draw or Away"

  • Spread / Hándicap / Run Line:
      ✅ "Bayern Munich -1.5"
      ✅ "Bremen +1.5"
      ✅ "Yankees -1.5 carreras"        (MLB)
      ❌ "Home -1.5" / "Visitante +1.5" / "1 -1.5"

  • Total Over / Total Under:
      ✅ "Más de 2.5 goles"             (fútbol ES)
      ✅ "Menos de 9.5 carreras"        (MLB ES)
      ✅ "Over 220.5 puntos"            (NBA, válido también)
      ❌ "Over 2.5" sin unidad / "Under" sin número

EJEMPLO DE PICK CORRECTO (Bayern Munich vs Werder Bremen, Doble Oportunidad):
  "recommendation": {{
    "market": "Doble Oportunidad",
    "selection": "Bayern Munich o empate",   ← nombre del equipo, no "Home/Draw"
    "odds_range": "1.20-1.28",
    "confidence_score": 78,
    "confidence_level": "Alta"
  }}

Si por cualquier razón no recuerdas el nombre exacto, usa el equipo tal como
aparece en `match_label` o en `home_team.name`/`away_team.name` del payload.
NUNCA uses "Home", "Local", "Visitante" como sustitutos.

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
    """Call OpenAI Chat Completions for a specific model with JSON-mode.

    Hard 90s timeout so a stalled API call (credits exhausted, rate-limit
    backoff, network glitch) can never freeze the modal. The progress
    poller will see `stage=failed` and surface a user-friendly message
    via `humanizeError()`.
    """
    from openai import AsyncOpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=90.0, max_retries=1)
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.2,
                max_tokens=4096,
                response_format={"type": "json_object"},
            ),
            timeout=95.0,   # extra grace over client timeout
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            "openai_timeout: la llamada al LLM superó 90s — probablemente "
            "rate-limit, créditos agotados o problema de red. Reintenta en breve."
        ) from exc
    return resp.choices[0].message.content or ""


async def _call_emergent(user_text: str, session_id: str, system_prompt: str) -> str:
    """Fallback provider: Claude Sonnet 4.5 via Emergent Universal Key.

    Same 90s timeout policy as `_call_openai_with_model` — keeps the job
    queue moving even when the universal key is rate-limited or out of
    credits.
    """
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    if not EMERGENT_LLM_KEY:
        raise RuntimeError("EMERGENT_LLM_KEY not configured")
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=system_prompt,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    try:
        return await asyncio.wait_for(
            chat.send_message(UserMessage(text=user_text)),
            timeout=95.0,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            "emergent_llm_timeout: la llamada al Emergent LLM superó 90s — "
            "probablemente créditos agotados o rate-limit. Reintenta en breve."
        ) from exc


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
    # MLB schedules by Eastern; using UTC here had the analyst occasionally
    # interpret today's late-night MLB games as "tomorrow".
    if sport == "baseball":
        try:
            from zoneinfo import ZoneInfo as _ZI
            _now_iso = datetime.now(_ZI("America/New_York")).isoformat()
        except Exception:
            _now_iso = datetime.now(timezone.utc).isoformat()
    else:
        _now_iso = datetime.now(timezone.utc).isoformat()
    user_text = (
        f"Pre-filtra los siguientes partidos de {sport.upper()}. "
        f"Devuelve JSON con todos los partidos clasificados.\n\n"
        f"FECHA ACTUAL: {_now_iso}\n"
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

async def _prefetch_corner_forms_for_rescue(
    matches: list[Optional[dict]],
    *,
    db: Any = None,
    timeout_seconds: float = 25.0,
) -> int:
    """Fetch corner-form (last 5 matches with corner kicks) for both teams of
    every match passed in, in parallel. Mutates each match dict to add:

        match["_corner_form"] = {
            "home":              { ...team_corner_form... },
            "away":              { ...team_corner_form... },
            "league_avg_total":  10.0,   # placeholder; can be refined per liga
            "h2h_avg_total":     None,   # computed from match["h2h"] if present
        }

    Skipped silently when:
      - the match is None
      - the match has no team IDs
      - timeout fires (returns whatever was completed)

    Returns the number of matches successfully enriched.
    """
    import asyncio as _aio
    import httpx as _httpx
    from . import api_football as _af

    real_matches = [m for m in matches if m and (m.get("home_team") or {}).get("id")
                    and (m.get("away_team") or {}).get("id")]
    if not real_matches:
        return 0

    season = int(os.environ.get("API_SPORTS_SEASON", _af.PROXY_SEASON))
    enriched = 0

    async def _one(client, m):
        nonlocal enriched
        home_id = (m.get("home_team") or {}).get("id")
        away_id = (m.get("away_team") or {}).get("id")
        try:
            home_form, away_form = await _aio.gather(
                _af.team_corner_form(client, int(home_id), n=5, season=season, db=db),
                _af.team_corner_form(client, int(away_id), n=5, season=season, db=db),
                return_exceptions=False,
            )
        except Exception as exc:
            log.debug("corner-form fetch failed for match %s: %s",
                      m.get("match_id"), exc)
            return
        # Optional H2H average if h2h_recent has corner data; otherwise None
        m["_corner_form"] = {
            "home":             home_form,
            "away":             away_form,
            "league_avg_total": 10.0,   # neutral fallback (refine per-league later)
            "h2h_avg_total":    None,
        }
        if (home_form.get("sample_size") or 0) >= 1 or (away_form.get("sample_size") or 0) >= 1:
            enriched += 1

    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            tasks = [_one(client, m) for m in real_matches]
            await _aio.wait_for(_aio.gather(*tasks, return_exceptions=True), timeout=timeout_seconds)
    except _aio.TimeoutError:
        log.info("corner pre-fetch timed out after %.0fs (enriched %d/%d so far)",
                 timeout_seconds, enriched, len(real_matches))
    except Exception as exc:
        log.warning("corner pre-fetch error: %s", exc)
    log.info("corner pre-fetch: enriched %d/%d matches", enriched, len(real_matches))
    return enriched




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


# ── Post-LLM correction guards ─────────────────────────────────────────────
# After the LLM returns its JSON we apply deterministic correction layers:
#   • Finals must NEVER live in discarded_motivation.
#   • Finals must have motivation_state = HIGH_BOTH + pressure_state = FINAL.
#   • Any "motivación normal" reason on a final gets rewritten.
#   • Knockout matches cannot be LOW_BOTH unless aggregate evidence proves it.
#   • Picks endorsing a team on a 3+ loss streak get a form_warning flag,
#     confidence penalty, and (when critical) get re-routed to discarded_market.
#
# These are safety nets for prompt drift: even if the LLM forgets a rule,
# the engine still emits coherent, defensible output.
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

def _apply_explicit_selection(parsed: dict, input_payload: list[dict], sport: str = "football") -> dict:
    """Rewrite opaque `recommendation.selection` codes using real team names.

    Even when the prompt forbids them, models occasionally emit "Home/Draw",
    "1X", "Home", "Visitante", etc. This deterministic post-processor rewrites
    those into selections the user can understand at a glance:

        "Home/Draw"        + Bayern vs Bremen        → "Bayern Munich o empate"
        "1X"                                          → "Bayern Munich o empate"
        "Home"             + Moneyline                → "Bayern Munich gana"
        "Visitante"        + Draw No Bet              → "Bremen gana"
        "Home -1.5"        + Spread                   → "Bayern Munich -1.5"
        "Under 2.5"        + Total Under (football)   → "Menos de 2.5 goles"

    Only rewrites when the original token clearly matches a placeholder; never
    mangles selections that already contain a literal team name.

    Mirror of the frontend `humanizeSelection()` so both sides stay coherent
    even for old picks loaded from history.
    """
    if not parsed or not isinstance(parsed, dict):
        return parsed

    # Build {match_id: (home_name, away_name)} from authoritative input.
    teams_by_id: dict[str, tuple[str, str]] = {}
    for m in input_payload:
        mid = m.get("match_id")
        if mid is None:
            continue
        home = (m.get("home_team") or {}).get("name") or ""
        away = (m.get("away_team") or {}).get("name") or ""
        teams_by_id[str(mid)] = (home, away)

    sport = (sport or "football").lower()
    if sport == "basketball":
        score_unit = "puntos"
    elif sport == "baseball":
        score_unit = "carreras"
    else:
        score_unit = "goles"

    DRAW = "empate"
    HOME_TOKENS = {"home", "local", "1", "h", "casa"}
    AWAY_TOKENS = {"away", "visitor", "visitante", "2", "v", "a", "road"}
    DRAW_TOKENS = {"draw", "empate", "x", "tie", "d"}

    def _resolve_side(token: str, home_name: str, away_name: str) -> str:
        tok = (token or "").strip()
        low = tok.lower()
        if not tok:
            return tok
        if low in DRAW_TOKENS:
            return DRAW
        if low in HOME_TOKENS:
            return home_name or "Local"
        if low in AWAY_TOKENS:
            return away_name or "Visitante"
        return tok  # already a literal — leave alone

    rewrites = 0
    picks = parsed.get("picks") or []
    for p in picks:
        rec = p.get("recommendation") or {}
        sel = rec.get("selection")
        if not sel or not isinstance(sel, str):
            continue
        market = (rec.get("market") or "").lower()
        sid = str(p.get("match_id"))
        home_name, away_name = teams_by_id.get(sid, ("", ""))
        original = sel.strip()
        new_sel = original

        # 1) Short 1X2 codes — "1X", "X2", "12", "1", "X", "2"
        compact = original.replace(" ", "").upper()
        if compact == "1X":
            new_sel = f"{home_name or 'Local'} o {DRAW}"
        elif compact == "X2":
            new_sel = f"{DRAW} o {away_name or 'Visitante'}"
        elif compact == "12":
            new_sel = f"{home_name or 'Local'} o {away_name or 'Visitante'}"
        elif compact == "1":
            new_sel = f"{home_name or 'Local'} gana"
        elif compact == "X":
            new_sel = DRAW
        elif compact == "2":
            new_sel = f"{away_name or 'Visitante'} gana"
        else:
            # 2) Spread/Run Line side prefix: "Home -1.5", "Visitante +1.5"
            spread_m = re.match(
                r"^(home|local|away|visitor|visitante|h|v|1|2)\s*([+-]?\d+(?:\.\d+)?)\s*$",
                original, flags=re.IGNORECASE,
            )
            if spread_m and ("spread" in market or "handicap" in market or "hándicap" in market or "run line" in market or not market):
                side = _resolve_side(spread_m.group(1), home_name, away_name)
                new_sel = f"{side} {spread_m.group(2)}"
            else:
                # 3) Double Chance pairs: "Home/Draw", "Draw or Away", "Local, Empate"
                dc_parts = re.split(r"\s*(?:/|\s+or\s+|\s+o\s+|,)\s*", original)
                dc_parts = [pp for pp in dc_parts if pp]
                if (
                    len(dc_parts) == 2
                    and ("doble" in market or "double chance" in market or "/" in original
                         or any(p.lower() in HOME_TOKENS | AWAY_TOKENS | DRAW_TOKENS for p in dc_parts))
                ):
                    a = _resolve_side(dc_parts[0], home_name, away_name)
                    b = _resolve_side(dc_parts[1], home_name, away_name)
                    if a and b:
                        new_sel = f"{a} o {b}"
                else:
                    # 4) Single-side Moneyline/DNB token: "Home", "Visitante"
                    if original.lower() in (HOME_TOKENS | AWAY_TOKENS | DRAW_TOKENS):
                        resolved = _resolve_side(original, home_name, away_name)
                        if "moneyline" in market or "draw no bet" in market or market.startswith("1x2") or not market:
                            if resolved == DRAW:
                                new_sel = DRAW
                            else:
                                new_sel = f"{resolved} gana"
                        else:
                            new_sel = resolved
                    else:
                        # 5) Totals — "Over 2.5", "Under 8.5" without unit
                        tot_m = re.match(
                            r"^(under|over|menos|m[aá]s)\s*(\d+(?:\.\d+)?)\s*$",
                            original, flags=re.IGNORECASE,
                        )
                        if tot_m:
                            is_under = bool(re.match(r"under|menos", tot_m.group(1), flags=re.IGNORECASE))
                            word = "Menos de" if is_under else "Más de"
                            new_sel = f"{word} {tot_m.group(2)} {score_unit}"

        if new_sel and new_sel != original:
            rec["selection"] = new_sel
            p["recommendation"] = rec
            p["_selection_rewritten"] = {"from": original, "to": new_sel}
            rewrites += 1

    parsed.setdefault("_pipeline", {})
    parsed["_pipeline"]["explicit_selection_rewrites"] = rewrites
    if rewrites:
        log.info("explicit_selection: rewrote %d opaque selection codes", rewrites)
    return parsed


def _apply_form_correction(parsed: dict, input_payload: list[dict]) -> dict:
    """Detect and mitigate picks that endorse a team on a 3+ loss streak.

    Strategy:
      • Severity 'warn'     → penalize confidence by -8 and append a form_warning
                              to the pick's `risks` array.
      • Severity 'critical' → move the pick into summary.discarded_market with
                              a form-based reason; DO NOT keep it in `picks`.
      • Always annotate the pick with a `_form_warning` payload (kept so the
        UI can show the warning even when only soft-penalized).

    This complements the LLM prompt rules: even if the LLM ignores the
    "no recommend against a bad streak" instruction, the engine self-corrects.
    """
    if not parsed or not isinstance(parsed, dict):
        return parsed

    # Local import to avoid circulars at module load.
    from . import form_guard as fg

    # Build lookup of (home_form, away_form, home_name, away_name) per match_id
    forms_by_id: dict[str, dict] = {}
    for m in input_payload:
        mid = str(m.get("match_id"))
        if not mid:
            continue
        home_ctx = (m.get("home_team") or {}).get("context") or {}
        away_ctx = (m.get("away_team") or {}).get("context") or {}
        forms_by_id[mid] = {
            "home_form":  home_ctx.get("form_last_5"),
            "away_form":  away_ctx.get("form_last_5"),
            "home_name":  (m.get("home_team") or {}).get("name", ""),
            "away_name":  (m.get("away_team") or {}).get("name", ""),
        }

    picks = list(parsed.get("picks") or [])
    summary = parsed.get("summary") or {}
    disc_mkt = list(summary.get("discarded_market") or [])

    kept_picks: list[dict] = []
    penalized = 0
    rerouted = 0
    for p in picks:
        mid = str(p.get("match_id"))
        ctx = forms_by_id.get(mid)
        rec = p.get("recommendation") or {}
        if not ctx or not rec.get("selection"):
            kept_picks.append(p)
            continue
        flag = fg.form_red_flag(
            ctx["home_form"], ctx["away_form"],
            rec.get("selection"), rec.get("market") or "",
            ctx["home_name"], ctx["away_name"],
        )
        if not flag:
            kept_picks.append(p)
            continue

        if flag["severity"] == "critical":
            # Re-route to discarded_market entirely.
            disc_mkt.append({
                "match_id": p.get("match_id"),
                "match_label": p.get("match_label"),
                "reason": (
                    f"Forma reciente desfavorable: {flag['reason_es']} "
                    f"Sin edge alternativo suficiente para superar el signal de forma."
                ),
                "_form_corrected": True,
                "_form_warning": flag,
            })
            rerouted += 1
        else:
            # Soft-penalize: -8 confidence + add risk + attach warning payload
            # ── Anti-doble-penalización ─────────────────────────────────
            # Si el array `risks` del pick ya menciona la racha (por nombre
            # del equipo o las palabras "racha"/"streak"), el LLM ya bajó
            # la confianza por este motivo. Saltamos el -8 adicional pero
            # mantenemos el warning visible.
            already_flagged = any(
                (flag["team_name"].lower() in (r or "").lower())
                or ("racha" in (r or "").lower())
                or ("streak" in (r or "").lower())
                for r in (p.get("risks") or [])
            )
            new_level_map = lambda c: ("Maxima" if c >= 80 else "Alta" if c >= 70 else "Media")  # noqa: E731
            if not already_flagged:
                cur = int(rec.get("confidence_score") or 0)
                rec["confidence_score"] = max(50, cur - 8)
                if rec.get("confidence_level"):
                    rec["confidence_level"] = new_level_map(rec["confidence_score"])
            p["recommendation"] = rec
            risks = list(p.get("risks") or [])
            risks.append(
                f"Racha reciente del lado {flag['side']} ({flag['team_name']}): "
                f"{flag['raw_form']} (form_score {flag['form_score']})."
            )
            p["risks"] = risks
            p["_form_warning"] = flag
            kept_picks.append(p)
            penalized += 1

    parsed["picks"] = kept_picks
    summary["discarded_market"] = disc_mkt
    parsed["summary"] = summary

    parsed.setdefault("_pipeline", {})
    parsed["_pipeline"]["form_corrections"] = {
        "penalized": penalized,
        "rerouted_to_market": rerouted,
    }
    if penalized or rerouted:
        log.info(
            "form_correction: penalized=%d picks, rerouted=%d to discarded_market",
            penalized, rerouted,
        )
    return parsed


def _apply_protected_alternative_scan(parsed: dict, input_payload: list[dict]) -> int:
    """Phase 9 — try to rescue Tier 1/2 matches discarded for "no direct edge".

    For every match in summary.discarded_market that:
      • belongs to a Tier 1/2 league (per _football_quality),
      • is `protected_alternative_eligible`,
      • does NOT already carry an alternative-market pick,
    we invoke `under_market_scan.scan_protected_alternatives`. If it returns
    a recommendation, we:
      1. Build a new pick dict with `market` = "Under 3.5" / "Under 2.5" /
         combo, plus `_alternative_market=True` and the reasons list.
      2. Append it to parsed["picks"].
      3. Remove the matching entry from summary.discarded_market.
      4. Return the count of promoted picks (caller will re-run Moneyball).

    The caller MUST re-run apply_moneyball_layer afterwards so the promoted
    picks pass the universal edge-gate and either keep their VALUE_BET /
    UNDERVALUED_EDGE classification or get re-routed if Moneyball rejects.
    """
    from . import under_market_scan as ums  # local import to avoid cycle

    summary = parsed.setdefault("summary", {})
    disc_mkt = list(summary.get("discarded_market") or [])
    picks = parsed.setdefault("picks", [])

    if not disc_mkt:
        return 0

    # Index the full input payload by match_id so we can look up the
    # hydrated doc (which carries odds_snapshots + h2h_recent + tier).
    by_id = {m.get("match_id"): m for m in input_payload}
    existing_pick_ids = {p.get("match_id") for p in picks}

    promoted: list[dict] = []
    remaining_discarded: list[dict] = []

    for entry in disc_mkt:
        mid = entry.get("match_id")
        m = by_id.get(mid) or {}
        fq = m.get("_football_quality") or {}
        # Only attempt rescue on Tier 1/2 with the eligibility flag set.
        if not fq.get("protected_alternative_eligible"):
            remaining_discarded.append(entry)
            continue
        if mid in existing_pick_ids:
            # Already has a pick (e.g. from an earlier rescue) → don't dup.
            remaining_discarded.append(entry)
            continue
        try:
            alt = ums.scan_protected_alternatives(
                m,
                tactical_score=60,    # neutral default; future work: read from LLM
                fragility_score=50,
            )
        except Exception as exc:
            log.warning("scan_protected_alternatives crashed for %s: %s", mid, exc)
            remaining_discarded.append(entry)
            continue
        if not alt or alt.get("state") not in (
            "PROTECTED_MARKET_RECOMMENDED",
            "UNDER35_WATCHLIST",
            "UNDER25_WATCHLIST",
        ):
            remaining_discarded.append(entry)
            continue

        # Build a structured pick. The LLM-friendly fields (motivation,
        # pressure) are kept neutral — Moneyball will recompute its own
        # implied/edge/EV from the odds we provide here.
        is_watchlist = alt["state"] in ("UNDER35_WATCHLIST", "UNDER25_WATCHLIST")
        # NB. minimum acceptable confidence for the PROTECTED ALTERNATIVE
        # path is 55 (vs 60 elsewhere). Watchlist sits exactly at the
        # floor; recommended sits comfortably above it.
        ALT_MIN_CONFIDENCE = 55
        confidence = ALT_MIN_CONFIDENCE if is_watchlist else 74

        pick = {
            "match_id": mid,
            "match_label": entry.get("match_label") or m.get("match_label"),
            "league": m.get("league"),
            "league_id": m.get("league_id"),
            "kickoff_iso": m.get("kickoff_iso"),
            "motivation_state": "ALTERNATIVE_MARKET_SCAN",
            "pressure_state": "NORMAL",
            "recommendation": {
                "market":         alt["market"],
                "selection":      alt["selection"],
                "odds":           alt["decimal_odds"],
                "stake_units":    1 if is_watchlist else 2,
                "confidence_score": confidence,
                "reasoning_es": (
                    f"Mercado directo sin valor en este partido. Se detectó valor "
                    f"protegido en {alt['market']} con edge {alt['edge_pct']:+.1f}% "
                    f"(profile score {alt['profile_score']}/100, "
                    f"H2H Under-rate {int(alt['h2h_under_rate']*100)}% en "
                    f"{alt['samples_h2h']} partidos)."
                ),
                "reasoning_en": (
                    f"No value in direct markets. Detected protected value on "
                    f"{alt['market']} with edge {alt['edge_pct']:+.1f}% "
                    f"(profile score {alt['profile_score']}/100, "
                    f"H2H under-rate {int(alt['h2h_under_rate']*100)}% over "
                    f"{alt['samples_h2h']} matches)."
                ),
            },
            "_alternative_market": True,
            "_alternative_market_payload": alt,
            "_football_quality": fq,
        }
        # Floor-clamp: this path is allowed to dip to 55 (vs 60 default).
        try:
            cs_now = int(pick["recommendation"].get("confidence_score") or 0)
            pick["recommendation"]["confidence_score"] = max(ALT_MIN_CONFIDENCE, cs_now)
        except Exception:
            pass
        picks.append(pick)
        promoted.append(pick)

    summary["discarded_market"] = remaining_discarded
    parsed["picks"] = picks
    if promoted:
        parsed.setdefault("_pipeline", {})["protected_alternative_scan"] = {
            "promoted_count": len(promoted),
            "promoted_ids": [p["match_id"] for p in promoted],
            "states": {p["_alternative_market_payload"]["state"]: 1 for p in promoted},
        }
        log.info(
            "protected_alternative_scan promoted %d match(es) from discarded_market: %s",
            len(promoted),
            [(p["match_label"], p["_alternative_market_payload"]["market"]) for p in promoted],
        )
    return len(promoted)


# ────────────────────────────────────────────────────────────────────────────
# Cross-confirm: when an external source bullet aligns with an aggregated
# signal, boost the signal's confidence and tag it with the source. The
# function mutates `signals` in place. NEVER raises.
# ────────────────────────────────────────────────────────────────────────────
_CROSS_CONFIRM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "UNDER_TREND_DETECTED":      ("under", "marcadores bajos", "pocos goles", "few goals", "low-scoring"),
    "CORNER_VOLUME_DETECTED":    ("córner", "corner", "cornerkick"),
    "EDITORIAL_INJURY_NOTE":     ("baja", "lesion", "injur", "out", "doubtful"),
    "EDITORIAL_MOTIVATION_NOTE": ("necesit", "motivac", "needs to win", "must-win"),
    "STRONG_H2H_PATTERN":        ("h2h", "histor", "head-to-head"),
    "PITCHER_DUEL_SIGNAL":       ("pitcher", "duelo de", "era"),
    "BULLPEN_FATIGUE_SIGNAL":    ("bullpen", "tired", "fatig"),
    "PACE_OVER_SIGNAL":          ("pace", "ritmo alto"),
}


def _cross_confirm_signals(signals: list[dict], external_evidence: list[dict]) -> None:
    if not signals or not external_evidence:
        return
    try:
        bullets_by_source: list[tuple[str, str]] = []
        for ev in external_evidence:
            if ev.get("status") != "ok":
                continue
            src_name = ev.get("source") or ""
            for b in (ev.get("extracted_data") or []):
                bullets_by_source.append((src_name, str(b).lower()))
        if not bullets_by_source:
            return
        for sig in signals:
            code = sig.get("code")
            kws = _CROSS_CONFIRM_KEYWORDS.get(code)
            if not kws:
                continue
            confirming_sources = {
                src for src, text in bullets_by_source
                if any(kw in text for kw in kws)
            }
            if confirming_sources:
                sig["confidence"] = min(100, int(sig.get("confidence") or 60) + 10)
                sig["source"] = ", ".join(sorted(confirming_sources))
                sig["explanation"] = (
                    (sig.get("explanation") or "").rstrip(".")
                    + f". Confirmado por: {sig['source']}."
                )
    except Exception:
        # NEVER let cross-confirm break the pipeline.
        return








async def analyze_matches(matches_payload: list[dict], sport: str = "football", db: Any = None) -> dict:
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

    # ── Stage 0 ── HARD time filter (recurring bug — past games leaked into picks)
    # Drop ANY match whose status is finished OR whose kickoff is in the
    # past (with a 15-minute safety buffer). This is the user's explicit
    # last-line guardrail per the Cubs vs Pirates regression.
    # Baseball-specific: keep IN-PROGRESS games so the MLB live engine can
    # re-evaluate them. They are tagged `_live_route=True` for the
    # orchestrator to branch into the live re-evaluation path.
    from .time_filter import filter_upcoming
    matches_payload, dropped_past = filter_upcoming(
        matches_payload, buffer_minutes=15,
        allow_live_for_sports={"baseball"},
    )
    live_routed_ids = [m.get("match_id") for m in matches_payload
                        if m.get("_live_route")]
    if live_routed_ids:
        log.info("time_filter: routing %d baseball live matches to live engine: %s",
                 len(live_routed_ids), live_routed_ids[:10])
    pipeline_meta: dict[str, Any] = {
        "stage0_dropped_past_or_finished": len(dropped_past),
        "stage0_dropped_match_ids": [m.get("match_id") for m in dropped_past[:20]],
        "stage1_model": None,
        "stage2_model": None,
        "stage1_skipped_reason": None,
        "stage1_candidates": None,
        "stage1_auto_discarded": 0,
    }
    if not matches_payload:
        log.warning("Analyst[%s]: ALL matches dropped by time_filter (Stage 0). dropped=%d",
                    sport, len(dropped_past))
        return _emit_no_value_response(matches_payload, [], sport, session_id, pipeline_meta)

    # ── Stage 1 ── pre-filter (skipped for very small batches)
    prefilter: dict[str, dict] = {}
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

    # ── Stage 1.6 ── P3 Editorial Context Engine (Scrapy, football only)
    # On-demand enrichment over the shortlist (max 8 matches). Adds editorial
    # consensus (motivation_notes, factual_notes, suggested_market) to each
    # match payload. Fail-soft: any failure → editorial_context.available=False
    # and analysis continues with P1+P2 data only.
    # P4.1 (2026-05-28): editorial context se ejecuta para los 3 deportes,
    # no solo football. Los registries ahora incluyen fuentes NBA y MLB
    # (AS.com NBA, Marca NBA, Covers.com NBA/MLB, ESPN MLB, AS.com MLB).
    if sport in ("football", "basketball", "baseball"):
        try:
            from .editorial_context import fetch_editorial_context_bulk
            editorial_input = [
                {
                    "match_id":     m.get("match_id"),
                    "sport":        sport,
                    "home_team":    m.get("home_team") or {},
                    "away_team":    m.get("away_team") or {},
                    "league":       m.get("league"),
                    "kickoff_iso":  m.get("kickoff_iso"),
                }
                for m in to_analyze[:8]
            ]
            # Hard outer timeout (defence-in-depth): the dispatcher already
            # wraps Scrapy + Playwright + Bright Data with their own wait_for,
            # but we wrap one more level so a misbehaving backend can NEVER
            # freeze the analyst engine. 30s = 25s internal + 5s safety.
            editorial_by_id = await asyncio.wait_for(
                fetch_editorial_context_bulk(
                    editorial_input, db=db, force_refresh=False, timeout_sec=25.0,
                ),
                timeout=30.0,
            )
            attached = 0
            for m in to_analyze:
                mid = str(m.get("match_id"))
                if mid in editorial_by_id:
                    m["editorial_context"] = editorial_by_id[mid]
                    if editorial_by_id[mid].get("available"):
                        attached += 1
            pipeline_meta["editorial_context_attached"] = attached
            pipeline_meta["editorial_context_evaluated"] = len(editorial_input)
            log.info("Analyst[%s]: editorial context attached %d/%d matches",
                     sport, attached, len(editorial_input))
        except asyncio.TimeoutError:
            log.warning("editorial_context Stage 1.6 timeout (>30s) — skipping, pipeline continues")
            pipeline_meta["editorial_context_error"] = "timeout"
        except Exception as exc:
            log.warning("editorial_context Stage 1.6 failed: %s", exc)
            pipeline_meta["editorial_context_error"] = str(exc)[:200]

    # ── Stage 1.7.b ── External Source Evidence Layer ─────────────────
    # Collect bullets/snippets from FotMob, SofaScore, Flashscore (via
    # BrightData when available) + FBref, Basketball-Reference, NBA Stats
    # (free). Sport-aware; sources whose APPLICABLE_SPORTS doesn't include
    # the current sport are skipped. Fail-soft.
    try:
        from .external_sources import collect_external_evidence
        evidence_input = [
            {
                "match_id":     m.get("match_id"),
                "home_team":    m.get("home_team") or {},
                "away_team":    m.get("away_team") or {},
                "home":         (m.get("home_team") or {}).get("name") if isinstance(m.get("home_team"), dict) else None,
                "away":         (m.get("away_team") or {}).get("name") if isinstance(m.get("away_team"), dict) else None,
                "league":       m.get("league"),
                "kickoff_iso":  m.get("kickoff_iso"),
            }
            for m in to_analyze[:8]
        ]
        evidence_by_id = await asyncio.wait_for(
            collect_external_evidence(evidence_input, sport, db=db, timeout_sec=45.0),
            timeout=50.0,
        )
        attached_ev = 0
        for m in to_analyze:
            mid = str(m.get("match_id"))
            if mid in evidence_by_id:
                m["external_source_evidence"] = evidence_by_id[mid]
                if evidence_by_id[mid]:
                    attached_ev += 1
        pipeline_meta["external_source_evidence_attached"] = attached_ev
        pipeline_meta["external_source_evidence_evaluated"] = len(evidence_input)
        log.info("Analyst[%s]: external_source_evidence attached to %d/%d matches",
                 sport, attached_ev, len(evidence_input))
    except asyncio.TimeoutError:
        log.warning("external_source_evidence Stage 1.7.b timeout — skipping")
        pipeline_meta["external_source_evidence_error"] = "timeout"
    except Exception as exc:
        log.warning("external_source_evidence Stage 1.7.b failed: %s", exc)
        pipeline_meta["external_source_evidence_error"] = str(exc)[:200]

    # ── Optional Stage 1.7 ── MLB Stats API hydration (baseball only)
    # Attaches `mlb_context` + `mlb_matchup` to each baseball match payload so
    # the LLM uses real pitcher/bullpen/batting data instead of soccer-style
    # narrative reasoning. Best-effort; never raises.
    if sport == "baseball" and db is not None:
        try:
            from . import mlb_stats_api as msapi
            from . import mlb_intelligence as mli
            mlb_hydrated = 0
            for m in to_analyze:
                try:
                    ctx = await msapi.hydrate_mlb_match_context(db, m)
                    if ctx and ctx.get("available"):
                        m["mlb_context"] = ctx
                        m["mlb_matchup"] = mli.score_mlb_matchup(ctx)
                        mlb_hydrated += 1
                except Exception as exc:
                    log.debug("MLB hydration skipped for %s: %s", m.get("match_id"), exc)
            if mlb_hydrated:
                pipeline_meta["mlb_stats_api_enriched"] = mlb_hydrated
                log.info("Analyst[baseball]: MLB Stats API hydrated %d/%d matches", mlb_hydrated, len(to_analyze))
        except Exception as exc:
            log.warning("MLB hydration block failed: %s", exc)

    # ── Stage 2 ── full analysis on shortlist
    # Eastern Time for MLB so the LLM can correctly reason about "tonight's"
    # games (late-night MLB starts wrap into the next UTC day).
    if sport == "baseball":
        try:
            from zoneinfo import ZoneInfo as _ZI
            _now_iso = datetime.now(_ZI("America/New_York")).isoformat()
        except Exception:
            _now_iso = datetime.now(timezone.utc).isoformat()
    else:
        _now_iso = datetime.now(timezone.utc).isoformat()
    user_text = (
        f"Analiza los siguientes partidos de {sport.upper()} según las reglas. Devuelve JSON estricto.\n\n"
        f"FECHA ACTUAL: {_now_iso}\n"
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

    # NOTE (2026-05-28): Emergent Universal Key fallback DISABLED por
    # política del usuario. El análisis SOLO usa OpenAI directo. Si
    # OpenAI falla, devolvemos un error explícito en vez de colgar el
    # job con un fallback que el usuario no quiere consumir.

    if not response:
        raise RuntimeError(
            f"OpenAI Stage 2 failed and Emergent fallback is disabled. "
            f"Last error: {last_error}"
        )

    raw = _strip_to_json(response)
    parsed = json.loads(raw)

    # ── Post-LLM deterministic correction layer ──
    # Even if the LLM forgets the COMPETITION_STAGE_OVERRIDE, we re-detect
    # the stage from the original input and fix:
    #   • finals incorrectly listed in discarded_motivation
    #   • "motivación normal" reasons attached to finals/knockouts
    #   • picks whose motivation_state is not HIGH_BOTH on a final
    parsed = _apply_stage_correction(parsed, matches_payload)

    # ── Explicit-team-name rewrite ──
    # Rewrite opaque selection codes ("Home/Draw", "1X", "Home") into
    # human-readable selections using the real team names from the payload.
    # Mirrors the frontend humanizeSelection() so old picks stay coherent too.
    parsed = _apply_explicit_selection(parsed, matches_payload, sport)

    # ── Form-recency safety net ──
    # Penalize / re-route picks that endorse a team on a 3+ loss streak.
    # Critical streaks (≥4 L or form_score ≤ -60) become discarded_market.
    parsed = _apply_form_correction(parsed, matches_payload)

    # ── MLB-specific sanitization (baseball only) ──
    # Re-route picks that use forbidden markets for MLB (Doble Oportunidad,
    # Draw No Bet, "o empate"). This is the deterministic fix for the
    # Rangers vs Angels / Texas Rangers o empate bug the user reported.
    if sport == "baseball":
        from . import mlb_intelligence as _mli
        parsed = _mli.sanitize_mlb_picks(parsed)

    # ── Universal Moneyball Betting Layer ──
    # Subsumes the prior Market Implied Probability Guardrail. For every pick:
    #   • computes implied/estimated/edge/EV/ROI (back-compat `_market_edge`)
    #   • computes fragility, public overreaction, trap signals, undervalued
    #     signals, and the final 9-state classification (VALUE_BET / STRONG_VALUE_BET
    #     / UNDERVALUED_EDGE / LIVE_VALUE_WINDOW / FRAGILE_EDGE / WAIT_FOR_BETTER_LINE
    #     / NO_BET_VALUE / MARKET_TRAP / PUBLIC_OVERREACTION)
    #   • reroutes the no-value classes to summary.discarded_market.
    from . import moneyball_layer as _mb
    parsed = _mb.apply_moneyball_layer(parsed, sport=sport, stake=10.0)

    # ── Phase 9 — Protected Alternative Market Scan (football only) ──────
    # For every Tier 1/2 match that the analyst dropped to discarded_market
    # without finding value in 1X2 / DC / DNB, see if there's value hiding
    # in a PROTECTED goal-line market instead (Under 3.5 / Under 2.5, or a
    # DC + Under combo). If yes, promote it back into `picks` with a clear
    # `_alternative_market` badge. This is what the Alavés vs Rayo case
    # asked for — never drop a top fixture without trying Under-line value.
    if sport == "football":
        try:
            promoted = _apply_protected_alternative_scan(parsed, matches_payload)
            if promoted:
                # Re-run Moneyball on the promoted picks ONLY, to make sure
                # they pass the universal edge-gate too. We never recommend
                # an Under "porque suena seguro" — every pick must have a
                # measurable edge.
                parsed = _mb.apply_moneyball_layer(parsed, sport=sport, stake=10.0)
        except Exception as exc:
            log.warning("protected alternative scan failed: %s", exc)

    # ── Phase 10 — Universal Alternative Market Rescue ──────────────────
    # Para CUALQUIER partido en summary.discarded_market (no solo Tier 1/2),
    # intentamos una última pasada por mercados protegidos antes de descartar
    # definitivamente. Esto cubre baseball/basketball y fútbol Tier 3/4 que
    # quedan fuera del scope del scan especializado de Phase 9.
    #
    # Lo que se rescata se mueve a `summary.rescued_picks` (o
    # `summary.watchlist` si la classification es WATCHLIST).
    try:
        from . import alternative_rescue as _ar
        by_id = {m.get("match_id"): m for m in matches_payload}
        rescued_now: list[dict] = list((parsed.get("summary") or {}).get("rescued_picks") or [])
        watchlist_now: list[dict] = list((parsed.get("summary") or {}).get("watchlist") or [])

        original_disc_mkt = list((parsed.get("summary") or {}).get("discarded_market") or [])
        kept_disc_mkt: list[dict] = []
        rescued_count = 0

        # Tracking: para no procesar el mismo match dos veces.
        already_rescued_ids = {r.get("match_id") for r in rescued_now} | \
                               {r.get("match_id") for r in watchlist_now}

        # ── Phase 10a — Pre-fetch corner forms (football only) ─────────
        # Hacemos pre-fetch en bulk async aquí porque alternative_rescue es
        # sync. Cache muy agresivo (12h por equipo) amortiza el coste.
        if sport == "football" and original_disc_mkt:
            try:
                await asyncio.wait_for(
                    _prefetch_corner_forms_for_rescue(
                        [by_id.get(e.get("match_id")) for e in original_disc_mkt
                         if e.get("match_id") in by_id and e.get("match_id") not in already_rescued_ids],
                        db=db,
                    ),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                log.warning("corner pre-fetch timeout (>12s) — skipping, pipeline continues")
            except Exception as exc:
                log.debug("corner pre-fetch failed: %s", exc)

        # ── Phase 10b — Pre-fetch basketball historical profile ─────────
        # Regla del producto: ningún match basketball que pase el filtro
        # prioritario puede descartarse sin antes consultar su historial
        # profundo (últimos 10–15 partidos por equipo + H2H). El profile
        # también queda adjunto al match para que la UI lo muestre incluso
        # cuando el rescue no genera pick.
        if sport == "basketball" and original_disc_mkt:
            try:
                from .historical_enrichment import prefetch_basketball_profiles
                await asyncio.wait_for(
                    prefetch_basketball_profiles(
                        [by_id.get(e.get("match_id")) for e in original_disc_mkt
                         if e.get("match_id") in by_id and e.get("match_id") not in already_rescued_ids],
                        db=db,
                    ),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                log.warning("basketball historical pre-fetch timeout (>12s) — skipping, pipeline continues")
            except Exception as exc:
                log.debug("basketball historical pre-fetch failed: %s", exc)

        # ── Phase 10c — Pre-fetch baseball historical profile ──────────
        # Misma regla para baseball: prefetch antes del rescue para que
        # baseball_runs_rescue.find_baseball_runs_value() encuentre el
        # `baseballHistoricalProfile` ya hidratado y pueda proyectar
        # total runs / F5 / team-total / run line.
        if sport == "baseball" and original_disc_mkt:
            try:
                from .historical_enrichment import prefetch_baseball_profiles
                await asyncio.wait_for(
                    prefetch_baseball_profiles(
                        [by_id.get(e.get("match_id")) for e in original_disc_mkt
                         if e.get("match_id") in by_id and e.get("match_id") not in already_rescued_ids],
                        db=db,
                    ),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                log.warning("baseball historical pre-fetch timeout (>12s) — skipping, pipeline continues")
            except Exception as exc:
                log.debug("baseball historical pre-fetch failed: %s", exc)

        for entry in original_disc_mkt:
            mid = entry.get("match_id")
            if mid in already_rescued_ids:
                # Ya fue rescatado por Phase 9 o por otra ronda — mantenerlo
                # en discarded sería duplicar; saltamos.
                continue

            m = by_id.get(mid)
            if not m:
                kept_disc_mkt.append(entry)
                continue

            base_conf = (entry.get("_market_edge") or {}).get("estimated_probability") or 0
            # Estimated_probability viene 0-1; convertir a confidence 0-100
            # con calibración inversa. Como aproximación: 0.6 ⇒ 65 conf.
            base_conf_int = int(round((base_conf or 0.6) * 100)) if base_conf else 60
            base_conf_int = max(55, min(80, base_conf_int))

            try:
                loop = asyncio.get_event_loop()
                rescue = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda m=m: _ar.attempt_alternative_market_rescue(
                            m,
                            sport=sport,
                            base_confidence=base_conf_int,
                            why_direct_failed=entry.get("reason"),
                        ),
                    ),
                    timeout=4.0,
                )
            except asyncio.TimeoutError:
                log.info("rescue Phase 10 timeout (>4s) for %s — skipping", mid)
                rescue = None
            except Exception as exc:
                log.debug("rescue Phase 10 failed for %s: %s", mid, exc)
                rescue = None

            if not rescue:
                kept_disc_mkt.append(entry)
                continue

            # Construir entrada uniforme con match_id/label + payload de rescue.
            payload = {
                "match_id":    mid,
                "match_label": entry.get("match_label"),
                **rescue,
            }
            if rescue.get("routed_to") == "watchlist":
                watchlist_now.append(payload)
            else:
                rescued_now.append(payload)
                rescued_count += 1

        (parsed.get("summary") or {})["rescued_picks"] = rescued_now
        (parsed.get("summary") or {})["watchlist"]     = watchlist_now
        (parsed.get("summary") or {})["discarded_market"] = kept_disc_mkt
        if rescued_count:
            log.info("rescue Phase 10: rescued %d match(es) from discarded → rescued_picks", rescued_count)
    except Exception as exc:
        log.warning("Phase 10 universal rescue failed: %s", exc)

    # ── Phase 11 — Sport Vocabulary Firewall ────────────────────────────
    # Last line of defense against terminology leaks. Re-routes any pick
    # whose `recommendation` / `reasoning` / `risks` references a sport
    # that doesn't match (e.g. baseball pick using "goles", basketball pick
    # talking about "córners", football pick using "carreras"). This is
    # the deterministic fix for the user-reported bug where MLB cards
    # rendered with football-only markets.
    try:
        from . import sport_vocab_guard as _svg
        parsed = _svg.apply_sport_vocab_guard(parsed, sport=sport)
        # Also scrub rescued_picks and watchlist — those bypass picks[] but
        # still surface in the UI.
        summary_now = parsed.get("summary") or {}
        for bucket in ("rescued_picks", "watchlist", "protected_acceptable"):
            items = summary_now.get(bucket) or []
            cleaned: list[dict] = []
            evicted: list[dict] = []
            for it in items:
                leaks = _svg.detect_vocab_leaks(it, sport)
                if leaks:
                    it["_sport_vocab_guard"] = {
                        "sport": sport, "forbidden_terms_found": leaks, "rerouted": True,
                    }
                    evicted.append({
                        "match_id":    it.get("match_id"),
                        "match_label": it.get("match_label"),
                        "reason": (
                            f"SPORT_VOCAB_LEAK ({bucket}): vocabulario incorrecto para {sport} "
                            f"({', '.join(set(leaks[:5]))})."
                        ),
                        "_sport_vocab_guard": it["_sport_vocab_guard"],
                        "_origin_bucket": bucket,
                    })
                else:
                    cleaned.append(it)
            summary_now[bucket] = cleaned
            if evicted:
                disc_now = list(summary_now.get("discarded_market") or [])
                disc_now.extend(evicted)
                summary_now["discarded_market"] = disc_now
                log.warning(
                    "sport_vocab_guard[%s]: evicted %d items from %s",
                    sport, len(evicted), bucket,
                )
        parsed["summary"] = summary_now
    except Exception as exc:
        log.warning("sport_vocab_guard failed: %s", exc)

    # ── Phase 12 — Moneyball ↔ Editorial Context interpretation ─────────
    # For every kept pick attach the "How Moneyball interprets editorial
    # context" payload. Also attach a passive interpretation to discarded
    # entries so the UI can render PUBLIC_NARRATIVE_RISK warnings when the
    # press recommended a market the motor rejected.
    if sport == "football":
        try:
            from .editorial_context import moneyball_interpretation as _mi
            by_id = {m.get("match_id"): m for m in matches_payload}
            picks_now = parsed.get("picks") or []
            for p in picks_now:
                mid = p.get("match_id")
                src = by_id.get(mid) or {}
                ed = src.get("editorial_context")
                if ed and ed.get("available"):
                    mb_market = (p.get("recommendation") or {}).get("market")
                    classification = (p.get("_moneyball") or {}).get("classification") \
                                    or (p.get("_market_edge") or {}).get("verdict")
                    p["_editorial_interpretation"] = _mi.interpret(
                        editorial=ed,
                        moneyball_pick={"market": mb_market, "recommendation": p.get("recommendation")},
                        moneyball_classification=classification,
                    )
                    # Mirror the available editorial context onto the pick so the
                    # UI doesn't need to cross-reference the match document.
                    p["_editorial_context"] = ed
            # Also annotate discarded_market with PUBLIC_NARRATIVE_RISK when
            # the editorial recommended that very market.
            summary_now = parsed.get("summary") or {}
            disc = summary_now.get("discarded_market") or []
            for entry in disc:
                mid = entry.get("match_id")
                src = by_id.get(mid) or {}
                ed = src.get("editorial_context")
                if ed and ed.get("available"):
                    interp = _mi.interpret(
                        editorial=ed,
                        moneyball_pick=None,
                        moneyball_classification="NO_BET_VALUE",
                    )
                    entry["_editorial_interpretation"] = interp
                    entry["_editorial_context"] = ed
            parsed.setdefault("_pipeline", {})
            parsed["_pipeline"]["editorial_interpretation"] = {
                "picks_annotated":         sum(1 for p in picks_now if p.get("_editorial_interpretation")),
                "discarded_annotated":     sum(1 for d in disc if d.get("_editorial_interpretation")),
            }
        except Exception as exc:
            log.warning("editorial_interpretation Phase 12 failed: %s", exc)

    # ── Phase 12b — Basketball Historical Profile annotation ────────────
    # When the basketball pre-fetch produced a profile (Phase 10b), copy it
    # into the user-facing payload entries (picks, discarded_market,
    # rescued_picks, watchlist, protected_acceptable) so the UI can render
    # the "Historial profundo" panel without re-fetching anything.
    if sport == "basketball":
        try:
            by_id_bk = {m.get("match_id"): m for m in matches_payload}
            summary_bk = parsed.setdefault("summary", {})
            picks_bk   = parsed.get("picks") or []
            buckets = [
                picks_bk,
                summary_bk.get("discarded_market")    or [],
                summary_bk.get("discarded_motivation") or [],
                summary_bk.get("rescued_picks")       or [],
                summary_bk.get("watchlist")           or [],
                summary_bk.get("protected_acceptable") or [],
            ]
            attached = 0
            for bucket in buckets:
                for entry in bucket:
                    mid = entry.get("match_id")
                    src = by_id_bk.get(mid) or {}
                    prof = src.get("basketballHistoricalProfile")
                    if prof:
                        entry["basketballHistoricalProfile"] = prof
                        attached += 1
            parsed.setdefault("_pipeline", {})
            parsed["_pipeline"]["basketball_historical"] = {
                "entries_annotated": attached,
                "engine_version":    "basketball-hist.1",
            }
        except Exception as exc:
            log.warning("basketball historical annotation failed: %s", exc)

    # ── Phase 12c — Baseball Historical Profile annotation ─────────────
    if sport == "baseball":
        try:
            by_id_bb = {m.get("match_id"): m for m in matches_payload}
            summary_bb = parsed.setdefault("summary", {})
            picks_bb   = parsed.get("picks") or []
            buckets = [
                picks_bb,
                summary_bb.get("discarded_market")    or [],
                summary_bb.get("discarded_motivation") or [],
                summary_bb.get("rescued_picks")       or [],
                summary_bb.get("watchlist")           or [],
                summary_bb.get("protected_acceptable") or [],
            ]
            attached = 0
            for bucket in buckets:
                for entry in bucket:
                    mid = entry.get("match_id")
                    src = by_id_bb.get(mid) or {}
                    prof = src.get("baseballHistoricalProfile")
                    if prof:
                        entry["baseballHistoricalProfile"] = prof
                        attached += 1
            parsed.setdefault("_pipeline", {})
            parsed["_pipeline"]["baseball_historical"] = {
                "entries_annotated": attached,
                "engine_version":    "baseball-hist.1",
            }
        except Exception as exc:
            log.warning("baseball historical annotation failed: %s", exc)

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
    rescued_picks = summary.get("rescued_picks") or []
    watchlist     = summary.get("watchlist") or []
    protected_acc = summary.get("protected_acceptable") or []
    picked_ids = {p.get("match_id") for p in picks}
    listed_ids = (
        picked_ids
        | {x.get("match_id") for x in (disc_mot + disc_mkt + incomp)}
        | {x.get("match_id") for x in rescued_picks}
        | {x.get("match_id") for x in watchlist}
    )
    expected_total = len(matches_payload)
    accounted = (
        len(picks) + len(disc_mot) + len(disc_mkt) + len(incomp)
        + len(rescued_picks) + len(watchlist)
    )
    if accounted < expected_total:
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
    summary["total_recommended"] = len(picks) + len(rescued_picks)
    summary["total_discarded"] = len(disc_mot) + len(disc_mkt) + len(incomp)
    summary["total_watchlist"] = len(watchlist)
    summary["total_protected_acceptable"] = len(protected_acc)
    summary["total_rescued"] = len(rescued_picks)
    parsed["summary"] = summary

    # ── Phase 13 — Editorial Context Signals propagation ────────────────
    # Build canonical `editorial_context_signals` for EVERY entry in
    # every bucket so the UI can show transparent reasoning even on
    # discarded matches. Sport-aware: codes from the catalog that are
    # not applicable to the current sport are silently dropped.
    try:
        from .signal_aggregator import (
            aggregate_signals_for_payload,
            build_signal_summary,
        )
        by_id_sg = {m.get("match_id"): m for m in matches_payload}
        bucket_lists: list[list[dict]] = [
            picks, disc_mot, disc_mkt, incomp,
            rescued_picks, watchlist, protected_acc,
        ]
        all_signals_by_match: dict[str, list[dict]] = {}
        for bucket in bucket_lists:
            for entry in bucket:
                if not isinstance(entry, dict):
                    continue
                mid = entry.get("match_id")
                src = by_id_sg.get(mid) or {}
                # Merge the source match fields the extractors need with
                # whatever the entry already carries (trap_signals etc.).
                merged_payload = {
                    **{k: v for k, v in src.items() if k.startswith("_") or k in (
                        "editorial_context", "_editorial_context",
                        "_basketball_pace_form", "_baseball_stats",
                        "_encounter_history", "_form_guard",
                    )},
                    **{k: v for k, v in entry.items()},
                }
                signals = aggregate_signals_for_payload(merged_payload, sport)
                # Propagate external_source_evidence into the entry from the
                # source match payload (so the UI can render it without
                # cross-referencing matches_payload).
                ext_ev = src.get("external_source_evidence") or []
                if ext_ev:
                    entry["external_source_evidence"] = ext_ev
                    # Cross-confirm: when an editorial signal matches a
                    # bullet from an external source, boost its confidence
                    # +10 and tag it with `source` so the UI can render
                    # "confirmado por scores24/fotmob".
                    _cross_confirm_signals(signals, ext_ev)
                    # Mark which sources ended up tagged as used.
                    used_codes = {s.get("code") for s in signals if s.get("source")}
                    for ev in ext_ev:
                        if ev.get("status") == "ok" and (used_codes or ev.get("extracted_data")):
                            ev["used_in_analysis"] = True
                entry["editorial_context_signals"] = signals
                if signals and mid is not None:
                    all_signals_by_match[str(mid)] = signals
        summary["editorial_signal_summary"] = build_signal_summary(all_signals_by_match)
        parsed.setdefault("_pipeline", {})["editorial_signal_aggregation"] = {
            "entries_annotated": sum(1 for k in all_signals_by_match),
            "total_signals":      summary["editorial_signal_summary"]["total_signals"],
        }
    except Exception as exc:
        log.warning("signal_aggregator Phase 13 failed: %s", exc)

    # ── Phase 13.5 — possible_alternative_markets ───────────────────────
    # Sport-aware suggestions for every discarded entry so the user can
    # review manually. Fail-soft; never raises.
    try:
        from .possible_alternative_markets import attach_alternatives_to_summary
        annotated = attach_alternatives_to_summary(summary, sport)
        parsed.setdefault("_pipeline", {})["possible_alternative_markets"] = {
            "entries_annotated": annotated,
        }
    except Exception as exc:
        log.warning("possible_alternative_markets Phase 13.5 failed: %s", exc)

    # ── Phase 13.6 — Football Market Trace (V4: explicit per-market audit) ─
    # For each discarded football entry, expose an explicit `market_trace`
    # (market, selection, team_side, odds, estimated_probability,
    # implied_probability, edge, fragility, confidence, rejection_code,
    # rejection_reason, fragility_drivers) + a `markets_checked` list and
    # a `card_header`. Other sports (basketball/baseball) are also
    # annotated for free since the function is sport-aware — the UI
    # currently gates by sport === 'football' so non-football payloads
    # remain inert.
    try:
        from .football_market_trace import attach_market_trace_to_summary
        trace_res = attach_market_trace_to_summary(summary, sport=sport)
        parsed.setdefault("_pipeline", {})["football_market_trace"] = trace_res
    except Exception as exc:
        log.warning("football_market_trace Phase 13.6 failed: %s", exc)

    parsed["_generated_at"] = datetime.now(timezone.utc).isoformat()
    parsed["_session_id"] = session_id
    parsed["_provider"] = provider_used
    parsed["_sport"] = sport
    # Merge editorial_signal_aggregation into pipeline_meta instead of overwriting
    pipeline_meta.update(parsed.get("_pipeline", {}))
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
