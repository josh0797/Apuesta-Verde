"""Fail-soft contract tests for ``mlb_stats_api.hydrate_team_offensive_roster``.

The function MUST NEVER raise. It must return a dict whose ``available``
flag is True on success or False on any failure (db missing, cache fail,
http fail, parse fail) — without bubbling exceptions to the caller.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.mlb_stats_api import hydrate_team_offensive_roster


# ─── helpers ──────────────────────────────────────────────────────────────────
def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_db_with_cache(get_side_effect=None, put_side_effect=None):
    """Return a mock db whose ``mlb_cache.find_one`` and ``update_one``
    can be tuned to raise or return ``None``."""
    db = MagicMock()
    db.mlb_cache.find_one  = AsyncMock(
        side_effect=get_side_effect if get_side_effect else None,
        return_value=None if get_side_effect is None else None,
    )
    db.mlb_cache.update_one = AsyncMock(
        side_effect=put_side_effect if put_side_effect else None,
        return_value=None if put_side_effect is None else None,
    )
    return db


# ─── tests ────────────────────────────────────────────────────────────────────
class TestHydrateTeamOffensiveRosterFailSoft:

    def test_no_team_id_returns_failsafe(self):
        out = _run(hydrate_team_offensive_roster(db=None, team_id=0))
        assert out["available"] is False
        assert out["reason"] == "no_team_id"

    def test_db_none_does_not_raise_and_returns_payload(self):
        """db=None must skip cache layer entirely and still return a
        payload from the warm fetch (mocked here)."""
        fake_data = {"roster": [{
            "person":   {"id": 1, "fullName": "X"},
            "position": {"abbreviation": "1B"},
        }]}

        async def fake_get(self, url, params=None):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value=fake_data)
            return r

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = _run(hydrate_team_offensive_roster(db=None, team_id=147))
        assert out["available"] is True
        assert out["team_id"] == 147
        assert isinstance(out["players"], list)
        assert len(out["players"]) == 1

    def test_cache_get_raises_does_not_raise_to_caller(self):
        db = _make_db_with_cache(get_side_effect=RuntimeError("mongo down"))
        fake_data = {"roster": []}

        async def fake_get(self, url, params=None):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value=fake_data)
            return r

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = _run(hydrate_team_offensive_roster(db=db, team_id=147))
        # Cache read blew up → swallowed; warm fetch succeeded.
        assert out["available"] is True
        assert out["players"] == []

    def test_cache_put_raises_does_not_raise_to_caller(self):
        db = _make_db_with_cache(put_side_effect=RuntimeError("write denied"))
        fake_data = {"roster": [{
            "person":   {"id": 9, "fullName": "Y"},
            "position": {"abbreviation": "OF"},
        }]}

        async def fake_get(self, url, params=None):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value=fake_data)
            return r

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = _run(hydrate_team_offensive_roster(db=db, team_id=147))
        # Cache write blew up → swallowed; payload returned anyway.
        assert out["available"] is True
        assert len(out["players"]) == 1

    def test_http_error_returns_safe_empty(self):
        async def fake_get(self, url, params=None):
            raise RuntimeError("network unreachable")

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = _run(hydrate_team_offensive_roster(db=None, team_id=147))
        assert out["available"] is False
        assert out["reason"] == "http_error"
        assert out["players"] == []
        assert out["team_id"] == 147

    def test_malformed_json_does_not_raise(self):
        """API returns garbage with no ``roster`` key → parse should
        survive and return an empty roster instead of crashing."""
        async def fake_get(self, url, params=None):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value={"unexpected": "shape"})
            return r

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = _run(hydrate_team_offensive_roster(db=None, team_id=147))
        assert out["available"] is True
        assert out["players"] == []

    def test_malformed_player_record_skipped_silently(self):
        """One broken player record must not poison the whole batch."""
        async def fake_get(self, url, params=None):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value={"roster": [
                None,  # garbage
                {"person": {"id": 1, "fullName": "Good"},
                 "position": {"abbreviation": "OF"}},
                {"person": None, "position": None},  # all None
            ]})
            return r

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = _run(hydrate_team_offensive_roster(db=None, team_id=147))
        assert out["available"] is True
        # We expect at least the two records that have a "person" key,
        # since the per-player try/except only skips on real exceptions.
        names = [p["name"] for p in out["players"]]
        assert "Good" in names

    def test_returns_dict_not_none(self):
        """Even with everything broken (db None + http blowing up), the
        return value is a dict — never None — so callers can do
        ``.get('available')`` safely."""
        async def fake_get(self, url, params=None):
            raise Exception("explode")

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = _run(hydrate_team_offensive_roster(db=None, team_id=147))
        assert isinstance(out, dict)
        assert out.get("available") is False
