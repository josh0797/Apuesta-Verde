"""F87.a — TheStatsAPI fixture discovery adapter.

Wraps the existing ``thestatsapi_client.fetch_fixtures`` to produce the
*exact* shape that ``data_ingestion._discover_football_fixtures``
consumes (i.e. the API-Football "next-48h" shape that the legacy
ingest pipeline already understands).

Public surface
--------------
* :func:`fetch_fixtures_next_48h` — primary discovery entry point.
* Reason-code constants (all ``THESTATSAPI_FIXTURES_*``) for diagnostics.

Fail-soft contract: never raises; always returns
``(fixtures, reason_codes)``.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger("services.thestatsapi_fixtures_adapter")

# Reason codes published in the audit trail.
RC_DISABLED    = "THESTATSAPI_FIXTURES_DISABLED"
RC_TIMEOUT     = "THESTATSAPI_FIXTURES_TIMEOUT"
RC_EMPTY       = "THESTATSAPI_FIXTURES_EMPTY"
RC_SUCCESS     = "THESTATSAPI_FIXTURES_SUCCESS"
RC_HTTP_ERROR  = "THESTATSAPI_FIXTURES_HTTP_ERROR"
RC_EXCEPTION   = "THESTATSAPI_FIXTURES_EXCEPTION"

DEFAULT_TIMEOUT_S = 12.0

# Heuristic regex for national/international competitions (used to flag
# fixtures without league_id alias coverage).
_INT_NAME_RX = re.compile(
    r"(?i)(world cup|nations league|conmebol|africa cup|asian cup|"
    r"copa america|euro\s|qualifi|friendl|club world|libertadores|"
    r"sudamericana|gold cup|afcon|concacaf|caf champions|"
    r"asian champions)",
)
_INT_COUNTRY_TOKENS: frozenset[str] = frozenset({
    "", "world", "international", "europe", "south-america", "north-america",
    "africa", "asia", "oceania",
})


def _flag_enabled() -> bool:
    raw = (os.environ.get("ENABLE_THESTATSAPI_FIXTURES_PRIMARY") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _coerce_ts(value: Any) -> Optional[int]:
    """Coerce ``value`` into a UNIX timestamp (seconds). Returns ``None``
    when the value cannot be parsed."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        try:
            return int(datetime.fromisoformat(s).timestamp())
        except (TypeError, ValueError):
            return None
    return None


def _is_national_or_international(league_name: str,
                                   country: Optional[str]) -> tuple[bool, bool]:
    """Return ``(is_national_team, is_international)`` heuristics."""
    name = league_name or ""
    cnorm = (country or "").strip().lower()
    intl_by_country = cnorm in _INT_COUNTRY_TOKENS
    intl_by_name    = bool(_INT_NAME_RX.search(name))
    is_international = intl_by_country or intl_by_name
    # National-team flag covers a stricter subset.
    is_national_team = bool(
        re.search(r"(?i)(world cup|nations league|copa america|"
                  r"africa cup|euro\s|gold cup|asian cup|qualifi|"
                  r"friendl)", name)
    )
    return is_national_team, is_international


def _normalise_fixture(raw: dict) -> Optional[dict]:
    """Convert a TheStatsAPI match dict into the API-Football "next-48h"
    shape consumed by ``data_ingestion.ingest_upcoming``. Returns
    ``None`` for unparseable rows."""
    if not isinstance(raw, dict):
        return None

    # TheStatsAPI keys are not 100% stable across tenants. Probe a few.
    mid = raw.get("id") or raw.get("match_id") or raw.get("uuid")
    if mid is None:
        return None
    mid = str(mid)

    # Timestamp / kickoff iso
    # NB: TheStatsAPI returns ``utc_date`` (ISO-8601 with milliseconds) on
    # the ``/football/matches`` endpoint; older / live endpoints use
    # ``timestamp`` or ``kickoff_iso``. F98: ``utc_date`` was missing
    # which silently dropped every TheStatsAPI fixture (regression after
    # 2026-05 schema change).
    ts_raw = (
        raw.get("timestamp")
        or raw.get("kickoff_ts")
        or raw.get("start_timestamp")
        or raw.get("kickoff_at")
        or raw.get("starts_at")
        or raw.get("kickoff_iso")
        or raw.get("utc_date")
        or raw.get("date")
    )
    ts = _coerce_ts(ts_raw)
    iso = None
    if ts is not None:
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    elif isinstance(ts_raw, str):
        iso = ts_raw

    if ts is None or iso is None:
        return None

    # Status (TheStatsAPI returns "scheduled", "live", "finished" …).
    raw_status = raw.get("status") or {}
    if isinstance(raw_status, dict):
        short = raw_status.get("short") or raw_status.get("code")
    else:
        short = str(raw_status or "").strip()
    short = (short or "").strip()
    # Map TheStatsAPI status → API-Football short codes.
    _MAP = {
        "scheduled": "NS", "not_started": "NS", "ns": "NS", "upcoming": "NS",
        "live":      "1H", "in_play":     "1H", "in-progress": "1H",
        "half_time": "HT", "ht":          "HT",
        "finished":  "FT", "ft":          "FT", "ended": "FT", "completed": "FT",
        "postponed": "PST", "cancelled":  "CANC", "canceled":  "CANC",
    }
    short_norm = _MAP.get(short.lower(), short or "NS")

    # League
    league_raw = raw.get("league") or raw.get("competition") or {}
    if not isinstance(league_raw, dict):
        league_raw = {"name": str(league_raw)}
    league_name    = league_raw.get("name") or league_raw.get("title") or ""
    league_country = league_raw.get("country") or league_raw.get("country_name")
    league_id      = league_raw.get("id") or league_raw.get("league_id")
    is_nt, is_intl = _is_national_or_international(league_name, league_country)

    # Teams — TheStatsAPI uses either ``{home, away}`` or ``{teams:{home,away}}``.
    teams_block = raw.get("teams") or {}
    if isinstance(teams_block, dict):
        home_raw = teams_block.get("home") or raw.get("home") or raw.get("home_team") or {}
        away_raw = teams_block.get("away") or raw.get("away") or raw.get("away_team") or {}
    else:
        home_raw = raw.get("home_team") or {}
        away_raw = raw.get("away_team") or {}
    if not isinstance(home_raw, dict):
        home_raw = {"name": str(home_raw)}
    if not isinstance(away_raw, dict):
        away_raw = {"name": str(away_raw)}

    home_name = home_raw.get("name") or home_raw.get("display_name")
    away_name = away_raw.get("name") or away_raw.get("display_name")
    # F87.1 Parte 1.5 — never fabricate "Home"/"Away" placeholders.
    # Return None so the contract audit captures the raw rejection with
    # full home/away candidate evidence instead of silently passing.
    if not isinstance(home_name, str) or not home_name.strip():
        return None
    if not isinstance(away_name, str) or not away_name.strip():
        return None
    home_id   = home_raw.get("id") or home_raw.get("team_id")
    away_id   = away_raw.get("id") or away_raw.get("team_id")
    home_tsid = home_raw.get("_thestatsapi_id") or home_raw.get("uuid") or home_id
    away_tsid = away_raw.get("_thestatsapi_id") or away_raw.get("uuid") or away_id

    venue = raw.get("venue") or {}
    if not isinstance(venue, dict):
        venue = {"name": str(venue)}

    return {
        # API-Football canonical keys consumed downstream.
        "id":        mid,
        "fixture": {
            "id":        mid,
            "date":      iso,
            "timestamp": ts,
            "status":    {"short": short_norm, "long": short},
            "venue":     {"name": venue.get("name"), "city": venue.get("city")},
        },
        "league": {
            "id":      league_id,
            "name":    league_name,
            "country": league_country,
            "_thestatsapi_id": str(league_raw.get("_thestatsapi_id")
                                   or league_raw.get("id") or mid),
        },
        "teams": {
            "home": {"id": home_id, "name": home_name,
                      "_thestatsapi_id": str(home_tsid) if home_tsid is not None else None},
            "away": {"id": away_id, "name": away_name,
                      "_thestatsapi_id": str(away_tsid) if away_tsid is not None else None},
        },
        # F87.a discovery metadata.
        "_external_source":     "thestatsapi",
        "_external_source_id":  mid,
        "_thestatsapi_raw_id":  mid,
        "_is_national_team":    is_nt,
        "_is_international":    is_intl,
        # Top-level mirrors for legacy consumers.
        "date":      iso,
        "timestamp": ts,
        "status":    {"short": short_norm},
    }


async def fetch_fixtures_next_48h(
    client: httpx.AsyncClient,
    *,
    date_iso: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> tuple[list[dict], list[str]]:
    """Primary discovery entry point.

    Returns ``(fixtures, reason_codes)`` where ``fixtures`` already
    matches the API-Football "next-48h" shape so ``ingest_upcoming``
    consumes it without per-source conditionals.
    """
    codes: list[str] = []
    if not _flag_enabled():
        codes.append(RC_DISABLED)
        return ([], codes)

    try:
        from . import thestatsapi_client as ts
    except Exception as exc:  # noqa: BLE001
        log.warning("[thestatsapi_fixtures] import failed: %s", exc)
        codes.append(RC_DISABLED)
        return ([], codes)

    if not ts.is_enabled() or not ts.get_api_key():
        codes.append(RC_DISABLED)
        return ([], codes)

    today = datetime.now(timezone.utc).date()
    df = date_iso or today.isoformat()
    dt = (today + timedelta(days=2)).isoformat()

    try:
        # Wrap the existing fetch_fixtures with a tighter timeout than
        # the global httpx client; F87 spec demands ≤ timeout_s.
        import asyncio as _aio
        raw_list = await _aio.wait_for(
            ts.fetch_fixtures(client, date_from=df, date_to=dt),
            timeout=timeout_s,
        )
    except _aio.TimeoutError:
        codes.append(RC_TIMEOUT)
        log.warning("[thestatsapi_fixtures] timed out after %.1fs", timeout_s)
        return ([], codes)
    except httpx.HTTPError as exc:
        codes.append(RC_HTTP_ERROR)
        log.warning("[thestatsapi_fixtures] HTTP error: %s", exc)
        return ([], codes)
    except Exception as exc:  # noqa: BLE001
        codes.append(RC_EXCEPTION)
        log.warning("[thestatsapi_fixtures] unexpected: %s", exc)
        return ([], codes)

    if not isinstance(raw_list, list) or not raw_list:
        codes.append(RC_EMPTY)
        return ([], codes)

    out: list[dict] = []
    for r in raw_list:
        normalised = _normalise_fixture(r)
        if normalised is not None:
            out.append(normalised)

    if not out:
        codes.append(RC_EMPTY)
    else:
        codes.append(RC_SUCCESS)
    log.info("[thestatsapi_fixtures] normalised %d/%d fixtures (codes=%s)",
              len(out), len(raw_list), codes)
    return (out, codes)


__all__ = [
    "RC_DISABLED", "RC_TIMEOUT", "RC_EMPTY", "RC_SUCCESS",
    "RC_HTTP_ERROR", "RC_EXCEPTION",
    "DEFAULT_TIMEOUT_S",
    "fetch_fixtures_next_48h",
    "_normalise_fixture",  # exposed for tests
]
