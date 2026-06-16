"""FIX-NEW-1 — TheSportsDB adapter + ingest tests.

Locks the contract for the new PRIMARY live source covering basketball
and baseball:

  * ``thesportsdb_client.fetch_livescore`` returns canonical envelope.
  * Status normalisation maps NS/SCHEDULED/Q1.../IN6.../FT correctly.
  * Adapter is fail-soft when the API is disabled or HTTP fails.
  * ``thesportsdb_live_ingest._doc_from_thesportsdb_item`` produces a
    valid match_doc compatible with the existing pipeline schema.
  * Unsupported sports return an explicit reason code without crashing.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.external_sources import thesportsdb_client as tsdb
from services.thesportsdb_live_ingest import (
    _doc_from_thesportsdb_item,
    ingest_thesportsdb_live,
)


# ─────────────────────────────────────────────────────────────────────
#   Client: status normalisation
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw_status,progress,expected", [
    ("NS",    None,  "SCHEDULED"),
    ("",      None,  "UNKNOWN"),
    ("FT",    None,  "FINISHED"),
    ("AET",   None,  "FINISHED"),
    ("Q3",    "3",   "LIVE"),
    ("BT",    "0",   "LIVE"),
    ("IN6",   "IN6", "LIVE"),
    ("HT",    None,  "LIVE"),
    ("",      "Q4",  "LIVE"),
    (None,    None,  "UNKNOWN"),
])
def test_status_normalisation_covers_all_canonical_states(
    raw_status, progress, expected,
):
    assert tsdb._normalize_status(raw_status, progress) == expected


def test_is_enabled_false_when_flag_off(monkeypatch):
    monkeypatch.setenv("THESPORTSDB_KEY", "abc")
    monkeypatch.setenv("ENABLE_THESPORTSDB", "false")
    assert tsdb.is_enabled() is False


def test_is_enabled_false_when_key_missing(monkeypatch):
    monkeypatch.delenv("THESPORTSDB_KEY", raising=False)
    monkeypatch.setenv("ENABLE_THESPORTSDB", "true")
    assert tsdb.is_enabled() is False


def test_is_enabled_true_when_flag_on_and_key_present(monkeypatch):
    monkeypatch.setenv("THESPORTSDB_KEY", "abc")
    monkeypatch.setenv("ENABLE_THESPORTSDB", "true")
    assert tsdb.is_enabled() is True


# ─────────────────────────────────────────────────────────────────────
#   Client: fetch_livescore canonical envelope
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_livescore_rejects_unsupported_sport(monkeypatch):
    monkeypatch.setenv("THESPORTSDB_KEY", "abc")
    monkeypatch.setenv("ENABLE_THESPORTSDB", "true")
    out = await tsdb.fetch_livescore("cricket")
    assert out["available"] is False
    assert "THESPORTSDB_UNSUPPORTED_SPORT" in out["reason_codes"]


@pytest.mark.asyncio
async def test_fetch_livescore_handles_disabled(monkeypatch):
    monkeypatch.delenv("THESPORTSDB_KEY", raising=False)
    out = await tsdb.fetch_livescore("basketball")
    assert out["available"] is False
    assert "THESPORTSDB_DISABLED" in out["reason_codes"]


@pytest.mark.asyncio
async def test_fetch_livescore_parses_canonical_payload(monkeypatch):
    monkeypatch.setenv("THESPORTSDB_KEY", "abc")
    monkeypatch.setenv("ENABLE_THESPORTSDB", "true")
    fake = {"livescore": [{
        "idLiveScore":     "11128251",
        "idEvent":         "2490796",
        "strSport":        "Basketball",
        "idLeague":        "4474",
        "strLeague":       "Israeli BPL",
        "idHomeTeam":      "136065",
        "idAwayTeam":      "136059",
        "strHomeTeam":     "Maccabi",
        "strAwayTeam":     "Hapoel",
        "strHomeTeamBadge":"https://h.png",
        "strAwayTeamBadge":"https://a.png",
        "intHomeScore":    "72",
        "intAwayScore":    "52",
        "strStatus":       "Q3",
        "strProgress":     "3",
        "strTimestamp":    "2026-06-16T17:50:00",
        "strEventTime":    "17:50",
        "dateEvent":       "2026-06-16",
        "updated":         "2026-06-16 20:13:21",
    }]}
    with patch.object(tsdb, "_request", new=AsyncMock(return_value=fake)):
        out = await tsdb.fetch_livescore("basketball")
    assert out["available"] is True
    assert out["count"] == 1
    item = out["items"][0]
    assert item["match_id"] == "2490796"
    assert item["home_team"]["name"] == "Maccabi"
    assert item["home_score"] == 72
    assert item["status_normalized"] == "LIVE"


# ─────────────────────────────────────────────────────────────────────
#   Ingest: _doc_from_thesportsdb_item produces canonical match_doc
# ─────────────────────────────────────────────────────────────────────

def test_doc_from_item_basketball_live():
    item = {
        "match_id":  "evt-1",
        "league_id": "4474", "league_name": "WNBA",
        "home_team": {"id": "1", "name": "A", "badge": "h.png"},
        "away_team": {"id": "2", "name": "B", "badge": "a.png"},
        "home_score": 80, "away_score": 75,
        "status": "Q4", "status_normalized": "LIVE",
        "progress": "4",
        "kickoff_iso": "2026-06-16T17:50:00",
        "date_event":  "2026-06-16",
        "event_time":  "17:50",
        "updated_at":  "2026-06-16T20:13:21",
    }
    doc = _doc_from_thesportsdb_item(item, "basketball")
    assert doc is not None
    assert doc["match_id"] == "tsdb_evt-1"
    assert doc["sport"] == "basketball"
    assert doc["provider"] == "thesportsdb"
    assert doc["thesportsdb_event_id"] == "evt-1"
    assert doc["home_team"]["_thesportsdb_id"] == "1"
    assert doc["is_live"] is True
    assert doc["is_finished"] is False


def test_doc_from_item_baseball_scheduled():
    item = {
        "match_id":  "evt-2",
        "league_id": "5085", "league_name": "International League",
        "home_team": {"id": "3", "name": "Mets", "badge": None},
        "away_team": {"id": "4", "name": "Tides", "badge": None},
        "home_score": None, "away_score": None,
        "status": "NS", "status_normalized": "SCHEDULED",
        "progress": "", "kickoff_iso": "2026-06-16T22:05:00",
        "date_event": "2026-06-16", "event_time": "22:05",
        "updated_at": "2026-06-16T20:13:21",
    }
    doc = _doc_from_thesportsdb_item(item, "baseball")
    assert doc is not None
    assert doc["sport"] == "baseball"
    assert doc["is_live"] is False
    assert doc["is_finished"] is False


def test_doc_from_item_rejects_missing_team_names():
    item = {
        "match_id":  "evt-3",
        "home_team": {"id": "x", "name": ""},
        "away_team": {"id": "y", "name": "B"},
        "status_normalized": "LIVE",
    }
    assert _doc_from_thesportsdb_item(item, "basketball") is None


def test_doc_from_item_rejects_missing_match_id():
    item = {
        "match_id":  "",
        "home_team": {"id": "1", "name": "A"},
        "away_team": {"id": "2", "name": "B"},
    }
    assert _doc_from_thesportsdb_item(item, "basketball") is None


# ─────────────────────────────────────────────────────────────────────
#   Ingest: end-to-end with mocked client + db
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_thesportsdb_live_persists_when_feed_available(monkeypatch):
    monkeypatch.setenv("THESPORTSDB_KEY", "abc")
    monkeypatch.setenv("ENABLE_THESPORTSDB", "true")
    fake_envelope = {
        "available": True, "source": "thesportsdb", "sport": "basketball",
        "items": [
            {"match_id": "e1", "league_id": "L", "league_name": "WNBA",
             "home_team": {"id": "1", "name": "A", "badge": None},
             "away_team": {"id": "2", "name": "B", "badge": None},
             "home_score": 10, "away_score": 8,
             "status": "Q1", "status_normalized": "LIVE",
             "progress": "1", "kickoff_iso": None,
             "date_event": "2026-06-16", "event_time": "20:00",
             "updated_at": "2026-06-16T20:01:00"},
        ],
        "reason_codes": ["THESPORTSDB_OK"],
        "count": 1,
    }

    matches_coll = AsyncMock()
    matches_coll.update_one = AsyncMock()

    class _FakeDB:
        def __init__(self): self.matches = matches_coll
    db = _FakeDB()

    with patch("services.thesportsdb_live_ingest.tsdb.fetch_livescore",
               new=AsyncMock(return_value=fake_envelope)):
        out = await ingest_thesportsdb_live(db, sport="basketball")

    assert out["available"] is True
    assert out["persisted"] == 1
    assert out["live_count"] == 1
    matches_coll.update_one.assert_awaited()


@pytest.mark.asyncio
async def test_ingest_thesportsdb_live_failsoft_when_unavailable():
    fake_envelope = {
        "available": False, "source": "thesportsdb", "sport": "basketball",
        "items": [], "reason_codes": ["THESPORTSDB_DISABLED"],
    }
    matches_coll = AsyncMock()
    matches_coll.update_one = AsyncMock()

    class _FakeDB:
        def __init__(self): self.matches = matches_coll
    db = _FakeDB()

    with patch("services.thesportsdb_live_ingest.tsdb.fetch_livescore",
               new=AsyncMock(return_value=fake_envelope)):
        out = await ingest_thesportsdb_live(db, sport="basketball")

    assert out["available"] is False
    assert out["persisted"] == 0
    matches_coll.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_thesportsdb_live_rejects_unsupported_sport():
    out = await ingest_thesportsdb_live(None, sport="cricket")
    assert out["available"] is False
    assert "THESPORTSDB_UNSUPPORTED_SPORT" in out["reason_codes"]
