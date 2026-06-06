"""Tests for services/box_score_providers/ — normalization layer only.

The async HTTP paths are NOT exercised here (those require live API
keys + network); we test the pure normalization functions which
translate provider JSON into our internal schema.
"""
from services.box_score_providers import (
    normalize_api_sports_basketball,
    normalize_balldontlie,
    normalize_api_sports_baseball,
    normalize_mlb_statsapi,
)


# ─────────────────────────────────────────────────────────────────────
# Basketball — API-Sports
# ─────────────────────────────────────────────────────────────────────
_API_SPORTS_BASKETBALL_PAYLOAD = {
    "response": [
        {
            "game": {
                "id":   123456,
                "date": "2025-02-07",
                "scores": {
                    "home": {"total": 110},
                    "away": {"total": 102},
                },
                "teams": {"home": {"id": 17}, "away": {"id": 2}},
            },
            "team":               {"id": 17, "name": "Lakers"},
            "field_goals":        {"attempts": 90, "made": 38},
            "threepoint_goals":   {"attempts": 35, "made": 12},
            "freethrows_goals":   {"attempts": 24, "made": 18},
            "rebounds":           {"offence": 10, "defense": 32},
            "turnovers":          {"total": 14},
        },
        {
            "game": {"id": 123456, "date": "2025-02-07",
                     "scores": {"home": {"total": 110}, "away": {"total": 102}},
                     "teams": {"home": {"id": 17}, "away": {"id": 2}}},
            "team": {"id": 2, "name": "Celtics"},
            "field_goals":      {"attempts": 88, "made": 36},
            "threepoint_goals": {"attempts": 38, "made": 14},
            "freethrows_goals": {"attempts": 18, "made": 12},
            "rebounds":         {"offence": 9,  "defense": 30},
            "turnovers":        {"total": 12},
        },
    ]
}


def test_normalize_api_sports_basketball_team_filter():
    rows = normalize_api_sports_basketball(_API_SPORTS_BASKETBALL_PAYLOAD, team_id=17)
    assert len(rows) == 1
    r = rows[0]
    assert r["_provider"] == "api_sports"
    assert r["fga"] == 90 and r["fgm"] == 38
    assert r["three_pa"] == 35 and r["three_pm"] == 12
    assert r["fta"] == 24 and r["ftm"] == 18
    assert r["orb"] == 10 and r["drb"] == 32
    assert r["tov"] == 14
    assert r["pts_for"] == 110 and r["pts_against"] == 102


def test_normalize_api_sports_basketball_team_id_none_returns_all():
    rows = normalize_api_sports_basketball(_API_SPORTS_BASKETBALL_PAYLOAD, team_id=None)
    assert len(rows) == 2


def test_normalize_api_sports_basketball_bad_payload():
    assert normalize_api_sports_basketball(None, team_id=1) == []
    assert normalize_api_sports_basketball({}, team_id=1) == []
    assert normalize_api_sports_basketball({"response": "not-a-list"}, team_id=1) == []


# ─────────────────────────────────────────────────────────────────────
# Basketball — Balldontlie
# ─────────────────────────────────────────────────────────────────────
def _bdl_player(team_id, gid, **overrides):
    base = {
        "team": {"id": team_id},
        "game": {
            "id": gid, "date": "2025-02-07",
            "home_team_id": team_id, "home_team_score": 115, "visitor_team_score": 108,
        },
        "fga": 10, "fgm": 4,
        "fg3a": 5, "fg3m": 2,
        "fta": 4, "ftm": 3,
        "oreb": 1, "dreb": 4,
        "turnover": 1,
        "min": "32:14",
        "pts": 13,
    }
    base.update(overrides)
    return base


def test_normalize_balldontlie_aggregates_player_rows_to_team_totals():
    payload = {"data": [
        _bdl_player(team_id=17, gid=999),
        _bdl_player(team_id=17, gid=999, fga=12, fgm=6, fg3a=4, fg3m=1, oreb=2, turnover=2),
        _bdl_player(team_id=2,  gid=999, fga=20, fgm=8),  # opponent
    ]}
    rows = normalize_balldontlie(payload, team_id=17)
    assert len(rows) == 1
    r = rows[0]
    assert r["_provider"] == "balldontlie"
    assert r["fga"] == 22
    assert r["fgm"] == 10
    assert r["three_pa"] == 9
    assert r["orb"] == 3
    assert r["tov"] == 3
    # Score is read from the game block, not the per-player aggregate.
    assert r["pts_against"] == 108  # visitor_score because home_team_id == 17


def test_normalize_balldontlie_handles_missing_data():
    assert normalize_balldontlie({}, team_id=17) == []
    assert normalize_balldontlie({"data": None}, team_id=17) == []


# ─────────────────────────────────────────────────────────────────────
# Baseball — API-Sports
# ─────────────────────────────────────────────────────────────────────
def test_normalize_api_sports_baseball_with_provider_obp_slg():
    payload = {"response": [{
        "game": {"id": 43210, "date": "2025-06-01",
                 "scores": {"home": {"total": 6}, "away": {"total": 4}},
                 "teams":  {"home": {"id": 156}, "away": {"id": 142}}},
        "team": {"id": 156, "name": "Yankees"},
        "batting": {
            "atBats": 34, "hits": 9, "runs": 4,
            "walks": 3, "strikeouts": 8, "stolenBases": 1,
            "obp": 0.342, "slg": 0.441,
            "homeruns": 1, "doubles": 2, "triples": 0,
        },
    }]}
    rows = normalize_api_sports_baseball(payload, team_id=156)
    assert len(rows) == 1
    r = rows[0]
    assert r["_provider"] == "api_sports"
    assert r["ab"] == 34 and r["h"] == 9 and r["bb"] == 3 and r["k"] == 8
    # Provider OBP/SLG are honoured as-is.
    assert r["obp"] == 0.342 and r["slg"] == 0.441
    # ISO computed locally: SLG (0.441) - AVG (9/34 = 0.2647) ≈ 0.1763
    assert abs(r["iso"] - 0.1763) < 0.001
    assert r["runs_for"] == 6 and r["runs_against"] == 4


# ─────────────────────────────────────────────────────────────────────
# Baseball — MLB StatsAPI
# ─────────────────────────────────────────────────────────────────────
_MLB_STATSAPI_PAYLOAD = {
    "gamePk": 716490,
    "gameData": {"datetime": {"officialDate": "2025-06-01"}},
    "liveData": {
        "boxscore": {"teams": {
            "home": {
                "team": {"id": 147},   # Yankees
                "teamStats": {"batting": {
                    "atBats": 34, "hits": 9, "runs": 4,
                    "baseOnBalls": 3, "strikeOuts": 8, "stolenBases": 1,
                    "homeRuns": 1, "doubles": 2, "triples": 0,
                    "obp": 0.342, "slg": 0.441,
                    "hitByPitch": 1, "sacFlies": 0, "sacBunts": 0,
                }},
            },
            "away": {
                "team": {"id": 111},   # Red Sox
                "teamStats": {"batting": {
                    "atBats": 30, "hits": 6, "runs": 2,
                    "baseOnBalls": 1, "strikeOuts": 10, "stolenBases": 0,
                    "homeRuns": 0, "doubles": 1, "triples": 0,
                    "hitByPitch": 0, "sacFlies": 0, "sacBunts": 0,
                }},
            },
        }},
        "linescore": {"teams": {"home": {"runs": 4}, "away": {"runs": 2}}},
    },
}


def test_normalize_mlb_statsapi_team_filter():
    rows = normalize_mlb_statsapi(_MLB_STATSAPI_PAYLOAD, team_id=147)
    assert len(rows) == 1
    r = rows[0]
    assert r["_provider"] == "mlb_statsapi"
    assert r["team_id"] == 147
    assert r["ab"] == 34 and r["h"] == 9
    assert r["bb"] == 3 and r["k"] == 8
    assert r["runs_for"] == 4 and r["runs_against"] == 2
    assert r["obp"] == 0.342 and r["slg"] == 0.441
    assert r["k_rate"] is not None and 0 < r["k_rate"] < 1
    assert r["bb_rate"] is not None and 0 < r["bb_rate"] < 1


def test_normalize_mlb_statsapi_both_teams_when_no_filter():
    rows = normalize_mlb_statsapi(_MLB_STATSAPI_PAYLOAD, team_id=None)
    assert len(rows) == 2
    sides = {r["team_id"] for r in rows}
    assert sides == {147, 111}


def test_normalize_mlb_statsapi_bad_payload():
    assert normalize_mlb_statsapi(None, team_id=147) == []
    assert normalize_mlb_statsapi({}, team_id=147) == []
    # Missing batting block → skipped, no rows.
    bad = {"liveData": {"boxscore": {"teams": {"home": {"team": {"id": 147}}}}}}
    assert normalize_mlb_statsapi(bad, team_id=147) == []


# ─────────────────────────────────────────────────────────────────────
# Top-level fetchers are async + fail-soft. We assert that they return
# an empty list when the team_id is missing / falsy (cheap unit test
# that doesn't touch the network).
# ─────────────────────────────────────────────────────────────────────
import asyncio   # noqa: E402

from services.box_score_providers import (   # noqa: E402
    fetch_basketball_team_games, fetch_baseball_team_games,
)


def test_fetch_basketball_team_games_empty_id_returns_empty():
    out = asyncio.run(fetch_basketball_team_games(""))
    assert out == []
    out = asyncio.run(fetch_basketball_team_games(None))
    assert out == []


def test_fetch_baseball_team_games_empty_id_returns_empty():
    out = asyncio.run(fetch_baseball_team_games(""))
    assert out == []
    out = asyncio.run(fetch_baseball_team_games(None))
    assert out == []
