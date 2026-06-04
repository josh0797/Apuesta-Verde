"""Tests for the bucket-NB recalibration helper inside
``mlb_day_orchestrator._resolve_bucket_ratio`` (Phase 1: passive
recalibration). The orchestrator end-to-end flow is exercised
implicitly via the existing analyze_mlb_day tests; here we focus on
the pure helper so the unit guarantees never depend on Mongo / fixtures.
"""
from __future__ import annotations

import pytest

from services.mlb_day_orchestrator import _resolve_bucket_ratio


# ─────────────────────────────────────────────────────────────────────
# Short-circuits
# ─────────────────────────────────────────────────────────────────────
def test_resolve_no_summary_returns_none():
    assert _resolve_bucket_ratio(
        pressure_tier="HIGH_PRESSURE",
        park_code="OFFENSIVE",
        park_runs_mult=1.15,
        summary=None,
    ) is None


def test_resolve_empty_summary_returns_none():
    assert _resolve_bucket_ratio(
        pressure_tier="HIGH_PRESSURE",
        park_code=None,
        park_runs_mult=None,
        summary={},
    ) is None


def test_resolve_no_eligible_buckets_returns_none():
    # Today's reality: policy block lists ZERO buckets eligible for apply.
    # Helper must short-circuit cleanly.
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {"buckets_eligible_for_apply": []},
        },
        "totals_dispersion_by_bucket": {
            "pressure": {"HIGH_PRESSURE": {
                "available": True, "apply_eligible": False,
                "suggested_ratio": 2.1, "sample_size": 40,
            }},
        },
    }
    assert _resolve_bucket_ratio(
        pressure_tier="HIGH_PRESSURE",
        park_code=None, park_runs_mult=None,
        summary=summary,
    ) is None


# ─────────────────────────────────────────────────────────────────────
# Pressure precedence
# ─────────────────────────────────────────────────────────────────────
def test_resolve_pressure_bucket_wins_when_eligible():
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {
                "buckets_eligible_for_apply": [
                    "pressure.HIGH_PRESSURE", "park.HITTER_FRIENDLY",
                ],
            },
        },
        "totals_dispersion_by_bucket": {
            "pressure": {"HIGH_PRESSURE": {
                "available": True, "apply_eligible": True,
                "suggested_ratio": 1.85, "sample_size": 120,
            }},
            "park": {"HITTER_FRIENDLY": {
                "available": True, "apply_eligible": True,
                "suggested_ratio": 2.30, "sample_size": 110,
            }},
        },
    }
    out = _resolve_bucket_ratio(
        pressure_tier="HIGH_PRESSURE",
        park_code="OFFENSIVE",
        park_runs_mult=1.15,
        summary=summary,
    )
    # Pressure runs first → wins even though park has a wider ratio.
    assert out == (1.85, "pressure.HIGH_PRESSURE")


def test_resolve_falls_back_to_park_when_pressure_not_eligible():
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {
                "buckets_eligible_for_apply": ["park.HITTER_FRIENDLY"],
            },
        },
        "totals_dispersion_by_bucket": {
            "pressure": {"HIGH_PRESSURE": {
                "available": True, "apply_eligible": False,
                "suggested_ratio": 1.85, "sample_size": 30,
            }},
            "park": {"HITTER_FRIENDLY": {
                "available": True, "apply_eligible": True,
                "suggested_ratio": 2.30, "sample_size": 150,
            }},
        },
    }
    out = _resolve_bucket_ratio(
        pressure_tier="HIGH_PRESSURE",
        park_code="OFFENSIVE",
        park_runs_mult=1.15,
        summary=summary,
    )
    assert out == (2.30, "park.HITTER_FRIENDLY")


# ─────────────────────────────────────────────────────────────────────
# Park key resolution (mirror of _park_bucket in the summary)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("code,mult,expected_key", [
    ("OFFENSIVE",        None, "HITTER_FRIENDLY"),
    ("HITTER_FRIENDLY",  None, "HITTER_FRIENDLY"),
    ("PITCHER_FRIENDLY", None, "PITCHER_FRIENDLY"),
    ("NEUTRAL",          None, "NEUTRAL_PARK"),
    (None,              1.07,  "HITTER_FRIENDLY"),     # >= 1.05
    (None,              0.92,  "PITCHER_FRIENDLY"),    # <= 0.95
    (None,              1.00,  "NEUTRAL_PARK"),
])
def test_park_key_resolution_by_code_or_multiplier(code, mult, expected_key):
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {
                "buckets_eligible_for_apply": [f"park.{expected_key}"],
            },
        },
        "totals_dispersion_by_bucket": {
            "park": {expected_key: {
                "available": True, "apply_eligible": True,
                "suggested_ratio": 2.0, "sample_size": 200,
            }},
        },
    }
    out = _resolve_bucket_ratio(
        pressure_tier=None,
        park_code=code, park_runs_mult=mult,
        summary=summary,
    )
    assert out == (2.0, f"park.{expected_key}")


def test_park_key_falls_back_to_none_on_garbage_multiplier():
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {
                "buckets_eligible_for_apply": ["park.NEUTRAL_PARK"],
            },
        },
        "totals_dispersion_by_bucket": {
            "park": {"NEUTRAL_PARK": {
                "available": True, "apply_eligible": True,
                "suggested_ratio": 1.9, "sample_size": 100,
            }},
        },
    }
    # No code + garbage mult → no park_key → no match.
    out = _resolve_bucket_ratio(
        pressure_tier=None,
        park_code=None, park_runs_mult="N/A",
        summary=summary,
    )
    assert out is None


# ─────────────────────────────────────────────────────────────────────
# Clamp on the returned ratio
# ─────────────────────────────────────────────────────────────────────
def test_resolve_clamps_high_ratio_to_25():
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {
                "buckets_eligible_for_apply": ["park.HITTER_FRIENDLY"],
            },
        },
        "totals_dispersion_by_bucket": {
            "park": {"HITTER_FRIENDLY": {
                "available": True, "apply_eligible": True,
                "suggested_ratio": 4.5, "sample_size": 150,
            }},
        },
    }
    out = _resolve_bucket_ratio(
        pressure_tier=None,
        park_code="OFFENSIVE", park_runs_mult=None,
        summary=summary,
    )
    assert out is not None
    assert out[0] == 2.5


def test_resolve_clamps_low_ratio_to_11():
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {
                "buckets_eligible_for_apply": ["park.PITCHER_FRIENDLY"],
            },
        },
        "totals_dispersion_by_bucket": {
            "park": {"PITCHER_FRIENDLY": {
                "available": True, "apply_eligible": True,
                "suggested_ratio": 0.4, "sample_size": 130,
            }},
        },
    }
    out = _resolve_bucket_ratio(
        pressure_tier=None,
        park_code="PITCHER_FRIENDLY", park_runs_mult=None,
        summary=summary,
    )
    assert out is not None
    assert out[0] == 1.1


def test_resolve_returns_none_when_suggested_ratio_is_string():
    # A non-numeric string that isn't even parseable as float must yield None.
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {
                "buckets_eligible_for_apply": ["park.HITTER_FRIENDLY"],
            },
        },
        "totals_dispersion_by_bucket": {
            "park": {"HITTER_FRIENDLY": {
                "available": True, "apply_eligible": True,
                "suggested_ratio": "garbage-value", "sample_size": 200,
            }},
        },
    }
    out = _resolve_bucket_ratio(
        pressure_tier=None,
        park_code="OFFENSIVE", park_runs_mult=None,
        summary=summary,
    )
    assert out is None


# ─────────────────────────────────────────────────────────────────────
# No pressure / no park → returns None
# ─────────────────────────────────────────────────────────────────────
def test_resolve_no_dimensions_returns_none():
    summary = {
        "totals_dispersion_calibration": {
            "bucket_application_policy": {
                "buckets_eligible_for_apply": ["pressure.HIGH_PRESSURE"],
            },
        },
        "totals_dispersion_by_bucket": {
            "pressure": {"HIGH_PRESSURE": {
                "available": True, "apply_eligible": True,
                "suggested_ratio": 1.9, "sample_size": 200,
            }},
        },
    }
    # No pressure_tier in ctx + no park → no candidates → None.
    assert _resolve_bucket_ratio(
        pressure_tier=None, park_code=None, park_runs_mult=None,
        summary=summary,
    ) is None
