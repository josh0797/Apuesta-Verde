"""Phase F74-post — Tests para los 9 cambios:

  1. Adapter editorial aplana recent_fixtures / TheStatsAPI / live_stats.
  2. Normalizer unifica TheStatsAPI a esquema F74 + persiste alias legacy.
  3. (integración indirecta) Pipeline editorial usa adapter.
  4. Market identity resolver resuelve por odds_snapshots y devuelve
     candidates en caso AMBIGUOUS.
  5. alternative_rescue acepta aliases de mercado (ES/EN).
  6. Adapter sube data_quality de THIN a LIMITED/USABLE cuando hay
     recent_fixtures.
  7. (frontend — fuera de scope pytest)
  8. resolve_thestatsapi_match_id_by_names existe + cache miss devuelve None.
  9. Pipeline moneyball intenta resolver identity UNKNOWN antes de
     ratificar el bucket.
"""
from __future__ import annotations

import pytest

from services import football_editorial_payload_adapter as adapter
from services import football_data_enrichment_normalizer as norm
from services import football_market_identity_resolver as mir
from services import alternative_rescue as ar
from services import thestatsapi_client as tsc
from services import moneyball_layer as mb


# ─────────────────────────────────────────────────────────────────────
# Cambio 1 + 6: Editorial payload adapter
# ─────────────────────────────────────────────────────────────────────
class TestEditorialPayloadAdapter:
    def test_flattens_recent_fixtures_with_real_names(self):
        match = {
            "home_team": {
                "name": "Brazil",
                "context": {"recent_fixtures": {
                    "gf": [2, 1, 3, 1, 2], "ga": [0, 1, 1, 1, 0],
                    "historical_goal_profile": {
                        "goals_for_avg": 1.8, "goals_against_avg": 0.6,
                        "btts_rate_l15": 0.55, "clean_sheet_rate_l15": 0.45,
                    },
                }},
            },
            "away_team": {
                "name": "Argentina",
                "context": {"recent_fixtures": {
                    "gf": [1, 2, 0, 1, 1], "ga": [1, 1, 2, 0, 1],
                    "historical_goal_profile": {
                        "goals_for_avg": 1.0, "goals_against_avg": 1.0,
                        "btts_rate_l15": 0.40,
                    },
                }},
            },
        }
        out = adapter.build_editorial_ready_match_payload(match)
        assert out["match_label"] == "Brazil vs Argentina"
        assert out["home_team_name"] == "Brazil"
        assert out["away_team_name"] == "Argentina"
        assert out["home_goals_scored_l5"] == 1.8
        assert out["away_goals_scored_l5"] == 1.0
        assert out["home_btts_rate_l15"] == 0.55
        assert out["internal_analysis_debug"]["recent_fixtures_flattened"] is True
        assert out["internal_analysis_debug"]["data_quality"] in ("LIMITED", "USABLE", "STRONG")

    def test_data_quality_strong_with_xg_btts_h2h(self):
        match = {
            "home_team": {"name": "X", "context": {"recent_fixtures": {
                "historical_goal_profile": {
                    "goals_for_avg": 1.5, "goals_against_avg": 0.9,
                    "btts_rate_l15": 0.50, "clean_sheet_rate_l15": 0.30,
                },
            }}},
            "away_team": {"name": "Y", "context": {"recent_fixtures": {
                "historical_goal_profile": {
                    "goals_for_avg": 1.1, "goals_against_avg": 1.1,
                    "btts_rate_l15": 0.45,
                },
            }}},
            "_thestatsapi_enrichment": {"team_stats": {
                "home": {"expected_goals_per_match": 1.6},
                "away": {"expected_goals_per_match": 1.1},
            }},
            "h2h_recent": [{"score": "1-1"}],
            "odds": 1.85,
        }
        norm.normalize_football_data_enrichment(match)
        out = adapter.build_editorial_ready_match_payload(match)
        assert out["internal_analysis_debug"]["data_quality"] == "STRONG"
        assert out["home_xg"] == 1.6
        assert out["away_xg"] == 1.1
        assert "H2H_RECENT_PASSED_THROUGH" in out["internal_analysis_debug"]["reason_codes"]

    def test_thin_match_reports_no_signals(self):
        out = adapter.build_editorial_ready_match_payload({})
        assert out["internal_analysis_debug"]["data_quality"] == "THIN"
        assert "EDITORIAL_ADAPTER_NO_SIGNALS_FOUND" in out["internal_analysis_debug"]["reason_codes"]

    def test_upgraded_from_thin_when_recent_fixtures_present(self):
        match = {
            "data_quality": "THIN",
            "home_team": {"name": "X", "context": {"recent_fixtures": {
                "historical_goal_profile": {"goals_for_avg": 1.2, "btts_rate_l15": 0.4},
            }}},
            "away_team": {"name": "Y", "context": {"recent_fixtures": {
                "historical_goal_profile": {"goals_for_avg": 0.8},
            }}},
        }
        out = adapter.build_editorial_ready_match_payload(match)
        assert out["internal_analysis_debug"]["data_quality"] != "THIN"
        assert "EDITORIAL_DATA_QUALITY_UPGRADED_FROM_THIN" in out["internal_analysis_debug"]["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Cambio 2: Football data enrichment normalizer
# ─────────────────────────────────────────────────────────────────────
class TestFootballDataEnrichmentNormalizer:
    def test_persists_both_legacy_keys(self):
        match = {"_thestatsapi_enrichment": {"team_stats": {
            "home": {"expected_goals_per_match": 1.4, "shots_per_match": 12.0},
            "away": {"expected_goals_per_match": 0.9},
        }}}
        norm.normalize_football_data_enrichment(match)
        assert isinstance(match["football_data_enrichment"], dict)
        assert isinstance(match["thestatsapi_snapshot"], dict)
        # Mismo objeto en ambos (alias).
        assert match["football_data_enrichment"] is match["thestatsapi_snapshot"]
        payload = match["football_data_enrichment"]
        assert payload["xg"]["home"] == 1.4
        assert payload["team_stats"]["home"]["xg_for_avg"] == 1.4
        assert payload["team_stats"]["home"]["shots_avg"] == 12.0
        assert "FOOTBALL_DATA_ENRICHMENT_NORMALIZED" in payload["reason_codes"]

    def test_merge_from_multiple_sources(self):
        match = {
            "_thestatsapi_enrichment": {"team_stats": {
                "home": {"expected_goals_per_match": 1.4},
            }},
            "thestatsapi_snapshot": {"team_stats": {
                "away": {"expected_goals_per_match": 1.1, "possession_per_match": 52},
            }},
        }
        norm.normalize_football_data_enrichment(match)
        payload = match["football_data_enrichment"]
        assert payload["xg"]["home"] == 1.4
        assert payload["xg"]["away"] == 1.1
        assert payload["team_stats"]["away"]["possession_avg"] == 52
        assert "ENRICHMENT_MERGED_FROM_MULTIPLE_SOURCES" in payload["reason_codes"]

    def test_thin_when_no_sources(self):
        match = {}
        norm.normalize_football_data_enrichment(match)
        payload = match["football_data_enrichment"]
        assert payload["data_quality"] == "THIN"
        assert payload["available"] is False


# ─────────────────────────────────────────────────────────────────────
# Cambio 4: Market identity resolver
# ─────────────────────────────────────────────────────────────────────
class TestMarketIdentityResolver:
    def test_resolves_from_recommendation(self):
        match = {}
        entry = {"recommendation": {"market": "Doble Oportunidad", "selection": "1X"}}
        r = mir.resolve_market_identity_for_discarded_entry(match, entry)
        assert r["state"] == mir.STATE_RESOLVED
        assert r["market_identity"]["identity_key"] == "DOUBLE_CHANCE:1X"
        assert r["resolved_from"] == "recommendation"

    def test_resolves_from_odds_single_match(self):
        match = {"odds_snapshots": [{"markets": {
            "Doble Oportunidad": [{"Local/Empate": 1.24, "Empate/Visitante": 2.5}],
        }}]}
        entry = {"recommendation": {"market": "?", "selection": "?",
                                      "odds_range": "1.24-1.24"}}
        r = mir.resolve_market_identity_for_discarded_entry(match, entry)
        assert r["state"] == mir.STATE_RESOLVED
        assert r["market_identity"]["identity_key"] == "DOUBLE_CHANCE:1X"
        assert r["resolved_from"] == "odds_snapshot_match"

    def test_ambiguous_two_markets_same_odds(self):
        match = {"odds_snapshots": [{"markets": {
            "Doble Oportunidad": [{"Local/Empate": 1.30}],
            "Over/Under":        [{"lines": [{"value": "Over 1.5", "odd": 1.30}]}],
        }}]}
        entry = {"recommendation": {"market": "?", "selection": "?",
                                      "odds_range": "1.30-1.30"}}
        r = mir.resolve_market_identity_for_discarded_entry(match, entry)
        assert r["state"] == mir.STATE_REQUIRES_MANUAL
        keys = [c["identity_key"] for c in r["candidate_markets"]]
        assert "DOUBLE_CHANCE:1X" in keys
        assert "TOTAL_GOALS:OVER:1.5" in keys

    def test_unknown_when_no_hints(self):
        r = mir.resolve_market_identity_for_discarded_entry(
            {}, {"recommendation": {"market": None, "selection": None}},
        )
        assert r["state"] == mir.STATE_UNKNOWN


# ─────────────────────────────────────────────────────────────────────
# Cambio 5: alternative_rescue aliases
# ─────────────────────────────────────────────────────────────────────
class TestAlternativeRescueAliases:
    def test_get_market_rows_by_alias_es(self):
        markets = {
            "Goles Totales": [{"lines": [{"value": "Más de 1.5", "odd": 1.30}]}],
            "Doble Oportunidad": [{"Local/Empate": 1.24}],
        }
        rows_ou = ar.get_market_rows_by_alias(markets, ar.OVER_UNDER_ALIASES)
        rows_dc = ar.get_market_rows_by_alias(markets, ar.DOUBLE_CHANCE_ALIASES)
        assert rows_ou and rows_dc

    def test_protected_odds_extracts_es_aliases(self):
        markets = {
            "Goles Totales": [{"lines": [
                {"value": "Más de 1.5", "odd": 1.30},
                {"value": "Menos de 3.5", "odd": 1.85},
            ]}],
            "Doble Oportunidad": [{
                "Local/Empate": 1.24, "Empate/Visitante": 2.50,
            }],
        }
        out = ar._football_extract_protected_odds(markets)
        assert out.get("Over 1.5") == 1.30
        assert out.get("Under 3.5") == 1.85
        assert out.get("1X") == 1.24
        assert out.get("X2") == 2.50

    def test_aliases_case_insensitive_and_accent_insensitive(self):
        markets = {"DOBLE oportunidad": [{"Home/Draw": 1.30}]}
        rows = ar.get_market_rows_by_alias(markets, ar.DOUBLE_CHANCE_ALIASES)
        assert rows


# ─────────────────────────────────────────────────────────────────────
# Cambio 8: TheStatsAPI fallback por nombres (función disponible)
# ─────────────────────────────────────────────────────────────────────
class TestTheStatsAPIMatchIdResolver:
    def test_function_is_exported(self):
        assert callable(tsc.resolve_thestatsapi_match_id_by_names)

    def test_team_name_normalisation(self):
        assert tsc._normalize_team_name_for_search("Brazil") == "brazil"
        assert tsc._normalize_team_name_for_search("Suiza") == "suiza"

    @pytest.mark.asyncio
    async def test_resolver_returns_none_when_disabled(self):
        # Sin STATSAPI_API_KEY el resolver debe devolver None sin romper.
        import os
        prev = os.environ.pop("STATSAPI_API_KEY", None)
        try:
            result = await tsc.resolve_thestatsapi_match_id_by_names(
                "Brazil", "Argentina", "2026-04-19",
            )
            assert result is None
        finally:
            if prev:
                os.environ["STATSAPI_API_KEY"] = prev


# ─────────────────────────────────────────────────────────────────────
# Cambio 9: Pipeline moneyball intenta resolver UNKNOWN antes del bucket
# ─────────────────────────────────────────────────────────────────────
class TestMoneyballPipelineResolvesUnknownIdentity:
    def test_unknown_with_resolvable_odds_does_not_go_to_requires_mi(self):
        parsed = {"picks": [{
            "match_id": "mid-1",
            "match_label": "Brazil vs Argentina",
            "recommendation": {
                "market": "?", "selection": "?",
                "odds_range": "1.24-1.24", "confidence_score": 70,
            },
            "market_identity": {"identity_key": "UNKNOWN:RAW:?",
                                  "family": None, "side": None},
            "odds_snapshots": [{"markets": {
                "Doble Oportunidad": [{"Local/Empate": 1.24,
                                        "Empate/Visitante": 2.5}],
            }}],
        }], "summary": {}}
        out = mb.apply_moneyball_layer(parsed, sport="football")
        # NO debe terminar en requires_market_identity (sí en discarded por falta
        # de estimated_probability, pero NO en requires_mi).
        assert len(out["summary"].get("requires_market_identity", [])) == 0

    def test_unknown_with_ambiguous_odds_returns_candidates(self):
        parsed = {"picks": [{
            "match_id": "mid-2",
            "match_label": "Canada vs Bosnia",
            "recommendation": {
                "market": "?", "selection": "?",
                "odds_range": "1.30-1.30", "confidence_score": 70,
            },
            "market_identity": {"identity_key": "UNKNOWN:RAW:?",
                                  "family": None, "side": None},
            "odds_snapshots": [{"markets": {
                "Doble Oportunidad": [{"Local/Empate": 1.30}],
                "Over/Under": [{"lines": [{"value": "Over 1.5", "odd": 1.30}]}],
            }}],
        }], "summary": {}}
        out = mb.apply_moneyball_layer(parsed, sport="football")
        bucket = out["summary"].get("requires_market_identity") or []
        assert len(bucket) == 1
        entry = bucket[0]
        assert entry["state"] == "REQUIRES_MANUAL_MARKET_SELECTION"
        assert "candidate_markets" in entry
        keys = [c["identity_key"] for c in entry["candidate_markets"]]
        assert "DOUBLE_CHANCE:1X" in keys
        assert "TOTAL_GOALS:OVER:1.5" in keys
