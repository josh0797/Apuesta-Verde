"""Storage layer for Live Territorial Control evaluations.

Persists each evaluation in the dedicated MongoDB collection
``live_territorial_evaluations`` (per user spec — feedback loop, hit-rate
metrics, calibration).

Document schema (UUID PK, timezone-aware ISO datetimes):

    {
        "id":                    str (uuid4),
        "user_id":               str,
        "match_id":              str | int,
        "sport":                 "football",
        "minute":                int,
        "score":                 str,                       # "0-1"
        "score_home":            int,
        "score_away":            int,
        "home_team":             str,
        "away_team":             str,
        "territorial_state":     str,
        "corner_pressure_state": bool,
        "corner_pressure_score": float (0..100),
        "recommended_live_market": str | None,
        "recommended_category":    str | None,
        "recommended_score":       float | None,
        "confidence":            float | None,
        "risk":                  str | None,
        "raw_metrics_snapshot":  dict,
        "raw_payload":           dict (full evaluation payload),
        "final_result":          dict | None (filled by separate update),
        "generated_at":          ISO 8601 UTC,
        "_v":                    1,
    }
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("live_evaluation_storage")


def build_evaluation_document(*,
                                user_id: str,
                                match_id: Any,
                                sport: str,
                                metrics: dict,
                                territorial: dict,
                                corner: dict,
                                ranked_markets: list[dict],
                                ) -> dict:
    """Translate the in-memory evaluation into a BSON-friendly doc."""
    top_market = ranked_markets[0] if ranked_markets else None
    score_home = territorial.get("score_home") or metrics.get("score_home") or 0
    score_away = territorial.get("score_away") or metrics.get("score_away") or 0

    # "confidence" maps to the top market score; "risk" derives from
    # state (territorial/corner) per the user's UX expectations.
    state = territorial.get("state") or "NO_CLEAR_DOMINANCE"
    if state == "CONTROL_WITH_PRESSURE":
        risk = "MEDIO"
    elif state == "CORNER_PRESSURE_STATE":
        risk = "MEDIO"
    elif state == "TERRITORIAL_CONTROL":
        risk = "BAJO"
    else:
        risk = "BAJO"

    now = datetime.now(timezone.utc)
    return {
        "id":                    str(uuid.uuid4()),
        "user_id":               user_id,
        "match_id":               match_id,
        "sport":                 sport,
        "minute":                int(territorial.get("minute") or metrics.get("minute") or 0),
        "score":                 f"{score_home}-{score_away}",
        "score_home":            int(score_home),
        "score_away":            int(score_away),
        "home_team":             metrics.get("home_team"),
        "away_team":             metrics.get("away_team"),
        "territorial_state":     state,
        "corner_pressure_state": bool(territorial.get("corner_pressure_state")),
        "corner_pressure_score": float(corner.get("score") or 0),
        "recommended_live_market":   (top_market or {}).get("market"),
        "recommended_category":      (top_market or {}).get("category"),
        "recommended_score":         (top_market or {}).get("score"),
        "confidence":            (top_market or {}).get("score"),
        "risk":                  risk,
        "raw_metrics_snapshot":  metrics,
        "raw_payload": {
            "territorial":     territorial,
            "corner":          corner,
            "ranked_markets":  ranked_markets,
        },
        "final_result":          None,
        "generated_at":          now.isoformat(),
        "_v":                    1,
    }


async def store_live_territorial_evaluation(db, *,
                                              user_id: str,
                                              match_id: Any,
                                              sport: str,
                                              metrics: dict,
                                              territorial: dict,
                                              corner: dict,
                                              ranked_markets: list[dict],
                                              ) -> Optional[str]:
    """Persist the evaluation. Returns the inserted ``id`` or None on
    failure (errors are swallowed and logged).
    """
    try:
        doc = build_evaluation_document(
            user_id=user_id,
            match_id=match_id,
            sport=sport,
            metrics=metrics,
            territorial=territorial,
            corner=corner,
            ranked_markets=ranked_markets,
        )
        await db.live_territorial_evaluations.insert_one(doc)
        return doc["id"]
    except Exception as exc:
        log.warning("store_live_territorial_evaluation failed: %s", exc)
        return None


async def query_live_territorial_evaluations(db, *,
                                              user_id: str,
                                              match_id: Optional[Any] = None,
                                              limit: int = 30,
                                              ) -> list[dict]:
    """Read recent evaluations for a user (optionally filtered by match)."""
    try:
        q: dict = {"user_id": user_id}
        if match_id is not None:
            # Tolerate string vs int historical inconsistency.
            q["match_id"] = {"$in": [str(match_id), match_id]}
        cur = db.live_territorial_evaluations.find(q, {"_id": 0}).sort(
            "generated_at", -1).limit(max(1, min(100, int(limit))))
        return await cur.to_list(length=int(limit))
    except Exception as exc:
        log.warning("query_live_territorial_evaluations failed: %s", exc)
        return []


async def update_final_result(db, *, evaluation_id: str,
                                final_result: dict) -> bool:
    """Patch the ``final_result`` field once the match concludes (for the
    feedback loop). Returns True on success.
    """
    try:
        r = await db.live_territorial_evaluations.update_one(
            {"id": evaluation_id},
            {"$set": {"final_result": final_result,
                       "final_result_at": datetime.now(timezone.utc).isoformat()}}
        )
        return r.matched_count > 0
    except Exception as exc:
        log.warning("update_final_result failed: %s", exc)
        return False


__all__ = [
    "build_evaluation_document",
    "store_live_territorial_evaluation",
    "query_live_territorial_evaluations",
    "update_final_result",
]
