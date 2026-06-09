"""Tests for MLB Offensive Injury Impact Score."""
from __future__ import annotations

import pytest

from services.mlb_offensive_injury_impact import (
    compute_offensive_injury_impact_for_team,
    compute_offensive_injury_impact,
    apply_impact_to_pipeline,
    BUCKET_LOW, BUCKET_MEDIUM, BUCKET_HIGH,
    RC_OFFENSIVE_INJURY_IMPACT_USED,
    RC_TOP_OFFENSIVE_PLAYER_MISSING,
    RC_MULTIPLE_TOP5_BATS_MISSING,
    RC_BOTH_TEAMS_OFFENSIVELY_DEPLETED,
    RC_INJURY_IMBALANCE_FAVORS_HEALTHIER_TEAM,
    RC_UNDER_SUPPORTED_BY_OFFENSIVE_INJURIES,
    RC_PITCHER_ONLY_INJURIES_NO_PENALTY,
)


def _star(name, **over):
    """A star-quality offensive player.

    Each star is slightly stronger than the next by default so the
    composite ordering is deterministic in tests. Override per-call by
    passing explicit stat kwargs.
    """
    # Extract index from "Star{N}" so Star1 > Star2 > ... > StarN.
    idx = 0
    if name.lower().startswith("star"):
        try:
            idx = int(name.lower().replace("star", "")) - 1
        except ValueError:
            idx = 0
    base = dict(
        id=name.lower(), name=name, position="OF",
        ops=0.950 - idx * 0.020,   # 0.950, 0.930, 0.910, ...
        runs=100 - idx * 3,
        rbi=105 - idx * 3,
        hr=32 - idx,
        xbh=70 - idx * 2,
        obp=0.390 - idx * 0.005,
        pa=620 - idx * 5,
    )
    base.update(over)
    return base


def _bench(name, **over):
    base = dict(
        id=name.lower(), name=name, position="UT",
        ops=0.650, runs=35, rbi=30, hr=8, xbh=18, obp=0.310, pa=200,
    )
    base.update(over)
    return base


def _pitcher(name, **over):
    base = dict(id=name.lower(), name=name, position="SP", pa=0)
    base.update(over)
    return base


def _roster_8_stars():
    """Eight balanced offensive players (all top-5 quality)."""
    return [_star(f"Star{i}") for i in range(1, 9)]


class TestPerTeamScore:
    def test_no_injuries_no_penalty(self):
        out = compute_offensive_injury_impact_for_team(
            team_name="Yankees", roster=_roster_8_stars(), injured=[],
        )
        assert out["offensive_injury_score"] == 0
        assert out["missing_top5_count"] == 0
        assert out["impact_bucket"] == BUCKET_LOW

    def test_one_top5_missing_small_penalty(self):
        roster = _roster_8_stars()
        # Move Star1 (top scorer) to injured.
        injured = [roster[0]]
        roster_active = roster[1:]
        out = compute_offensive_injury_impact_for_team(
            team_name="Yankees", roster=roster_active, injured=injured,
        )
        assert out["missing_top5_count"] == 1
        assert 15 <= out["offensive_injury_score"] <= 30
        assert out["impact_bucket"] == BUCKET_LOW
        assert RC_TOP_OFFENSIVE_PLAYER_MISSING in out["reason_codes"]

    def test_two_top5_missing_moderate(self):
        roster = _roster_8_stars()
        injured = roster[:2]
        out = compute_offensive_injury_impact_for_team(
            team_name="Yankees", roster=roster[2:], injured=injured,
        )
        assert out["missing_top5_count"] == 2
        assert 30 <= out["offensive_injury_score"] <= 55
        assert out["impact_bucket"] == BUCKET_MEDIUM
        assert RC_MULTIPLE_TOP5_BATS_MISSING in out["reason_codes"]

    def test_three_top5_missing_strong(self):
        roster = _roster_8_stars()
        injured = roster[:3]
        out = compute_offensive_injury_impact_for_team(
            team_name="Yankees", roster=roster[3:], injured=injured,
        )
        assert out["missing_top5_count"] == 3
        assert out["offensive_injury_score"] >= 55
        assert out["impact_bucket"] == BUCKET_HIGH

    def test_pitcher_only_injuries_no_penalty(self):
        roster = _roster_8_stars()
        injured = [_pitcher("Ace1"), _pitcher("Ace2")]
        out = compute_offensive_injury_impact_for_team(
            team_name="Yankees", roster=roster, injured=injured,
        )
        assert out["offensive_injury_score"] == 0
        assert RC_PITCHER_ONLY_INJURIES_NO_PENALTY in out["reason_codes"]

    def test_bench_player_injury_does_not_trigger_top5(self):
        roster = _roster_8_stars()
        injured = [_bench("Bench1")]
        out = compute_offensive_injury_impact_for_team(
            team_name="Yankees", roster=roster, injured=injured,
        )
        # Bench player is not in top-5 → no penalty.
        assert out["offensive_injury_score"] == 0

    def test_missing_data_fail_soft(self):
        out = compute_offensive_injury_impact_for_team(
            team_name="Mystery", roster=None, injured=None,
        )
        assert out["available"] is False
        assert out["offensive_injury_score"] == 0

    def test_fallback_when_only_ops_runs_hr_present(self):
        """If wRC+ / RBI / OBP missing → still computes via fallback."""
        partial = [
            dict(id=f"p{i}", name=f"P{i}", position="OF",
                 ops=0.850 - i * 0.05, runs=90 - i, hr=25 - i)
            for i in range(6)
        ]
        out = compute_offensive_injury_impact_for_team(
            team_name="Mystery", roster=partial[1:], injured=[partial[0]],
        )
        assert out["missing_top5_count"] == 1
        assert out["offensive_injury_score"] > 0

    def test_double_counting_prevented(self):
        """Player on BOTH roster and injured list counts ONCE as injured."""
        roster = _roster_8_stars()
        # Star1 appears in both lists.
        out = compute_offensive_injury_impact_for_team(
            team_name="Yankees", roster=roster, injured=[roster[0]],
        )
        # Total players in top5 still = 5, missing = 1.
        assert out["missing_top5_count"] == 1
        assert len(out["top5_missing"]) + len(out["top5_available"]) == 5

    def test_two_way_player_not_classified_as_pitcher(self):
        """Ohtani-style: position 'P/DH' with PA >= 50 → offensive."""
        ohtani = _star("Shohei", position="P/DH", pa=620, ops=1.0,
                       runs=110, rbi=120, hr=45, xbh=85, obp=0.420)
        # Add 7 weaker teammates so the pool is realistic and the
        # offensive-impact pipeline isn't blocked by the small-roster
        # guard. Ohtani's composite should land in the top-5.
        teammates = [_star(f"Star{i+1}") for i in range(7)]
        out = compute_offensive_injury_impact_for_team(
            team_name="Angels", roster=[ohtani] + teammates, injured=[],
        )
        # He must show up in top-5 available.
        names = [p["name"] for p in out["top5_available"]]
        assert "Shohei" in names

    def test_insufficient_roster_returns_fail_soft(self):
        """When fewer than 5 offensive players are known we cannot
        rank a credible top-5 — module returns available=False without
        inflating any penalty."""
        from services.mlb_offensive_injury_impact import (
            RC_DATA_INCOMPLETE_FALLBACK_USED,
        )
        tiny = [_star(f"Star{i+1}") for i in range(3)]
        out = compute_offensive_injury_impact_for_team(
            team_name="Mystery", roster=tiny[1:], injured=[tiny[0]],
        )
        assert out["available"] is False
        assert out["offensive_injury_score"] == 0
        assert RC_DATA_INCOMPLETE_FALLBACK_USED in out["reason_codes"]



class TestMatchupImbalance:
    def test_balanced_when_both_healthy(self):
        out = compute_offensive_injury_impact(
            home_team_name="Yankees", away_team_name="Guardians",
            home_roster=_roster_8_stars(), home_injured=[],
            away_roster=_roster_8_stars(), away_injured=[],
        )
        assert out["imbalance"] == "BALANCED"
        assert out["favors_team"] is None
        assert out["under_support"] is False

    def test_both_depleted_supports_under(self):
        home = _roster_8_stars()
        away = _roster_8_stars()
        out = compute_offensive_injury_impact(
            home_team_name="Yankees", away_team_name="Guardians",
            home_roster=home[3:], home_injured=home[:3],   # 3 missing
            away_roster=away[3:], away_injured=away[:3],   # 3 missing
        )
        assert out["under_support"] is True
        assert RC_BOTH_TEAMS_OFFENSIVELY_DEPLETED in out["reason_codes"]
        assert RC_UNDER_SUPPORTED_BY_OFFENSIVE_INJURIES in out["reason_codes"]

    def test_one_depleted_favors_healthier(self):
        home = _roster_8_stars()
        away = _roster_8_stars()
        out = compute_offensive_injury_impact(
            home_team_name="Yankees", away_team_name="Guardians",
            home_roster=home, home_injured=[],            # healthy
            away_roster=away[3:], away_injured=away[:3],  # depleted
        )
        assert out["imbalance"] == "HOME_HEALTHIER"
        assert out["favors_team"] == "home"
        assert RC_INJURY_IMBALANCE_FAVORS_HEALTHIER_TEAM in out["reason_codes"]

    def test_does_not_auto_flip_polarity(self):
        """Module never declares 'pick ML for healthier team' on its own
        — it only emits a reason code + favors_team annotation."""
        home = _roster_8_stars()
        away = _roster_8_stars()
        out = compute_offensive_injury_impact(
            home_team_name="Yankees", away_team_name="Guardians",
            home_roster=home, home_injured=[],
            away_roster=away[3:], away_injured=away[:3],
        )
        # The payload must NOT contain a market recommendation.
        forbidden_keys = ("pick", "recommendation", "market_side", "selection")
        for k in forbidden_keys:
            assert k not in out, f"forbidden key in matchup payload: {k}"

    def test_narrative_es_present_when_injuries(self):
        home = _roster_8_stars()
        out = compute_offensive_injury_impact(
            home_team_name="Yankees", away_team_name="Guardians",
            home_roster=home[2:], home_injured=home[:2],
            away_roster=_roster_8_stars(), away_injured=[],
        )
        assert out["narrative_es"] is not None
        assert "Yankees" in out["narrative_es"]


class TestPipelineAdjustments:
    def test_no_injury_returns_neutral_multipliers(self):
        out = compute_offensive_injury_impact(
            home_team_name="A", away_team_name="B",
            home_roster=_roster_8_stars(), home_injured=[],
            away_roster=_roster_8_stars(), away_injured=[],
        )
        adj = apply_impact_to_pipeline(impact_payload=out, side="home")
        assert adj["lambda_7_9_multiplier"] == 1.0
        assert adj["traffic_score_multiplier"] == 1.0

    def test_severe_injuries_cap_at_0_85(self):
        """Multipliers never go below 0.85 (15% suppression cap)."""
        home = _roster_8_stars()
        out = compute_offensive_injury_impact(
            home_team_name="A", away_team_name="B",
            home_roster=home[5:], home_injured=home[:5],
            away_roster=_roster_8_stars(), away_injured=[],
        )
        adj = apply_impact_to_pipeline(impact_payload=out, side="home")
        assert adj["lambda_7_9_multiplier"] >= 0.85
        assert adj["lambda_7_9_multiplier"] < 1.0

    def test_pipeline_returns_reason_codes(self):
        home = _roster_8_stars()
        out = compute_offensive_injury_impact(
            home_team_name="A", away_team_name="B",
            home_roster=home[2:], home_injured=home[:2],
            away_roster=_roster_8_stars(), away_injured=[],
        )
        adj = apply_impact_to_pipeline(impact_payload=out, side="home")
        assert RC_MULTIPLE_TOP5_BATS_MISSING in adj["reason_codes"]
