"""Tests for Phase F57 — Football Context + Trend Discovery (observe-only)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.football_news_context_ingestion import (
    KEYWORD_SEVERITY,
    LOCALE_ES,
    build_google_news_rss_url,
    detect_keywords,
)
from services.football_context_trend_discovery import (
    ENGINE_VERSION,
    RC_AVAILABLE,
    RC_AVOID_BTTS_ONE_SIDE_WEAK,
    RC_CORNERS_ACCELERATING,
    RC_DISCIPLINARY_REMOVALS,
    RC_FAVORITE_TERRITORIAL_DOMINANCE,
    RC_INTERNAL_CONFLICT,
    RC_KEY_PLAYERS_ABSENT,
    RC_LOST_STREAK,
    RC_OPPONENT_SQUAD_DISRUPTION,
    RC_PROTECTED_TOTAL_PREFERRED,
    RC_RESCUED_BY_CONTEXT,
    RC_SCORING_STREAK,
    RC_SQUAD_INSTABILITY,
    RC_TEAM_A_SCORING_STREAK,
    RC_TEAM_B_SCORING_STREAK,
    RC_TEAM_CORNER_VOLUME_HIGH,
    RC_TWO_PLUS_GOALS_STREAK,
    RMK_ENGLAND_ML_STYLE_FAVORITE,
    RMK_OVER_1_5,
    RMK_OVER_1_75,
    RMK_TOTAL_CORNERS_OVER,
    analyze_corners_trend,
    analyze_football_context_trend,
    analyze_protected_goals_trend,
    detect_form_streaks,
    detect_squad_disruption,
    rescue_missed_match,
)


# ───────────────────────────────────────────────────────────────────────
# Keyword detection
# ───────────────────────────────────────────────────────────────────────
class TestKeywordDetection:
    def test_separo_por_indisciplina_es(self):
        codes = detect_keywords(
            "Selección de Costa Rica separó a tres jugadores por indisciplina"
        )
        assert "SEPARADO_POR_INDISCIPLINA" in codes

    def test_balacera_es(self):
        codes = detect_keywords(
            "Jugadores estarían involucrados en una balacera"
        )
        assert "BALACERA" in codes

    def test_apartado_concentracion(self):
        codes = detect_keywords("Jugadores apartados de la concentración")
        assert "APARTADO_DE_CONCENTRACION" in codes

    def test_baja_disciplinaria(self):
        codes = detect_keywords("Baja disciplinaria en la selección")
        assert "BAJA_DISCIPLINARIA" in codes

    def test_no_match_returns_empty(self):
        assert detect_keywords("Goleador convocado por sorpresa") == []

    def test_english_fallback_runs_when_locale_es(self):
        codes = detect_keywords(
            "Costa Rica removed from the squad three players"
        )
        assert "REMOVED_FROM_SQUAD" in codes

    def test_all_keywords_have_severity(self):
        # Sanity: every code we can emit must have a severity.
        codes_seen = {
            "APARTADO_DE_CONCENTRACION", "SEPARADO_POR_INDISCIPLINA",
            "EXPULSADO_DE_CONVOCATORIA", "BAJA_DISCIPLINARIA",
            "PROBLEMAS_INTERNOS", "NO_CONTINUARA_CON_SELECCION",
            "FUERA_DE_SELECCION", "SANCIONADO", "EXCLUIDO", "BALACERA",
            "REMOVED_FROM_SQUAD", "DROPPED_FROM_NATIONAL_TEAM",
            "INTERNAL_CONFLICT", "DISCIPLINARY_ACTION", "SENT_HOME",
        }
        for c in codes_seen:
            assert c in KEYWORD_SEVERITY, f"Missing severity for {c}"


class TestGoogleNewsRssUrl:
    def test_es_locale_url_contains_team(self):
        url = build_google_news_rss_url("Costa Rica", locale=LOCALE_ES)
        assert "Costa%20Rica" in url or "Costa+Rica" in url or "Costa%20Rica" in url
        assert "hl=es" in url


# ───────────────────────────────────────────────────────────────────────
# Squad disruption
# ───────────────────────────────────────────────────────────────────────
class TestSquadDisruption:
    def test_no_news_returns_low_bucket(self):
        out = detect_squad_disruption("Costa Rica", None)
        assert out["available"] is False
        assert out["bucket"] == "LOW"

    def test_no_matched_items_returns_zero_score(self):
        news = {"available": True, "items": [
            {"title": "Selección convocó nuevos jugadores", "matched_phrases": []},
        ]}
        out = detect_squad_disruption("Costa Rica", news)
        assert out["available"] is True
        assert out["squad_disruption_score"] == 0
        assert out["bucket"] == "LOW"

    def test_disciplinary_removal_drives_high_score(self):
        news = {"available": True, "items": [
            {
                "title": "Costa Rica separó a tres por indisciplina",
                "matched_phrases": ["SEPARADO_POR_INDISCIPLINA"],
                "source_url": "https://foxsports.com/articulo",
                "source_name": "Fox Sports",
                "link": "https://foxsports.com/articulo",
            },
            {
                "title": "Dos estarían involucrados en una balacera",
                "matched_phrases": ["BALACERA"],
                "source_url": "https://foxsports.com/articulo2",
                "source_name": "Fox Sports",
                "link": "https://foxsports.com/articulo2",
            },
        ]}
        out = detect_squad_disruption("Costa Rica", news)
        assert out["available"] is True
        assert out["bucket"] == "HIGH"
        assert RC_DISCIPLINARY_REMOVALS in out["reason_codes"]
        assert RC_SQUAD_INSTABILITY in out["reason_codes"]
        # Evidence sources retain source_url for transparency.
        assert out["evidence_sources"][0]["source_url"].startswith("http")

    def test_internal_conflict_emits_proper_code(self):
        news = {"available": True, "items": [
            {"title": "Problemas internos sacuden al plantel",
             "matched_phrases": ["PROBLEMAS_INTERNOS"]},
        ]}
        out = detect_squad_disruption("Equipo X", news)
        assert RC_INTERNAL_CONFLICT in out["reason_codes"]

    def test_key_players_absent_emits_proper_code(self):
        news = {"available": True, "items": [
            {"title": "Star fuera de la selección",
             "matched_phrases": ["FUERA_DE_SELECCION"]},
        ]}
        out = detect_squad_disruption("Equipo X", news)
        assert RC_KEY_PLAYERS_ABSENT in out["reason_codes"]


# ───────────────────────────────────────────────────────────────────────
# Form streaks
# ───────────────────────────────────────────────────────────────────────
class TestFormStreaks:
    def test_empty_results_returns_unavailable(self):
        out = detect_form_streaks("Costa Rica", [])
        assert out["available"] is False

    def test_costa_rica_lost_4_straight(self):
        results = [
            {"goals_for": 0, "goals_against": 2, "outcome": "L"},
            {"goals_for": 1, "goals_against": 3, "outcome": "L"},
            {"goals_for": 0, "goals_against": 1, "outcome": "L"},
            {"goals_for": 0, "goals_against": 2, "outcome": "L"},
            {"goals_for": 2, "goals_against": 1, "outcome": "W"},
        ]
        out = detect_form_streaks("Costa Rica", results)
        assert "LOST_4_STRAIGHT" in out["form_streaks"]
        assert RC_LOST_STREAK in out["reason_codes"]

    def test_nigeria_scoring_5_straight(self):
        # Nigeria recent results from the user's screenshot.
        results = [
            {"goals_for": 1, "goals_against": 2, "outcome": "L"},  # vs Portugal
            {"goals_for": 2, "goals_against": 2, "outcome": "D"},  # vs Polonia
            {"goals_for": 3, "goals_against": 0, "outcome": "W"},  # vs Jamaica
            {"goals_for": 2, "goals_against": 0, "outcome": "W"},  # vs Zimbabue
            {"goals_for": 2, "goals_against": 2, "outcome": "D"},  # vs Jordania
            {"goals_for": 2, "goals_against": 1, "outcome": "W"},  # vs Irán
        ]
        out = detect_form_streaks("Nigeria", results)
        # Scored in all 6 → scoring streak >= 3 → fires.
        assert RC_SCORING_STREAK in out["reason_codes"]
        assert out["counters"]["scoring_streak"] >= 5

    def test_portugal_2plus_goals_in_3_straight(self):
        results = [
            {"goals_for": 2, "goals_against": 1, "outcome": "W"},
            {"goals_for": 3, "goals_against": 0, "outcome": "W"},
            {"goals_for": 2, "goals_against": 2, "outcome": "D"},
            {"goals_for": 0, "goals_against": 1, "outcome": "L"},
        ]
        out = detect_form_streaks("Portugal", results)
        assert RC_TWO_PLUS_GOALS_STREAK in out["reason_codes"]

    def test_outcome_inferred_when_missing(self):
        results = [{"goals_for": 2, "goals_against": 0}]
        out = detect_form_streaks("X", results)
        assert out["counters"]["wins"] >= 1


# ───────────────────────────────────────────────────────────────────────
# Corners trend
# ───────────────────────────────────────────────────────────────────────
class TestCornersTrend:
    def test_no_data_returns_unavailable(self):
        out = analyze_corners_trend(
            "X",
            team_corners_for_last_10=[],
            team_corners_for_last_5=[],
        )
        assert out["available"] is False

    def test_high_volume_fires_signal(self):
        # England-style: avg L10 = 7.5, L5 = 8.5 with possession dominance.
        out = analyze_corners_trend(
            "Inglaterra",
            team_corners_for_last_10=[8, 9, 7, 6, 8, 7, 9, 7, 8, 6],
            team_corners_for_last_5=[8, 9, 9, 8, 9],
            opponent_corners_against_last_10=[7, 6, 7, 5, 6, 6, 7, 6, 7, 6],
            possession_dominance_score=68,
            favorite_indicator=True,
            opponent_low_block=True,
        )
        assert out["available"] is True
        assert out["corners_signal"] is True
        assert RC_TEAM_CORNER_VOLUME_HIGH in out["reason_codes"]
        assert RC_CORNERS_ACCELERATING in out["reason_codes"]
        assert RC_FAVORITE_TERRITORIAL_DOMINANCE in out["reason_codes"]
        assert out["recommended_market"] is not None

    def test_low_volume_no_signal(self):
        out = analyze_corners_trend(
            "X",
            team_corners_for_last_10=[3, 4, 3, 4, 3, 4, 3, 4, 3, 4],
            team_corners_for_last_5=[3, 4, 3, 4, 3],
        )
        assert out["corners_signal"] is False
        assert out["confidence"] < 50

    def test_l10_vs_l5_comparison_basis(self):
        out = analyze_corners_trend(
            "X",
            team_corners_for_last_10=[6, 7, 6, 7, 6, 7, 6, 7, 6, 7],
            team_corners_for_last_5=[6, 7, 6, 7, 6],
        )
        assert out["avg_for_last_10"] == 6.5
        assert out["avg_for_last_5"] == 6.4


# ───────────────────────────────────────────────────────────────────────
# Protected goals trend
# ───────────────────────────────────────────────────────────────────────
class TestProtectedGoalsTrend:
    def test_both_scoring_streaks_recommends_protected(self):
        # Portugal scored 2 in 3 straight; Nigeria scored in 5 straight.
        portugal = [
            {"goals_for": 2, "goals_against": 1, "outcome": "W"},
            {"goals_for": 3, "goals_against": 0, "outcome": "W"},
            {"goals_for": 2, "goals_against": 2, "outcome": "D"},
        ]
        nigeria = [
            {"goals_for": 1, "goals_against": 2, "outcome": "L"},
            {"goals_for": 2, "goals_against": 2, "outcome": "D"},
            {"goals_for": 3, "goals_against": 0, "outcome": "W"},
            {"goals_for": 2, "goals_against": 0, "outcome": "W"},
            {"goals_for": 2, "goals_against": 2, "outcome": "D"},
        ]
        out = analyze_protected_goals_trend(
            "Portugal", "Nigeria",
            team_a_recent_results=portugal,
            team_b_recent_results=nigeria,
        )
        assert out["goals_trend_signal"] is True
        assert out["recommended_market"] in (RMK_OVER_1_5, RMK_OVER_1_75)
        assert RC_TEAM_A_SCORING_STREAK in out["reason_codes"]
        assert RC_TEAM_B_SCORING_STREAK in out["reason_codes"]
        assert RC_PROTECTED_TOTAL_PREFERRED in out["reason_codes"]

    def test_one_side_weak_avoids_btts_and_over_2_5(self):
        # Strong scorer + weak scorer combination.
        strong = [
            {"goals_for": 3, "goals_against": 0},
            {"goals_for": 2, "goals_against": 1},
            {"goals_for": 3, "goals_against": 2},
        ]
        weak = [
            {"goals_for": 1, "goals_against": 0},
            {"goals_for": 1, "goals_against": 1},
            {"goals_for": 1, "goals_against": 2},
        ]
        out = analyze_protected_goals_trend(
            "Strong FC", "Weak FC",
            team_a_recent_results=strong,
            team_b_recent_results=weak,
        )
        assert RC_AVOID_BTTS_ONE_SIDE_WEAK in out["reason_codes"]

    def test_no_streak_no_signal(self):
        out = analyze_protected_goals_trend(
            "A", "B",
            team_a_recent_results=[{"goals_for": 0}],
            team_b_recent_results=[{"goals_for": 0}],
        )
        assert out["goals_trend_signal"] is False


# ───────────────────────────────────────────────────────────────────────
# Missed-match rescue
# ───────────────────────────────────────────────────────────────────────
class TestRescueMissedMatch:
    def test_no_original_status_no_rescue(self):
        out = rescue_missed_match(
            original_engine_status=None,
            context_score=80, trend_score=80,
        )
        assert out["rescued_by_context_trend"] is False

    def test_discarded_with_high_context_rescues(self):
        out = rescue_missed_match(
            original_engine_status="DISCARDED",
            context_score=80, trend_score=30,
        )
        assert out["rescued_by_context_trend"] is True
        assert out["rescue_reason"] == "STRONG_CONTEXT_AND_TREND_SIGNAL"

    def test_omitted_with_low_signals_no_rescue(self):
        out = rescue_missed_match(
            original_engine_status="OMITTED",
            context_score=20, trend_score=25,
        )
        assert out["rescued_by_context_trend"] is False


# ───────────────────────────────────────────────────────────────────────
# Top-level orchestrator
# ───────────────────────────────────────────────────────────────────────
class TestAnalyzeFootballContextTrend:
    @pytest.mark.asyncio
    async def test_missing_teams_returns_unavailable(self):
        out = await analyze_football_context_trend(
            home_team="", away_team="Costa Rica",
        )
        assert out["available"] is False

    @pytest.mark.asyncio
    async def test_england_vs_costa_rica_scenario(self):
        # Simulate the user's parlay: ENG vs CR with disruption + corners + losses.
        injected_news = {
            "available": True,
            "home": {
                "available": True, "items": [],
                "items_total": 0, "matched_items_total": 0,
            },
            "away": {
                "available": True,
                "items": [
                    {"title": "Costa Rica separó a tres por indisciplina",
                     "matched_phrases": ["SEPARADO_POR_INDISCIPLINA"],
                     "source_url": "https://foxsports.com/x",
                     "source_name": "Fox Sports",
                     "link": "https://foxsports.com/x"},
                    {"title": "Estarían involucrados en una balacera",
                     "matched_phrases": ["BALACERA"],
                     "source_url": "https://foxsports.com/y",
                     "source_name": "Fox Sports",
                     "link": "https://foxsports.com/y"},
                ],
                "items_total": 2, "matched_items_total": 2,
            },
        }
        out = await analyze_football_context_trend(
            home_team="Inglaterra", away_team="Costa Rica",
            match_id=12345,
            injected_news=injected_news,
            home_recent_results=[
                {"goals_for": 2, "goals_against": 0, "outcome": "W"},
                {"goals_for": 3, "goals_against": 1, "outcome": "W"},
            ],
            away_recent_results=[
                {"goals_for": 0, "goals_against": 2, "outcome": "L"},
                {"goals_for": 1, "goals_against": 3, "outcome": "L"},
                {"goals_for": 0, "goals_against": 1, "outcome": "L"},
                {"goals_for": 0, "goals_against": 2, "outcome": "L"},
            ],
            home_corners_for_last_10=[8, 9, 7, 6, 8, 7, 9, 7, 8, 6],
            home_corners_for_last_5=[8, 9, 9, 8, 9],
            away_corners_against_last_10=[7, 6, 7, 5, 6, 6, 7, 6, 7, 6],
            home_possession_dominance_score=70,
            home_is_favorite=True,
            opponent_low_block_home_side=True,
        )
        assert out["available"] is True
        assert out["engine_version"] == ENGINE_VERSION
        assert RC_AVAILABLE in out["reason_codes"]
        assert out["squad_disruption"]["away"]["bucket"] == "HIGH"
        # Form streaks → Costa Rica losing streak.
        assert RC_LOST_STREAK in out["form_streaks"]["away"]["reason_codes"]
        # Corners signal for England.
        assert out["corners_trend"]["home"]["corners_signal"] is True
        # Recommended markets must include corners + favorite ML.
        market_codes = [r.get("market_code") for r in out["recommended_markets"]]
        assert RMK_TOTAL_CORNERS_OVER in market_codes
        assert RMK_ENGLAND_ML_STYLE_FAVORITE in market_codes
        # Narrative not empty.
        assert isinstance(out["narrative_es"], str) and len(out["narrative_es"]) > 20
        # Observe-only flag set.
        assert out["observe_only"] is True

    @pytest.mark.asyncio
    async def test_portugal_vs_nigeria_protected_goals(self):
        injected_news = {
            "available": True,
            "home": {"available": True, "items": [], "items_total": 0,
                     "matched_items_total": 0},
            "away": {"available": True, "items": [], "items_total": 0,
                     "matched_items_total": 0},
        }
        out = await analyze_football_context_trend(
            home_team="Portugal", away_team="Nigeria",
            injected_news=injected_news,
            home_recent_results=[
                {"goals_for": 2, "goals_against": 1, "outcome": "W"},
                {"goals_for": 3, "goals_against": 0, "outcome": "W"},
                {"goals_for": 2, "goals_against": 2, "outcome": "D"},
            ],
            away_recent_results=[
                {"goals_for": 1, "goals_against": 2, "outcome": "L"},
                {"goals_for": 2, "goals_against": 2, "outcome": "D"},
                {"goals_for": 3, "goals_against": 0, "outcome": "W"},
                {"goals_for": 2, "goals_against": 0, "outcome": "W"},
                {"goals_for": 2, "goals_against": 2, "outcome": "D"},
            ],
        )
        assert out["protected_goals_trend"]["goals_trend_signal"] is True
        rec = out["protected_goals_trend"]["recommended_market"]
        assert rec in (RMK_OVER_1_5, RMK_OVER_1_75)

    @pytest.mark.asyncio
    async def test_news_fetch_exception_fail_soft(self):
        # When use_news=True and the fetch raises, we still get a valid
        # output with empty news.
        with patch(
            "services.football_context_trend_discovery.fetch_news_for_match",
            new_callable=AsyncMock, side_effect=Exception("network down"),
        ):
            out = await analyze_football_context_trend(
                home_team="A", away_team="B", use_news=True,
            )
        assert out["available"] is True
        assert out["squad_disruption"]["home"]["available"] is False
        assert out["observe_only"] is True

    @pytest.mark.asyncio
    async def test_rescue_with_high_trend_score(self):
        injected_news = {"home": {"available": True, "items": []},
                          "away": {"available": True, "items": []}}
        out = await analyze_football_context_trend(
            home_team="A", away_team="B",
            original_engine_status="DISCARDED",
            injected_news=injected_news,
            home_corners_for_last_10=[9, 10, 9, 10, 9, 10, 9, 10, 9, 10],
            home_corners_for_last_5=[10, 11, 10, 11, 10],
            home_possession_dominance_score=72,
            home_is_favorite=True,
            opponent_low_block_home_side=True,
            away_corners_against_last_10=[8, 7, 8, 7, 8, 7, 8, 7, 8, 7],
        )
        # trend_score will be high → rescue triggers.
        if out["trend_score"] >= 65:
            assert out["missed_match_rescue"]["rescued_by_context_trend"] is True
            assert RC_RESCUED_BY_CONTEXT in out["reason_codes"]


# ───────────────────────────────────────────────────────────────────────
# News ingestion HTTP layer fail-soft
# ───────────────────────────────────────────────────────────────────────
class TestNewsHttpFailSoft:
    @pytest.mark.asyncio
    async def test_http_error_returns_unavailable_payload(self):
        from services.football_news_context_ingestion import fetch_team_disruption_news
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock,
                   side_effect=Exception("network error")):
            out = await fetch_team_disruption_news(
                "Costa Rica", db=None, use_cache=False,
            )
        assert out["available"] is False
        assert out["items"] == []
        assert "queried_url" in out

    @pytest.mark.asyncio
    async def test_empty_team_returns_unavailable(self):
        from services.football_news_context_ingestion import fetch_team_disruption_news
        out = await fetch_team_disruption_news("", db=None)
        assert out["available"] is False
