"""Phase F82.1 + F83 — Tests for non-blocking enrichment + manual market identity.

F82.1: corners provider must NOT call 365Scores inline (gateway timeout fix).
F83  : manual market identity recalculation endpoint + validator.
"""
from __future__ import annotations

import pytest

from services import football_corners_provider as cp
from services import manual_market_identity as mmi


# ─────────────────────────────────────────────────────────────────────
# F82.1 — fast tier
# ─────────────────────────────────────────────────────────────────────
class TestF82_1_FastTier:
    @pytest.mark.asyncio
    async def test_fast_tier_does_not_call_365scores(self, monkeypatch):
        called = {"n": 0}

        async def fake_extract_365(client, match_doc, *, allow_name_resolver=True, timeout_s=None):
            called["n"] += 1
            return None, []
        monkeypatch.setattr(cp, "_extract_365scores_corners", fake_extract_365)

        # Match WITHOUT API-Sports / TheStatsAPI corners.
        match_doc = {"match_id": "mid-fast-1"}
        result = await cp.enrich_match_corners_fast(None, None, match_doc)
        assert result["available"] is False
        assert called["n"] == 0, "365Scores must not be called in fast tier"
        assert cp.RC_365_SKIPPED_INLINE in result["reason_codes"]

    @pytest.mark.asyncio
    async def test_external_tier_does_call_365scores(self, monkeypatch):
        called = {"n": 0}

        async def fake_extract_365(client, match_doc, *, allow_name_resolver=True, timeout_s=None):
            called["n"] += 1
            return None, [cp.RC_NO_365_ID]
        monkeypatch.setattr(cp, "_extract_365scores_corners", fake_extract_365)

        result = await cp.enrich_match_corners_external(None, None, {"match_id": "mid-ext"})
        assert called["n"] == 1
        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_365scores_timeout_does_not_crash(self, monkeypatch):
        """If 365Scores takes longer than FOOTBALL_365SCORES_TIMEOUT_MS, the
        provider must return gracefully with RC_365_TIMEOUT — not raise."""
        import asyncio
        async def slow_fake(client, match_doc, *, allow_name_resolver=True, timeout_s=None):
            await asyncio.sleep(10)  # would normally timeout
            return None, []
        # Don't monkey the wrapper — replace the inner _do_fetch behavior
        # by stubbing the score365_client functions.
        from services.external_sources import score365_client as s365
        monkeypatch.setattr(s365, "resolve_game_id_from_match_doc",
                             lambda m: ("mt_x", None))
        async def slow_fetch(*a, **k):
            await asyncio.sleep(10)
            return {}
        monkeypatch.setattr(s365, "fetch_game_stats", slow_fetch)
        monkeypatch.setattr(s365, "fetch_game_data",  slow_fetch)

        result, codes = await cp._extract_365scores_corners(
            None, {"match_id": "mid-slow"}, timeout_s=0.05,
        )
        assert result is None
        assert cp.RC_365_TIMEOUT in codes

    def test_feature_flags_default(self):
        # Defaults: inline disabled, background enabled.
        assert cp.is_inline_365scores_enabled() is False
        assert cp.is_background_365scores_enabled() is True
        assert cp.score365_timeout_seconds() > 0
        assert cp.corners_fast_timeout_seconds() > 0


# ─────────────────────────────────────────────────────────────────────
# F83 — manual market identity validator
# ─────────────────────────────────────────────────────────────────────
class TestF83_Validator:
    def test_rejects_unknown_market_type(self):
        ok, err = mmi.validate_manual_payload({
            "market_type": "RANDOM", "selection": "X",
            "manual_odd": 1.5,
        })
        assert not ok
        assert "market_type" in err

    def test_rejects_invalid_selection(self):
        ok, err = mmi.validate_manual_payload({
            "market_type": "TOTAL_GOALS", "selection": "INVALID",
            "manual_odd": 1.5, "line": 2.5,
        })
        assert not ok
        assert "selection" in err

    def test_rejects_missing_line_when_required(self):
        ok, err = mmi.validate_manual_payload({
            "market_type": "TOTAL_GOALS", "selection": "OVER",
            "manual_odd": 1.5,
        })
        assert not ok
        assert "line" in err

    def test_rejects_odd_below_1_01(self):
        ok, err = mmi.validate_manual_payload({
            "market_type": "DOUBLE_CHANCE", "selection": "1X",
            "manual_odd": 0.95,
        })
        assert not ok
        assert "1.01" in err

    def test_accepts_valid_dc(self):
        ok, err = mmi.validate_manual_payload({
            "market_type": "DOUBLE_CHANCE", "selection": "1X",
            "manual_odd": 1.30,
        })
        assert ok
        assert err is None

    def test_accepts_valid_total_goals(self):
        ok, err = mmi.validate_manual_payload({
            "market_type": "TOTAL_GOALS", "selection": "UNDER",
            "manual_odd": 1.85, "line": 3.5,
        })
        assert ok

    def test_accepts_corners_total(self):
        ok, err = mmi.validate_manual_payload({
            "market_type": "CORNERS_TOTAL", "selection": "OVER",
            "manual_odd": 1.92, "line": 9.5,
        })
        assert ok

    def test_rejects_line_not_in_allowed_list(self):
        ok, err = mmi.validate_manual_payload({
            "market_type": "TOTAL_GOALS", "selection": "OVER",
            "manual_odd": 1.85, "line": 7.5,  # 7.5 not in allowed_lines for TOTAL_GOALS
        })
        assert not ok


class TestF83_Recalculation:
    def test_recalculate_total_goals_under(self):
        result = mmi.recalculate_with_manual_market({
            "market_type": "TOTAL_GOALS", "selection": "UNDER",
            "manual_odd": 1.38, "line": 3.5,
        })
        mmi_block = result["manual_market_identity"]
        assert mmi_block["market_type"] == "TOTAL_GOALS"
        assert mmi_block["selection"] == "UNDER"
        assert mmi_block["line"] == 3.5
        assert mmi_block["identity_key"] == "TOTAL_GOALS:UNDER:3.5"
        rec = result["recalculated_pick"]
        assert "Under 3.5" in rec["recommended_market"]
        assert rec["tolerance_category"] == "protected"
        # FIX-NEW-2: without a base_pick exposing model probability, we
        # MUST NOT fabricate an edge. The honest contract returns
        # MODEL_PROBABILITY_UNAVAILABLE so the UI shows "cuota saved but
        # no edge calculable". This replaces the historical bug where
        # the heuristic ``implied_prob * 1.05`` always produced a fake
        # positive edge (even 1.01 looked favorable).
        assert rec["status"] == "MODEL_PROBABILITY_UNAVAILABLE"
        assert rec["manual_edge"] is None
        assert rec["model_probability"] is None
        assert rec["model_prob_source"] == "missing"
        assert "warnings" in result and len(result["warnings"]) >= 2

    def test_recalculate_total_goals_under_with_base_pick(self):
        """When base_pick exposes a real model probability, the honest
        edge IS computed. This is the post-FIX-NEW-2 positive path."""
        base_pick = {
            "_market_edge": {"estimated_probability": 0.78},
            "_moneyball":   {"confidence": 70,
                             "fragility": {"score": 22}},
        }
        result = mmi.recalculate_with_manual_market({
            "market_type": "TOTAL_GOALS", "selection": "UNDER",
            "manual_odd": 1.38, "line": 3.5,
        }, base_pick=base_pick)
        rec = result["recalculated_pick"]
        # Implied = 1/1.38 ≈ 72.46%; model = 78% → edge ≈ +5.54%.
        assert rec["model_prob_source"] == "base_pick"
        assert rec["model_probability"] == 78.0
        assert rec["manual_edge"] > 0
        assert rec["status"] in ("MANUAL_VALUE_REVIEW", "MANUAL_THIN_VALUE")

    def test_recalculate_double_chance(self):
        result = mmi.recalculate_with_manual_market({
            "market_type": "DOUBLE_CHANCE", "selection": "1X",
            "manual_odd": 1.24,
        })
        assert result["manual_market_identity"]["identity_key"] == "DOUBLE_CHANCE:1X"
        assert result["recalculated_pick"]["tolerance_category"] == "protected"
        assert "Doble Oportunidad 1X" in result["recalculated_pick"]["recommended_market"]

    def test_recalculate_btts(self):
        result = mmi.recalculate_with_manual_market({
            "market_type": "BTTS", "selection": "NO",
            "manual_odd": 1.80,
        })
        assert result["manual_market_identity"]["line"] is None
        assert result["manual_market_identity"]["identity_key"] == "BTTS:NO"
