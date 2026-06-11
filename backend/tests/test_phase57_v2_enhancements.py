"""Phase 57 v2 / F57 v2 — tests for the v2 enhancements:

* MLB Player Props:
  - ``player_prop_fragility`` block (0-100 score, bucket, reasons).
  - Lineup PA mapping (1st = 4.6 ... 9th = 3.6).
  - Market-priority ranking in ``_select_best_market_per_player``.
  - Hybrid integration: ``analyze_mlb_day(..., include_player_props=True)``.

* Football Context+Trend:
  - New keywords: ``SE_PIERDE_PROXIMO_PARTIDO`` / ``LESIONADO`` (ES) +
    ``MISS_NEXT_MATCH`` / ``INJURED`` (EN).
  - Injury / next-match reason codes propagation.
  - BBC Sport + Reuters Sports as direct RSS feeds.
  - Hybrid integration: ``analyze_matches(..., include_context_trend=True)``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.football_news_context_ingestion import (
    DIRECT_RSS_FEEDS,
    KEYWORD_SEVERITY,
    detect_keywords,
)
from services.football_context_trend_discovery import (
    RC_INJURY_REPORTED,
    RC_KEY_PLAYERS_ABSENT,
    RC_MISSING_NEXT_MATCH,
    detect_squad_disruption,
)
from services.mlb_player_props_discovery import (
    LINEUP_POSITION_PA,
    MARKET_H_R_RBI,
    MARKET_HITS_1P,
    MARKET_PRIORITY_ORDER,
    MARKET_RBI_1P,
    MARKET_RUNS_1P,
    MARKET_TB,
    RC_FRAG_DATA_MINIMAL,
    RC_FRAG_DATA_PARTIAL,
    RC_FRAG_ELITE_PITCHER,
    RC_FRAG_HR_DEPENDENCY,
    RC_FRAG_LOW_AVG_HIGH_ISO,
    RC_FRAG_LOW_LINEUP_SPOT,
    RC_FRAG_RBI_DEPENDENCY,
    _compute_player_prop_fragility,
    _resolve_pa_per_game,
    _select_best_market_per_player,
    predict_player_prop,
)


# ───────────────────────────────────────────────────────────────────────
# Lineup PA mapping
# ───────────────────────────────────────────────────────────────────────
class TestLineupPAMapping:
    def test_table_has_all_nine_spots(self):
        for spot in range(1, 10):
            assert spot in LINEUP_POSITION_PA

    def test_top_of_order_higher_than_bottom(self):
        assert LINEUP_POSITION_PA[1] > LINEUP_POSITION_PA[9]
        assert LINEUP_POSITION_PA[1] == 4.6
        assert LINEUP_POSITION_PA[9] == 3.6

    def test_resolve_explicit_pa_wins(self):
        p = {"pa_per_game": 5.0, "batting_order": 9}
        assert _resolve_pa_per_game(p) == 5.0

    def test_resolve_uses_batting_order(self):
        assert _resolve_pa_per_game({"batting_order": 1}) == 4.6
        assert _resolve_pa_per_game({"lineup_position": 7}) == 3.8

    def test_resolve_falls_back_to_default(self):
        assert _resolve_pa_per_game({}) == 4.1
        assert _resolve_pa_per_game({"batting_order": 11}) == 4.1

    def test_predict_uses_lineup_pa(self):
        # Same player, different batting order → top hitter gets more PA
        # → lambda + probability higher than bottom hitter.
        player_top = {
            "id": 1, "name": "T", "position": "CF",
            "obp": 0.350, "slg": 0.460, "avg": 0.280,
            "games_played": 100, "runs": 65, "rbi": 60,
            "batting_order": 1,
        }
        player_bot = {**player_top, "batting_order": 9}
        out_top = predict_player_prop(player=player_top, market=MARKET_HITS_1P)
        out_bot = predict_player_prop(player=player_bot, market=MARKET_HITS_1P)
        assert out_top["pa_per_game"] == 4.6
        assert out_bot["pa_per_game"] == 3.6
        assert out_top["lambda_estimate"] > out_bot["lambda_estimate"]
        assert out_top["model_probability"] >= out_bot["model_probability"]


# ───────────────────────────────────────────────────────────────────────
# Player-prop fragility
# ───────────────────────────────────────────────────────────────────────
class TestPlayerPropFragility:
    def test_default_includes_fragility_block(self):
        out = predict_player_prop(
            player={"obp": 0.340, "slg": 0.430, "avg": 0.270,
                    "games_played": 120, "runs": 60, "rbi": 55},
            market=MARKET_H_R_RBI,
        )
        assert "player_prop_fragility" in out
        assert "fragility_bucket" in out
        assert "fragility_reasons" in out
        assert 0 <= out["player_prop_fragility"] <= 100
        assert out["fragility_bucket"] in ("LOW", "MEDIUM", "HIGH")

    def test_rbi_market_marked_fragile(self):
        out = predict_player_prop(
            player={"obp": 0.340, "slg": 0.430, "avg": 0.270,
                    "games_played": 120, "runs": 60, "rbi": 55},
            market=MARKET_RBI_1P,
        )
        assert RC_FRAG_RBI_DEPENDENCY in out["fragility_reasons"]

    def test_hr_dependency_in_tb_market(self):
        # 30 HR / 150 G ≈ 0.20 hr/g → HR_DEPENDENCY fires for TB.
        out = predict_player_prop(
            player={"obp": 0.330, "slg": 0.520, "avg": 0.245,
                    "games_played": 150, "runs": 80, "rbi": 95,
                    "hr": 32},
            market=MARKET_TB,
        )
        assert RC_FRAG_HR_DEPENDENCY in out["fragility_reasons"]

    def test_low_avg_high_iso_flagged(self):
        # AVG 0.225, SLG 0.450 → ISO 0.225 → LOW_AVG_HIGH_ISO fires.
        out = predict_player_prop(
            player={"obp": 0.310, "slg": 0.450, "avg": 0.225,
                    "games_played": 120, "runs": 50, "rbi": 60},
            market=MARKET_HITS_1P,
        )
        assert RC_FRAG_LOW_AVG_HIGH_ISO in out["fragility_reasons"]

    def test_elite_pitcher_increases_fragility(self):
        pitcher = {"era": 2.50, "whip": 0.95}
        out = predict_player_prop(
            player={"obp": 0.350, "slg": 0.470, "avg": 0.270,
                    "games_played": 120, "runs": 60, "rbi": 65},
            opposing_pitcher=pitcher,
            market=MARKET_HITS_1P,
        )
        assert RC_FRAG_ELITE_PITCHER in out["fragility_reasons"]

    def test_low_lineup_spot_increases_fragility(self):
        out = predict_player_prop(
            player={"obp": 0.310, "slg": 0.400, "avg": 0.250,
                    "games_played": 110, "runs": 35, "rbi": 38,
                    "batting_order": 8},
            market=MARKET_HITS_1P,
        )
        assert RC_FRAG_LOW_LINEUP_SPOT in out["fragility_reasons"]

    def test_minimal_data_quality_adds_penalty(self):
        block = _compute_player_prop_fragility(
            player={"obp": 0.330, "slg": 0.420, "avg": 0.260,
                    "games_played": 80, "runs": 30, "rbi": 35},
            market=MARKET_H_R_RBI,
            opposing_pitcher=None,
            data_quality="MINIMAL",
            base_rates={},
        )
        assert RC_FRAG_DATA_MINIMAL in block["fragility_reasons"]
        # Cannot exceed 100.
        assert block["player_prop_fragility"] <= 100

    def test_partial_quality_lighter_penalty_than_minimal(self):
        partial = _compute_player_prop_fragility(
            player={"avg": 0.270, "slg": 0.420, "obp": 0.330,
                    "games_played": 100, "runs": 50, "rbi": 50},
            market=MARKET_H_R_RBI, opposing_pitcher=None,
            data_quality="PARTIAL", base_rates={},
        )
        minimal = _compute_player_prop_fragility(
            player={"avg": 0.270, "slg": 0.420, "obp": 0.330,
                    "games_played": 100, "runs": 50, "rbi": 50},
            market=MARKET_H_R_RBI, opposing_pitcher=None,
            data_quality="MINIMAL", base_rates={},
        )
        assert RC_FRAG_DATA_PARTIAL in partial["fragility_reasons"]
        assert minimal["player_prop_fragility"] > partial["player_prop_fragility"]

    def test_clean_profile_low_bucket(self):
        block = _compute_player_prop_fragility(
            player={"avg": 0.290, "slg": 0.430, "obp": 0.370,
                    "games_played": 140, "runs": 80, "rbi": 70,
                    "batting_order": 3},
            market=MARKET_H_R_RBI, opposing_pitcher={"era": 4.20, "whip": 1.30},
            data_quality="COMPLETE", base_rates={},
        )
        assert block["fragility_bucket"] == "LOW"
        assert block["player_prop_fragility"] < 30


# ───────────────────────────────────────────────────────────────────────
# Market priority ordering
# ───────────────────────────────────────────────────────────────────────
class TestMarketPriority:
    def test_priority_tuple_h_r_rbi_first(self):
        assert MARKET_PRIORITY_ORDER[0] == MARKET_H_R_RBI
        assert MARKET_PRIORITY_ORDER[1] == MARKET_TB
        assert MARKET_PRIORITY_ORDER[-1] == MARKET_RBI_1P

    def test_selector_prefers_h_r_rbi_over_rbi_in_same_tier(self):
        rbi_pred = {
            "available": True, "confidence_tier": "VALUE",
            "market": MARKET_RBI_1P, "edge_score": 80,
            "model_probability": 0.62,
        }
        h_r_rbi_pred = {
            "available": True, "confidence_tier": "VALUE",
            "market": MARKET_H_R_RBI, "edge_score": 60,    # lower edge
            "model_probability": 0.61,
        }
        chosen = _select_best_market_per_player([rbi_pred, h_r_rbi_pred])
        assert chosen["market"] == MARKET_H_R_RBI

    def test_selector_prefers_tier_over_priority(self):
        # WATCH H_R_RBI vs VALUE Hits → VALUE wins regardless of priority.
        watch_hrrbi = {
            "available": True, "confidence_tier": "WATCH",
            "market": MARKET_H_R_RBI, "edge_score": 30,
            "model_probability": 0.52,
        }
        value_hits = {
            "available": True, "confidence_tier": "VALUE",
            "market": MARKET_HITS_1P, "edge_score": 50,
            "model_probability": 0.65,
        }
        chosen = _select_best_market_per_player([watch_hrrbi, value_hits])
        assert chosen["confidence_tier"] == "VALUE"
        assert chosen["market"] == MARKET_HITS_1P

    def test_selector_drops_avoid_only(self):
        avoid = {"available": True, "confidence_tier": "AVOID",
                  "market": MARKET_H_R_RBI, "edge_score": 90,
                  "model_probability": 0.40}
        assert _select_best_market_per_player([avoid]) is None


# ───────────────────────────────────────────────────────────────────────
# Football news v2 — new keywords & sources
# ───────────────────────────────────────────────────────────────────────
class TestFootballNewsV2:
    def test_se_pierde_proximo_partido_es(self):
        codes = detect_keywords(
            "El delantero se pierde el próximo partido por lesión",
            locale="es",
        )
        assert "SE_PIERDE_PROXIMO_PARTIDO" in codes

    def test_lesionado_es(self):
        codes = detect_keywords("Goleador lesionado por dos semanas", locale="es")
        assert "LESIONADO" in codes

    def test_cae_lesionado_es(self):
        codes = detect_keywords("Cae lesionado en entrenamiento", locale="es")
        assert "LESIONADO" in codes

    def test_miss_next_match_en(self):
        codes = detect_keywords("Will miss the next match with a hamstring injury",
                                 locale="en")
        assert "MISS_NEXT_MATCH" in codes
        assert "INJURED" in codes

    def test_injured_en_specific_body_part(self):
        codes = detect_keywords("Knee injury sidelines the captain",
                                 locale="en")
        assert "INJURED" in codes

    def test_keyword_severities_set(self):
        for code in ("SE_PIERDE_PROXIMO_PARTIDO", "LESIONADO",
                     "MISS_NEXT_MATCH", "INJURED"):
            assert code in KEYWORD_SEVERITY

    def test_direct_rss_feeds_registered(self):
        assert "BBC Sport Football" in DIRECT_RSS_FEEDS
        assert "Reuters Sports" in DIRECT_RSS_FEEDS
        assert DIRECT_RSS_FEEDS["BBC Sport Football"].startswith("http")


class TestSquadDisruptionV2:
    def test_lesionado_emits_injury_and_key_absent(self):
        news = {"available": True, "items": [{
            "title": "Goleador lesionado para el próximo encuentro",
            "matched_phrases": ["LESIONADO"],
        }]}
        out = detect_squad_disruption("Equipo X", news)
        assert RC_INJURY_REPORTED in out["reason_codes"]
        assert RC_KEY_PLAYERS_ABSENT in out["reason_codes"]

    def test_miss_next_match_emits_missing_and_key_absent(self):
        news = {"available": True, "items": [{
            "title": "Star will miss next match",
            "matched_phrases": ["MISS_NEXT_MATCH"],
        }]}
        out = detect_squad_disruption("Equipo X", news)
        assert RC_MISSING_NEXT_MATCH in out["reason_codes"]
        assert RC_KEY_PLAYERS_ABSENT in out["reason_codes"]


# ───────────────────────────────────────────────────────────────────────
# Hybrid integration: analyze_mlb_day(include_player_props=True)
# ───────────────────────────────────────────────────────────────────────
class TestMLBHybridIntegration:
    def test_default_excludes_player_props_block(self):
        # _empty_payload (the early-exit path) must NEVER include the
        # player_props_discovery block — confirms additive-only contract.
        from services.mlb_day_orchestrator import _empty_payload
        out = _empty_payload({})
        assert "player_props_discovery" not in out

    def test_analyze_mlb_day_signature_has_flag(self):
        # Validate that the new keyword-only flag is part of the signature.
        import inspect
        from services.mlb_day_orchestrator import analyze_mlb_day
        sig = inspect.signature(analyze_mlb_day)
        assert "include_player_props" in sig.parameters
        param = sig.parameters["include_player_props"]
        assert param.default is False
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.asyncio
    async def test_player_props_block_shape_matches_contract(self):
        # Directly validate the shape the orchestrator would attach
        # when include_player_props=True succeeds.
        from services.mlb_player_props_discovery import compute_player_props_for_day
        # When schedule returns empty the function returns an
        # "available: True, games_processed: 0" payload — perfect to
        # validate the shape contract used by the orchestrator.
        with patch("services.mlb_stats_api.get_schedule_with_probables",
                   new_callable=AsyncMock, return_value=[]):
            pp_result = await compute_player_props_for_day("2026-04-15", db=None)
        assert pp_result["available"] is True
        # The orchestrator copies these specific keys into the block —
        # confirm they all exist:
        for key in ("engine_version", "props", "props_total",
                    "data_quality_summary"):
            assert key in pp_result


# ───────────────────────────────────────────────────────────────────────
# Hybrid integration: analyze_matches(include_context_trend=True)
# ───────────────────────────────────────────────────────────────────────
class TestFootballHybridIntegration:
    def test_analyze_matches_signature_has_flag(self):
        # Validate the new keyword-only flag.
        import inspect
        from services.analyst_engine import analyze_matches
        sig = inspect.signature(analyze_matches)
        assert "include_context_trend" in sig.parameters
        param = sig.parameters["include_context_trend"]
        assert param.default is False
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.asyncio
    async def test_context_trend_helper_callable(self):
        # The helper used by the integration must be importable + runnable
        # with mocked news, returning an `available: True` shape.
        from services.football_context_trend_discovery import (
            analyze_football_context_trend,
        )
        out = await analyze_football_context_trend(
            home_team="Inglaterra", away_team="Costa Rica",
            injected_news={"home": {"available": True, "items": []},
                            "away": {"available": True, "items": []}},
        )
        assert out["available"] is True
        assert out["observe_only"] is True

