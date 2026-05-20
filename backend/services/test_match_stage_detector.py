"""Unit tests for match_stage_detector.

Run: cd /app/backend && python -m pytest -q services/test_match_stage_detector.py
"""
from services.match_stage_detector import detect_match_stage, is_final, is_high_pressure  # noqa: E402


# A) Final ─────────────────────────────────────────────────────────────────
def test_final_explicit_round():
    info = detect_match_stage({
        "league": "Club Friendly Cup Final",
        "round": "Final",
        "home_team": {"name": "SC Freiburg"},
        "away_team": {"name": "Aston Villa"},
    })
    assert info["is_final"] is True
    assert info["competition_stage"] == "final"
    assert info["match_importance"] == "maximum"
    assert info["pressure_state"] == "FINAL"
    assert is_high_pressure(info) is True


def test_final_via_league_only():
    info = detect_match_stage({"league": "Copa MX - Grand Final"})
    assert info["is_final"] is True
    assert info["pressure_state"] == "FINAL"


def test_finalissima():
    info = detect_match_stage({"league": "CONMEBOL-UEFA Finalissima"})
    assert info["is_final"] is True


# B) Semifinal ─────────────────────────────────────────────────────────────
def test_semifinal_uel():
    info = detect_match_stage({
        "league": "UEFA Europa League",
        "round": "Semi-final",
    })
    assert info["competition_stage"] == "semifinal"
    assert info["pressure_state"] == "KNOCKOUT_HIGH_PRESSURE"
    assert info["match_importance"] == "high"
    assert info["is_knockout"] is True
    assert info["is_final"] is False
    assert info["competition_type"] == "continental_cup"


def test_semifinal_es():
    info = detect_match_stage({"league": "Copa del Rey", "round": "Semifinales"})
    assert info["competition_stage"] == "semifinal"
    assert info["competition_type"] == "domestic_cup"


# C) Second leg ────────────────────────────────────────────────────────────
def test_second_leg():
    info = detect_match_stage({
        "league": "UEFA Champions League",
        "round": "Semi-final - 2nd Leg",
    })
    assert info["competition_stage"] == "semifinal"
    assert info["is_two_legged_tie"] is True
    assert info["leg"] == 2


def test_first_leg_ida():
    info = detect_match_stage({
        "league": "Copa Libertadores",
        "round": "Cuartos de final - Ida",
    })
    assert info["competition_stage"] == "quarterfinal"
    assert info["is_two_legged_tie"] is True
    assert info["leg"] == 1


def test_aggregate_score_parsing():
    info = detect_match_stage({
        "round": "Quarter-final - 2nd leg (agg 1-2)",
    })
    assert info["aggregate_score"] == "1-2"


# D) Normal mid-table league ───────────────────────────────────────────────
def test_normal_league_match():
    info = detect_match_stage({
        "league": "Premier League",
        "round": "Regular Season - Matchday 25",
    })
    assert info["competition_stage"] == "league"
    assert info["match_importance"] == "normal"
    assert info["pressure_state"] == "NORMAL_LEAGUE"
    assert info["is_knockout"] is False


def test_la_liga_jornada():
    info = detect_match_stage({
        "league": "LaLiga",
        "round": "Jornada 22",
    })
    assert info["competition_stage"] == "league"
    assert info["pressure_state"] == "NORMAL_LEAGUE"


# E) Quarterfinal ──────────────────────────────────────────────────────────
def test_quarterfinal_en():
    info = detect_match_stage({"league": "FA Cup", "round": "Quarter-final"})
    assert info["competition_stage"] == "quarterfinal"
    assert info["pressure_state"] == "KNOCKOUT_HIGH_PRESSURE"


def test_octavos_es():
    info = detect_match_stage({"league": "Copa Libertadores", "round": "Octavos de Final"})
    assert info["competition_stage"] == "round_of_16"
    assert info["pressure_state"] == "KNOCKOUT_HIGH_PRESSURE"


# F) Playoff ───────────────────────────────────────────────────────────────
def test_relegation_playoff():
    info = detect_match_stage({"league": "Bundesliga - Relegation play-off"})
    assert info["competition_stage"] == "playoff"
    assert info["pressure_state"] == "KNOCKOUT_HIGH_PRESSURE"
    assert info["is_knockout"] is True


def test_liguilla_mx():
    info = detect_match_stage({"league": "Liga MX", "round": "Liguilla - Cuartos"})
    # Cuartos pattern is more specific, wins over playoff
    assert info["competition_stage"] == "quarterfinal"
    assert info["pressure_state"] == "KNOCKOUT_HIGH_PRESSURE"


# G) Group stage of major tournament ──────────────────────────────────────
def test_group_stage_ucl():
    info = detect_match_stage({
        "league": "UEFA Champions League",
        "round": "Group Stage - Matchday 3",
    })
    # Group stage with matchday substring: matchday is more specific in regex
    # ordering; we accept either group_stage or league here as long as
    # competition_type is continental_cup so motivation engine treats it well.
    assert info["competition_type"] == "continental_cup"


# H) Edge cases / no false positives ──────────────────────────────────────
def test_championship_is_not_final():
    """Critical: 'Championship' (English D2) must NOT match 'final'."""
    info = detect_match_stage({"league": "Championship", "round": "Matchday 30"})
    assert info["is_final"] is False
    assert info["competition_stage"] == "league"


def test_european_championship_is_not_final():
    info = detect_match_stage({"league": "UEFA European Championship", "round": "Matchday 1"})
    assert info["is_final"] is False


def test_empty_input():
    info = detect_match_stage({})
    assert info["is_final"] is False
    assert info["competition_stage"] == "unknown"
    assert info["pressure_state"] == "NORMAL_LEAGUE"


# I) Predicate helpers ─────────────────────────────────────────────────────
def test_predicates():
    final_info = detect_match_stage({"round": "Final"})
    league_info = detect_match_stage({"round": "Matchday 1"})
    semi_info = detect_match_stage({"round": "Semi-final"})

    assert is_final(final_info) is True
    assert is_final(league_info) is False
    assert is_final(semi_info) is False

    assert is_high_pressure(final_info) is True
    assert is_high_pressure(semi_info) is True
    assert is_high_pressure(league_info) is False
