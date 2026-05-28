"""Historical Detail Enrichment — multi-sport package.

This package owns the deep-historical profiles that the analyst engine
attaches to each shortlisted match BEFORE deciding whether to discard it.

Modules:
    basketball_historical.py
        enrich_basketball_historical_profile(match)        # async
        compute_basketball_profile(home_games, away_games, h2h)
        prefetch_basketball_profiles(matches, db=…)

    baseball_historical.py  (P4 — coming next)
        enrich_baseball_historical_profile(match)

Rule of thumb (matches the user spec):
    Ningún match basketball/baseball prioritario debe ser descartado sin
    antes consultar su historial profundo.

All public functions are fail-soft: if the upstream API is rate-limited
or returns nothing, we attach `available=False, _reason=<...>` instead
of raising. The downstream pipeline keeps running.
"""
from .basketball_historical import (   # noqa: F401
    enrich_basketball_historical_profile,
    compute_basketball_profile,
    prefetch_basketball_profiles,
    empty_basketball_profile,
)
from .basketball_trap_signals import (  # noqa: F401
    collect_basketball_trap_signals,
    compute_extra_fragility,
)

__all__ = [
    "enrich_basketball_historical_profile",
    "compute_basketball_profile",
    "prefetch_basketball_profiles",
    "empty_basketball_profile",
    "collect_basketball_trap_signals",
    "compute_extra_fragility",
]
