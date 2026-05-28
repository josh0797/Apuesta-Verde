"""Bright Data Web Unlocker fetcher — third editorial backend.

Used for sources whose anti-bot protection (Cloudflare, PerimeterX, Akamai,
DataDome…) blocks both plain HTTP (Scrapy) and headless Chromium (Playwright)
when running from datacenter IPs (which is the case in this container).

Two transport modes are supported, decided by what is configured in the env:

1. **Direct API mode** (`BRIGHTDATA_API_KEY` set):
     POST https://api.brightdata.com/request
       headers:  Authorization: Bearer <BRIGHTDATA_API_KEY>
       body:     {zone, url, format}
   Returns the upstream HTML (when format="raw") or a JSON envelope
   containing the HTML in `body` (when format="json"). We use `json` so we
   can detect upstream errors gracefully.

2. **Native proxy mode** (`BRIGHTDATA_ZONE_PASSWORD` set):
     PROXY:  http://brd-customer-<customer-id>-zone-<ZONE>:<PASSWORD>@brd.superproxy.io:22225
   This is used as the `proxies=` argument for `httpx.AsyncClient`. We
   currently only enable this when the API mode is unavailable.

Either mode keeps the fetcher fail-soft: ANY failure (auth, timeout, parse)
returns an empty item list so the analyst engine keeps running.

Output shape matches the Scrapy + Playwright runners:
    {
        "source":         str,
        "source_url":     str,
        "published_at":   str|None,
        "language":       str,
        "title":          str,
        "raw_text":       str,
        "scraped_at":     str,
        "_match_payload": dict,
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
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("editorial.brightdata_fetcher")

# ── Hard limits (sane defaults) ─────────────────────────────────────────
_API_ENDPOINT             = "https://api.brightdata.com/request"
_MAX_ARTICLES_PER_SOURCE  = 12
_PER_REQUEST_TIMEOUT_SEC  = 25.0
_DEFAULT_USER_AGENT       = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ── Helpers ─────────────────────────────────────────────────────────────
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


# ── Bright Data API client ──────────────────────────────────────────────
class _BrightDataClient:
    """Thin async client around the Bright Data Web Unlocker API.

    All credentials are read from environment variables at construction
    time. Nothing is hard-coded:
        BRIGHTDATA_API_KEY        — required for API mode (Bearer token)
        BRIGHTDATA_ZONE           — zone name (default `web_unlocker1`)
        BRIGHTDATA_ZONE_PASSWORD  — optional, used for native-proxy fallback
    """

    def __init__(self) -> None:
        self.api_key  = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
        self.zone     = os.environ.get("BRIGHTDATA_ZONE", "web_unlocker1").strip()
        self.password = os.environ.get("BRIGHTDATA_ZONE_PASSWORD", "").strip()
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.zone)

    async def __aenter__(self) -> "_BrightDataClient":
        # Bright Data API responds best on HTTP/1.1
        self._client = httpx.AsyncClient(
            http2=False,
            timeout=_PER_REQUEST_TIMEOUT_SEC,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str, *, country: Optional[str] = None) -> Optional[str]:
        """Fetch `url` through Bright Data. Returns HTML or None on failure.

        We use `format=json` so the upstream HTTP code is visible. When the
        upstream returned 200, we hand back `body`. Otherwise we log and
        return None — callers should NEVER raise.
        """
        if not self.available or self._client is None:
            return None
        payload: dict[str, Any] = {
            "zone":   self.zone,
            "url":    url,
            "format": "json",
        }
        if country:
            payload["country"] = country
        try:
            r = await self._client.post(_API_ENDPOINT, json=payload)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            log.warning("[BRIGHTDATA_EDITORIAL_FAILED] %s: request error: %s", url, exc)
            return None

        if r.status_code != 200:
            # API-level error (rare — usually a transport problem)
            log.warning(
                "[BRIGHTDATA_EDITORIAL_FAILED] %s: API HTTP %s: %s",
                url, r.status_code, r.text[:200],
            )
            return None

        try:
            data = r.json()
        except Exception as exc:
            log.warning("[BRIGHTDATA_EDITORIAL_FAILED] %s: JSON parse: %s", url, exc)
            return None

        upstream_status = data.get("status_code")
        body            = data.get("body") or ""
        if not isinstance(upstream_status, int) or upstream_status >= 400:
            err_msg = ""
            try:
                hdrs = data.get("headers") or {}
                err_msg = hdrs.get("x-brd-err-msg") or hdrs.get("proxy-status") or ""
            except Exception:
                pass
            log.warning(
                "[BRIGHTDATA_EDITORIAL_BLOCKED] %s upstream=%s err=%s",
                url, upstream_status, (err_msg or "")[:160],
            )
            return None
        if not body or len(body) < 50:
            log.info("[BRIGHTDATA_EDITORIAL_OK_EMPTY] %s: tiny body (%d bytes)", url, len(body))
            return None
        return body


# ── HTML extraction (BeautifulSoup) ─────────────────────────────────────
def _select_first_text(soup: BeautifulSoup, css: Optional[str]) -> str:
    if not css:
        return ""
    for sel in css.split(","):
        sel = sel.strip().rstrip(".text").rstrip(":")
        # Scrapy-style "::text" — strip when present, BS4 returns text by default
        sel = re.sub(r"::text$", "", sel).strip()
        if not sel:
            continue
        try:
            el = soup.select_one(sel)
            if el:
                v = el.get_text(" ", strip=True)
                if v:
                    return v
        except Exception:
            continue
    return ""


def _select_attr(soup: BeautifulSoup, css_attr: Optional[str]) -> str:
    """Selector shape `cssSelector::attr(name)` → value of that attribute."""
    if not css_attr:
        return ""
    for sel in css_attr.split(","):
        sel = sel.strip()
        m = re.match(r"^(.*)::attr\((\w+)\)$", sel)
        if not m:
            continue
        css, attr = m.group(1).strip(), m.group(2)
        try:
            el = soup.select_one(css)
            if el and el.has_attr(attr):
                v = el[attr]
                if isinstance(v, list):
                    v = " ".join(v)
                v = (v or "").strip()
                if v:
                    return v
        except Exception:
            continue
    return ""


def _select_body_text(soup: BeautifulSoup, css: Optional[str]) -> str:
    if not css:
        # Fallback: all paragraph text
        return " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))
    for sel in css.split(","):
        sel = sel.strip()
        sel = re.sub(r"::text$", "", sel).strip()
        if not sel:
            continue
        try:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                t = re.sub(r"\s+", " ", t)
                if len(t) >= 200:
                    return t
        except Exception:
            continue
    return " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))


def _select_anchors(soup: BeautifulSoup, css: str) -> list[tuple[str, str]]:
    """Return (href, anchor_text) pairs for elements matched by `css`."""
    out: list[tuple[str, str]] = []
    if not css:
        css = "a"
    try:
        for el in soup.select(css):
            href = (el.get("href") or "").strip()
            if not href:
                continue
            text = el.get_text(" ", strip=True)
            out.append((href, text))
    except Exception as exc:
        log.debug("anchor select failed (%s): %s", css, exc)
    return out


# ── Per-source crawl ────────────────────────────────────────────────────
async def _fetch_source(
    client: _BrightDataClient,
    source: dict,
    matches: list[dict],
    *,
    timeout_sec: float,
) -> list[dict]:
    name         = source.get("name") or "unknown"
    started      = time.time()
    base_url     = source.get("base_url") or ""
    selectors    = source.get("selectors") or {}
    anchor_css   = selectors.get("preview_anchors") or "a"
    url_patterns = [p.lower() for p in (source.get("article_url_patterns") or [])]
    url_excludes = [p.lower() for p in (source.get("article_url_exclude_patterns") or [])]
    country_hint = source.get("brightdata_country") or None  # optional geo pin

    items:   list[dict] = []
    visited: set[str]   = set()

    for index_url in (source.get("index_urls") or []):
        if time.time() - started >= timeout_sec:
            log.info("[BRIGHTDATA_EDITORIAL_BUDGET] %s: budget hit, stop", name)
            break
        html = await client.fetch(index_url, country=country_hint)
        if not html:
            # Already logged; try the next index URL.
            continue
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        anchors = _select_anchors(soup, anchor_css)
        chosen:  list[tuple[str, dict]] = []
        for href, text in anchors:
            # Absolutise
            if not href.startswith("http"):
                href = urljoin(base_url + "/", href)
            href_lower = href.lower()
            if url_patterns and not any(p in href_lower for p in url_patterns):
                continue
            if url_excludes and any(p in href_lower for p in url_excludes):
                continue
            anchor_ctx = href + " " + (text or "")
            for m in matches:
                if _article_matches_pair(anchor_ctx, m.get("home"), m.get("away")):
                    if href in visited:
                        continue
                    visited.add(href)
                    chosen.append((href, m))
                    break
            if len(chosen) >= _MAX_ARTICLES_PER_SOURCE:
                break

        if not chosen:
            log.info("[BRIGHTDATA_EDITORIAL_OK_EMPTY] %s: no matching anchors on %s", name, index_url)
            continue

        for href, match_info in chosen:
            if time.time() - started >= timeout_sec:
                break
            art_html = await client.fetch(href, country=country_hint)
            if not art_html:
                continue
            try:
                art_soup = BeautifulSoup(art_html, "lxml")
            except Exception:
                art_soup = BeautifulSoup(art_html, "html.parser")
            title = _select_first_text(art_soup, selectors.get("title") or "h1")
            pub   = _select_attr(art_soup, selectors.get("published_at"))
            body  = _select_body_text(art_soup, selectors.get("body"))
            if not body and not title:
                continue
            full_text = f"{title}\n\n{body}"
            if not _article_matches_pair(full_text, match_info.get("home"), match_info.get("away")):
                continue
            items.append({
                "source":         name,
                "source_url":     href,
                "published_at":   pub or None,
                "language":       source.get("language") or "es",
                "title":          (title or "").strip(),
                "raw_text":       body[:8000],
                "scraped_at":     _now_iso(),
                "_match_payload": match_info,
            })
            log.info("[BRIGHTDATA_EDITORIAL_OK] %s captured %s", name, href)
    return items


# ── Public API ──────────────────────────────────────────────────────────
async def fetch_with_brightdata(
    matches: list[dict],
    sources: list[dict],
    *,
    timeout_sec: float = 35.0,
    user_agent: Optional[str] = None,
) -> list[dict]:
    """Fetch editorial items from sources that need anti-bot bypass.

    Mirrors the public contract of `scrapy_runner.run_scrapy` and
    `playwright_runner.run_playwright`. Returns `[]` whenever:
        • Bright Data credentials are missing
        • The zone is disabled in the Bright Data dashboard
        • Every fetch fails
    The pipeline keeps running regardless — this is a soft enrichment.
    """
    if not matches or not sources:
        return []
    # Feature flag (so ops can disable BrightData without redeploying code)
    if os.environ.get("EDITORIAL_BRIGHTDATA_ENABLED", "true").lower() not in ("1", "true", "yes"):
        log.info("[BRIGHTDATA_EDITORIAL_DISABLED] feature flag off")
        return []

    unlocker_sources = [s for s in sources if s.get("requires_unlocker") and s.get("enabled")]
    if not unlocker_sources:
        return []

    client = _BrightDataClient()
    if not client.available:
        log.warning("[BRIGHTDATA_EDITORIAL_NO_CREDS] api key / zone missing — skipping unlocker sources")
        return []

    items:   list[dict] = []
    started               = time.time()
    log.info(
        "[BRIGHTDATA_EDITORIAL_START] matches=%d unlocker_sources=%d timeout=%.0fs zone=%s",
        len(matches), len(unlocker_sources), timeout_sec, client.zone,
    )

    async with client:
        # Process sources sequentially (Bright Data charges per request — keep it
        # predictable). Each source gets its own slice of the overall budget.
        per_source_budget = max(8.0, timeout_sec / max(1, len(unlocker_sources)))
        for src in sorted(unlocker_sources, key=lambda s: s.get("priority", 99)):
            remaining = max(5.0, timeout_sec - (time.time() - started))
            budget    = min(per_source_budget, remaining)
            try:
                src_items = await _fetch_source(client, src, matches, timeout_sec=budget)
                items.extend(src_items)
            except Exception as exc:
                log.warning("[BRIGHTDATA_EDITORIAL_FAILED] source %s: %s", src.get("name"), exc)
                continue
            if time.time() - started >= timeout_sec:
                break

    log.info("[BRIGHTDATA_EDITORIAL_DONE] items=%d duration=%.1fs",
             len(items), time.time() - started)
    return items


# Alias for symmetry with the other runners (same call site name).
run_brightdata = fetch_with_brightdata


__all__ = ["fetch_with_brightdata", "run_brightdata"]
