"""Tests for the bucket-specific dispersion activation in
``mlb_pregame_analytics_v2`` — covers the bucket detector, the
resolver, the precedence rules and the wiring through
``smart_total_line_selector``.
"""
from __future__ import annotations

import pytest

from services.mlb_pregame_analytics_v2 import (
    BUCKETS_APPLY_WHITELIST,
    MLB_TOTALS_DISPERSION_RATIO,
    choose_bucket_ratio,
    detect_totals_dispersion_buckets,
    resolve_totals_dispersion_ratio,
    smart_total_line_selector,
)


# ─────────────────────────────────────────────────────────────────────
# detect_totals_dispersion_buckets
# ─────────────────────────────────────────────────────────────────────
def test_detect_empty_ctx_returns_no_buckets():
    assert detect_totals_dispersion_buckets({}) == []
    assert detect_totals_dispersion_buckets(None) == []


def test_detect_high_pressure_bucket():
    ctx = {"pressure_base": {"pressure_environment": "HIGH_PRESSURE"}}
    out = detect_totals_dispersion_buckets(ctx)
    assert "pressure.HIGH_PRESSURE" in out


def test_detect_high_pressure_from_combined_tier():
    ctx = {"pressure_base": {"combined": {"pressure_tier": "HIGH_PRESSURE"}}}
    out = detect_totals_dispersion_buckets(ctx)
    assert "pressure.HIGH_PRESSURE" in out


def test_detect_hitter_friendly_park_by_code():
    ctx = {"park": {"code": "HITTER_FRIENDLY"}}
    assert "park.HITTER_FRIENDLY" in detect_totals_dispersion_buckets(ctx)


def test_detect_hitter_friendly_park_by_dynamic_mult():
    ctx = {"park_factor_live": {"dynamic": 1.12}}
    assert "park.HITTER_FRIENDLY" in detect_totals_dispersion_buckets(ctx)


def test_detect_does_not_flag_neutral_park():
    ctx = {"park": {"park_runs_mult": 1.02}}
    assert "park.HITTER_FRIENDLY" not in detect_totals_dispersion_buckets(ctx)


def test_detect_fragility_high_observer_only():
    ctx = {"fragility_score": {"tier": "HIGH"}}
    out = detect_totals_dispersion_buckets(ctx)
    assert "fragility.HIGH" in out
    # Fragility is NOT whitelisted yet — must NOT be APPLY-eligible.
    assert "fragility.HIGH" not in BUCKETS_APPLY_WHITELIST


def test_detect_market_scope_full_game():
    ctx = {"market_scope": "full_game"}
    assert "market_scope.full_game" in detect_totals_dispersion_buckets(ctx)


def test_detect_multiple_buckets_in_order():
    ctx = {
        "pressure_base": {"pressure_environment": "HIGH_PRESSURE"},
        "park":          {"code": "HITTER_FRIENDLY"},
        "fragility_score": {"tier": "HIGH"},
        "market_scope":  "full_game",
    }
    out = detect_totals_dispersion_buckets(ctx)
    assert out[0] == "pressure.HIGH_PRESSURE"
    assert "park.HITTER_FRIENDLY" in out
    assert "fragility.HIGH" in out
    assert "market_scope.full_game" in out


# ─────────────────────────────────────────────────────────────────────
# choose_bucket_ratio
# ─────────────────────────────────────────────────────────────────────
def test_choose_bucket_under_picks_largest_ratio():
    cands = [
        {"bucket_key": "park.HITTER_FRIENDLY", "empirical_ratio": 2.1, "sample_size": 120},
        {"bucket_key": "pressure.HIGH_PRESSURE", "empirical_ratio": 1.7, "sample_size": 150},
    ]
    winner = choose_bucket_ratio(cands, market_direction="UNDER")
    assert winner["bucket_key"] == "park.HITTER_FRIENDLY"


def test_choose_bucket_over_picks_smallest_ratio():
    cands = [
        {"bucket_key": "park.HITTER_FRIENDLY", "empirical_ratio": 2.1, "sample_size": 120},
        {"bucket_key": "pressure.HIGH_PRESSURE", "empirical_ratio": 1.7, "sample_size": 150},
    ]
    winner = choose_bucket_ratio(cands, market_direction="OVER")
    assert winner["bucket_key"] == "pressure.HIGH_PRESSURE"


def test_choose_bucket_empty_returns_none():
    assert choose_bucket_ratio([], market_direction="UNDER") is None


# ─────────────────────────────────────────────────────────────────────
# resolve_totals_dispersion_ratio
# ─────────────────────────────────────────────────────────────────────
def test_resolve_default_when_no_summary_no_context():
    r = resolve_totals_dispersion_ratio()
    assert r["ratio"] == MLB_TOTALS_DISPERSION_RATIO
    assert r["source"] == "default"
    assert r["bucket_mode"] == "GLOBAL"
    assert r["bucket_key"] is None


def test_resolve_active_weights_override_wins():
    r = resolve_totals_dispersion_ratio(
        active_weights={"dispersion_ratio": 1.65},
    )
    assert r["ratio"] == 1.65
    assert r["source"] == "active_weights"


def test_resolve_global_calibration_when_useful():
    summary = {
        "totals_dispersion_calibration": {
            "available":       True,
            "suggested_ratio": 2.1,
            "confidence_tier": "USEFUL",
        },
    }
    r = resolve_totals_dispersion_ratio(calibration_summary=summary)
    assert r["ratio"] == 2.1
    assert r["source"] == "global_calibration"


def test_resolve_bucket_applies_when_whitelisted_and_eligible():
    ctx = {"park": {"code": "HITTER_FRIENDLY"}}
    summary = {
        "totals_dispersion_calibration": {
            "available":       True,
            "suggested_ratio": 1.8,
            "confidence_tier": "USEFUL",
        },
        "totals_dispersion_by_bucket": {
            "park": {
                "HITTER_FRIENDLY": {
                    "available":       True,
                    "apply_eligible":  True,
                    "suggested_ratio": 2.3,
                    "sample_size":     150,
                },
            },
        },
    }
    r = resolve_totals_dispersion_ratio(
        scoring_ctx=ctx,
        calibration_summary=summary,
        market_direction="UNDER",
    )
    assert r["source"] == "bucket_calibration"
    assert r["bucket_key"] == "park.HITTER_FRIENDLY"
    assert r["bucket_mode"] == "APPLY"
    assert r["ratio"] == 2.3
    assert r["bucket_sample_size"] == 150


def test_resolve_bucket_does_not_apply_when_observe_only():
    ctx = {"park": {"code": "HITTER_FRIENDLY"}}
    summary = {
        "totals_dispersion_by_bucket": {
            "park": {
                "HITTER_FRIENDLY": {
                    "available":       True,
                    "apply_eligible":  False,    # below 100 samples
                    "suggested_ratio": 2.3,
                    "sample_size":     30,
                },
            },
        },
    }
    r = resolve_totals_dispersion_ratio(
        scoring_ctx=ctx, calibration_summary=summary,
    )
    # Bucket detected but NOT applied → must fall back to global/default.
    assert r["bucket_key"] is None
    assert r["source"] == "default"
    assert r["bucket_mode"] == "NOT_ELIGIBLE"
    # The observe_only metadata must be surfaced for the UI.
    assert any(c["bucket_key"] == "park.HITTER_FRIENDLY"
                for c in r["observe_only_buckets"])


def test_resolve_under_picks_higher_ratio_when_two_buckets_apply():
    ctx = {
        "park":          {"code": "HITTER_FRIENDLY"},
        "pressure_base": {"pressure_environment": "HIGH_PRESSURE"},
    }
    summary = {
        "totals_dispersion_by_bucket": {
            "park": {
                "HITTER_FRIENDLY": {
                    "available": True, "apply_eligible": True,
                    "suggested_ratio": 2.4, "sample_size": 120,
                },
            },
            "pressure": {
                "HIGH_PRESSURE": {
                    "available": True, "apply_eligible": True,
                    "suggested_ratio": 1.85, "sample_size": 130,
                },
            },
        },
    }
    r = resolve_totals_dispersion_ratio(
        scoring_ctx=ctx, calibration_summary=summary, market_direction="UNDER",
    )
    assert r["bucket_key"] == "park.HITTER_FRIENDLY"
    assert r["ratio"] == 2.4


def test_resolve_ratio_clamped_to_max():
    summary = {
        "totals_dispersion_by_bucket": {
            "park": {
                "HITTER_FRIENDLY": {
                    "available": True, "apply_eligible": True,
                    "suggested_ratio": 4.0, "sample_size": 150,
                },
            },
        },
    }
    ctx = {"park": {"code": "HITTER_FRIENDLY"}}
    r = resolve_totals_dispersion_ratio(
        scoring_ctx=ctx, calibration_summary=summary,
    )
    assert r["ratio"] <= 2.5


# ─────────────────────────────────────────────────────────────────────
# Wiring through smart_total_line_selector
# ─────────────────────────────────────────────────────────────────────
def test_selector_uses_resolved_ratio_when_summary_passed():
    summary = {
        "totals_dispersion_by_bucket": {
            "park": {
                "HITTER_FRIENDLY": {
                    "available": True, "apply_eligible": True,
                    "suggested_ratio": 2.3, "sample_size": 200,
                },
            },
        },
    }
    ctx = {"park": {"code": "HITTER_FRIENDLY"}}
    out = smart_total_line_selector(
        9.0, ctx, calibration_summary=summary, market_direction="UNDER",
    )
    assert "dispersionResolved" in out
    assert out["dispersionResolved"]["bucket_mode"] == "APPLY"
    assert out["dispersionResolved"]["bucket_key"] == "park.HITTER_FRIENDLY"


def test_selector_falls_back_to_default_without_summary():
    out = smart_total_line_selector(9.0, {})
    assert out["dispersionResolved"]["source"] in ("default", "global_calibration")
    assert out["dispersionResolved"]["bucket_mode"] in ("GLOBAL", "NOT_ELIGIBLE")
