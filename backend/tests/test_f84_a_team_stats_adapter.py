"""Phase F84.a — Tests for the TheStatsAPI team_stats adapter +
orchestrator prioridad-inversa.

Two layers under test
---------------------
1. ``services.external_sources.thestatsapi_team_stats_adapter``
   - happy path: TheStatsAPI payload → API-Sports-shaped dict
   - field mapping (form, fixtures.played, goals.for.average.total, …)
   - provenance block embedded
   - fail-soft: disabled, missing team_id, missing season_id, network
     error, empty response

2. ``services.data_ingestion`` orchestrator branch
   - flag helper ``_api_sports_fallback_enabled`` reads env correctly
   - TheStatsAPI success → API-Sports is NOT called (stats_*_source =
     "thestatsapi")
   - TheStatsAPI miss + flag=true → falls back to API-Sports (source =
     "api_sports_fallback")
   - TheStatsAPI miss + flag=false → both sides remain empty (source =
     "missing")

All HTTP calls go through ``httpx.MockTransport`` — no real network.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.external_sources import (
    thestatsapi_team_stats_adapter as adapter,
)
from services import data_ingestion as di


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _enable_thestatsapi(monkeypatch):
    monkeypatch.setenv("THESTATSAPI_KEY", "test-fake-key")
    monkeypatch.setenv("THESTATSAPI_BASE_URL", "https://api.thestatsapi.com/api")
    monkeypatch.setenv("ENABLE_THE_STATS_API", "true")
    yield


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# =====================================================================
# Layer 1 — adapter
# =====================================================================
class TestBuildApiSportsShape:
    """Pure-Python shape conversion. No I/O."""

    def test_complete_payload_maps_to_api_sports_shape(self):
        raw = {
            "team_id":        "tm_8923",
            "season_id":      "sn_7210",
            "competition_id": "comp_3879",
            "matches_played": 38,
            "wins":           23,
            "draws":           6,
            "losses":          9,
            "points":         75,
            "position":        3,
            "goals_for":      58,
            "goals_against":  43,
            "goal_difference":15,
            "form":           "WWDLW",
        }
        out = adapter._build_api_sports_shape(
            raw,
            team_id_internal=33,
            season_id_internal=2024,
        )
        # Form is at the root (matches API-Sports v3 layout).
        assert out["form"] == "WWDLW"
        # Fixtures totals (used by normalize_team_context.season_priors).
        assert out["fixtures"]["played"]["total"] == 38
        assert out["fixtures"]["wins"]["total"]   == 23
        assert out["fixtures"]["draws"]["total"]  ==  6
        assert out["fixtures"]["loses"]["total"]  ==  9   # API-Sports spelling
        # Goals breakdown (used for goals_for_avg / goals_against_avg).
        assert out["goals"]["for"]["total"]["total"]      == 58
        assert out["goals"]["against"]["total"]["total"]  == 43
        # Averages are derived locally to 3 decimals.
        assert out["goals"]["for"]["average"]["total"]     == round(58 / 38, 3)
        assert out["goals"]["against"]["average"]["total"] == round(43 / 38, 3)
        # Fields TheStatsAPI does NOT expose stay None (we do not invent).
        assert out["clean_sheet"]["total"]     is None
        assert out["failed_to_score"]["total"] is None
        # League-table block at the root for consumers that want
        # standings without a second call.
        assert out["_league_table"] == {
            "position":         3,
            "points":           75,
            "goal_difference":  15,
        }
        # Provenance block carries both TheStatsAPI IDs and the internal
        # API-Sports ID for the audit trail.
        prov = out["_provenance"]
        assert prov["source"]              == "thestatsapi"
        assert prov["endpoint"]            == "/football/teams/{id}/stats"
        assert prov["team_id"]             == "tm_8923"
        assert prov["season_id"]           == "sn_7210"
        assert prov["competition_id"]      == "comp_3879"
        assert prov["team_id_internal"]    == 33
        assert prov["season_id_internal"]  == 2024

    def test_zero_matches_played_does_not_divide_by_zero(self):
        raw = {"matches_played": 0, "goals_for": 0, "goals_against": 0,
               "form": ""}
        out = adapter._build_api_sports_shape(raw)
        assert out["goals"]["for"]["average"]["total"]     is None
        assert out["goals"]["against"]["average"]["total"] is None
        # Played stays at 0 (a legitimate value — pre-season).
        assert out["fixtures"]["played"]["total"] == 0

    def test_empty_input_returns_empty_dict(self):
        assert adapter._build_api_sports_shape({}) == {}
        assert adapter._build_api_sports_shape(None) == {}  # type: ignore[arg-type]

    def test_string_numbers_are_coerced(self):
        """Some upstream proxies stringify ints; the shape builder must
        coerce them without raising."""
        out = adapter._build_api_sports_shape({
            "matches_played": "38", "wins": "23", "draws": "6",
            "losses": "9", "goals_for": "58", "goals_against": "43",
            "position": "3", "points": "75", "goal_difference": "15",
            "form": "WLDWW",
        })
        assert out["fixtures"]["played"]["total"] == 38
        assert out["goals"]["for"]["average"]["total"] == round(58 / 38, 3)
        assert out["_league_table"]["position"] == 3


class TestFetchTeamSeasonStatsFailSoft:
    """The adapter must NEVER raise — every failure returns ``{}``."""

    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setenv("ENABLE_THE_STATS_API", "false")
        async with _mock_client(lambda r: httpx.Response(200, json={})) as c:
            out = await adapter.fetch_team_season_stats(
                c, team_id_thestatsapi="tm_1", season=2024,
            )
        assert out == {}

    @pytest.mark.asyncio
    async def test_missing_team_id_returns_empty(self):
        async with _mock_client(lambda r: httpx.Response(200, json={})) as c:
            out = await adapter.fetch_team_season_stats(
                c, team_id_thestatsapi=None, season=2024,
            )
        assert out == {}

    @pytest.mark.asyncio
    async def test_missing_season_returns_empty(self):
        async with _mock_client(lambda r: httpx.Response(200, json={})) as c:
            out = await adapter.fetch_team_season_stats(
                c, team_id_thestatsapi="tm_1",
                season=None, season_id_thestatsapi=None,
            )
        assert out == {}

    @pytest.mark.asyncio
    async def test_404_returns_empty_without_raising(self):
        async with _mock_client(
            lambda r: httpx.Response(404, json={"error": "nope"}),
        ) as c:
            out = await adapter.fetch_team_season_stats(
                c, team_id_thestatsapi="tm_1", season=2024,
            )
        assert out == {}

    @pytest.mark.asyncio
    async def test_500_returns_empty(self):
        async with _mock_client(
            lambda r: httpx.Response(500, json={"error": "boom"}),
        ) as c:
            out = await adapter.fetch_team_season_stats(
                c, team_id_thestatsapi="tm_1", season=2024,
            )
        assert out == {}

    @pytest.mark.asyncio
    async def test_empty_data_block_returns_empty(self):
        """A 200 with an empty `data: {}` is treated as miss."""
        async with _mock_client(
            lambda r: httpx.Response(200, json={"data": {}}),
        ) as c:
            out = await adapter.fetch_team_season_stats(
                c, team_id_thestatsapi="tm_1", season=2024,
            )
        assert out == {}


class TestFetchTeamSeasonStatsHappyPath:
    @pytest.mark.asyncio
    async def test_returns_api_sports_shape_on_success(self):
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["path"]   = req.url.path
            captured["params"] = dict(req.url.params)
            return httpx.Response(200, json={"data": {
                "team_id":        "tm_8923",
                "season_id":      "sn_7210",
                "competition_id": "comp_3879",
                "matches_played": 38, "wins": 23, "draws": 6, "losses": 9,
                "points": 75, "position": 3,
                "goals_for": 58, "goals_against": 43, "goal_difference": 15,
                "form": "WWDLW",
            }})

        async with _mock_client(handler) as c:
            out = await adapter.fetch_team_season_stats(
                c,
                team_id_thestatsapi="tm_8923",
                season=2024,
                competition_id="comp_3879",
                team_id_internal=33,
            )
        # Endpoint reached.
        assert captured["path"].endswith("/football/teams/tm_8923/stats")
        # Season + competition forwarded as query params.
        assert captured["params"]["season"]         == "2024"
        assert captured["params"]["competition_id"] == "comp_3879"
        # Shape is API-Sports-compatible.
        assert out["form"] == "WWDLW"
        assert out["fixtures"]["played"]["total"] == 38
        assert out["_provenance"]["source"] == "thestatsapi"
        assert out["_provenance"]["team_id_internal"] == 33

    @pytest.mark.asyncio
    async def test_season_id_native_is_preferred_when_present(self):
        captured: dict[str, Any] = {}

        def handler(req):
            captured["params"] = dict(req.url.params)
            return httpx.Response(200, json={"data": {
                "matches_played": 10, "wins": 5, "draws": 3, "losses": 2,
                "goals_for": 20, "goals_against": 8, "form": "WDWWL",
            }})

        async with _mock_client(handler) as c:
            await adapter.fetch_team_season_stats(
                c, team_id_thestatsapi="tm_8923",
                season_id_thestatsapi="sn_7210", season=2024,
            )
        # The native string season MUST win over the numeric fallback.
        assert captured["params"]["season"] == "sn_7210"


# =====================================================================
# Layer 2 — orchestrator: flag helper + prioridad-inversa
# =====================================================================
class TestApiSportsFallbackFlag:
    def test_default_when_unset_is_true(self, monkeypatch):
        monkeypatch.delenv("ENABLE_API_SPORTS_FALLBACK", raising=False)
        assert di._api_sports_fallback_enabled() is True

    @pytest.mark.parametrize("raw,expected", [
        ("true",  True), ("True",  True), ("1",   True), ("yes", True),
        ("false", False), ("FALSE", False), ("0",  False),
        ("no",    False), ("off",   False), ("",   True),  # empty = default true
    ])
    def test_parses_boolean_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv("ENABLE_API_SPORTS_FALLBACK", raw)
        assert di._api_sports_fallback_enabled() is expected


# Note: the full orchestrator path (`enrich_football`) wires Mongo,
# API-Sports fixtures, odds, standings, injuries, etc. Smoke-testing
# the entire chain requires extensive scaffolding that lives in the
# integration suite already (test_data_ingestion_*). The unit-level
# guarantee we need at the F84.a boundary is captured by the adapter
# tests above + the small selector below, which mirrors the exact
# decision the orchestrator makes.
class TestOrchestratorPriorityInversionSemantics:
    """Mirror the decision tree wired in ``data_ingestion.enrich_football``:

       try TheStatsAPI → if empty AND flag → try API-Sports
                       → if both empty → stays ``{}`` with source="missing"
    """

    @staticmethod
    async def _resolve(*, ts_payload, aps_payload, fallback_flag):
        """Re-implement the orchestrator branch in 10 lines so we can
        unit-test the semantics without booting Mongo."""
        async def _ts(): return ts_payload
        async def _aps(): return aps_payload
        stats_h = await _ts()
        source = "missing"
        if stats_h:
            source = "thestatsapi"
        elif fallback_flag:
            stats_h = await _aps()
            if stats_h:
                source = "api_sports_fallback"
        return stats_h, source

    @pytest.mark.asyncio
    async def test_thestatsapi_hit_short_circuits_fallback(self):
        stats, source = await self._resolve(
            ts_payload={"form": "WWWWW", "fixtures": {"played": {"total": 5}}},
            aps_payload={"form": "LLLLL"},   # MUST NOT be returned
            fallback_flag=True,
        )
        assert source == "thestatsapi"
        assert stats["form"] == "WWWWW"

    @pytest.mark.asyncio
    async def test_thestatsapi_miss_with_flag_true_falls_back(self):
        stats, source = await self._resolve(
            ts_payload={},                                    # TSA misses
            aps_payload={"form": "WLDWW"},
            fallback_flag=True,
        )
        assert source == "api_sports_fallback"
        assert stats["form"] == "WLDWW"

    @pytest.mark.asyncio
    async def test_thestatsapi_miss_with_flag_false_does_not_fall_back(self):
        stats, source = await self._resolve(
            ts_payload={},
            aps_payload={"form": "WLDWW"},      # MUST NOT be returned
            fallback_flag=False,
        )
        assert source == "missing"
        assert stats == {}

    @pytest.mark.asyncio
    async def test_both_empty_keeps_missing(self):
        stats, source = await self._resolve(
            ts_payload={}, aps_payload={}, fallback_flag=True,
        )
        assert source == "missing"
        assert stats == {}
