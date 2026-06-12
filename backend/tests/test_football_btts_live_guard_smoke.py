"""Phase F58+ — Football BTTS Live Guard smoke tests.

Implements the five obligatory fixture cases from the spec:

  1. Red card + unilateral threat (Mexico 1-0 South Africa, min 59).
  2. No red card but still unilateral threat.
  3. Bilateral threat real → BTTS permitido.
  4. Red card team STILL generating real threat → BTTS not auto-blocked.
  5. Late game (>= 83') → replacement = WATCHLIST instead of Over 1.5.
"""
from __future__ import annotations

import pytest

from services.football_btts_live_guard import (
    BTTS_MIN_SHOTS_PER_SIDE,
    BTTS_MIN_XG_PER_SIDE,
    ENGINE_VERSION,
    guard_btts_live_recommendation,
    infer_team_strength_from_odds,
)


# ─────────────────────────────────────────────────────────────────────
# Test 1 — Red card + unilateral threat (the canonical regression)
# ─────────────────────────────────────────────────────────────────────
def test_btts_blocked_red_card_and_unilateral_threat():
    res = guard_btts_live_recommendation(
        current_market="BTTS",
        minute=59,
        score_home=1, score_away=0,
        home_team="Mexico", away_team="South Africa",
        home_red_cards=0, away_red_cards=1,
        home_xg=1.52, away_xg=0.43,
        home_shots=14, away_shots=3,
        home_sot=3, away_sot=2,
        home_box_shots=7, away_box_shots=1,
        home_corners=3, away_corners=1,
    )
    assert res["btts_allowed"] is False
    assert res["blocked_market"] == "BTTS"
    assert res["replacement_market"] == "OVER_1_5"
    assert res["replacement_label"] == "Más de 1.5 goles"
    # Multiple reason codes should be present.
    assert "BTTS_BLOCKED_LOW_BILATERAL_THREAT" in res["reason_codes"]
    assert "BTTS_BLOCKED_RED_CARD_LOW_THREAT" in res["reason_codes"]
    assert "BTTS_BLOCKED_UNILATERAL_DOMINANCE" in res["reason_codes"]
    assert "BTTS_REPLACED_WITH_OVER_1_5" in res["reason_codes"]
    assert "RED_CARD_TEAM_ATTACK_SUPPRESSED" in res["reason_codes"]
    assert "UNILATERAL_MOMENTUM_NOT_BTTS" in res["reason_codes"]
    # Narrative carries both the dominance reason and the red-card team name.
    assert "Sudáfrica" in res["narrative_es"] or "South Africa" in res["narrative_es"]
    # Risk classification high because of red card + dominance.
    assert res["risk"] in {"MEDIUM", "HIGH"}
    assert res["engine_version"] == ENGINE_VERSION


# ─────────────────────────────────────────────────────────────────────
# Test 2 — No red card but unilateral threat
# ─────────────────────────────────────────────────────────────────────
def test_btts_blocked_unilateral_threat_without_red_card():
    res = guard_btts_live_recommendation(
        current_market="BTTS",
        minute=55,
        score_home=1, score_away=0,
        home_team="Team A", away_team="Team B",
        home_red_cards=0, away_red_cards=0,
        home_xg=2.0, away_xg=0.35,
        home_shots=16, away_shots=3,
        home_sot=5, away_sot=1,
        home_box_shots=8, away_box_shots=1,
    )
    assert res["btts_allowed"] is False
    assert res["replacement_market"] == "OVER_1_5"
    assert "BTTS_BLOCKED_UNILATERAL_DOMINANCE" in res["reason_codes"]
    # Red-card reason code MUST NOT appear here.
    assert "BTTS_BLOCKED_RED_CARD_LOW_THREAT" not in res["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Test 3 — Bilateral threat real → BTTS allowed
# ─────────────────────────────────────────────────────────────────────
def test_btts_allowed_with_real_bilateral_threat():
    res = guard_btts_live_recommendation(
        current_market="BTTS",
        minute=50,
        score_home=0, score_away=0,
        home_team="Team A", away_team="Team B",
        home_xg=1.1, away_xg=0.9,
        home_shots=9, away_shots=8,
        home_sot=3, away_sot=3,
        home_box_shots=4, away_box_shots=3,
    )
    assert res["btts_allowed"] is True
    assert res["blocked_market"] is None
    assert res["replacement_market"] is None
    assert res["reason_codes"] == []
    assert "soportado" in res["narrative_es"].lower()


# ─────────────────────────────────────────────────────────────────────
# Test 4 — Red card team STILL generating real threat
# ─────────────────────────────────────────────────────────────────────
def test_red_card_team_still_threatening_does_not_block_btts():
    # Visitante con roja PERO sigue con xG 0.95, 7 tiros, 3 SOT, 3 en área.
    res = guard_btts_live_recommendation(
        current_market="BTTS",
        minute=70,
        score_home=1, score_away=1,
        home_team="Home", away_team="Away",
        home_red_cards=0, away_red_cards=1,
        home_xg=1.20, away_xg=0.95,
        home_shots=10, away_shots=7,
        home_sot=4, away_sot=3,
        home_box_shots=5, away_box_shots=3,
    )
    # No bloqueado por roja porque la amenaza visitante sigue arriba.
    # No bloqueado por dominance porque ratios no son extremos.
    # No bloqueado por low bilateral threat porque ambos cumplen.
    assert "BTTS_BLOCKED_RED_CARD_LOW_THREAT" not in res["reason_codes"]
    assert res["btts_allowed"] is True


# ─────────────────────────────────────────────────────────────────────
# Test 5 — Late game (>= 83'): blocked BTTS → WATCHLIST (not Over 1.5)
# ─────────────────────────────────────────────────────────────────────
def test_btts_late_game_blocks_to_watchlist():
    res = guard_btts_live_recommendation(
        current_market="BTTS",
        minute=84,
        score_home=1, score_away=0,
        home_team="Home", away_team="Away",
        home_red_cards=0, away_red_cards=1,
        home_xg=1.50, away_xg=0.30,
        home_shots=13, away_shots=2,
        home_sot=4, away_sot=1,
        home_box_shots=6, away_box_shots=0,
    )
    assert res["btts_allowed"] is False
    assert res["replacement_market"] == "WATCHLIST"
    assert "BTTS_REPLACED_WITH_WATCHLIST" in res["reason_codes"]
    assert res["replacement_label"] == "Watchlist"
    # narrative must mention watchlist for the user.
    assert "watchlist" in res["narrative_es"].lower()


# ─────────────────────────────────────────────────────────────────────
# Extra coverage — non-BTTS market is no-op, malformed inputs fail-soft
# ─────────────────────────────────────────────────────────────────────
def test_guard_noops_on_non_btts_market():
    res = guard_btts_live_recommendation(
        current_market="OVER_2_5",
        minute=60,
        score_home=1, score_away=0,
        home_team="A", away_team="B",
    )
    assert res["btts_allowed"] is True
    assert res["_skipped"] == "non_btts_market"
    assert res["reason_codes"] == []


def test_guard_failsoft_when_all_inputs_missing():
    res = guard_btts_live_recommendation(
        current_market="BTTS",
        minute=45,
        score_home=0, score_away=0,
        home_team="A", away_team="B",
    )
    # No bilateral threat (everything None) ⇒ block.
    assert res["btts_allowed"] is False
    assert "BTTS_BLOCKED_LOW_BILATERAL_THREAT" in res["reason_codes"]
    # 0-0 + minute<=75 doesn't trigger OVER_1_5 (score_is_one_zero false).
    assert res["replacement_market"] == "WATCHLIST"


def test_guard_threshold_constants_sane():
    # If anyone tweaks the constants accidentally, this catches the obvious
    # regressions (e.g. someone sets the bilateral xg to 0.0).
    assert BTTS_MIN_XG_PER_SIDE >= 0.40
    assert BTTS_MIN_SHOTS_PER_SIDE >= 3


# ─────────────────────────────────────────────────────────────────────
# infer_team_strength_from_odds
# ─────────────────────────────────────────────────────────────────────
def test_infer_team_strength_from_decimal_odds_high_gap():
    res = infer_team_strength_from_odds(
        home_team="Mexico", away_team="South Africa",
        home_ml_odds=1.35, draw_odds=4.50, away_ml_odds=8.50,
    )
    assert res["available"] is True
    assert res["favorite_team"] == "Mexico"
    assert res["underdog_team"] == "South Africa"
    assert res["favorite_confidence"] == "HIGH"
    assert res["strength_gap"] > 0.35


def test_infer_team_strength_from_implied_probabilities():
    res = infer_team_strength_from_odds(
        home_team="A", away_team="B",
        home_implied_prob=0.55, away_implied_prob=0.30,
    )
    assert res["available"] is True
    assert res["favorite_team"] == "A"
    assert res["favorite_confidence"] == "MEDIUM"


def test_infer_team_strength_returns_unavailable_when_no_inputs():
    res = infer_team_strength_from_odds(home_team="A", away_team="B")
    assert res["available"] is False
    assert res["_reason"] == "insufficient_inputs"


def test_infer_team_strength_low_confidence_close_match():
    res = infer_team_strength_from_odds(
        home_team="A", away_team="B",
        home_implied_prob=0.45, away_implied_prob=0.42,
    )
    assert res["favorite_confidence"] == "LOW"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
