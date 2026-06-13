"""Phase F84.e — Tests for the TheStatsAPI odds adapter + orchestrator
prioridad-inversa (TheStatsAPI primaria, API-Sports fallback)."""
from __future__ import annotations

from typing import Any

import pytest

from services.external_sources import thestatsapi_odds_adapter as adapter


# ─────────────────────────────────────────────────────────────────────
# Adapter layer (pure, mocked)
# ─────────────────────────────────────────────────────────────────────
class TestFetchOddsApiSportsShape:
    @pytest.mark.asyncio
    async def test_disabled_returns_triple_none(self, monkeypatch):
        from services.external_sources import thestatsapi_client as _ts
        monkeypatch.setattr(_ts, "is_enabled", lambda: False)
        out = await adapter.fetch_odds_api_sports_shape(
            client=None, fx_raw={"_thestatsapi_raw_id": "mt_1"},
        )
        assert out == (None, None, None)

    @pytest.mark.asyncio
    async def test_uses_cached_raw_id_when_present(self, monkeypatch):
        from services.external_sources import thestatsapi_client as _ts
        from services.external_sources import thestatsapi_normalizer as _ts_norm
        from services import normalizer as nz
        captured: dict[str, Any] = {}

        monkeypatch.setattr(_ts, "is_enabled", lambda: True)

        async def _fake_odds(client, match_id):
            captured["match_id"] = match_id
            return {"data": {"bookmakers": [{"name": "Pinnacle"}]}}

        monkeypatch.setattr(_ts, "odds_for_fixture", _fake_odds)

        def _fake_shape(raw):
            return [{"_opening_odds": {"P|H|2.10": 2.10},
                     "bookmakers": [{"id": 1, "name": "Pinnacle",
                                       "bets": [{"id": 1, "values": []}]}],
                     "fixture": {"id": 1}}]
        monkeypatch.setattr(
            _ts_norm, "normalize_thestatsapi_odds_to_apisports_shape",
            _fake_shape,
        )

        def _fake_norm(shape):
            return {"available": True,
                     "bookmakers": shape[0]["bookmakers"],
                     "_normalised": True}
        monkeypatch.setattr(nz, "normalize_odds", _fake_norm)

        shape, norm, mid = await adapter.fetch_odds_api_sports_shape(
            client=None,
            fx_raw={"_thestatsapi_raw_id": "mt_500"},
        )
        # The cached raw id was used directly — no resolve call needed.
        assert captured["match_id"] == "mt_500"
        assert mid == "mt_500"
        assert norm["available"] is True
        assert "_opening_odds" in norm
        assert norm["_opening_odds"] == {"P|H|2.10": 2.10}

    @pytest.mark.asyncio
    async def test_uses_external_source_id_when_provider_is_thestatsapi(
            self, monkeypatch):
        from services.external_sources import thestatsapi_client as _ts
        from services.external_sources import thestatsapi_normalizer as _ts_norm
        from services import normalizer as nz

        captured: dict[str, Any] = {}
        monkeypatch.setattr(_ts, "is_enabled", lambda: True)

        async def _fake_odds(client, match_id):
            captured["match_id"] = match_id
            return {"data": {}}

        monkeypatch.setattr(_ts, "odds_for_fixture", _fake_odds)
        monkeypatch.setattr(
            _ts_norm, "normalize_thestatsapi_odds_to_apisports_shape",
            lambda _: [{"_opening_odds": {}, "bookmakers": [{}]}],
        )
        monkeypatch.setattr(
            nz, "normalize_odds", lambda _: {"available": True, "bookmakers": []},
        )

        await adapter.fetch_odds_api_sports_shape(
            client=None,
            fx_raw={"_external_source": "thestatsapi",
                     "_external_source_id": "mt_999"},
        )
        assert captured["match_id"] == "mt_999"

    @pytest.mark.asyncio
    async def test_resolves_by_names_when_no_raw_id(self, monkeypatch):
        from services.external_sources import thestatsapi_client as _ts
        from services.external_sources import thestatsapi_normalizer as _ts_norm
        from services import normalizer as nz

        monkeypatch.setattr(_ts, "is_enabled", lambda: True)

        async def _resolve(client, *, home, away, date, competition):
            assert home == "United States"
            assert away == "Paraguay"
            return "mt_resolved_42"

        monkeypatch.setattr(_ts, "resolve_thestatsapi_match_id_by_names", _resolve)

        async def _fake_odds(client, mid):
            return {"data": {"bookmakers": []}}

        monkeypatch.setattr(_ts, "odds_for_fixture", _fake_odds)
        monkeypatch.setattr(
            _ts_norm, "normalize_thestatsapi_odds_to_apisports_shape",
            lambda _: [{"_opening_odds": {}, "bookmakers": [{}]}],
        )
        monkeypatch.setattr(
            nz, "normalize_odds", lambda _: {"available": True, "bookmakers": []},
        )

        _, _, mid = await adapter.fetch_odds_api_sports_shape(
            client=None, fx_raw={},
            home_name="United States", away_name="Paraguay",
            kickoff="2026-06-12T15:00:00Z", league_name="Friendlies",
        )
        assert mid == "mt_resolved_42"

    @pytest.mark.asyncio
    async def test_empty_response_returns_no_norm(self, monkeypatch):
        from services.external_sources import thestatsapi_client as _ts
        monkeypatch.setattr(_ts, "is_enabled", lambda: True)

        async def _empty(client, mid):
            return None
        monkeypatch.setattr(_ts, "odds_for_fixture", _empty)

        shape, norm, mid = await adapter.fetch_odds_api_sports_shape(
            client=None, fx_raw={"_thestatsapi_raw_id": "mt_1"},
        )
        assert shape is None and norm is None
        assert mid == "mt_1"

    @pytest.mark.asyncio
    async def test_no_match_id_returns_triple_none(self, monkeypatch):
        from services.external_sources import thestatsapi_client as _ts
        monkeypatch.setattr(_ts, "is_enabled", lambda: True)

        # No raw id, no names → cannot resolve.
        out = await adapter.fetch_odds_api_sports_shape(
            client=None, fx_raw={},
        )
        assert out == (None, None, None)

    @pytest.mark.asyncio
    async def test_normalise_failure_returns_no_norm(self, monkeypatch):
        from services.external_sources import thestatsapi_client as _ts
        from services.external_sources import thestatsapi_normalizer as _ts_norm
        monkeypatch.setattr(_ts, "is_enabled", lambda: True)

        async def _fake(client, mid):
            return {"data": {"bookmakers": [{}]}}
        monkeypatch.setattr(_ts, "odds_for_fixture", _fake)

        def _raise(_):
            raise RuntimeError("normaliser exploded")
        monkeypatch.setattr(
            _ts_norm, "normalize_thestatsapi_odds_to_apisports_shape", _raise,
        )

        shape, norm, mid = await adapter.fetch_odds_api_sports_shape(
            client=None, fx_raw={"_thestatsapi_raw_id": "mt_x"},
        )
        assert shape is None and norm is None
        assert mid == "mt_x"

    @pytest.mark.asyncio
    async def test_opening_odds_preserved_in_norm(self, monkeypatch):
        from services.external_sources import thestatsapi_client as _ts
        from services.external_sources import thestatsapi_normalizer as _ts_norm
        from services import normalizer as nz
        monkeypatch.setattr(_ts, "is_enabled", lambda: True)

        async def _ok(_, __):
            return {"data": {"bookmakers": [{"name": "P"}]}}
        monkeypatch.setattr(_ts, "odds_for_fixture", _ok)

        opening = {"Pinnacle|MR|HOME": 2.05}
        monkeypatch.setattr(
            _ts_norm, "normalize_thestatsapi_odds_to_apisports_shape",
            lambda _: [{"_opening_odds": opening,
                          "bookmakers": [{"id": 1, "name": "Pinnacle",
                                            "bets": []}]}],
        )
        monkeypatch.setattr(
            nz, "normalize_odds", lambda _: {"available": True, "bookmakers": [{}]},
        )

        _, norm, _ = await adapter.fetch_odds_api_sports_shape(
            client=None, fx_raw={"_thestatsapi_raw_id": "mt_1"},
        )
        assert norm["_opening_odds"] == opening


# ─────────────────────────────────────────────────────────────────────
# Orchestrator priority semantics (mirror the decision tree)
# ─────────────────────────────────────────────────────────────────────
class TestOrchestratorOddsPriorityInversion:
    """Mirror the F84.e branch in ``data_ingestion._enrich_football``:

        try TheStatsAPI → if available → source="thestatsapi"
                       → else if flag → try API-Sports → "api_sports_fallback"
                       → else → "no_odds"
    """
    @staticmethod
    async def _resolve(*, ts_norm, aps_norm, fallback_flag):
        async def _ts():
            return ts_norm
        async def _aps():
            return aps_norm
        norm = await _ts()
        source = "no_odds"
        if norm and norm.get("available"):
            source = "thestatsapi"
        elif fallback_flag:
            norm = await _aps()
            if norm and norm.get("available"):
                source = "api_sports_fallback"
            else:
                source = "no_odds"
        else:
            norm = {"available": False}
            source = "no_odds"
        return norm, source

    @pytest.mark.asyncio
    async def test_thestatsapi_hit_short_circuits_fallback(self):
        norm, source = await self._resolve(
            ts_norm={"available": True, "bookmakers": [{"id": 1}],
                       "_opening_odds": {"P|H|2.0": 2.0}},
            aps_norm={"available": True, "bookmakers": [{"id": 99}]},  # MUST NOT be returned
            fallback_flag=True,
        )
        assert source == "thestatsapi"
        assert norm["bookmakers"][0]["id"] == 1
        assert "_opening_odds" in norm

    @pytest.mark.asyncio
    async def test_thestatsapi_miss_flag_true_falls_back(self):
        norm, source = await self._resolve(
            ts_norm=None,
            aps_norm={"available": True, "bookmakers": [{"id": 99}]},
            fallback_flag=True,
        )
        assert source == "api_sports_fallback"
        assert norm["bookmakers"][0]["id"] == 99

    @pytest.mark.asyncio
    async def test_thestatsapi_miss_flag_false_keeps_no_odds(self):
        norm, source = await self._resolve(
            ts_norm=None,
            aps_norm={"available": True, "bookmakers": [{"id": 99}]},
            fallback_flag=False,
        )
        assert source == "no_odds"
        assert norm["available"] is False

    @pytest.mark.asyncio
    async def test_both_empty_keeps_no_odds(self):
        norm, source = await self._resolve(
            ts_norm=None, aps_norm={"available": False}, fallback_flag=True,
        )
        assert source == "no_odds"
        assert norm["available"] is False


class TestFlagWiring:
    """The same ENABLE_API_SPORTS_FALLBACK flag introduced in F84.a/b
    governs the odds branch too — assert that the helper still parses
    the truthy/falsy values consistently."""

    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("FALSE", False), ("1", True), ("off", False),
        ("", True),  # empty = default true
    ])
    def test_flag_helper_is_shared(self, monkeypatch, raw, expected):
        from services import data_ingestion as di
        monkeypatch.setenv("ENABLE_API_SPORTS_FALLBACK", raw)
        assert di._api_sports_fallback_enabled() is expected
