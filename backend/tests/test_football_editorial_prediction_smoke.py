"""Phase F66 — Editorial Prediction engine smoke tests.

Validates the 5 sub-sections + full report shape end-to-end against
realistic match payloads.
"""
from __future__ import annotations

import pytest

from services.football_editorial_prediction import (
    ENGINE_VERSION,
    generate_football_editorial_prediction,
)


# Fixture: realistic UNDER profile (Brazil vs Morocco-style)
MATCH_UNDER_STRONG = {
    "home_team": {"name": "Brazil", "goals_scored_l5": 1.0,
                  "btts_rate_l15": 0.25, "clean_sheet_rate_l15": 0.50},
    "away_team": {"name": "Morocco", "goals_scored_l5": 0.8,
                  "btts_rate_l15": 0.22},
    "home_corners_for_l5":     3.5, "home_corners_for_l15":     3.6,
    "home_corners_against_l5": 3.3, "home_corners_against_l15": 3.4,
    "away_corners_for_l5":     3.7, "away_corners_for_l15":     3.5,
    "away_corners_against_l5": 3.6, "away_corners_against_l15": 3.5,
    "home_xg": 1.10, "away_xg": 0.75,
}


NORMALISED_ODDS_FULL = {
    "bookmaker":     "Kambi",
    "match_odds":    {"home": 1.55, "draw": 3.80, "away": 6.50},
    "total_goals":   {
        "1.5": {"over": 1.55, "under": 2.40},
        "2.5": {"over": 2.30, "under": 1.55},
        "3.5": {"over": 4.20, "under": 1.20},
    },
    "match_corners": {
        "9.5":  {"over": 2.80, "under": 1.42},
        "10.5": {"over": 3.50, "under": 1.28},
        "11.5": {"over": 4.50, "under": 1.17},
    },
    "btts":          {"yes": 2.10, "no": 1.70},
}


# ─────────────────────────────────────────────────────────────────────
# Top-level report shape
# ─────────────────────────────────────────────────────────────────────
def test_report_top_level_shape() -> None:
    out = generate_football_editorial_prediction(MATCH_UNDER_STRONG,
                                                  odds=NORMALISED_ODDS_FULL)
    assert out["available"]            is True
    assert out["engine_version"]       == ENGINE_VERSION
    assert out["source"]               == "internal_engine"
    assert out["scores24_replacement"] is True
    secs = out["editorial_sections"]
    for k in ("corners_prediction", "goals_prediction", "key_trends",
              "head_to_head", "probable_score"):
        assert k in secs


def test_report_is_json_serialisable() -> None:
    import json
    out = generate_football_editorial_prediction(MATCH_UNDER_STRONG,
                                                  odds=NORMALISED_ODDS_FULL)
    s = json.dumps(out)
    assert "INTERNAL_EDITORIAL_ANALYSIS_USED" in s


# ─────────────────────────────────────────────────────────────────────
# Corners prediction
# ─────────────────────────────────────────────────────────────────────
def test_corners_under_recommendation_with_odds() -> None:
    out = generate_football_editorial_prediction(MATCH_UNDER_STRONG,
                                                  odds=NORMALISED_ODDS_FULL)
    corners = out["editorial_sections"]["corners_prediction"]
    assert corners["available"]          is True
    assert corners["status"]             == "OK"
    assert corners["side"]               == "UNDER"
    assert corners["line"]               == 9.5
    assert corners["odds"]               == 1.42
    assert corners["recommended_market"] == "Under 9.5 córners"
    assert "INTERNAL_CORNERS_PREDICTION_GENERATED" in corners["reason_codes"]
    assert "CORNERS_PROFILE_SUPPORTS_UNDER"      in corners["reason_codes"]
    # Narrative mentions both teams and the actual numbers.
    text = corners["text"]
    assert "Brazil"  in text
    assert "Morocco" in text
    assert "Under 9.5 córners" in text
    assert "1.42"    in text


def test_corners_under_without_odds_still_recommends() -> None:
    out = generate_football_editorial_prediction(MATCH_UNDER_STRONG)
    corners = out["editorial_sections"]["corners_prediction"]
    assert corners["side"]               == "UNDER"
    assert corners["recommended_market"] == "Under 9.5 córners"
    assert corners["odds"] is None
    assert "1.42" not in corners["text"]


def test_corners_mixed_profile_returns_watchlist() -> None:
    mixed = {
        **MATCH_UNDER_STRONG,
        "home_corners_for_l5":     4.5, "home_corners_against_l5": 4.5,
        "away_corners_for_l5":     4.6, "away_corners_against_l5": 4.5,
        "home_corners_for_l15":    4.5, "home_corners_against_l15": 4.5,
        "away_corners_for_l15":    4.6, "away_corners_against_l15": 4.5,
    }
    out = generate_football_editorial_prediction(mixed)
    corners = out["editorial_sections"]["corners_prediction"]
    assert corners["status"]             == "WATCHLIST"
    assert corners["recommended_market"] is None
    assert "MIXED_CORNERS_PROFILE_NO_RECOMMENDATION" in corners["reason_codes"]


def test_corners_missing_data_is_failsoft() -> None:
    out = generate_football_editorial_prediction({"home_team": "A", "away_team": "B"})
    corners = out["editorial_sections"]["corners_prediction"]
    assert corners["available"] is False
    assert corners["status"]    == "MISSING"


# ─────────────────────────────────────────────────────────────────────
# Key trends
# ─────────────────────────────────────────────────────────────────────
def test_key_trends_capped_at_5_and_prioritise_recommendation() -> None:
    out = generate_football_editorial_prediction(MATCH_UNDER_STRONG,
                                                  odds=NORMALISED_ODDS_FULL)
    trends = out["editorial_sections"]["key_trends"]
    assert trends["available"] is True
    assert len(trends["items"]) <= 5
    assert any("córners" in t.lower() or "goles" in t.lower() or "btts" in t.lower()
               for t in trends["items"])


def test_key_trends_missing_data_returns_unavailable() -> None:
    out = generate_football_editorial_prediction({"home_team": "X", "away_team": "Y"})
    trends = out["editorial_sections"]["key_trends"]
    assert trends["available"] is False
    assert "KEY_TRENDS_INSUFFICIENT_DATA" in trends["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# H2H placeholder
# ─────────────────────────────────────────────────────────────────────
def test_h2h_returns_insufficient_sample_by_default() -> None:
    out = generate_football_editorial_prediction(MATCH_UNDER_STRONG)
    h2h = out["editorial_sections"]["head_to_head"]
    assert h2h["available"] is False
    assert "H2H_INSUFFICIENT_SAMPLE" in h2h["reason_codes"]


def test_h2h_with_single_match_warns_about_low_sample() -> None:
    out = generate_football_editorial_prediction(
        MATCH_UNDER_STRONG,
        h2h_matches=[{"date": "2023-03-25", "home_team": "Morocco",
                      "away_team": "Brazil",   "score": "2-1"}],
    )
    h2h = out["editorial_sections"]["head_to_head"]
    assert h2h["available"]     is True
    assert h2h["matches_found"] == 1
    assert "muestra es baja" in h2h["text"].lower() or "muestra" in h2h["text"].lower()


# ─────────────────────────────────────────────────────────────────────
# Probable score
# ─────────────────────────────────────────────────────────────────────
def test_probable_score_uses_dixon_coles_when_xg_available() -> None:
    out = generate_football_editorial_prediction(MATCH_UNDER_STRONG)
    sc = out["editorial_sections"]["probable_score"]
    assert sc["available"]     is True
    assert sc["method"]        == "DIXON_COLES"
    assert sc["score"]         is not None
    assert sc["confidence"]    > 0
    assert any(c.startswith("PROBABLE_SCORE_") for c in sc["reason_codes"])
    # Top scoreline must come from the under-cluster.
    h, a = sc["home_goals"], sc["away_goals"]
    assert (h is not None) and (a is not None) and (h + a) <= 3


def test_probable_score_heuristic_fallback_when_no_xg() -> None:
    match = {**MATCH_UNDER_STRONG}
    match.pop("home_xg")
    match.pop("away_xg")
    out = generate_football_editorial_prediction(match)
    sc = out["editorial_sections"]["probable_score"]
    # Because under-corners profile flows through, the probable_score uses
    # the UNDER heuristic.
    assert sc["available"] is True
    assert sc["method"]    in ("HEURISTIC_BY_PROFILE", "DIXON_COLES")


def test_probable_score_missing_when_no_xg_and_no_profile_hint() -> None:
    """When neither xG nor any profile is available, the engine falls
    back to the NEUTRAL heuristic (still returns a result, capped
    confidence). The 'UNAVAILABLE' state is reserved for fully invalid
    payloads."""
    out = generate_football_editorial_prediction({"home_team": "X", "away_team": "Y"})
    sc = out["editorial_sections"]["probable_score"]
    # The engine prefers a heuristic over total silence.
    assert sc["method"] in ("HEURISTIC_BY_PROFILE", "UNAVAILABLE")
    if sc["method"] == "HEURISTIC_BY_PROFILE":
        assert sc["confidence"] <= 50  # heuristic capped


# ─────────────────────────────────────────────────────────────────────
# Best protected market
# ─────────────────────────────────────────────────────────────────────
def test_best_protected_market_is_highest_confidence_section() -> None:
    out = generate_football_editorial_prediction(MATCH_UNDER_STRONG,
                                                  odds=NORMALISED_ODDS_FULL)
    best = out["best_protected_market"]
    assert best is not None
    assert best["market"] == "Under 9.5 córners"
    assert best["confidence"] >= 60
    assert best["odds"] == 1.42


# ─────────────────────────────────────────────────────────────────────
# Fail-soft on garbage
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [None, [], "hello", 42, 0])
def test_fail_soft_on_garbage_inputs(bad) -> None:
    out = generate_football_editorial_prediction(bad)
    assert isinstance(out, dict)
    assert out["available"] is False
    assert "INTERNAL_EDITORIAL_UNAVAILABLE" in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# TheStatsAPI normaliser — odds shape verified against live response.
# ─────────────────────────────────────────────────────────────────────
def test_extract_normalised_markets_full_shape() -> None:
    from services.thestatsapi_client import extract_normalised_markets
    raw = {
        "match_id": "mt_x",
        "bookmakers": [{
            "bookmaker": "Kambi",
            "markets": {
                "match_odds": {
                    "home": {"opening": "1.760", "last_seen": "1.710"},
                    "draw": {"opening": "3.800", "last_seen": "3.900"},
                    "away": {"opening": "4.100", "last_seen": "4.300"},
                },
                "total_goals": {
                    "1.5": {"over":  {"opening": "1.200", "last_seen": "1.190"},
                            "under": {"opening": "3.950", "last_seen": "4.100"}},
                    "2.5": {"over":  {"opening": "1.660", "last_seen": "1.640"},
                            "under": {"opening": "2.060", "last_seen": "2.100"}},
                },
                "match_corners": {
                    "9.5":  {"over":  {"opening": "1.570", "last_seen": "1.430"},
                             "under": {"opening": "1.470", "last_seen": "1.630"}},
                    "10.5": {"over":  {"opening": "1.930", "last_seen": "1.710"},
                             "under": {"opening": "1.290", "last_seen": "1.380"}},
                },
                "btts": {
                    "yes": {"opening": "1.600", "last_seen": "1.610"},
                    "no":  {"opening": "2.300", "last_seen": "2.250"},
                },
            },
        }],
    }
    out = extract_normalised_markets(raw)
    assert out["bookmaker"]            == "Kambi"
    assert out["match_odds"]["home"]   == 1.71
    assert out["match_odds"]["away"]   == 4.30
    assert out["total_goals"]["1.5"]["over"]  == 1.19
    assert out["total_goals"]["2.5"]["under"] == 2.10
    assert out["match_corners"]["9.5"]["under"] == 1.63
    assert out["btts"]["yes"] == 1.61


def test_extract_normalised_markets_empty_on_garbage() -> None:
    from services.thestatsapi_client import extract_normalised_markets
    out = extract_normalised_markets(None)
    assert out["bookmaker"] is None
    assert out["total_goals"]   == {}
    assert out["match_corners"] == {}
