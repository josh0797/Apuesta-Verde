"""Playwright-based fetcher for JS-rendered editorial sources (P4).

The Scrapy spider (`editorial_spider_main.py`) handles server-rendered HTML.
This module handles the OTHER case: sources whose preview indexes and/or
articles are rendered client-side by JavaScript (React/Vue/Svelte SPAs).

Design
------
  * Runs in a SUBPROCESS like Scrapy (`playwright_runner.py`) so the asyncio
    + chromium reactor never collides with FastAPI's event loop.
  * Reuses the SAME `EditorialContextSignal` output shape so the downstream
    normalizer doesn't need to know which backend produced the data.
  * Stealth defaults: hides `navigator.webdriver`, sets realistic UA + locale,
    blocks heavy assets (images/fonts/media) to keep latency under control.
  * Strict fail-soft: any per-source failure is logged and skipped — never
    raised — so analyst_engine can keep going with whatever signals it has.
  * Honours Cloudflare-style interstitials by waiting up to N seconds for the
    challenge title ("Un momento...") to clear; if it doesn't, the source is
    marked as blocked and the fetcher moves on (proxies needed).

Note on scores24.live (and any Cloudflare-protected source)
-----------------------------------------------------------
Cloudflare's Bot Fight Mode aggressively blocks requests originating from
datacenter IP ranges (which is the case in our container). The challenge
does NOT auto-resolve from a plain headless browser. To activate such
sources, a **residential proxy** must be configured via the env var
`PLAYWRIGHT_PROXY` (`http://user:pass@host:port`). Without it, the fetcher
logs `[PLAYWRIGHT_EDITORIAL_BLOCKED]` and returns no items — the rest of
the editorial pipeline keeps running normally.

Public API
----------
    await fetch_with_playwright(matches, sources, *, timeout_sec=30) -> list[dict]

Returns the SAME raw-item shape as the Scrapy runner:
    {
        "source":         str,
        "source_url":     str,
        "published_at":   str|None,
        "language":       str,
        "title":          str,
        "raw_text":       str,
        "scraped_at":     str (iso),
        "_match_payload": dict   (the input match this item belongs to)
    }
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

log = logging.getLogger("editorial.playwright_fetcher")

# Hard limits to protect the pipeline
_MAX_ARTICLES_PER_SOURCE = 12
_INDEX_NAV_TIMEOUT_MS    = 30_000
_ARTICLE_NAV_TIMEOUT_MS  = 20_000
_CHALLENGE_MAX_WAIT_SEC  = 12
_BLOCKED_TITLE_HINTS = (
    "un momento",
    "just a moment",
    "checking your browser",
    "client challenge",          # PerimeterX / Akamai (BeSoccer)
    "access denied",
    "attention required",        # Cloudflare classic block
    "verifying you are human",   # Cloudflare Turnstile
)

# Block heavy assets to keep memory + latency reasonable in subprocess.
_ABORT_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins',  { get: () => [1,2,3,4,5] });
Object.defineProperty(navigator, 'languages',{ get: () => ['es-ES','es','en-US','en'] });
window.chrome = { runtime: {} };
"""


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(c)
    )


def _team_keywords(name: Optional[str]) -> list[str]:
    if not name:
        return []
    raw = _strip_accents(name.lower())
    raw = re.sub(
        r"\b(f\.?c\.?|c\.?f\.?|a\.?c\.?|s\.?c\.?|cd|ad|club|deportivo|de)\b",
        " ",
        raw,
    )
    return [t for t in re.split(r"\s+", raw) if len(t) >= 3][:3]


def _article_matches_pair(text: str, home: Optional[str], away: Optional[str]) -> bool:
    if not text:
        return False
    norm = _strip_accents(text.lower())
    h = _team_keywords(home)
    a = _team_keywords(away)
    if not h or not a:
        return False
    return any(k in norm for k in h) and any(k in norm for k in a)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _wait_for_challenge_to_clear(page, *, max_wait_sec: int = _CHALLENGE_MAX_WAIT_SEC) -> bool:
    """Poll the page title for Cloudflare-style challenge hints. Returns True
    if the challenge clears; False if we timed out (blocked).
    """
    waited = 0.0
    while waited < max_wait_sec:
        try:
            title = (await page.title()) or ""
        except Exception:
            title = ""
        if not any(h in title.lower() for h in _BLOCKED_TITLE_HINTS):
            return True
        await page.wait_for_timeout(1500)
        waited += 1.5
    return False


async def _extract_first_text(page, selector: Optional[str]) -> str:
    if not selector:
        return ""
    for sel in selector.split(","):
        sel = sel.strip()
        if not sel:
            continue
        try:
            v = await page.locator(sel).first.text_content(timeout=2000)
            if v and v.strip():
                return v.strip()
        except Exception:
            continue
    return ""


async def _extract_attribute(page, css_attr: Optional[str]) -> str:
    """Selector format: `cssSelector::attr(attrName)` or `meta[..]::attr(content)`."""
    if not css_attr:
        return ""
    for sel in css_attr.split(","):
        sel = sel.strip()
        if not sel:
            continue
        m = re.match(r"^(.*)::attr\((\w+)\)$", sel)
        if m:
            css, attr = m.group(1), m.group(2)
            try:
                v = await page.locator(css).first.get_attribute(attr, timeout=2000)
                if v and v.strip():
                    return v.strip()
            except Exception:
                continue
    return ""


async def _extract_body(page, selector: Optional[str]) -> str:
    if not selector:
        try:
            v = await page.evaluate("() => (document.body.innerText || '')")
            return (v or "").strip()
        except Exception:
            return ""
    for sel in selector.split(","):
        sel = sel.strip()
        if not sel:
            continue
        try:
            v = await page.locator(sel).first.inner_text(timeout=2500)
            if v and len(v.strip()) >= 200:
                return re.sub(r"\s+", " ", v.strip())
        except Exception:
            continue
    # Last-resort: full page text
    try:
        v = await page.evaluate("() => (document.body.innerText || '')")
        return re.sub(r"\s+", " ", (v or "").strip())
    except Exception:
        return ""


async def _harvest_anchors(page, css: str) -> list[tuple[str, str]]:
    """Return [(href, text), ...] from anchors matching `css`."""
    try:
        items = await page.evaluate(
            """(css) => {
                const all = Array.from(document.querySelectorAll(css));
                return all.slice(0, 200).map(a => [a.href || a.getAttribute('href') || '', (a.innerText || a.textContent || '').replace(/\\s+/g,' ').slice(0, 200)]);
            }""",
            css,
        )
        return [tuple(x) for x in (items or []) if x[0]]
    except Exception as exc:
        log.debug("_harvest_anchors failed (%s): %s", css, exc)
        return []


async def _new_context(p, *, user_agent: str, proxy: Optional[dict]) -> tuple:
    """Spin up a fresh Chromium + context with stealth init script.

    Returns (browser, context). Caller MUST close both.
    """
    launch_args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-features=IsolateOrigins,site-per-process",
    ]
    launch_kwargs: dict[str, Any] = {"headless": True, "args": launch_args}
    if proxy:
        launch_kwargs["proxy"] = proxy
    browser = await p.chromium.launch(**launch_kwargs)
    ctx = await browser.new_context(
        user_agent=user_agent,
        locale="es-ES",
        viewport={"width": 1920, "height": 1080},
        extra_http_headers={
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    await ctx.add_init_script(_STEALTH_INIT_SCRIPT)
    return browser, ctx


async def _fetch_source(
    p,
    source: dict,
    matches: list[dict],
    *,
    timeout_sec: float,
    user_agent: str,
    proxy: Optional[dict],
) -> list[dict]:
    """Crawl ONE JS-rendered source and return matching items.

    Args:
        p          — the playwright API root (`async_playwright()` context).
        source     — registry entry for the source.
        matches    — list of {sport, home, away, kickoff_iso, match_id, ...}.
        timeout_sec— wall-clock budget for THIS source.
        user_agent — UA string for the browser context.
        proxy      — optional playwright proxy dict ({server, username, password}).

    Returns:
        list of raw items.
    """
    name = source.get("name") or "unknown"
    started = time.time()
    items: list[dict] = []
    selectors = source.get("selectors") or {}
    anchor_css = selectors.get("preview_anchors") or "a"
    url_patterns = [p_.lower() for p_ in (source.get("article_url_patterns") or [])]
    url_excludes = [p_.lower() for p_ in (source.get("article_url_exclude_patterns") or [])]

    browser = None
    try:
        browser, ctx = await _new_context(p, user_agent=user_agent, proxy=proxy)
        page = await ctx.new_page()
        # Asset blocker
        async def _route_handler(route):
            try:
                if route.request.resource_type in _ABORT_RESOURCE_TYPES:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass
        await ctx.route("**/*", _route_handler)

        for index_url in (source.get("index_urls") or []):
            if time.time() - started >= timeout_sec:
                log.info("[PLAYWRIGHT_EDITORIAL_TIMEOUT] %s: stopped, budget hit", name)
                break
            try:
                await page.goto(index_url, wait_until="domcontentloaded", timeout=_INDEX_NAV_TIMEOUT_MS)
            except Exception as exc:
                log.warning("[PLAYWRIGHT_EDITORIAL_SOURCE_FAILED] %s: index goto failed: %s", name, exc)
                continue
            if not await _wait_for_challenge_to_clear(page):
                log.warning("[PLAYWRIGHT_EDITORIAL_BLOCKED] %s: Cloudflare/anti-bot challenge did not clear (residential proxy required)", name)
                continue

            # Some SPAs need a bit more time after DOMContentLoaded
            await page.wait_for_timeout(2500)
            anchors = await _harvest_anchors(page, anchor_css)
            # Filter anchors by url_patterns + match pair
            chosen: list[tuple[str, dict]] = []
            for href, text in anchors:
                if not href.startswith("http"):
                    # Relative URL: prepend base_url
                    base = source.get("base_url") or ""
                    href = base.rstrip("/") + ("/" + href.lstrip("/"))
                if url_patterns:
                    if not any(p_ in href.lower() for p_ in url_patterns):
                        continue
                if url_excludes and any(p_ in href.lower() for p_ in url_excludes):
                    continue
                ctx_text = href + " " + (text or "")
                for m in matches:
                    if _article_matches_pair(ctx_text, m.get("home"), m.get("away")):
                        chosen.append((href, m))
                        break
                if len(chosen) >= _MAX_ARTICLES_PER_SOURCE:
                    break

            if not chosen:
                log.info("[PLAYWRIGHT_EDITORIAL_SOURCE_OK] %s: no matching anchors on %s", name, index_url)
                continue

            for href, match_info in chosen:
                if time.time() - started >= timeout_sec:
                    break
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=_ARTICLE_NAV_TIMEOUT_MS)
                except Exception as exc:
                    log.warning("[PLAYWRIGHT_EDITORIAL_SOURCE_FAILED] %s: article %s nav failed: %s", name, href, exc)
                    continue
                if not await _wait_for_challenge_to_clear(page):
                    log.warning("[PLAYWRIGHT_EDITORIAL_BLOCKED] %s: challenge on article page", name)
                    continue
                await page.wait_for_timeout(2000)
                title = await _extract_first_text(page, selectors.get("title") or "h1")
                published_at = await _extract_attribute(page, selectors.get("published_at"))
                body = await _extract_body(page, selectors.get("body"))
                if not body and not title:
                    continue
                full_text = f"{title}\n\n{body}"
                if not _article_matches_pair(full_text, match_info.get("home"), match_info.get("away")):
                    # The anchor matched but the body did not — drop, false positive.
                    continue
                items.append({
                    "source":         name,
                    "source_url":     href,
                    "published_at":   published_at or None,
                    "language":       source.get("language") or "es",
                    "title":          (title or "").strip(),
                    "raw_text":       body[:8000],
                    "scraped_at":     _now_iso(),
                    "_match_payload": match_info,
                })
                log.info("[PLAYWRIGHT_EDITORIAL_SOURCE_OK] %s captured %s", name, href)
    except Exception as exc:
        log.warning("[PLAYWRIGHT_EDITORIAL_SOURCE_FAILED] %s: %s", name, exc)
    finally:
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
    return items


def _build_proxy_from_env() -> Optional[dict]:
    """Read `PLAYWRIGHT_PROXY` env var and translate to Playwright dict.

    Format accepted:  http://user:pass@host:port  OR  http://host:port
    Returns None when no env var is set.
    """
    url = os.environ.get("PLAYWRIGHT_PROXY")
    if not url:
        return None
    m = re.match(r"^(\w+)://(?:([^:@]+):([^@]+)@)?([^:/]+):(\d+)/?$", url)
    if not m:
        return {"server": url}
    scheme, user, pwd, host, port = m.groups()
    out: dict[str, Any] = {"server": f"{scheme}://{host}:{port}"}
    if user:
        out["username"] = user
    if pwd:
        out["password"] = pwd
    return out


async def fetch_with_playwright(
    matches: list[dict],
    sources: list[dict],
    *,
    timeout_sec: float = 30.0,
    user_agent: Optional[str] = None,
) -> list[dict]:
    """Public API — fetch raw editorial items from JS-rendered sources.

    Mirrors the contract of `scrapy_runner.run_scrapy`. Returns an empty
    list when there is nothing to do or when Playwright fails (fail-soft).
    """
    if not matches or not sources:
        return []
    js_sources = [s for s in sources if s.get("requires_js")]
    if not js_sources:
        return []

    ua = user_agent or (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    proxy = _build_proxy_from_env()
    if proxy:
        log.info("[PLAYWRIGHT_EDITORIAL_START] %d match(es) × %d JS source(s), proxy=%s",
                 len(matches), len(js_sources), proxy.get("server"))
    else:
        log.info("[PLAYWRIGHT_EDITORIAL_START] %d match(es) × %d JS source(s), no-proxy",
                 len(matches), len(js_sources))

    # Late import so the module is import-safe even when playwright is missing.
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        log.warning("[PLAYWRIGHT_EDITORIAL_SOURCE_FAILED] playwright not installed: %s", exc)
        return []

    items: list[dict] = []
    started = time.time()
    try:
        async with async_playwright() as p:
            for src in sorted(js_sources, key=lambda s: s.get("priority", 99)):
                remaining = max(5.0, timeout_sec - (time.time() - started))
                source_items = await _fetch_source(
                    p, src, matches,
                    timeout_sec=remaining,
                    user_agent=ua,
                    proxy=proxy,
                )
                items.extend(source_items)
                if time.time() - started >= timeout_sec:
                    break
    except Exception as exc:
        log.warning("[PLAYWRIGHT_EDITORIAL_SOURCE_FAILED] outer exception: %s", exc)
    log.info("[PLAYWRIGHT_EDITORIAL_DONE] items=%d duration=%.1fs", len(items), time.time() - started)
    return items


__all__ = ["fetch_with_playwright"]
