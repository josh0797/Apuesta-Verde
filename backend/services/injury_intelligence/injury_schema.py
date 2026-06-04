"""Canonical injury_intelligence shape — schema constants and empty factories.

Design: every consumer (UI, analyst, market_selection) reads from a single
shape with `available` flags so missing data never crashes anything.
"""
from __future__ import annotations

from typing import Any

INJURY_SCHEMA_VERSION = "injury-intel.basketball.1"

# Canonical normalised statuses.
STATUS_VALUES = (
    "out",                    # Confirmed absent
    "doubtful",               # Likely out (75%+ chance)
    "questionable",           # Coin-flip (~50%)
    "probable",               # Likely to play (~75%+)
    "day_to_day",             # No firm timeline
    "suspended",              # Disciplinary / league rule (treated as out)
    "minutes_restriction",    # Will play but reduced minutes
    "rest",                   # Healthy scratch (load management)
    "unknown",                # Source listed but no clear status
)

# Statuses that count as "absent" for the team-strength calculation.
ABSENT_STATUSES = frozenset({"out", "suspended", "doubtful"})
UNCERTAIN_STATUSES = frozenset({"questionable", "day_to_day", "unknown"})
RESTRICTED_STATUSES = frozenset({"minutes_restriction"})

ROLE_VALUES = (
    "superstar",   # Franchise player (LeBron, Curry, Doncic, Jokic, etc.)
    "star",        # All-Star calibre
    "starter",     # Starting 5 but not All-Star
    "rotation",    # Key bench (8-9 man rotation)
    "bench",       # End of bench
    "unknown",
)

IMPACT_TIERS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

FRESHNESS_VALUES = ("fresh", "partial", "stale", "unknown")


def empty_team_block() -> dict:
    """Return the canonical empty team-side block."""
    return {
        "team_name":          None,
        "team_id":            None,
        "injuries":           [],
        "team_injury_impact": {
            "total_absences":                 0,
            "star_absences":                  0,
            "starter_absences":               0,
            "questionable_key_players":       0,
            "minutes_restriction_key_players": 0,
            "team_strength_adjustment":       0,
            "impact_score":                   0,
            "impact_tier":                    "LOW",
            "reason_codes":                   [],
            "summary":                        "",
        },
        "basketball_injury_score": {
            "team_strength_adjustment":   0,
            "offense_adjustment":         0,
            "defense_adjustment":         0,
            "pace_adjustment":            0,
            "spread_adjustment":          0,
            "moneyline_adjustment":       0,
            "total_points_adjustment":    0,
            "fragility_adjustment":       0,
            "reason_codes":               [],
        },
    }


def empty_payload(*, sport: str = "basketball", reason: str = "not_available") -> dict:
    """Return the canonical empty payload — UI renders nothing important."""
    return {
        "available":           False,
        "sport":               sport,
        "schema_version":      INJURY_SCHEMA_VERSION,
        "home":                empty_team_block(),
        "away":                empty_team_block(),
        "match_injury_edge":   {
            "home_total_adjustment": 0,
            "away_total_adjustment": 0,
            "net_edge":              "neutral",
            "net_edge_points":       0,
            "edge_tier":             "SMALL",
            "high_volatility":       False,
            "summary":               "",
        },
        "match_impact":        {
            "injury_edge":            "neutral",
            "confidence_adjustment":  0,
            "fragility_adjustment":   0,
            "market_warnings":        [],
            "reason_codes":           [],
            "summary":                "",
        },
        "source_status":       {
            "api_sports":        "skipped",
            "thestatsapi":       "skipped",
            "espn":              "skipped",
            "rotowire":          "skipped",
            "official":          "skipped",
            "editorial_context": "skipped",
        },
        "freshness":           "unknown",
        "_reason":             reason,
    }


__all__ = [
    "INJURY_SCHEMA_VERSION",
    "STATUS_VALUES",
    "ROLE_VALUES",
    "IMPACT_TIERS",
    "FRESHNESS_VALUES",
    "ABSENT_STATUSES",
    "UNCERTAIN_STATUSES",
    "RESTRICTED_STATUSES",
    "empty_payload",
    "empty_team_block",
]
