"""F94.3 — Tests for Live Enrichment Persistence Audit.

Validates the rule:
    discovery_count > 0 AND persisted_count == 0
        → emit error code ``LIVE_ENRICHMENT_DROPPED_FIXTURES``.

Covers:
  * Pure evaluator (``evaluate_enrichment_drop``).
  * Integration with ``compute_football_live_visibility``:
      - Counter is exposed (``persisted_live_count``).
      - Banner flag is set/unset correctly.
      - Fail-soft when DB lookup throws.
      - Fail-soft when db has no ``matches`` collection.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.live_enrichment_audit import (
    LIVE_ENRICHMENT_DROPPED_FIXTURES,
    evaluate_enrichment_drop,
)


# ─────────────────────────────────────────────────────────────────────
#   Pure evaluator
# ─────────────────────────────────────────────────────────────────────

def test_evaluate_triggers_when_discovery_positive_and_persisted_zero():
    out = evaluate_enrichment_drop(discovery_count=3, persisted_count=0)
    assert out["triggered"] is True
    assert out["error_code"] == LIVE_ENRICHMENT_DROPPED_FIXTURES
    assert out["message"] is not None
    assert "3" in out["message"]
    assert "0" in out["message"]


def test_evaluate_does_not_trigger_when_persisted_positive():
    out = evaluate_enrichment_drop(discovery_count=3, persisted_count=2)
    assert out["triggered"] is False
    assert out["error_code"] is None
    assert out["message"] is None


def test_evaluate_does_not_trigger_when_discovery_zero():
    out = evaluate_enrichment_drop(discovery_count=0, persisted_count=0)
    assert out["triggered"] is False
    assert out["error_code"] is None
    assert out["message"] is None


def test_evaluate_does_not_trigger_when_both_match():
    out = evaluate_enrichment_drop(discovery_count=5, persisted_count=5)
    assert out["triggered"] is False
    assert out["error_code"] is None


def test_evaluate_treats_none_as_zero_and_does_not_trigger_when_no_discovery():
    out = evaluate_enrichment_drop(discovery_count=None, persisted_count=None)
    assert out["triggered"] is False
    assert out["error_code"] is None


def test_evaluate_triggers_when_discovery_positive_and_persisted_none():
    # persisted=None coerces to 0; discovery>0 → still triggers.
    out = evaluate_enrichment_drop(discovery_count=2, persisted_count=None)
    assert out["triggered"] is True
    assert out["error_code"] == LIVE_ENRICHMENT_DROPPED_FIXTURES


def test_evaluate_handles_garbage_inputs_gracefully():
    out = evaluate_enrichment_drop(discovery_count="abc", persisted_count="xyz")
    assert out["triggered"] is False
    out2 = evaluate_enrichment_drop(discovery_count=-5, persisted_count=-1)
    assert out2["triggered"] is False


def test_evaluate_output_schema_is_stable():
    """Schema must always include the three keys, regardless of result."""
    keys = {"triggered", "error_code", "message"}
    for d, p in [(0, 0), (3, 0), (3, 3), (None, None)]:
        out = evaluate_enrichment_drop(discovery_count=d, persisted_count=p)
        assert set(out.keys()) == keys


# ─────────────────────────────────────────────────────────────────────
#   Integration with compute_football_live_visibility
# ─────────────────────────────────────────────────────────────────────

def _mock_db_with_count(count_value):
    """Build a mock db whose ``matches.count_documents`` returns ``count_value``."""
    matches = MagicMock()
    matches.count_documents = AsyncMock(return_value=count_value)
    db = MagicMock()
    db.matches = matches
    return db


def _mock_db_with_count_raises(exc: Exception):
    matches = MagicMock()
    matches.count_documents = AsyncMock(side_effect=exc)
    db = MagicMock()
    db.matches = matches
    return db


def _fake_live_raw(n: int):
    """Generate ``n`` minimal API-Football-shaped live fixtures."""
    out = []
    for i in range(n):
        out.append({
            "fixture": {"id": 1000 + i, "status": {"short": "1H"}},
            "league":  {"id": 39, "name": "Premier League", "country": "England"},
            "teams":   {"home": {"name": f"Home{i}"}, "away": {"name": f"Away{i}"}},
            "goals":   {"home": 0, "away": 0},
        })
    return out


def test_compute_visibility_exposes_persisted_live_count_when_db_returns_value():
    from services import football_live_visibility as flv

    db = _mock_db_with_count(5)
    fake_raw = _fake_live_raw(3)

    async def _agg(_client, _db):
        return (fake_raw, {"meta": "ok"})

    with patch.object(flv, "fetch_live_football_fixtures", _agg, create=True):
        # Patch the local import inside the function.
        with patch("services.football_live_aggregator.fetch_live_football_fixtures",
                   _agg, create=True):
            result = asyncio.run(flv.compute_football_live_visibility(None, db))

    assert result["ok"] is True
    debug = result["live_debug"]
    assert debug["persisted_live_count"] == 5
    assert debug["enrichment_dropped_all_fixtures"] is False
    assert debug["enrichment_error_code"] is None


def test_compute_visibility_triggers_banner_when_discovery_positive_persisted_zero():
    from services import football_live_visibility as flv

    db = _mock_db_with_count(0)
    fake_raw = _fake_live_raw(3)  # discovery_count = 3

    async def _agg(_client, _db):
        return (fake_raw, {"meta": "ok"})

    with patch("services.football_live_aggregator.fetch_live_football_fixtures",
               _agg, create=True):
        result = asyncio.run(flv.compute_football_live_visibility(None, db))

    debug = result["live_debug"]
    # Sanity: provider_live_count comes from raw, not from items.
    assert debug["provider_live_count"] == 3
    assert debug["persisted_live_count"] == 0
    assert debug["enrichment_dropped_all_fixtures"] is True
    assert debug["enrichment_error_code"] == LIVE_ENRICHMENT_DROPPED_FIXTURES
    assert debug["enrichment_error_message"] is not None
    assert "3" in debug["enrichment_error_message"]


def test_compute_visibility_does_not_trigger_when_discovery_zero():
    from services import football_live_visibility as flv

    db = _mock_db_with_count(0)

    async def _agg(_client, _db):
        return ([], {"meta": "ok"})

    with patch("services.football_live_aggregator.fetch_live_football_fixtures",
               _agg, create=True):
        result = asyncio.run(flv.compute_football_live_visibility(None, db))

    debug = result["live_debug"]
    assert debug["provider_live_count"] == 0
    assert debug["persisted_live_count"] == 0
    assert debug["enrichment_dropped_all_fixtures"] is False
    assert debug["enrichment_error_code"] is None


def test_compute_visibility_failsoft_when_db_count_raises():
    from services import football_live_visibility as flv

    db = _mock_db_with_count_raises(RuntimeError("mongo timeout"))
    fake_raw = _fake_live_raw(2)

    async def _agg(_client, _db):
        return (fake_raw, {"meta": "ok"})

    with patch("services.football_live_aggregator.fetch_live_football_fixtures",
               _agg, create=True):
        result = asyncio.run(flv.compute_football_live_visibility(None, db))

    debug = result["live_debug"]
    # Lookup failed → persisted_live_count falls back to 0 but the
    # endpoint MUST NOT raise. The rule then fires because we have
    # discovery>0 and persisted=0 — which is the safer default
    # (surface the issue rather than hide it).
    assert result["ok"] is True
    assert debug["persisted_live_count"] == 0
    assert debug["enrichment_dropped_all_fixtures"] is True
    assert debug["enrichment_error_code"] == LIVE_ENRICHMENT_DROPPED_FIXTURES


def test_compute_visibility_failsoft_when_db_is_none():
    from services import football_live_visibility as flv

    fake_raw = _fake_live_raw(0)

    async def _agg(_client, _db):
        return (fake_raw, {"meta": "ok"})

    with patch("services.football_live_aggregator.fetch_live_football_fixtures",
               _agg, create=True):
        result = asyncio.run(flv.compute_football_live_visibility(None, None))

    debug = result["live_debug"]
    # db=None → lookup skipped silently; discovery=0 → rule does not fire.
    assert result["ok"] is True
    assert debug["persisted_live_count"] == 0
    assert debug["enrichment_dropped_all_fixtures"] is False


def test_live_debug_contract_always_includes_f94_3_counters():
    """Forward-compat: even with the simplest path the new keys MUST be present."""
    from services import football_live_visibility as flv

    db = _mock_db_with_count(1)
    fake_raw = _fake_live_raw(1)

    async def _agg(_client, _db):
        return (fake_raw, {"meta": "ok"})

    with patch("services.football_live_aggregator.fetch_live_football_fixtures",
               _agg, create=True):
        result = asyncio.run(flv.compute_football_live_visibility(None, db))

    debug = result["live_debug"]
    for k in (
        "persisted_live_count",
        "enrichment_dropped_all_fixtures",
        "enrichment_error_code",
        "enrichment_error_message",
    ):
        assert k in debug, f"missing F94.3 counter: {k}"
