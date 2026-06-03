"""Tests for MLB live-state boxscore extraction + persistence (Fix MLB-2a).

Plus the robust pregame-pick lookup logic (Fix MLB-2b) — verified via a
helper extracted from the comparison block.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from services import mlb_live_state as mls


# ──────────────────────────────────────────────────────────────────────
# Fix MLB-2a — _extract_box_score
# ──────────────────────────────────────────────────────────────────────
def _full_box_payload() -> dict:
    return {
        "teams": {
            "home": {
                "teamStats": {
                    "batting":  {"hits": 8, "homeRuns": 2, "baseOnBalls": 3,
                                 "strikeOuts": 7, "atBats": 32, "leftOnBase": 5},
                    "pitching": {"numberOfPitches": 95},
                    "fielding": {"errors": 1},
                }
            },
            "away": {
                "teamStats": {
                    "batting":  {"hits": 6, "homeRuns": 1, "baseOnBalls": 2,
                                 "strikeOuts": 10, "atBats": 28, "leftOnBase": 4},
                    "pitching": {"numberOfPitches": 87},
                    "fielding": {"errors": 0},
                }
            },
        }
    }


def test_extract_box_score_full_payload():
    out = mls._extract_box_score(_full_box_payload())
    assert out["hits"] == {"home": 8, "away": 6}
    assert out["walks"] == {"home": 3, "away": 2}
    assert out["home_runs"] == {"home": 2, "away": 1}
    assert out["errors"] == {"home": 1, "away": 0}
    assert out["strikeouts"] == {"home": 7, "away": 10}
    assert out["pitches_home"] == 95
    assert out["pitches_away"] == 87
    assert out["at_bats"] == {"home": 32, "away": 28}


def test_extract_box_score_handles_missing_fielding():
    """Pre-game or very early innings sometimes omit `fielding`."""
    payload = {
        "teams": {
            "home": {"teamStats": {"batting": {"hits": 2, "homeRuns": 0,
                                                "baseOnBalls": 1, "strikeOuts": 1}}},
            "away": {"teamStats": {"batting": {"hits": 1, "homeRuns": 0,
                                                "baseOnBalls": 0, "strikeOuts": 2}}},
        }
    }
    out = mls._extract_box_score(payload)
    assert out["hits"] == {"home": 2, "away": 1}
    # errors block was all-None → dropped
    assert "errors" not in out


def test_extract_box_score_empty_input():
    assert mls._extract_box_score({}) == {}
    assert mls._extract_box_score(None) == {}
    assert mls._extract_box_score({"teams": {}}) == {}
    assert mls._extract_box_score({"teams": {"home": {}, "away": {}}}) == {}


def test_extract_box_score_drops_all_none_sections():
    payload = {
        "teams": {
            "home": {"teamStats": {"batting": {"hits": 5}, "fielding": {}}},
            "away": {"teamStats": {"batting": {"hits": 3}, "fielding": {}}},
        }
    }
    out = mls._extract_box_score(payload)
    assert out["hits"] == {"home": 5, "away": 3}
    # walks block fully None → dropped
    assert "walks" not in out
    assert "errors" not in out


def test_extract_box_score_coerces_strings_to_int():
    """MLB Stats API has been known to ship some counters as strings."""
    payload = {
        "teams": {
            "home": {"teamStats": {"batting": {"hits": "8", "baseOnBalls": "3"}}},
            "away": {"teamStats": {"batting": {"hits": "6", "baseOnBalls": "2"}}},
        }
    }
    out = mls._extract_box_score(payload)
    assert out["hits"] == {"home": 8, "away": 6}
    assert out["walks"] == {"home": 3, "away": 2}


# ──────────────────────────────────────────────────────────────────────
# Fix MLB-2a — fetch_live_state full path with mocked transport
# ──────────────────────────────────────────────────────────────────────
def _make_transport(linescore_json, schedule_json, boxscore_json):
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if "linescore" in path:
            return httpx.Response(200, json=linescore_json)
        if "schedule" in path:
            return httpx.Response(200, json=schedule_json)
        if "boxscore" in path:
            return httpx.Response(200, json=boxscore_json)
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_live_state_includes_box_score(monkeypatch):
    """End-to-end: linescore + schedule + boxscore are all parallelized
    and the result includes a populated `box_score` field."""
    linescore = {
        "teams": {"home": {"runs": 4}, "away": {"runs": 3}},
        "currentInning": 7,
        "inningHalf": "Top",
        "currentInningOrdinal": "7th",
        "outs": 1, "balls": 2, "strikes": 1,
        "offense": {"first": True},
        "defense": {"pitcher": {"id": 1, "fullName": "Test Pitcher"}},
    }
    schedule = {"dates": [{"games": [{
        "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
    }]}]}
    boxscore = _full_box_payload()

    # Patch httpx.AsyncClient inside the module to use our mock transport.
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(transport=_make_transport(linescore, schedule, boxscore))

    monkeypatch.setattr(mls.httpx, "AsyncClient", fake_async_client)

    snap = await mls.fetch_live_state("824515")
    assert snap["game_pk"] == 824515
    assert snap["is_live"] is True
    assert snap["state"] == "live-data-ready"
    assert snap["score"] == {"home": 4, "away": 3}
    assert snap["inning"]["number"] == 7
    # Box score wired through:
    assert snap["box_score"]["hits"] == {"home": 8, "away": 6}
    assert snap["box_score"]["walks"] == {"home": 3, "away": 2}
    assert snap["box_score"]["home_runs"] == {"home": 2, "away": 1}
    assert snap["box_score"]["errors"] == {"home": 1, "away": 0}


@pytest.mark.asyncio
async def test_fetch_live_state_box_score_missing_is_empty_dict(monkeypatch):
    linescore = {"teams": {"home": {"runs": 0}, "away": {"runs": 0}}}
    schedule  = {"dates": [{"games": [{"status": {"detailedState": "Pre-Game",
                                                    "abstractGameState": "Preview"}}]}]}

    def handler(req):
        path = req.url.path
        if "linescore" in path:
            return httpx.Response(200, json=linescore)
        if "schedule" in path:
            return httpx.Response(200, json=schedule)
        if "boxscore" in path:
            # Simulate a 404 / missing boxscore for a pre-game state
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(404, json={})

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(mls.httpx, "AsyncClient", fake_async_client)
    snap = await mls.fetch_live_state("824515")
    assert snap["state"] == "no-live-data"
    assert snap["box_score"] == {}


@pytest.mark.asyncio
async def test_fetch_live_state_boxscore_exception_does_not_break_snap(monkeypatch):
    """If only the boxscore call raises, the rest of the snap is still
    returned (graceful degradation)."""
    linescore = {
        "teams": {"home": {"runs": 1}, "away": {"runs": 0}},
        "currentInning": 3, "inningHalf": "Bot",
        "currentInningOrdinal": "3rd", "outs": 0, "balls": 0, "strikes": 0,
    }
    schedule = {"dates": [{"games": [{"status": {"abstractGameState": "Live",
                                                    "detailedState": "In Progress"}}]}]}

    def handler(req):
        path = req.url.path
        if "linescore" in path:
            return httpx.Response(200, json=linescore)
        if "schedule" in path:
            return httpx.Response(200, json=schedule)
        if "boxscore" in path:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(404, json={})

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_async_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(mls.httpx, "AsyncClient", fake_async_client)
    snap = await mls.fetch_live_state("824515")
    assert snap["is_live"] is True
    assert snap["score"]["home"] == 1
    assert snap["box_score"] == {}


@pytest.mark.asyncio
async def test_fetch_live_state_non_numeric_match_id():
    snap = await mls.fetch_live_state("abc-not-a-pk")
    assert snap["game_pk"] is None
    assert snap["state"] == "no-live-data"
    assert snap.get("_reason") == "match_id_not_numeric"


# ──────────────────────────────────────────────────────────────────────
# Fix MLB-2b — pregame pick lookup robustness
# ──────────────────────────────────────────────────────────────────────
def _build_doc_candidates(doc: dict) -> set[str]:
    """Mirror of the inline logic in server.match_detail."""
    out: set[str] = set()
    mid = doc.get("match_id")
    if mid is not None:
        out.add(str(mid))
        try:
            out.add(str(int(mid)))
        except (TypeError, ValueError):
            pass
    if doc.get("sport") == "baseball":
        gp = doc.get("game_pk") or (doc.get("live_stats") or {}).get("game_pk")
        if gp is not None:
            out.add(str(gp))
            try:
                out.add(str(int(gp)))
            except (TypeError, ValueError):
                pass
    return out


def _build_pick_candidates(p: dict) -> set[str]:
    out: set[str] = set()
    for fld in ("match_id", "game_pk"):
        v = p.get(fld)
        if v is not None:
            out.add(str(v))
            try:
                out.add(str(int(v)))
            except (TypeError, ValueError):
                pass
    return out


def test_pregame_lookup_baseball_matches_via_game_pk():
    """The most important case: doc.match_id is an API-Sports id and the
    pick's `match_id` is the gamePk. They must still match via game_pk."""
    doc = {"sport": "baseball", "match_id": "12345-apisports", "game_pk": 824515}
    pick = {"match_id": "824515", "game_pk": 824515}
    assert _build_doc_candidates(doc) & _build_pick_candidates(pick)


def test_pregame_lookup_baseball_matches_when_doc_match_id_is_game_pk():
    """Standard MLB path — doc.match_id IS the gamePk as text."""
    doc  = {"sport": "baseball", "match_id": "824515"}
    pick = {"match_id": "824515", "game_pk": 824515}
    assert _build_doc_candidates(doc) & _build_pick_candidates(pick)


def test_pregame_lookup_baseball_matches_via_live_stats_game_pk():
    """When doc.game_pk isn't set but live_stats has it."""
    doc  = {"sport": "baseball", "match_id": "x99",
            "live_stats": {"game_pk": 824515}}
    pick = {"game_pk": 824515}   # no match_id at all
    assert _build_doc_candidates(doc) & _build_pick_candidates(pick)


def test_pregame_lookup_football_unaffected():
    """For football the new branch never triggers — pure match_id compare."""
    doc  = {"sport": "football", "match_id": 12345}
    pick = {"match_id": 12345}
    assert _build_doc_candidates(doc) & _build_pick_candidates(pick)
    # And a non-match returns empty set:
    pick_other = {"match_id": 67890}
    assert not (_build_doc_candidates(doc) & _build_pick_candidates(pick_other))


def test_pregame_lookup_no_match_when_unrelated():
    doc  = {"sport": "baseball", "match_id": "111", "game_pk": 111}
    pick = {"match_id": "222", "game_pk": 222}
    assert not (_build_doc_candidates(doc) & _build_pick_candidates(pick))
