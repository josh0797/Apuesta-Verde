"""Editorial Context Engine (P3).

Layer 3 of the multi-source enrichment stack:
  P1: API-Football / API-Sports  (structured data)
  P2: Crawlee / scrapers actuales (fallback + scraping rápido)
  P3: Editorial Context — Scrapy   (contexto editorial profundo)

P3 captures PREDICTIONS, MOTIVATION, KEY ARGUMENTS, INJURIES and RISKS from
football preview/prediction sites (Spanish-language football for MVP). It is
intentionally **fail-soft**: if every source fails, the analyst engine
continues with P1+P2 data — `editorial_context.available = False` is the
only visible side-effect.

Public entrypoints:
    services.editorial_context.fetch_editorial_context(match, *, db, force_refresh=False)
    services.editorial_context.editorial_signal_mapper.classify_signal(text)
    services.editorial_context.moneyball_interpretation.interpret(editorial, moneyball_verdict)
"""
from __future__ import annotations

from .editorial_context_service import (
    fetch_editorial_context,
    fetch_editorial_context_bulk,
    EDITORIAL_CONTEXT_VERSION,
)
from .match_key import canonical_match_key, normalize_team_name
from .editorial_source_registry import SOURCES, enabled_sources
from . import editorial_signal_mapper as signal_mapper
from . import moneyball_interpretation

__all__ = [
    "fetch_editorial_context",
    "fetch_editorial_context_bulk",
    "EDITORIAL_CONTEXT_VERSION",
    "canonical_match_key",
    "normalize_team_name",
    "SOURCES",
    "enabled_sources",
    "signal_mapper",
    "moneyball_interpretation",
]
