"""Unit tests for the MLB backtest runner.

Network calls (StatsAPI) are mocked so the suite is hermetic and runs
in milliseconds. The async DB persistence is tested via a
``FakeMongoDB`` that captures inserts and emits the calls the real
runner expects.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.mlb_backtest_runner import (
    DEFAULT_BACKTEST_USER_ID,
    _approximate_book_line,
    _classify_result,
    _pitcher_score_from_era,
    _validate_date_range,
    build_backtest_scoring_ctx,
    fetch_game_pitchers,
    fetch_historical_schedule,
    run_backtest,
)


# ─────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────
def test_pitcher_score_neutral_when_missing():
    assert _pitcher_score_from_era(None) == 50
    assert _pitcher_score_from_era({}) == 50
    assert _pitcher_score_from_era({"era": None}) == 50
    assert _pitcher_score_from_era({"era": "nope"}) == 50


def test_pitcher_score_elite_vs_terrible():
    # ERA 2.5 → 100, ERA 6.5 → 0 by formula.
    elite = _pitcher_score_from_era({"era": 2.5})
    avg   = _pitcher_score_from_era({"era": 4.5})
    bad   = _pitcher_score_from_era({"era": 6.5})
    assert elite == 100
    assert 40 <= avg <= 60
    assert bad == 0


def test_pitcher_score_clamped():
    # Out-of-range ERAs (very low / sky-high) must clamp.
    assert _pitcher_score_from_era({"era": 1.0}) == 100
    assert _pitcher_score_from_era({"era": 9.0}) == 0


def test_build_backtest_scoring_ctx_shape():
    ctx = build_backtest_scoring_ctx(
        {"era": 3.0, "name": "Ace"},
        {"era": 4.5, "name": "Mid"},
        "Yankees", "Red Sox",
    )
    assert ctx["home_pitcher_quality"]["score"] >= 70
    assert 40 <= ctx["away_pitcher_quality"]["score"] <= 60
    assert ctx["park"]["park_runs_mult"] == 1.0
    assert ctx["home_team"] == "Yankees"
    assert ctx["away_team"] == "Red Sox"
    assert ctx["home_pitcher_name"] == "Ace"
    assert ctx["away_pitcher_name"] == "Mid"


def test_approximate_book_line_ladder():
    assert _approximate_book_line(6.5) == 8.5
    assert _approximate_book_line(8.0) == 9.0
    assert _approximate_book_line(9.0) == 9.5
    assert _approximate_book_line(11.0) == 10.0


def test_approximate_book_line_invalid_defaults():
    assert _approximate_book_line("not a number") == 9.5  # falls back to 9.0 → ladder 9.5


def test_classify_result_branches():
    assert _classify_result(7, 9.0) == "won"
    assert _classify_result(11, 9.0) == "lost"
    assert _classify_result(9, 9.0) == "push"


def test_validate_date_range_happy():
    sd, ed = _validate_date_range("2026-04-01", "2026-04-30")
    assert (ed - sd).days == 29


def test_validate_date_range_bad_format():
    with pytest.raises(ValueError):
        _validate_date_range("04/01/2026", "2026-04-30")


def test_validate_date_range_inverted():
    with pytest.raises(ValueError):
        _validate_date_range("2026-04-30", "2026-04-01")


def test_validate_date_range_too_wide():
    with pytest.raises(ValueError):
        _validate_date_range("2026-01-01", "2026-12-31")


# ─────────────────────────────────────────────────────────────────────
# HTTP integration (mocked)
# ─────────────────────────────────────────────────────────────────────
def _fake_response(payload):
    """Tiny stand-in for httpx.Response — only the bits the code uses."""
    class _R:
        def __init__(self, p):
            self._p = p
        def raise_for_status(self):
            return None
        def json(self):
            return self._p
    return _R(payload)


SCHEDULE_FIXTURE = {
    "dates": [{
        "date": "2026-04-15",
        "games": [
            {
                "gamePk": 777001,
                "status": {"abstractGameState": "Final"},
                "teams": {
                    "home": {"team": {"name": "Yankees"}, "score": 5},
                    "away": {"team": {"name": "Red Sox"}, "score": 3},
                },
            },
            {
                # Postponed — must be skipped
                "gamePk": 777002,
                "status": {"abstractGameState": "Preview"},
                "teams": {
                    "home": {"team": {"name": "Cubs"}, "score": 0},
                    "away": {"team": {"name": "Mets"}, "score": 0},
                },
            },
            {
                # Final but missing gamePk → skipped
                "gamePk": None,
                "status": {"abstractGameState": "Final"},
                "teams": {
                    "home": {"team": {"name": "X"}, "score": 1},
                    "away": {"team": {"name": "Y"}, "score": 2},
                },
            },
        ],
    }],
}


@pytest.mark.asyncio
async def test_fetch_historical_schedule_filters_final():
    with patch("services.mlb_backtest_runner.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_fake_response(SCHEDULE_FIXTURE))
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value = mock_client  # for explicit usage path
        rows = await fetch_historical_schedule("2026-04-15", "2026-04-15", client=mock_client)
    assert len(rows) == 1
    assert rows[0]["game_pk"] == 777001
    assert rows[0]["home_team"] == "Yankees"
    assert rows[0]["home_runs"] == 5
    assert rows[0]["away_runs"] == 3
    assert rows[0]["game_date"] == "2026-04-15"


@pytest.mark.asyncio
async def test_fetch_historical_schedule_failsoft_on_http_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("network down"))
    rows = await fetch_historical_schedule("2026-04-15", "2026-04-15", client=mock_client)
    assert rows == []


BOXSCORE_FIXTURE = {
    "teams": {
        "home": {
            "pitchers": [123],
            "players": {
                "ID123": {
                    "person": {"fullName": "Gerrit Cole"},
                    "seasonStats": {
                        "pitching": {"era": "2.80", "whip": "1.05", "inningsPitched": "180.0"},
                    },
                },
            },
        },
        "away": {
            "pitchers": [456],
            "players": {
                "ID456": {
                    "person": {"fullName": "Nick Pivetta"},
                    "stats": {
                        "pitching": {"era": "4.20", "whip": "1.30", "inningsPitched": "150.0"},
                    },
                },
            },
        },
    },
}


@pytest.mark.asyncio
async def test_fetch_game_pitchers_parses_boxscore():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_fake_response(BOXSCORE_FIXTURE))
    out = await fetch_game_pitchers(777001, client=mock_client)
    assert out["home"]["name"] == "Gerrit Cole"
    assert out["home"]["era"] == pytest.approx(2.80, abs=1e-6)
    assert out["away"]["name"] == "Nick Pivetta"
    assert out["away"]["era"] == pytest.approx(4.20, abs=1e-6)


@pytest.mark.asyncio
async def test_fetch_game_pitchers_failsoft_on_missing_keys():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_fake_response({"teams": {}}))
    out = await fetch_game_pitchers(777001, client=mock_client)
    assert out == {}


# ─────────────────────────────────────────────────────────────────────
# run_backtest end-to-end (dry run + persistence path with fake DB)
# ─────────────────────────────────────────────────────────────────────
class _FakeCollection:
    def __init__(self):
        self.inserted: list[dict] = []
        self.updates:  list[dict] = []
    async def insert_one(self, doc):
        self.inserted.append(doc)
        return type("R", (), {"inserted_id": "fake"})()
    async def update_one(self, q, payload):
        self.updates.append({"q": q, "payload": payload})
        return type("R", (), {"matched_count": 1, "modified_count": 1})()
    def find(self, q, projection=None):
        class _Cur:
            def __init__(self, docs):
                self._docs = docs
            def sort(self, *_a, **_kw): return self
            def limit(self, n): return self
            async def to_list(self, length=None):
                return []
        return _Cur(self.inserted)
    async def find_one(self, q):
        for d in self.inserted:
            if d.get("id") == q.get("id"):
                return d
        return None


class _FakeDB:
    def __init__(self):
        self.mlb_run_evaluations = _FakeCollection()
    def __getitem__(self, key):
        return getattr(self, key)


@pytest.mark.asyncio
async def test_run_backtest_dry_run_does_not_persist():
    with patch("services.mlb_backtest_runner.fetch_historical_schedule",
                new=AsyncMock(return_value=[
                    {"game_pk": 777001, "home_team": "A", "away_team": "B",
                     "home_runs": 4, "away_runs": 3, "game_date": "2026-04-15"},
                    {"game_pk": 777002, "home_team": "C", "away_team": "D",
                     "home_runs": 7, "away_runs": 8, "game_date": "2026-04-15"},
                ])), \
         patch("services.mlb_backtest_runner.fetch_game_pitchers",
                new=AsyncMock(return_value={
                    "home": {"name": "P1", "era": 3.0},
                    "away": {"name": "P2", "era": 4.0},
                })), \
         patch("services.mlb_backtest_runner.asyncio.sleep",
                new=AsyncMock()):
        db = _FakeDB()
        result = await run_backtest(
            db, "2026-04-15", "2026-04-15", dry_run=True,
        )
    assert result["games_fetched"] == 2
    assert result["games_inserted"] == 2
    assert result["games_failed"] == 0
    assert db.mlb_run_evaluations.inserted == []   # nothing persisted
    assert result["won"] + result["lost"] + result["push"] == 2


@pytest.mark.asyncio
async def test_run_backtest_persist_path_writes_settled_docs():
    with patch("services.mlb_backtest_runner.fetch_historical_schedule",
                new=AsyncMock(return_value=[
                    {"game_pk": 888001, "home_team": "Yankees", "away_team": "Red Sox",
                     "home_runs": 2, "away_runs": 1, "game_date": "2026-05-01"},
                ])), \
         patch("services.mlb_backtest_runner.fetch_game_pitchers",
                new=AsyncMock(return_value={
                    "home": {"name": "Cole", "era": 2.6},
                    "away": {"name": "Pivetta", "era": 4.5},
                })), \
         patch("services.mlb_backtest_runner.asyncio.sleep",
                new=AsyncMock()):
        db = _FakeDB()
        result = await run_backtest(
            db, "2026-05-01", "2026-05-01", dry_run=False,
        )
    assert result["games_inserted"] == 1
    assert result["games_failed"] == 0
    assert len(db.mlb_run_evaluations.inserted) == 1
    doc = db.mlb_run_evaluations.inserted[0]
    # Provenance fields must be present
    assert doc["_source"] == "backtest"
    assert doc["_line_approximated"] is True
    assert doc["user_id"] == DEFAULT_BACKTEST_USER_ID
    assert doc["sport"] == "baseball"
    assert doc["actual_total"] == 3
    assert doc["final_total"] == 3
    # NB telemetry shuttled through
    assert doc["totals_model"]["model_used"] == "NegativeBinomial"
    assert doc["totals_model"]["expected_total"] is not None
    assert doc["totals_model"]["book_total"] is not None
    # update_run_evaluation_result should have been called once
    assert len(db.mlb_run_evaluations.updates) == 1


@pytest.mark.asyncio
async def test_run_backtest_bad_date_range_failsoft():
    result = await run_backtest(None, "bad-date", "also-bad", dry_run=True)
    assert result["games_fetched"] == 0
    assert "error" in result


@pytest.mark.asyncio
async def test_run_backtest_empty_schedule_returns_zero():
    with patch("services.mlb_backtest_runner.fetch_historical_schedule",
                new=AsyncMock(return_value=[])):
        db = _FakeDB()
        result = await run_backtest(db, "2026-04-15", "2026-04-15", dry_run=True)
    assert result["games_fetched"] == 0
    assert result["games_inserted"] == 0
