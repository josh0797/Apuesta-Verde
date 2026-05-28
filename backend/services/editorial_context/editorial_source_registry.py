"""Editorial source registry.

Each entry describes a single source the Scrapy spider knows how to crawl.
Entries are intentionally declarative so a non-Python user can flip
`enabled`, tune `rate_limit_seconds` or add a new source without touching
spider code.

Fields
------
    name             — short stable identifier ('besoccer', 'sportytrader')
    base_url         — site root (no trailing slash)
    enabled          — feature flag; safe to flip off without code change
    sport            — 'football' for now (MVP scope)
    country / language
    priority         — 1 = highest; lower number scraped first
    rate_limit_seconds — Scrapy DOWNLOAD_DELAY per source
    requires_js      — if true, the spider should SKIP (Scrapy doesn't run JS).
                       Kept for forward compat with future Playwright sources.
    search_url       — template with {home} {away} {date_yyyymmdd} placeholders;
                       used by the spider's start_requests().
    selectors        — CSS / XPath fragments the parser knows how to use.
                       Each spider in editorial_spider_main.py decides which
                       selectors apply; the registry is just the source of truth.

Keep the registry SMALL (MVP = 2 sources). Adding more is a one-PR change.
"""
from __future__ import annotations

from typing import Any


SOURCES: list[dict[str, Any]] = [
    {
        "name":               "sportytrader_es",
        "base_url":           "https://www.sportytrader.es",
        "enabled":            True,
        "sport":              "football",
        "country":            "ES",
        "language":           "es",
        "priority":           1,
        "rate_limit_seconds": 2.0,
        "requires_js":        False,
        # Sportytrader uses /pronosticos-futbol/ as a hub. The spider hits the
        # main pronosticos index and follows links whose anchor text mentions
        # both teams; this avoids fragile slug guessing.
        "index_urls": [
            "https://www.sportytrader.es/pronosticos-futbol/",
        ],
        "selectors": {
            "preview_anchors":        "a[href*='/pronosticos/']",
            "title":                  "h1::text, h1 *::text",
            "published_at":           "time::attr(datetime), meta[property='article:published_time']::attr(content)",
            "body":                   "article, .content, .container .pronostico, main",
            "prediction":             ".prono-fact h2::text, .prediction h2::text",
            "suggested_market":       ".pari-recommande, .bet-recommendation, .recommendation",
            "suggested_odds":         ".odd, .cuota, .cote",
        },
    },
    {
        "name":               "besoccer_es",
        "base_url":           "https://es.besoccer.com",
        "enabled":            True,
        "sport":              "football",
        "country":            "ES",
        "language":           "es",
        "priority":           2,
        "rate_limit_seconds": 2.0,
        "requires_js":        False,
        # BeSoccer's preview/analysis articles live under /analisis/ and
        # /noticias/. We also accept their dedicated /match/{slug} preview.
        "index_urls": [
            "https://es.besoccer.com/analisis",
            "https://es.besoccer.com/noticias",
        ],
        "selectors": {
            "preview_anchors":        "a[href*='/analisis/'], a[href*='/match/'], a[href*='/noticia/']",
            "title":                  "h1::text, h1 *::text",
            "published_at":           "time::attr(datetime), meta[property='article:published_time']::attr(content)",
            "body":                   "article, .article-body, .news-body, main",
            "prediction":             ".prediction, .prono",
            "suggested_market":       ".tip, .recommended-bet",
            "suggested_odds":         ".odd, .cuota",
        },
    },
]


def enabled_sources(sport: str = "football") -> list[dict[str, Any]]:
    """Return enabled sources for the given sport, ordered by priority asc."""
    sport = (sport or "football").lower()
    src = [s for s in SOURCES if s.get("enabled") and s.get("sport") == sport]
    src.sort(key=lambda s: s.get("priority", 99))
    return src


__all__ = ["SOURCES", "enabled_sources"]
