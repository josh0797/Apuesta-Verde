"""
Bright Data Web Unlocker — thin async client.

Used by the MLB / NBA editorial scrapers to bypass Cloudflare-protected
sources (Baseball Savant, ESPN, MLB.com news, etc.). The credential is
read from `BRIGHTDATA_TOKEN` and the zone defaults to `web_unlocker1`.

Design
------
- **Fail-soft**: on any error returns `None` so callers can fall back to
  their existing primary source.
- **Honest about state**: when the token is missing the client logs a
  one-time warning and silently no-ops; it does NOT raise.
- **Bounded**: 20s default timeout, exposes a `last_status` for callers
  that want to surface scraping errors to the UI/audit log.
- **Observable**: keeps an in-memory 24h rolling counter of fetch
  attempts / successes / failures so `/api/admin/brightdata` can report
  the health of the integration without hitting Mongo on every call.

Usage
-----
    from services.brightdata_client import fetch_unlocked
    html = await fetch_unlocked("https://baseballsavant.mlb.com/...")
    if html:
        ...  # parse
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"
_TOKEN_ENV = "BRIGHTDATA_TOKEN"
_ZONE_ENV  = "BRIGHTDATA_ZONE"
_warned_missing = False

# ── Observability — rolling 24h fetch ledger (in-memory) ────────────────────
# Each entry: (ts_unix, status_int_or_None, ok_bool, url_short)
# `status` is the HTTP status from Bright Data; `ok` is True for 2xx with
# non-empty body. We trim aggressively on every push so memory stays bounded
# (one Web Unlocker request per scraper, dozens of scrapers, ~few k/day).
_LEDGER_MAX     = 5000
_LEDGER_WINDOW  = 24 * 3600
_ledger: deque = deque(maxlen=_LEDGER_MAX)


def _record_fetch(url: str, status: Optional[int], ok: bool) -> None:
    now = time.time()
    # Trim entries older than the window (cheap because deque keeps order).
    cutoff = now - _LEDGER_WINDOW
    while _ledger and _ledger[0][0] < cutoff:
        _ledger.popleft()
    _ledger.append((now, status, ok, (url or "")[:80]))


def get_health_snapshot() -> dict:
    """Snapshot of the last-24h ledger. Used by `/api/admin/brightdata`."""
    now   = time.time()
    cutoff = now - _LEDGER_WINDOW
    while _ledger and _ledger[0][0] < cutoff:
        _ledger.popleft()
    total = len(_ledger)
    ok    = sum(1 for _, _, k, _ in _ledger if k)
    fail  = total - ok
    last  = None
    if _ledger:
        ts, status, k, u = _ledger[-1]
        last = {"ts_iso": _epoch_to_iso(ts), "status": status, "ok": k, "url": u}
    return {
        "fetches_24h":   total,
        "ok_24h":        ok,
        "fail_24h":      fail,
        "success_ratio": round(ok / total, 3) if total else None,
        "last_fetch":    last,
    }


def _epoch_to_iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _get_credentials() -> tuple[Optional[str], str]:
    global _warned_missing
    token = os.environ.get(_TOKEN_ENV)
    zone  = os.environ.get(_ZONE_ENV, "web_unlocker1")
    if not token and not _warned_missing:
        log.warning(
            "BRIGHTDATA_TOKEN missing from env — fetch_unlocked() will no-op. "
            "Set it in backend/.env to enable Cloudflare-protected sources."
        )
        _warned_missing = True
    return token, zone


async def fetch_unlocked(
    url: str,
    *,
    timeout: float = 20.0,
    fmt: str = "raw",
) -> Optional[str]:
    """Return the raw HTML/JSON behind a Cloudflare-protected URL.

    Returns `None` when the token is missing, the endpoint errors out,
    or the response is empty. Callers must always handle the None case.
    """
    token, zone = _get_credentials()
    if not token or not url:
        return None
    payload = {"zone": zone, "url": url, "format": fmt}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                _BRIGHTDATA_ENDPOINT,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
            )
        if r.status_code != 200:
            log.warning("brightdata fetch %s → status %s", url, r.status_code)
            _record_fetch(url, r.status_code, False)
            return None
        body = r.text
        ok = bool(body)
        _record_fetch(url, 200, ok)
        return body or None
    except (httpx.HTTPError, httpx.ReadTimeout) as exc:
        log.warning("brightdata fetch %s failed: %s", url, exc)
        _record_fetch(url, None, False)
        return None


async def healthcheck() -> dict:
    """Tiny probe used by /api/admin to confirm credentials still work."""
    token, zone = _get_credentials()
    if not token:
        return {"ok": False, "reason": "missing_token"}
    try:
        body = await fetch_unlocked("https://geo.brdtest.com/mygeo.json", timeout=10.0)
        return {"ok": bool(body), "zone": zone, "sample": (body or "")[:120]}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "reason": str(exc)}
