"""Phase F74 — Tests para floors granulares, schema canónico de
enriquecimiento y enriquecimiento TheStatsAPI con Dixon-Coles.

Cobertura:
  1. ``market_tolerance``:
     - Over 1.5 clasifica como PROTECTED.
     - ``get_protected_floor`` devuelve los pisos correctos
       (DC -4%, DNB -3%, U3.5 -3%, U4.5 -3%, O1.5 -2%, default -1.5%).
     - ``resolve_edge_floors`` propaga sub-family y watchlist coherente.

  2. ``moneyball_layer.classify_pick``:
     - UNKNOWN market_identity → ``MARKET_IDENTITY_MISSING`` +
       state ``REQUIRES_MARKET_IDENTIFICATION``.
     - Floors granulares activos (DC -2.5% → PROTECTED_ACCEPTABLE).

  3. ``football_data_enrichment.normalize_football_enrichment``:
     - Fusiona TheStatsAPI + Forebet + API-Sports (best-effort).
     - data_quality coherente con providers/xG.
     - ``requires_market_identity`` cuando market_identity es UNKNOWN.

  4. ``football_data_enrichment.attach_estimated_probability``:
     - Bloquea cuando data_quality es THIN.
     - Bloquea cuando identity_key es UNKNOWN.

  5. ``thestatsapi_football_enrichment.enrich_football_match_with_thestatsapi``:
     - Tier 1 Dixon-Coles cuando hay xG ambos lados.
     - Tier 3 logística observe-only con solo Forebet.
     - THIN → no inyecta nada.
     - UNKNOWN market_identity → no inyecta nada.
"""
from __future__ import annotations

import pytest

from services import market_tolerance as mt
from services import moneyball_layer as mb
from services import football_data_enrichment as fde
from services.external_sources import thestatsapi_football_enrichment as ts_fb


# ─────────────────────────────────────────────────────────────────────
# market_tolerance — clasificación + floors granulares
# ─────────────────────────────────────────────────────────────────────
class TestMarketToleranceF74:
    def test_over_1_5_is_protected(self):
        assert mt.classify_market_tolerance("Over 1.5") == mt.CATEGORY_PROTECTED
        assert mt.classify_market_tolerance("Más de 1.5") == mt.CATEGORY_PROTECTED
        assert mt.classify_market_tolerance("Más de 1.5", "Over") == mt.CATEGORY_PROTECTED

    def test_under_2_5_stays_balanced(self):
        # Under 2.5 sigue siendo BALANCED, no debemos haber tocado eso.
        assert mt.classify_market_tolerance("Under 2.5") == mt.CATEGORY_BALANCED

    def test_double_chance_protected(self):
        assert mt.classify_market_tolerance("Doble Oportunidad", "1X") == mt.CATEGORY_PROTECTED

    def test_dnb_protected(self):
        assert mt.classify_market_tolerance("Draw No Bet", "Home") == mt.CATEGORY_PROTECTED

    def test_get_protected_floor_double_chance(self):
        assert mt.get_protected_floor("Doble Oportunidad", "1X") == pytest.approx(-0.04)

    def test_get_protected_floor_dnb(self):
        assert mt.get_protected_floor("Draw No Bet", "Home") == pytest.approx(-0.03)

    def test_get_protected_floor_under_3_5(self):
        assert mt.get_protected_floor("Under 3.5", "Under") == pytest.approx(-0.03)

    def test_get_protected_floor_under_4_5(self):
        assert mt.get_protected_floor("Under 4.5", "Under") == pytest.approx(-0.03)

    def test_get_protected_floor_over_1_5(self):
        assert mt.get_protected_floor("Over 1.5", "Over") == pytest.approx(-0.02)

    def test_get_protected_floor_default(self):
        # Asian Handicap +0.5 es protected pero sin override granular.
        assert mt.get_protected_floor("Asian Handicap", "+0.5") == pytest.approx(-0.015)

    def test_get_protected_floor_via_market_identity(self):
        mi = {"family": "TOTAL_GOALS", "side": "OVER", "line": 1.5}
        assert mt.get_protected_floor(market_identity=mi) == pytest.approx(-0.02)

    def test_resolve_edge_floors_dc_granular(self):
        out = mt.resolve_edge_floors(
            mt.CATEGORY_PROTECTED, market="Doble Oportunidad", selection="1X",
        )
        assert out["protected_subfamily"] == "DOUBLE_CHANCE"
        assert out["negative_edge_floor"] == pytest.approx(-0.04)
        # watchlist está un escalón por debajo (default protegido tiene gap = -0.01).
        assert out["watchlist_floor"] == pytest.approx(-0.05)
        assert out["is_default"] is False

    def test_resolve_edge_floors_default_for_balanced(self):
        out = mt.resolve_edge_floors(mt.CATEGORY_BALANCED, market="Under 2.5")
        assert out["is_default"] is True
        assert out["protected_subfamily"] is None
        # Default balanced floor sigue siendo -0.01 (sin cambios).
        assert out["negative_edge_floor"] == pytest.approx(-0.01)


# ─────────────────────────────────────────────────────────────────────
# moneyball_layer.classify_pick — UNKNOWN guard + granular floors
# ─────────────────────────────────────────────────────────────────────
class TestMoneyballGuardsF74:
    def _common_kwargs(self, edge: float):
        return dict(
            edge=edge, threshold=0.03, bet_type="moneyline",
            confidence=70, fragility=40, overreaction=30,
            trap_signals=[], undervalued_signals=[],
            line_movement_favourable=False,
        )

    def test_unknown_market_identity_blocks_negative_edge(self):
        out = mb.classify_pick(
            **self._common_kwargs(-0.02),
            market_category=mt.CATEGORY_PROTECTED,
            market="?",
            market_identity={"identity_key": "UNKNOWN:RAW:???",
                              "family": None, "side": None},
        )
        assert out["classification"] == "MARKET_IDENTITY_MISSING"
        assert out["state"] == "REQUIRES_MARKET_IDENTIFICATION"
        assert "MARKET_IDENTITY_MISSING" in out["reason_codes"]
        assert "EDGE_CALCULATION_BLOCKED_UNKNOWN_MARKET" in out["reason_codes"]

    def test_unknown_market_identity_blocks_even_strong_trap_signals(self):
        out = mb.classify_pick(
            **self._common_kwargs(-0.10),
            market_category=mt.CATEGORY_PROTECTED,
            trap_signals=["a", "b", "c"],  # noqa: F841 — sobreescribiendo helper
            market="?",
            market_identity={"identity_key": "UNKNOWN:RAW:???",
                              "family": None, "side": None},
        ) if False else mb.classify_pick(  # noqa: E501 — keep formatting compact
            edge=-0.10, threshold=0.03, bet_type="moneyline",
            confidence=70, fragility=40, overreaction=30,
            trap_signals=["a", "b", "c"], undervalued_signals=[],
            line_movement_favourable=False,
            market_category=mt.CATEGORY_PROTECTED,
            market="?",
            market_identity={"identity_key": "UNKNOWN:RAW:?",
                              "family": None, "side": None},
        )
        # Aunque tengamos 3 trap signals, el guard de UNKNOWN debe ganar.
        assert out["classification"] == "MARKET_IDENTITY_MISSING"

    def test_dc_protected_floor_accepts_negative_2_5_pct(self):
        out = mb.classify_pick(
            **self._common_kwargs(-0.025),
            market_category=mt.CATEGORY_PROTECTED,
            market="Doble Oportunidad", selection="1X",
            market_identity={"identity_key": "DOUBLE_CHANCE:1X",
                              "family": "DOUBLE_CHANCE", "side": "1X"},
        )
        # DC floor es -4%, -2.5% queda dentro de tolerancia.
        assert out["classification"] == "PROTECTED_ACCEPTABLE"
        assert "DOUBLE_CHANCE" in out["reason"]

    def test_over_1_5_protected_floor_rejects_at_3_pct_negative(self):
        # Over 1.5 floor es -2%; -3% queda fuera (debe ir a WATCHLIST o NO_BET).
        out = mb.classify_pick(
            **self._common_kwargs(-0.03),
            market_category=mt.CATEGORY_PROTECTED,
            market="Over 1.5", selection="Over",
            market_identity={"identity_key": "TOTAL_GOALS:OVER:1.5",
                              "family": "TOTAL_GOALS", "side": "OVER", "line": 1.5},
        )
        # Está justo en el watchlist_floor (-3%) — debe caer en WATCHLIST.
        assert out["classification"] in ("WATCHLIST", "PROTECTED_ACCEPTABLE")
        # -2% no se respeta como floor estricto cuando edge==-3% (porque
        # -3% < -2%) → la lógica debe RECHAZAR PROTECTED_ACCEPTABLE.
        if out["classification"] == "PROTECTED_ACCEPTABLE":
            pytest.fail("Over 1.5 con edge -3% NO debería ser PROTECTED_ACCEPTABLE "
                        "porque su floor granular es -2%.")

    def test_dnb_floor_propagates_in_reason(self):
        out = mb.classify_pick(
            **self._common_kwargs(-0.025),
            market_category=mt.CATEGORY_PROTECTED,
            market="Draw No Bet", selection="Home",
            market_identity={"identity_key": "DNB:HOME",
                              "family": "DNB", "side": "HOME"},
        )
        # DNB floor -3%, -2.5% acepta.
        assert out["classification"] == "PROTECTED_ACCEPTABLE"
        assert "DNB" in out["reason"]


# ─────────────────────────────────────────────────────────────────────
# football_data_enrichment.normalize_football_enrichment
# ─────────────────────────────────────────────────────────────────────
class TestCanonicalFootballEnrichment:
    def test_empty_match_is_thin(self):
        c = fde.normalize_football_enrichment({})
        assert c["data_quality"] == fde.DQ_THIN
        assert c["providers_used"] == []
        assert fde.RC_NO_PROVIDERS in c["reason_codes"]

    def test_thestatsapi_xg_with_forebet_is_strong(self):
        match = {
            "_thestatsapi_enrichment": {
                "team_stats": {
                    "home": {"expected_goals_per_match": 1.6},
                    "away": {"expected_goals_per_match": 1.1},
                },
            },
            "_forebet_prediction": {
                "probabilities": {"home": 50, "draw": 25, "away": 25},
                "predicted_score": "2-1",
                "goals_avg": 3.0,
            },
        }
        c = fde.normalize_football_enrichment(match)
        assert c["data_quality"] == fde.DQ_STRONG
        assert c["xg"]["home"] == 1.6 and c["xg"]["away"] == 1.1
        assert "thestatsapi_enrichment" in c["providers_used"]
        assert "forebet" in c["providers_used"]
        assert c["external_context"]["forebet"]["predicted_score"] == "2-1"

    def test_only_forebet_is_limited(self):
        match = {"_forebet_prediction": {
            "probabilities": {"home": 60, "draw": 20, "away": 20},
            "predicted_score": "2-0", "goals_avg": 2.4,
        }}
        c = fde.normalize_football_enrichment(match)
        assert c["data_quality"] == fde.DQ_LIMITED
        assert c["xg"]["home"] is None and c["xg"]["away"] is None

    def test_unknown_market_identity_flags_requires_mi(self):
        match = {"_thestatsapi_enrichment": {
            "team_stats": {
                "home": {"expected_goals_per_match": 1.4},
                "away": {"expected_goals_per_match": 1.2},
            },
        }}
        mi = {"identity_key": "UNKNOWN:RAW:?", "family": None, "side": None}
        c = fde.normalize_football_enrichment(match, market_identity=mi)
        assert c["requires_market_identity"] is True
        assert fde.RC_REQUIRES_MARKET_IDENTITY in c["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# attach_estimated_probability — guards de calidad/identity
# ─────────────────────────────────────────────────────────────────────
class TestAttachEstimatedProbability:
    def test_blocks_on_thin(self):
        c = fde.normalize_football_enrichment({})
        ok = fde.attach_estimated_probability(
            c, "DOUBLE_CHANCE:1X",
            probability=0.7, method="DIXON_COLES",
        )
        assert ok is False
        assert c["estimated_probabilities"] == {}
        assert fde.RC_PROBABILITIES_BLOCKED_THIN in c["reason_codes"]

    def test_blocks_on_unknown_key(self):
        match = {"_thestatsapi_enrichment": {
            "team_stats": {
                "home": {"expected_goals_per_match": 1.4},
                "away": {"expected_goals_per_match": 1.2},
            },
        }}
        c = fde.normalize_football_enrichment(match)
        ok = fde.attach_estimated_probability(
            c, "UNKNOWN:RAW:?",
            probability=0.7, method="DIXON_COLES",
        )
        assert ok is False
        assert c["requires_market_identity"] is True

    def test_attaches_when_strong(self):
        match = {
            "_thestatsapi_enrichment": {
                "team_stats": {
                    "home": {"expected_goals_per_match": 1.6},
                    "away": {"expected_goals_per_match": 1.1},
                },
            },
            "_forebet_prediction": {
                "probabilities": {"home": 50, "draw": 25, "away": 25},
            },
        }
        c = fde.normalize_football_enrichment(match)
        ok = fde.attach_estimated_probability(
            c, "DOUBLE_CHANCE:1X",
            probability=0.78, method="DIXON_COLES", quality="STRONG",
        )
        assert ok is True
        assert c["estimated_probabilities"]["DOUBLE_CHANCE:1X"]["p"] == pytest.approx(0.78)
        assert c["estimated_probabilities"]["DOUBLE_CHANCE:1X"]["method"] == "DIXON_COLES"


# ─────────────────────────────────────────────────────────────────────
# thestatsapi_football_enrichment.enrich_football_match_with_thestatsapi
# ─────────────────────────────────────────────────────────────────────
class TestTheStatsAPIFootballEnrichment:
    def _strong_match(self):
        return {
            "_thestatsapi_enrichment": {
                "team_stats": {
                    "home": {"expected_goals_per_match": 1.6},
                    "away": {"expected_goals_per_match": 1.1},
                },
            },
            "_forebet_prediction": {
                "probabilities": {"home": 50, "draw": 25, "away": 25},
                "predicted_score": "2-1", "goals_avg": 3.0,
            },
        }

    def test_dixon_coles_tier_when_both_xg(self):
        c = ts_fb.enrich_football_match_with_thestatsapi(self._strong_match())
        probs = c["estimated_probabilities"]
        assert "1X2:HOME" in probs and probs["1X2:HOME"]["method"] == "DIXON_COLES"
        assert probs["1X2:HOME"]["quality"] == "STRONG"
        # Sumas coherentes (1X2 ≈ 1).
        s = probs["1X2:HOME"]["p"] + probs["1X2:DRAW"]["p"] + probs["1X2:AWAY"]["p"]
        assert s == pytest.approx(1.0, rel=1e-2)
        # Over 2.5 + Under 2.5 ≈ 1.
        s2 = probs["TOTAL_GOALS:OVER:2.5"]["p"] + probs["TOTAL_GOALS:UNDER:2.5"]["p"]
        assert s2 == pytest.approx(1.0, abs=1e-3)

    def test_poisson_when_dc_disabled(self):
        c = ts_fb.enrich_football_match_with_thestatsapi(
            self._strong_match(), prefer_dixon_coles=False,
        )
        probs = c["estimated_probabilities"]
        assert probs["1X2:HOME"]["method"] == "POISSON"

    def test_observe_only_when_no_xg(self):
        match = {"_forebet_prediction": {
            "probabilities": {"home": 60, "draw": 20, "away": 20},
            "predicted_score": "2-0", "goals_avg": 2.4,
        }}
        c = ts_fb.enrich_football_match_with_thestatsapi(match)
        probs = c["estimated_probabilities"]
        assert probs["1X2:HOME"]["method"] == "LOGISTIC_OBSERVE_ONLY"
        assert probs["1X2:HOME"]["quality"] == "OBSERVE_ONLY"
        # No debe haber TOTAL_GOALS porque la logística observe-only solo
        # cubre 1X2 / DC.
        assert "TOTAL_GOALS:OVER:2.5" not in probs

    def test_thin_blocks_all_probabilities(self):
        c = ts_fb.enrich_football_match_with_thestatsapi({})
        assert c["data_quality"] == fde.DQ_THIN
        assert c["estimated_probabilities"] == {}

    def test_unknown_market_identity_blocks_all_probabilities(self):
        mi = {"identity_key": "UNKNOWN:RAW:?", "family": None, "side": None}
        c = ts_fb.enrich_football_match_with_thestatsapi(
            self._strong_match(), market_identity=mi,
        )
        assert c["requires_market_identity"] is True
        assert c["estimated_probabilities"] == {}
