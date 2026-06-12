"""Phase F67 — TheStatsAPI market normaliser disambiguation tests.

These tests pin the contract that the normaliser MUST distinguish:

    * ``total_goals``         (MATCH total)  vs  ``team_total_goals``  (per-team)
    * ``match_corners``       (MATCH total)  vs  ``team_corners``      (per-team)
    * ``asian_handicap``      vs ``match_odds`` (1X2)
    * ``asian_corners``       vs ``match_corners``

The risk this guards against is mis-labelling "Under 3.5 goles de Brasil"
as the match Under 3.5 (way thinner line, different odds, different
analytical meaning). If the normaliser ever silently merges these, the
editorial engine would recommend the wrong market.
"""
from __future__ import annotations

from services.thestatsapi_client import extract_normalised_markets


# ─────────────────────────────────────────────────────────────────────
# total_goals  vs  team_total_goals
# ─────────────────────────────────────────────────────────────────────
def test_normaliser_picks_match_total_goals_not_team_total_goals() -> None:
    """Even when both markets are present, ``total_goals`` MUST land in
    ``out["total_goals"]`` and ``team_total_goals`` MUST be IGNORED by
    the normaliser (we do not surface per-team goals in the editorial yet,
    so the safe behaviour is to drop them and never confuse the user)."""
    raw = {
        "bookmakers": [{
            "bookmaker": "Kambi",
            "markets": {
                "total_goals": {
                    "2.5": {"over":  {"opening": "1.95", "last_seen": "1.90"},
                            "under": {"opening": "1.85", "last_seen": "1.95"}},
                },
                # IMPORTANT: per-team market. Different odds, different meaning.
                "team_total_goals": {
                    "home": {
                        "3.5": {"over":  {"opening": "3.30", "last_seen": "3.40"},
                                "under": {"opening": "1.30", "last_seen": "1.28"}},
                    },
                    "away": {
                        "3.5": {"over":  {"opening": "5.00", "last_seen": "5.50"},
                                "under": {"opening": "1.16", "last_seen": "1.14"}},
                    },
                },
            },
        }],
    }
    out = extract_normalised_markets(raw)
    # The match line at 2.5 must be present with the MATCH odds.
    assert out["total_goals"]["2.5"]["over"]  == 1.90
    assert out["total_goals"]["2.5"]["under"] == 1.95
    # And the team line at 3.5 must NOT have leaked in.
    assert "3.5" not in out["total_goals"], (
        "team_total_goals[home][3.5] leaked into match total_goals[3.5]")


def test_normaliser_with_only_team_total_goals_returns_empty_total_goals() -> None:
    """If the bookmaker only surfaces per-team lines, the normaliser must
    return an EMPTY ``total_goals`` (rather than synthesising a wrong
    match total)."""
    raw = {
        "bookmakers": [{
            "bookmaker": "Kambi",
            "markets": {
                "team_total_goals": {
                    "home": {"3.5": {"over": {"last_seen": "3.40"},
                                     "under": {"last_seen": "1.28"}}},
                },
            },
        }],
    }
    out = extract_normalised_markets(raw)
    assert out["total_goals"] == {}


# ─────────────────────────────────────────────────────────────────────
# match_corners  vs  team_corners
# ─────────────────────────────────────────────────────────────────────
def test_normaliser_picks_match_corners_not_team_corners() -> None:
    raw = {
        "bookmakers": [{
            "bookmaker": "Kambi",
            "markets": {
                "match_corners": {
                    "9.5": {"over":  {"last_seen": "1.45"},
                            "under": {"last_seen": "2.70"}},
                },
                "team_corners": {
                    "home": {
                        "4.5": {"over":  {"last_seen": "1.65"},
                                "under": {"last_seen": "2.25"}},
                    },
                    "away": {
                        "4.5": {"over":  {"last_seen": "1.95"},
                                "under": {"last_seen": "1.85"}},
                    },
                },
            },
        }],
    }
    out = extract_normalised_markets(raw)
    assert out["match_corners"]["9.5"]["over"] == 1.45
    # team line 4.5 must NOT pollute the match corners dict.
    assert "4.5" not in out["match_corners"]


# ─────────────────────────────────────────────────────────────────────
# asian_corners / asian_handicap  vs  canonical markets
# ─────────────────────────────────────────────────────────────────────
def test_normaliser_ignores_asian_corners_market() -> None:
    """``asian_corners`` uses split / quarter lines (9.0, 9.25, 9.5, 9.75)
    and is NOT the same as Over/Under match_corners. The normaliser must
    only surface canonical match_corners lines so the editorial engine
    never quotes an Asian line as if it were European."""
    raw = {
        "bookmakers": [{
            "bookmaker": "Kambi",
            "markets": {
                "asian_corners": {
                    "9.25": {"over": {"last_seen": "1.55"},
                             "under": {"last_seen": "2.40"}},
                    "9.75": {"over": {"last_seen": "1.75"},
                             "under": {"last_seen": "2.10"}},
                },
            },
        }],
    }
    out = extract_normalised_markets(raw)
    assert out["match_corners"] == {}, "Asian corners leaked into match_corners"


def test_normaliser_ignores_asian_handicap_for_match_odds() -> None:
    """``asian_handicap`` is a different market from ``match_odds``."""
    raw = {
        "bookmakers": [{
            "bookmaker": "Kambi",
            "markets": {
                "asian_handicap": {
                    "home": {"-0.5": {"opening": "1.95", "last_seen": "1.90"}},
                    "away": {"+0.5": {"opening": "1.95", "last_seen": "1.95"}},
                },
            },
        }],
    }
    out = extract_normalised_markets(raw)
    # match_odds dict must stay None when only AH is present.
    assert out["match_odds"] is None


# ─────────────────────────────────────────────────────────────────────
# Bookmaker isolation
# ─────────────────────────────────────────────────────────────────────
def test_normaliser_uses_first_bookmaker_consistently() -> None:
    """When multiple bookmakers are returned, the normaliser must pick
    one (the first by convention) and NOT mix odds across them."""
    raw = {
        "bookmakers": [
            {"bookmaker": "Kambi",
             "markets": {"match_odds": {"home": {"last_seen": "1.71"},
                                         "draw": {"last_seen": "3.80"},
                                         "away": {"last_seen": "4.30"}}}},
            {"bookmaker": "Bet365",
             "markets": {"match_odds": {"home": {"last_seen": "1.66"},
                                         "draw": {"last_seen": "3.90"},
                                         "away": {"last_seen": "5.00"}}}},
        ],
    }
    out = extract_normalised_markets(raw)
    assert out["bookmaker"]            == "Kambi"
    assert out["match_odds"]["home"]   == 1.71
    # Bet365 odds must NOT be present.
    assert out["match_odds"]["home"]  != 1.66


# ─────────────────────────────────────────────────────────────────────
# Edge: btts_yes/no but no "btts" key
# ─────────────────────────────────────────────────────────────────────
def test_normaliser_btts_only_extracted_from_btts_market() -> None:
    raw = {
        "bookmakers": [{
            "bookmaker": "Kambi",
            "markets": {
                # NOT the canonical key — must be ignored.
                "both_teams_to_score": {
                    "yes": {"last_seen": "1.40"},
                    "no":  {"last_seen": "2.90"},
                },
            },
        }],
    }
    out = extract_normalised_markets(raw)
    assert out["btts"] is None


# ─────────────────────────────────────────────────────────────────────
# Editorial engine MUST NOT quote a per-team odds when extracting match
# corners (regression test against P4.4).
# ─────────────────────────────────────────────────────────────────────
def test_corners_pred_does_not_quote_team_corners_odds() -> None:
    """Even if a (broken) odds payload contained a per-team line at the
    same numeric value (4.5), the corners section MUST NOT cite that
    odds. Only the lines under ``out["match_corners"]`` reach the
    editorial engine."""
    from services.football_editorial_prediction import (
        generate_football_editorial_prediction,
    )
    match = {
        "home_team": {"name": "Brazil"}, "away_team": {"name": "Morocco"},
        "home_corners_for_l5":     3.5, "home_corners_against_l5":  3.3,
        "away_corners_for_l5":     3.7, "away_corners_against_l5":  3.6,
        "home_corners_for_l15":    3.5, "home_corners_against_l15": 3.3,
        "away_corners_for_l15":    3.7, "away_corners_against_l15": 3.6,
    }
    # Broken-looking payload — team_corners at 4.5 with team_corners odds.
    broken_odds = {
        "bookmaker": "Kambi",
        "match_corners": {
            "9.5":  {"over": 2.80, "under": 1.42},
            "10.5": {"over": 3.50, "under": 1.28},
        },
        "total_goals":   {},
        "btts":          None,
    }
    out = generate_football_editorial_prediction(match, odds=broken_odds)
    corners = out["editorial_sections"]["corners_prediction"]
    # The engine must have picked 9.5 (match) and odds 1.42 (match-under),
    # never 4.5 / per-team odds.
    assert corners["line"] == 9.5
    assert corners["odds"] == 1.42
