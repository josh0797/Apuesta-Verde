"""F87.1 — Football Fixture Contract.

Single source of truth for the shape of a football fixture as it enters
``data_ingestion._enrich_football`` and the rest of the pipeline.

Every F87 discovery adapter (TheStatsAPI, API-Football, ESPN, Sofascore
PW, scrape.do) MUST flow through :func:`ensure_api_football_fixture_shape`
before the cascade does counting / short-circuit / merge / dedupe so the
downstream ``_enrich_football`` receives a uniform, nested shape:

    {
      "fixture": {"id", "date", "timestamp", "status": {"short"}, "venue"},
      "league":  {"id", "name", "country", "season"},
      "teams":   {"home": {"id", "name"}, "away": {"id", "name"}},
      "_external_source", "_external_source_id", "_discovery_source",
      "_is_national_team", "_is_international",
    }

Reason codes
------------
* ``FIXTURE_SHAPE_ALREADY_VALID``        — input was already nested.
* ``FIXTURE_SHAPE_NORMALIZED``           — flattened input promoted.
* ``FIXTURE_SHAPE_INVALID_MISSING_TEAMS`` — home or away name missing.
* ``FIXTURE_SHAPE_SYNTHETIC_ID_CREATED``  — id was missing; synthesised.

The function NEVER raises. ``None`` is returned for inputs that cannot
be promoted (missing team names or unparseable kickoff).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("services.football_fixture_contract")

# Reason codes (exported).
RC_ALREADY_VALID         = "FIXTURE_SHAPE_ALREADY_VALID"
RC_NORMALIZED            = "FIXTURE_SHAPE_NORMALIZED"
RC_INVALID_MISSING_TEAMS = "FIXTURE_SHAPE_INVALID_MISSING_TEAMS"
RC_SYNTHETIC_ID_CREATED  = "FIXTURE_SHAPE_SYNTHETIC_ID_CREATED"
RC_DATE_NAIVE_ASSUMED    = "FIXTURE_DATE_NAIVE_ASSUMED_UTC"


_INT_NAME_RX = re.compile(
    r"(?i)(world cup|nations league|conmebol|africa cup|asian cup|"
    r"copa america|euro\s|qualifi|friendl|club world|libertadores|"
    r"sudamericana|gold cup|afcon|concacaf|caf champions|"
    r"asian champions)",
)
_NT_NAME_RX = re.compile(
    r"(?i)(world cup|nations league|copa america|africa cup|"
    r"euro\s|gold cup|asian cup|qualifi|friendl)",
)


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ASCII", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _coerce_kickoff(value: Any) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Try to derive ``(timestamp_int, iso_string, reason_code)``."""
    if value is None:
        return (None, None, None)
    if isinstance(value, (int, float)):
        try:
            ts = int(value)
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            return (ts, iso, None)
        except (TypeError, ValueError, OverflowError, OSError):
            return (None, None, None)
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except (TypeError, ValueError):
            return (None, None, None)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            return (int(dt.timestamp()), dt.isoformat(), RC_DATE_NAIVE_ASSUMED)
        dt = dt.astimezone(timezone.utc)
        return (int(dt.timestamp()), dt.isoformat(), None)
    return (None, None, None)


def _pick(*candidates):
    for c in candidates:
        if c is not None and c != "":
            return c
    return None


def _dict_or_empty(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def ensure_api_football_fixture_shape(
    fx: Any,
    *,
    source: str = "unknown",
    reason_codes: Optional[list[str]] = None,
) -> Optional[dict]:
    """Normalise *any* discovery-source fixture into the canonical
    API-Football nested shape that ``_enrich_football`` consumes.

    Accepts both:
      * The nested API-Football shape (kept as-is, only fills gaps).
      * The flat F87 legacy shape used by the first F87 adapters.

    Returns ``None`` when home / away team names cannot be resolved or
    when the kickoff cannot be parsed. The ``reason_codes`` list, when
    provided, is appended to for diagnostics.
    """
    if reason_codes is None:
        reason_codes = []

    if not isinstance(fx, dict):
        reason_codes.append(RC_INVALID_MISSING_TEAMS)
        return None

    fixture_block = _dict_or_empty(fx.get("fixture"))
    league_block  = _dict_or_empty(fx.get("league"))
    teams_block   = _dict_or_empty(fx.get("teams"))

    home_block = _dict_or_empty(teams_block.get("home")
                                  or fx.get("home_team")
                                  or fx.get("home"))
    away_block = _dict_or_empty(teams_block.get("away")
                                  or fx.get("away_team")
                                  or fx.get("away"))

    home_name = _pick(home_block.get("name"), home_block.get("displayName"))
    away_name = _pick(away_block.get("name"), away_block.get("displayName"))
    if not home_name or not away_name:
        reason_codes.append(RC_INVALID_MISSING_TEAMS)
        return None

    home_id = _pick(home_block.get("id"), home_block.get("team_id"))
    away_id = _pick(away_block.get("id"), away_block.get("team_id"))

    # Kickoff resolution: nested first, then top-level legacy fields.
    raw_ts  = _pick(fixture_block.get("timestamp"), fx.get("timestamp"),
                     fx.get("kickoff_ts"))
    raw_iso = _pick(fixture_block.get("date"), fx.get("date"),
                     fx.get("kickoff_iso"), fx.get("commence_time"),
                     fx.get("starts_at"))
    ts, iso, ko_rc = (None, None, None)
    if raw_ts is not None:
        ts, iso, ko_rc = _coerce_kickoff(raw_ts)
    if (ts is None or iso is None) and raw_iso is not None:
        ts2, iso2, rc2 = _coerce_kickoff(raw_iso)
        ts  = ts  if ts  is not None else ts2
        iso = iso if iso is not None else iso2
        ko_rc = ko_rc or rc2
    if ko_rc:
        reason_codes.append(ko_rc)

    if ts is None or iso is None:
        # Cannot place the fixture on the timeline → unanalysable.
        reason_codes.append(RC_INVALID_MISSING_TEAMS)
        return None

    # Status — nested wins over flat.
    status_block = _dict_or_empty(fixture_block.get("status")
                                    or fx.get("status"))
    short = status_block.get("short")
    if not isinstance(short, str) or not short.strip():
        # Some MLB/basketball legacy paths store the status as a string.
        flat_status = fx.get("status")
        if isinstance(flat_status, str) and flat_status.strip():
            short = flat_status.strip()
        else:
            short = "NS"
    long_ = status_block.get("long") or short
    venue_block = _dict_or_empty(fixture_block.get("venue") or fx.get("venue"))

    # Fixture id — prefer nested, then top-level, then synthesise.
    fid = _pick(fixture_block.get("id"), fx.get("id"))
    if not fid:
        date_only = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        fid = f"{source}-{_slug(home_name)}-{_slug(away_name)}-{date_only}"
        reason_codes.append(RC_SYNTHETIC_ID_CREATED)
    fid = str(fid)

    # League season fallback: year of kickoff (Aug-Jul convention left to
    # downstream — the field is purely informational here).
    season = _pick(league_block.get("season"),
                   datetime.fromtimestamp(ts, tz=timezone.utc).year)

    league_name    = _pick(league_block.get("name"), fx.get("league")) or ""
    league_country = _pick(league_block.get("country"),
                            (league_block.get("category") or {}).get("name")
                              if isinstance(league_block.get("category"), dict) else None)
    league_id      = _pick(league_block.get("id"), league_block.get("league_id"))

    is_intl = bool(fx.get("_is_international")
                    or _INT_NAME_RX.search(league_name or ""))
    is_nt   = bool(fx.get("_is_national_team")
                    or _NT_NAME_RX.search(league_name or ""))

    ext_src    = _pick(fx.get("_external_source"), source)
    ext_src_id = _pick(fx.get("_external_source_id"),
                        fx.get("_thestatsapi_raw_id"), str(fid))

    already_valid = (
        isinstance(fx.get("fixture"), dict)
        and isinstance(fx["fixture"].get("id"), (str, int))
        and isinstance(fx["fixture"].get("status"), dict)
        and isinstance(fx.get("league"), dict)
        and isinstance(fx.get("teams"), dict)
    )
    reason_codes.append(RC_ALREADY_VALID if already_valid else RC_NORMALIZED)

    out = {
        "fixture": {
            "id":        fid,
            "date":      iso,
            "timestamp": ts,
            "status":    {"short": short, "long": long_},
            "venue":     {"name": venue_block.get("name"),
                           "city": venue_block.get("city")},
        },
        "league": {
            "id":      league_id,
            "name":    league_name,
            "country": league_country,
            "season":  season,
        },
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "_external_source":     ext_src,
        "_external_source_id":  str(ext_src_id) if ext_src_id is not None else None,
        "_thestatsapi_raw_id":  fx.get("_thestatsapi_raw_id"),
        "_discovery_source":    _pick(fx.get("_discovery_source"), source),
        "_is_national_team":    is_nt,
        "_is_international":    is_intl,
        # Mirror top-level fields too — multiple legacy call-sites still
        # reach for ``fx["timestamp"]`` / ``fx["status"]`` directly.
        "id":        fid,
        "date":      iso,
        "timestamp": ts,
        "status":    {"short": short, "long": long_},
    }
    return out


def normalize_bucket(fixtures: list[Any], *, source: str) -> tuple[list[dict], dict]:
    """Apply :func:`ensure_api_football_fixture_shape` to a whole bucket
    of fixtures. Returns ``(valid_fixtures, audit)`` where ``audit``
    aggregates the reason-code counts for the bucket.

    NEVER raises.
    """
    audit: dict = {
        "source":         source,
        "raw_count":      len(fixtures or []),
        "kept_count":     0,
        "dropped_count":  0,
        "reason_codes":   {},
    }
    out: list[dict] = []
    if not fixtures:
        return (out, audit)
    for f in fixtures:
        rcs: list[str] = []
        norm = ensure_api_football_fixture_shape(f, source=source, reason_codes=rcs)
        for rc in rcs:
            audit["reason_codes"][rc] = audit["reason_codes"].get(rc, 0) + 1
        if norm is None:
            audit["dropped_count"] += 1
            continue
        out.append(norm)
        audit["kept_count"] += 1
    return (out, audit)


__all__ = [
    "RC_ALREADY_VALID", "RC_NORMALIZED",
    "RC_INVALID_MISSING_TEAMS", "RC_SYNTHETIC_ID_CREATED",
    "RC_DATE_NAIVE_ASSUMED",
    "ensure_api_football_fixture_shape",
    "normalize_bucket",
]
