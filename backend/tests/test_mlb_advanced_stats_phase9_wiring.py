"""Tests for Phase 9 wiring — weighted application of Statcast adjustments.

We don't invoke the full orchestrator (it requires DB + many async calls).
Instead we replicate the exact ponderación logic from the orchestrator
and verify the contract holds: data_quality strong/partial/missing → 60/35/0.

Also covers the metadata persistence shape that orchestrator must produce
on ``pick_payload["advanced_adjustments"]``.
"""
from __future__ import annotations

import pytest

from services.mlb_advanced_stats_helpers import (
    compute_all_advanced_adjustments,
    extract_mlb_advanced_context,
)


def _full_snapshot(quality="strong"):
    """Build a snapshot with all 4 blocks available."""
    return {
        "advanced_stats_snapshot": {
            "home_pitcher_advanced": {
                "available": True,
                "data_quality": quality,
                "pitcher": {
                    "era": 3.5, "xera": 4.2,  # xERA worse than ERA by 0.7
                    "xwoba_allowed": 0.348,
                    "barrel_pct_allowed": 10.0,
                    "hard_hit_pct_allowed": 44.0,
                    "k_pct": 22.0, "bb_pct": 9.0,
                    "whiff_pct": 26.0, "chase_pct": 30.0,
                },
                "sources_consulted": ["pybaseball"],
                "field_sources": {"xera": "pybaseball"},
            },
            "away_pitcher_advanced": {
                "available": True,
                "data_quality": quality,
                "pitcher": {
                    "era": 4.0, "xera": 3.9,
                    "xwoba_allowed": 0.320,
                    "barrel_pct_allowed": 7.0,
                    "hard_hit_pct_allowed": 38.0,
                },
                "sources_consulted": ["pybaseball"],
            },
            "home_team_advanced": {
                "available": True,
                "data_quality": quality,
                "team": {
                    "team_barrel_pct": 8.5,
                    "team_hard_hit_pct": 40.0,
                    "team_xwoba": 0.335,
                },
            },
            "away_team_advanced": {
                "available": True,
                "data_quality": quality,
                "team": {
                    "team_barrel_pct": 9.5,
                    "team_hard_hit_pct": 41.0,
                    "team_xwoba": 0.340,
                },
            },
        }
    }


def _empty_snapshot():
    return {
        "advanced_stats_snapshot": {
            "home_pitcher_advanced": {"available": False, "data_quality": "missing"},
            "away_pitcher_advanced": {"available": False, "data_quality": "missing"},
            "home_team_advanced":    {"available": False, "data_quality": "missing"},
            "away_team_advanced":    {"available": False, "data_quality": "missing"},
        }
    }


# ─────────────────────────────────────────────────────────────────────
# Context extractor (sanity)
# ─────────────────────────────────────────────────────────────────────
def test_extract_context_full_strong():
    ctx = extract_mlb_advanced_context(_full_snapshot())
    assert ctx["available"] is True
    assert ctx["data_quality"] == "strong"


def test_extract_context_missing():
    ctx = extract_mlb_advanced_context(_empty_snapshot())
    assert ctx["available"] is False
    assert ctx["data_quality"] == "missing"


def test_extract_context_passes_through_when_already_snapshot():
    """Accepts the snapshot directly (no wrapper)."""
    ctx = extract_mlb_advanced_context(_full_snapshot()["advanced_stats_snapshot"])
    assert ctx["available"] is True


# ─────────────────────────────────────────────────────────────────────
# compute_all_advanced_adjustments — basic shape
# ─────────────────────────────────────────────────────────────────────
def test_compute_summary_shape_with_strong_data():
    out = compute_all_advanced_adjustments(_full_snapshot())
    assert "advanced_stats_used" in out
    assert "advanced_stats_data_quality" in out
    assert "advanced_stats_adjustment_summary" in out
    summary = out["advanced_stats_adjustment_summary"]
    for key in ("home_pitcher_quality", "away_pitcher_quality",
                "over_under", "fragility", "starter_under"):
        assert key in summary
        block = summary[key]
        assert "adjustment" in block
        assert "reason_codes" in block
        assert "applied" in block


def test_compute_summary_when_missing_returns_zero_adjustments():
    out = compute_all_advanced_adjustments(_empty_snapshot())
    assert out["advanced_stats_used"] is False
    assert out["advanced_stats_data_quality"] == "missing"
    for block in out["advanced_stats_adjustment_summary"].values():
        assert block["adjustment"] == 0 or block["adjustment"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# Ponderación replication (orchestrator contract)
# ─────────────────────────────────────────────────────────────────────
WEIGHT_BY_QUALITY = {"strong": 0.60, "partial": 0.35, "thin": 0.35, "missing": 0.0}


def _replicate_orchestrator_weighting(pick_payload, market_label):
    """Mirror of the orchestrator's Phase 9 weighting logic."""
    summary = compute_all_advanced_adjustments(pick_payload)
    dq = summary.get("advanced_stats_data_quality") or "missing"
    weight = WEIGHT_BY_QUALITY.get(dq, 0.0)
    adj = summary.get("advanced_stats_adjustment_summary", {})

    is_under = "under" in market_label.lower() and "team total" not in market_label.lower()
    is_over = "over" in market_label.lower() and "team total" not in market_label.lower()

    raw_ou = float((adj.get("over_under") or {}).get("adjustment") or 0)
    raw_frag = float((adj.get("fragility") or {}).get("adjustment") or 0)
    raw_su = float((adj.get("starter_under") or {}).get("adjustment") or 0)
    raw_hpq = float((adj.get("home_pitcher_quality") or {}).get("adjustment") or 0)
    raw_apq = float((adj.get("away_pitcher_quality") or {}).get("adjustment") or 0)

    ou_conf = raw_ou if is_over else (-raw_ou if is_under else 0.0)
    su_conf = raw_su if is_under else 0.0
    pq_conf = (raw_hpq + raw_apq) / 2.0 * 0.5
    frag_conf = -raw_frag * 0.5

    raw_total = ou_conf + su_conf + pq_conf + frag_conf
    weighted = round(raw_total * weight, 2)
    return {
        "data_quality": dq, "weight": weight,
        "raw": raw_total, "weighted": weighted,
        "used": summary.get("advanced_stats_used"),
    }


def test_weighting_strong_quality_uses_60pct():
    pp = _full_snapshot()
    out = _replicate_orchestrator_weighting(pp, "Full Game Under 8.5")
    assert out["weight"] == 0.60
    assert out["used"] is True
    assert out["weighted"] != 0


def test_weighting_partial_quality_uses_35pct():
    pp = _full_snapshot(quality="partial")
    out = _replicate_orchestrator_weighting(pp, "Full Game Under 8.5")
    assert out["weight"] == 0.35


def test_weighting_missing_quality_uses_zero():
    pp = _empty_snapshot()
    out = _replicate_orchestrator_weighting(pp, "Full Game Under 8.5")
    assert out["weight"] == 0.0
    assert out["weighted"] == 0.0


def test_weighting_under_pick_flips_ou_sign():
    """If OU adjustment supports OVER (positive), an UNDER pick should see
    NEGATIVE contribution from that channel."""
    pp = _full_snapshot()
    summary = compute_all_advanced_adjustments(pp)
    ou_raw = (summary["advanced_stats_adjustment_summary"]["over_under"] or {}).get("adjustment")
    if ou_raw and ou_raw > 0:
        under = _replicate_orchestrator_weighting(pp, "Full Game Under 8.5")
        over = _replicate_orchestrator_weighting(pp, "Full Game Over 8.5")
        # Under should be smaller (or negative) compared to Over
        assert under["weighted"] <= over["weighted"]


def test_weighting_capped_via_helper_internal_caps():
    """Even with extreme inputs, raw_breakdown stays within helper's ±15 cap."""
    pp = _full_snapshot()
    summary = compute_all_advanced_adjustments(pp)
    for key in ("over_under", "fragility", "starter_under",
                 "home_pitcher_quality", "away_pitcher_quality"):
        adj = (summary["advanced_stats_adjustment_summary"].get(key) or {}).get("adjustment") or 0
        assert -15.0 <= adj <= 15.0


@pytest.mark.parametrize("market", [
    "Full Game Under 8.5",
    "Full Game Over 8.5",
    "Team Total Under 4.5",
    "NRFI",
])
def test_weighting_runs_clean_for_any_market(market):
    """Sanity: the weighting layer never raises for common markets."""
    pp = _full_snapshot()
    out = _replicate_orchestrator_weighting(pp, market)
    assert "weighted" in out
    assert isinstance(out["weighted"], (int, float))


def test_metadata_shape_matches_orchestrator_contract():
    """The summary must include keys the orchestrator persists on pick_payload."""
    summary = compute_all_advanced_adjustments(_full_snapshot())
    required = (
        "advanced_stats_used",
        "advanced_stats_data_quality",
        "advanced_stats_reason_codes",
        "advanced_stats_adjustment_summary",
        "advanced_stats_sources_consulted",
    )
    for k in required:
        assert k in summary
    # adjustment_summary inner shape
    for key in ("home_pitcher_quality", "away_pitcher_quality",
                "over_under", "fragility", "starter_under"):
        assert key in summary["advanced_stats_adjustment_summary"]
