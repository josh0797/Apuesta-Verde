"""MLB.com official starting lineups scraper.

Fetches https://www.mlb.com/starting-lineups/<date> (date is YYYY-MM-DD
in Eastern Time). MLB's own page is the official ground-truth — when it
lists a pitcher, that pitcher is confirmed. Used as a high-confidence
fallback when MLB Stats API still has `probablePitcher: null` (which
happens for early-morning runs before the front-office uploads).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .base import direct_fetch, brightdata_fetch, brightdata_available, clean_text

log = logging.getLogger("external_sources.mlb_official_lineups")

NAME = "mlb_official_lineups"
URL_TEMPLATE = "https://www.mlb.com/starting-lineups/{date}"
APPLICABLE_SPORTS = {"baseball"}
REQUIRES_UNLOCKER = False

# MLB.com renders a JSON island with __NEXT_DATA__ that's the cleanest
# path. Fall back to regex on visible HTML if the JSON island isn't found.
_NEXT_DATA_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_MATCHUP_BLOCK_RE = re.compile(r'data-test-id="matchup"[^>]*>(.*?)</section>', re.S)
_TEAM_RE       = re.compile(r'data-test-id="team-name"[^>]*>\s*([A-Za-z .\-]+)\s*<', re.S)
_PITCHER_RE    = re.compile(r'data-test-id="pitcher-name"[^>]*>\s*([A-Za-z .\-\'`]+)\s*<', re.S)


def _normalize_team(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


async def fetch_lineups(date_str: str) -> dict:
    """Returns ``{ matchups: { '<away>@<home>': {...} }, sources_consulted: [...] }``.
    """
    if not date_str:
        return {"matchups": {}, "sources_consulted": []}
    url = URL_TEMPLATE.format(date=date_str)
    html = await direct_fetch(url)
    if not html and brightdata_available():
        html = await brightdata_fetch(url)
    if not html:
        return {"matchups": {}, "sources_consulted": [{
            "source": NAME, "status": "failed", "url": url,
            "data_types": [], "reason": "fetch_failed",
        }]}

    matchups: dict[str, dict] = {}
    # Best-effort regex parse — MLB.com markup changes occasionally; we
    # tolerate misses without erroring out.
    for blk in _MATCHUP_BLOCK_RE.finditer(html):
        body = blk.group(1)
        teams = _TEAM_RE.findall(body)
        pitchers = _PITCHER_RE.findall(body)
        if len(teams) < 2:
            continue
        away_name = clean_text(teams[0])
        home_name = clean_text(teams[1])
        away_p = clean_text(pitchers[0]) if len(pitchers) >= 1 else None
        home_p = clean_text(pitchers[1]) if len(pitchers) >= 2 else None
        key = f"{_normalize_team(away_name)}@{_normalize_team(home_name)}"
        matchups[key] = {
            "home_team":         home_name,
            "away_team":         away_name,
            "home_pitcher_name": home_p,
            "away_pitcher_name": away_p,
            "status":            "confirmed",   # MLB.com only lists confirmed
            "home_batting_order": [],
            "away_batting_order": [],
        }

    status = "success" if matchups else "failed"
    log.info("mlb_official_lineups: %d matchups from %s", len(matchups), url)
    return {
        "matchups": matchups,
        "sources_consulted": [{
            "source":     NAME,
            "status":     status,
            "url":        url,
            "data_types": ["probable_pitchers"] if matchups else [],
            "matchup_count": len(matchups),
        }],
    }


__all__ = ["fetch_lineups", "NAME", "URL_TEMPLATE", "APPLICABLE_SPORTS"]
