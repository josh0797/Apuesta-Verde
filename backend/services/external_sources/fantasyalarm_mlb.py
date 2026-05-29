"""FantasyAlarm MLB Lineups scraper.

Fetches https://www.fantasyalarm.com/mlb/lineups. FantasyAlarm publishes
early-morning projected lineups + late scratches; useful as a 6th
opinion to flag SOURCE_CONFLICT.
"""
from __future__ import annotations

import logging
import re

from .base import direct_fetch, brightdata_fetch, brightdata_available, clean_text

log = logging.getLogger("external_sources.fantasyalarm_mlb")

NAME = "fantasyalarm_mlb_lineups"
URL = "https://www.fantasyalarm.com/mlb/lineups"
APPLICABLE_SPORTS = {"baseball"}
REQUIRES_UNLOCKER = False

_GAME_RE = re.compile(
    r'<div[^>]*class="[^"]*lineup-card[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>', re.S,
)
_TEAM_RE     = re.compile(r'class="team-name"[^>]*>\s*([A-Za-z .\-]+?)\s*<', re.S)
_PITCHER_RE  = re.compile(
    r'class="probable-pitcher[^"]*"[^>]*>\s*<[^>]+>\s*([A-Za-z .\-\'`]+?)\s*<', re.S,
)
_STATUS_RE   = re.compile(r'class="lineup-status[^"]*"[^>]*>\s*(Confirmed|Projected|Expected)', re.I)
_SCRATCH_RE  = re.compile(r'(Scratched|Late scratch)', re.I)


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
    scratch_warnings = 0
    for g in _GAME_RE.finditer(html):
        body = g.group(1)
        teams    = _TEAM_RE.findall(body)
        pitchers = _PITCHER_RE.findall(body)
        status_m = _STATUS_RE.search(body)
        status   = (status_m.group(1).lower() if status_m else "projected")
        if len(teams) < 2:
            continue
        away_name = clean_text(teams[0])
        home_name = clean_text(teams[1])
        away_p    = clean_text(pitchers[0]) if pitchers else None
        home_p    = clean_text(pitchers[1]) if len(pitchers) >= 2 else None
        if not (home_p or away_p):
            continue
        if _SCRATCH_RE.search(body):
            scratch_warnings += 1
        key = f"{_normalize_team(away_name)}@{_normalize_team(home_name)}"
        matchups[key] = {
            "home_team":          home_name,
            "away_team":          away_name,
            "home_pitcher_name":  home_p,
            "away_pitcher_name":  away_p,
            "status":             status,
            "home_batting_order": [],
            "away_batting_order": [],
        }
        if status == "confirmed":
            confirmed += 1

    status_overall = "success" if matchups else "failed"
    log.info(
        "fantasyalarm_mlb: %d matchups (%d confirmed, %d scratch warnings)",
        len(matchups), confirmed, scratch_warnings,
    )
    return {
        "matchups": matchups,
        "sources_consulted": [{
            "source":     NAME,
            "status":     status_overall,
            "url":        url,
            "data_types": ["probable_pitchers"] if matchups else [],
            "matchup_count":   len(matchups),
            "confirmed_count": confirmed,
            "scratch_warnings": scratch_warnings,
        }],
    }


__all__ = ["fetch_lineups", "NAME", "URL", "APPLICABLE_SPORTS"]
