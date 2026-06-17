"""Sprint-B · Tests for Fix 1 (scheduler jobs), Fix 2 (real
TheStatsAPI adapter), Fix 3 (CONCACAF/CAF hydrator)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from services.football_learning_snapshot_jobs import (
    _kickoff_in_window,
    PRE_MATCH_CREATE_MIN_SEC,
    PRE_MATCH_CREATE_MAX_SEC,
    PRE_MATCH_REFRESH_MAX_SEC,
)
from services.football_concacaf_caf_hydrator import (
    RC_QUALIFIER_PROXY,
    CONFED_DEFAULTS,
    hydrate_debutant_proxy,
    adapter_concacaf_caf_hydrator,
    _lookup_confed,
)
from services.football_pre_match_data_aggregator import (
    SRC_CONCACAF_CAF_HYDR,
    _default_adapter_chain,
)


# ═══════════════════════════════════════════════════════════════════
# Fix 1 — Scheduler jobs
# ═══════════════════════════════════════════════════════════════════
class TestKickoffInWindow:
    def _match(self, hours_ahead: float) -> dict:
        return {"kickoff_at":
                datetime.now(timezone.utc) + timedelta(hours=hours_ahead)}

    def test_inside_2_to_6h_window(self):
        m = self._match(3.5)
        assert _kickoff_in_window(m, min_sec=2*3600, max_sec=6*3600) is True

    def test_outside_2_to_6h_window_too_late(self):
        m = self._match(0.5)
        assert _kickoff_in_window(m, min_sec=2*3600, max_sec=6*3600) is False

    def test_outside_2_to_6h_window_too_early(self):
        m = self._match(8.0)
        assert _kickoff_in_window(m, min_sec=2*3600, max_sec=6*3600) is False

    def test_window_30min_to_60min_is_inside_refresh_window(self):
        m = self._match(0.75)
        assert _kickoff_in_window(m, min_sec=0,
                                   max_sec=PRE_MATCH_REFRESH_MAX_SEC) is True

    def test_iso_string_kickoff_is_accepted(self):
        ko = datetime.now(timezone.utc) + timedelta(hours=4)
        m = {"kickoff_at": ko.isoformat()}
        assert _kickoff_in_window(m, min_sec=PRE_MATCH_CREATE_MIN_SEC,
                                   max_sec=PRE_MATCH_CREATE_MAX_SEC) is True

    def test_garbage_kickoff_returns_false(self):
        assert _kickoff_in_window({"kickoff_at": "not-a-date"},
                                    min_sec=0, max_sec=10000) is False
        assert _kickoff_in_window({}, min_sec=0, max_sec=10000) is False
        assert _kickoff_in_window(None, min_sec=0, max_sec=10000) is False


# ═══════════════════════════════════════════════════════════════════
# Fix 2 — TheStatsAPI real fetcher: averaging helper
# ═══════════════════════════════════════════════════════════════════
class TestThestatsapiAvg:
    def test_avg_basic(self):
        from services.external_sources.thestatsapi_pre_match_summary import _avg
        assert _avg([1.0, 2.0, 3.0], 3) == 2.0

    def test_avg_empty(self):
        from services.external_sources.thestatsapi_pre_match_summary import _avg
        assert _avg([], 5) is None

    def test_avg_skips_garbage(self):
        from services.external_sources.thestatsapi_pre_match_summary import _avg
        assert _avg([1.0, "x", 2.0, None], 5) == 1.5

    def test_avg_caps_at_k(self):
        from services.external_sources.thestatsapi_pre_match_summary import _avg
        # 5 values, k=2 → avg of first 2.
        assert _avg([4, 6, 100, 100, 100], 2) == 5.0


@pytest.mark.asyncio
class TestThestatsapiAdapter_RealFetcher:
    async def test_returns_empty_when_module_disabled(self):
        """When TheStatsAPI is disabled, the function must short-circuit
        with ``{}`` so the aggregator records FAILED and moves on."""
        from services.external_sources import thestatsapi_pre_match_summary as mod
        with patch.object(mod, "fetch_match_pre_match_summary",
                           new=AsyncMock(return_value={})):
            from services.football_pre_match_data_aggregator import (
                _adapter_thestatsapi,
            )
            data, status = await _adapter_thestatsapi(
                "France", "Senegal", 42,
            )
            assert data == {}
            assert status == "FAILED"

    async def test_returns_complete_when_all_core_fields_filled(self):
        from services.football_pre_match_data_aggregator import (
            _adapter_thestatsapi,
        )
        with patch(
            "services.external_sources.thestatsapi_pre_match_summary."
            "fetch_match_pre_match_summary",
            new=AsyncMock(return_value={
                "home_xg_l5": 2.1, "away_xg_l5": 1.4,
                "home_corners_l5": 6.2, "away_corners_l5": 5.1,
            }),
        ):
            data, status = await _adapter_thestatsapi(
                "France", "Senegal", 42,
            )
            assert status == "COMPLETE"
            assert data["home_xg_l5"] == 2.1
            assert data["home_corners_l5"] == 6.2

    async def test_returns_partial_when_only_some_core_fields(self):
        from services.football_pre_match_data_aggregator import (
            _adapter_thestatsapi,
        )
        with patch(
            "services.external_sources.thestatsapi_pre_match_summary."
            "fetch_match_pre_match_summary",
            new=AsyncMock(return_value={
                "home_xg_l5": 2.1, "away_xg_l5": 1.4,
                # No corners.
            }),
        ):
            data, status = await _adapter_thestatsapi(
                "France", "Senegal", 42,
            )
            assert status == "PARTIAL"
            assert data["home_xg_l5"] == 2.1


# ═══════════════════════════════════════════════════════════════════
# Fix 3 — CONCACAF/CAF hydrator
# ═══════════════════════════════════════════════════════════════════
class TestConfedLookup:
    def test_curacao_is_concacaf(self):
        assert _lookup_confed("Curacao") == "CONCACAF"
        assert _lookup_confed("Curaçao") == "CONCACAF"

    def test_cabo_verde_is_caf(self):
        assert _lookup_confed("Cabo Verde") == "CAF"
        assert _lookup_confed("Cape Verde") == "CAF"

    def test_jordan_is_afc(self):
        assert _lookup_confed("Jordan") == "AFC"

    def test_unknown_country_returns_none(self):
        assert _lookup_confed("Atlantis") is None
        assert _lookup_confed(None) is None
        assert _lookup_confed("") is None


class TestHydrateDebutantProxy:
    def test_caf_debutant_gets_proxy_values(self):
        out = hydrate_debutant_proxy(home_team="Spain",
                                       away_team="Cabo Verde")
        # Only the AWAY side (Cabo Verde, CAF) gets a proxy.
        assert out["away_xg_l5"] == CONFED_DEFAULTS["CAF"]["xg_for"]
        assert out["away_corners_l5"] == CONFED_DEFAULTS["CAF"]["corners_for"]
        # Home side (Spain) is NOT in the table → no proxy.
        assert "home_xg_l5" not in out
        assert out["_provenance"]["reason_code"] == RC_QUALIFIER_PROXY
        assert out["_provenance"]["away_confed"] == "CAF"
        assert out["_provenance"]["home_confed"] is None

    def test_concacaf_debutant_curacao_gets_proxy(self):
        out = hydrate_debutant_proxy(home_team="Curacao",
                                       away_team="Germany")
        assert out["home_xg_l5"] == CONFED_DEFAULTS["CONCACAF"]["xg_for"]
        assert out["home_corners_l5"] == CONFED_DEFAULTS["CONCACAF"]["corners_for"]
        assert out["_provenance"]["home_confed"] == "CONCACAF"

    def test_both_sides_are_debutants(self):
        out = hydrate_debutant_proxy(home_team="Cabo Verde",
                                       away_team="Curacao")
        assert out["home_xg_l5"] == CONFED_DEFAULTS["CAF"]["xg_for"]
        assert out["away_xg_l5"] == CONFED_DEFAULTS["CONCACAF"]["xg_for"]

    def test_no_national_teams_returns_empty(self):
        out = hydrate_debutant_proxy(home_team="Real Madrid",
                                       away_team="Barcelona")
        assert out == {}

    def test_two_unknown_nationals_returns_empty(self):
        # Italy / France are nationals but not in the table → empty
        out = hydrate_debutant_proxy(home_team="Italy",
                                       away_team="France")
        assert out == {}


@pytest.mark.asyncio
class TestConcacafCafAdapterStatus:
    async def test_adapter_partial_when_debutant_present(self):
        data, status = await adapter_concacaf_caf_hydrator(
            "Spain", "Cabo Verde", 42,
        )
        # Cabo Verde is CAF → adapter returns PARTIAL with priors.
        assert status == "PARTIAL"
        assert data["away_xg_l5"] is not None
        assert data["_provenance"]["reason_code"] == RC_QUALIFIER_PROXY

    async def test_adapter_failed_when_no_debutant(self):
        data, status = await adapter_concacaf_caf_hydrator(
            "Spain", "Brazil", 42,
        )
        assert status == "FAILED"
        assert data == {}


# ═══════════════════════════════════════════════════════════════════
# Default adapter chain includes CONCACAF/CAF as the 4th source.
# ═══════════════════════════════════════════════════════════════════
class TestCascadeChainIncludesConcacafCaf:
    def test_chain_contains_4_adapters_with_concacaf_caf_last(self):
        chain = _default_adapter_chain()
        names = [n for n, _ in chain]
        assert names[0] == "thestatsapi"
        assert names[1] == "api_sports"
        assert names[2] == "scrape_do"
        assert names[-1] == SRC_CONCACAF_CAF_HYDR
        assert SRC_CONCACAF_CAF_HYDR == "concacaf_caf_hydrator"


# ═══════════════════════════════════════════════════════════════════
# End-to-end: aggregator falls through to CONCACAF/CAF hydrator when
# upstream sources fail for a debutant match.
# ═══════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestAggregatorE2E:
    async def test_debutant_falls_through_to_concacaf_caf(self):
        from services.football_pre_match_data_aggregator import (
            gather_pre_match_data,
        )
        async def _fail(*_a, **_kw):
            return {}, "FAILED"
        # All 3 primary sources fail. Hydrator covers Cabo Verde
        # because it's a CAF national team.
        from services.football_concacaf_caf_hydrator import (
            adapter_concacaf_caf_hydrator,
        )
        chain = [
            ("thestatsapi", _fail),
            ("api_sports",  _fail),
            ("scrape_do",   _fail),
            ("concacaf_caf_hydrator", adapter_concacaf_caf_hydrator),
        ]
        result = await gather_pre_match_data(
            home_team="Spain", away_team="Cabo Verde", match_id=42,
            adapters=chain,
        )
        # PARTIAL status because not all CORE fields are populated
        # (BTTS / Over 2.5 probabilities are still None).
        assert result["status"] == "PARTIAL"
        assert result["inputs"]["away_xg_l5"] is not None
        assert result["inputs"]["away_corners_l5"] is not None
        # Audit trail recorded the hydrator step.
        sources = [s["source"] for s in result["source_audit"]["pre_match_sources"]]
        assert "concacaf_caf_hydrator" in sources
