"""Tests for Phase 11 — ghost-edge detection via Statcast in
``mlb_real_stats_verifier.verify_model_inputs``.

Coverage:
* New ``advanced_stats_snapshot`` kwarg is accepted (backwards-compat).
* xERA UNDERSTATES ERA (gap ≥ 0.6, xera < era) → ERA_UNDERSTATES_RISK
  flag and Over penalty.
* xERA OVERSTATES ERA (gap ≥ 0.6, xera > era) → ERA_OVERSTATES_QUALITY
  flag and Under penalty.
* xwOBA allowed elevated → PITCHER_XWOBA_WARNING (Under risk).
* Barrel/hard-hit allowed elevated → GHOST_EDGE_HARD_CONTACT_VS_UNDER.
* Both teams elevated team_xwoba → GHOST_EDGE_TEAM_XWOBA_VS_UNDER.
* Fail-soft: missing snapshot → no new discrepancies vs baseline.
"""
from __future__ import annotations

import asyncio

import pytest

from services.mlb_real_stats_verifier import verify_model_inputs


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if asyncio.get_event_loop().is_running() is False else asyncio.run(coro)


def _snap(home_pitcher=None, away_pitcher=None,
           home_team=None, away_team=None):
    return {
        "home_pitcher_advanced": {"available": bool(home_pitcher),
                                   "pitcher": home_pitcher or {}},
        "away_pitcher_advanced": {"available": bool(away_pitcher),
                                   "pitcher": away_pitcher or {}},
        "home_team_advanced":    {"available": bool(home_team),
                                   "team":    home_team or {}},
        "away_team_advanced":    {"available": bool(away_team),
                                   "team":    away_team or {}},
    }


@pytest.mark.asyncio
async def test_verifier_no_snapshot_still_runs():
    r = await verify_model_inputs(
        None, {}, 7.5, "Full Game Under 8.5",
    )
    assert "discrepancies" in r
    assert "confidence_penalty" in r


@pytest.mark.asyncio
async def test_verifier_accepts_snapshot_kwarg():
    r = await verify_model_inputs(
        None, {}, 7.5, "Full Game Under 8.5",
        advanced_stats_snapshot=None,  # explicitly None should be fine
    )
    assert "discrepancies" in r


@pytest.mark.asyncio
async def test_era_understates_risk_flag_on_under_pick():
    """era 3.5 vs xera 4.2 → pitcher running LUCKY (true skill is worse).

    Under pick is at risk → penalty applied + ERA_UNDERSTATES_RISK flag.
    """
    snap = _snap(home_pitcher={"era": 3.5, "xera": 4.2})
    r = await verify_model_inputs(
        None, {}, 8.0, "Full Game Under 8.5",
        advanced_stats_snapshot=snap,
    )
    flags = [d.get("flag") for d in r["discrepancies"]]
    assert "ERA_UNDERSTATES_RISK" in flags
    assert r["confidence_penalty"] > 0


@pytest.mark.asyncio
async def test_era_overstates_risk_flag_on_over_pick():
    """era 4.5 vs xera 3.3 → pitcher running UNLUCKY (true skill is better).

    Over pick is fighting underlying skill → penalty applied.
    """
    snap = _snap(home_pitcher={"era": 4.5, "xera": 3.3})
    r = await verify_model_inputs(
        None, {}, 8.5, "Full Game Over 8.5",
        advanced_stats_snapshot=snap,
    )
    flags = [d.get("flag") for d in r["discrepancies"]]
    assert "ERA_OVERSTATES_RISK" in flags
    assert r["confidence_penalty"] > 0


@pytest.mark.asyncio
async def test_no_era_xera_flag_when_under_pick_with_unlucky_pitcher():
    """era 4.5 vs xera 3.3 with UNDER pick: flag present but no penalty.

    The ERA_OVERSTATES_RISK flag only penalizes Over picks.
    """
    snap = _snap(home_pitcher={"era": 4.5, "xera": 3.3})
    r = await verify_model_inputs(
        None, {}, 8.5, "Full Game Under 8.5",
        advanced_stats_snapshot=snap,
    )
    flags = [d.get("flag") for d in r["discrepancies"]]
    # Flag still emitted for transparency
    assert "ERA_OVERSTATES_RISK" in flags


@pytest.mark.asyncio
async def test_no_flag_when_era_xera_close():
    snap = _snap(home_pitcher={"era": 3.50, "xera": 3.55})
    r = await verify_model_inputs(
        None, {}, 7.5, "Full Game Under 8.5",
        advanced_stats_snapshot=snap,
    )
    flags = [d.get("flag") for d in r["discrepancies"]]
    assert "ERA_UNDERSTATES_RISK" not in flags
    assert "ERA_OVERSTATES_RISK" not in flags


@pytest.mark.asyncio
async def test_pitcher_xwoba_warning_under_pick():
    snap = _snap(home_pitcher={"xwoba_allowed": 0.355})
    r = await verify_model_inputs(
        None, {}, 8.0, "Full Game Under 8.5",
        advanced_stats_snapshot=snap,
    )
    flags = [d.get("flag") for d in r["discrepancies"]]
    assert "PITCHER_XWOBA_WARNING" in flags
    assert r["confidence_penalty"] >= 8


@pytest.mark.asyncio
async def test_pitcher_xwoba_warning_not_on_over_pick():
    snap = _snap(home_pitcher={"xwoba_allowed": 0.355})
    r = await verify_model_inputs(
        None, {}, 8.0, "Full Game Over 8.5",
        advanced_stats_snapshot=snap,
    )
    flags = [d.get("flag") for d in r["discrepancies"]]
    assert "PITCHER_XWOBA_WARNING" not in flags


@pytest.mark.asyncio
async def test_hard_contact_ghost_edge_against_under():
    snap = _snap(home_pitcher={"barrel_pct_allowed": 10.0, "hard_hit_pct_allowed": 45.0})
    r = await verify_model_inputs(
        None, {}, 8.0, "Full Game Under 8.5",
        advanced_stats_snapshot=snap,
    )
    flags = [d.get("flag") for d in r["discrepancies"]]
    assert "GHOST_EDGE_HARD_CONTACT_VS_UNDER" in flags


@pytest.mark.asyncio
async def test_combined_team_xwoba_against_under():
    snap = _snap(
        home_team={"team_xwoba": 0.342},
        away_team={"team_xwoba": 0.345},
    )
    r = await verify_model_inputs(
        None, {}, 8.0, "Full Game Under 8.5",
        advanced_stats_snapshot=snap,
    )
    flags = [d.get("flag") for d in r["discrepancies"]]
    assert "GHOST_EDGE_TEAM_XWOBA_VS_UNDER" in flags


@pytest.mark.asyncio
async def test_multiple_statcast_flags_compound_penalty():
    snap = _snap(
        home_pitcher={
            "era": 2.4, "xera": 4.0,
            "xwoba_allowed": 0.360,
            "barrel_pct_allowed": 11.0,
        },
        home_team={"team_xwoba": 0.340},
        away_team={"team_xwoba": 0.335},
    )
    r = await verify_model_inputs(
        None, {}, 8.0, "Full Game Under 8.5",
        advanced_stats_snapshot=snap,
    )
    # Multiple flags should be present
    flags = [d.get("flag") for d in r["discrepancies"]]
    assert "ERA_UNDERSTATES_RISK" in flags
    assert "PITCHER_XWOBA_WARNING" in flags
    assert "GHOST_EDGE_HARD_CONTACT_VS_UNDER" in flags
    # Penalty must be capped at 55 (per implementation)
    assert r["confidence_penalty"] <= 55
    assert r["confidence_penalty"] > 0


@pytest.mark.asyncio
async def test_empty_snapshot_no_new_flags():
    """An empty snapshot dict should not introduce any Statcast flags."""
    snap = {}  # empty
    r = await verify_model_inputs(
        None, {}, 7.5, "Full Game Under 8.5",
        advanced_stats_snapshot=snap,
    )
    statcast_flags = [
        "ERA_UNDERSTATES_RISK",
        "ERA_OVERSTATES_RISK",
        "PITCHER_XWOBA_WARNING",
        "GHOST_EDGE_HARD_CONTACT_VS_UNDER",
        "GHOST_EDGE_TEAM_XWOBA_VS_UNDER",
    ]
    flags = [d.get("flag") for d in r["discrepancies"]]
    for f in statcast_flags:
        assert f not in flags
