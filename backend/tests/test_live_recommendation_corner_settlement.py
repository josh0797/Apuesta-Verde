"""Tests for `services.live_recommendation_settlement.settle_corner_market`.

Covers all Fix 3 acceptance criteria — total / team / handicap corners
in English and Spanish, missing stats, push/void, and Asian quarter
routing to manual.
"""

from __future__ import annotations

import pytest

from services.live_recommendation_settlement import (
    settle_corner_market,
    settle_event_extended,
    RC_CORNER_TOTAL_OVER_HIT,
    RC_CORNER_TOTAL_OVER_MISS,
    RC_CORNER_TOTAL_UNDER_HIT,
    RC_CORNER_TOTAL_UNDER_MISS,
    RC_CORNER_TOTAL_VOID_PUSH,
    RC_TEAM_CORNERS_OVER_HIT,
    RC_TEAM_CORNERS_UNDER_HIT,
    RC_CORNER_HANDICAP_HIT,
    RC_CORNER_HANDICAP_MISS,
    RC_CORNER_HANDICAP_VOID_PUSH,
    RC_MISSING_CORNER_STATS,
    RC_ASIAN_REQUIRES_MANUAL,
    RC_UNKNOWN_CORNER_MARKET,
)


def _evt(market: str, selection: str | None = None) -> dict:
    return {
        "sport":    "football",
        "match_id": "m-1",
        "source":   "engine",
        "minute":   90,
        "score":    {"home": 1, "away": 1},
        "recommendation": {
            "market":    market,
            "selection": selection or market,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Total corners
# ─────────────────────────────────────────────────────────────────────
def test_total_corners_over_8_5_hits_with_10():
    out = settle_corner_market(
        _evt("Over 8.5 corners"),
        {"corners_home": 6, "corners_away": 4},
    )
    assert out["status"] == "hit"
    assert out["market_type"] == "TOTAL_CORNERS"
    assert out["line"] == 8.5
    assert out["total_corners"] == 10
    assert RC_CORNER_TOTAL_OVER_HIT in out["reason_codes"]


def test_total_corners_under_9_5_misses_with_10():
    out = settle_corner_market(
        _evt("Under 9.5 corners"),
        {"corners_home": 7, "corners_away": 3},
    )
    assert out["status"] == "miss"
    assert RC_CORNER_TOTAL_UNDER_MISS in out["reason_codes"]


def test_total_corners_over_9_voids_at_exactly_9():
    out = settle_corner_market(
        _evt("Over 9 corners"),
        {"corners_home": 5, "corners_away": 4},
    )
    assert out["status"] == "void"
    assert RC_CORNER_TOTAL_VOID_PUSH in out["reason_codes"]


def test_total_corners_under_9_voids_at_exactly_9():
    out = settle_corner_market(
        _evt("Under 9 corners"),
        {"home_corners": 4, "away_corners": 5},
    )
    assert out["status"] == "void"


# ─────────────────────────────────────────────────────────────────────
# Team corners
# ─────────────────────────────────────────────────────────────────────
def test_home_team_over_4_5_corners_hits_with_5():
    out = settle_corner_market(
        _evt("Home team Over 4.5 corners"),
        {"corners_home": 5, "corners_away": 2},
    )
    assert out["status"] == "hit"
    assert out["market_type"] == "TEAM_CORNERS"
    assert out["side"] == "home"
    assert out["actual_value"] == 5
    assert RC_TEAM_CORNERS_OVER_HIT in out["reason_codes"]


def test_away_team_under_3_5_corners_hits_with_3():
    out = settle_corner_market(
        _evt("Away team Under 3.5 corners"),
        {"corners_home": 6, "corners_away": 3},
    )
    assert out["status"] == "hit"
    assert out["side"] == "away"
    assert RC_TEAM_CORNERS_UNDER_HIT in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Spanish detection
# ─────────────────────────────────────────────────────────────────────
def test_spanish_mas_de_8_5_corners_settles_correctly():
    out = settle_corner_market(
        _evt("Más de 8.5 córners"),
        {"corners_home": 5, "corners_away": 5},
    )
    assert out["status"] == "hit"
    assert out["line"] == 8.5


def test_spanish_menos_de_9_5_tiros_de_esquina_settles_correctly():
    out = settle_corner_market(
        _evt("Menos de 9.5 tiros de esquina"),
        {"corners_home": 4, "corners_away": 4},
    )
    assert out["status"] == "hit"
    assert out["total_corners"] == 8


def test_spanish_team_corners_resolves_team_via_name():
    # Spanish team-corners market that uses the actual team name
    # instead of the generic "local"/"visitante" tokens.
    evt = _evt("México más de 4.5 córners")
    fms = {
        "home_team":     {"name": "México"},
        "away_team":     {"name": "Serbia"},
        "corners_home":  5,
        "corners_away":  1,
    }
    out = settle_corner_market(evt, fms)
    assert out["status"] == "hit"
    assert out["side"] == "home"


# ─────────────────────────────────────────────────────────────────────
# Missing stats → pending (never miss)
# ─────────────────────────────────────────────────────────────────────
def test_missing_corner_stats_returns_pending_not_miss():
    out = settle_corner_market(
        _evt("Over 8.5 corners"),
        {"home_team": {"name": "A"}, "away_team": {"name": "B"}},
    )
    assert out["status"] == "pending"
    assert out["settled"] is False
    assert RC_MISSING_CORNER_STATS in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Simple corner handicap
# ─────────────────────────────────────────────────────────────────────
def test_home_corners_handicap_minus_1_5_hits():
    # Home: 8 corners, away: 4 corners → 8-1.5=6.5 > 4 → hit
    out = settle_corner_market(
        _evt("Home corners -1.5"),
        {"corners_home": 8, "corners_away": 4},
    )
    assert out["status"] == "hit"
    assert out["market_type"] == "CORNER_HANDICAP"
    assert out["side"] == "home"
    assert RC_CORNER_HANDICAP_HIT in out["reason_codes"]


def test_home_corners_handicap_minus_1_5_misses():
    # Home: 5 corners, away: 5 → 5-1.5=3.5 < 5 → miss
    out = settle_corner_market(
        _evt("Home corners -1.5"),
        {"corners_home": 5, "corners_away": 5},
    )
    assert out["status"] == "miss"
    assert RC_CORNER_HANDICAP_MISS in out["reason_codes"]


def test_integer_corner_handicap_voids_on_exact_tie():
    # Home: 6 corners, away: 7 → 6+1=7 == 7 → void (push)
    out = settle_corner_market(
        _evt("Home corner handicap +1"),
        {"corners_home": 6, "corners_away": 7},
    )
    assert out["status"] == "void"
    assert RC_CORNER_HANDICAP_VOID_PUSH in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Asian quarter handicap → manual
# ─────────────────────────────────────────────────────────────────────
def test_asian_quarter_corner_handicap_requires_manual_settlement():
    out = settle_corner_market(
        _evt("Home corner handicap +0.25"),
        {"corners_home": 6, "corners_away": 5},
    )
    assert out["status"] == "requires_manual_settlement"
    assert out["settled"] is False
    assert RC_ASIAN_REQUIRES_MANUAL in out["reason_codes"]


def test_asian_minus_0_75_also_routes_to_manual():
    out = settle_corner_market(
        _evt("Away corner handicap -0.75"),
        {"corners_home": 6, "corners_away": 5},
    )
    assert out["status"] == "requires_manual_settlement"


# ─────────────────────────────────────────────────────────────────────
# Non-corner markets are ignored by this branch
# ─────────────────────────────────────────────────────────────────────
def test_non_corner_market_is_skipped_by_corner_branch():
    out = settle_corner_market(
        _evt("Over 2.5 goals"),
        {"corners_home": 5, "corners_away": 4},
    )
    assert out["status"] == "pending"
    assert out["market_type"] == "UNKNOWN"


def test_settle_event_extended_returns_none_for_non_corner_markets():
    out = settle_event_extended(
        _evt("BTTS YES"),
        {"corners_home": 6, "corners_away": 4},
    )
    assert out is None


def test_settle_event_extended_dispatches_corner_markets():
    out = settle_event_extended(
        _evt("Over 8.5 corners"),
        {"corners_home": 5, "corners_away": 5},
    )
    assert out is not None
    assert out["status"] == "hit"


# ─────────────────────────────────────────────────────────────────────
# Stats shape variants
# ─────────────────────────────────────────────────────────────────────
def test_corner_stats_under_final_stats_nested_shape():
    out = settle_corner_market(
        _evt("Over 8.5 corners"),
        {"final_stats": {"corners": {"home": 7, "away": 4}}},
    )
    assert out["status"] == "hit"


def test_corner_stats_under_stats_home_away_shape():
    out = settle_corner_market(
        _evt("Over 8.5 corners"),
        {"stats": {"home": {"corners": 5}, "away": {"corners": 5}}},
    )
    assert out["status"] == "hit"


# ─────────────────────────────────────────────────────────────────────
# Existing BTTS / total-goals settlement unaffected (smoke)
# ─────────────────────────────────────────────────────────────────────
def test_existing_btts_settlement_still_works():
    """Sanity check — the legacy settle_live_event_from_score path remains
    intact and still resolves BTTS YES against a 1-1 score."""
    from services.live_recommendation_history import settle_live_event_from_score
    s = settle_live_event_from_score(
        {"recommendation": {"normalized_market": "BTTS_YES"}},
        {"home": 1, "away": 1},
    )
    assert s["result"] == "hit"
