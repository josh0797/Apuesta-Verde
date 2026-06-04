"""Tests for the Football Totals Model + Over Support + Early Goal 0–30
+ TheStatsAPI shotmap adapter.

Coverage:
  * derived_early_goal: 0–30 metrics derived from events; missing data
    yields data_quality=missing.
  * thestatsapi_early_goal: shotmap derivation; aggregation per team;
    merge with API-Sports profile.
  * football_totals_model_normalizer: mode resolution (unavailable /
    defaults / empirical), fail-soft on missing features.
  * football_over_support: scoring + reason codes + recommended_over_market;
    controlled match blocks Over; top scorer out degrades Over;
    defensive injuries add support; missing data → unavailable.
"""

from __future__ import annotations

import pytest

from services.derived_early_goal import derive_early_goal_profile_from_fixtures
from services.external_sources.thestatsapi_early_goal import (
    derive_early_goal_0_30_from_shotmap,
    aggregate_team_early_goal_profile_from_matches,
    merge_early_goal_sources,
)
from services.football_moneyball.football_totals_model_normalizer import (
    build_football_totals_model,
    TOTALS_MODEL_SOURCE,
)
from services.football_moneyball.football_over_support import (
    calculate_football_over_support,
    MKT_OVER_1_5,
    MKT_OVER_2_5,
    MKT_NONE,
    RC_BOTH_TEAMS_SCORE_EARLY,
    RC_EARLY_GOAL_30_SUPPORT,
    RC_CONTROLLED_MATCH_BLOCKS_OVER,
    RC_TOP_SCORER_OUT_WEAKENS_OVER,
    RC_INJURY_DEFENSE_WEAKENED_OVER_SUPPORT,
    RC_OVER_1_5_PROTECTED,
    RC_OVER_2_5_FRAGILE,
    RC_DC_NB_PREFERS_UNDER,
    RC_DEFENSIVE_LEAK_SUPPORTS_OVER,
)


# ═════════════════════════════════════════════════════════════════════
# derived_early_goal 0–30 extensions
# ═════════════════════════════════════════════════════════════════════
def _fixture_with_goals(*minutes_for, minutes_against=()) -> dict:
    return {
        "events": (
            [{"type": "Goal", "minute": m, "team_for": True} for m in minutes_for]
            + [{"type": "Goal", "minute": m, "team_for": False} for m in minutes_against]
        ),
    }


def test_derive_early_goal_30_metrics_present():
    fixtures = [
        _fixture_with_goals(8, 50),                 # 1 goal in 0-30, 1 in 31+
        _fixture_with_goals(25, 70, minutes_against=(20,)),  # scored 0-30, conceded 0-30
        _fixture_with_goals(40, minutes_against=(10,)),       # only conceded 0-30
        _fixture_with_goals(15, 18),                # both 0-30
        _fixture_with_goals(60, 80, minutes_against=(85,)),   # nothing 0-30
    ]
    profile = derive_early_goal_profile_from_fixtures(fixtures, team_id=None)
    assert profile is not None
    for key in (
        "early_goal_30_pct", "early_concede_30_pct",
        "team_scored_0_30_pct", "team_conceded_0_30_pct",
        "first_30_goal_presence_pct",
        "goals_for_0_30", "goals_against_0_30",
    ):
        assert key in profile, f"missing key: {key}"
    # 0-30 goals scored: 8, 25, 15, 18 → 4
    assert profile["goals_for_0_30"] == 4
    # 0-30 goals conceded: 20, 10 → 2
    assert profile["goals_against_0_30"] == 2
    # Fixtures with any 0-30 goal: 8 (yes), 25/20 (yes), 10 (yes), 15/18 (yes), none (no) → 4/5
    assert profile["first_30_goal_presence_pct"] == round(4 / 5, 3)


def test_derive_early_goal_30_no_events_returns_none():
    assert derive_early_goal_profile_from_fixtures([]) is None
    assert derive_early_goal_profile_from_fixtures([{"events": []}]) is None


# ═════════════════════════════════════════════════════════════════════
# TheStatsAPI shotmap derivation + aggregation
# ═════════════════════════════════════════════════════════════════════
def _shot(minute, team_id, *, is_goal=False, xg=None, on_target=False):
    return {
        "minute": minute, "team_id": team_id,
        "is_goal": is_goal, "xg": xg, "on_target": on_target,
    }


def test_shotmap_derive_per_match_basic():
    shotmap = [
        _shot(8, 10, is_goal=True, xg=0.5, on_target=True),
        _shot(15, 10, xg=0.2, on_target=True),
        _shot(20, 20, is_goal=True, xg=0.3),
        _shot(50, 10, is_goal=True, xg=0.4),
    ]
    out = derive_early_goal_0_30_from_shotmap(shotmap, home_team_id=10, away_team_id=20)
    assert out is not None
    assert out["home"]["goals_total"] == 2
    assert out["home"]["goals_0_30"] == 1   # only minute 8 was a 0-30 goal
    assert out["away"]["goals_0_30"] == 1
    assert out["xg_available"] is True


def test_shotmap_derive_returns_none_when_empty():
    assert derive_early_goal_0_30_from_shotmap(None, home_team_id=1, away_team_id=2) is None
    assert derive_early_goal_0_30_from_shotmap([], home_team_id=1, away_team_id=2) is None


def test_shotmap_aggregate_team_profile():
    # Two matches: team 10 is home in match 1 (scored 1 in 0-30,
    # conceded 0); team 10 is away in match 2 (scored 1 in 0-30,
    # conceded 1 in 0-30).
    match1 = {
        "home": {"goals_total": 2, "goals_0_30": 1, "xg_0_30": 0.5,
                  "shots_0_30": 4, "shots_on_target_0_30": 2, "big_chances_0_30": 1},
        "away": {"goals_total": 1, "goals_0_30": 0, "xg_0_30": 0.2,
                  "shots_0_30": 2, "shots_on_target_0_30": 1, "big_chances_0_30": 0},
        "home_team_id": 10, "away_team_id": 20, "xg_available": True,
    }
    match2 = {
        "home": {"goals_total": 2, "goals_0_30": 1, "xg_0_30": 0.3,
                  "shots_0_30": 3, "shots_on_target_0_30": 1, "big_chances_0_30": 0},
        "away": {"goals_total": 1, "goals_0_30": 1, "xg_0_30": 0.4,
                  "shots_0_30": 4, "shots_on_target_0_30": 2, "big_chances_0_30": 1},
        "home_team_id": 30, "away_team_id": 10, "xg_available": True,
    }
    out = aggregate_team_early_goal_profile_from_matches([match1, match2], team_id=10)
    assert out is not None
    # team 10 goals_0_30: 1 + 1 = 2; conceded: 0 + 1 = 1
    assert out["goals_for_0_30"] == 2
    assert out["goals_against_0_30"] == 1
    # n=2, fixtures with scored 0-30: both → 2/2 = 1.0
    assert out["team_scored_0_30_pct"] == 1.0
    assert out["source"] == "thestatsapi_shotmap"


def test_merge_early_goal_sources_prefers_thestatsapi_when_xg_available():
    api_sports = {
        "early_goal_30_pct": 0.30, "data_quality": "strong",
        "source": "derived_api_sports",
    }
    thestats = {
        "early_goal_30_pct": 0.35, "xg_available": True, "data_quality": "usable",
        "source": "thestatsapi_shotmap", "first_30_xg_for": 1.2,
    }
    merged = merge_early_goal_sources(api_sports, thestats)
    assert merged["source"] == "thestatsapi_shotmap"
    assert merged["early_goal_30_pct"] == 0.35
    assert merged["first_30_xg_for"] == 1.2
    assert merged["secondary_source"] == "derived_api_sports"


def test_merge_early_goal_sources_falls_back_when_thestats_thin():
    api_sports = {
        "early_goal_30_pct": 0.40, "data_quality": "strong",
        "source": "derived_api_sports",
    }
    thestats = {
        "early_goal_30_pct": 0.20, "xg_available": False, "data_quality": "thin",
        "source": "thestatsapi_shotmap",
    }
    merged = merge_early_goal_sources(api_sports, thestats)
    assert merged["source"] == "derived_api_sports"


# ═════════════════════════════════════════════════════════════════════
# football_totals_model_normalizer
# ═════════════════════════════════════════════════════════════════════
def test_totals_model_unavailable_when_features_missing():
    out = build_football_totals_model({}, calibration_summary={"available": True})
    assert out["available"] is False
    assert out["mode"] == "unavailable"
    assert out["totals_model_source"] == TOTALS_MODEL_SOURCE


def test_totals_model_defaults_mode_when_global_under_100():
    feats = {
        "p_under_2_5": 0.55, "p_under_3_5": 0.85,
        "p_under_2_5_poisson": 0.52, "p_under_3_5_poisson": 0.83,
        "dc_rho_used": -0.05, "goals_dispersion_ratio": 1.0,
        "dc_nb_delta_2_5_pts": 3.0, "dc_nb_delta_3_5_pts": 2.0,
    }
    pick = {"_statsbomb_features": feats}
    cs = {"available": True, "global_applies": False, "sample_size": 50}
    out = build_football_totals_model(pick, calibration_summary=cs)
    assert out["available"] is True
    assert out["mode"] == "defaults"
    assert out["rho_used"] == -0.05
    assert out["goals_dispersion_ratio"] == 1.0
    assert out["under_2_5"]["dc_nb"] == 0.55
    assert out["under_3_5"]["delta_pts"] == 2.0
    assert "DEFAULT_CALIBRATION" in out["reason_codes"]
    assert "NB_INERT" in out["reason_codes"]
    assert "DC_ACTIVE" in out["reason_codes"]


def test_totals_model_empirical_mode_when_global_applies():
    feats = {
        "p_under_2_5": 0.55, "p_under_3_5": 0.85,
        "p_under_2_5_poisson": 0.52, "p_under_3_5_poisson": 0.83,
        "dc_rho_used": -0.08, "goals_dispersion_ratio": 1.15,
    }
    pick = {"_statsbomb_features": feats}
    cs = {"available": True, "global_applies": True, "sample_size": 150}
    out = build_football_totals_model(pick, calibration_summary=cs)
    assert out["mode"] == "empirical"
    assert "EMPIRICAL_CALIBRATION" in out["reason_codes"]
    assert "NB_ACTIVE" in out["reason_codes"]  # ratio > 1.0
    assert out["sample_size"] == 150


def test_totals_model_recomputes_delta_when_missing():
    feats = {
        "p_under_2_5": 0.60, "p_under_3_5": 0.90,
        "p_under_2_5_poisson": 0.55, "p_under_3_5_poisson": 0.85,
        "dc_rho_used": -0.05, "goals_dispersion_ratio": 1.0,
        # delta_*_pts intentionally missing.
    }
    pick = {"_statsbomb_features": feats}
    out = build_football_totals_model(pick, calibration_summary={"available": True, "global_applies": False, "sample_size": 0})
    # 0.60 - 0.55 = 0.05 → 5.0 pts
    assert out["under_2_5"]["delta_pts"] == 5.0
    assert out["under_3_5"]["delta_pts"] == 5.0


# ═════════════════════════════════════════════════════════════════════
# football_over_support
# ═════════════════════════════════════════════════════════════════════
def _over_match(
    *,
    lambda_total=2.5,
    home_score_30=0.40, away_score_30=0.40,
    home_concede_30=0.30, away_concede_30=0.30,
    home_presence_30=0.65, away_presence_30=0.65,
    goals_against_home=1.4, goals_against_away=1.3,
    totals_model=None, pressure_tier=None, controlled=False,
    injury_codes=None, live_stats=None,
):
    match = {
        "home_team": {
            "context": {
                "goals_for_avg": 1.6,
                "goals_against_avg": goals_against_home,
                "recent_fixtures": {
                    "team_scored_0_30_pct": home_score_30,
                    "team_conceded_0_30_pct": home_concede_30,
                    "first_30_goal_presence_pct": home_presence_30,
                    "early_goal_30_pct": 0.30,
                    "early_concede_30_pct": 0.25,
                    "first_half_goal_pct": 0.50,
                },
            },
        },
        "away_team": {
            "context": {
                "goals_for_avg": 1.5,
                "goals_against_avg": goals_against_away,
                "recent_fixtures": {
                    "team_scored_0_30_pct": away_score_30,
                    "team_conceded_0_30_pct": away_concede_30,
                    "first_30_goal_presence_pct": away_presence_30,
                    "early_goal_30_pct": 0.31,
                    "early_concede_30_pct": 0.26,
                    "first_half_goal_pct": 0.50,
                },
            },
        },
        "match_features": {"lambda_total": lambda_total},
        "football_totals_model": totals_model,
    }
    if pressure_tier:
        match["goal_pressure_profile"] = {
            "available": True,
            "combined": {"pressure_tier": pressure_tier},
        }
    if controlled:
        match["_form_guard"] = {"fragile": False, "verdict": "CONTROLLED"}
    if injury_codes:
        match["injury_intelligence"] = {"available": True, "reason_codes": injury_codes}
    if live_stats:
        match["live_stats"] = live_stats
    return match


def test_over_support_unavailable_when_no_inputs():
    out = calculate_football_over_support({})
    fos = out["football_over_support"]
    assert fos["available"] is False
    assert fos["recommended_over_market"] == MKT_NONE


def test_over_support_over_1_5_strong_with_early_goal_pressure():
    match = _over_match(
        lambda_total=2.5, home_score_30=0.45, away_score_30=0.45,
        home_concede_30=0.35, away_concede_30=0.35,
        home_presence_30=0.70, away_presence_30=0.70,
    )
    out = calculate_football_over_support(match)
    fos = out["football_over_support"]
    assert fos["available"] is True
    assert fos["over_1_5_support_score"] >= 50
    assert RC_EARLY_GOAL_30_SUPPORT in fos["reason_codes"]
    assert RC_BOTH_TEAMS_SCORE_EARLY in fos["reason_codes"]
    assert fos["recommended_over_market"] in (MKT_OVER_1_5, MKT_OVER_2_5)


def test_over_support_over_2_5_blocked_by_controlled_match():
    match = _over_match(
        lambda_total=3.0, home_score_30=0.50, away_score_30=0.50,
        home_presence_30=0.80, away_presence_30=0.80,
        pressure_tier="HIGH_PRESSURE", controlled=True,
    )
    out = calculate_football_over_support(match)
    fos = out["football_over_support"]
    # Controlled cap: over_2_5_support_score must be ≤ 30
    assert fos["over_2_5_support_score"] <= 30
    assert RC_CONTROLLED_MATCH_BLOCKS_OVER in fos["reason_codes"]
    assert fos["recommended_over_market"] != MKT_OVER_2_5


def test_over_support_top_scorer_out_degrades_over():
    match = _over_match(
        lambda_total=2.9, home_score_30=0.40, away_score_30=0.45,
        home_presence_30=0.70, away_presence_30=0.70,
        injury_codes=["TOP_SCORER_OUT"],
    )
    out = calculate_football_over_support(match)
    fos = out["football_over_support"]
    assert RC_TOP_SCORER_OUT_WEAKENS_OVER in fos["reason_codes"]


def test_over_support_key_defender_out_adds_support():
    match = _over_match(injury_codes=["KEY_DEFENDER_OUT"])
    out = calculate_football_over_support(match)
    fos = out["football_over_support"]
    assert RC_INJURY_DEFENSE_WEAKENED_OVER_SUPPORT in fos["reason_codes"]


def test_over_support_dc_nb_prefers_under_pulls_over_score():
    totals_model = {
        "available": True,
        "under_3_5": {"delta_pts": 4.0},  # strong Under tilt
    }
    match = _over_match(lambda_total=2.9, totals_model=totals_model)
    out = calculate_football_over_support(match)
    fos = out["football_over_support"]
    assert RC_DC_NB_PREFERS_UNDER in fos["reason_codes"]


def test_over_support_defensive_leak_supports_over():
    match = _over_match(goals_against_home=1.7, goals_against_away=1.6)
    out = calculate_football_over_support(match)
    fos = out["football_over_support"]
    assert RC_DEFENSIVE_LEAK_SUPPORTS_OVER in fos["reason_codes"]
    assert fos["defensive_leak_score"] >= 60


def test_over_support_low_lambda_results_in_over_1_5_or_none():
    match = _over_match(
        lambda_total=2.0,
        home_score_30=0.30, away_score_30=0.30,
        home_presence_30=0.45, away_presence_30=0.45,
    )
    out = calculate_football_over_support(match)
    fos = out["football_over_support"]
    assert fos["recommended_over_market"] in (MKT_OVER_1_5, MKT_NONE)


def test_over_support_offense_bucket_derived_from_lambda():
    match = _over_match(lambda_total=3.2)
    out = calculate_football_over_support(match)
    assert out["football_over_support"]["offense_bucket"] == "HIGH_OFFENSE"


def test_over_support_does_not_raise_on_missing_data():
    # Match with only home_team minimal data.
    match = {"home_team": {"context": {"goals_for_avg": 1.0}}}
    out = calculate_football_over_support(match)
    assert isinstance(out, dict)
    assert "football_over_support" in out
