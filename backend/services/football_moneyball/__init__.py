"""Football Moneyball Intelligence Layer + Pattern Memory.

A fail-soft, historical/learning Moneyball-style layer for football.
Mirrors the architecture of the MLB pipeline (warehouse + pressure/profile
+ snapshot + pattern memory + market selection + feedback loop) but with
football-specific signals (xG, xGA, under/over rates, btts, clean sheets,
early-goal profile, corner volatility, league quality, form guard).

Design principles (NON-NEGOTIABLE):
  * Fail-soft everywhere: if Mongo is unavailable, if a signal is missing,
    or if any helper raises, the engine MUST keep running unchanged.
  * No automatic pick forcing: pattern memory only suggests **mild**
    confidence adjustments and prefers **protected** markets (e.g. Under
    3.5 over Under 2.5 when volatility is detected). It never replaces
    the LLM's recommendation outright.
  * Football-only: no MLB / Basketball touchpoints. The orchestrator
    gates by `sport == 'football'` before invoking this package.
  * Pure where possible: pressure profile, snapshot builder and market
    selection are pure (no IO). Only the warehouse and feedback loop
    touch Mongo.
  * Reuses existing modules: `football_quality`, `football_corner_pregame`,
    `derived_early_goal`, `form_guard`, `editorial_context`.

Public surface (see individual modules for full docs):
  * football_intelligence_warehouse — Mongo IO (4 collections).
  * football_goal_pressure_profile  — pure pressure classifier.
  * football_snapshot_builder       — pregame/live snapshot digest.
  * football_pattern_memory         — derive / lookup / attach.
  * football_market_selection       — final protected-market layer.
  * football_feedback_loop          — persist settled outcomes.
  * football_pattern_matcher        — orchestrator-friendly facade.
"""

from .football_intelligence_warehouse import (
    COLL_TEAM_DAILY,
    COLL_MATCH_SNAPSHOTS,
    COLL_MARKET_RESULTS,
    COLL_PATTERN_MEMORY,
    ensure_football_indexes,
    load_team_profile,
    upsert_team_profile,
    persist_match_intelligence_snapshot,
    persist_football_market_result,
    lookup_pattern_match,
    attach_pattern_match_to_payload,
    update_pattern_memory_from_result,
    summarize_pattern_memory,
    FRESHNESS_HOURS_DEFAULT,
    KNOWN_PATTERNS,
)
from .football_goal_pressure_profile import (
    calculate_team_goal_pressure,
    calculate_match_goal_pressure_context,
    derive_goal_pressure_impact,
    HIGH_PRESSURE,
    MODERATE_PRESSURE,
    LOW_PRESSURE,
    NEUTRAL_PRESSURE,
    UNAVAILABLE,
)
from .football_snapshot_builder import (
    build_pregame_snapshot,
    build_live_snapshot,
    build_full_intelligence_snapshot,
)
from .football_pattern_memory import (
    derive_pattern_keys,
)
from .football_market_selection import (
    select_football_market,
    is_total_line_already_hit,
)
from .football_feedback_loop import (
    record_football_pick_outcome,
)
from .football_pattern_matcher import (
    attach_football_intelligence_to_payload,
    compare_live_vs_pregame,
)
from .football_totals_model_normalizer import (
    build_football_totals_model,
    TOTALS_MODEL_SOURCE,
)
from .football_over_support import (
    calculate_football_over_support,
)

__all__ = [
    # Collections
    "COLL_TEAM_DAILY",
    "COLL_MATCH_SNAPSHOTS",
    "COLL_MARKET_RESULTS",
    "COLL_PATTERN_MEMORY",
    # Constants
    "FRESHNESS_HOURS_DEFAULT",
    "KNOWN_PATTERNS",
    "HIGH_PRESSURE",
    "MODERATE_PRESSURE",
    "LOW_PRESSURE",
    "NEUTRAL_PRESSURE",
    "UNAVAILABLE",
    # Warehouse
    "ensure_football_indexes",
    "load_team_profile",
    "upsert_team_profile",
    "persist_match_intelligence_snapshot",
    "persist_football_market_result",
    "lookup_pattern_match",
    "attach_pattern_match_to_payload",
    "update_pattern_memory_from_result",
    "summarize_pattern_memory",
    # Pressure profile
    "calculate_team_goal_pressure",
    "calculate_match_goal_pressure_context",
    "derive_goal_pressure_impact",
    # Snapshot builder
    "build_pregame_snapshot",
    "build_live_snapshot",
    "build_full_intelligence_snapshot",
    # Pattern memory
    "derive_pattern_keys",
    # Market selection
    "select_football_market",
    "is_total_line_already_hit",
    # Feedback loop
    "record_football_pick_outcome",
    # Orchestrator facade
    "attach_football_intelligence_to_payload",
    "compare_live_vs_pregame",
    # Totals Model + Over Support
    "build_football_totals_model",
    "calculate_football_over_support",
    "TOTALS_MODEL_SOURCE",
]
