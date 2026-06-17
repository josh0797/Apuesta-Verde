"""Sprint-D4 · The Odds API (historical snapshots) client.

Lightweight, fail-soft client for The Odds API v4 historical-odds
endpoint:

    GET /v4/historical/sports/{sport}/odds
        ?apiKey={apiKey}
        &regions={regions}
        &markets={markets}
        &date={ISO8601 UTC timestamp}
        &oddsFormat=decimal

The API returns the snapshot **at or just before** the supplied
``date`` parameter, which is exactly what we want for pre-kickoff
backtest odds (we never see odds set after the match started).

Local caching
-------------
Every snapshot fetch is keyed by ``(sport, regions, markets, date)``
and cached on disk at ``/tmp/the_odds_api_cache/`` as a JSON file.
This avoids burning quota on repeated backtest runs and lets the
tests work fully offline once cached.

Strict invariants
-----------------
* Fail-soft: any network error / non-2xx → returns ``None`` (the
  caller decides what to do).
* Never raises. Never blocks app startup.
* No production wiring: this module is only consumed by Sprint-D4
  backtest scripts and the matching test suite.

Public API
----------
.. code-block:: python

    from services.external_sources.the_odds_api_client import (
        get_historical_odds_snapshot,
    )
    snap = await get_historical_odds_snapshot(
        sport="soccer_epl",
        date="2024-08-17T11:00:00Z",
        regions="uk,eu",
        markets="h2h",
    )
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("the_odds_api_client")

# ─── Configuration ─────────────────────────────────────────────────────
BASE_URL: str         = "https://api.the-odds-api.com/v4"
DEFAULT_REGIONS: str  = "uk,eu"
DEFAULT_MARKETS: str  = "h2h"
DEFAULT_ODDS_FMT: str = "decimal"
HTTP_TIMEOUT_SEC: float = 30.0
CACHE_DIR: str        = "/tmp/the_odds_api_cache"

# Soccer sport-keys for backtest scope (extend as needed).
SPORT_KEY_EPL    = "soccer_epl"
SPORT_KEY_WC2022 = "soccer_fifa_world_cup"
SPORT_KEY_EURO24 = "soccer_uefa_european_championship"


def _api_key() -> Optional[str]:
    """Return the API key from env. We never hardcode it."""
    return (os.environ.get("THE_ODDS_API_KEY")
             or os.environ.get("ODDS_API_KEY") or None)


def _cache_path(sport: str, date: str, regions: str, markets: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    key_str = f"{sport}|{date}|{regions}|{markets}"
    digest  = hashlib.sha1(key_str.encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{digest}.json")


def _read_cache(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("cache read failed: %s", exc)
        return None


def _write_cache(path: str, payload: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except OSError as exc:
        log.debug("cache write failed: %s", exc)


async def get_historical_odds_snapshot(
    *,
    sport: str,
    date: str,
    regions: str = DEFAULT_REGIONS,
    markets: str = DEFAULT_MARKETS,
    use_cache: bool = True,
) -> Optional[dict]:
    """Fetch one historical snapshot. Returns the raw JSON dict or
    ``None`` on failure.

    Parameters
    ----------
    sport
        e.g. ``"soccer_epl"``, ``"soccer_fifa_world_cup"``.
    date
        ISO8601 UTC timestamp, e.g. ``"2024-08-17T11:00:00Z"``.
    regions
        Comma-separated, default ``"uk,eu"``.
    markets
        Comma-separated, default ``"h2h"``.
    use_cache
        Default True. Disable for tests that need a real fetch.
    """
    cache_path = _cache_path(sport, date, regions, markets)
    if use_cache:
        cached = _read_cache(cache_path)
        if cached is not None:
            return cached

    api_key = _api_key()
    if not api_key:
        log.warning("THE_ODDS_API_KEY not set; cannot fetch historical odds")
        return None

    url = f"{BASE_URL}/historical/sports/{sport}/odds"
    params = {
        "apiKey":     api_key,
        "regions":    regions,
        "markets":    markets,
        "date":       date,
        "oddsFormat": DEFAULT_ODDS_FMT,
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                log.warning(
                    "the_odds_api returned %s for sport=%s date=%s",
                    r.status_code, sport, date,
                )
                return None
            payload = r.json()
            if use_cache:
                _write_cache(cache_path, payload)
            return payload
    except Exception as exc:    # noqa: BLE001
        log.warning("the_odds_api fetch failed: %s", exc)
        return None


def extract_match_odds(
    snapshot: dict, *,
    home_team: str, away_team: str,
    market: str = "h2h",
) -> Optional[dict]:
    """From a historical snapshot payload, extract the odds row for
    ``(home_team, away_team)``. Returns a dict::

        {
          "odd_home":  <float>,
          "odd_draw":  <float | None>,
          "odd_away":  <float>,
          "bookmaker": <str>,
          "last_update": <str>,
        }

    or ``None`` if not found / malformed.

    Team-name matching is case-insensitive substring; users with
    multi-language datasets may need to normalise upstream.
    """
    if not snapshot:
        return None
    events = (snapshot.get("data") if isinstance(snapshot, dict) else
              snapshot) or []
    if isinstance(events, dict):
        events = events.get("events") or []
    h_lc = (home_team or "").lower().strip()
    a_lc = (away_team or "").lower().strip()
    for ev in events:
        ev_home = (ev.get("home_team") or "").lower().strip()
        ev_away = (ev.get("away_team") or "").lower().strip()
        if not (h_lc in ev_home or ev_home in h_lc):
            continue
        if not (a_lc in ev_away or ev_away in a_lc):
            continue
        # Use the first bookmaker that provides ``market``.
        for bm in ev.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt.get("key") != market:
                    continue
                outcomes = {(o.get("name") or "").lower(): o.get("price")
                             for o in mkt.get("outcomes", [])}
                # Soccer h2h markets emit three outcomes named after the
                # teams + literal "Draw".
                oh = outcomes.get(ev_home)
                oa = outcomes.get(ev_away)
                od = outcomes.get("draw")
                if oh and oa:
                    return {
                        "odd_home":   float(oh),
                        "odd_draw":   float(od) if od else None,
                        "odd_away":   float(oa),
                        "bookmaker":  bm.get("title") or bm.get("key"),
                        "last_update": mkt.get("last_update"),
                    }
    return None


# ════════════════════════════════════════════════════════════════════════
# Sprint-E.1 · Live (current) odds endpoints
# ════════════════════════════════════════════════════════════════════════
#
# These two helpers extend the historical-only client to also fetch the
# **current** odds snapshot for an event. They are used by
# ``services.live_odds_monitor`` to populate ``odds_snapshots`` with
# fresh prices for the matches that are visible in the latest pick run.
#
# Strict invariants (same as the historical helper above):
#   * Fail-soft: any error → returns ``None``; never raises.
#   * Never blocks app startup.
#   * Respects ``THE_ODDS_API_KEY`` from env; no hardcoded keys.
#   * Surfaces rate-limit headers (``x-requests-remaining`` /
#     ``x-requests-used``) so the caller can degrade gracefully.

# Live polling never goes through the on-disk cache by default — we want
# fresh prices on every cycle. The constant is kept here so tests can
# override it if needed.
LIVE_USE_CACHE_DEFAULT: bool = False


def _extract_quota_headers(headers) -> dict:
    """Return a small dict with the API quota headers, fail-soft.

    The Odds API exposes ``x-requests-remaining`` and ``x-requests-used``
    on every response. We surface them so the live odds monitor can log
    /react when quota is low. Missing headers → ``None`` values.
    """
    try:
        remaining = headers.get("x-requests-remaining")
        used      = headers.get("x-requests-used")
        last_cost = headers.get("x-requests-last")
        return {
            "remaining": int(remaining) if remaining is not None else None,
            "used":      int(used) if used is not None else None,
            "last_cost": int(last_cost) if last_cost is not None else None,
        }
    except (TypeError, ValueError, AttributeError):
        return {"remaining": None, "used": None, "last_cost": None}


async def fetch_events(
    *,
    sport: str,
    regions: str = DEFAULT_REGIONS,
    use_cache: bool = False,
) -> Optional[dict]:
    """Fetch the list of **upcoming events** for a given sport key.

    Endpoint: ``GET /v4/sports/{sport}/events``.

    Returns a dict::

        {
            "events": [
                {"id": "abc123", "sport_key": "soccer_epl",
                 "commence_time": "2026-01-15T15:00:00Z",
                 "home_team": "Arsenal", "away_team": "Chelsea"},
                ...
            ],
            "quota": {"remaining": N, "used": M, "last_cost": K}
        }

    Used to resolve our internal ``match_id`` → Odds API ``event_id``
    mappings. Fail-soft: returns ``None`` on any failure.
    """
    api_key = _api_key()
    if not api_key:
        log.warning("THE_ODDS_API_KEY not set; cannot fetch live events")
        return None

    cache_path = _cache_path(sport, "events", regions, "events")
    if use_cache:
        cached = _read_cache(cache_path)
        if cached is not None:
            return cached

    url = f"{BASE_URL}/sports/{sport}/events"
    params = {"apiKey": api_key, "regions": regions}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            r = await client.get(url, params=params)
            quota = _extract_quota_headers(r.headers)
            if r.status_code != 200:
                log.warning(
                    "the_odds_api fetch_events returned %s for sport=%s "
                    "(quota_remaining=%s)",
                    r.status_code, sport, quota.get("remaining"),
                )
                return None
            data = r.json() or []
            payload = {"events": data if isinstance(data, list) else [],
                       "quota":  quota}
            if use_cache:
                _write_cache(cache_path, payload)
            return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("the_odds_api fetch_events failed: %s", exc)
        return None


async def fetch_current_odds(
    *,
    sport: str,
    regions: str = DEFAULT_REGIONS,
    markets: str = DEFAULT_MARKETS,
    event_ids: Optional[list] = None,
    use_cache: bool = LIVE_USE_CACHE_DEFAULT,
) -> Optional[dict]:
    """Fetch **current (live)** odds for a sport key.

    Endpoint: ``GET /v4/sports/{sport}/odds``.

    If ``event_ids`` is given we forward it as the ``eventIds`` query
    parameter so the API returns only the events we care about (saves
    quota when polling a small universe).

    Returns a dict::

        {
            "events": [ ... raw event docs (with bookmakers) ... ],
            "quota":  {"remaining": ..., "used": ..., "last_cost": ...}
        }

    Fail-soft: returns ``None`` on any failure (network, non-2xx, parse).
    """
    api_key = _api_key()
    if not api_key:
        log.warning("THE_ODDS_API_KEY not set; cannot fetch live odds")
        return None

    cache_key_extra = "|".join(sorted(event_ids)) if event_ids else "all"
    cache_path = _cache_path(sport, f"live|{cache_key_extra}",
                              regions, markets)
    if use_cache:
        cached = _read_cache(cache_path)
        if cached is not None:
            return cached

    url = f"{BASE_URL}/sports/{sport}/odds"
    params = {
        "apiKey":     api_key,
        "regions":    regions,
        "markets":    markets,
        "oddsFormat": DEFAULT_ODDS_FMT,
    }
    if event_ids:
        # The API supports a comma-separated `eventIds` filter.
        params["eventIds"] = ",".join(event_ids)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            r = await client.get(url, params=params)
            quota = _extract_quota_headers(r.headers)
            if r.status_code != 200:
                log.warning(
                    "the_odds_api fetch_current_odds returned %s "
                    "for sport=%s (quota_remaining=%s)",
                    r.status_code, sport, quota.get("remaining"),
                )
                return None
            data = r.json() or []
            payload = {"events": data if isinstance(data, list) else [],
                       "quota":  quota}
            if use_cache:
                _write_cache(cache_path, payload)
            return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("the_odds_api fetch_current_odds failed: %s", exc)
        return None


__all__ = [
    "BASE_URL", "DEFAULT_REGIONS", "DEFAULT_MARKETS",
    "DEFAULT_ODDS_FMT", "CACHE_DIR", "HTTP_TIMEOUT_SEC",
    "SPORT_KEY_EPL", "SPORT_KEY_WC2022", "SPORT_KEY_EURO24",
    "get_historical_odds_snapshot",
    "extract_match_odds",
    "fetch_events",
    "fetch_current_odds",
    "_api_key", "_cache_path", "_extract_quota_headers",
    "LIVE_USE_CACHE_DEFAULT",
]
