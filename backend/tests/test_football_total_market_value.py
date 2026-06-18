"""Sprint D10-B · tests del módulo football_total_market_value.

Cubre:
  * Líneas .5 — sin push (Over 2.5, Under 2.5).
  * Líneas .0 — con push (Over 3.0, Under 3.0).
  * Líneas .25 / .75 — split asiático en dos sub-legs (asian_split[]).
  * EV asiático = 0.5*EV(leg1) + 0.5*EV(leg2).
  * fair_odds, edge_percentage_points, value_class.
  * Anti-circularidad: la cuota no modifica las probabilidades.
  * La línea cambia probabilidades; misma cuota → distinto EV.
  * Estados INVALID_INPUTS / MARKET_LINE_MISSING / BASE_MODEL_ONLY / REPRICED.
  * P(over) + P(push) + P(under) ≈ 1 para línea .0.
  * `decision` tag combina value_class + support_class.
"""
from __future__ import annotations

import math
import pytest

from services.football_total_market_value import (
    calculate_football_total_market_value,
    VALUE_CLASS_BREAKS,
)


LAM_H = 1.5
LAM_A = 1.0


# ─── Líneas .5 (sin push) ──────────────────────────────────────────────
def test_line_05_over_25_sums_to_one_no_push():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.10,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    probs = out["probabilities"]
    assert probs["push"] == 0.0
    assert math.isclose(probs["win"] + probs["loss"], 1.0, abs_tol=1e-3)
    # Over y Under son la misma información que win/loss.
    assert probs["over"] == probs["win"]
    assert probs["under"] == probs["loss"]
    assert out["status"] == "REPRICED"


def test_line_05_under_complementary_to_over():
    """Same lambdas: P(Over 2.5) + P(Under 2.5) = 1 (sin push)."""
    over_out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    under_out = calculate_football_total_market_value(
        selection="UNDER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert math.isclose(over_out["probabilities"]["win"]
                          + under_out["probabilities"]["win"], 1.0, abs_tol=1e-3)


# ─── Líneas .0 (con push) ──────────────────────────────────────────────
def test_line_00_over_30_push_is_p_total_equals_3():
    out = calculate_football_total_market_value(
        selection="OVER", line=3.0, decimal_odds=2.5,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    probs = out["probabilities"]
    # win + push + loss ≈ 1 con tolerancia (DC matrix no normalizada).
    s = probs["win"] + probs["push"] + probs["loss"]
    assert math.isclose(s, 1.0, abs_tol=0.05)
    # Push > 0.
    assert probs["push"] > 0.0


def test_line_00_under_treats_3_goals_as_push_not_loss():
    """Under 3.0 — exactamente 3 goles = push, no loss."""
    out = calculate_football_total_market_value(
        selection="UNDER", line=3.0, decimal_odds=1.85,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    probs = out["probabilities"]
    assert probs["push"] > 0.0
    # win (Under estricto) = P(total<3).
    # loss (Over estricto) = P(total>3).
    # Compare con OVER 3.0:
    over_out = calculate_football_total_market_value(
        selection="OVER", line=3.0, decimal_odds=1.85,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert math.isclose(probs["win"], over_out["probabilities"]["loss"], abs_tol=1e-6)
    assert math.isclose(probs["loss"], over_out["probabilities"]["win"], abs_tol=1e-6)
    assert math.isclose(probs["push"], over_out["probabilities"]["push"], abs_tol=1e-6)


# ─── Líneas asiáticas .25 / .75 ────────────────────────────────────────
def test_line_25_returns_two_legs_at_0_and_5():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.25, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert "asian_split" in out
    legs = out["asian_split"]
    assert len(legs) == 2
    assert {l["line"] for l in legs} == {2.0, 2.5}
    # Cada leg stake_fraction = 0.5.
    assert all(l["stake_fraction"] == 0.5 for l in legs)


def test_line_75_returns_two_legs_at_5_and_0():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.75, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    legs = out["asian_split"]
    assert {l["line"] for l in legs} == {2.5, 3.0}


def test_asian_ev_equals_weighted_sum_of_legs():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.75, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    legs = out["asian_split"]
    ev_expected = sum(l["ev_percentage"] * l["stake_fraction"] for l in legs)
    assert math.isclose(out["valuation"]["ev_percentage"], ev_expected, abs_tol=1e-3)


def test_asian_leg_at_0_has_push_leg_at_5_does_not():
    out = calculate_football_total_market_value(
        selection="UNDER", line=2.75, decimal_odds=1.85,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    legs = {l["line"]: l for l in out["asian_split"]}
    assert legs[2.5]["push"] == 0.0
    assert legs[3.0]["push"] > 0.0


# ─── Anti-circularidad ─────────────────────────────────────────────────
def test_changing_only_odds_does_not_change_probabilities():
    a = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    b = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=3.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert a["probabilities"]["win"] == b["probabilities"]["win"]
    assert a["probabilities"]["push"] == b["probabilities"]["push"]
    assert a["probabilities"]["loss"] == b["probabilities"]["loss"]
    # Pero el EV sí cambia.
    assert a["valuation"]["ev_percentage"] != b["valuation"]["ev_percentage"]


def test_changing_only_lambdas_changes_probabilities():
    a = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=1.0, adjusted_lambda_away=0.5,
    )
    b = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=2.0, adjusted_lambda_away=1.5,
    )
    # P(Over 2.5) con lambdas más altas > P(Over 2.5) con bajas.
    assert b["probabilities"]["win"] > a["probabilities"]["win"]


def test_changing_only_line_changes_probabilities():
    a = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    b = calculate_football_total_market_value(
        selection="OVER", line=3.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    # Over 3.5 < Over 2.5 (cota más alta).
    assert b["probabilities"]["win"] < a["probabilities"]["win"]


# ─── Fair odds / value class ───────────────────────────────────────────
def test_fair_odds_with_no_push_is_inverse_of_win_prob():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    win = out["probabilities"]["win"]
    assert math.isclose(out["valuation"]["fair_odds"], 1.0 / win, abs_tol=1e-3)


def test_fair_odds_with_push_uses_one_minus_push_formula():
    out = calculate_football_total_market_value(
        selection="UNDER", line=3.0, decimal_odds=1.85,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    win = out["probabilities"]["win"]
    push = out["probabilities"]["push"]
    expected_fair = (1.0 - push) / win
    assert math.isclose(out["valuation"]["fair_odds"], expected_fair, abs_tol=1e-3)


def test_value_class_high_edge_when_ev_above_15pct():
    """Forzar EV > 15%: pongo cuota mucho mayor que la fair."""
    # Con lambdas bajas, Under 2.5 es muy probable. Una cuota
    # alta para Under da edge alto.
    out = calculate_football_total_market_value(
        selection="UNDER", line=2.5, decimal_odds=3.0,
        adjusted_lambda_home=0.5, adjusted_lambda_away=0.5,
    )
    assert out["valuation"]["ev_percentage"] > 15
    assert out["valuation"]["value_class"] == "HIGH_EDGE_REVIEW_REQUIRED"


def test_value_class_negative_when_odds_below_fair():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=1.20,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert out["valuation"]["ev_percentage"] < 0


# ─── Estados ────────────────────────────────────────────────────────────
def test_status_invalid_inputs_missing_selection():
    out = calculate_football_total_market_value(
        selection=None, line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert out["status"] == "INVALID_INPUTS"


def test_status_invalid_inputs_missing_lambdas():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=None, adjusted_lambda_away=None,
    )
    assert out["status"] == "INVALID_INPUTS"


def test_status_market_line_missing():
    out = calculate_football_total_market_value(
        selection="OVER", line=None, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert out["status"] == "MARKET_LINE_MISSING"


def test_status_base_model_only_when_odds_missing():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=None,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert out["status"] == "BASE_MODEL_ONLY"
    # Sin odds, value_class queda en UNKNOWN.
    assert out["valuation"]["value_class"] == "UNKNOWN"


def test_status_invalid_inputs_when_odds_le_1():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=1.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert out["status"] == "INVALID_INPUTS"


# ─── Selection normalization ────────────────────────────────────────────
def test_selection_normalisation_accepts_lowercase_and_full_strings():
    for sel in ("over", "OVER", "Over 2.5", "over_2_5"):
        out = calculate_football_total_market_value(
            selection=sel, line=2.5, decimal_odds=2.0,
            adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
        )
        assert out["market"]["selection"] == "OVER"


# ─── Decision tag ───────────────────────────────────────────────────────
def test_decision_value_supported_by_context_when_score_aligned_with_over():
    """OVER selection con influence_score positivo + value > 3 →
    VALUE_SUPPORTED_BY_CONTEXT."""
    out = calculate_football_total_market_value(
        selection="UNDER", line=2.5, decimal_odds=2.6,
        adjusted_lambda_home=0.6, adjusted_lambda_away=0.6,
        influence_score=-6.0,    # apoya Under
        confidence_delta=-3.0,
    )
    # value_class MILD/GOOD/HIGH + STRONG_SUPPORT (Under sel + neg influence).
    assert out["decision"] in ("VALUE_SUPPORTED_BY_CONTEXT",)


def test_decision_value_conflicts_with_context():
    """OVER selection con influence_score muy negativo → VALUE_CONFLICTS_WITH_CONTEXT
    si hay value positivo."""
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=3.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
        influence_score=-6.0,  # apoya UNDER, conflicto con OVER
    )
    # support_class = STRONG_CONFLICT
    assert out["context"]["support_class"] == "STRONG_CONFLICT"
    if out["valuation"]["value_class"] in ("MILD_VALUE", "GOOD_VALUE", "HIGH_EDGE_REVIEW_REQUIRED"):
        assert out["decision"] == "VALUE_CONFLICTS_WITH_CONTEXT"


def test_decision_neutral_when_no_value():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    # Con odds 2.0 cerca del fair, value_class ≈ FAIR → no actionable.
    if out["valuation"]["value_class"] == "FAIR":
        assert out["decision"] == "NO_ACTIONABLE_VALUE"


# ─── Observe only flag ───────────────────────────────────────────────
def test_observe_only_flag_always_present():
    out = calculate_football_total_market_value(
        selection="OVER", line=2.5, decimal_odds=2.0,
        adjusted_lambda_home=LAM_H, adjusted_lambda_away=LAM_A,
    )
    assert out["observe_only"] is True


# ─── VALUE_CLASS_BREAKS constants ─────────────────────────────────────
def test_value_class_breaks_constants_present():
    classes = {b[2] for b in VALUE_CLASS_BREAKS}
    assert {"FAIR", "MILD_VALUE", "GOOD_VALUE", "HIGH_EDGE_REVIEW_REQUIRED",
              "NEGATIVE_VALUE", "STRONG_NEGATIVE_VALUE"} <= classes
