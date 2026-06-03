"""Data provenance tagging for matches.

Records which upstream source produced each slice of a match document so the
UI can render badges like:

  Datos por: API-Sports
  · Cuotas: API-Sports · hace 12 min
  · Stats: API-Sports · hace 2 h
  · H2H: ESPN · hace 6 h
  · Lineups: no disponible

The freshness ("hace X min/horas") is computed on the client from the
`fetched_at` ISO timestamp recorded here, so the indicator always stays
truthful even if the doc is cached for hours.

`primary_source` is the most authoritative source touching this doc and is
what the global badge renders. Fallback paths set it to `espn`,
`flashscore`, `sofascore`, etc.; the main API-Sports ingestion sets it to
`api_sports`.

Public API:
    build_provenance(...)   → returns the `_provenance` dict for a match.
    bump_section(...)       → updates one slice when a deep-enrich refreshes
                              it (e.g. odds_snapshots, lineups).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


# Display label per known source id (kept in English; the frontend localizes
# user-facing labels via lib/i18n).
SOURCE_LABELS: dict[str, str] = {
    "api_sports":         "API-Sports",
    "api_football":       "API-Sports",        # legacy alias
    "mlb_stats_api":      "MLB Stats API",
    "espn":               "ESPN",
    "espn_fallback":      "ESPN",
    "flashscore":         "Flashscore",
    "flashscore_crawlee": "Flashscore",
    "flashscore_pw":      "Flashscore",
    "sofascore":          "Sofascore",
    "sofascore_crawlee":  "Sofascore",
    "sofascore_pw":       "Sofascore",
    "sportytrader":       "Sportytrader",
    "thestatsapi":        "TheStatsAPI",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _section(source: Optional[str], fetched_at: Optional[str] = None, available: bool = True) -> dict:
    """Build a single provenance section payload.

    `available=False` means "we attempted to fetch this slice but it returned
    nothing meaningful" — the UI renders a "no disponible" placeholder
    instead of a freshness timestamp.
    """
    if not source or not available:
        return {
            "source": None,
            "source_label": None,
            "fetched_at": None,
            "available": False,
        }
    return {
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "fetched_at": fetched_at or _now_iso(),
        "available": True,
    }


def build_provenance(
    primary_source: str,
    *,
    odds_available: bool = False,
    stats_available: bool = False,
    h2h_available: bool = False,
    lineups_available: bool = False,
    context_available: bool = False,
    live_available: bool = False,
    odds_source: Optional[str] = None,
    stats_source: Optional[str] = None,
    h2h_source: Optional[str] = None,
    lineups_source: Optional[str] = None,
    context_source: Optional[str] = None,
    live_source: Optional[str] = None,
    fetched_at: Optional[str] = None,
) -> dict:
    """Build the full `_provenance` payload attached to a match doc.

    `primary_source` is the authoritative source for the match itself (the
    one that produced the fixture id, kickoff, teams). Per-section sources
    default to `primary_source` so we don't have to repeat ourselves when a
    single feed produced everything; pass an explicit value to override.
    """
    ts = fetched_at or _now_iso()
    return {
        "primary_source": primary_source,
        "primary_source_label": SOURCE_LABELS.get(primary_source, primary_source),
        "fetched_at": ts,
        "sections": {
            "odds":     _section(odds_source     or primary_source, ts, odds_available),
            "stats":    _section(stats_source    or primary_source, ts, stats_available),
            "h2h":      _section(h2h_source      or primary_source, ts, h2h_available),
            "lineups":  _section(lineups_source  or primary_source, ts, lineups_available),
            "context":  _section(context_source  or primary_source, ts, context_available),
            "live":     _section(live_source     or primary_source, ts, live_available),
        },
    }


def bump_section(provenance: dict, section: str, *, source: Optional[str] = None, available: bool = True) -> dict:
    """Refresh a single section's `fetched_at` (used when a deep-enrich pass
    refreshes that slice). Mutates and returns `provenance` for convenience.
    """
    if not isinstance(provenance, dict):
        return provenance
    sections = provenance.setdefault("sections", {})
    current = sections.get(section) or {}
    eff_source = source or current.get("source") or provenance.get("primary_source")
    sections[section] = _section(eff_source, _now_iso(), available)
    return provenance


def attach_to_match(match_doc: dict, primary_source: str, **kwargs) -> dict:
    """Convenience: build provenance and attach in-place to `match_doc`."""
    if match_doc is None:
        return match_doc
    match_doc["_provenance"] = build_provenance(primary_source, **kwargs)
    return match_doc
