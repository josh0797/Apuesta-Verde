"""Basketball Reference scraper — public HTML, no BrightData required."""
from __future__ import annotations

import logging
import re
from typing import Optional

from .base import direct_fetch, clean_text
from .schema import failed_evidence, make_evidence, skipped_evidence

log = logging.getLogger("external_sources.basketball_reference")

NAME = "basketball_reference"
APPLICABLE_SPORTS = {"basketball"}
REQUIRES_UNLOCKER = False

_SEARCH_URL = "https://www.basketball-reference.com/search/search.fcgi?search={q}"
_TEAM_LINK_RE = re.compile(r'href="(/teams/[A-Z]{3}/2[0-9]{3}\.html)"')


async def _team_url(team: str) -> Optional[str]:
    q = team.replace(" ", "+")
    html = await direct_fetch(_SEARCH_URL.format(q=q))
    if not html:
        return None
    m = _TEAM_LINK_RE.search(html)
    if m:
        return f"https://www.basketball-reference.com{m.group(1)}"
    return None


def _extract_team_bullets(html: str, team: str) -> list[str]:
    bullets: list[str] = []
    # Record
    m = re.search(r"Record:\s*</strong>\s*([0-9]+-[0-9]+)", html)
    if m:
        bullets.append(f"Récord {team}: {m.group(1)}")
    # Last10
    m = re.search(r"Last 10:\s*</strong>\s*([0-9]+-[0-9]+)", html)
    if m:
        bullets.append(f"Últimos 10 {team}: {m.group(1)}")
    # Pace
    m = re.search(r'data-stat="pace"[^>]*>([0-9\.]+)<', html)
    if m:
        bullets.append(f"Pace {team}: {m.group(1)}")
    # Off / Def rating
    m = re.search(r'data-stat="off_rtg"[^>]*>([0-9\.]+)<', html)
    if m:
        bullets.append(f"OffRtg {team}: {m.group(1)}")
    m = re.search(r'data-stat="def_rtg"[^>]*>([0-9\.]+)<', html)
    if m:
        bullets.append(f"DefRtg {team}: {m.group(1)}")
    return bullets


async def fetch(home: str, away: str, *, league: str = "", sport: str = "basketball", **_) -> dict:
    if sport not in APPLICABLE_SPORTS:
        return skipped_evidence(NAME, reason="sport_not_supported")
    try:
        bullets: list[str] = []
        first_url: Optional[str] = None
        for team in (home, away):
            if not team:
                continue
            url = await _team_url(team)
            if not url:
                continue
            first_url = first_url or url
            html = await direct_fetch(url)
            if html:
                bullets.extend(_extract_team_bullets(html, team)[:3])
        if not bullets:
            return failed_evidence(NAME, reason="no_data_found")
        return make_evidence(
            NAME,
            url=first_url or "https://www.basketball-reference.com",
            title=f"Basketball-Reference: {home} vs {away}",
            evidence_type="historical_trends",
            extracted_data=bullets,
            confidence=72,
            freshness="fresh",
        )
    except Exception as exc:
        log.warning("basketball_reference.fetch crashed: %s", exc)
        return failed_evidence(NAME, reason=f"unexpected:{exc}"[:120])
