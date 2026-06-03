"""Tests for services.mlb_intelligence_warehouse (Fix 3).

Coverage:
* Pure helpers: derive_pattern_keys, _compute_pattern_adjustment.
* Fail-soft behavior when ``db`` is None.
* Sample-size gate enforcement: <20 no adjust, 20-49 moderate ±5,
  >=50 strong ±8, negative ROI inverts.
* End-to-end: persist snapshot + lookup + attach to payload using a
  minimal AsyncMock Mongo.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.mlb_intelligence_warehouse import (
    PATTERN_SAMPLE_NO_ADJUST, PATTERN_SAMPLE_MODERATE,
    PATTERN_MAX_ADJUSTMENT_MODERATE, PATTERN_MAX_ADJUSTMENT_STRONG,
    RC_PATTERN_LOW_SAMPLE, RC_PATTERN_MODERATE_BOOST,
    RC_PATTERN_STRONG_BOOST, RC_PATTERN_NEGATIVE_ROI,
    RC_PATTERN_NO_MATCH, RC_WAREHOUSE_DISABLED,
    derive_pattern_keys,
    _compute_pattern_adjustment,
    attach_pattern_match_to_payload,
    lookup_pattern_match,
    persist_market_result,
    update_pattern_memory_from_result,
)


# ─────────────────────────────────────────────────────────────────────
# derive_pattern_keys
# ─────────────────────────────────────────────────────────────────────
def test_derive_keys_empty_payload():
    assert derive_pattern_keys({}) == []
    assert derive_pattern_keys(None) == []


def test_derive_keys_low_pressure_strong_fip_both():
    pp = {
        "pressure_base": {"combined": {"pressure_tier": "LOW_PRESSURE"}},
        "sabermetrics": {
            "home": {"starting_pitcher_fip": {"tier": "ELITE_FIP"}},
            "away": {"starting_pitcher_fip": {"tier": "STRONG_FIP"}},
        },
    }
    keys = derive_pattern_keys(pp)
    assert "LOW_PRESSURE_STRONG_FIP_BOTH" in keys


def test_derive_keys_high_hit_pressure():
    pp = {
        "pressure_base": {"combined": {"pressure_tier": "HIGH_PRESSURE"}},
    }
    keys = derive_pattern_keys(pp)
    assert "HIGH_HIT_PRESSURE_LOW_RUN_CONVERSION" in keys


def test_derive_keys_era_understates_risk():
    pp = {
        "model_verification": {"discrepancies": [{"flag": "ERA_UNDERSTATES_RISK"}]},
    }
    keys = derive_pattern_keys(pp)
    assert "ERA_UNDERSTATES_RISK" in keys


def test_derive_keys_f5_preferred():
    pp = {
        "market_selection": {"reason_codes": ["F5_UNDER_PREFERRED_OVER_FULL_GAME"]},
    }
    assert "F5_UNDER_BETTER_THAN_FULL_GAME" in derive_pattern_keys(pp)


def test_derive_keys_run_line_margin_supported():
    pp = {
        "_mlb_script_v2": {"marginProjection": 2.5, "runLineCoverProb": 0.55},
    }
    assert "RUN_LINE_MARGIN_SUPPORTED" in derive_pattern_keys(pp)


def test_derive_keys_moneyline_safer():
    pp = {
        "market_selection": {"reason_codes": ["MONEYLINE_SAFER_THAN_RUN_LINE"]},
    }
    assert "MONEYLINE_SAFER_THAN_RUN_LINE" in derive_pattern_keys(pp)


def test_derive_keys_ghost_edge_blocked():
    pp = {
        "market_selection": {"reason_codes": ["GHOST_EDGE_BLOCKED_PICK"]},
    }
    assert "GHOST_EDGE_BLOCKED_PICK" in derive_pattern_keys(pp)


# ─────────────────────────────────────────────────────────────────────
# _compute_pattern_adjustment — gate enforcement
# ─────────────────────────────────────────────────────────────────────
def test_adjustment_low_sample_returns_zero():
    adj, codes, warn = _compute_pattern_adjustment(sample_size=15, hit_rate=0.8, roi=0.3)
    assert adj == 0.0
    assert RC_PATTERN_LOW_SAMPLE in codes
    assert warn is not None


@pytest.mark.parametrize("ss", [20, 30, 49])
def test_adjustment_moderate_capped_at_plus5(ss):
    adj, codes, _ = _compute_pattern_adjustment(sample_size=ss, hit_rate=0.62, roi=0.10)
    assert RC_PATTERN_MODERATE_BOOST in codes
    assert -PATTERN_MAX_ADJUSTMENT_MODERATE <= adj <= PATTERN_MAX_ADJUSTMENT_MODERATE


def test_adjustment_moderate_negative_roi():
    adj, codes, _ = _compute_pattern_adjustment(sample_size=30, hit_rate=0.45, roi=-0.05)
    assert adj < 0
    assert RC_PATTERN_MODERATE_BOOST in codes
    assert RC_PATTERN_NEGATIVE_ROI in codes


def test_adjustment_strong_positive_roi():
    adj, codes, _ = _compute_pattern_adjustment(sample_size=60, hit_rate=0.62, roi=0.15)
    assert RC_PATTERN_STRONG_BOOST in codes
    assert 0 < adj <= PATTERN_MAX_ADJUSTMENT_STRONG


def test_adjustment_strong_negative_roi():
    adj, codes, _ = _compute_pattern_adjustment(sample_size=60, hit_rate=0.4, roi=-0.10)
    assert RC_PATTERN_NEGATIVE_ROI in codes
    assert adj < 0


# ─────────────────────────────────────────────────────────────────────
# Fail-soft: db = None
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_lookup_with_db_none():
    out = await lookup_pattern_match(None, ["LOW_PRESSURE_STRONG_FIP_BOTH"])
    assert out["sample_size"] == 0
    assert out["confidence_adjustment"] == 0.0
    assert RC_WAREHOUSE_DISABLED in out["reason_codes"] or RC_PATTERN_NO_MATCH in out["reason_codes"]


@pytest.mark.asyncio
async def test_attach_pattern_match_with_db_none_does_not_break():
    pp = {
        "pressure_base": {"combined": {"pressure_tier": "LOW_PRESSURE"}},
    }
    summary = await attach_pattern_match_to_payload(None, pp)
    assert summary["confidence_adjustment"] == 0.0
    # pick_payload now has the canonical field
    assert "historical_pattern_match" in pp
    assert pp["historical_pattern_match"]["sample_size"] == 0


@pytest.mark.asyncio
async def test_persist_market_result_db_none():
    ok = await persist_market_result(
        None, game_pk=123, pattern_keys=[], market=None,
        stake=1.0, won=False,
    )
    assert ok is False


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


def _make_db_with_pattern(docs):
    db = MagicMock()
    collection = MagicMock()
    collection.find = MagicMock(return_value=_FakeCursor(docs))
    collection.find_one = AsyncMock(return_value=None)
    collection.replace_one = AsyncMock(return_value=None)
    collection.insert_one = AsyncMock(return_value=None)
    db.__getitem__.return_value = collection
    return db, collection


@pytest.mark.asyncio
async def test_lookup_returns_primary_pattern_with_largest_sample():
    docs = [
        {"pattern_key": "LOW_PRESSURE_STRONG_FIP_BOTH",
         "sample_size": 60, "wins": 38, "hit_rate": 0.63,
         "total_stake": 60.0, "total_payout": 75.0, "roi": 0.25,
         "best_market": "Full Game Under 8.5"},
        {"pattern_key": "ERA_UNDERSTATES_RISK",
         "sample_size": 18, "wins": 10, "hit_rate": 0.55,
         "total_stake": 18.0, "total_payout": 17.0, "roi": -0.05},
    ]
    db, _ = _make_db_with_pattern(docs)
    out = await lookup_pattern_match(db, ["LOW_PRESSURE_STRONG_FIP_BOTH",
                                           "ERA_UNDERSTATES_RISK"])
    assert out["primary_key"] == "LOW_PRESSURE_STRONG_FIP_BOTH"
    assert out["sample_size"] == 60
    assert out["hit_rate"] == 0.63
    assert out["best_market"] == "Full Game Under 8.5"
    assert RC_PATTERN_STRONG_BOOST in out["reason_codes"]
    assert out["confidence_adjustment"] > 0


@pytest.mark.asyncio
async def test_attach_pattern_match_writes_canonical_payload_fields():
    docs = [{
        "pattern_key": "LOW_PRESSURE_STRONG_FIP_BOTH",
        "sample_size": 35, "wins": 22, "hit_rate": 0.629,
        "total_stake": 35.0, "total_payout": 42.0, "roi": 0.20,
        "best_market": "F5 Under 4.5",
    }]
    db, _ = _make_db_with_pattern(docs)
    pp = {
        "pressure_base": {"combined": {"pressure_tier": "LOW_PRESSURE"}},
        "sabermetrics": {
            "home": {"starting_pitcher_fip": {"tier": "ELITE_FIP"}},
            "away": {"starting_pitcher_fip": {"tier": "STRONG_FIP"}},
        },
    }
    summary = await attach_pattern_match_to_payload(db, pp)
    assert summary["primary_key"] == "LOW_PRESSURE_STRONG_FIP_BOTH"
    # Top-level mirrors per user spec
    assert pp["historical_hit_rate"] == 0.629
    assert pp["historical_roi"] == 0.20
    assert pp["best_historical_market"] == "F5 Under 4.5"
    assert pp["pattern_confidence_adjustment"] != 0
    assert pp["historical_pattern_match"]["primary_pattern"] == "LOW_PRESSURE_STRONG_FIP_BOTH"


@pytest.mark.asyncio
async def test_no_pattern_keys_returns_no_match():
    pp = {}
    summary = await attach_pattern_match_to_payload(None, pp)
    assert RC_PATTERN_NO_MATCH in summary["reason_codes"]
    assert summary["confidence_adjustment"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# Spec — sample_size cap caps
# ─────────────────────────────────────────────────────────────────────
def test_strong_boost_cap_never_exceeds_8():
    # Extreme inputs
    adj, _, _ = _compute_pattern_adjustment(sample_size=200, hit_rate=0.95, roi=0.80)
    assert adj <= PATTERN_MAX_ADJUSTMENT_STRONG


def test_moderate_cap_never_exceeds_5():
    adj, _, _ = _compute_pattern_adjustment(sample_size=25, hit_rate=0.90, roi=0.50)
    assert abs(adj) <= PATTERN_MAX_ADJUSTMENT_MODERATE
