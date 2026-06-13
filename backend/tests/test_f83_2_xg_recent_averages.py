"""Phase F83.2-E5 — Tests for the xG recent-averages stack.

Covers four layers, all with mocked ``httpx.AsyncClient`` transports
(no real network calls). The suite must pass even when
``THESTATSAPI_KEY`` is unset in CI — we set it via the autouse fixture.

Layers under test
-----------------
1. ``services.external_sources.thestatsapi_shotmap_client.fetch_shotmap_xg``
   - resolution order (stored → live → data[] fallback)
   - sanity-cap / NaN coercion
   - fail-soft on 4xx/5xx/timeouts
2. ``services.football_xg_recent_averages.compute_xg_recent_averages``
   - L1 / L5 / L15 aggregation per side
   - partial samples → ``partial=True``
   - cache hit short-circuits the second call
   - no-recent-match-ids → ``available=False``
3. ``services.football_xg_signals.derive_xg_signals``
   - LOW_RECENT_XG_PROFILE, XG_APOYA_UNDER, XG_APOYA_OVER, XG_FORM_SHIFT,
     DEFENSIVE_XG_SUPPRESSION (existing signals)
   - NEW partial-sample signals: XG_PARTIAL_SAMPLE, XG_L1_ONLY,
     XG_L5_AVAILABLE_L15_MISSING, XG_L15_AVAILABLE_L5_MISSING,
     XG_RECENT_SAMPLE_INSUFFICIENT
4. server endpoints ``/api/football/xg-recent-averages/{run-now,background,status}``
   - request validation (numeric / blank / null match_id)
   - run-now path returns SUCCESS shape on hit
   - run-now returns MATCH_NOT_FOUND when DB has nothing
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from services.external_sources import thestatsapi_shotmap_client as sm_client
from services import football_xg_recent_averages as agg
from services import football_xg_signals as sig


# ─────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────
def _mock_client(handler):
    """Build a httpx.AsyncClient backed by a MockTransport."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _enable_thestatsapi(monkeypatch):
    monkeypatch.setenv("THESTATSAPI_KEY", "test-fake-key")
    monkeypatch.setenv("THESTATSAPI_BASE_URL", "https://api.thestatsapi.com/api")
    monkeypatch.setenv("ENABLE_THE_STATS_API", "true")
    # Reset the in-memory cache between tests so the second test does
    # not see the first test's payload.
    agg.reset_cache()
    yield
    agg.reset_cache()


# =====================================================================
# Layer 1 — thestatsapi_shotmap_client.fetch_shotmap_xg
# =====================================================================
class TestFetchShotmapStored:
    @pytest.mark.asyncio
    async def test_returns_stored_summary_when_present(self):
        payload = {
            "home_team": {"id": 100},
            "away_team": {"id": 200},
            "np_xg_summary": {
                "stored": {"home_team": 1.42, "away_team": 0.94},
                "live":   {"home_team": 0.0,  "away_team": 0.0},
            },
            "data": [],
        }

        def handler(req: httpx.Request) -> httpx.Response:
            assert "/football/matches/mt_500/shotmap" in req.url.path
            return httpx.Response(200, json=payload)

        async with _mock_client(handler) as c:
            out = await sm_client.fetch_shotmap_xg(c, "mt_500")
        assert out["available"] is True
        assert out["home_np_xg"] == 1.42
        assert out["away_np_xg"] == 0.94
        assert out["home_team_id"] == 100
        assert out["away_team_id"] == 200
        assert sm_client.RC_FROM_STORED in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_dict_shaped_team_blocks_are_parsed(self):
        """When team_blocks are nested dicts carrying np_xg / xg keys."""
        payload = {
            "home_team_id": 1, "away_team_id": 2,
            "np_xg_summary": {
                "stored": {
                    "home_team": {"np_xg": 1.10},
                    "away_team": {"expected_goals": 0.80},
                },
            },
        }

        def handler(req): return httpx.Response(200, json=payload)
        async with _mock_client(handler) as c:
            out = await sm_client.fetch_shotmap_xg(c, "mt_42")
        assert out["available"] is True
        assert out["home_np_xg"] == 1.10
        assert out["away_np_xg"] == 0.80


class TestFetchShotmapLiveFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_live_when_stored_missing(self):
        payload = {
            "home_team_id": 9, "away_team_id": 10,
            "np_xg_summary": {
                "stored": {},  # empty → triggers live
                "live":   {"home_team": 0.55, "away_team": 1.20},
            },
        }

        def handler(req): return httpx.Response(200, json=payload)
        async with _mock_client(handler) as c:
            out = await sm_client.fetch_shotmap_xg(c, "mt_88")
        assert out["available"] is True
        assert sm_client.RC_FROM_LIVE in out["reason_codes"]
        assert out["home_np_xg"] == 0.55
        assert out["away_np_xg"] == 1.20


class TestFetchShotmapDataFallback:
    @pytest.mark.asyncio
    async def test_sums_non_penalty_xg_per_team(self):
        """When both summary blocks are empty, sum data[] rows by team_id
        and skip is_penalty=True rows."""
        payload = {
            "home_team_id": 7, "away_team_id": 8,
            "np_xg_summary": {"stored": {}, "live": {}},
            "data": [
                {"team_id": 7, "expected_goals": 0.40, "is_penalty": False},
                {"team_id": 7, "expected_goals": 0.30, "is_penalty": False},
                # Penalty must be ignored.
                {"team_id": 7, "expected_goals": 0.76, "is_penalty": True},
                {"team_id": 8, "expected_goals": 0.20, "is_penalty": False},
                {"team_id": 8, "expected_goals": 0.50, "is_penalty": False},
            ],
        }

        def handler(req): return httpx.Response(200, json=payload)
        async with _mock_client(handler) as c:
            out = await sm_client.fetch_shotmap_xg(c, "mt_99")
        assert out["available"] is True
        assert sm_client.RC_FROM_FALLBACK in out["reason_codes"]
        # 0.40 + 0.30 = 0.70 (penalty excluded)
        assert out["home_np_xg"] == 0.70
        assert out["away_np_xg"] == 0.70


class TestFetchShotmapFailSoft:
    @pytest.mark.asyncio
    async def test_returns_unavailable_on_404(self):
        def handler(req): return httpx.Response(404, json={"error": "nope"})
        async with _mock_client(handler) as c:
            out = await sm_client.fetch_shotmap_xg(c, "mt_x")
        assert out["available"] is False
        assert sm_client.RC_NO_SHOTMAP in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_returns_unavailable_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_THE_STATS_API", "false")
        async with _mock_client(lambda r: httpx.Response(200, json={})) as c:
            out = await sm_client.fetch_shotmap_xg(c, "mt_x")
        assert out["available"] is False
        assert "THESTATSAPI_DISABLED" in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_blank_match_id_short_circuits(self):
        out = await sm_client.fetch_shotmap_xg(None, "")
        assert out["available"] is False
        assert "MATCH_ID_MISSING" in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_insane_xg_value_is_rejected_as_unavailable(self):
        # >12 is rejected by _coerce_xg → returns None → unavailable.
        payload = {
            "home_team_id": 1, "away_team_id": 2,
            "np_xg_summary": {"stored": {"home_team": 999.0, "away_team": 0.5}},
        }

        def handler(req): return httpx.Response(200, json=payload)
        async with _mock_client(handler) as c:
            out = await sm_client.fetch_shotmap_xg(c, "mt_bad")
        # Both halves must coerce; only home is None → falls back, also empty.
        assert out["available"] is False


# =====================================================================
# Layer 2 — compute_xg_recent_averages
# =====================================================================
def _make_shotmap_payload(home_id: int, away_id: int,
                          home_xg: float, away_xg: float) -> dict:
    return {
        "home_team_id": home_id, "away_team_id": away_id,
        "np_xg_summary": {"stored": {"home_team": home_xg,
                                      "away_team": away_xg}},
        "data": [],
    }


def _build_match_doc(home_recent_ids, away_recent_ids,
                     home_id=100, away_id=200) -> dict:
    return {
        "match_id": "TARGET_1",
        "home_team": {
            "id":   home_id,
            "name": "Home FC",
            "thestatsapi_recent_match_ids": home_recent_ids,
        },
        "away_team": {
            "id":   away_id,
            "name": "Away FC",
            "thestatsapi_recent_match_ids": away_recent_ids,
        },
    }


class TestComputeRecentAveragesHappyPath:
    @pytest.mark.asyncio
    async def test_aggregates_l1_l5_l15_for_both_sides(self):
        """Home team played 15 matches, away team played 15. All home
        matches credit home_team_id; all away matches credit away_team_id
        as the home side."""
        # 15 fixtures per side, xG always 1.0 for the team we care about.
        def handler(req: httpx.Request) -> httpx.Response:
            mid = req.url.path.rsplit("/", 2)[-2]  # …/matches/<id>/shotmap
            # The recent ids for home start with "h_", for away with "a_".
            if mid.startswith("h_"):
                # Home team is on the home side, xG=1.0; opponent=0.5.
                return httpx.Response(200, json=_make_shotmap_payload(
                    home_id=100, away_id=999, home_xg=1.0, away_xg=0.5))
            if mid.startswith("a_"):
                # Away team was the home side in their recent fixture,
                # xG=2.0; opponent=0.4.
                return httpx.Response(200, json=_make_shotmap_payload(
                    home_id=200, away_id=888, home_xg=2.0, away_xg=0.4))
            return httpx.Response(404, json={})

        match = _build_match_doc(
            home_recent_ids=[f"h_{i}" for i in range(15)],
            away_recent_ids=[f"a_{i}" for i in range(15)],
        )

        async with _mock_client(handler) as c:
            out = await agg.compute_xg_recent_averages(match, client=c)

        assert out["available"] is True
        assert out["partial"] is False
        assert out["source"] == "thestatsapi_shotmap"

        h = out["home"]
        assert h["team"] == "Home FC"
        assert h["l1"]["xg_for_avg"]     == 1.0
        assert h["l1"]["xg_against_avg"] == 0.5
        assert h["l5"]["xg_for_avg"]     == 1.0
        assert h["l5"]["sample_size"]    == 5
        assert h["l15"]["xg_for_avg"]    == 1.0
        assert h["l15"]["sample_size"]   == 15

        a = out["away"]
        assert a["l5"]["xg_for_avg"]    == 2.0
        assert a["l15"]["xg_for_avg"]   == 2.0
        assert a["l1"]["xg_against_avg"] == 0.4

    @pytest.mark.asyncio
    async def test_cache_short_circuits_second_call(self):
        """Calling twice with the same match_id only hits the network once."""
        call_counter = {"n": 0}

        def handler(req):
            call_counter["n"] += 1
            return httpx.Response(200, json=_make_shotmap_payload(
                100, 200, 1.0, 0.5))

        match = _build_match_doc(["h_0", "h_1"], ["a_0", "a_1"])
        async with _mock_client(handler) as c:
            out1 = await agg.compute_xg_recent_averages(match, client=c)
            calls_after_first = call_counter["n"]
            out2 = await agg.compute_xg_recent_averages(match, client=c)
        # First call ran shotmap fetches; second call MUST be served
        # from the in-process cache.
        assert calls_after_first > 0
        assert call_counter["n"] == calls_after_first
        assert out1 is out2 or out1 == out2


class TestComputeRecentAveragesPartial:
    @pytest.mark.asyncio
    async def test_partial_sample_when_l5_missing_on_one_side(self):
        """When one side has zero usable shotmaps, the other side
        produces L1/L5/L15 but the aggregate is flagged partial."""
        def handler(req):
            mid = req.url.path.rsplit("/", 2)[-2]
            if mid.startswith("a_"):
                # Only the away side returns usable data.
                return httpx.Response(200, json=_make_shotmap_payload(
                    200, 888, 1.8, 0.6))
            # Home shotmaps are 404 → no rows aggregated.
            return httpx.Response(404, json={})

        match = _build_match_doc(
            home_recent_ids=[f"h_{i}" for i in range(5)],
            away_recent_ids=[f"a_{i}" for i in range(15)],
        )
        async with _mock_client(handler) as c:
            out = await agg.compute_xg_recent_averages(match, client=c)
        assert out["available"] is True
        assert out["partial"] is True
        # Home payload should have no windows.
        h = out["home"]
        assert h["l1"] is None and h["l5"] is None and h["l15"] is None
        # Away payload should carry all windows.
        a = out["away"]
        assert a["l1"] is not None
        assert a["l5"] is not None
        assert a["l15"] is not None

    @pytest.mark.asyncio
    async def test_no_recent_ids_returns_unavailable(self):
        match = _build_match_doc([], [])
        out = await agg.compute_xg_recent_averages(match)
        assert out["available"] is False
        # Reason must include the no-ids code.
        assert agg.RC_NO_RECENT_MATCH_IDS in (
            out.get("reason_codes") or []
            + (out.get("home", {}) or {}).get("reason_codes", [])
            + (out.get("away", {}) or {}).get("reason_codes", [])
        ) or any(
            agg.RC_NO_RECENT_MATCH_IDS in (side.get("reason_codes") or [])
            for side in (out.get("home") or {}, out.get("away") or {})
        )

    @pytest.mark.asyncio
    async def test_invalid_match_doc_returns_build_failed(self):
        out = await agg.compute_xg_recent_averages("not-a-dict")  # type: ignore[arg-type]
        assert out["available"] is False
        assert agg.RC_BUILD_FAILED in out["reason_codes"]


# =====================================================================
# Layer 3 — derive_xg_signals (existing + NEW partial signals)
# =====================================================================
def _xg_payload(
    home_l1=None, home_l5=None, home_l15=None,
    away_l1=None, away_l5=None, away_l15=None,
    available=True, partial=False,
) -> dict:
    def _w(for_v, ag_v):
        if for_v is None and ag_v is None:
            return None
        return {"xg_for_avg": for_v, "xg_against_avg": ag_v, "sample_size": 5}
    return {
        "available": available,
        "partial":   partial,
        "home": {
            "team": "H",
            "l1":   _w(*home_l1)  if home_l1  else None,
            "l5":   _w(*home_l5)  if home_l5  else None,
            "l15":  _w(*home_l15) if home_l15 else None,
        },
        "away": {
            "team": "A",
            "l1":   _w(*away_l1)  if away_l1  else None,
            "l5":   _w(*away_l5)  if away_l5  else None,
            "l15":  _w(*away_l15) if away_l15 else None,
        },
    }


class TestExistingSignals:
    def test_low_recent_xg_profile_emitted(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=(1.10, 1.05), home_l15=(1.20, 1.10),
            away_l5=(1.00, 1.10), away_l15=(1.10, 1.10),
        ))
        assert sig.LOW_RECENT_XG_PROFILE in out["signals"]

    def test_xg_apoya_over_emitted(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=(1.70, 1.20), home_l15=(1.50, 1.20),
            away_l5=(1.40, 1.20), away_l15=(1.40, 1.20),
        ))
        assert sig.XG_APOYA_OVER in out["signals"]

    def test_xg_apoya_under_emitted(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=(1.00, 1.10), home_l15=(1.10, 1.10),
            away_l5=(1.20, 1.10), away_l15=(1.20, 1.10),
        ))
        assert sig.XG_APOYA_UNDER in out["signals"]

    def test_defensive_xg_suppression(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=(0.80, 0.90), away_l5=(0.90, 0.95),
            home_l15=(1.00, 1.00), away_l15=(1.00, 1.00),
        ))
        assert sig.DEFENSIVE_XG_SUPPRESSION in out["signals"]

    def test_unavailable_input_returns_empty(self):
        out = sig.derive_xg_signals({"available": False})
        assert out["signals"] == []
        out2 = sig.derive_xg_signals(None)  # type: ignore[arg-type]
        assert out2["signals"] == []


class TestPartialSampleSignals:
    """Phase F83.2-E5 — partial-sample / coverage signals."""

    def test_xg_partial_sample_when_aggregator_flags_partial(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=(1.0, 1.0),  away_l5=(1.0, 1.0),
            home_l15=None,       away_l15=None,    # ← L15 missing
            partial=True,
        ))
        assert sig.XG_PARTIAL_SAMPLE in out["signals"]
        # And the granular code that says exactly which window survives.
        assert sig.XG_L5_AVAILABLE_L15_MISSING in out["signals"]

    def test_xg_l1_only_when_only_l1_present_both_sides(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l1=(1.0, 0.5), away_l1=(1.2, 0.7),
            partial=True,
        ))
        assert sig.XG_L1_ONLY in out["signals"]
        # Sample-insufficient must also fire when L5 and L15 are missing
        # on BOTH sides (which is the same condition).
        assert sig.XG_RECENT_SAMPLE_INSUFFICIENT in out["signals"]
        # Crucially, Over/Under support signals must NOT fire when only
        # L1 is available.
        assert sig.XG_APOYA_OVER  not in out["signals"]
        assert sig.XG_APOYA_UNDER not in out["signals"]

    def test_xg_l5_available_l15_missing(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=(1.0, 1.0),  away_l5=(1.0, 1.0),
            home_l15=None,       away_l15=None,
            partial=True,
        ))
        assert sig.XG_L5_AVAILABLE_L15_MISSING in out["signals"]
        assert sig.XG_L15_AVAILABLE_L5_MISSING not in out["signals"]

    def test_xg_l15_available_l5_missing(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=None,         away_l5=None,
            home_l15=(1.2, 1.0),  away_l15=(1.1, 1.0),
            partial=True,
        ))
        assert sig.XG_L15_AVAILABLE_L5_MISSING in out["signals"]
        assert sig.XG_L5_AVAILABLE_L15_MISSING not in out["signals"]

    def test_xg_recent_sample_insufficient_when_no_window_pair(self):
        """Neither L5 nor L15 covers both sides."""
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=(1.0, 1.0), away_l5=None,       # asymmetric
            home_l15=None,      away_l15=(1.0, 1.0),
            partial=True,
        ))
        assert sig.XG_RECENT_SAMPLE_INSUFFICIENT in out["signals"]

    def test_no_partial_signals_when_full_coverage(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l1=(1.0, 0.5),  away_l1=(1.0, 0.5),
            home_l5=(1.0, 1.0),  away_l5=(1.0, 1.0),
            home_l15=(1.0, 1.0), away_l15=(1.0, 1.0),
            partial=False,
        ))
        assert sig.XG_PARTIAL_SAMPLE             not in out["signals"]
        assert sig.XG_L1_ONLY                    not in out["signals"]
        assert sig.XG_L5_AVAILABLE_L15_MISSING   not in out["signals"]
        assert sig.XG_L15_AVAILABLE_L5_MISSING   not in out["signals"]
        assert sig.XG_RECENT_SAMPLE_INSUFFICIENT not in out["signals"]

    def test_coverage_metrics_are_exposed(self):
        out = sig.derive_xg_signals(_xg_payload(
            home_l5=(1.0, 1.0), away_l5=(1.0, 1.0),
            home_l15=None,      away_l15=None,
            partial=True,
        ))
        coverage = (out.get("metrics") or {}).get("coverage") or {}
        assert coverage.get("l5_both") is True
        assert coverage.get("l15_both") is False


# =====================================================================
# Layer 4 — server endpoints
# =====================================================================
class TestXGRecentAveragesRequestValidation:
    def test_numeric_match_id_is_coerced_to_string(self):
        from server import XGRecentAveragesRequest

        req = XGRecentAveragesRequest(match_id=12345)
        assert isinstance(req.match_id, str)
        assert req.match_id == "12345"

    def test_blank_match_id_rejected(self):
        from pydantic import ValidationError
        from server import XGRecentAveragesRequest

        with pytest.raises(ValidationError):
            XGRecentAveragesRequest(match_id="   ")

    def test_null_match_id_rejected(self):
        from pydantic import ValidationError
        from server import XGRecentAveragesRequest

        with pytest.raises(ValidationError):
            XGRecentAveragesRequest(match_id=None)


class TestXGRunNowEndpoint:
    @pytest.mark.asyncio
    async def test_match_not_found_returns_clean_error(self, monkeypatch):
        """If the match_id cannot be located in analyst_runs, the
        endpoint MUST return a fail-soft dict — never raise."""
        import server

        async def _none_loader(mid):
            return None
        monkeypatch.setattr(server, "_load_match_doc_for_corners", _none_loader)

        result = await server._do_xg_recent_fetch("UNKNOWN_MID")
        assert result["available"] is False
        assert result["status"] == "MATCH_NOT_FOUND"
        assert "MATCH_NOT_FOUND" in result["reason_codes"]

    @pytest.mark.asyncio
    async def test_run_now_wires_signals_into_result(self, monkeypatch):
        """When compute returns data, the endpoint must enrich the
        result with the derived signals + explanations + metrics."""
        import server

        match_doc = _build_match_doc(["h_0"], ["a_0"])

        async def _fake_loader(mid):
            return match_doc

        async def _fake_compute(doc, *, client=None, timeout_s=6.0, use_cache=True):
            # Mimic a partial / L1-only output.
            return {
                "available": True,
                "partial":   True,
                "source":    "thestatsapi_shotmap",
                "home": {"team": "H", "l1": {"xg_for_avg": 1.0,
                                              "xg_against_avg": 0.5,
                                              "sample_size": 1},
                          "l5": None, "l15": None, "reason_codes": []},
                "away": {"team": "A", "l1": {"xg_for_avg": 1.2,
                                              "xg_against_avg": 0.7,
                                              "sample_size": 1},
                          "l5": None, "l15": None, "reason_codes": []},
                "reason_codes": [agg.RC_FROM_SHOTMAP, agg.RC_PARTIAL_SAMPLE],
            }

        async def _fake_persist(mid, snap): return None

        monkeypatch.setattr(server, "_load_match_doc_for_corners", _fake_loader)
        monkeypatch.setattr(agg, "compute_xg_recent_averages", _fake_compute)
        monkeypatch.setattr(server, "_persist_xg_recent_to_run", _fake_persist)

        out = await server._do_xg_recent_fetch("TARGET_1")
        assert out["available"] is True
        assert out["status"] == "SUCCESS"
        # Signals enriched on top.
        assert sig.XG_L1_ONLY in (out.get("signals") or [])
        # No Over/Under support emitted with only L1.
        assert sig.XG_APOYA_OVER  not in (out.get("signals") or [])
        assert sig.XG_APOYA_UNDER not in (out.get("signals") or [])
        # Explanations are surfaced.
        assert isinstance(out.get("explanations"), dict)
