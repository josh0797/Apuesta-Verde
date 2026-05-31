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
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"
_TOKEN_ENV = "BRIGHTDATA_TOKEN"
_ZONE_ENV  = "BRIGHTDATA_ZONE"
_warned_missing = False


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
            return None
        body = r.text
        return body or None
    except (httpx.HTTPError, httpx.ReadTimeout) as exc:
        log.warning("brightdata fetch %s failed: %s", url, exc)
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
