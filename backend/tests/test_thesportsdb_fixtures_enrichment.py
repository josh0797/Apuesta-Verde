"""F96.3 — Tests para fixtures fallback + enrichment de TheSportsDB.

Cobertura:
  - `fetch_upcoming_events_by_date`: success, no key, empty payload,
    invalid date, network error.
  - `fetch_next_events_by_league`: success, empty events.
  - `enrich_team_badge`: prefer soccer match, fallback to first, not found.
  - `search_leagues`: filter by country/sport.
  - `lookup_league`: single league return.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.external_sources import thesportsdb_client as tsdb


SAMPLE_EVENT = {
    "idEvent":          "9999",
    "idLeague":         "4328",
    "strLeague":        "Premier League",
    "idHomeTeam":       "133602",
    "idAwayTeam":       "133616",
    "strHomeTeam":      "Liverpool",
    "strAwayTeam":      "Arsenal",
    "strHomeTeamBadge": "https://r2.example/lfc.png",
    "strAwayTeamBadge": "https://r2.example/afc.png",
    "dateEvent":        "2026-06-22",
    "strTime":          "16:30:00",
    "strSeason":        "2025-2026",
    "strStatus":        "NS",
}


# =====================================================================
# fetch_upcoming_events_by_date
# =====================================================================
class TestFetchUpcomingByDate:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(client, path, *, params=None, timeout=8.0):
            assert "eventsday.php" in path
            assert params == {"d": "2026-06-22", "s": "Soccer"}
            return {"events": [SAMPLE_EVENT]}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            env = await tsdb.fetch_upcoming_events_by_date("2026-06-22")

        assert env["available"] is True
        assert env["endpoint"] == "eventsday"
        assert env["count"] == 1
        item = env["items"][0]
        assert item["event_id"] == "9999"
        assert item["home_team"]["name"] == "Liverpool"
        assert item["away_team"]["name"] == "Arsenal"
        assert item["home_team"]["badge"].startswith("https://")

    @pytest.mark.asyncio
    async def test_disabled_when_no_key(self, monkeypatch):
        monkeypatch.delenv("THESPORTSDB_KEY", raising=False)
        env = await tsdb.fetch_upcoming_events_by_date("2026-06-22")
        assert env["available"] is False
        assert "THESPORTSDB_DISABLED" in env["reason_codes"]

    @pytest.mark.asyncio
    async def test_empty_payload_returns_unavailable(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(*a, **kw):
            return {}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            env = await tsdb.fetch_upcoming_events_by_date("2026-06-22")
        assert env["available"] is False
        assert "THESPORTSDB_EVENTSDAY_EMPTY" in env["reason_codes"]

    @pytest.mark.asyncio
    async def test_invalid_date_short_circuits(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")
        env = await tsdb.fetch_upcoming_events_by_date("")
        assert env["available"] is False
        assert "THESPORTSDB_DATE_MISSING" in env["reason_codes"]

    @pytest.mark.asyncio
    async def test_network_error_fail_soft(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def boom(*a, **kw):
            raise RuntimeError("dns down")

        with patch.object(tsdb, "_request", side_effect=boom):
            env = await tsdb.fetch_upcoming_events_by_date("2026-06-22")
        assert env["available"] is False
        assert "THESPORTSDB_EVENTSDAY_FAILED" in env["reason_codes"]


# =====================================================================
# fetch_next_events_by_league
# =====================================================================
class TestFetchNextEventsByLeague:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(client, path, *, params=None, timeout=8.0):
            assert "eventsnextleague.php" in path
            assert params == {"id": "4328"}
            return {"events": [SAMPLE_EVENT, SAMPLE_EVENT]}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            env = await tsdb.fetch_next_events_by_league(4328)
        assert env["available"] is True
        assert env["count"] == 2
        assert env["league_id"] == "4328"

    @pytest.mark.asyncio
    async def test_no_events_in_payload(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(*a, **kw):
            return {"events": None}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            env = await tsdb.fetch_next_events_by_league(4328)
        assert env["available"] is False
        assert "THESPORTSDB_EVENTSNEXTLEAGUE_NO_EVENTS" in env["reason_codes"]

    @pytest.mark.asyncio
    async def test_missing_league_id(self):
        env = await tsdb.fetch_next_events_by_league("")
        assert env["available"] is False
        assert "THESPORTSDB_LEAGUE_ID_MISSING" in env["reason_codes"]


# =====================================================================
# enrich_team_badge
# =====================================================================
class TestEnrichTeamBadge:
    @pytest.mark.asyncio
    async def test_prefers_soccer_team(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_search_teams(name, *, client=None, timeout=8.0):
            return [
                {"strSport": "Basketball", "idTeam": "1", "strBadge": "bball"},
                {"strSport": "Soccer",     "idTeam": "133602",
                 "strBadge": "https://r2/lfc.png",
                 "strCountry": "England", "strLeague": "Premier League"},
            ]

        with patch.object(tsdb, "search_teams", side_effect=fake_search_teams):
            res = await tsdb.enrich_team_badge("Liverpool")
        assert res["available"] is True
        assert res["team_id"] == "133602"
        assert res["badge"] == "https://r2/lfc.png"
        assert res["country"] == "England"
        assert res["league"] == "Premier League"

    @pytest.mark.asyncio
    async def test_not_found(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def empty(*a, **kw):
            return []

        with patch.object(tsdb, "search_teams", side_effect=empty):
            res = await tsdb.enrich_team_badge("Unknown FC")
        assert res["available"] is False
        assert "THESPORTSDB_TEAM_NOT_FOUND" in res["reason_codes"]

    @pytest.mark.asyncio
    async def test_falls_back_to_first_when_no_soccer(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_search(*a, **kw):
            return [
                {"strSport": "Basketball", "idTeam": "B1",
                 "strBadge": "bball.png", "strCountry": "USA"},
            ]

        with patch.object(tsdb, "search_teams", side_effect=fake_search):
            res = await tsdb.enrich_team_badge("Lakers")
        assert res["available"] is True
        assert res["team_id"] == "B1"


# =====================================================================
# search_leagues / lookup_league
# =====================================================================
class TestLeagueLookups:
    @pytest.mark.asyncio
    async def test_search_leagues_returns_list(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(client, path, *, params=None, timeout=8.0):
            assert "search_all_leagues.php" in path
            return {"countrys": [
                {"idLeague": "4328", "strLeague": "Premier League"},
                {"idLeague": "4335", "strLeague": "Spanish La Liga"},
            ]}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            res = await tsdb.search_leagues(country="England", sport="Soccer")
        assert isinstance(res, list)
        assert len(res) == 2

    @pytest.mark.asyncio
    async def test_search_leagues_disabled(self, monkeypatch):
        monkeypatch.delenv("THESPORTSDB_KEY", raising=False)
        res = await tsdb.search_leagues()
        assert res == []

    @pytest.mark.asyncio
    async def test_lookup_league_returns_single_dict(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(client, path, *, params=None, timeout=8.0):
            assert "lookupleague.php" in path
            assert params == {"id": "4328"}
            return {"leagues": [{"idLeague": "4328",
                                  "strLeague": "Premier League",
                                  "strCountry": "England"}]}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            res = await tsdb.lookup_league("4328")
        assert isinstance(res, dict)
        assert res["idLeague"] == "4328"

    @pytest.mark.asyncio
    async def test_lookup_league_empty(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(*a, **kw):
            return {}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            res = await tsdb.lookup_league("4328")
        assert res == {}


# =====================================================================
# Públicos
# =====================================================================
class TestPublicSymbolsF96_3:
    def test_all_exported(self):
        for name in (
            "fetch_upcoming_events_by_date",
            "fetch_next_events_by_league",
            "enrich_team_badge",
            "search_leagues",
            "lookup_league",
        ):
            assert hasattr(tsdb, name)
            assert callable(getattr(tsdb, name))
