"""Sprint E.2 · Odds Alerts persistence + dedupe.

Bridge between the pure detector (:mod:`services.odds_value_detector`)
and the ``odds_alerts`` Mongo collection. Strict ``observe_only`` —
this module never produces a bet, it only writes auditable signals.

Schema of an ``odds_alerts`` document::

    {
      "_id":            <uuid str>,
      "alert_id":       <uuid str>,
      "fingerprint":    <str>,        # dedupe key (see _fingerprint)
      "match_id":       "...",
      "market":         "h2h" | "totals" | ...,
      "outcome_name":   "Over" | "Arsenal" | ...,
      "outcome_point":  2.5 | None,
      "bookmaker_key":  "pinnacle" | None,
      "signal_type":    "OUTLIER" | "EDGE_VS_MODEL" | "FAST_MOVE" | "DISPERSION",
      "severity":       "LOW" | "MEDIUM" | "HIGH",
      "payload":        { ...full signal dict from detector... },
      "created_at":     <UTC datetime>,
      "updated_at":     <UTC datetime>,
      "occurrences":    <int>,        # bumped on dedupe hit
      "acked":          <bool>,
      "acked_at":       <UTC|None>,
      "acked_by":       <str|None>,
    }

Dedupe strategy
---------------
For each signal we build a stable ``fingerprint`` that ignores the
exact numeric value but identifies the *kind* of alert. If we see the
same fingerprint within ``ALERTS_DEDUPE_WINDOW_SECONDS`` we bump the
existing document's ``occurrences`` + ``updated_at`` instead of
creating a new one.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("odds_alerts")

COLLECTION_NAME: str = "odds_alerts"

ALERTS_DEDUPE_WINDOW_SECONDS: int = int(
    os.environ.get("ODDS_ALERTS_DEDUPE_WINDOW", "1800")   # 30 min
)


def _fingerprint(signal: dict) -> str:
    """Stable fingerprint key. Excludes the exact numerical values so
    near-identical signals collapse into the same alert."""
    parts = [
        signal.get("signal_type"),
        str(signal.get("match_id")),
        str(signal.get("market")),
        str(signal.get("outcome_name")),
        str(signal.get("outcome_point")),
        str(signal.get("bookmaker_key") or signal.get("best_bookmaker") or ""),
    ]
    return "|".join(p or "" for p in parts)


def _build_doc(signal: dict, *, now: datetime) -> dict:
    return {
        "_id":             str(uuid.uuid4()),
        "alert_id":        str(uuid.uuid4()),
        "fingerprint":     _fingerprint(signal),
        "match_id":        str(signal.get("match_id")) if signal.get("match_id") else None,
        "market":          signal.get("market"),
        "outcome_name":    signal.get("outcome_name"),
        "outcome_point":   signal.get("outcome_point"),
        "bookmaker_key":   (signal.get("bookmaker_key")
                             or signal.get("best_bookmaker")),
        "signal_type":     signal.get("signal_type"),
        "severity":        signal.get("severity"),
        "payload":         dict(signal),
        "created_at":      now,
        "updated_at":      now,
        "occurrences":     1,
        "acked":           False,
        "acked_at":        None,
        "acked_by":        None,
    }


async def persist_signals(db, *, signals: list[dict]) -> dict:
    """Upsert / dedupe a batch of signals against ``odds_alerts``.

    Returns a report ``{inserted, deduped, errors}`` (counts only). Each
    signal that matches an existing alert with the same fingerprint
    within :data:`ALERTS_DEDUPE_WINDOW_SECONDS` bumps the existing
    document's ``occurrences`` + ``updated_at`` and refreshes its
    ``payload`` so the latest snapshot wins.
    """
    report = {"inserted": 0, "deduped": 0, "errors": 0,
              "alert_ids": []}
    if not signals:
        return report
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=ALERTS_DEDUPE_WINDOW_SECONDS)
    for sig in signals:
        if not isinstance(sig, dict):
            report["errors"] += 1
            continue
        try:
            fp = _fingerprint(sig)
            existing = await db.odds_alerts.find_one(
                {"fingerprint": fp,
                 "updated_at":  {"$gte": cutoff}},
                sort=[("updated_at", -1)],
            )
            if existing:
                # Dedupe: bump counters + refresh latest payload.
                await db.odds_alerts.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"updated_at": now,
                              "payload":    dict(sig),
                              "severity":   sig.get("severity"),
                              # If the new severity escalated, surface it.
                              },
                     "$inc": {"occurrences": 1}},
                )
                report["deduped"] += 1
                report["alert_ids"].append(existing.get("alert_id"))
            else:
                doc = _build_doc(sig, now=now)
                await db.odds_alerts.insert_one(doc)
                report["inserted"] += 1
                report["alert_ids"].append(doc["alert_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("persist signal failed: %s", exc)
            report["errors"] += 1
    return report


async def list_alerts(
    db,
    *,
    match_id: Optional[str] = None,
    signal_type: Optional[str] = None,
    severity: Optional[str] = None,
    acked: Optional[bool] = None,
    limit: int = 50,
    since_hours: int = 24,
) -> list[dict]:
    """Return alerts filtered by the given criteria, newest first."""
    q: dict = {}
    if match_id:
        q["match_id"] = str(match_id)
    if signal_type:
        q["signal_type"] = signal_type
    if severity:
        q["severity"] = severity
    if acked is not None:
        q["acked"] = bool(acked)
    if since_hours and since_hours > 0:
        q["updated_at"] = {"$gte": (datetime.now(timezone.utc)
                                     - timedelta(hours=since_hours))}
    lim = max(1, min(int(limit or 50), 500))
    try:
        cursor = db.odds_alerts.find(q).sort("updated_at", -1).limit(lim)
        out = await cursor.to_list(length=lim)
    except Exception as exc:  # noqa: BLE001
        log.warning("list_alerts failed: %s", exc)
        return []
    # Strip BSON _id + normalise datetimes to ISO.
    normalised: list[dict] = []
    for d in out:
        d.pop("_id", None)
        for k in ("created_at", "updated_at", "acked_at"):
            v = d.get(k)
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        normalised.append(d)
    return normalised


async def ack_alert(db, *, alert_id: str, acked_by: Optional[str] = None) -> dict:
    """Mark an alert as acknowledged. Returns a small status dict."""
    if not alert_id:
        return {"ok": False, "reason_code": "ALERT_ID_REQUIRED"}
    try:
        res = await db.odds_alerts.update_one(
            {"alert_id": str(alert_id)},
            {"$set": {"acked": True,
                      "acked_at": datetime.now(timezone.utc),
                      "acked_by": acked_by}},
        )
        return {"ok": (res.matched_count or 0) > 0,
                "matched": res.matched_count,
                "modified": res.modified_count}
    except Exception as exc:  # noqa: BLE001
        log.warning("ack_alert failed: %s", exc)
        return {"ok": False, "reason_code": "WRITE_FAILED",
                "_error": str(exc)}


__all__ = [
    "COLLECTION_NAME", "ALERTS_DEDUPE_WINDOW_SECONDS",
    "_fingerprint", "persist_signals", "list_alerts", "ack_alert",
]
