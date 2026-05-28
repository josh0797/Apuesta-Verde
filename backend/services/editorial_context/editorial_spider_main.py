"""Standalone Scrapy entry point used by `scrapy_runner.py`.

Designed to be invoked as a SUBPROCESS so the Twisted reactor never collides
with the FastAPI asyncio loop. Usage:

    python -m services.editorial_context.editorial_spider_main \\
        --input  /tmp/edctx_in.json \\
        --output /tmp/edctx_out.json

Input JSON shape:
    {
        "matches": [
            {
                "sport":       "football",
                "home":        "Alavés",
                "away":        "Rayo Vallecano",
                "league":      "La Liga",
                "kickoff_iso": "2026-05-22T19:00:00Z",
            },
            ...
        ],
        "sources":   [   # registry entries to use (we don't import to keep
                         # the subprocess startup tiny).
          {
              "name": "sportytrader_es",
              "index_urls": [...],
              "selectors":  {...},
              "rate_limit_seconds": 2.0,
              ...
          },
          ...
        ],
        "timeout_sec": 25,
        "user_agent": "...",
    }

Output JSON shape: list[raw_dict] consumable by
`editorial_normalizer.build_editorial_context_signal`.

IMPORTANT:
    The spider does its best with shallow scraping (one index page + N
    article pages, no JS). Selectors live in the source registry so a
    non-coder can tune them. When a source returns nothing for a given
    match, we just emit no item — fail-soft, no exceptions thrown.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
)
log = logging.getLogger("editorial_spider")


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(c)
    )


def _slug(s: str) -> str:
    s = _strip_accents((s or "").lower())
    return re.sub(r"[^a-z0-9]+", "", s)


def _team_keywords(name: str) -> list[str]:
    """Return loose substrings that should appear in an article anchor for a
    given team. We strip the obvious suffixes so 'Atlético de Madrid' →
    'atletico', 'madrid'.
    """
    if not name:
        return []
    raw = _strip_accents(name.lower())
    raw = re.sub(
        r"\b(f\.?c\.?|c\.?f\.?|a\.?c\.?|s\.?c\.?|cd|ad|club|deportivo|de)\b",
        " ",
        raw,
    )
    tokens = [t for t in re.split(r"\s+", raw) if len(t) >= 3]
    return tokens[:3]


def _article_matches_pair(text: str, home: str, away: str) -> bool:
    """True iff `text` contains at least one keyword from BOTH teams."""
    if not text:
        return False
    norm = _strip_accents(text.lower())
    h_kws = _team_keywords(home)
    a_kws = _team_keywords(away)
    if not h_kws or not a_kws:
        return False
    h_hit = any(k in norm for k in h_kws)
    a_hit = any(k in norm for k in a_kws)
    return h_hit and a_hit


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Spider ----------------------------------------------------------------

try:
    import scrapy
    from scrapy.crawler import CrawlerProcess
    from scrapy.linkextractors import LinkExtractor
except Exception as exc:                                  # pragma: no cover
    log.error("Scrapy import failed: %s", exc)
    raise SystemExit(0)


class EditorialSpider(scrapy.Spider):
    name = "editorial"
    custom_settings: dict[str, Any] = {
        "USER_AGENT":              os.environ.get(
            "EDITORIAL_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        ),
        "ROBOTSTXT_OBEY":          False,
        "DOWNLOAD_TIMEOUT":        12,
        "DOWNLOAD_DELAY":          1.0,
        "CONCURRENT_REQUESTS":     4,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "AUTOTHROTTLE_ENABLED":    True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.5,
        "LOG_LEVEL":               "WARNING",
        "COOKIES_ENABLED":         False,
        "REDIRECT_MAX_TIMES":      3,
        "HTTPERROR_ALLOWED_CODES": [404],
        "DEFAULT_REQUEST_HEADERS": {
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
            "Accept":          "text/html,application/xhtml+xml",
        },
    }

    def __init__(self, *, payload: dict, output_path: str, **kwargs):
        super().__init__(**kwargs)
        self._payload    = payload or {}
        self._output     = output_path
        self._items: list[dict] = []
        self._started_at = time.time()
        # Index of matches we're searching for, by lower-cased keyword pair.
        self._matches    = self._payload.get("matches") or []
        self._sources    = self._payload.get("sources") or []
        self._timeout    = float(self._payload.get("timeout_sec") or 25.0)
        self._visited:   set[str] = set()

    # ── entry point ─────────────────────────────────────────────
    def start_requests(self) -> Iterable[scrapy.Request]:
        for src in self._sources:
            if not src.get("enabled"):
                continue
            urls = src.get("index_urls") or []
            for url in urls:
                yield scrapy.Request(
                    url,
                    callback=self.parse_index,
                    cb_kwargs={"source": src},
                    dont_filter=True,
                    meta={"source_name": src.get("name")},
                )

    # ── index page ─────────────────────────────────────────────
    def parse_index(self, response, source: dict):
        try:
            sel = (source.get("selectors") or {}).get("preview_anchors") or "a"
            extractor = LinkExtractor(restrict_css=sel, allow_domains=[
                response.url.split("//")[1].split("/")[0]
            ])
            links = extractor.extract_links(response)
        except Exception as exc:                              # pragma: no cover
            log.warning("[SCRAPY_EDITORIAL_SOURCE_FAILED] index parse %s: %s",
                        response.url, exc)
            return

        wanted: list[tuple[str, dict]] = []
        for link in links:
            anchor = (link.text or "") + " " + link.url
            for m in self._matches:
                if _article_matches_pair(anchor, m.get("home", ""), m.get("away", "")):
                    if link.url not in self._visited:
                        self._visited.add(link.url)
                        wanted.append((link.url, m))
                        if len(wanted) >= 20:
                            break
        if not wanted:
            log.info("[SCRAPY_EDITORIAL_SOURCE_OK] %s: no matching anchors on %s",
                     source.get("name"), response.url)
            return

        for url, match_info in wanted:
            if time.time() - self._started_at > self._timeout:
                log.warning("[SCRAPY_EDITORIAL_SOURCE_FAILED] timeout reached, stopping")
                return
            yield scrapy.Request(
                url,
                callback=self.parse_article,
                cb_kwargs={"source": source, "match_info": match_info},
                dont_filter=True,
            )

    # ── article page ──────────────────────────────────────────────
    def parse_article(self, response, source: dict, match_info: dict):
        try:
            sels = source.get("selectors") or {}
            title = self._first_text(response, sels.get("title"))
            pub   = self._first_text(response, sels.get("published_at"))
            body  = self._text_block(response, sels.get("body"))
            if not body:
                # Fallback: pull all <p> text
                body = " ".join(response.css("p::text").getall())
            if not title and not body:
                log.info("[SCRAPY_EDITORIAL_SOURCE_OK] empty article %s", response.url)
                return

            full_text = f"{title}\n\n{body}"
            if not _article_matches_pair(full_text, match_info.get("home", ""), match_info.get("away", "")):
                # Drop: anchor matched but article body doesn't. Avoids false-positives.
                return

            item = {
                "source":         source.get("name"),
                "source_url":     response.url,
                "published_at":   pub or None,
                "language":       source.get("language") or "es",
                "title":          (title or "").strip(),
                "raw_text":       body[:8000],
                "scraped_at":     _now_iso(),
                "_match_payload": match_info,
            }
            self._items.append(item)
            log.info("[SCRAPY_EDITORIAL_SOURCE_OK] %s captured %s",
                     source.get("name"), response.url)
        except Exception as exc:
            log.warning("[SCRAPY_EDITORIAL_SOURCE_FAILED] parse_article %s: %s",
                        response.url, exc)

    def closed(self, reason: str) -> None:
        """Persist whatever we have to disk — even on errors / timeouts."""
        try:
            with open(self._output, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False)
            log.info("[SCRAPY_EDITORIAL_DONE] reason=%s items=%d output=%s",
                     reason, len(self._items), self._output)
        except Exception as exc:
            log.error("failed to write output: %s", exc)

    # ── helpers ───────────────────────────────────────────────────
    @staticmethod
    def _first_text(response, selector: str | None) -> str:
        if not selector:
            return ""
        for sel in selector.split(","):
            sel = sel.strip()
            if not sel:
                continue
            try:
                v = response.css(sel).get()
                if v:
                    return v.strip()
            except Exception:
                continue
        return ""

    @staticmethod
    def _text_block(response, selector: str | None) -> str:
        if not selector:
            return ""
        for sel in selector.split(","):
            sel = sel.strip()
            if not sel:
                continue
            try:
                parts = response.css(f"{sel} ::text").getall()
                if parts:
                    txt = " ".join(p.strip() for p in parts if p and p.strip())
                    if len(txt) >= 200:
                        return txt
            except Exception:
                continue
        return ""


def _run(input_path: str, output_path: str) -> int:
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        log.error("failed to read input: %s", exc)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        return 1

    timeout = float(payload.get("timeout_sec") or 25.0)
    process = CrawlerProcess(settings={
        "USER_AGENT":              payload.get("user_agent") or EditorialSpider.custom_settings["USER_AGENT"],
        "ROBOTSTXT_OBEY":          False,
        "LOG_LEVEL":               "WARNING",
        "CLOSESPIDER_TIMEOUT":     int(timeout),
        "REACTOR_THREADPOOL_MAXSIZE": 4,
    })
    process.crawl(EditorialSpider, payload=payload, output_path=output_path)
    process.start()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",  required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    sys.exit(_run(args.input, args.output))
