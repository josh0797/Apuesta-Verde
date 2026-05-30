"""Storage layer for Live Corner Intelligence evaluations.

Persists every strong corner recommendation into the dedicated MongoDB
collection ``live_corner_evaluations`` (per user spec — feedback loop,
hit-rate metrics, calibration).

Document schema (UUID PK, ISO-8601 UTC datetimes)::

    {
        "id":                    str (uuid4),
        "user_id":               str,
        "match_id":              str | int,
        "sport":                 "football",
        "minute":                int,
        "score":                 str,                          # "1-1"
        "score_home":            int,
        "score_away":            int,
        "home_team":             str,
        "away_team":             str,

        "current_corners":       {"home": int, "away": int, "total": int},
        "corner_pace":           float,
        "corner_pace_total":     float,
        "projected_corner_total": float,

        "recommended_market":    str,
        "recommended_team":      "home"|"away"|None,
        "recommended_line":      float | None,
        "recommended_odds":      float | None,

        "corner_pressure_score": int (0..100),
        "confidence":            int (0..100),
        "risk":                  "LOW" | "MEDIUM" | "HIGH",
        "state":                 str,
        "classification":        dict (sub-flags),
        "reason_codes":          list[str],
        "human_reasons":         list[str],
        "explanation":           str,
        "avoid_markets":         list[str],
        "raw_metrics_snapshot":  dict,

        "final_corner_count":    int | None,      # filled by post-match update
        "result":                "won" | "lost" | "pending" | "void",
        "reference_profile_tag": str | None,      # REFERENCE_LIVE_CORNER_PRESSURE_PROFILE
        "generated_at":          ISO-8601 UTC,
        "_v":                    1,
    }
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("live_corner_storage")

REFERENCE_LIVE_CORNER_PRESSURE_PROFILE = "REFERENCE_LIVE_CORNER_PRESSURE_PROFILE"


def _derive_reference_tag(corner_recommendation: dict,
                           result: Optional[str]) -> Optional[str]:
    """Tag the document with the PSG-Arsenal reference profile when:
      • territorial dominance + corner dominance + low goal conversion
      • late pressure
      • corner bet wins
    """
    if not corner_recommendation:
        return None
    cls = corner_recommendation.get("classification") or {}
    if (
        cls.get("psg_benchmark")
        and cls.get("control_without_goal_depth")
        and corner_recommendation.get("minute", 0) >= 60
        and result == "won"
    ):
        return REFERENCE_LIVE_CORNER_PRESSURE_PROFILE
    return None


def build_corner_evaluation_document(*,
                                       user_id: str,
                                       match_id: Any,
                                       corner_recommendation: dict,
                                       metrics: dict,
                                       result: str = "pending",
                                       final_corner_count: Optional[int] = None,
                                       ) -> dict:
    """Translate the in-memory corner recommendation into a BSON-friendly doc."""
    cr = corner_recommendation or {}
    score = cr.get("score") or {}
    score_home = score.get("home", 0)
    score_away = score.get("away", 0)

    return {
        "id":                    str(uuid.uuid4()),
        "user_id":               user_id,
        "match_id":              match_id,
        "sport":                 "football",
        "minute":                int(cr.get("minute") or 0),
        "score":                 f"{score_home}-{score_away}",
        "score_home":            int(score_home),
        "score_away":            int(score_away),
        "home_team":             metrics.get("home_team"),
        "away_team":             metrics.get("away_team"),

        "current_corners":       cr.get("current_corners") or {},
        "corner_pace":           cr.get("corner_pace"),
        "corner_pace_total":     cr.get("corner_pace_total"),
        "projected_corner_total": cr.get("projected_corner_total"),

        "recommended_market":    cr.get("recommended_market"),
        "recommended_team":      cr.get("recommended_team"),
        "recommended_line":      cr.get("recommended_line"),
        "recommended_odds":      cr.get("recommended_odds"),

        "corner_pressure_score": cr.get("corner_pressure_score"),
        "confidence":            cr.get("confidence"),
        "risk":                  cr.get("risk"),
        "state":                 cr.get("state"),
        "classification":        cr.get("classification") or {},
        "reason_codes":          list(cr.get("reason_codes") or []),
        "human_reasons":         list(cr.get("human_reasons") or []),
        "explanation":           cr.get("explanation"),
        "avoid_markets":         list(cr.get("avoid_markets") or []),
        "raw_metrics_snapshot":  metrics,

        "final_corner_count":    final_corner_count,
        "result":                result,
        "reference_profile_tag": _derive_reference_tag(cr, result),
        "generated_at":          datetime.now(timezone.utc).isoformat(),
        "_v":                    1,
    }


async def store_live_corner_evaluation(db, *,
                                         user_id: str,
                                         match_id: Any,
                                         corner_recommendation: dict,
                                         metrics: dict,
                                         only_strong: bool = True,
                                         ) -> Optional[str]:
    """Persist a corner evaluation. By default we only persist when the
    engine produced an actionable recommendation (``should_recommend``).

    Returns the inserted ``id`` or None when skipped/failed.
    """
    try:
        if only_strong and not (corner_recommendation or {}).get("should_recommend"):
            return None
        doc = build_corner_evaluation_document(
            user_id=user_id,
            match_id=match_id,
            corner_recommendation=corner_recommendation,
            metrics=metrics,
            result="pending",
        )
        await db.live_corner_evaluations.insert_one(doc)
        return doc["id"]
    except Exception as exc:
        log.warning("store_live_corner_evaluation failed: %s", exc)
        return None


async def update_corner_evaluation_result(db, *,
                                            evaluation_id: str,
                                            final_corner_count: int,
                                            result: str,
                                            ) -> bool:
    """Patch the final corner count + result once the match ends.

    Also (re)compute the ``reference_profile_tag`` because it depends on
    the result.
    """
    try:
        doc = await db.live_corner_evaluations.find_one({"id": evaluation_id})
        if not doc:
            return False
        cr = {
            "classification": doc.get("classification") or {},
            "minute":         doc.get("minute") or 0,
        }
        tag = _derive_reference_tag(cr, result)
        await db.live_corner_evaluations.update_one(
            {"id": evaluation_id},
            {"$set": {
                "final_corner_count":      int(final_corner_count),
                "result":                  result,
                "reference_profile_tag":   tag,
                "resolved_at":             datetime.now(timezone.utc).isoformat(),
            }},
        )
        return True
    except Exception as exc:
        log.warning("update_corner_evaluation_result failed: %s", exc)
        return False


async def query_corner_evaluations(db, *,
                                     user_id: str,
                                     match_id: Optional[Any] = None,
                                     reference_only: bool = False,
                                     limit: int = 30,
                                     ) -> list[dict]:
    """Read recent corner evaluations for a user.

    Parameters
    ----------
    match_id : optional filter
    reference_only : if True, return only documents tagged as
        REFERENCE_LIVE_CORNER_PRESSURE_PROFILE.
    limit : capped at 100.
    """
    try:
        q: dict = {"user_id": user_id, "sport": "football"}
        if match_id is not None:
            q["match_id"] = {"$in": [str(match_id), match_id]}
        if reference_only:
            q["reference_profile_tag"] = REFERENCE_LIVE_CORNER_PRESSURE_PROFILE
        cur = db.live_corner_evaluations.find(q, {"_id": 0}).sort(
            "generated_at", -1).limit(max(1, min(100, int(limit))))
        return await cur.to_list(length=int(limit))
    except Exception as exc:
        log.warning("query_corner_evaluations failed: %s", exc)
        return []


__all__ = [
    "REFERENCE_LIVE_CORNER_PRESSURE_PROFILE",
    "build_corner_evaluation_document",
    "store_live_corner_evaluation",
    "update_corner_evaluation_result",
    "query_corner_evaluations",
]
