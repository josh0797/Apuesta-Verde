"""F94 — Football Live Visibility (independent of priority filters).

Five obligatory tests:
  1. exotic-league live match appears in Live
  2. live match without sportytrader appears visible but not analyzed
  3. fixture discarded by market-identity-missing appears in discards
  4. recommended=0 does not hide the detail (covered separately in
     pick_run payload; here we assert visibility audit independent of
     pick_run state)
  5. priority filters DO NOT remove fixtures from the UI list — they only
     classify them (hidden_by_priority_filter must be 0).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.football_live_visibility import (
    classify_live_fixture,
    compute_football_live_visibility,
)


def _fx(*, league_id=None, league_name="Friendly", country="World",
        home="Saudi Arabia", away="Uruguay", status="1H",
        elapsed=15, fid="fx-1"):
    return {
        "fixture": {"id": fid, "status": {"short": status}, "elapsed": elapsed,
                    "date": "2026-06-15T20:00:00+00:00", "timestamp": 1781550000},
        "league":  {"id": league_id, "name": league_name, "country": country},
        "teams":   {"home": {"id": 1, "name": home},
                    "away": {"id": 2, "name": away}},
    }


# ─────────────────────────────────────────────────────────────────────
# 1. Exotic-league live fixture appears as VISIBLE / DISCARDED / EXOTIC_LEAGUE
# ─────────────────────────────────────────────────────────────────────
def test_exotic_league_live_fixture_is_visible_but_discarded():
    fx = _fx(league_id=256, league_name="USL League Two", country="USA",
             home="Christos", away="Annapolis Blues")
    c = classify_live_fixture(fx)
    assert c["visibility_status"] == "VISIBLE"
    assert c["analysis_status"]   == "DISCARDED"
    assert c["discard_reason"]    == "EXOTIC_LEAGUE"


# ─────────────────────────────────────────────────────────────────────
# 2. Live match without sportytrader (no league_id) → visible + secondary
# ─────────────────────────────────────────────────────────────────────
def test_live_without_sportytrader_visible_with_secondary_reason():
    fx = _fx(league_id=None, league_name="Exotic Cup", country="Atlantis",
             home="FC Local", away="Atlantic Club")
    c = classify_live_fixture(fx)
    assert c["visibility_status"] == "VISIBLE"
    assert c["analysis_status"]   == "DISCARDED"
    assert "SPORTYTRADER_NOT_FOUND" in c["secondary_reasons"]


# ─────────────────────────────────────────────────────────────────────
# 3. Fixture without league_id AND without league_name → NO_MARKET_IDENTITY
# ─────────────────────────────────────────────────────────────────────
def test_market_identity_missing_appears_in_discards():
    fx = _fx(league_id=None, league_name="", country="",
             home="FC Local", away="Atlantic Club")
    c = classify_live_fixture(fx)
    assert c["analysis_status"]   == "DISCARDED"
    assert c["discard_reason"]    == "NO_MARKET_IDENTITY"
    assert "MISSING_LEAGUE_FIELDS" in c["secondary_reasons"]


# ─────────────────────────────────────────────────────────────────────
# 4. National-team competition (World Cup) → ANALYZABLE even if not in
#    the static allowlist.
# ─────────────────────────────────────────────────────────────────────
def test_national_team_live_fixture_is_analyzable():
    # Use the detector via league_name containing 'World Cup'.
    fx = _fx(league_id=1, league_name="World Cup", country="World",
             home="Saudi Arabia", away="Uruguay")
    c = classify_live_fixture(fx)
    assert c["analysis_status"]   == "ANALYZABLE"
    assert c["_is_national_team"] is True
    # World Cup is in the static allowlist (tier_1) so we just assert it
    # is one of the accepted tiers — what matters is ANALYZABLE.
    from services import football_competitions as fc
    assert (c["competition_meta"] or {}).get("tier") in fc.ALLOWED_TIERS


# ─────────────────────────────────────────────────────────────────────
# 5. compute_football_live_visibility never hides fixtures (invariant).
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_priority_filter_never_hides_fixtures_from_visibility_payload():
    raw = [
        _fx(league_id=256, league_name="USL League Two", country="USA",
            home="Christos", away="Annapolis Blues"),                          # exotic
        _fx(league_id=1,   league_name="World Cup",      country="World",
            home="Saudi Arabia", away="Uruguay"),                              # analyzable
        _fx(league_id=None, league_name="", country="",
            home="FC Local", away="Atlantic Club"),                            # market-missing
    ]
    with patch("services.football_live_aggregator.fetch_live_football_fixtures",
                AsyncMock(return_value=(raw, {"src": "test"}))):
        payload = await compute_football_live_visibility(None, MagicMock())

    assert payload["ok"] is True
    assert len(payload["items"]) == 3
    debug = payload["live_debug"]
    assert debug["provider_live_count"]          == 3
    assert debug["after_sport_filter_count"]     == 3
    assert debug["after_league_filter_count"]    == 3
    assert debug["visible_live_count"]           == 3
    assert debug["analysis_eligible_live_count"] == 1
    # F94 invariant: visibility filter never hides; analysis chooses.
    assert debug["hidden_by_priority_filter"]    == 0


@pytest.mark.asyncio
async def test_visibility_payload_handles_aggregator_failure():
    """Fail-soft: even if both the aggregator AND the fallback raise the
    endpoint returns a clean empty payload (never throws)."""
    with patch("services.football_live_aggregator.fetch_live_football_fixtures",
                AsyncMock(side_effect=RuntimeError("aggregator down"))), \
         patch("services.api_sports.fixtures_live",
                AsyncMock(side_effect=RuntimeError("api_sports down"))):
        payload = await compute_football_live_visibility(None, MagicMock())
    assert payload["ok"] is True
    assert payload["items"] == []
    assert payload["live_debug"]["provider_live_count"] == 0
