"""Phase F68 — TheStatsAPI player_id resolver.

Goal
====
Map an internal player NAME (e.g. ``"Kylian Mbappé"``) to TheStatsAPI's
canonical ``pl_*`` identifier, persisting the mapping in Mongo so
repeated lookups are free.

Strategy
========
1. If the name is empty → ``None``.
2. Cache hit in ``player_id_mappings`` → return it.
3. On-demand: call ``/api/football/players?search=<name>`` (verified
   live against the API on 2026-06-12, returns ``data: [{"id": "pl_…",
   "name", "first_name", "last_name", "current_team": {"id", "name"} }]``).
4. Pick the **best match** using a normalised exact / prefix score,
   optionally biased toward a specific ``current_team`` when provided.
5. Persist the chosen ``pl_*`` with TTL 90 days.

Public API
==========
``resolve_player_id_by_name(player_name, *, db, team_hint=None) -> Optional[str]``

All paths are fail-soft.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("player_id_mapping")

COLLECTION = "player_id_mappings"
TTL_DAYS   = 90


def _normalise(s: Optional[str]) -> str:
    """Lower, strip accents, collapse whitespace.

    Acentos: usamos un mapeo manual de los 12 caracteres comunes en
    nombres ES/PT/FR/IT en lugar de ``unicodedata.normalize`` para
    mantener el módulo libre de dependencias raras.
    """
    if not isinstance(s, str):
        return ""
    out = s.strip().lower()
    table = str.maketrans({
        "á":"a","é":"e","í":"i","ó":"o","ú":"u","ñ":"n","ü":"u",
        "â":"a","ê":"e","î":"i","ô":"o","û":"u","ã":"a","õ":"o","ç":"c",
        "à":"a","è":"e","ì":"i","ò":"o","ù":"u",
    })
    out = out.translate(table)
    return re.sub(r"\s+", " ", out)


def _candidate_score(query: str, candidate: dict, team_hint: Optional[str]) -> int:
    """Return a positive score; higher = better match."""
    q  = _normalise(query)
    c_full  = _normalise(candidate.get("name"))
    c_first = _normalise(candidate.get("first_name"))
    c_last  = _normalise(candidate.get("last_name"))
    if not q:
        return 0
    score = 0
    if q == c_full:                                  score += 200
    elif q == c_last:                                score += 150
    elif q.endswith(c_last) and c_last:              score += 120
    elif q.startswith(c_first) and c_first:          score += 80
    elif q in c_full and c_full:                     score += 60
    elif c_full and c_full in q:                     score += 40

    # Team disambiguation (helps when two players share a last name).
    if team_hint:
        ct = candidate.get("current_team") or {}
        ct_norm = _normalise(ct.get("name"))
        team_norm = _normalise(team_hint)
        if ct_norm and team_norm and (ct_norm == team_norm or team_norm in ct_norm):
            score += 50
    return score


# ─────────────────────────────────────────────────────────────────────
# Cache layer.
# ─────────────────────────────────────────────────────────────────────
async def get_cached(db, key: str) -> Optional[dict]:
    if db is None or not key:
        return None
    try:
        return await db[COLLECTION].find_one({"_id": key})
    except Exception as exc:  # noqa: BLE001
        log.debug("[PLAYER_CACHE_FETCH_FAIL] %s: %s", key, exc)
        return None


async def upsert(db, key: str, *, player_id: str,
                  player_name: str, team_name: Optional[str] = None,
                  current_team_id: Optional[str] = None,
                  source: str = "search") -> bool:
    if db is None or not (key and player_id):
        return False
    try:
        await db[COLLECTION].update_one(
            {"_id": key},
            {"$set": {
                "thestatsapi_id":  player_id,
                "player_name":     player_name,
                "team_name":       team_name,
                "current_team_id": current_team_id,
                "resolved_at":     datetime.now(timezone.utc),
                "source":          source,
            }},
            upsert=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("[PLAYER_CACHE_UPSERT_FAIL] %s: %s", key, exc)
        return False


def _cache_key(player_name: str, team_hint: Optional[str]) -> str:
    """Stable key: normalised name [+ team]."""
    base = _normalise(player_name)
    if team_hint:
        return f"{base}|{_normalise(team_hint)}"
    return base


# ─────────────────────────────────────────────────────────────────────
# Public resolver.
# ─────────────────────────────────────────────────────────────────────
async def resolve_player_id_by_name(
    player_name: str, *, db,
    team_hint: Optional[str] = None,
) -> Optional[str]:
    """Return a ``pl_*`` id for ``player_name`` (or None on failure).

    ``team_hint`` is the current team name (e.g. ``"Real Madrid"``)
    which the resolver uses to disambiguate when multiple players share
    a last name.
    """
    if not isinstance(player_name, str) or not player_name.strip():
        return None
    key = _cache_key(player_name, team_hint)
    cached = await get_cached(db, key)
    if cached and cached.get("thestatsapi_id"):
        return cached["thestatsapi_id"]

    # Live lookup.
    try:
        from services.thestatsapi_client import is_enabled, _http_get
        if not is_enabled():
            return None
        # Phase F68 — TheStatsAPI's /players?search= endpoint does NOT
        # accept accented characters URL-encoded; we must send a plain
        # ASCII search term. We try TWO queries:
        #   1) full normalised name ("kylian mbappe")
        #   2) just the last token ("mbappe") — often the most
        #      discriminative anchor for football players.
        clean_full = _normalise(player_name)
        last_token = clean_full.split(" ")[-1] if clean_full else ""
        queries = [clean_full]
        if last_token and last_token != clean_full:
            queries.append(last_token)

        from urllib.parse import quote
        data = None
        for q in queries:
            if not q:
                continue
            path = f"/api/football/players?search={quote(q)}&limit=10"
            data = await _http_get(path)
            if isinstance(data, list) and data:
                break
        if not isinstance(data, list):
            return None
        # Choose the best candidate.
        ranked = sorted(
            ((_candidate_score(player_name, c, team_hint), c) for c in data if isinstance(c, dict)),
            key=lambda t: t[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            return None
        score, best = ranked[0]
        pl_id = best.get("id")
        if not (isinstance(pl_id, str) and pl_id.startswith("pl_")):
            return None
        # Persist.
        team_name = (best.get("current_team") or {}).get("name")
        current_team_id = (best.get("current_team") or {}).get("id")
        await upsert(db, key,
                       player_id=pl_id,
                       player_name=best.get("name") or player_name,
                       team_name=team_name,
                       current_team_id=current_team_id,
                       source="search")
        return pl_id
    except Exception as exc:  # noqa: BLE001
        log.debug("[PLAYER_RESOLVE_FAIL] %s: %s", player_name, exc)
        return None


__all__ = [
    "COLLECTION", "TTL_DAYS",
    "_normalise", "_candidate_score", "_cache_key",
    "get_cached", "upsert",
    "resolve_player_id_by_name",
]
