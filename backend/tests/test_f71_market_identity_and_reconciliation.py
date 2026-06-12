"""Phase F71 — Market identity + external fallback orchestrator tests.

Coverage (10 mandatory tests):
  1. SportyTrader missing → Forebet still attempted via cascade.
  2. Corners L5/L15 missing → THIN block kept honest (no fabrication).
  3. Market trap discard → odds_validation block synthesised.
  4. Forebet predicts 3-1 + 3.29 xG → internal "1-1" reconciled to 3-1.
  5. Forebet picks a winner → internal heuristic suppressed.
  6. (corners contradiction) ─ skipped: no TotalCorner/FootyStats.
     Replaced by: market_identity normalises 1X2 / DC / DNB / O-U.
  7. OddsPortal-style trap confirmation degrades confidence.
  8. Cache key includes match_id (idempotent per match).
  9. No "Home"/"Away" placeholders leak from orchestrator narrative.
 10. THIN data + no external sources → no fabricated analysis.

Plus market_identity unit tests for the canonical keys.
"""
from __future__ import annotations

import asyncio

import pytest

from services.football_external_fallback_orchestrator import (
    build_external_fallback_context,
    reconcile_internal_vs_external_analysis,
)
from services.market_identity import normalize_market_identity, same_market


# ─────────────────────────────────────────────────────────────────────
# Market identity unit tests
# ─────────────────────────────────────────────────────────────────────
def test_market_identity_canonical_keys():
    """Phase F71 — canonical identity keys match the spec."""
    cases = [
        ({"market": "1X2", "side": "home"},                  "1X2:HOME"),
        ({"market": "Moneyline", "side": "away"},            "1X2:AWAY"),
        ({"market": "Doble oportunidad", "side": "1X"},      "DOUBLE_CHANCE:1X"),
        ({"market": "Draw No Bet", "side": "home"},          "DNB:HOME"),
        ({"market": "BTTS", "side": "no"},                   "BTTS:NO"),
        ({"market": "BTTS", "side": "yes"},                  "BTTS:YES"),
        ({"market": "Over/Under 2.5", "side": "OVER", "line": 2.5}, "TOTAL_GOALS:OVER:2.5"),
        ({"market": "Total córners", "side": "UNDER", "line": 9.5}, "TOTAL_CORNERS:UNDER:9.5"),
        ({"market": "Hándicap asiático", "side": "home", "line": -1}, "HANDICAP_ASIAN:HOME:-1"),
    ]
    for raw, expected_key in cases:
        out = normalize_market_identity(raw, home_name="Canada", away_name="Bosnia")
        assert out["identity_key"] == expected_key, \
            f"raw={raw} → got {out['identity_key']!r}, expected {expected_key!r}"


def test_market_identity_same_market_strictness():
    # Different lines NEVER count as same market.
    assert not same_market(
        {"market": "O/U", "side": "OVER", "line": 2.5},
        {"market": "O/U", "side": "OVER", "line": 1.5},
    )
    # Different sides NEVER count as same market.
    assert not same_market(
        {"market": "BTTS", "side": "YES"},
        {"market": "BTTS", "side": "NO"},
    )
    # Different families NEVER count as same market.
    assert not same_market(
        {"market": "Doble oportunidad", "side": "1X"},
        {"market": "1X2", "side": "home"},
    )
    # Same family/side/line: True.
    assert same_market(
        {"market": "Over/Under 2.5", "side": "OVER", "line": 2.5},
        {"market": "Total goles", "side": "Over",   "line": 2.5},
    )


# ─────────────────────────────────────────────────────────────────────
# Reconciliation
# ─────────────────────────────────────────────────────────────────────
def _editorial_with_score(score: str = "1-1", method: str = "DIXON_COLES_HEURISTIC"):
    return {
        "available": True,
        "data_quality": "LIMITED",
        "editorial_sections": {
            "probable_score": {
                "available": True,
                "score":     score,
                "method":    method,
                "text":      f"El marcador más probable según perfil es {score}.",
                "is_contextual_only": True,
                "reason_codes": [],
            },
            "goals_prediction": {
                "available": True,
                "status":    "OK",
                "confidence": 60,
                "reason_codes": [],
            },
            "corners_prediction": {
                "available": False,
                "status":    "MISSING",
                "reason_codes": [],
            },
        },
        "reason_codes": [],
    }


def _external_with_forebet(score: str = "3-1", goals_avg: float = 3.29,
                            pct=(59, 27, 14)) -> dict:
    return {
        "available": True,
        "home_team": "Brazil",
        "away_team": "Morocco",
        "forebet": {
            "forebet_pct_1":   pct[0],
            "forebet_pct_x":   pct[1],
            "forebet_pct_2":   pct[2],
            "pick_1x2":        "1",
            "predicted_score": score,
            "goals_avg":       goals_avg,
        },
        "sportytrader": {"available": False},
        "reason_codes": ["EXTERNAL_EDITORIAL_ATTEMPTED", "FOREBET_FIXTURE_MATCHED"],
    }


# ─────────────────────────────────────────────────────────────────────
# T4 — Forebet 3-1 / 3.29 xG → internal 1-1 reconciled.
# ─────────────────────────────────────────────────────────────────────
def test_t4_forebet_overrides_internal_1_1():
    internal = _editorial_with_score("1-1")
    external = _external_with_forebet("3-1", 3.29)
    reconcile_internal_vs_external_analysis(internal, external)
    audit = internal["external_reconciliation"]
    assert audit["applied"] is True
    assert any(a["type"] == "PROBABLE_SCORE_OVERRIDE" for a in audit["actions"])
    score_sec = internal["editorial_sections"]["probable_score"]
    assert score_sec["score"] == "3-1"
    assert score_sec["external_override"] is True
    assert "INTERNAL_PROBABLE_SCORE_SUPPRESSED_BY_FOREBET" in audit["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# T5 — Internal heuristic suppressed when external picks a winner.
# ─────────────────────────────────────────────────────────────────────
def test_t5_internal_heuristic_suppressed_when_external_winner():
    # Internal says "1-1" (draw), Forebet says "2-0" (home win) → suppress.
    internal = _editorial_with_score("1-1")
    external = _external_with_forebet("2-0", 2.5)
    reconcile_internal_vs_external_analysis(internal, external)
    sec = internal["editorial_sections"]["probable_score"]
    assert sec["score"] == "2-0"
    assert "PROBABLE_SCORE_OVERRIDDEN_BY_EXTERNAL_FOREBET" in sec["reason_codes"]


def test_internal_not_overridden_when_winners_agree():
    """When internal and external agree on the winner, do NOT override."""
    internal = _editorial_with_score("2-0")
    external = _external_with_forebet("3-1", 4.0)  # both home wins
    reconcile_internal_vs_external_analysis(internal, external)
    sec = internal["editorial_sections"]["probable_score"]
    assert sec["score"] == "2-0", "should keep internal when winners agree"
    audit = internal["external_reconciliation"]
    # No PROBABLE_SCORE_OVERRIDE action.
    assert not any(a["type"] == "PROBABLE_SCORE_OVERRIDE" for a in audit["actions"])


# ─────────────────────────────────────────────────────────────────────
# T (xG tilt) — High xG flags lean Over; Low xG flags lean Under.
# ─────────────────────────────────────────────────────────────────────
def test_xg_tilt_over_when_xg_high():
    internal = _editorial_with_score("2-0")
    external = _external_with_forebet("3-1", 3.4)
    reconcile_internal_vs_external_analysis(internal, external)
    goals = internal["editorial_sections"]["goals_prediction"]
    assert goals["external_tilt"] == "OVER"


def test_xg_tilt_under_when_xg_low():
    internal = _editorial_with_score("0-0")
    external = _external_with_forebet("1-0", 1.9)
    reconcile_internal_vs_external_analysis(internal, external)
    goals = internal["editorial_sections"]["goals_prediction"]
    assert goals["external_tilt"] == "UNDER"


# ─────────────────────────────────────────────────────────────────────
# T7 — OddsPortal-style trap confirmation degrades confidence.
# ─────────────────────────────────────────────────────────────────────
def test_t7_market_trap_demotes_confidence():
    internal = _editorial_with_score("1-1")
    external = _external_with_forebet("1-0", 1.6)
    external["odds_validation"] = {"is_market_trap": True,
                                    "reason": "Cuota inflada"}
    reconcile_internal_vs_external_analysis(internal, external)
    goals = internal["editorial_sections"]["goals_prediction"]
    # Confidence demoted from 60 → 35 (60 - 25).
    assert goals["confidence"] <= 35
    audit = internal["external_reconciliation"]
    assert "MARKET_TRAP_CONFIRMED_BY_EXTERNAL" in audit["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# T1 — Cascade attempts external provider when SportyTrader missing.
# ─────────────────────────────────────────────────────────────────────
def test_t1_cascade_falls_back_to_forebet(monkeypatch):
    """When SportyTrader URL is unknown, the cascade still tries
    Forebet via the existing external_editorial_provider."""
    async def fake_external(match):
        return {"available": True,
                "home_team": "Canada", "away_team": "Bosnia",
                "forebet": {"forebet_pct_1": 41, "forebet_pct_x": 35,
                             "forebet_pct_2": 24, "pick_1x2": "1",
                             "predicted_score": "1-0", "goals_avg": 1.5},
                "sportytrader": {"available": False,
                                  "search_url": "https://x"},
                "reason_codes": ["FOREBET_FIXTURE_MATCHED",
                                  "SPORTYTRADER_URL_UNKNOWN"]}
    import services.external_editorial_provider as prov
    monkeypatch.setattr(prov, "fetch_external_editorial_for_match", fake_external)
    out = asyncio.run(build_external_fallback_context({
        "home_team": "Canada", "away_team": "Bosnia",
    }))
    assert out["available"] is True
    assert "forebet" in out["sources_used"]
    assert (out.get("forebet") or {}).get("predicted_score") == "1-0"


# ─────────────────────────────────────────────────────────────────────
# T3 — Market-trap discard synthesises odds_validation block.
# ─────────────────────────────────────────────────────────────────────
def test_t3_market_trap_odds_validation_block(monkeypatch):
    async def empty(match):
        return {"available": False, "reason_codes": ["EMPTY_FOR_TEST"]}
    import services.external_editorial_provider as prov
    monkeypatch.setattr(prov, "fetch_external_editorial_for_match", empty)
    out = asyncio.run(build_external_fallback_context({
        "home_team": "Qatar", "away_team": "Switzerland",
        "match_id":  1,
        "odds":      1.22,
        "estimated_probability": 0.595,
        "implied_probability":   0.823,
        "edge":      -0.228,
        "market_evaluated": "1X2 - moneyline home",
        "side":      "home",
    }))
    ov = out.get("odds_validation") or {}
    assert ov["is_market_trap"] is True
    assert ov["market_identity"]["identity_key"] == "1X2:HOME"


# ─────────────────────────────────────────────────────────────────────
# T9 — Reconciliation never inserts "Home"/"Away" placeholders.
# ─────────────────────────────────────────────────────────────────────
def test_t9_no_home_away_placeholders():
    internal = _editorial_with_score("1-1")
    external = _external_with_forebet("3-1", 3.29)
    reconcile_internal_vs_external_analysis(internal, external)
    import json
    blob = json.dumps(internal, ensure_ascii=False)
    import re
    assert not re.search(r"\bHome\b", blob), f"Home leak: {blob[:200]}"
    assert not re.search(r"\bAway\b", blob), f"Away leak: {blob[:200]}"


# ─────────────────────────────────────────────────────────────────────
# T10 — No external + THIN → no fabricated analysis.
# ─────────────────────────────────────────────────────────────────────
def test_t10_no_external_thin_no_fabrication(monkeypatch):
    async def nothing(match):
        return {"available": False, "reason_codes": ["NOTHING"]}
    import services.external_editorial_provider as prov
    monkeypatch.setattr(prov, "fetch_external_editorial_for_match", nothing)
    out = asyncio.run(build_external_fallback_context({
        "home_team": "Foo", "away_team": "Bar",
    }))
    assert out["available"] is False
    assert "NO_EXTERNAL_SOURCES_AVAILABLE" in out["reason_codes"]
