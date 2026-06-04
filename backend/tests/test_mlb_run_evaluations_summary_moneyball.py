"""Tests for mlb_run_evaluations_summary — Moneyball breakdowns."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.mlb_run_evaluations_summary import (
    compute_run_evaluations_summary,
    MARKET_SELECTED_BUCKETS,
    PRESSURE_ENVIRONMENT_BUCKETS,
    SCRIPT_SURVIVAL_BUCKETS,
    FRAGILITY_TIER_BUCKETS,
    SABERMETRICS_EDGE_BUCKETS,
    GHOST_EDGE_BUCKETS,
)


def _make_db(eval_docs=None, pattern_docs=None):
    """Build a Motor-compatible mock DB.

    The summary code uses two queries:
        * ``db.mlb_run_evaluations.find(...).to_list(...)`` — TWICE
        * ``db["mlb_pattern_memory"].find(...).to_list(...)``
    """
    eval_docs = list(eval_docs or [])
    pattern_docs = list(pattern_docs or [])

    eval_coll = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=eval_docs)
    eval_coll.find = MagicMock(return_value=cursor)

    pattern_coll = MagicMock()
    pcursor = MagicMock()
    pcursor.to_list = AsyncMock(return_value=pattern_docs)
    pattern_coll.find = MagicMock(return_value=pcursor)

    class _DB:
        mlb_run_evaluations = eval_coll
        def __getitem__(self, k):
            if k == "mlb_pattern_memory":
                return pattern_coll
            return MagicMock()
    return _DB()


@pytest.mark.asyncio
async def test_summary_has_all_new_moneyball_keys_with_empty_data():
    """When there are no settled docs, every Moneyball breakdown must
    still be present (all buckets initialised to 0/null)."""
    db = _make_db()
    summary = await compute_run_evaluations_summary(db)

    expected_keys = {
        "by_market_selected", "by_pressure_environment", "by_script_survival",
        "by_fragility_tier", "by_sabermetrics_edge", "by_ghost_edge",
        "f5_vs_full_game_under", "manual_odds_review_outcomes",
        "pattern_memory_performance", "summary_schema_version",
    }
    assert expected_keys.issubset(set(summary.keys()))

    # Every bucket key must be initialised even when there's no data.
    for b in MARKET_SELECTED_BUCKETS:
        assert b in summary["by_market_selected"]
        assert summary["by_market_selected"][b]["total"] == 0
        assert summary["by_market_selected"][b]["hit_rate"] is None
    for b in PRESSURE_ENVIRONMENT_BUCKETS:
        assert b in summary["by_pressure_environment"]
    for b in SCRIPT_SURVIVAL_BUCKETS:
        assert b in summary["by_script_survival"]
    for b in FRAGILITY_TIER_BUCKETS:
        assert b in summary["by_fragility_tier"]
    for b in SABERMETRICS_EDGE_BUCKETS:
        assert b in summary["by_sabermetrics_edge"]
    for b in GHOST_EDGE_BUCKETS:
        assert b in summary["by_ghost_edge"]
    assert summary["pattern_memory_performance"] == []
    assert summary["summary_schema_version"] == "moneyball.2"


@pytest.mark.asyncio
async def test_summary_keeps_legacy_fields_intact():
    db = _make_db()
    summary = await compute_run_evaluations_summary(db)
    # Legacy fields the existing UI / endpoints depend on.
    for legacy_key in (
        "by_risk_tier", "by_flip", "by_market_scope", "by_miss_type",
        "high_conservative_won_anyway", "reference_profile_activations",
        "dynamic_park_blocks", "central_under_vetoes", "park_blocks_saved",
        "overall", "evaluated_total",
    ):
        assert legacy_key in summary, f"legacy key missing: {legacy_key}"


@pytest.mark.asyncio
async def test_summary_buckets_recommended_market_correctly():
    docs = [
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "won",
            "market_selection": {"recommended_market": "F5 Under"},
            "game_pk": "100",
        },
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "lost",
            "market_selection": {"recommended_market": "F5 Under"},
            "game_pk": "101",
        },
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "won",
            "market_selection": {"recommended_market": "Moneyline"},
            "game_pk": "102",
        },
    ]
    db = _make_db(eval_docs=docs)
    s = await compute_run_evaluations_summary(db)
    assert s["by_market_selected"]["F5 Under"]["total"] == 2
    assert s["by_market_selected"]["F5 Under"]["won"] == 1
    assert s["by_market_selected"]["F5 Under"]["hit_rate"] == 50.0
    assert s["by_market_selected"]["Moneyline"]["total"] == 1
    assert s["by_market_selected"]["Moneyline"]["won"] == 1


@pytest.mark.asyncio
async def test_summary_f5_vs_full_game_under_detects_bullpen_break():
    docs = [
        # Same game_pk → F5 won, FG lost
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "won",  "game_pk": "200",
            "market_selection": {"recommended_market": "F5 Under"},
        },
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "lost", "game_pk": "200",
            "market_selection": {"recommended_market": "Full Game Under"},
        },
        # FG won on a different game
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "won",  "game_pk": "201",
            "market_selection": {"recommended_market": "F5 Under"},
        },
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "won",  "game_pk": "201",
            "market_selection": {"recommended_market": "Full Game Under"},
        },
    ]
    db = _make_db(eval_docs=docs)
    s = await compute_run_evaluations_summary(db)
    block = s["f5_vs_full_game_under"]
    assert block["games_with_both_markets"] == 2
    assert block["f5_won_full_game_lost"]["total"] == 1
    assert block["bullpen_broke_under"]["total"] == 1
    assert block["full_game_won"]["total"] == 1


@pytest.mark.asyncio
async def test_summary_pattern_memory_performance_reads_from_warehouse():
    pattern_docs = [
        {
            "pattern_key":  "F5_UNDER_BETTER_THAN_FULL_GAME",
            "sample_size": 35,
            "hit_rate":    0.62,
            "roi":         0.18,
            "best_market": "F5 Under",
            "updated_at":  "2026-06-01T00:00:00+00:00",
        },
        {
            "pattern_key":  "LOW_PRESSURE_STRONG_FIP_BOTH",
            "sample_size": 8,
            "hit_rate":    0.50,
            "roi":         0.0,
            "best_market": "Full Game Under",
            "updated_at":  "2026-06-01T00:00:00+00:00",
        },
    ]
    db = _make_db(pattern_docs=pattern_docs)
    s = await compute_run_evaluations_summary(db)
    rows = s["pattern_memory_performance"]
    assert len(rows) == 2
    # Sorted by sample_size desc.
    assert rows[0]["pattern_key"] == "F5_UNDER_BETTER_THAN_FULL_GAME"
    assert rows[0]["sample_size"] == 35
    assert rows[0]["best_market"] == "F5 Under"


@pytest.mark.asyncio
async def test_summary_fragility_tier_derived_from_score():
    docs = [
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "won",
            "fragility": {"score": 80},   # HIGH
            "market_selection": {"recommended_market": "Moneyline"},
        },
        {
            "user_id": "_slate", "sport": "baseball",
            "generated_at": "2026-06-01T00:00:00+00:00",
            "result": "lost",
            "fragility_score": 15,        # LOW
            "market_selection": {"recommended_market": "F5 Under"},
        },
    ]
    db = _make_db(eval_docs=docs)
    s = await compute_run_evaluations_summary(db)
    assert s["by_fragility_tier"]["HIGH"]["total"] == 1
    assert s["by_fragility_tier"]["LOW"]["total"] == 1
    assert s["by_fragility_tier"]["MEDIUM"]["total"] == 0
