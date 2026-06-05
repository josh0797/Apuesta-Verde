"""Tests for Phase 33 — Over Support layer integration into
`football_market_selection.select_football_market`.

Covers:
  * Over 1.5 protected-conditional selection.
  * Over 2.5 strict gates (support, fragility, lambda_total, early goal).
  * Over 2.5 downgrade to Over 1.5 when fragility blocks it.
  * DC/NB Under conflict blocks Over 2.5.
  * Controlled-match block.
  * Top-scorer-out (offensive injury) block / downgrade.
  * Defensive-injury Over 1.5 boost.
  * Line-already-hit (score 1-1 blocks new Over 1.5 entry).
  * Missing odds → manual review.
  * Missing football_over_support → fail-soft (no crash, no Over).
  * `is_total_line_already_hit` thresholds (pure).
"""

from __future__ import annotations

import pytest

from services.football_moneyball.football_market_selection import (
    select_football_market,
    is_total_line_already_hit,
    MKT_OVER_15,
    MKT_OVER_25,
    MKT_WATCHLIST,
    RC_OVER_SUPPORT_CONFIRMED,
    RC_OVER_1_5_PROTECTED_SELECTED,
    RC_OVER_2_5_ALLOWED_LOW_FRAGILITY,
    RC_OVER_2_5_DOWNGRADED_TO_OVER_1_5,
    RC_OVER_2_5_FRAGILE,
    RC_DC_NB_UNDER_CONFLICT,
    RC_OVER_LINE_ALREADY_HIT,
    RC_OVER_BLOCKED_BY_CONTROLLED,
    RC_OVER_BLOCKED_BY_INJURY,
    RC_OVER_DEFENSE_INJURY_BOOST,
    RC_MANUAL_ODDS_REVIEW_REQUIRED,
)


# ─────────────────────────────────────────────────────────────────────
# Factory helpers
# ─────────────────────────────────────────────────────────────────────
def _build_pick(
    *,
    market="Over 1.5",
    over_support=None,
    totals_model=None,
    odds=True,
    score=None,
    confidence=60,
    fragility=40,
):
    pick = {
        "recommendation": {
            "market":           market,
            "confidence_score": confidence,
            "fragility":        fragility,
        },
        "football_over_support":  over_support or {},
        "football_totals_model":  totals_model or {},
    }
    if odds:
        pick["odds_snapshots"] = [{"book": "pinnacle", "odds": 1.45}]
    if score is not None:
        pick["live_stats"] = {"score": score}
    return pick


def _over_support_payload(
    *,
    sup_15=75,
    sup_25=50,
    fragility=40,
    recommended="OVER_1_5",
    reason_codes=None,
    lambda_total=2.5,
    early_goal=55,
):
    return {
        "available":                  True,
        "over_1_5_support_score":     sup_15,
        "over_2_5_support_score":     sup_25,
        "fragility_score":            fragility,
        "recommended_over_market":    recommended,
        "reason_codes":               list(reason_codes or []),
        "lambda_total":               lambda_total,
        "early_goal_pressure_score":  early_goal,
    }


def _totals_model(under_3_5_dcnb=0.50):
    return {
        "available":   True,
        "under_2_5":   {"poisson": 0.40, "dc_nb": 0.42, "delta_pts": 2.0},
        "under_3_5":   {"poisson": 0.65, "dc_nb": under_3_5_dcnb, "delta_pts": 5.0},
    }


def _ms(out):
    return out["market_selection"]


# ─────────────────────────────────────────────────────────────────────
# Helper: is_total_line_already_hit
# ─────────────────────────────────────────────────────────────────────
def test_is_total_line_already_hit_basic_thresholds():
    src = {"live_stats": {"score": {"home": 1, "away": 1}}}
    assert is_total_line_already_hit(src, "Over 0.5") is True
    assert is_total_line_already_hit(src, "Over 1.5") is True
    assert is_total_line_already_hit(src, "Over 2.5") is False
    assert is_total_line_already_hit(src, "Over 3.5") is False


def test_is_total_line_already_hit_score_label_only():
    src = {"score": {"label": "2-2"}}
    assert is_total_line_already_hit(src, "Over 3.5") is True
    assert is_total_line_already_hit(src, "Over 4.5") is False


def test_is_total_line_already_hit_no_score():
    assert is_total_line_already_hit({}, "Over 1.5") is False
    assert is_total_line_already_hit(None, "Over 1.5") is False
    assert is_total_line_already_hit({"live_stats": {}}, "Over 2.5") is False


def test_is_total_line_already_hit_non_over_market_returns_false():
    src = {"live_stats": {"score": {"home": 1, "away": 1}}}
    assert is_total_line_already_hit(src, "Under 2.5") is False
    assert is_total_line_already_hit(src, "Moneyline Home") is False
    assert is_total_line_already_hit(src, None) is False


# ─────────────────────────────────────────────────────────────────────
# Over 1.5 protected-conditional selection
# ─────────────────────────────────────────────────────────────────────
def test_over_1_5_selected_when_support_70_and_fragility_60():
    pick = _build_pick(
        market="Over 1.5",
        over_support=_over_support_payload(sup_15=72, fragility=55, recommended="OVER_1_5"),
    )
    out = _ms(select_football_market(pick))
    assert out["recommended_market"] == MKT_OVER_15
    assert RC_OVER_SUPPORT_CONFIRMED in out["reason_codes"]
    assert RC_OVER_1_5_PROTECTED_SELECTED in out["reason_codes"]
    assert out["watchlist"] is False


def test_over_1_5_requires_manual_review_when_no_odds():
    pick = _build_pick(
        market="Over 1.5",
        over_support=_over_support_payload(sup_15=80, fragility=50, recommended="OVER_1_5"),
        odds=False,
    )
    out = _ms(select_football_market(pick))
    assert out["recommended_market"] == MKT_OVER_15
    assert out["requires_manual_odds"] is True
    assert out["watchlist"] is True


# ─────────────────────────────────────────────────────────────────────
# Over 2.5 strict gates
# ─────────────────────────────────────────────────────────────────────
def test_over_2_5_allowed_when_all_gates_satisfied():
    pick = _build_pick(
        market="Over 2.5",
        over_support=_over_support_payload(
            sup_15=82, sup_25=85, fragility=40,
            recommended="OVER_2_5", lambda_total=3.0, early_goal=70,
        ),
    )
    out = _ms(select_football_market(pick))
    assert out["recommended_market"] == MKT_OVER_25
    assert RC_OVER_2_5_ALLOWED_LOW_FRAGILITY in out["reason_codes"]


def test_over_2_5_downgraded_when_fragility_high():
    pick = _build_pick(
        market="Over 2.5",
        over_support=_over_support_payload(
            sup_15=80, sup_25=85, fragility=70,
            recommended="OVER_2_5", lambda_total=3.0, early_goal=70,
        ),
    )
    out = _ms(select_football_market(pick))
    # Over 2.5 must NOT be the recommended market; downgrade trace must be present.
    assert RC_OVER_2_5_FRAGILE in out["reason_codes"]
    assert RC_OVER_2_5_DOWNGRADED_TO_OVER_1_5 in out["reason_codes"]
    assert out["recommended_market"] != MKT_OVER_25


# ─────────────────────────────────────────────────────────────────────
# DC/NB conflict
# ─────────────────────────────────────────────────────────────────────
def test_dcnb_under_conflict_blocks_over_2_5():
    pick = _build_pick(
        market="Over 2.5",
        over_support=_over_support_payload(
            sup_15=80, sup_25=85, fragility=40,
            recommended="OVER_2_5", lambda_total=3.0, early_goal=70,
        ),
        totals_model=_totals_model(under_3_5_dcnb=0.75),
    )
    out = _ms(select_football_market(pick))
    assert RC_DC_NB_UNDER_CONFLICT in out["reason_codes"]
    assert out["recommended_market"] != MKT_OVER_25
    # With stricter thresholds (sup>=75, frag<=55) Over 1.5 can still pass.
    assert out["recommended_market"] == MKT_OVER_15


# ─────────────────────────────────────────────────────────────────────
# Controlled match blocks Over entirely
# ─────────────────────────────────────────────────────────────────────
def test_controlled_match_blocks_over():
    pick = _build_pick(
        market="Over 1.5",
        over_support=_over_support_payload(
            sup_15=85, sup_25=85, fragility=30,
            recommended="OVER_2_5", reason_codes=["CONTROLLED_MATCH_BLOCKS_OVER"],
        ),
    )
    out = _ms(select_football_market(pick))
    assert RC_OVER_BLOCKED_BY_CONTROLLED in out["reason_codes"]
    assert out["recommended_market"] not in (MKT_OVER_15, MKT_OVER_25)


# ─────────────────────────────────────────────────────────────────────
# Injuries
# ─────────────────────────────────────────────────────────────────────
def test_top_scorer_out_blocks_over_2_5():
    pick = _build_pick(
        market="Over 2.5",
        over_support=_over_support_payload(
            sup_15=82, sup_25=85, fragility=40,
            recommended="OVER_2_5", lambda_total=3.0, early_goal=70,
            reason_codes=["TOP_SCORER_OUT_WEAKENS_OVER"],
        ),
    )
    out = _ms(select_football_market(pick))
    assert RC_OVER_BLOCKED_BY_INJURY in out["reason_codes"]
    assert out["recommended_market"] != MKT_OVER_25


def test_defensive_injury_boosts_over_1_5():
    pick = _build_pick(
        market="Over 1.5",
        over_support=_over_support_payload(
            sup_15=72, sup_25=55, fragility=50,
            recommended="OVER_1_5",
            reason_codes=["INJURY_DEFENSE_WEAKENED_OVER_SUPPORT"],
        ),
    )
    out = _ms(select_football_market(pick))
    assert RC_OVER_DEFENSE_INJURY_BOOST in out["reason_codes"]
    assert out["recommended_market"] == MKT_OVER_15


# ─────────────────────────────────────────────────────────────────────
# Line already hit
# ─────────────────────────────────────────────────────────────────────
def test_over_1_5_blocked_when_line_already_hit():
    # Score 1-1 ⇒ total 2 ⇒ Over 1.5 already cashed.
    pick = _build_pick(
        market="Over 1.5",
        over_support=_over_support_payload(sup_15=85, fragility=40, recommended="OVER_1_5"),
        score={"home": 1, "away": 1, "label": "1-1"},
    )
    out = _ms(select_football_market(pick))
    assert RC_OVER_LINE_ALREADY_HIT in out["reason_codes"]
    # Either degrades to watchlist or strips Over 1.5 as recommended market.
    assert out["recommended_market"] != MKT_OVER_15 or out["watchlist"] is True


# ─────────────────────────────────────────────────────────────────────
# Missing payloads → fail-soft
# ─────────────────────────────────────────────────────────────────────
def test_missing_over_support_does_not_crash_or_promote_over():
    pick = _build_pick(
        market="Under 2.5",
        over_support={},
        totals_model={},
    )
    out = _ms(select_football_market(pick))
    # Under 2.5 path still triggers protected_alt logic; no Over reasons.
    assert RC_OVER_SUPPORT_CONFIRMED not in out["reason_codes"]
    assert RC_OVER_1_5_PROTECTED_SELECTED not in out["reason_codes"]


def test_missing_odds_triggers_manual_review_when_over_supported():
    pick = _build_pick(
        market="Over 1.5",
        over_support=_over_support_payload(sup_15=75, fragility=45, recommended="OVER_1_5"),
        odds=False,
    )
    out = _ms(select_football_market(pick))
    assert RC_MANUAL_ODDS_REVIEW_REQUIRED in out["reason_codes"]
    assert out["requires_manual_odds"] is True


# ─────────────────────────────────────────────────────────────────────
# Non-football sports unaffected — pass payload with no football blocks
# ─────────────────────────────────────────────────────────────────────
def test_non_football_payload_does_not_promote_over():
    # A pick from another sport (no football_over_support attached) must
    # not trigger any Over support reason code or change the market.
    pick = {
        "recommendation": {
            "market":           "Moneyline Home",
            "confidence_score": 65,
            "fragility":        40,
        },
        "odds_snapshots": [{"book": "x", "odds": 1.8}],
    }
    out = _ms(select_football_market(pick))
    assert out["recommended_market"] == "Moneyline Home"
    assert RC_OVER_SUPPORT_CONFIRMED not in out["reason_codes"]
    assert RC_OVER_1_5_PROTECTED_SELECTED not in out["reason_codes"]
    assert RC_OVER_2_5_ALLOWED_LOW_FRAGILITY not in out["reason_codes"]
