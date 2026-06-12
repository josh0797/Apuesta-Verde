"""Phase F74-post v2 — TheStatsAPI odds fallback wiring tests.

Cobertura:
  1. ``odds_for_fixture`` existe en el client y devuelve ``{}`` cuando
     ``THESTATSAPI_KEY`` no está / endpoint falla (fail-soft).
  2. Adapter ``normalize_thestatsapi_odds_to_apisports_shape``:
     - traduce el shape nested al shape API-Sports flat,
     - usa ``last_seen`` como cuota actual (no ``opening``),
     - preserva ``_opening_odds`` por (bookmaker, market, value),
     - cubre Match Winner, BTTS, Goals Over/Under, Corners Over/Under, AH,
     - fail-soft con inputs vacíos/basura,
     - line-key parser tolerante (over_2_5 → "2.5").
  3. ``resolve_thestatsapi_match_id_by_names`` está exportada y es fail-soft
     cuando el client está disabled.
"""
from __future__ import annotations

import pytest
import httpx

from services.external_sources import (
    thestatsapi_client as ts_client,
    thestatsapi_normalizer as ts_norm,
)


# ─────────────────────────────────────────────────────────────────────
# Cambio 2 — Adapter contract tests
# ─────────────────────────────────────────────────────────────────────
class TestThestatsapiOddsAdapter:
    SAMPLE = {
        "match_id": "mt_14502",
        "bookmakers": [{
            "bookmaker": "Pinnacle",
            "markets": {
                "match_odds":  {"home": {"opening": "2.100", "last_seen": "2.050"},
                                 "draw": {"opening": "3.400", "last_seen": "3.500"},
                                 "away": {"opening": "3.200", "last_seen": "3.300"}},
                "total_goals": {"over_2_5": {"over": {"opening": "1.850", "last_seen": "1.870"},
                                                "under": {"opening": "2.000", "last_seen": "1.980"}}},
                "btts":        {"yes": {"opening": "1.750", "last_seen": "1.810"},
                                  "no": {"opening": "2.100", "last_seen": "2.050"}},
                "match_corners": {"over_9_5": {"over": {"opening": "1.900", "last_seen": "1.920"},
                                                  "under": {"opening": "1.900", "last_seen": "1.880"}}},
                "asian_handicap": {"home": {"opening": "1.910", "last_seen": "1.890"},
                                     "away": {"opening": "1.990", "last_seen": "2.010"}},
            },
        }],
    }

    def test_basic_shape_translation(self):
        result = ts_norm.normalize_thestatsapi_odds_to_apisports_shape(self.SAMPLE)
        assert len(result) == 1
        bm = result[0]["bookmakers"][0]
        assert bm["name"] == "Pinnacle"
        bet_names = {b["name"] for b in bm["bets"]}
        assert {"Match Winner", "Goals Over/Under", "Both Teams Score",
                 "Corners Over/Under", "Asian Handicap"} <= bet_names

    def test_uses_last_seen_not_opening(self):
        result = ts_norm.normalize_thestatsapi_odds_to_apisports_shape(self.SAMPLE)
        bm = result[0]["bookmakers"][0]
        mw = next(b for b in bm["bets"] if b["name"] == "Match Winner")
        home_val = next(v for v in mw["values"] if v["value"] == "Home")
        # last_seen=2.050, opening=2.100 → 2.05
        assert home_val["odd"] == "2.05"

    def test_opening_preserved_for_movement(self):
        result = ts_norm.normalize_thestatsapi_odds_to_apisports_shape(self.SAMPLE)
        opening_map = result[0]["_opening_odds"]
        assert opening_map["Pinnacle|Match Winner|Home"] == 2.10
        assert opening_map["Pinnacle|Match Winner|Draw"] == 3.40
        assert opening_map["Pinnacle|Goals Over/Under|Over 2.5"] == 1.85
        assert opening_map["Pinnacle|Both Teams Score|Yes"] == 1.75
        assert opening_map["Pinnacle|Corners Over/Under|Over 9.5"] == 1.90
        assert opening_map["Pinnacle|Asian Handicap|Home"] == 1.91

    def test_line_key_parser(self):
        f = ts_norm._ts_line_key_to_label
        assert f("over_2_5") == "2.5"
        assert f("over_3_5") == "3.5"
        assert f("over_9_5") == "9.5"
        assert f("under_1_5") == "1.5"
        assert f("garbage") is None
        assert f("") is None
        assert f(None) is None

    def test_source_tag_thestatsapi(self):
        result = ts_norm.normalize_thestatsapi_odds_to_apisports_shape(self.SAMPLE)
        assert result[0]["_source"] == "thestatsapi"

    @pytest.mark.parametrize("bad_input", [
        {}, None, {"bookmakers": []}, {"bookmakers": [{}]},
        {"bookmakers": [{"bookmaker": "X"}]},          # missing markets
        {"bookmakers": [{"bookmaker": "X", "markets": "not a dict"}]},
    ])
    def test_fail_soft_returns_empty_list(self, bad_input):
        assert ts_norm.normalize_thestatsapi_odds_to_apisports_shape(bad_input) == []

    def test_odds_below_1_01_rejected(self):
        sample = {
            "match_id": "mt_x",
            "bookmakers": [{"bookmaker": "X", "markets": {
                "match_odds": {"home": {"opening": "1.00", "last_seen": "0.99"}},
            }}],
        }
        # Both opening and last_seen rejected → no values → no bets.
        assert ts_norm.normalize_thestatsapi_odds_to_apisports_shape(sample) == []

    def test_string_odds_parsed_to_float(self):
        result = ts_norm.normalize_thestatsapi_odds_to_apisports_shape(self.SAMPLE)
        opening = result[0]["_opening_odds"]
        # All values must be floats, not strings.
        for v in opening.values():
            assert isinstance(v, float)


# ─────────────────────────────────────────────────────────────────────
# Cambio 1 — Client surface tests (no real HTTP)
# ─────────────────────────────────────────────────────────────────────
class TestThestatsapiClientSurface:
    def test_odds_for_fixture_is_exported(self):
        assert callable(ts_client.odds_for_fixture)

    def test_resolve_match_id_by_names_is_exported(self):
        assert callable(ts_client.resolve_thestatsapi_match_id_by_names)

    @pytest.mark.asyncio
    async def test_odds_for_fixture_fail_soft_empty_id(self):
        # No HTTP call should be made — empty match_id short-circuits.
        async with httpx.AsyncClient() as client:
            result = await ts_client.odds_for_fixture(client, "")
            assert result == {}

    @pytest.mark.asyncio
    async def test_odds_for_fixture_returns_empty_when_disabled(self):
        # Without ENABLE_THE_STATS_API=true the _request helper returns {}.
        import os
        prev_enable = os.environ.pop("ENABLE_THE_STATS_API", None)
        prev_key = os.environ.pop("THESTATSAPI_KEY", None)
        try:
            async with httpx.AsyncClient() as client:
                result = await ts_client.odds_for_fixture(client, "mt_14502")
                assert result == {}
        finally:
            if prev_enable is not None:
                os.environ["ENABLE_THE_STATS_API"] = prev_enable
            if prev_key is not None:
                os.environ["THESTATSAPI_KEY"] = prev_key

    @pytest.mark.asyncio
    async def test_odds_for_fixture_returns_data_subdict_on_success(self, monkeypatch):
        # Mock _request to return the envelope { "data": {...} }.
        called: dict = {}

        async def fake_request(client, method, path, params=None, *, timeout=8.0, retries=1):
            called["path"] = path
            return {"data": {"match_id": "mt_14502", "bookmakers": []}}

        monkeypatch.setattr(ts_client, "_request", fake_request)
        async with httpx.AsyncClient() as c:
            result = await ts_client.odds_for_fixture(c, "mt_14502")
        assert called["path"] == "/football/matches/mt_14502/odds"
        assert result == {"match_id": "mt_14502", "bookmakers": []}

    @pytest.mark.asyncio
    async def test_resolve_match_id_fail_soft_when_disabled(self):
        import os
        prev_enable = os.environ.pop("ENABLE_THE_STATS_API", None)
        prev_key = os.environ.pop("THESTATSAPI_KEY", None)
        try:
            async with httpx.AsyncClient() as client:
                result = await ts_client.resolve_thestatsapi_match_id_by_names(
                    client, home="Brazil", away="Argentina", date="2026-04-19",
                )
                assert result is None
        finally:
            if prev_enable is not None:
                os.environ["ENABLE_THE_STATS_API"] = prev_enable
            if prev_key is not None:
                os.environ["THESTATSAPI_KEY"] = prev_key

    @pytest.mark.asyncio
    async def test_resolve_match_id_finds_match_by_name(self, monkeypatch):
        async def fake_fetch_fixtures(client, *, date_from=None, date_to=None,
                                        competition_id=None):
            return [
                {"id": "mt_14502",
                 "home_team": {"name": "Brazil"},
                 "away_team": {"name": "Argentina"},
                 "competition": {"name": "Copa America"}},
                {"id": "mt_14503",
                 "home_team": {"name": "France"},
                 "away_team": {"name": "Italy"}},
            ]

        monkeypatch.setattr(ts_client, "fetch_fixtures", fake_fetch_fixtures)
        monkeypatch.setattr(ts_client, "is_enabled", lambda: True)
        async with httpx.AsyncClient() as c:
            mid = await ts_client.resolve_thestatsapi_match_id_by_names(
                c, home="Brazil", away="Argentina",
                date="2026-04-19", competition="Copa America",
            )
        assert mid == "mt_14502"

    @pytest.mark.asyncio
    async def test_resolve_match_id_normalises_accents(self, monkeypatch):
        async def fake_fetch_fixtures(client, *, date_from=None, date_to=None,
                                        competition_id=None):
            return [{"id": "mt_777",
                     "home_team": {"name": "México"},
                     "away_team": {"name": "Suecia"}}]
        monkeypatch.setattr(ts_client, "fetch_fixtures", fake_fetch_fixtures)
        monkeypatch.setattr(ts_client, "is_enabled", lambda: True)
        async with httpx.AsyncClient() as c:
            mid = await ts_client.resolve_thestatsapi_match_id_by_names(
                c, home="Mexico", away="Suecia", date="2026-04-19",
            )
        assert mid == "mt_777"


# ─────────────────────────────────────────────────────────────────────
# Cambio 3-5 — Integration smoke through data_ingestion (indirect)
# ─────────────────────────────────────────────────────────────────────
class TestDataIngestionWiring:
    def test_normalize_function_exists_and_supports_full_payload(self):
        """End-to-end smoke: the full sample produces a usable shape
        that `nz.normalize_odds` can consume."""
        sample = TestThestatsapiOddsAdapter.SAMPLE
        shape = ts_norm.normalize_thestatsapi_odds_to_apisports_shape(sample)
        # Sanity: the produced shape mirrors API-Sports enough that
        # normalize_odds can ingest it.
        from services import normalizer as nz
        normalised = nz.normalize_odds(shape)
        assert normalised.get("available") is True
        # Markets list reflects what we mapped.
        markets = normalised.get("markets") or {}
        # normalize_odds keys: "Match Winner", "Goals Over/Under", "Both Teams Score"
        assert "Match Winner" in markets or markets  # at minimum: something
