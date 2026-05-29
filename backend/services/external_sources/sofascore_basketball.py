"""SofaScore Basketball scraper.

Fetches https://www.sofascore.com/basketball/livescore — covers NBA + many
international leagues that ESPN doesn't surface. Used as a backup source
when ESPN NBA returns 0 for the day or when the user analyses non-NBA
basketball.

SofaScore exposes a JSON API at:
  https://api.sofascore.com/api/v1/sport/basketball/scheduled-events/{date}
No API key required for the public schedule endpoint.
"""
from __future__ import annotations

import logging
import re

from .base import direct_fetch_json

log = logging.getLogger("external_sources.sofascore_basketball")

NAME = "sofascore_basketball"
APPLICABLE_SPORTS = {"basketball"}
REQUIRES_UNLOCKER = False
URL_TEMPLATE = "https://api.sofascore.com/api/v1/sport/basketball/scheduled-events/{date}"


def _norm_team(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


async def fetch_matchups(date_str: str) -> dict:
    """Returns ``{matchups: {<away>@<home>: ...}, sources_consulted: [...]}``.
    `date_str` should be YYYY-MM-DD.
    """
    if not date_str:
        return {"matchups": {}, "sources_consulted": []}
    url = URL_TEMPLATE.format(date=date_str)
    data = await direct_fetch_json(url)
    if not data:
        return {"matchups": {}, "sources_consulted": [{
            "source": NAME, "status": "failed", "url": url,
            "data_types": [], "reason": "fetch_failed",
        }]}

    matchups: dict[str, dict] = {}
    events = data.get("events") or []
    for ev in events:
        try:
            home = (ev.get("homeTeam") or {}).get("name")
            away = (ev.get("awayTeam") or {}).get("name")
            if not (home and away):
                continue
            league = ((ev.get("tournament") or {}).get("name") or "")
            # Skip nothing — we want NBA + EuroLeague + LNB + etc.
            status_obj = ev.get("status") or {}
            status_type = (status_obj.get("type") or "")
            kickoff_ts = ev.get("startTimestamp")
            key = f"{_norm_team(away)}@{_norm_team(home)}"
            matchups[key] = {
                "home_team":   home,
                "away_team":   away,
                "league":      league,
                "status":      status_type,
                "kickoff_ts":  int(kickoff_ts) if kickoff_ts else None,
                "sofascore_id": ev.get("id"),
            }
        except Exception as exc:
            log.debug("sofascore event parse failed: %s", exc)
            continue

    status = "success" if matchups else "failed"
    log.info("sofascore_basketball: %d matchups for %s", len(matchups), date_str)
    return {
        "matchups": matchups,
        "sources_consulted": [{
            "source":     NAME,
            "status":     status,
            "url":        url,
            "data_types": ["schedule", "team_meta"] if matchups else [],
            "matchup_count": len(matchups),
        }],
    }


__all__ = ["fetch_matchups", "NAME", "URL_TEMPLATE", "APPLICABLE_SPORTS"]
