"""
cuotasahora_scraper
===================

Scraping de https://www.cuotasahora.com (Sprint-D9-followup-2 · Jun-2026).

Cuotasahora es un agregador de cuotas español con buena cobertura de
**mercados exóticos** (córners asiáticos, tarjetas, hándicap asiático,
totales por equipo, etc.) que TheOddsAPI y OddsPortal típicamente no
cubren.  Se usa como **primario** en la nueva cascade ``fetch_football_odds``.

Reglas:

- Scraping HTML vía ``scrape_do_client`` (sin headless browser, fail-soft).
- Cache en Mongo TTL 4h (``cuotasahora_odds_cache``) para evitar rate-limits.
- Sanity check: si las cuotas implican una probabilidad agregada fuera del
  rango ``[1.00, 1.25]`` el resultado se descarta (overround inválido).
- **Sin secretos**: no se exponen credenciales ni cookies.
- **Fail-soft**: cualquier excepción se traduce en
  ``{"available": False, "reason_codes": ["CUOTASAHORA_*"]}``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

CACHE_COLLECTION = "cuotasahora_odds_cache"
CACHE_TTL_HOURS = 4

# Endpoints públicos de cuotasahora
_BASE_URL = "https://www.cuotasahora.com"
_SEARCH_URL = _BASE_URL + "/buscar?q={query}"

# Sanity check para overround (suma de implied probs).
# 1.00 = arbitraje perfecto (no debería ocurrir, sospechoso).
# 1.25 = 25% de margin (techo razonable para mercados exóticos).
_MIN_OVERROUND = 1.01
_MAX_OVERROUND = 1.25


def _slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", s).strip().lower()
    return re.sub(r"\s+", "-", s)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _validate_implied_overround(odds: Dict[str, float]) -> Tuple[bool, float]:
    """True si la suma de 1/odds está dentro del rango aceptable.

    Para H2H 3-way: home + draw + away ≈ 1.0–1.25.
    Para 2-way (BTTS, Over/Under): 2 odds ≈ 1.02–1.15 típicamente; usamos
    el mismo rango por simplicidad.
    """
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
        if not cached_at:
            return None
        if isinstance(cached_at, str):
            try:
                cached_at = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
            except ValueError:
                return None
        if _now_utc() - cached_at > timedelta(hours=CACHE_TTL_HOURS):
            return None
        return doc.get("payload")
    except Exception as exc:  # noqa: BLE001
        log.debug("[cuotasahora] cache lookup failed: %s", exc)
        return None


async def _cache_store(db, key: str, payload: Dict[str, Any]) -> None:
    if db is None:
        return
    try:
        coll = db[CACHE_COLLECTION]
        await coll.update_one(
            {"_key": key},
            {"$set": {
                "_key": key,
                "_cached_at": _now_utc().isoformat(),
                "payload": payload,
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[cuotasahora] cache store failed: %s", exc)


def _parse_odds_block(html: str) -> Dict[str, Dict[str, float]]:
    """
    Parsea el HTML de cuotasahora y devuelve un dict de mercados.

    Estructura esperada (best-effort, fail-soft):
        {
            "h2h": {"home": 2.10, "draw": 3.30, "away": 3.50},
            "btts": {"yes": 1.85, "no": 1.95},
            "over_under_2_5": {"over": 2.05, "under": 1.80},
            "asian_handicap": {"home_-0.5": 2.00, "away_+0.5": 1.85},
            "corners_total_9_5": {"over": 1.95, "under": 1.90},
            "cards_total_4_5": {"over": 2.10, "under": 1.75},
            ...
        }

    Esta es una implementación **defensiva**: si el sitio cambia su HTML,
    el parser devuelve ``{}`` en lugar de romper.
    """
    out: Dict[str, Dict[str, float]] = {}
    if not isinstance(html, str) or len(html) < 100:
        return out

    # H2H (1X2) — patrón típico de tarjetas con clase "odds" o "cuota"
    h2h = {}
    m_home = re.search(r'data-market="1X2"[^>]*data-pick="1"[^>]*data-odd="([\d.]+)"', html)
    m_draw = re.search(r'data-market="1X2"[^>]*data-pick="X"[^>]*data-odd="([\d.]+)"', html)
    m_away = re.search(r'data-market="1X2"[^>]*data-pick="2"[^>]*data-odd="([\d.]+)"', html)
    if m_home and m_draw and m_away:
        h2h = {
            "home": float(m_home.group(1)),
            "draw": float(m_draw.group(1)),
            "away": float(m_away.group(1)),
        }
        valid, overround = _validate_implied_overround(h2h)
        if valid:
            out["h2h"] = h2h

    # BTTS
    m_btts_yes = re.search(r'data-market="BTTS"[^>]*data-pick="YES"[^>]*data-odd="([\d.]+)"', html)
    m_btts_no  = re.search(r'data-market="BTTS"[^>]*data-pick="NO"[^>]*data-odd="([\d.]+)"', html)
    if m_btts_yes and m_btts_no:
        btts = {"yes": float(m_btts_yes.group(1)), "no": float(m_btts_no.group(1))}
        valid, _ = _validate_implied_overround(btts)
        if valid:
            out["btts"] = btts

    # Over/Under 2.5
    m_ov25 = re.search(r'data-market="OU"[^>]*data-line="2.5"[^>]*data-pick="OVER"[^>]*data-odd="([\d.]+)"', html)
    m_un25 = re.search(r'data-market="OU"[^>]*data-line="2.5"[^>]*data-pick="UNDER"[^>]*data-odd="([\d.]+)"', html)
    if m_ov25 and m_un25:
        ou = {"over": float(m_ov25.group(1)), "under": float(m_un25.group(1))}
        valid, _ = _validate_implied_overround(ou)
        if valid:
            out["over_under_2_5"] = ou

    # Corners total — varias líneas comunes (8.5, 9.5, 10.5)
    for line in ("8.5", "9.5", "10.5"):
        m_co_ov = re.search(
            rf'data-market="CORNERS"[^>]*data-line="{re.escape(line)}"[^>]*data-pick="OVER"[^>]*data-odd="([\d.]+)"',
            html,
        )
        m_co_un = re.search(
            rf'data-market="CORNERS"[^>]*data-line="{re.escape(line)}"[^>]*data-pick="UNDER"[^>]*data-odd="([\d.]+)"',
            html,
        )
        if m_co_ov and m_co_un:
            key = f"corners_total_{line.replace('.', '_')}"
            ou = {"over": float(m_co_ov.group(1)), "under": float(m_co_un.group(1))}
            valid, _ = _validate_implied_overround(ou)
            if valid:
                out[key] = ou

    # Cards total — líneas comunes 3.5, 4.5, 5.5
    for line in ("3.5", "4.5", "5.5"):
        m_c_ov = re.search(
            rf'data-market="CARDS"[^>]*data-line="{re.escape(line)}"[^>]*data-pick="OVER"[^>]*data-odd="([\d.]+)"',
            html,
        )
        m_c_un = re.search(
            rf'data-market="CARDS"[^>]*data-line="{re.escape(line)}"[^>]*data-pick="UNDER"[^>]*data-odd="([\d.]+)"',
            html,
        )
        if m_c_ov and m_c_un:
            key = f"cards_total_{line.replace('.', '_')}"
            ou = {"over": float(m_c_ov.group(1)), "under": float(m_c_un.group(1))}
            valid, _ = _validate_implied_overround(ou)
            if valid:
                out[key] = ou

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
    """
    Fetch odds for a single match from cuotasahora.com.

    Returns a dict with the contract::

        {
            "available": bool,
            "source": "cuotasahora",
            "markets": {...},                   # only if available=True
            "snapshot_at": iso8601,
            "reason_codes": ["CUOTASAHORA_HIT" | "CUOTASAHORA_NO_MATCH" | ...],
        }

    **Never raises** — caller can trust this is fail-soft.
    """
    cache_key = f"{_slugify(home_name)}__vs__{_slugify(away_name)}"
    if kickoff_iso:
        cache_key += f"__{kickoff_iso[:10]}"

    cached = await _cache_lookup(db, cache_key)
    if cached:
        cached.setdefault("reason_codes", []).append("CUOTASAHORA_CACHE_HIT")
        return cached

    try:
        from . import scrape_do_client as _scrape  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log.debug("[cuotasahora] scrape_do_client unavailable: %s", exc)
        return {
            "available": False,
            "source": "cuotasahora",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["CUOTASAHORA_SCRAPE_CLIENT_UNAVAILABLE"],
        }

    query = f"{home_name} vs {away_name}"
    search_url = _SEARCH_URL.format(query=quote_plus(query))

    html: Optional[str] = None
    try:
        html = await asyncio.wait_for(
            _scrape.scrape_url(search_url),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        return {
            "available": False,
            "source": "cuotasahora",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["CUOTASAHORA_REQUEST_TIMEOUT"],
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("[cuotasahora] request failed: %s", exc)
        return {
            "available": False,
            "source": "cuotasahora",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["CUOTASAHORA_REQUEST_FAILED"],
        }

    if not html or len(html) < 200:
        return {
            "available": False,
            "source": "cuotasahora",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["CUOTASAHORA_EMPTY_RESPONSE"],
        }

    markets = _parse_odds_block(html)
    if not markets:
        result = {
            "available": False,
            "source": "cuotasahora",
            "snapshot_at": _now_utc().isoformat(),
            "reason_codes": ["CUOTASAHORA_NO_MARKETS_RECOGNIZED"],
        }
        await _cache_store(db, cache_key, result)
        return result

    result = {
        "available": True,
        "source": "cuotasahora",
        "markets": markets,
        "snapshot_at": _now_utc().isoformat(),
        "reason_codes": ["CUOTASAHORA_HIT"],
    }
    await _cache_store(db, cache_key, result)
    return result


__all__ = [
    "fetch_match_odds",
    "CACHE_COLLECTION",
    "CACHE_TTL_HOURS",
]
