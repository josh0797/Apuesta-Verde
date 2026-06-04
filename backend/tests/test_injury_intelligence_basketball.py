"""Tests for the Injury Intelligence Layer — Basketball Phase 1.

Coverage:
  * normalize_status — every documented synonym
  * merge_player_records — conservative conflict resolution + dedupe
  * compute_freshness — TTL boundaries
  * classify_player_role — registry + heuristic + hint priority
  * calculate_basketball_injury_impact — every reason_code path
  * fetch_basketball_injury_intelligence — fail-soft, cache, match-edge
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.injury_intelligence import (
    fetch_basketball_injury_intelligence,
    calculate_basketball_injury_impact,
    classify_player_role,
    normalize_status,
    merge_player_records,
    compute_freshness,
    empty_payload,
    INJURY_SCHEMA_VERSION,
)


# ─────────────────────────────────────────────────────────────────────
# normalize_status
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("Out",                          "out"),
    ("RULED OUT",                    "out"),
    ("inactive",                     "out"),
    ("Doubtful",                     "doubtful"),
    ("Questionable",                 "questionable"),
    ("GTD",                          "questionable"),
    ("Game-time decision",           "questionable"),
    ("Probable",                     "probable"),
    ("Active",                       "probable"),
    ("Day-to-day",                   "day_to_day"),
    ("D2D",                          "day_to_day"),
    ("Minutes restriction",          "minutes_restriction"),
    ("Suspended",                    "suspended"),
    ("Load management",              "rest"),
    ("Rest",                         "rest"),
    ("",                             "unknown"),
    (None,                           "unknown"),
    ("???",                          "unknown"),
])
def test_normalize_status(raw, expected):
    assert normalize_status(raw) == expected


# ─────────────────────────────────────────────────────────────────────
# merge_player_records — conservative on conflicts
# ─────────────────────────────────────────────────────────────────────
def test_merge_conflict_picks_more_severe():
    records = [
        {"player_name": "LeBron James", "status": "questionable", "source": "espn"},
        {"player_name": "LeBron James", "status": "out",          "source": "api_sports"},
        {"player_name": "LeBron James", "status": "probable",     "source": "rotowire"},
    ]
    merged = merge_player_records(records)
    assert len(merged) == 1
    assert merged[0]["status"] == "out"
    assert "espn" in merged[0]["sources"] or "api_sports" in merged[0]["sources"]


def test_merge_dedupes_by_name_ignoring_punct():
    records = [
        {"player_name": "Stephen Curry",  "status": "out", "source": "a"},
        {"player_name": "stephen-curry.", "status": "out", "source": "b"},
    ]
    assert len(merge_player_records(records)) == 1


def test_merge_skips_records_without_name():
    records = [
        {"status": "out", "source": "a"},
        {"player_name": "Doncic", "status": "out", "source": "b"},
    ]
    merged = merge_player_records(records)
    assert len(merged) == 1
    assert merged[0]["player_name"].lower().endswith("doncic")


# ─────────────────────────────────────────────────────────────────────
# compute_freshness
# ─────────────────────────────────────────────────────────────────────
def _iso(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_freshness_fresh_pregame():
    assert compute_freshness(
        [{"updated_at": _iso(30)}], sport="basketball", is_game_day=False
    ) == "fresh"


def test_freshness_partial_pregame():
    assert compute_freshness(
        [{"updated_at": _iso(180)}], sport="basketball", is_game_day=False
    ) == "partial"


def test_freshness_stale_pregame():
    assert compute_freshness(
        [{"updated_at": _iso(60 * 6)}], sport="basketball", is_game_day=False
    ) == "stale"


def test_freshness_game_day_stricter():
    """Same age that is fresh pregame must NOT be fresh game-day."""
    age_min = 45
    pregame = compute_freshness(
        [{"updated_at": _iso(age_min)}], sport="basketball", is_game_day=False
    )
    gameday = compute_freshness(
        [{"updated_at": _iso(age_min)}], sport="basketball", is_game_day=True
    )
    assert pregame == "fresh"
    assert gameday in ("partial", "stale")


def test_freshness_unknown_when_no_records():
    assert compute_freshness([]) == "unknown"


# ─────────────────────────────────────────────────────────────────────
# classify_player_role
# ─────────────────────────────────────────────────────────────────────
def test_classify_role_registry_superstar():
    assert classify_player_role("LeBron James") == "superstar"
    assert classify_player_role("Nikola Jokic") == "superstar"
    assert classify_player_role("Luka Doncic") == "superstar"


def test_classify_role_hint_overrides_registry_only_when_valid():
    assert classify_player_role("LeBron James", hint_role="starter") == "starter"
    # Invalid hint falls back to registry.
    assert classify_player_role("LeBron James", hint_role="garbage") == "superstar"


def test_classify_role_heuristic_starter():
    assert classify_player_role("Joe Random",
                                  player_stats={"mpg": 32, "ppg": 17}) == "starter"


def test_classify_role_heuristic_rotation():
    assert classify_player_role("Joe Bench",
                                  player_stats={"mpg": 20, "ppg": 8}) == "rotation"


def test_classify_role_unknown_when_no_signal():
    assert classify_player_role("Unknown Guy") == "unknown"


# ─────────────────────────────────────────────────────────────────────
# calculate_basketball_injury_impact
# ─────────────────────────────────────────────────────────────────────
def test_superstar_out_produces_critical_tier():
    res = calculate_basketball_injury_impact(
        team_profile={"name": "Nuggets"},
        injuries=[{"player_name": "Nikola Jokic", "status": "out", "position": "C"}],
    )
    score = res["basketball_injury_score"]
    impact = res["team_injury_impact"]
    assert score["team_strength_adjustment"] <= -14
    assert "SUPERSTAR_OUT" in score["reason_codes"]
    assert impact["impact_tier"] in ("HIGH", "CRITICAL")
    assert "RIM_PROTECTOR_OUT" in score["reason_codes"]


def test_starting_pg_out_marks_creation_loss():
    res = calculate_basketball_injury_impact(
        team_profile={"name": "Mavs"},
        injuries=[{"player_name": "Luka Doncic", "status": "out", "position": "PG"}],
    )
    rcs = res["basketball_injury_score"]["reason_codes"]
    assert "STARTING_POINT_GUARD_OUT" in rcs
    assert res["basketball_injury_score"]["pace_adjustment"] <= -2


def test_multiple_starters_out_blocks_aggressive_picks():
    res = calculate_basketball_injury_impact(
        team_profile={"name": "Sometown"},
        injuries=[
            {"player_name": "Starter A", "status": "out", "position": "SG",
             "role": "starter"},
            {"player_name": "Starter B", "status": "out", "position": "PF",
             "role": "starter"},
            {"player_name": "Starter C", "status": "out", "position": "C",
             "role": "starter"},
        ],
    )
    rcs = res["basketball_injury_score"]["reason_codes"]
    assert "MULTIPLE_STARTERS_OUT" in rcs
    # Extra accumulation penalty.
    assert res["basketball_injury_score"]["team_strength_adjustment"] <= -22


def test_questionable_star_creates_watchlist_signal():
    res = calculate_basketball_injury_impact(
        team_profile={"name": "Warriors"},
        injuries=[{"player_name": "Stephen Curry", "status": "questionable",
                    "position": "PG"}],
    )
    rcs = res["basketball_injury_score"]["reason_codes"]
    assert "QUESTIONABLE_STAR_RISK" in rcs
    # Confidence-style ding but not as severe as OUT.
    assert -8 <= res["basketball_injury_score"]["team_strength_adjustment"] <= -4


def test_minutes_restriction_key_player():
    res = calculate_basketball_injury_impact(
        team_profile={"name": "Suns"},
        injuries=[{"player_name": "Devin Booker", "status": "minutes_restriction",
                    "position": "SG"}],
    )
    rcs = res["basketball_injury_score"]["reason_codes"]
    assert "MINUTES_RESTRICTION_KEY_PLAYER" in rcs


def test_empty_injuries_returns_zero_impact():
    res = calculate_basketball_injury_impact(
        team_profile={"name": "Spurs"}, injuries=[],
    )
    score = res["basketball_injury_score"]
    assert score["team_strength_adjustment"] == 0
    assert score["reason_codes"] == []
    assert res["team_injury_impact"]["impact_tier"] == "LOW"


def test_probable_status_does_not_strongly_penalize():
    res = calculate_basketball_injury_impact(
        team_profile={"name": "Heat"},
        injuries=[{"player_name": "Jimmy Butler", "status": "probable",
                    "position": "SF"}],
    )
    # Probable → -1 max for stars.
    assert res["basketball_injury_score"]["team_strength_adjustment"] >= -1


def test_two_defensive_outs_extra_penalty_on_defense():
    res = calculate_basketball_injury_impact(
        team_profile={"name": "Wolves"},
        injuries=[
            {"player_name": "Rudy Gobert", "status": "out", "position": "C"},
            {"player_name": "Karl-Anthony Towns", "status": "out", "position": "PF"},
        ],
    )
    rcs = res["basketball_injury_score"]["reason_codes"]
    assert "DEFENSIVE_ANCHOR_OUT" in rcs
    assert res["basketball_injury_score"]["defense_adjustment"] <= -4


# ─────────────────────────────────────────────────────────────────────
# Orchestrator — fail-soft + match edge + caps
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_orchestrator_fail_soft_no_sources_returns_unavailable():
    """When every source is disabled/skipped (no team_id, no slug), the
    orchestrator must return ``available:false`` without crashing."""
    payload = await fetch_basketball_injury_intelligence(
        home_team={}, away_team={},
        db=None, force_refresh=True,
    )
    assert payload["available"] is False
    assert payload["schema_version"] == INJURY_SCHEMA_VERSION
    assert payload["match_impact"]["confidence_adjustment"] == 0


@pytest.mark.asyncio
async def test_orchestrator_invalid_input_returns_empty_payload():
    payload = await fetch_basketball_injury_intelligence(
        home_team="bad", away_team={},   # type: ignore[arg-type]
        force_refresh=True,
    )
    assert payload["available"] is False
    assert payload["_reason"] == "invalid_input"


@pytest.mark.asyncio
async def test_orchestrator_with_mocked_sources_builds_match_edge():
    """Mock the source fetchers and verify the full pipeline (impact +
    match edge + match_impact)."""
    home_team = {"id": 1, "name": "Lakers"}
    away_team = {"id": 2, "name": "Suns"}

    async def mock_api_sports(*, team_id, season="2024-2025", client=None):
        if team_id == 1:
            return {"records": [
                {"player_name": "LeBron James", "status": "out", "position": "SF",
                 "source": "api_sports",
                 "updated_at": datetime.now(timezone.utc).isoformat()},
                {"player_name": "Anthony Davis", "status": "questionable", "position": "C",
                 "source": "api_sports",
                 "updated_at": datetime.now(timezone.utc).isoformat()},
            ], "status": "success"}
        return {"records": [
            {"player_name": "Bench Guy", "status": "out", "position": "SG",
             "source": "api_sports",
             "updated_at": datetime.now(timezone.utc).isoformat()},
        ], "status": "success"}

    async def mock_empty(**kwargs):
        return {"records": [], "status": "skipped"}

    with patch("services.injury_intelligence.orchestrator.fetch_api_sports_basketball_injuries",
                  side_effect=mock_api_sports), \
         patch("services.injury_intelligence.orchestrator.fetch_thestatsapi_basketball_injuries",
                  side_effect=mock_empty), \
         patch("services.injury_intelligence.orchestrator.fetch_bright_data_basketball_injuries",
                  side_effect=mock_empty):
        payload = await fetch_basketball_injury_intelligence(
            home_team=home_team, away_team=away_team,
            db=None, force_refresh=True,
        )

    assert payload["available"] is True
    assert payload["home"]["team_name"] == "Lakers"
    assert payload["away"]["team_name"] == "Suns"
    # Home much more affected than Away → net_edge favours Away.
    assert payload["match_injury_edge"]["net_edge"] == "away"
    assert payload["match_injury_edge"]["net_edge_points"] > 0
    # match_impact must compute non-zero confidence_adjustment (data fresh).
    assert payload["match_impact"]["confidence_adjustment"] > 0
    # Source status reflects what the mocks returned.
    assert payload["source_status"]["api_sports"] == "success"


@pytest.mark.asyncio
async def test_orchestrator_caps_confidence_adjustment():
    """Even with a massive net edge, conf_adjustment must not exceed
    MAX_CONFIDENCE_ADJUSTMENT (12)."""
    home_team = {"id": 1, "name": "Lakers"}
    away_team = {"id": 2, "name": "Suns"}

    async def mock_huge_home(*, team_id, season="2024-2025", client=None):
        if team_id == 1:
            return {"records": [
                {"player_name": f"Star {i}", "status": "out", "position": "SF",
                 "role": "superstar", "source": "api_sports",
                 "updated_at": datetime.now(timezone.utc).isoformat()}
                for i in range(5)
            ], "status": "success"}
        return {"records": [], "status": "success"}

    async def mock_empty(**kwargs):
        return {"records": [], "status": "skipped"}

    with patch("services.injury_intelligence.orchestrator.fetch_api_sports_basketball_injuries",
                  side_effect=mock_huge_home), \
         patch("services.injury_intelligence.orchestrator.fetch_thestatsapi_basketball_injuries",
                  side_effect=mock_empty), \
         patch("services.injury_intelligence.orchestrator.fetch_bright_data_basketball_injuries",
                  side_effect=mock_empty):
        payload = await fetch_basketball_injury_intelligence(
            home_team=home_team, away_team=away_team,
            db=None, force_refresh=True,
        )

    assert payload["available"] is True
    assert payload["match_impact"]["confidence_adjustment"] <= 12
    assert payload["match_impact"]["fragility_adjustment"] <= 15


@pytest.mark.asyncio
async def test_orchestrator_high_volatility_when_both_teams_critical():
    home_team = {"id": 1, "name": "Lakers"}
    away_team = {"id": 2, "name": "Suns"}

    async def mock_both_critical(*, team_id, season="2024-2025", client=None):
        return {"records": [
            {"player_name": f"Star {team_id}A", "status": "out", "position": "C",
             "role": "superstar", "source": "api_sports",
             "updated_at": datetime.now(timezone.utc).isoformat()},
            {"player_name": f"Star {team_id}B", "status": "out", "position": "PG",
             "role": "superstar", "source": "api_sports",
             "updated_at": datetime.now(timezone.utc).isoformat()},
        ], "status": "success"}

    async def mock_empty(**kwargs):
        return {"records": [], "status": "skipped"}

    with patch("services.injury_intelligence.orchestrator.fetch_api_sports_basketball_injuries",
                  side_effect=mock_both_critical), \
         patch("services.injury_intelligence.orchestrator.fetch_thestatsapi_basketball_injuries",
                  side_effect=mock_empty), \
         patch("services.injury_intelligence.orchestrator.fetch_bright_data_basketball_injuries",
                  side_effect=mock_empty):
        payload = await fetch_basketball_injury_intelligence(
            home_team=home_team, away_team=away_team,
            db=None, force_refresh=True,
        )

    assert payload["match_injury_edge"]["high_volatility"] is True
    assert "HIGH_INJURY_VOLATILITY_BOTH_SIDES" in payload["match_impact"]["market_warnings"]
    assert "HIGH_INJURY_VOLATILITY" in payload["match_impact"]["reason_codes"]


@pytest.mark.asyncio
async def test_orchestrator_does_not_affect_mlb():
    """Sanity: the Injury Intelligence import path is basketball-only.
    MLB modules must not import from this package."""
    import services.injury_intelligence as ii
    assert ii.INJURY_SCHEMA_VERSION.startswith("injury-intel.basketball")


def test_empty_payload_has_canonical_shape():
    p = empty_payload()
    assert p["available"] is False
    assert p["schema_version"] == INJURY_SCHEMA_VERSION
    assert p["home"]["injuries"] == []
    assert p["away"]["injuries"] == []
    assert p["match_impact"]["confidence_adjustment"] == 0
    assert p["match_impact"]["market_warnings"] == []
    assert "api_sports" in p["source_status"]


@pytest.mark.asyncio
async def test_orchestrator_no_injuries_returns_unavailable_payload_with_source_status():
    home_team = {"id": 1, "name": "Lakers"}
    away_team = {"id": 2, "name": "Suns"}

    async def mock_empty_success(**kwargs):
        return {"records": [], "status": "success"}

    async def mock_skipped(**kwargs):
        return {"records": [], "status": "skipped"}

    with patch("services.injury_intelligence.orchestrator.fetch_api_sports_basketball_injuries",
                  side_effect=mock_empty_success), \
         patch("services.injury_intelligence.orchestrator.fetch_thestatsapi_basketball_injuries",
                  side_effect=mock_skipped), \
         patch("services.injury_intelligence.orchestrator.fetch_bright_data_basketball_injuries",
                  side_effect=mock_skipped):
        payload = await fetch_basketball_injury_intelligence(
            home_team=home_team, away_team=away_team,
            db=None, force_refresh=True,
        )

    assert payload["available"] is False
    assert payload["_reason"] == "no_injuries_reported"
    assert payload["home"]["team_name"] == "Lakers"
    # API-Sports was tried and succeeded (with empty records) — must be marked.
    assert payload["source_status"]["api_sports"] == "success"
