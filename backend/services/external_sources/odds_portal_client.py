"""Sprint-D9-OddsCascade · OddsPortal scraper client (fallback de odds H2H).

Reemplaza a Sportytrader como **fallback** para extraer cuotas H2H
(home/draw/away) cuando TheOddsAPI no devuelve cobertura para el partido.

Diseño:
  * Fail-soft end-to-end: cualquier error → ``{"available": False, "reason_code": "..."}``;
    NUNCA levanta excepciones que propaguen al pipeline.
  * Pasa SIEMPRE por ``scrape_do_client`` (Bright Data está bloqueado por
    política de gambling). Si ``SCRAPEDO_TOKEN`` no está configurado,
    retorna ``ODDS_PORTAL_SCRAPEDO_DISABLED`` y la cascada continúa.
  * Cache en MongoDB ``external_odds_cache`` con TTL 6h (las cuotas H2H
    cambian poco prematch para mercados líquidos).
  * Parser tolerante a cambios de marcado (selectores compuestos +
    fallback regex sobre el texto plano).

Public API:
  ``async fetch_oddsportal_h2h(home, away, *, league=None, kickoff_iso=None)``
      → dict::

          {
            "available":   bool,
            "source":      "oddsportal",
            "home_team":   "...",
            "away_team":   "...",
            "odd_home":    float | None,
            "odd_draw":    float | None,
            "odd_away":    float | None,
            "bookmaker":   str (mejor cuota disponible) | "average",
            "fetched_at":  ISO-8601 UTC,
            "reason_code": "..."  (cuando available=False)
          }
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("oddsportal")

# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────
ODDSPORTAL_BASE = "https://www.oddsportal.com"
CACHE_TTL_S = 6 * 3600  # 6h
HTTP_TIMEOUT_S = float(os.environ.get("ODDS_PORTAL_TIMEOUT_S", "45"))


def _strip_accents(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


def _team_slug(name: str) -> str:
    """OddsPortal usa slugs alfanuméricos con guiones."""
    if not isinstance(name, str):
        return ""
    s = _strip_accents(name).strip()
    s = re.sub(r"[^a-z0-9\s\-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def build_oddsportal_match_url(home: str, away: str,
                                 *, league_slug: Optional[str] = None) -> Optional[str]:
    """Best-effort URL construction.

    Patterns OddsPortal aceptados (rough heuristic, fail-soft):
      - ``/soccer/<league>/<home>-<away>/``
      - ``/soccer/<league>/<home>-vs-<away>/``

    Sin league_slug, devolvemos un search-URL que la UI puede usar como
    deep-link informativo.
    """
    if not home or not away:
        return None
    hs, as_ = _team_slug(home), _team_slug(away)
    if not hs or not as_:
        return None
    if league_slug:
        league_slug = _team_slug(league_slug)
        return f"{ODDSPORTAL_BASE}/soccer/{league_slug}/{hs}-{as_}/"
    # Search URL — útil aunque no carguemos cuotas (UI link).
    q = f"{hs.replace('-', '+')}+{as_.replace('-', '+')}"
    return f"{ODDSPORTAL_BASE}/search/results/{q}/"


# ─────────────────────────────────────────────────────────────────────
# Cache (Mongo)
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
            _db_handle = client.get_default_database()
            return _db_handle
        except Exception:  # noqa: BLE001
            return None


async def _cache_lookup(cache_key: str) -> Optional[dict]:
    db = _get_db()
    if db is None:
        return None
    try:
        doc = await db.external_odds_cache.find_one({"cache_key": cache_key})
        if not doc:
            return None
        # Validar TTL manualmente (el índice TTL es del lado server,
        # pero el test/dev puede correr sin TTL aplicado).
        cached_at = doc.get("cached_at")
        if isinstance(cached_at, datetime):
            age_s = (datetime.now(timezone.utc) - cached_at).total_seconds()
            if age_s > CACHE_TTL_S:
                return None
        return doc.get("payload")
    except Exception as exc:  # noqa: BLE001
        log.debug("[odds_portal_cache] lookup failed: %s", exc)
        return None


async def _cache_save(cache_key: str, payload: dict) -> None:
    db = _get_db()
    if db is None:
        return
    try:
        await db.external_odds_cache.update_one(
            {"cache_key": cache_key},
            {"$set": {
                "cache_key": cache_key,
                "payload":   payload,
                "cached_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[odds_portal_cache] save failed: %s", exc)


async def ensure_indexes() -> None:
    """Idempotente. TTL 6h sobre ``cached_at``."""
    db = _get_db()
    if db is None:
        return
    try:
        await db.external_odds_cache.create_index(
            "cached_at", expireAfterSeconds=CACHE_TTL_S,
        )
        await db.external_odds_cache.create_index(
            "cache_key", unique=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[odds_portal_cache] ensure_indexes failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────
# HTML parser (fail-soft)
# ─────────────────────────────────────────────────────────────────────
# OddsPortal pone la línea promedio H2H en un bloque tipo:
#   <div class="...avg..."> 2.35 </div> <div class="..."> 3.40 </div>
#   <div class="..."> 2.85 </div>
# pero el marcado cambia con frecuencia. Estrategia:
#   1. Buscar bloques "1 X 2" o "Home Draw Away" cercanos en el HTML.
#   2. Extraer los 3 primeros números decimales válidos posteriores.
#   3. Validar rango plausible (1.01 ≤ odd ≤ 50.0).
_DECIMAL_ODDS_RE = re.compile(r"\b(\d{1,2}\.\d{1,3})\b")
_AVG_BLOCK_HINTS = (
    "average", "avg.", "promedio", "1 x 2", "1x2", "home draw away",
)


def parse_oddsportal_h2h(html: str) -> dict:
    """Parsea HTML de OddsPortal y devuelve odds H2H + bookmaker.

    Fail-soft: si no encuentra al menos (home, draw, away) válidos,
    devuelve ``{"available": False, "reason_code": "..."}``.
    """
    if not isinstance(html, str) or len(html) < 200:
        return {"available": False, "reason_code": "ODDS_PORTAL_HTML_EMPTY"}

    text_lc = html.lower()

    # Estrategia 1: si encontramos un bloque "average" / "promedio",
    # extraemos los 3 primeros odds DESPUÉS de ese ancla.
    anchor_idx = -1
    for hint in _AVG_BLOCK_HINTS:
        idx = text_lc.find(hint)
        if idx != -1:
            anchor_idx = idx
            break

    candidates: list[float] = []
    if anchor_idx >= 0:
        # Buscar los próximos 800 chars tras el ancla.
        window = html[anchor_idx:anchor_idx + 1500]
        for m in _DECIMAL_ODDS_RE.finditer(window):
            try:
                v = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if 1.01 <= v <= 50.0:
                candidates.append(v)
            if len(candidates) >= 3:
                break

    # Estrategia 2 (fallback): tomar los primeros 3 decimales del
    # documento entero (riesgo: matchear estadísticas, no odds).
    if len(candidates) < 3:
        for m in _DECIMAL_ODDS_RE.finditer(html):
            try:
                v = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if 1.01 <= v <= 50.0:
                candidates.append(v)
            if len(candidates) >= 3:
                break

    if len(candidates) < 3:
        return {"available": False, "reason_code": "ODDS_PORTAL_PARSE_NO_TRIPLE"}

    odd_home, odd_draw, odd_away = candidates[0], candidates[1], candidates[2]

    # Sanity: la suma de implied probs debe estar en rango razonable
    # (1.0 ≤ Σ ≤ 1.25) para descartar tripletas extraídas de stats.
    try:
        sum_ip = 1.0 / odd_home + 1.0 / odd_draw + 1.0 / odd_away
    except ZeroDivisionError:
        return {"available": False, "reason_code": "ODDS_PORTAL_PARSE_DIV_ZERO"}
    if not (0.95 <= sum_ip <= 1.30):
        return {"available": False,
                "reason_code": "ODDS_PORTAL_PARSE_IMPLAUSIBLE_TRIPLE",
                "debug": {"odd_home": odd_home, "odd_draw": odd_draw,
                          "odd_away": odd_away, "sum_implied": round(sum_ip, 3)}}

    return {
        "available":  True,
        "odd_home":   odd_home,
        "odd_draw":   odd_draw,
        "odd_away":   odd_away,
        "bookmaker":  "oddsportal_avg",
    }


# ─────────────────────────────────────────────────────────────────────
# Public fetcher
# ─────────────────────────────────────────────────────────────────────
async def fetch_oddsportal_h2h(
    home: str,
    away: str,
    *,
    league_slug: Optional[str] = None,
    kickoff_iso: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    """Fetch + parse OddsPortal H2H odds para un partido. Fail-soft.

    Returns dict con shape estable (ver módulo docstring).
    """
    if not home or not away:
        return {"available": False, "source": "oddsportal",
                "reason_code": "ODDS_PORTAL_TEAMS_MISSING"}

    url = build_oddsportal_match_url(home, away, league_slug=league_slug)
    if not url:
        return {"available": False, "source": "oddsportal",
                "reason_code": "ODDS_PORTAL_URL_BUILD_FAILED"}

    cache_key = f"oddsportal:{url}"
    if use_cache:
        cached = await _cache_lookup(cache_key)
        if cached is not None:
            return cached

    if not os.environ.get("SCRAPEDO_TOKEN"):
        payload = {"available": False, "source": "oddsportal",
                   "reason_code": "ODDS_PORTAL_SCRAPEDO_DISABLED",
                   "search_url": url}
        # No cacheamos disabled — cambia al setear el token.
        return payload

    try:
        from services.scrape_do_client import fetch_via_scrapedo_result
        result = await fetch_via_scrapedo_result(url, timeout=HTTP_TIMEOUT_S)
        if not result.get("ok"):
            payload = {
                "available":   False,
                "source":      "oddsportal",
                "reason_code": result.get("reason_code") or "ODDS_PORTAL_FETCH_FAILED",
                "search_url":  url,
                "fetched_at":  datetime.now(timezone.utc).isoformat(),
            }
            if use_cache:
                await _cache_save(cache_key, payload)
            return payload

        html = result.get("html") or ""
        parsed = parse_oddsportal_h2h(html)
        if not parsed.get("available"):
            payload = {
                "available":   False,
                "source":      "oddsportal",
                "reason_code": parsed.get("reason_code") or "ODDS_PORTAL_PARSE_FAILED",
                "search_url":  url,
                "fetched_at":  datetime.now(timezone.utc).isoformat(),
            }
            if use_cache:
                await _cache_save(cache_key, payload)
            return payload

        payload = {
            "available":  True,
            "source":     "oddsportal",
            "home_team":  home,
            "away_team":  away,
            "odd_home":   parsed["odd_home"],
            "odd_draw":   parsed["odd_draw"],
            "odd_away":   parsed["odd_away"],
            "bookmaker":  parsed.get("bookmaker") or "oddsportal_avg",
            "source_url": url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if use_cache:
            await _cache_save(cache_key, payload)
        return payload

    except Exception as exc:  # noqa: BLE001
        log.warning("[odds_portal] fetch failed for %s vs %s: %s",
                    home, away, exc)
        return {"available": False, "source": "oddsportal",
                "reason_code": "ODDS_PORTAL_EXCEPTION",
                "search_url": url,
                "fetched_at": datetime.now(timezone.utc).isoformat()}


__all__ = [
    "ODDSPORTAL_BASE",
    "build_oddsportal_match_url",
    "parse_oddsportal_h2h",
    "fetch_oddsportal_h2h",
    "ensure_indexes",
]
