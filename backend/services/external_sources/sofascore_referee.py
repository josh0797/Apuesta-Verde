"""Sprint-D9-HOTFIX4 · Sofascore referee extractor (HTML público + Scrape.do).

Extrae los datos del árbitro asignado a un partido de Sofascore parseando
el bloque ``__NEXT_DATA__`` (Next.js SSR JSON embebido) que vive dentro
del HTML público. Esta ruta funciona vía Scrape.do mientras
``api.sofascore.com`` está bloqueado para esa CDN.

Datos extraídos por partido::

    {
      "available":            bool,
      "source":               "sofascore",
      "fetch_method":         "scrapedo+html",
      "match_slug":           "iran-belgium/rUbsqVb",
      "match_id":             15186499,
      "match_label":          "Iran vs Belgium",
      "kickoff_iso":          "2026-06-21T13:00:00+00:00",
      "competition":          "FIFA World Cup",
      "stadium":              "SoFi Stadium",
      "city":                 "Inglewood, United States",
      "referee": {
        "id":                 322839,
        "slug":               "dario-herrera",
        "name":               "Dario Herrera",
        "country":            {"name": "Argentina", "alpha2": "AR"},
        "games":              466,
        "yellow_cards":       2534,
        "red_cards":          99,
        "yellow_red_cards":   79,
        "yellow_cards_per_game":     5.44,
        "red_cards_per_game":        0.21,
        "second_yellow_per_game":    0.17,
        "all_red_cards_per_game":    0.38,  # red + yellow_red, lo que SofaScore muestra como "0.38"
        "profile_url":        "https://www.sofascore.com/es/football/referee/dario-herrera/322839",
      },
      "fetched_at":           ISO-8601 UTC,
      "reason_codes":         [...]  (cuando available=False)
    }

Cache MongoDB: ``external_referee_cache`` con TTL 24h
(árbitro raramente cambia prematch).

Public API:
  ``async fetch_sofascore_referee_for_match(home, away, *, slug=None,
                                              use_cache=True)``
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("sofascore_referee")

CACHE_TTL_S = 24 * 3600  # 24h
HTTP_TIMEOUT_S = float(os.environ.get("SOFASCORE_REFEREE_TIMEOUT_S", "45"))

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)

_BASE = "https://www.sofascore.com"


def _strip_accents_lower(s: str) -> str:
    if not isinstance(s, str):
        return ""
    out = "".join(c for c in unicodedata.normalize("NFD", s)
                  if unicodedata.category(c) != "Mn")
    return out.lower().strip()


def _team_slug(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = _strip_accents_lower(name)
    s = re.sub(r"[^a-z0-9\s\-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


# ─────────────────────────────────────────────────────────────────────
# Match URL builders / parsers
# ─────────────────────────────────────────────────────────────────────
def build_match_url(home: str, away: str, *, code: Optional[str] = None,
                      lang: str = "es") -> Optional[str]:
    """Construye la URL canónica del partido en SofaScore.

    Patrón aceptado por el front:
        /{lang}/football/match/{home-slug}-{away-slug}/{code}

    Si no se conoce el ``code`` (string corto opaco de SofaScore,
    p.ej. ``rUbsqVb``), se devuelve la URL sin code y SofaScore la
    redirige a la versión canónica.
    """
    h, a = _team_slug(home), _team_slug(away)
    if not h or not a:
        return None
    if code:
        return f"{_BASE}/{lang}/football/match/{h}-{a}/{code}"
    return f"{_BASE}/{lang}/football/match/{h}-{a}"


def _safe_div(num: float, den: float) -> Optional[float]:
    try:
        if not den or den <= 0:
            return None
        return round(float(num) / float(den), 3)
    except (TypeError, ValueError):
        return None


def parse_sofascore_match_next_data(html: str) -> dict:
    """Parsea el HTML público de un partido SofaScore y devuelve el
    payload normalizado con referee + metadata.

    Fail-soft: si no encuentra ``__NEXT_DATA__`` o el árbitro no está
    en el JSON, devuelve ``{"available": False, "reason_codes": [...]}``.
    """
    if not isinstance(html, str) or len(html) < 500:
        return {"available": False,
                "reason_codes": ["REFEREE_HTML_EMPTY_OR_SHORT"]}

    m = _NEXT_DATA_RE.search(html)
    if not m:
        return {"available": False,
                "reason_codes": ["REFEREE_NEXT_DATA_NOT_FOUND"]}
    try:
        next_data = json.loads(m.group(1))
    except Exception as exc:  # noqa: BLE001
        return {"available": False,
                "reason_codes": [f"REFEREE_NEXT_DATA_PARSE_FAILED:{exc!s}"[:120]]}

    event = (
        ((next_data.get("props") or {}).get("pageProps") or {}).get("event")
        or {}
    )
    if not isinstance(event, dict):
        return {"available": False,
                "reason_codes": ["REFEREE_EVENT_BLOCK_MISSING"]}

    referee_raw = event.get("referee") or {}
    if not isinstance(referee_raw, dict) or not referee_raw.get("id"):
        return {"available": False,
                "reason_codes": ["REFEREE_NOT_ASSIGNED_BY_SOFASCORE"],
                "match_id": event.get("id"),
                "match_label": _build_match_label(event)}

    games   = referee_raw.get("games") or 0
    yellows = referee_raw.get("yellowCards") or 0
    reds    = referee_raw.get("redCards") or 0
    yr      = referee_raw.get("yellowRedCards") or 0

    referee_out = {
        "id":               referee_raw.get("id"),
        "slug":             referee_raw.get("slug"),
        "name":             referee_raw.get("name"),
        "country":          {
            "name":    (referee_raw.get("country") or {}).get("name"),
            "alpha2":  (referee_raw.get("country") or {}).get("alpha2"),
            "alpha3":  (referee_raw.get("country") or {}).get("alpha3"),
            "slug":    (referee_raw.get("country") or {}).get("slug"),
        },
        "games":            int(games),
        "yellow_cards":     int(yellows),
        "red_cards":        int(reds),
        "yellow_red_cards": int(yr),
        "yellow_cards_per_game":  _safe_div(yellows, games),
        "red_cards_per_game":     _safe_div(reds, games),
        "second_yellow_per_game": _safe_div(yr, games),
        # SofaScore muestra como "rojas" la suma de directas + segunda amarilla.
        "all_red_cards_per_game": _safe_div(reds + yr, games),
        "profile_url":      (
            f"{_BASE}/es/football/referee/{referee_raw.get('slug')}/"
            f"{referee_raw.get('id')}"
            if referee_raw.get("slug") and referee_raw.get("id")
            else None
        ),
    }

    # Metadata adicional (best-effort, fail-soft).
    season = (event.get("season") or {}).get("name")
    tournament = (event.get("tournament") or {}).get("name")
    venue = event.get("venue") or {}
    stadium_name = (venue.get("stadium") or {}).get("name") or venue.get("name")
    city_name = (venue.get("city") or {}).get("name")
    venue_country = (venue.get("country") or {}).get("name")

    kickoff_ts = event.get("startTimestamp")
    kickoff_iso = None
    if isinstance(kickoff_ts, int):
        try:
            kickoff_iso = datetime.fromtimestamp(kickoff_ts, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            kickoff_iso = None

    return {
        "available":     True,
        "source":        "sofascore",
        "fetch_method":  "scrapedo+html",
        "match_id":      event.get("id"),
        "match_slug":    event.get("slug"),
        "match_label":   _build_match_label(event),
        "kickoff_iso":   kickoff_iso,
        "competition":   tournament,
        "season":        season,
        "stadium":       stadium_name,
        "city":          (
            f"{city_name}, {venue_country}"
            if city_name and venue_country
            else (city_name or venue_country)
        ),
        "referee":       referee_out,
        "reason_codes":  ["REFEREE_PARSED_OK"],
    }


def _build_match_label(event: dict) -> Optional[str]:
    home = (event.get("homeTeam") or {}).get("name")
    away = (event.get("awayTeam") or {}).get("name")
    if home and away:
        return f"{home} vs {away}"
    return None


# ─────────────────────────────────────────────────────────────────────
# Cache (MongoDB) — TTL 24h
# ─────────────────────────────────────────────────────────────────────
_db_handle = None


def _get_db():
    global _db_handle
    if _db_handle is not None:
        return _db_handle
    try:
        from server import db  # type: ignore
        _db_handle = db
        return db
    except Exception:  # noqa: BLE001
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            mongo_url = os.environ.get("MONGO_URL")
            if not mongo_url:
                return None
            client = AsyncIOMotorClient(mongo_url)
            _db_handle = client[os.environ.get("DB_NAME", "test_database")]
            return _db_handle
        except Exception:  # noqa: BLE001
            return None


async def _cache_lookup(cache_key: str) -> Optional[dict]:
    db = _get_db()
    if db is None:
        return None
    try:
        doc = await db.external_referee_cache.find_one({"cache_key": cache_key})
        if not doc:
            return None
        cached_at = doc.get("cached_at")
        if isinstance(cached_at, datetime):
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()
            if age > CACHE_TTL_S:
                return None
        return doc.get("payload")
    except Exception:  # noqa: BLE001
        return None


async def _cache_save(cache_key: str, payload: dict) -> None:
    db = _get_db()
    if db is None:
        return
    try:
        await db.external_referee_cache.update_one(
            {"cache_key": cache_key},
            {"$set": {
                "cache_key": cache_key,
                "payload":   payload,
                "cached_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except Exception:  # noqa: BLE001
        pass


async def ensure_indexes() -> None:
    """Idempotente. TTL 24h sobre ``cached_at``."""
    db = _get_db()
    if db is None:
        return
    try:
        await db.external_referee_cache.create_index(
            "cached_at", expireAfterSeconds=CACHE_TTL_S,
        )
        await db.external_referee_cache.create_index(
            "cache_key", unique=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[referee_cache] ensure_indexes failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────
# Public fetcher
# ─────────────────────────────────────────────────────────────────────
async def fetch_sofascore_referee_for_match(
    home: str,
    away: str,
    *,
    code: Optional[str] = None,
    lang: str = "es",
    use_cache: bool = True,
) -> dict:
    """Fetch + parse del árbitro asignado a un partido SofaScore.

    Parameters
    ----------
    home, away : str
        Nombres de los equipos (para construir el slug).
    code : str, opcional
        Código corto opaco SofaScore (p.ej. ``rUbsqVb``). Si se provee,
        la URL del partido será determinística; si no, SofaScore
        redirige al canonical.
    lang : str, opcional
        Idioma para el slug (``es``, ``en``...). Default ``es``.
    use_cache : bool, opcional
        Si activa, lee/escribe Mongo ``external_referee_cache`` con
        TTL 24h.

    Returns
    -------
    dict
        Shape estable (ver módulo docstring). Fail-soft.
    """
    if not home or not away:
        return {"available": False, "source": "sofascore",
                "reason_codes": ["REFEREE_TEAMS_MISSING"]}

    url = build_match_url(home, away, code=code, lang=lang)
    if not url:
        return {"available": False, "source": "sofascore",
                "reason_codes": ["REFEREE_URL_BUILD_FAILED"]}

    cache_key = f"sofascore_referee:{url}"
    if use_cache:
        cached = await _cache_lookup(cache_key)
        if cached is not None:
            cached.setdefault("from_cache", True)
            return cached

    if not os.environ.get("SCRAPEDO_TOKEN"):
        return {"available": False, "source": "sofascore",
                "reason_codes": ["REFEREE_SCRAPEDO_DISABLED"],
                "source_url": url}

    try:
        from services.scrape_do_client import fetch_via_scrapedo_result
        # render=True: SofaScore es SPA Next.js; el HTML llega sin
        # __NEXT_DATA__ si NO se renderiza JS. Con render=True llega
        # el SSR completo.
        res = await fetch_via_scrapedo_result(
            url, timeout=HTTP_TIMEOUT_S, render=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[sofascore_referee] fetch crashed: %s", exc)
        return {"available": False, "source": "sofascore",
                "reason_codes": [f"REFEREE_FETCH_EXCEPTION:{exc!s}"[:120]],
                "source_url": url}

    if not res or not res.get("ok"):
        payload = {
            "available":   False,
            "source":      "sofascore",
            "reason_codes": [
                (res or {}).get("reason_code") or "REFEREE_FETCH_NON_OK",
            ],
            "status_code": (res or {}).get("status_code"),
            "source_url":  url,
            "fetched_at":  datetime.now(timezone.utc).isoformat(),
        }
        # Sin cache para fallos transitorios.
        return payload

    parsed = parse_sofascore_match_next_data(res.get("html") or "")
    parsed.setdefault("source_url", url)
    parsed.setdefault("fetched_at", datetime.now(timezone.utc).isoformat())

    # Cache solo cuando hay árbitro real (no para "no asignado").
    if use_cache and parsed.get("available"):
        await _cache_save(cache_key, parsed)

    return parsed


__all__ = [
    "build_match_url",
    "parse_sofascore_match_next_data",
    "fetch_sofascore_referee_for_match",
    "ensure_indexes",
]
