"""LLM Analyst Engine.

Wraps the betting-analyst persona (full Spanish system prompt) around
Claude Sonnet 4.5 via Emergent Universal Key. Parses strict JSON output.
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
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


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


def _build_system_prompt(sport: str) -> str:
    sport_rules = SPORT_RULES.get(sport, SPORT_RULES["football"])
    return f"""Eres un analista deportivo profesional especializado en apuestas de VALOR con gestión de riesgo. Tu objetivo es identificar apuestas de alta probabilidad y baja volatilidad en eventos deportivos (próximas 48h o en vivo).

DEPORTE A ANALIZAR: {sport.upper()}

{sport_rules}

REGLAS GENERALES (todos los deportes):
1. Análisis MOTIVACIONAL OBLIGATORIO antes de cualquier análisis técnico. Clasifica cada equipo 1-5:
   - 5 Urgencia máxima (descenso, playoffs, final clasificación)
   - 4 Alta motivación (puesto playoffs, semifinal)
   - 3 Normal
   - 2 Baja (objetivo asegurado, ya eliminado pero juega por dignidad)
   - 1 Sin motivación (campeón, eliminado, tanking en NBA, descansando titulares)

   Si equipo.motivacion <= 2: reducir confianza victoria 15-25% y NO recomendar como pick principal.
   Si AMBOS equipos motivacion <= 2: DESCARTAR partido entero.

2. SCORE DE CONFIANZA (0-100), pesos:
   - Diferencia nivel 20% + Motivación 25% + Forma reciente 15% + H2H 10% + Local/Visitante 10% + Bajas 10% + Estabilidad mercado 10%.
   - Mínimo para recomendar: 60 (modo MODERADO). Media: 60-69. Alta: 70-79. Máxima: >=80.
   - Penalizaciones: contexto ausente/>12h: -10; odds ausentes/>1h: -5; solo 1 snapshot: -5; oponente motivacion=5: -5.

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
        "home": {{"level": 1-5, "label": "string", "reason": "string"}},
        "away": {{"level": 1-5, "label": "string", "reason": "string"}}
      }},
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
    "discarded_motivation": [{{"match_id": (int|string), "match_label": "string", "reason": "string"}}],
    "discarded_market": [{{"match_id": (int|string), "match_label": "string", "reason": "string"}}],
    "incomplete_data": [{{"match_id": (int|string), "match_label": "string", "missing": "string"}}],
    "total_analyzed": int,
    "total_recommended": int,
    "total_discarded": int,
    "data_freshness": {{"odds": "fresh"|"stale", "context": "fresh"|"stale", "live_active": int}}
  }}
}}

NOTAS IMPORTANTES SOBRE LOS DATOS DISPONIBLES:
- `data_source_season` puede ser "2024 (proxy)" porque el plan API no permite season actual. Esto es ESPERADO. Trata estos datos (form_last_5, position, wins/losses) como indicadores SÓLIDOS. Marca context como "stale" pero NO descartes por esto.
- Si tienes odds + position + h2h, TIENES SUFICIENTE para hacer un análisis razonable.

REGLA CRÍTICA DE CATEGORIZACIÓN (NO NEGOCIABLE):
TODO partido analizado DEBE aparecer en EXACTAMENTE UNA de estas listas:
  - `picks` (si lo recomiendas)
  - `summary.discarded_motivation`
  - `summary.discarded_market`
  - `summary.incomplete_data`

VALIDACIÓN: len(picks) + len(discarded_motivation) + len(discarded_market) + len(incomplete_data) === total_analyzed.

NUNCA dejes las listas de descarte vacías cuando total_discarded > 0. Cada partido descartado debe explicarse con su razón concreta.

ÚNICAMENTE responde JSON válido. NO uses markdown, NO uses bloques de código, NO añadas explicaciones fuera del JSON."""


# Backward-compat alias used by older imports. Always rebuild per-sport at runtime.
ANALYST_SYSTEM_PROMPT = _build_system_prompt("football")


def _strip_to_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("no JSON object found")
    return t[s : e + 1]


async def _call_openai(user_text: str, session_id: str, system_prompt: str) -> str:
    """Primary provider: gpt-4o-mini via direct OpenAI key."""
    from openai import AsyncOpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
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


async def analyze_matches(matches_payload: list[dict], sport: str = "football") -> dict:
    """Send matches to the LLM analyst, return parsed structured response.

    Args:
      matches_payload: compact match dicts (output of normalizer.summarize_match_for_llm)
      sport: one of "football" | "basketball" | "baseball" — drives the system prompt
             rules (markets, motivation cues, anti-trap thresholds).

    Provider priority:
      1. OpenAI gpt-4o-mini (direct key)
      2. Emergent LLM Key (Claude Sonnet 4.5)
    """
    sport = (sport or "football").lower()
    if sport not in SPORT_RULES:
        sport = "football"
    system_prompt = _build_system_prompt(sport)
    session_id = f"analyst-{sport}-{uuid.uuid4().hex[:12]}"
    user_text = (
        f"Analiza los siguientes partidos de {sport.upper()} según las reglas. Devuelve JSON estricto.\n\n"
        f"FECHA ACTUAL: {datetime.now(timezone.utc).isoformat()}\n"
        f"DEPORTE: {sport}\n"
        f"TOTAL PARTIDOS: {len(matches_payload)}\n\n"
        f"PARTIDOS:\n{json.dumps(matches_payload, ensure_ascii=False, default=str)}"
    )

    response: str = ""
    provider_used: str = ""
    last_error: Exception | None = None

    # Try OpenAI first
    if OPENAI_API_KEY:
        try:
            log.info("Analyst[%s]: trying OpenAI %s (primary)", sport, OPENAI_MODEL)
            response = await _call_openai(user_text, session_id, system_prompt)
            provider_used = f"openai:{OPENAI_MODEL}"
        except Exception as exc:
            log.warning("OpenAI primary failed: %s — falling back to Emergent", exc)
            last_error = exc

    # Fallback to Emergent
    if not response and EMERGENT_LLM_KEY:
        try:
            log.info("Analyst[%s]: using Emergent LLM Key (fallback)", sport)
            response = await _call_emergent(user_text, session_id, system_prompt)
            provider_used = "emergent:claude-sonnet-4-5"
        except Exception as exc:
            log.error("Emergent fallback also failed: %s", exc)
            last_error = exc

    if not response:
        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    raw = _strip_to_json(response)
    parsed = json.loads(raw)

    # Reconciliation: if total_discarded > 0 but all discard lists are empty,
    # auto-fill incomplete_data with the input matches that did NOT make it to picks.
    summary = parsed.get("summary") or {}
    picks = parsed.get("picks") or []
    picked_ids = {p.get("match_id") for p in picks}
    disc_mot = summary.get("discarded_motivation") or []
    disc_mkt = summary.get("discarded_market") or []
    incomp = summary.get("incomplete_data") or []
    total_listed = len(picks) + len(disc_mot) + len(disc_mkt) + len(incomp)
    expected = summary.get("total_analyzed") or len(matches_payload)
    if total_listed < expected:
        # Build a missing-matches fallback list using the original payload
        existing_in_lists = {x.get("match_id") for x in (disc_mot + disc_mkt + incomp)}
        for m in matches_payload:
            mid = m.get("match_id")
            if mid in picked_ids or mid in existing_in_lists:
                continue
            label = f"{(m.get('home_team') or {}).get('name','?')} vs {(m.get('away_team') or {}).get('name','?')}"
            # Heuristic: if no odds → incomplete; if form missing on both → incomplete; else market
            home_ctx = (m.get('home_team') or {}).get('context') or {}
            away_ctx = (m.get('away_team') or {}).get('context') or {}
            has_odds = bool(m.get('odds_snapshots'))
            has_form = bool(home_ctx.get('form_last_5')) or bool(away_ctx.get('form_last_5'))
            if not has_odds:
                incomp.append({"match_id": mid, "match_label": label, "missing": "Sin cuotas disponibles"})
            elif not has_form:
                incomp.append({"match_id": mid, "match_label": label, "missing": "Sin forma reciente ni posición"})
            else:
                disc_mkt.append({"match_id": mid, "match_label": label, "reason": "No cumple criterios de valor (cuotas/motivación insuficientes para mercados protegidos)"})
        summary["discarded_motivation"] = disc_mot
        summary["discarded_market"] = disc_mkt
        summary["incomplete_data"] = incomp
        summary["total_discarded"] = len(disc_mot) + len(disc_mkt) + len(incomp)
        parsed["summary"] = summary

    parsed["_generated_at"] = datetime.now(timezone.utc).isoformat()
    parsed["_session_id"] = session_id
    parsed["_provider"] = provider_used
    parsed["_sport"] = sport
    return parsed
