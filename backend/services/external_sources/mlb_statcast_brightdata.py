"""Bright Data Web Unlocker scraper for Statcast / Baseball Savant / FanGraphs.

This is a **lightweight scaffold** that the `mlb_statcast_adapter`
imports lazily as a fallback when pybaseball is unavailable or returns
empty data. The real scraping logic is intentionally kept thin in this
first iteration — the adapter pattern means we can plug in heavier
scraping (parsing DOM, retries, normalization) later without changing
any caller.

Public surface (used by `mlb_statcast_adapter.fetch_with_brightdata`):

    async def fetch_advanced(*, player_id, player_name, team_id, team_name,
                             season, role) -> dict

Returns a partial payload shaped like the other adapter sources::

    {
        "source_status":  "success" | "failed" | "skipped",
        "pitcher" | "batting" | "team": {...},
        "_reason":  str (when not success),
    }

Fail-soft contract:
  * If `BRIGHTDATA_TOKEN` is missing → `skipped`.
  * Any HTTP / parse error → `failed` (never raises).
  * Empty response → `failed` with `_reason="empty"`.

The actual URL endpoints are intentionally not hardcoded here yet —
when the user provides scraping targets we'll switch this stub into a
proper parser. Until then it returns `skipped` so the adapter merges
cleanly with the other sources.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


def _has_brightdata_config() -> bool:
    return bool(
        (os.environ.get("BRIGHTDATA_TOKEN") or "").strip()
        and (os.environ.get("BRIGHTDATA_ZONE") or "").strip()
    )


async def fetch_advanced(
    *,
    player_id: Any = None,
    player_name: str | None = None,
    team_id: Any = None,
    team_name: str | None = None,
    season: int | None = None,
    role: str | None = None,
) -> dict:
    """Stub implementation — returns `skipped` while real scraping
    targets are not configured. Safe to invoke from the adapter."""
    if not _has_brightdata_config():
        return {"source_status": "skipped", "_reason": "no_brightdata_config"}

    # TODO (Batch B+): wire actual Baseball Savant / FanGraphs scrapers
    # via services.external_sources.base.brightdata_get(). The contract
    # is in place; only the parser is pending so the adapter doesn't
    # block on this.
    log.debug(
        "[brightdata_statcast] scaffold-only fetch for role=%s id=%s name=%s — returning skipped",
        role, player_id or team_id, player_name or team_name,
    )
    return {
        "source_status": "skipped",
        "_reason": "scaffold_only_pending_parser",
        "_meta": {
            "role":   role,
            "season": season,
            "subject": player_name or team_name or str(player_id or team_id or ""),
        },
    }
