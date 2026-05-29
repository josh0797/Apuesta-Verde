"""FBref scraper — football statistics tables, public HTML.

FBref publishes match previews and historical data. We use a lightweight
direct fetch (no BrightData) — the site doesn't block reasonable
datacenter traffic.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .base import direct_fetch, clean_text
from .schema import failed_evidence, make_evidence, skipped_evidence

log = logging.getLogger("external_sources.fbref")

NAME = "fbref"
APPLICABLE_SPORTS = {"football"}
REQUIRES_UNLOCKER = False

_SEARCH_URL = "https://fbref.com/search/search.fcgi?search={q}"

# Lightweight pattern to find first team profile link.
_TEAM_LINK_RE = re.compile(r'href="(/en/squads/[a-z0-9]+/[A-Za-z0-9\-]+(?:Stats|history|-Stats)?)"')


async def _fetch_team_profile_url(team: str) -> Optional[str]:
    q = team.replace(" ", "+")
    html = await direct_fetch(_SEARCH_URL.format(q=q))
    if not html:
        return None
    m = _TEAM_LINK_RE.search(html)
    if m:
        return f"https://fbref.com{m.group(1)}"
    return None


def _extract_form_bullets(html: str, team: str) -> list[str]:
    bullets: list[str] = []
    # Last results — look for the "Last 5" rows in the recent matches section.
    # FBref has tables with td headers like 'Result'. We grep a few easy-to-spot
    # phrases instead of full pandas to keep the scraper light.
    last5 = re.findall(r'(?:Result|Resultado)[^<]*<[^>]*>([WLD])<', html)
    if last5:
        bullets.append(f"FBref forma reciente {team}: {' '.join(last5[:5])}")
    # xG / xGA totals (season)
    m = re.search(r"data-stat=\"xg\"[^>]*>([0-9\.]+)<", html)
    if m:
        bullets.append(f"xG temporada {team}: {m.group(1)}")
    m = re.search(r"data-stat=\"xg_against\"[^>]*>([0-9\.]+)<", html)
    if m:
        bullets.append(f"xGA temporada {team}: {m.group(1)}")
    return bullets


async def fetch(home: str, away: str, *, league: str = "", sport: str = "football", **_) -> dict:
    if sport not in APPLICABLE_SPORTS:
        return skipped_evidence(NAME, reason="sport_not_supported")
    try:
        bullets: list[str] = []
        first_url: Optional[str] = None
        for team in (home, away):
            if not team:
                continue
            url = await _fetch_team_profile_url(team)
            if not url:
                continue
            first_url = first_url or url
            html = await direct_fetch(url)
            if html:
                bullets.extend(_extract_form_bullets(html, team)[:2])
        if not bullets:
            return failed_evidence(NAME, reason="no_data_found")
        return make_evidence(
            NAME,
            url=first_url or "https://fbref.com",
            title=f"FBref: {home} vs {away}",
            evidence_type="historical_trends",
            extracted_data=bullets,
            confidence=70,
            freshness="fresh",
        )
    except Exception as exc:
        log.warning("fbref.fetch crashed: %s", exc)
        return failed_evidence(NAME, reason=f"unexpected:{exc}"[:120])
