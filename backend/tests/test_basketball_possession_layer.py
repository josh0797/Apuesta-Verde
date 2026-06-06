"""Tests for ``services/basketball_possession_layer.py`` (Phase 37).

Covers:
  * estimate_possessions — happy path + missing keys
  * calculate_four_factors — eFG / TOV / ORB / FTr
  * calculate_team_efficiency_profile — aggregation + missing data
  * calculate_matchup_possession_context — projection + pace env
  * derive_basketball_market_adjustments — leans + reason codes
  * build_basketball_possession_profile — top-level fail-soft wrapper
"""
from services import basketball_possession_layer as bpl


# ──────────────────────────────────────────────────────────────────
# estimate_possessions
# ──────────────────────────────────────────────────────────────────
def test_estimate_possessions_basic_formula():
    # Dean Oliver: FGA + 0.475*FTA - ORB + TOV
    game = {"fga": 80, "fta": 20, "orb": 10, "tov": 12}
    # 80 + 9.5 - 10 + 12 = 91.5
    assert bpl.estimate_possessions(game) == 91.5


def test_estimate_possessions_with_opponent_averages_both_sides():
    game = {
        "fga": 80, "fta": 20, "orb": 10, "tov": 12,
        "opp_fga": 82, "opp_fta": 18, "opp_orb": 12, "opp_tov": 14,
    }
    # team_poss = 91.5
    # opp_poss  = 82 + 8.55 - 12 + 14 = 92.55
    # avg       = (91.5 + 92.55) / 2 = 92.025 → 92.02 / 92.03 depending on rounding
    assert bpl.estimate_possessions(game) in (92.02, 92.03)


def test_estimate_possessions_missing_keys_returns_none():
    assert bpl.estimate_possessions({"fga": 80}) is None
    assert bpl.estimate_possessions(None) is None
    assert bpl.estimate_possessions({}) is None


# ──────────────────────────────────────────────────────────────────
# calculate_four_factors
# ──────────────────────────────────────────────────────────────────
def test_four_factors_full_shape():
    ts = {
        "fgm": 40, "fga": 85, "three_pm": 12,
        "fta": 22, "tov": 13,
        "orb": 12, "opp_drb": 30,
    }
    ff = bpl.calculate_four_factors(ts)
    # eFG = (40 + 6) / 85 ≈ 0.5412
    assert ff["efg"] == 0.5412
    # TOV% = 13 / (85 + 0.44*22 + 13) = 13 / 107.68 ≈ 0.1207
    assert ff["tov_rate"] == 0.1207
    # ORB% = 12 / (12 + 30) = 0.2857
    assert ff["orb_rate"] == 0.2857
    # FTr  = 22 / 85 = 0.2588
    assert ff["ft_rate"] == 0.2588


def test_four_factors_missing_inputs_return_none():
    ff = bpl.calculate_four_factors({})
    assert ff == {"efg": None, "tov_rate": None, "orb_rate": None, "ft_rate": None}


# ──────────────────────────────────────────────────────────────────
# calculate_team_efficiency_profile
# ──────────────────────────────────────────────────────────────────
def _sample_game(scale: float = 1.0) -> dict:
    return {
        "pts_for":      int(110 * scale),
        "pts_against":  int(106 * scale),
        "fga":          85,
        "fgm":          40,
        "three_pa":     35,
        "three_pm":     13,
        "fta":          22,
        "tov":          13,
        "orb":          11,
        "drb":          33,
        "opp_fga":      82,
        "opp_fta":      18,
        "opp_orb":      10,
        "opp_tov":      14,
        "opp_drb":      30,
        "minutes":      48,
    }


def test_team_efficiency_profile_aggregates_metrics():
    games = [_sample_game(scale=1.0) for _ in range(5)]
    prof = bpl.calculate_team_efficiency_profile(games)
    assert prof["sample_size"] == 5
    assert prof["pace"] is not None and prof["pace"] > 80
    assert prof["offensive_rating"] is not None
    assert prof["defensive_rating"] is not None
    assert prof["net_rating"] is not None
    assert prof["efg"] is not None
    assert prof["tov_rate"] is not None
    assert prof["orb_rate"] is not None
    assert prof["ft_rate"] is not None
    assert prof["avg_points_for"] == 110.0
    assert prof["missing_data"] is False  # sample_size >= 4


def test_team_efficiency_profile_low_sample_marks_missing():
    games = [_sample_game()]
    prof = bpl.calculate_team_efficiency_profile(games)
    assert prof["sample_size"] == 1
    assert prof["missing_data"] is True  # below MIN_GAMES_FULL_PROFILE


def test_team_efficiency_profile_empty_input():
    assert bpl.calculate_team_efficiency_profile([]) == {
        "sample_size": 0, "missing_data": True,
    }
    assert bpl.calculate_team_efficiency_profile(None) == {
        "sample_size": 0, "missing_data": True,
    }


# ──────────────────────────────────────────────────────────────────
# calculate_matchup_possession_context
# ──────────────────────────────────────────────────────────────────
def _profile(*, pace: float, ortg: float, drtg: float, sample: int = 6) -> dict:
    return {
        "sample_size":      sample,
        "pace":             pace,
        "offensive_rating": ortg,
        "defensive_rating": drtg,
        "net_rating":       round(ortg - drtg, 2),
        "efg":              0.52,
        "tov_rate":         0.13,
        "orb_rate":         0.27,
        "ft_rate":          0.22,
        "three_pa_rate":    0.38,
        "three_p_pct_variance": 0.03,
        "total_points_std": 9.0,
    }


def test_matchup_context_high_pace_environment():
    home = _profile(pace=104.0, ortg=115.0, drtg=108.0)
    away = _profile(pace=106.0, ortg=114.0, drtg=110.0)
    ctx = bpl.calculate_matchup_possession_context(home, away)
    assert ctx["pace_environment"] == "HIGH"
    assert ctx["projected_possessions"] == 105.0
    assert ctx["projected_total_points"] > 200


def test_matchup_context_low_pace_environment():
    home = _profile(pace=92.0, ortg=108.0, drtg=110.0)
    away = _profile(pace=90.0, ortg=106.0, drtg=109.0)
    ctx = bpl.calculate_matchup_possession_context(home, away)
    assert ctx["pace_environment"] == "LOW"


def test_matchup_context_efficiency_edge_home():
    home = _profile(pace=100.0, ortg=118.0, drtg=105.0)  # net +13
    away = _profile(pace=100.0, ortg=108.0, drtg=112.0)  # net -4
    ctx = bpl.calculate_matchup_possession_context(home, away)
    assert ctx["efficiency_edge"] == "home"
    assert ctx["net_rating_edge"] > 0


def test_matchup_context_low_sample_returns_empty():
    home = _profile(pace=100, ortg=110, drtg=110, sample=0)
    away = _profile(pace=100, ortg=110, drtg=110, sample=6)
    ctx = bpl.calculate_matchup_possession_context(home, away)
    assert ctx.get("missing_data") is True


# ──────────────────────────────────────────────────────────────────
# derive_basketball_market_adjustments
# ──────────────────────────────────────────────────────────────────
def test_derive_market_adjustments_over_supported_high_pace():
    home = _profile(pace=104, ortg=118, drtg=108)
    home["efg"] = 0.58
    home["orb_rate"] = 0.32
    away = _profile(pace=106, ortg=116, drtg=110)
    away["efg"] = 0.56
    away["orb_rate"] = 0.31
    ctx = bpl.calculate_matchup_possession_context(home, away)
    out = bpl.derive_basketball_market_adjustments(
        ctx, home_profile=home, away_profile=away,
        bookmaker_total_line=200.0,
    )
    assert out["total_points_lean"] == "OVER"
    assert bpl.RC_HIGH_PACE_ENVIRONMENT in out["reason_codes"]
    assert bpl.RC_TOTAL_OVER_SUPPORTED in out["reason_codes"]
    assert bpl.RC_OFFENSIVE_REBOUND_EDGE in out["reason_codes"]


def test_derive_market_adjustments_under_supported_low_pace():
    home = _profile(pace=90, ortg=104, drtg=110)
    home["efg"] = 0.46
    home["tov_rate"] = 0.17
    away = _profile(pace=88, ortg=102, drtg=108)
    away["efg"] = 0.45
    away["tov_rate"] = 0.18
    ctx = bpl.calculate_matchup_possession_context(home, away)
    out = bpl.derive_basketball_market_adjustments(
        ctx, home_profile=home, away_profile=away,
        bookmaker_total_line=215.0,
    )
    assert out["total_points_lean"] == "UNDER"
    assert bpl.RC_LOW_PACE_ENVIRONMENT in out["reason_codes"]
    assert bpl.RC_TOTAL_UNDER_SUPPORTED in out["reason_codes"]
    assert bpl.RC_TURNOVER_RISK in out["reason_codes"]


def test_derive_market_adjustments_strong_net_supports_spread():
    home = _profile(pace=100, ortg=120, drtg=104)  # net +16
    away = _profile(pace=100, ortg=106, drtg=114)  # net -8
    ctx = bpl.calculate_matchup_possession_context(home, away)
    out = bpl.derive_basketball_market_adjustments(
        ctx, home_profile=home, away_profile=away,
        bookmaker_spread_line=-6.5,
    )
    assert out["moneyline_lean"] == "home"
    # Should either support spread OR flag moneyline-safer if margin doesn't beat.
    assert (
        out["spread_support"] == "home"
        or bpl.RC_MONEYLINE_SAFER_THAN_SPREAD in out["reason_codes"]
    )


def test_derive_market_adjustments_three_point_variance_flag():
    home = _profile(pace=100, ortg=110, drtg=108)
    home["three_pa_rate"] = 0.48      # very high 3PA volume
    home["three_p_pct_variance"] = 0.08
    away = _profile(pace=100, ortg=110, drtg=108)
    away["three_pa_rate"] = 0.46
    away["three_p_pct_variance"] = 0.07
    ctx = bpl.calculate_matchup_possession_context(home, away)
    out = bpl.derive_basketball_market_adjustments(
        ctx, home_profile=home, away_profile=away,
    )
    assert bpl.RC_THREE_POINT_VARIANCE_RISK in out["reason_codes"]
    # Fragility should reflect the risk.
    assert out["fragility_score"] >= 15


def test_derive_market_adjustments_data_insufficient_fallback():
    out = bpl.derive_basketball_market_adjustments({"missing_data": True})
    assert out["total_points_lean"] == "NEUTRAL"
    assert out["spread_support"] == "none"
    assert out["fragility_score"] == 100
    assert bpl.RC_DATA_INSUFFICIENT_FALLBACK in out["reason_codes"]


# ──────────────────────────────────────────────────────────────────
# build_basketball_possession_profile (top-level fail-soft)
# ──────────────────────────────────────────────────────────────────
def test_build_full_profile_available_true():
    games_h = [_sample_game() for _ in range(6)]
    games_a = [_sample_game() for _ in range(6)]
    payload = bpl.build_basketball_possession_profile(games_h, games_a)
    profile = payload["basketball_possession_profile"]
    assert profile["available"] is True
    assert profile["matchup"]["pace_environment"] in ("LOW", "MODERATE", "HIGH")
    assert "reason_codes" in profile["matchup"]
    assert profile["_engine_version"] == bpl.ENGINE_VERSION


def test_build_profile_missing_data_returns_available_false():
    payload = bpl.build_basketball_possession_profile([], [])
    profile = payload["basketball_possession_profile"]
    assert profile["available"] is False
    assert profile["matchup"]["reason_codes"] == [bpl.RC_DATA_INSUFFICIENT_FALLBACK]
    assert profile["_engine_version"] == bpl.ENGINE_VERSION


def test_build_profile_uses_historical_fallback_when_sample_empty():
    fallback = {
        "gamesAnalyzed": 5,
        "paceProxy":     97.0,
        "pointsForAvg":  108.0,
        "pointsAgainstAvg": 106.0,
    }
    payload = bpl.build_basketball_possession_profile(
        None, None,
        home_fallback=fallback, away_fallback=fallback,
    )
    profile = payload["basketball_possession_profile"]
    # Fallback should produce a usable matchup (available True).
    assert profile["available"] is True
    assert profile["home"]["_source"] == "historical_fallback"
    assert profile["away"]["_source"] == "historical_fallback"
    assert profile["matchup"]["projected_total_points"] is not None


def test_build_profile_never_raises_on_bad_input():
    # Should NOT throw — should return an empty profile.
    payload = bpl.build_basketball_possession_profile("not-a-list", 123)  # type: ignore[arg-type]
    profile = payload["basketball_possession_profile"]
    assert profile["available"] is False
