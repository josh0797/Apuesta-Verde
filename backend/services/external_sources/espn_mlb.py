"""ESPN MLB Scoreboard scraper (via ESPN's public JSON API).

ESPN exposes a free JSON API at
  https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates=YYYYMMDD
That endpoint returns game status, venue, both teams, AND probable pitchers
when ESPN has them. No HTML parsing required.
"""
from __future__ import annotations

import logging
import re

from .base import direct_fetch_json

log = logging.getLogger("external_sources.espn_mlb")

NAME = "espn_mlb_scoreboard"
APPLICABLE_SPORTS = {"baseball"}
REQUIRES_UNLOCKER = False

URL_TEMPLATE = (
    "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
    "?dates={date}"
)


def _normalize_team(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


async def fetch_lineups(date_str: str) -> dict:
    """`date_str` may be either YYYY-MM-DD or YYYYMMDD; both accepted."""
    if not date_str:
        return {"matchups": {}, "sources_consulted": []}
    compact = date_str.replace("-", "")
    url = URL_TEMPLATE.format(date=compact)
    payload = await direct_fetch_json(url)
    if not payload or "events" not in payload:
        return {"matchups": {}, "sources_consulted": [{
            "source": NAME, "status": "failed", "url": url,
            "data_types": [], "reason": "fetch_failed",
        }]}

    matchups: dict[str, dict] = {}
    confirmed = 0
    for ev in payload.get("events") or []:
        try:
            competition = (ev.get("competitions") or [{}])[0]
            competitors = competition.get("competitors") or []
            if len(competitors) < 2:
                continue
            # ESPN orders [home, away] sometimes; normalize via homeAway field.
            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not (home and away):
                continue
            home_name = (home.get("team") or {}).get("displayName")
            away_name = (away.get("team") or {}).get("displayName")
            home_p = ((home.get("probables") or [{}])[0].get("athlete") or {}).get("displayName")
            away_p = ((away.get("probables") or [{}])[0].get("athlete") or {}).get("displayName")
            status = ((competition.get("status") or {}).get("type") or {}).get("name") or ""
            if not (home_name and away_name):
                continue
            key = f"{_normalize_team(away_name)}@{_normalize_team(home_name)}"
            matchups[key] = {
                "home_team":         home_name,
                "away_team":         away_name,
                "home_pitcher_name": home_p,
                "away_pitcher_name": away_p,
                # ESPN doesn't expose lineup confirmation; treat as projected
                # unless game is in progress.
                "status":            "projected" if (home_p or away_p) else "missing",
                "home_batting_order": [],
                "away_batting_order": [],
                "venue":             (competition.get("venue") or {}).get("fullName"),
                "espn_event_id":     ev.get("id"),
            }
            if home_p and away_p:
                confirmed += 1
        except Exception as exc:
            log.debug("espn_mlb event parse failed: %s", exc)
            continue

    status_overall = "success" if matchups else "failed"
    log.info("espn_mlb: %d events (%d with both pitchers)", len(matchups), confirmed)
    return {
        "matchups": matchups,
        "sources_consulted": [{
            "source":     NAME,
            "status":     status_overall,
            "url":        url,
            "data_types": ["probable_pitchers"] if matchups else [],
            "matchup_count": len(matchups),
            "confirmed_count": confirmed,
        }],
    }


__all__ = ["fetch_lineups", "NAME", "URL_TEMPLATE", "APPLICABLE_SPORTS"]
