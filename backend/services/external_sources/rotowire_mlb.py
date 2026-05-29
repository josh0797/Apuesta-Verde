"""RotoWire MLB Daily Lineups scraper.

Fetches https://www.rotowire.com/baseball/daily-lineups.php and extracts
starting pitchers + projected/confirmed lineups for every MLB game of the
day. RotoWire publishes lineups several hours before first pitch, often
before MLB Stats API has them, so this is our highest-yield fallback for
MLB pitcher confirmation.

Function `fetch_lineups(date_str)` returns a dict keyed by
`"<away_team>@<home_team>"` (lowercase, normalized) for easy lookup
from the orchestrator.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .base import direct_fetch, brightdata_fetch, brightdata_available, clean_text

log = logging.getLogger("external_sources.rotowire_mlb")

NAME = "rotowire_mlb_lineups"
URL  = "https://www.rotowire.com/baseball/daily-lineups.php"
APPLICABLE_SPORTS = {"baseball"}
REQUIRES_UNLOCKER = False  # try direct first, brightdata as backup

# Match a game card. RotoWire HTML uses repeating `.lineup` blocks with
# `.lineup__team` for the team name and `.lineup__player` rows.
_TEAM_BLOCK_RE  = re.compile(r'<div[^>]*class="[^"]*lineup__main[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>', re.S)
_TEAM_NAME_RE   = re.compile(r'class="lineup__team[^"]*"[^>]*>\s*(?:<[^>]+>)?\s*([A-Za-z .\-]+?)\s*<', re.S)
_PITCHER_LI_RE  = re.compile(r'class="lineup__player-highlight-name"[^>]*>\s*<a[^>]*>\s*([A-Za-z .\-\'`]+)\s*</a>', re.S)
_PLAYER_LI_RE   = re.compile(r'class="lineup__player[^"]*"[^>]*>(.*?)</li>', re.S)
_PLAYER_NAME_RE = re.compile(r'<a[^>]*title="([^"]+)"', re.S)
_PLAYER_POS_RE  = re.compile(r'class="lineup__pos"[^>]*>\s*([A-Z0-9]+)\s*<', re.S)
_STATUS_RE      = re.compile(r'is-(confirmed|projected|expected)', re.I)


def _normalize_team(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


async def fetch_lineups(date_str: str = "") -> dict:
    """Returns ``{ matchups: { '<away>@<home>': {...} }, sources_consulted: [...] }``
    where each value carries home/away pitchers + (optionally) batting orders.
    Always returns a dict; never raises.
    """
    url = URL + (f"?date={date_str}" if date_str else "")
    html = await direct_fetch(url)
    if not html and brightdata_available():
        html = await brightdata_fetch(url)
    if not html:
        log.debug("rotowire_mlb: empty HTML for %s", url)
        return {"matchups": {}, "sources_consulted": [{
            "source": NAME, "status": "failed", "url": url,
            "data_types": [], "reason": "fetch_failed",
        }]}

    # RotoWire renders pairs of consecutive .lineup__main divs (visitor +
    # home). We split by `.lineup is-`<status> wrappers and then per team.
    # Use a forgiving heuristic: find every (status, team_name, pitcher)
    # triple in document order.
    games_html = re.split(r'<div[^>]+class="[^"]*lineup is-mlb[^"]*"', html)
    matchups: dict[str, dict] = {}
    confirmed = 0
    for chunk in games_html[1:]:
        # Find the two team names + pitchers inside this game card.
        team_blocks = _TEAM_BLOCK_RE.findall(chunk[:35000])
        names = _TEAM_NAME_RE.findall(chunk[:35000])
        pitchers = _PITCHER_LI_RE.findall(chunk[:35000])
        status_m = _STATUS_RE.search(chunk[:2000])
        status = (status_m.group(1).lower() if status_m else "projected")
        if len(names) < 2 or len(pitchers) < 2:
            continue
        away_name = clean_text(names[0])
        home_name = clean_text(names[1])
        away_p    = clean_text(pitchers[0])
        home_p    = clean_text(pitchers[1])
        key = f"{_normalize_team(away_name)}@{_normalize_team(home_name)}"

        # Try to pull batting order rows (best-effort; not always available)
        away_order: list[dict] = []
        home_order: list[dict] = []
        for i, players_chunk in enumerate(team_blocks[:2]):
            order_out = (away_order if i == 0 else home_order)
            for li in _PLAYER_LI_RE.finditer(players_chunk):
                pname_m = _PLAYER_NAME_RE.search(li.group(1))
                ppos_m  = _PLAYER_POS_RE.search(li.group(1))
                if pname_m:
                    order_out.append({
                        "spot":     len(order_out) + 1,
                        "name":     clean_text(pname_m.group(1)),
                        "position": (ppos_m.group(1).strip() if ppos_m else None),
                    })
                if len(order_out) >= 9:
                    break

        matchups[key] = {
            "home_team":          home_name,
            "away_team":          away_name,
            "home_pitcher_name":  home_p or None,
            "away_pitcher_name":  away_p or None,
            "status":             status,  # 'confirmed' | 'projected' | 'expected'
            "home_batting_order": home_order,
            "away_batting_order": away_order,
        }
        if status == "confirmed":
            confirmed += 1

    source_status = "success" if matchups else "failed"
    data_types = []
    if matchups:
        data_types.append("probable_pitchers")
        if any(m["home_batting_order"] or m["away_batting_order"] for m in matchups.values()):
            data_types.append("projected_lineups" if confirmed == 0 else "confirmed_lineups")

    log.info("rotowire_mlb: %d matchups (%d confirmed) from %s",
             len(matchups), confirmed, url)
    return {
        "matchups": matchups,
        "sources_consulted": [{
            "source":     NAME,
            "status":     source_status,
            "url":        url,
            "data_types": data_types,
            "matchup_count": len(matchups),
            "confirmed_count": confirmed,
        }],
    }


__all__ = ["fetch_lineups", "NAME", "URL", "APPLICABLE_SPORTS"]
