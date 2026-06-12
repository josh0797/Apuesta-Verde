"""Phase F74-post v2.5 — Tests for opening odds → line movement wiring.

Cubre:
  1. Resolución canónica de mercados ES/EN (Goles totales → Goals Over/Under).
  2. Resolución de selecciones (Local→Home, Más de 2.5→Over 2.5, Sí→Yes).
  3. Búsqueda de opening_odds en `_opening_odds` dict y current_odds en
     `bookmakers[].bets[].values[]`.
  4. Cálculo de line_movement integrado.
  5. Mutación correcta de `pick["key_data"]["line_movement"]` (forma
     legacy que `moneyball_layer.analyze_pick` lee).
  6. Iteración sobre `parsed["picks"]` con cross a `matches_payload`.
  7. Fail-soft con inputs malformados / faltantes.
"""
from __future__ import annotations

import pytest

from services.opening_odds_movement import (
    attach_line_movement_from_opening_odds,
    enrich_picks_with_opening_movement,
    _resolve_canonical_market,
    _resolve_selection_value,
    _opening_odds_for,
    _current_odds_for,
)


# ─────────────────────────────────────────────────────────────────────
# 1) Resolución canónica de mercados
# ─────────────────────────────────────────────────────────────────────
class TestCanonicalMarketResolution:
    @pytest.mark.parametrize("raw,expected", [
        ("Match Winner",       "Match Winner"),
        ("match winner",       "Match Winner"),
        ("1X2",                "Match Winner"),
        ("Moneyline",          "Match Winner"),
        ("Goals Over/Under",   "Goals Over/Under"),
        ("Total Goals",        "Goals Over/Under"),
        ("Goles totales",      "Goals Over/Under"),
        ("Both Teams Score",   "Both Teams Score"),
        ("BTTS",               "Both Teams Score"),
        ("Ambos equipos anotan", "Both Teams Score"),
        ("Corners",            "Corners Over/Under"),
        ("Asian Handicap",     "Asian Handicap"),
    ])
    def test_known_markets_normalised(self, raw, expected):
        assert _resolve_canonical_market(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", None, "Random Market", "Doble Oportunidad",  # DC not in our keymap
    ])
    def test_unknown_markets_return_none(self, raw):
        assert _resolve_canonical_market(raw) is None


# ─────────────────────────────────────────────────────────────────────
# 2) Resolución de selecciones (incluye normalización ES/EN)
# ─────────────────────────────────────────────────────────────────────
class TestSelectionResolution:
    @pytest.mark.parametrize("market,sel,expected", [
        ("Match Winner", "Home",  "Home"),
        ("Match Winner", "Local", "Home"),
        ("Match Winner", "1",     "Home"),
        ("Match Winner", "Draw",  "Draw"),
        ("Match Winner", "Empate","Draw"),
        ("Match Winner", "X",     "Draw"),
        ("Match Winner", "Away",  "Away"),
        ("Match Winner", "Visitante", "Away"),
        ("Match Winner", "2",     "Away"),
        ("Both Teams Score", "Yes", "Yes"),
        ("Both Teams Score", "Sí",  "Yes"),
        ("Both Teams Score", "No",  "No"),
        ("Goals Over/Under", "Over 2.5", "Over 2.5"),
        ("Goals Over/Under", "Más de 2.5", "Over 2.5"),
        ("Goals Over/Under", "Menos de 3.5", "Under 3.5"),
        ("Asian Handicap", "Home", "Home"),
        ("Asian Handicap", "Local", "Home"),
    ])
    def test_known_selections_normalised(self, market, sel, expected):
        assert _resolve_selection_value(market, sel) == expected

    def test_over_with_separate_line(self):
        assert _resolve_selection_value("Goals Over/Under", "Over", line=2.5) == "Over 2.5"
        assert _resolve_selection_value("Goals Over/Under", "Más", line=2.5) == "Over 2.5"


# ─────────────────────────────────────────────────────────────────────
# 3) Búsqueda en _opening_odds y bookmakers[]
# ─────────────────────────────────────────────────────────────────────
class TestOpeningCurrentOddsLookup:
    OPENING_MAP = {
        "Pinnacle|Match Winner|Home": 2.10,
        "Pinnacle|Match Winner|Draw": 3.40,
        "Bet365|Match Winner|Home":   2.08,    # otro bookmaker, lower
        "Pinnacle|Goals Over/Under|Over 2.5": 1.85,
    }

    def test_picks_best_opening_across_bookmakers(self):
        # Pinnacle 2.10 > Bet365 2.08 → debe elegir Pinnacle.
        assert _opening_odds_for(self.OPENING_MAP, "Match Winner", "Home") == 2.10

    def test_returns_none_if_market_absent(self):
        assert _opening_odds_for(self.OPENING_MAP, "Match Winner", "Away") is None

    def test_current_odds_from_bookmakers_list(self):
        match = {"odds_snapshots": [{"bookmakers": [{
            "name": "Pinnacle",
            "bets": [{"name": "Match Winner",
                       "values": [{"value": "Home", "odd": "2.05"},
                                    {"value": "Draw", "odd": "3.50"}]}],
        }]}]}
        assert _current_odds_for(match, "Match Winner", "Home") == 2.05


# ─────────────────────────────────────────────────────────────────────
# 4–5) Attach: mutates pick.key_data.line_movement + _line_movement
# ─────────────────────────────────────────────────────────────────────
class TestAttachLineMovement:
    def _match_with_opening(self):
        return {"odds_snapshots": [{
            "_opening_odds": {
                "Pinnacle|Match Winner|Home": 2.10,
                "Pinnacle|Goals Over/Under|Over 2.5": 1.85,
            },
            "bookmakers": [{"name": "Pinnacle", "bets": [
                {"name": "Match Winner", "values": [
                    {"value": "Home", "odd": "2.05"},
                ]},
                {"name": "Goals Over/Under", "values": [
                    {"value": "Over 2.5", "odd": "1.87"},
                ]},
            ]}],
        }]}

    def test_attaches_for_match_winner_home(self):
        match = self._match_with_opening()
        pick = {"recommendation": {"market": "Match Winner", "selection": "Home"}}
        assert attach_line_movement_from_opening_odds(pick, match) is True
        lm = pick["_line_movement"]
        assert lm["opening_odds"] == 2.10
        assert lm["current_odds"] == 2.05
        assert lm["odds_movement"] == pytest.approx(-0.05, abs=1e-3)
        assert lm["source"] == "thestatsapi_opening"
        assert lm["market"] == "Match Winner"
        assert lm["selection"] == "Home"
        # Legacy compact form in key_data
        kd_lm = pick["key_data"]["line_movement"]
        assert kd_lm["direction"] == lm["direction"]
        assert kd_lm["source"] == "thestatsapi_opening"

    def test_attaches_for_es_more_than(self):
        match = self._match_with_opening()
        pick = {"recommendation": {"market": "Goles totales", "selection": "Más de 2.5"}}
        assert attach_line_movement_from_opening_odds(pick, match) is True
        lm = pick["_line_movement"]
        assert lm["selection"] == "Over 2.5"
        assert lm["opening_odds"] == 1.85
        assert lm["current_odds"] == 1.87
        assert lm["odds_movement"] == pytest.approx(0.02, abs=1e-3)

    def test_fail_soft_no_opening(self):
        match = {"odds_snapshots": [{"_opening_odds": {}}]}
        pick = {"recommendation": {"market": "Match Winner", "selection": "Home"}}
        assert attach_line_movement_from_opening_odds(pick, match) is False
        assert "_line_movement" not in pick

    def test_fail_soft_unknown_market(self):
        match = self._match_with_opening()
        pick = {"recommendation": {"market": "Random Market", "selection": "X"}}
        assert attach_line_movement_from_opening_odds(pick, match) is False

    def test_fail_soft_no_snapshots(self):
        pick = {"recommendation": {"market": "Match Winner", "selection": "Home"}}
        assert attach_line_movement_from_opening_odds(pick, {}) is False


# ─────────────────────────────────────────────────────────────────────
# 6) Iteration helper: parsed → matches cross
# ─────────────────────────────────────────────────────────────────────
class TestEnrichPicksWithOpeningMovement:
    def test_enriches_picks_by_match_id(self):
        match_doc = {
            "match_id": "mid-1",
            "odds_snapshots": [{
                "_opening_odds": {"Pinnacle|Match Winner|Home": 2.20},
                "bookmakers": [{"name": "Pinnacle", "bets": [
                    {"name": "Match Winner",
                      "values": [{"value": "Home", "odd": "2.00"}]},
                ]}],
            }],
        }
        parsed = {"picks": [
            {"match_id": "mid-1",
              "recommendation": {"market": "Match Winner", "selection": "Home"}},
            {"match_id": "mid-2",   # no match doc → skipped
              "recommendation": {"market": "Match Winner", "selection": "Home"}},
        ]}
        n = enrich_picks_with_opening_movement(parsed, [match_doc])
        assert n == 1
        assert "_line_movement" in parsed["picks"][0]
        assert "_line_movement" not in parsed["picks"][1]

    def test_empty_inputs_return_zero(self):
        assert enrich_picks_with_opening_movement({}, []) == 0
        assert enrich_picks_with_opening_movement({"picks": []}, []) == 0
