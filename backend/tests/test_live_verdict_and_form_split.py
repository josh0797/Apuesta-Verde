"""Tests for the new live_verdict + final_settled promotion + recent_form
split with HR/BB/hits deltas (2026-06 batch).
"""
from services.live_pre_match_comparison import (
    compare_pregame_vs_live,
    _derive_live_verdict,
)
from services.mlb_recent_form_split import (
    build_recent_form_payload,
    _classify_run_trend,
    _classify_on_base_trend,
)


# ── _derive_live_verdict ────────────────────────────────────────────────
class TestLiveVerdict:
    def test_already_lost_returns_pick_already_lost(self):
        out = _derive_live_verdict(
            pregame_status="already_lost", script_status="broken_script",
            market="Under 9.5", actual_total=13, expected_through=7.4,
            is_final=True,
        )
        assert out == "PICK_ALREADY_LOST"

    def test_already_won_returns_pick_already_won(self):
        out = _derive_live_verdict(
            pregame_status="already_won", script_status="on_script",
            market="Over 7.5", actual_total=10, expected_through=8.0,
            is_final=True,
        )
        assert out == "PICK_ALREADY_WON"

    def test_under_pick_with_broken_script_above_expected_pivots_over(self):
        out = _derive_live_verdict(
            pregame_status="still_playable", script_status="broken_script",
            market="Under 9.5", actual_total=11, expected_through=6.0,
            is_final=False,
        )
        assert out == "AVOID_UNDER_OR_LOOK_OVER"

    def test_over_pick_with_broken_script_below_expected_pivots_cashout(self):
        out = _derive_live_verdict(
            pregame_status="still_playable", script_status="broken_script",
            market="Over 8.5", actual_total=2, expected_through=7.0,
            is_final=False,
        )
        assert out == "AVOID_OVER_OR_CASHOUT"

    def test_on_script_returns_maintain(self):
        out = _derive_live_verdict(
            pregame_status="still_playable", script_status="on_script",
            market="Under 9.5", actual_total=5, expected_through=5.5,
            is_final=False,
        )
        assert out == "MAINTAIN"

    def test_soft_deviation_returns_cashout(self):
        out = _derive_live_verdict(
            pregame_status="still_playable", script_status="soft_deviation",
            market="Over 8.5", actual_total=6, expected_through=4.0,
            is_final=False,
        )
        assert out == "CASHOUT"

    def test_final_without_resolution_returns_no_actionable(self):
        out = _derive_live_verdict(
            pregame_status="not_actionable", script_status="final_settled",
            market="F5 Under 4.5", actual_total=9, expected_through=None,
            is_final=True,
        )
        assert out == "NO_ACTIONABLE"


# ── compare_pregame_vs_live: final_settled promotion ────────────────────
class TestCompareFinalSettled:
    def test_final_game_with_score_promotes_to_final_settled(self):
        """User-reported: Minnesota 6-4 final with pick UNDER 9.5 was
        showing 'Datos live insuficientes' because period_n was None.
        Now should report final_settled + already_lost + verdict."""
        out = compare_pregame_vs_live(
            pregame_pick={
                "recommendation": {"market": "Under 9.5", "selection": "Under 9.5"},
                "_mlb_script_v2": {"expectedRuns": 7.2},
            },
            live_state={
                "is_live": False,
                "status": "final",
                "score": {"home": 6, "away": 4},
                "inning": {"number": None, "half": None},
            },
            sport="baseball",
        )
        # No longer insufficient_data.
        assert out["script_status"] == "final_settled"
        assert out["pregame_pick_status"] == "already_lost"
        assert out["live_verdict"] == "PICK_ALREADY_LOST"
        # Live data extracted from the live_state.
        assert out["live_data"]["total_runs"] == 10
        assert out["live_data"]["is_final"] is True

    def test_truly_insufficient_keeps_insufficient_status(self):
        """No score and no useful info → still insufficient_data."""
        out = compare_pregame_vs_live(
            pregame_pick={
                "recommendation": {"market": "Under 9.5"},
                "_mlb_script_v2": {"expectedRuns": 7.2},
            },
            live_state={"is_live": False, "status": "scheduled"},
            sport="baseball",
        )
        assert out["script_status"] == "insufficient_data"

    def test_under_already_lost_yankees_vs_cleveland_real_case(self):
        """Real user case: Yankees 4 - Cleveland 9, 9th inning, Under 9.5
        pregame. Should resolve as broken_script + already_lost + the
        AVOID_UNDER_OR_LOOK_OVER pivot."""
        out = compare_pregame_vs_live(
            pregame_pick={
                "recommendation": {"market": "Under 9.5", "selection": "Under 9.5"},
                "_mlb_script_v2": {"expectedRuns": 7.4},
            },
            live_state={
                "is_live": True,
                "status": "live",
                "score": {"home": 4, "away": 9},
                "inning": {"number": 9, "half": "bottom"},
            },
            sport="baseball",
        )
        assert out["pregame_pick_status"] == "already_lost"
        assert out["live_verdict"] == "PICK_ALREADY_LOST"


# ── recent_form_split: HR + deltas ──────────────────────────────────────
class TestRecentFormSplitHRDeltas:
    def test_payload_exposes_hr_and_deltas(self):
        home_form = {
            "team_id": 1,
            "runs_scored_avg_last_5": 5.6, "runs_scored_avg_last_15": 4.4,
            "hits_avg_last_5": 9.0,  "hits_avg_last_15": 8.0,
            "walks_avg_last_5": 3.2, "walks_avg_last_15": 2.8,
            "hbp_avg_last_5": 0.3,   "hbp_avg_last_15": 0.2,
            "home_runs_avg_last_5": 1.8, "home_runs_avg_last_15": 1.1,
            "times_on_base_avg_last_5": 12.5, "times_on_base_avg_last_15": 11.0,
            "obp_last_5": None, "obp_last_15": None,
            "games_played_last_5": 5, "games_played_last_15": 15,
        }
        away_form = dict(home_form, team_id=2,
                         runs_scored_avg_last_5=3.0,
                         runs_scored_avg_last_15=4.0,
                         home_runs_avg_last_5=0.4,
                         home_runs_avg_last_15=0.9)

        payload = build_recent_form_payload(home_form, away_form)
        home_ob = payload["on_base_profile"]["home"]
        assert home_ob["hits_avg_last_5"] == 9.0
        assert home_ob["hits_delta_5_vs_15"] == 1.0
        assert home_ob["walks_delta_5_vs_15"] == 0.4
        assert home_ob["hbp_delta_5_vs_15"] == 0.1
        assert home_ob["home_runs_avg_last_5"] == 1.8
        assert home_ob["home_runs_avg_last_15"] == 1.1
        assert home_ob["home_runs_delta_5_vs_15"] == 0.7

    def test_run_trend_classification_thresholds(self):
        assert _classify_run_trend(2.5)   == "RISING_RUN_ENVIRONMENT"
        assert _classify_run_trend(1.25)  == "RISING_RUN_ENVIRONMENT"
        assert _classify_run_trend(0.0)   == "STABLE_RUN_ENVIRONMENT"
        assert _classify_run_trend(-1.5)  == "DECLINING_RUN_ENVIRONMENT"
        assert _classify_run_trend(None)  == "UNKNOWN_RUN_ENVIRONMENT"

    def test_on_base_trend_classification_thresholds(self):
        assert _classify_on_base_trend(2.0)   == "RISING_ON_BASE_PRESSURE"
        assert _classify_on_base_trend(0.0)   == "STABLE_ON_BASE_PRESSURE"
        assert _classify_on_base_trend(-1.5)  == "DECLINING_ON_BASE_PRESSURE"
        assert _classify_on_base_trend(None)  == "UNKNOWN_ON_BASE_PRESSURE"
