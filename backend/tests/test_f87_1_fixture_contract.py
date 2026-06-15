"""F87.1 — Football fixture contract + discovery audit tests.

Validates the user spec:
  * ``ensure_api_football_fixture_shape`` promotes both flat F87 legacy
    and nested API-Football inputs into the canonical nested contract.
  * ``_enrich_football`` reads ``fx["fixture"]["id"]`` etc. without
    breaking when the discovery cascade returns the new shape.
  * ``_discover_football_fixtures`` exposes the discovery audit with
    ``counts_raw`` vs ``counts_after_shape_normalization``.
  * The MLB isolation guarantee from F87 still holds.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import data_ingestion as di
from services import football_fixture_contract as ffc


# =====================================================================
# Helpers
# =====================================================================
def _flat_f87(home="H", away="A", *, kickoff_in_h: float = 2.0,
              status: str = "NS", league: str = "Premier League",
              source: str = "sofascore_pw", fid: str | None = None) -> dict:
    ts = int((datetime.now(timezone.utc) + timedelta(hours=kickoff_in_h)).timestamp())
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    out: dict = {
        "id":           fid or f"{source}-1",
        "date":         iso,
        "timestamp":    ts,
        "status":       {"short": status},
        "league":       {"id": None, "name": league, "country": "England"},
        "teams":        {"home": {"id": None, "name": home},
                          "away": {"id": None, "name": away}},
        "_external_source":    source,
        "_external_source_id": fid or "1",
    }
    return out


def _nested(home="H", away="A", *, kickoff_in_h: float = 2.0,
            status: str = "NS", league: str = "Premier League") -> dict:
    ts = int((datetime.now(timezone.utc) + timedelta(hours=kickoff_in_h)).timestamp())
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return {
        "fixture": {"id": "af-1", "date": iso, "timestamp": ts,
                     "status": {"short": status, "long": status},
                     "venue":  {"name": None, "city": None}},
        "league":  {"id": 39, "name": league, "country": "England", "season": 2026},
        "teams":   {"home": {"id": 11, "name": home},
                     "away": {"id": 22, "name": away}},
    }


# =====================================================================
# ensure_api_football_fixture_shape
# =====================================================================
class TestContractNormaliser:
    def test_flat_f87_fixture_is_normalized_to_api_football_shape(self):
        flat = _flat_f87(home="Liverpool", away="Man City", source="sofascore_pw")
        codes: list[str] = []
        fx = ffc.ensure_api_football_fixture_shape(flat, source="sofascore_pw",
                                                    reason_codes=codes)
        assert fx is not None
        # Nested keys MUST exist.
        assert "fixture" in fx
        assert "league"  in fx
        assert "teams"   in fx
        assert isinstance(fx["fixture"].get("id"), str)
        assert isinstance(fx["fixture"].get("timestamp"), int)
        assert fx["fixture"]["status"]["short"] == "NS"
        assert fx["teams"]["home"]["name"] == "Liverpool"
        assert fx["teams"]["away"]["name"] == "Man City"
        # Discovery / source metadata preserved.
        assert fx["_external_source"]  == "sofascore_pw"
        assert fx["_discovery_source"] == "sofascore_pw"
        assert ffc.RC_NORMALIZED in codes

    def test_already_nested_input_kept_as_valid(self):
        nested = _nested()
        codes: list[str] = []
        fx = ffc.ensure_api_football_fixture_shape(nested, source="api_football",
                                                    reason_codes=codes)
        assert fx is not None
        assert fx["fixture"]["id"] == "af-1"
        assert ffc.RC_ALREADY_VALID in codes

    def test_synthetic_fixture_id_created_when_missing(self):
        flat = _flat_f87(home="Genk", away="Antwerp", source="scrapedo")
        flat["id"] = None
        codes: list[str] = []
        fx = ffc.ensure_api_football_fixture_shape(flat, source="scrapedo",
                                                    reason_codes=codes)
        assert fx is not None
        assert fx["fixture"]["id"].startswith("scrapedo-genk-antwerp-")
        assert ffc.RC_SYNTHETIC_ID_CREATED in codes

    def test_missing_home_away_shape_invalid(self):
        for bad in [
            {"id": "x", "teams": {"home": {"name": ""},
                                    "away": {"name": "A"}}, "timestamp": 1700000000},
            {"id": "y", "teams": {"home": {"name": "H"},
                                    "away": {}}, "timestamp": 1700000000},
            {"id": "z", "teams": {}, "timestamp": 1700000000},
            "not a dict", None, 42, [],
        ]:
            codes: list[str] = []
            fx = ffc.ensure_api_football_fixture_shape(bad, source="thestatsapi",
                                                        reason_codes=codes)
            assert fx is None

    def test_kickoff_ts_can_be_derived_from_iso_when_ts_missing(self):
        # Only ISO date; timestamp missing — the contract must compute it.
        kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
        raw = {
            "id":     "x-1",
            "date":   kickoff.isoformat(),
            "league": {"name": "Champions League"},
            "teams":  {"home": {"name": "H"}, "away": {"name": "A"}},
        }
        fx = ffc.ensure_api_football_fixture_shape(raw, source="x")
        assert fx is not None
        assert fx["fixture"]["timestamp"] is not None
        assert fx["fixture"]["timestamp"] >= int(kickoff.timestamp()) - 2

    def test_naive_iso_assumed_utc(self):
        raw = {
            "id":     "x-1",
            "date":   "2026-06-15T15:00:00",   # no tz offset
            "league": {"name": "Premier League"},
            "teams":  {"home": {"name": "H"}, "away": {"name": "A"}},
        }
        codes: list[str] = []
        fx = ffc.ensure_api_football_fixture_shape(raw, source="x",
                                                    reason_codes=codes)
        assert fx is not None
        assert ffc.RC_DATE_NAIVE_ASSUMED in codes

    def test_normalise_bucket_aggregates_counts(self):
        bucket = [
            _flat_f87(home="A1", away="B1"),
            _flat_f87(home="A2", away="B2"),
            {"teams": {"home": {"name": ""}, "away": {"name": "X"}}},  # invalid
        ]
        out, audit = ffc.normalize_bucket(bucket, source="thestatsapi")
        assert audit["raw_count"] == 3
        assert audit["kept_count"] == 2
        assert audit["dropped_count"] == 1
        assert audit["reason_codes"][ffc.RC_INVALID_MISSING_TEAMS] == 1


# =====================================================================
# Adapter outputs satisfy the contract
# =====================================================================
class TestAdapterOutputsContract:
    def test_thestatsapi_adapter_output_has_fixture_nested_contract(self):
        from services.external_sources import thestatsapi_fixtures_adapter as ts
        raw = {
            "id":        "ts-1",
            "timestamp": int(datetime.now(timezone.utc).timestamp()) + 7200,
            "status":    {"short": "scheduled"},
            "league":    {"id": 39, "name": "Premier League", "country": "England"},
            "teams":     {"home": {"id": 1, "name": "Liverpool"},
                           "away": {"id": 2, "name": "Man City"}},
        }
        out = ts._normalise_fixture(raw)
        assert out is not None
        assert "fixture" in out
        assert isinstance(out["fixture"], dict)
        assert out["fixture"]["status"]["short"] == "NS"
        # FFC re-normalisation MUST be idempotent.
        fx = ffc.ensure_api_football_fixture_shape(out, source="thestatsapi")
        assert fx is not None
        assert fx["fixture"]["id"] == out["fixture"]["id"]

    def test_sofascore_adapter_output_normalises_through_contract(self):
        from services.external_sources import sofascore_fixtures_adapter as so
        ev = {
            "id":         "sofa-123",
            "league":     "Premier League - England",
            "kickoff_iso": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            "home_team":  {"name": "Arsenal"},
            "away_team":  {"name": "Chelsea"},
        }
        raw = so._normalise_sofascore_event(ev, source_tag="sofascore_pw")
        fx = ffc.ensure_api_football_fixture_shape(raw, source="sofascore_pw")
        assert fx is not None
        assert fx["teams"]["home"]["name"] == "Arsenal"
        assert fx["fixture"]["status"]["short"] == "NS"


# =====================================================================
# Discovery cascade — counts_raw vs counts_after_shape_normalization
# =====================================================================
class TestDiscoveryAudit:
    @pytest.mark.asyncio
    async def test_discovery_counts_raw_vs_after_shape_normalization(self, monkeypatch):
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        # Two valid + one invalid (missing team) from TheStatsAPI.
        ts_raw = [
            _flat_f87(home="A", away="B", source="thestatsapi", fid="ts-1"),
            _flat_f87(home="C", away="D", source="thestatsapi", fid="ts-2"),
            {"id": "ts-3", "teams": {"home": {"name": ""},   # invalid
                                       "away": {"name": "X"}},
             "timestamp": 1700000000},
        ]
        # Force the cascade to merge (< MIN_VIABLE) so we exercise every probe.
        monkeypatch.setenv("F87_MIN_VIABLE_COUNT", "10")
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=(ts_raw, ["THESTATSAPI_FIXTURES_SUCCESS"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=[])), \
             patch("services.data_ingestion.fb.espn_soccer_scoreboard",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])):
            fixtures, audit = await di._discover_football_fixtures(object())

        # The cascade DROPS the invalid one before any further work.
        assert len(fixtures) == 2
        assert audit["counts_per_src"]["thestatsapi"]    == 3
        assert audit["counts_normalised"]["thestatsapi"] == 2
        assert audit["shape_audit"]["thestatsapi"]["dropped_count"] == 1
        # Every emitted fixture has the nested contract.
        for f in fixtures:
            assert isinstance(f.get("fixture"), dict)
            assert isinstance(f["fixture"].get("id"), str)
            assert isinstance(f.get("teams"), dict)

    @pytest.mark.asyncio
    async def test_thestatsapi_short_circuit_publishes_audit(self, monkeypatch):
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        monkeypatch.delenv("F87_MIN_VIABLE_COUNT", raising=False)
        ts_raw = [_flat_f87(home=f"A{i}", away=f"B{i}", source="thestatsapi",
                              fid=f"ts-{i}") for i in range(8)]
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=(ts_raw, ["THESTATSAPI_FIXTURES_SUCCESS"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h", AsyncMock(return_value=[])):
            fixtures, audit = await di._discover_football_fixtures(object())

        assert audit["primary_winner"] == "thestatsapi"
        assert audit["counts_per_src"]["thestatsapi"] == 8
        assert audit["counts_normalised"]["thestatsapi"] == 8
        assert len(fixtures) == 8
        # Public accessor returns the same audit.
        pub = di.get_last_football_discovery_audit()
        assert pub["primary_winner"] == "thestatsapi"
        assert pub["counts_normalised"]["thestatsapi"] == 8
        assert any(s.get("home") == "A0" for s in pub.get("sample_fixtures", []))

    @pytest.mark.asyncio
    async def test_get_last_audit_isolated_copy(self, monkeypatch):
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        ts_raw = [_flat_f87(home=f"A{i}", away=f"B{i}", source="thestatsapi",
                              fid=f"ts-{i}") for i in range(6)]
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=(ts_raw, ["OK"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h", AsyncMock(return_value=[])):
            await di._discover_football_fixtures(object())
        pub = di.get_last_football_discovery_audit()
        pub["sources_called"].append("nuked")
        pub2 = di.get_last_football_discovery_audit()
        # Top-level mutations on the public copy do not affect the module global.
        assert "nuked" not in pub2.get("sources_called", [])


# =====================================================================
# Merge step uses normalised shape
# =====================================================================
class TestMergeUsesNormalisedShape:
    @pytest.mark.asyncio
    async def test_f87_merge_uses_normalized_fixture_shape(self, monkeypatch):
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        monkeypatch.setenv("F87_MIN_VIABLE_COUNT", "20")  # force merge
        same_ts = int(datetime.now(timezone.utc).timestamp()) + 3600
        ts_raw = [{
            "id":          "ts-1",
            "timestamp":   same_ts,
            "league":      {"id": 39, "name": "Premier League"},
            "teams":       {"home": {"name": "Liverpool FC"},
                             "away": {"name": "Man City"}},
        }]
        af_raw = [{
            "fixture": {"id": "af-1", "timestamp": same_ts,
                         "date": datetime.fromtimestamp(same_ts, tz=timezone.utc).isoformat(),
                         "status": {"short": "NS"}},
            "league":  {"id": 39, "name": "Premier League"},
            "teams":   {"home": {"name": "Liverpool"},
                         "away": {"name": "Man City"}},
        }]
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=(ts_raw, ["OK"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=af_raw)), \
             patch("services.data_ingestion.fb.espn_soccer_scoreboard",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])):
            merged, audit = await di._discover_football_fixtures(object())

        # The TS entry wins (highest priority). The merge must also have
        # emitted nested shape so _enrich_football consumes it cleanly.
        assert audit["merged"] is True
        assert len(merged) == 1
        assert merged[0]["_discovery_source"] == "thestatsapi"
        assert isinstance(merged[0]["fixture"], dict)
        assert isinstance(merged[0]["fixture"]["id"], str)


# =====================================================================
# MLB isolation still holds with the F87.1 contract
# =====================================================================
class TestMLBIsolationStillHolds:
    @pytest.mark.asyncio
    async def test_mlb_qcm_does_not_import_in_football_discovery(self, monkeypatch):
        import sys
        original = {
            "services.mlb_quality_contact_matchup":
                sys.modules.get("services.mlb_quality_contact_matchup"),
            "services.mlb_pipeline_payload_contract":
                sys.modules.get("services.mlb_pipeline_payload_contract"),
        }

        class _Exploding:
            def __getattr__(self, name):
                raise RuntimeError(f"MLB touched football: {name}")

        sys.modules["services.mlb_quality_contact_matchup"]    = _Exploding()
        sys.modules["services.mlb_pipeline_payload_contract"]  = _Exploding()

        try:
            with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                        AsyncMock(return_value=([_flat_f87(home="A", away="B",
                                                            source="thestatsapi",
                                                            fid=f"ts-{i}")
                                                 for i in range(6)],
                                                ["OK"]))), \
                 patch("services.data_ingestion.af.fixtures_next_48h",
                        AsyncMock(return_value=[])):
                fixtures, audit = await di._discover_football_fixtures(object())
            assert len(fixtures) == 6
            assert audit["isolated_from_mlb"] is True
            assert audit["f87_1_contract"] is True
        finally:
            for k, v in original.items():
                if v is not None:
                    sys.modules[k] = v
                else:
                    sys.modules.pop(k, None)


# =====================================================================
# Public contract guarantees for _enrich_football compatibility
# =====================================================================
class TestEnrichFootballContract:
    def test_normalised_fixture_has_all_fields_required_by_enrich_football(self):
        flat = _flat_f87(home="Real Madrid", away="FC Barcelona",
                          league="La Liga", source="thestatsapi")
        fx = ffc.ensure_api_football_fixture_shape(flat, source="thestatsapi")
        # _enrich_football reads (sample, not exhaustive):
        #   fx["fixture"]["id"]
        #   fx["fixture"]["date"]
        #   fx["fixture"]["timestamp"]
        #   fx["fixture"]["status"]["short"]
        #   fx["league"]["id"], fx["league"]["name"]
        #   fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
        assert isinstance(fx["fixture"]["id"], str)
        assert isinstance(fx["fixture"]["date"], str)
        assert isinstance(fx["fixture"]["timestamp"], int)
        assert isinstance(fx["fixture"]["status"]["short"], str)
        assert "id" in fx["league"]
        assert isinstance(fx["league"]["name"], str)
        assert isinstance(fx["teams"]["home"]["name"], str)
        assert isinstance(fx["teams"]["away"]["name"], str)
        # Top-level mirrors stay (legacy consumers).
        assert fx["id"] == fx["fixture"]["id"]
        assert fx["timestamp"] == fx["fixture"]["timestamp"]
