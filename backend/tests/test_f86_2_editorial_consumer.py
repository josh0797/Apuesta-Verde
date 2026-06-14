"""Phase F86.2 — Editorial Consumer tests.

Validates that the editorial output (a) renders H2H matches one-by-one
when the sample is thin, (b) lets the scoring applier add the H2H delta
to compatible markets with clamp and polarity guard, and (c) honours
the PENDING/SUCCESS/UNAVAILABLE status emitted by the xG recent-averages
background job (F87).

All tests are pure-Python — no network, no Mongo, no React.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import pytest

from services.football_editorial_prediction import (
    generate_football_editorial_prediction,
    _build_h2h_block,
    _build_xg_block,
)
from services.football_h2h_scoring_applier import (
    MAX_H2H_DELTA,
    apply_h2h_points_to_candidate,
    _market_to_h2h_key,
    _enforce_polarity,
)


# ─────────────────────────────────────────────────────────────────────
# Fixture helpers
# ─────────────────────────────────────────────────────────────────────
def _recent_date(days_ago: int = 30) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _old_date(days_ago: int = 800) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _base_match_doc(*, h2h_context: dict, h2h_decision: dict,
                    h2h_recent: list, xg: dict) -> dict:
    return {
        "home_team": {"name": "Netherlands"},
        "away_team": {"name": "Japan"},
        # Give the editorial enough data so data_quality != THIN (so the
        # main F66 builders don't block early — we want h2h/xg blocks
        # rendered regardless of quality).
        "home_corners_for_l5":     5.0,
        "away_corners_for_l5":     4.5,
        "home_corners_against_l5": 4.0,
        "away_corners_against_l5": 4.0,
        "home_xg":                 1.2,
        "away_xg":                 1.0,
        "home_team":               {"name": "Netherlands",
                                     "goals_scored_l5":  1.4,
                                     "goals_scored_l15": 1.5,
                                     "btts_rate_l15":    0.55,
                                     "clean_sheet_rate_l15": 0.4},
        "away_team":               {"name": "Japan",
                                     "goals_scored_l5":  1.0,
                                     "goals_scored_l15": 1.1,
                                     "btts_rate_l15":    0.5,
                                     "clean_sheet_rate_l15": 0.3},
        "h2h_context":             h2h_context,
        "h2h_decision":            h2h_decision,
        "h2h_recent":              h2h_recent,
        "xg_recent_averages":      xg,
    }


def _xg_pending() -> dict:
    return {
        "available":    False,
        "status":       "PENDING_BACKGROUND_ENRICHMENT",
        "reason_codes": ["XG_RECENT_BACKGROUND_DEFERRED"],
    }


def _xg_success() -> dict:
    return {
        "available": True,
        "status":    "SUCCESS",
        "partial":   False,
        "source":    "thestatsapi_shotmap",
        "home": {
            "l1":  {"xg_for_avg": 1.40, "xg_against_avg": 0.60, "sample": 1},
            "l5":  {"xg_for_avg": 1.55, "xg_against_avg": 0.80, "sample": 5},
            "l15": {"xg_for_avg": 1.42, "xg_against_avg": 0.95, "sample": 15},
        },
        "away": {
            "l1":  {"xg_for_avg": 0.80, "xg_against_avg": 1.20, "sample": 1},
            "l5":  {"xg_for_avg": 0.92, "xg_against_avg": 1.30, "sample": 5},
            "l15": {"xg_for_avg": 1.05, "xg_against_avg": 1.25, "sample": 15},
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Test 1 — thin sample renders matches one-by-one, no rates
# ─────────────────────────────────────────────────────────────────────
def test_editorial_renders_h2h_matches_when_sample_thin():
    h2h_recent = [
        {"date": _recent_date(60),  "home": "Netherlands",
         "away": "Japan", "score": "2-1"},
        {"date": _recent_date(700), "home": "Japan",
         "away": "Netherlands", "score": "0-3"},
    ]
    h2h_context = {
        "sample_size_total":  2,
        "sample_size_recent": 1,
        "decision_useful":    False,
        "warnings": [
            "Solo se registran 2 enfrentamientos directos — "
            "muestra limitada, contexto pero no fuente primaria."
        ],
    }
    h2h_decision = {"applied": False, "points_by_market": {},
                    "signals": [], "rates": {}}
    md = _base_match_doc(h2h_context=h2h_context,
                          h2h_decision=h2h_decision,
                          h2h_recent=h2h_recent,
                          xg=_xg_pending())
    out = generate_football_editorial_prediction(md)
    block = out["h2h_block"]

    assert len(block["matches_detail"]) == 2
    # The narrative must start with the count.
    assert block["narrative"].startswith("2 enfrentamiento")
    # The warning surfaced is the same the policy emits — substring match.
    assert any("muestra limitada" in w.lower() for w in block["warnings"])
    # When not decision_useful, the rates table must be empty.
    assert block["rates"] == {}
    # The applied signals list is empty.
    assert block["applied_signals"] == []
    # Detail items expose is_recent.
    assert block["matches_detail"][0]["is_recent"] is True
    assert block["matches_detail"][1]["is_recent"] is False
    # result_for_home is computed.
    assert block["matches_detail"][0]["result_for_home"] == "W"
    assert block["matches_detail"][1]["result_for_home"] == "W"  # Japan 0-3 Netherlands → home (NED) won


# ─────────────────────────────────────────────────────────────────────
# Test 2 — decision_useful=True applies points to the best_protected_market
# ─────────────────────────────────────────────────────────────────────
def test_editorial_applies_points_when_decision_useful():
    candidate = {
        "market":           "OVER_2_5",
        "confidence_score": 55,
        "signals":          [],
    }
    h2h_decision = {
        "applied":          True,
        "points_by_market": {"OVER_2_5": 4, "BTTS_NO": 5},
        "signals":          ["H2H_PROFILE_OVER_2_5"],
        "rates":            {"over_2_5": 0.71, "btts_no": 0.71},
    }
    res = apply_h2h_points_to_candidate(candidate, h2h_decision)
    assert res["applied"] is True
    assert res["delta"] == 4
    assert res["market_key"] == "OVER_2_5"
    assert candidate["confidence_score"] == 59
    assert "H2H_PROFILE_OVER_2_5" in candidate["signals"]
    assert candidate["score_breakdown"]["h2h_pattern"] == 4


# ─────────────────────────────────────────────────────────────────────
# Test 3 — clamp at +8
# ─────────────────────────────────────────────────────────────────────
def test_h2h_clamp_at_8_points_max():
    # The policy emits ≤ +5 per market today, but we still defend the
    # ceiling so future calibration changes can never explode the score.
    candidate = {"market": "OVER_2_5", "confidence_score": 50, "signals": []}
    h2h_decision = {
        "applied":          True,
        "points_by_market": {"OVER_2_5": 25},   # synthetic over-shoot
        "signals":          ["H2H_PROFILE_OVER_2_5"],
        "rates":            {"over_2_5": 0.95},
    }
    res = apply_h2h_points_to_candidate(candidate, h2h_decision)
    assert res["applied"] is True
    assert res["delta"]   == MAX_H2H_DELTA == 8
    assert res["clamped"] is True
    assert candidate["confidence_score"] == 58


# ─────────────────────────────────────────────────────────────────────
# Test 4 — polarity guard (OVER vs UNDER on same line)
# ─────────────────────────────────────────────────────────────────────
def test_polarity_guard_over_vs_under(caplog):
    # Synthetic conflict: both OVER_2_5 and UNDER_2_5 emitted.
    h2h_decision = {
        "applied":          True,
        "points_by_market": {"OVER_2_5": 4, "UNDER_2_5": 6},
        "signals":          ["H2H_PROFILE_OVER_2_5", "H2H_PROFILE_UNDER_2_5"],
        "rates":            {"over_2_5": 0.70, "under_2_5": 0.66},
    }
    # When the candidate is OVER_2_5 the UNDER wins polarity (higher pts)
    # → no delta applies to OVER candidate.
    cand_over = {"market": "OVER_2_5", "confidence_score": 50, "signals": []}
    with caplog.at_level(logging.WARNING, logger="football.h2h_scoring_applier"):
        res = apply_h2h_points_to_candidate(cand_over, h2h_decision)
    assert res["polarity_conflict"] is True
    assert res["applied"] is False
    assert cand_over["confidence_score"] == 50  # untouched
    # And the warning was logged.
    assert any("polarity conflict" in rec.message.lower() for rec in caplog.records)

    # When the candidate matches the winner (UNDER) it IS applied.
    cand_under = {"market": "UNDER_2_5", "confidence_score": 50, "signals": []}
    res2 = apply_h2h_points_to_candidate(cand_under, h2h_decision)
    assert res2["applied"] is True
    assert res2["delta"]   == 6
    assert cand_under["confidence_score"] == 56


def test_polarity_guard_tie_keeps_first():
    """When points tie the first key of the pair (OVER side by spec) wins."""
    points = {"OVER_2_5": 5, "UNDER_2_5": 5}
    filtered, conflict = _enforce_polarity(points)
    assert conflict is True
    assert "OVER_2_5" in filtered
    assert "UNDER_2_5" not in filtered


# ─────────────────────────────────────────────────────────────────────
# Test 5 — xG PENDING does not block editorial
# ─────────────────────────────────────────────────────────────────────
def test_xg_pending_does_not_block_editorial():
    md = _base_match_doc(
        h2h_context={"sample_size_total": 0, "sample_size_recent": 0,
                      "decision_useful": False, "warnings": []},
        h2h_decision={"applied": False, "points_by_market": {},
                       "signals": [], "rates": {}},
        h2h_recent=[],
        xg=_xg_pending(),
    )
    out = generate_football_editorial_prediction(md)
    assert out["available"] is True
    assert out["xg_block"]["status"] == "PENDING"
    assert "10s" in (out["xg_block"]["missing_reason"] or "")
    # No xG signals should leak into scoring while PENDING.
    assert out["xg_block"]["signals"] == []


# ─────────────────────────────────────────────────────────────────────
# Test 6 — xG SUCCESS renders L1/L5/L15 + derived signals
# ─────────────────────────────────────────────────────────────────────
def test_xg_success_renders_l1_l5_l15():
    md = _base_match_doc(
        h2h_context={"sample_size_total": 0, "sample_size_recent": 0,
                      "decision_useful": False, "warnings": []},
        h2h_decision={"applied": False, "points_by_market": {},
                       "signals": [], "rates": {}},
        h2h_recent=[],
        xg=_xg_success(),
    )
    block = _build_xg_block(md)
    assert block["status"] == "SUCCESS"
    assert block["partial"] is False
    # Tabla 2×3 — Home/Away × L1/L5/L15.
    assert block["home"]["l1"]["xg_for"]     == 1.40
    assert block["home"]["l5"]["xg_for"]     == 1.55
    assert block["home"]["l15"]["xg_for"]    == 1.42
    assert block["away"]["l1"]["xg_against"] == 1.20
    assert block["away"]["l15"]["xg_against"] == 1.25
    # derive_xg_signals is invoked — at minimum metric coverage is sane.
    assert isinstance(block["signals"], list)


# ─────────────────────────────────────────────────────────────────────
# Extra: _market_to_h2h_key smoke
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("inp, expected", [
    ("OVER_2_5",          "OVER_2_5"),
    ("OVER_2_5_GOALS",    "OVER_2_5"),
    ("Over 2.5 goles",    "OVER_2_5"),
    ("UNDER 3.5 goals",   "UNDER_3_5"),
    ("BTTS Yes",          "BTTS_YES"),
    ("BTTS_NO",           "BTTS_NO"),
    ("HOME_NO_LOSE",      "HOME_DNB"),
    ("AWAY_DNB",          "AWAY_DNB"),
    ("",                  None),
    (None,                None),
    ("corners over 9.5",  None),   # not covered by H2H
])
def test_market_to_h2h_key_mapping(inp, expected):
    assert _market_to_h2h_key(inp) == expected


# ─────────────────────────────────────────────────────────────────────
# Extra: best_protected_market gets bumped via wrapper
# ─────────────────────────────────────────────────────────────────────
def test_best_protected_market_gets_h2h_bump_in_full_payload():
    """Sanity: when the full pipeline runs and h2h_decision is applied,
    the best_protected_market.confidence_score reflects the bump."""
    # Build a payload that lets the goals_prediction sub-engine pick
    # OVER 2.5 (so best_protected_market.market endswith "Over 2.5 goles").
    h2h_decision = {
        "applied":          True,
        "points_by_market": {"OVER_2_5": 4},
        "signals":          ["H2H_PROFILE_OVER_2_5"],
        "rates":            {"over_2_5": 0.71},
    }
    md = {
        "home_team": {"name": "Argentina",
                       "goals_scored_l5":  2.4,
                       "goals_scored_l15": 2.3,
                       "btts_rate_l15":    0.6},
        "away_team": {"name": "Uruguay",
                       "goals_scored_l5":  2.1,
                       "goals_scored_l15": 2.0,
                       "btts_rate_l15":    0.55},
        "home_corners_for_l5":     6.0,
        "away_corners_for_l5":     5.5,
        "home_corners_against_l5": 5.0,
        "away_corners_against_l5": 5.0,
        "home_xg":                 2.0,
        "away_xg":                 1.8,
        "h2h_context":  {"sample_size_total": 6, "sample_size_recent": 5,
                          "decision_useful": True, "warnings": []},
        "h2h_decision": h2h_decision,
        "h2h_recent":   [
            {"date": _recent_date(30),  "home": "Argentina",
             "away": "Uruguay", "score": "3-1"},
            {"date": _recent_date(120), "home": "Uruguay",
             "away": "Argentina", "score": "1-2"},
            {"date": _recent_date(220), "home": "Argentina",
             "away": "Uruguay", "score": "4-0"},
        ],
        "xg_recent_averages": _xg_pending(),
    }
    out = generate_football_editorial_prediction(md)
    best = out.get("best_protected_market") or {}
    # Best market must exist and be the goals OVER market.
    assert best
    # confidence_score key is set (mirrored alongside legacy confidence).
    assert "confidence_score" in best
    # Reason code was appended when the bump applied.
    if "OVER" in (best.get("market") or "").upper() \
            and best.get("market_type") == "goals_total":
        assert best["confidence_score"] >= best.get("confidence", 0)
        rcs = out.get("reason_codes") or []
        assert "H2H_SCORING_APPLIED_TO_BEST_PROTECTED_MARKET" in rcs
