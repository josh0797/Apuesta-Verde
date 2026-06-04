"""Tests for the bucket validation tracker and the lowered apply
threshold (90) for whitelisted buckets.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.mlb_bucket_validation_tracker import (
    STATUS_OBSERVING,
    STATUS_RESET,
    STATUS_VALIDATED,
    TRACKED_BUCKETS,
    VALIDATION_WINDOW_DAYS,
    _days_between,
    _parse_iso,
    get_bucket_validation_overview,
    get_bucket_validation_state,
    upsert_bucket_observation,
)
from services.mlb_run_evaluations_summary import (
    _compute_totals_dispersion_by_buckets,
)


# ─────────────────────────────────────────────────────────────────────
# Tracker — pure helpers
# ─────────────────────────────────────────────────────────────────────
def test_parse_iso_roundtrips():
    now = datetime.now(timezone.utc)
    assert _parse_iso(now.isoformat()) == now
    assert _parse_iso(None) is None
    assert _parse_iso("garbage") is None


def test_days_between_basic_math():
    base = datetime.now(timezone.utc) - timedelta(days=10)
    assert _days_between(base.isoformat()) == 10


# ─────────────────────────────────────────────────────────────────────
# Tracker — Mongo upsert flow (FakeMongo)
# ─────────────────────────────────────────────────────────────────────
class _FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
    async def find_one(self, q):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)   # return a copy so callers can't mutate
        return None
    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return None
    async def update_one(self, q, payload):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                for k, v in (payload.get("$set") or {}).items():
                    d[k] = v
                for k, v in (payload.get("$inc") or {}).items():
                    d[k] = (d.get(k) or 0) + v
                break
        return None


class _FakeDB:
    def __init__(self):
        self._coll = _FakeCollection()
    def __getitem__(self, key):
        return self._coll


@pytest.mark.asyncio
async def test_upsert_creates_doc_on_first_eligible():
    db = _FakeDB()
    out = await upsert_bucket_observation(
        db, bucket_key="pressure.HIGH_PRESSURE",
        sample_size=92, apply_eligible=True,
    )
    assert out is not None
    assert out["status"] == STATUS_OBSERVING
    assert out["first_eligible_sample_size"] == 92
    assert out["validated_at"] is None


@pytest.mark.asyncio
async def test_upsert_ignores_non_tracked_bucket():
    db = _FakeDB()
    out = await upsert_bucket_observation(
        db, bucket_key="fragility.HIGH",
        sample_size=120, apply_eligible=True,
    )
    assert out is None


@pytest.mark.asyncio
async def test_upsert_skips_creation_when_not_eligible():
    db = _FakeDB()
    out = await upsert_bucket_observation(
        db, bucket_key="park.HITTER_FRIENDLY",
        sample_size=30, apply_eligible=False,
    )
    assert out is None
    state = await get_bucket_validation_state(db, "park.HITTER_FRIENDLY")
    assert state["status"] == STATUS_OBSERVING
    assert state["first_eligible_at"] is None


@pytest.mark.asyncio
async def test_upsert_auto_validates_after_14_days():
    db = _FakeDB()
    # Seed with first_eligible 15 days ago.
    past_iso = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    db._coll.docs.append({
        "bucket_key":              "pressure.HIGH_PRESSURE",
        "first_eligible_at":       past_iso,
        "first_eligible_sample_size": 95,
        "latest_seen_at":          past_iso,
        "latest_sample_size":      95,
        "status":                  STATUS_OBSERVING,
        "validated_at":            None,
        "reset_count":             0,
    })
    out = await upsert_bucket_observation(
        db, bucket_key="pressure.HIGH_PRESSURE",
        sample_size=110, apply_eligible=True,
    )
    assert out["status"] == STATUS_VALIDATED
    assert out["validated_at"] is not None


@pytest.mark.asyncio
async def test_upsert_resets_when_sample_drops_below_threshold():
    db = _FakeDB()
    seed_iso = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    db._coll.docs.append({
        "bucket_key":              "park.HITTER_FRIENDLY",
        "first_eligible_at":       seed_iso,
        "first_eligible_sample_size": 92,
        "latest_seen_at":          seed_iso,
        "latest_sample_size":      92,
        "status":                  STATUS_OBSERVING,
        "validated_at":            None,
        "reset_count":             0,
    })
    out = await upsert_bucket_observation(
        db, bucket_key="park.HITTER_FRIENDLY",
        sample_size=85, apply_eligible=False,
    )
    assert out["status"] == STATUS_RESET
    assert out["first_eligible_at"] is None
    assert out["reset_count"] == 1


@pytest.mark.asyncio
async def test_get_state_default_when_no_doc():
    db = _FakeDB()
    state = await get_bucket_validation_state(db, "pressure.HIGH_PRESSURE")
    assert state["status"] == STATUS_OBSERVING
    assert state["validated"] is False
    assert state["validation_window_days"] == VALIDATION_WINDOW_DAYS
    assert state["latest_sample_size"] == 0


@pytest.mark.asyncio
async def test_overview_returns_all_tracked_buckets():
    db = _FakeDB()
    overview = await get_bucket_validation_overview(db)
    assert set(overview.keys()) == set(TRACKED_BUCKETS)


@pytest.mark.asyncio
async def test_tracker_failsoft_on_none_db():
    out = await upsert_bucket_observation(
        None, bucket_key="pressure.HIGH_PRESSURE",
        sample_size=120, apply_eligible=True,
    )
    assert out is None
    state = await get_bucket_validation_state(None, "pressure.HIGH_PRESSURE")
    assert state["status"] == STATUS_OBSERVING


# ─────────────────────────────────────────────────────────────────────
# Lowered threshold (90 vs 100) for whitelisted buckets
# ─────────────────────────────────────────────────────────────────────
def _mk_doc(park_mult=1.0, exp=8.5, final=10):
    return {
        "expected_total":  exp,
        "actual_total":    final,
        "final_total":     final,
        "park_runs_mult":  park_mult,
        "totals_model":    {"model_used": "NegativeBinomial",
                              "under_calibration_delta_pts": 3.0,
                              "expected_total": exp},
        "result":          "won" if final < 9 else "lost",
    }


def test_whitelisted_park_eligible_at_n_90():
    docs = [_mk_doc(park_mult=1.10) for _ in range(90)]
    buckets = _compute_totals_dispersion_by_buckets(docs)
    park = buckets["park"]["HITTER_FRIENDLY"]
    assert park["sample_size"] == 90
    assert park["whitelisted"] is True
    assert park["apply_eligible"] is True
    assert park["min_apply_threshold"] == 90
    assert park["mode"] == "APPLY"


def test_whitelisted_park_below_90_is_warning_only():
    docs = [_mk_doc(park_mult=1.10) for _ in range(45)]
    buckets = _compute_totals_dispersion_by_buckets(docs)
    park = buckets["park"]["HITTER_FRIENDLY"]
    assert park["apply_eligible"] is False
    assert park["mode"] == "WARNING_ONLY"
    assert park["samples_until_apply"] == 45
    assert park["adjustment_tier"] == "INSUFFICIENT"


def test_non_whitelisted_park_still_requires_100():
    # Neutral park is NOT whitelisted → keeps the legacy n≥100 threshold.
    docs = [_mk_doc(park_mult=1.0) for _ in range(92)]
    buckets = _compute_totals_dispersion_by_buckets(docs)
    park = buckets["park"]["NEUTRAL_PARK"]
    assert park["whitelisted"] is False
    assert park["min_apply_threshold"] == 100
    assert park["apply_eligible"] is False


def test_adjustment_tier_soft_at_90():
    docs = [_mk_doc(park_mult=1.10) for _ in range(120)]
    buckets = _compute_totals_dispersion_by_buckets(docs)
    park = buckets["park"]["HITTER_FRIENDLY"]
    assert park["adjustment_tier"] == "SOFT"
    assert park["max_confidence_adjust"] == 3
    assert park["max_fragility_bump"] == 5


def test_adjustment_tier_full_at_150():
    docs = [_mk_doc(park_mult=1.10) for _ in range(160)]
    buckets = _compute_totals_dispersion_by_buckets(docs)
    park = buckets["park"]["HITTER_FRIENDLY"]
    assert park["adjustment_tier"] == "FULL"
    assert park["max_confidence_adjust"] == 7
    assert park["max_fragility_bump"] == 10


def test_non_whitelisted_bucket_ships_zero_caps():
    docs = [_mk_doc(park_mult=1.0) for _ in range(200)]
    buckets = _compute_totals_dispersion_by_buckets(docs)
    neutral = buckets["park"]["NEUTRAL_PARK"]
    assert neutral["whitelisted"] is False
    # Non-whitelisted buckets — caps are always zero so consumers can
    # blindly apply min(delta, cap) without branching.
    assert neutral["max_confidence_adjust"] == 0
    assert neutral["max_fragility_bump"] == 0
    assert neutral["adjustment_tier"] == "INSUFFICIENT"
