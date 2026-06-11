"""
services.football_news_context_ingestion
=========================================

Phase F57 — fail-soft football news ingestion for context detection.

Mission
-------
Fetch recent football-relevant news headlines that may signal **squad
disruption** events (disciplinary removals, internal conflict, players
rejected from camp). Detection is conservative: we never assert a
disruption without a source URL + a literal headline phrase.

Design principles
-----------------
* **Opt-in & fail-soft**: any HTTP / parse / rate-limit failure returns
  an empty payload (never raises).
* **Short timeout** (default 4s) so a slow source can't stall the
  trend engine.
* **Cache** (default 6h) keyed by ``(team_name, locale)``.
* **Source transparency**: every headline carries ``source_url`` +
  ``source_name`` + ``fetched_at`` for the UI to render.
* **Locale-aware keyword library**: Spanish first, with light English
  fallbacks.
* **Configurable source list**: callers may inject custom RSS feeds.
  By default we use **Google News RSS** because it aggregates the
  Spanish-language outlets the user requested (Marca, Mundo Deportivo,
  ESPN Deportes, Yahoo Deportes) without us scraping each independently.

Usage
-----
::

    payload = await fetch_team_disruption_news("Costa Rica", db=db)
    if payload["available"]:
        for item in payload["items"]:
            print(item["title"], item["source_url"])
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from xml.etree import ElementTree as ET

import httpx

log = logging.getLogger("football_news_context_ingestion")

ENGINE_VERSION = "football_news_context_ingestion.v1"

_DEFAULT_TIMEOUT_SEC = 4.0
_CACHE_TTL_SECONDS   = 6 * 3600    # 6 hours
_MAX_ITEMS_PER_TEAM  = 25

# Locale knobs.
LOCALE_ES = "es"
LOCALE_EN = "en"

# ---------------------------------------------------------------------
# Keyword library (Spanish first — user requested).
# Each entry: ``(canonical_code, regex)``. Regex must be lower-case +
# unicode-safe; we lower() titles before matching.
# ---------------------------------------------------------------------
_KEYWORDS_ES: list[tuple[str, re.Pattern]] = [
    ("APARTADO_DE_CONCENTRACION",
     re.compile(r"apartad[oa]s?\s+de\s+la\s+concentraci[oó]n")),
    ("SEPARADO_POR_INDISCIPLINA",
     re.compile(r"(separad[oa]s?|separ[oó])\s+.*?por\s+indisciplina")),
    ("EXPULSADO_DE_CONVOCATORIA",
     re.compile(r"expulsad[oa]s?\s+de\s+(la\s+)?convocatoria")),
    ("BAJA_DISCIPLINARIA",
     re.compile(r"baja\s+disciplinaria")),
    ("PROBLEMAS_INTERNOS",
     re.compile(r"problemas?\s+internos|conflicto\s+interno|crisis\s+interna")),
    ("NO_CONTINUARA_CON_SELECCION",
     re.compile(r"no\s+continuar[aá]\s+(con\s+)?(la\s+)?selecci[oó]n")),
    ("FUERA_DE_SELECCION",
     re.compile(r"fuera\s+de\s+(la\s+)?selecci[oó]n")),
    ("SANCIONADO",
     re.compile(r"sancionad[oa]s?\s+(por|tras)")),
    ("EXCLUIDO",
     re.compile(r"exclu[ií]d[oa]s?\s+de\s+(la\s+)?(selecci[oó]n|convocatoria|concentraci[oó]n)")),
    ("BALACERA",
     re.compile(r"balacera|tiroteo|involucrad[oa]s?\s+en\s+un?\s+(bar|incidente|altercado)")),
]

_KEYWORDS_EN: list[tuple[str, re.Pattern]] = [
    ("REMOVED_FROM_SQUAD",
     re.compile(r"removed\s+from\s+(the\s+)?squad")),
    ("DROPPED_FROM_NATIONAL_TEAM",
     re.compile(r"dropped\s+from\s+the\s+national\s+team")),
    ("INTERNAL_CONFLICT",
     re.compile(r"internal\s+(conflict|dispute|row)")),
    ("DISCIPLINARY_ACTION",
     re.compile(r"disciplinary\s+(action|measure|reasons?)")),
    ("SENT_HOME",
     re.compile(r"sent\s+home\s+from\s+(camp|the\s+squad)")),
]

# Severity weights (per keyword code) used by the discovery engine.
KEYWORD_SEVERITY: dict[str, int] = {
    "APARTADO_DE_CONCENTRACION":     35,
    "SEPARADO_POR_INDISCIPLINA":     40,
    "EXPULSADO_DE_CONVOCATORIA":     35,
    "BAJA_DISCIPLINARIA":            30,
    "PROBLEMAS_INTERNOS":            25,
    "NO_CONTINUARA_CON_SELECCION":   30,
    "FUERA_DE_SELECCION":            25,
    "SANCIONADO":                    20,
    "EXCLUIDO":                      30,
    "BALACERA":                      40,
    "REMOVED_FROM_SQUAD":            30,
    "DROPPED_FROM_NATIONAL_TEAM":    30,
    "INTERNAL_CONFLICT":             25,
    "DISCIPLINARY_ACTION":           25,
    "SENT_HOME":                     35,
}


# ---------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------
def build_google_news_rss_url(
    team_name: str, locale: str = LOCALE_ES,
    extra_keywords: Optional[Iterable[str]] = None,
) -> str:
    """Build a Google News RSS URL for a team + disruption keywords.

    Google News aggregates Marca, Mundo Deportivo, ESPN Deportes, Yahoo
    Deportes (etc.) without us scraping each individually.
    """
    kw_es = ["indisciplina", "apartado", "separado", "convocatoria", "baja"]
    kw_en = ["removed", "dropped", "disciplinary"]
    kws   = kw_es if locale == LOCALE_ES else kw_en
    if extra_keywords:
        kws = list(kws) + list(extra_keywords)
    query = f'"{team_name}" ({" OR ".join(kws)})'
    q = urllib.parse.quote(query)
    if locale == LOCALE_ES:
        return f"https://news.google.com/rss/search?q={q}&hl=es&gl=US&ceid=US:es"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


DEFAULT_SOURCE_NAMES = (
    "Marca", "Mundo Deportivo", "ESPN Deportes",
    "Yahoo Deportes", "Fox Sports",
)


# ---------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------
_MEM_CACHE: dict[str, tuple[float, dict]] = {}
_MEM_CACHE_MAX = 500


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _mem_get(key: str) -> Optional[dict]:
    entry = _MEM_CACHE.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if _now_ts() >= expires_at:
        _MEM_CACHE.pop(key, None)
        return None
    return value


def _mem_put(key: str, value: dict, ttl: int) -> None:
    if len(_MEM_CACHE) >= _MEM_CACHE_MAX:
        try:
            oldest = min(_MEM_CACHE, key=lambda k: _MEM_CACHE[k][0])
            _MEM_CACHE.pop(oldest, None)
        except ValueError:
            return
    _MEM_CACHE[key] = (_now_ts() + ttl, value)


async def _cache_get(db: Any, key: str) -> Optional[dict]:
    if db is None:
        return _mem_get(key)
    try:
        doc = await db.football_news_cache.find_one({"_id": key})
        if not doc:
            return None
        expires_at = doc.get("expires_at")
        if isinstance(expires_at, (int, float)) and expires_at <= _now_ts():
            return None
        return doc.get("data")
    except Exception as exc:
        log.debug("football news cache_get failed for %s: %s", key, exc)
        return None


async def _cache_put(db: Any, key: str, data: dict,
                     ttl: int = _CACHE_TTL_SECONDS) -> None:
    _mem_put(key, data, ttl)
    if db is None:
        return
    try:
        await db.football_news_cache.update_one(
            {"_id": key},
            {"$set": {"data": data, "expires_at": _now_ts() + ttl}},
            upsert=True,
        )
    except Exception as exc:
        log.debug("football news cache_put failed for %s: %s", key, exc)


# ---------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------
def detect_keywords(
    text: str, locale: str = LOCALE_ES,
) -> list[str]:
    """Return list of canonical keyword codes that fired on the given
    text. Lower-cases the input before matching."""
    if not text:
        return []
    lowered = text.lower()
    library = _KEYWORDS_ES if locale == LOCALE_ES else _KEYWORDS_EN
    out: list[str] = []
    for code, pat in library:
        if pat.search(lowered):
            out.append(code)
    if locale == LOCALE_ES:
        # Always also scan English library when ES is primary — news
        # outlets sometimes reproduce English snippets in titles.
        for code, pat in _KEYWORDS_EN:
            if pat.search(lowered) and code not in out:
                out.append(code)
    return out


def _domain_from_url(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc
        return netloc.replace("www.", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------
# RSS parser (fail-soft)
# ---------------------------------------------------------------------
def _parse_rss(xml_text: str) -> list[dict]:
    """Parse a Google News RSS payload into ``[{title, link, source,
    pub_date}]``. Returns ``[]`` on any error."""
    items: list[dict] = []
    if not xml_text:
        return items
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    channel = root.find("channel")
    if channel is None:
        return items
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link") or "").strip()
        pub   = (item.findtext("pubDate") or "").strip()
        # <source url="...">Marca</source>
        src_el = item.find("source")
        src_name = (src_el.text or "").strip() if src_el is not None else ""
        src_url  = (src_el.get("url") or "").strip() if src_el is not None else ""
        if not src_name and link:
            src_name = _domain_from_url(link)
        if not title or not link:
            continue
        items.append({
            "title":       title,
            "link":        link,
            "source_name": src_name or "unknown",
            "source_url":  src_url or link,
            "pub_date":    pub,
        })
        if len(items) >= _MAX_ITEMS_PER_TEAM:
            break
    return items


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
async def fetch_team_disruption_news(
    team_name: str,
    *,
    db: Any = None,
    locale: str = LOCALE_ES,
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    rss_url: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    """Fetch + parse disruption-relevant news for a team.

    Returns ``{available, items, items_total, matched_items_total,
    queried_url, reason_codes}``. Always returns ``available: True``
    even when the items list is empty as long as the fetch succeeded
    — callers should rely on ``matched_items_total`` to decide whether
    a disruption signal exists.
    """
    if not team_name:
        return {
            "available": False, "reason": "no_team_name",
            "engine_version": ENGINE_VERSION,
            "items": [], "items_total": 0, "matched_items_total": 0,
        }

    key = f"footnews:{locale}:{team_name.lower()}"
    if use_cache:
        cached = await _cache_get(db, key)
        if cached is not None:
            return cached

    url = rss_url or build_google_news_rss_url(team_name, locale=locale)
    raw_items: list[dict] = []
    try:
        async with httpx.AsyncClient(
            timeout=timeout_sec, follow_redirects=True,
        ) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.0 Safari/605.1.15"
                    ),
                    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.9",
                    "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
                },
            )
        if resp.status_code >= 400 or not resp.text:
            log.debug("news rss http %s for %s", resp.status_code, team_name)
            payload = {
                "available": False,
                "reason": f"http_{resp.status_code}",
                "engine_version": ENGINE_VERSION,
                "queried_url": url,
                "items": [], "items_total": 0, "matched_items_total": 0,
            }
            if use_cache:
                await _cache_put(db, key, payload, ttl=300)   # short retry
            return payload
        raw_items = _parse_rss(resp.text)
    except (httpx.HTTPError, asyncio.TimeoutError, Exception) as exc:
        log.debug("news rss fetch failed for %s: %s", team_name, exc)
        payload = {
            "available":     False,
            "reason":         "fetch_failed",
            "engine_version": ENGINE_VERSION,
            "queried_url":    url,
            "error":          str(exc),
            "items":          [], "items_total": 0, "matched_items_total": 0,
        }
        if use_cache:
            await _cache_put(db, key, payload, ttl=300)
        return payload

    enriched_items = []
    matched = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    for it in raw_items:
        codes = detect_keywords(it.get("title", ""), locale=locale)
        item = {
            **it,
            "matched_phrases": codes,
            "fetched_at":      fetched_at,
            "locale":          locale,
            "affected_team":   team_name,
        }
        if codes:
            matched += 1
        enriched_items.append(item)

    payload = {
        "available":           True,
        "engine_version":       ENGINE_VERSION,
        "team":                 team_name,
        "locale":               locale,
        "queried_url":          url,
        "items":                enriched_items,
        "items_total":          len(enriched_items),
        "matched_items_total":  matched,
        "fetched_at":           fetched_at,
        "reason_codes":         ["NEWS_FETCH_OK"]
        + (["NEWS_HAS_MATCHES"] if matched > 0 else ["NEWS_NO_MATCHES"]),
    }
    if use_cache:
        await _cache_put(db, key, payload)
    return payload


async def fetch_news_for_match(
    home_team: str, away_team: str,
    *, db: Any = None, locale: str = LOCALE_ES,
) -> dict:
    """Convenience: fetch disruption news for both teams in a match,
    in parallel, fail-soft."""
    home_p, away_p = await asyncio.gather(
        fetch_team_disruption_news(home_team, db=db, locale=locale),
        fetch_team_disruption_news(away_team, db=db, locale=locale),
        return_exceptions=True,
    )
    home = home_p if isinstance(home_p, dict) else {
        "available": False, "items": [], "matched_items_total": 0,
    }
    away = away_p if isinstance(away_p, dict) else {
        "available": False, "items": [], "matched_items_total": 0,
    }
    return {
        "available":     home.get("available", False) or away.get("available", False),
        "engine_version": ENGINE_VERSION,
        "home":           home,
        "away":           away,
        "home_team":      home_team,
        "away_team":      away_team,
        "locale":         locale,
    }


__all__ = [
    "ENGINE_VERSION",
    "LOCALE_ES", "LOCALE_EN",
    "KEYWORD_SEVERITY",
    "DEFAULT_SOURCE_NAMES",
    "build_google_news_rss_url",
    "detect_keywords",
    "fetch_team_disruption_news",
    "fetch_news_for_match",
]
