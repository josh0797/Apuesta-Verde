"""
Sprint D13 — MLB Matchup Familiarity Overlay tests.

Cubre las 4 secciones del spec:
  1) Detección de enfrentamientos recientes (clasificación de ventana).
  2) Métricas H2H.
  3) Familiarity score 0..100 + buckets.
  4) Totals overlay (OVER/UNDER points, clamping ±5, reason codes).

Más:
  * Fail-soft / contratos defensivos.
  * Alineación de polaridad (home/away swap entre juegos pasados).
  * Multi-orientation inputs (compat con games_detail).
"""

from __future__ import annotations

import pytest

from services.mlb_matchup_familiarity_overlay import (
    BUCKET_HIGH,
    BUCKET_LOW,
    BUCKET_MEDIUM,
    BUCKET_NONE,
    LEAN_NEUTRAL,
    LEAN_OVER,
    LEAN_UNDER,
    MAX_OVERLAY_POINTS,
    WINDOW_3_DAYS,
    WINDOW_5_DAYS,
    WINDOW_15_DAYS,
    WINDOW_NONE,
    calculate_matchup_familiarity_overlay,
)


# Reference matchup used across tests.
HOME = "New York Yankees"
AWAY = "Boston Red Sox"
GAME_DATE = "2025-06-20"


def _game(date, home_team, away_team, home_score, away_score, **extra):
    """Quick spec-shaped game dict."""
    g = {
        "date":       date,
        "home_team":  home_team,
        "away_team":  away_team,
        "home_score": home_score,
        "away_score": away_score,
    }
    g.update(extra)
    return g


def _ctx(
    games,
    market="TOTAL",
    side="OVER",
    line=8.5,
    **kwargs,
):
    base = {
        "home_team":           HOME,
        "away_team":           AWAY,
        "game_date":           GAME_DATE,
        "current_pick_market": market,
        "current_pick_side":   side,
        "current_line":        line,
        "recent_h2h_games":    games,
    }
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────
# 0) Fail-soft / contracts
# ─────────────────────────────────────────────────────────────────────
class TestFailSoftContract:
    def test_none_context_returns_neutral(self):
        out = calculate_matchup_familiarity_overlay(None)
        assert out["available"] is True
        assert out["recent_h2h_found"] is False
        assert out["bucket"] == BUCKET_NONE
        assert out["familiarity_score"] == 0.0
        assert out["totals_overlay"]["points"] == 0.0
        assert out["totals_overlay"]["lean"] == LEAN_NEUTRAL

    def test_non_dict_context_neutral(self):
        out = calculate_matchup_familiarity_overlay("not a dict")
        assert out["bucket"] == BUCKET_NONE

    def test_missing_team_names_neutral(self):
        out = calculate_matchup_familiarity_overlay({"home_team": "", "away_team": "X"})
        assert "MISSING_TEAM_NAMES" in out["missing_fields"]

    def test_recent_h2h_not_list_neutral(self):
        ctx = _ctx([])
        ctx["recent_h2h_games"] = "garbage"
        out = calculate_matchup_familiarity_overlay(ctx)
        assert out["bucket"] == BUCKET_NONE
        assert "RECENT_H2H_NOT_LIST" in out["missing_fields"]

    def test_empty_games_returns_neutral(self):
        out = calculate_matchup_familiarity_overlay(_ctx([]))
        assert out["recent_h2h_found"] is False
        assert out["h2h_window"] == WINDOW_NONE
        assert out["bucket"] == BUCKET_NONE
        assert out["totals_overlay"]["points"] == 0.0

    def test_malformed_game_skipped_not_raise(self):
        games = [
            {"foo": "bar"},  # no date/scores
            _game("2025-06-19", HOME, AWAY, 5, 3),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["games_count"] == 1
        assert any("H2H_SKIPPED_INVALID" in m for m in out["missing_fields"])

    def test_invalid_date_skipped(self):
        games = [
            _game("not-a-date", HOME, AWAY, 5, 3),
            _game("2025-06-19", HOME, AWAY, 5, 3),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["games_count"] == 1

    def test_negative_score_skipped(self):
        games = [_game("2025-06-19", HOME, AWAY, -1, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["games_count"] == 0


# ─────────────────────────────────────────────────────────────────────
# 1) Window classification
# ─────────────────────────────────────────────────────────────────────
class TestWindowClassification:
    def test_yesterday_classifies_3_days(self):
        # game_date = 2025-06-20 → yesterday = 2025-06-19 → delta ~1d.
        games = [_game("2025-06-19", HOME, AWAY, 7, 4)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["h2h_window"] == WINDOW_3_DAYS

    def test_four_days_back_classifies_5_days(self):
        games = [_game("2025-06-16", HOME, AWAY, 6, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["h2h_window"] == WINDOW_5_DAYS

    def test_ten_days_back_classifies_15_days(self):
        games = [_game("2025-06-10", HOME, AWAY, 6, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["h2h_window"] == WINDOW_15_DAYS

    def test_twenty_days_back_excluded(self):
        # > 16 days = excluded from relevant entirely.
        games = [_game("2025-05-30", HOME, AWAY, 6, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["games_count"] == 0
        # But classified as 2-years older flag (we have something).
        assert out["h2h_window"] in ("LAST_2_YEARS", WINDOW_NONE)
        assert out["recent_h2h_found"] is False

    def test_future_game_dropped(self):
        # Game AFTER reference date should not count (it would be the
        # upcoming game itself or a future one).
        games = [_game("2025-06-21", HOME, AWAY, 6, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["games_count"] == 0

    def test_same_day_game_dropped(self):
        # Same-day games should not count as H2H history.
        games = [_game("2025-06-20", HOME, AWAY, 6, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["games_count"] == 0


# ─────────────────────────────────────────────────────────────────────
# 2) H2H Metrics
# ─────────────────────────────────────────────────────────────────────
class TestH2HMetrics:
    def test_spec_example_3_games_15_days(self):
        # Spec example: totals 7, 11, 8 in last 15 days.
        games = [
            _game("2025-06-17", HOME, AWAY, 4, 3),  # total 7
            _game("2025-06-18", HOME, AWAY, 6, 5),  # total 11
            _game("2025-06-19", HOME, AWAY, 5, 3),  # total 8
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        m = out["metrics"]
        assert m["avg_total_runs_h2h"] == round(26 / 3, 2)
        assert m["max_total_runs_h2h"] == 11
        assert m["min_total_runs_h2h"] == 7
        # Spec: over_8_5 = 33%  (only "11" beats 8.5).
        assert m["over_rate_by_line"]["over_8_5"] == pytest.approx(1 / 3, abs=0.01)
        # Spec: under_9_5 = 66%  (7 and 8 are below 9.5).
        assert m["under_rate_by_line"]["under_9_5"] == pytest.approx(2 / 3, abs=0.01)

    def test_median_with_even_count(self):
        games = [
            _game("2025-06-18", HOME, AWAY, 1, 1),  # 2
            _game("2025-06-19", HOME, AWAY, 5, 5),  # 10
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["metrics"]["median_total_runs_h2h"] == 6.0

    def test_home_away_alignment_when_teams_swapped(self):
        # Past game had teams swapped: AWAY hosted HOME.
        games = [
            _game("2025-06-19", AWAY, HOME, 3, 8),  # AWAY scored 3, HOME 8.
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        m = out["metrics"]
        # Re-aligned: HOME's runs scored = 8, AWAY's = 3.
        assert m["avg_home_runs_scored"] == 8.0
        assert m["avg_away_runs_scored"] == 3.0
        assert m["home_win_rate_h2h"] == 1.0
        assert m["away_win_rate_h2h"] == 0.0

    def test_runline_cover_rates(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 5, 1),  # home wins by 4 → covers -1.5
            _game("2025-06-18", HOME, AWAY, 4, 3),  # home wins by 1 → NO cover -1.5
            _game("2025-06-19", HOME, AWAY, 2, 5),  # away wins by 3 → covers -1.5
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        m = out["metrics"]
        # Home covered -1.5 in 1 of 3.
        assert m["runline_cover_rate_home_minus_1_5"] == pytest.approx(1 / 3, abs=0.01)
        # Away covered -1.5 in 1 of 3.
        assert m["runline_cover_rate_away_minus_1_5"] == pytest.approx(1 / 3, abs=0.01)


# ─────────────────────────────────────────────────────────────────────
# 3) Familiarity score + buckets
# ─────────────────────────────────────────────────────────────────────
class TestFamiliarityScore:
    def test_no_recent_h2h_bucket_none(self):
        out = calculate_matchup_familiarity_overlay(_ctx([]))
        assert out["bucket"] == BUCKET_NONE
        assert out["familiarity_score"] == 0.0

    def test_one_game_in_15_days_bucket_medium(self):
        games = [_game("2025-06-10", HOME, AWAY, 6, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["bucket"] == BUCKET_MEDIUM
        assert out["familiarity_score"] >= 30.0

    def test_two_games_in_15_days_bucket_medium(self):
        games = [
            _game("2025-06-12", HOME, AWAY, 6, 3),
            _game("2025-06-15", HOME, AWAY, 4, 2),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["bucket"] == BUCKET_MEDIUM
        assert out["familiarity_score"] >= 50.0

    def test_three_plus_games_bucket_high(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 5, 3),
            _game("2025-06-18", HOME, AWAY, 4, 6),
            _game("2025-06-19", HOME, AWAY, 7, 2),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["bucket"] == BUCKET_HIGH
        assert out["familiarity_score"] >= 80.0

    def test_active_series_consecutive_boost(self):
        games = [
            _game("2025-06-18", HOME, AWAY, 5, 3),
            _game("2025-06-19", HOME, AWAY, 4, 6),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert "ACTIVE_SERIES_CONSECUTIVE" in out["drivers"]
        assert "PLAYED_LAST_3_DAYS" in out["drivers"]

    def test_only_old_games_bucket_low(self):
        games = [_game("2025-05-30", HOME, AWAY, 6, 3)]  # >16d
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["bucket"] == BUCKET_LOW
        assert out["familiarity_score"] > 0
        assert "OLDER_H2H_ONLY" in out["drivers"]

    def test_bullpen_exposed_recent_boost(self):
        games = [
            _game("2025-06-18", HOME, AWAY, 5, 3,
                  bullpen_pitch_count_home=85),
            _game("2025-06-19", HOME, AWAY, 4, 6),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert "BULLPEN_EXPOSED_RECENT" in out["drivers"]

    def test_same_starter_recent_boost(self):
        games = [
            _game("2025-06-18", HOME, AWAY, 5, 3, starter_home="G. Cole"),
        ]
        ctx = _ctx(games)
        ctx["starter_info"] = {"home_starter": "G. Cole"}
        out = calculate_matchup_familiarity_overlay(ctx)
        assert "SAME_STARTER_RECENT_HOME" in out["drivers"]


# ─────────────────────────────────────────────────────────────────────
# 4) Totals overlay — OVER triggers
# ─────────────────────────────────────────────────────────────────────
class TestTotalsOverlayOver:
    def test_avg_runs_above_line_plus_1_over_2pts(self):
        # avg = 10.0, line = 8.5 → 10 >= 9.5 → +2 OVER
        games = [
            _game("2025-06-17", HOME, AWAY, 6, 4),
            _game("2025-06-18", HOME, AWAY, 7, 3),
            _game("2025-06-19", HOME, AWAY, 5, 5),
        ]
        out = calculate_matchup_familiarity_overlay(
            _ctx(games, line=8.5, side="OVER"),
        )
        ov = out["totals_overlay"]
        assert "H2H_AVG_RUNS_SUPPORTS_OVER" in ov["reason_codes"]
        assert ov["lean"] == LEAN_OVER
        assert ov["points"] > 0

    def test_recent_over_rate_high_2pts(self):
        # 3 games with totals 10, 12, 11 → over_8_5 = 100% ≥ 0.65 → +2 OVER
        games = [
            _game("2025-06-17", HOME, AWAY, 6, 4),
            _game("2025-06-18", HOME, AWAY, 6, 6),
            _game("2025-06-19", HOME, AWAY, 7, 4),
        ]
        out = calculate_matchup_familiarity_overlay(
            _ctx(games, line=8.5),
        )
        ov = out["totals_overlay"]
        assert "RECENT_H2H_OVER_RATE_HIGH" in ov["reason_codes"]

    def test_multiple_high_run_games_2pts(self):
        # 2 games with ≥10 runs.
        games = [
            _game("2025-06-17", HOME, AWAY, 6, 5),   # 11
            _game("2025-06-18", HOME, AWAY, 8, 3),   # 11
            _game("2025-06-19", HOME, AWAY, 2, 1),   # 3
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=9.5))
        assert "MULTIPLE_HIGH_RUN_GAMES_RECENT" in out["totals_overlay"]["reason_codes"]

    def test_bullpen_exposed_in_recent_series_1pt(self):
        games = [
            _game("2025-06-19", HOME, AWAY, 4, 3,
                  bullpen_pitch_count_home=70),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=8.5))
        assert "BULLPEN_EXPOSED_IN_RECENT_SERIES" in out["totals_overlay"]["reason_codes"]

    def test_offensive_adaptation_over_1pt(self):
        # Both teams ≥4 in 2 recent games.
        games = [
            _game("2025-06-18", HOME, AWAY, 4, 5),
            _game("2025-06-19", HOME, AWAY, 6, 4),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=9.5))
        # With avg = 9.5, NOT ≥ 9.5+1, so we only get the adaptation boost.
        assert "SERIES_OFFENSIVE_ADAPTATION_OVER" in out["totals_overlay"]["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 4) Totals overlay — UNDER triggers
# ─────────────────────────────────────────────────────────────────────
class TestTotalsOverlayUnder:
    def test_avg_runs_below_line_minus_1_under_2pts(self):
        # avg = 5.0, line = 8.5 → ≤ 7.5 → +2 UNDER
        games = [
            _game("2025-06-17", HOME, AWAY, 2, 2),  # 4
            _game("2025-06-18", HOME, AWAY, 3, 1),  # 4
            _game("2025-06-19", HOME, AWAY, 4, 3),  # 7
        ]
        out = calculate_matchup_familiarity_overlay(
            _ctx(games, line=8.5, side="UNDER"),
        )
        ov = out["totals_overlay"]
        assert "H2H_AVG_RUNS_SUPPORTS_UNDER" in ov["reason_codes"]
        assert ov["lean"] == LEAN_UNDER

    def test_recent_under_rate_high_2pts(self):
        # under_9_5 = 100%.
        games = [
            _game("2025-06-17", HOME, AWAY, 2, 1),  # 3
            _game("2025-06-18", HOME, AWAY, 3, 2),  # 5
            _game("2025-06-19", HOME, AWAY, 4, 3),  # 7
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=9.5))
        assert "RECENT_H2H_UNDER_RATE_HIGH" in out["totals_overlay"]["reason_codes"]

    def test_multiple_low_run_games_2pts(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 2, 1),  # 3
            _game("2025-06-18", HOME, AWAY, 3, 2),  # 5
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=8.5))
        assert "MULTIPLE_LOW_RUN_GAMES_RECENT" in out["totals_overlay"]["reason_codes"]

    def test_series_low_traffic_1pt(self):
        # Both teams ≤3 in 2+ games.
        games = [
            _game("2025-06-18", HOME, AWAY, 2, 1),
            _game("2025-06-19", HOME, AWAY, 3, 3),
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=8.5))
        assert "SERIES_LOW_TRAFFIC_UNDER" in out["totals_overlay"]["reason_codes"]

    def test_familiarity_suppresses_offense_1pt(self):
        # avg margin small AND avg runs ≤ line - 0.5.
        games = [
            _game("2025-06-18", HOME, AWAY, 3, 2),  # margin 1, total 5
            _game("2025-06-19", HOME, AWAY, 4, 3),  # margin 1, total 7
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=8.5))
        assert "FAMILIARITY_SUPPRESSES_OFFENSE" in out["totals_overlay"]["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Safety — clamping ±5
# ─────────────────────────────────────────────────────────────────────
class TestOverlayClamping:
    def test_clamp_max_5_over(self):
        # Stack ALL OVER triggers: avg >> line, over_rate 100%,
        # multi-high, bullpen, adaptation = potential 10+ pts before clamp.
        games = [
            _game("2025-06-17", HOME, AWAY, 8, 6,
                  bullpen_pitch_count_home=80),  # 14
            _game("2025-06-18", HOME, AWAY, 7, 7),  # 14
            _game("2025-06-19", HOME, AWAY, 6, 5),  # 11
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=7.5))
        assert out["totals_overlay"]["points"] <= MAX_OVERLAY_POINTS
        assert out["totals_overlay"]["points"] >= 4.0  # well above 0

    def test_clamp_max_5_under(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 1, 1),  # 2
            _game("2025-06-18", HOME, AWAY, 2, 2),  # 4
            _game("2025-06-19", HOME, AWAY, 3, 2),  # 5
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=10.5))
        assert out["totals_overlay"]["points"] >= -MAX_OVERLAY_POINTS

    def test_non_total_market_neutral_overlay(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 4, 4),
            _game("2025-06-18", HOME, AWAY, 5, 5),
        ]
        out = calculate_matchup_familiarity_overlay(
            _ctx(games, market="MONEYLINE", side="HOME"),
        )
        assert out["totals_overlay"]["points"] == 0.0
        assert out["totals_overlay"]["lean"] == LEAN_NEUTRAL

    def test_runline_market_neutral_overlay(self):
        games = [_game("2025-06-19", HOME, AWAY, 3, 5)]
        out = calculate_matchup_familiarity_overlay(
            _ctx(games, market="RUNLINE", side="HOME_RL"),
        )
        assert out["totals_overlay"]["points"] == 0.0

    def test_no_line_no_line_dependent_triggers(self):
        games = [_game("2025-06-19", HOME, AWAY, 8, 5)]
        out = calculate_matchup_familiarity_overlay(_ctx(games, line=None))
        # Without a line, we can't fire the "≥ line+1" rule, but
        # other rules (high-run games count, etc.) can still trigger.
        ov = out["totals_overlay"]
        assert "H2H_AVG_RUNS_SUPPORTS_OVER" not in ov["reason_codes"]
        assert "H2H_AVG_RUNS_SUPPORTS_UNDER" not in ov["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Confidence
# ─────────────────────────────────────────────────────────────────────
class TestConfidence:
    def test_three_games_max_confidence(self):
        games = [
            _game("2025-06-17", HOME, AWAY, 4, 3),
            _game("2025-06-18", HOME, AWAY, 5, 2),
            _game("2025-06-19", HOME, AWAY, 6, 4),
        ]
        ctx = _ctx(games)
        ctx["bullpen_usage"] = {"home": {}, "away": {}}
        ctx["starter_info"] = {"home_starter": "X"}
        ctx["lineups"] = {"home": {"batters": []}, "away": {"batters": []}}
        out = calculate_matchup_familiarity_overlay(ctx)
        assert out["confidence"] >= 60.0

    def test_missing_data_reduces_confidence(self):
        games = [_game("2025-06-19", HOME, AWAY, 5, 3)]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        # Single game = base 35, with multiple missing fields → ~0-14.
        assert out["confidence"] < 35.0

    def test_zero_games_zero_confidence(self):
        out = calculate_matchup_familiarity_overlay(_ctx([]))
        assert out["confidence"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# Input shape compat (games_detail from mlb_active_series_analyzer)
# ─────────────────────────────────────────────────────────────────────
class TestInputCompat:
    def test_games_detail_shape_works(self):
        # games_detail uses 'home', 'away', 'kickoff', 'total_runs'.
        games = [
            {
                "game_number":   1,
                "home":          6,
                "away":          3,
                "home_team":     HOME,
                "away_team":     AWAY,
                "total_runs":    9,
                "kickoff":       "2025-06-18T19:05:00",
                "summary":       "G1: ...",
            },
            {
                "game_number":   2,
                "home":          4,
                "away":          5,
                "home_team":     HOME,
                "away_team":     AWAY,
                "total_runs":    9,
                "kickoff":       "2025-06-19T19:05:00",
                "summary":       "G2: ...",
            },
        ]
        out = calculate_matchup_familiarity_overlay(_ctx(games))
        assert out["games_count"] == 2
        assert out["bucket"] == BUCKET_MEDIUM
        assert out["metrics"]["avg_total_runs_h2h"] == 9.0


# ─────────────────────────────────────────────────────────────────────
# E2E spec example
# ─────────────────────────────────────────────────────────────────────
class TestSpecExampleEndToEnd:
    def test_spec_example_under_line_9_5(self):
        # Spec: 3 juegos en 15 días con totales 7, 11, 8
        # → avg = 8.67
        # → over_8_5 = 33% (no triggera over_rate ≥ 0.65)
        # → under_9_5 = 66% ≥ 0.65 (UNDER trigger)
        games = [
            _game("2025-06-17", HOME, AWAY, 4, 3),  # 7
            _game("2025-06-18", HOME, AWAY, 6, 5),  # 11
            _game("2025-06-19", HOME, AWAY, 5, 3),  # 8
        ]
        out = calculate_matchup_familiarity_overlay(
            _ctx(games, line=9.5, side="UNDER"),
        )
        m = out["metrics"]
        assert m["avg_total_runs_h2h"] == 8.67
        assert m["over_rate_by_line"]["over_8_5"] == pytest.approx(0.3333, abs=0.01)
        assert m["under_rate_by_line"]["under_9_5"] == pytest.approx(0.6667, abs=0.01)
        # Bucket HIGH because 3 games in 15 days.
        assert out["bucket"] == BUCKET_HIGH
        # Overlay should lean UNDER.
        assert out["totals_overlay"]["lean"] == LEAN_UNDER
        assert "RECENT_H2H_UNDER_RATE_HIGH" in out["totals_overlay"]["reason_codes"]
