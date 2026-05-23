"""Inject a mock football pick_run that exercises every Phase 8 state so the
Dashboard renders FootballQualityBadge + SkippedMatchRow with realistic data.
Run with:  python scripts/inject_phase8_mock.py
"""
import asyncio, os, sys, uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",  "test_database")
DEMO_EMAIL = "demo@valuebet.app"


def _fq(state, tier, score, liq=60, league="Premier League", priority=None, skip=None):
    return {
        "score": score,
        "state": state,
        "tier": tier,
        "tier_key": f"tier_{tier}" if tier <= 3 else None,
        "league_quality": {"score": score + 5, "factors": [f"Tier {tier}", "stats completas"], "tier": tier, "tier_key": f"tier_{tier}"},
        "market_liquidity": {"score": liq, "label": "alta" if liq >= 70 else "media" if liq >= 40 else "baja", "factors": ["8 bookmakers", "3 mercados activos"]},
        "priority_reason": priority,
        "skip_reason": skip,
        "is_exotic": tier == 4,
        "allowed_for_analysis": skip is None,
    }


def _pick(match_id, label, league, league_id, fq, confidence=78, selection="Manchester City gana", market="Resultado Final"):
    return {
        "match_id": match_id,
        "match_label": label,
        "league": league,
        "league_id": league_id,
        "kickoff_iso": "2026-05-23T18:00:00+00:00",
        "motivation_state": "HIGH_BOTH",
        "pressure_state": "NORMAL",
        "motivation": {
            "home": {"level": 4, "label": "Alta", "reason": "Lucha por el título"},
            "away": {"level": 4, "label": "Alta", "reason": "Necesita 3 puntos para Champions"},
        },
        "recommendation": {
            "market": market,
            "selection": selection,
            "odds": 1.85,
            "stake_units": 2,
            "confidence_score": confidence,
            "reasoning_es": "Modelo + xG y mercado coinciden. Edge claro detectado.",
        },
        "_football_quality": fq,
        "_market_edge": {"edge_pct": 4.2, "fair_odds": 1.78, "passes_guardrail": True},
        "_moneyball": {"ev_pct": 6.1, "kelly_stake": 1.5, "verdict": "VALUE"},
    }


def build_payload():
    return {
        "verdict": "value_found",
        "picks": [
            _pick(
                1001, "Manchester City vs Arsenal", "Premier League", 39,
                _fq("PRIORITY_MATCH", 1, 92, liq=88, priority="Liga top (Tier 1) + score 92/100. Cobertura y liquidez suficientes para análisis profundo."),
                confidence=84, selection="Manchester City gana",
            ),
            _pick(
                1002, "Real Madrid vs Barcelona", "La Liga", 140,
                _fq("PRIORITY_MATCH", 1, 89, liq=82, priority="Clásico — máxima liquidez y datos completos."),
                confidence=80, selection="Más de 2.5 goles", market="Total Goles",
            ),
            _pick(
                1003, "Inter vs Juventus", "Serie A", 135,
                _fq("HIGH_LIQUIDITY", 2, 76, liq=80, priority="Mercado muy líquido (80/100) — vale la pena aún fuera de Tier 1."),
                confidence=72, selection="Empate o Inter", market="Doble Oportunidad",
            ),
            _pick(
                1004, "PSV vs Ajax", "Eredivisie", 88,
                _fq("STANDARD", 2, 62, liq=55, priority="Match con datos y liquidez suficientes (score 62)."),
                confidence=66, selection="Ambos equipos marcan", market="BTTS",
            ),
        ],
        "summary": {
            "total_recommended": 4,
            "total_discarded": 3,
            "high_confidence": [
                {"match_id": 1001, "match_label": "Manchester City vs Arsenal", "market": "Resultado Final", "confidence": 84},
                {"match_id": 1002, "match_label": "Real Madrid vs Barcelona", "market": "Total Goles", "confidence": 80},
            ],
            "medium_confidence": [
                {"match_id": 1003, "match_label": "Inter vs Juventus", "market": "Doble Oportunidad", "confidence": 72},
                {"match_id": 1004, "match_label": "PSV vs Ajax", "market": "BTTS", "confidence": 66},
            ],
            "discarded_motivation": [
                {"match_id": 2001, "match_label": "Brighton vs Burnley", "reason": "Ambos sin motivación a 2 jornadas del final.", "motivation_state": "LOW_BOTH"},
            ],
            "discarded_market": [
                {"match_id": 2002, "match_label": "Bayern vs Stuttgart", "reason": "Cuotas con sobrecarga del 7%, no hay edge real."},
            ],
            "incomplete_data": [
                {"match_id": 2003, "match_label": "Atletico vs Sevilla", "missing": "Faltan alineaciones probables y stats xG."},
            ],
            "skipped_low_relevance": [
                {
                    "match_id": 3001,
                    "match_label": "Gaborone United vs Township Rollers",
                    "league": "Botswana Premier League",
                    "league_id": 561,
                    "tier": 4,
                    "state": "EXOTIC_LEAGUE_WARNING",
                    "score": 18,
                    "reason": "Liga fuera de la allowlist Tier 1/2/3 (Botswana Premier League). Datos y liquidez típicamente insuficientes; analizar solo en fallback.",
                },
                {
                    "match_id": 3002,
                    "match_label": "FC Minsk Reserves vs Dinamo Brest Reserves",
                    "league": "Belarus Reserve League",
                    "league_id": 9999,
                    "tier": 4,
                    "state": "EXOTIC_LEAGUE_WARNING",
                    "score": 12,
                    "reason": "Liga reserve/youth detectada — categoría siempre Tier 4.",
                },
                {
                    "match_id": 3003,
                    "match_label": "Defensor vs Cerro Largo",
                    "league": "Uruguay Segunda División",
                    "league_id": 12345,
                    "tier": 4,
                    "state": "LOW_MARKET_SUPPORT",
                    "score": 28,
                    "reason": "Liquidez baja (15/100): pocos books / mercados frágiles.",
                },
                {
                    "match_id": 3004,
                    "match_label": "Cracovia vs Lechia",
                    "league": "Ekstraklasa",
                    "league_id": 106,
                    "tier": 3,
                    "state": "LOW_DATA_QUALITY",
                    "score": 32,
                    "reason": "Calidad de liga baja (32/100): falta lineup/xG/stats.",
                },
            ],
        },
        "_pipeline": {
            "football_quality": {
                "ingested_total": 24,
                "analysable_total": 8,
                "selected_total": 4,
                "skipped_total": 4,
                "by_tier": {"1": 6, "2": 4, "3": 5, "4": 9},
                "by_state": {"PRIORITY_MATCH": 2, "HIGH_LIQUIDITY": 1, "STANDARD": 1, "EXOTIC_LEAGUE_WARNING": 2, "LOW_MARKET_SUPPORT": 1, "LOW_DATA_QUALITY": 1},
                "cascade_used": [1, 2],
                "target_count": 8,
                "tier_4_enabled": False,
            },
            "football_tier_reached": 2,
        },
    }


async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    user = await db.users.find_one({"email": DEMO_EMAIL})
    if not user:
        print(f"ERR: user {DEMO_EMAIL} not found in {DB_NAME}")
        return 1
    user_id = user["id"]
    record = {
        "id": uuid.uuid4().hex[:10],
        "user_id": user_id,
        "sport": "football",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matches_analyzed": 8,
        "payload": build_payload(),
        "_mock_phase8": True,
    }
    # Wipe any prior mock so /picks/today returns this one (most-recent wins).
    await db.picks.delete_many({"user_id": user_id, "_mock_phase8": True})
    await db.picks.insert_one(record)
    print(f"OK: inserted mock pick_run {record['id']} for {DEMO_EMAIL}")
    print(f"  picks={len(record['payload']['picks'])} skipped={len(record['payload']['summary']['skipped_low_relevance'])}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
