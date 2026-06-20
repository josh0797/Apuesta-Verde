"""F96.2 — Tests para TheSportsDB `lookup_event_stats` + settler corners wiring.

Cobertura:
  - `lookup_event_stats` con payloads V1 y V2.
  - Fail-soft cuando el cliente está disabled o el HTTP falla.
  - `_lookup_corners_from_thesportsdb` cuando:
      * snapshot tiene `thesportsdb_event_id` directo
      * snapshot NO tiene event_id → se resuelve vía `fetch_livescore`
      * resolución falla → `THESPORTSDB_CORNERS_NOT_AVAILABLE`
      * stats vienen con `Corner Kicks` (formato V1 intHome/intAway)
      * stats parciales (total sin home/away) → `PARTIAL_CORNERS_DATA`
      * stats sin corner alias → `THESPORTSDB_CORNERS_NOT_AVAILABLE`
  - Integración final en `lookup_total_corners`: cuando TheStatsAPI
    falla pero TheSportsDB tiene corners completos, la cascada los
    retorna como `PROVIDER_THESPORTSDB`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import football_finished_game_settler as fgs
from services.external_sources import thesportsdb_client as tsdb


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
def _snap_with_tsdb_id(event_id="2490796"):
    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(hours=5)
    return {
        "match_id":              "m-1",
        "thesportsdb_event_id":  event_id,
        "sport":                 "football",
        "home_team":             {"name": "Brazil"},
        "away_team":             {"name": "Haiti"},
        "match_date":            kickoff,
        "reason_codes":          [],
    }


def _snap_no_tsdb_id():
    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(hours=5)
    return {
        "match_id":     "m-2",
        "sport":        "football",
        "home_team":    {"name": "Brazil"},
        "away_team":    {"name": "Haiti"},
        "match_date":   kickoff,
        "reason_codes": [],
    }


# =====================================================================
# tsdb.lookup_event_stats
# =====================================================================
class TestLookupEventStats:
    @pytest.mark.asyncio
    async def test_v1_happy_path(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(client, path, *, params=None, timeout=8.0):
            assert "lookupeventstats.php" in path
            assert params == {"id": "2490796"}
            return {
                "eventstats": [
                    {"strStat": "Shots on Target", "intHome": "5", "intAway": "3"},
                    {"strStat": "Corner Kicks",    "intHome": "7", "intAway": "4"},
                ],
            }

        with patch.object(tsdb, "_request", side_effect=fake_request):
            env = await tsdb.lookup_event_stats("2490796", client=None)
        assert env["available"] is True
        assert env["endpoint"] == "v1"
        assert any(
            isinstance(r, dict) and r.get("strStat") == "Corner Kicks"
            for r in env["raw_stats"]
        )
        assert "Corner Kicks" in env["raw_names"]

    @pytest.mark.asyncio
    async def test_v2_payload_shape(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(client, path, *, params=None, timeout=8.0):
            # In prefer_v2 mode, v2 is tried first.
            if "v2/json/lookup/event_stats" in path:
                return {"stats": [
                    {"name": "Corners", "home": 6, "away": 2},
                ]}
            return {}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            env = await tsdb.lookup_event_stats(
                "2490796", client=None, prefer_v2=True,
            )
        assert env["available"] is True
        assert env["endpoint"] == "v2"
        assert env["raw_names"] == ["Corners"]

    @pytest.mark.asyncio
    async def test_v2_nested_data_stats(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(client, path, *, params=None, timeout=8.0):
            if "v2/json/lookup/event_stats" in path:
                return {"data": {"stats": [
                    {"name": "Corner Kicks", "home": 4, "away": 5},
                ]}}
            return {}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            env = await tsdb.lookup_event_stats(
                "9999", client=None, prefer_v2=True,
            )
        assert env["available"] is True
        assert env["endpoint"] == "v2"

    @pytest.mark.asyncio
    async def test_missing_event_id_returns_reason_code(self):
        env = await tsdb.lookup_event_stats("", client=None)
        assert env["available"] is False
        assert "THESPORTSDB_EVENT_ID_MISSING" in env["reason_codes"]

    @pytest.mark.asyncio
    async def test_disabled_client(self, monkeypatch):
        monkeypatch.delenv("THESPORTSDB_KEY", raising=False)
        env = await tsdb.lookup_event_stats("12345", client=None)
        assert env["available"] is False
        assert "THESPORTSDB_DISABLED" in env["reason_codes"]

    @pytest.mark.asyncio
    async def test_both_endpoints_empty(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_request(client, path, *, params=None, timeout=8.0):
            return {}

        with patch.object(tsdb, "_request", side_effect=fake_request):
            env = await tsdb.lookup_event_stats("12345", client=None)
        assert env["available"] is False
        # Should have collected empty reason codes for both endpoints.
        codes = " ".join(env["reason_codes"])
        assert "V1_EMPTY" in codes
        assert "V2_EMPTY" in codes


# =====================================================================
# _lookup_corners_from_thesportsdb (settler wiring)
# =====================================================================
class TestSettlerCornersWiringTheSportsDB:
    @pytest.mark.asyncio
    async def test_uses_event_id_from_snapshot(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_event_stats(event_id, *, client=None, **kw):
            assert event_id == "2490796"
            return {
                "available": True,
                "endpoint": "v1",
                "event_id": event_id,
                "raw_stats": [
                    {"strStat": "Corner Kicks", "intHome": "8", "intAway": "3"},
                ],
                "raw_names": ["Corner Kicks"],
                "reason_codes": [],
            }

        with patch.object(tsdb, "lookup_event_stats", side_effect=fake_event_stats):
            res = await fgs._lookup_corners_from_thesportsdb(
                _snap_with_tsdb_id(), http_client=None,
            )
        assert res["available"] is True
        assert res["home_corners"] == 8
        assert res["away_corners"] == 3
        assert res["total_corners"] == 11
        assert res["source"] == fgs.PROVIDER_THESPORTSDB
        assert fgs.RC_CORNERS_FROM_THESPORTSDB in res["reason_codes"]

    @pytest.mark.asyncio
    async def test_resolves_event_id_via_livescore_when_missing(
        self, monkeypatch,
    ):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_livescore(sport, *, client=None, **kw):
            return {
                "available": True,
                "source":    "thesportsdb",
                "sport":     sport,
                "items": [
                    {"match_id": "abc-resolved",
                     "home_team": {"name": "Brazil"},
                     "away_team": {"name": "Haiti"},
                     "status_normalized": "FINISHED",
                     "home_score": 2, "away_score": 1},
                ],
                "reason_codes": ["THESPORTSDB_OK"],
            }

        async def fake_event_stats(event_id, *, client=None, **kw):
            assert event_id == "abc-resolved"
            return {
                "available": True, "endpoint": "v1",
                "event_id":  event_id,
                "raw_stats": [
                    {"strStat": "Corners", "intHome": "5", "intAway": "5"},
                ],
                "raw_names": ["Corners"], "reason_codes": [],
            }

        with patch.object(tsdb, "fetch_livescore", side_effect=fake_livescore), \
             patch.object(tsdb, "lookup_event_stats", side_effect=fake_event_stats):
            res = await fgs._lookup_corners_from_thesportsdb(
                _snap_no_tsdb_id(), http_client=None,
            )
        assert res["available"] is True
        assert res["total_corners"] == 10
        assert res["source"] == fgs.PROVIDER_THESPORTSDB

    @pytest.mark.asyncio
    async def test_no_event_id_and_livescore_empty(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def empty_livescore(sport, *, client=None, **kw):
            return {"available": False, "items": []}

        with patch.object(tsdb, "fetch_livescore", side_effect=empty_livescore):
            res = await fgs._lookup_corners_from_thesportsdb(
                _snap_no_tsdb_id(), http_client=None,
            )
        assert res["available"] is False
        assert fgs.RC_THESPORTSDB_CORNERS_NOT_AVAILABLE in res["reason_codes"]

    @pytest.mark.asyncio
    async def test_event_stats_partial_only_total(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_event_stats(event_id, *, client=None, **kw):
            return {
                "available": True, "endpoint": "v2",
                "event_id":  event_id,
                "raw_stats": [
                    {"name": "Corner Kicks", "value": "11"},   # total only
                ],
                "raw_names": ["Corner Kicks"], "reason_codes": [],
            }

        with patch.object(tsdb, "lookup_event_stats", side_effect=fake_event_stats):
            res = await fgs._lookup_corners_from_thesportsdb(
                _snap_with_tsdb_id(), http_client=None,
            )
        assert res["available"] is False
        assert fgs.RC_PARTIAL_CORNERS_DATA in res["reason_codes"]
        assert "Corner Kicks" in res["raw_names"]

    @pytest.mark.asyncio
    async def test_event_stats_no_corners_alias(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def fake_event_stats(event_id, *, client=None, **kw):
            return {
                "available": True, "endpoint": "v1",
                "event_id":  event_id,
                "raw_stats": [
                    {"strStat": "Possession", "intHome": "55", "intAway": "45"},
                    {"strStat": "Shots",      "intHome": "12", "intAway": "10"},
                ],
                "raw_names": ["Possession", "Shots"], "reason_codes": [],
            }

        with patch.object(tsdb, "lookup_event_stats", side_effect=fake_event_stats):
            res = await fgs._lookup_corners_from_thesportsdb(
                _snap_with_tsdb_id(), http_client=None,
            )
        assert res["available"] is False
        assert fgs.RC_THESPORTSDB_CORNERS_NOT_AVAILABLE in res["reason_codes"]
        # Debug audit must surface the names we DID see.
        assert "Possession" in res["raw_names"]
        assert "Shots" in res["raw_names"]

    @pytest.mark.asyncio
    async def test_lookup_event_stats_raises_fail_soft(self, monkeypatch):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def boom(*args, **kwargs):
            raise RuntimeError("provider down")

        with patch.object(tsdb, "lookup_event_stats", side_effect=boom):
            res = await fgs._lookup_corners_from_thesportsdb(
                _snap_with_tsdb_id(), http_client=None,
            )
        assert res["available"] is False
        assert fgs.RC_THESPORTSDB_CORNERS_NOT_AVAILABLE in res["reason_codes"]

    @pytest.mark.asyncio
    async def test_disabled_client_returns_reason_code(self, monkeypatch):
        monkeypatch.delenv("THESPORTSDB_KEY", raising=False)
        res = await fgs._lookup_corners_from_thesportsdb(
            _snap_with_tsdb_id(), http_client=None,
        )
        assert res["available"] is False
        assert "THESPORTSDB_DISABLED" in res["reason_codes"]


# =====================================================================
# Cascada final: TheStatsAPI fail → TheSportsDB succeeds
# =====================================================================
class TestCornersCascadeFallback:
    @pytest.mark.asyncio
    async def test_thesportsdb_fills_in_when_thestatsapi_empty(
        self, monkeypatch,
    ):
        monkeypatch.setenv("THESPORTSDB_KEY", "test-key")

        async def empty_ts(match_id, *, http_client):
            return {"available": False, "home_corners": None,
                    "away_corners": None, "total_corners": None,
                    "source": None, "raw_names": [], "reason_codes": []}

        async def good_sdb(snap, *, http_client):
            return {
                "available": True, "home_corners": 6, "away_corners": 5,
                "total_corners": 11, "source": fgs.PROVIDER_THESPORTSDB,
                "raw_names": ["Corner Kicks"],
                "reason_codes": [fgs.RC_CORNERS_FROM_THESPORTSDB],
            }

        with patch.object(fgs, "_lookup_corners_from_thestatsapi",
                          side_effect=empty_ts), \
             patch.object(fgs, "_lookup_corners_from_thesportsdb",
                          side_effect=good_sdb):
            res = await fgs.lookup_total_corners(
                "m-1", _snap_with_tsdb_id(), http_client=None,
            )
        assert res["available"] is True
        assert res["source"] == fgs.PROVIDER_THESPORTSDB
        assert res["total_corners"] == 11
        assert fgs.RC_CORNERS_FROM_THESPORTSDB in res["reason_codes"]
