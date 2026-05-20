"""Optional team-news / injury / scandal scrapers.

Sources covered (each behind its own feature flag):
    rotowire.com         — fast NFL/NBA/MLB style injury beats (limited soccer)
    sportsgambler.com    — football-focused team news / probable lineups
    promiedos.com.ar     — Latin America focused (Argentine + South American
                           football + Liga MX coverage)

Design notes:
    These scrapers ADD latency. They are OPT-IN behind env flags so they
    never run by default. The scraper used today (api_football + ESPN) is
    already enriched enough for the LLM pipeline; these sources are for
    edge cases where the user/operator wants extra signal on Tier-1 matches.

    A per-process in-memory TTL cache prevents hitting the same page twice
    in the same analysis run (set conservative: 30 min).

Public API:
    INJURY_SOURCES_ENABLED — bool
    fetch_team_news(home_team, away_team, competition, *, sources=None,
                    timeout=15) -> dict with per-source raw text snippets
        Returns shape:
            {
              "home": {"rotowire": [...], "sportsgambler": [...], "promiedos": [...]},
              "away": {...},
              "sources_attempted": [...],
              "sources_skipped": [...],
              "errors": {source: "msg"}
            }

    All scrapers fail SOFT: any source raising an exception is captured under
    `errors` and the caller still gets a partial dict.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import unicodedata
from typing import Iterable, Optional

import httpx

log = logging.getLogger("injury_sources")

INJURY_SOURCES_ENABLED = os.environ.get("INJURY_SOURCES_ENABLED", "false").lower() in ("1", "true", "yes")
INJURY_SOURCES_TTL_SEC = int(os.environ.get("INJURY_SOURCES_TTL_SEC", "1800"))

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9,es;q=0.8"}

# Process-local TTL cache: {key: (expires_ts, value)}
_CACHE: dict[str, tuple[float, dict]] = {}


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s


def _cache_get(key: str) -> Optional[dict]:
    hit = _CACHE.get(key)
    if not hit:
        return None
    expires, value = hit
    if expires < time.time():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: dict) -> None:
    _CACHE[key] = (time.time() + INJURY_SOURCES_TTL_SEC, value)


def _strip_tags(html: str, max_chars: int = 1200) -> list[str]:
    """Minimal HTML→text extraction. Returns up to ~6 short snippets."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>",   " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>",                   " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Split on common sentence boundaries and keep short, news-like fragments.
    parts = re.split(r"(?<=[.!?])\s+", text[:max_chars * 6])
    snippets = [p.strip() for p in parts if 25 <= len(p.strip()) <= 280][:6]
    return snippets


async def _fetch_html(client: httpx.AsyncClient, url: str, timeout: int) -> Optional[str]:
    try:
        resp = await client.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if 200 <= resp.status_code < 300 and resp.text:
            return resp.text
        log.info("injury_sources: %s -> HTTP %s", url, resp.status_code)
        return None
    except Exception as exc:
        log.info("injury_sources: GET %s failed: %s", url, exc)
        return None


# ── Per-source plug-ins ─────────────────────────────────────────────────────
# Each is a coroutine that returns a list[str] of news snippets for the team.
# All of them are best-effort: empty list on miss, exception is caught upstream.
async def _fetch_rotowire(client: httpx.AsyncClient, team: str, timeout: int) -> list[str]:
    # Rotowire's soccer coverage is limited; search endpoint is a decent fallback.
    url = f"https://www.rotowire.com/search.php?searchterm={team.replace(' ', '+')}&sport=soccer"
    html = await _fetch_html(client, url, timeout)
    return _strip_tags(html or "")


async def _fetch_sportsgambler(client: httpx.AsyncClient, team: str, timeout: int) -> list[str]:
    slug = _slug(team)
    url = f"https://www.sportsgambler.com/teams/football/{slug}/"
    html = await _fetch_html(client, url, timeout)
    return _strip_tags(html or "")


async def _fetch_promiedos(client: httpx.AsyncClient, team: str, timeout: int) -> list[str]:
    slug = _slug(team)
    # Promiedos uses team slug pages
    url = f"https://www.promiedos.com.ar/club/{slug}"
    html = await _fetch_html(client, url, timeout)
    return _strip_tags(html or "")


SOURCES = {
    "rotowire":      _fetch_rotowire,
    "sportsgambler": _fetch_sportsgambler,
    "promiedos":     _fetch_promiedos,
}


async def fetch_team_news(
    home_team: str,
    away_team: str,
    competition: str,
    *,
    sources: Optional[Iterable[str]] = None,
    timeout: int = 15,
) -> dict:
    """Pull injury / team news snippets for both sides from selected sources.

    When the global feature flag is off, returns an empty placeholder dict so
    callers can no-op cleanly.
    """
    if not INJURY_SOURCES_ENABLED:
        return {
            "home": {}, "away": {},
            "sources_attempted": [],
            "sources_skipped": list(SOURCES.keys()),
            "errors": {},
            "_disabled": True,
        }

    src_keys = [s for s in (sources or SOURCES.keys()) if s in SOURCES]
    cache_key = f"{_slug(home_team)}__{_slug(away_team)}__{_slug(competition)}__{','.join(sorted(src_keys))}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "_cached": True}

    out = {
        "home": {}, "away": {},
        "sources_attempted": src_keys,
        "sources_skipped": [],
        "errors": {},
    }
    async with httpx.AsyncClient() as client:
        async def _one(side: str, team: str, src: str):
            try:
                snippets = await SOURCES[src](client, team, timeout)
                out[side][src] = snippets
            except Exception as exc:
                out["errors"][f"{src}:{side}"] = str(exc)[:160]

        tasks = []
        for src in src_keys:
            tasks.append(_one("home", home_team, src))
            tasks.append(_one("away", away_team, src))
        await asyncio.gather(*tasks)

    _cache_set(cache_key, out)
    return out
