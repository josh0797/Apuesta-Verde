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

from . import national_team_detector as ntd

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
    # Fallback heuristic: if the provider didn't flag national-team explicitly
    # (typical for /football/matches which only ships `home_team.name` etc.),
    # ask the national-team detector. It combines a FIFA-team allowlist + a
    # keyword-based comp detector + region check.
    if not is_national:
        is_national = ntd.is_national_team_match(
            home_name=home.get("name"),
            away_name=away.get("name"),
            league_name=league.get("name"),
            league_country=league.get("country"),
        )
    # `is_international` likewise can be inferred from the comp name/country
    # even when the provider doesn't ship a `type` field.
    is_intl = bool(league.get("is_international"))
    if not is_intl:
        is_intl = ntd.is_international_competition(
            league_name=league.get("name"),
            league_country=league.get("country"),
        )

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
        "_is_international":   is_intl,
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


# ─────────────────────────────────────────────────────────────────────
# Match-stats normalizer — maps TheStatsAPI per-match stats payload onto
# the API-Sports ``live_stats`` shape so the rest of the pipeline (xG
# proxy, live_xg_proxy, territorial control, etc.) doesn't need to
# branch on the source. The API-Sports format is::
#
#   {"home_stats": {"<stat type>": <value>}, "away_stats": {...},
#    "score": {"home": int|None, "away": int|None},
#    "minute": int|None, "status": str|None, "fetched_at": ISO}
#
# We deliberately key home_stats / away_stats by the same string
# identifiers API-Sports uses ("Shots on Goal", "Ball Possession",
# "Total Shots", "expected_goals") so the downstream code can read
# them with no provider awareness.
# ─────────────────────────────────────────────────────────────────────
_STAT_FIELD_MAP: dict[str, str] = {
    # TheStatsAPI key                  → API-Sports type label
    "xg":                                "expected_goals",
    "xg_total":                          "expected_goals",
    "expected_goals":                    "expected_goals",
    "shots_total":                       "Total Shots",
    "shots":                             "Total Shots",
    "shots_on_target":                   "Shots on Goal",
    "shots_on_goal":                     "Shots on Goal",
    "shots_off_target":                  "Shots off Goal",
    "shots_off_goal":                    "Shots off Goal",
    "shots_blocked":                     "Blocked Shots",
    "shots_inside_box":                  "Shots insidebox",
    "shots_outside_box":                 "Shots outsidebox",
    "possession":                        "Ball Possession",
    "ball_possession":                   "Ball Possession",
    "possession_percent":                "Ball Possession",
    "passes":                            "Total passes",
    "passes_total":                      "Total passes",
    "passes_accurate":                   "Passes accurate",
    "passes_pct":                        "Passes %",
    "passes_accuracy":                   "Passes %",
    "corners":                           "Corner Kicks",
    "corner_kicks":                      "Corner Kicks",
    "fouls":                             "Fouls",
    "offsides":                          "Offsides",
    "yellow_cards":                      "Yellow Cards",
    "red_cards":                         "Red Cards",
    "saves":                             "Goalkeeper Saves",
    "goalkeeper_saves":                  "Goalkeeper Saves",
    "attacks":                           "attacks",
    "dangerous_attacks":                 "dangerous_attacks",
}


def _format_stat_value(canon_key: str, value: Any) -> Any:
    """Coerce values to the same shape API-Sports uses.

    API-Sports stores possession as a string ``"55%"`` (not a float).
    Everything else is numeric. We mirror that to keep downstream
    consumers identical.
    """
    if value is None:
        return None
    if canon_key == "Ball Possession":
        try:
            v = float(value)
            if 0.0 <= v <= 1.0:
                v = v * 100.0
            return f"{int(round(v))}%"
        except (TypeError, ValueError):
            s = str(value).strip()
            if not s.endswith("%"):
                # try to extract number
                try:
                    return f"{int(round(float(s)))}%"
                except (TypeError, ValueError):
                    return s
            return s
    return value


def _extract_team_stats(raw_team: dict | None) -> dict[str, Any]:
    """Convert a TheStatsAPI per-team stats blob to the API-Sports keyed dict."""
    if not isinstance(raw_team, dict):
        return {}
    out: dict[str, Any] = {}
    for ts_key, val in raw_team.items():
        canon = _STAT_FIELD_MAP.get(ts_key.lower() if isinstance(ts_key, str) else "")
        if canon is None:
            continue
        formatted = _format_stat_value(canon, val)
        if formatted is None:
            continue
        # If two TheStatsAPI keys map to the same canonical type, the first
        # non-null wins (covers `xg` and `xg_total` both → expected_goals).
        out.setdefault(canon, formatted)
    return out


# FIX-2 — Real TheStatsAPI shape uses ``overview.<stat>.all.{home,away}``
# (verified live with mt_986264843 Iran vs NZ): corners, possession, shots,
# expected_goals and friends are nested under buckets named after their
# split (``all`` | ``first_half`` | ``second_half``). Previously this
# normalizer only handled the flat shape, so corners (and every other
# overview stat) were silently dropped from TheStatsAPI fixtures.
def _split_overview_to_team_blobs(overview: dict) -> tuple[dict, dict]:
    """Pivot ``overview.<stat>.all.{home,away}`` → ``({stat: home}, {stat: away})``.

    Fail-soft: keys whose ``all`` bucket is missing or malformed are
    skipped. Returns ``({}, {})`` if the input is not a dict.
    """
    if not isinstance(overview, dict):
        return ({}, {})
    home: dict[str, Any] = {}
    away: dict[str, Any] = {}
    for stat_key, bucket in overview.items():
        if not isinstance(bucket, dict):
            continue
        # Prefer 'all'; fall back to top-level scalars in case the API
        # ships a flatter shape for some stat types.
        allb = bucket.get("all") if isinstance(bucket.get("all"), dict) else bucket
        if not isinstance(allb, dict):
            continue
        h = allb.get("home")
        a = allb.get("away")
        if h is not None:
            home[stat_key] = h
        if a is not None:
            away[stat_key] = a
    return (home, away)


def normalize_match_stats(raw: dict, fallback_status: str | None = None) -> dict | None:
    """Convert a TheStatsAPI ``/football/matches/{id}/stats`` payload to
    the API-Sports ``live_stats`` shape.

    Recognised input layouts (all permissive):

      A. Flat:
         ``{"home": {...stats}, "away": {...stats},
            "score": {"home": 1, "away": 0}, "minute": 67, "status": "live"}``

      B. Team-keyed:
         ``{"home_team_stats": {...}, "away_team_stats": {...},
            ...}``

      C. Data-wrapped:
         ``{"data": {"home": {...}, "away": {...}, ...}}``  → unwrapped before call

      D. *Real* TheStatsAPI shape (FIX-2): pivot-by-stat with split
         buckets. Example::

            {"data": {
                "overview": {
                    "corner_kicks":  {"all": {"home": 4, "away": 1}, ...},
                    "expected_goals": {"all": {"home": 1.49, "away": 1.24}, ...},
                    ...
                },
                ...
            }}

         We pivot ``overview.<stat>.all.{home,away}`` → flat per-team
         blobs so the existing ``_extract_team_stats`` mapper handles
         it without changes.

    Returns ``None`` if no usable stats found (so callers can keep their
    existing API-Sports payload untouched).
    """
    if not isinstance(raw, dict):
        return None

    # FIX-2 — Real shape: TheStatsAPI nests stats under ``overview``,
    # sometimes one level below ``data``. Detect that path FIRST.
    overview = raw.get("overview")
    if not isinstance(overview, dict):
        data_block = raw.get("data") if isinstance(raw.get("data"), dict) else None
        if data_block is not None and isinstance(data_block.get("overview"), dict):
            overview = data_block["overview"]

    if isinstance(overview, dict):
        home_raw, away_raw = _split_overview_to_team_blobs(overview)
        if home_raw or away_raw:
            home_stats = _extract_team_stats(home_raw)
            away_stats = _extract_team_stats(away_raw)
            if home_stats or away_stats:
                # Score / minute / status may live at root or under data.
                root = raw.get("data") if isinstance(raw.get("data"), dict) else raw
                score_raw = root.get("score") or {}
                if isinstance(score_raw, dict):
                    score = {"home": score_raw.get("home"), "away": score_raw.get("away")}
                else:
                    score = {"home": None, "away": None}
                minute = root.get("minute") or root.get("elapsed")
                status_str = root.get("status") or fallback_status
                return {
                    "minute":     minute,
                    "status":     _norm_status(status_str) if status_str else None,
                    "score":      score,
                    "home_stats": home_stats,
                    "away_stats": away_stats,
                    "incidents":  [],
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "_source":    "thestatsapi",
                }

    home_raw = (
        raw.get("home_team_stats")
        or raw.get("home_stats")
        or raw.get("home")
        or raw.get("home_team")
    )
    away_raw = (
        raw.get("away_team_stats")
        or raw.get("away_stats")
        or raw.get("away")
        or raw.get("away_team")
    )
    if not (isinstance(home_raw, dict) and isinstance(away_raw, dict)):
        return None

    home_stats = _extract_team_stats(home_raw)
    away_stats = _extract_team_stats(away_raw)
    if not (home_stats or away_stats):
        return None

    # Score
    score_raw = raw.get("score") or {}
    if isinstance(score_raw, dict):
        score = {"home": score_raw.get("home"), "away": score_raw.get("away")}
    else:
        score = {"home": None, "away": None}

    minute = raw.get("minute") or raw.get("elapsed")
    status_str = raw.get("status") or fallback_status

    return {
        "minute":    minute,
        "status":    _norm_status(status_str) if status_str else None,
        "score":     score,
        "home_stats": home_stats,
        "away_stats": away_stats,
        "incidents": [],   # TheStatsAPI doesn't ship per-event stream here
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "_source":   "thestatsapi",
    }


def merge_live_stats(primary: dict | None, secondary: dict | None) -> dict | None:
    """Combine two ``live_stats`` payloads. Primary's non-empty values win.

    Used to "graft" TheStatsAPI xG / shots / possession onto an
    API-Sports ``live_stats`` that was returned but came back with
    empty ``home_stats`` / ``away_stats`` blocks.
    """
    if primary is None:
        return secondary
    if secondary is None:
        return primary
    merged = dict(primary)
    for side in ("home_stats", "away_stats"):
        a = primary.get(side) or {}
        b = secondary.get(side) or {}
        out = dict(b)   # start from secondary, then overlay primary
        for k, v in a.items():
            if v is not None and v != "":
                out[k] = v
        merged[side] = out
    # Mark provenance (additive — doesn't clobber existing _source)
    sources = set(filter(None, [primary.get("_source"), secondary.get("_source")]))
    if sources:
        merged["_sources"] = sorted(sources)
    return merged


# ─────────────────────────────────────────────────────────────────────
# Phase F74-post v2 — Odds adapter: TheStatsAPI → API-Sports shape
# ─────────────────────────────────────────────────────────────────────
def _ts_extract_last_opening(selection: Any) -> tuple[float | None, float | None]:
    """Return ``(last_seen, opening)`` parsed as floats. Both None on bad input.

    TheStatsAPI odds come as STRINGS ("2.100") — must be parsed to float.
    Rejects odds <= 1.01 (treated as invalid).
    """
    if not isinstance(selection, dict):
        return None, None

    def _to_float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
            return f if f >= 1.01 else None
        except (TypeError, ValueError):
            return None

    return _to_float(selection.get("last_seen")), _to_float(selection.get("opening"))


def _ts_line_key_to_label(line_key: str) -> str | None:
    """Convert ``"over_2_5"`` → ``"2.5"``, ``"over_3_5"`` → ``"3.5"``, etc.

    Format expected: ``"<over|under>_<int>_<decimal>"`` or bare digits.
    Defensive — returns ``None`` on unexpected input.
    """
    if not isinstance(line_key, str):
        return None
    parts = line_key.lower().split("_")
    digits = [p for p in parts if p.isdigit()]
    if len(digits) >= 2:
        return f"{digits[0]}.{digits[1]}"
    if len(digits) == 1:
        return digits[0]
    return None


def normalize_thestatsapi_odds_to_apisports_shape(
    thestatsapi_odds_data: dict,
) -> list[dict]:
    """Translate TheStatsAPI odds payload to the API-Football shape that
    ``normalizer.normalize_odds()`` expects.

    Input (raw TheStatsAPI ``data`` sub-dict)::

        {
          "match_id": "mt_14502",
          "bookmakers": [{
            "bookmaker": "Pinnacle",
            "markets": {
              "match_odds":    {"home":{"opening":"2.100","last_seen":"2.050"}, ...},
              "btts":          {"yes":{...}, "no":{...}},
              "total_goals":   {"over_2_5":{"over":{...},"under":{...}}, ...},
              "match_corners": {"over_9_5":{"over":{...},"under":{...}}, ...},
              "asian_handicap":{"home":{...},"away":{...}},
            }
          }]
        }

    Output (API-Football shape — one entry per match)::

        [{"bookmakers": [{"name": "...", "bets": [{"name": "...", "values": [...]}]}],
          "_source":      "thestatsapi",
          "_opening_odds": {"<bm>|<market>|<value>": float, ...}}]

    Uses ``last_seen`` as the current odd. Preserves opening separately
    in ``_opening_odds`` so ``odds_value_engine`` can compute line movement
    from day one without snapshot history.

    Fail-soft: returns ``[]`` if no valid bookmaker/market is found.
    """
    if not isinstance(thestatsapi_odds_data, dict):
        return []
    bookmakers_raw = thestatsapi_odds_data.get("bookmakers") or []
    if not isinstance(bookmakers_raw, list) or not bookmakers_raw:
        return []

    bookmakers_out: list[dict] = []
    # Preserve opening by (bookmaker, market, value) so the engine can
    # compute movement = last_seen - opening.
    opening_index: dict[tuple[str, str, str], float] = {}

    for bm in bookmakers_raw:
        if not isinstance(bm, dict):
            continue
        bm_name = str(bm.get("bookmaker") or "TheStatsAPI")
        markets = bm.get("markets") or {}
        if not isinstance(markets, dict):
            continue

        bets: list[dict] = []

        # ── match_odds → "Match Winner" (Home/Draw/Away) ─────────────
        mo = markets.get("match_odds")
        if isinstance(mo, dict):
            mo_values = []
            for ts_key, api_value in (("home", "Home"),
                                        ("draw", "Draw"),
                                        ("away", "Away")):
                last, opening = _ts_extract_last_opening(mo.get(ts_key))
                if last is not None:
                    mo_values.append({"value": api_value, "odd": str(last)})
                    if opening is not None:
                        opening_index[(bm_name, "Match Winner", api_value)] = opening
            if mo_values:
                bets.append({"name": "Match Winner", "values": mo_values})

        # ── btts → "Both Teams Score" (Yes/No) ───────────────────────
        btts = markets.get("btts")
        if isinstance(btts, dict):
            btts_values = []
            for ts_key, api_value in (("yes", "Yes"), ("no", "No")):
                last, opening = _ts_extract_last_opening(btts.get(ts_key))
                if last is not None:
                    btts_values.append({"value": api_value, "odd": str(last)})
                    if opening is not None:
                        opening_index[(bm_name, "Both Teams Score", api_value)] = opening
            if btts_values:
                bets.append({"name": "Both Teams Score", "values": btts_values})

        # ── total_goals → "Goals Over/Under" ─────────────────────────
        tg = markets.get("total_goals")
        if isinstance(tg, dict):
            tg_values = []
            for line_key, sides in tg.items():
                if not isinstance(sides, dict):
                    continue
                line_label = _ts_line_key_to_label(line_key)
                if line_label is None:
                    continue
                for side_key, side_label in (("over", "Over"), ("under", "Under")):
                    last, opening = _ts_extract_last_opening(sides.get(side_key))
                    if last is not None:
                        api_value = f"{side_label} {line_label}"
                        tg_values.append({"value": api_value, "odd": str(last)})
                        if opening is not None:
                            opening_index[(bm_name, "Goals Over/Under", api_value)] = opening
            if tg_values:
                bets.append({"name": "Goals Over/Under", "values": tg_values})

        # ── match_corners → "Corners Over/Under" ─────────────────────
        mc = markets.get("match_corners")
        if isinstance(mc, dict):
            mc_values = []
            for line_key, sides in mc.items():
                if not isinstance(sides, dict):
                    continue
                line_label = _ts_line_key_to_label(line_key)
                if line_label is None:
                    continue
                for side_key, side_label in (("over", "Over"), ("under", "Under")):
                    last, opening = _ts_extract_last_opening(sides.get(side_key))
                    if last is not None:
                        api_value = f"{side_label} {line_label}"
                        mc_values.append({"value": api_value, "odd": str(last)})
                        if opening is not None:
                            opening_index[(bm_name, "Corners Over/Under", api_value)] = opening
            if mc_values:
                bets.append({"name": "Corners Over/Under", "values": mc_values})

        # ── asian_handicap → "Asian Handicap" ────────────────────────
        # TheStatsAPI only exposes home/away sides (no explicit line label).
        # Pass through as-is; downstream resolvers may enrich the line.
        ah = markets.get("asian_handicap")
        if isinstance(ah, dict):
            ah_values = []
            for ts_key, api_value in (("home", "Home"), ("away", "Away")):
                last, opening = _ts_extract_last_opening(ah.get(ts_key))
                if last is not None:
                    ah_values.append({"value": api_value, "odd": str(last)})
                    if opening is not None:
                        opening_index[(bm_name, "Asian Handicap", api_value)] = opening
            if ah_values:
                bets.append({"name": "Asian Handicap", "values": ah_values})

        if bets:
            bookmakers_out.append({"name": bm_name, "bets": bets})

    if not bookmakers_out:
        return []

    return [{
        "bookmakers":    bookmakers_out,
        "_source":       "thestatsapi",
        "_opening_odds": {
            f"{bm}|{mkt}|{val}": opening
            for (bm, mkt, val), opening in opening_index.items()
        },
    }]
