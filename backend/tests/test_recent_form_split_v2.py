"""Tests for the rewritten mlb_recent_form_split (schedule + boxscore).

The previous implementation called ``/teams/{id}/stats?stats=lastXGames``
which returned the same season-to-date split for any ``limit`` value —
so L5 and L15 ended up identical (Δ=0.0) and the UI was useless.

These tests monkeypatch the HTTP layer to assert:
  - Schedule + boxscore are called and aggregated correctly.
  - The L5 and L15 windows produce DIFFERENT averages when the per-game
    inputs differ.
  - Δ_5_vs_15 is computed exactly.
  - times_on_base = hits + walks + hbp.
"""
import httpx
import pytest

from services.mlb_recent_form_split import (
    get_team_recent_form,
    build_recent_form_payload,
    _aggregate,
    _classify_run_trend,
    _classify_on_base_trend,
    _SCHEDULE_CACHE,
    _BOX_CACHE,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    _SCHEDULE_CACHE.clear()
    _BOX_CACHE.clear()
    yield
    _SCHEDULE_CACHE.clear()
    _BOX_CACHE.clear()


def _fake_schedule(team_id: int, n_games: int = 16) -> dict:
    """Build a minimal MLB Stats API schedule response with N FINAL games."""
    dates = []
    for i in range(n_games):
        game_pk = 700000 + i
        dates.append({
            "date": f"2026-05-{(31 - i):02d}",
            "games": [{
                "gamePk": game_pk,
                "status": {"abstractGameState": "Final"},
                "teams": {
                    "home": {"team": {"id": team_id}, "score": 5},
                    "away": {"team": {"id": 999},     "score": 3},
                },
            }],
        })
    return {"dates": dates}


def _fake_boxscore(team_id: int, runs: int, hits: int, walks: int,
                   hbp: int, hr: int, obp: str = ".320") -> dict:
    return {
        "teams": {
            "home": {
                "team": {"id": team_id},
                "teamStats": {"batting": {
                    "runs": runs, "hits": hits, "baseOnBalls": walks,
                    "hitByPitch": hbp, "homeRuns": hr, "obp": obp,
                    "plateAppearances": 38,
                }},
            },
            "away": {
                "team": {"id": 999},
                "teamStats": {"batting": {
                    "runs": 3, "hits": 7, "baseOnBalls": 2, "hitByPitch": 0,
                    "homeRuns": 1, "obp": ".300", "plateAppearances": 34,
                }},
            },
        }
    }


# ── _aggregate ──────────────────────────────────────────────────────────
def test_aggregate_basic_means():
    lines = [
        {"runs": 5, "hits": 9, "walks": 3, "hbp": 0, "home_runs": 2, "obp": 0.330},
        {"runs": 3, "hits": 6, "walks": 2, "hbp": 1, "home_runs": 1, "obp": 0.310},
    ]
    out = _aggregate(lines)
    assert out["runs"] == 4.0
    assert out["hits"] == 7.5
    assert out["walks"] == 2.5
    assert out["hbp"] == 0.5
    assert out["home_runs"] == 1.5
    assert abs(out["obp"] - 0.320) < 1e-6
    assert out["games"] == 2


def test_aggregate_empty_returns_empty():
    assert _aggregate([]) == {}


# ── classifiers (smoke for the new constants) ───────────────────────────
def test_run_trend_threshold_125():
    assert _classify_run_trend(1.25) == "RISING_RUN_ENVIRONMENT"
    assert _classify_run_trend(1.24) == "STABLE_RUN_ENVIRONMENT"
    assert _classify_run_trend(-1.25) == "DECLINING_RUN_ENVIRONMENT"
    assert _classify_run_trend(None) == "UNKNOWN_RUN_ENVIRONMENT"


def test_ob_trend_threshold_15():
    assert _classify_on_base_trend(1.5) == "RISING_ON_BASE_PRESSURE"
    assert _classify_on_base_trend(1.49) == "STABLE_ON_BASE_PRESSURE"
    assert _classify_on_base_trend(-1.5) == "DECLINING_ON_BASE_PRESSURE"


# ── End-to-end with monkeypatched HTTP ──────────────────────────────────
class _FakeClient:
    """Minimal AsyncClient stand-in. Routes URLs to a response map."""
    def __init__(self, route_map: dict):
        self.route_map = route_map
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        # Strip base URL.
        if url in self.route_map:
            payload = self.route_map[url]
        else:
            payload = None
        if payload is None:
            return httpx.Response(404, request=httpx.Request("GET", url))
        if isinstance(payload, httpx.Response):
            payload._request = httpx.Request("GET", url)
            return payload
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


@pytest.mark.asyncio
async def test_get_team_recent_form_distinct_l5_vs_l15():
    """The crux fix: L5 must differ from L15 when per-game lines differ.

    We mock 15 boxscores where the FIRST 5 games are "hot" (runs=8) and
    games 6-15 are "cold" (runs=2). Expected:
      L5  runs avg = 8.0
      L15 runs avg = (5*8 + 10*2) / 15 = 60/15 = 4.0
      Δ = 4.0
    """
    team_id = 147
    route_map = {
        "https://statsapi.mlb.com/api/v1/schedule":
            _fake_schedule(team_id, n_games=15),
    }
    for i in range(15):
        pk = 700000 + i
        if i < 5:
            box = _fake_boxscore(team_id, runs=8, hits=12, walks=4, hbp=1, hr=3, obp=".380")
        else:
            box = _fake_boxscore(team_id, runs=2, hits=5,  walks=1, hbp=0, hr=0, obp=".280")
        route_map[f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore"] = box

    client = _FakeClient(route_map)
    form = await get_team_recent_form(client, team_id, 2026)

    assert form, "form payload must not be empty"
    assert form["runs_scored_avg_last_5"]  == 8.0
    assert form["runs_scored_avg_last_15"] == 4.0
    # Times on base = hits + walks + hbp.
    # L5:  12 + 4 + 1 = 17
    # L15: (5*(12+4+1) + 10*(5+1+0)) / 15 = (85 + 60) / 15 = 9.667
    assert form["times_on_base_avg_last_5"] == 17.0
    assert abs(form["times_on_base_avg_last_15"] - 9.667) < 0.01
    assert form["home_runs_avg_last_5"]  == 3.0
    assert form["home_runs_avg_last_15"] == 1.0
    assert form["games_played_last_5"]  == 5
    assert form["games_played_last_15"] == 15


@pytest.mark.asyncio
async def test_empty_schedule_returns_empty_form():
    client = _FakeClient({
        "https://statsapi.mlb.com/api/v1/schedule": {"dates": []},
    })
    form = await get_team_recent_form(client, 147, 2026)
    assert form == {}


@pytest.mark.asyncio
async def test_schedule_error_returns_empty_form_failsoft():
    client = _FakeClient({
        "https://statsapi.mlb.com/api/v1/schedule":
            httpx.Response(503, request=httpx.Request("GET", "/")),
    })
    form = await get_team_recent_form(client, 147, 2026)
    assert form == {}


@pytest.mark.asyncio
async def test_build_payload_combined_delta_rising_run_env():
    """Two teams both rising → combined trend = RISING_RUN_ENVIRONMENT."""
    home_form = {
        "runs_scored_avg_last_5": 6.0,
        "runs_scored_avg_last_15": 4.0,
        "hits_avg_last_5": 10.0,   "hits_avg_last_15": 8.0,
        "walks_avg_last_5": 4.0,   "walks_avg_last_15": 3.0,
        "hbp_avg_last_5": 0.2,     "hbp_avg_last_15": 0.1,
        "home_runs_avg_last_5": 2.0, "home_runs_avg_last_15": 1.0,
        "times_on_base_avg_last_5": 14.2, "times_on_base_avg_last_15": 11.1,
        "obp_last_5": 0.350, "obp_last_15": 0.320,
    }
    away_form = {
        "runs_scored_avg_last_5": 4.0,
        "runs_scored_avg_last_15": 3.0,
        "hits_avg_last_5": 8.0,    "hits_avg_last_15": 7.0,
        "walks_avg_last_5": 3.0,   "walks_avg_last_15": 2.5,
        "hbp_avg_last_5": 0.0,     "hbp_avg_last_15": 0.0,
        "home_runs_avg_last_5": 1.0, "home_runs_avg_last_15": 0.5,
        "times_on_base_avg_last_5": 11.0, "times_on_base_avg_last_15": 9.5,
        "obp_last_5": 0.310, "obp_last_15": 0.290,
    }
    payload = build_recent_form_payload(home_form, away_form)

    rs = payload["recent_run_split"]
    assert rs["total_runs_avg_last_5"]  == 10.0
    assert rs["total_runs_avg_last_15"] == 7.0
    assert rs["total_runs_delta_5_vs_15"] == 3.0
    assert payload["recent_run_trend"] == "RISING_RUN_ENVIRONMENT"

    home_ob = payload["on_base_profile"]["home"]
    assert home_ob["hits_delta_5_vs_15"] == 2.0
    assert home_ob["walks_delta_5_vs_15"] == 1.0
    assert home_ob["home_runs_delta_5_vs_15"] == 1.0
    assert home_ob["times_on_base_delta_5_vs_15"] == 3.1
    assert home_ob["trend"] == "RISING_ON_BASE_PRESSURE"

    combined = payload["on_base_profile"]["combined"]
    assert combined["times_on_base_avg_last_5"]  == 25.2
    assert combined["times_on_base_avg_last_15"] == 20.6
    assert combined["times_on_base_delta_5_vs_15"] == 4.6
    assert combined["trend"] == "RISING_ON_BASE_PRESSURE"
    assert combined["home_runs_delta_5_vs_15"] == 1.5
