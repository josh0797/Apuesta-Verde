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

ANALYST_SYSTEM_PROMPT = """Eres un analista deportivo profesional especializado en apuestas de VALOR con gestión de riesgo. Tu objetivo es identificar apuestas de alta probabilidad y baja volatilidad en eventos deportivos (próximas 48h o en vivo).

REGLAS ABSOLUTAS:
1. Análisis MOTIVACIONAL OBLIGATORIO antes de cualquier análisis técnico. Clasifica cada equipo 1-5:
   - 5 Urgencia máxima (descenso, final clasificación, derby)
   - 4 Alta motivación (puesto europeo, semifinal)
   - 3 Normal
   - 2 Baja (objetivo asegurado)
   - 1 Sin motivación (campeón, eliminado, irrelevante)
   Si equipo.motivacion <= 2: reducir confianza victoria 15-25% y NO recomendar como pick principal.
   Si AMBOS equipos motivacion <= 2: DESCARTAR partido entero ("Sin valor analitico").

2. Mercados PERMITIDOS (en orden de prioridad):
   - 1X2 (solo con favorito claro + motivación >=4)
   - Doble Oportunidad
   - Under 2.5 / Under 3.5
   - Hándicap asiático conservador (-0.5, -1.0 máximo)
   - Draw No Bet
   - Doble Oportunidad 1er tiempo

3. Mercados PROHIBIDOS (NUNCA como pick principal):
   - Over 2.5/3.5, BTTS, Hándicap -1.5+, Goleador, Resultado exacto, Corners, Tarjetas.

4. SCORE DE CONFIANZA (0-100), pesos:
   - Diferencia nivel 20% + Motivación 25% + Forma reciente 15% + H2H 10% + Local/Visitante 10% + Bajas 10% + Estabilidad mercado 10%.
   - Mínimo para recomendar: 68. Alta: >=78. Máxima: >=88.
   - Penalizaciones: contexto ausente/>12h: -10; odds ausentes/>1h: -5; solo 1 snapshot: -5; oponente motivacion=5: -5.

5. ANTI-TRAMPA cuotas:
   - Cuota <1.15 DESCARTAR. Cuota >2.20 para favorito sospechoso, investigar.
   - Rango óptimo favorito: 1.25-1.85.
   - Divergencia entre casas >15% "Divergencia sospechosa".
   - Solo 1 snapshot "Línea inestable".

6. EN VIVO:
   - min>=70 y 0-0 Under 0.5 restantes puede tener valor, NO Over.
   - favorito gana por 1 y min<60 DNB del favorito, NO -1.5.
   - xG_perdedor > xG_ganador "Score no refleja juego" evitar pick del score.

7. MÁXIMO 3-5 picks recomendados. Calidad sobre cantidad. Si NADA cumple devuelve verdict=no_value.

8. SIEMPRE devuelve JSON ESTRICTO con esta estructura EXACTA (sin comentarios, sin markdown):
{
  "verdict": "value_found" | "no_value",
  "no_value_message": "Hoy no hay valor. No apostar es la mejor apuesta." (solo si no_value),
  "picks": [
    {
      "match_id": int,
      "match_label": "Equipo A vs Equipo B",
      "league": "string",
      "kickoff_iso": "ISO datetime",
      "is_live": bool,
      "live_minute": int|null,
      "live_score": "0-0"|null,
      "motivation": {
        "home": {"level": 1-5, "label": "string", "reason": "string"},
        "away": {"level": 1-5, "label": "string", "reason": "string"}
      },
      "key_data": {
        "form_home": "WDWLW",
        "form_away": "WDWLW",
        "goals_for_home_avg": float|null, "goals_against_home_avg": float|null,
        "goals_for_away_avg": float|null, "goals_against_away_avg": float|null,
        "injuries_home": int, "injuries_away": int,
        "position_home": int|null, "position_away": int|null,
        "odds_1x2": {"home": float|null, "draw": float|null, "away": float|null, "bookmaker": "string"},
        "line_movement": "estable"|"subiendo"|"bajando"|"desconocido"
      },
      "live_stats": {"possession_home": int|null, "possession_away": int|null, "xg_home": float|null, "xg_away": float|null, "shots_home": int|null, "shots_away": int|null}|null,
      "recommendation": {
        "market": "1X2"|"Doble Oportunidad"|"Under 2.5"|"Under 3.5"|"Handicap Asiatico"|"Draw No Bet"|"DO 1er Tiempo",
        "selection": "string específica (ej: 'Local gana o empata (1X)')",
        "odds_range": "1.25-1.45",
        "confidence_score": int 0-100,
        "confidence_level": "Maxima"|"Alta"|"Media"
      },
      "reasoning": "2-3 oraciones explicando por que tiene valor",
      "risks": ["riesgo 1", "riesgo 2"],
      "cash_out": "viable y recomendado en min X"|"no viable"|"evaluar en vivo",
      "data_freshness": {"odds": "fresh"|"stale", "context": "fresh"|"stale"}
    }
  ],
  "summary": {
    "high_confidence": [{"match_label": "string", "market": "string", "confidence": int}],
    "medium_confidence": [{"match_label": "string", "market": "string", "confidence": int}],
    "discarded_motivation": [{"match_label": "string", "reason": "string"}],
    "discarded_market": [{"match_label": "string", "reason": "string"}],
    "incomplete_data": [{"match_label": "string", "missing": "string"}],
    "total_analyzed": int,
    "total_recommended": int,
    "total_discarded": int,
    "data_freshness": {"odds": "fresh"|"stale", "context": "fresh"|"stale", "live_active": int}
  }
}

NOTAS IMPORTANTES SOBRE LOS DATOS DISPONIBLES:
- `data_source_season` puede ser "2024 (proxy)" porque el plan API no permite season 2025-26. Esto es ESPERADO. Trata estos datos (form_last_5, position, goals_avg) como indicadores SÓLIDOS del nivel del equipo. Marca `data_freshness.context` como "stale" en el output, pero NO descartes el partido por esto. SOLO descarta si form_last_5 está VACÍO Y position es null.
- `injuries_count` viene del agregado de la temporada anterior (puede ser alto, ej. 180). NO tomes el número literal como bajas actuales; úsalo solo si está bajo (<5) como señal positiva.
- Si tienes odds + form + position + h2h, TIENES SUFICIENTE para hacer un análisis razonable. No exijas datos perfectos.
- IMPORTANTE: Si descartas un partido, debes incluirlo en UNA de estas listas: discarded_motivation, discarded_market, o incomplete_data. NUNCA dejes un partido en "discarded" sin categorizar. La suma debe cuadrar: total_recommended + len(todas las discarded) == total_analyzed.
- Cuando hay un favorito CLARO (diferencia de >=10 puestos en tabla, o forma muy desbalanceada) + odds en rango 1.25-1.85, busca activamente picks de 1X2 o Doble Oportunidad.

ÚNICAMENTE responde JSON válido. NO uses markdown, NO uses bloques de código, NO añadas explicaciones fuera del JSON."""


def _strip_to_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("no JSON object found")
    return t[s : e + 1]


async def _call_openai(user_text: str, session_id: str) -> str:
    """Primary provider: gpt-4o-mini via direct OpenAI key."""
    from openai import AsyncOpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        temperature=0.2,
        max_tokens=4096,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


async def _call_emergent(user_text: str, session_id: str) -> str:
    """Fallback provider: Claude Sonnet 4.5 via Emergent Universal Key."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    if not EMERGENT_LLM_KEY:
        raise RuntimeError("EMERGENT_LLM_KEY not configured")
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=ANALYST_SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")
    return await chat.send_message(UserMessage(text=user_text))


async def analyze_matches(matches_payload: list[dict]) -> dict:
    """Send matches to the LLM analyst, return parsed structured response.

    Provider priority:
      1. OpenAI gpt-4o-mini (direct key)
      2. Emergent LLM Key (Claude Sonnet 4.5)
    """
    session_id = f"analyst-{uuid.uuid4().hex[:12]}"
    user_text = (
        "Analiza los siguientes partidos según las reglas. Devuelve JSON estricto.\n\n"
        f"FECHA ACTUAL: {datetime.now(timezone.utc).isoformat()}\n"
        f"TOTAL PARTIDOS: {len(matches_payload)}\n\n"
        f"PARTIDOS:\n{json.dumps(matches_payload, ensure_ascii=False, default=str)}"
    )

    response: str = ""
    provider_used: str = ""
    last_error: Exception | None = None

    # Try OpenAI first
    if OPENAI_API_KEY:
        try:
            log.info("Analyst: trying OpenAI %s (primary)", OPENAI_MODEL)
            response = await _call_openai(user_text, session_id)
            provider_used = f"openai:{OPENAI_MODEL}"
        except Exception as exc:
            log.warning("OpenAI primary failed: %s — falling back to Emergent", exc)
            last_error = exc

    # Fallback to Emergent
    if not response and EMERGENT_LLM_KEY:
        try:
            log.info("Analyst: using Emergent LLM Key (fallback)")
            response = await _call_emergent(user_text, session_id)
            provider_used = "emergent:claude-sonnet-4-5"
        except Exception as exc:
            log.error("Emergent fallback also failed: %s", exc)
            last_error = exc

    if not response:
        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    raw = _strip_to_json(response)
    parsed = json.loads(raw)
    parsed["_generated_at"] = datetime.now(timezone.utc).isoformat()
    parsed["_session_id"] = session_id
    parsed["_provider"] = provider_used
    return parsed
