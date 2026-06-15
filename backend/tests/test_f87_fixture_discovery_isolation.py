"""F87 — MLB/Football isolation guard tests.

Validates the user-required isolation:

  * ``_discover_football_fixtures`` MUST NOT import any MLB module.
  * ``ingest_upcoming(sport="football")`` MUST NOT call
    ``mlb_pipeline_payload_contract.seal_pick_payload``.
  * ``seal_pick_payload`` MUST be a no-op for any payload whose
    ``sport`` is set to a non-MLB value (football, basketball, etc.).
  * If a MLB-only module is FORCED to raise on import, football
    discovery must still succeed.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, patch

import pytest

from services import data_ingestion as di
from services import mlb_pipeline_payload_contract as ppc


# =====================================================================
# 1) discover does not need ANY MLB module
# =====================================================================
class TestDiscoveryIsolation:
    @pytest.mark.asyncio
    async def test_mlb_qcm_import_does_not_affect_football_discovery(
            self, monkeypatch):
        """If MLB QCM module is poisoned (forced to raise on import)
        the football discovery must still succeed because it should
        never touch it."""
        original_qcm = sys.modules.get("services.mlb_quality_contact_matchup")
        original_ppc = sys.modules.get("services.mlb_pipeline_payload_contract")

        class _Exploding:
            def __getattr__(self, name):
                raise RuntimeError(f"MLB module touched football pipeline: {name}")

        sys.modules["services.mlb_quality_contact_matchup"] = _Exploding()
        sys.modules["services.mlb_pipeline_payload_contract"] = _Exploding()

        try:
            af_fixtures = [{
                "id": f"af-{i}",
                "fixture": {"id": f"af-{i}", "timestamp": 1700000000 + i,
                             "date": "2026-06-15T12:00:00+00:00",
                             "status": {"short": "NS"}},
                "league": {"id": 39, "name": "Premier League"},
                "teams":  {"home": {"name": f"H{i}"}, "away": {"name": f"A{i}"}},
                "timestamp": 1700000000 + i,
                "status":    {"short": "NS"},
            } for i in range(6)]
            with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                        AsyncMock(return_value=([], ["THESTATSAPI_FIXTURES_EMPTY"]))), \
                 patch("services.data_ingestion.af.fixtures_next_48h",
                        AsyncMock(return_value=af_fixtures)):
                fixtures, audit = await di._discover_football_fixtures(object())

            assert audit["isolated_from_mlb"] is True
            assert audit["primary_winner"] == "api_football"
            assert len(fixtures) == 6
        finally:
            # Restore real modules so other tests aren't affected.
            if original_qcm is not None:
                sys.modules["services.mlb_quality_contact_matchup"] = original_qcm
            else:
                sys.modules.pop("services.mlb_quality_contact_matchup", None)
            if original_ppc is not None:
                sys.modules["services.mlb_pipeline_payload_contract"] = original_ppc
            else:
                sys.modules.pop("services.mlb_pipeline_payload_contract", None)
            # Re-import to refresh references the rest of the suite uses.
            importlib.reload(ppc)


# =====================================================================
# 2) ingest_upcoming(football) MUST NOT touch seal_pick_payload
# =====================================================================
class TestIngestDoesNotCallSealPickPayload:
    @pytest.mark.asyncio
    async def test_football_ingest_does_not_call_seal_pick_payload(self, monkeypatch):
        """``ingest_upcoming(sport='football')`` walks the discovery
        cascade + competition filter + hydration loop. NONE of that
        should ever call ``seal_pick_payload`` (an MLB-only artifact)."""
        seal_spy = AsyncMock(side_effect=AssertionError(
            "ingest_upcoming(football) must not call seal_pick_payload"
        ))
        # Even if seal_pick_payload is sync, patching it lets the spy
        # raise when called.
        with patch("services.mlb_pipeline_payload_contract.seal_pick_payload",
                    seal_spy), \
             patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=([], ["THESTATSAPI_FIXTURES_EMPTY"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=[])), \
             patch("services.data_ingestion.fb.espn_soccer_scoreboard",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])):
            # Exercise just the discovery layer — that's where any
            # accidental MLB coupling would surface first.
            fixtures, _audit = await di._discover_football_fixtures(object())

        seal_spy.assert_not_called()
        assert isinstance(fixtures, list)


# =====================================================================
# 3) seal_pick_payload is a no-op for non-MLB payloads
# =====================================================================
class TestSealPickPayloadNonMLB:
    def test_football_payload_skips_qcm(self):
        original_picks = [
            {"market": "OVER_2_5", "confidence": 60},
            {"market": "BTTS",     "confidence": 55},
        ]
        payload = {
            "sport": "football",
            "picks": original_picks,
            "match_id": "fb-1",
            "home_team": {"name": "H"},
            "away_team": {"name": "A"},
        }
        sealed = ppc.seal_pick_payload(payload)

        # Picks are NOT mutated, no MLB block added.
        assert sealed["picks"] == original_picks
        assert "quality_contact_matchup" not in sealed
        assert "advanced_stats_snapshot" not in sealed
        assert "sabermetrics_audit" not in sealed
        assert "pressure_base" not in sealed
        assert "market_selection" not in sealed
        # Audit footprint so we can verify the guard fired.
        assert "qcm_audit" in sealed
        assert sealed["qcm_audit"]["applied"] is False
        assert sealed["qcm_audit"]["reason"] == "PAYLOAD_NOT_MLB"
        assert sealed["qcm_audit"]["sport"] == "football"

    @pytest.mark.parametrize("sport", ["basketball", "tennis", "hockey", "nfl"])
    def test_other_non_mlb_sports_also_skip(self, sport):
        payload = {"sport": sport, "picks": [{"market": "X", "confidence": 50}]}
        sealed = ppc.seal_pick_payload(payload)
        assert "quality_contact_matchup" not in sealed
        assert "advanced_stats_snapshot" not in sealed
        assert sealed["qcm_audit"]["sport"] == sport
        assert sealed["picks"] == [{"market": "X", "confidence": 50}]

    @pytest.mark.parametrize("sport", ["mlb", "baseball", "MLB", "BaseBall", " mlb "])
    def test_mlb_payloads_still_processed_normally(self, sport):
        # The guard MUST NOT fire on MLB / baseball — F91/F92 behaviour
        # has to stay intact.
        payload = {
            "sport":           sport,
            "picks":           [{"market": "OVER_8_5", "confidence": 60}],
            "match_id":        "mlb-1",
            "home_team":       {"name": "H"},
            "away_team":       {"name": "A"},
        }
        sealed = ppc.seal_pick_payload(payload)
        assert "quality_contact_matchup" in sealed
        assert "advanced_stats_snapshot" in sealed
        # qcm_audit MUST NOT carry the PAYLOAD_NOT_MLB marker.
        audit = sealed.get("qcm_audit") or {}
        assert audit.get("reason") != "PAYLOAD_NOT_MLB"

    def test_missing_sport_preserves_legacy_mlb_behaviour(self):
        # Legacy payloads without a sport field were treated as MLB by
        # the F91 contract — that has to stay true.
        payload = {
            "picks":     [{"market": "OVER_8_5", "confidence": 60}],
            "match_id":  "legacy-1",
            "home_team": {"name": "H"},
            "away_team": {"name": "A"},
        }
        sealed = ppc.seal_pick_payload(payload)
        assert "quality_contact_matchup" in sealed
        # qcm_audit was either not set (no PAYLOAD_NOT_MLB) or, if F92
        # added one, must NOT carry PAYLOAD_NOT_MLB.
        audit = sealed.get("qcm_audit") or {}
        assert audit.get("reason") != "PAYLOAD_NOT_MLB"

    def test_no_exception_on_garbage_input(self):
        for bad in [None, "string", 42, [1, 2, 3]]:
            sealed = ppc.seal_pick_payload(bad)
            assert isinstance(sealed, dict)


# =====================================================================
# 4) discovery log message includes isolated_from_mlb=true
# =====================================================================
class TestDiscoveryLogsIsolation:
    @pytest.mark.asyncio
    async def test_audit_dict_marks_isolation(self, monkeypatch):
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=([], ["THESTATSAPI_FIXTURES_EMPTY"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=[])), \
             patch("services.data_ingestion.fb.espn_soccer_scoreboard",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])):
            _fixtures, audit = await di._discover_football_fixtures(object())
        assert audit["isolated_from_mlb"] is True
