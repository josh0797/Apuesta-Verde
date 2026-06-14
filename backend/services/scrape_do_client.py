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

Phase F83-update — :func:`fetch_via_scrapedo_result`:
    A structured-result variant returning a dict with ``ok``,
    ``html``, ``status_code``, ``reason_code``, ``message_debug`` and
    ``fetched_at``. Used by the corners 365Scores cascade to report
    *exactly* why a fetch failed.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone
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

# Reason codes surfaced by ``fetch_via_scrapedo_result``.
RC_TOKEN_MISSING = "SCRAPEDO_TOKEN_MISSING"
RC_BREAKER_OPEN  = "SCRAPEDO_BREAKER_OPEN"
RC_HTTP_ERROR    = "SCRAPEDO_HTTP_ERROR"
RC_TIMEOUT       = "SCRAPEDO_TIMEOUT"
RC_EMPTY_BODY    = "SCRAPEDO_EMPTY_BODY"
RC_EXCEPTION     = "SCRAPEDO_EXCEPTION"


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
def _empty_result(target_url: str) -> dict:
    return {
        "ok":            False,
        "html":          None,
        "status_code":   None,
        "target_url":    target_url,
        "provider":      "scrape_do",
        "reason_code":   None,
        "message_debug": None,
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
    }


async def fetch_via_scrapedo_result(
    target_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    render: bool = False,
    geo: Optional[str] = None,
) -> dict:
    """Structured-result variant of :func:`fetch_via_scrapedo`.

    Returns a dict with::

        {
          "ok":            bool,
          "html":          str | None,
          "status_code":   int | None,
          "target_url":    str,
          "provider":      "scrape_do",
          "reason_code":   str | None,
          "message_debug": str | None,
          "fetched_at":    "<iso>"
        }

    Reason codes:
      * ``SCRAPEDO_TOKEN_MISSING`` — env var absent.
      * ``SCRAPEDO_BREAKER_OPEN``  — circuit breaker tripped for host.
      * ``SCRAPEDO_HTTP_ERROR``    — non-200 status.
      * ``SCRAPEDO_TIMEOUT``       — read/connect timed out.
      * ``SCRAPEDO_EMPTY_BODY``    — 200 but body empty.
      * ``SCRAPEDO_EXCEPTION``     — any other transport error.
    """
    out = _empty_result(target_url)
    host = urllib.parse.urlparse(target_url).netloc.lower()

    if not _token():
        out["reason_code"]   = RC_TOKEN_MISSING
        out["message_debug"] = (
            "SCRAPEDO_TOKEN env var not set; cannot reach scrape.do for "
            f"{target_url}"
        )
        log.debug("[F83_SCRAPEDO_RESULT] token missing for %s", target_url)
        return out

    if _cb_open(host):
        out["reason_code"]   = RC_BREAKER_OPEN
        out["message_debug"] = (
            f"Circuit breaker is OPEN for host={host}; "
            f"pause_s={_CB_PAUSE_S}s, threshold={_CB_THRESHOLD}"
        )
        log.debug("[F83_SCRAPEDO_RESULT] breaker open for %s", host)
        return out

    api_url = _build_request_url(target_url, render=render, geo=geo)
    if not api_url:  # defensive (token was checked above)
        out["reason_code"]   = RC_TOKEN_MISSING
        out["message_debug"] = "Failed to build scrape.do request URL"
        return out

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(api_url)
    except httpx.TimeoutException as exc:
        out["reason_code"]   = RC_TIMEOUT
        out["message_debug"] = (
            f"scrape.do timed out after {timeout:.0f}s for {target_url}: {exc}"
        )
        _cb_record_failure(host)
        log.warning("[F83_SCRAPEDO_RESULT] timeout %s after %.0fs",
                    target_url, timeout)
        return out
    except Exception as exc:  # noqa: BLE001
        out["reason_code"]   = RC_EXCEPTION
        out["message_debug"] = (
            f"scrape.do transport exception for {target_url}: "
            f"{type(exc).__name__}: {exc}"
        )
        _cb_record_failure(host)
        log.warning("[F83_SCRAPEDO_RESULT] exception %s: %s", target_url, exc)
        return out

    out["status_code"] = r.status_code
    body = r.text or ""

    if r.status_code != 200:
        out["reason_code"]   = RC_HTTP_ERROR
        out["message_debug"] = (
            f"HTTP {r.status_code} from scrape.do for {target_url} "
            f"(body_len={len(body)})"
        )
        _cb_record_failure(host)
        log.warning("[F83_SCRAPEDO_RESULT] HTTP %s for %s",
                    r.status_code, target_url)
        return out

    if not body:
        out["reason_code"]   = RC_EMPTY_BODY
        out["message_debug"] = (
            f"scrape.do returned 200 with EMPTY body for {target_url}"
        )
        _cb_record_failure(host)
        log.warning("[F83_SCRAPEDO_RESULT] empty body for %s", target_url)
        return out

    out["ok"]   = True
    out["html"] = body
    _cb_record_success(host)
    return out


async def fetch_via_scrapedo(target_url: str,
                              *, timeout: float = DEFAULT_TIMEOUT,
                              render: bool = False,
                              geo: Optional[str] = None) -> Optional[str]:
    """Async fetch via scrape.do. Returns HTML string or ``None``.

    Legacy wrapper around :func:`fetch_via_scrapedo_result` — preserved
    for back-compat. All failures (no token, breaker open, HTTP error,
    timeout) return ``None`` so callers can degrade silently.
    """
    res = await fetch_via_scrapedo_result(
        target_url, timeout=timeout, render=render, geo=geo,
    )
    return res["html"] if res.get("ok") else None


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
    "fetch_via_scrapedo_result",
    "fetch_via_scrapedo_sync",
    "is_enabled",
    "breaker_status",
    "RC_TOKEN_MISSING", "RC_BREAKER_OPEN", "RC_HTTP_ERROR",
    "RC_TIMEOUT", "RC_EMPTY_BODY", "RC_EXCEPTION",
]
