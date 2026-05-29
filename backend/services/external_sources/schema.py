"""Schema constants + lightweight helpers for external_source_evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

EVIDENCE_TYPES = {
    "news_context",
    "injuries",
    "probable_lineups",
    "historical_trends",
    "h2h",
    "recent_form",
    "tactical_context",
    "standings_context",
    "odds_context",
    "live_stats",
    "market_context",
}

# Standard shape returned by every scraper, then enriched by the dispatcher
# (sets `used_in_analysis` after cross-confirm).
EvidenceItem = dict  # type alias — keeps the codebase JSON-friendly


def make_evidence(
    source: str,
    *,
    url: str = "",
    title: Optional[str] = None,
    evidence_type: str = "news_context",
    extracted_data: Optional[list[str]] = None,
    confidence: int = 60,
    freshness: str = "unknown",
    status: str = "ok",
    errors: Optional[list[str]] = None,
) -> dict[str, Any]:
    if evidence_type not in EVIDENCE_TYPES:
        evidence_type = "news_context"
    return {
        "source":           source,
        "url":              url,
        "title":            title,
        "evidence_type":    evidence_type,
        "extracted_data":   [s for s in (extracted_data or []) if s][:8],
        "confidence":       max(0, min(100, int(confidence))),
        "freshness":        freshness if freshness in {"fresh", "stale", "unknown"} else "unknown",
        "used_in_analysis": False,  # set by dispatcher after cross-confirm
        "status":           status,
        "errors":           list(errors or []),
        "fetched_at":       datetime.now(timezone.utc).isoformat(),
    }


def failed_evidence(source: str, *, url: str = "", reason: str) -> dict[str, Any]:
    return make_evidence(
        source,
        url=url,
        extracted_data=[],
        confidence=0,
        status="failed",
        errors=[reason],
    )


def skipped_evidence(source: str, *, url: str = "", reason: str) -> dict[str, Any]:
    return make_evidence(
        source,
        url=url,
        extracted_data=[],
        confidence=0,
        status="skipped",
        errors=[reason],
    )
