"""Phase F60 — Football Corner Cross Integration smoke tests.

Validates the orchestration glue:
    external_context_gate  →  scores24_scraper  →  football_corner_profile_cross

Scenarios covered:
    A. Gate denies (low priority + main value clean) → cross still computed,
       scraper NOT invoked.
    B. Gate allows (strong under cross candidate) + scraper returns
       confirming Over/Under prediction.
    C. Gate allows but no match URL is available → scraper skipped,
       fail-soft.
    D. Gate allows + scraper raises → fail-soft; internal-only cross
       still attached.
    E. Empty / missing inputs → safe no-op.
    F. Cross + Scores24 confirmation vs conflict scenarios.
"""
from __future__ import annotations

import asyncio
import pytest

from services.football_corner_cross_integration import (
    ENGINE_VERSION,
    RC_CROSS_COMPUTED,
    RC_GATE_DENIED,
    RC_GATE_OPENED,
    RC_NO_MATCH_URL,
    RC_SCRAPER_FAILED,
    RC_SCRAPER_OK,
    RC_SCRAPER_SKIPPED,
    attach_football_corner_cross_to_payload,
)


# Phase F82.2 — Scores24 is disabled by default in production. These
# legacy smoke tests verify the LEGACY path still works when the flag
# is explicitly turned on. The new production default behaviour is
# covered by ``test_f82_2_corner_365_cross.py``.
@pytest.fixture(autouse=True)
def _force_scores24_flag_on(monkeypatch):
    monkeypatch.setenv("ENABLE_SCORES24_CORNERS_CONFIRMATION", "true")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if asyncio.get_event_loop().is_running() is False \
        else asyncio.run(coro)


def _team(corners_for, corners_against):
    """Build a side with newest-first per-match corners arrays."""
    return {
        "recent_fixtures": {
            "corners_for":     corners_for,
            "corners_against": corners_against,
        }
    }


def _strong_under_match():
    """Both teams average ~3.5 corners for + ~3.5 against → STRONG_UNDER."""
    return {
        "match_id": "smoke-001",
        "home_team": _team([4, 3, 3, 4, 3, 3, 4, 3, 4, 3, 3, 4, 3, 4, 3],
                           [3, 4, 3, 3, 4, 3, 4, 3, 3, 4, 3, 3, 4, 3, 3]),
        "away_team": _team([3, 3, 4, 3, 3, 4, 3, 3, 4, 3, 3, 4, 3, 3, 4],
                           [4, 3, 3, 4, 3, 3, 4, 3, 3, 4, 3, 4, 3, 3, 4]),
        "home_team_name": "Defensive A",
        "away_team_name": "Defensive B",
        "competition":    "Liga Local",
    }


def _high_priority_match():
    return {
        "match_id": "smoke-002",
        "home_team": _team([6, 7, 6, 7, 6], [5, 6, 5, 6, 5]),
        "away_team": _team([6, 6, 7, 6, 6], [5, 6, 5, 5, 6]),
        "home_team_name": "Real Madrid",
        "away_team_name": "Barcelona",
        "competition":    "UEFA Champions League Final",
        "scores24_url":   "https://scores24.live/es/soccer/m-real-madrid-vs-barcelona-prediction",
    }


def _make_fetcher(html_returned: str | None = None,
                  raises: bool = False):
    """Build an injectable fetcher matching ``scrape_scores24_match`` API."""
    async def _f(url: str):
        if raises:
            raise RuntimeError("brightdata down")
        return html_returned
    return _f


# ─────────────────────────────────────────────────────────────────────
# A. Gate denies → scraper NOT invoked, cross still computed
# ─────────────────────────────────────────────────────────────────────
def test_gate_denies_low_priority_skips_scraper():
    """When the cross is MIXED (no candidate) and there's no corner line
    available, the gate must deny → no premium fetch happens."""
    pick = {
        "recommendation": {"market": "Over 2.5", "confidence_score": 50},
        "priority":        "low",
    }
    # Build a match where corners are mixed (some high, some low) → no
    # clean candidate, so the gate cannot use the candidate allow rule.
    match = {
        "match_id": "smoke-mixed-001",
        "home_team": _team([3, 8, 2, 9, 1, 7, 2, 8, 3, 9, 1, 7, 2, 8, 3],
                           [3, 8, 2, 9, 1, 7, 2, 8, 3, 9, 1, 7, 2, 8, 3]),
        "away_team": _team([8, 2, 9, 1, 7, 2, 8, 3, 9, 1, 7, 2, 8, 3, 9],
                           [8, 2, 9, 1, 7, 2, 8, 3, 9, 1, 7, 2, 8, 3, 9]),
        "home_team_name": "Mixed A",
        "away_team_name": "Mixed B",
        "competition":    "Liga Local",
    }
    audit = asyncio.run(attach_football_corner_cross_to_payload(pick, match))

    # Cross was still computed (it's cheap).
    assert audit["cross_available"] is True
    assert RC_CROSS_COMPUTED in audit["reason_codes"]

    # But the scraper was NOT touched.
    assert audit["scores24_attempted"] is False
    assert audit["scores24_ok"] is False
    assert RC_SCRAPER_SKIPPED in audit["reason_codes"]
    # Gate verdict reflected.
    assert audit["gate_should_fetch"] is False
    assert RC_GATE_DENIED in audit["reason_codes"]
    # Payload mutated.
    assert "combined_football_corner_profile_cross" in pick
    assert pick["football_corner_cross_applied"] is audit


# ─────────────────────────────────────────────────────────────────────
# B. Gate opens + scraper OK → external_confirmation attached
# ─────────────────────────────────────────────────────────────────────
def test_gate_opens_with_strong_candidate_scraper_confirms():
    """When the gate opens and Scores24 confirms the Under hypothesis,
    the integrator attaches ``external_confirmation=True`` on the cross.
    """
    pick = {
        "recommendation": {"market": "Over 2.5", "confidence_score": 50},
    }
    match = _strong_under_match()
    match["scores24_url"] = "https://scores24.live/es/soccer/m-test-strong-under"

    # Fake scraper that returns a payload supporting Under corners.
    async def _fake_scraper(url: str):
        return None  # fetcher path; the underlying function will fall back.

    # We inject directly at the integrator level by monkey-patching the
    # scraper module's scrape function.
    from services import scores24_scraper as s24mod

    async def _stub_scrape_scores24_match(*, url, use_cache, fetcher=None):
        return {
            "available":      True,
            "engine_version": "stub",
            "url":            url,
            "source":         "stub:test",
            "sections":       [],
            "consensus":      {
                "primary_market_type": "corners_total",
                "primary_section":     "corners_total",
                "primary_side":        "UNDER",
                "primary_line":        9.5,
                "primary_odds":        1.9,
            },
            "reason_codes":   ["STUB_OK"],
        }

    real = s24mod.scrape_scores24_match
    s24mod.scrape_scores24_match = _stub_scrape_scores24_match  # type: ignore[assignment]
    try:
        audit = asyncio.run(attach_football_corner_cross_to_payload(pick, match))
    finally:
        s24mod.scrape_scores24_match = real  # type: ignore[assignment]

    assert audit["available"] is True
    assert audit["gate_should_fetch"] is True
    assert RC_GATE_OPENED in audit["reason_codes"]
    assert audit["scores24_attempted"] is True
    assert audit["scores24_ok"] is True
    assert RC_SCRAPER_OK in audit["reason_codes"]
    # External confirmation: STRONG_UNDER + Scores24 says UNDER.
    assert audit["external_confirmation"] is True
    assert audit["external_conflict"] is False
    # Payload now carries the cross with the confirmation block.
    cross = pick["combined_football_corner_profile_cross"]
    assert cross["external_confirmation"] is True
    # And the raw scraper payload was stashed.
    assert pick["scores24_corner_payload"]["available"] is True


# ─────────────────────────────────────────────────────────────────────
# C. Gate opens but no match URL → scraper skipped, fail-soft.
# ─────────────────────────────────────────────────────────────────────
def test_gate_opens_but_no_match_url_skips_scraper():
    pick = {
        "recommendation": {"market": "Over 2.5", "confidence_score": 50},
    }
    match = _strong_under_match()
    # No ``scores24_url`` in the match dict.

    audit = asyncio.run(attach_football_corner_cross_to_payload(pick, match))
    assert audit["gate_should_fetch"] is True
    assert audit["scores24_attempted"] is False
    assert RC_NO_MATCH_URL in audit["reason_codes"]
    # Cross still attached from internal data.
    assert audit["cross_available"] is True


# ─────────────────────────────────────────────────────────────────────
# D. Gate opens but scraper raises → fail-soft (no scores24_ok).
# ─────────────────────────────────────────────────────────────────────
def test_scraper_raises_fail_soft():
    pick = {
        "recommendation": {"market": "Over 2.5", "confidence_score": 50},
    }
    match = _strong_under_match()
    match["scores24_url"] = "https://scores24.live/es/soccer/m-explodes"

    from services import scores24_scraper as s24mod

    async def _boom(*, url, use_cache, fetcher=None):
        raise RuntimeError("brightdata exploded")

    real = s24mod.scrape_scores24_match
    s24mod.scrape_scores24_match = _boom  # type: ignore[assignment]
    try:
        audit = asyncio.run(attach_football_corner_cross_to_payload(pick, match))
    finally:
        s24mod.scrape_scores24_match = real  # type: ignore[assignment]

    assert audit["gate_should_fetch"] is True
    assert audit["scores24_attempted"] is True
    assert audit["scores24_ok"] is False
    assert RC_SCRAPER_FAILED in audit["reason_codes"]
    # Internal cross still intact.
    assert audit["cross_available"] is True


# ─────────────────────────────────────────────────────────────────────
# E. Safe no-op on bad inputs.
# ─────────────────────────────────────────────────────────────────────
def test_invalid_pick_payload_returns_audit_only():
    audit = asyncio.run(attach_football_corner_cross_to_payload(None, {}))
    assert audit["available"] is False
    assert audit["engine_version"] == ENGINE_VERSION


def test_empty_match_falls_through_failsoft():
    pick: dict = {}
    audit = asyncio.run(attach_football_corner_cross_to_payload(pick, {}))
    assert audit["engine_version"] == ENGINE_VERSION
    assert audit["cross_available"] is False
    # cross block still attached as ``available:False``.
    assert pick["combined_football_corner_profile_cross"]["available"] is False


# ─────────────────────────────────────────────────────────────────────
# F. External CONFLICT: cross says UNDER, Scores24 says OVER.
# ─────────────────────────────────────────────────────────────────────
def test_external_conflict_marked():
    pick = {
        "recommendation": {"market": "Over 2.5", "confidence_score": 50},
    }
    match = _strong_under_match()
    match["scores24_url"] = "https://scores24.live/es/soccer/m-conflict"

    from services import scores24_scraper as s24mod

    async def _scrape_over(*, url, use_cache, fetcher=None):
        return {
            "available":    True,
            "engine_version": "stub",
            "url":          url,
            "source":       "stub:test",
            "sections":     [],
            "consensus":    {
                "primary_market_type": "corners_total",
                "primary_section":     "corners_total",
                "primary_side":        "OVER",
                "primary_line":        9.5,
                "primary_odds":        1.9,
            },
            "reason_codes": ["STUB_OK"],
        }

    real = s24mod.scrape_scores24_match
    s24mod.scrape_scores24_match = _scrape_over  # type: ignore[assignment]
    try:
        audit = asyncio.run(attach_football_corner_cross_to_payload(pick, match))
    finally:
        s24mod.scrape_scores24_match = real  # type: ignore[assignment]

    assert audit["scores24_ok"] is True
    assert audit["external_conflict"] is True
    assert audit["external_confirmation"] is False


# ─────────────────────────────────────────────────────────────────────
# G. enable_premium_fetch=False → gate bypassed.
# ─────────────────────────────────────────────────────────────────────
def test_disabled_premium_fetch_short_circuits_gate():
    pick = {
        "recommendation": {"market": "Over 2.5", "confidence_score": 50},
    }
    match = _strong_under_match()
    match["scores24_url"] = "https://scores24.live/es/soccer/m-should-never-be-called"

    audit = asyncio.run(attach_football_corner_cross_to_payload(
        pick, match, enable_premium_fetch=False,
    ))
    assert audit["gate_should_fetch"] is False
    assert audit["scores24_attempted"] is False
    assert audit["cross_available"] is True
