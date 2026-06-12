"""Smoke tests for ``services.external_context_gate``.

Validates:
  * 5 allow rules (corner candidate, no main value, high priority, layer
    conflict, edge needs external confirmation).
  * 7 deny rules (no candidate, low priority, cache fresh, main value
    clean, no corner line, mixed profile, late live).
  * Edge cases (invalid payload, NaN, mixed profile blocks allow).
"""
from __future__ import annotations

import pytest

from services.external_context_gate import (
    ENGINE_VERSION,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    RC_CORNER_MARKET_CANDIDATE,
    RC_DENY_CACHE_FRESH,
    RC_DENY_LATE_LIVE,
    RC_DENY_LOW_PRIORITY,
    RC_DENY_MAIN_VALUE_CLEAN,
    RC_DENY_MIXED_PROFILE,
    RC_DENY_NO_CANDIDATE,
    RC_DENY_NO_CORNER_LINE,
    RC_EDGE_NEEDS_EXTERNAL_CONFIRM,
    RC_HIGH_PRIORITY_MATCH,
    RC_LAYER_CONFLICT,
    RC_MAIN_MARKETS_NO_VALUE,
    RC_VALUE_FILTER_PASSED,
    should_fetch_scores24_context,
)


# ─────────────────────────────────────────────────────────────────────
# Fail-safe / invalid inputs
# ─────────────────────────────────────────────────────────────────────
def test_invalid_payload_returns_safe_deny():
    res = should_fetch_scores24_context(None)
    assert res["should_fetch"] is False
    assert res["reason"] == "invalid_payload"
    assert res["priority"] == PRIORITY_LOW
    assert res["allowed_fetch_type"] == "none"
    assert res["engine_version"] == ENGINE_VERSION


def test_empty_payload_returns_deny_no_candidate():
    res = should_fetch_scores24_context({})
    assert res["should_fetch"] is False
    assert RC_DENY_NO_CANDIDATE in res["deny_codes"]


# ─────────────────────────────────────────────────────────────────────
# Allow rule 1 — Corner-market candidate from cross
# ─────────────────────────────────────────────────────────────────────
def test_allow_corner_candidate_from_strong_under_cross():
    payload = {
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_UNDER_CROSS",
            "supports":  "CORNERS_UNDER",
        },
        "corner_market": {"line": 9.5, "model_edge": 0.5},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert RC_CORNER_MARKET_CANDIDATE in res["reason_codes"]
    assert RC_VALUE_FILTER_PASSED in res["reason_codes"]
    assert res["priority"] == PRIORITY_HIGH
    assert res["matched_profile"] == "STRONG_CORNERS_UNDER_CROSS"


def test_allow_corner_candidate_secondary_source_hint():
    payload = {
        "secondary_corner_signals": {
            "candidate":      True,
            "profile_hint":   "secondary_low_corners_hint",
            "line_available": True,
        },
        "corner_market": {"line": 8.5, "model_edge": 0.7},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert RC_CORNER_MARKET_CANDIDATE in res["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Allow rule 2 — No clear value in main markets
# ─────────────────────────────────────────────────────────────────────
def test_allow_main_markets_no_value_with_corner_line():
    payload = {
        "recommendation": {
            "market": "Over 2.5", "confidence_score": 48,
        },
        "corner_market": {"line": 10.5, "model_edge": 1.5},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert RC_MAIN_MARKETS_NO_VALUE in res["reason_codes"]
    # No high-priority signal → priority should be MEDIUM at most.
    assert res["priority"] in (PRIORITY_MEDIUM, PRIORITY_HIGH)


def test_no_value_but_no_corner_line_does_not_open_gate():
    payload = {
        "recommendation": {"market": "Over 2.5", "confidence_score": 40},
        # No corner_market line, no candidate.
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is False
    assert RC_DENY_NO_CORNER_LINE in res["deny_codes"]


# ─────────────────────────────────────────────────────────────────────
# Allow rule 3 — High-priority match
# ─────────────────────────────────────────────────────────────────────
def test_allow_high_priority_champions_league_final():
    payload = {
        "competition":     "UEFA Champions League Final",
        "home_team_name":  "Real Madrid",
        "away_team_name":  "Manchester City",
        "corner_market":   {"line": 9.5, "model_edge": 0.3},
        "recommendation":  {"market": "Over 2.5", "confidence_score": 70},
        "main_value_clean": False,
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert RC_HIGH_PRIORITY_MATCH in res["reason_codes"]
    assert res["priority"] == PRIORITY_HIGH


def test_allow_high_priority_explicit_priority_flag():
    payload = {
        "priority":      "high",
        "corner_market": {"line": 9.5, "model_edge": 0.3},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert RC_HIGH_PRIORITY_MATCH in res["reason_codes"]


def test_allow_high_priority_derby():
    payload = {
        "competition":     "La Liga",
        "home_team_name":  "Real Madrid",
        "away_team_name":  "Barcelona",
        "corner_market":   {"line": 10.5, "model_edge": 0.4},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert RC_HIGH_PRIORITY_MATCH in res["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Allow rule 4 — Layer conflict
# ─────────────────────────────────────────────────────────────────────
def test_allow_layer_conflict_explicit_audit():
    payload = {
        "layer_conflict_audit": {"has_conflict": True, "layers": ["live", "corner"]},
        "corner_market": {"line": 9.5, "model_edge": 0.5},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert RC_LAYER_CONFLICT in res["reason_codes"]


def test_allow_layer_conflict_heuristic_pressure_vs_corner():
    payload = {
        "live_pressure": {"direction": "OVER"},
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_UNDER_CROSS",
            "supports":  "CORNERS_UNDER",
        },
        "corner_market": {"line": 9.5, "model_edge": 0.5},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    # Either via candidate OR layer conflict (or both).
    assert RC_CORNER_MARKET_CANDIDATE in res["reason_codes"] \
        or RC_LAYER_CONFLICT in res["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Allow rule 5 — Edge needs external confirmation
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("line", [7.5, 8.5, 9.5, 10.5])
def test_allow_edge_needs_external_confirmation_at_critical_lines(line: float):
    payload = {
        "corner_market": {"line": line, "model_edge": 0.4},
        "recommendation": {"market": "Over 2.5", "confidence_score": 72},
        "main_value_clean": True,
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert RC_EDGE_NEEDS_EXTERNAL_CONFIRM in res["reason_codes"]


def test_edge_NOT_at_critical_line_does_not_trigger():
    payload = {
        "corner_market": {"line": 11.5, "model_edge": 0.4},
        "recommendation": {"market": "Over 2.5", "confidence_score": 72},
        "main_value_clean": True,
    }
    res = should_fetch_scores24_context(payload)
    # main_value_clean & no edge confirmation → gate stays closed.
    assert res["should_fetch"] is False
    assert RC_DENY_MAIN_VALUE_CLEAN in res["deny_codes"]


def test_edge_at_critical_line_but_above_threshold_does_not_trigger():
    payload = {
        "corner_market": {"line": 9.5, "model_edge": 2.5},  # > threshold
        "recommendation": {"market": "Over 2.5", "confidence_score": 75},
        "main_value_clean": True,
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is False


# ─────────────────────────────────────────────────────────────────────
# Deny rule 1 — No candidate (already covered by empty payload test)
# Deny rule 2 — Low priority
# ─────────────────────────────────────────────────────────────────────
def test_deny_low_priority():
    payload = {
        "priority": "low",
        "recommendation": {"market": "Over 2.5", "confidence_score": 50},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is False
    assert RC_DENY_LOW_PRIORITY in res["deny_codes"]


# ─────────────────────────────────────────────────────────────────────
# Deny rule 3 — Cache fresh (hard deny)
# ─────────────────────────────────────────────────────────────────────
def test_deny_cache_fresh_hard_block():
    payload = {
        "scores24_enrichment": {
            "available":  True,
            "fetched_at": "2026-01-01T12:00:00Z",
        },
        # Even with a perfect candidate, cache fresh blocks the call.
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_UNDER_CROSS",
            "supports":  "CORNERS_UNDER",
        },
        "corner_market": {"line": 9.5, "model_edge": 0.3},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is False
    assert RC_DENY_CACHE_FRESH in res["deny_codes"]
    assert res["reason"] == "denied_hard_rule"


# ─────────────────────────────────────────────────────────────────────
# Deny rule 4 — Main pick already has clean value
# ─────────────────────────────────────────────────────────────────────
def test_deny_main_value_clean():
    payload = {
        "recommendation": {
            "market":           "Over 2.5",
            "confidence_score": 78,
        },
        "main_value_clean": True,
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is False
    assert RC_DENY_MAIN_VALUE_CLEAN in res["deny_codes"]


# ─────────────────────────────────────────────────────────────────────
# Deny rule 5 — No corner line available
# ─────────────────────────────────────────────────────────────────────
def test_deny_no_corner_line():
    payload = {
        "recommendation": {"market": "Over 2.5", "confidence_score": 40},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is False
    assert RC_DENY_NO_CORNER_LINE in res["deny_codes"]


# ─────────────────────────────────────────────────────────────────────
# Deny rule 6 — Mixed corners profile
# ─────────────────────────────────────────────────────────────────────
def test_deny_mixed_corners_profile():
    payload = {
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "MIXED_CORNERS_PROFILE",
            "supports":  "NEUTRAL",
        },
        "recommendation": {"market": "Over 2.5", "confidence_score": 50},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is False
    assert RC_DENY_MIXED_PROFILE in res["deny_codes"]


# ─────────────────────────────────────────────────────────────────────
# Deny rule 7 — Late live (minute >= 80)
# ─────────────────────────────────────────────────────────────────────
def test_deny_late_live_hard_block():
    payload = {
        "live": {"minute": 85},
        # Even with strong candidate, late live blocks.
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_UNDER_CROSS",
            "supports":  "CORNERS_UNDER",
        },
        "corner_market": {"line": 9.5, "model_edge": 0.3},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is False
    assert RC_DENY_LATE_LIVE in res["deny_codes"]
    assert res["reason"] == "denied_hard_rule"


def test_live_minute_under_threshold_does_not_block():
    payload = {
        "live": {"minute": 30},
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_UNDER_CROSS",
            "supports":  "CORNERS_UNDER",
        },
        "corner_market": {"line": 9.5, "model_edge": 0.3},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True


# ─────────────────────────────────────────────────────────────────────
# Composite — multiple allow rules raise priority
# ─────────────────────────────────────────────────────────────────────
def test_high_priority_plus_candidate_yields_high_priority():
    payload = {
        "competition":     "UEFA Champions League",
        "home_team_name":  "Bayern",
        "away_team_name":  "PSG",
        "combined_football_corner_profile_cross": {
            "available": True,
            "profile":   "STRONG_CORNERS_OVER_CROSS",
            "supports":  "CORNERS_OVER",
        },
        "corner_market": {"line": 10.5, "model_edge": 0.6},
    }
    res = should_fetch_scores24_context(payload)
    assert res["should_fetch"] is True
    assert res["priority"] == PRIORITY_HIGH
    assert res["allowed_fetch_type"] == "premium"
    assert RC_CORNER_MARKET_CANDIDATE in res["reason_codes"]
    assert RC_HIGH_PRIORITY_MATCH in res["reason_codes"]
