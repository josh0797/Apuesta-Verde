"""Tests for Phase 10 — Statcast context detector in mlb_explosive_inning_engine.

Coverage:
* `_detect_statcast_contact_context` standalone with synthetic snapshots.
* End-to-end: `evaluate_explosive_inning` consumes ``advanced_stats_snapshot``
  and shifts pressure_score accordingly.
* Fail-soft: missing snapshot doesn't break the engine.
"""
from __future__ import annotations

from services.mlb_explosive_inning_engine import (
    _detect_statcast_contact_context,
    evaluate_explosive_inning,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _snap(home_pitcher=None, away_pitcher=None,
           home_team=None, away_team=None):
    """Build a canonical ``advanced_stats_snapshot`` dict."""
    return {
        "home_pitcher_advanced": {
            "available": bool(home_pitcher),
            "pitcher":   home_pitcher or {},
        },
        "away_pitcher_advanced": {
            "available": bool(away_pitcher),
            "pitcher":   away_pitcher or {},
        },
        "home_team_advanced": {
            "available": bool(home_team),
            "team":      home_team or {},
        },
        "away_team_advanced": {
            "available": bool(away_team),
            "team":      away_team or {},
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Standalone detector
# ─────────────────────────────────────────────────────────────────────
def test_statcast_detector_fail_soft_no_snapshot():
    pts, codes, humans, flags = _detect_statcast_contact_context({}, None, None)
    assert pts == 0
    assert codes == []
    assert humans == []
    assert flags == {}


def test_statcast_detector_pitcher_barrel_elevated():
    snap = _snap(home_pitcher={"barrel_pct_allowed": 11.0})
    pts, codes, humans, _ = _detect_statcast_contact_context(
        {"advanced_stats_snapshot": snap},
        pitching_side="home",
        batting_side="away",
    )
    assert pts >= 4
    assert "BARREL_RISK_ELEVATED" in codes
    assert any("barrel" in h.lower() for h in humans)


def test_statcast_detector_pitcher_hard_hit_high():
    snap = _snap(home_pitcher={"hard_hit_pct_allowed": 45.0})
    pts, codes, _, _ = _detect_statcast_contact_context(
        {"advanced_stats_snapshot": snap},
        pitching_side="home",
        batting_side="away",
    )
    assert pts >= 3
    assert "STATCAST_HARD_CONTACT_SUPPORT" in codes


def test_statcast_detector_pitcher_xwoba_warning():
    snap = _snap(home_pitcher={"xwoba_allowed": 0.355})
    pts, codes, _, _ = _detect_statcast_contact_context(
        {"advanced_stats_snapshot": snap},
        pitching_side="home",
        batting_side="away",
    )
    assert "PITCHER_XWOBA_WARNING" in codes
    assert pts >= 3


def test_statcast_detector_batting_team_power_profile():
    snap = _snap(
        home_pitcher={"barrel_pct_allowed": 3.0},  # quiet pitcher
        away_team={"team_barrel_pct": 9.5, "team_xwoba": 0.345},
    )
    pts, codes, _, flags = _detect_statcast_contact_context(
        {"advanced_stats_snapshot": snap},
        pitching_side="home",   # away bats
        batting_side="away",
    )
    assert "POWER_BAT_STATCAST_SUPPORT" in codes
    assert flags.get("batting_power_profile") is True
    assert pts >= 3


def test_statcast_detector_cooled_environment_decreases_pressure():
    snap = _snap(
        home_pitcher={"xwoba_allowed": 0.295, "barrel_pct_allowed": 5.0},
        away_pitcher={"xwoba_allowed": 0.300, "barrel_pct_allowed": 6.0},
    )
    pts, codes, _, flags = _detect_statcast_contact_context(
        {"advanced_stats_snapshot": snap},
        pitching_side="home",
        batting_side="away",
    )
    assert "LOW_HARD_CONTACT_ENVIRONMENT" in codes
    assert pts < 0  # negative pressure adjustment
    assert flags.get("env_cooled") is True


def test_statcast_detector_clamps_at_plus_8():
    snap = _snap(
        home_pitcher={
            "barrel_pct_allowed": 15.0,
            "hard_hit_pct_allowed": 50.0,
            "xwoba_allowed": 0.400,
        },
        away_team={"team_barrel_pct": 12.0, "team_xwoba": 0.360},
    )
    pts, _, _, _ = _detect_statcast_contact_context(
        {"advanced_stats_snapshot": snap},
        pitching_side="home",
        batting_side="away",
    )
    assert pts <= 8


# ─────────────────────────────────────────────────────────────────────
# End-to-end via evaluate_explosive_inning
# ─────────────────────────────────────────────────────────────────────
def test_evaluate_explosive_inning_fail_soft_without_snapshot():
    out = evaluate_explosive_inning({
        "inning": 5, "half_inning": "top", "outs": 1,
        "score_home": 3, "score_away": 2,
        "current_total_runs": 5,
    })
    assert "explosive_inning_pressure_score" in out
    # statcast_contact should appear in contribs with 0
    assert "statcast_contact" in out["score_contributions"]
    assert out["score_contributions"]["statcast_contact"] == 0


def test_evaluate_explosive_inning_applies_statcast_pressure():
    base_metrics = {
        "inning": 4, "half_inning": "bottom", "outs": 1,
        "score_home": 2, "score_away": 1,
        "current_total_runs": 3,
        "pitching_team": "home", "batting_team": "away",
        "hits_this_inning": 1,
    }
    # Without snapshot
    no_snap = evaluate_explosive_inning(base_metrics)
    score_no = no_snap["explosive_inning_pressure_score"]

    # With high-risk snapshot
    high_snap = dict(base_metrics)
    high_snap["advanced_stats_snapshot"] = _snap(
        home_pitcher={
            "barrel_pct_allowed": 10.0,
            "hard_hit_pct_allowed": 44.0,
            "xwoba_allowed": 0.350,
        },
        away_team={"team_barrel_pct": 9.5, "team_xwoba": 0.340},
    )
    with_snap = evaluate_explosive_inning(high_snap)
    score_with = with_snap["explosive_inning_pressure_score"]

    assert score_with > score_no
    assert with_snap["score_contributions"]["statcast_contact"] > 0
    assert "BARREL_RISK_ELEVATED" in with_snap["reason_codes"]


def test_evaluate_explosive_inning_cooled_environment_lowers_score():
    base_metrics = {
        "inning": 3, "half_inning": "top", "outs": 2,
        "score_home": 1, "score_away": 0,
        "pitching_team": "home", "batting_team": "away",
        "hits_this_inning": 1,
        "walks_this_inning": 1,  # adds some base traffic baseline
    }
    cool_snap = dict(base_metrics)
    cool_snap["advanced_stats_snapshot"] = _snap(
        home_pitcher={"xwoba_allowed": 0.290, "barrel_pct_allowed": 5.0},
        away_pitcher={"xwoba_allowed": 0.295, "barrel_pct_allowed": 5.5},
    )
    out = evaluate_explosive_inning(cool_snap)
    assert out["score_contributions"]["statcast_contact"] < 0
    assert "LOW_HARD_CONTACT_ENVIRONMENT" in out["reason_codes"]


def test_evaluate_explosive_inning_score_stays_in_bounds():
    """Pressure score must remain in [0, 100] even with extreme statcast."""
    metrics = {
        "inning": 6, "half_inning": "bottom", "outs": 0,
        "score_home": 4, "score_away": 3,
        "pitching_team": "home", "batting_team": "away",
        "hits_this_inning": 5,
        "walks_this_inning": 3,
        "hard_contact_this_inning": 4,
        "barrels_this_inning": 3,
        "pitch_count": 110,
        "advanced_stats_snapshot": _snap(
            home_pitcher={
                "barrel_pct_allowed": 14.0,
                "hard_hit_pct_allowed": 48.0,
                "xwoba_allowed": 0.400,
            },
            away_team={"team_barrel_pct": 11.0, "team_xwoba": 0.360},
        ),
    }
    out = evaluate_explosive_inning(metrics)
    assert 0 <= out["explosive_inning_pressure_score"] <= 100
