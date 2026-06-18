"""Sprint-D9.2 Block A — Cross-tournament corner window.

Bug fixed
---------
``services/api_football.fixtures_last_n`` filtered by ``season=PROXY_SEASON``,
so for a national team analysed inside the World Cup it surfaced only the
≤ 7 partidos del torneo — never the friendlies / qualifiers that live in
other API-Sports ``season`` buckets. This made the L1/L5/L15 corner window
useless for selecciones.

These tests cover:

* The new ``include_all_competitions`` flag drops the ``season`` filter on
  the wire AND uses a separate cache namespace (``"last_n_global"``).
* The default contract is back-compat: leagues keep using ``season=YEAR``.
* ``fetch_team_corners_history_apisports`` propagates the flag and stamps
  ``AS_LAST_N_GLOBAL_USED`` so the calling layer can audit it.
* ``fetch_team_corners_history`` accepts and forwards the flag through the
  whole stack.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import api_football as af
from services import football_corners_history as ch


# ════════════════════════════════════════════════════════════════════════
# 1) fixtures_last_n
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestFixturesLastNFlag:
    async def test_default_contract_keeps_season_filter(self):
        """Legacy callers must keep the old behaviour."""
        with patch.object(af, "_get", new=AsyncMock(return_value={"response": []})) as gp, \
             patch.object(af, "_cache_get", new=AsyncMock(return_value=None)), \
             patch.object(af, "_cache_set", new=AsyncMock(return_value=None)):
            await af.fixtures_last_n(None, team_id=33, n=15, season=2024)
            assert gp.await_args.args[1] == "/fixtures"
            params = gp.await_args.args[2]
            assert params == {"team": 33, "last": 15, "season": 2024}

    async def test_include_all_competitions_drops_season_filter(self):
        """National-team window must NOT send the season param."""
        with patch.object(af, "_get", new=AsyncMock(return_value={"response": []})) as gp, \
             patch.object(af, "_cache_get", new=AsyncMock(return_value=None)), \
             patch.object(af, "_cache_set", new=AsyncMock(return_value=None)):
            await af.fixtures_last_n(None, team_id=33, n=15, season=2024,
                                       include_all_competitions=True)
            params = gp.await_args.args[2]
            assert "season" not in params, (
                f"season must be omitted when global; got params={params}"
            )
            assert params == {"team": 33, "last": 15}

    async def test_season_none_also_drops_filter(self):
        """``season=None`` is the same as ``include_all_competitions=True``."""
        with patch.object(af, "_get", new=AsyncMock(return_value={"response": []})) as gp, \
             patch.object(af, "_cache_get", new=AsyncMock(return_value=None)), \
             patch.object(af, "_cache_set", new=AsyncMock(return_value=None)):
            await af.fixtures_last_n(None, team_id=33, n=15, season=None)
            params = gp.await_args.args[2]
            assert "season" not in params

    async def test_cache_namespace_is_isolated(self):
        """Global and per-season caches must NOT collide. We assert this
        by checking the cache *write* key issued by each call."""
        seen_keys: list[dict] = []

        async def fake_cache_set(_db, _coll, key, _val):
            seen_keys.append(key)

        with patch.object(af, "_get", new=AsyncMock(return_value={"response": []})), \
             patch.object(af, "_cache_get", new=AsyncMock(return_value=None)), \
             patch.object(af, "_cache_set", new=AsyncMock(side_effect=fake_cache_set)):
            await af.fixtures_last_n(None, team_id=33, n=15, season=2024)
            await af.fixtures_last_n(None, team_id=33, n=15, season=2024,
                                       include_all_competitions=True)
        kinds = [k["kind"] for k in seen_keys]
        assert "last_n"        in kinds
        assert "last_n_global" in kinds
        # And the global key carries season=None to avoid accidental
        # cross-pollination with the legacy entry.
        global_key = next(k for k in seen_keys if k["kind"] == "last_n_global")
        assert global_key["season"] is None

    async def test_cache_hit_returns_without_remote_call(self):
        """Existing 12-hour cache must short-circuit the HTTP call."""
        cached = [{"fixture": {"id": 1, "timestamp": 100}}]
        with patch.object(af, "_get", new=AsyncMock(return_value={"response": []})) as gp, \
             patch.object(af, "_cache_get", new=AsyncMock(return_value=cached)), \
             patch.object(af, "_cache_set", new=AsyncMock(return_value=None)):
            out = await af.fixtures_last_n(None, team_id=33, n=15, season=2024)
            assert out == cached
            assert gp.await_count == 0


# ════════════════════════════════════════════════════════════════════════
# 2) fetch_team_corners_history_apisports propagates the flag
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestCornersApiSportsBranch:
    async def test_propagates_include_all_competitions(self):
        captured: dict = {}

        async def fake_fixtures_last_n(_client, _team, **kw):
            captured.update(kw)
            return []

        with patch.object(ch.af, "fixtures_last_n",
                            new=AsyncMock(side_effect=fake_fixtures_last_n)):
            hist, reasons = await ch.fetch_team_corners_history_apisports(
                None, None, team_id=5106, season=2022, n=15,
                include_all_competitions=True,
            )
            assert hist == []
            assert captured.get("include_all_competitions") is True
            assert "AS_LAST_N_GLOBAL_USED" in reasons

    async def test_default_does_not_set_flag(self):
        captured: dict = {}

        async def fake_fixtures_last_n(_client, _team, **kw):
            captured.update(kw)
            return []

        with patch.object(ch.af, "fixtures_last_n",
                            new=AsyncMock(side_effect=fake_fixtures_last_n)):
            _, reasons = await ch.fetch_team_corners_history_apisports(
                None, None, team_id=33, season=2024, n=15,
            )
            assert captured.get("include_all_competitions") in (False, None)
            assert "AS_LAST_N_GLOBAL_USED" not in reasons


# ════════════════════════════════════════════════════════════════════════
# 3) Public entry point honours the flag
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestPublicEntryPoint:
    async def test_forwards_flag_to_apisports_branch(self):
        captured: dict = {}

        async def fake_apisports(_client, _db, **kw):
            captured.update(kw)
            return ([], ["AS_NO_RECENT_FIXTURES"])

        with patch.object(ch, "fetch_team_corners_history_apisports",
                            new=AsyncMock(side_effect=fake_apisports)), \
             patch.object(ch, "fetch_team_corners_history_thestatsapi",
                            new=AsyncMock(return_value=([], ["TSA_404"]))):
            out = await ch.fetch_team_corners_history(
                None, None,
                team_id_thestatsapi=None,   # force fallback to API-Sports
                team_id_apisports=5106,
                season=2022, n=15, use_cache=False,
                include_all_competitions=True,
            )
            assert captured["include_all_competitions"] is True
            assert out["source"] == "none"
