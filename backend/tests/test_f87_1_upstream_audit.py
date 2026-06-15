"""F87.1 Parte 1.5 — Upstream discovery audit (adapter + contract).

Six obligatory tests covering the upstream visibility requirements:

1. ``test_thestatsapi_adapter_does_not_return_empty_when_api_has_matches``
2. ``test_contract_accepts_competitors_array_home_away``
3. ``test_contract_accepts_homeTeam_awayTeam_shape``
4. ``test_contract_rejection_audit_includes_dropped_samples``
5. ``test_discovery_debug_reports_adapter_empty_vs_contract_rejected``
6. ``test_no_matches_message_not_used_when_raw_fixtures_were_rejected``
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import data_ingestion as di
from services import football_fixture_contract as ffc
from services.external_sources import thestatsapi_fixtures_adapter as ts_fx


def _future_ts(hours: float = 2.0) -> tuple[int, str]:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return int(dt.timestamp()), dt.isoformat()


# =====================================================================
# 1. Adapter does not silently return empty when API has matches
# =====================================================================
class TestAdapterDoesNotReturnEmptyWhenAPIHasMatches:
    def test_thestatsapi_adapter_does_not_return_empty_when_api_has_matches(self):
        ts, iso = _future_ts()
        raw_rows = [
            {
                "id":        f"ts-{i}",
                "timestamp": ts + i * 60,
                "status":    {"short": "scheduled"},
                "league":    {"id": 39, "name": "Premier League",
                              "country": "England"},
                "teams":     {"home": {"id": 1, "name": f"Home{i}"},
                              "away": {"id": 2, "name": f"Away{i}"}},
            }
            for i in range(5)
        ]
        out = [ts_fx._normalise_fixture(r) for r in raw_rows]
        out = [o for o in out if o is not None]
        assert len(out) == 5, (
            f"Adapter dropped fixtures despite valid raw input: kept={len(out)}"
        )
        # And every output is contract-compatible.
        for o in out:
            fx = ffc.ensure_api_football_fixture_shape(o, source="thestatsapi")
            assert fx is not None
            assert fx["teams"]["home"]["name"].startswith("Home")
            assert fx["teams"]["away"]["name"].startswith("Away")


# =====================================================================
# 2. Contract accepts competitors[] array (ESPN-style)
# =====================================================================
class TestContractAcceptsCompetitorsArray:
    def test_contract_accepts_competitors_array_home_away(self):
        ts, iso = _future_ts()
        raw = {
            "id":   "espn-1",
            "date": iso,
            "league": {"name": "MLS", "country": "United States"},
            "competitors": [
                {"name": "LAFC",         "homeAway": "home", "home": True},
                {"name": "Inter Miami",  "homeAway": "away", "home": False},
            ],
        }
        codes: list[str] = []
        fx = ffc.ensure_api_football_fixture_shape(raw, source="espn",
                                                    reason_codes=codes)
        assert fx is not None, f"Contract rejected competitors[] (codes={codes})"
        assert fx["teams"]["home"]["name"] == "LAFC"
        assert fx["teams"]["away"]["name"] == "Inter Miami"

    def test_contract_accepts_competitors_index_fallback(self):
        """When ``competitors`` lacks home/away markers, [0]→home, [1]→away."""
        ts, iso = _future_ts()
        raw = {
            "id":   "vendor-1",
            "date": iso,
            "league": {"name": "Some League"},
            "competitors": [
                {"name": "Team A"},
                {"name": "Team B"},
            ],
        }
        fx = ffc.ensure_api_football_fixture_shape(raw, source="vendor")
        assert fx is not None
        assert fx["teams"]["home"]["name"] == "Team A"
        assert fx["teams"]["away"]["name"] == "Team B"


# =====================================================================
# 3. Contract accepts Sofascore homeTeam/awayTeam shape
# =====================================================================
class TestContractAcceptsHomeTeamAwayTeamShape:
    def test_contract_accepts_homeTeam_awayTeam_shape(self):
        ts, iso = _future_ts()
        raw = {
            "id":             "sofa-raw-1",
            "startTimestamp": ts,
            "tournament":     {"name": "La Liga"},
            "homeTeam":       {"name": "Real Madrid"},
            "awayTeam":       {"name": "FC Barcelona"},
        }
        codes: list[str] = []
        fx = ffc.ensure_api_football_fixture_shape(raw, source="sofascore",
                                                    reason_codes=codes)
        assert fx is not None, f"Contract rejected raw Sofascore (codes={codes})"
        assert fx["teams"]["home"]["name"] == "Real Madrid"
        assert fx["teams"]["away"]["name"] == "FC Barcelona"
        assert fx["fixture"]["timestamp"] == ts


# =====================================================================
# 4. Contract rejection audit includes dropped_samples with full evidence
# =====================================================================
class TestContractRejectionAuditIncludesDroppedSamples:
    def test_contract_rejection_audit_includes_dropped_samples(self):
        bucket = [
            # Valid — kept.
            {"id": "ok-1", "timestamp": _future_ts()[0],
             "teams": {"home": {"name": "H1"}, "away": {"name": "A1"}},
             "league": {"name": "L"}},
            # Invalid — no teams anywhere.
            {"id": "bad-1", "timestamp": _future_ts()[0],
             "league": {"name": "L"}},
            # Invalid — empty team blocks.
            {"id": "bad-2", "timestamp": _future_ts()[0],
             "teams": {"home": {"name": ""}, "away": {"name": "OnlyAway"}},
             "league": {"name": "L"}},
        ]
        out, audit = ffc.normalize_bucket(bucket, source="thestatsapi")
        assert audit["raw_count"]     == 3
        assert audit["kept_count"]    == 1
        assert audit["dropped_count"] == 2
        # dropped_samples (cap 3) with rich evidence.
        samples = audit["dropped_samples"]
        assert isinstance(samples, list)
        assert 1 <= len(samples) <= audit["dropped_samples_cap"]
        for s in samples:
            assert "home_candidates"    in s
            assert "away_candidates"    in s
            assert "kickoff_candidates" in s
            assert "reason_code"        in s
            assert s["reason_code"] == ffc.RC_INVALID_MISSING_TEAMS
            # The home_candidates dict MUST list at least the canonical
            # nested paths we probed.
            assert "teams.home.name"   in s["home_candidates"]
            assert "teams.away.name"   in s["away_candidates"]

    def test_dropped_samples_cap_is_respected(self, monkeypatch):
        monkeypatch.setenv("DISCOVERY_DROPPED_SAMPLE_CAP", "2")
        bad_bucket = [{"id": f"x{i}"} for i in range(10)]   # all invalid
        out, audit = ffc.normalize_bucket(bad_bucket, source="x")
        assert audit["dropped_count"]         == 10
        assert audit["dropped_samples_shown"] == 2
        assert audit["dropped_samples_cap"]   == 2
        assert len(audit["dropped_samples"])  == 2


# =====================================================================
# 5. Discovery debug distinguishes "adapter empty" vs "contract rejected"
# =====================================================================
class TestDiscoveryDebugReportsAdapterEmptyVsContractRejected:
    @pytest.mark.asyncio
    async def test_discovery_debug_reports_adapter_empty_vs_contract_rejected(
            self, monkeypatch):
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        monkeypatch.setenv("F87_MIN_VIABLE_COUNT", "100")  # force merge path

        # Case A — TheStatsAPI returns empty raw.
        # Case B — API-Football returns 5 raw rows that all fail the contract.
        ts_ko, _iso = _future_ts()
        bad_af_rows = [
            {"fixture": {"id": f"af-{i}", "timestamp": ts_ko,
                          "status": {"short": "NS"}},
             "league":  {"id": 39, "name": "PL"},
             "teams":   {"home": {"name": ""}, "away": {"name": ""}}}
            for i in range(5)
        ]
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=([], ["THESTATSAPI_FIXTURES_EMPTY"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=bad_af_rows)), \
             patch("services.data_ingestion.fb.espn_soccer_scoreboard",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])):
            fixtures, audit = await di._discover_football_fixtures(object())

        assert fixtures == []
        ts_audit = audit["shape_audit"]["thestatsapi"]
        af_audit = audit["shape_audit"]["api_football"]

        # Case A: thestatsapi raw_count=0 → adapter_returned_empty=True.
        assert ts_audit["raw_count"]              == 0
        assert ts_audit["adapter_returned_empty"] is True
        assert ts_audit["had_raw_but_all_rejected"] is False
        assert ts_audit["reason_codes"].get(ffc.RC_ADAPTER_RETURNED_EMPTY) == 1

        # Case B: api_football raw_count=5, kept=0 → had_raw_but_all_rejected.
        assert af_audit["raw_count"]              == 5
        assert af_audit["kept_count"]             == 0
        assert af_audit["had_raw_but_all_rejected"] is True
        assert af_audit["adapter_returned_empty"] is False
        assert af_audit["top_reason"] == ffc.RC_INVALID_MISSING_TEAMS
        # And we captured dropped_samples (cap default 3).
        assert af_audit["dropped_samples_shown"] >= 1


# =====================================================================
# 6. UI message must distinguish "rejected by contract" vs "no matches"
# =====================================================================
class TestNoMatchesMessageNotUsedWhenRawFixturesRejected:
    @pytest.mark.asyncio
    async def test_no_matches_message_not_used_when_raw_fixtures_were_rejected(
            self, monkeypatch):
        """Hit /api/football/discovery/debug after a run where raw>0 but
        contract rejected everything. The endpoint MUST surface a
        message saying fixtures were rejected by contract, NOT "no
        hay partidos"."""
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        monkeypatch.setenv("F87_MIN_VIABLE_COUNT", "100")

        ts_ko, _ = _future_ts()
        bad_af_rows = [
            {"fixture": {"id": f"af-{i}", "timestamp": ts_ko,
                          "status": {"short": "NS"}},
             "league":  {"id": 39, "name": "PL"},
             "teams":   {"home": {"name": ""}, "away": {"name": ""}}}
            for i in range(4)
        ]
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=([], ["EMPTY"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=bad_af_rows)), \
             patch("services.data_ingestion.fb.espn_soccer_scoreboard",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])):
            await di._discover_football_fixtures(object())

        # Build the response payload the way server.py does.
        from server import football_discovery_debug as _endpoint  # type: ignore

        class _FakeUser(dict):
            pass

        payload = await _endpoint(user=_FakeUser({"sub": "test"}), refresh=False)
        assert payload["ok"] is True
        assert payload["ran"] is True
        # Raw>0 but normalised=0 → had_raw_but_all_rejected.
        assert payload["raw_total"]        == 4
        assert payload["normalised_total"] == 0
        assert payload["had_raw_but_all_rejected"] is True
        # UI message MUST NOT be the "no hay partidos" copy.
        msg = (payload.get("ui_message") or "").lower()
        assert "no hay partidos" not in msg
        assert "rechaz" in msg or "contract" in msg, (
            f"Expected rejected/contract phrasing, got: {payload.get('ui_message')!r}"
        )

    @pytest.mark.asyncio
    async def test_ui_message_when_all_adapters_empty(self, monkeypatch):
        """When raw_total==0 across all adapters the message says the
        adapters returned 0 fixtures (not silent failure)."""
        monkeypatch.delenv("ENABLE_THESTATSAPI_FIXTURES_PRIMARY", raising=False)
        with patch("services.external_sources.thestatsapi_fixtures_adapter.fetch_fixtures_next_48h",
                    AsyncMock(return_value=([], ["EMPTY"]))), \
             patch("services.data_ingestion.af.fixtures_next_48h",
                    AsyncMock(return_value=[])), \
             patch("services.data_ingestion.fb.espn_soccer_scoreboard",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.sofascore_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])), \
             patch("services.external_sources.scrapedo_fixtures_adapter.fetch_fixtures_today",
                    AsyncMock(return_value=[])):
            await di._discover_football_fixtures(object())

        from server import football_discovery_debug as _endpoint  # type: ignore
        payload = await _endpoint(user={"sub": "test"}, refresh=False)
        assert payload["raw_total"]        == 0
        assert payload["normalised_total"] == 0
        assert payload["had_raw_but_all_rejected"] is False
        assert payload["any_adapter_returned_empty"] is True
        msg = (payload.get("ui_message") or "").lower()
        assert "0 fixtures" in msg or "0 partidos" in msg or "empty" in msg or \
               "devolvieron 0" in msg
