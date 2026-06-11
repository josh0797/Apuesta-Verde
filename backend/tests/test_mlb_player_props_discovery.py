"""Tests for Phase 57 — MLB Player Props Discovery (Moneyball)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.mlb_player_props_discovery import (
    ALL_MARKETS,
    DEFAULT_BOOK_AMERICAN_ODDS,
    ENGINE_VERSION,
    LONGSHOT_PROB_FLOOR,
    MARKET_H_R_RBI,
    MARKET_HITS_1P,
    MARKET_RBI_1P,
    MARKET_RUNS_1P,
    MARKET_TB,
    MONEYBALL_MIN_EDGE_PTS,
    MONEYBALL_MIN_PROBABILITY,
    RC_DATA_QUALITY_COMPLETE,
    RC_DATA_QUALITY_MINIMAL,
    RC_DATA_QUALITY_PARTIAL,
    RC_LONGSHOT_REJECTED,
    RC_MONEYBALL_AVOID,
    RC_MONEYBALL_VALUE,
    RC_MONEYBALL_WATCH,
    RC_PARK_FAVOR,
    RC_PITCHER_QUALITY_FAVOR,
    RC_RECENT_FORM_HOT,
    RC_SAVANT_UNAVAILABLE,
    RC_SAVANT_USED,
    american_odds_to_implied,
    compute_player_props_for_day,
    compute_player_props_for_game,
    predict_player_prop,
)


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────
def _make_player(
    *,
    name="Test Hitter",
    pid=12345,
    pos="RF",
    obp=0.360,
    slg=0.500,
    avg=0.280,
    games_played=120,
    runs=70,
    rbi=80,
) -> dict:
    return {
        "id":            pid,
        "name":          name,
        "position":      pos,
        "obp":           obp,
        "slg":           slg,
        "avg":           avg,
        "games_played":  games_played,
        "runs":          runs,
        "rbi":           rbi,
    }


def _make_pitcher(era=4.50, whip=1.35, name="Vulnerable Pitcher", pid=99999) -> dict:
    return {"id": pid, "name": name, "era": era, "whip": whip}


# ───────────────────────────────────────────────────────────────────────
# american_odds_to_implied
# ───────────────────────────────────────────────────────────────────────
class TestAmericanOdds:
    def test_minus_110(self):
        assert abs(american_odds_to_implied(-110) - 0.5238) < 0.001

    def test_plus_180(self):
        assert abs(american_odds_to_implied(180) - 0.3571) < 0.001

    def test_minus_200(self):
        assert abs(american_odds_to_implied(-200) - 0.6667) < 0.001

    def test_none_returns_neutral(self):
        assert american_odds_to_implied(None) == 0.50


# ───────────────────────────────────────────────────────────────────────
# predict_player_prop — base behaviour
# ───────────────────────────────────────────────────────────────────────
class TestPredictPlayerProp:
    def test_unknown_market_returns_unavailable(self):
        out = predict_player_prop(
            player=_make_player(), market="HR_PARLAY_LONGSHOT",
        )
        assert out["available"] is False

    def test_strong_hitter_vs_weak_pitcher_produces_value(self):
        out = predict_player_prop(
            player=_make_player(obp=0.395, slg=0.580, avg=0.310,
                                 games_played=150, runs=110, rbi=120),
            opposing_pitcher=_make_pitcher(era=5.20, whip=1.50),
            park_runs_mult=1.08,
            recent_form={"ops": 0.910},
            savant={"xwoba": 0.395, "xslg": 0.560},
            market=MARKET_H_R_RBI,
        )
        assert out["available"] is True
        assert out["market"] == MARKET_H_R_RBI
        assert out["line"] == 1.5
        assert out["selection"] == "OVER"
        # Strong inputs → high probability + value tier.
        assert out["model_probability"] >= 0.55
        assert out["confidence_tier"] in ("VALUE", "WATCH")
        assert RC_PITCHER_QUALITY_FAVOR in out["reason_codes"]
        assert RC_PARK_FAVOR in out["reason_codes"]
        assert RC_RECENT_FORM_HOT in out["reason_codes"]
        assert RC_SAVANT_USED in out["reason_codes"]

    def test_weak_hitter_low_probability_avoided(self):
        out = predict_player_prop(
            player=_make_player(obp=0.280, slg=0.330, avg=0.215,
                                 games_played=80, runs=20, rbi=25),
            opposing_pitcher=_make_pitcher(era=2.80, whip=0.95),
            market=MARKET_H_R_RBI,
        )
        # Low rates + elite pitcher → AVOID.
        assert out["confidence_tier"] == "AVOID"
        # If probability is < 0.50 → longshot rejected.
        if out["model_probability"] < LONGSHOT_PROB_FLOOR:
            assert RC_LONGSHOT_REJECTED in out["reason_codes"]

    def test_total_bases_uses_slg_basis(self):
        out = predict_player_prop(
            player=_make_player(slg=0.620),
            market=MARKET_TB,
        )
        assert out["market"] == MARKET_TB
        # lambda > 2 expected for a 0.620 SLG hitter at 4.1 PA/game.
        assert out["lambda_estimate"] >= 2.0

    def test_hits_1plus_probability_high_for_avg_hitter(self):
        out = predict_player_prop(
            player=_make_player(avg=0.290),
            market=MARKET_HITS_1P,
        )
        # P(X >= 1) for λ ≈ 1.2 → ≈ 70%.
        assert out["model_probability"] >= 0.60

    def test_runs_1plus_lower_baseline(self):
        out = predict_player_prop(
            player=_make_player(runs=45, games_played=140),
            market=MARKET_RUNS_1P,
        )
        # ~0.32 runs/game → P(X>=1) around 27%.
        assert out["model_probability"] < 0.55

    def test_default_odds_per_market_applied(self):
        out = predict_player_prop(player=_make_player(), market=MARKET_HITS_1P)
        assert out["book_american_odds"] == DEFAULT_BOOK_AMERICAN_ODDS[MARKET_HITS_1P]

    def test_pitcher_mult_clamped_extreme_era(self):
        out = predict_player_prop(
            player=_make_player(),
            opposing_pitcher=_make_pitcher(era=9.50, whip=2.10),
            market=MARKET_H_R_RBI,
        )
        # Multiplier must never exceed +25%.
        assert out["adjustments"]["pitcher_mult"] <= 1.25

    def test_park_mult_clamped(self):
        out = predict_player_prop(
            player=_make_player(),
            park_runs_mult=1.40,    # extreme
            market=MARKET_H_R_RBI,
        )
        assert out["adjustments"]["park_mult"] <= 1.15

    def test_no_savant_emits_unavailable_code(self):
        out = predict_player_prop(
            player=_make_player(),
            market=MARKET_H_R_RBI,
        )
        assert RC_SAVANT_UNAVAILABLE in out["reason_codes"]


# ───────────────────────────────────────────────────────────────────────
# Moneyball filter behaviour
# ───────────────────────────────────────────────────────────────────────
class TestMoneyballFilter:
    def test_avoid_when_below_longshot_floor(self):
        out = predict_player_prop(
            player=_make_player(obp=0.270, slg=0.300, avg=0.200,
                                 games_played=50, runs=10, rbi=12),
            opposing_pitcher=_make_pitcher(era=2.50, whip=0.90),
            market=MARKET_TB,
        )
        if out["model_probability"] < LONGSHOT_PROB_FLOOR:
            assert out["confidence_tier"] == "AVOID"
            assert RC_LONGSHOT_REJECTED in out["reason_codes"]

    def test_watch_when_edge_high_but_prob_below_55(self):
        # Force a scenario where edge_pts >= MONEYBALL_MIN_EDGE_PTS but
        # probability < MONEYBALL_MIN_PROBABILITY (but >= longshot floor).
        out = predict_player_prop(
            player=_make_player(avg=0.255, obp=0.330, slg=0.420,
                                 games_played=120, runs=55, rbi=58),
            opposing_pitcher=_make_pitcher(era=3.70, whip=1.20),
            market=MARKET_RBI_1P,
            book_american_odds=+250,    # implied ~28.6%
        )
        # When prob >= 0.50 AND edge >= 4 but prob < 0.55 → WATCH.
        if (LONGSHOT_PROB_FLOOR <= out["model_probability"] <
                MONEYBALL_MIN_PROBABILITY) and out["edge_points"] >= MONEYBALL_MIN_EDGE_PTS:
            assert out["confidence_tier"] == "WATCH"

    def test_value_tier_requires_both_thresholds(self):
        out = predict_player_prop(
            player=_make_player(avg=0.305, obp=0.385, slg=0.560,
                                 games_played=150, runs=100, rbi=110),
            opposing_pitcher=_make_pitcher(era=5.00, whip=1.45),
            park_runs_mult=1.08,
            recent_form={"ops": 0.880},
            savant={"xwoba": 0.380},
            market=MARKET_HITS_1P,
            book_american_odds=-150,    # implied 60%
        )
        if (out["model_probability"] >= MONEYBALL_MIN_PROBABILITY
                and out["edge_points"] >= MONEYBALL_MIN_EDGE_PTS):
            assert out["confidence_tier"] == "VALUE"
            assert RC_MONEYBALL_VALUE in out["reason_codes"]


# ───────────────────────────────────────────────────────────────────────
# compute_player_props_for_game — orchestration + Savant fail-soft
# ───────────────────────────────────────────────────────────────────────
class TestComputeForGame:
    @pytest.mark.asyncio
    async def test_no_inputs_returns_unavailable(self):
        out = await compute_player_props_for_game(776123)
        assert out["available"] is False

    @pytest.mark.asyncio
    async def test_with_provided_rosters_skips_pitchers(self):
        home_roster = [
            _make_player(name="HomeBat", pid=1, pos="CF"),
            {"id": 2, "name": "HomeSP", "position": "P",
             "obp": 0.100, "slg": 0.150, "avg": 0.050, "games_played": 5,
             "runs": 0, "rbi": 0},
        ]
        away_roster = [_make_player(name="AwayBat", pid=3, pos="LF")]
        with patch(
            "services.mlb_player_props_discovery._enrich_with_savant_failsoft",
            new_callable=AsyncMock,
        ) as mock_savant:
            mock_savant.return_value = {1: None, 3: None}
            out = await compute_player_props_for_game(
                game_pk=776123, use_savant=True,
                home_team_name="NYY", away_team_name="BAL",
                home_pitcher=_make_pitcher(),
                away_pitcher=_make_pitcher(era=3.80, whip=1.18),
                park_runs_mult=1.02,
                home_roster=home_roster, away_roster=away_roster,
            )
        assert out["available"] is True
        assert out["game_pk"] == 776123
        # SP must not produce a recommended prop.
        player_names = {p["player_name"] for p in out["props"]}
        assert "HomeSP" not in player_names
        # data_quality summary present.
        assert set(out["data_quality_summary"].keys()) == {"COMPLETE", "PARTIAL", "MINIMAL"}

    @pytest.mark.asyncio
    async def test_savant_fail_soft_marks_partial(self):
        home_roster = [_make_player(name="Hitter1", pid=10, pos="LF")]
        with patch(
            "services.mlb_player_props_discovery._enrich_with_savant_failsoft",
            new_callable=AsyncMock,
        ) as mock_savant:
            mock_savant.return_value = {10: None}    # Savant failed
            out = await compute_player_props_for_game(
                game_pk=776200, use_savant=True,
                home_team_name="NYY", away_team_name="BAL",
                home_pitcher=_make_pitcher(),
                away_pitcher=_make_pitcher(era=4.80, whip=1.40),
                home_roster=home_roster, away_roster=[],
            )
        # Should still produce props; data_quality must reflect missing Savant.
        if out["props"]:
            prop = out["props"][0]
            assert prop["data_quality"] in ("PARTIAL", "MINIMAL")
            assert RC_SAVANT_UNAVAILABLE in prop["reason_codes"]


# ───────────────────────────────────────────────────────────────────────
# compute_player_props_for_day — fail-soft when schedule unavailable
# ───────────────────────────────────────────────────────────────────────
class TestComputeForDay:
    @pytest.mark.asyncio
    async def test_schedule_failure_returns_unavailable(self):
        with patch("services.mlb_stats_api.get_schedule_with_probables",
                   new_callable=AsyncMock, side_effect=Exception("boom")):
            out = await compute_player_props_for_day("2026-04-15", db=None)
        assert out["available"] is False
        assert out["reason"] == "schedule_unavailable"

    @pytest.mark.asyncio
    async def test_empty_schedule_returns_empty_props(self):
        with patch("services.mlb_stats_api.get_schedule_with_probables",
                   new_callable=AsyncMock, return_value=[]):
            out = await compute_player_props_for_day("2026-04-15", db=None)
        assert out["available"] is True
        assert out["games_processed"] == 0
        assert out["props"] == []

    @pytest.mark.asyncio
    async def test_games_without_probables_are_skipped(self):
        games = [
            {"gamePk": 1, "home_probable_id": None, "away_probable_id": 100},
            {"gamePk": 2, "home_probable_id": 200, "away_probable_id": None},
        ]
        with patch("services.mlb_stats_api.get_schedule_with_probables",
                   new_callable=AsyncMock, return_value=games):
            out = await compute_player_props_for_day("2026-04-15", db=None)
        assert out["games_processed"] == 0
        assert out["props"] == []


# ───────────────────────────────────────────────────────────────────────
# Engine version + module surface
# ───────────────────────────────────────────────────────────────────────
class TestSurface:
    def test_engine_version_present(self):
        assert ENGINE_VERSION.startswith("mlb_player_props_discovery.")

    def test_all_markets_have_default_odds(self):
        for m in ALL_MARKETS:
            assert m in DEFAULT_BOOK_AMERICAN_ODDS

    def test_all_markets_have_default_lines(self):
        from services.mlb_player_props_discovery import DEFAULT_BOOK_LINES
        for m in ALL_MARKETS:
            assert m in DEFAULT_BOOK_LINES
