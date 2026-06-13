"""Phase F84.b — Tests for the TheStatsAPI h2h adapter +
orchestrator prioridad-inversa.

Layers under test
-----------------
1. ``thestatsapi_h2h_adapter._parse_iso_to_epoch``
2. ``thestatsapi_h2h_adapter._shape_fixture_as_api_sports``
3. ``thestatsapi_h2h_adapter._list_finished_matches_for_team`` (single page)
4. ``thestatsapi_h2h_adapter.fetch_head_to_head`` (list+filter+pagination+cache)
5. Orchestrator branch semantics (mirrors the decision tree in
   ``data_ingestion.enrich_football``)

All HTTP calls go through ``httpx.MockTransport``.
The Mongo cache (``_af._cache_get`` / ``_af._cache_set``) is bypassed by
passing ``db=None``.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from services.external_sources import (
    thestatsapi_h2h_adapter as adapter,
)


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


def _ts_match(
    *, mid="mt_1", utc_date="2024-01-15T15:00:00.000Z",
    home_id="tm_A", home_name="Team A",
    away_id="tm_B", away_name="Team B",
    score_h=2, score_a=1, competition_id="comp_1", status="finished",
) -> dict:
    """Build a TheStatsAPI match payload for fixtures."""
    return {
        "id":             mid,
        "competition_id": competition_id,
        "utc_date":       utc_date,
        "status":         status,
        "home_team":      {"id": home_id, "name": home_name},
        "away_team":      {"id": away_id, "name": away_name},
        "score":          {"home": score_h, "away": score_a},
    }


# =====================================================================
# Layer 1 — _parse_iso_to_epoch
# =====================================================================
class TestParseIsoToEpoch:
    def test_z_suffix_is_handled(self):
        # 2024-01-15T15:00:00Z == 1705330800 (UTC)
        assert adapter._parse_iso_to_epoch("2024-01-15T15:00:00.000Z") == 1705330800

    def test_offset_suffix_is_handled(self):
        assert adapter._parse_iso_to_epoch("2024-01-15T15:00:00+00:00") == 1705330800

    def test_naive_iso_assumes_utc(self):
        assert adapter._parse_iso_to_epoch("2024-01-15T15:00:00") == 1705330800

    @pytest.mark.parametrize("bad", [None, "", "not a date", 42, [], {}])
    def test_invalid_returns_none(self, bad):
        assert adapter._parse_iso_to_epoch(bad) is None


# =====================================================================
# Layer 2 — _shape_fixture_as_api_sports
# =====================================================================
class TestShapeFixtureAsApiSports:
    def test_emits_api_sports_v3_compatible_shape(self):
        raw = _ts_match(mid="mt_42")
        out = adapter._shape_fixture_as_api_sports(
            raw, home_team_id_internal=33, away_team_id_internal=42,
        )
        # `fixture` block
        assert out["fixture"]["id"] == "mt_42"
        assert out["fixture"]["timestamp"] == 1705330800
        assert out["fixture"]["date"] == "2024-01-15T15:00:00.000Z"
        # `teams` block
        assert out["teams"]["home"]["id"]   == "tm_A"
        assert out["teams"]["home"]["name"] == "Team A"
        assert out["teams"]["away"]["id"]   == "tm_B"
        assert out["teams"]["away"]["name"] == "Team B"
        # `goals` block
        assert out["goals"]["home"] == 2
        assert out["goals"]["away"] == 1
        # `league` block (id forwarded, name unknown at this level)
        assert out["league"]["id"]   == "comp_1"
        assert out["league"]["name"] is None
        # Provenance carries internal IDs for audit
        assert out["_provenance"]["source"] == "thestatsapi"
        assert out["_provenance"]["home_team_id_internal"] == 33
        assert out["_provenance"]["away_team_id_internal"] == 42

    def test_invalid_input_returns_none(self):
        assert adapter._shape_fixture_as_api_sports(None) is None  # type: ignore[arg-type]
        assert adapter._shape_fixture_as_api_sports("bad") is None  # type: ignore[arg-type]

    def test_missing_score_yields_none_goals(self):
        raw = _ts_match()
        raw["score"] = {}
        out = adapter._shape_fixture_as_api_sports(raw)
        assert out["goals"]["home"] is None
        assert out["goals"]["away"] is None


# =====================================================================
# Layer 3 — _list_finished_matches_for_team
# =====================================================================
class TestListFinishedMatches:
    @pytest.mark.asyncio
    async def test_forwards_team_id_status_per_page_page(self):
        captured: dict[str, Any] = {}

        def handler(req):
            captured["path"]   = req.url.path
            captured["params"] = dict(req.url.params)
            return httpx.Response(200, json={"data": [_ts_match()]})

        async with _mock_client(handler) as c:
            out = await adapter._list_finished_matches_for_team(
                c, "tm_A", page=2, per_page=50,
            )
        assert captured["path"].endswith("/football/matches")
        assert captured["params"]["team_id"]  == "tm_A"
        assert captured["params"]["status"]   == "finished"
        assert captured["params"]["per_page"] == "50"
        assert captured["params"]["page"]     == "2"
        assert len(out) == 1

    @pytest.mark.asyncio
    async def test_network_error_returns_empty_list(self):
        async with _mock_client(
            lambda r: httpx.Response(500, json={"error": "boom"}),
        ) as c:
            out = await adapter._list_finished_matches_for_team(c, "tm_A")
        assert out == []

    @pytest.mark.asyncio
    async def test_extracts_from_matches_key_too(self):
        async with _mock_client(
            lambda r: httpx.Response(200, json={"matches": [_ts_match()]}),
        ) as c:
            out = await adapter._list_finished_matches_for_team(c, "tm_A")
        assert len(out) == 1


# =====================================================================
# Layer 4 — fetch_head_to_head (list + filter + paginate + cache)
# =====================================================================
class TestFetchHeadToHeadFailSoft:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setenv("ENABLE_THE_STATS_API", "false")
        async with _mock_client(lambda r: httpx.Response(200, json={})) as c:
            out = await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi="tm_A",
                away_team_id_thestatsapi="tm_B", db=None,
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_missing_team_ids_return_empty(self):
        async with _mock_client(lambda r: httpx.Response(200, json={})) as c:
            assert await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi=None,
                away_team_id_thestatsapi="tm_B", db=None,
            ) == []
            assert await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi="tm_A",
                away_team_id_thestatsapi=None, db=None,
            ) == []

    @pytest.mark.asyncio
    async def test_no_matches_at_all_returns_empty(self):
        async with _mock_client(
            lambda r: httpx.Response(200, json={"data": []}),
        ) as c:
            out = await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi="tm_A",
                away_team_id_thestatsapi="tm_B", db=None,
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_no_matches_against_target_team_returns_empty(self):
        """Home team has finished matches but NONE vs tm_B."""
        page = [
            _ts_match(mid="mt_1", away_id="tm_X", away_name="X"),
            _ts_match(mid="mt_2", away_id="tm_Y", away_name="Y"),
            _ts_match(mid="mt_3", away_id="tm_Z", away_name="Z"),
        ]
        async with _mock_client(
            lambda r: httpx.Response(200, json={"data": page}),
        ) as c:
            out = await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi="tm_A",
                away_team_id_thestatsapi="tm_B", db=None,
            )
        assert out == []


class TestFetchHeadToHeadHappyPath:
    @pytest.mark.asyncio
    async def test_filters_only_matches_vs_target_team(self):
        page = [
            _ts_match(mid="mt_vs_B_1", away_id="tm_B",
                       utc_date="2024-01-01T10:00:00Z"),
            _ts_match(mid="mt_vs_X",   away_id="tm_X",
                       utc_date="2024-01-15T10:00:00Z"),
            _ts_match(mid="mt_vs_B_2", away_id="tm_B",
                       utc_date="2024-02-10T10:00:00Z"),
            # Reverse-side fixture: tm_A was AWAY, tm_B was HOME — still h2h.
            _ts_match(mid="mt_vs_B_3",
                       home_id="tm_B", home_name="B",
                       away_id="tm_A", away_name="A",
                       utc_date="2024-03-01T10:00:00Z"),
        ]
        async with _mock_client(
            lambda r: httpx.Response(200, json={"data": page}),
        ) as c:
            out = await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi="tm_A",
                away_team_id_thestatsapi="tm_B", db=None,
            )
        # All 3 vs-B matches kept, ordered by epoch desc.
        ids = [(x["fixture"] or {}).get("id") for x in out]
        assert ids == ["mt_vs_B_3", "mt_vs_B_2", "mt_vs_B_1"]
        # Length respects default limit (5).
        assert len(out) == 3

    @pytest.mark.asyncio
    async def test_limit_truncates_after_sort(self):
        page = [
            _ts_match(mid=f"mt_{i}", away_id="tm_B",
                       utc_date=f"2024-01-{i:02d}T10:00:00Z")
            for i in range(1, 11)
        ]
        async with _mock_client(
            lambda r: httpx.Response(200, json={"data": page}),
        ) as c:
            out = await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi="tm_A",
                away_team_id_thestatsapi="tm_B",
                limit=3, db=None,
            )
        assert len(out) == 3
        # Newest first.
        assert out[0]["fixture"]["id"] == "mt_10"
        assert out[1]["fixture"]["id"] == "mt_9"
        assert out[2]["fixture"]["id"] == "mt_8"

    @pytest.mark.asyncio
    async def test_pagination_stops_when_short_page_received(self):
        """A page shorter than PAGE_SIZE is the last page — no further
        requests should be made."""
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            # First page: 1 fixture (less than PAGE_SIZE=100) → stop.
            if calls["n"] == 1:
                return httpx.Response(200, json={"data": [
                    _ts_match(mid="mt_only", away_id="tm_B"),
                ]})
            # If we ever get here, the test fails.
            return httpx.Response(500, json={"error": "should not happen"})

        async with _mock_client(handler) as c:
            out = await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi="tm_A",
                away_team_id_thestatsapi="tm_B", db=None,
            )
        assert calls["n"] == 1
        assert len(out) == 1

    @pytest.mark.asyncio
    async def test_pagination_respects_max_pages_cap(self):
        """If every page is full (PAGE_SIZE matches) but none match the
        opponent, pagination must STOP at MAX_PAGES requests."""
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            # Always return PAGE_SIZE fixtures vs a non-target team, so
            # the loop keeps requesting until MAX_PAGES.
            return httpx.Response(200, json={"data": [
                _ts_match(mid=f"mt_p{calls['n']}_{i}", away_id="tm_X")
                for i in range(adapter.PAGE_SIZE)
            ]})

        async with _mock_client(handler) as c:
            await adapter.fetch_head_to_head(
                c, home_team_id_thestatsapi="tm_A",
                away_team_id_thestatsapi="tm_B", db=None,
            )
        assert calls["n"] == adapter.MAX_PAGES


# =====================================================================
# Layer 5 — Orchestrator semantics (mirror, no Mongo)
# =====================================================================
class TestOrchestratorH2HPriorityInversion:
    @staticmethod
    async def _resolve(*, ts_payload, aps_payload, fallback_flag):
        async def _ts():  return ts_payload
        async def _aps(): return aps_payload
        h2h = await _ts()
        source = "missing"
        if h2h:
            source = "thestatsapi"
        elif fallback_flag:
            h2h = await _aps()
            if h2h:
                source = "api_sports_fallback"
        return h2h, source

    @pytest.mark.asyncio
    async def test_thestatsapi_hit_short_circuits_fallback(self):
        h2h, source = await self._resolve(
            ts_payload=[{"fixture": {"id": "mt_1"}}],
            aps_payload=[{"fixture": {"id": "should_not_appear"}}],
            fallback_flag=True,
        )
        assert source == "thestatsapi"
        assert h2h[0]["fixture"]["id"] == "mt_1"

    @pytest.mark.asyncio
    async def test_thestatsapi_miss_with_flag_true_falls_back(self):
        h2h, source = await self._resolve(
            ts_payload=[],
            aps_payload=[{"fixture": {"id": "aps_1"}}],
            fallback_flag=True,
        )
        assert source == "api_sports_fallback"
        assert h2h[0]["fixture"]["id"] == "aps_1"

    @pytest.mark.asyncio
    async def test_thestatsapi_miss_with_flag_false_does_not_fall_back(self):
        h2h, source = await self._resolve(
            ts_payload=[],
            aps_payload=[{"fixture": {"id": "aps_1"}}],
            fallback_flag=False,
        )
        assert source == "missing"
        assert h2h == []
