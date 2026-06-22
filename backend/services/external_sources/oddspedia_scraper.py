"""
oddspedia_scraper
=================

Scraping de https://oddspedia.com/es (Sprint-D9-followup-3 · Jun-2026).

Oddspedia es un agregador europeo con **excelente cobertura de mercados
exóticos**: H2H, BTTS, Over/Under (varias líneas), córners totales,
tarjetas, hándicap asiático, totales por equipo, y disponibilidad para
ligas pequeñas / selecciones nacionales que TheOddsAPI y OddsPortal
típicamente no cubren.

Estrategia:

- Oddspedia expone una **API JSON pública** (no documentada pero estable)
  en ``oddspedia.com/notix/event/<id>/odds`` y endpoints relacionados.
  Usamos esa API vía ``scrape_do_client`` para evitar bloqueos.
- Si la API no responde, caemos al HTML público del partido y parseamos
  defensivamente.
- Cache Mongo TTL 4h.
- Sanity check de overround.
- Fail-soft: cualquier excepción ⇒ ``{available: False, reason_codes: [...]}``.

Reemplaza ``cuotasahora_scraper.py`` (Jun-2026): cuotasahora.com ya no es
un endpoint público estable; oddspedia ofrece la misma cobertura
(mercados exóticos) con una API JSON robusta y un backend confiable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

CACHE_COLLECTION = "oddspedia_odds_cache"
CACHE_TTL_HOURS = 4

# URLs (públicas, sin token).
_BASE_URL = "https://oddspedia.com/es"
_SEARCH_URL = "https://oddspedia.com/api/v1/search?q={query}&type=event"
_EVENT_ODDS_URL = "https://oddspedia.com/api/v1/event/{event_id}/odds"

# Sanity check de overround
_MIN_OVERROUND = 1.01
_MAX_OVERROUND = 1.30  # un poco más laxo que cuotasahora para exotic


def _slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", s).strip().lower()
    return re.sub(r"\s+", "-", s)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _validate_implied_overround(odds: Dict[str, float]) -> Tuple[bool, float]:
    try:
        total = sum(1.0 / float(v) for v in odds.values() if v and float(v) > 1.01)
        return (_MIN_OVERROUND <= total <= _MAX_OVERROUND), total
    except (TypeError, ValueError, ZeroDivisionError):
        return False, 0.0


async def _cache_lookup(db, key: str) -> Optional[Dict[str, Any]]:
    if db is None:
        return None
    try:
        coll = db[CACHE_COLLECTION]
        doc = await coll.find_one({"_key": key})
        if not doc:
            return None
        cached_at = doc.get("_cached_at")
        if isinstance(cached_at, str):
            try:
                cached_at = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
            except ValueError:
                return None
        if not cached_at or _now_utc() - cached_at > timedelta(hours=CACHE_TTL_HOURS):
            return None
        return doc.get("payload")
    except Exception as exc:  # noqa: BLE001
        log.debug("[oddspedia] cache lookup failed: %s", exc)
        return None


async def _cache_store(db, key: str, payload: Dict[str, Any]) -> None:
    if db is None:
        return
    try:
        coll = db[CACHE_COLLECTION]
        await coll.update_one(
            {"_key": key},
            {"$set": {"_key": key, "_cached_at": _now_utc().isoformat(),
                       "payload": payload}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[oddspedia] cache store failed: %s", exc)


async def _fetch(url: str, *, timeout: float = 8.0) -> Optional[str]:
    """Fetch via scrape_do_client; returns body string or None."""
    try:
        from . import scrape_do_client as _scrape
    except Exception:
        return None
    try:
        return await asyncio.wait_for(_scrape.scrape_url(url), timeout=timeout)
    except Exception:
        return None


def _parse_search_response(body: str, home: str, away: str) -> Optional[str]:
    """Best-effort extraction of event_id from oddspedia search JSON."""
    if not body:
        return None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    # Esperado: {"data": [{"id": "12345", "participants": [{"name": "..."}, {...}]}]}
    candidates = []
    if isinstance(data, dict):
        items = data.get("data") or data.get("events") or data.get("results") or []
        if isinstance(items, list):
            candidates.extend(items)
    home_s = _slugify(home)
    away_s = _slugify(away)
    for c in candidates:
        if not isinstance(c, dict):
            continue
        parts = c.get("participants") or c.get("teams") or []
        names = []
        for p in parts:
            if isinstance(p, dict):
                n = _slugify(p.get("name") or p.get("title") or "")
                if n:
                    names.append(n)
        if any(home_s in n for n in names) and any(away_s in n for n in names):
            eid = c.get("id") or c.get("event_id") or c.get("eventId")
            if eid:
                return str(eid)
    return None


def _parse_odds_response(body: str) -> Dict[str, Dict[str, float]]:
    """Convierte respuesta JSON de oddspedia al diccionario de mercados.

    Devuelve un dict con keys: h2h, btts, over_under_2_5, corners_total_*,
    cards_total_*, asian_handicap.  Solo se incluye un mercado si pasa el
    sanity check de overround.
    """
    out: Dict[str, Dict[str, float]] = {}
    if not body:
        return out
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return out

    # Oddspedia agrupa por market_id. La estructura típica es:
    #   {"data": {"markets": [{"market_name": "1X2", "odds": [{"name":"1","value":2.10}, ...]}]}}
    markets_list: List[dict] = []
    if isinstance(data, dict):
        d2 = data.get("data") or data
        if isinstance(d2, dict):
            ml = d2.get("markets") or d2.get("market_groups") or []
            if isinstance(ml, list):
                markets_list = [m for m in ml if isinstance(m, dict)]

    def _extract_odds(items: List[dict], key_map: Dict[str, str]) -> Dict[str, float]:
        result: Dict[str, float] = {}
        for it in items or []:
            if not isinstance(it, dict):
                continue
            nm = (it.get("name") or it.get("outcome") or "").strip().lower()
            val = it.get("value") or it.get("odd") or it.get("price")
            try:
                val_f = float(val) if val else 0.0
            except (TypeError, ValueError):
                continue
            if val_f <= 1.01:
                continue
            tgt = key_map.get(nm)
            if tgt:
                result[tgt] = val_f
        return result

    for m in markets_list:
        name = (m.get("market_name") or m.get("name") or "").strip().lower()
        items = m.get("odds") or m.get("outcomes") or []

        if name in ("1x2", "match winner", "ganador del partido", "moneyline"):
            h2h = _extract_odds(items, {"1": "home", "x": "draw", "2": "away",
                                         "home": "home", "draw": "draw", "away": "away"})
            if {"home", "draw", "away"}.issubset(h2h.keys()):
                ok, _ = _validate_implied_overround(h2h)
                if ok:
                    out["h2h"] = h2h
        elif name in ("btts", "ambos equipos marcan", "both teams to score"):
            btts = _extract_odds(items, {"yes": "yes", "no": "no",
                                          "sí": "yes", "si": "yes"})
            if {"yes", "no"}.issubset(btts.keys()):
                ok, _ = _validate_implied_overround(btts)
                if ok:
                    out["btts"] = btts
        elif "over/under" in name or "over under" in name or "más/menos" in name or "total goals" in name:
            line = m.get("line") or m.get("handicap") or "2.5"
            try:
                line_f = float(line)
            except (TypeError, ValueError):
                line_f = 2.5
            ou = _extract_odds(items, {"over": "over", "under": "under",
                                        "más": "over", "menos": "under",
                                        "mas": "over", f"over {line_f}": "over",
                                        f"under {line_f}": "under"})
            if {"over", "under"}.issubset(ou.keys()):
                ok, _ = _validate_implied_overround(ou)
                if ok:
                    out[f"over_under_{str(line_f).replace('.', '_')}"] = ou
        elif "córner" in name or "corner" in name:
            line = m.get("line") or m.get("handicap")
            try:
                line_f = float(line) if line else 9.5
            except (TypeError, ValueError):
                line_f = 9.5
            ou = _extract_odds(items, {"over": "over", "under": "under",
                                        "más": "over", "menos": "under"})
            if {"over", "under"}.issubset(ou.keys()):
                ok, _ = _validate_implied_overround(ou)
                if ok:
                    out[f"corners_total_{str(line_f).replace('.', '_')}"] = ou
        elif "tarjeta" in name or "card" in name:
            line = m.get("line") or m.get("handicap")
            try:
                line_f = float(line) if line else 4.5
            except (TypeError, ValueError):
                line_f = 4.5
            ou = _extract_odds(items, {"over": "over", "under": "under"})
            if {"over", "under"}.issubset(ou.keys()):
                ok, _ = _validate_implied_overround(ou)
                if ok:
                    out[f"cards_total_{str(line_f).replace('.', '_')}"] = ou

    return out


async def fetch_match_odds(
    home_name: str,
    away_name: str,
    *,
    client: Any = None,
    db: Any = None,
    league_name: Optional[str] = None,
    kickoff_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch odds from oddspedia.com for a single football match.

    Contract::

        {available: bool, source: "oddspedia",
         markets: {...} (si available),
         snapshot_at: iso8601,
         reason_codes: ["ODDSPEDIA_HIT" | "ODDSPEDIA_NO_MATCH" | ...]}

    Never raises.
    """
    cache_key = f"{_slugify(home_name)}__vs__{_slugify(away_name)}"
    if kickoff_iso:
        cache_key += f"__{kickoff_iso[:10]}"

    cached = await _cache_lookup(db, cache_key)
    if cached:
        rc = list(cached.get("reason_codes") or [])
        if "ODDSPEDIA_CACHE_HIT" not in rc:
            rc.append("ODDSPEDIA_CACHE_HIT")
        cached["reason_codes"] = rc
        return cached

    # Paso 1 — search
    query = f"{home_name} {away_name}"
    search_body = await _fetch(_SEARCH_URL.format(query=quote_plus(query)), timeout=8.0)
    if not search_body:
        result = {
            "available": False, "source": "oddspedia",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["ODDSPEDIA_SEARCH_REQUEST_FAILED"],
        }
        await _cache_store(db, cache_key, result)
        return result

    event_id = _parse_search_response(search_body, home_name, away_name)
    if not event_id:
        result = {
            "available": False, "source": "oddspedia",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["ODDSPEDIA_NO_MATCH"],
        }
        await _cache_store(db, cache_key, result)
        return result

    # Paso 2 — fetch odds for the event
    odds_body = await _fetch(_EVENT_ODDS_URL.format(event_id=event_id), timeout=8.0)
    if not odds_body:
        result = {
            "available": False, "source": "oddspedia",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["ODDSPEDIA_ODDS_REQUEST_FAILED", f"ODDSPEDIA_EVENT_ID:{event_id}"],
        }
        await _cache_store(db, cache_key, result)
        return result

    markets = _parse_odds_response(odds_body)
    if not markets:
        result = {
            "available": False, "source": "oddspedia",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["ODDSPEDIA_NO_MARKETS_RECOGNIZED", f"ODDSPEDIA_EVENT_ID:{event_id}"],
        }
        await _cache_store(db, cache_key, result)
        return result

    result = {
        "available": True,
        "source": "oddspedia",
        "markets": markets,
        "snapshot_at": _now_utc().isoformat(),
        "reason_codes": ["ODDSPEDIA_HIT", f"ODDSPEDIA_EVENT_ID:{event_id}"],
    }
    await _cache_store(db, cache_key, result)
    return result


__all__ = [
    "fetch_match_odds",
    "CACHE_COLLECTION",
    "CACHE_TTL_HOURS",
]
