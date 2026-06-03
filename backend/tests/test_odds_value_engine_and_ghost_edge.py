"""Tests for the new odds_value_engine + extended ghost-edge L5/L15
verifier (Batch A 2026-06)."""
import pytest

from services.odds_value_engine import (
    normalize_decimal_odds,
    parse_midpoint_odds,
    implied_probability,
    calculate_edge,
    calculate_expected_value,
    detect_line_movement,
    compare_bookmaker_odds,
    evaluate_market,
    DIR_TOWARD_OVER, DIR_TOWARD_UNDER, DIR_STABLE,
)
from services.mlb_real_stats_verifier import verify_model_inputs


# ── normalize_decimal_odds ───────────────────────────────────────────
class TestNormalizeDecimalOdds:
    @pytest.mark.parametrize("v,expected", [
        (1.85, 1.85),
        ("1.85", 1.85),
        ("1,85", 1.85),     # Spanish locale
        ("+150", 2.5),      # American positive
        ("-110", 1.9091),   # American negative
        ("9/4", 3.25),      # Fractional
        ("11/8", 2.375),
        (2, 2.0),
    ])
    def test_valid_inputs(self, v, expected):
        assert normalize_decimal_odds(v) == pytest.approx(expected, rel=1e-3)

    @pytest.mark.parametrize("v", [
        None, "", "  ", "abc", 0.5, 1.0, "1.0", "0/3", "1/0",
    ])
    def test_invalid_inputs(self, v):
        assert normalize_decimal_odds(v) is None

    def test_american_minus_100_is_2_decimal(self):
        # -100 American = even odds = 2.00 decimal, which is valid.
        assert normalize_decimal_odds("-100") == 2.0


# ── parse_midpoint_odds ──────────────────────────────────────────────
class TestParseMidpoint:
    def test_dash_range(self):
        assert parse_midpoint_odds("1.80-1.95") == 1.875

    def test_slash_range_with_comma(self):
        assert parse_midpoint_odds("1,80 / 1,95") == 1.875

    def test_single_value(self):
        assert parse_midpoint_odds("2.10") == pytest.approx(2.10)

    def test_empty(self):
        assert parse_midpoint_odds("") is None
        assert parse_midpoint_odds(None) is None


# ── edge / EV ────────────────────────────────────────────────────────
class TestEdgeAndEV:
    def test_edge_value_when_model_above_implied(self):
        # implied at 1.85 = 54.05%. Model 60% → edge ≈ +6%
        out = calculate_edge(0.60, 1.85)
        assert out["verdict"] == "VALUE"
        assert out["edge_pct"] > 3
        assert out["model_probability"] == 0.6

    def test_no_value_when_model_below_implied(self):
        out = calculate_edge(0.45, 1.85)
        assert out["verdict"] == "NO_VALUE"
        assert out["edge_pct"] < -3

    def test_fair_value_when_close(self):
        out = calculate_edge(0.54, 1.85)
        assert out["verdict"] == "FAIR_VALUE"

    def test_edge_unknown_when_no_inputs(self):
        assert calculate_edge(None, None)["verdict"] == "UNKNOWN"
        assert calculate_edge(0.6, None)["verdict"] == "UNKNOWN"
        assert calculate_edge(None, 1.85)["verdict"] == "UNKNOWN"

    def test_expected_value_positive(self):
        ev = calculate_expected_value(0.60, 1.85, stake=10)
        assert ev["expected_value"] > 0
        assert ev["is_positive_ev"] is True

    def test_expected_value_negative(self):
        ev = calculate_expected_value(0.40, 1.85, stake=10)
        assert ev["expected_value"] < 0
        assert ev["is_positive_ev"] is False

    def test_ev_unknown_inputs(self):
        ev = calculate_expected_value(None, None)
        assert ev["expected_value"] is None
        assert ev["is_positive_ev"] is False


# ── line movement ───────────────────────────────────────────────────
class TestLineMovement:
    def test_line_drift_toward_over(self):
        out = detect_line_movement(opening_line=8.5, current_line=9.0)
        assert out["movement"] == 0.5
        assert out["direction"] == DIR_TOWARD_OVER

    def test_line_drift_toward_under(self):
        out = detect_line_movement(opening_line=9.5, current_line=8.5)
        assert out["movement"] == -1.0
        assert out["direction"] == DIR_TOWARD_UNDER
        assert out["steam_detected"] is True

    def test_odds_movement_only(self):
        # Over odds dropping from 2.00 → 1.70 = steam toward Over.
        out = detect_line_movement(
            opening_odds=2.00, current_odds=1.70, market_side="over",
        )
        assert out["odds_movement"] == pytest.approx(-0.30, rel=1e-3)
        assert out["direction"] == DIR_TOWARD_OVER
        assert out["steam_detected"] is True

    def test_stable_when_no_change(self):
        out = detect_line_movement(opening_line=9.0, current_line=9.0)
        assert out["direction"] == DIR_STABLE


# ── compare_bookmaker_odds ──────────────────────────────────────────
class TestCompareBookmakers:
    def test_picks_best_price(self):
        out = compare_bookmaker_odds([
            {"bookmaker": "Pinnacle", "odds": 1.85},
            {"bookmaker": "Bet365",   "odds": 1.91},
            {"bookmaker": "DraftKings","odds": 1.88},
        ])
        assert out["best_odds"] == 1.91
        assert out["best_bookmaker"] == "Bet365"
        assert out["spread_pct"] > 0

    def test_handles_invalid_entries(self):
        out = compare_bookmaker_odds([
            {"bookmaker": "A", "odds": "garbage"},
            {"bookmaker": "B", "odds": 1.95},
        ])
        assert out["best_odds"] == 1.95
        assert out["best_bookmaker"] == "B"

    def test_empty_list(self):
        out = compare_bookmaker_odds([])
        assert out["best_odds"] is None


# ── evaluate_market (top-level) ─────────────────────────────────────
class TestEvaluateMarket:
    def test_priced_with_value(self):
        out = evaluate_market(
            decimal_odds=1.85,
            model_probability=0.60,
            opening_line=8.5, current_line=9.0,
        )
        assert out["market_status"] == "priced"
        assert out["value_verdict"] == "VALUE"
        assert out["best_odds"] == 1.85
        assert out["line_movement"]["direction"] == DIR_TOWARD_OVER

    def test_manual_odds_required_when_model_known_but_no_odds(self):
        out = evaluate_market(decimal_odds=None, model_probability=0.55)
        assert out["market_status"] == "manual_odds_required"
        assert out["best_odds"] is None
        assert out["value_verdict"] == "UNKNOWN"

    def test_no_odds_when_nothing_provided(self):
        out = evaluate_market()
        assert out["market_status"] == "no_odds"

    def test_picks_best_bookmaker_over_decimal(self):
        out = evaluate_market(
            decimal_odds=1.80,
            bookmaker_quotes=[
                {"bookmaker": "A", "odds": 1.90},
                {"bookmaker": "B", "odds": 1.95},
            ],
            model_probability=0.55,
        )
        assert out["best_odds"] == 1.95
        assert out["best_bookmaker"] == "B"


# ── Ghost edge L5/L15 verifier ──────────────────────────────────────
class TestGhostEdgeVerifier:
    @pytest.mark.asyncio
    async def test_under_flagged_when_l5_runs_far_above_expected(self):
        ctx = {}
        rrs = {
            "total_runs_avg_last_5":  10.5,
            "total_runs_avg_last_15": 9.0,
        }
        out = await verify_model_inputs(
            db=None, scoring_ctx=ctx,
            expected_runs=7.0,
            recommended_market="Under 9.5",
            recent_run_split=rrs,
        )
        flags = [d.get("flag") for d in out["discrepancies"]]
        assert "GHOST_EDGE_UNDER_VS_L5_HIGH_SCORING" in flags
        assert out["confidence_penalty"] >= 18

    @pytest.mark.asyncio
    async def test_over_flagged_when_l5_below_expected(self):
        ctx = {}
        rrs = {
            "total_runs_avg_last_5":  4.0,
            "total_runs_avg_last_15": 7.0,
        }
        out = await verify_model_inputs(
            db=None, scoring_ctx=ctx,
            expected_runs=9.5,
            recommended_market="Over 8.5",
            recent_run_split=rrs,
        )
        flags = [d.get("flag") for d in out["discrepancies"]]
        assert "GHOST_EDGE_OVER_VS_L5_LOW_SCORING" in flags

    @pytest.mark.asyncio
    async def test_no_recent_data_means_no_extra_penalty(self):
        out = await verify_model_inputs(
            db=None, scoring_ctx={},
            expected_runs=7.0,
            recommended_market="Under 9.5",
        )
        # No recent_run_split provided → no L5/L15 flags.
        assert all("GHOST_EDGE" not in (d.get("flag") or "") for d in out["discrepancies"])

    @pytest.mark.asyncio
    async def test_recent_run_trend_contradicts_under(self):
        # L5 = 9.0, L15 = 6.5 → +2.5 trend rising vs Under pick.
        rrs = {
            "total_runs_avg_last_5":  9.0,
            "total_runs_avg_last_15": 6.5,
        }
        out = await verify_model_inputs(
            db=None, scoring_ctx={},
            expected_runs=7.5,
            recommended_market="Under 9.5",
            recent_run_split=rrs,
        )
        flags = [d.get("flag") for d in out["discrepancies"]]
        assert "RECENT_RUN_TREND_CONTRADICTS_UNDER" in flags

    @pytest.mark.asyncio
    async def test_rising_on_base_pressure_flags_under(self):
        out = await verify_model_inputs(
            db=None, scoring_ctx={},
            expected_runs=7.0,
            recommended_market="Under 9.5",
            on_base_profile={"combined": {"times_on_base_delta_5_vs_15": 2.8}},
        )
        flags = [d.get("flag") for d in out["discrepancies"]]
        assert "GHOST_EDGE_RISING_ON_BASE_VS_UNDER" in flags

    @pytest.mark.asyncio
    async def test_f5_ghost_edge(self):
        out = await verify_model_inputs(
            db=None, scoring_ctx={},
            expected_runs=7.0,    # F5 expected ≈ 3.85
            recommended_market="F5 Under 4.5",
            f5_split={"combined": {"f5_runs_avg_last_5": 5.5}},
        )
        flags = [d.get("flag") for d in out["discrepancies"]]
        assert "GHOST_EDGE_F5_UNDER_VS_L5" in flags

    @pytest.mark.asyncio
    async def test_penalty_capped_at_45(self):
        # Stack every ghost-edge flag and confirm cap holds.
        ctx = {
            "active_series_context": {"total_runs_avg": 14, "games_in_series": 3},
            "home_runs_per_game_model": 3.0,
            "home_batting": {"runs_per_game": 5.0},
        }
        rrs = {"total_runs_avg_last_5": 12.0, "total_runs_avg_last_15": 8.0}
        ob  = {"combined": {"times_on_base_delta_5_vs_15": 4.0}}
        out = await verify_model_inputs(
            db=None, scoring_ctx=ctx,
            expected_runs=6.0,
            recommended_market="Under 9.5",
            recent_run_split=rrs,
            on_base_profile=ob,
        )
        assert out["confidence_penalty"] == 45  # cap
