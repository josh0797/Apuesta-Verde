"""Tests for services/pick_divergence_analysis.py — the engine-vs-user
divergence analyzer.

Covers:
  • parse_pick() — all market types (MLB totals/F5/RL/ML + football
    Over/Under/BTTS/DC/1X2).
  • settle_pick_against_score() — WIN/LOSS/PUSH/PENDING/VOID per market.
  • compute_divergence() — DELTA_NONE / PROTECTED / AGGRESSIVE /
    DIFFERENT_MARKET / OPPOSITE_SIDE.
  • evaluate_engine_vs_user() — end-to-end on the four canonical cases
    from the user's spec.
"""
from __future__ import annotations

import pytest

from services.pick_divergence_analysis import (
    parse_pick, settle_pick_against_score, compute_divergence,
    evaluate_engine_vs_user,
    RESULT_WIN, RESULT_LOSS, RESULT_PUSH, RESULT_PENDING,
    DELTA_NONE, DELTA_USER_PROTECTED_LINE, DELTA_USER_AGGRESSIVE_LINE,
    DELTA_DIFFERENT_MARKET, DELTA_OPPOSITE_SIDE,
    LINE_DIR_MORE_PROTECTED, LINE_DIR_LESS_PROTECTED, LINE_DIR_SAME,
)


# ─── parse_pick ───────────────────────────────────────────────────────────────
class TestParsePick:
    def test_mlb_under_total(self):
        p = parse_pick(raw="UNDER 9.5")
        assert p["available"] is True
        assert p["market_type"] == "total_runs"
        assert p["side"] == "UNDER"
        assert p["line"] == 9.5

    def test_mlb_over_total_spanish(self):
        p = parse_pick(raw="Más de 10")
        assert p["available"] is True
        assert p["market_type"] == "total_runs"
        assert p["side"] == "OVER"
        assert p["line"] == 10.0

    def test_mlb_f5(self):
        p = parse_pick(raw="F5 UNDER 5")
        assert p["market_type"] == "f5_total_runs"
        assert p["side"] == "UNDER"
        assert p["line"] == 5.0

    def test_mlb_f5_spanish_first_5(self):
        p = parse_pick(raw="Primeros 5 Under 4.5")
        assert p["market_type"] == "f5_total_runs"
        assert p["line"] == 4.5

    def test_mlb_run_line(self):
        p = parse_pick(raw="RL +1.5 Phillies", market="run_line")
        assert p["market_type"] == "run_line"
        assert p["line"] == 1.5

    def test_mlb_run_line_default_line(self):
        p = parse_pick(market="run_line", selection="HOME")
        assert p["market_type"] == "run_line"
        assert p["line"] == 1.5  # auto-default

    def test_mlb_moneyline(self):
        p = parse_pick(raw="ML Phillies")
        assert p["market_type"] == "moneyline"
        assert "PHILLIES" in (p["team"] or "")

    def test_football_total_goals(self):
        p = parse_pick(raw="UNDER 2.5")
        assert p["market_type"] == "total_goals"
        assert p["line"] == 2.5

    def test_football_btts_yes(self):
        p = parse_pick(raw="BTTS SI")
        assert p["market_type"] == "btts"
        assert p["side"] == "YES"

    def test_football_btts_no(self):
        p = parse_pick(raw="BTTS NO")
        assert p["market_type"] == "btts"
        assert p["side"] == "NO"

    def test_football_double_chance_1x(self):
        p = parse_pick(raw="DC 1X")
        assert p["market_type"] == "double_chance"
        assert p["side"] == "1X"

    def test_football_1x2_draw(self):
        p = parse_pick(market="1x2", selection="Empate")
        assert p["market_type"] == "moneyline_1x2"
        assert p["side"] == "DRAW"

    def test_empty_input_failsoft(self):
        p = parse_pick(raw="")
        assert p["available"] is False

    def test_garbage_input_failsoft(self):
        p = parse_pick(raw="@@@nonsense$$$")
        assert p["available"] is False


# ─── settle_pick_against_score — totals ──────────────────────────────────────
class TestSettleTotals:
    def test_under_wins(self):
        p = parse_pick(raw="UNDER 9.5")
        r = settle_pick_against_score(pick=p, final_home=4, final_away=3)
        assert r["result"] == RESULT_WIN  # 7 < 9.5

    def test_under_loses(self):
        p = parse_pick(raw="UNDER 9.5")
        r = settle_pick_against_score(pick=p, final_home=6, final_away=4)
        assert r["result"] == RESULT_LOSS  # 10 > 9.5

    def test_over_wins(self):
        p = parse_pick(raw="OVER 7.5")
        r = settle_pick_against_score(pick=p, final_home=5, final_away=4)
        assert r["result"] == RESULT_WIN  # 9 > 7.5

    def test_push_on_whole_line(self):
        p = parse_pick(raw="UNDER 10")
        r = settle_pick_against_score(pick=p, final_home=5, final_away=5)
        assert r["result"] == RESULT_PUSH

    def test_missing_score_pending(self):
        p = parse_pick(raw="UNDER 9.5")
        r = settle_pick_against_score(pick=p, final_home=None, final_away=None)
        assert r["result"] == RESULT_PENDING

    def test_f5_total_uses_dedicated_score(self):
        p = parse_pick(raw="F5 UNDER 5")
        # Game ended 10-1 but F5 was 2-1 → 3 → UNDER 5 wins.
        r = settle_pick_against_score(
            pick=p, final_home=10, final_away=1, f5_home=2, f5_away=1,
        )
        assert r["result"] == RESULT_WIN


# ─── settle — moneylines / btts / dc / rl ────────────────────────────────────
class TestSettleOther:
    def test_btts_yes_wins(self):
        p = parse_pick(raw="BTTS SI")
        r = settle_pick_against_score(pick=p, final_home=2, final_away=1)
        assert r["result"] == RESULT_WIN

    def test_btts_yes_loses_when_clean_sheet(self):
        p = parse_pick(raw="BTTS SI")
        r = settle_pick_against_score(pick=p, final_home=2, final_away=0)
        assert r["result"] == RESULT_LOSS

    def test_btts_no_wins_on_clean_sheet(self):
        p = parse_pick(raw="BTTS NO")
        r = settle_pick_against_score(pick=p, final_home=3, final_away=0)
        assert r["result"] == RESULT_WIN

    def test_1x2_draw_wins(self):
        p = parse_pick(market="1x2", selection="Empate")
        r = settle_pick_against_score(pick=p, final_home=1, final_away=1)
        assert r["result"] == RESULT_WIN

    def test_1x2_home_loses_on_draw(self):
        p = parse_pick(market="1x2", selection="HOME")
        r = settle_pick_against_score(pick=p, final_home=1, final_away=1)
        assert r["result"] == RESULT_LOSS

    def test_dc_1x_wins_on_home(self):
        p = parse_pick(raw="DC 1X")
        r = settle_pick_against_score(pick=p, final_home=2, final_away=0)
        assert r["result"] == RESULT_WIN

    def test_dc_1x_wins_on_draw(self):
        p = parse_pick(raw="DC 1X")
        r = settle_pick_against_score(pick=p, final_home=1, final_away=1)
        assert r["result"] == RESULT_WIN

    def test_dc_1x_loses_on_away(self):
        p = parse_pick(raw="DC 1X")
        r = settle_pick_against_score(pick=p, final_home=0, final_away=2)
        assert r["result"] == RESULT_LOSS

    def test_run_line_home_plus_15_wins_on_one_run_loss(self):
        p = parse_pick(market="run_line", selection="HOME", line=1.5)
        # Home loses by 1 → +1.5 covers.
        r = settle_pick_against_score(pick=p, final_home=4, final_away=5)
        assert r["result"] == RESULT_WIN


# ─── compute_divergence ──────────────────────────────────────────────────────
class TestComputeDivergence:
    def test_same_pick_followed(self):
        eng = parse_pick(raw="UNDER 9.5")
        usr = parse_pick(raw="UNDER 9.5")
        d = compute_divergence(engine_pick=eng, user_pick=usr)
        assert d["followed_engine"] is True
        assert d["delta"] == DELTA_NONE
        assert d["line_difference"] == 0.0

    def test_no_user_pick_assumed_followed(self):
        eng = parse_pick(raw="UNDER 9.5")
        d = compute_divergence(engine_pick=eng, user_pick=None)
        assert d["followed_engine"] is True
        assert d["delta"] == DELTA_NONE

    def test_under_protected_line(self):
        # Engine UNDER 9.5 → User UNDER 10.5 (more cushion).
        eng = parse_pick(raw="UNDER 9.5")
        usr = parse_pick(raw="UNDER 10.5")
        d = compute_divergence(engine_pick=eng, user_pick=usr)
        assert d["followed_engine"] is False
        assert d["delta"] == DELTA_USER_PROTECTED_LINE
        assert d["line_difference"] == 1.0
        assert d["line_direction"] == LINE_DIR_MORE_PROTECTED

    def test_under_aggressive_line(self):
        # Engine UNDER 9.5 → User UNDER 8.5 (shortened).
        eng = parse_pick(raw="UNDER 9.5")
        usr = parse_pick(raw="UNDER 8.5")
        d = compute_divergence(engine_pick=eng, user_pick=usr)
        assert d["delta"] == DELTA_USER_AGGRESSIVE_LINE
        assert d["line_direction"] == LINE_DIR_LESS_PROTECTED

    def test_over_protected_when_line_drops(self):
        # Engine OVER 9.5 → User OVER 8.5 (easier line to hit).
        eng = parse_pick(raw="OVER 9.5")
        usr = parse_pick(raw="OVER 8.5")
        d = compute_divergence(engine_pick=eng, user_pick=usr)
        assert d["delta"] == DELTA_USER_PROTECTED_LINE

    def test_different_market(self):
        eng = parse_pick(raw="UNDER 9.5")
        usr = parse_pick(raw="ML Phillies")
        d = compute_divergence(engine_pick=eng, user_pick=usr)
        assert d["delta"] == DELTA_DIFFERENT_MARKET
        assert d["followed_engine"] is False

    def test_opposite_side_same_market(self):
        eng = parse_pick(raw="UNDER 9.5")
        usr = parse_pick(raw="OVER 9.5")
        d = compute_divergence(engine_pick=eng, user_pick=usr)
        assert d["delta"] == DELTA_OPPOSITE_SIDE


# ─── evaluate_engine_vs_user — 4 canonical cases from spec ───────────────────
class TestCanonicalCases:
    """The four cases in the user's spec."""

    def test_case1_both_win(self):
        # Engine UNDER 9.5, User UNDER 10, Final 7 runs → both WIN.
        out = evaluate_engine_vs_user(
            engine_market="total_runs", engine_selection="UNDER", engine_line=9.5,
            user_market="total_runs",   user_selection="UNDER", user_line=10.0,
            final_home=4, final_away=3,
        )
        assert out["engine_result"] == RESULT_WIN
        assert out["user_result"]   == RESULT_WIN
        assert out["followed_engine"] is False
        assert out["delta"] == DELTA_USER_PROTECTED_LINE

    def test_case2_engine_loses_user_protected(self):
        # Engine UNDER 9.5 LOSS, User UNDER 10.5 WIN, Final 10 runs.
        out = evaluate_engine_vs_user(
            engine_market="total_runs", engine_selection="UNDER", engine_line=9.5,
            user_market="total_runs",   user_selection="UNDER", user_line=10.5,
            final_home=6, final_away=4,
        )
        assert out["engine_result"] == RESULT_LOSS
        assert out["user_result"]   == RESULT_WIN
        assert out["delta"]         == DELTA_USER_PROTECTED_LINE
        assert out["line_difference"] == 1.0

    def test_case3_engine_wins_user_aggressive(self):
        # Engine UNDER 9.5 WIN, User UNDER 8.5 LOSS, Final 9 runs.
        out = evaluate_engine_vs_user(
            engine_market="total_runs", engine_selection="UNDER", engine_line=9.5,
            user_market="total_runs",   user_selection="UNDER", user_line=8.5,
            final_home=5, final_away=4,
        )
        assert out["engine_result"] == RESULT_WIN
        assert out["user_result"]   == RESULT_LOSS
        assert out["delta"]         == DELTA_USER_AGGRESSIVE_LINE

    def test_case4_both_lose(self):
        out = evaluate_engine_vs_user(
            engine_market="total_runs", engine_selection="UNDER", engine_line=9.5,
            user_market="total_runs",   user_selection="UNDER", user_line=10.0,
            final_home=8, final_away=4,
        )
        assert out["engine_result"] == RESULT_LOSS
        assert out["user_result"]   == RESULT_LOSS

    def test_engine_loss_user_push_on_whole_line(self):
        # Real-world example from spec: Engine UNDER 9.5 LOSS,
        # User UNDER 10.0 PUSH, Final 10.
        out = evaluate_engine_vs_user(
            engine_market="total_runs", engine_selection="UNDER", engine_line=9.5,
            user_market="total_runs",   user_selection="UNDER", user_line=10.0,
            final_home=6, final_away=4,
        )
        assert out["engine_result"] == RESULT_LOSS
        assert out["user_result"]   == RESULT_PUSH


# ─── fail-soft / never raises ─────────────────────────────────────────────────
class TestNeverRaises:
    def test_evaluate_with_none_inputs(self):
        out = evaluate_engine_vs_user()
        assert isinstance(out, dict)

    def test_evaluate_with_garbage(self):
        out = evaluate_engine_vs_user(
            engine_market="???", engine_selection="???",
            user_market="???",   user_selection="???",
            final_home="bad",    final_away="bad",
        )
        assert isinstance(out, dict)
        assert "engine_result" in out
