"""FIX-NEW-1 — TheSportsDB API client + adapter.

TheSportsDB is now the **primary** source of live data for basketball
and baseball. Existing providers (SofaScore, MLB Stats API, NBA API,
etc.) remain registered as **fallbacks** — this module never replaces
them, it inserts itself at the head of the cascade.

API documentation: https://www.thesportsdb.com/docs_api_data
Headers: ``X-API-KEY`` (the env var ``THESPORTSDB_KEY`` provides it).

Two surface endpoints we use today:

  * V1 — ``/api/v1/json/{key}/searchteams.php?t=TeamName``
        (team lookup; on-demand only).
  * V2 — ``/api/v2/json/livescore/{sport}``
        (real-time scoreboard for `soccer`, `basketball`, `baseball`,
        `american football`, etc.).

V2 livescore payload (per item)::

    {
      "idLiveScore":     "11128251",
      "idEvent":         "2490796",
      "strSport":        "Basketball",
      "idLeague":        "4474",
      "strLeague":       "Israeli Basketball Premier League",
      "idHomeTeam":      "136065",
      "idAwayTeam":      "136059",
      "strHomeTeam":     "Maccabi Tel Aviv BC",
      "strAwayTeam":     "Hapoel Tel Aviv BC",
      "strHomeTeamBadge":"https://r2.../badge.png",
      "strAwayTeamBadge":"https://r2.../badge.png",
      "intHomeScore":    "72",
      "intAwayScore":    "52",
      "strStatus":       "BT",
      "strProgress":     "0",
      "strTimestamp":    "2026-06-16T17:50:00",
      "strEventTime":    "17:50",
      "dateEvent":       "2026-06-16",
      "updated":         "2026-06-16 20:13:21"
    }

Adapter outputs a normalised dict shape (canonical contract::

    {
      "available":     bool,
      "source":        "thesportsdb",
      "sport":         "basketball" | "baseball",
      "items": [
        {
          "match_id":         str,
          "league_id":        str,
          "league_name":      str,
          "home_team":        {"id": str, "name": str, "badge": str|None},
          "away_team":        {"id": str, "name": str, "badge": str|None},
          "home_score":       int|None,
          "away_score":       int|None,
          "status":           str,         # raw provider status (BT/IN6/NS/...)
          "status_normalized":str,         # "LIVE" | "FINISHED" | "SCHEDULED" | "UNKNOWN"
          "progress":         str|None,
          "kickoff_iso":      str|None,    # ISO 8601 UTC if parseable
          "updated_at":       str|None,
        },
        ...
      ],
      "reason_codes": [...],
    }
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger("services.external_sources.thesportsdb")

DEFAULT_TIMEOUT_SEC = 8.0
_LIVESCORE_PATH_TPL = "/v2/json/livescore/{sport}"
_SEARCH_TEAMS_TPL   = "/v1/json/{key}/searchteams.php"

# Status normalisation.
_STATUS_FINISHED = {"FT", "FINAL", "AET", "PEN", "FINISHED"}
_STATUS_LIVE_PREFIXES = (
    # Basketball / general live indicators (Q1..Q4, OT, HT, BT, IN[1-9]).
    "Q", "OT", "HT", "BT", "1H", "2H", "ET",
    # Baseball innings IN1..IN9, T1..T9, B1..B9.
    "IN", "T", "B",
)
_STATUS_SCHEDULED = {"NS", "TBD", "SCHEDULED", ""}


def is_enabled() -> bool:
    flag = (os.environ.get("ENABLE_THESPORTSDB") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(os.environ.get("THESPORTSDB_KEY"))


def get_base_url() -> str:
    return (
        os.environ.get("THESPORTSDB_BASE_URL")
        or "https://www.thesportsdb.com/api"
    ).rstrip("/")


def _headers() -> dict[str, str]:
    return {"X-API-KEY": os.environ.get("THESPORTSDB_KEY", "")}


# ─────────────────────────────────────────────────────────────────────
#   Status normalisation
# ─────────────────────────────────────────────────────────────────────

def _normalize_status(raw_status: Optional[str], progress: Optional[str]) -> str:
    if not raw_status and not progress:
        return "UNKNOWN"
    candidates: list[str] = []
    if raw_status:
        candidates.append(str(raw_status).upper().strip())
    if progress:
        candidates.append(str(progress).upper().strip())
    for cand in candidates:
        if cand in _STATUS_FINISHED:
            return "FINISHED"
        if cand in _STATUS_SCHEDULED:
            return "SCHEDULED"
        if any(cand.startswith(p) for p in _STATUS_LIVE_PREFIXES):
            return "LIVE"
        # Numeric "0" / "1" inside strProgress.
        if cand.isdigit():
            return "LIVE"
    return "UNKNOWN"


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(timestamp: Optional[str]) -> Optional[str]:
    if not timestamp:
        return None
    s = str(timestamp).strip()
    if not s:
        return None
    # ``strTimestamp`` is local TZ of the API feed — naive ISO format.
    # We return as-is to preserve provenance; consumers can re-interpret.
    if "T" in s and len(s) >= 19:
        return s
    # ``updated`` looks like "2026-06-16 20:13:21".
    if " " in s and len(s) >= 19:
        return s.replace(" ", "T")
    return s


# ─────────────────────────────────────────────────────────────────────
#   HTTP helper
# ─────────────────────────────────────────────────────────────────────

async def _request(client: Optional[httpx.AsyncClient], path: str,
                   *, params: Optional[dict] = None,
                   timeout: float = DEFAULT_TIMEOUT_SEC) -> dict:
    """Authenticated GET. Returns ``{}`` on any error (fail-soft)."""
    if not is_enabled():
        return {}
    url = get_base_url() + path
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        try:
            r = await client.get(url, headers=_headers(), params=params,
                                 timeout=timeout)
        except Exception as exc:
            log.debug("[thesportsdb] GET %s failed: %s", path, exc)
            return {}
        if r.status_code != 200:
            log.debug("[thesportsdb] GET %s → HTTP %d", path, r.status_code)
            return {}
        try:
            return r.json()
        except Exception as exc:
            log.debug("[thesportsdb] GET %s JSON parse failed: %s", path, exc)
            return {}
    finally:
        if owns_client and client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────
#   Public — livescore
# ─────────────────────────────────────────────────────────────────────

def _normalize_item(raw: dict, sport: str) -> dict:
    """Map one TheSportsDB livescore item to the canonical shape."""
    raw = raw or {}
    return {
        "match_id":      str(raw.get("idEvent") or raw.get("idLiveScore") or ""),
        "league_id":     str(raw.get("idLeague") or ""),
        "league_name":   raw.get("strLeague") or "",
        "home_team": {
            "id":    str(raw.get("idHomeTeam") or ""),
            "name":  raw.get("strHomeTeam") or "",
            "badge": raw.get("strHomeTeamBadge") or None,
        },
        "away_team": {
            "id":    str(raw.get("idAwayTeam") or ""),
            "name":  raw.get("strAwayTeam") or "",
            "badge": raw.get("strAwayTeamBadge") or None,
        },
        "home_score":        _safe_int(raw.get("intHomeScore")),
        "away_score":        _safe_int(raw.get("intAwayScore")),
        "status":            raw.get("strStatus") or "",
        "status_normalized": _normalize_status(raw.get("strStatus"),
                                                raw.get("strProgress")),
        "progress":          raw.get("strProgress") or None,
        "kickoff_iso":       _parse_iso(raw.get("strTimestamp")),
        "event_time":        raw.get("strEventTime") or None,
        "date_event":        raw.get("dateEvent") or None,
        "updated_at":        _parse_iso(raw.get("updated")),
        "_raw_sport":        raw.get("strSport") or sport,
    }


async def fetch_livescore(
    sport: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """Fetch the V2 livescore feed for the given sport.

    Args:
      sport: "basketball" | "baseball" | "soccer" | ...

    Returns the canonical envelope (see module docstring).
    """
    sport_l = (sport or "").strip().lower()
    if sport_l not in {"basketball", "baseball", "soccer",
                        "american_football", "ice_hockey"}:
        return {
            "available":    False,
            "source":       "thesportsdb",
            "sport":        sport_l,
            "items":        [],
            "reason_codes": ["THESPORTSDB_UNSUPPORTED_SPORT"],
        }
    if not is_enabled():
        return {
            "available":    False,
            "source":       "thesportsdb",
            "sport":        sport_l,
            "items":        [],
            "reason_codes": ["THESPORTSDB_DISABLED"],
        }
    payload = await _request(
        client, _LIVESCORE_PATH_TPL.format(sport=sport_l), timeout=timeout,
    )
    rows = payload.get("livescore") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {
            "available":    False,
            "source":       "thesportsdb",
            "sport":        sport_l,
            "items":        [],
            "reason_codes": ["THESPORTSDB_EMPTY_PAYLOAD"],
        }
    items = [_normalize_item(r, sport_l) for r in rows if isinstance(r, dict)]
    return {
        "available":    True,
        "source":       "thesportsdb",
        "sport":        sport_l,
        "items":        items,
        "reason_codes": ["THESPORTSDB_OK"],
        "count":        len(items),
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
#   Public — searchteams (on-demand)
# ─────────────────────────────────────────────────────────────────────

async def search_teams(name: str,
                       *,
                       client: Optional[httpx.AsyncClient] = None,
                       timeout: float = DEFAULT_TIMEOUT_SEC) -> list[dict]:
    """V1 team search. Returns the raw ``teams`` list or ``[]``."""
    if not name or not is_enabled():
        return []
    payload = await _request(
        client,
        _SEARCH_TEAMS_TPL.format(key=os.environ["THESPORTSDB_KEY"]),
        params={"t": name},
        timeout=timeout,
    )
    teams = payload.get("teams") if isinstance(payload, dict) else None
    return teams if isinstance(teams, list) else []


__all__ = [
    "is_enabled",
    "get_base_url",
    "fetch_livescore",
    "search_teams",
]
