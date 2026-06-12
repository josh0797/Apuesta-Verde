"""Common HTTP helpers for external_sources scrapers.

Two transports:
  • `direct_fetch(url, headers=...)` — plain httpx, polite UA, short timeout.
    Used for FBref, Basketball-Reference, NBA Stats API, Football-Data.
  • `brightdata_fetch(url, country=None)` — routes through Bright Data
    Web Unlocker. Used for Cloudflare-protected providers (FotMob,
    SofaScore, Flashscore).

`brightdata_available()` returns True only when `BRIGHTDATA_API_KEY` and
`BRIGHTDATA_ZONE` are configured. When False, the dispatcher silently
skips every scraper that has `requires_unlocker=True`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

import httpx

log = logging.getLogger("external_sources.base")

DEFAULT_TIMEOUT_SEC      = 8.0
BRIGHTDATA_TIMEOUT_SEC   = 25.0
DEFAULT_USER_AGENT       = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def brightdata_available() -> bool:
    """Phase F65 — also honours the global opt-IN gate
    ``BRIGHTDATA_ENABLED``. Set it to ``false`` to pause Bright Data
    globally without removing credentials (incident response)."""
    if not (
        os.environ.get("BRIGHTDATA_API_KEY", "").strip()
        and os.environ.get("BRIGHTDATA_ZONE", "").strip()
    ):
        return False
    try:
        from services.external_sources.circuit_breaker import is_brightdata_enabled
        return is_brightdata_enabled()
    except Exception:  # noqa: BLE001
        return True


async def direct_fetch(
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> Optional[str]:
    """Plain async fetch. Returns body text on 2xx, else None."""
    hdrs = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            r = await client.get(url, headers=hdrs)
    except (httpx.HTTPError, asyncio.TimeoutError, Exception) as exc:
        log.debug("[EXT_SRC_DIRECT_FAIL] %s: %s", url, exc)
        return None
    if r.status_code >= 400:
        log.debug("[EXT_SRC_DIRECT_HTTP] %s: %s", url, r.status_code)
        return None
    return r.text


async def direct_fetch_json(
    url: str, *, headers: Optional[dict] = None, timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> Optional[Any]:
    body = await direct_fetch(url, headers=headers, timeout_sec=timeout_sec)
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


async def brightdata_fetch(
    url: str, *, country: Optional[str] = None,
    timeout_sec: float = BRIGHTDATA_TIMEOUT_SEC,
) -> Optional[str]:
    """Route fetch through Bright Data Web Unlocker. Returns body or None.

    Phase F65 — wrapped in a per-host circuit breaker. When a domain
    accumulates ``FAIL_THRESHOLD`` consecutive failures (default 5), the
    breaker opens for ``PAUSE_SEC`` (default 30 min) and subsequent
    calls return ``None`` without hitting Bright Data, saving budget.
    Policy refusals (gambling/adult/copyright) open the breaker for 24h.
    """
    if not brightdata_available():
        return None
    # Phase F65 — circuit breaker short-circuit.
    try:
        from services.external_sources.circuit_breaker import (
            is_open, record_success, record_failure,
        )
        if is_open(url):
            log.debug("[BD_BREAKER_OPEN] short-circuit %s", url)
            return None
    except Exception:  # noqa: BLE001
        is_open = None  # type: ignore[assignment]
        record_success = None  # type: ignore[assignment]
        record_failure = None  # type: ignore[assignment]
    # Import inside the function so this module can be imported even when
    # Bright Data deps are missing.
    try:
        from services.editorial_context.brightdata_fetcher import _BrightDataClient  # type: ignore
    except Exception as exc:
        log.warning("brightdata client import failed: %s", exc)
        if record_failure:
            record_failure(url, error_code="import_error", error_msg=str(exc))
        return None
    try:
        async with _BrightDataClient() as client:
            html = await asyncio.wait_for(
                client.fetch(url, country=country),
                timeout=timeout_sec,
            )
        if html:
            if record_success:
                record_success(url)
            return html
        if record_failure:
            record_failure(url, error_code="empty_body", error_msg="upstream returned no body")
        return None
    except (asyncio.TimeoutError, Exception) as exc:
        log.debug("[EXT_SRC_BD_FAIL] %s: %s", url, exc)
        if record_failure:
            record_failure(url, error_code=type(exc).__name__, error_msg=str(exc))
        return None


# ────────────────────────────────────────────────────────────────────────────
# Tiny string helpers used by extractors
# ────────────────────────────────────────────────────────────────────────────
_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", str(s)).strip()


def first_n_sentences(text: str, n: int = 3) -> list[str]:
    """Split into sentences with a regex (no NLTK dependency) and return
    the first N non-empty trimmed sentences."""
    parts = re.split(r"(?<=[\.\!\?])\s+", clean_text(text))
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if p and len(p) > 10:
            out.append(p[:220])
        if len(out) >= n:
            break
    return out
