"""
POC: Value Bet Intelligence — Core Workflow Test
=================================================
Validates end-to-end pipeline:
  1) Fetch fixtures (next 48h) from API-Football
  2) Fetch odds + team statistics + standings
  3) Normalize into 3-layer schema (odds_snapshots, team_context, live_stats)
  4) Run LLM analyst (Claude Sonnet 4.5 via Emergent Universal Key)
  5) Validate strict JSON output (picks or "no value" message)
  6) Smoke-test scraping fallback (ESPN public scoreboard)

Run: python /app/poc/test_core.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import re
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

# Load env from backend .env
load_dotenv("/app/backend/.env")

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "441843e6941ad8326973da1f9acea5a0")
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
API_BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

# ANSI colors
GREEN, RED, YELLOW, BLUE, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[94m", "\033[0m"


def log(msg: str, lvl: str = "INFO") -> None:
    color = {"OK": GREEN, "ERR": RED, "WARN": YELLOW, "INFO": BLUE}.get(lvl, "")
    print(f"{color}[{lvl}]{RESET} {msg}")


# ──────────────────────────────────────────────────────────────────────────────
# 1) API-Football client
# ──────────────────────────────────────────────────────────────────────────────
async def api_get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    r = await client.get(url, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        # API-Football returns errors as dict or list
        errs = data["errors"]
        if errs and (isinstance(errs, dict) and errs or isinstance(errs, list) and errs):
            log(f"API-Football errors for {path}: {errs}", "WARN")
    return data


async def fetch_fixtures_next_48h(client: httpx.AsyncClient) -> list[dict]:
    """Fetch fixtures starting from now up to +48h (status NS = Not Started)."""
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    fixtures = []
    for d in [today, tomorrow]:
        data = await api_get(client, "/fixtures", {"date": d.isoformat()})
        fixtures.extend(data.get("response", []))
    # Keep only upcoming within 48h
    cutoff = datetime.now(timezone.utc) + timedelta(hours=48)
    upcoming = []
    for f in fixtures:
        try:
            ts = f["fixture"]["timestamp"]
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            status = f["fixture"]["status"]["short"]
            if status in ("NS", "TBD") and datetime.now(timezone.utc) <= dt <= cutoff:
                upcoming.append(f)
        except Exception:
            pass
    return upcoming


async def fetch_live_fixtures(client: httpx.AsyncClient) -> list[dict]:
    data = await api_get(client, "/fixtures", {"live": "all"})
    return data.get("response", [])


async def fetch_odds(client: httpx.AsyncClient, fixture_id: int) -> list[dict]:
    """Fetch odds (multi-bookmaker) for a fixture."""
    data = await api_get(client, "/odds", {"fixture": fixture_id})
    return data.get("response", [])


async def fetch_team_statistics(client: httpx.AsyncClient, team_id: int, league_id: int, season: int) -> dict:
    data = await api_get(
        client,
        "/teams/statistics",
        {"team": team_id, "league": league_id, "season": season},
    )
    return data.get("response", {}) or {}


async def fetch_standings(client: httpx.AsyncClient, league_id: int, season: int) -> list[dict]:
    data = await api_get(client, "/standings", {"league": league_id, "season": season})
    return data.get("response", [])


# ──────────────────────────────────────────────────────────────────────────────
# 2) Normalization to 3-layer schema
# ──────────────────────────────────────────────────────────────────────────────
def normalize_odds(odds_response: list[dict]) -> dict:
    """Convert API-Football odds into our odds_snapshot schema."""
    if not odds_response:
        return {"available": False, "markets": {}, "bookmakers": [], "snapshot_at": datetime.now(timezone.utc).isoformat()}

    item = odds_response[0]
    bookmakers_data = item.get("bookmakers", [])
    markets: dict[str, list[dict]] = {"1X2": [], "Over/Under": [], "BTTS": [], "Handicap": []}
    bookmaker_names = []
    for bm in bookmakers_data:
        bm_name = bm.get("name", "Unknown")
        bookmaker_names.append(bm_name)
        for bet in bm.get("bets", []):
            bname = bet.get("name", "")
            values = bet.get("values", [])
            if bname == "Match Winner":
                row = {"bookmaker": bm_name}
                for v in values:
                    if v["value"] == "Home":
                        row["home"] = float(v["odd"])
                    elif v["value"] == "Draw":
                        row["draw"] = float(v["odd"])
                    elif v["value"] == "Away":
                        row["away"] = float(v["odd"])
                markets["1X2"].append(row)
            elif bname in ("Goals Over/Under", "Over/Under"):
                row = {"bookmaker": bm_name, "lines": {}}
                for v in values:
                    row["lines"][v["value"]] = float(v["odd"])
                markets["Over/Under"].append(row)
            elif bname in ("Both Teams Score", "Both Teams To Score"):
                row = {"bookmaker": bm_name}
                for v in values:
                    row[v["value"].lower()] = float(v["odd"])
                markets["BTTS"].append(row)
            elif bname == "Asian Handicap":
                row = {"bookmaker": bm_name, "lines": []}
                for v in values:
                    row["lines"].append({"value": v["value"], "odd": float(v["odd"])})
                markets["Handicap"].append(row)
    return {
        "available": True,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "bookmakers": bookmaker_names,
        "markets": markets,
    }


def normalize_team_context(stats: dict, standings: list[dict], team_id: int) -> dict:
    """Extract form, goals, position from API-Football team statistics + standings."""
    ctx: dict[str, Any] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "form_last_5": "",
        "goals_for": 0,
        "goals_against": 0,
        "injuries_count": 0,
        "suspensions_count": 0,
        "position": None,
        "points": None,
        "league_stage": "regular",
        "motivation_flags": {
            "already_champion": False,
            "relegated": False,
            "nothing_to_play_for": False,
        },
    }
    if stats:
        form = stats.get("form") or ""
        # API returns full form string e.g. "WDWLWWDL"; keep last 5
        ctx["form_last_5"] = form[-5:] if form else ""
        goals = stats.get("goals", {})
        ctx["goals_for"] = (goals.get("for", {}) or {}).get("total", {}).get("total", 0) or 0
        ctx["goals_against"] = (goals.get("against", {}) or {}).get("total", {}).get("total", 0) or 0
    # Standings: find this team
    try:
        if standings:
            league = standings[0]["league"]
            for group in league.get("standings", []):
                for row in group:
                    if row["team"]["id"] == team_id:
                        ctx["position"] = row.get("rank")
                        ctx["points"] = row.get("points")
                        desc = (row.get("description") or "").lower()
                        if "champion" in desc or "promotion" in desc:
                            pass  # not yet champion necessarily, but elite
                        if "relegation" in desc:
                            ctx["motivation_flags"]["relegated"] = False  # only if confirmed
                        break
    except Exception:
        pass
    return ctx


def normalize_live_stats(fixture: dict) -> dict | None:
    fx = fixture.get("fixture", {})
    status = fx.get("status", {})
    short = status.get("short")
    if short not in ("1H", "2H", "HT", "ET", "P", "LIVE"):
        return None
    goals = fixture.get("goals", {})
    stats_list = fixture.get("statistics", [])  # may be empty
    home_stats: dict[str, Any] = {}
    away_stats: dict[str, Any] = {}
    for side in stats_list:
        team_id = side.get("team", {}).get("id")
        bucket = {}
        for s in side.get("statistics", []):
            bucket[s.get("type")] = s.get("value")
        if not home_stats:
            home_stats = bucket
        else:
            away_stats = bucket
    return {
        "minute": status.get("elapsed"),
        "status": short,
        "score": {"home": goals.get("home"), "away": goals.get("away")},
        "home_stats": home_stats,
        "away_stats": away_stats,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 3) LLM Analyst (Claude Sonnet 4.5 via Emergent)
# ──────────────────────────────────────────────────────────────────────────────
ANALYST_SYSTEM_PROMPT = """Eres un analista deportivo profesional especializado en apuestas de VALOR con gestión de riesgo. Tu objetivo es identificar apuestas de alta probabilidad y baja volatilidad en eventos deportivos (próximas 48h o en vivo).

REGLAS ABSOLUTAS:
1. Análisis MOTIVACIONAL OBLIGATORIO antes de cualquier análisis técnico. Clasifica cada equipo 1-5:
   - 5 🔴 Urgencia máxima (descenso, final clasificación)
   - 4 🟠 Alta motivación (puesto europeo, semifinal)
   - 3 🟡 Normal
   - 2 🟢 Baja (objetivo asegurado)
   - 1 ⚪ Sin motivación (campeón, eliminado)
   Si equipo.motivacion <= 2: reducir confianza victoria 15-25% y NO recomendar como pick principal.
   Si AMBOS equipos motivacion <= 2: DESCARTAR partido entero ("🚫 Sin valor analítico").

2. Mercados PERMITIDOS (en orden de prioridad):
   ✅ 1X2 (solo con favorito claro + motivación >=4)
   ✅ Doble Oportunidad
   ✅ Under 2.5 / Under 3.5
   ✅ Hándicap asiático conservador (-0.5, -1.0 máximo)
   ✅ Draw No Bet
   ✅ Doble Oportunidad 1er tiempo

3. Mercados PROHIBIDOS (NUNCA como pick principal):
   ❌ Over 2.5/3.5, BTTS, Hándicap -1.5+, Goleador, Resultado exacto, Corners, Tarjetas.

4. SCORE DE CONFIANZA (0-100), pesos:
   - Diferencia nivel 20% + Motivación 25% + Forma reciente 15% + H2H 10% + Local/Visitante 10% + Bajas 10% + Estabilidad mercado 10%.
   - Mínimo para recomendar: 68. Alta: >=78. Máxima: >=88.
   - Penalizaciones: contexto ausente/>12h: -10; odds ausentes/>1h: -5; solo 1 snapshot: -5; oponente motivacion=5: -5.

5. ANTI-TRAMPA cuotas:
   - Cuota <1.15 → DESCARTAR. Cuota >2.20 para favorito → sospechoso, investigar.
   - Rango óptimo favorito: 1.25–1.85.
   - Si divergencia entre casas >15% → "⚠️ Divergencia sospechosa".
   - Si solo 1 snapshot → "⚠️ Línea inestable".

6. EN VIVO:
   - min>=70 y 0-0 → Under 0.5 restantes puede tener valor, NO Over.
   - favorito gana por 1 y min<60 → DNB del favorito, NO -1.5.
   - xG_perdedor > xG_ganador → "⚠️ Score no refleja juego" → evitar pick del score.

7. MÁXIMO 3-5 picks recomendados. Calidad sobre cantidad. Si NADA cumple → "Hoy no hay valor. No apostar es la mejor apuesta."

8. SIEMPRE devuelve JSON ESTRICTO con esta estructura EXACTA (sin comentarios, sin markdown):
{
  "verdict": "value_found" | "no_value",
  "no_value_message": "Hoy no hay valor. No apostar es la mejor apuesta." (solo si verdict=no_value),
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
        "goals_for_home": int, "goals_against_home": int,
        "goals_for_away": int, "goals_against_away": int,
        "injuries_home": int, "injuries_away": int,
        "odds_1x2": {"home": float|null, "draw": float|null, "away": float|null, "bookmaker": "string"},
        "line_movement": "estable"|"subiendo"|"bajando"|"desconocido"
      },
      "live_stats": {"possession_home": int|null, "possession_away": int|null, "xg_home": float|null, "xg_away": float|null, "shots_home": int|null, "shots_away": int|null}|null,
      "recommendation": {
        "market": "1X2"|"Doble Oportunidad"|"Under 2.5"|"Under 3.5"|"Hándicap Asiático"|"Draw No Bet"|"DO 1er Tiempo",
        "selection": "string específica (ej: 'Local gana o empata (1X)')",
        "odds_range": "1.25–1.45",
        "confidence_score": int 0-100,
        "confidence_level": "Máxima"|"Alta"|"Media"
      },
      "reasoning": "2-3 oraciones explicando por qué tiene valor",
      "risks": ["riesgo 1", "riesgo 2"],
      "cash_out": "viable y recomendado en min X"|"no viable"|"evaluar en vivo"
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

ÚNICAMENTE responde JSON válido. NO uses markdown, NO uses bloques de código, NO añadas explicaciones fuera del JSON."""


async def run_llm_analysis(matches_payload: list[dict], session_id: str = "poc-test") -> dict:
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=ANALYST_SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")

    user_text = (
        "Analiza los siguientes partidos según las reglas. Devuelve JSON estricto.\n\n"
        f"PARTIDOS ({len(matches_payload)}):\n"
        + json.dumps(matches_payload, ensure_ascii=False, default=str)
    )
    msg = UserMessage(text=user_text)
    response = await chat.send_message(msg)
    return response


def parse_llm_json(raw: str) -> dict:
    """Robustly extract JSON object from LLM response."""
    text = raw.strip()
    # strip markdown fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(text[start : end + 1])


# ──────────────────────────────────────────────────────────────────────────────
# 4) Fallback: ESPN public scoreboard scrape (smoke test)
# ──────────────────────────────────────────────────────────────────────────────
async def fallback_scrape_espn(client: httpx.AsyncClient) -> list[dict]:
    """Smoke test: ESPN soccer scoreboard JSON endpoint (public)."""
    url = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard"
    r = await client.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    events = data.get("events", []) or []
    matches = []
    for ev in events[:10]:
        name = ev.get("name") or ev.get("shortName", "")
        date = ev.get("date")
        comp = ev.get("competitions", [{}])[0]
        status = (comp.get("status", {}) or {}).get("type", {}).get("description", "")
        matches.append({"name": name, "date": date, "status": status, "source": "espn_fallback"})
    return matches


# ──────────────────────────────────────────────────────────────────────────────
# 5) MAIN POC FLOW
# ──────────────────────────────────────────────────────────────────────────────
async def main() -> int:
    print("\n" + "=" * 70)
    print("  VALUE BET INTELLIGENCE — Core POC")
    print("=" * 70 + "\n")

    if not EMERGENT_LLM_KEY:
        log("EMERGENT_LLM_KEY missing", "ERR")
        return 1
    if not API_FOOTBALL_KEY:
        log("API_FOOTBALL_KEY missing", "ERR")
        return 1

    results = {
        "api_football_fixtures_ok": False,
        "api_football_odds_ok": False,
        "api_football_stats_ok": False,
        "normalization_ok": False,
        "llm_call_ok": False,
        "llm_json_parse_ok": False,
        "llm_output_valid": False,
        "fallback_scrape_ok": False,
    }

    async with httpx.AsyncClient() as client:
        # ── Step A: Fetch upcoming fixtures
        log("Step A: Fetching fixtures next 48h from API-Football…")
        try:
            upcoming = await fetch_fixtures_next_48h(client)
            log(f"  Got {len(upcoming)} upcoming fixtures", "OK")
            results["api_football_fixtures_ok"] = True
        except Exception as e:
            log(f"  Fixtures fetch failed: {e}", "ERR")
            traceback.print_exc()
            upcoming = []

        # ── Step A2: Fetch live fixtures
        log("Step A2: Fetching live fixtures…")
        try:
            live = await fetch_live_fixtures(client)
            log(f"  Got {len(live)} live fixtures", "OK")
        except Exception as e:
            log(f"  Live fetch failed: {e}", "WARN")
            live = []

        # Choose sample matches: prefer top leagues (filter by league id heuristic) and limit to 5
        TOP_LEAGUES = {39, 140, 135, 78, 61, 2, 3, 88, 94, 71, 128}  # EPL, LaLiga, Serie A, Bundes, Ligue1, UCL, UEL, Eredivisie, PrimeiraLiga, Brasileirao, LigaArg
        sample_pool = [f for f in upcoming if f["league"]["id"] in TOP_LEAGUES][:5]
        if not sample_pool:
            sample_pool = upcoming[:3]
        if not sample_pool and live:
            sample_pool = live[:2]
        log(f"Selected {len(sample_pool)} sample fixtures for deep analysis")

        # ── Step B: For each sample, fetch odds + team stats + standings
        normalized_matches = []
        for fx in sample_pool:
            fid = fx["fixture"]["id"]
            lid = fx["league"]["id"]
            season = fx["league"]["season"]
            home_id = fx["teams"]["home"]["id"]
            away_id = fx["teams"]["away"]["id"]
            home_name = fx["teams"]["home"]["name"]
            away_name = fx["teams"]["away"]["name"]
            league_name = fx["league"]["name"]
            kickoff = fx["fixture"]["date"]
            status_short = fx["fixture"]["status"]["short"]
            is_live = status_short in ("1H", "2H", "HT", "ET", "P", "LIVE")

            log(f"  → {home_name} vs {away_name} ({league_name})")
            try:
                odds = await fetch_odds(client, fid)
                results["api_football_odds_ok"] = True
            except Exception as e:
                log(f"    odds failed: {e}", "WARN")
                odds = []
            try:
                stats_home = await fetch_team_statistics(client, home_id, lid, season)
                stats_away = await fetch_team_statistics(client, away_id, lid, season)
                results["api_football_stats_ok"] = True
            except Exception as e:
                log(f"    stats failed: {e}", "WARN")
                stats_home, stats_away = {}, {}
            try:
                standings = await fetch_standings(client, lid, season)
            except Exception as e:
                log(f"    standings failed: {e}", "WARN")
                standings = []

            norm_odds = normalize_odds(odds)
            ctx_home = normalize_team_context(stats_home, standings, home_id)
            ctx_away = normalize_team_context(stats_away, standings, away_id)
            live_stats = normalize_live_stats(fx) if is_live else None

            normalized_matches.append({
                "match_id": fid,
                "league": league_name,
                "league_id": lid,
                "season": season,
                "kickoff_iso": kickoff,
                "is_live": is_live,
                "home_team": {"id": home_id, "name": home_name, "context": ctx_home},
                "away_team": {"id": away_id, "name": away_name, "context": ctx_away},
                "odds_snapshots": [norm_odds] if norm_odds["available"] else [],
                "live_stats": live_stats,
                "venue": fx.get("fixture", {}).get("venue", {}).get("name"),
            })
        if normalized_matches:
            results["normalization_ok"] = True

        log(f"Normalized {len(normalized_matches)} matches into 3-layer schema", "OK")

        # ── Step C: LLM analysis
        log("Step C: Calling LLM analyst (Claude Sonnet 4.5)…")
        if not normalized_matches:
            log("  No matches normalized — skipping LLM call but creating mock payload", "WARN")
            normalized_matches = []

        try:
            raw_response = await run_llm_analysis(normalized_matches[:5])
            results["llm_call_ok"] = True
            log("  LLM response received", "OK")
            print(f"\n--- LLM RAW (first 600 chars) ---\n{raw_response[:600]}\n--- ---\n")
        except Exception as e:
            log(f"  LLM call failed: {e}", "ERR")
            traceback.print_exc()
            raw_response = ""

        parsed = None
        if raw_response:
            try:
                parsed = parse_llm_json(raw_response)
                results["llm_json_parse_ok"] = True
                log("  JSON parsed successfully", "OK")
            except Exception as e:
                log(f"  JSON parse failed: {e}", "ERR")
                print(f"FULL RAW:\n{raw_response}\n")

        if parsed:
            verdict = parsed.get("verdict")
            if verdict in ("value_found", "no_value"):
                results["llm_output_valid"] = True
                log(f"  Verdict: {verdict}", "OK")
                if verdict == "value_found":
                    picks = parsed.get("picks", [])
                    log(f"  Picks count: {len(picks)}", "OK")
                    for p in picks[:3]:
                        print(f"     • {p.get('match_label')} → {p['recommendation']['market']}: {p['recommendation']['selection']} (conf {p['recommendation']['confidence_score']})")
                else:
                    log(f"  Message: {parsed.get('no_value_message')}", "INFO")
                summary = parsed.get("summary", {})
                print(f"  Summary: analyzed={summary.get('total_analyzed')} recommended={summary.get('total_recommended')} discarded={summary.get('total_discarded')}")
            else:
                log(f"  Invalid verdict: {verdict}", "ERR")

        # ── Step D: Fallback smoke test
        log("Step D: Testing fallback scraper (ESPN)…")
        try:
            fb = await fallback_scrape_espn(client)
            log(f"  Fallback got {len(fb)} matches", "OK")
            results["fallback_scrape_ok"] = True
            if fb:
                print(f"     First: {fb[0]}")
        except Exception as e:
            log(f"  Fallback failed: {e}", "WARN")

    # ── Final report
    print("\n" + "=" * 70)
    print("  POC RESULTS")
    print("=" * 70)
    for k, v in results.items():
        emoji = "✅" if v else "❌"
        print(f"  {emoji} {k}: {v}")

    critical = ["api_football_fixtures_ok", "api_football_odds_ok", "normalization_ok",
                "llm_call_ok", "llm_json_parse_ok", "llm_output_valid"]
    failed = [k for k in critical if not results[k]]
    if failed:
        log(f"CRITICAL FAILURES: {failed}", "ERR")
        return 2
    log("ALL CRITICAL CHECKS PASSED ✓", "OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
