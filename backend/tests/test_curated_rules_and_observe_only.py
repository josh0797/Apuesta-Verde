"""Tests for the curated MLB Under veto rules (Enfoque C) plus the
observe-only bucket calibration policy and the cohort breakdown.
"""
from __future__ import annotations

from unittest.mock import AsyncMock  # noqa: F401

import pytest

from services.learning_cases import (
    CURATED_UNDER_VETO_RULES,
    detect_mlb_under_warning_pattern,
)
from services.mlb_pregame_analytics_v2 import MLB_TOTALS_DISPERSION_RATIO
from services.mlb_run_evaluations_summary import (
    _compute_cohort_breakdown,
    _compute_totals_dispersion_by_buckets,
    _current_dispersion_default,
)


# ─────────────────────────────────────────────────────────────────────
# Cambio 1 — default ratio bumped to 1.9
# ─────────────────────────────────────────────────────────────────────
def test_default_dispersion_ratio_is_19():
    assert MLB_TOTALS_DISPERSION_RATIO == 1.9
    assert _current_dispersion_default() == 1.9


# ─────────────────────────────────────────────────────────────────────
# Cambio 3 — curated cases
# ─────────────────────────────────────────────────────────────────────
def test_curated_rules_exposed():
    keys = {r["rule_key"] for r in CURATED_UNDER_VETO_RULES}
    assert {
        "OFFENSIVE_PARK_PLUS_TIRED_BULLPENS",
        "ACTIVE_SERIES_HIGH_SCORING",
        "LATE_SERIES_BOTH_LINEUPS_HOT",
        "OVERPERFORMING_ACE_REGRESSION",
    }.issubset(keys)


def test_offensive_park_plus_tired_bullpens_blocks():
    ctx = {
        "park_factor_live":  {"code": "OFFENSIVE", "dynamic": 1.15},
        "home_bullpen_real": {"pitch_stress_index": 1.5},
        "away_bullpen_real": {"pitch_stress_index": 1.4},
    }
    r = detect_mlb_under_warning_pattern(match={}, scoring_ctx=ctx)
    assert r is not None
    assert r["any_block"] is True
    assert any(
        x["rule_key"] == "OFFENSIVE_PARK_PLUS_TIRED_BULLPENS" and x["block"]
        for x in r["rules_fired"]
    )


def test_offensive_park_alone_does_not_block_curated_rule():
    ctx = {
        "park_factor_live":  {"code": "OFFENSIVE", "dynamic": 1.15},
        "home_bullpen_real": {"pitch_stress_index": 0.8},
        "away_bullpen_real": {"pitch_stress_index": 0.9},
    }
    r = detect_mlb_under_warning_pattern(match={}, scoring_ctx=ctx)
    assert r is None or all(
        x["rule_key"] != "OFFENSIVE_PARK_PLUS_TIRED_BULLPENS"
        for x in (r or {}).get("rules_fired", [])
    )


def test_active_series_high_scoring_blocks():
    ctx = {
        "active_series_context": {
            "series_override": True,
            "series_lean":     "OVER",
            "total_runs_avg":  15.0,
            "games_in_series": 2,
        },
    }
    r = detect_mlb_under_warning_pattern(match={}, scoring_ctx=ctx)
    assert r is not None
    fired = {x["rule_key"] for x in r["rules_fired"]}
    assert "ACTIVE_SERIES_HIGH_SCORING" in fired
    assert r["any_block"] is True


def test_late_series_both_hot_warns_but_no_block():
    ctx = {
        "series_degradation": {"game_in_series": 3},
        "recent_run_split":   {
            "home": {"delta_pct": 25.0},
            "away": {"delta_pct": 22.0},
        },
    }
    r = detect_mlb_under_warning_pattern(match={}, scoring_ctx=ctx)
    assert r is not None
    rule = next(
        (x for x in r["rules_fired"] if x["rule_key"] == "LATE_SERIES_BOTH_LINEUPS_HOT"),
        None,
    )
    assert rule is not None
    assert rule["block"] is False
    assert rule["severity"] == "WARNING"


def test_overperforming_ace_regression_warns():
    ctx = {
        "home_pitcher": {"_regression_signal": "PITCHER_OVERPERFORMING"},
        "away_pitcher": {},
    }
    r = detect_mlb_under_warning_pattern(match={}, scoring_ctx=ctx)
    assert r is not None
    rule = next(
        (x for x in r["rules_fired"] if x["rule_key"] == "OVERPERFORMING_ACE_REGRESSION"),
        None,
    )
    assert rule is not None
    assert rule["block"] is False


def test_curated_rule_evaluation_failsoft_on_bad_input():
    # `recent_run_split.home` is the wrong type; the rule must not crash.
    ctx = {
        "series_degradation": {"game_in_series": "three"},
        "recent_run_split":   {"home": "not a dict", "away": None},
    }
    r = detect_mlb_under_warning_pattern(match={}, scoring_ctx=ctx)
    # Should return None or a sane payload — never raise.
    assert r is None or "rules_fired" in r


def test_legacy_rules_still_fire():
    # Power-bat rule must still trigger even when no curated rules apply.
    ctx = {"home_team_ops": 0.812, "away_team_ops": 0.690}
    r = detect_mlb_under_warning_pattern(match={}, scoring_ctx=ctx)
    assert r is not None
    fired = {x["rule_key"] for x in r["rules_fired"]}
    assert "power_bat_visiting_avoid_under" in fired


# ─────────────────────────────────────────────────────────────────────
# Cambio 2 — bucket observe-only policy
# ─────────────────────────────────────────────────────────────────────
def _mk_doc(*, exp=8.5, final=10, market="Full Game Under", **extras):
    base = {
        "expected_total":   exp,
        "actual_total":     final,
        "final_total":      final,
        "park_runs_mult":   1.0,  # neutral park by default
        "totals_model":     {"model_used": "NegativeBinomial",
                              "under_calibration_delta_pts": 4.0,
                              "expected_total": exp},
        "recommended_market": market,
        "result":           "won" if final < 9 else "lost",
    }
    base.update(extras)
    return base


def test_bucket_below_100_is_observe_only():
    docs = [_mk_doc() for _ in range(20)]
    buckets = _compute_totals_dispersion_by_buckets(docs)
    park_bucket = buckets["park"]["NEUTRAL_PARK"]
    assert park_bucket["sample_size"] == 20
    assert park_bucket["apply_eligible"] is False
    assert park_bucket["mode"] == "OBSERVE_ONLY"
    assert park_bucket["samples_until_apply"] == 80


def test_bucket_above_100_becomes_apply_eligible():
    docs = [_mk_doc() for _ in range(120)]
    buckets = _compute_totals_dispersion_by_buckets(docs)
    park_bucket = buckets["park"]["NEUTRAL_PARK"]
    assert park_bucket["sample_size"] == 120
    assert park_bucket["apply_eligible"] is True
    assert park_bucket["mode"] == "APPLY"
    assert park_bucket["samples_until_apply"] == 0


def test_empty_bucket_observe_only_at_zero():
    buckets = _compute_totals_dispersion_by_buckets([])
    # Every dimension has at least one empty bucket — it must report 0.
    park_bucket = buckets["park"]["HITTER_FRIENDLY"]
    assert park_bucket["sample_size"] == 0
    assert park_bucket["available"] is False
    # Empty buckets don't get an `apply_eligible` field — only buckets
    # with at least 1 sample do.
    assert "apply_eligible" not in park_bucket


# ─────────────────────────────────────────────────────────────────────
# Mejora extra — cohort breakdown
# ─────────────────────────────────────────────────────────────────────
class _FakeFindCursor:
    def __init__(self, docs):
        self._docs = docs
    def to_list(self, length=None):
        async def _coro():
            return list(self._docs)
        return _coro()


class _FakeMongo:
    """Tiny stand-in for the Motor collection — supports ``find`` with
    the user_id filter the cohort breakdown issues."""
    def __init__(self, slate_docs, backtest_docs):
        self._slate = slate_docs
        self._backtest = backtest_docs
        self.mlb_run_evaluations = self
    def find(self, q, projection=None):
        cohort = q.get("user_id")
        if cohort == "_slate":
            return _FakeFindCursor(self._slate)
        if cohort == "_slate_backtest":
            return _FakeFindCursor(self._backtest)
        return _FakeFindCursor([])


@pytest.mark.asyncio
async def test_cohort_breakdown_real_first_with_sufficient_real():
    # 35 real settled docs → real cohort drives the recommendation.
    real_docs = [_mk_doc(exp=9.0, final=8) for _ in range(35)]
    bt_docs   = [_mk_doc(exp=9.0, final=12) for _ in range(120)]
    db = _FakeMongo(real_docs, bt_docs)
    # When primary_user_id == "_slate" we shortcut and reuse primary_disp,
    # so the caller must pre-populate it as the real-cohort calibration.
    result = await _compute_cohort_breakdown(
        db,
        primary_user_id="_slate",
        primary_disp={"sample_size": 35, "suggested_ratio": 1.6},
        cutoff_iso="2020-01-01T00:00:00+00:00",
        cohort_priority="real_first",
    )
    assert result["ratio_source_used"] == "real_slate"
    assert result["backtest_reference"]["_contaminated"] is True
    assert result["backtest_reference"]["sample_size"] == 120
    assert result["real_slate"]["sample_size"] == 35


@pytest.mark.asyncio
async def test_cohort_breakdown_falls_back_to_preliminary_when_real_low():
    real_docs = [_mk_doc() for _ in range(5)]   # below 30 threshold
    bt_docs   = [_mk_doc() for _ in range(120)]
    db = _FakeMongo(real_docs, bt_docs)
    result = await _compute_cohort_breakdown(
        db,
        primary_user_id="_slate",
        primary_disp={"sample_size": 5},
        cutoff_iso="2020-01-01T00:00:00+00:00",
        cohort_priority="real_first",
    )
    assert result["ratio_source_used"] == "combined_preliminary"


@pytest.mark.asyncio
async def test_cohort_breakdown_backtest_only_override():
    real_docs = [_mk_doc() for _ in range(200)]
    bt_docs   = [_mk_doc() for _ in range(50)]
    db = _FakeMongo(real_docs, bt_docs)
    result = await _compute_cohort_breakdown(
        db,
        primary_user_id="_slate_backtest",
        primary_disp={"sample_size": 50},
        cutoff_iso="2020-01-01T00:00:00+00:00",
        cohort_priority="backtest_only",
    )
    assert result["ratio_source_used"] == "backtest_reference"
    assert result["policy"] == "backtest_only"
