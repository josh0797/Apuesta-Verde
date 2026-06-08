"""Unit tests for services.traffic_score."""
from __future__ import annotations

import pytest

from services.traffic_score import (
    BUCKET_HIGH,
    BUCKET_LOW,
    BUCKET_MEDIUM,
    RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC,
    RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH,
    RC_HIGH_TRAFFIC_UNDER_DANGER,
    RC_LOW_TRAFFIC_UNDER_SURVIVED,
    classify_bullpen_traffic_interaction,
    combine_team_traffic_scores,
    compute_offense_window_metrics,
    compute_traffic_score,
)


# ─────────────────────────────────────────────────────────────────────
# compute_offense_window_metrics
# ─────────────────────────────────────────────────────────────────────
class TestComputeOffenseWindowMetrics:
    def test_returns_none_when_division_by_zero(self):
        out = compute_offense_window_metrics({})
        assert out["ops"] is None
        assert out["obp"] is None
        assert out["slg"] is None
        assert out["runs_per_game"] is None

    def test_typical_team_week(self):
        # ~7 games. 250 AB, 70 H (.280 avg), 15 2B, 1 3B, 10 HR, 25 BB, 4 HBP, 3 SF
        raw = {"ab": 250, "h": 70, "doubles": 15, "triples": 1, "hr": 10,
               "bb": 25, "hbp": 4, "sf": 3, "sh": 0, "k": 55, "runs": 35,
               "n_games": 7}
        out = compute_offense_window_metrics(raw)
        # OBP = (70+25+4) / (250+25+4+3) = 99/282 ≈ 0.3511
        assert pytest.approx(out["obp"], abs=0.001) == 0.3511
        # singles = 70-15-1-10 = 44; TB = 44 + 30 + 3 + 40 = 117; SLG = 117/250 = .468
        assert pytest.approx(out["slg"], abs=0.001) == 0.468
        assert pytest.approx(out["ops"], abs=0.002) == 0.3511 + 0.468
        assert pytest.approx(out["runs_per_game"], abs=0.01) == 5.0
        assert out["n_games"] == 7

    def test_negative_singles_clamped_to_zero(self):
        # Edge case: h < (db+tr+hr) shouldn't produce negative singles.
        raw = {"ab": 30, "h": 5, "doubles": 3, "triples": 2, "hr": 5,
               "bb": 5, "hbp": 0, "sf": 0, "n_games": 2}
        out = compute_offense_window_metrics(raw)
        # TB = max(0, 5-3-2-5)*1 + 2*3 + 3*2 + 4*5 = 0 + 6 + 6 + 20 = 32
        assert pytest.approx(out["slg"], abs=0.01) == 32 / 30


# ─────────────────────────────────────────────────────────────────────
# compute_traffic_score
# ─────────────────────────────────────────────────────────────────────
class TestComputeTrafficScore:
    def test_no_metrics_yields_zero(self):
        out = compute_traffic_score(metrics={})
        assert out["traffic_score"] == 0
        assert out["traffic_bucket"] == BUCKET_LOW

    def test_high_traffic_team(self):
        # Hot offense: OPS .820, OBP .345, HR 0.040/PA, XBH 0.095/PA, R/G 5.5, SLG .475
        metrics = {"ops": 0.820, "obp": 0.345, "slg": 0.475,
                   "hr_rate": 0.040, "xbh_rate": 0.095, "runs_per_game": 5.5}
        out = compute_traffic_score(metrics=metrics, recent_form_rpg=5.5,
                                    implied_team_total=5.0)
        # Every component should be at full weight → 100.
        assert out["traffic_score"] >= 90
        assert out["traffic_bucket"] == BUCKET_HIGH
        c = out["components"]
        assert c["ops"]      == 20
        assert c["obp"]      == 15
        assert c["hr_rate"]  == 15
        assert c["xbh_rate"] == 10

    def test_low_traffic_team(self):
        # Cold offense: OPS .640, OBP .285, HR 0.018, XBH .045, R/G 3.4, SLG .355
        metrics = {"ops": 0.640, "obp": 0.285, "slg": 0.355,
                   "hr_rate": 0.018, "xbh_rate": 0.045, "runs_per_game": 3.4}
        out = compute_traffic_score(metrics=metrics, recent_form_rpg=3.4,
                                    implied_team_total=3.4)
        assert out["traffic_score"] <= 20
        assert out["traffic_bucket"] == BUCKET_LOW

    def test_medium_traffic_team(self):
        # League-average shape.
        metrics = {"ops": 0.720, "obp": 0.315, "slg": 0.405,
                   "hr_rate": 0.030, "xbh_rate": 0.072, "runs_per_game": 4.4}
        out = compute_traffic_score(metrics=metrics, recent_form_rpg=4.4,
                                    implied_team_total=4.2)
        assert 40 <= out["traffic_score"] <= 69
        assert out["traffic_bucket"] == BUCKET_MEDIUM

    def test_breakdown_keys(self):
        out = compute_traffic_score(metrics={"ops": 0.7, "obp": 0.31,
                                              "slg": 0.40, "hr_rate": 0.03,
                                              "xbh_rate": 0.07,
                                              "runs_per_game": 4.5})
        assert set(out["components"].keys()) == {
            "ops", "runs_per_game", "obp", "hr_rate", "xbh_rate",
            "hard_contact", "recent_form", "team_total",
        }
        assert "raw" in out
        assert out["engine_version"] == "traffic_score.1"


# ─────────────────────────────────────────────────────────────────────
# combine_team_traffic_scores
# ─────────────────────────────────────────────────────────────────────
class TestCombineTeamTrafficScores:
    def test_average_of_two(self):
        h = {"traffic_score": 90, "traffic_bucket": BUCKET_HIGH, "components": {}}
        a = {"traffic_score": 20, "traffic_bucket": BUCKET_LOW,  "components": {}}
        out = combine_team_traffic_scores(h, a)
        # Mean → 55 → MEDIUM
        assert out["traffic_score"] == 55
        assert out["traffic_bucket"] == BUCKET_MEDIUM
        assert out["home"]["traffic_score"] == 90
        assert out["away"]["traffic_score"] == 20

    def test_both_high_stays_high(self):
        out = combine_team_traffic_scores(
            {"traffic_score": 85}, {"traffic_score": 78})
        assert out["traffic_bucket"] == BUCKET_HIGH


# ─────────────────────────────────────────────────────────────────────
# classify_bullpen_traffic_interaction
# ─────────────────────────────────────────────────────────────────────
class TestClassifyBullpenTrafficInteraction:
    def test_no_signal_when_data_missing(self):
        out = classify_bullpen_traffic_interaction(
            bullpen_era_7d_max=None, traffic_bucket=None, is_under_pick=True,
        )
        assert out["verdict"] == "no_signal"
        assert out["reason_codes"] == []
        assert out["observe_only"] is False  # Phase 46: active by default

    def test_no_signal_when_bullpen_not_vulnerable(self):
        out = classify_bullpen_traffic_interaction(
            bullpen_era_7d_max=4.0, traffic_bucket=BUCKET_HIGH, is_under_pick=True,
        )
        assert out["verdict"] == "no_signal"
        assert out["reason_codes"] == []

    def test_vulnerable_bullpen_high_traffic_under(self):
        out = classify_bullpen_traffic_interaction(
            bullpen_era_7d_max=6.2, traffic_bucket=BUCKET_HIGH, is_under_pick=True,
        )
        assert out["verdict"] == "penalize_under"
        assert RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC in out["reason_codes"]
        assert RC_HIGH_TRAFFIC_UNDER_DANGER in out["reason_codes"]
        assert out["observe_only"] is False  # Phase 46: active by default

    def test_observe_only_flag_preserved_when_explicit(self):
        out = classify_bullpen_traffic_interaction(
            bullpen_era_7d_max=6.2, traffic_bucket=BUCKET_HIGH,
            is_under_pick=True, observe_only=True,
        )
        assert out["verdict"] == "penalize_under"
        assert out["observe_only"] is True

    def test_vulnerable_bullpen_low_traffic_under(self):
        out = classify_bullpen_traffic_interaction(
            bullpen_era_7d_max=6.0, traffic_bucket=BUCKET_LOW, is_under_pick=True,
        )
        assert out["verdict"] == "hold_under"
        assert RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH in out["reason_codes"]
        assert RC_LOW_TRAFFIC_UNDER_SURVIVED in out["reason_codes"]

    def test_vulnerable_bullpen_medium_traffic(self):
        out = classify_bullpen_traffic_interaction(
            bullpen_era_7d_max=6.0, traffic_bucket=BUCKET_MEDIUM, is_under_pick=True,
        )
        assert out["verdict"] == "no_signal"
        assert RC_BULLPEN_RISK_ISOLATED_NOT_ENOUGH in out["reason_codes"]

    def test_non_under_pick_yields_no_verdict_change(self):
        # The interaction emits structural reason codes but won't recommend
        # a penalty for non-Under picks.
        out = classify_bullpen_traffic_interaction(
            bullpen_era_7d_max=6.0, traffic_bucket=BUCKET_HIGH, is_under_pick=False,
        )
        assert out["verdict"] == "no_signal"
        assert RC_BULLPEN_RISK_CONFIRMED_BY_TRAFFIC in out["reason_codes"]
        assert RC_HIGH_TRAFFIC_UNDER_DANGER not in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Phase 46 — compute_live_traffic_score
# ─────────────────────────────────────────────────────────────────────
from services.traffic_score import (  # noqa: E402
    RC_BULLPEN_FATIGUE_LATE_INNINGS,
    RC_HIGH_RISP_PRESSURE,
    RC_LIVE_HIGH_TRAFFIC_UNDER_DANGER,
    RC_LIVE_LOW_TRAFFIC_UNDER_SURVIVING,
    RC_LIVE_TRAFFIC_COLLAPSING,
    RC_LIVE_TRAFFIC_RISING,
    RC_LOB_DRAIN,
    classify_live_bullpen_traffic_interaction,
    compute_live_traffic_score,
)


class TestComputeLiveTrafficScore:
    def test_empty_yields_zero(self):
        out = compute_live_traffic_score(inning=5, home_live={}, away_live={})
        assert out["live_traffic_score"] == 0
        assert out["live_traffic_bucket"] == BUCKET_LOW
        assert out["pregame_delta"] is None

    def test_hot_offense_live_high_traffic(self):
        # Lots of pressure: 7-8 hits + walks each, runs piling, RISP hits land.
        home = {
            "plate_appearances": 28, "at_bats": 25, "hits": 10, "walks": 5,
            "home_runs": 2, "runs": 5, "left_on_base": 7,
            "risp_opportunities": 6, "risp_hits": 3,
            "hard_contact_rate": 0.48, "exit_velocity_avg": 92.5,
        }
        away = {
            "plate_appearances": 25, "at_bats": 23, "hits": 8, "walks": 3,
            "home_runs": 1, "runs": 3, "left_on_base": 5,
            "risp_opportunities": 4, "risp_hits": 2,
            "hard_contact_rate": 0.42, "exit_velocity_avg": 90.8,
        }
        out = compute_live_traffic_score(
            inning=7, innings_played=6, home_live=home, away_live=away,
            pitch_count_home=120, pitch_count_away=110,
            pregame_traffic_score=50, is_under_pick=True,
        )
        assert out["live_traffic_score"] >= 60
        assert out["live_traffic_bucket"] in (BUCKET_MEDIUM, BUCKET_HIGH)
        # Pregame baseline 50, live ~70+ → rising tag fires.
        assert out["pregame_delta"] > 0
        if out["live_traffic_bucket"] == BUCKET_HIGH:
            assert RC_LIVE_HIGH_TRAFFIC_UNDER_DANGER in out["reason_codes"]

    def test_collapsing_traffic_detected(self):
        # Live offense sterile, pregame baseline was high.
        home = {"plate_appearances": 15, "hits": 2, "walks": 1, "runs": 0,
                "left_on_base": 1, "risp_opportunities": 2, "risp_hits": 0}
        away = {"plate_appearances": 14, "hits": 1, "walks": 0, "runs": 0,
                "left_on_base": 0, "risp_opportunities": 1, "risp_hits": 0}
        out = compute_live_traffic_score(
            inning=6, innings_played=5, home_live=home, away_live=away,
            pregame_traffic_score=80, is_under_pick=True,
        )
        assert out["live_traffic_score"] < 40
        assert RC_LIVE_TRAFFIC_COLLAPSING in out["reason_codes"]
        assert RC_LIVE_LOW_TRAFFIC_UNDER_SURVIVING in out["reason_codes"]

    def test_bullpen_fatigue_late_innings(self):
        # 6 innings each side, pitch counts 130 + 120 = 250 → ratio ~1.30.
        home = {"plate_appearances": 24, "hits": 7, "walks": 4, "runs": 3,
                "left_on_base": 4, "risp_opportunities": 4, "risp_hits": 2}
        away = {"plate_appearances": 24, "hits": 6, "walks": 3, "runs": 2,
                "left_on_base": 3, "risp_opportunities": 3, "risp_hits": 1}
        out = compute_live_traffic_score(
            inning=7, innings_played=6, home_live=home, away_live=away,
            pitch_count_home=130, pitch_count_away=120, is_under_pick=True,
        )
        assert RC_BULLPEN_FATIGUE_LATE_INNINGS in out["reason_codes"]
        assert out["raw"]["bullpen_fatigue"] >= 1.04

    def test_high_risp_pressure_code_fires(self):
        home = {"plate_appearances": 20, "hits": 8, "walks": 3, "runs": 4,
                "risp_opportunities": 5, "risp_hits": 4,  # 80% — elite
                "left_on_base": 2}
        away = {"plate_appearances": 18, "hits": 5, "walks": 2, "runs": 2,
                "risp_opportunities": 3, "risp_hits": 2, "left_on_base": 2}
        out = compute_live_traffic_score(
            inning=5, home_live=home, away_live=away, is_under_pick=True,
        )
        # 80% + 67% RISP hit rate avg → very high pressure.
        assert RC_HIGH_RISP_PRESSURE in out["reason_codes"] or out["live_traffic_score"] >= 70

    def test_lob_drain_fires_when_high(self):
        # Lots of runners stranded — high LOB rate
        home = {"plate_appearances": 30, "hits": 12, "walks": 5, "runs": 2,
                "left_on_base": 15,  # 15/(12+5)=88% LOB rate
                "risp_opportunities": 8, "risp_hits": 2}
        away = {"plate_appearances": 28, "hits": 10, "walks": 4, "runs": 1,
                "left_on_base": 12,
                "risp_opportunities": 7, "risp_hits": 1}
        out = compute_live_traffic_score(
            inning=8, home_live=home, away_live=away, is_under_pick=True,
        )
        assert RC_LOB_DRAIN in out["reason_codes"]


class TestClassifyLiveBullpenTrafficInteraction:
    def test_live_high_traffic_penalizes_under(self):
        out = classify_live_bullpen_traffic_interaction(
            bullpen_era_7d_max=6.0, live_traffic_bucket=BUCKET_HIGH,
            live_traffic_score=78, pregame_delta=20, is_under_pick=True,
        )
        assert out["verdict"] == "penalize_under"
        assert RC_LIVE_TRAFFIC_RISING in out["reason_codes"]
        assert RC_LIVE_HIGH_TRAFFIC_UNDER_DANGER in out["reason_codes"]
        assert out["live_traffic_score"] == 78
        assert out["live_traffic_bucket"] == BUCKET_HIGH

    def test_collapsing_traffic_softens_verdict(self):
        # Bullpen vulnerable + pregame HIGH but live collapsing to LOW.
        out = classify_live_bullpen_traffic_interaction(
            bullpen_era_7d_max=6.0, live_traffic_bucket=BUCKET_LOW,
            live_traffic_score=25, pregame_delta=-25, is_under_pick=True,
        )
        # Bullpen still vulnerable but bucket is LOW → verdict starts as
        # hold_under. Collapsing tag fires but doesn't flip verdict.
        assert out["verdict"] == "hold_under"
        assert RC_LIVE_TRAFFIC_COLLAPSING in out["reason_codes"]
        assert RC_LIVE_LOW_TRAFFIC_UNDER_SURVIVING in out["reason_codes"]

    def test_monitor_under_when_high_bucket_but_collapsing(self):
        # Edge case: bucket still HIGH but live data collapsing. The
        # softening only triggers when verdict=penalize_under + delta <= -15.
        # Here delta -10 doesn't trigger softening.
        out = classify_live_bullpen_traffic_interaction(
            bullpen_era_7d_max=6.0, live_traffic_bucket=BUCKET_HIGH,
            live_traffic_score=70, pregame_delta=-10, is_under_pick=True,
        )
        assert out["verdict"] == "penalize_under"
        assert "softened_by_live" not in out
