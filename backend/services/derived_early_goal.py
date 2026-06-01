"""Derived Early-Goal Profile — backfills `early_goal_pct` from API-Sports
recent_fixtures when the SoccerSTATS scraper returns ``data_quality="missing"``.

Why this exists
---------------
SoccerSTATS only covers ~30 leagues with reliable HTML structure. For
teams in smaller leagues — or when Bright Data rate-limits — we need
a backup path that doesn't depend on scraping.

API-Sports already ships goal events with `time.elapsed` per fixture
inside the match enrichment payload. By scanning the last N played
fixtures of a team we can derive:

  * early_goal_pct        ← share of goals scored in minutes 1..15
  * early_concede_pct     ← share of goals conceded in minutes 1..15
  * first_half_goal_pct   ← share of goals scored in minutes 1..45
  * team_scored_first_half_pct  ← fraction of fixtures with ≥1 goal-for in 1H
  * team_conceded_first_half_pct ← fraction of fixtures with ≥1 goal-against in 1H

Expected fixture shape (matches the canonical recent_fixtures we
already attach during corner pre-fetch):

    {
        "fixture_id":    int,
        "team_id":       int,                  # the team we're computing FOR
        "goals_for":     int,
        "goals_against": int,
        "events": [
            {"type": "Goal", "minute": 7,  "team_for": True},
            {"type": "Goal", "minute": 38, "team_for": True},
            {"type": "Goal", "minute": 12, "team_for": False},
            ...
        ],
    }

If `events` is missing the fixture is ignored (we can still proceed
with the rest of the sample).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("derived_early_goal")


_EARLY_MAX_MIN     = 15
_FIRST_HALF_MAX_MIN = 45


def derive_early_goal_profile_from_fixtures(
    fixtures: list[dict],
    *,
    team_id: Optional[int] = None,
    team_name: Optional[str] = None,
    league: Optional[str] = None,
    season: Optional[str] = None,
) -> Optional[dict]:
    """Derive an `early_goal_profile` payload from a team's last fixtures.

    Returns None when no usable events were found. Otherwise returns a
    dict shaped identically to `soccerstats.fetch_team_early_goal_profile`
    (so the orchestrator can merge / prefer either source).
    """
    if not isinstance(fixtures, list) or not fixtures:
        return None

    gf_total = ga_total = 0
    gf_0_15  = ga_0_15  = 0
    gf_fh    = ga_fh    = 0
    fixtures_with_fh_goal_for     = 0
    fixtures_with_fh_goal_against = 0
    used = 0

    for f in fixtures:
        if not isinstance(f, dict):
            continue
        events = f.get("events") or f.get("goal_events") or []
        if not isinstance(events, list):
            continue
        scored_fh = False
        conceded_fh = False
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if (ev.get("type") or "").lower() != "goal":
                continue
            minute = ev.get("minute") or (ev.get("time") or {}).get("elapsed")
            try:
                minute = int(minute)
            except (TypeError, ValueError):
                continue
            # `team_for=True` → team scored. We also accept a `team_id` match.
            team_for = ev.get("team_for")
            if team_for is None and team_id is not None and ev.get("team_id") is not None:
                try:
                    team_for = int(ev["team_id"]) == int(team_id)
                except (TypeError, ValueError):
                    team_for = None
            if team_for is True:
                gf_total += 1
                if minute <= _EARLY_MAX_MIN:
                    gf_0_15 += 1
                if minute <= _FIRST_HALF_MAX_MIN:
                    gf_fh += 1
                    scored_fh = True
            elif team_for is False:
                ga_total += 1
                if minute <= _EARLY_MAX_MIN:
                    ga_0_15 += 1
                if minute <= _FIRST_HALF_MAX_MIN:
                    ga_fh += 1
                    conceded_fh = True

        # Only count the fixture if at least one goal was attributable.
        if events:
            used += 1
            if scored_fh:
                fixtures_with_fh_goal_for += 1
            if conceded_fh:
                fixtures_with_fh_goal_against += 1

    if used == 0 or (gf_total + ga_total) == 0:
        return None

    def _ratio(num: int, den: int) -> Optional[float]:
        return round(num / den, 3) if den > 0 else None

    if used >= 10:
        data_quality = "strong"
    elif used >= 7:
        data_quality = "usable"
    elif used >= 4:
        data_quality = "thin"
    else:
        data_quality = "missing"

    return {
        "early_goal_pct":              _ratio(gf_0_15, gf_total),
        "early_concede_pct":           _ratio(ga_0_15, ga_total),
        "early_goal_involvement_pct":  _ratio(gf_0_15 + ga_0_15, gf_total + ga_total),
        "first_half_goal_pct":         _ratio(gf_fh, gf_total),
        "team_scored_first_half_pct":  _ratio(fixtures_with_fh_goal_for, used),
        "team_conceded_first_half_pct":_ratio(fixtures_with_fh_goal_against, used),
        "goals_for_0_15":              gf_0_15,
        "goals_against_0_15":          ga_0_15,
        "goals_for_first_half":        gf_fh,
        "goals_against_first_half":    ga_fh,
        "total_goals_for":             gf_total,
        "total_goals_against":         ga_total,
        "sample_size":                 used,
        "league":                      league,
        "season":                      season,
        "source":                      "derived_api_sports",
        "source_url":                  None,
        "fetched_at":                  datetime.now(timezone.utc).isoformat(),
        "data_quality":                data_quality,
    }


def merge_early_goal_profiles(
    primary: Optional[dict], fallback: Optional[dict],
) -> Optional[dict]:
    """Prefer the higher-quality profile, but fill blanks from the other.

    Quality ordering: strong > usable > thin > missing > None.
    """
    rank = {"strong": 3, "usable": 2, "thin": 1, "missing": 0}
    if not primary and not fallback:
        return None
    if not primary:
        return fallback
    if not fallback:
        return primary
    p = rank.get(primary.get("data_quality"), 0)
    f = rank.get(fallback.get("data_quality"), 0)
    if p >= f:
        base, extra = primary, fallback
    else:
        base, extra = fallback, primary
    merged = dict(base)
    # Fill None fields from `extra` when `base` doesn't have them.
    for k, v in extra.items():
        if merged.get(k) is None and v is not None:
            merged[k] = v
    # Surface the secondary source for audit.
    secondary_src = extra.get("source")
    if secondary_src and secondary_src != merged.get("source"):
        merged["secondary_source"] = secondary_src
    return merged


__all__ = [
    "derive_early_goal_profile_from_fixtures",
    "merge_early_goal_profiles",
]
