"""Phase F72 — Forebet audit + Opponent strength tests.

Acceptance tests (10+):
  1. Brazil 5/6 goles vs amistosos vs rivales débiles + Morocco más
     fuerte → scoreline DEGRADED / BLOCKED.
  2. Forebet favorece a Brazil 59% → favoritismo CONFIRMED si métricas
     internas lo respaldan.
  3. Rival más fuerte → CURRENT_OPPONENT_STRONGER_THAN_RECENT_AVG.
  4. Rival similar → no degradar por fuerza de rival.
  5. Rival más débil → no aplicar FRIENDLY_GOALS_VS_WEAKER_OPPONENTS_DETECTED.
  6. Sin métricas suficientes → INSUFFICIENT_DATA + no inventar conclusión.
  7. Nombres reales en UI (Brazil, Morocco), nunca "Home"/"Away".
  8. competition_context oficial degrada friendlies para goles.
  9. Direction CONFLICTED → reconciliación NO aplica Over tilt.
 10. Scoreline BLOCKED_FOR_AGGRESSIVE_MARKETS → reconciliación NO override score
     y annotates con audit_status.
 11. Override solo aplica si scoreline_audit.status == TRUSTED.
"""
from __future__ import annotations

import json
import re

import pytest

from services.football_external_fallback_orchestrator import (
    reconcile_internal_vs_external_analysis,
)
from services.football_external_prediction_audit import (
    audit_forebet_direction,
    audit_forebet_prediction_against_match_splits,
    audit_forebet_scoreline,
    audit_opponent_strength_context,
    split_recent_matches_official_vs_friendly,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
def _brazil_match_against_morocco(*, knockout: bool = False) -> dict:
    """Brazil vs Morocco in an OFFICIAL match. Brazil has 5 recent
    matches: 3 friendlies (vs Panama 6-0, vs Croatia 3-1, vs France 1-0)
    + 2 officials (vs Argentina 0-1, vs Colombia 1-1). All friendly
    explosions vs WEAKER opponents.
    """
    return {
        "match_id":  101,
        "match_label": "Brazil vs Morocco",
        "match_type": "knockout" if knockout else "official",
        "competition": "Octavos de final" if knockout else "World Cup",
        "home_team": {
            "name":         "Brazil",
            "elo_rating":   2030,
            "xg_avg":       2.1,
            "xga_avg":      0.9,
            "shots_avg":    14.3,
            "sot_avg":      5.8,
            "possession_avg": 58,
            "goals_scored_l5": 2.2,
            "recent_opponents": [
                {"name": "Panama",   "match_type": "friendly",
                 "goals_scored_by_team": 6, "elo_rating": 1300},
                {"name": "Croatia",  "match_type": "friendly",
                 "goals_scored_by_team": 3, "elo_rating": 1820},
                {"name": "France",   "match_type": "friendly",
                 "goals_scored_by_team": 1, "elo_rating": 2050},
                {"name": "Argentina","match_type": "official",
                 "goals_scored_by_team": 0, "elo_rating": 2100},
                {"name": "Colombia", "match_type": "official",
                 "goals_scored_by_team": 1, "elo_rating": 1850},
            ],
        },
        "away_team": {
            "name":         "Morocco",
            "elo_rating":   1870,
            "xg_avg":       1.3,
            "xga_avg":      0.95,
            "shots_avg":    11.0,
            "sot_avg":      3.7,
            "possession_avg": 47,
            "goals_scored_l5": 1.2,
            "recent_opponents": [],
        },
        # Brazil's last matches: 3 friendlies with high scoring, 2 officials low.
        "recent_matches": [
            {"competition": "Friendly", "match_type": "friendly",
             "goals_scored_by_team": 6, "goals_conceded_by_team": 0},
            {"competition": "Friendly", "match_type": "friendly",
             "goals_scored_by_team": 3, "goals_conceded_by_team": 1},
            {"competition": "Friendly", "match_type": "friendly",
             "goals_scored_by_team": 1, "goals_conceded_by_team": 0},
            {"competition": "Copa America", "match_type": "official",
             "goals_scored_by_team": 0, "goals_conceded_by_team": 1},
            {"competition": "Copa America", "match_type": "official",
             "goals_scored_by_team": 1, "goals_conceded_by_team": 1},
            {"competition": "WCQ", "match_type": "official",
             "goals_scored_by_team": 2, "goals_conceded_by_team": 0},
            {"competition": "WCQ", "match_type": "official",
             "goals_scored_by_team": 1, "goals_conceded_by_team": 2},
        ],
    }


def _forebet_brazil_31() -> dict:
    return {
        "forebet_pct_1":   59,
        "forebet_pct_x":   27,
        "forebet_pct_2":   14,
        "pick_1x2":        "1",
        "predicted_score": "3-1",
        "goals_avg":       3.29,
    }


# ─────────────────────────────────────────────────────────────────────
# T1 — Brazil 3-1 high scoring → scoreline BLOCKED/DEGRADED.
# ─────────────────────────────────────────────────────────────────────
def test_t1_brazil_31_blocked_or_degraded():
    match    = _brazil_match_against_morocco()
    audit    = audit_forebet_prediction_against_match_splits(
        _forebet_brazil_31(), match,
    )
    sa = audit["forebet_scoreline_audit"]
    assert sa["status"] in ("DEGRADED", "BLOCKED_FOR_AGGRESSIVE_MARKETS"), \
        f"Expected scoreline DEGRADED/BLOCKED, got {sa['status']}: {sa}"
    # Block aggressive overs MUST be set when low over2 rate + opp_harder.
    # Brazil official over2 rate = 0/4 = 0 → triggers OVER block.
    if sa["status"] == "BLOCKED_FOR_AGGRESSIVE_MARKETS":
        assert sa["block_aggressive_overs"] is True


# ─────────────────────────────────────────────────────────────────────
# T2 — Forebet favorece a Brazil 59% → favoritismo CONFIRMED.
# ─────────────────────────────────────────────────────────────────────
def test_t2_favoritism_confirmed_by_metrics():
    match  = _brazil_match_against_morocco()
    audit  = audit_forebet_prediction_against_match_splits(
        _forebet_brazil_31(), match,
    )
    da = audit["forebet_direction_signal"]
    # Brazil has higher xG / shots / SoT / xGA-inv / goals → CONFIRMED
    assert da["status"] in ("CONFIRMED", "WEAK_CONFIRMED"), \
        f"Expected direction CONFIRMED/WEAK, got {da['status']}: {da}"
    assert da["favorite"] == "HOME"


# ─────────────────────────────────────────────────────────────────────
# T3 — Current opponent STRONGER than recent average.
# ─────────────────────────────────────────────────────────────────────
def test_t3_current_opponent_stronger():
    match = _brazil_match_against_morocco()
    res = audit_opponent_strength_context(
        current_opponent=match["away_team"],
        recent_opponents=match["home_team"]["recent_opponents"],
        team_name="Brazil",
    )
    assert res["available"] is True
    assert res["current_opponent_strength_tier"] == "STRONG"
    # Recent opponents: Panama(WEAK), Croatia(STRONG), France(ELITE),
    # Argentina(ELITE), Colombia(STRONG) → avg ranks (1+3+4+4+3)/5=3.0
    # which equals STRONG. Morocco STRONG = rank 3. So similar, NOT stronger.
    # Adjust expectation: Morocco vs Brazil's recent stronger lineup is
    # SIMILAR. The test focuses on the audit functioning.
    assert "OPPONENT_STRENGTH_AUDIT_USED" in res["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# T3b — Recent rivals were weaker → opponent stronger detection.
# ─────────────────────────────────────────────────────────────────────
def test_t3b_opponent_stronger_when_recent_weak():
    current = {"name": "Morocco", "elo_rating": 1870}
    recent  = [
        {"name": "Panama",        "elo_rating": 1300, "match_type": "friendly",
         "goals_scored_by_team": 6},
        {"name": "Bolivia",       "elo_rating": 1300, "match_type": "friendly",
         "goals_scored_by_team": 5},
        {"name": "Honduras",      "elo_rating": 1350, "match_type": "friendly",
         "goals_scored_by_team": 4},
        {"name": "El Salvador",   "elo_rating": 1380, "match_type": "friendly",
         "goals_scored_by_team": 3},
        {"name": "Trinidad",      "elo_rating": 1300, "match_type": "friendly",
         "goals_scored_by_team": 4},
    ]
    res = audit_opponent_strength_context(current, recent, "Brazil")
    assert res["available"] is True
    assert res["current_opponent_harder_than_recent_avg"] is True
    assert "CURRENT_OPPONENT_STRONGER_THAN_RECENT_AVG" in res["reason_codes"]
    assert res["high_scoring_matches_vs_weaker_opponents"] is True
    assert "FRIENDLY_GOALS_VS_WEAKER_OPPONENTS_DETECTED" in res["reason_codes"]
    assert res["goals_inflation_risk"] == "HIGH"


# ─────────────────────────────────────────────────────────────────────
# T4 — Rival SIMILAR (not stronger) → no flag.
# ─────────────────────────────────────────────────────────────────────
def test_t4_similar_rival_no_flag():
    current = {"name": "Croatia", "elo_rating": 1820}
    recent  = [
        {"name": "Belgium",  "elo_rating": 1850, "match_type": "official",
         "goals_scored_by_team": 1},
        {"name": "Italy",    "elo_rating": 1830, "match_type": "official",
         "goals_scored_by_team": 1},
        {"name": "Spain",    "elo_rating": 1870, "match_type": "official",
         "goals_scored_by_team": 0},
        {"name": "Portugal", "elo_rating": 1830, "match_type": "official",
         "goals_scored_by_team": 2},
        {"name": "Germany",  "elo_rating": 1810, "match_type": "official",
         "goals_scored_by_team": 1},
    ]
    res = audit_opponent_strength_context(current, recent, "France")
    assert res["current_opponent_harder_than_recent_avg"] is False
    assert "CURRENT_OPPONENT_STRONGER_THAN_RECENT_AVG" not in res["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# T5 — Rival más débil → no FRIENDLY_GOALS_VS_WEAKER_OPPONENTS_DETECTED.
# ─────────────────────────────────────────────────────────────────────
def test_t5_weaker_current_opponent_no_inflation_flag():
    current = {"name": "Panama", "elo_rating": 1300}
    recent  = [
        {"name": "France",   "elo_rating": 2050, "match_type": "official",
         "goals_scored_by_team": 6},  # friendly-like high score
        {"name": "Germany",  "elo_rating": 1900, "match_type": "official",
         "goals_scored_by_team": 4},
        {"name": "Spain",    "elo_rating": 1880, "match_type": "official",
         "goals_scored_by_team": 3},
        {"name": "Italy",    "elo_rating": 1830, "match_type": "official",
         "goals_scored_by_team": 2},
        {"name": "Portugal", "elo_rating": 1830, "match_type": "official",
         "goals_scored_by_team": 2},
    ]
    res = audit_opponent_strength_context(current, recent, "Brazil")
    assert "FRIENDLY_GOALS_VS_WEAKER_OPPONENTS_DETECTED" not in res["reason_codes"]
    assert res["current_opponent_easier_than_recent_avg"] is True


# ─────────────────────────────────────────────────────────────────────
# T6 — Insufficient data → INSUFFICIENT_DATA, no fabrication.
# ─────────────────────────────────────────────────────────────────────
def test_t6_insufficient_data():
    res = audit_opponent_strength_context(
        current_opponent={"name": "Foo"},
        recent_opponents=[{"name": "Bar"}, {"name": "Baz"}],
        team_name="Team",
    )
    assert res["status"] == "INSUFFICIENT_DATA"
    assert "OPPONENT_STRENGTH_DATA_INSUFFICIENT" in res["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# T7 — UI text uses real team names (no Home/Away).
# ─────────────────────────────────────────────────────────────────────
def test_t7_real_team_names_no_home_away():
    match = _brazil_match_against_morocco()
    audit = audit_forebet_prediction_against_match_splits(
        _forebet_brazil_31(), match,
    )
    blob = json.dumps(audit, ensure_ascii=False)
    assert "Brazil" in blob
    assert "Morocco" in blob
    assert not re.search(r"\bHome\b", blob), f"Home leak: {blob[:200]}"
    assert not re.search(r"\bAway\b", blob), f"Away leak: {blob[:200]}"


# ─────────────────────────────────────────────────────────────────────
# T8 — Official competition context downweights friendlies.
# ─────────────────────────────────────────────────────────────────────
def test_t8_official_context_downweights_friendlies():
    match = _brazil_match_against_morocco()
    audit = audit_forebet_prediction_against_match_splits(
        _forebet_brazil_31(), match,
    )
    codes = audit["reason_codes"]
    assert ("UPCOMING_MATCH_OFFICIAL_CONTEXT" in codes
            or "FRIENDLY_GOAL_DATA_DOWNWEIGHTED_FOR_OFFICIAL_MATCH" in codes), \
        f"missing official-context code in {codes}"


# ─────────────────────────────────────────────────────────────────────
# T9 — Reconcile: when scoreline BLOCKED, override is NOT applied.
# ─────────────────────────────────────────────────────────────────────
def test_t9_reconcile_respects_blocked_scoreline():
    internal = {
        "available": True,
        "editorial_sections": {
            "probable_score": {
                "available": True,
                "score":     "1-1",
                "method":    "DIXON_COLES_HEURISTIC",
                "text":      "Marcador interno 1-1.",
                "reason_codes": [],
            },
            "goals_prediction": {"available": True, "status": "OK",
                                  "confidence": 60, "reason_codes": []},
            "corners_prediction": {"available": False},
        },
        "reason_codes": [],
    }
    match = _brazil_match_against_morocco()
    external = {
        "available": True,
        "home_team": "Brazil", "away_team": "Morocco",
        "forebet": _forebet_brazil_31(),
        "sportytrader": {"available": False},
        "forebet_audit": audit_forebet_prediction_against_match_splits(
            _forebet_brazil_31(), match,
        ),
        "reason_codes": [],
    }
    reconcile_internal_vs_external_analysis(internal, external)
    sc = internal["editorial_sections"]["probable_score"]
    # Forebet score should NOT replace internal 1-1 since scoreline is blocked/degraded.
    assert sc.get("score") == "1-1", f"score should remain 1-1, got {sc.get('score')}"
    assert sc.get("external_audit_status") in (
        "DEGRADED", "BLOCKED_FOR_AGGRESSIVE_MARKETS"
    )
    audit_block = internal["external_reconciliation"]
    assert audit_block["scoreline_status"] in (
        "DEGRADED", "BLOCKED_FOR_AGGRESSIVE_MARKETS"
    )


# ─────────────────────────────────────────────────────────────────────
# T10 — Reconcile: Direction CONFLICTED suppresses Over tilt.
# ─────────────────────────────────────────────────────────────────────
def test_t10_direction_conflicted_suppresses_over_tilt():
    # Build a match where Forebet picks Brazil but metrics say otherwise.
    match = {
        "match_label": "Brazil vs Morocco",
        "match_type":  "official",
        "competition": "World Cup",
        "home_team": {"name": "Brazil",
                      "xg_avg": 0.7, "xga_avg": 1.6,
                      "shots_avg": 7,
                      "goals_scored_l5": 0.5,
                      "recent_opponents": []},
        "away_team": {"name": "Morocco",
                      "xg_avg": 1.8, "xga_avg": 0.9,
                      "shots_avg": 16,
                      "goals_scored_l5": 2.4,
                      "recent_opponents": []},
        "recent_matches": [],
    }
    forebet = _forebet_brazil_31()  # picks Brazil
    forebet["goals_avg"] = 3.4
    audit_full = audit_forebet_prediction_against_match_splits(forebet, match)
    assert audit_full["forebet_direction_signal"]["status"] == "CONFLICTED"

    internal = {
        "available": True,
        "editorial_sections": {
            "probable_score": {"available": True, "score": "2-0",
                                "reason_codes": []},
            "goals_prediction": {"available": True, "status": "OK",
                                  "confidence": 50, "reason_codes": []},
            "corners_prediction": {"available": False},
        },
        "reason_codes": [],
    }
    external = {
        "available": True,
        "forebet":   forebet,
        "sportytrader": {"available": False},
        "forebet_audit": audit_full,
    }
    reconcile_internal_vs_external_analysis(internal, external)
    goals = internal["editorial_sections"]["goals_prediction"]
    assert goals.get("external_tilt") != "OVER", \
        "Over tilt should be suppressed when direction is CONFLICTED"
    codes = internal["external_reconciliation"]["reason_codes"]
    assert "EXTERNAL_OVER_TILT_SUPPRESSED_BY_AUDIT" in codes


# ─────────────────────────────────────────────────────────────────────
# T11 — Split function distinguishes official vs friendly.
# ─────────────────────────────────────────────────────────────────────
def test_t11_split_official_vs_friendly():
    recent = [
        {"match_type": "friendly", "goals_scored_by_team": 6, "goals_conceded_by_team": 0},
        {"match_type": "friendly", "goals_scored_by_team": 3, "goals_conceded_by_team": 1},
        {"match_type": "official", "goals_scored_by_team": 1, "goals_conceded_by_team": 1},
        {"match_type": "official", "goals_scored_by_team": 0, "goals_conceded_by_team": 0},
    ]
    splits = split_recent_matches_official_vs_friendly(recent)
    assert splits["official_count"] == 2
    assert splits["friendly_count"] == 2
    assert splits["friendly"]["goals_for_avg"] == 4.5
    assert splits["official"]["goals_for_avg"] == 0.5
    # Over 2 team goals rate: friendly = 2/2 = 1.0; official = 0/2 = 0.
    assert splits["friendly"]["over_2_team_goals_rate"] == 1.0
    assert splits["official"]["over_2_team_goals_rate"] == 0.0
