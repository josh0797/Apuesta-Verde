"""Sprint-F98.1 · Tests for upstream hydration improvements.

Three layers tested here:
  A. ``thesportsdb_client.fetch_last_events_by_team`` — new endpoint
     adapter for ``eventslast.php``.
  B. ``data_ingestion`` integration — when API-Sports returns nothing
     for recent_fixtures, the TheSportsDB fallback kicks in.
  C. ``football_national_team_seed`` — idempotent seed populator for
     top national teams, with skip-if-recent guard.

All tests are offline: TheSportsDB HTTP calls are mocked so the suite
remains deterministic and fast.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.external_sources.thesportsdb_client import (
    _normalize_event_to_recent_fixture,
    fetch_last_events_by_team,
)
from services.football_national_team_seed import (
    SEED_SCHEMA_VERSION,
    TOP_NATIONAL_TEAMS,
    _aggregate_recent,
    _normalize_team_name,
    _resolve_national_team_id,
    seed_national_team_recent_form,
    seed_one_national_team,
)


# ─────────────────────────────────────────────────────────────────────
# A. fetch_last_events_by_team + _normalize_event_to_recent_fixture
# ─────────────────────────────────────────────────────────────────────
_TSDB_RAW_EVENT_ARG_VS_ITALY = {
    "idEvent":      "1839472",
    "strHomeTeam":  "Argentina",
    "strAwayTeam":  "Italy",
    "intHomeScore": "3",
    "intAwayScore": "1",
    "dateEvent":    "2026-06-17",
    "strTimestamp": "2026-06-17T01:00:00",
    "idHomeTeam":   "134509",
    "idAwayTeam":   "133616",
    "idLeague":     "4480",
    "strLeague":    "FIFA World Cup",
    "strSeason":    "2026",
}


def test_normalize_event_handles_zero_scores_correctly():
    """REGRESSION GUARD: 0 is a valid score; the normalizer must
    NOT drop events where home_score=0 or away_score=0."""
    ev = dict(_TSDB_RAW_EVENT_ARG_VS_ITALY)
    ev["intHomeScore"] = "0"
    ev["intAwayScore"] = "2"
    out = _normalize_event_to_recent_fixture(ev)
    assert out is not None
    assert out["goals"]["home"] == 0
    assert out["goals"]["away"] == 2


def test_normalize_event_drops_missing_scores():
    """When BOTH scores are missing the event is useless for L5 aggregates."""
    ev = dict(_TSDB_RAW_EVENT_ARG_VS_ITALY)
    ev["intHomeScore"] = None
    ev["intAwayScore"] = None
    out = _normalize_event_to_recent_fixture(ev)
    assert out is None


def test_normalize_event_produces_api_sports_compatible_shape():
    """The shape MUST match what ``normalize_recent_fixtures`` expects
    (envelope of API-Sports ``fixtures_last_n``)."""
    out = _normalize_event_to_recent_fixture(_TSDB_RAW_EVENT_ARG_VS_ITALY)
    assert out is not None
    assert "fixture" in out and "teams" in out and "goals" in out
    assert out["fixture"]["status"]["short"] == "FT"
    assert out["teams"]["home"]["name"] == "Argentina"
    assert out["teams"]["away"]["name"] == "Italy"
    assert out["goals"]["home"] == 3
    assert out["_provider"] == "thesportsdb"


def test_normalize_event_fail_soft_on_garbage():
    assert _normalize_event_to_recent_fixture(None) is None
    assert _normalize_event_to_recent_fixture("garbage") is None
    assert _normalize_event_to_recent_fixture([]) is None
    assert _normalize_event_to_recent_fixture({}) is None


@pytest.mark.asyncio
async def test_fetch_last_events_returns_empty_when_disabled():
    """When TheSportsDB is disabled, the function must NOT raise."""
    with patch(
        "services.external_sources.thesportsdb_client.is_enabled",
        return_value=False,
    ):
        result = await fetch_last_events_by_team("134509", n=5)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_last_events_returns_empty_for_blank_team_id():
    """Blank team id must short-circuit (no HTTP call)."""
    result = await fetch_last_events_by_team("", n=5)
    assert result == []
    result = await fetch_last_events_by_team(None, n=5)  # type: ignore[arg-type]
    assert result == []


@pytest.mark.asyncio
async def test_fetch_last_events_clamps_n_to_provider_max():
    """TheSportsDB returns at most 5 — n=100 must not error."""
    fake_payload = {"results": [_TSDB_RAW_EVENT_ARG_VS_ITALY] * 7}
    with patch(
        "services.external_sources.thesportsdb_client.is_enabled",
        return_value=True,
    ), patch.dict("os.environ", {"THESPORTSDB_KEY": "test"}), patch(
        "services.external_sources.thesportsdb_client._request",
        new=AsyncMock(return_value=fake_payload),
    ):
        result = await fetch_last_events_by_team("134509", n=100)
    # Provider cap is 5; the function must respect it.
    assert 1 <= len(result) <= 5


@pytest.mark.asyncio
async def test_fetch_last_events_returns_normalised_items():
    fake_payload = {"results": [_TSDB_RAW_EVENT_ARG_VS_ITALY]}
    with patch(
        "services.external_sources.thesportsdb_client.is_enabled",
        return_value=True,
    ), patch.dict("os.environ", {"THESPORTSDB_KEY": "test"}), patch(
        "services.external_sources.thesportsdb_client._request",
        new=AsyncMock(return_value=fake_payload),
    ):
        result = await fetch_last_events_by_team("134509", n=5)
    assert len(result) == 1
    assert result[0]["teams"]["home"]["name"] == "Argentina"
    assert result[0]["goals"]["home"] == 3


@pytest.mark.asyncio
async def test_fetch_last_events_fail_soft_on_garbage_payload():
    """Provider returning a non-dict / non-list payload must not raise."""
    with patch(
        "services.external_sources.thesportsdb_client.is_enabled",
        return_value=True,
    ), patch.dict("os.environ", {"THESPORTSDB_KEY": "test"}), patch(
        "services.external_sources.thesportsdb_client._request",
        new=AsyncMock(return_value={"results": "not-a-list"}),
    ):
        result = await fetch_last_events_by_team("134509", n=5)
    assert result == []


# ─────────────────────────────────────────────────────────────────────
# B. seed populator
# ─────────────────────────────────────────────────────────────────────
def test_normalize_team_name():
    assert _normalize_team_name("Argentina") == "argentina"
    assert _normalize_team_name("  NEW   ZEALAND ") == "new zealand"
    assert _normalize_team_name("São Paulo") == "sao paulo"


def test_aggregate_recent_handles_team_perspective():
    events = [
        # Argentina played AS HOME, won 3-1
        {"teams": {"home": {"id": "134509"}, "away": {"id": "1"}},
         "goals": {"home": 3, "away": 1},
         "fixture": {"id": "a", "date": "2026-06-17"},
         "league":  {"name": "WC"}},
        # Argentina played AS AWAY, lost 0-2
        {"teams": {"home": {"id": "2"}, "away": {"id": "134509"}},
         "goals": {"home": 2, "away": 0},
         "fixture": {"id": "b", "date": "2026-06-10"},
         "league":  {"name": "WC"}},
    ]
    agg = _aggregate_recent(events, perspective_team_id="134509")
    assert agg["matches_count"] == 2
    assert agg["goals_scored_avg"]   == pytest.approx(1.5)
    assert agg["goals_conceded_avg"] == pytest.approx(1.5)
    assert agg["matches"][0]["is_home"] is True
    assert agg["matches"][1]["is_home"] is False


def test_aggregate_recent_drops_events_without_scores():
    events = [
        {"teams": {"home": {"id": "134509"}, "away": {"id": "1"}},
         "goals": {"home": None, "away": None}},   # dropped
        {"teams": {"home": {"id": "134509"}, "away": {"id": "1"}},
         "goals": {"home": 1, "away": 0}},
    ]
    agg = _aggregate_recent(events, perspective_team_id="134509")
    assert agg["matches_count"] == 1


def test_aggregate_recent_handles_empty_list():
    agg = _aggregate_recent([], perspective_team_id="x")
    assert agg == {"matches_count": 0, "matches": []}


@pytest.mark.asyncio
async def test_resolve_national_team_id_prefers_soccer_world_cup_match():
    """Filter out "Argentina Rugby" / "Argentina U17" — pick the
    "Argentina / FIFA World Cup / Soccer" entry."""
    candidates = [
        {"idTeam": "999",    "strTeam": "Argentina Rugby",
         "strLeague": "Rugby World Cup",  "strSport": "Rugby"},
        {"idTeam": "152251", "strTeam": "Argentina U20",
         "strLeague": "FIFA U20 World Cup", "strSport": "Soccer"},
        {"idTeam": "134509", "strTeam": "Argentina",
         "strLeague": "FIFA World Cup",   "strSport": "Soccer"},
    ]
    with patch(
        "services.external_sources.thesportsdb_client.is_enabled",
        return_value=True,
    ), patch(
        "services.external_sources.thesportsdb_client.search_teams",
        new=AsyncMock(return_value=candidates),
    ):
        tid = await _resolve_national_team_id("Argentina", client=None)
    assert tid == "134509"


@pytest.mark.asyncio
async def test_resolve_national_team_id_returns_none_when_no_soccer_candidate():
    with patch(
        "services.external_sources.thesportsdb_client.is_enabled",
        return_value=True,
    ), patch(
        "services.external_sources.thesportsdb_client.search_teams",
        new=AsyncMock(return_value=[
            {"idTeam": "999", "strTeam": "x", "strSport": "Rugby"},
        ]),
    ):
        tid = await _resolve_national_team_id("Whatever", client=None)
    assert tid is None


@pytest.mark.asyncio
async def test_seed_one_national_team_skips_when_fresh():
    """Idempotency guard: if a doc was seeded < skip_if_recent_hours
    ago, skip without HTTP."""
    db = MagicMock()
    db.__getitem__.return_value.find_one = AsyncMock(return_value={
        "team_norm":  "argentina",
        "seeded_at":  datetime.now(timezone.utc).isoformat(),
    })
    audit = await seed_one_national_team(
        "Argentina", db=db, client=None, skip_if_recent_hours=12.0,
    )
    assert audit["status"] == "skipped_fresh"
    assert "SKIPPED_FRESH_SEED" in audit["reason_codes"]


@pytest.mark.asyncio
async def test_seed_one_national_team_resolves_then_persists():
    db = MagicMock()
    coll = db.__getitem__.return_value
    coll.find_one = AsyncMock(return_value=None)
    coll.update_one = AsyncMock(return_value=None)
    fake_events = [_normalize_event_to_recent_fixture(_TSDB_RAW_EVENT_ARG_VS_ITALY)]
    with patch(
        "services.football_national_team_seed._resolve_national_team_id",
        new=AsyncMock(return_value="134509"),
    ), patch(
        "services.external_sources.thesportsdb_client.fetch_last_events_by_team",
        new=AsyncMock(return_value=fake_events),
    ):
        audit = await seed_one_national_team("Argentina", db=db, client=None)
    assert audit["status"] == "ok"
    assert audit["matches_count"] == 1
    assert "SEED_PERSISTED" in audit["reason_codes"]
    coll.update_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_seed_one_national_team_team_not_found_fail_soft():
    db = MagicMock()
    db.__getitem__.return_value.find_one = AsyncMock(return_value=None)
    with patch(
        "services.football_national_team_seed._resolve_national_team_id",
        new=AsyncMock(return_value=None),
    ):
        audit = await seed_one_national_team("ZZZ", db=db, client=None)
    assert audit["status"] == "error"
    assert "THESPORTSDB_TEAM_NOT_FOUND" in audit["reason_codes"]


@pytest.mark.asyncio
async def test_seed_batch_runs_concurrently_and_aggregates():
    db = MagicMock()
    db.__getitem__.return_value.find_one   = AsyncMock(return_value=None)
    db.__getitem__.return_value.update_one = AsyncMock(return_value=None)
    fake_events = [_normalize_event_to_recent_fixture(_TSDB_RAW_EVENT_ARG_VS_ITALY)]
    with patch(
        "services.football_national_team_seed._resolve_national_team_id",
        new=AsyncMock(return_value="134509"),
    ), patch(
        "services.external_sources.thesportsdb_client.fetch_last_events_by_team",
        new=AsyncMock(return_value=fake_events),
    ):
        out = await seed_national_team_recent_form(
            db=db, client=None, teams=["Argentina", "Uruguay", "Egypt"],
            concurrency=2,
        )
    assert out["seeded"] == 3
    assert out["errors"] == 0
    assert out["schema_version"] == SEED_SCHEMA_VERSION
    assert len(out["per_team"]) == 3


@pytest.mark.asyncio
async def test_seed_batch_fail_soft_on_per_team_exception():
    db = MagicMock()
    db.__getitem__.return_value.find_one = AsyncMock(return_value=None)

    async def _flaky_resolver(name, *, client):
        if name == "BadName":
            raise RuntimeError("kaboom")
        return "134509"

    with patch(
        "services.football_national_team_seed._resolve_national_team_id",
        new=AsyncMock(side_effect=_flaky_resolver),
    ), patch(
        "services.external_sources.thesportsdb_client.fetch_last_events_by_team",
        new=AsyncMock(return_value=[]),  # no events → error path
    ):
        out = await seed_national_team_recent_form(
            db=db, client=None, teams=["BadName", "GoodName"], concurrency=2,
        )
    # Both end up in 'error' (one because resolver raised, one because no events).
    assert out["errors"] >= 1
    # BadName must surface an EXCEPTION reason code.
    bad = next(r for r in out["per_team"] if r["team"] == "BadName")
    assert any(rc.startswith("EXCEPTION") for rc in bad.get("reason_codes", []))


def test_top_national_teams_list_includes_focus_matches():
    """The seed list MUST cover the user's focus matches so re-runs
    of the diagnostics script will find data for them."""
    upper = {t.lower() for t in TOP_NATIONAL_TEAMS}
    for team in ("Argentina", "Austria", "Uruguay", "Cape Verde",
                  "New Zealand", "Egypt"):
        assert team.lower() in upper, f"missing {team!r} from seed list"
