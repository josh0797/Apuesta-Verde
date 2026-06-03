"""Pre-match enrichment via TheStatsAPI for football / basketball / baseball.

This module produces a single ``_thestatsapi_enrichment`` dict that gets
attached to each match_doc (and forwarded onto pick payloads). It is
deliberately additive: every section is optional and the rest of the
pipeline must keep working if every field is absent.

Output shape::

    {
        "fetched_at": ISO8601,
        "source":     "thestatsapi",
        "match":      {...details from /matches/{id}},  # optional
        "team_stats": {
            "home": {...season stats},
            "away": {...season stats},
        },
        "player_stats": {
            "home": [{...top player stats}, ...],   # optional, top ~5
            "away": [{...}, ...],
        },
        "_errors": ["match_details: timeout", ...],   # only if failures
    }

Trigger logic lives in ``data_ingestion`` — this module is a *pure*
fetch+normalize helper. It never decides whether enrichment should
happen; it just executes when called.

All calls are wrapped in best-effort try/except so a partial failure
doesn't blow up the whole match enrichment.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from . import thestatsapi_cache as ts_cache
from . import thestatsapi_client as ts_client

log = logging.getLogger(__name__)

# Cache scope keys — each goes into the `external_source_cache` collection.
_CACHE_TEAM_STATS  = "team_stats"     # 6h custom TTL (registered below)
_CACHE_PLAYER_STATS = "player_stats"  # 6h custom TTL
_CACHE_MATCH_DETAILS = "match_details"  # 2h custom TTL

# Extend ts_cache.TTL_SECONDS so Batch-3 endpoints get sensible TTLs.
ts_cache.TTL_SECONDS.setdefault(_CACHE_TEAM_STATS,    6 * 60 * 60)
ts_cache.TTL_SECONDS.setdefault(_CACHE_PLAYER_STATS,  6 * 60 * 60)
ts_cache.TTL_SECONDS.setdefault(_CACHE_MATCH_DETAILS, 2 * 60 * 60)


async def _cached_call(
    db,
    endpoint: str,
    key: str,
    coro_factory,
):
    """Try cache → call → write cache, with full fail-soft semantics."""
    try:
        cached = await ts_cache.cache_get(db, endpoint, key)
        if cached is not None:
            return cached
    except Exception:
        cached = None
    try:
        result = await coro_factory()
    except Exception as exc:  # noqa: BLE001
        log.debug("[ts_enrichment] %s/%s call failed: %s", endpoint, key, exc)
        return {}
    if result:
        try:
            await ts_cache.cache_set(db, endpoint, key, result)
        except Exception:
            pass
    return result or {}


async def enrich_pre_match(
    client: httpx.AsyncClient,
    db,
    *,
    sport: str,
    match_raw_id: int | str | None,
    home_team_id: int | str | None,
    away_team_id: int | str | None,
    season: int | str | None = None,
    competition_id: int | str | None = None,
) -> dict:
    """Build the ``_thestatsapi_enrichment`` payload.

    All identifiers MUST be the **TheStatsAPI raw ids** (e.g. ``mt_xxx``,
    ``tm_xxx``) — never our namespaced ints. The caller is responsible
    for unwrapping namespaced ids before invoking this function.

    Sport is one of ``football`` | ``basketball`` | ``baseball``.

    Returns ``{}`` if the integration is disabled or fully unreachable.
    """
    if not ts_client.is_enabled():
        return {}

    sport = (sport or "football").lower()

    out: dict[str, Any] = {
        "source":     "thestatsapi",
        "sport":      sport,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    errors: list[str] = []

    # ── Match details (1 call) ─────────────────────────────────────
    async def _match_call():
        if match_raw_id is None:
            return {}
        return await ts_client.fetch_match_details(client, match_raw_id, sport=sport)

    # ── Per-team stats (2 parallel calls) ──────────────────────────
    async def _team_call(team_id):
        if team_id is None:
            return {}
        return await ts_client.fetch_team_stats(
            client, team_id,
            sport=sport, season=season, competition_id=competition_id,
        )

    # Launch all three in parallel.
    match_key = f"{sport}:{match_raw_id}" if match_raw_id else f"{sport}:noid"
    home_key  = f"{sport}:{home_team_id}:{season}:{competition_id}"
    away_key  = f"{sport}:{away_team_id}:{season}:{competition_id}"

    try:
        match_task = _cached_call(db, _CACHE_MATCH_DETAILS, match_key, _match_call)
        home_task  = _cached_call(db, _CACHE_TEAM_STATS,    home_key, lambda: _team_call(home_team_id))
        away_task  = _cached_call(db, _CACHE_TEAM_STATS,    away_key, lambda: _team_call(away_team_id))
        match_d, home_s, away_s = await asyncio.gather(
            match_task, home_task, away_task, return_exceptions=True,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        log.warning("[ts_enrichment] gather failed for %s: %s", match_raw_id, exc)
        return out

    def _unwrap(result):
        if isinstance(result, Exception):
            errors.append(str(result))
            return {}
        return result if isinstance(result, dict) else {}

    match_d = _unwrap(match_d)
    home_s  = _unwrap(home_s)
    away_s  = _unwrap(away_s)

    if match_d:
        out["match"] = match_d
    if home_s or away_s:
        out["team_stats"] = {"home": home_s, "away": away_s}

    # ── Top-N player stats (optional, only if match details exposed
    # lineups). We pull a small fan-out to keep request budget low. ──
    player_stats: dict[str, list[dict]] = {"home": [], "away": []}
    try:
        for side, lineup_key in (("home", "home_lineup"), ("away", "away_lineup")):
            lineup = match_d.get(lineup_key) if isinstance(match_d, dict) else None
            if not isinstance(lineup, list):
                continue
            top_players = lineup[:5]   # cap at 5 per side
            for p in top_players:
                pid = p.get("id") or p.get("player_id")
                if pid is None:
                    continue
                p_key = f"{sport}:{pid}:{season}"
                p_stats = await _cached_call(
                    db, _CACHE_PLAYER_STATS, p_key,
                    lambda pid=pid: ts_client.fetch_player_stats(
                        client, pid, sport=sport, season=season,
                    ),
                )
                if p_stats:
                    player_stats[side].append({"id": pid, "stats": p_stats})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"player_stats: {exc}")

    if player_stats["home"] or player_stats["away"]:
        out["player_stats"] = player_stats

    if errors:
        out["_errors"] = errors

    # If literally nothing came back, drop the whole block so consumers
    # can check `if match_doc.get("_thestatsapi_enrichment"):` and avoid
    # rendering an empty section.
    if not any(k in out for k in ("match", "team_stats", "player_stats")):
        return {}
    return out
