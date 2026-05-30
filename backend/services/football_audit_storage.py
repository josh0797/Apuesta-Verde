"""Football Market Audit storage layer.

Persists each football analysis run's market audit (the per-pick
``market_trace`` + ``markets_checked``) into MongoDB collection
``football_market_audit`` so the user can later query historical audits
by date / match / user.

The collection schema (UUID primary keys, timezone-aware datetimes):

    {
        "id":              "<uuid4>",
        "user_id":         "<user id>",
        "pick_run_id":     "<run id from db.picks>",
        "sport":           "football",
        "generated_at":    ISO-8601 (UTC),
        "match_date":      "YYYY-MM-DD",
        "total_discarded": int,
        "audit_rows":      [ ...row dicts (from build_run_audit_payload)... ],
        "summary_meta": {
            "histogram":          {"PROTECTED_BELOW_FLOOR": 3, ...},
            "rejection_codes":    [...],
            "dominant_rejection": "PROTECTED_BELOW_FLOOR",
        },
    }

All write helpers are fail-soft — exceptions are logged but never
propagate to the request handler so a Mongo hiccup never breaks the
``/api/analysis/run`` response.
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("football_audit_storage")


def _histogram_of_rejection_codes(audit_rows: list[dict]) -> dict[str, int]:
    codes = []
    for row in audit_rows or []:
        trace = (row or {}).get("market_trace") or {}
        code = trace.get("rejection_code") or "UNKNOWN"
        codes.append(code)
    return dict(Counter(codes))


def build_audit_document(*,
                          user_id: str,
                          pick_run_id: Optional[str],
                          audit_payload: dict,
                          match_date: Optional[str] = None) -> dict:
    """Translate the in-memory audit payload into a BSON-friendly doc."""
    rows = audit_payload.get("audit_rows") or []
    histogram = _histogram_of_rejection_codes(rows)
    dominant = max(histogram, key=histogram.get) if histogram else None

    now = datetime.now(timezone.utc)
    return {
        "id":              str(uuid.uuid4()),
        "user_id":         user_id,
        "pick_run_id":     pick_run_id,
        "sport":           "football",
        "generated_at":    now.isoformat(),
        "match_date":      match_date or now.date().isoformat(),
        "total_discarded": int(audit_payload.get("total_discarded") or 0),
        "audit_rows":      rows,
        "summary_meta": {
            "histogram":          histogram,
            "rejection_codes":    sorted(histogram.keys()),
            "dominant_rejection": dominant,
        },
        "_v": 1,
    }


async def store_football_audit(db,
                                *,
                                user_id: str,
                                pick_run_id: Optional[str],
                                audit_payload: dict,
                                match_date: Optional[str] = None) -> Optional[str]:
    """Insert the audit document. Returns the inserted ``id`` or ``None``
    on failure (errors are swallowed and logged).
    """
    try:
        if not audit_payload or audit_payload.get("total_discarded", 0) == 0:
            return None
        doc = build_audit_document(
            user_id=user_id,
            pick_run_id=pick_run_id,
            audit_payload=audit_payload,
            match_date=match_date,
        )
        await db.football_market_audit.insert_one(doc)
        return doc["id"]
    except Exception as exc:
        log.warning("store_football_audit failed: %s", exc)
        return None


async def query_football_audit(db,
                                *,
                                user_id: str,
                                date: Optional[str] = None,
                                limit: int = 30) -> list[dict]:
    """Read the most recent football audits for a user.

    Parameters
    ----------
    date : str | None
        Optional ``YYYY-MM-DD`` filter (matches ``match_date``).
    limit : int
        Max number of documents returned (defaults to 30).
    """
    try:
        q: dict[str, Any] = {"user_id": user_id, "sport": "football"}
        if date:
            q["match_date"] = date
        cur = db.football_market_audit.find(q, {"_id": 0}).sort("generated_at", -1).limit(int(limit))
        return await cur.to_list(length=int(limit))
    except Exception as exc:
        log.warning("query_football_audit failed: %s", exc)
        return []


async def query_latest_football_audit(db,
                                       *,
                                       user_id: str) -> Optional[dict]:
    """Return the most recent football audit document for a user, or None."""
    docs = await query_football_audit(db, user_id=user_id, limit=1)
    return docs[0] if docs else None


__all__ = [
    "build_audit_document",
    "store_football_audit",
    "query_football_audit",
    "query_latest_football_audit",
]
