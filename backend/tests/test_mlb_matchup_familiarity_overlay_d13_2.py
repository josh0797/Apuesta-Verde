"""
Sprint D13.2 — Matchup Familiarity Overlay · Moneyline + Runline tests.

Cubre:
  * Section 5 — Moneyline overlay (RECENT_H2H_SUPPORTS_*_ML,
    SERIES_BULLPEN_EDGE_*, STARTER_SEEN_RECENTLY_EDGE).
  * Section 6 — Runline overlay (RECENT_H2H_RUNLINE_SUPPORT,
    SERIES_MARGIN_EDGE, LATE_INNING_SCORING_EDGE,
    BULLPEN_FATIGUE_RUNLINE_EDGE, RL_VETOED_LOW_BASE_MARGIN).
  * Safety: clamps (±5 puntos, ±5% win prob, ±1.5 carreras margen).
  * Veto automático con threshold ajustado a <2.0.
  * Renombre / alias del payload (over_under_impact + totals_overlay).
"""

from __future__ import annotations

import pytest

from services.mlb_matchup_familiarity_overlay import (
    LEAN_AWAY,
    LEAN_AWAY_RL,
    LEAN_HOME,
    LEAN_HOME_RL,
    LEAN_NEUTRAL,
    MAX_ML_WIN_PROB_DELTA,
    MAX_OVERLAY_POINTS,
    MAX_RL_MARGIN_DELTA,
    RL_BASE_MARGIN_VETO_THRESHOLD,
    calculate_matchup_familiarity_overlay,
)


HOME = "New York Yankees"
AWAY = "Boston Red Sox"
GAME_DATE = "2025-06-20"


def _game(date, home_team, away_team, home_score, away_score, **extra):
    g = {
        "date":       date,
        "home_team":  home_team,
        "away_team":  away_team,
        "home_score": home_score,
        "away_score": away_score,
    }
    g.update(extra)
    return g


def _ctx_ml(games, **kwargs):
    base = {
        "home_team":           HOME,
        "away_team":           AWAY,
        "game_date":           GAME_DATE,
        "current_pick_market": "MONEYLINE",
        "current_pick_side":   "HOME",
        "current_line":        None,
        "recent_h2h_games":    games,
    }
    base.update(kwargs)
    return base


def _ctx_rl(games, **kwargs):
    base = {
        "home_team":           HOME,
        "away_team":           AWAY,
        "game_date":           GAME_DATE,
        "current_pick_market": "RUNLINE",
        "current_pick_side":   "HOME_RL",
        "current_line":        -1.5,
        "recent_h2h_games":    games,
    }
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────
# Section 5 — Moneyline overlay
# ─────────────────────────────────────────────────────────────────────
class TestMoneylineOverlay:
    def test_no_games_neutral(self):
        out = calculate_matchup_familiarity_overlay(_ctx_ml([]))
        ml = out["moneyline_impact"]
        assert ml["lean"] == LEAN_NEUTRAL
        assert ml["points"] == 0.0
        assert ml["win_prob_adjustment"] == 0.0

    def test_only_total_market_returns_neutral_ml(self):
        games = [_game("2025-06-19", HOME, AWAY, 7, 2)]
        ctx = _ctx_ml(games, current_pick_market="TOTAL", current_pick_side="OVER")
        out = calculate_matchup_familiarity_overlay(ctx)
        assert out["moneyline_impact"]["lean"] == LEAN_NEUTRAL
        assert out["moneyline_impact"]["points"] == 0.0

    def test_home_2_plus_clear_wins_supports_home_ml(self):
        # 2 home wins with margin ≥2 each + avg diff supports home.
        games = [
            _game("2025-06-17", HOME, AWAY, 7, 2),
            _game("2025-06-18", HOME, AWAY, 6, 1),
            _game("2025-06-19", HOME, AWAY, 5, 4),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx_ml(games))
        ml = out["moneyline_impact"]
        assert "RECENT_H2H_SUPPORTS_HOME_ML" in ml["reason_codes"]
        assert ml["lean"] == LEAN_HOME
        assert ml["points"] > 0
        assert ml["win_prob_adjustment"] > 0

    def test_away_2_plus_clear_wins_supports_away_ml(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 2, 7),
            _game("2025-06-18", HOME, AWAY, 1, 6),
            _game("2025-06-19", HOME, AWAY, 3, 4),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx_ml(games))
        ml = out["moneyline_impact"]
        assert "RECENT_H2H_SUPPORTS_AWAY_ML" in ml["reason_codes"]
        assert ml["lean"] == LEAN_AWAY
        assert ml["points"] < 0
        assert ml["win_prob_adjustment"] < 0

    def test_single_won_yesterday_alone_does_not_award_points(self):
        # 1 home win, no other supporting metric → safety rule drops.
        games = [_game("2025-06-19", HOME, AWAY, 4, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx_ml(games))
        ml = out["moneyline_impact"]
        assert ml["points"] == 0.0
        assert ml["lean"] == LEAN_NEUTRAL

    def test_bullpen_edge_home_when_away_bullpen_tired(self):
        # H2H shows bullpen exposure + away bullpen fatigue now.
        games = [
            _game("2025-06-18", HOME, AWAY, 6, 4, bullpen_pitch_count_away=80),
            _game("2025-06-19", HOME, AWAY, 7, 5),
        ]
        ctx = _ctx_ml(games)
        ctx["bullpen_usage"] = {
            "home": {"bullpen_fatigue": 0.30},
            "away": {"bullpen_fatigue": 0.75},  # away exhausted → home edge
        }
        out = calculate_matchup_familiarity_overlay(ctx)
        ml = out["moneyline_impact"]
        assert "SERIES_BULLPEN_EDGE_HOME" in ml["reason_codes"]
        assert ml["points"] > 0

    def test_bullpen_edge_away_when_home_bullpen_tired(self):
        games = [
            _game("2025-06-18", HOME, AWAY, 4, 6, bullpen_pitch_count_home=80),
            _game("2025-06-19", HOME, AWAY, 5, 7),
        ]
        ctx = _ctx_ml(games)
        ctx["bullpen_usage"] = {
            "home": {"bullpen_fatigue": 0.80},  # home exhausted → away edge
            "away": {"bullpen_fatigue": 0.20},
        }
        out = calculate_matchup_familiarity_overlay(ctx)
        ml = out["moneyline_impact"]
        assert "SERIES_BULLPEN_EDGE_AWAY" in ml["reason_codes"]
        assert ml["points"] < 0

    def test_starter_seen_recently_edge(self):
        # Away team scored ≥6 vs home starter recently → AWAY edge.
        games = [
            _game("2025-06-18", HOME, AWAY, 3, 8, starter_home="G. Cole"),
            _game("2025-06-19", HOME, AWAY, 2, 7, starter_home="G. Cole"),
        ]
        ctx = _ctx_ml(games)
        ctx["starter_info"] = {"home_starter": "G. Cole"}
        out = calculate_matchup_familiarity_overlay(ctx)
        ml = out["moneyline_impact"]
        assert "STARTER_SEEN_RECENTLY_EDGE" in ml["reason_codes"]
        assert ml["points"] < 0  # away favoured

    def test_ml_points_clamped_to_5(self):
        # Stack many triggers in favor of home.
        games = [
            _game("2025-06-17", HOME, AWAY, 12, 1, bullpen_pitch_count_away=90,
                  starter_away="J. Smith"),
            _game("2025-06-18", HOME, AWAY, 10, 2),
            _game("2025-06-19", HOME, AWAY, 9, 3),
        ]
        ctx = _ctx_ml(games)
        ctx["bullpen_usage"] = {"home": {"bullpen_fatigue": 0.10},
                                "away": {"bullpen_fatigue": 0.80}}
        ctx["starter_info"] = {"away_starter": "J. Smith"}
        out = calculate_matchup_familiarity_overlay(ctx)
        ml = out["moneyline_impact"]
        assert ml["points"] <= MAX_OVERLAY_POINTS
        assert ml["points"] >= 4.0  # well above 0

    def test_ml_win_prob_clamped_to_5_pct(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 12, 1, bullpen_pitch_count_away=90,
                  starter_away="J. Smith"),
            _game("2025-06-18", HOME, AWAY, 10, 2),
            _game("2025-06-19", HOME, AWAY, 9, 3),
        ]
        ctx = _ctx_ml(games)
        ctx["bullpen_usage"] = {"home": {"bullpen_fatigue": 0.10},
                                "away": {"bullpen_fatigue": 0.80}}
        out = calculate_matchup_familiarity_overlay(ctx)
        ml = out["moneyline_impact"]
        assert abs(ml["win_prob_adjustment"]) <= MAX_ML_WIN_PROB_DELTA + 1e-6


# ─────────────────────────────────────────────────────────────────────
# Section 6 — Runline overlay
# ─────────────────────────────────────────────────────────────────────
class TestRunlineOverlay:
    def test_no_games_neutral(self):
        out = calculate_matchup_familiarity_overlay(_ctx_rl([]))
        rl = out["runline_impact"]
        assert rl["lean"] == LEAN_NEUTRAL
        assert rl["points"] == 0.0
        assert rl["projected_margin_adjustment"] == 0.0
        assert rl["vetoed"] is False

    def test_only_total_market_returns_neutral_rl(self):
        games = [_game("2025-06-19", HOME, AWAY, 5, 3)]
        ctx = _ctx_rl(games, current_pick_market="TOTAL")
        out = calculate_matchup_familiarity_overlay(ctx)
        assert out["runline_impact"]["lean"] == LEAN_NEUTRAL

    def test_home_dominant_runline_support(self):
        # 2+ home wins by 2+ runs + avg margin ≥2.
        games = [
            _game("2025-06-17", HOME, AWAY, 7, 2),
            _game("2025-06-18", HOME, AWAY, 6, 1),
            _game("2025-06-19", HOME, AWAY, 5, 1),
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = 2.5  # above veto threshold
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        assert "RECENT_H2H_RUNLINE_SUPPORT" in rl["reason_codes"]
        assert "SERIES_MARGIN_EDGE" in rl["reason_codes"]
        assert rl["lean"] == LEAN_HOME_RL
        assert rl["points"] > 0
        assert rl["vetoed"] is False

    def test_away_dominant_runline_support(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 2, 7),
            _game("2025-06-18", HOME, AWAY, 1, 6),
            _game("2025-06-19", HOME, AWAY, 1, 5),
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = -2.5
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        assert rl["lean"] == LEAN_AWAY_RL
        assert rl["points"] < 0

    def test_veto_when_base_margin_under_2(self):
        # Strong H2H signal but base margin < 2.0 → vetoed.
        games = [
            _game("2025-06-17", HOME, AWAY, 7, 2),
            _game("2025-06-18", HOME, AWAY, 6, 1),
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = 1.5  # below 2.0 threshold
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        assert rl["vetoed"] is True
        assert rl["points"] == 0.0
        assert rl["projected_margin_adjustment"] == 0.0
        assert rl["lean"] == LEAN_NEUTRAL
        assert "RL_VETOED_LOW_BASE_MARGIN" in rl["reason_codes"]

    def test_no_veto_at_exactly_2(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 7, 2),
            _game("2025-06-18", HOME, AWAY, 6, 1),
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = 2.0  # exactly at threshold
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        # Veto fires only when strictly below.
        assert rl["vetoed"] is False

    def test_veto_with_negative_base_margin(self):
        # Symmetric: base margin -1.2 (abs < 2.0) → vetoed too.
        games = [
            _game("2025-06-17", HOME, AWAY, 2, 7),
            _game("2025-06-18", HOME, AWAY, 1, 6),
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = -1.2
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        assert rl["vetoed"] is True
        assert "RL_VETOED_LOW_BASE_MARGIN" in rl["reason_codes"]

    def test_late_inning_scoring_edge(self):
        # innings_breakdown shows runs in inning 6-9.
        games = [
            _game("2025-06-17", HOME, AWAY, 7, 2,
                  innings_breakdown=[1, 0, 0, 1, 0, 2, 0, 3, 0]),  # late = 5 runs
            _game("2025-06-18", HOME, AWAY, 6, 1,
                  innings_breakdown=[0, 1, 0, 0, 1, 1, 1, 1, 1]),  # late = 4 runs
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = 2.5
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        assert "LATE_INNING_SCORING_EDGE" in rl["reason_codes"]

    def test_bullpen_fatigue_runline_edge(self):
        games = [
            _game("2025-06-18", HOME, AWAY, 6, 4),
            _game("2025-06-19", HOME, AWAY, 7, 5),
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = 2.5
        ctx["bullpen_usage"] = {
            "home": {"bullpen_fatigue": 0.20},
            "away": {"bullpen_fatigue": 0.75},
        }
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        assert "BULLPEN_FATIGUE_RUNLINE_EDGE" in rl["reason_codes"]

    def test_rl_points_clamped_to_5(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 10, 1,
                  innings_breakdown=[1, 0, 1, 1, 0, 2, 1, 2, 2],
                  bullpen_pitch_count_away=80),
            _game("2025-06-18", HOME, AWAY, 9, 2),
            _game("2025-06-19", HOME, AWAY, 8, 1),
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = 3.0
        ctx["bullpen_usage"] = {"home": {"bullpen_fatigue": 0.10},
                                "away": {"bullpen_fatigue": 0.80}}
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        assert rl["points"] <= MAX_OVERLAY_POINTS

    def test_rl_margin_adj_clamped(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 10, 1),
            _game("2025-06-18", HOME, AWAY, 9, 2),
            _game("2025-06-19", HOME, AWAY, 8, 1),
        ]
        ctx = _ctx_rl(games)
        ctx["base_projected_margin"] = 3.0
        out = calculate_matchup_familiarity_overlay(ctx)
        rl = out["runline_impact"]
        assert abs(rl["projected_margin_adjustment"]) <= MAX_RL_MARGIN_DELTA + 1e-6


# ─────────────────────────────────────────────────────────────────────
# Output shape — D13.2 renames
# ─────────────────────────────────────────────────────────────────────
class TestOutputShapeD13_2:
    def test_over_under_impact_present(self):
        games = [_game("2025-06-19", HOME, AWAY, 5, 3)]
        out = calculate_matchup_familiarity_overlay({
            "home_team": HOME, "away_team": AWAY, "game_date": GAME_DATE,
            "current_pick_market": "TOTAL", "current_pick_side": "OVER",
            "current_line": 8.5, "recent_h2h_games": games,
        })
        assert "over_under_impact" in out
        # Back-compat alias.
        assert "totals_overlay" in out
        # They must be the same content.
        assert out["over_under_impact"] == out["totals_overlay"]

    def test_moneyline_impact_present(self):
        out = calculate_matchup_familiarity_overlay(_ctx_ml([]))
        assert "moneyline_impact" in out
        assert "win_prob_adjustment" in out["moneyline_impact"]

    def test_runline_impact_present(self):
        out = calculate_matchup_familiarity_overlay(_ctx_rl([]))
        assert "runline_impact" in out
        assert "projected_margin_adjustment" in out["runline_impact"]
        assert "vetoed" in out["runline_impact"]


# ─────────────────────────────────────────────────────────────────────
# Constants sanity
# ─────────────────────────────────────────────────────────────────────
class TestConstantsSanity:
    def test_rl_veto_threshold_is_2(self):
        assert RL_BASE_MARGIN_VETO_THRESHOLD == 2.0

    def test_max_ml_win_prob_delta(self):
        assert MAX_ML_WIN_PROB_DELTA == pytest.approx(0.05)

    def test_max_rl_margin_delta(self):
        assert MAX_RL_MARGIN_DELTA == pytest.approx(1.5)
