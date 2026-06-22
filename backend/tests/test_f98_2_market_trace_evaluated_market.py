"""Sprint-F98.2 · Tests for the user-binding market_trace improvements.

Fix B-1 — ``evaluated_market`` block with ``market_identity_key``.
Fix B-2 — Low odds (<1.40) without identified market must NOT be
          classified as MARKET_TRAP. The user-facing reason must mention
          "Cuota baja detectada, pero no se puede evaluar trampa sin
          identificar el mercado exacto."

User-binding examples of ``market_identity_key``:
  * 1X2:HOME
  * 1X2:AWAY
  * DOUBLE_CHANCE:1X
  * DOUBLE_CHANCE:X2
  * DNB:HOME
  * TOTAL_GOALS:UNDER:2.5
  * TOTAL_GOALS:UNDER:3.5
  * TOTAL_GOALS:OVER:1.5
  * BTTS:YES
  * BTTS:NO
  * CORNERS_TOTAL:UNDER:9.5
  * HANDICAP:HOME:-1
"""
from __future__ import annotations

import pytest

from services.football_market_trace import build_market_trace
from services.market_identity import normalize_market_identity


# ─────────────────────────────────────────────────────────────────────
# Fix B-1 · evaluated_market block — every user-listed identity_key
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("market,selection,line,expected_key", [
    # 1X2
    ("Resultado 1X2",      "Canada",          None, "1X2:HOME"),
    ("Match Winner",       "Visitante",       None, "1X2:AWAY"),
    # Double Chance — token-based detection (user's exact example)
    ("Doble Oportunidad",  "Canada o Empate", None, "DOUBLE_CHANCE:1X"),
    ("Doble Oportunidad",  "Empate o Curacao",None, "DOUBLE_CHANCE:X2"),
    ("Double Chance",      "Canada o Curacao",None, "DOUBLE_CHANCE:12"),
    # Compact form (legacy still works)
    ("Doble Oportunidad",  "1X",              None, "DOUBLE_CHANCE:1X"),
    # DNB
    ("Draw No Bet",        "Canada",          None, "DNB:HOME"),
    # Totals
    ("Total Goles",        "Under 2.5",       2.5,  "TOTAL_GOALS:UNDER:2.5"),
    ("Goals Over/Under",   "Under 3.5",       3.5,  "TOTAL_GOALS:UNDER:3.5"),
    ("Total Goals",        "Over 1.5",        1.5,  "TOTAL_GOALS:OVER:1.5"),
    # BTTS
    ("BTTS",               "Yes",             None, "BTTS:YES"),
    ("Ambos Marcan",       "No",              None, "BTTS:NO"),
    # Corners (internal naming: TOTAL_CORNERS — user spec accepts both)
    ("Total Corners",      "Under 9.5",       9.5,  "TOTAL_CORNERS:UNDER:9.5"),
    # Handicap
    ("Handicap",           "Canada -1",       -1.0, "HANDICAP:HOME:-1"),
])
def test_market_identity_key_examples_from_user_spec(
    market, selection, line, expected_key,
):
    """All identity keys listed in the user spec must resolve correctly."""
    out = normalize_market_identity(
        {"market": market, "selection": selection, "line": line},
        home_name="Canada", away_name="Curacao",
    )
    assert out["identity_key"] == expected_key, (
        f"market={market!r} selection={selection!r} line={line!r}: "
        f"expected {expected_key!r}, got {out['identity_key']!r}"
    )


def test_evaluated_market_block_exposed_when_identity_resolved():
    """Fix B-1: when the engine identified the market, the trace MUST
    expose a self-describing ``evaluated_market`` block."""
    trace = build_market_trace({
        "match_label": "Canada vs Curacao",
        "recommendation": {
            "market":     "Doble Oportunidad",
            "selection":  "Canada o Empate",
            "odds_range": "1.25",
            "confidence": 75,
        },
    })
    em = trace["evaluated_market"]
    assert em is not None
    assert em["market_family"]       == "DOUBLE_CHANCE"
    assert em["side"]                == "1X"
    assert em["line"]                is None
    assert em["odds"]                == 1.25
    assert em["selection"]           == "Canada o Empate"
    assert em["market_identity_key"] == "DOUBLE_CHANCE:1X"
    # ``market_name`` is human-readable (e.g. "Doble oportunidad 1X")
    assert em["market_name"]
    assert "doble" in em["market_name"].lower()


def test_evaluated_market_block_is_none_when_identity_missing():
    """Fix B-1 (negative side): when no identity could be resolved,
    ``evaluated_market`` MUST be None — never a fake/placeholder dict."""
    trace = build_market_trace({
        "match_label": "Argentina vs Austria",
        "recommendation": {
            "odds_range": "1.25",
            "confidence": 70,
            # NO market / selection
        },
        "_moneyball": {
            "classification": "MARKET_TRAP",
            "classification_reason": "cuota baja",
            "confidence": 70,
            "fragility": {"score": 40, "factors": []},
        },
    })
    assert trace["evaluated_market"] is None


def test_evaluated_market_block_for_total_goals_under_2_5():
    trace = build_market_trace({
        "match_label": "Real Madrid vs Barcelona",
        "recommendation": {
            "market":     "Total Goles",
            "selection":  "Under 2.5",
            "line":       2.5,
            "odds_range": "1.80",
            "confidence": 70,
        },
    })
    em = trace["evaluated_market"]
    assert em is not None
    assert em["market_family"]       == "TOTAL_GOALS"
    assert em["side"]                == "UNDER"
    assert em["line"]                == 2.5
    assert em["market_identity_key"] == "TOTAL_GOALS:UNDER:2.5"


def test_evaluated_market_block_for_btts_yes():
    trace = build_market_trace({
        "match_label": "Atletico vs Sevilla",
        "recommendation": {
            "market":     "BTTS",
            "selection":  "Yes",
            "odds_range": "1.65",
            "confidence": 67,
        },
    })
    em = trace["evaluated_market"]
    assert em is not None
    assert em["market_family"]       == "BTTS"
    assert em["side"]                == "YES"
    assert em["market_identity_key"] == "BTTS:YES"


def test_evaluated_market_block_for_corners_under_9_5():
    trace = build_market_trace({
        "match_label": "Liverpool vs Brentford",
        "recommendation": {
            "market":     "Total Corners",
            "selection":  "Under 9.5",
            "line":       9.5,
            "odds_range": "1.90",
            "confidence": 65,
        },
    })
    em = trace["evaluated_market"]
    assert em is not None
    assert em["market_family"]       == "TOTAL_CORNERS"
    assert em["side"]                == "UNDER"
    assert em["line"]                == 9.5
    assert em["market_identity_key"] == "TOTAL_CORNERS:UNDER:9.5"


# ─────────────────────────────────────────────────────────────────────
# Fix B-2 · low odds + no market identity → MARKET_IDENTITY_MISSING
# ─────────────────────────────────────────────────────────────────────
def test_low_odds_without_market_identity_classified_as_identity_missing():
    """Fix B-2: a 1.25 odds entry with NO market identified MUST NOT
    be classified as MARKET_TRAP / LOW_ODDS_NO_CUSHION. It must be
    MARKET_IDENTITY_MISSING with the user-binding phrasing."""
    trace = build_market_trace({
        "match_label": "Argentina vs Austria",
        "recommendation": {
            "odds_range": "1.25",
            "confidence": 70,
            # market / selection deliberately omitted
        },
        "_moneyball": {
            "classification": "MARKET_TRAP",
            "classification_reason": "cuota baja",
            "confidence": 70,
            "fragility": {"score": 40, "factors": []},
        },
    })
    assert trace["rejection_code"]   == "MARKET_IDENTITY_MISSING"
    assert trace["classification"]    == "MARKET_IDENTITY_MISSING"
    assert trace["state"]              == "REQUIRES_MARKET_IDENTIFICATION"
    # Binding user phrasing:
    assert "cuota baja detectada" in trace["rejection_reason"].lower()
    assert "trampa" in trace["rejection_reason"].lower()
    assert "mercado exacto" in trace["rejection_reason"].lower()
    # Edge fields blanked (cannot compute without identity)
    assert trace["edge"] is None
    assert trace["edge_pct"] is None
    assert trace["estimated_probability"] is None
    assert trace["implied_probability"] is None
    # Odds preserved for the UI's "Cuota detectada" line.
    assert trace["odds_visible"] == 1.25
    # Audit trail
    assert "MARKET_IDENTITY_MISSING" in trace["f73_reason_codes"]
    assert any("LOW_ODDS_TRAP_SUPPRESSED" in c
                for c in trace["f73_reason_codes"])


def test_low_odds_threshold_140_inclusive():
    """Exactly 1.40 must NOT trigger the low-odds-specific message
    (the threshold is < 1.40 strictly)."""
    trace = build_market_trace({
        "match_label": "x vs y",
        "recommendation": {
            "odds_range": "1.40",
            "confidence": 65,
        },
        "_moneyball": {
            "classification": "MARKET_TRAP",
            "classification_reason": "trampa",
            "confidence": 65,
            "fragility": {"score": 30, "factors": []},
        },
    })
    # Either classification is allowed here (still MARKET_IDENTITY_MISSING
    # because of MARKET_TRAP without identity) — but the reason should
    # NOT use the "Cuota baja" phrasing.
    assert trace["rejection_code"] == "MARKET_IDENTITY_MISSING"
    assert "cuota baja detectada" not in trace["rejection_reason"].lower()


def test_normal_odds_without_market_identity_uses_generic_reason():
    """At 2.50 with no market identity, the reason must NOT claim
    "cuota baja"."""
    trace = build_market_trace({
        "match_label": "x vs y",
        "recommendation": {
            "odds_range": "2.50",
            "confidence": 60,
        },
        "_moneyball": {
            "classification": "MARKET_TRAP",
            "classification_reason": "trampa",
            "confidence": 60,
            "fragility": {"score": 40, "factors": []},
        },
    })
    assert trace["rejection_code"] == "MARKET_IDENTITY_MISSING"
    assert "cuota baja detectada" not in trace["rejection_reason"].lower()
    # Must still mention "no se identificó".
    assert "no se identific" in trace["rejection_reason"].lower()


def test_market_identified_with_low_odds_keeps_real_classification():
    """When the market IS identified, low-odds rejection is allowed
    (the F73 guard does NOT override it). Just sanity-check."""
    trace = build_market_trace({
        "match_label": "Canada vs Curacao",
        "recommendation": {
            "market":     "Doble Oportunidad",
            "selection":  "Canada o Empate",
            "odds_range": "1.25",
            "confidence": 70,
        },
        "_moneyball": {
            "classification": "NO_BET_VALUE",
            "classification_reason": "cuota baja sin colchón",
            "confidence": 70,
            "fragility": {"score": 35, "factors": []},
        },
    })
    # Identity resolved → F73 does NOT trigger.
    assert trace["rejection_code"] != "MARKET_IDENTITY_MISSING"
    assert trace["evaluated_market"] is not None
    assert trace["evaluated_market"]["market_identity_key"] == "DOUBLE_CHANCE:1X"
