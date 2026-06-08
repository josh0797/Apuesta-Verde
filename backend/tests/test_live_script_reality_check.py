"""Tests for services/live_script_reality_check.py (Phase 44 / P3)."""
from services import live_script_reality_check as lsrc


# 1) LIVE_UNDER_CONFIRMATION — exact acceptance criterion
def test_under_confirmation_acceptance_criterion():
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=7.5,
        recommended_market="Under 9.5",
        current_inning=7, current_score_total=2,
        combined_hits=8, combined_walks=3,
        combined_home_runs=0, combined_errors=0,
        combined_left_on_base=6,
    )
    assert out["classification"] == lsrc.CLASS_UNDER_CONFIRMATION
    assert lsrc.RC_LIVE_UNDER_SCRIPT_CONFIRMED in out["reason_codes"]
    assert lsrc.RC_NO_HOME_RUN_SIGNAL in out["reason_codes"]
    assert lsrc.RC_LOW_RUN_CONVERSION in out["reason_codes"]
    assert out["supports_pick"] is True


# 2) LIVE_OVER_WARNING — Under pick + fragility signals
def test_over_warning_high_hits_and_hr():
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=7.5,
        recommended_market="Under 9.5",
        current_inning=5, current_score_total=4,
        combined_hits=13, combined_walks=4,
        combined_home_runs=2, combined_left_on_base=8,
    )
    assert out["classification"] == lsrc.CLASS_OVER_WARNING
    assert lsrc.RC_UNDER_SCRIPT_FRAGILE_LIVE in out["reason_codes"]
    assert lsrc.RC_HIGH_BASE_TRAFFIC in out["reason_codes"]
    assert lsrc.RC_EXPLOSIVE_INNING_RISK in out["reason_codes"]
    assert out["supports_pick"] is False


def test_over_warning_bullpen_in_early():
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=7.5,
        recommended_market="Under 9.5",
        current_inning=4, current_score_total=2,
        combined_hits=7, combined_walks=2,
        combined_home_runs=0, bullpen_usage=0.7,
    )
    assert out["classification"] == lsrc.CLASS_OVER_WARNING
    assert lsrc.RC_BULLPEN_PRESSURE_RISING in out["reason_codes"]


# 3) LIVE_OVER_CONFIRMATION — Over pick + supporting signals
def test_over_confirmation_high_traffic_and_pace():
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=8.5,
        recommended_market="Over 7.5",
        current_inning=6, current_score_total=9,
        combined_hits=14, combined_walks=5,
        combined_home_runs=2, bullpen_usage=0.6,
        projected_final_runs_live=12.0,
    )
    assert out["classification"] == lsrc.CLASS_OVER_CONFIRMATION
    assert lsrc.RC_LIVE_OVER_SCRIPT_CONFIRMED in out["reason_codes"]
    assert lsrc.RC_BASE_TRAFFIC_SUPPORTS_OVER in out["reason_codes"]
    assert lsrc.RC_BULLPEN_ENTRY_SUPPORTS_OVER in out["reason_codes"]
    assert out["supports_pick"] is True


# 4) LIVE_OVER_DANGER — Over user pick + cold game
def test_over_danger_acceptance_criterion():
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=7.5,
        recommended_market="Over 5.5",
        user_market="Over 5.5",
        current_inning=7, current_score_total=2,
        combined_hits=8, combined_walks=2,
        combined_home_runs=0, combined_left_on_base=6,
    )
    assert out["classification"] == lsrc.CLASS_OVER_DANGER
    assert lsrc.RC_OVER_SCRIPT_NOT_MATERIALIZING in out["reason_codes"]
    assert lsrc.RC_NEEDS_BULLPEN_COLLAPSE in out["reason_codes"]
    assert lsrc.RC_STARTERS_DOMINATING in out["reason_codes"]
    assert out["supports_pick"] is False
    assert out["fragility_live"] > 0.5


# Neutral fallback
def test_neutral_when_game_on_track():
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=8.0,
        recommended_market="Under 9.5",
        current_inning=5, current_score_total=4,
        combined_hits=9, combined_walks=2,
        combined_home_runs=0,
    )
    # Not in 7th yet → not Under confirmation. No fragility signals.
    assert out["classification"] in (lsrc.CLASS_NEUTRAL, lsrc.CLASS_UNDER_CONFIRMATION)


# Live projection extrapolation
def test_live_projection_extrapolates_when_missing():
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=8.0,
        recommended_market="Under 9.5",
        current_inning=6, current_score_total=3,
        combined_hits=8, combined_home_runs=0,
    )
    # 3 runs in 6 innings → ~ 4.5 runs at 9.
    assert out["live_projected_final_runs"] == 4.5


# Fail-soft on garbage input
def test_never_raises_on_bad_input():
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=None,
        recommended_market=None,
    )
    assert out["classification"] == lsrc.CLASS_NEUTRAL
    assert out["engine_version"] == lsrc.ENGINE_VERSION


def test_user_market_overrides_engine_pick():
    """Engine recommended Under but user actually placed Over."""
    out = lsrc.evaluate_live_script(
        pre_match_expected_runs=7.5,
        recommended_market="Under 9.5",
        user_market="Over 5.5",
        current_inning=7, current_score_total=2,
        combined_hits=8, combined_home_runs=0,
    )
    # User is Over → activates Over_Danger logic.
    assert out["classification"] == lsrc.CLASS_OVER_DANGER
