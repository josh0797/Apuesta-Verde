"""Football live-fixtures aggregator: API-Sports + TheStatsAPI.

This is the **only** entry point ``data_ingestion.ingest_live`` should
use for ``sport == 'football'`` going forward. It merges fixtures from
the primary provider (API-Sports / ``services.api_football``) with the
supplementary provider (TheStatsAPI), deduplicates them, and tags every
fixture with provenance metadata so the UI can render badges.

Behaviour contract (fail-soft):
  * If ``ENABLE_THE_STATS_API`` is False or its key is missing → behaves
    like a thin wrapper around ``api_football.fixtures_live`` (no
    behavioural change vs the pre-integration baseline).
  * Any exception from TheStatsAPI is swallowed; API-Sports results
    are still returned.
  * Any exception from API-Sports propagates only as far as the
    aggregator; we still try TheStatsAPI and return whatever we got.

Deduplication strategy:
    Two fixtures are considered the same match if::
        (home_team_norm, away_team_norm) match AND
        |kickoff_ts_a - kickoff_ts_b| <= 60 minutes

    Team-name comparison normalises case, accents and known suffixes
    (FC, CF, AC, SC, etc.). API-Sports wins on a tie since its fixtures
    have richer downstream enrichment (odds, standings, injuries).
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

import httpx

from . import api_football as af
from .external_sources import thestatsapi_cache as ts_cache
from .external_sources import thestatsapi_client as ts_client
from .external_sources import thestatsapi_normalizer as ts_norm

log = logging.getLogger(__name__)

# 60-minute window — two fixtures with same teams within an hour of
# each other are almost certainly the same game (covers slight TZ /
# rounding differences between providers).
_DEDUPE_WINDOW_SEC = 60 * 60

_TEAM_SUFFIX_RE = re.compile(
    r"\b(fc|cf|ac|sc|sv|tsv|cd|sd|ud|fk|hc|afc|bfc|club|de|del|the)\b",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _normalize_team(name: str | None) -> str:
    if not name:
        return ""
    s = _strip_accents(str(name)).lower()
    s = _TEAM_SUFFIX_RE.sub(" ", s)
    s = _NON_WORD_RE.sub(" ", s)
    return " ".join(s.split())


def _kickoff_ts(fx: dict) -> int | None:
    try:
        ts = (fx.get("fixture") or {}).get("timestamp")
        if isinstance(ts, (int, float)):
            return int(ts)
        iso = (fx.get("fixture") or {}).get("date")
        if isinstance(iso, str):
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return int(dt.timestamp())
    except Exception:
        pass
    return None


def _teams_key(fx: dict) -> tuple[str, str]:
    teams = fx.get("teams") or {}
    home = _normalize_team((teams.get("home") or {}).get("name"))
    away = _normalize_team((teams.get("away") or {}).get("name"))
    return home, away


def _tag_primary(fx: dict) -> dict:
    """Mark an API-Sports fixture with provenance so the UI badge logic
    can identify it. Mutates and returns the same dict.
    """
    fx.setdefault("_external_source", "api_sports")
    fx.setdefault("_external_source_id", (fx.get("fixture") or {}).get("id"))
    return fx


def _is_same_match(a: dict, b: dict) -> bool:
    a_home, a_away = _teams_key(a)
    b_home, b_away = _teams_key(b)
    if not (a_home and a_away and b_home and b_away):
        return False
    if (a_home, a_away) != (b_home, b_away) and (a_home, a_away) != (b_away, b_home):
        return False
    ts_a, ts_b = _kickoff_ts(a), _kickoff_ts(b)
    if ts_a is None or ts_b is None:
        return True  # same teams, missing ts → treat as same
    return abs(ts_a - ts_b) <= _DEDUPE_WINDOW_SEC


def merge_and_deduplicate(
    primary: list[dict],
    secondary: list[dict],
) -> tuple[list[dict], dict]:
    """Return ``(merged_fixtures, metadata)``.

    Primary wins on conflict but inherits the secondary's badge so the
    UI can show that *both* providers covered the match.
    """
    primary = [_tag_primary(dict(fx)) for fx in primary or []]
    secondary = list(secondary or [])
    merged: list[dict] = list(primary)
    duplicates_dropped = 0
    secondary_added = 0

    for sec_fx in secondary:
        match_found = False
        for prim_fx in merged:
            if _is_same_match(prim_fx, sec_fx):
                # Mark the primary as ALSO covered by TheStatsAPI.
                covered = set(prim_fx.get("_external_sources_covered") or [])
                covered.add(prim_fx.get("_external_source") or "api_sports")
                covered.add("thestatsapi")
                prim_fx["_external_sources_covered"] = sorted(covered)
                # Mirror national-team flag onto primary if secondary had it
                if sec_fx.get("_is_national_team") and not prim_fx.get("_is_national_team"):
                    prim_fx["_is_national_team"] = True
                duplicates_dropped += 1
                match_found = True
                break
        if not match_found:
            # Append the secondary fixture verbatim (already normalized).
            sec_fx = dict(sec_fx)
            sec_fx.setdefault("_external_source", "thestatsapi")
            sec_fx["_external_sources_covered"] = ["thestatsapi"]
            merged.append(sec_fx)
            secondary_added += 1

    meta = {
        "primary_count":      len(primary),
        "secondary_count":    len(secondary),
        "duplicates_dropped": duplicates_dropped,
        "secondary_added":    secondary_added,
        "total":              len(merged),
    }
    return merged, meta


async def _fetch_competitions_index(client: httpx.AsyncClient, db) -> dict[str, dict]:
    """Return a {raw_competition_id: meta} index, using Mongo cache (24h TTL).

    Walks all pages of `/football/competitions` up to a hard cap so the
    full international set is included. Fail-soft: returns {} on any error.
    """
    if not ts_client.is_enabled():
        return {}
    try:
        cached = await ts_cache.cache_get(db, "competitions", key="all")
        if isinstance(cached, dict) and cached:
            return cached
    except Exception:
        pass
    try:
        raw = await ts_client.fetch_competitions(client)
        index = ts_norm.build_competitions_index(raw)
        # Persist to cache (24h TTL is configured in thestatsapi_cache.py)
        try:
            await ts_cache.cache_set(db, "competitions", "all", index)
        except Exception:
            pass
        return index
    except Exception as exc:  # noqa: BLE001
        log.warning("[aggregator] competitions index fetch failed: %s", exc)
        return {}


async def _fetch_thestatsapi_live(client: httpx.AsyncClient, db) -> list[dict]:
    """Fetch + normalize live matches from TheStatsAPI, with Mongo cache."""
    if not ts_client.is_enabled():
        return []
    # Try cache first
    try:
        cached = await ts_cache.cache_get(db, "live_matches", key="all")
        if cached is not None and isinstance(cached, list):
            return cached
    except Exception:
        pass
    # Fetch competitions index in parallel with the live matches call so we
    # can enrich the league name / international flag in a single round-trip.
    comps_task = asyncio.create_task(_fetch_competitions_index(client, db))
    try:
        raw = await ts_client.fetch_live_matches(client)
    except Exception as exc:  # noqa: BLE001
        log.warning("[aggregator] thestatsapi fetch_live_matches failed: %s", exc)
        comps_task.cancel()
        return []
    try:
        comp_index = await comps_task
    except Exception:
        comp_index = {}
    normalized = ts_norm.normalize_matches(raw, competitions_index=comp_index)
    try:
        await ts_cache.cache_set(db, "live_matches", "all", normalized)
    except Exception:
        pass
    return normalized


async def fetch_live_football_fixtures(
    client: httpx.AsyncClient,
    db=None,
    *,
    enable_thestatsapi: bool | None = None,
) -> tuple[list[dict], dict]:
    """Return ``(fixtures, meta)`` merged across providers.

    ``client`` is reused across both providers (an ``httpx.AsyncClient``
    is multi-host).
    ``db`` is the Motor database; if None, caching is skipped.
    ``enable_thestatsapi`` overrides the env flag when not None (useful
    for tests).
    """
    use_secondary = (
        ts_client.is_enabled() if enable_thestatsapi is None else bool(enable_thestatsapi)
    )

    # Parallel fetch — primary failure should not block secondary and vice versa.
    async def _safe_primary() -> list[dict]:
        try:
            res = await af.fixtures_live(client)
            return res or []
        except Exception as exc:  # noqa: BLE001
            log.warning("[aggregator] api_football.fixtures_live failed: %s", exc)
            return []

    async def _safe_secondary() -> list[dict]:
        if not use_secondary:
            return []
        try:
            return await _fetch_thestatsapi_live(client, db)
        except Exception as exc:  # noqa: BLE001
            log.warning("[aggregator] thestatsapi live failed: %s", exc)
            return []

    primary, secondary = await asyncio.gather(
        _safe_primary(), _safe_secondary(), return_exceptions=False
    )

    merged, meta = merge_and_deduplicate(primary, secondary)
    meta["thestatsapi_enabled"] = use_secondary
    log.info(
        "[aggregator] live football: primary=%d secondary=%d merged=%d (dropped_dupes=%d, added_from_secondary=%d, ts_enabled=%s)",
        meta["primary_count"], meta["secondary_count"], meta["total"],
        meta["duplicates_dropped"], meta["secondary_added"], use_secondary,
    )
    return merged, meta
