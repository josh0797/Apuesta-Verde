"""Phase F67 — match_id mapping API-Sports ↔ TheStatsAPI.

Goal
====
Resolve a canonical match identifier from EITHER source. The REST
endpoint ``/api/football/editorial-prediction/{match_id}`` today only
works when ``match_id`` is already a TheStatsAPI ``mt_*`` id. With this
mapping layer, the endpoint becomes bidirectional:

  * If the incoming id starts with ``mt_`` → already canonical.
  * Otherwise (API-Sports numeric id or our own UUID) → look up the
    mapping in Mongo; if missing, perform an on-demand search against
    TheStatsAPI ``/api/football/matches?date_from=…&date_to=…`` filtered
    by team names, persist the mapping (90d TTL), return ``mt_*``.

Schema (``match_id_mappings``)::

    {
      "_id":              "<internal-or-apisports-id>",   # primary key
      "thestatsapi_id":   "mt_511134637",
      "home_team":        "Brazil",
      "away_team":        "Morocco",
      "utc_date":         "2026-06-14T...",
      "resolved_at":      <datetime UTC>,
      "source":           "mt_prefix" | "name_date_lookup",
    }

All public coroutines are fail-soft and never raise.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("match_id_mapping")

COLLECTION = "match_id_mappings"
TTL_DAYS   = 90


def is_thestatsapi_id(match_id: Any) -> bool:
    """True iff ``match_id`` is already a TheStatsAPI canonical id."""
    return isinstance(match_id, str) and match_id.startswith("mt_")


def _normalise_team_name(name: Optional[str]) -> str:
    if not isinstance(name, str):
        return ""
    n = name.strip().lower()
    # Strip common suffixes (FC, AFC, CF, …) so "Real Madrid CF" matches "Real Madrid".
    n = re.sub(r"\b(fc|cf|afc|sc|sd|cd|ac|as|ud)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


# ─────────────────────────────────────────────────────────────────────
# Mongo helpers
# ─────────────────────────────────────────────────────────────────────
async def get_cached_mapping(db, match_id: str) -> Optional[dict]:
    if db is None or not match_id:
        return None
    try:
        return await db[COLLECTION].find_one({"_id": match_id})
    except Exception as exc:  # noqa: BLE001
        log.debug("[MAPPING_FETCH_FAIL] %s: %s", match_id, exc)
        return None


async def upsert_mapping(
    db, internal_id: str, thestatsapi_id: str, *,
    home_team: Optional[str] = None, away_team: Optional[str] = None,
    utc_date: Optional[str] = None, source: str = "name_date_lookup",
) -> bool:
    if db is None or not internal_id or not thestatsapi_id:
        return False
    try:
        await db[COLLECTION].update_one(
            {"_id": internal_id},
            {"$set": {
                "thestatsapi_id": thestatsapi_id,
                "home_team":      home_team,
                "away_team":      away_team,
                "utc_date":       utc_date,
                "resolved_at":    datetime.now(timezone.utc),
                "source":         source,
            }},
            upsert=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("[MAPPING_UPSERT_FAIL] %s→%s: %s",
                  internal_id, thestatsapi_id, exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Public resolver
# ─────────────────────────────────────────────────────────────────────
async def resolve_to_thestatsapi_id(
    incoming_id: str, *, db,
    match_hint: Optional[dict] = None,
) -> Optional[str]:
    """Return the canonical ``mt_*`` id for ``incoming_id`` or None.

    Strategy:
      1. If already ``mt_*`` → echo.
      2. If cached mapping exists → return it.
      3. Otherwise perform an on-demand lookup against TheStatsAPI using
         ``match_hint`` (which should include ``home_team`` /
         ``away_team`` / ``utc_date``). Persist the mapping.

    Fail-soft: returns None when the lookup is impossible.
    """
    if not incoming_id:
        return None
    if is_thestatsapi_id(incoming_id):
        return incoming_id

    cached = await get_cached_mapping(db, incoming_id)
    if cached and cached.get("thestatsapi_id"):
        return cached["thestatsapi_id"]

    # ── On-demand lookup ─────────────────────────────────────────────
    if not isinstance(match_hint, dict):
        return None
    try:
        from services.thestatsapi_client import is_enabled, _http_get
        if not is_enabled():
            return None
        utc_date = match_hint.get("utc_date") or match_hint.get("date") \
                   or match_hint.get("kickoff")
        if not utc_date:
            return None
        # Trim ISO timestamps to YYYY-MM-DD for the API filter.
        date_str = str(utc_date)[:10]
        # Bound the search window to a ±1 day envelope to cover timezone
        # ambiguity (TheStatsAPI returns matches in UTC dates).
        d_from = (datetime.fromisoformat(date_str) - timedelta(days=1)).date().isoformat()
        d_to   = (datetime.fromisoformat(date_str) + timedelta(days=1)).date().isoformat()
        payload = await _http_get(
            f"/api/football/matches?date_from={d_from}&date_to={d_to}&limit=100"
        )
        if not isinstance(payload, list):
            return None
        h_norm = _normalise_team_name(match_hint.get("home_team")
                                       or (match_hint.get("home") or {}).get("name")
                                       if isinstance(match_hint.get("home"), dict)
                                       else match_hint.get("home"))
        a_norm = _normalise_team_name(match_hint.get("away_team")
                                       or (match_hint.get("away") or {}).get("name")
                                       if isinstance(match_hint.get("away"), dict)
                                       else match_hint.get("away"))
        if not (h_norm and a_norm):
            return None
        for m in payload:
            if not isinstance(m, dict):
                continue
            mh = _normalise_team_name((m.get("home_team") or {}).get("name"))
            ma = _normalise_team_name((m.get("away_team") or {}).get("name"))
            if mh == h_norm and ma == a_norm:
                mt_id = m.get("id")
                if mt_id and mt_id.startswith("mt_"):
                    await upsert_mapping(
                        db, incoming_id, mt_id,
                        home_team=match_hint.get("home_team"),
                        away_team=match_hint.get("away_team"),
                        utc_date=utc_date,
                    )
                    return mt_id
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("[MAPPING_LOOKUP_FAIL] %s: %s", incoming_id, exc)
        return None


__all__ = [
    "COLLECTION", "TTL_DAYS",
    "is_thestatsapi_id", "_normalise_team_name",
    "get_cached_mapping", "upsert_mapping",
    "resolve_to_thestatsapi_id",
]
