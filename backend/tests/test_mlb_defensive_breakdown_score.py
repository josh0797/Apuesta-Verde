"""Unit tests for services.mlb_defensive_breakdown_score — Phase 50."""
from __future__ import annotations

from services.mlb_defensive_breakdown_score import (
    BUCKET_HIGH, BUCKET_LOW, BUCKET_MEDIUM,
    COMPONENT_WEIGHTS,
    RC_BULLPEN_TRAFFIC_DEFENSE_EXPLOSION_RISK,
    RC_DEFENSIVE_MELTDOWN_RISK,
    RC_LIVE_ERRORS_RAISE_RUN_RISK,
    RC_PASSED_BALL_PRESSURE,
    RC_POOR_FIELDING_PROFILE,
    RC_STOLEN_BASE_DEFENSIVE_FAILURE,
    RC_UNEARNED_RUN_RISK,
    RC_WILD_PITCH_PRESSURE,
    classify_combined_explosion_risk,
    compute_defensive_breakdown_score,
)


class TestLiveMode:
    def test_multiple_errors_raise_score(self):
        out = compute_defensive_breakdown_score(
            mode="live", live_errors=3, runners_advanced_on_errors=2,
            live_wild_pitches=1, live_stolen_bases=2,
        )
        assert out["defensive_breakdown_score"] >= 25
        assert RC_LIVE_ERRORS_RAISE_RUN_RISK in out["reason_codes"]
        assert RC_STOLEN_BASE_DEFENSIVE_FAILURE in out["reason_codes"]
        assert out["components"]["errors"] > 0

    def test_passed_balls_and_wild_pitches_raise_risk(self):
        out = compute_defensive_breakdown_score(
            mode="live", live_passed_balls=2, live_wild_pitches=2,
        )
        assert RC_PASSED_BALL_PRESSURE in out["reason_codes"]
        assert RC_WILD_PITCH_PRESSURE in out["reason_codes"]
        assert out["components"]["passed_balls"] >= 6
        assert out["components"]["wild_pitches"] >= 6

    def test_unearned_runs_routed_into_fielding_penalty(self):
        out = compute_defensive_breakdown_score(mode="live", unearned_runs=2)
        assert RC_UNEARNED_RUN_RISK in out["reason_codes"]
        assert out["components"]["fielding_pct_penalty"] >= 8

    def test_extreme_inputs_respect_25_pct_cap(self):
        out = compute_defensive_breakdown_score(
            mode="live", live_errors=20, live_passed_balls=10,
            live_wild_pitches=10, live_stolen_bases=10,
            live_catcher_mistakes=10, unearned_runs=10,
            runners_advanced_on_errors=10,
        )
        for name, pts in out["components"].items():
            assert pts <= COMPONENT_WEIGHTS[name]
            assert pts <= 25, f"{name}={pts} exceeds 25% cap"

    def test_clean_live_yields_low_bucket(self):
        out = compute_defensive_breakdown_score(mode="live")
        assert out["defensive_breakdown_score"] == 0
        assert out["defensive_bucket"] == BUCKET_LOW


class TestPregameMode:
    def test_poor_fielding_profile_raises_score(self):
        out = compute_defensive_breakdown_score(
            mode="pregame", fielding_pct=0.975, errors_per_game=0.95,
            drs=-12, passed_balls_per_game=0.35, sb_allowed_per_game=1.1,
            wp_allowed_per_game=0.65,
        )
        assert out["defensive_breakdown_score"] >= 40
        assert RC_POOR_FIELDING_PROFILE in out["reason_codes"]
        assert out["components"]["fielding_pct_penalty"] > 0
        assert out["components"]["drs_penalty"] > 0

    def test_strong_defense_yields_low_bucket(self):
        out = compute_defensive_breakdown_score(
            mode="pregame", fielding_pct=0.990, errors_per_game=0.40,
            drs=8, oaa=10, passed_balls_per_game=0.10,
            sb_allowed_per_game=0.4, wp_allowed_per_game=0.30,
        )
        assert out["defensive_bucket"] == BUCKET_LOW

    def test_oaa_fallback_when_no_drs(self):
        out = compute_defensive_breakdown_score(
            mode="pregame", fielding_pct=0.978, errors_per_game=0.70,
            oaa=-12,
        )
        # OAA fallback should populate drs_penalty.
        assert out["components"]["drs_penalty"] > 0

    def test_high_meltdown_emits_reason_code(self):
        out = compute_defensive_breakdown_score(
            mode="pregame", fielding_pct=0.972, errors_per_game=1.1,
            drs=-22, passed_balls_per_game=0.45, sb_allowed_per_game=1.5,
            wp_allowed_per_game=0.80,
        )
        assert out["defensive_bucket"] == BUCKET_HIGH
        assert RC_DEFENSIVE_MELTDOWN_RISK in out["reason_codes"]


class TestCombinedExplosionRisk:
    def test_full_trifecta_fires(self):
        out = classify_combined_explosion_risk(
            bullpen_era_7d_max=6.2,
            live_traffic_bucket="HIGH_TRAFFIC",
            defensive_bucket=BUCKET_HIGH,
            is_under_pick=True,
        )
        assert out["verdict"] == "penalize_under"
        assert RC_BULLPEN_TRAFFIC_DEFENSE_EXPLOSION_RISK in out["reason_codes"]
        assert "explosiva" in (out["ui_message_es"] or "").lower()

    def test_missing_defense_does_not_fire(self):
        out = classify_combined_explosion_risk(
            bullpen_era_7d_max=6.2,
            live_traffic_bucket="HIGH_TRAFFIC",
            defensive_bucket=BUCKET_LOW,
            is_under_pick=True,
        )
        assert out["verdict"] == "no_signal"
        assert out["reason_codes"] == []

    def test_medium_defense_still_fires(self):
        out = classify_combined_explosion_risk(
            bullpen_era_7d_max=5.8,
            live_traffic_bucket="HIGH_TRAFFIC",
            defensive_bucket=BUCKET_MEDIUM,
            is_under_pick=True,
        )
        assert out["verdict"] == "penalize_under"

    def test_does_not_fire_for_over_picks(self):
        out = classify_combined_explosion_risk(
            bullpen_era_7d_max=6.2,
            live_traffic_bucket="HIGH_TRAFFIC",
            defensive_bucket=BUCKET_HIGH,
            is_under_pick=False,
        )
        # Trifecta still recorded but verdict only modifies Under picks.
        assert out["verdict"] == "no_signal"
        assert RC_BULLPEN_TRAFFIC_DEFENSE_EXPLOSION_RISK in out["reason_codes"]

    def test_high_defense_alone_does_not_flip_polarity(self):
        # Defense breakdown alone — no bullpen / no traffic — must NOT
        # produce a penalize verdict.
        out = classify_combined_explosion_risk(
            bullpen_era_7d_max=3.5,
            live_traffic_bucket="LOW_TRAFFIC",
            defensive_bucket=BUCKET_HIGH,
            is_under_pick=True,
        )
        assert out["verdict"] == "no_signal"
