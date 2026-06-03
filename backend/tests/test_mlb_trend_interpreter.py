"""Tests for mlb_trend_interpreter.

Covers the user-spec rules:
  - strong/moderate rising/declining on-base pressure → over/under support
  - run-environment delta → adjustment direction
  - HR power spike → over support + risk warning
  - Under/Over/Runline +1.5 score adjustments with proper clamps
  - mixed-signals payload and decision_notes
"""
import pytest

from services.mlb_trend_interpreter import (
    interpret_recent_form,
    combine_trend_signals,
    interpret_run_trend,
    STRONG_OB_RISE, MOD_OB_RISE,
)


def _ob_block(tob_delta, hits_d=0.0, walks_d=0.0, hr_d=0.0, side_trend=None):
    return {
        "times_on_base_delta_5_vs_15": tob_delta,
        "hits_delta_5_vs_15":          hits_d,
        "walks_delta_5_vs_15":         walks_d,
        "home_runs_delta_5_vs_15":     hr_d,
        "trend": side_trend or "STABLE_ON_BASE_PRESSURE",
    }


def _build_profiles(combined_tob_delta, combined_run_delta=0.0,
                    home_tob=0.0, away_tob=0.0,
                    home_hr=0.0, away_hr=0.0):
    return (
        {"total_runs_delta_5_vs_15": combined_run_delta},
        {
            "home": _ob_block(home_tob, hr_d=home_hr),
            "away": _ob_block(away_tob, hr_d=away_hr),
            "combined": {"times_on_base_delta_5_vs_15": combined_tob_delta},
        },
    )


# ── Strength bands ─────────────────────────────────────────────────────
class TestStrengthBands:
    def test_strong_rising_pressure_gives_full_over_support_and_explosive_boost(self):
        rs, ob = _build_profiles(combined_tob_delta=2.5)
        out = interpret_recent_form(rs, ob, selected_market="Over 8.5")
        assert out["pressure_strength"] == "strong_rising"
        assert out["over_support_score"] == 16
        assert out["explosive_risk_boost"] >= 12
        assert out["trend_decision"] == "SUPPORTS_OVER"

    def test_moderate_rising_pressure(self):
        rs, ob = _build_profiles(combined_tob_delta=1.6)
        out = interpret_recent_form(rs, ob, selected_market="Over 8.5")
        assert out["pressure_strength"] == "moderate_rising"
        assert out["over_support_score"] == 10
        assert out["explosive_risk_boost"] >= 8

    def test_strong_declining_pressure_supports_under(self):
        rs, ob = _build_profiles(combined_tob_delta=-2.5)
        out = interpret_recent_form(rs, ob, selected_market="Under 9.5")
        assert out["pressure_strength"] == "strong_declining"
        # under_support clamped to 16 in the returned payload
        assert out["under_support_score"] == 16
        assert out["trend_decision"] == "SUPPORTS_UNDER"

    def test_stable_pressure_neutral(self):
        rs, ob = _build_profiles(combined_tob_delta=0.0)
        out = interpret_recent_form(rs, ob, selected_market="Under 9.5")
        assert out["pressure_strength"] == "stable"
        assert out["trend_decision"] in ("NEUTRAL", "SUPPORTS_UNDER")


# ── Pick / market integration ───────────────────────────────────────────
class TestMarketIntegration:
    def test_under_pick_with_rising_pressure_loses_confidence(self):
        rs, ob = _build_profiles(combined_tob_delta=2.5, combined_run_delta=2.5)
        out = interpret_recent_form(rs, ob, selected_market="Under 8.5")
        # SUPPORTS_OVER vs Under pick → confidence and score should drop.
        assert out["trend_decision"] == "SUPPORTS_OVER"
        assert out["confidence_adjustment"] <= -10
        assert out["score_adjustment"] <= -10
        assert "TREND_CONTRADICTS_UNDER" in out["reason_codes"]

    def test_under_pick_with_declining_pressure_gains_confidence(self):
        rs, ob = _build_profiles(combined_tob_delta=-2.5, combined_run_delta=-2.0)
        out = interpret_recent_form(rs, ob, selected_market="Under 9.5")
        assert out["trend_decision"] == "SUPPORTS_UNDER"
        assert out["confidence_adjustment"] == 6
        assert out["score_adjustment"] == 6
        assert "TREND_SUPPORTS_UNDER" in out["reason_codes"]

    def test_over_pick_with_rising_pressure_gains_confidence(self):
        rs, ob = _build_profiles(combined_tob_delta=2.5, combined_run_delta=2.5)
        out = interpret_recent_form(rs, ob, selected_market="Over 8.5")
        assert out["trend_decision"] == "SUPPORTS_OVER"
        assert out["confidence_adjustment"] == 6
        assert out["score_adjustment"] == 6
        assert "TREND_SUPPORTS_OVER" in out["reason_codes"]

    def test_mixed_signals_when_hr_rising_but_pressure_declining(self):
        rs, ob = _build_profiles(
            combined_tob_delta=-1.6,
            combined_run_delta=-1.5,
            home_hr=0.6, away_hr=0.0,
        )
        out = interpret_recent_form(rs, ob, selected_market="Under 9.5")
        # The payload at least labels mixed when over+under signals coexist.
        ms = out["mixed_signals"]
        assert ms["has_mixed_signals"] is True
        assert "HR_RISING" in ms["over_signals"]
        assert "PRESSURE_DECLINING" in ms["under_signals"]


class TestRunlinePlus15:
    def test_underdog_offense_competitive_boosts_runline(self):
        rs = {"total_runs_delta_5_vs_15": 0.0}
        ob = {
            "home": {
                "times_on_base_delta_5_vs_15": 1.6,
                "hits_delta_5_vs_15": 0.5,
                "walks_delta_5_vs_15": 0.2,
                "home_runs_delta_5_vs_15": 0.0,
                "trend": "RISING_ON_BASE_PRESSURE",
            },
            "away": {
                "times_on_base_delta_5_vs_15": 0.0,
                "hits_delta_5_vs_15": 0.0,
                "walks_delta_5_vs_15": 0.0,
                "home_runs_delta_5_vs_15": 0.0,
                "trend": "STABLE_ON_BASE_PRESSURE",
            },
            "combined": {"times_on_base_delta_5_vs_15": 1.6},
        }
        out = interpret_recent_form(
            rs, ob,
            selected_market="Runline +1.5 home",
            runline_context={"underdog_side": "home"},
        )
        assert "UNDERDOG_OFFENSE_CAN_COMPETE" in out["reason_codes"]
        assert out["score_adjustment"] > 0

    def test_favorite_surging_drops_runline_score(self):
        # Favorite = away in this scenario.
        # Recent run split: away (favorite) +2.5 runs, home (underdog) -1.5.
        rs = {
            "total_runs_delta_5_vs_15":     1.0,
            "runs_scored_delta_5_vs_15_home": -1.5,
            "runs_scored_delta_5_vs_15_away": +2.5,
        }
        ob = {
            "home": {
                "times_on_base_delta_5_vs_15": -1.6,
                "home_runs_delta_5_vs_15": 0.0,
                "hits_delta_5_vs_15": -0.5, "walks_delta_5_vs_15": -0.2,
                "trend": "DECLINING_ON_BASE_PRESSURE",
            },
            "away": {
                "times_on_base_delta_5_vs_15": 2.0,
                "home_runs_delta_5_vs_15": 0.6,
                "hits_delta_5_vs_15": 1.0, "walks_delta_5_vs_15": 0.3,
                "trend": "RISING_ON_BASE_PRESSURE",
            },
            "combined": {"times_on_base_delta_5_vs_15": 0.4},
        }
        out = interpret_recent_form(
            rs, ob,
            selected_market="Runline +1.5 home",
            runline_context={"underdog_side": "home"},
        )
        assert "FAVORITE_OFFENSE_SURGING_AGAINST_RUNLINE" in out["reason_codes"]
        assert "FAVORITE_POWER_SPIKE_RUNLINE_RISK" in out["reason_codes"]
        assert out["score_adjustment"] < 0


class TestClampsAndShape:
    def test_score_adjustment_clamp_low(self):
        rs, ob = _build_profiles(combined_tob_delta=2.5, combined_run_delta=2.5)
        out = interpret_recent_form(rs, ob, selected_market="Under 9.5")
        # confidence_adjustment clamped at -12.
        assert out["confidence_adjustment"] >= -12
        assert out["score_adjustment"] >= -15

    def test_returns_empty_when_no_input(self):
        assert interpret_recent_form(None, None) == {}

    def test_has_human_summary_and_impact_phrase(self):
        rs, ob = _build_profiles(combined_tob_delta=2.5, combined_run_delta=2.5)
        out = interpret_recent_form(rs, ob, selected_market="Under 9.5")
        assert "human_summary" in out and isinstance(out["human_summary"], str)
        assert out["human_summary"]
        assert "impact_on_final_pick" in out
        assert "score" in out["impact_on_final_pick"] or "confianza" in out["impact_on_final_pick"]

    def test_combine_trend_signals_alias(self):
        rs, ob = _build_profiles(combined_tob_delta=2.5)
        a = interpret_recent_form(rs, ob)
        b = combine_trend_signals(rs, ob)
        assert a["trend_decision"] == b["trend_decision"]

    def test_interpret_run_trend_wrapper_works(self):
        out = interpret_run_trend({"total_runs_delta_5_vs_15": 2.5})
        assert out["run_strength"] == "strong_rising"
