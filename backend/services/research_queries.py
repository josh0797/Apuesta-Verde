"""Per-match research query builder.

Generates targeted, intent-tagged queries to fetch the smallest possible set
of signals (team news, motivation, odds movement, recent form, h2h, live)
for a SPECIFIC match — instead of broad searches that flood the system with
irrelevant results.

Queries are grouped by intent. Each group carries:
  - max_results: cap for whichever search/scraper consumes the query
  - freshness_required: bool — should the consumer prefer fresh results?

A per-tier budget caps the total number of queries actually emitted per match:
  Tier 1 → 8   Tier 2 → 5   Tier 3 → 3

The function is PURE: it builds the queries and does not execute them. The
consumer (e.g. injury_sources, future LLM tools/agents) decides what to do.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .football_competitions import get_competition_meta


TIER_BUDGETS = {
    "tier_1": 8,
    "tier_2": 5,
    "tier_3": 3,
}

# Intent priority — higher = added first when budget is tight.
INTENT_PRIORITY = {
    "team_news":         100,  # injuries / suspensions / scandals
    "motivation_context": 90,
    "live_context":       85,
    "odds_context":       70,
    "recent_form":        60,
    "h2h":                40,
}


def _date_str(iso: Optional[str]) -> str:
    if not iso:
        return datetime.utcnow().strftime("%B %Y")
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return datetime.utcnow().strftime("%B %Y")
    return dt.strftime("%B %Y")


def _season_str(iso: Optional[str]) -> str:
    if not iso:
        return str(datetime.utcnow().year)
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return str(datetime.utcnow().year)
    # Sept–May seasons span two years (e.g. "2025/26")
    if dt.month >= 7:
        return f"{dt.year}/{str(dt.year + 1)[-2:]}"
    return f"{dt.year - 1}/{str(dt.year)[-2:]}"


def build_match_research_queries(match: dict) -> dict:
    """Return grouped research queries for a single match.

    Inputs (from the match_doc): home_team.name, away_team.name, league,
    kickoff_iso, is_live.

    Output schema:
      {
        "team_news":          [ {query, intent, max_results, freshness_required}, ... ],
        "motivation_context": [...],
        "odds_context":       [...],
        "recent_form":        [...],
        "h2h":                [...],
        "live_context":       [...],          # only if match.is_live
        "_meta": {
          "home": str, "away": str, "competition": str,
          "tier": str|None, "budget": int, "emitted": int
        }
      }
    """
    home = ((match.get("home_team") or {}).get("name") or "").strip()
    away = ((match.get("away_team") or {}).get("name") or "").strip()
    competition = (match.get("competition_canonical_name") or match.get("league") or "").strip()
    kickoff = match.get("kickoff_iso")
    date_label = _date_str(kickoff)
    season_label = _season_str(kickoff)
    is_live = bool(match.get("is_live"))

    tier = match.get("competition_tier") or (get_competition_meta(competition) or {}).get("tier")
    budget = TIER_BUDGETS.get(tier, 3)

    def q(query: str, intent: str, max_results: int, freshness_required: bool = True) -> dict:
        return {
            "query": query.strip(),
            "intent": intent,
            "max_results": max_results,
            "freshness_required": freshness_required,
        }

    groups: dict[str, list[dict]] = {
        "team_news": [
            q(f"{home} vs {away} team news {competition} {date_label}", "team_news", 3, True),
            q(f"{home} injuries suspensions vs {away}",                  "team_news", 3, True),
            q(f"{away} injuries suspensions vs {home}",                  "team_news", 3, True),
        ],
        "motivation_context": [
            q(f"{home} {away} standings motivation {competition} {season_label}", "motivation_context", 3, True),
            q(f"{home} relegation playoff title race context",                    "motivation_context", 2, True),
            q(f"{away} relegation playoff title race context",                    "motivation_context", 2, True),
        ],
        "odds_context": [
            q(f"{home} vs {away} odds movement",                              "odds_context", 2, True),
            q(f"{home} vs {away} betting preview {competition}",              "odds_context", 2, True),
        ],
        "recent_form": [
            q(f"{home} recent form last 5 matches", "recent_form", 2, False),
            q(f"{away} recent form last 5 matches", "recent_form", 2, False),
        ],
        "h2h": [
            q(f"{home} vs {away} head to head", "h2h", 2, False),
        ],
    }
    if is_live:
        groups["live_context"] = [
            q(f"{home} vs {away} live stats",          "live_context", 2, True),
            q(f"{home} vs {away} live score momentum", "live_context", 2, True),
            q(f"{home} vs {away} current match stats", "live_context", 2, True),
        ]

    # Apply tier budget: keep highest-priority intents first while still
    # preserving the group structure.
    flat: list[tuple[int, str, dict]] = []
    for group, items in groups.items():
        weight = INTENT_PRIORITY.get(group, 0)
        for i, item in enumerate(items):
            # Within a group, the first query (most specific) wins ties.
            flat.append((weight - i, group, item))
    flat.sort(key=lambda t: -t[0])

    keep: dict[str, list[dict]] = {g: [] for g in groups}
    for _w, group, item in flat:
        total = sum(len(v) for v in keep.values())
        if total >= budget:
            break
        keep[group].append(item)

    keep = {g: items for g, items in keep.items() if items}
    keep["_meta"] = {
        "home": home,
        "away": away,
        "competition": competition,
        "tier": tier,
        "budget": budget,
        "emitted": sum(len(v) for v in keep.values() if isinstance(v, list)),
    }
    return keep
