"""Editorial source registry — declarative configuration.

Each entry tells the Scrapy spider HOW to crawl a source:
    name             — stable identifier ('as_com', 'besoccer_es')
    base_url         — root URL (no trailing slash)
    enabled          — feature flag (flip without code change)
    requires_js      — true → spider skips entry (kept for future Playwright)
    sport / country / language
    priority         — 1 = scraped first; lower number wins on duplicates
    rate_limit_seconds — Scrapy DOWNLOAD_DELAY per source
    index_urls       — list of pages the spider hits to find preview anchors
    article_url_patterns — substrings that must appear in candidate URLs
                       (filters out unrelated news links on news-heavy sites)
    selectors        — CSS selectors used by editorial_spider_main.py:
        preview_anchors   → restrict_css for LinkExtractor on index pages
        title             → article h1
        published_at      → datetime attribute / meta
        body              → main article container
        prediction        → optional: structured prediction CSS
        suggested_market  → optional: market suggestion CSS
        suggested_odds    → optional: odds value CSS

Selectors were tuned against real HTML on 2026-05-28. They are intentionally
loose (multiple comma-separated alternatives) so that minor HTML changes don't
silently break scraping. Add more selectors here, NOT in the spider code.
"""
from __future__ import annotations

from typing import Any


SOURCES: list[dict[str, Any]] = [
    # ────────────────────────────────────────────────────────────────────────
    # 1) AS.com — best Spanish source for full match previews + market suggestion
    #    Verified HTML 2026-05-28:
    #      • Preview URLs follow `/apuestas/pronosticos/{home}-vs-{away}-pronostico-{date}/`
    #      • Index page lists matches as `.match a` and `.highlight-of-the-day .cta-5`
    #      • Articles have h1 + `.page__content` with prediction + odds
    # ────────────────────────────────────────────────────────────────────────
    {
        "name":               "as_com",
        "base_url":           "https://as.com",
        "enabled":            True,
        "requires_js":        False,
        "sport":              "football",
        "country":            "ES",
        "language":           "es",
        "priority":           1,
        "rate_limit_seconds": 1.5,
        "index_urls": [
            "https://as.com/apuestas/pronosticos/",
            "https://as.com/apuestas/pronosticos/manana/",
        ],
        "article_url_patterns": [
            "/apuestas/pronosticos/",
            "-pronostico-",
        ],
        "selectors": {
            "preview_anchors": (
                "a.cta-5, "
                ".match a[href*='/apuestas/pronosticos/'], "
                ".highlight-of-the-day a[href*='/apuestas/pronosticos/'], "
                "a[href*='-pronostico-']"
            ),
            "title":              "h1::text, h1 *::text",
            "published_at":       "time::attr(datetime), meta[property='article:published_time']::attr(content)",
            "body":               ".page__content, article .entry-content, article",
            "prediction":         "h2.wp-block-heading, .prediction",
            "suggested_market":   ".cta_wrapper .cta, .wp-block-e2-cta-external .cta",
            "suggested_odds":     ".cta_wrapper .cta",  # AS embeds odds inline: "cuota 1.70"
        },
    },

    # ────────────────────────────────────────────────────────────────────────
    # 2) Sportytrader ES — dedicated to football predictions
    #    Loose selectors so we don't break on minor template tweaks
    # ────────────────────────────────────────────────────────────────────────
    {
        "name":               "sportytrader_es",
        "base_url":           "https://www.sportytrader.es",
        "enabled":            True,
        "requires_js":        False,
        "sport":              "football",
        "country":            "ES",
        "language":           "es",
        "priority":           2,
        "rate_limit_seconds": 2.5,
        "index_urls": [
            "https://www.sportytrader.es/pronosticos-futbol/",
            "https://www.sportytrader.es/pronosticos/futbol/",
        ],
        "article_url_patterns": [
            "/pronostico",
            "/futbol/",
        ],
        "selectors": {
            "preview_anchors": (
                "a[href*='/pronostico'], "
                "a[href*='/pronosticos/futbol/'], "
                "a[href*='/pronosticos-futbol/']"
            ),
            "title":              "h1::text, h1 *::text, .article-title::text",
            "published_at":       "time::attr(datetime), meta[property='article:published_time']::attr(content), .article-date::text",
            "body":               "article, .article-content, .pronostico-content, main .container, main",
            "prediction":         ".prono-fact h2::text, .prediction h2::text, .prono h3::text",
            "suggested_market":   ".pari-recommande, .bet-recommendation, .recommendation, .prono-recommended",
            "suggested_odds":     ".odd, .cuota, .cote, span.odds",
        },
    },

    # ────────────────────────────────────────────────────────────────────────
    # 3) BeSoccer ES — analysis + match preview articles
    #    Articles live under /analisis/, /noticias/ and /match/{slug}
    # ────────────────────────────────────────────────────────────────────────
    {
        "name":               "besoccer_es",
        "base_url":           "https://es.besoccer.com",
        "enabled":            True,
        "requires_js":        False,
        "sport":              "football",
        "country":            "ES",
        "language":           "es",
        "priority":           3,
        "rate_limit_seconds": 2.5,
        "index_urls": [
            "https://es.besoccer.com/analisis",
            "https://es.besoccer.com/noticias",
            "https://es.besoccer.com/previa",
        ],
        "article_url_patterns": [
            "/analisis/",
            "/noticia/",
            "/match/",
            "/previa/",
        ],
        "selectors": {
            "preview_anchors": (
                "a[href*='/analisis/'], "
                "a[href*='/noticia/'], "
                "a[href*='/match/'], "
                "a[href*='/previa/']"
            ),
            "title":              "h1::text, h1 *::text, .article-title::text",
            "published_at":       "time::attr(datetime), meta[property='article:published_time']::attr(content)",
            "body":               "article, .article-body, .news-body, .content-body, main",
            "prediction":         ".prediction, .prono, .tipster",
            "suggested_market":   ".tip, .recommended-bet, .pronostico",
            "suggested_odds":     ".odd, .cuota, .quote",
        },
    },

    # ────────────────────────────────────────────────────────────────────────
    # 4) Marca.com (Unidad Editorial) — news + alineaciones probables
    #    Heavier on news than dedicated predictions, but valuable for:
    #      • injury reports
    #      • motivation / context
    #      • likely lineups ("alineaciones-probables-...")
    # ────────────────────────────────────────────────────────────────────────
    {
        "name":               "marca_com",
        "base_url":           "https://www.marca.com",
        "enabled":            True,
        "requires_js":        False,
        "sport":              "football",
        "country":            "ES",
        "language":           "es",
        "priority":           4,
        "rate_limit_seconds": 2.0,
        "index_urls": [
            "https://www.marca.com/futbol.html",
            "https://www.marca.com/futbol/primera-division.html",
            "https://www.marca.com/futbol/champions-league.html",
            "https://www.marca.com/futbol/europa-league.html",
            "https://www.marca.com/futbol/premier-league.html",
        ],
        # Marca uses long topic-based URLs. We filter for things that look like
        # match-related editorial pieces (alineaciones, prevía, crónica, análisis).
        "article_url_patterns": [
            "/futbol/",
            "alineaciones-probables",
            "previa",
            "cronica",
            "analisis",
        ],
        "selectors": {
            "preview_anchors": (
                "a.ue-c-cover-content__link-whole-content[href*='/futbol/'], "
                "a.ue-c-widget-news__link[href*='/futbol/'], "
                "a[href*='alineaciones-probables-'], "
                "a[href*='/futbol/'][href*='/cronica/']"
            ),
            "title":              "h1::text, h1 *::text, .ue-c-article__headline::text",
            "published_at":       "time::attr(datetime), meta[property='article:published_time']::attr(content)",
            "body": (
                ".ue-c-article__body, "
                ".ue-c-article-body, "
                "article .ue-l-article__body, "
                "article"
            ),
            "prediction":         ".ue-c-article__subheadline, h2",
            "suggested_market":   None,    # Marca rarely surfaces explicit markets
            "suggested_odds":     None,
        },
    },

    # ────────────────────────────────────────────────────────────────────────
    # 5) scores24.live — DISABLED (SPA / requires JavaScript)
    #    The site is a fully client-side rendered React SPA (styled-components
    #    with hashed class names like `sc-1xsn000-3`). Scrapy alone can't
    #    extract content. Kept here as a registry placeholder so that when a
    #    Playwright-based fetcher is added later, only `requires_js: false`
    #    and the selectors need updating.
    # ────────────────────────────────────────────────────────────────────────
    {
        "name":               "scores24_live",
        "base_url":           "https://scores24.live",
        "enabled":            False,         # ← flip to true once JS rendering is wired up
        "requires_js":        True,
        "sport":              "football",
        "country":            "Multi",
        "language":           "es",
        "priority":           9,
        "rate_limit_seconds": 3.0,
        "index_urls": [
            "https://scores24.live/es/soccer",
            "https://scores24.live/es/soccer/tomorrow",
        ],
        "article_url_patterns": [
            "/es/soccer/m-",
            "/predictions",
        ],
        "selectors": {
            "preview_anchors":  "a[href*='/soccer/m-']",
            "title":            "h1::text",
            "published_at":     "time::attr(datetime), meta[property='article:published_time']::attr(content)",
            "body":             "main, .match-content",
            "prediction":       None,
            "suggested_market": None,
            "suggested_odds":   None,
        },
    },
]


def enabled_sources(sport: str = "football") -> list[dict[str, Any]]:
    """Return enabled sources for the given sport, ordered by priority asc.

    Sources flagged `requires_js: True` are skipped here even if `enabled`,
    because the current Scrapy spider does not run JavaScript. Once a
    Playwright integration is added, this guard can be relaxed.
    """
    sport_lower = (sport or "football").lower()
    src = [
        s for s in SOURCES
        if s.get("enabled")
        and s.get("sport") == sport_lower
        and not s.get("requires_js")
    ]
    src.sort(key=lambda s: s.get("priority", 99))
    return src


__all__ = ["SOURCES", "enabled_sources"]
