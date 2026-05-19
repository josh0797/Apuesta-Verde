"""Fallback data layer: when API-Football fails, fetch basic fixtures + scores
from public sources (ESPN unofficial JSON, plus a thin Sofascore parser).
We keep this minimal — only the data we can confidently pull from public endpoints.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("fallback")


async def espn_soccer_scoreboard(client: httpx.AsyncClient) -> list[dict]:
    url = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard"
    try:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("ESPN fallback failed: %s", e)
        return []
    out = []
    for ev in (data.get("events") or [])[:50]:
        try:
            comp = (ev.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors if c.get("homeAway") == "away"), {})
            status = (comp.get("status") or {}).get("type", {})
            out.append({
                "id": f"espn-{ev.get('id')}",
                "source": "espn_fallback",
                "league": (ev.get("league", {}) or {}).get("name") or (comp.get("league") or ""),
                "kickoff_iso": ev.get("date"),
                "status": status.get("description"),
                "is_live": status.get("state") in ("in", "live"),
                "home_team": {
                    "name": (home.get("team") or {}).get("displayName", "Home"),
                    "id": int(home.get("id") or 0) or None,
                    "score": int(home.get("score", 0) or 0),
                },
                "away_team": {
                    "name": (away.get("team") or {}).get("displayName", "Away"),
                    "id": int(away.get("id") or 0) or None,
                    "score": int(away.get("score", 0) or 0),
                },
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            continue
    return out
