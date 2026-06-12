"""Phase F65 — watchlist_odds_needed backtest scorer smoke tests.

Validates the pure-functional scorer end-to-end against the synthetic
demo dataset shipped with the module + a battery of edge cases.
"""
from __future__ import annotations

import pytest

from services.watchlist_odds_backtest import (
    ENGINE_VERSION,
    _synthetic_demo_dataset,
    best_positive_snapshot,
    did_rescued_market_win,
    edge_pct,
    empty_report,
    hours_to_first_positive_edge,
    implied_probability,
    run_watchlist_backtest,
)


# ─────────────────────────────────────────────────────────────────────
# Math helpers
# ─────────────────────────────────────────────────────────────────────
def test_implied_probability_basic() -> None:
    assert abs(implied_probability(2.00) - 0.50) < 1e-9
    assert abs(implied_probability(1.50) - 0.6666666) < 1e-3
    assert implied_probability(0.99) is None
    assert implied_probability(None) is None  # type: ignore[arg-type]
    assert implied_probability("nope") is None


def test_edge_pct_basic() -> None:
    # est=0.60, odds=1.50 → imp=0.667 → edge=-6.67%
    assert edge_pct(0.60, 1.50) == -6.67
    # est=0.55, odds=2.00 → imp=0.50 → edge=+5.0%
    assert edge_pct(0.55, 2.00) == 5.0
    assert edge_pct(None, 1.5) is None
    assert edge_pct(0.5, None) is None


# ─────────────────────────────────────────────────────────────────────
# Snapshot helpers
# ─────────────────────────────────────────────────────────────────────
def test_best_positive_snapshot_picks_max_edge() -> None:
    snaps = [
        {"odds": 1.50, "estimated_prob": 0.55},        # edge -11.7%
        {"odds": 1.80, "estimated_prob": 0.55},        # edge -0.6%
        {"odds": 2.10, "estimated_prob": 0.55},        # edge +7.4% ← winner
        {"odds": 1.90, "estimated_prob": 0.55},        # edge +2.4%
    ]
    best = best_positive_snapshot(snaps)
    assert best is not None
    assert best["odds"] == 2.10


def test_best_positive_snapshot_returns_none_when_all_negative() -> None:
    snaps = [
        {"odds": 1.50, "estimated_prob": 0.55},
        {"odds": 1.60, "estimated_prob": 0.55},
    ]
    assert best_positive_snapshot(snaps) is None


def test_hours_to_first_positive_edge_returns_first_crossing() -> None:
    snaps = [
        {"captured_at": "2026-06-14T10:00:00Z", "odds": 1.50, "estimated_prob": 0.55},
        {"captured_at": "2026-06-14T14:00:00Z", "odds": 1.70, "estimated_prob": 0.55},   # still negative
        {"captured_at": "2026-06-14T18:00:00Z", "odds": 1.90, "estimated_prob": 0.55},   # +1.97% ← first positive
        {"captured_at": "2026-06-14T22:00:00Z", "odds": 2.10, "estimated_prob": 0.55},
    ]
    h = hours_to_first_positive_edge(snaps)
    assert h == 8.0


def test_hours_to_first_positive_edge_handles_no_cross() -> None:
    snaps = [
        {"captured_at": "2026-06-14T10:00:00Z", "odds": 1.50, "estimated_prob": 0.55},
        {"captured_at": "2026-06-14T22:00:00Z", "odds": 1.65, "estimated_prob": 0.55},
    ]
    assert hours_to_first_positive_edge(snaps) is None


# ─────────────────────────────────────────────────────────────────────
# Settlement classifier
# ─────────────────────────────────────────────────────────────────────
def test_settlement_corners_over_wins() -> None:
    rescued = {"market": "Total corners Over", "family": "CORNERS", "line": 9.5}
    assert did_rescued_market_win(rescued, {"final_corners_total": 12}) is True
    assert did_rescued_market_win(rescued, {"final_corners_total": 5})  is False
    # Push → None (not a win, not a loss).
    assert did_rescued_market_win(rescued, {"final_corners_total": 9.5}) is None


def test_settlement_corners_under_wins() -> None:
    rescued = {"market": "Total corners Under", "family": "CORNERS", "line": 9.5}
    assert did_rescued_market_win(rescued, {"final_corners_total": 6})  is True
    assert did_rescued_market_win(rescued, {"final_corners_total": 12}) is False


def test_settlement_goals_over_under() -> None:
    over = {"market": "Goals Over",  "family": "GOALS", "line": 2.5}
    under = {"market": "Goals Under", "family": "GOALS", "line": 2.5}
    assert did_rescued_market_win(over,  {"final_goals_total": 3}) is True
    assert did_rescued_market_win(over,  {"final_goals_total": 1}) is False
    assert did_rescued_market_win(under, {"final_goals_total": 1}) is True
    assert did_rescued_market_win(under, {"final_goals_total": 4}) is False


def test_settlement_missing_data_returns_none() -> None:
    assert did_rescued_market_win(None,  {"final_corners_total": 10}) is None
    assert did_rescued_market_win({}, {}) is None
    assert did_rescued_market_win({"market": "Spread", "family": "OTHER"}, {}) is None


# ─────────────────────────────────────────────────────────────────────
# Full report — empty input and synthetic demo dataset.
# ─────────────────────────────────────────────────────────────────────
def test_run_watchlist_backtest_empty_input_returns_empty_report() -> None:
    report = run_watchlist_backtest([], {}, {})
    assert report["engine_version"]   == ENGINE_VERSION
    assert report["n_picks_total"]    == 0
    assert report["hit_rate_pct"]     is None
    assert report["roi_pct"]          is None
    assert report["per_family"]       == {}
    assert report["per_league_tier"]  == {}
    assert "no_picks_in_window" in report["notes"]


def test_run_watchlist_backtest_synthetic_demo() -> None:
    picks, settlements, snapshots = _synthetic_demo_dataset()
    report = run_watchlist_backtest(picks, settlements, snapshots, flat_stake=1.0)

    # 5 picks, 4 settled (m4 unsettled), m5 is a push → 3 with decision.
    assert report["n_picks_total"]    == 5
    assert report["n_picks_settled"]  == 4
    # m1 wins (corners over), m2 loses (corners under), m3 wins (goals over),
    # m5 is a push (None outcome → not counted).
    assert report["n_picks_won"]      == 2
    assert report["n_picks_lost"]     == 1
    # m1 has a snapshot at odds 1.85 with estimated 0.58 → settle odds=1.85.
    # m3 has NO snapshots so settles at pick odds (1.70).
    # Stake = 3 (only 3 picks had a decisive outcome). Returned = 1.85 + 1.70 = 3.55.
    # ROI = (3.55 - 3) / 3 = 18.33%.
    assert report["hit_rate_pct"]    == pytest.approx(66.67, rel=1e-2)
    assert report["roi_pct"]         == pytest.approx(18.33, abs=0.1)

    # Average edge at pick across the 5 picks (negative band, as expected).
    assert report["avg_edge_at_pick"] is not None
    assert report["avg_edge_at_pick"] < 0

    # m1 crosses positive only at the 20:00 snapshot (odds 1.85, est 0.58
    # → imp 0.541, edge ~+3.9%; the 16:00 snapshot at 1.65 is still
    # negative since imp ~0.606 > est 0.58). 20:00 - 10:00 = 10h.
    assert report["median_hours_to_positive_edge"] is not None
    assert report["median_hours_to_positive_edge"] == pytest.approx(10.0, abs=0.1)

    # Per-family breakdown — CORNERS has 3 settled (m1 win, m2 loss, m5 push),
    # GOALS has 1 settled (m3 win).
    fam = report["per_family"]
    assert "CORNERS" in fam and "GOALS" in fam
    assert fam["CORNERS"]["n"]   == 2   # m1, m2 (push m5 dropped before counting)
    assert fam["CORNERS"]["won"] == 1
    assert fam["GOALS"]["n"]     == 1
    assert fam["GOALS"]["won"]   == 1

    # Per-tier breakdown.
    tier = report["per_league_tier"]
    assert "Tier 1" in tier         # La Liga (m1), Bundesliga (m2)
    assert tier["Tier 1"]["n"]   == 2
    assert tier["Tier 1"]["won"] == 1


def test_run_watchlist_backtest_handles_unsettled_picks() -> None:
    picks = [{
        "match_id": "future", "league": "Premier League",
        "edge_pct": -15.0, "estimated_prob": 0.5, "odds": 1.5,
        "rescued_market": {"market": "Total corners Over", "family": "CORNERS",
                           "line": 9.5},
    }]
    report = run_watchlist_backtest(picks, {}, {})
    assert report["n_picks_total"]   == 1
    assert report["n_picks_settled"] == 0
    assert report["n_picks_won"]     == 0
    assert report["n_picks_lost"]    == 0
    assert report["hit_rate_pct"]    is None
    assert "no_settlements_yet" in report["notes"]


def test_run_watchlist_backtest_handles_only_pushes() -> None:
    """All settled picks land on the line → hit_rate should be None,
    not 0 (we don't want to penalise the engine for push-prone matches)."""
    picks = [{
        "match_id": "p1", "league": "MLS",
        "edge_pct": -10.0, "estimated_prob": 0.5, "odds": 2.0,
        "rescued_market": {"market": "Total corners Over", "family": "CORNERS",
                           "line": 9.5},
    }]
    report = run_watchlist_backtest(picks, {"p1": {"final_corners_total": 9.5}}, {})
    assert report["n_picks_settled"] == 1
    assert report["n_picks_won"]     == 0
    assert report["n_picks_lost"]    == 0
    assert report["hit_rate_pct"]    is None
    assert report["roi_pct"]         is None


def test_run_watchlist_backtest_fail_soft_on_garbage() -> None:
    # Each pick is deliberately malformed.
    picks = [
        {},                                       # nothing at all
        {"match_id": "x"},                        # no rescued_market
        {"rescued_market": "not a dict"},         # type error
    ]
    report = run_watchlist_backtest(picks, {}, {})    # MUST NOT raise
    assert isinstance(report, dict)
    assert report["n_picks_total"] == 3
    assert report["n_picks_settled"] == 0


def test_empty_report_is_serialisable() -> None:
    import json
    rep = empty_report()
    s = json.dumps(rep)              # MUST NOT raise
    assert isinstance(s, str)
    assert ENGINE_VERSION in s
