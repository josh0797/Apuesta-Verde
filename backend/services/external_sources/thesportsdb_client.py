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
_EVENTSLAST_TPL     = "/v1/json/{key}/eventslast.php"

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


# ─────────────────────────────────────────────────────────────────────
#   Sprint-F98.1 — eventslast.php (last N results for a team)
# ─────────────────────────────────────────────────────────────────────
def _normalize_event_to_recent_fixture(raw: dict) -> Optional[dict]:
    """Project a TheSportsDB ``eventslast`` event into the shape that
    ``services.normalizers.normalize_recent_fixtures`` already consumes.

    The shape is INTENTIONALLY identical to the API-Sports
    ``fixtures_last_n`` envelope so the downstream pipeline is
    drop-in compatible.
    """
    if not isinstance(raw, dict):
        return None
    h_score = _safe_int(raw.get("intHomeScore"))
    a_score = _safe_int(raw.get("intAwayScore"))
    if h_score is None and a_score is None:
        # Not finished / missing score → useless for the L5/L15 aggregator.
        return None
    return {
        "fixture": {
            "id":        raw.get("idEvent"),
            "date":      raw.get("strTimestamp") or raw.get("dateEvent"),
            "status":    {"short": "FT", "long": "Finished"},
        },
        "teams": {
            "home": {
                "id":   raw.get("idHomeTeam"),
                "name": raw.get("strHomeTeam"),
            },
            "away": {
                "id":   raw.get("idAwayTeam"),
                "name": raw.get("strAwayTeam"),
            },
        },
        "goals": {"home": h_score, "away": a_score},
        "league": {
            "id":     raw.get("idLeague"),
            "name":   raw.get("strLeague"),
            "season": raw.get("strSeason"),
        },
        # Non-canonical extras used by some adapters (xG via SofaScore).
        "_provider":           "thesportsdb",
        "_thesportsdb_event":  raw.get("idEvent"),
    }


async def fetch_last_events_by_team(
    team_id: str | int,
    *,
    n: int = 5,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> list[dict]:
    """Sprint-F98.1 — Fallback for ``api_football.fixtures_last_n``.

    TheSportsDB's ``eventslast.php?id=<idTeam>`` returns the team's
    last 5 finished events. This is the **only viable replacement**
    for the API-Sports endpoint of the same name (now disabled) for
    national-team coverage.

    Notes:
      * The provider returns at most 5 items per call; ``n`` is
        therefore clamped to 5 internally.
      * Pure fail-soft: ``[]`` on any error or when disabled.
      * Output items are normalised to the API-Sports
        ``fixtures_last_n`` envelope so existing normalizers stay
        unchanged.
    """
    if team_id is None or str(team_id).strip() == "":
        return []
    if not is_enabled():
        return []
    payload = await _request(
        client,
        _EVENTSLAST_TPL.format(key=os.environ["THESPORTSDB_KEY"]),
        params={"id": str(team_id)},
        timeout=timeout,
    )
    raw_results = (payload or {}).get("results")
    if not isinstance(raw_results, list):
        return []
    out: list[dict] = []
    for raw in raw_results[: max(1, min(int(n), 5))]:
        norm = _normalize_event_to_recent_fixture(raw)
        if norm:
            out.append(norm)
    return out


# ─────────────────────────────────────────────────────────────────────
#   F96.2 — Public — lookup_event_stats (post-match corners / stats)
# ─────────────────────────────────────────────────────────────────────
_LOOKUP_EVENT_STATS_V1_TPL = "/v1/json/{key}/lookupeventstats.php"
_LOOKUP_EVENT_STATS_V2_TPL = "/v2/json/lookup/event_stats/{event_id}"


async def lookup_event_stats(
    event_id: str | int,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    prefer_v2: bool = False,
) -> dict:
    """F96.2 — Fetch post-match event stats for an idEvent.

    Args:
      event_id: TheSportsDB idEvent (the long numeric/string id from
        :func:`fetch_livescore` items).
      prefer_v2: if True, try V2 ``/lookup/event_stats/{id}`` first
        (premium endpoint; some keys lack access). Falls back to V1.

    Returns a canonical envelope (NEVER raises)::

        {
          "available":     bool,
          "source":        "thesportsdb",
          "endpoint":      "v1" | "v2" | None,
          "event_id":      str,
          "raw_stats":     list[dict],     # raw items from provider
          "raw_names":     list[str],      # stat names observed
          "reason_codes":  list[str],
        }

    The caller (settler corners cascade) inspects ``raw_stats`` /
    ``raw_names`` and runs the defensive parser (see
    :func:`services.football_finished_game_settler._extract_corners_from_payload`).
    """
    out: dict = {
        "available":     False,
        "source":        "thesportsdb",
        "endpoint":      None,
        "event_id":      str(event_id) if event_id is not None else "",
        "raw_stats":     [],
        "raw_names":     [],
        "reason_codes":  [],
    }
    if event_id is None or not str(event_id).strip():
        out["reason_codes"].append("THESPORTSDB_EVENT_ID_MISSING")
        return out
    if not is_enabled():
        out["reason_codes"].append("THESPORTSDB_DISABLED")
        return out

    eid = str(event_id).strip()

    # Order of endpoints: prefer_v2 swaps the priority.
    endpoints: list[tuple[str, str]] = []
    if prefer_v2:
        endpoints.append(("v2", _LOOKUP_EVENT_STATS_V2_TPL.format(event_id=eid)))
        endpoints.append(("v1", _LOOKUP_EVENT_STATS_V1_TPL.format(
            key=os.environ.get("THESPORTSDB_KEY", ""))))
    else:
        endpoints.append(("v1", _LOOKUP_EVENT_STATS_V1_TPL.format(
            key=os.environ.get("THESPORTSDB_KEY", ""))))
        endpoints.append(("v2", _LOOKUP_EVENT_STATS_V2_TPL.format(event_id=eid)))

    # V1 uses ?id= query string; V2 has it baked into the path.
    for label, path in endpoints:
        params: Optional[dict] = {"id": eid} if label == "v1" else None
        try:
            payload = await _request(
                client, path, params=params, timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[thesportsdb] lookup_event_stats %s raised: %s",
                      label, exc)
            out["reason_codes"].append(
                f"THESPORTSDB_LOOKUP_EVENT_STATS_{label.upper()}_FAILED"
            )
            continue
        if not isinstance(payload, dict) or not payload:
            out["reason_codes"].append(
                f"THESPORTSDB_LOOKUP_EVENT_STATS_{label.upper()}_EMPTY"
            )
            continue

        # V1 typically returns {"eventstats": [{"strStat": "...", "intHome": ..., "intAway": ...}, ...]}
        # V2 may return a similar list under a top-level "stats" or
        # "event_stats" or "data" key. We try them all.
        rows: list[Any] = []
        for key in ("eventstats", "event_stats", "stats", "data"):
            v = payload.get(key)
            if isinstance(v, list):
                rows = v
                break
        if not rows:
            # Some V2 shapes nest one more level: {"data": {"stats": [...]}}.
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("eventstats", "event_stats", "stats"):
                    v = data.get(key)
                    if isinstance(v, list):
                        rows = v
                        break
        if not rows:
            out["reason_codes"].append(
                f"THESPORTSDB_LOOKUP_EVENT_STATS_{label.upper()}_NO_ROWS"
            )
            continue

        # Collect raw stat names for debug + return raw rows.
        raw_names: list[str] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            name = (
                r.get("strStat")
                or r.get("name")
                or r.get("type")
                or r.get("stat")
            )
            if isinstance(name, str):
                raw_names.append(name)
        # Debug logging (always, requested by the user).
        log.info(
            "[thesportsdb] lookup_event_stats %s event=%s raw_names=%s",
            label, eid, raw_names,
        )
        out["available"]   = True
        out["endpoint"]    = label
        out["raw_stats"]   = rows
        out["raw_names"]   = raw_names
        out["reason_codes"].append(f"THESPORTSDB_LOOKUP_EVENT_STATS_{label.upper()}_OK")
        return out

    return out


# ─────────────────────────────────────────────────────────────────────
#   F96.3 — Public — fixtures fallback + enrichment
# ─────────────────────────────────────────────────────────────────────
_EVENTS_DAY_TPL          = "/v1/json/{key}/eventsday.php"
_EVENTS_NEXT_LEAGUE_TPL  = "/v1/json/{key}/eventsnextleague.php"
_SEARCH_LEAGUES_TPL      = "/v1/json/{key}/search_all_leagues.php"
_LOOKUP_LEAGUE_TPL       = "/v1/json/{key}/lookupleague.php"


def _normalize_event_item(raw: dict) -> dict:
    """Map a TheSportsDB event item (eventsday/eventsnextleague) to a
    canonical fixture shape used by callers needing only the basics.
    """
    raw = raw or {}
    return {
        "event_id":     str(raw.get("idEvent") or ""),
        "league_id":    str(raw.get("idLeague") or ""),
        "league_name":  raw.get("strLeague") or "",
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
        "date_event":   raw.get("dateEvent")    or None,
        "event_time":   raw.get("strTime")      or raw.get("strEventTime") or None,
        "timestamp":    raw.get("strTimestamp") or None,
        "season":       raw.get("strSeason")    or None,
        "status":       raw.get("strStatus")    or None,
    }


async def fetch_upcoming_events_by_date(
    date: str,
    *,
    sport: str = "Soccer",
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """F96.3 — Fixtures for a given calendar date.

    Args:
      date: ``YYYY-MM-DD`` (UTC; TheSportsDB does not honour timezones
        on this endpoint).
      sport: e.g. "Soccer" (default), "Basketball", "Baseball".

    Returns the canonical envelope (NEVER raises)::

        {"available": bool, "source": "thesportsdb",
         "endpoint": "eventsday",
         "items": [normalized_event, ...],
         "reason_codes": [...]}
    """
    out: dict = {
        "available":    False,
        "source":       "thesportsdb",
        "endpoint":     "eventsday",
        "items":        [],
        "reason_codes": [],
    }
    if not date or not isinstance(date, str):
        out["reason_codes"].append("THESPORTSDB_DATE_MISSING")
        return out
    if not is_enabled():
        out["reason_codes"].append("THESPORTSDB_DISABLED")
        return out
    path = _EVENTS_DAY_TPL.format(key=os.environ.get("THESPORTSDB_KEY", ""))
    try:
        payload = await _request(
            client, path,
            params={"d": date.strip(), "s": sport},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[thesportsdb] eventsday raised: %s", exc)
        out["reason_codes"].append("THESPORTSDB_EVENTSDAY_FAILED")
        return out
    if not isinstance(payload, dict) or not payload:
        out["reason_codes"].append("THESPORTSDB_EVENTSDAY_EMPTY")
        return out
    events = payload.get("events")
    if not isinstance(events, list):
        out["reason_codes"].append("THESPORTSDB_EVENTSDAY_NO_EVENTS")
        return out
    out["items"]      = [_normalize_event_item(e) for e in events if isinstance(e, dict)]
    out["available"]  = bool(out["items"])
    out["count"]      = len(out["items"])
    out["fetched_at"] = datetime.now(timezone.utc).isoformat()
    out["reason_codes"].append("THESPORTSDB_EVENTSDAY_OK")
    return out


async def fetch_next_events_by_league(
    league_id: str | int,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """F96.3 — Next ~15 upcoming events for a TheSportsDB league id.

    Some endpoints require premium; this one is part of the basic V1
    surface but may return ``{"events": null}`` on free keys for some
    leagues — fail-soft.
    """
    out: dict = {
        "available":    False,
        "source":       "thesportsdb",
        "endpoint":     "eventsnextleague",
        "league_id":    str(league_id) if league_id is not None else "",
        "items":        [],
        "reason_codes": [],
    }
    if league_id is None or not str(league_id).strip():
        out["reason_codes"].append("THESPORTSDB_LEAGUE_ID_MISSING")
        return out
    if not is_enabled():
        out["reason_codes"].append("THESPORTSDB_DISABLED")
        return out
    path = _EVENTS_NEXT_LEAGUE_TPL.format(
        key=os.environ.get("THESPORTSDB_KEY", ""),
    )
    try:
        payload = await _request(
            client, path,
            params={"id": str(league_id).strip()},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[thesportsdb] eventsnextleague raised: %s", exc)
        out["reason_codes"].append("THESPORTSDB_EVENTSNEXTLEAGUE_FAILED")
        return out
    if not isinstance(payload, dict) or not payload:
        out["reason_codes"].append("THESPORTSDB_EVENTSNEXTLEAGUE_EMPTY")
        return out
    events = payload.get("events")
    if not isinstance(events, list):
        out["reason_codes"].append("THESPORTSDB_EVENTSNEXTLEAGUE_NO_EVENTS")
        return out
    out["items"]      = [_normalize_event_item(e) for e in events if isinstance(e, dict)]
    out["available"]  = bool(out["items"])
    out["count"]      = len(out["items"])
    out["fetched_at"] = datetime.now(timezone.utc).isoformat()
    out["reason_codes"].append("THESPORTSDB_EVENTSNEXTLEAGUE_OK")
    return out


# ─────────────────────────────────────────────────────────────────────
#   F96.3 — Enrichment helpers (badges / league info)
# ─────────────────────────────────────────────────────────────────────
async def enrich_team_badge(
    team_name: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """Return a small dict with badge URL + team_id for a given team name.

    Wraps :func:`search_teams` and picks the first soccer match found.
    """
    out: dict = {
        "available":    False,
        "team_name":    team_name,
        "team_id":      None,
        "badge":        None,
        "country":      None,
        "league":       None,
        "reason_codes": [],
    }
    teams = await search_teams(team_name, client=client, timeout=timeout)
    if not teams:
        out["reason_codes"].append("THESPORTSDB_TEAM_NOT_FOUND")
        return out
    # Prefer soccer teams; fall back to first.
    selected: Optional[dict] = None
    for t in teams:
        if not isinstance(t, dict):
            continue
        sport = (t.get("strSport") or "").lower()
        if sport == "soccer":
            selected = t
            break
    if selected is None:
        selected = next((t for t in teams if isinstance(t, dict)), None)
    if not isinstance(selected, dict):
        out["reason_codes"].append("THESPORTSDB_TEAM_INVALID_SHAPE")
        return out
    out["available"]    = True
    out["team_id"]      = str(selected.get("idTeam") or "")
    out["badge"]        = selected.get("strBadge") or selected.get("strTeamBadge") or None
    out["country"]      = selected.get("strCountry") or None
    out["league"]       = selected.get("strLeague")  or None
    out["reason_codes"].append("THESPORTSDB_TEAM_FOUND")
    return out


async def search_leagues(
    *,
    country: Optional[str] = None,
    sport: str = "Soccer",
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> list[dict]:
    """V1 league search — filters by country and/or sport.

    Returns the raw ``leagues`` (or ``countrys``) list, or ``[]``.
    """
    if not is_enabled():
        return []
    path = _SEARCH_LEAGUES_TPL.format(key=os.environ.get("THESPORTSDB_KEY", ""))
    params: dict[str, Any] = {}
    if country:
        params["c"] = country
    if sport:
        params["s"] = sport
    try:
        payload = await _request(client, path, params=params or None,
                                  timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log.debug("[thesportsdb] search_leagues raised: %s", exc)
        return []
    if not isinstance(payload, dict):
        return []
    leagues = payload.get("countrys") or payload.get("leagues") or payload.get("countries")
    return leagues if isinstance(leagues, list) else []


async def lookup_league(
    league_id: str | int,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """V1 league lookup by idLeague. Returns ``{}`` when missing."""
    if league_id is None or not is_enabled():
        return {}
    path = _LOOKUP_LEAGUE_TPL.format(key=os.environ.get("THESPORTSDB_KEY", ""))
    try:
        payload = await _request(
            client, path,
            params={"id": str(league_id).strip()},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[thesportsdb] lookup_league raised: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    leagues = payload.get("leagues")
    if isinstance(leagues, list) and leagues and isinstance(leagues[0], dict):
        return leagues[0]
    return {}


__all__ = [
    "is_enabled",
    "get_base_url",
    "fetch_livescore",
    "search_teams",
    "lookup_event_stats",
    "fetch_upcoming_events_by_date",
    "fetch_next_events_by_league",
    "enrich_team_badge",
    "search_leagues",
    "lookup_league",
]
