"""Multi-source injury fetcher (Phase 1 — Basketball).

Fails soft: any source raising an exception is captured under
``source_status`` and the merge continues with the remaining sources.

Sources (ordered by reliability):
    1. API-Sports basketball /injuries  (primary)
    2. TheStatsAPI                       (TODO — placeholder, returns empty)
    3. Bright Data → ESPN / Rotowire    (fallback, opt-in)
    4. Editorial context                 (complement, opt-in)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

log = logging.getLogger("injury_intelligence.sources")

# Feature flags so the operator can disable individual sources without
# code changes.
INJURY_USE_API_SPORTS = os.environ.get("INJURY_USE_API_SPORTS", "true").lower() in ("1", "true", "yes")
INJURY_USE_THESTATSAPI = os.environ.get("INJURY_USE_THESTATSAPI", "false").lower() in ("1", "true", "yes")
INJURY_USE_ESPN = os.environ.get("INJURY_USE_ESPN", "false").lower() in ("1", "true", "yes")
INJURY_USE_ROTOWIRE = os.environ.get("INJURY_USE_ROTOWIRE", "false").lower() in ("1", "true", "yes")

# Per-fetch timeout (we already have a higher-level cache so a transient
# miss is OK).
_SOURCE_TIMEOUT_SEC = 8


# ====================================================================
# 1) API-Sports basketball injuries
# ====================================================================
async def fetch_api_sports_basketball_injuries(
    *,
    team_id: int,
    season: str = "2024-2025",
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """Fetch injuries for a single basketball team from API-Sports.

    Returns ``{"records": [...], "status": "success|failed|skipped"}``.
    """
    if not INJURY_USE_API_SPORTS:
        return {"records": [], "status": "skipped", "reason": "feature_flag_off"}
    try:
        from .. import api_sports as aps
        if not aps.API_KEY:
            return {"records": [], "status": "skipped", "reason": "no_api_key"}
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=_SOURCE_TIMEOUT_SEC)
        try:
            raw = await aps._get(  # type: ignore[attr-defined]
                "basketball",
                client,
                "/injuries",
                params={"team": team_id, "season": season},
            )
        finally:
            if own_client:
                await client.aclose()
        records: list[dict] = []
        for row in (raw or {}).get("response", []) or []:
            if not isinstance(row, dict):
                continue
            player = row.get("player") or {}
            team = row.get("team") or {}
            records.append({
                "player_name":     player.get("name") or "",
                "player_id":       player.get("id"),
                "status":          row.get("reason") or row.get("type") or "",
                "injury_type":     row.get("type") or "",
                "expected_return": row.get("return") or "",
                "position":        (player.get("position") or ""),
                "source":          "api_sports",
                "source_url":      f"https://v1.basketball.api-sports.io/injuries?team={team_id}",
                "updated_at":      row.get("date") or datetime.now(timezone.utc).isoformat(),
                "confidence":      0.8,
                "team_id":         team.get("id") or team_id,
                "team_name":       team.get("name") or "",
            })
        return {"records": records, "status": "success" if records else "partial"}
    except Exception as exc:
        log.debug("api_sports basketball injuries failed: %s", exc)
        return {"records": [], "status": "failed", "reason": str(exc)[:120]}


# ====================================================================
# 2) TheStatsAPI — placeholder (returns empty until user wires real key)
# ====================================================================
async def fetch_thestatsapi_basketball_injuries(
    *,
    team_id: int | str,
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    if not INJURY_USE_THESTATSAPI:
        return {"records": [], "status": "skipped", "reason": "feature_flag_off"}
    api_key = os.environ.get("THESTATSAPI_KEY", "")
    if not api_key:
        return {"records": [], "status": "skipped", "reason": "no_api_key"}
    # NOTE: real wiring intentionally left as a placeholder — the user
    # said "design module to accept new keys when available" (option B).
    return {"records": [], "status": "skipped", "reason": "not_implemented"}


# ====================================================================
# 3) Bright Data — ESPN / Rotowire scraping (fallback)
# ====================================================================
async def fetch_bright_data_basketball_injuries(
    *,
    team_slug: str,
    source: str = "espn",
) -> dict:
    """Scrape ESPN or Rotowire injury reports via Bright Data unlocker.

    Fails soft — returns empty records if the scraper or parser fails.
    """
    if source == "espn" and not INJURY_USE_ESPN:
        return {"records": [], "status": "skipped", "reason": "feature_flag_off"}
    if source == "rotowire" and not INJURY_USE_ROTOWIRE:
        return {"records": [], "status": "skipped", "reason": "feature_flag_off"}
    try:
        from .. import brightdata_client as bd
        if source == "espn":
            url = f"https://www.espn.com/nba/team/injuries/_/name/{team_slug}"
        elif source == "rotowire":
            url = f"https://www.rotowire.com/basketball/team/{team_slug}/injuries"
        else:
            return {"records": [], "status": "skipped", "reason": "unknown_source"}
        resp = await bd.fetch_unlocked(url, timeout=_SOURCE_TIMEOUT_SEC)
        if not resp or not resp.get("ok"):
            return {"records": [], "status": "failed",
                    "reason": (resp or {}).get("error", "unlocker_failed")[:120]}
        # Parsing is best-effort — we just return the raw HTML for now and
        # the operator can implement the parser later. The orchestrator
        # accepts the empty-records path gracefully.
        return {"records": [], "status": "partial", "reason": "parser_not_implemented",
                "html_size": len(resp.get("body") or "")}
    except Exception as exc:
        log.debug("bright_data %s injuries failed: %s", source, exc)
        return {"records": [], "status": "failed", "reason": str(exc)[:120]}


__all__ = [
    "fetch_api_sports_basketball_injuries",
    "fetch_thestatsapi_basketball_injuries",
    "fetch_bright_data_basketball_injuries",
]
