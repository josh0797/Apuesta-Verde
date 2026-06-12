"""Scores24 enrichment integration for the football pipeline.

Thin helper that attaches scraped Scores24 editorial sections to a pick
payload **as context only** — the football engine still owns the pick
decision. Callers (orchestrator, UI route, manual operator) invoke
:func:`attach_scores24_to_pick_payload` with a match URL.

Design contract
---------------
* **Enrichment only**. Never mutates ``recommendation.market`` or
  ``confidence_score``. Adds an audit block:
  ``pick_payload["scores24_enrichment"] = {...}``.
* **Fail-soft**. When the scraper returns ``available=False``, we still
  attach the empty payload so the UI can show "no editorial data" without
  breaking.
* **Idempotent**. Safe to call multiple times — last write wins.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("scores24_enrichment")


async def attach_scores24_to_pick_payload(
    pick_payload: dict | None,
    *,
    scores24_url: Optional[str],
    use_cache: bool = True,
) -> dict:
    """Scrape ``scores24_url`` and attach the result to ``pick_payload``.

    Returns the same audit dict that gets stored at
    ``pick_payload["scores24_enrichment"]`` (or a no-op dict when the
    payload is missing).
    """
    if not isinstance(pick_payload, dict):
        return {"available": False, "_reason": "no_pick_payload"}
    if not scores24_url:
        audit = {"available": False, "_reason": "no_url"}
        pick_payload["scores24_enrichment"] = audit
        return audit

    try:
        from services.scores24_scraper import scrape_scores24_match
    except Exception as exc:  # noqa: BLE001
        log.debug("scores24_scraper unavailable: %s", exc)
        audit = {"available": False, "_reason": "module_unavailable", "error": str(exc)}
        pick_payload["scores24_enrichment"] = audit
        return audit

    try:
        payload = await scrape_scores24_match(url=scores24_url, use_cache=use_cache)
    except Exception as exc:  # noqa: BLE001
        log.debug("scores24 scrape raised: %s", exc)
        audit = {"available": False, "_reason": "scrape_failed", "error": str(exc)}
        pick_payload["scores24_enrichment"] = audit
        return audit

    # Build the audit block — same shape as the scraper output but trimmed
    # to keep raw HTML out of the persistent payload.
    audit = {
        "available":      bool(payload.get("available")),
        "engine_version": payload.get("engine_version"),
        "url":            payload.get("url"),
        "source":         payload.get("source"),
        "fetched_at":     payload.get("fetched_at"),
        "sections":       payload.get("sections") or [],
        "consensus":      payload.get("consensus") or {},
        "reason_codes":   payload.get("reason_codes") or [],
    }
    pick_payload["scores24_enrichment"] = audit
    # Mirror into camelCase for UI convenience.
    fhp = pick_payload.get("footballHistoricalProfile") or {}
    fhp["scores24Enrichment"] = audit
    pick_payload["footballHistoricalProfile"] = fhp
    return audit


__all__ = ["attach_scores24_to_pick_payload"]
