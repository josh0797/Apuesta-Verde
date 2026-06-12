"""Phase F66 — TheStatsAPI async client (thestatsapi.com).

Goals
=====
* Fetch prematch odds (`/api/football/matches/{match_id}/odds`).
* Fetch live odds  (`/api/football/matches/{match_id}/odds/live`).
* Fetch player heatmap (`/api/football/players/{pid}/competitions/{cid}/seasons/{sid}/heatmap`).

Design
======
* **Opt-IN**: if ``STATSAPI_API_KEY`` is empty, every public coroutine returns
  ``None`` immediately — the editorial engine then renders without odds.
* **Auth**: ``Authorization: Bearer <key>`` (verified end-to-end against the live
  API on 2026-06-12).
* **Fail-soft**: every error path returns ``None``; never raises.
* **MongoDB cache** with TTL (auto-purged): 15 min prematch / 60s live / 24h heatmaps.
* **Circuit breaker**: re-uses the Phase F65 per-host breaker so repeated
  upstream failures auto-pause the integration for 30 min.

Response shape (Kambi-style — verified live)::

    data = {
      "match_id": "mt_…",
      "bookmakers": [{
        "bookmaker": "Kambi",
        "markets": {
          "match_odds":    {"home": {"opening": "1.760", "last_seen": "1.710"}, …},
          "total_goals":   {"1.5": {"over": {…}, "under": {…}}, "2.5": …},
          "match_corners": {"7.5": {"over": {…}, "under": {…}}, "9.5": …},
          "btts":          {"yes": {…}, "no": {…}},
          "asian_handicap": {"home": {"-0.5": …}, "away": {"+1.5": …}},
        },
      }],
    }

The :func:`extract_normalised_markets` helper flattens this into a clean dict
with *last_seen* decimal odds for the markets the editorial engine consumes
(total_goals @ 1.5/2.5/3.5, match_corners @ 9.5/10.5/11.5, btts, match_odds).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger("thestatsapi.client")

DEFAULT_BASE_URL = "https://api.thestatsapi.com"
DEFAULT_TIMEOUT  = 12.0

PREMATCH_TTL_SEC = 15 * 60          # 15 minutes
LIVE_TTL_SEC     = 60               # 60 seconds
HEATMAP_TTL_SEC  = 24 * 60 * 60     # 24 hours

GOAL_LINES   = (1.5, 2.5, 3.5)
CORNER_LINES = (9.5, 10.5, 11.5)


def is_enabled() -> bool:
    """True when both API key is present AND breaker says go."""
    if not (os.environ.get("STATSAPI_API_KEY", "").strip()):
        return False
    # Honour the global Bright Data-style flag (single switch for ALL
    # external scraping/integrations). We reuse the same env name to keep
    # the operator UI consistent.
    try:
        from services.external_sources.circuit_breaker import is_brightdata_enabled
        return is_brightdata_enabled()
    except Exception:  # noqa: BLE001
        return True


def _api_key() -> str:
    return os.environ.get("STATSAPI_API_KEY", "").strip()


def _base_url() -> str:
    return os.environ.get("STATSAPI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


# ─────────────────────────────────────────────────────────────────────
# HTTP layer
# ─────────────────────────────────────────────────────────────────────
async def _http_get(path: str, *, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
    """Authenticated GET with full fail-soft + breaker handling."""
    if not is_enabled():
        return None
    url = f"{_base_url()}{path}"
    # Per-host breaker check.
    try:
        from services.external_sources.circuit_breaker import (
            is_open, record_success, record_failure,
        )
        if is_open(url):
            log.debug("[STATSAPI_BREAKER_OPEN] %s", url)
            return None
    except Exception:  # noqa: BLE001
        record_success = None       # type: ignore[assignment]
        record_failure = None       # type: ignore[assignment]

    headers = {"Authorization": f"Bearer {_api_key()}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, headers=headers)
    except (httpx.HTTPError, Exception) as exc:  # noqa: BLE001
        log.warning("[STATSAPI_FETCH_FAIL] %s: %s", url, exc)
        if record_failure:
            record_failure(url, error_code=type(exc).__name__, error_msg=str(exc))
        return None
    if r.status_code == 429:
        if record_failure:
            record_failure(url, error_code="http_429",
                           error_msg="rate limited")
        return None
    if r.status_code != 200:
        if record_failure:
            record_failure(url, error_code=f"http_{r.status_code}",
                           error_msg=(r.text or "")[:200])
        return None
    try:
        payload = r.json()
    except ValueError as exc:
        if record_failure:
            record_failure(url, error_code="json_decode", error_msg=str(exc))
        return None
    if record_success:
        record_success(url)
    # API consistently wraps real payload under "data".
    return payload.get("data") if isinstance(payload, dict) else payload


# ─────────────────────────────────────────────────────────────────────
# Mongo cache layer — TTL indexes are created by server.py at startup.
# ─────────────────────────────────────────────────────────────────────
async def _cache_get(db, collection: str, doc_id: str) -> Optional[dict]:
    if db is None:
        return None
    try:
        d = await db[collection].find_one({"_id": doc_id})
        return d.get("data") if d else None
    except Exception as exc:  # noqa: BLE001
        log.debug("[STATSAPI_CACHE_GET_FAIL] %s/%s: %s", collection, doc_id, exc)
        return None


async def _cache_set(db, collection: str, doc_id: str, data: dict) -> None:
    if db is None or not isinstance(data, dict):
        return
    try:
        await db[collection].update_one(
            {"_id": doc_id},
            {"$set": {
                "data":        data,
                "fetched_at":  datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[STATSAPI_CACHE_SET_FAIL] %s/%s: %s", collection, doc_id, exc)


# ─────────────────────────────────────────────────────────────────────
# Public endpoint wrappers
# ─────────────────────────────────────────────────────────────────────
async def get_prematch_odds(match_id: str, *, db=None) -> Optional[dict]:
    if not match_id:
        return None
    cached = await _cache_get(db, "thestatsapi_prematch_cache", match_id)
    if cached is not None:
        return cached
    data = await _http_get(f"/api/football/matches/{match_id}/odds")
    if data is None:
        return None
    await _cache_set(db, "thestatsapi_prematch_cache", match_id, data)
    return data


async def get_live_odds(match_id: str, *, db=None) -> Optional[dict]:
    if not match_id:
        return None
    cached = await _cache_get(db, "thestatsapi_live_cache", match_id)
    if cached is not None:
        return cached
    data = await _http_get(f"/api/football/matches/{match_id}/odds/live")
    if data is None:
        return None
    await _cache_set(db, "thestatsapi_live_cache", match_id, data)
    return data


async def get_player_heatmap(
    player_id: str, competition_id: str, season_id: str, *, db=None,
) -> Optional[dict]:
    if not (player_id and competition_id and season_id):
        return None
    doc_id = f"{player_id}:{competition_id}:{season_id}"
    cached = await _cache_get(db, "thestatsapi_heatmap_cache", doc_id)
    if cached is not None:
        return cached
    path = (f"/api/football/players/{player_id}"
            f"/competitions/{competition_id}/seasons/{season_id}/heatmap")
    data = await _http_get(path)
    if data is None:
        return None
    await _cache_set(db, "thestatsapi_heatmap_cache", doc_id, data)
    return data


# ─────────────────────────────────────────────────────────────────────
# Phase F74-post — fallback resolver: match-id por (fecha, home, away)
# ─────────────────────────────────────────────────────────────────────
def _normalize_team_name_for_search(name: str) -> str:
    """Pasada simple: lower + acentos + alias internos (cuando estén)."""
    if not isinstance(name, str):
        return ""
    import unicodedata as _u
    nf = _u.normalize("NFD", name)
    base = "".join(c for c in nf if _u.category(c) != "Mn").lower().strip()
    # Aliases internos opcionales (best-effort)
    try:
        from services.team_name_translations import _slug_accented as _slug
        slugged = _slug(base)
        if slugged:
            base = slugged
    except Exception:  # noqa: BLE001
        pass
    return base


async def resolve_thestatsapi_match_id_by_names(
    home_team: str,
    away_team: str,
    date: str,
    *,
    competition: Optional[str] = None,
    db=None,
) -> Optional[str]:
    """Fallback: cuando no tenemos ``_thestatsapi_raw_id`` ni
    ``_external_source_id``, intentar resolver el match en TheStatsAPI
    listando ``/api/football/matches?date_from=DATE&date_to=DATE`` y
    matcheando por nombres normalizados.

    Cachea POSITIVOS y NEGATIVOS para no martillar el endpoint.

    Returns
    -------
    Optional[str]
        El ``match_id`` de TheStatsAPI si encuentra coincidencia, o
        ``None`` si no (con código ``THESTATSAPI_MATCH_MAPPING_NOT_FOUND``
        registrado en logs).
    """
    if not (home_team and away_team and date):
        return None
    if not is_enabled():
        return None

    norm_home = _normalize_team_name_for_search(home_team)
    norm_away = _normalize_team_name_for_search(away_team)
    cache_key = f"{date}:{norm_home}:{norm_away}"

    cached = await _cache_get(db, "thestatsapi_match_mapping_cache", cache_key)
    if cached is not None:
        # Cache puede contener {"match_id": "..."} o {"match_id": None} (negativo).
        return (cached or {}).get("match_id")

    data = await _http_get(
        f"/api/football/matches?date_from={date}&date_to={date}",
    )
    if not isinstance(data, list):
        # API puede devolver dict con "data" — ya lo desempaquetamos en _http_get,
        # pero si por algún motivo viene dict puro, intentar 'matches' clave.
        if isinstance(data, dict):
            data = data.get("matches") or data.get("data") or []
        else:
            data = []

    found_id: Optional[str] = None
    for entry in data:
        if not isinstance(entry, dict):
            continue
        h = (entry.get("home_team") or entry.get("home") or {}) if isinstance(
            entry.get("home_team") or entry.get("home"), dict
        ) else {"name": entry.get("home_team")}
        a = (entry.get("away_team") or entry.get("away") or {}) if isinstance(
            entry.get("away_team") or entry.get("away"), dict
        ) else {"name": entry.get("away_team")}
        h_name = _normalize_team_name_for_search(
            (h or {}).get("name") or entry.get("home_team_name") or ""
        )
        a_name = _normalize_team_name_for_search(
            (a or {}).get("name") or entry.get("away_team_name") or ""
        )
        # Match exact or substring fallback (e.g. "Brazil" ⊂ "Brazil U23").
        match_ok = (
            (h_name == norm_home and a_name == norm_away)
            or (h_name and norm_home and (h_name in norm_home or norm_home in h_name)
                and a_name and norm_away
                and (a_name in norm_away or norm_away in a_name))
        )
        if competition:
            comp_name = _normalize_team_name_for_search(
                (entry.get("competition") or {}).get("name", "")
                if isinstance(entry.get("competition"), dict)
                else (entry.get("competition_name") or "")
            )
            norm_comp = _normalize_team_name_for_search(competition)
            if comp_name and norm_comp and norm_comp not in comp_name:
                continue
        if match_ok:
            found_id = (entry.get("id") or entry.get("match_id")
                        or entry.get("_id"))
            break

    # Cache resultado (positivo o negativo) para evitar repetir lookups.
    await _cache_set(
        db, "thestatsapi_match_mapping_cache", cache_key,
        {"match_id": found_id, "home": norm_home,
         "away": norm_away, "date": date},
    )
    if not found_id:
        log.info("[THESTATSAPI_MATCH_MAPPING_NOT_FOUND] %s vs %s @%s",
                 norm_home, norm_away, date)
        return None
    return str(found_id)


# ─────────────────────────────────────────────────────────────────────
# Normalisation helpers — used by the editorial engine and by tests.
# ─────────────────────────────────────────────────────────────────────
def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _odds_last_seen(node: Any) -> Optional[float]:
    """Pull `last_seen` decimal odds from a leaf ``{"opening":..,"last_seen":..}``."""
    if not isinstance(node, dict):
        return None
    return _safe_float(node.get("last_seen") or node.get("opening"))


def extract_normalised_markets(odds_payload: Any) -> dict:
    """Reduce a TheStatsAPI odds payload to the markets the editorial
    engine consumes. Picks the FIRST bookmaker available (typically Kambi).

    Returns a stable, JSON-friendly dict::

      {
        "bookmaker":   "Kambi",
        "match_odds":  {"home": 1.71, "draw": 3.90, "away": 4.30} | None,
        "total_goals": {"1.5": {"over": 1.19, "under": 4.10}, "2.5": {…}, "3.5": {…}},
        "match_corners": {"9.5": {"over": 1.43, "under": 1.63}, "10.5": {…}, "11.5": {…}},
        "btts":        {"yes": 1.61, "no": 2.30} | None,
      }

    Always returns a dict (possibly empty) — never None.
    """
    out: dict = {
        "bookmaker":     None,
        "match_odds":    None,
        "total_goals":   {},
        "match_corners": {},
        "btts":          None,
    }
    if not isinstance(odds_payload, dict):
        return out
    books = odds_payload.get("bookmakers")
    if not isinstance(books, list) or not books:
        return out
    bm = books[0] if isinstance(books[0], dict) else {}
    out["bookmaker"] = bm.get("bookmaker")
    markets = bm.get("markets") if isinstance(bm.get("markets"), dict) else {}
    # match_odds (1X2)
    mo = markets.get("match_odds")
    if isinstance(mo, dict):
        h = _odds_last_seen(mo.get("home"))
        d = _odds_last_seen(mo.get("draw"))
        a = _odds_last_seen(mo.get("away"))
        if h or d or a:
            out["match_odds"] = {"home": h, "draw": d, "away": a}
    # total goals
    tg = markets.get("total_goals")
    if isinstance(tg, dict):
        for line in GOAL_LINES:
            node = tg.get(str(line)) or tg.get(f"{line:.1f}")
            if isinstance(node, dict):
                out["total_goals"][str(line)] = {
                    "over":  _odds_last_seen(node.get("over")),
                    "under": _odds_last_seen(node.get("under")),
                }
    # match corners
    mc = markets.get("match_corners")
    if isinstance(mc, dict):
        for line in CORNER_LINES:
            node = mc.get(str(line)) or mc.get(f"{line:.1f}")
            if isinstance(node, dict):
                out["match_corners"][str(line)] = {
                    "over":  _odds_last_seen(node.get("over")),
                    "under": _odds_last_seen(node.get("under")),
                }
    # btts
    bt = markets.get("btts")
    if isinstance(bt, dict):
        y = _odds_last_seen(bt.get("yes"))
        n = _odds_last_seen(bt.get("no"))
        if y or n:
            out["btts"] = {"yes": y, "no": n}
    return out


__all__ = [
    "PREMATCH_TTL_SEC", "LIVE_TTL_SEC", "HEATMAP_TTL_SEC",
    "GOAL_LINES", "CORNER_LINES",
    "is_enabled",
    "get_prematch_odds", "get_live_odds", "get_player_heatmap",
    "resolve_thestatsapi_match_id_by_names",
    "extract_normalised_markets",
]
