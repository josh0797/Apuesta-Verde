"""RotoGrinders MLB Lineups scraper.

Fetches https://rotogrinders.com/lineups/mlb and extracts projected /
confirmed starting lineups + batting orders. DFS-oriented but exposes
the same probable-pitcher data we need. Used as a 5th opinion alongside
RotoWire / MLB.com / FantasyPros / ESPN.
"""
from __future__ import annotations

import logging
import re

from .base import direct_fetch, brightdata_fetch, brightdata_available, clean_text

log = logging.getLogger("external_sources.rotogrinders_mlb")

NAME = "rotogrinders_mlb_lineups"
URL = "https://rotogrinders.com/lineups/mlb"
APPLICABLE_SPORTS = {"baseball"}
REQUIRES_UNLOCKER = False

# RotoGrinders renders matchups inside .blk__matchup blocks with .team
# blocks. Both projected and confirmed lineups expose the same SP slot.
_MATCHUP_RE = re.compile(
    r'<div[^>]*class="[^"]*blk__matchup[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
    re.S,
)
_TEAM_NAME_RE = re.compile(r'class="team[^"]*"[^>]*>\s*([A-Za-z .\-]+?)\s*<', re.S)
_PITCHER_RE = re.compile(
    r'class="player pitcher[^"]*"[^>]*>.*?<a[^>]*>\s*([A-Za-z .\-\'`]+)\s*</a>',
    re.S,
)
_STATUS_RE = re.compile(r'class="status[^"]*"[^>]*>\s*(Confirmed|Expected|Projected)', re.I)


def _normalize_team(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


async def fetch_lineups(date_str: str = "") -> dict:
    url = URL
    html = await direct_fetch(url)
    if not html and brightdata_available():
        html = await brightdata_fetch(url)
    if not html:
        return {"matchups": {}, "sources_consulted": [{
            "source": NAME, "status": "failed", "url": url,
            "data_types": [], "reason": "fetch_failed",
        }]}

    matchups: dict[str, dict] = {}
    confirmed = 0
    for m in _MATCHUP_RE.finditer(html):
        body = m.group(1)
        teams    = _TEAM_NAME_RE.findall(body)
        pitchers = _PITCHER_RE.findall(body)
        status_m = _STATUS_RE.search(body)
        status   = (status_m.group(1).lower() if status_m else "projected")
        if len(teams) < 2 or len(pitchers) < 2:
            continue
        away_name = clean_text(teams[0])
        home_name = clean_text(teams[1])
        away_p    = clean_text(pitchers[0])
        home_p    = clean_text(pitchers[1])
        key = f"{_normalize_team(away_name)}@{_normalize_team(home_name)}"
        matchups[key] = {
            "home_team":          home_name,
            "away_team":          away_name,
            "home_pitcher_name":  home_p or None,
            "away_pitcher_name":  away_p or None,
            "status":             status,
            "home_batting_order": [],
            "away_batting_order": [],
        }
        if status == "confirmed":
            confirmed += 1

    status_overall = "success" if matchups else "failed"
    log.info("rotogrinders_mlb: %d matchups (%d confirmed)", len(matchups), confirmed)
    return {
        "matchups": matchups,
        "sources_consulted": [{
            "source":     NAME,
            "status":     status_overall,
            "url":        url,
            "data_types": ["probable_pitchers"] if matchups else [],
            "matchup_count":   len(matchups),
            "confirmed_count": confirmed,
        }],
    }


__all__ = ["fetch_lineups", "NAME", "URL", "APPLICABLE_SPORTS"]
