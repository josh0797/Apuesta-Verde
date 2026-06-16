"""FIX-NEW-1 — TheSportsDB ingest layer for basketball + baseball.

Promotes TheSportsDB to **primary** live source by persisting its
livescore feed into ``db.matches`` *before* the existing providers
(SofaScore, MLB Stats API, NBA API, ESPN) run their own fetchers.

Critical design points:
  * The schema written to ``db.matches`` is the same canonical shape
    the rest of the pipeline already consumes (``match_id``, ``sport``,
    ``home_team``, ``away_team``, ``home_score``, ``away_score``,
    ``status``, ``is_live``, ``provider``...).
  * ``provider="thesportsdb"`` is stamped explicitly so downstream
    layers can audit the data origin.
  * ``thesportsdb_event_id`` is stored alongside so we can cross-link
    later (e.g. additional team / league enrichment).
  * Fail-soft everywhere; the fallback providers stay in charge if any
    step crashes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .external_sources import thesportsdb_client as tsdb

log = logging.getLogger("services.thesportsdb_live_ingest")


def _doc_from_thesportsdb_item(item: dict, sport: str) -> Optional[dict]:
    """Convert a normalised TheSportsDB livescore item to a match_doc."""
    if not isinstance(item, dict):
        return None
    match_id = item.get("match_id")
    if not match_id:
        return None

    home_team = item.get("home_team") or {}
    away_team = item.get("away_team") or {}
    if not (home_team.get("name") and away_team.get("name")):
        return None

    is_live = item.get("status_normalized") == "LIVE"
    is_finished = item.get("status_normalized") == "FINISHED"
    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "match_id":  f"tsdb_{match_id}",
        "sport":     sport,
        "provider":  "thesportsdb",
        "thesportsdb_event_id": match_id,
        "league_id":   item.get("league_id"),
        "league_name": item.get("league_name"),
        "home_team": {
            "id":    home_team.get("id"),
            "name":  home_team.get("name"),
            "badge": home_team.get("badge"),
            "_thesportsdb_id": home_team.get("id"),
        },
        "away_team": {
            "id":    away_team.get("id"),
            "name":  away_team.get("name"),
            "badge": away_team.get("badge"),
            "_thesportsdb_id": away_team.get("id"),
        },
        "home_score":   item.get("home_score"),
        "away_score":   item.get("away_score"),
        "status_short": item.get("status"),
        "status_normalized": item.get("status_normalized"),
        "is_live":      is_live,
        "is_finished":  is_finished,
        "progress":     item.get("progress"),
        "kickoff_iso":  item.get("kickoff_iso"),
        "commence_time": item.get("kickoff_iso"),
        "commence_date": item.get("date_event"),
        "event_time":   item.get("event_time"),
        "last_provider_update": item.get("updated_at"),
        "thesportsdb_fetched_at": now_iso,
    }


async def ingest_thesportsdb_live(
    db,
    *,
    sport: str,
) -> dict:
    """Fetch + upsert TheSportsDB livescore feed for one sport.

    Returns::

        {
          "available":   bool,
          "source":      "thesportsdb",
          "sport":       str,
          "persisted":   int,
          "live_count":  int,
          "items":       [normalised TheSportsDB items],
          "reason_codes": [...]
        }
    """
    sport = (sport or "").lower()
    if sport not in {"basketball", "baseball"}:
        return {
            "available":  False, "source": "thesportsdb",
            "sport":      sport, "persisted": 0, "live_count": 0,
            "items":      [],   "reason_codes": ["THESPORTSDB_UNSUPPORTED_SPORT"],
        }

    bundle = await tsdb.fetch_livescore(sport)
    if not bundle.get("available"):
        return {
            "available":  False, "source": "thesportsdb",
            "sport":      sport, "persisted": 0, "live_count": 0,
            "items":      bundle.get("items") or [],
            "reason_codes": list(bundle.get("reason_codes") or []),
        }

    persisted = 0
    live_count = 0
    for it in bundle.get("items") or []:
        doc = _doc_from_thesportsdb_item(it, sport)
        if not doc:
            continue
        try:
            if db is not None:
                await db.matches.update_one(
                    {"match_id": doc["match_id"]},
                    {"$set": doc},
                    upsert=True,
                )
            persisted += 1
            if doc.get("is_live"):
                live_count += 1
        except Exception as exc:
            log.debug("[thesportsdb_live_ingest] upsert failed %s: %s",
                      doc.get("match_id"), exc)

    log.info(
        "[thesportsdb_live_ingest] sport=%s persisted=%d live=%d total=%d",
        sport, persisted, live_count, len(bundle.get("items") or []),
    )

    return {
        "available":   True,
        "source":      "thesportsdb",
        "sport":       sport,
        "persisted":   persisted,
        "live_count":  live_count,
        "items":       bundle.get("items") or [],
        "reason_codes": list(bundle.get("reason_codes") or []),
    }


__all__ = [
    "ingest_thesportsdb_live",
    "_doc_from_thesportsdb_item",  # exported for tests
]
