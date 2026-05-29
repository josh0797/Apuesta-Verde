"""Flashscore Basketball scraper.

Fetches https://www.flashscore.com/basketball/. Flashscore covers nearly
every international league + NBA/WNBA, often with quarter-level live
scoring. Used as a 3rd basketball source alongside ESPN + SofaScore.

Note: Flashscore's HTML is heavily client-rendered, so we mostly rely on
the Brightdata unlocker. The direct fetch is a best-effort fallback.
"""
from __future__ import annotations

import logging
import re

from .base import direct_fetch, brightdata_fetch, brightdata_available, clean_text

log = logging.getLogger("external_sources.flashscore_basketball")

NAME = "flashscore_basketball"
URL = "https://www.flashscore.com/basketball/"
APPLICABLE_SPORTS = {"basketball"}
REQUIRES_UNLOCKER = True

# Flashscore embeds events as div blocks with classes like
# "event__match event__match--scheduled" — pre-rendered HTML when the
# unlocker renders the page.
_EVENT_RE = re.compile(
    r'<div[^>]+class="event__match[^"]*"[^>]*>(.*?)</div>\s*</div>', re.S,
)
_HOME_RE = re.compile(r'class="event__participant--home"[^>]*>\s*([^<]+?)\s*<', re.S)
_AWAY_RE = re.compile(r'class="event__participant--away"[^>]*>\s*([^<]+?)\s*<', re.S)
_TIME_RE = re.compile(r'class="event__time"[^>]*>\s*([0-9:]+)', re.S)
_STAGE_RE = re.compile(r'class="event__stage[^"]*"[^>]*>\s*([^<]+?)\s*<', re.S)


def _norm_team(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


async def fetch_matchups(date_str: str = "") -> dict:
    url = URL
    html = None
    if brightdata_available():
        html = await brightdata_fetch(url)
    if not html:
        html = await direct_fetch(url)
    if not html:
        return {"matchups": {}, "sources_consulted": [{
            "source": NAME, "status": "failed", "url": url,
            "data_types": [], "reason": "fetch_failed",
        }]}

    matchups: dict[str, dict] = {}
    for ev in _EVENT_RE.finditer(html):
        body = ev.group(1)
        home_m = _HOME_RE.search(body)
        away_m = _AWAY_RE.search(body)
        time_m = _TIME_RE.search(body)
        stage_m = _STAGE_RE.search(body)
        if not (home_m and away_m):
            continue
        home = clean_text(home_m.group(1))
        away = clean_text(away_m.group(1))
        key = f"{_norm_team(away)}@{_norm_team(home)}"
        matchups[key] = {
            "home_team":     home,
            "away_team":     away,
            "start_time":    time_m.group(1) if time_m else None,
            "stage":         clean_text(stage_m.group(1)) if stage_m else None,
            "sport":         "basketball",
        }

    status = "success" if matchups else "failed"
    log.info("flashscore_basketball: %d matchups", len(matchups))
    return {
        "matchups": matchups,
        "sources_consulted": [{
            "source":     NAME,
            "status":     status,
            "url":        url,
            "data_types": ["schedule"] if matchups else [],
            "matchup_count": len(matchups),
        }],
    }


__all__ = ["fetch_matchups", "NAME", "URL", "APPLICABLE_SPORTS"]
