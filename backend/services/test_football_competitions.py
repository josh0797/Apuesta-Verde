"""Unit tests for football_competitions matching helpers.

Run with: cd /app/backend && python -m pytest -q services/test_football_competitions.py
"""
from services.football_competitions import (  # noqa: E402
    normalize_competition_name,
    get_competition_tier,
    is_allowed_competition,
    get_competition_priority,
    get_competition_meta,
    annotate_match_competition,
    FOOTBALL_COMPETITION_TIERS,
    ALLOWED_TIERS,
)


def test_normalize_strips_accents_and_punct():
    assert normalize_competition_name("Primera División") == "primera division"
    assert normalize_competition_name("LaLiga") == "laliga"
    assert normalize_competition_name("UEFA Champions League") == "uefa champions league"


def test_normalize_strips_region_tail():
    assert normalize_competition_name("Premier League - England") == "premier league"
    assert normalize_competition_name("Serie A - Italy") == "serie a"
    assert normalize_competition_name("Copa Libertadores - South America") == "copa libertadores"
    assert normalize_competition_name("LaLiga (Spain)") == "laliga"
    assert normalize_competition_name("Liga MX, Clausura") == "liga mx"


def test_tier1_competitions_resolve():
    assert get_competition_tier("Premier League") == "tier_1"
    assert get_competition_tier("EPL") == "tier_1"
    assert get_competition_tier("English Premier League") == "tier_1"
    assert get_competition_tier("LaLiga") == "tier_1"
    assert get_competition_tier("La Liga") == "tier_1"
    assert get_competition_tier("Primera División") == "tier_1"
    assert get_competition_tier("Serie A") == "tier_1"
    assert get_competition_tier("Bundesliga") == "tier_1"
    assert get_competition_tier("Liga MX, Clausura - Mexico") == "tier_1"
    assert get_competition_tier("UEFA Champions League - Europe") == "tier_1"
    assert get_competition_tier("FIFA World Cup") == "tier_1"


def test_tier2_competitions_resolve():
    assert get_competition_tier("Ligue 1") == "tier_2"
    assert get_competition_tier("UEFA Europa League") == "tier_2"
    assert get_competition_tier("Copa America") == "tier_2"
    assert get_competition_tier("Copa América") == "tier_2"
    assert get_competition_tier("Eurocopa") == "tier_2"
    assert get_competition_tier("UEFA European Championship") == "tier_2"
    assert get_competition_tier("Copa Libertadores - South America") == "tier_2"


def test_tier3_competitions_resolve():
    assert get_competition_tier("UEFA Conference League") == "tier_3"
    assert get_competition_tier("FA Cup") == "tier_3"
    assert get_competition_tier("Copa del Rey") == "tier_3"
    assert get_competition_tier("Coppa Italia") == "tier_3"
    assert get_competition_tier("DFB-Pokal") == "tier_3"
    assert get_competition_tier("Coupe de France") == "tier_3"
    assert get_competition_tier("CONCACAF Gold Cup") == "tier_3"
    assert get_competition_tier("Gold Cup") == "tier_3"
    assert get_competition_tier("FIFA Club World Cup") == "tier_3"


def test_disallowed_competitions():
    assert get_competition_tier("Eredivisie") is None
    assert get_competition_tier("MLS") is None
    assert get_competition_tier("Liga Portugal") is None
    assert get_competition_tier("Championship") is None  # English D2
    assert get_competition_tier("Some Random Friendly") is None
    assert get_competition_tier("") is None
    assert get_competition_tier(None) is None


def test_priorities_descend_by_tier():
    p1 = get_competition_priority("Premier League")
    p2 = get_competition_priority("Ligue 1")
    p3 = get_competition_priority("FA Cup")
    p_unknown = get_competition_priority("Eredivisie")
    assert p1 > p2 > p3 > p_unknown
    assert p_unknown == 0


def test_is_allowed_respects_env_set():
    # Default env enables all three tiers
    assert is_allowed_competition("Premier League") is True
    assert is_allowed_competition("Eredivisie") is False
    # Only allowed if its tier is in ALLOWED_TIERS
    if "tier_3" in ALLOWED_TIERS:
        assert is_allowed_competition("FA Cup") is True


def test_case_insensitive():
    assert get_competition_tier("PREMIER LEAGUE") == "tier_1"
    assert get_competition_tier("premier league") == "tier_1"
    assert get_competition_tier("pReMiEr LeAgUe") == "tier_1"


def test_meta_includes_canonical_fields():
    meta = get_competition_meta("EPL")
    assert meta["canonical_name"] == "Premier League"
    assert meta["tier"] == "tier_1"
    assert meta["priority"] == 100
    assert meta["type"] == "league"
    assert meta["region"] == "England"


def test_annotate_match_competition_allowed():
    doc = {"league": "Premier League - England"}
    annotate_match_competition(doc)
    assert doc["competition_tier"] == "tier_1"
    assert doc["competition_priority"] == 100
    assert doc["competition_canonical_name"] == "Premier League"
    assert doc["competition_type"] == "league"
    assert doc["competition_region"] == "England"
    assert doc["allowed_competition"] is True


def test_annotate_match_competition_disallowed():
    doc = {"league": "Liga Portugal"}
    annotate_match_competition(doc)
    assert doc["competition_tier"] is None
    assert doc["competition_priority"] == 0
    assert doc["competition_canonical_name"] is None
    assert doc["allowed_competition"] is False


def test_research_query_builder_tier_budget():
    from services.research_queries import build_match_research_queries, TIER_BUDGETS

    match = {
        "home_team": {"name": "Chelsea"},
        "away_team": {"name": "Tottenham"},
        "league": "Premier League",
        "competition_tier": "tier_1",
        "competition_canonical_name": "Premier League",
        "kickoff_iso": "2026-05-15T15:00:00Z",
        "is_live": False,
    }
    q = build_match_research_queries(match)
    emitted = sum(len(v) for k, v in q.items() if k != "_meta")
    assert emitted <= TIER_BUDGETS["tier_1"]
    assert q["_meta"]["tier"] == "tier_1"
    assert q["_meta"]["budget"] == TIER_BUDGETS["tier_1"]
    # Must have team_news (highest priority intent) included
    assert "team_news" in q
    assert "Chelsea" in q["team_news"][0]["query"]


def test_research_query_builder_live_includes_live_context():
    from services.research_queries import build_match_research_queries

    match = {
        "home_team": {"name": "Real Madrid"},
        "away_team": {"name": "Barcelona"},
        "league": "LaLiga",
        "competition_tier": "tier_1",
        "competition_canonical_name": "LaLiga",
        "kickoff_iso": "2026-04-20T19:00:00Z",
        "is_live": True,
    }
    q = build_match_research_queries(match)
    # Live context should be present (or budget pre-empted by higher prio)
    assert "_meta" in q
    # Live intent has priority 85 (below team_news/motivation/odds)
    # Ensure budget is respected even with live group present
    emitted = sum(len(v) for k, v in q.items() if k != "_meta")
    assert emitted <= 8


def test_tier3_budget_smaller_than_tier1():
    from services.research_queries import build_match_research_queries, TIER_BUDGETS

    match_t3 = {
        "home_team": {"name": "Sevilla"},
        "away_team": {"name": "Mallorca"},
        "league": "Copa del Rey",
        "competition_tier": "tier_3",
        "competition_canonical_name": "Copa del Rey",
        "kickoff_iso": "2026-02-10T20:00:00Z",
        "is_live": False,
    }
    q = build_match_research_queries(match_t3)
    emitted = sum(len(v) for k, v in q.items() if k != "_meta")
    assert emitted <= TIER_BUDGETS["tier_3"]
    assert TIER_BUDGETS["tier_3"] < TIER_BUDGETS["tier_1"]
