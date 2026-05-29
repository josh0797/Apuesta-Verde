"""NBA Stats API — undocumented public JSON used by nba.com/stats.

We use a couple of common endpoints to surface very lightweight context
per team. NO BrightData required (the endpoint accepts plain UA headers).
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import direct_fetch_json
from .schema import failed_evidence, make_evidence, skipped_evidence

log = logging.getLogger("external_sources.nba_stats")

NAME = "nba_stats"
APPLICABLE_SPORTS = {"basketball"}
REQUIRES_UNLOCKER = False

# Public NBA stats endpoint. They REQUIRE specific browser-like headers.
_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

_LEAGUE_STANDINGS_URL = (
    "https://stats.nba.com/stats/leaguestandingsv3?"
    "GroupBy=conf&LeagueID=00&Season=2024-25&SeasonType=Regular+Season&Section=overall"
)


async def fetch(home: str, away: str, *, league: str = "", sport: str = "basketball", **_) -> dict:
    if sport not in APPLICABLE_SPORTS:
        return skipped_evidence(NAME, reason="sport_not_supported")
    try:
        data = await direct_fetch_json(_LEAGUE_STANDINGS_URL, headers=_HEADERS, timeout_sec=8.0)
        if not data:
            return failed_evidence(NAME, reason="nba_stats_blocked_or_empty")
        rs = (data.get("resultSets") or [])
        if not rs:
            return failed_evidence(NAME, reason="empty_payload")
        headers = rs[0].get("headers") or []
        rows    = rs[0].get("rowSet") or []
        # Find TeamCity / TeamName column indices.
        try:
            i_name = headers.index("TeamName")
            i_w    = headers.index("WINS")
            i_l    = headers.index("LOSSES")
            i_pct  = headers.index("WinPCT")
            i_rank = headers.index("PlayoffRank")
            i_last10 = headers.index("L10")
        except ValueError:
            return failed_evidence(NAME, reason="schema_mismatch")
        bullets: list[str] = []
        first_url: Optional[str] = None
        for team in (home, away):
            if not team:
                continue
            match_row = next((r for r in rows if team.lower() in str(r[i_name]).lower()), None)
            if match_row:
                bullets.append(
                    f"NBA {team}: {match_row[i_w]}-{match_row[i_l]} "
                    f"({float(match_row[i_pct] or 0):.3f}) "
                    f"seed #{match_row[i_rank]}, L10 {match_row[i_last10]}"
                )
                first_url = "https://www.nba.com/standings"
        if not bullets:
            return failed_evidence(NAME, reason="teams_not_found_in_standings")
        return make_evidence(
            NAME,
            url=first_url or "https://www.nba.com/stats",
            title=f"NBA standings: {home} vs {away}",
            evidence_type="standings_context",
            extracted_data=bullets,
            confidence=80,
            freshness="fresh",
        )
    except Exception as exc:
        log.warning("nba_stats.fetch crashed: %s", exc)
        return failed_evidence(NAME, reason=f"unexpected:{exc}"[:120])
