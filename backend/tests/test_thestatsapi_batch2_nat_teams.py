"""Tests for TheStatsAPI Batch 2 — national-team detection, country
aliases (ES↔EN), stats enrichment, and the real "Bélgica vs Croacia"
deduplication scenario.

All tests use in-process fixtures + mocked httpx transports — no real
network calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from services import football_live_aggregator as agg
from services.external_sources import national_team_detector as ntd
from services.external_sources import thestatsapi_normalizer as ts_norm


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────
def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _enable_env(monkeypatch):
    monkeypatch.setenv("THESTATSAPI_KEY", "test-fake-key")
    monkeypatch.setenv("THESTATSAPI_BASE_URL", "https://api.thestatsapi.com/api")
    monkeypatch.setenv("ENABLE_THE_STATS_API", "true")
    yield


# ──────────────────────────────────────────────────────────────────────
# 1) National-team detector — basic vocab
# ──────────────────────────────────────────────────────────────────────
def test_normalize_country_name_strips_accents_and_lowercases():
    assert ntd.normalize_country_name("Bélgica")     == "belgium"
    assert ntd.normalize_country_name("CROACIA")     == "croatia"
    assert ntd.normalize_country_name("Alemania")    == "germany"
    assert ntd.normalize_country_name("España")      == "spain"
    assert ntd.normalize_country_name("Inglaterra")  == "england"
    assert ntd.normalize_country_name("USA")         == "united states"
    assert ntd.normalize_country_name("United States") == "united states"


def test_normalize_country_name_handles_empty():
    assert ntd.normalize_country_name(None) == ""
    assert ntd.normalize_country_name("")   == ""
    assert ntd.normalize_country_name("   ") == ""


def test_is_national_team_name_recognises_aliases():
    assert ntd.is_national_team_name("Bélgica") is True
    assert ntd.is_national_team_name("Belgium") is True
    assert ntd.is_national_team_name("Croacia") is True
    assert ntd.is_national_team_name("Croatia") is True
    assert ntd.is_national_team_name("Argentina") is True
    assert ntd.is_national_team_name("São Tomé and Príncipe") is True


def test_is_national_team_name_rejects_clubs():
    assert ntd.is_national_team_name("Real Madrid") is False
    assert ntd.is_national_team_name("Manchester United") is False
    assert ntd.is_national_team_name("Renaissance Zemamra") is False
    assert ntd.is_national_team_name("") is False
    assert ntd.is_national_team_name(None) is False


def test_country_canonical_returns_none_for_clubs():
    assert ntd.country_canonical("Bélgica")     == "belgium"
    assert ntd.country_canonical("Belgium")     == "belgium"
    assert ntd.country_canonical("Real Madrid") is None


# ──────────────────────────────────────────────────────────────────────
# 2) International-competition keywords
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,expected", [
    ("FIFA World Cup",                      True),
    ("UEFA Euro 2024",                      True),
    ("UEFA Nations League",                 True),
    ("Copa America 2024",                   True),
    ("Copa América",                        True),
    ("CONCACAF Gold Cup",                   True),
    ("AFCON 2025",                          True),
    ("Africa Cup of Nations",               True),
    ("AFC Asian Cup",                       True),
    ("International Friendly",              True),
    ("World Cup Qualification Europe",      True),
    ("Eliminatorias CONMEBOL",              True),
    ("Amistoso Internacional",              True),
    ("Eurocopa Femenina",                   True),
    ("Premier League",                      False),
    ("La Liga",                             False),
    ("Bundesliga",                          False),
    ("Champions League",                    False),
    ("",                                    False),
])
def test_is_international_competition_keywords(name, expected):
    assert ntd.is_international_competition(league_name=name) is expected


def test_is_international_competition_by_region():
    assert ntd.is_international_competition(league_country="World") is True
    assert ntd.is_international_competition(league_country="International") is True
    assert ntd.is_international_competition(league_country="Europe") is True
    assert ntd.is_international_competition(league_country="South America") is True
    assert ntd.is_international_competition(league_country="Spain") is False
    assert ntd.is_international_competition(league_country=None) is False


# ──────────────────────────────────────────────────────────────────────
# 3) is_national_team_match — combined decision
# ──────────────────────────────────────────────────────────────────────
def test_is_national_team_match_both_teams_known():
    """The canonical "Bélgica vs Croacia" case — both names are FIFA
    nations after ES→EN alias resolution. Should be True even without
    any competition info."""
    assert ntd.is_national_team_match("Bélgica", "Croacia") is True


def test_is_national_team_match_one_team_unknown_with_intl_comp():
    """If only one team matches but the comp is international, still True."""
    # 'Belgium U21' — not in our FIFA list (it's the senior list)
    assert ntd.is_national_team_match(
        "Belgium U21", "Croatia U21",
        league_name="UEFA Euro U21 Qualification",
    ) is True


def test_is_national_team_match_clubs_in_non_intl_comp():
    assert ntd.is_national_team_match(
        "Real Madrid", "Barcelona",
        league_name="La Liga",
        league_country="Spain",
    ) is False


def test_is_national_team_match_clubs_with_friendly_name_collision():
    """Defensive: clubs occasionally share names with countries
    (Liechtenstein FC). The `is_national_team_match` predicate
    requires BOTH ends to match, so a single match is ignored
    when the comp isn't international."""
    assert ntd.is_national_team_match(
        "Liechtenstein", "Vaduz",
        league_name="Liechtenstein Cup",
        league_country="Liechtenstein",
    ) is False


# ──────────────────────────────────────────────────────────────────────
# 4) Normalizer integration — auto-detect is_national_team
# ──────────────────────────────────────────────────────────────────────
def test_normalize_match_auto_flags_national_team_via_detector():
    """TheStatsAPI doesn't ship is_national_team flags. The normalizer
    must auto-detect via the team names + competition name."""
    raw = {
        "id": "mt_999",
        "competition_id": "comp_5",
        "competition_name": "UEFA Nations League",
        "status": "first_half",
        "utc_date": "2026-09-05T19:00:00.000Z",
        "home_team": {"id": "tm_1", "name": "Belgium"},
        "away_team": {"id": "tm_2", "name": "Croatia"},
    }
    n = ts_norm.normalize_match(raw)
    assert n is not None
    assert n["_is_national_team"] is True
    assert n["_is_international"] is True


def test_normalize_match_does_not_flag_club_fixture():
    raw = {
        "id": "mt_1", "competition_id": "comp_1",
        "competition_name": "Premier League",
        "country": "England",
        "status": "live", "utc_date": "2026-06-15T15:00:00Z",
        "home_team": {"id": "tm_1", "name": "Manchester City"},
        "away_team": {"id": "tm_2", "name": "Arsenal"},
    }
    n = ts_norm.normalize_match(raw)
    assert n is not None
    assert n["_is_national_team"] is False
    assert n["_is_international"] is False


# ──────────────────────────────────────────────────────────────────────
# 5) Aggregator — country-aware dedupe (Bélgica ↔ Belgium)
# ──────────────────────────────────────────────────────────────────────
def _af_fixture(home: str, away: str, ts: int, fid: int = 1) -> dict:
    return {
        "fixture": {"id": fid, "date": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "timestamp": ts, "status": {"short": "1H"}, "venue": {"name": None}},
        "league": {"id": 5, "name": "UEFA Nations League", "season": 2026,
                   "logo": None, "country": "World", "round": None},
        "teams": {"home": {"id": 10, "name": home, "logo": None},
                  "away": {"id": 20, "name": away, "logo": None}},
        "goals": {"home": 0, "away": 0},
    }


def _ts_fixture_nat(home: str, away: str, ts: int, raw_id: str = "mt_5050") -> dict:
    return ts_norm.normalize_match({
        "id": raw_id,
        "competition_id": "comp_5", "competition_name": "UEFA Nations League",
        "teams": {"home": {"id": "tm_1", "name": home},
                  "away": {"id": "tm_2", "name": away}},
        "date": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "status": "first_half",
    })


def test_dedupe_belgica_vs_belgium_alias_match():
    """API-Sports → 'Bélgica vs Croacia' (some feeds localise);
    TheStatsAPI → 'Belgium vs Croatia'. Must collapse to a single
    fixture with both providers tagged in `_external_sources_covered`.
    """
    ts = 1_700_000_000
    primary = [_af_fixture("Bélgica", "Croacia", ts, fid=42)]
    secondary = [_ts_fixture_nat("Belgium", "Croatia", ts, raw_id="mt_777")]
    merged, meta = agg.merge_and_deduplicate(primary, secondary)

    assert meta["duplicates_dropped"] == 1
    assert meta["secondary_added"] == 0
    assert meta["total"] == 1

    fx = merged[0]
    # Primary team names are preserved (we don't rewrite display names)
    assert fx["teams"]["home"]["name"] == "Bélgica"
    assert fx["teams"]["away"]["name"] == "Croacia"
    # But provenance lists both providers
    assert sorted(fx["_external_sources_covered"]) == ["api_sports", "thestatsapi"]
    # And the TheStatsAPI raw id is grafted onto the primary so
    # _enrich_football can pull xG/shots later. The primary's own
    # `_external_source_id` (API-Sports fixture id) is preserved
    # untouched.
    assert fx["_thestatsapi_raw_id"] == "mt_777"
    assert fx["_external_source_id"] == 42   # API-Sports fixture id (unchanged)
    assert fx["_external_source"] == "api_sports"
    # National-team flag inherited from secondary
    assert fx.get("_is_national_team") is True


def test_dedupe_germany_vs_alemania_alias_match():
    ts = 1_700_000_000
    primary = [_af_fixture("Alemania", "Francia", ts)]
    secondary = [_ts_fixture_nat("Germany", "France", ts + 120)]   # 2min later
    merged, meta = agg.merge_and_deduplicate(primary, secondary)
    assert meta["duplicates_dropped"] == 1
    assert meta["total"] == 1


def test_dedupe_does_not_collapse_unrelated_clubs():
    """Two club games with similar but not country-mapped names must
    stay distinct."""
    ts = 1_700_000_000
    primary = [_af_fixture("Real Madrid", "Barcelona", ts)]
    secondary = [_ts_fixture_nat("Argentina", "Brazil", ts)]
    merged, meta = agg.merge_and_deduplicate(primary, secondary)
    assert meta["total"] == 2


# ──────────────────────────────────────────────────────────────────────
# 6) Match-stats normalizer (xG / shots / possession)
# ──────────────────────────────────────────────────────────────────────
def test_normalize_match_stats_flat_layout():
    raw = {
        "home": {"xg": 1.42, "shots_total": 12, "shots_on_target": 4, "possession": 56.2,
                 "corners": 6, "passes": 412, "fouls": 9},
        "away": {"xg": 0.83, "shots_total": 8,  "shots_on_target": 2, "possession": 43.8,
                 "corners": 3, "passes": 295, "fouls": 12},
        "score": {"home": 1, "away": 0},
        "minute": 67,
        "status": "second_half",
    }
    n = ts_norm.normalize_match_stats(raw)
    assert n is not None
    assert n["status"] == "2H"
    assert n["minute"] == 67
    assert n["score"] == {"home": 1, "away": 0}
    assert n["home_stats"]["expected_goals"] == 1.42
    assert n["home_stats"]["Total Shots"] == 12
    assert n["home_stats"]["Shots on Goal"] == 4
    # Possession formatted as "56%" string (API-Sports compat)
    assert n["home_stats"]["Ball Possession"] == "56%"
    assert n["away_stats"]["expected_goals"] == 0.83
    assert n["_source"] == "thestatsapi"


def test_normalize_match_stats_team_keyed_layout():
    raw = {
        "home_team_stats": {"xg_total": 2.1, "shots": 15, "possession_percent": 0.62},
        "away_team_stats": {"xg_total": 0.9, "shots": 6,  "possession_percent": 0.38},
        "score": {"home": 2, "away": 1},
    }
    n = ts_norm.normalize_match_stats(raw)
    assert n is not None
    # possession given as 0..1 fraction → must come out as percentage string
    assert n["home_stats"]["Ball Possession"] == "62%"
    assert n["home_stats"]["expected_goals"] == 2.1


def test_normalize_match_stats_returns_none_on_empty():
    assert ts_norm.normalize_match_stats({}) is None
    assert ts_norm.normalize_match_stats({"home": {}, "away": {}}) is None
    assert ts_norm.normalize_match_stats(None) is None
    assert ts_norm.normalize_match_stats({"home": {"unknown_field": 5}, "away": {}}) is None


def test_normalize_match_stats_unknown_fields_skipped():
    raw = {
        "home": {"xg": 1.0, "garbage_field": 999, "another_thing": "x"},
        "away": {"xg": 0.5},
    }
    n = ts_norm.normalize_match_stats(raw)
    assert n is not None
    assert "garbage_field" not in n["home_stats"]
    assert "another_thing" not in n["home_stats"]
    assert n["home_stats"]["expected_goals"] == 1.0


# ──────────────────────────────────────────────────────────────────────
# 7) merge_live_stats — primary wins on non-empty values
# ──────────────────────────────────────────────────────────────────────
def test_merge_live_stats_primary_wins_on_overlap():
    primary = {
        "home_stats": {"Total Shots": 10, "Ball Possession": "55%"},
        "away_stats": {"Total Shots": 5,  "Ball Possession": "45%"},
        "_source": "api_sports",
    }
    secondary = {
        "home_stats": {"Total Shots": 999, "expected_goals": 1.5},
        "away_stats": {"expected_goals": 0.7},
        "_source": "thestatsapi",
    }
    merged = ts_norm.merge_live_stats(primary, secondary)
    # primary wins on `Total Shots`
    assert merged["home_stats"]["Total Shots"] == 10
    # secondary fills the missing xG
    assert merged["home_stats"]["expected_goals"] == 1.5
    assert merged["away_stats"]["expected_goals"] == 0.7
    assert "api_sports" in merged["_sources"]
    assert "thestatsapi" in merged["_sources"]


def test_merge_live_stats_handles_nones():
    assert ts_norm.merge_live_stats(None, None) is None
    p = {"home_stats": {"a": 1}, "away_stats": {}}
    s = {"home_stats": {"b": 2}, "away_stats": {"c": 3}}
    assert ts_norm.merge_live_stats(p, None) == p
    assert ts_norm.merge_live_stats(None, s) == s
    merged = ts_norm.merge_live_stats(p, s)
    assert merged["home_stats"] == {"a": 1, "b": 2}
    assert merged["away_stats"] == {"c": 3}


# ──────────────────────────────────────────────────────────────────────
# 8) Client.fetch_match_stats — happy path + 404 + auth failure
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_client_fetch_match_stats_unwraps_data_wrapper():
    from services.external_sources import thestatsapi_client as ts_client

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/football/matches/mt_555/stats")
        return httpx.Response(200, json={"data": {
            "home": {"xg": 1.1, "shots": 11},
            "away": {"xg": 0.7, "shots": 6},
        }})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_match_stats(c, "mt_555")
    assert "home" in out
    assert out["home"]["xg"] == 1.1


@pytest.mark.asyncio
async def test_client_fetch_match_stats_404_is_empty():
    from services.external_sources import thestatsapi_client as ts_client

    def handler(req):
        return httpx.Response(404, json={"error": "not found"})

    async with _mock_client(handler) as c:
        out = await ts_client.fetch_match_stats(c, "mt_nope")
    assert out == {}
