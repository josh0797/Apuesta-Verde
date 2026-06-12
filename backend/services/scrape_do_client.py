"""Phase F70 — scrape.do HTTP client.

Generic anti-bot scraping helper. We use scrape.do because Bright Data's
policy blocks gambling/prediction sites (Sportytrader, Forebet deep
pages). scrape.do supports those domains.

Design goals:
  * Fail-soft: any exception → ``None`` (caller falls back to internal
    editorial engine).
  * Telemetry-friendly: returns a typed dict with status/source so
    callers can log audit codes.
  * Circuit-breaker aware: pause requests after repeated failures.
  * Async + sync wrappers (the codebase mixes both).

Configuration:
  * ``SCRAPEDO_TOKEN``  — required, loaded from ``backend/.env``.
  * ``SCRAPEDO_BASE``   — defaults to ``http://api.scrape.do``.

Usage:
    html = await fetch_via_scrapedo(
        "https://www.sportytrader.com/es/pronosticos/canada-bosnia-353713/",
        timeout=45.0,
    )
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse
from typing import Optional

import httpx

log = logging.getLogger("scrapedo")

# ─────────────────────────────────────────────────────────────────────
# Module config
# ─────────────────────────────────────────────────────────────────────
DEFAULT_BASE = "http://api.scrape.do"
DEFAULT_TIMEOUT = 60.0

# Lightweight in-process circuit breaker. Resets every CB_WINDOW_S.
_CB_FAILS: dict[str, int] = {}
_CB_OPENED_AT: dict[str, float] = {}
_CB_THRESHOLD = 5
_CB_PAUSE_S = 600  # 10 min cool-down


def _token() -> Optional[str]:
    return (os.environ.get("SCRAPEDO_TOKEN")
            or os.environ.get("SCRAPE_DO_TOKEN")
            or os.environ.get("SCRAPEDO_API_KEY")
            or None)


def _base() -> str:
    return os.environ.get("SCRAPEDO_BASE") or DEFAULT_BASE


def _cb_open(host: str) -> bool:
    opened = _CB_OPENED_AT.get(host)
    if not opened:
        return False
    if (time.time() - opened) > _CB_PAUSE_S:
        # Cool-down expired → reset.
        _CB_FAILS.pop(host, None)
        _CB_OPENED_AT.pop(host, None)
        return False
    return True


def _cb_record_failure(host: str) -> None:
    _CB_FAILS[host] = _CB_FAILS.get(host, 0) + 1
    if _CB_FAILS[host] >= _CB_THRESHOLD and host not in _CB_OPENED_AT:
        _CB_OPENED_AT[host] = time.time()
        log.warning("[F70_CB] scrape.do breaker opened for host=%s "
                    "(threshold=%d). Pausing %ds.",
                    host, _CB_THRESHOLD, _CB_PAUSE_S)


def _cb_record_success(host: str) -> None:
    if host in _CB_FAILS:
        _CB_FAILS.pop(host, None)
        _CB_OPENED_AT.pop(host, None)


def _build_request_url(target_url: str, *,
                        render: bool = False,
                        geo: Optional[str] = None) -> Optional[str]:
    token = _token()
    if not token:
        log.debug("[F70_SCRAPEDO] no token in env — skipping")
        return None
    params = {"url": target_url, "token": token}
    if render:
        params["render"] = "true"
    if geo:
        params["geoCode"] = geo
    return _base() + "/?" + urllib.parse.urlencode(params, safe="")


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
async def fetch_via_scrapedo(target_url: str,
                              *, timeout: float = DEFAULT_TIMEOUT,
                              render: bool = False,
                              geo: Optional[str] = None) -> Optional[str]:
    """Async fetch via scrape.do. Returns HTML string or ``None``.

    All failures (no token, breaker open, HTTP error, timeout) return
    ``None`` so callers can degrade silently.
    """
    host = urllib.parse.urlparse(target_url).netloc.lower()
    if _cb_open(host):
        log.debug("[F70_SCRAPEDO] breaker open for host=%s — skipping", host)
        return None

    api_url = _build_request_url(target_url, render=render, geo=geo)
    if not api_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(api_url)
        if r.status_code == 200 and r.text:
            _cb_record_success(host)
            return r.text
        log.warning("[F70_SCRAPEDO] HTTP %s for %s (len=%d)",
                    r.status_code, target_url, len(r.text or ""))
        _cb_record_failure(host)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("[F70_SCRAPEDO] fetch failed %s: %s", target_url, exc)
        _cb_record_failure(host)
        return None


def fetch_via_scrapedo_sync(target_url: str,
                             *, timeout: float = DEFAULT_TIMEOUT,
                             render: bool = False,
                             geo: Optional[str] = None) -> Optional[str]:
    """Sync version (used by tests and cron jobs)."""
    host = urllib.parse.urlparse(target_url).netloc.lower()
    if _cb_open(host):
        return None
    api_url = _build_request_url(target_url, render=render, geo=geo)
    if not api_url:
        return None
    try:
        r = httpx.get(api_url, timeout=timeout, follow_redirects=True)
        if r.status_code == 200 and r.text:
            _cb_record_success(host)
            return r.text
        log.warning("[F70_SCRAPEDO_SYNC] HTTP %s for %s",
                    r.status_code, target_url)
        _cb_record_failure(host)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("[F70_SCRAPEDO_SYNC] fetch failed %s: %s", target_url, exc)
        _cb_record_failure(host)
        return None


def is_enabled() -> bool:
    return _token() is not None


def breaker_status() -> dict:
    """Diagnostic snapshot of the in-process circuit breaker."""
    return {
        "open_hosts": list(_CB_OPENED_AT.keys()),
        "fail_counts": dict(_CB_FAILS),
        "threshold":   _CB_THRESHOLD,
        "pause_s":     _CB_PAUSE_S,
    }


__all__ = [
    "fetch_via_scrapedo",
    "fetch_via_scrapedo_sync",
    "is_enabled",
    "breaker_status",
]
