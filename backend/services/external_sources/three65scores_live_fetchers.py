"""Sprint F.3 — production fetchers for 365Scores identity resolution.

Provides the two async functions that
:func:`three65scores_identity_resolver.resolve_match_identity`
expects:

* ``games_fetcher(date_iso)`` — list of 365Scores game dicts for one
  calendar day, used by the search-by-context path.
* ``game_detail_fetcher(game_id)`` — full game payload (with
  ``competitors`` and ``startTime``), used by the URL path to validate
  team IDs against names.

Both are fail-soft: on transport failure they return an empty container
(``[]`` or ``{}``) so the resolver gracefully degrades to
``SOURCE_UNAVAILABLE`` / ``NOT_FOUND``.

The functions are split from the resolver itself so the resolver stays
pure / unit-testable (no HTTP). Production code wires these in;
tests inject mocks.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from ..scrape_do_client import fetch_via_scrapedo_result, is_enabled

log = logging.getLogger(__name__)

WEBWS_BASE = "https://webws.365scores.com/web"
DEFAULT_TIMEOUT_S = 35.0
DEFAULT_TIMEZONE  = "UTC"
DEFAULT_GEO       = "mx"
DEFAULT_LANG_ID   = 1
SPORT_ID_FOOTBALL = 1


def _build_allscores_url(date_ddmmyyyy: str,
                          *, sport_id: int = SPORT_ID_FOOTBALL,
                          timezone_name: str = DEFAULT_TIMEZONE,
                          lang_id: int = DEFAULT_LANG_ID) -> str:
    return (
        f"{WEBWS_BASE}/games/allscores/?appTypeId=5&langId={lang_id}"
        f"&timezoneName={timezone_name}&sports={sport_id}"
        f"&startDate={date_ddmmyyyy}&endDate={date_ddmmyyyy}"
    )


def _build_game_detail_url(game_id: str,
                            *, timezone_name: str = DEFAULT_TIMEZONE,
                            lang_id: int = DEFAULT_LANG_ID) -> str:
    return (
        f"{WEBWS_BASE}/game/?appTypeId=5&langId={lang_id}"
        f"&timezoneName={timezone_name}&gameId={game_id}"
    )


def _iso_to_ddmmyyyy(date_iso: str) -> str:
    """Translate ``YYYY-MM-DD[...]`` into ``DD/MM/YYYY`` (the format
    365Scores expects on the listing endpoint).
    """
    if not isinstance(date_iso, str) or len(date_iso) < 10:
        return ""
    try:
        dt = datetime.strptime(date_iso[:10], "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        return ""


async def _fetch_json(url: str) -> Any:
    """Fail-soft JSON GET via Scrape.do."""
    if not is_enabled():
        log.info("[365scores_live] SCRAPEDO_TOKEN missing — fetch aborted")
        return None
    try:
        res = await fetch_via_scrapedo_result(
            url, timeout=DEFAULT_TIMEOUT_S, render=False, geo=DEFAULT_GEO,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("[365scores_live] transport raised: %s", exc)
        return None
    if not res.get("ok"):
        log.info("[365scores_live] transport not ok: rc=%s status=%s",
                 res.get("reason_code"), res.get("status_code"))
        return None
    body = res.get("html") or ""
    try:
        return json.loads(body)
    except (ValueError, TypeError) as exc:
        log.info("[365scores_live] json parse failed: %s", exc)
        return None


async def fetch_games_by_date(date_iso: str) -> list[dict]:
    """Implementation of the ``games_fetcher`` contract."""
    day = _iso_to_ddmmyyyy(date_iso)
    if not day:
        return []
    url = _build_allscores_url(day)
    data = await _fetch_json(url)
    if isinstance(data, dict):
        games = data.get("games")
        if isinstance(games, list):
            return games
    if isinstance(data, list):
        return data
    return []


async def fetch_game_detail(game_id: str) -> dict:
    """Implementation of the ``game_detail_fetcher`` contract."""
    if not game_id:
        return {}
    url = _build_game_detail_url(str(game_id))
    data = await _fetch_json(url)
    if isinstance(data, dict):
        return data
    return {}


__all__ = [
    "fetch_games_by_date", "fetch_game_detail",
]
