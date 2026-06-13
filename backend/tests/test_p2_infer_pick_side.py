"""Phase P2 — Tests for ``alternative_rescue.infer_original_pick_side()``.

Verifies the 4-source cascade behaves as expected and that the function
returns ``None`` only when all sources fail or are ambiguous.
"""
from __future__ import annotations

import pytest

from services.alternative_rescue import (
    infer_original_pick_side,
    _infer_side_from_recommendation,
    _infer_side_from_forebet,
    _infer_side_from_odds,
    _infer_side_from_thestatsapi_edge,
)


# ─── Source 1 — recommendation.selection ─────────────────────────────
class TestSource1Recommendation:
    def test_short_token_home(self):
        entry = {"recommendation": {"selection": "1"}}
        assert _infer_side_from_recommendation(entry, "Man City", "Liverpool") == "home"

    def test_short_token_away(self):
        entry = {"recommendation": {"selection": "2"}}
        assert _infer_side_from_recommendation(entry, "Man City", "Liverpool") == "away"

    def test_team_name_home(self):
        entry = {"recommendation": {"selection": "Manchester City gana"}}
        assert _infer_side_from_recommendation(
            entry, "Manchester City", "Liverpool",
        ) == "home"

    def test_team_name_away(self):
        entry = {"recommendation": {"selection": "Real Madrid gana"}}
        assert _infer_side_from_recommendation(
            entry, "Barcelona", "Real Madrid",
        ) == "away"

    def test_spread_prefix_away(self):
        entry = {"recommendation": {"selection": "Visitante +1.5"}}
        assert _infer_side_from_recommendation(entry, "Local FC", "Visit FC") == "away"

    def test_no_recommendation_returns_none(self):
        assert _infer_side_from_recommendation({}, "Home", "Away") is None
        assert _infer_side_from_recommendation(
            {"recommendation": {"selection": "X"}}, "H", "A",
        ) is None  # draw → ambiguous


# ─── Source 2 — Forebet predicted score / winner ──────────────────────
class TestSource2Forebet:
    def test_predicted_winner_home(self):
        m = {"football_data_enrichment": {"editorial": {"forebet": {"predicted_winner": "home"}}}}
        assert _infer_side_from_forebet(m) == "home"

    def test_predicted_winner_away(self):
        m = {"forebet": {"predicted_winner": "away"}}
        assert _infer_side_from_forebet(m) == "away"

    def test_predicted_winner_draw_returns_none(self):
        m = {"forebet": {"predicted_winner": "draw"}}
        assert _infer_side_from_forebet(m) is None

    def test_predicted_score_home_wins(self):
        m = {"forebet": {"predicted_score": "2-1"}}
        assert _infer_side_from_forebet(m) == "home"

    def test_predicted_score_away_wins(self):
        m = {"forebet": {"predicted_score": "0-3"}}
        assert _infer_side_from_forebet(m) == "away"

    def test_predicted_score_draw_returns_none(self):
        m = {"forebet": {"predicted_score": "1-1"}}
        assert _infer_side_from_forebet(m) is None

    def test_no_forebet_returns_none(self):
        assert _infer_side_from_forebet({}) is None


# ─── Source 3 — Match Winner odds favourite ──────────────────────────
class TestSource3OddsFavourite:
    def test_home_favourite_clear_gap(self):
        match = {
            "odds_snapshots": [{
                "markets": {
                    "Match Winner": [
                        {"value": "Home", "odd": 1.40},
                        {"value": "Draw", "odd": 4.50},
                        {"value": "Away", "odd": 7.50},
                    ],
                },
            }],
        }
        assert _infer_side_from_odds(match) == "home"

    def test_away_favourite_clear_gap(self):
        match = {
            "odds_snapshots": [{
                "markets": {
                    "Moneyline": [
                        {"value": "Home", "odd": 3.20},
                        {"value": "Away", "odd": 2.00},
                    ],
                },
            }],
        }
        assert _infer_side_from_odds(match) == "away"

    def test_no_clear_gap_returns_none(self):
        # 2.10 vs 2.05 → gap < 10% → no inference.
        match = {
            "odds_snapshots": [{
                "markets": {
                    "Match Winner": [
                        {"value": "Home", "odd": 2.10},
                        {"value": "Away", "odd": 2.05},
                    ],
                },
            }],
        }
        assert _infer_side_from_odds(match) is None

    def test_missing_odds_returns_none(self):
        assert _infer_side_from_odds({"odds_snapshots": []}) is None
        assert _infer_side_from_odds({}) is None


# ─── Source 4 — TheStatsAPI directional edge ─────────────────────────
class TestSource4TheStatsAPI:
    def test_market_edge_side_home(self):
        entry = {"_market_edge": {"side": "home", "verdict": "value"}}
        assert _infer_side_from_thestatsapi_edge(entry, {}) == "home"

    def test_market_edge_side_away(self):
        entry = {"_market_edge": {"favoured_side": "away"}}
        assert _infer_side_from_thestatsapi_edge(entry, {}) == "away"

    def test_market_edge_verdict_home(self):
        entry = {"_market_edge": {"verdict": "home shows edge"}}
        assert _infer_side_from_thestatsapi_edge(entry, {}) == "home"

    def test_thestatsapi_home_vs_away_edge(self):
        match = {"football_data_enrichment": {
            "thestatsapi": {"home_edge": 0.08, "away_edge": 0.02},
        }}
        assert _infer_side_from_thestatsapi_edge({}, match) == "home"

    def test_thestatsapi_edge_too_close_returns_none(self):
        match = {"football_data_enrichment": {
            "thestatsapi": {"home_edge": 0.04, "away_edge": 0.03},
        }}
        assert _infer_side_from_thestatsapi_edge({}, match) is None


# ─── Cascade — order of preference & NULL fallback ───────────────────
class TestCascadeOrdering:
    def test_recommendation_wins_over_forebet(self):
        """If recommendation says home but Forebet predicts away,
        recommendation MUST win (Source 1 has priority)."""
        match = {
            "home_team": {"name": "Alpha"},
            "away_team": {"name": "Beta"},
            "forebet":   {"predicted_winner": "away"},
        }
        entry = {"recommendation": {"selection": "Alpha gana"}}
        assert infer_original_pick_side(match, entry) == "home"

    def test_forebet_wins_when_no_recommendation(self):
        match = {
            "home_team": {"name": "Alpha"},
            "away_team": {"name": "Beta"},
            "forebet":   {"predicted_winner": "away"},
        }
        assert infer_original_pick_side(match, {}) == "away"

    def test_odds_used_when_only_odds_available(self):
        match = {
            "odds_snapshots": [{
                "markets": {
                    "Match Winner": [
                        {"value": "Home", "odd": 1.50},
                        {"value": "Away", "odd": 6.00},
                    ],
                },
            }],
        }
        assert infer_original_pick_side(match, {}) == "home"

    def test_thestatsapi_used_as_last_resort(self):
        match = {"football_data_enrichment": {
            "thestatsapi": {"home_edge": 0.10, "away_edge": 0.01},
        }}
        assert infer_original_pick_side(match, {}) == "home"

    def test_all_sources_blank_returns_none(self):
        """Strict guarantee — when NO source is confident, return
        ``None`` so the legacy conservative behaviour is preserved
        (rescue layer will skip directional candidates)."""
        match = {
            "home_team": {"name": "Alpha"},
            "away_team": {"name": "Beta"},
            "odds_snapshots": [{
                "markets": {
                    "Match Winner": [
                        {"value": "Home", "odd": 2.05},
                        {"value": "Away", "odd": 2.10},  # gap < 10%
                    ],
                },
            }],
        }
        # No recommendation, no forebet, odds gap too small, no
        # thestatsapi edge.
        assert infer_original_pick_side(match, {}) is None

    def test_handles_malformed_entry_gracefully(self):
        # Must NEVER raise; should fall back to None.
        assert infer_original_pick_side(None, None) is None  # type: ignore[arg-type]
        assert infer_original_pick_side({}, "not a dict") is None  # type: ignore[arg-type]


# ─── Integration with attempt_alternative_market_rescue ──────────────
class TestRescueWiring:
    def test_rescue_skips_directional_when_side_is_none(self):
        """If ``original_pick_side=None`` (no inference), directional
        candidates like 1X / X2 must still be skipped — preserves the
        legacy behaviour from before P2."""
        from services.alternative_rescue import attempt_alternative_market_rescue
        match = {
            "match_id": "P2_INT_001",
            "home_team": {"name": "Alpha"},
            "away_team": {"name": "Beta"},
            "odds_snapshots": [{
                "markets": {
                    "Double Chance": [
                        {"value": "Home/Draw",  "odd": 1.25},
                        {"value": "Draw/Away",  "odd": 1.30},
                        {"value": "Home/Away",  "odd": 1.40},
                    ],
                    "Over/Under": [
                        {"value": "Over",  "odd": 1.50, "line": "1.5"},
                        {"value": "Under", "odd": 1.70, "line": "3.5"},
                    ],
                },
            }],
        }
        result = attempt_alternative_market_rescue(
            match, sport="football", original_pick_side=None,
        )
        # Rescue MAY succeed via Under 3.5 (non-directional), but if it
        # does it must NOT be a directional Double Chance side.
        if result and isinstance(result, dict):
            mkt = (result.get("market") or "").lower()
            assert "double" not in mkt and "doble" not in mkt
