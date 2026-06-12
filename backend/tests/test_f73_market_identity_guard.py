"""Phase F73 — Market identity guard tests.

Acceptance:
  1. has_valid_market_identity rejects UNKNOWN keys and missing families.
  2. gate_classification swaps MARKET_TRAP → MARKET_IDENTITY_MISSING when
     identity is missing.
  3. gate_classification leaves valid markets untouched.
  4. build_market_trace returns state=REQUIRES_MARKET_IDENTIFICATION
     when market_identity is UNKNOWN and the original classification
     was MARKET_TRAP.
  5. build_market_trace blanks edge/probabilities when identity missing.
  6. build_market_trace preserves edge for known markets.
  7. Original rejection_code is preserved under original_rejection_code.
  8. UI receives odds_visible to display "Cuota detectada: 1.25".
"""
from __future__ import annotations

from services.football_market_trace import build_market_trace
from services.market_identity_guards import (
    FORBIDDEN_WHEN_IDENTITY_MISSING,
    gate_classification,
    has_valid_market_identity,
)


# ─────────────────────────────────────────────────────────────────────
# has_valid_market_identity
# ─────────────────────────────────────────────────────────────────────
def test_has_valid_identity_accepts_known_key_with_side():
    assert has_valid_market_identity({
        "identity_key": "1X2:HOME", "family": "1X2", "side": "HOME",
    }) is True


def test_has_valid_identity_rejects_unknown_key():
    assert has_valid_market_identity({
        "identity_key": "UNKNOWN:RAW:empty", "family": None, "side": None,
    }) is False


def test_has_valid_identity_rejects_missing_side():
    assert has_valid_market_identity({
        "identity_key": "TOTAL_GOALS", "family": "TOTAL_GOALS", "side": None,
    }) is False


def test_has_valid_identity_accepts_plain_string_key():
    assert has_valid_market_identity("TOTAL_GOALS:OVER:2.5") is True
    assert has_valid_market_identity("UNKNOWN:RAW:abc") is False
    assert has_valid_market_identity("") is False
    assert has_valid_market_identity(None) is False


# ─────────────────────────────────────────────────────────────────────
# gate_classification
# ─────────────────────────────────────────────────────────────────────
def test_gate_swaps_market_trap_when_identity_missing():
    cls, codes = gate_classification(
        "MARKET_TRAP",
        {"identity_key": "UNKNOWN:RAW:empty", "family": None},
    )
    assert cls == "MARKET_IDENTITY_MISSING"
    assert "MARKET_IDENTITY_MISSING" in codes
    assert "EDGE_CALCULATION_BLOCKED_UNKNOWN_MARKET" in codes


def test_gate_keeps_market_trap_when_identity_valid():
    cls, codes = gate_classification(
        "MARKET_TRAP",
        {"identity_key": "1X2:HOME", "family": "1X2", "side": "HOME"},
    )
    assert cls == "MARKET_TRAP"
    assert codes == []


def test_gate_passes_through_safe_classifications():
    cls, codes = gate_classification(
        "VALUE_BET",
        {"identity_key": "UNKNOWN:RAW:empty", "family": None},
    )
    assert cls == "VALUE_BET"  # VALUE_BET is NOT in forbidden set
    assert codes == []


def test_gate_blocks_every_forbidden_classification():
    # Ensure all forbidden ones swap to MARKET_IDENTITY_MISSING.
    for forbidden in FORBIDDEN_WHEN_IDENTITY_MISSING:
        cls, codes = gate_classification(
            forbidden,
            {"identity_key": "UNKNOWN:RAW:x", "family": None},
        )
        assert cls == "MARKET_IDENTITY_MISSING", \
            f"{forbidden} should be gated"


# ─────────────────────────────────────────────────────────────────────
# build_market_trace integration
# ─────────────────────────────────────────────────────────────────────
def test_trace_unknown_market_becomes_requires_identification():
    pick = {
        "match_label":            "Qatar vs Switzerland",
        "odds":                   1.25,
        "estimated_probability":  0.637,
        "implied_probability":    0.80,
        "edge":                   -0.163,
        "fragility_score":        12,
        "reason":                 "Señales de trampa detectadas",
        "_moneyball": {
            "classification":         "MARKET_TRAP",
            "classification_reason":  "Señales de trampa detectadas",
        },
    }
    trace = build_market_trace(pick, sport="football")
    # Original classification preserved.
    assert trace["original_classification"] == "MARKET_TRAP"
    assert trace["original_rejection_code"] == "MARKET_TRAP"
    # New state.
    assert trace["classification"] == "MARKET_IDENTITY_MISSING"
    assert trace["rejection_code"]  == "MARKET_IDENTITY_MISSING"
    assert trace["state"]            == "REQUIRES_MARKET_IDENTIFICATION"
    # Edge / probabilities BLANKED.
    assert trace["edge"]                   is None
    assert trace["edge_pct"]               is None
    assert trace["estimated_probability"]  is None
    assert trace["implied_probability"]    is None
    # Odds visible preserved for UI.
    assert trace["odds_visible"] == 1.25
    # Reason codes.
    assert "MARKET_IDENTITY_MISSING" in trace["f73_reason_codes"]
    assert "EDGE_CALCULATION_BLOCKED_UNKNOWN_MARKET" in trace["f73_reason_codes"]


def test_trace_known_market_keeps_edge():
    pick = {
        "match_label":            "Brazil vs Morocco",
        "market":                 "1X2",
        "selection":              "home",
        "odds":                   2.10,
        "estimated_probability":  0.50,
        "implied_probability":    0.476,
        "edge":                   0.05,
        "fragility_score":        20,
        "_moneyball": {
            "classification":         "VALUE_BET",
            "classification_reason":  "Edge positivo, mercado estable",
        },
    }
    trace = build_market_trace(pick, sport="football")
    assert trace["classification"] == "VALUE_BET"
    # Edge is recomputed by the trace builder from probabilities; just
    # verify it's a real number (NOT None / blanked).
    assert trace["edge"] is not None
    # `state` field only set when F73 guard triggers; absent for healthy traces.
    assert trace.get("state") != "REQUIRES_MARKET_IDENTIFICATION"
    assert trace["market_identity_key"] == "1X2:HOME"


def test_trace_unknown_market_low_odds_does_not_call_trap():
    """Cuota 1.25 sin mercado conocido NO debe clasificarse como
    LOW_ODDS_NO_CUSHION (F73 cambio 3)."""
    pick = {
        "match_label":            "Qatar vs Switzerland",
        "odds":                   1.25,
        "fragility_score":        12,
        "_moneyball": {
            "classification":         "LOW_ODDS_NO_CUSHION",
            "classification_reason":  "Cuota baja detectada",
        },
    }
    trace = build_market_trace(pick, sport="football")
    assert trace["classification"] == "MARKET_IDENTITY_MISSING"
    assert "LOW_ODDS_TRAP_SUPPRESSED_BY_F73_NO_MARKET_ID" in trace["f73_reason_codes"]
