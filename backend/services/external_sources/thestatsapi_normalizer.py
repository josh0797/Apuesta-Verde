"""Normalize TheStatsAPI payloads into the API-Sports football shape.

The rest of the pipeline (``data_ingestion._enrich_football`` etc.)
expects each fixture to look like::

    {
      "fixture": {
        "id":        int,
        "date":      ISO8601,
        "timestamp": int (unix seconds, UTC),
        "status":    {"short": "NS"|"1H"|"HT"|"2H"|"FT"|...},
        "venue":     {"name": str | None},
      },
      "league": {"id": int, "name": str, "season": int,
                 "logo": str|None, "country": str|None, "round": str|None},
      "teams":  {"home": {"id": int, "name": str, "logo": str|None},
                 "away": {"id": int, "name": str, "logo": str|None}},
      "goals":  {"home": int|None, "away": int|None},
      "statistics": [...],          # only on live fetches
      "_external_source":  "thestatsapi",  # marker — used by the UI badge
      "_external_source_id": <thestatsapi match id>,
      "_is_national_team":  bool,    # true if any team flagged as nat-team
    }

IDs from TheStatsAPI are namespaced into a high integer range
(``900_000_000 + raw_id``) so they cannot collide with API-Sports IDs.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Offset to avoid ID collisions with API-Sports (which uses 1..~1.5M)
_ID_NAMESPACE_OFFSET = 900_000_000

# Strip well-known prefixes used by TheStatsAPI ID format:
#   mt_370102627, tm_28025, comp_6107, sn_118868
_ID_PREFIX_RE = re.compile(r"^[a-z]+_", re.IGNORECASE)

# Status code mapping → API-Sports `status.short`
_STATUS_MAP = {
    # not started
    "scheduled": "NS", "ns": "NS", "not_started": "NS", "upcoming": "NS", "pre": "NS",
    "tbd": "TBD", "postponed": "PST", "cancelled": "CANC", "canceled": "CANC",
    # live
    "live": "LIVE", "in_progress": "LIVE", "in-progress": "LIVE",
    "first_half": "1H", "firsthalf": "1H", "1h": "1H",
    "halftime": "HT", "half_time": "HT", "ht": "HT",
    "second_half": "2H", "secondhalf": "2H", "2h": "2H",
    "extra_time": "ET", "et": "ET",
    "penalty": "P", "penalties": "P",
    # finished
    "finished": "FT", "full_time": "FT", "ft": "FT", "final": "FT",
    "aet": "AET", "pen": "PEN",
}


def _ns_id(raw: Any) -> int | None:
    """Map a TheStatsAPI ID (which may be ``int``, ``"123"`` or
    ``"mt_370102627"``) into our namespaced integer space.

    * Pure ints / numeric strings → ``offset + int``
    * Prefixed strings (``mt_…``, ``tm_…``, ``comp_…``) → strip prefix
      and parse the numeric tail.
    * Strings without any digits → derive a stable int via blake2b
      (first 8 hex chars), still offset into our namespace.
    Returns ``None`` only if ``raw`` is ``None`` or unparseable.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return _ID_NAMESPACE_OFFSET + int(raw)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        stripped = _ID_PREFIX_RE.sub("", s, count=1)
        # Try the prefix-stripped tail first
        if stripped.isdigit():
            try:
                return _ID_NAMESPACE_OFFSET + int(stripped)
            except (TypeError, ValueError, OverflowError):
                pass
        # Or the full original (in case it was already numeric)
        if s.isdigit():
            try:
                return _ID_NAMESPACE_OFFSET + int(s)
            except (TypeError, ValueError, OverflowError):
                pass
        # Fallback: deterministic hash so the same raw_id always maps to
        # the same int across requests (idempotent dedupe).
        digest = hashlib.blake2b(s.encode("utf-8"), digest_size=4).hexdigest()
        try:
            return _ID_NAMESPACE_OFFSET + int(digest, 16)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _parse_dt(value: Any) -> tuple[str | None, int | None]:
    """Return ``(iso_str, unix_timestamp_seconds_utc)`` or ``(None, None)``."""
    if value is None:
        return None, None
    if isinstance(value, (int, float)):
        # already unix seconds (or millis?)
        ts = int(value)
        if ts > 10**12:  # millis
            ts //= 1000
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat(), ts
        except Exception:
            return None, None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None, None
        # ISO8601 — handle trailing Z
        try:
            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat(), int(dt.timestamp())
        except Exception:
            return None, None
    return None, None


def _norm_status(value: Any) -> str:
    if not value:
        return "NS"
    k = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return _STATUS_MAP.get(k, value.upper() if isinstance(value, str) else "NS")


def _get(d: dict | None, *keys: str, default: Any = None) -> Any:
    """Safe nested get: ``_get(d, 'a', 'b')`` returns ``d['a']['b']``."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _extract_team(raw: dict | None, default_prefix: str = "team") -> dict:
    if not isinstance(raw, dict):
        return {"id": None, "name": None, "logo": None, "is_national_team": False}
    name = raw.get("name") or raw.get("team_name") or raw.get("shortName") or raw.get("short_name")
    type_ = (raw.get("type") or "").lower()
    is_nt = bool(
        raw.get("is_national_team")
        or raw.get("national_team")
        or type_ in {"national", "national_team", "selection"}
    )
    return {
        "id":   _ns_id(raw.get("id") or raw.get("team_id")),
        "name": name,
        "logo": raw.get("logo") or raw.get("crest") or raw.get("image"),
        "is_national_team": is_nt,
        "country": raw.get("country") or raw.get("country_code"),
    }


def _extract_league(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {"id": None, "name": None, "season": None,
                "logo": None, "country": None, "round": None,
                "is_international": False}
    name = raw.get("name") or raw.get("competition_name") or raw.get("tournament")
    country = raw.get("country") or raw.get("region")
    type_ = (raw.get("type") or "").lower()
    is_intl = (
        bool(raw.get("is_international"))
        or type_ in {"international", "national_team", "nation"}
        or (isinstance(country, str) and country.strip().lower() in {"world", "international"})
    )
    return {
        "id":      _ns_id(raw.get("id") or raw.get("competition_id")),
        "name":    name,
        "season": raw.get("season") or raw.get("year") or datetime.now(timezone.utc).year,
        "logo":   raw.get("logo") or raw.get("image"),
        "country": country,
        "round":  raw.get("round") or raw.get("matchday") or raw.get("stage"),
        "is_international": is_intl,
        "_raw_id": raw.get("id") or raw.get("competition_id"),
    }


def _apply_competitions_index(league: dict, competitions_index: dict | None) -> dict:
    """Enrich a league payload from a {raw_competition_id: meta_dict} index.

    Used when TheStatsAPI returns only ``competition_id`` on the match
    object — we look up the full competition metadata that we previously
    cached from ``/football/competitions``.
    """
    if not competitions_index:
        return league
    raw_id = league.get("_raw_id")
    if not raw_id:
        return league
    meta = competitions_index.get(str(raw_id))
    if not isinstance(meta, dict):
        return league
    if not league.get("name") and meta.get("name"):
        league["name"] = meta["name"]
    if not league.get("country") and meta.get("country"):
        league["country"] = meta["country"]
    if meta.get("is_international") and not league.get("is_international"):
        league["is_international"] = True
    return league


def normalize_match(raw: dict, competitions_index: dict | None = None) -> dict | None:
    """Convert a TheStatsAPI match payload into API-Sports fixture shape.

    ``competitions_index`` (optional) is a ``{raw_competition_id: meta}``
    mapping built from a recent ``/football/competitions`` fetch. When
    provided, it lets us enrich the league name / international flag
    even on match payloads that ship only ``competition_id``.

    Returns ``None`` if the payload is too incomplete to be useful
    (missing teams, missing match id, or missing kickoff).
    """
    if not isinstance(raw, dict):
        return None

    match_id = _ns_id(raw.get("id") or raw.get("match_id") or raw.get("fixture_id"))
    if match_id is None:
        return None

    # Teams — try several layouts
    teams_obj = raw.get("teams")
    if isinstance(teams_obj, dict):
        home_raw, away_raw = teams_obj.get("home"), teams_obj.get("away")
    else:
        home_raw = raw.get("home_team") or raw.get("home") or {}
        away_raw = raw.get("away_team") or raw.get("away") or {}

    home = _extract_team(home_raw, "home")
    away = _extract_team(away_raw, "away")
    if not (home.get("name") and away.get("name")):
        return None

    # League / competition — TheStatsAPI sometimes nests it as
    # ``competition: {...}`` and sometimes provides only a flat
    # ``competition_id`` at the top level (e.g. on /football/matches).
    league_obj = raw.get("competition") or raw.get("league") or raw.get("tournament")
    if not isinstance(league_obj, dict) and raw.get("competition_id"):
        league_obj = {
            "id":   raw.get("competition_id"),
            "name": raw.get("competition_name") or None,
            "season": raw.get("season_id") or raw.get("season"),
            "type":   raw.get("competition_type") or None,
            "country": raw.get("country") or None,
        }
    league = _extract_league(league_obj)
    league = _apply_competitions_index(league, competitions_index)

    # Kickoff
    kickoff_iso, kickoff_ts = _parse_dt(
        raw.get("date")
        or raw.get("utc_date")
        or raw.get("utcDate")
        or raw.get("kickoff")
        or raw.get("start_time")
        or raw.get("datetime")
    )
    if kickoff_iso is None:
        return None

    # Status
    status_short = _norm_status(
        raw.get("status") or _get(raw, "status", "short") or raw.get("state")
    )

    # Goals
    goals_home = (
        raw.get("home_score")
        or _get(raw, "goals", "home")
        or _get(raw, "score", "home")
        or _get(raw, "score", "fullTime", "home")
    )
    goals_away = (
        raw.get("away_score")
        or _get(raw, "goals", "away")
        or _get(raw, "score", "away")
        or _get(raw, "score", "fullTime", "away")
    )

    venue_name = raw.get("venue")
    if isinstance(venue_name, dict):
        venue_name = venue_name.get("name")

    is_national = bool(home.get("is_national_team") or away.get("is_national_team")
                       or league.get("is_international"))

    fixture = {
        "fixture": {
            "id":        match_id,
            "date":      kickoff_iso,
            "timestamp": kickoff_ts,
            "status":    {"short": status_short, "long": status_short},
            "venue":     {"name": venue_name} if venue_name else {"name": None},
        },
        "league": {
            "id":      league.get("id"),
            "name":    league.get("name"),
            "season": league.get("season"),
            "logo":   league.get("logo"),
            "country": league.get("country"),
            "round":  league.get("round"),
        },
        "teams": {
            "home": {"id": home["id"], "name": home["name"], "logo": home["logo"]},
            "away": {"id": away["id"], "name": away["name"], "logo": away["logo"]},
        },
        "goals": {"home": goals_home, "away": goals_away},
        "_external_source":     "thestatsapi",
        "_external_source_id":  raw.get("id") or raw.get("match_id"),
        "_is_national_team":    is_national,
        "_is_international":   league.get("is_international", False),
    }
    return fixture


def normalize_matches(raw_list: list[dict], competitions_index: dict | None = None) -> list[dict]:
    """Bulk-normalize a list, dropping malformed entries (fail-soft)."""
    out: list[dict] = []
    for raw in raw_list or []:
        try:
            n = normalize_match(raw, competitions_index=competitions_index)
            if n is not None:
                out.append(n)
        except Exception as exc:  # noqa: BLE001
            log.debug("[thestatsapi] normalize failed: %s", exc)
            continue
    return out


def normalize_competition(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    cid = _ns_id(raw.get("id") or raw.get("competition_id"))
    name = raw.get("name") or raw.get("competition_name")
    if cid is None or not name:
        return None
    type_ = (raw.get("type") or "").lower()
    country = raw.get("country") or raw.get("region")
    return {
        "id":      cid,
        "name":    name,
        "country": country,
        "type":    type_,
        "is_international": type_ in {"international", "national_team", "nation"}
                            or (isinstance(country, str) and country.lower() in {"world", "international"}),
        "raw_id":  raw.get("id") or raw.get("competition_id"),
    }


def build_competitions_index(raw_competitions: list[dict]) -> dict[str, dict]:
    """Build a ``{raw_competition_id: meta_dict}`` lookup table.

    Used by the aggregator to enrich match payloads that only carry a
    ``competition_id`` (no nested competition object).
    """
    index: dict[str, dict] = {}
    for raw in raw_competitions or []:
        n = normalize_competition(raw)
        if n is None:
            continue
        raw_id = n.get("raw_id")
        if raw_id is None:
            continue
        index[str(raw_id)] = n
    return index
