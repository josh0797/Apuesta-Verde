"""Injury Intelligence Layer.

Phase 1 (current): Basketball.
Phase 2 (planned): Football.

Fail-soft, multi-source, cache-aware, sport-specific, explicable, conservative.
MLB is NOT affected by this layer.

Public API:
    fetch_basketball_injury_intelligence(home_team, away_team, *, db=None, force_refresh=False)
        Returns the canonical ``injury_intelligence`` payload for a basketball match.

    INJURY_SCHEMA_VERSION
        Version of the canonical payload shape.
"""
from .injury_schema import (
    INJURY_SCHEMA_VERSION,
    STATUS_VALUES,
    ROLE_VALUES,
    IMPACT_TIERS,
    FRESHNESS_VALUES,
    empty_payload,
    empty_team_block,
)
from .injury_normalizer import (
    normalize_status,
    merge_player_records,
    compute_freshness,
)
from .basketball_injury_impact import (
    calculate_basketball_injury_impact,
    classify_player_role,
    NBA_SUPERSTARS,
    NBA_STARS,
)
from .orchestrator import (
    fetch_basketball_injury_intelligence,
)

__all__ = [
    "INJURY_SCHEMA_VERSION",
    "STATUS_VALUES",
    "ROLE_VALUES",
    "IMPACT_TIERS",
    "FRESHNESS_VALUES",
    "empty_payload",
    "empty_team_block",
    "normalize_status",
    "merge_player_records",
    "compute_freshness",
    "calculate_basketball_injury_impact",
    "classify_player_role",
    "NBA_SUPERSTARS",
    "NBA_STARS",
    "fetch_basketball_injury_intelligence",
]
