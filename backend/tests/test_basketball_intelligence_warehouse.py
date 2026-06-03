"""Tests for services.basketball_intelligence_warehouse (Fix 1).

Strict isolation from MLB: imports MUST work without pulling MLB
warehouse. Pattern keys are basketball-specific.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.basketball_intelligence_warehouse import (
    PATTERN_SAMPLE_NO_ADJUST, PATTERN_SAMPLE_MODERATE,
    PATTERN_MAX_ADJUSTMENT_MODERATE, PATTERN_MAX_ADJUSTMENT_STRONG,
    RC_PATTERN_LOW_SAMPLE, RC_PATTERN_MODERATE_BOOST,
    RC_PATTERN_STRONG_BOOST, RC_PATTERN_NEGATIVE_ROI,
    RC_PATTERN_NO_MATCH, RC_WAREHOUSE_DISABLED,
    derive_pattern_keys, _compute_pattern_adjustment,
    lookup_pattern_match, attach_pattern_match_to_payload,
    persist_market_result, update_pattern_memory_from_result,
    persist_game_intelligence_snapshot,
    load_team_profile, upsert_team_profile,
    load_player_profile, upsert_player_profile,
)


# ─────────────────────────────────────────────────────────────────────
# derive_pattern_keys — basketball-specific
# ─────────────────────────────────────────────────────────────────────
def test_derive_empty_payload():
    assert derive_pattern_keys({}) == []
    assert derive_pattern_keys(None) == []


def test_derive_high_pace():
    pp = {"home_team_profile": {"pace": 106}, "away_team_profile": {"pace": 104}}
    assert "HIGH_PACE_OVER_PROFILE" in derive_pattern_keys(pp)


def test_derive_low_pace():
    pp = {"home_team_profile": {"pace": 92}, "away_team_profile": {"pace": 94}}
    assert "LOW_PACE_UNDER_PROFILE" in derive_pattern_keys(pp)


def test_derive_strong_ortg_edge():
    pp = {
        "home_team_profile": {"offensive_rating": 120},
        "away_team_profile": {"offensive_rating": 110},
    }
    assert "STRONG_OFFENSIVE_RATING_EDGE" in derive_pattern_keys(pp)


def test_derive_strong_drtg_edge():
    pp = {
        "home_team_profile": {"defensive_rating": 105},
        "away_team_profile": {"defensive_rating": 115},
    }
    assert "STRONG_DEFENSIVE_RATING_EDGE" in derive_pattern_keys(pp)


def test_derive_spread_margin_supported():
    pp = {"_basketball_script": {"marginProjection": 6.0, "spreadCoverProb": 0.60}}
    assert "SPREAD_MARGIN_SUPPORTED" in derive_pattern_keys(pp)


def test_derive_moneyline_safer_than_spread():
    pp = {
        "_basketball_script": {"marginProjection": 2.0, "spreadCoverProb": 0.45},
        "recommendation": {"market": "Spread -3.5"},
    }
    assert "MONEYLINE_SAFER_THAN_SPREAD" in derive_pattern_keys(pp)


def test_derive_live_momentum_favorite():
    pp = {"live_state": {"momentum": {"side": "favorite"}}}
    assert "LIVE_MOMENTUM_FAVORITE" in derive_pattern_keys(pp)


def test_derive_three_point_variance_risk():
    pp = {"home_team_profile": {"three_pt_variance": 0.35}}
    assert "THREE_POINT_VARIANCE_RISK" in derive_pattern_keys(pp)


# ─────────────────────────────────────────────────────────────────────
# Sample-size gates (mirror MLB spec)
# ─────────────────────────────────────────────────────────────────────
def test_adjust_low_sample_returns_zero():
    adj, codes, warn = _compute_pattern_adjustment(sample_size=10, hit_rate=0.8, roi=0.3)
    assert adj == 0.0
    assert RC_PATTERN_LOW_SAMPLE in codes
    assert warn is not None


def test_adjust_moderate_capped_at_plus5():
    adj, codes, _ = _compute_pattern_adjustment(sample_size=30, hit_rate=0.62, roi=0.10)
    assert RC_PATTERN_MODERATE_BOOST in codes
    assert -PATTERN_MAX_ADJUSTMENT_MODERATE <= adj <= PATTERN_MAX_ADJUSTMENT_MODERATE


def test_adjust_moderate_negative_roi():
    adj, codes, _ = _compute_pattern_adjustment(sample_size=30, hit_rate=0.45, roi=-0.05)
    assert adj < 0
    assert RC_PATTERN_NEGATIVE_ROI in codes


def test_adjust_strong_positive_roi():
    adj, codes, _ = _compute_pattern_adjustment(sample_size=60, hit_rate=0.62, roi=0.15)
    assert RC_PATTERN_STRONG_BOOST in codes
    assert 0 < adj <= PATTERN_MAX_ADJUSTMENT_STRONG


def test_adjust_caps_never_exceeded():
    a1, _, _ = _compute_pattern_adjustment(sample_size=200, hit_rate=0.99, roi=1.0)
    a2, _, _ = _compute_pattern_adjustment(sample_size=25, hit_rate=0.99, roi=1.0)
    assert a1 <= PATTERN_MAX_ADJUSTMENT_STRONG
    assert abs(a2) <= PATTERN_MAX_ADJUSTMENT_MODERATE


# ─────────────────────────────────────────────────────────────────────
# Fail-soft (db=None)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_load_team_db_none_returns_none():
    assert await load_team_profile(None, 1) is None


@pytest.mark.asyncio
async def test_upsert_team_db_none_returns_false():
    assert await upsert_team_profile(None, 1, {"x": 1}) is False


@pytest.mark.asyncio
async def test_load_player_db_none_returns_none():
    assert await load_player_profile(None, 99) is None


@pytest.mark.asyncio
async def test_upsert_player_db_none_returns_false():
    assert await upsert_player_profile(None, 99, {"x": 1}) is False


@pytest.mark.asyncio
async def test_lookup_pattern_db_none():
    out = await lookup_pattern_match(None, ["HIGH_PACE_OVER_PROFILE"])
    assert RC_WAREHOUSE_DISABLED in out["reason_codes"]
    assert out["confidence_adjustment"] == 0.0


@pytest.mark.asyncio
async def test_persist_game_snapshot_db_none():
    assert await persist_game_intelligence_snapshot(
        None, game_id="x", match_id=None, home_team_id=None,
        away_team_id=None, pick_payload={"recommendation": {}},
    ) is False


@pytest.mark.asyncio
async def test_persist_market_result_db_none():
    assert await persist_market_result(
        None, game_id="x", pattern_keys=["HIGH_PACE_OVER_PROFILE"],
        market="Over 220.5", stake=1.0, won=True, payout=1.9,
    ) is False


@pytest.mark.asyncio
async def test_attach_pattern_match_db_none_does_not_crash():
    pp = {"home_team_profile": {"pace": 106},
           "away_team_profile": {"pace": 104}}
    summary = await attach_pattern_match_to_payload(None, pp)
    assert "historical_pattern_match" in pp
    assert summary["confidence_adjustment"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# End-to-end with mock Mongo
# ─────────────────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


def _make_db(docs):
    db = MagicMock()
    coll = MagicMock()
    coll.find = MagicMock(return_value=_FakeCursor(docs))
    coll.find_one = AsyncMock(return_value=None)
    coll.replace_one = AsyncMock(return_value=None)
    coll.insert_one = AsyncMock(return_value=None)
    db.__getitem__.return_value = coll
    return db


@pytest.mark.asyncio
async def test_lookup_returns_primary_pattern():
    docs = [
        {"pattern_key": "HIGH_PACE_OVER_PROFILE",
         "sample_size": 55, "wins": 33, "hit_rate": 0.60,
         "total_stake": 55.0, "total_payout": 70.0, "roi": 0.27,
         "best_market": "Over 220.5"},
    ]
    db = _make_db(docs)
    out = await lookup_pattern_match(db, ["HIGH_PACE_OVER_PROFILE"])
    assert out["primary_key"] == "HIGH_PACE_OVER_PROFILE"
    assert out["best_market"] == "Over 220.5"
    assert RC_PATTERN_STRONG_BOOST in out["reason_codes"]
    assert out["confidence_adjustment"] > 0


@pytest.mark.asyncio
async def test_update_pattern_memory_from_result_no_patterns_returns_false():
    db = _make_db([])
    assert await update_pattern_memory_from_result(
        db, pattern_keys=[], market="X", stake=1.0, won=True, payout=1.9,
    ) is False


# ─────────────────────────────────────────────────────────────────────
# SPORT ISOLATION — basketball reason codes are distinct from MLB
# ─────────────────────────────────────────────────────────────────────
def test_reason_codes_isolated_from_mlb():
    # Basketball reason codes must start with "BBALL_" prefix.
    for rc in (RC_PATTERN_LOW_SAMPLE, RC_PATTERN_MODERATE_BOOST,
               RC_PATTERN_STRONG_BOOST, RC_PATTERN_NEGATIVE_ROI,
               RC_PATTERN_NO_MATCH, RC_WAREHOUSE_DISABLED):
        assert rc.startswith("BBALL_"), f"basketball RC must be prefixed: {rc}"


def test_collections_isolated_from_mlb():
    from services.basketball_intelligence_warehouse import (
        COLL_TEAM_DAILY, COLL_PLAYER_DAILY, COLL_GAME_SNAPSHOTS,
        COLL_MARKET_RESULTS, COLL_PATTERN_MEMORY,
    )
    for c in (COLL_TEAM_DAILY, COLL_PLAYER_DAILY, COLL_GAME_SNAPSHOTS,
              COLL_MARKET_RESULTS, COLL_PATTERN_MEMORY):
        assert c.startswith("bball_"), f"basketball collection must be prefixed: {c}"
