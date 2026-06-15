"""F87.b — Sofascore fixture discovery via scrape.do.

Calls the public Sofascore JSON endpoint through ``scrape_do_client``
(no headless browser needed) and normalises events into the API-Football
"next-48h" shape via :func:`sofascore_fixtures_adapter._normalise_sofascore_event`.

Public surface
--------------
* :func:`fetch_fixtures_today` — pull today's Sofascore schedule via scrape.do.

Fail-soft: never raises; returns ``[]`` on any failure.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from .sofascore_fixtures_adapter import _normalise_sofascore_event

log = logging.getLogger("services.scrapedo_fixtures_adapter")

SOFASCORE_API_TPL = (
    "https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date}"
)
DEFAULT_TIMEOUT_S = 45.0


def _flag_enabled() -> bool:
    raw = (os.environ.get("ENABLE_SCRAPEDO_FIXTURES_FALLBACK") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


async def fetch_fixtures_today(date_iso: Optional[str] = None,
                                *, timeout_s: float = DEFAULT_TIMEOUT_S,
                                ) -> list[dict]:
    """Fetch today's Sofascore football schedule via scrape.do.

    Returns ``[]`` on any failure (token missing, breaker open, HTTP
    error, JSON parse error). NEVER raises.
    """
    if not _flag_enabled():
        return []
    try:
        from ..scrape_do_client import fetch_via_scrapedo_result, is_enabled
    except Exception as exc:  # noqa: BLE001
        log.warning("[scrapedo_fixtures] import failed: %s", exc)
        return []

    if not is_enabled():
        log.info("[scrapedo_fixtures] scrape.do disabled (token missing) — returning []")
        return []

    di = date_iso or datetime.now(timezone.utc).date().isoformat()
    url = SOFASCORE_API_TPL.format(date=di)

    try:
        res = await fetch_via_scrapedo_result(url, timeout=timeout_s, render=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("[scrapedo_fixtures] transport raised: %s", exc)
        return []

    if not isinstance(res, dict) or not res.get("ok") or not res.get("html"):
        log.info("[scrapedo_fixtures] empty / failed response (reason_code=%s)",
                  (res or {}).get("reason_code"))
        return []

    body = res.get("html")
    try:
        data = json.loads(body)
    except (TypeError, ValueError) as exc:
        log.warning("[scrapedo_fixtures] JSON parse failed: %s", exc)
        return []

    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list) or not events:
        log.info("[scrapedo_fixtures] no events for %s", di)
        return []

    out: list[dict] = []
    for ev in events[:200]:
        normalised = _normalise_sofascore_event(ev, source_tag="scrapedo")
        if normalised is not None:
            out.append(normalised)
    log.info("[scrapedo_fixtures] normalised %d/%d events (date=%s)",
              len(out), len(events), di)
    return out


__all__ = [
    "SOFASCORE_API_TPL",
    "DEFAULT_TIMEOUT_S",
    "fetch_fixtures_today",
    "_flag_enabled",
]
