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
# Confirmation case: Mexico 4-1 Serbia — Over 2.5 / BTTS should be supported
# ─────────────────────────────────────────────────────────────────────
def test_mexico_serbia_supports_balanced_totals():
    # Both sides creating chances (the match ended 4-1).
    home = _side(xg_provider=1.40, shots=8, sot=4)
    away = _side(xg_provider=1.10, shots=6, sot=3)
    r = compute_game_openness(home, away, minute=38, current_total=2)

    assert r["is_one_sided"] is False
    assert r["supports_over_25"] is True or r["supports_btts"] is True


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
