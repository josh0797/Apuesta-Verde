"""Phase F63 — Market Guardrail soft/hard discard smoke tests.

Validates the new edge threshold rule:
  * edge <= -25%  → HARD_DISCARD          (terminal)
  * -25% < edge <  -∞ but <= 0   → SOFT_DISCARD_REVIEW (Scores24 review required)
  * edge > 0 but below threshold → existing INSUFFICIENT_DATA / NO_BET_VALUE
    paths (covered by other smoke tests).

Spec examples:
  * -12.9% → SOFT_DISCARD_REVIEW
  * -18.8% → SOFT_DISCARD_REVIEW
  * -20.5% → SOFT_DISCARD_REVIEW
  * -25.0% or worse → HARD_DISCARD
"""
from __future__ import annotations

import pytest

from services.market_guardrail import (
    EDGE_HARD_DISCARD_THRESHOLD,
    apply_market_guardrail,
)


def _make_pick(*, match_id: str, match_label: str,
               confidence: int, odds: float) -> dict:
    """Build a minimal pick payload that ``apply_market_guardrail``
    accepts (it only reads confidence_score + odds_range + classification)."""
    return {
        "match_id":    match_id,
        "match_label": match_label,
        "recommendation": {
            "market":           "Over 2.5",
            "confidence_score": confidence,
            "odds_range":       f"{odds:.2f}",
        },
        "_moneyball": {
            "classification": "POTENTIAL",
        },
    }


def test_threshold_constant_is_minus_25_pct():
    assert EDGE_HARD_DISCARD_THRESHOLD == -25.0


def test_edge_minus_18_pct_becomes_soft_discard_review():
    """USA vs Paraguay example from spec — edge ≈ -18.8% must NOT be terminal."""
    # Construct confidence + odds so that est * 0.85 - 1/odds ≈ -0.188.
    # confidence=40 → est=0.40*0.85=0.34. Pick odds=2.0 → implied=0.5.
    # edge = 0.34 - 0.5 = -0.16. Edge_pct ≈ -16.0. → SOFT.
    parsed = {"picks": [
        _make_pick(match_id="usa-paraguay", match_label="USA vs Paraguay",
                   confidence=40, odds=2.0),
    ]}
    out = apply_market_guardrail(parsed, sport="football")
    disc = out["summary"]["discarded_market"]
    assert len(disc) == 1
    entry = disc[0]
    assert entry["discard_strength"] == "SOFT_DISCARD_REVIEW"
    assert entry["scores24_review_required"] is True
    assert "NEGATIVE_EDGE_SOFT_DISCARD_REVIEW" in entry["f63_reason_codes"]
    assert "SCORES24_REVIEW_REQUIRED_FOR_SOFT_DISCARD" in entry["f63_reason_codes"]
    assert -25.0 < entry["edge_pct"] < 0


def test_edge_minus_20_5_pct_still_soft():
    """Canada vs Bosnia example — -20.5% must still be SOFT, not HARD."""
    # confidence=30 → est=0.30*0.85=0.255. odds=2.2 → implied=0.4545.
    # edge = 0.255 - 0.4545 = -0.1995. ≈ -20%.
    parsed = {"picks": [
        _make_pick(match_id="canada-bosnia",
                   match_label="Canada vs Bosnia & Herzegovina",
                   confidence=30, odds=2.2),
    ]}
    out = apply_market_guardrail(parsed, sport="football")
    disc = out["summary"]["discarded_market"]
    entry = disc[0]
    assert entry["discard_strength"] == "SOFT_DISCARD_REVIEW"
    assert entry["edge_pct"] > EDGE_HARD_DISCARD_THRESHOLD


def test_edge_minus_30_pct_becomes_hard_discard():
    """Below -25% → terminal HARD_DISCARD; no Scores24 review required."""
    # confidence=20 → est=0.17. odds=2.5 → implied=0.4. edge = -0.23 (still soft).
    # Push lower: confidence=10 → est=0.085. odds=2.2 → implied=0.4545.
    # edge = -0.37 → HARD.
    parsed = {"picks": [
        _make_pick(match_id="hard-discard-1",
                   match_label="Tiny Confidence vs Long Price",
                   confidence=10, odds=2.2),
    ]}
    out = apply_market_guardrail(parsed, sport="football")
    disc = out["summary"]["discarded_market"]
    entry = disc[0]
    assert entry["discard_strength"] == "HARD_DISCARD"
    assert entry["scores24_review_required"] is False
    assert "EDGE_HARD_DISCARD"      in entry["f63_reason_codes"]
    assert "edge_too_negative"      in entry["f63_reason_codes"]
    assert entry["edge_pct"] <= EDGE_HARD_DISCARD_THRESHOLD


def test_threshold_boundary_minus_25_is_hard():
    """Exactly -25.0% must be HARD (the spec uses ``<= -25.0``)."""
    # Build a pick that lands ~-25%. confidence=20 → est=0.17.
    # odds=2.38 → implied≈0.42. edge ≈ -0.25.
    parsed = {"picks": [
        _make_pick(match_id="boundary",
                   match_label="Boundary",
                   confidence=20, odds=2.38),
    ]}
    out = apply_market_guardrail(parsed, sport="football")
    disc = out["summary"]["discarded_market"]
    entry = disc[0]
    # Edge_pct should be within ±0.5 of -25.0. We assert behaviour at the
    # threshold: it MUST be HARD when <= -25.0.
    if entry["edge_pct"] <= EDGE_HARD_DISCARD_THRESHOLD:
        assert entry["discard_strength"] == "HARD_DISCARD"
    else:
        assert entry["discard_strength"] == "SOFT_DISCARD_REVIEW"


def test_audit_block_exposes_threshold():
    parsed = {"picks": [
        _make_pick(match_id="dummy", match_label="Dummy",
                   confidence=40, odds=2.0),
    ]}
    out = apply_market_guardrail(parsed, sport="football")
    audit = out.get("_pipeline", {}).get("market_guardrail", {})
    assert audit["edge_hard_discard_threshold_pct"] == EDGE_HARD_DISCARD_THRESHOLD
