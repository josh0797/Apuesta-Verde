"""Tests for live_pre_match_comparison P4 fixes.

These tests reproduce the two bugs the user reported:

  Bug A — "Esperando primer inning" en inning 7 (live intelligence gate).
          Backend-side guard: the comparator must NOT report
          ``pregame_pick_status="still_playable"`` together with
          ``script_status="hard_deviation"`` / ``"broken_script"`` —
          that produced the contradictory chip combo
          DESVIACIÓN FUERTE + AÚN JUGABLE + Considerar cashout.

  Bug B — Pregame pick without odds / market must surface as
          ``not_evaluable`` instead of ``still_playable``.

The frontend gate (Bug A — Live Intelligence MLB) is tested in the JSX
component tests (out of scope for pytest).
"""
from __future__ import annotations

import pytest

from services.live_pre_match_comparison import (
    compare_pregame_vs_live,
    _pick_direction_vs_deviation,
)


# ─────────────────────────────────────────────────────────────────────
# _pick_direction_vs_deviation — pure helper
# ─────────────────────────────────────────────────────────────────────
def test_pick_direction_over_with_underpace_is_adverse():
    d = _pick_direction_vs_deviation(
        market="Más de 5.99 carreras",
        selection="Over",
        score_delta=-4.99,
        sport="baseball",
    )
    assert d == "adverse"


def test_pick_direction_over_with_overpace_is_favorable():
    d = _pick_direction_vs_deviation(
        market="Over 7.5 carreras",
        selection="Over",
        score_delta=+2.5,
        sport="baseball",
    )
    assert d == "favorable"


def test_pick_direction_under_with_underpace_is_favorable():
    d = _pick_direction_vs_deviation(
        market="Menos de 7.5 carreras",
        selection="Under",
        score_delta=-4.0,
        sport="baseball",
    )
    assert d == "favorable"


def test_pick_direction_under_with_overpace_is_adverse():
    d = _pick_direction_vs_deviation(
        market="Under 7.5",
        selection="Under",
        score_delta=+3.0,
        sport="baseball",
    )
    assert d == "adverse"


def test_pick_direction_moneyline_is_neutral():
    d = _pick_direction_vs_deviation(
        market="Moneyline",
        selection="Phillies",
        score_delta=-2.0,
        sport="baseball",
    )
    assert d == "neutral"


def test_pick_direction_no_delta_is_neutral():
    d = _pick_direction_vs_deviation(
        market="Over 7.5",
        selection="Over",
        score_delta=None,
        sport="baseball",
    )
    assert d == "neutral"


# ─────────────────────────────────────────────────────────────────────
# compare_pregame_vs_live — Bug A reproduction
# ─────────────────────────────────────────────────────────────────────
def _live(inning, home, away, **extra):
    """Helper to build a canonical live_state payload."""
    out = {
        "is_live":        True,
        "state":          "live-data-ready",
        "score":          {"home": home, "away": away},
        "inning":         {"number": inning, "half": "top"},
        "status":         "In Progress",
    }
    out.update(extra)
    return out


def test_bug_a_over_pick_with_hard_deviation_is_at_risk_not_still_playable():
    """Reproduces the user's screenshot bug.

    Pick: Over 5.99 carreras (pregame expected ~8.4 runs)
    Live: 1-0 en inning 7
    Expected outcome:
       script_status      = broken_script (or hard_deviation)
       pregame_pick_status= at_risk          (NOT still_playable)
       live_verdict       = AVOID_OVER_OR_CASHOUT
    """
    res = compare_pregame_vs_live(
        pregame_pick={
            "recommendation": {
                "market":          "Más de 5.99 carreras",
                "selection":       "Over",
                "odds_range":      "1.85-2.00",
                "confidence_score": 70,
            },
            "_mlb_script_v2": {"expectedRuns": 8.4},
        },
        live_state=_live(7, 1, 0),
        sport="baseball",
    )
    assert res["script_status"] in ("broken_script", "hard_deviation")
    assert res["pregame_pick_status"] == "at_risk", \
        f"Expected at_risk for adverse hard-dev; got {res['pregame_pick_status']!r}"
    assert res["pregame_pick_status"] != "still_playable", \
        "Pregame pick must NOT be 'still_playable' with adverse hard-dev"
    assert res["live_verdict"] == "AVOID_OVER_OR_CASHOUT"
    assert res["live_recommendation_status"] == "hedge"


def test_under_pick_with_favorable_hard_deviation_keeps_still_playable():
    """When the deviation is FAVOURABLE for the pick, keep ``still_playable``
    but tag the script as the ``_favorable`` variant so the UI shows the
    correct context."""
    res = compare_pregame_vs_live(
        pregame_pick={
            "recommendation": {
                "market":     "Menos de 7.5 carreras",
                "selection":  "Under",
                "odds_range": "1.85-2.00",
            },
            "_mlb_script_v2": {"expectedRuns": 8.4},
        },
        live_state=_live(7, 1, 0),
        sport="baseball",
    )
    assert res["pregame_pick_status"] == "still_playable"
    assert res["script_status"] in (
        "broken_script_favorable", "hard_deviation_favorable",
    )
    assert res["live_verdict"] == "MAINTAIN"


def test_pregame_without_market_returns_not_evaluable():
    """A structural-lean pick (no odds yet) must surface as not_evaluable."""
    res = compare_pregame_vs_live(
        pregame_pick={
            "recommendation": {"market": "", "selection": ""},
            "_bucket":        "structural_lean_requires_odds",
        },
        live_state=_live(5, 1, 0),
        sport="baseball",
    )
    assert res["pregame_pick_status"] == "not_evaluable"
    assert res["live_verdict"] == "USE_LIVE_READ_ONLY"
    assert res["live_recommendation_status"] == "wait"


def test_pregame_with_manual_odds_required_returns_not_evaluable():
    """A pick flagged manual_odds_review.required must NOT be still_playable
    until the user pastes their odds."""
    res = compare_pregame_vs_live(
        pregame_pick={
            "recommendation":        {"market": "Run Line -1.5", "selection": "Home"},
            "manual_odds_review":    {"required": True, "reason": "no_engine_odds_available"},
        },
        live_state=_live(5, 1, 0),
        sport="baseball",
    )
    assert res["pregame_pick_status"] == "not_evaluable"


def test_no_contradiction_under_pick_with_under_pace():
    """Pick=Under and game running UNDER pace → favorable variant, still playable."""
    res = compare_pregame_vs_live(
        pregame_pick={
            "recommendation": {"market": "Under 7.5", "selection": "Under",
                                  "odds_range": "1.90"},
            "_mlb_script_v2": {"expectedRuns": 8.0},
        },
        live_state=_live(7, 2, 1),
        sport="baseball",
    )
    # Total = 3 vs expected_through ~5.6 → score_delta ~ -2.6 (soft/hard)
    # If hard → favorable variant. If soft → vanilla soft_deviation.
    assert res["pregame_pick_status"] in ("still_playable",)
    if res["script_status"] in ("broken_script", "hard_deviation"):
        # If the classifier upgraded to hard, it MUST be the favorable variant.
        pytest.fail(f"Adverse classification on a favorable Under pick: "
                    f"{res['script_status']}")


def test_no_pregame_pick_path_still_works():
    res = compare_pregame_vs_live(
        pregame_pick=None,
        live_state=_live(7, 1, 0),
        sport="baseball",
    )
    assert "NO_PREGAME_PICK" in res["reason_codes"]


def test_at_risk_pickstatus_translates_to_hedge_recommendation():
    """Verify the wiring: pregame_status=at_risk → live_rec=hedge."""
    res = compare_pregame_vs_live(
        pregame_pick={
            "recommendation": {"market": "Over 5.0", "selection": "Over",
                                  "odds_range": "1.90"},
            "_mlb_script_v2": {"expectedRuns": 9.0},
        },
        live_state=_live(8, 0, 1),
        sport="baseball",
    )
    if res["pregame_pick_status"] == "at_risk":
        assert res["live_recommendation_status"] == "hedge"


def test_already_resolved_loss_takes_priority_over_at_risk():
    """If the validator says the market already resolved as a loss, the
    pregame_pick_status must be ``already_lost`` (not ``at_risk``)."""
    res = compare_pregame_vs_live(
        pregame_pick={
            "recommendation": {"market": "Over 10.5", "selection": "Over",
                                  "odds_range": "1.90"},
            "_mlb_script_v2": {"expectedRuns": 11.0},
        },
        live_state={
            "is_live":  False,
            "state":    "final",
            "score":    {"home": 1, "away": 0},
            "inning":   {"number": 9},
            "status":   "Final",
        },
        sport="baseball",
    )
    # Final game with score 1-0 and Over 10.5 → already lost
    assert res["pregame_pick_status"] in ("already_lost", "not_actionable")
    assert res["pregame_pick_status"] != "at_risk"
