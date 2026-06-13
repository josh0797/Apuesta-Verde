"""Phase F83.1 — Tests for data_availability sections + manual-market
match_id robustness.

Covers the two production bugs the user reported:

  * "body.match_id: Input should be a valid string" — caused by the
    frontend sending a number/null; the endpoint now coerces and the
    Pydantic validator gives a precise error.
  * "xG disponible vs xG faltante" contradiction — the legacy
    ``internal_analysis_debug.thestatsapi_found`` flag was true whenever
    ``football_data_enrichment`` was present, even if xG itself was not
    normalised. The new ``data_availability.sections`` map exposes
    distinct states.
"""
from __future__ import annotations

import pytest

from services.football_data_availability import (
    has_xg_available, has_thestatsapi_available, has_h2h_available,
    has_corners_l5_l15_available, has_market_identity_available,
    has_recent_form_available, build_data_availability_sections,
)
from services.football_editorial_payload_adapter import (
    build_editorial_ready_match_payload,
)


# ─────────────────────────────────────────────────────────────────────
# has_xg_available — only true when an actual home/away pair exists
# ─────────────────────────────────────────────────────────────────────
class TestHasXgAvailable:
    def test_returns_true_when_fde_xg_present(self):
        assert has_xg_available({
            "football_data_enrichment": {"xg": {"home": 1.28, "away": 0.94}},
        }) is True

    def test_returns_true_when_thestatsapi_snapshot_xg_present(self):
        assert has_xg_available({
            "thestatsapi_snapshot": {"xg": {"home": 1.1, "away": 0.9}},
        }) is True

    def test_returns_true_when_live_stats_xg_present(self):
        assert has_xg_available({
            "live_stats": {"xg_home": 1.5, "xg_away": 1.2},
        }) is True

    def test_returns_false_when_fde_exists_but_no_xg(self):
        """The contradiction we are fixing: ``football_data_enrichment``
        is present but the ``xg`` sub-block has no actual values."""
        assert has_xg_available({
            "football_data_enrichment": {"team_stats": {}, "source": "thestatsapi"},
        }) is False

    def test_returns_false_when_xg_missing_one_side(self):
        assert has_xg_available({
            "football_data_enrichment": {"xg": {"home": 1.28, "away": None}},
        }) is False

    def test_returns_false_on_empty_match(self):
        assert has_xg_available({}) is False
        assert has_xg_available(None) is False  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────
# build_data_availability_sections — no contradictions
# ─────────────────────────────────────────────────────────────────────
class TestBuildDataAvailabilitySections:
    def test_thestatsapi_present_but_xg_missing_marks_normalization(self):
        """The user's exact contradiction case."""
        match = {
            "football_data_enrichment": {
                "source":      "thestatsapi",
                "team_stats":  {"home": {"goals_avg": 1.4}, "away": {"goals_avg": 1.1}},
            },
            "h2h_recent": [{"date": "2024-01-01", "result": "1-1"}],
        }
        out = build_data_availability_sections(match)
        sec = out["sections"]
        # TheStatsAPI is available.
        assert sec["thestatsapi"]["available"] is True
        # xG explicitly says "missing normalization", NOT "available".
        assert sec["xg"]["available"] is False
        assert sec["xg"]["status"] == "MISSING_NORMALIZATION"
        # The missing list must include xG.
        assert "xG" in out["missing_sections"]
        assert "XG_NOT_NORMALIZED" in out["missing_codes"]
        # h2h still available — partial rendering ok.
        assert sec["h2h"]["available"] is True

    def test_full_availability(self):
        match = {
            "football_data_enrichment": {
                "source": "thestatsapi",
                "xg":     {"home": 1.28, "away": 0.94},
            },
            "h2h_context": {"available": True},
            "combined_football_corner_profile_cross": {
                "available": True,
                "home": {"corners_for_l5": 4.5, "corners_for_l15": 4.2},
                "away": {"corners_for_l5": 3.1, "corners_for_l15": 3.5},
            },
            "market_identity": {"identity_key": "MATCH_WINNER:HOME"},
            "recent_fixtures": [{"id": "x"}],
        }
        out = build_data_availability_sections(match)
        sec = out["sections"]
        assert sec["xg"]["available"]              is True
        assert sec["thestatsapi"]["available"]     is True
        assert sec["h2h"]["available"]             is True
        assert sec["corners"]["available"]         is True
        assert sec["market_identity"]["available"] is True
        assert out["missing_sections"] == []

    def test_market_identity_unknown_marks_required(self):
        match = {"market_identity": {"identity_key": "UNKNOWN:1.25"}}
        out = build_data_availability_sections(match)
        assert out["sections"]["market_identity"]["available"] is False
        assert out["sections"]["market_identity"]["status"] == "REQUIRES_MANUAL_INPUT"
        assert "market_identity" in out["missing_sections"]

    def test_h2h_recent_list_marks_available(self):
        match = {"h2h_recent": [{"date": "2024-01-01", "score": "2-1"}]}
        out = build_data_availability_sections(match)
        assert out["sections"]["h2h"]["available"] is True


# ─────────────────────────────────────────────────────────────────────
# editorial adapter integration — no contradiction
# ─────────────────────────────────────────────────────────────────────
class TestEditorialAdapterIntegration:
    def test_debug_block_carries_sections_map(self):
        match = {
            "football_data_enrichment": {
                "source":     "thestatsapi",
                "team_stats": {},
            },
            "h2h_recent": [{"date": "2024-01-01"}],
        }
        payload = build_editorial_ready_match_payload(match)
        dbg = payload.get("internal_analysis_debug") or {}
        sections = dbg.get("sections") or {}
        # Sections map is present.
        assert sections, "internal_analysis_debug.sections must be populated"
        # xG explicit MISSING_NORMALIZATION (TheStatsAPI present, xG not).
        assert sections.get("xg", {}).get("status") == "MISSING_NORMALIZATION"
        # No xG in "available_sections" but TheStatsAPI is.
        avail = dbg.get("available_sections") or []
        assert "xg" not in avail
        assert "thestatsapi" in avail
        # xG should appear in missing_sections.
        miss = dbg.get("missing_sections") or []
        assert "xG" in miss

    def test_top_level_data_availability_field(self):
        match = {"football_data_enrichment": {"xg": {"home": 1.1, "away": 0.9}}}
        payload = build_editorial_ready_match_payload(match)
        assert isinstance(payload.get("data_availability"), dict)
        assert "sections" in payload["data_availability"]


# ─────────────────────────────────────────────────────────────────────
# Manual market reprice — match_id coercion + robustness
# ─────────────────────────────────────────────────────────────────────
class TestManualMarketRepriceMatchIdCoercion:
    @pytest.mark.asyncio
    async def test_numeric_match_id_is_accepted(self, monkeypatch):
        """A numeric match_id (sent by the frontend without explicit
        ``String(...)``) must be coerced to a stripped string and the
        endpoint must respond 200."""
        from server import manual_market_reprice_endpoint, ManualMarketRepriceRequest

        # Bypass the DB lookup — we only care that the endpoint accepts
        # the payload and returns a SUCCESS-shaped dict.
        async def _empty_find(*a, **k): return None

        class _FakeColl:
            find_one = staticmethod(_empty_find)

        class _FakeDB:
            analyst_runs = _FakeColl()

        import server
        monkeypatch.setattr(server, "db", _FakeDB())

        payload = ManualMarketRepriceRequest(
            match_id=12345,           # ← numeric, not a string
            detected_odd=2.10,
            manual_odd=2.10,
            market_type="DOUBLE_CHANCE",
            selection="1X",
        )
        # Pydantic must have coerced it to a string.
        assert isinstance(payload.match_id, str)
        assert payload.match_id == "12345"

        result = await manual_market_reprice_endpoint(payload)
        assert result["match_id"] == "12345"
        assert isinstance(result.get("manual_market_identity"), dict)
        assert result["manual_market_identity"]["detected_odd"] == 2.10

    def test_null_match_id_raises_value_error(self):
        from pydantic import ValidationError
        from server import ManualMarketRepriceRequest

        with pytest.raises(ValidationError):
            ManualMarketRepriceRequest(
                match_id=None,
                manual_odd=1.5, market_type="DOUBLE_CHANCE", selection="1X",
            )

    def test_blank_string_match_id_raises_value_error(self):
        from pydantic import ValidationError
        from server import ManualMarketRepriceRequest

        with pytest.raises(ValidationError):
            ManualMarketRepriceRequest(
                match_id="  ",
                manual_odd=1.5, market_type="DOUBLE_CHANCE", selection="1X",
            )

    def test_undefined_literal_string_raises_value_error(self):
        from pydantic import ValidationError
        from server import ManualMarketRepriceRequest

        with pytest.raises(ValidationError):
            ManualMarketRepriceRequest(
                match_id="undefined",
                manual_odd=1.5, market_type="DOUBLE_CHANCE", selection="1X",
            )
