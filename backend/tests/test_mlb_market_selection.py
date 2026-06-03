"""Tests for services.mlb_market_selection (Phase 13.1).

Per spec — all 9 scenarios required:
* F5 Under preferred when bullpen risky + strong starters.
* Run Line blocked when projected margin is low.
* Moneyline chosen over Run Line when favorite is solid but margin uncertain.
* Over not forced by OPS alone.
* Ghost-edge blocks strong pick.
* High hit pressure lowers Under confidence.
* Missing odds → manual review.
* Missing data does not break engine.
* Football/basketball not affected.
"""
from __future__ import annotations

import pytest

from services.mlb_market_selection import (
    MKT_MONEYLINE, MKT_F5_UNDER, MKT_WATCHLIST, MKT_MANUAL_ODDS,
    RC_F5_UNDER_PREFERRED_OVER_FULL,
    RC_RUN_LINE_NOT_SUPPORTED,
    RC_MONEYLINE_SAFER_THAN_RUN_LINE,
    RC_OVER_REQUIRES_ODDS_CONFIRMATION,
    RC_GHOST_EDGE_BLOCKED_PICK,
    RC_PRESSURE_BASE_CHANGED_MARKET,
    RC_FULL_GAME_UNDER_FRAGILE,
    RC_BULLPEN_RISK_FAVORS_F5,
    RC_MANUAL_ODDS_REVIEW_REQUIRED,
    RC_SABERMETRICS_CONFIRMED_EDGE,
    RC_STATCAST_CONFIRMED_EDGE,
    RC_NO_INPUTS_AVAILABLE,
    select_protected_market,
)


# ─────────────────────────────────────────────────────────────────────
# Fail-soft & empty
# ─────────────────────────────────────────────────────────────────────
def test_missing_data_does_not_break_engine():
    """No payload → watchlist (no crash)."""
    out = select_protected_market(None)
    ms = out["market_selection"]
    assert ms["recommended_market"] == MKT_WATCHLIST
    assert ms["watchlist"] is True


def test_empty_dict_returns_watchlist():
    out = select_protected_market({})
    ms = out["market_selection"]
    assert ms["watchlist"] is True
    assert RC_NO_INPUTS_AVAILABLE in ms["reason_codes"]


def test_shape_is_canonical():
    out = select_protected_market({"recommendation": {"market": "Moneyline"}})
    ms = out["market_selection"]
    for k in ("recommended_market", "protected_alternative",
              "market_confidence", "fragility", "reason_codes",
              "why_this_market", "why_not_other_markets",
              "requires_manual_odds", "watchlist"):
        assert k in ms


# ─────────────────────────────────────────────────────────────────────
# F5 Under preferred when bullpen risky + strong starters
# ─────────────────────────────────────────────────────────────────────
def test_f5_under_preferred_when_bullpen_risky_and_pitchers_strong():
    pp = {
        "recommendation": {
            "market": "Full Game Under 8.5",
            "confidence_score": 72,
            "odds_range": "1.85",
        },
        "fragility": {"score": 30},
        "pressure_base": {"combined": {"pressure_tier": "NEUTRAL_PRESSURE"}},
        "bullpen_risk": {"risky": True},
        "sabermetrics": {
            "available": True, "data_quality": "strong",
            "adjustments": {"pitcher_quality_adjustment": 8,
                             "total_runs_adjustment": -3,
                             "fragility_adjustment": -2,
                             "script_survival_adjustment": 6},
            "match_edges": {},
        },
        "script_survival": {"score": 78},
        "pitcher_quality_score": 80,
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == MKT_F5_UNDER
    assert RC_F5_UNDER_PREFERRED_OVER_FULL in ms["reason_codes"]
    assert RC_BULLPEN_RISK_FAVORS_F5 in ms["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Run Line blocked when margin is low
# ─────────────────────────────────────────────────────────────────────
def test_run_line_blocked_when_low_projected_margin():
    pp = {
        "recommendation": {
            "market": "Run Line -1.5",
            "confidence_score": 65,
            "odds_range": "2.10",
        },
        "fragility": {"score": 40},
        "_mlb_script_v2": {
            "marginProjection": 1.2,
            "runLineCoverProb": 0.42,
            "favoriteSide": "home",
        },
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == MKT_MONEYLINE
    assert RC_RUN_LINE_NOT_SUPPORTED in ms["reason_codes"]
    assert RC_MONEYLINE_SAFER_THAN_RUN_LINE in ms["reason_codes"]
    assert any("Run Line" in w for w in ms["why_not_other_markets"])


def test_run_line_kept_when_margin_supports():
    pp = {
        "recommendation": {
            "market": "Run Line -1.5",
            "confidence_score": 70,
            "odds_range": "2.00",
        },
        "fragility": {"score": 30},
        "_mlb_script_v2": {
            "marginProjection": 2.8,
            "runLineCoverProb": 0.62,
            "favoriteSide": "home",
        },
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    # Margin supports → no override, RL stays
    assert ms["recommended_market"] == "Run Line -1.5"
    assert RC_RUN_LINE_NOT_SUPPORTED not in ms["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Over not forced by OPS alone (no odds)
# ─────────────────────────────────────────────────────────────────────
def test_over_without_odds_routed_to_manual_review():
    pp = {
        "recommendation": {
            "market": "Full Game Over 8.5",
            "confidence_score": 78,
            # No odds_range
        },
        "fragility": {"score": 35},
        "sabermetrics": {
            "available": True, "data_quality": "strong",
            "adjustments": {"total_runs_adjustment": 8},
            "match_edges": {"ops_edge": "home"},
        },
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == MKT_MANUAL_ODDS
    assert ms["requires_manual_odds"] is True
    assert RC_OVER_REQUIRES_ODDS_CONFIRMATION in ms["reason_codes"]
    assert RC_MANUAL_ODDS_REVIEW_REQUIRED in ms["reason_codes"]


def test_over_with_odds_and_alignment_kept():
    """Over with odds AND multi-layer confirmation should be kept (small boost)."""
    pp = {
        "recommendation": {
            "market": "Full Game Over 8.5",
            "confidence_score": 70,
            "odds_range": "1.95",
        },
        "fragility": {"score": 30},
        "sabermetrics": {
            "available": True, "data_quality": "strong",
            "adjustments": {"total_runs_adjustment": 8},
            "match_edges": {},
        },
        "pressure_base": {"combined": {"pressure_tier": "MODERATE_PRESSURE"}},
        "advanced_adjustments": {
            "data_quality": "strong",
            "reason_codes": ["STATCAST_OVER_SUPPORT"],
        },
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == "Full Game Over 8.5"
    # Confidence should be boosted slightly
    assert ms["market_confidence"] > 70


# ─────────────────────────────────────────────────────────────────────
# Ghost-edge blocks pick
# ─────────────────────────────────────────────────────────────────────
def test_ghost_edge_blocks_under_pick():
    pp = {
        "recommendation": {
            "market": "Full Game Under 8.5",
            "confidence_score": 75,
            "odds_range": "1.90",
        },
        "fragility": {"score": 35},
        "model_verification": {
            "discrepancies": [{"flag": "ERA_UNDERSTATES_RISK"},
                              {"flag": "PITCHER_XWOBA_WARNING"}],
        },
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert RC_GHOST_EDGE_BLOCKED_PICK in ms["reason_codes"]
    # Should be either F5 Under (if pitchers strong) or watchlist
    assert ms["recommended_market"] in (MKT_F5_UNDER, MKT_WATCHLIST)
    assert any("ghost-edge" in w.lower() for w in ms["why_not_other_markets"])


def test_ghost_edge_blocks_over_pick():
    pp = {
        "recommendation": {
            "market": "Full Game Over 8.5",
            "confidence_score": 75,
            "odds_range": "1.95",
        },
        "fragility": {"score": 30},
        "model_verification": {
            "discrepancies": [{"flag": "GHOST_EDGE_OVER_VS_L5_LOW_SCORING"}],
        },
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == MKT_WATCHLIST
    assert RC_GHOST_EDGE_BLOCKED_PICK in ms["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# High hit pressure → Under degraded / swapped
# ─────────────────────────────────────────────────────────────────────
def test_high_pressure_swaps_full_game_under_to_f5():
    pp = {
        "recommendation": {
            "market": "Full Game Under 8.5",
            "confidence_score": 74,
            "odds_range": "1.85",
        },
        "fragility": {"score": 28},
        "pressure_base": {"combined": {"pressure_tier": "HIGH_PRESSURE"}},
        "sabermetrics": {
            "available": True, "data_quality": "partial",
            "adjustments": {"pitcher_quality_adjustment": 6},
            "match_edges": {},
        },
        "script_survival": {"score": 72},
        "pitcher_quality_score": 75,
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == MKT_F5_UNDER
    assert RC_PRESSURE_BASE_CHANGED_MARKET in ms["reason_codes"]
    assert RC_F5_UNDER_PREFERRED_OVER_FULL in ms["reason_codes"]
    assert RC_FULL_GAME_UNDER_FRAGILE in ms["reason_codes"]


def test_high_pressure_with_weak_pitchers_goes_watchlist():
    pp = {
        "recommendation": {
            "market": "Full Game Under 8.5",
            "confidence_score": 60,
            "odds_range": "1.85",
        },
        "fragility": {"score": 50},
        "pressure_base": {"combined": {"pressure_tier": "HIGH_PRESSURE"}},
        "pitcher_quality_score": 50,
        "script_survival": {"score": 50},
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["watchlist"] is True
    assert ms["recommended_market"] == MKT_WATCHLIST


# ─────────────────────────────────────────────────────────────────────
# Missing odds (Under) → manual review
# ─────────────────────────────────────────────────────────────────────
def test_under_pick_without_odds_routed_to_manual_review():
    pp = {
        "recommendation": {
            "market": "Full Game Under 8.5",
            "confidence_score": 72,
        },
        "fragility": {"score": 35},
        "pressure_base": {"combined": {"pressure_tier": "LOW_PRESSURE"}},
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == MKT_MANUAL_ODDS
    assert ms["requires_manual_odds"] is True


# ─────────────────────────────────────────────────────────────────────
# Multi-layer alignment boosts confidence (Under)
# ─────────────────────────────────────────────────────────────────────
def test_under_with_full_alignment_gets_small_boost():
    pp = {
        "recommendation": {
            "market": "Full Game Under 8.0",
            "confidence_score": 70,
            "odds_range": "1.95",
        },
        "fragility": {"score": 30},
        "pressure_base": {"combined": {"pressure_tier": "LOW_PRESSURE"}},
        "sabermetrics": {
            "available": True, "data_quality": "strong",
            "adjustments": {"total_runs_adjustment": -5,
                             "pitcher_quality_adjustment": 7},
            "match_edges": {},
        },
        "advanced_adjustments": {
            "data_quality": "strong",
            "reason_codes": ["LOW_HARD_CONTACT_ENVIRONMENT"],
        },
        "script_survival": {"score": 78},
        "pitcher_quality_score": 78,
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == "Full Game Under 8.0"
    assert ms["market_confidence"] > 70  # boosted
    assert RC_SABERMETRICS_CONFIRMED_EDGE in ms["reason_codes"] \
        or RC_STATCAST_CONFIRMED_EDGE in ms["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Sport isolation
# ─────────────────────────────────────────────────────────────────────
def test_football_payload_safe_no_crash():
    pp = {"sport": "football", "recommendation": {"market": "BTTS Yes"}}
    out = select_protected_market(pp)
    # Module is sport-agnostic but the orchestrator gates by sport;
    # at the module level we just keep current market (no MLB-specific rules fire)
    assert "market_selection" in out


def test_basketball_payload_safe_no_crash():
    pp = {"sport": "basketball", "recommendation": {"market": "Over 220.5"}}
    out = select_protected_market(pp)
    assert "market_selection" in out


# ─────────────────────────────────────────────────────────────────────
# Default flow — current market kept
# ─────────────────────────────────────────────────────────────────────
def test_neutral_signals_keep_current_market():
    pp = {
        "recommendation": {
            "market": "Moneyline",
            "confidence_score": 68,
            "odds_range": "2.05",
        },
        "fragility": {"score": 35},
    }
    out = select_protected_market(pp)
    ms = out["market_selection"]
    assert ms["recommended_market"] == "Moneyline"
    assert ms["watchlist"] is False
    assert ms["requires_manual_odds"] is False
