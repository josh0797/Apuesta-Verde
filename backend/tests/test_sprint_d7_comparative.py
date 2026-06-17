"""Sprint-D7 · Tests for the comparative-backtest scaffolding.

All tests are **offline**: they inject a fake transport into the
historical client and a fake picks list into the orchestrator. No
network, no credit spend.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from pathlib import Path
from unittest.mock import patch

import pytest

from services import theoddsapi_historical_client as histc
from scripts import run_backtest_d7_comparative as orch


# ════════════════════════════════════════════════════════════════════════
# Fake HTTP transport
# ════════════════════════════════════════════════════════════════════════
def _make_http(events_per_date=1, used_start=100, used_step=10,
                fail_after=None):
    """Build an async fake HTTP that increments x-requests-used on every
    call. ``fail_after`` (int) returns a 502 after N calls."""
    state = {"calls": 0, "used": used_start}

    async def _http(url, params, **kwargs):
        state["calls"] += 1
        if fail_after is not None and state["calls"] > fail_after:
            return {"ok": False, "status": 502, "json": None,
                    "headers": {"x-requests-used": str(state["used"])}}
        # Events listing call.
        if "/events?" not in url and url.endswith("/events"):
            state["used"] += used_step
            data = [
                {"id": f"evt-{i}-{state['calls']}",
                 "commence_time": "2024-06-20T15:00:00Z",
                 "home_team": "A", "away_team": "B"}
                for i in range(events_per_date)
            ]
            return {"ok": True, "status": 200,
                    "json": {"data": data, "timestamp": "x"},
                    "headers": {"x-requests-used": str(state["used"])}}
        # Event odds call (10 credits in reality).
        state["used"] += used_step
        return {"ok": True, "status": 200,
                "json": {"data": {"bookmakers": []}, "timestamp": "y"},
                "headers": {"x-requests-used": str(state["used"])}}
    return _http, state


# ════════════════════════════════════════════════════════════════════════
# 1) Credit-cap aborts before overspend
# ════════════════════════════════════════════════════════════════════════
def test_credit_cap_aborts_before_overspend():
    """Cap=50 → should abort mid-way without raising."""
    http, _state = _make_http(events_per_date=10, used_start=0, used_step=10)
    dates = [f"2024-06-{d:02d}T00:00:00Z" for d in range(1, 6)]
    res = asyncio.run(histc.fetch_tournament_pit_odds(
        sport_key="soccer_test",
        dates_iso=dates, max_credits=50,
        api_key="fake",
        http=http,
    ))
    assert res["aborted"] is True
    assert "MAX_CREDITS_REACHED" in res["reason_codes"]
    assert res["credits_used"] >= 50


# ════════════════════════════════════════════════════════════════════════
# 2) odds_type is marked per block (compile-time invariant)
# ════════════════════════════════════════════════════════════════════════
def test_odds_type_marked_per_block():
    # Domestic uses parse_football_data_csv with prefer_closing=False →
    # OPENING. The orchestrator hard-codes "OPENING" / "POINT_IN_TIME_PREMATCH".
    src = Path(__file__).resolve().parents[1] / "scripts" / "run_backtest_d7_comparative.py"
    code = src.read_text()
    assert "OPENING" in code
    assert "POINT_IN_TIME_PREMATCH" in code


# ════════════════════════════════════════════════════════════════════════
# 3) Combined comparison warns on odds-type mismatch
# ════════════════════════════════════════════════════════════════════════
def test_combined_comparison_warns_on_odds_type_mismatch():
    domestic = {"odds_type": "OPENING",
                 "per_league": {
                     "x": {"available": True,
                           "metrics": {"n_bets": 100, "roi": 0.05,
                                        "hit_rate": 0.30,
                                        "is_roi_significant": False}}}}
    national = {"odds_type": "POINT_IN_TIME_PREMATCH",
                 "per_tournament": {
                     "y": {"available": True,
                           "metrics": {"n_bets": 30, "roi": 0.10,
                                        "hit_rate": 0.40,
                                        "is_roi_significant": True}}}}
    out = orch.build_combined_comparison(domestic, national, all_picks=[])
    assert "W_ODDS_TYPE_MISMATCH" in out["warnings"]


# ════════════════════════════════════════════════════════════════════════
# 4) Cohort defined ONLY by pre-match features (anti-overfitting)
# ════════════════════════════════════════════════════════════════════════
def test_cohort_defined_by_prematch_only():
    """``detect_cohorts`` must never read ``fthg``/``ftag``/``ftr``."""
    import inspect
    from services import football_cohort_detector as fcd
    src = inspect.getsource(fcd.detect_cohorts)
    # The detector receives the features dict — assert it doesn't read
    # post-match keys from the pick either.
    forbidden = ("fthg", "ftag", "ftr", "actual_outcome",
                  "match_result", "_outcome")
    for k in forbidden:
        assert k not in src, f"detect_cohorts must not reference {k!r}"

    # Bonus runtime check: call detect_cohorts with a pick that DOES
    # carry fthg/ftag and confirm the resulting tags don't depend on them.
    pick = {"prediction": 0.34, "edge_pp": 9.0,
             "fthg": 1, "ftag": 1}      # post-match noise
    feats = {"elo_diff": 220, "stage": "GROUP_STAGE",
              "favorite_implied": 0.55}
    tags_a = fcd.detect_cohorts(pick, feats)
    pick_no_post = {k: v for k, v in pick.items()
                     if k not in ("fthg", "ftag")}
    tags_b = fcd.detect_cohorts(pick_no_post, feats)
    assert sorted(tags_a) == sorted(tags_b)


# ════════════════════════════════════════════════════════════════════════
# 5) Pattern not proven when sample is small
# ════════════════════════════════════════════════════════════════════════
def test_pattern_not_proven_when_sample_small():
    domestic = {"odds_type": "OPENING", "per_league": {}}
    national = {"odds_type": "POINT_IN_TIME_PREMATCH",
                 "per_tournament": {}}

    # Inject a fake summary into summarise_picks_by_cohort.
    def _fake_summary(_picks):
        return {
            "DOMINANT_FAVORITE_DRAW_VALUE+TOURNAMENT_GROUP_STAGE_DRAW_VALUE": {
                "n": 7,
                "metrics": {"roi_ci_low": 0.04, "roi": 0.15},
            },
        }
    with patch.object(orch, "summarise_picks_by_cohort", _fake_summary):
        out = orch.build_combined_comparison(domestic, national,
                                                all_picks=[{"_x": 1}])
    assert out["spain_capeverde_pattern"]["status"] == (
        "PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE"
    )


def test_pattern_repeatable_with_enough_sample():
    def _fake_summary(_picks):
        return {
            "DOMINANT_FAVORITE_DRAW_VALUE+TOURNAMENT_GROUP_STAGE_DRAW_VALUE": {
                "n": 35,
                "metrics": {"roi_ci_low": 0.07, "roi": 0.18},
            },
        }
    with patch.object(orch, "summarise_picks_by_cohort", _fake_summary):
        out = orch.build_combined_comparison(
            {"odds_type": "OPENING", "per_league": {}},
            {"odds_type": "POINT_IN_TIME_PREMATCH", "per_tournament": {}},
            all_picks=[{"_x": 1}],
        )
    assert out["spain_capeverde_pattern"]["status"] == "PATTERN_REPEATABLE"


# ════════════════════════════════════════════════════════════════════════
# 6) Tournament unavailable does not abort the sprint
# ════════════════════════════════════════════════════════════════════════
def test_national_tournament_unavailable_does_not_abort():
    """Returning an empty events payload must mark the tournament as
    ``UNAVAILABLE_NO_COVERAGE`` without raising or stopping the loop."""
    async def _empty_http(url, params, **kwargs):
        return {"ok": False, "status": 404, "json": None,
                "headers": {"x-requests-used": "200"}}

    res = asyncio.run(histc.fetch_tournament_pit_odds(
        sport_key="soccer_no_coverage",
        dates_iso=["2021-06-15T12:00:00Z"],
        max_credits=200, api_key="fake", http=_empty_http,
    ))
    assert res["events"] == []
    assert "UNAVAILABLE_NO_COVERAGE" in res["reason_codes"]
    assert res["aborted"] is False


# ════════════════════════════════════════════════════════════════════════
# 7) Settlement uses openfootball, NOT The Odds API
# ════════════════════════════════════════════════════════════════════════
def test_settlement_uses_openfootball_not_oddsapi():
    """``_merge_pit_odds_with_truth`` discards any odds-API row that
    lacks a matching openfootball ground-truth row → settlement is
    impossible from the odds payload alone."""
    odds_events = [{
        "event_id": "e1", "home_team": "A", "away_team": "B",
        "event_payload": {"bookmakers": [{
            "markets": [{"key": "h2h", "outcomes": [
                {"name": "A", "price": 2.0},
                {"name": "B", "price": 3.0},
                {"name": "Draw", "price": 3.5},
            ]}],
        }]},
    }]
    # No matching truth row → must drop the event silently.
    assert orch._merge_pit_odds_with_truth(odds_events, []) == []

    truth = [{"home_team": "A", "away_team": "B",
              "fthg": 1, "ftag": 1, "ftr": "D"}]
    merged = orch._merge_pit_odds_with_truth(odds_events, truth)
    assert len(merged) == 1
    # Ground truth came from openfootball, not the odds payload.
    assert merged[0]["fthg"] == 1 and merged[0]["ftag"] == 1
    assert merged[0]["odds_type"] == "POINT_IN_TIME_PREMATCH"


# ════════════════════════════════════════════════════════════════════════
# 8) Cap pre-aborts a future call without re-spending
# ════════════════════════════════════════════════════════════════════════
def test_cap_short_circuits_next_call():
    tracker = histc.CreditTracker(max_credits=10)
    tracker.update(0)
    tracker.update(15)        # already over cap
    assert tracker.must_abort()
    res = asyncio.run(histc.fetch_events_for_date(
        sport_key="x", date_iso="2024-01-01T00:00:00Z",
        tracker=tracker, api_key="k", http=None,
    ))
    assert res["available"] is False
    assert res["reason_code"] == "MAX_CREDITS_REACHED"
