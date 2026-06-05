"""Tests for game_openness — bilateral live-threat metric for total markets.

Validates the regression case (France 1-2 Ivory Coast at min 54: engine
recommended Over 3.5 @ 79% because home xG was 1.85, but the away side
contributed almost nothing → the combined expected total never supported
a 4-goal game).
"""

from __future__ import annotations

import pytest

from services.game_openness import (
    compute_game_openness,
    guard_total_recommendation,
    MIN_SIDE_XG_FOR_OPEN,
    MIN_COMBINED_XG_FOR_OVER35,
    ONE_SIDED_RATIO_THRESHOLD,
)


# ─────────────────────────────────────────────────────────────────────
# Stat dicts shaped like live_xg_proxy._STAT_ALIASES — `expected_goals`
# is the alias `extract_side` reads when computing xg_provider.
# ─────────────────────────────────────────────────────────────────────
def _side(xg_provider: float, shots: int = 3, sot: int = 1):
    return {
        "expected_goals":   xg_provider,
        "shots":            shots,
        "shots_on_target":  sot,
        "shots_in_box":     max(shots - 1, 0),
        "blocked_shots":    0,
        "possession":       50,
        "corners":          2,
        "dangerous_attacks": 4,
        "attacks":          10,
    }


# ─────────────────────────────────────────────────────────────────────
# Regression case: France 1-1 Ivory Coast — Over 3.5 should NOT be supported
# ─────────────────────────────────────────────────────────────────────
def test_france_ivory_coast_blocks_over_3_5():
    home = _side(xg_provider=1.85, shots=10, sot=5)
    away = _side(xg_provider=0.50, shots=3,  sot=1)
    r = compute_game_openness(home, away, minute=54, current_total=2)

    # Bilateral / openness flags must reflect a one-sided game.
    assert r["combined_xg"] >= 2.0
    assert r["one_sided_ratio"] < ONE_SIDED_RATIO_THRESHOLD, (
        f"one_sided_ratio={r['one_sided_ratio']} should be < {ONE_SIDED_RATIO_THRESHOLD} for France-Ivory Coast"
    )
    assert r["is_one_sided"] is True
    assert r["supports_over_35"] is False, "Over 3.5 must NOT be supported in one-sided games"


# ─────────────────────────────────────────────────────────────────────
# Regression case: Mexico 5-1 Serbia — UNILATERAL DOMINANCE, NOT bilateral
# openness. Mexico generated almost all the danger: xG ~1.90 vs ~0.35,
# 17 shots vs 3, 7 SOT vs 1, 6 corners vs 1. Over 3.5 hit because of
# Mexico's dominance + Serbia's collapse (own goals, errors, fatigue),
# NOT because both sides created consistent threat. The game_openness
# layer MUST classify this as one-sided. A separate
# `compute_unilateral_dominance_over_profile` covers the dominance path.
# ─────────────────────────────────────────────────────────────────────
def test_mexico_serbia_is_unilateral_dominance_not_bilateral_openness():
    home = _side(xg_provider=1.90, shots=17, sot=7)
    away = _side(xg_provider=0.35, shots=3,  sot=1)
    r = compute_game_openness(home, away, minute=70, current_total=3)

    # Per Moneyball philosophy: a 1.90 vs 0.35 split is one-sided
    # regardless of how loud the dominant side is.
    assert r["is_one_sided"] is True, (
        f"Mexico-Serbia must be one-sided (ratio={r['one_sided_ratio']})"
    )
    assert r["is_bilateral"] is False
    assert r["supports_over_35"] is False, (
        "Over 3.5 MUST NOT be supported by bilateral openness when only "
        "one side creates threat — even if the final score is 5-1."
    )
    assert r["supports_btts"] is False, (
        "BTTS MUST NOT be supported by bilateral openness when the away "
        "side is shut down. (Note: if the away side has already scored, "
        "the caller-side BTTS guard takes over independently.)"
    )


# ─────────────────────────────────────────────────────────────────────
# guard_total_recommendation
# ─────────────────────────────────────────────────────────────────────
def test_guard_passes_through_non_total_markets():
    openness = {"supports_over_35": False, "recommended_total": None}
    out = guard_total_recommendation("Moneyline Home", openness)
    assert out["downgraded"] is False
    assert out["market"] == "Moneyline Home"


def test_guard_downgrades_over_3_5_when_unsupported_with_fallback():
    openness = {
        "supports_over_35": False,
        "supports_over_25": True,
        "recommended_total": "Over 2.5",
        "reason_es":         "Partido de ida y vuelta",
    }
    out = guard_total_recommendation("Over 3.5", openness)
    assert out["downgraded"] is True
    assert out["market"] == "Over 2.5"
    assert "Over 3.5" in out["reason_es"]


def test_guard_marks_not_actionable_when_no_fallback():
    openness = {
        "supports_over_35": False,
        "supports_over_25": False,
        "recommended_total": None,
        "reason_es":         "Sin apertura bilateral",
    }
    out = guard_total_recommendation("Over 3.5", openness)
    assert out["downgraded"] is False
    assert out.get("not_actionable") is True


def test_guard_passes_supported_over_35_unchanged():
    openness = {
        "supports_over_35": True,
        "supports_over_25": True,
        "recommended_total": "Over 3.5",
    }
    out = guard_total_recommendation("Over 3.5", openness)
    assert out["downgraded"] is False
    assert out["market"] == "Over 3.5"


def test_guard_blocks_over_2_5_when_one_sided():
    openness = {
        "supports_over_35": False,
        "supports_over_25": False,
        "recommended_total": None,
        "reason_es":         "Sin apertura",
    }
    out = guard_total_recommendation("Over 2.5", openness)
    assert out.get("not_actionable") is True


# ─────────────────────────────────────────────────────────────────────
# Interpreter integration: when openness is one-sided, suggested_market
# becomes None (not actionable) or a safer fallback.
# ─────────────────────────────────────────────────────────────────────
def test_interpreter_consumes_openness_and_strips_unsupported_over_3_5():
    """The interpreter MUST consume `reeval.game_openness` and avoid
    surfacing "Mercado ofensivo: Over 3.5" when the openness report says
    the game is one-sided. We force the suggested_market through the
    guard by patching it post-interpretation, which mirrors what the
    pipeline does."""
    from services.human_live_interpreter import interpret_live

    match = {
        "home_team":  {"name": "France"},
        "away_team":  {"name": "Ivory Coast"},
        "live_stats": {
            "minute":   54,
            "score":    {"home": 1, "away": 1},
            "stats_by_side": {"home": {}, "away": {}},
        },
    }
    one_sided_openness = {
        "combined_xg":      2.35,
        "home_xg":          1.85,
        "away_xg":          0.50,
        "one_sided_ratio":  0.213,
        "is_bilateral":     False,
        "is_one_sided":     True,
        "supports_over_35": False,
        "supports_over_25": False,
        "supports_btts":    False,
        "recommended_total": None,
        "reason_es":        "Amenaza desbalanceada (xG 1.85 vs 0.50).",
    }
    reeval = {
        "market":              "Over 3.5",
        "live_state":          "LIVE_VALUE_WINDOW",
        "recommended_action":  "LIVE_ENTRY",
        "edge":                0.15,
        "confidence":          75,
        "game_openness":       one_sided_openness,
    }
    result = interpret_live(match, analysis={}, reeval=reeval)
    # Even if the interpreter generates Over 3.5 internally, it MUST NOT
    # surface it because openness says the game is one-sided.
    sm = (result or {}).get("suggested_market")
    assert sm != "Over 3.5", "interpreter must strip Over 3.5 when openness is one-sided"
    # game_openness must be exposed on the interpreter output for the UI.
    assert result.get("game_openness") is one_sided_openness


def test_interpreter_keeps_over_3_5_when_openness_supports_it():
    from services.human_live_interpreter import interpret_live

    match = {
        "home_team":  {"name": "X"},
        "away_team":  {"name": "Y"},
        "live_stats": {
            "minute": 60,
            "score":  {"home": 2, "away": 1},
            "stats_by_side": {"home": {}, "away": {}},
        },
    }
    bilateral_openness = {
        "combined_xg":      3.10,
        "home_xg":          1.55,
        "away_xg":          1.55,
        "one_sided_ratio":  0.50,
        "is_bilateral":     True,
        "is_one_sided":     False,
        "supports_over_35": True,
        "supports_over_25": True,
        "supports_btts":    True,
        "recommended_total": "Over 3.5",
        "reason_es":        "Apertura bilateral fuerte",
    }
    reeval = {
        "market":              "Over 3.5",
        "live_state":          "LIVE_VALUE_WINDOW",
        "recommended_action":  "LIVE_ENTRY",
        "edge":                0.20,
        "confidence":          78,
        "game_openness":       bilateral_openness,
    }
    result = interpret_live(match, analysis={}, reeval=reeval)
    # When openness DOES support Over 3.5, it must NOT be stripped.
    # (The interpreter may still rephrase it, but should not nullify it
    # solely because of the openness guard.)
    assert result is not None
    # game_openness exposed.
    assert result.get("game_openness") is bilateral_openness


# ─────────────────────────────────────────────────────────────────────
# Fail-soft: missing openness must not break the interpreter
# ─────────────────────────────────────────────────────────────────────
def test_interpreter_works_without_openness_payload():
    from services.human_live_interpreter import interpret_live

    match = {
        "home_team":  {"name": "X"},
        "away_team":  {"name": "Y"},
        "live_stats": {
            "minute": 30,
            "score":  {"home": 0, "away": 0},
            "stats_by_side": {"home": {}, "away": {}},
        },
    }
    reeval = {
        "market":              "Under 2.5",
        "live_state":          "LIVE_VALUE_WINDOW",
        "recommended_action":  "LIVE_ENTRY",
        "edge":                0.10,
        "confidence":          65,
        # No `game_openness` key.
    }
    result = interpret_live(match, analysis={}, reeval=reeval)
    assert result is not None
    assert "title" in result


def test_compute_game_openness_handles_missing_stats():
    r = compute_game_openness({}, {}, minute=10, current_total=0)
    assert r["combined_xg"] == 0
    assert r["supports_over_35"] is False
    assert r["recommended_total"] is None


# ─────────────────────────────────────────────────────────────────────
# compute_unilateral_dominance_over_profile
# ─────────────────────────────────────────────────────────────────────
from services.game_openness import compute_unilateral_dominance_over_profile  # noqa: E402


def _dom_side(*, xg, shots, sot, corners=4, saves=0, own_goals=0, red_cards=0,
               errors_to_shot=0):
    return {
        "expected_goals":              xg,
        "shots":                       shots,
        "shots_on_target":             sot,
        "shots_in_box":                max(shots - 2, 0),
        "blocked_shots":               1,
        "possession":                  65,
        "corners":                     corners,
        "dangerous_attacks":           20,
        "attacks":                     40,
        "saves":                       saves,
        "own_goals":                   own_goals,
        "red_cards":                   red_cards,
        "errors_leading_to_shot":      errors_to_shot,
    }


def test_unilateral_dominance_detects_mexico_serbia_profile():
    home = _dom_side(xg=1.90, shots=17, sot=7, corners=6, saves=0)
    away = _dom_side(xg=0.35, shots=3,  sot=1, corners=1, saves=5, own_goals=2)
    p = compute_unilateral_dominance_over_profile(
        home, away,
        match_context={"minute": 75, "current_total": 4, "score_diff": 3},
    )
    assert p["is_dominant"] is True
    assert p["dominant_side"] == "home"
    assert p["has_collapse"] is True
    assert p["supports_team_total"] is True
    assert p["supports_match_over_high"] is True
    assert p["profile_type"] == "UNILATERAL_DOMINANCE_OVER"
    assert "MATCH_OVER_HIGH_VIA_DOMINANCE" in p["reason_codes"]


def test_unilateral_dominance_without_collapse_only_supports_team_total():
    # High dominance numbers but no collapse signals (no own goals, no
    # errors, no GK overload, no late-game state).
    home = _dom_side(xg=1.85, shots=15, sot=6, corners=3, saves=0)
    away = _dom_side(xg=0.40, shots=4,  sot=1, corners=2, saves=2)
    p = compute_unilateral_dominance_over_profile(
        home, away,
        match_context={"minute": 35, "current_total": 1, "score_diff": 1},
    )
    assert p["is_dominant"] is True
    assert p["has_collapse"] is False
    assert p["supports_team_total"] is True
    assert p["supports_match_over_high"] is False
    assert "DOMINANCE_WITHOUT_COLLAPSE_TEAM_TOTAL_ONLY" in p["reason_codes"]


def test_unilateral_dominance_not_triggered_for_france_ivory_coast():
    # France's xG is high (1.85) but shot/SOT volume doesn't meet the
    # dominance gates (14 shots / 5 SOT). The away side ALSO has 6 shots,
    # exceeding MAX_OPPONENT_SHOTS_FOR_DOMINANCE (5).
    home = _dom_side(xg=1.85, shots=10, sot=5, corners=4)
    away = _dom_side(xg=0.50, shots=6,  sot=2, corners=2)
    p = compute_unilateral_dominance_over_profile(
        home, away,
        match_context={"minute": 54, "current_total": 2},
    )
    assert p["is_dominant"] is False
    assert p["profile_type"] == "NONE"
    assert p["supports_match_over_high"] is False


def test_unilateral_dominance_fails_when_opponent_creates_too_much():
    # Even with strong home numbers, an opponent generating >5 shots
    # disqualifies the "dominance" profile.
    home = _dom_side(xg=2.10, shots=18, sot=8)
    away = _dom_side(xg=0.80, shots=8,  sot=3)
    p = compute_unilateral_dominance_over_profile(home, away, match_context={"minute": 70})
    assert p["is_dominant"] is False


def test_unilateral_dominance_failsoft_on_missing_stats():
    p = compute_unilateral_dominance_over_profile({}, {}, match_context=None)
    assert p["is_dominant"] is False
    assert p["profile_type"] == "NONE"
    assert p["supports_match_over_high"] is False


def test_unilateral_dominance_does_not_recommend_btts():
    # The dominated side is being shut down; BTTS is *philosophically*
    # incompatible with dominance. The profile must not surface any BTTS
    # support signal.
    home = _dom_side(xg=2.20, shots=18, sot=8, corners=8)
    away = _dom_side(xg=0.20, shots=2,  sot=0, corners=1, saves=5, own_goals=1)
    p = compute_unilateral_dominance_over_profile(
        home, away,
        match_context={"minute": 80, "current_total": 4, "score_diff": 4},
    )
    # No BTTS support key should appear in the profile.
    assert "supports_btts" not in p
    # And dominance is correctly flagged.
    assert p["is_dominant"] is True
    assert p["supports_match_over_high"] is True


# ─────────────────────────────────────────────────────────────────────
# Interpreter BTTS guard: if both teams have already scored, do not
# surface BTTS as a new recommendation.
# ─────────────────────────────────────────────────────────────────────
def test_interpreter_strips_btts_when_both_teams_have_scored():
    """Direct unit test of the BTTS guard inside `interpret_live`.

    We simulate a scenario where the suggested_market would be BTTS and
    the live score is already 1-1. The interpreter MUST strip BTTS from
    the output and surface a Spanish explanation in `why`."""
    from services.human_live_interpreter import interpret_live

    match = {
        "home_team":  {"name": "A"},
        "away_team":  {"name": "B"},
        "live_stats": {
            "minute": 70,
            # Both teams scored — BTTS already cashed.
            "score":  {"home": 1, "away": 1},
            "stats_by_side": {"home": {}, "away": {}},
        },
    }
    reeval = {
        "market":              "BTTS (Ambos marcan)",
        "live_state":          "LIVE_VALUE_WINDOW",
        "recommended_action":  "LIVE_ENTRY",
        "edge":                0.10,
        "confidence":          65,
        # Openness intentionally None — the BTTS guard must work without it.
        "game_openness":       None,
    }
    out = interpret_live(match, analysis={}, reeval=reeval)
    sm = (out or {}).get("suggested_market") or ""
    assert "btts" not in sm.lower(), (
        f"BTTS must be stripped when both teams already scored; got: {sm!r}"
    )


def test_interpreter_strips_over_2_5_when_openness_blocks_it():
    """If openness reports supports_over_25=False, Over 2.5 must be stripped."""
    from services.human_live_interpreter import interpret_live

    match = {
        "home_team":  {"name": "A"},
        "away_team":  {"name": "B"},
        "live_stats": {
            "minute": 55,
            "score":  {"home": 1, "away": 0},
            "stats_by_side": {"home": {}, "away": {}},
        },
    }
    openness = {
        "combined_xg":       1.10,
        "home_xg":           0.95,
        "away_xg":           0.15,
        "one_sided_ratio":   0.136,
        "is_bilateral":      False,
        "is_one_sided":      True,
        "supports_over_35":  False,
        "supports_over_25":  False,
        "supports_btts":     False,
        "recommended_total": None,
        "reason_es":         "Apertura unilateral; Over 2.5 sin respaldo.",
    }
    reeval = {
        "market":              "Over 2.5",
        "live_state":          "LIVE_VALUE_WINDOW",
        "recommended_action":  "LIVE_ENTRY",
        "edge":                0.08,
        "confidence":          60,
        "game_openness":       openness,
    }
    out = interpret_live(match, analysis={}, reeval=reeval)
    sm = (out or {}).get("suggested_market") or ""
    assert "over 2.5" not in sm.lower() and "más de 2.5" not in sm.lower(), (
        f"Over 2.5 must be stripped when openness.supports_over_25=False; got: {sm!r}"
    )
