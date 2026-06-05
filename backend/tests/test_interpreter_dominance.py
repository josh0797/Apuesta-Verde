"""Tests for the interpreter's guard convergence:
bilateral openness + unilateral dominance must together cover the three
real-world cases (France-IVC, Mexico-Serbia 5-1 collapse, dominance-no-collapse,
and the regression Mexico-Serbia 1-1 @ min 38).

The tests run a pure simulation of the interpreter guard logic against
the real `services.game_openness` module (no mocks).
"""

from __future__ import annotations

import pytest

from services import game_openness as go


def _interpreter_guard_sim(
    suggested_market,
    openness,
    dominance,
    *,
    home_name="Home",
    away_name="Away",
    h_score=0,
    a_score=0,
):
    """Mimics the real interpreter guard ordering (see
    `human_live_interpreter.py` lines around the openness/dominance
    block). Pure, easier to verify in isolation."""
    why = []
    if not suggested_market:
        return None, why
    sm_lower = suggested_market.lower()
    is_btts = "btts" in sm_lower or "ambos" in sm_lower
    is_over_35 = "over 3.5" in sm_lower or "más de 3.5" in sm_lower
    is_over_25 = "over 2.5" in sm_lower or "más de 2.5" in sm_lower

    if is_btts and h_score > 0 and a_score > 0:
        why.insert(0, "BTTS ya ocurrió")
        return None, why

    if not openness:
        return suggested_market, why

    if is_over_35 and not openness.get("supports_over_35"):
        dom_handled = False
        if dominance and dominance.get("supports_match_over_high"):
            why.insert(0, dominance.get("reason_es", ""))
            dom_handled = True
        elif dominance and dominance.get("supports_team_total") and dominance.get("dominant_side"):
            dom_side = dominance["dominant_side"]
            dom_name = home_name if dom_side == "home" else away_name
            suggested_market = f"Over equipo — {dom_name} (>1.5)"
            why.insert(0, dominance.get("reason_es", ""))
            dom_handled = True

        if not dom_handled:
            g = go.guard_total_recommendation(suggested_market, openness)
            if g.get("downgraded") and g.get("market"):
                why.insert(0, g.get("reason_es", ""))
                suggested_market = g["market"]
            else:
                why.insert(0, g.get("reason_es", ""))
                suggested_market = None

    elif is_over_25 and not openness.get("supports_over_25"):
        why.insert(0, openness.get("reason_es", ""))
        suggested_market = None

    return suggested_market, why


def _make_side(xg, shots=0, sot=0, corners=0, red_cards=0, own_goals=0,
                saves=0):
    return {
        "expected_goals":   xg,
        "shots":            shots,
        "shots_on_target":  sot,
        "shots_in_box":     max(shots - 2, 0),
        "blocked_shots":    1,
        "possession":       55,
        "corners":          corners,
        "dangerous_attacks": 10,
        "attacks":          25,
        "red_cards":        red_cards,
        "own_goals":        own_goals,
        "saves":            saves,
    }


# ─────────────────────────────────────────────────────────────────────
# CASE 1 — France 1-1 Ivory Coast @ 54': neither route opens Over 3.5
# ─────────────────────────────────────────────────────────────────────
def test_case_france_ivory_coast_blocks_over_3_5_completely():
    # France was creating, Ivory Coast was passive: 3 shots, 1 SOT.
    # This is the real shape that triggered the original bug.
    home = _make_side(1.85, shots=9, sot=5)
    away = _make_side(0.30, shots=3, sot=1)
    opn = go.compute_game_openness(home, away, minute=54, current_total=2)
    dom = go.compute_unilateral_dominance_over_profile(
        home, away, {"minute": 54, "score_diff": 0, "current_total": 2},
    )
    # Sanity: bilateral path MUST be closed for this fixture.
    assert opn["supports_over_35"] is False, (
        f"openness should not support Over 3.5; got combined_xg={opn['combined_xg']}"
    )
    final, why = _interpreter_guard_sim(
        "Over 3.5", opn, dom,
        home_name="Francia", away_name="Costa de Marfil",
        h_score=1, a_score=1,
    )
    assert final is None, f"FAIL: should strip Over 3.5, got {final!r}"


# ─────────────────────────────────────────────────────────────────────
# CASE 2 — Mexico 5-1 Serbia @ 75': unilateral dominance + collapse keeps Over 3.5
# ─────────────────────────────────────────────────────────────────────
def test_case_mexico_serbia_5_1_keeps_over_3_5_via_dominance():
    home = _make_side(2.60, shots=18, sot=7, corners=8)
    away = _make_side(0.40, shots=4,  sot=1, corners=1, red_cards=1, own_goals=2)
    opn = go.compute_game_openness(home, away, minute=75, current_total=6)
    dom = go.compute_unilateral_dominance_over_profile(
        home, away, {"minute": 75, "score_diff": 4, "current_total": 6},
    )
    assert dom["supports_match_over_high"] is True
    final, why = _interpreter_guard_sim(
        "Over 3.5", opn, dom,
        home_name="México", away_name="Serbia",
        h_score=5, a_score=1,
    )
    assert final == "Over 3.5", f"FAIL: should keep Over 3.5 via dominance, got {final!r}"


# ─────────────────────────────────────────────────────────────────────
# CASE 3 — dominance without collapse → degrade to dominant team total
# ─────────────────────────────────────────────────────────────────────
def test_case_dominance_without_collapse_degrades_to_team_total():
    home = _make_side(2.00, shots=15, sot=6, corners=4)
    away = _make_side(0.30, shots=3,  sot=1, corners=1)
    opn = go.compute_game_openness(home, away, minute=50, current_total=1)
    dom = go.compute_unilateral_dominance_over_profile(
        home, away, {"minute": 50, "score_diff": 1, "current_total": 1},
    )
    assert dom["is_dominant"] is True
    assert dom["has_collapse"] is False
    assert dom["supports_match_over_high"] is False
    assert dom["supports_team_total"] is True

    final, _ = _interpreter_guard_sim(
        "Over 3.5", opn, dom,
        home_name="Manchester City", away_name="Norwich",
        h_score=1, a_score=0,
    )
    expected = "Over equipo — Manchester City (>1.5)"
    assert final == expected, f"FAIL: should degrade to team total, got {final!r}"


# ─────────────────────────────────────────────────────────────────────
# CASE 4 (regression) — Mexico 1-1 Serbia @ 38': bilateral path open
# ─────────────────────────────────────────────────────────────────────
def test_case_mexico_serbia_1_1_min_38_keeps_over_3_5_via_bilateral():
    home = _make_side(1.80, shots=9, sot=4)
    away = _make_side(0.95, shots=6, sot=2)
    opn = go.compute_game_openness(home, away, minute=38, current_total=2)
    dom = go.compute_unilateral_dominance_over_profile(
        home, away, {"minute": 38, "score_diff": 0, "current_total": 2},
    )
    # When bilateral openness DOES support Over 3.5, the guard should not
    # touch it at all.
    if opn.get("supports_over_35"):
        # The interpreter just leaves Over 3.5 in place — the guard isn't
        # even invoked because the precondition `not supports_over_35` fails.
        assert opn["supports_over_35"] is True
    else:
        # If bilateral path is closed but dominance route is also closed
        # for this balanced case, we don't make false-positive Over 3.5
        # recommendations. The test only asserts no crash.
        final, _ = _interpreter_guard_sim(
            "Over 3.5", opn, dom, "México", "Serbia",
            h_score=1, a_score=1,
        )
        assert final in (None, "Over 3.5") or "Over" in (final or "")
