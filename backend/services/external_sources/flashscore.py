"""Flashscore scraper — uses the public mobile JSON feed.

Flashscore's main site is fortified with anti-bot but the mobile API
endpoint (`/x/feed/`) exposes JSON-ish data with a known prefix. Heavy
Cloudflare → BrightData required.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .base import brightdata_fetch, brightdata_available, clean_text
from .schema import failed_evidence, make_evidence, skipped_evidence

log = logging.getLogger("external_sources.flashscore")

NAME = "flashscore"
APPLICABLE_SPORTS = {"football", "basketball", "baseball"}
REQUIRES_UNLOCKER = True

# Flashscore search endpoint. They return TS-prefixed text.
_SEARCH_URL = (
    "https://s.flashscore.com/x/feed/search_{q}"
)

_SPORT_ID_MAP = {"football": "1", "basketball": "2", "baseball": "6"}


def _parse_flashscore_feed(text: str) -> list[dict]:
    """Flashscore returns CR-LF rows like `~AC÷xyz¬AD÷home¬AE÷away¬...`
    where each entity is delimited by `~` and each field by `¬` + key+`÷`.
    We parse to list[dict] for the first few entries.
    """
    out: list[dict] = []
    if not text:
        return out
    # strip BOM / leading TS marker if any
    for raw_row in text.split("~"):
        row = raw_row.strip()
        if not row:
            continue
        fields = row.split("¬")
        d: dict = {}
        for f in fields:
            if "÷" in f:
                k, v = f.split("÷", 1)
                d[k.strip()] = v.strip()
        if d:
            out.append(d)
        if len(out) >= 5:
            break
    return out


async def _resolve_event(home: str, away: str, sport: str) -> Optional[dict]:
    if not home or not away:
        return None
    q = f"{home}+{away}".replace(" ", "+")
    body = await brightdata_fetch(_SEARCH_URL.format(q=q))
    if not body:
        return None
    rows = _parse_flashscore_feed(body)
    # Look for an event whose label resembles "home - away"
    home_l = home.lower()
    away_l = away.lower()
    for r in rows:
        label = (r.get("PD") or r.get("EI") or "").lower()
        if home_l[:6] in label and away_l[:6] in label:
            return r
    return rows[0] if rows else None


def _bullets_from_event(evt: dict, sport: str) -> tuple[list[str], str]:
    bullets: list[str] = []
    label = clean_text(evt.get("PD") or evt.get("EI") or "")
    if label:
        bullets.append(f"Encuentro localizado en Flashscore: {label[:120]}")
    if evt.get("ER"):
        bullets.append(f"Resultado/ETA Flashscore: {evt.get('ER')}")
    if evt.get("AH"):
        bullets.append("Datos H2H disponibles en Flashscore.")
    etype = "news_context"
    if "AH" in evt:
        etype = "h2h"
    return bullets, etype


async def fetch(home: str, away: str, *, league: str = "", sport: str = "football", **_) -> dict:
    if sport not in APPLICABLE_SPORTS:
        return skipped_evidence(NAME, reason="sport_not_supported")
    if not brightdata_available():
        return skipped_evidence(NAME, reason="brightdata_not_configured")
    try:
        evt = await _resolve_event(home, away, sport)
        if not evt:
            return failed_evidence(NAME, reason="event_not_resolved")
        bullets, etype = _bullets_from_event(evt, sport)
        eid = evt.get("AA") or evt.get("EI") or ""
        url = f"https://www.flashscore.com/match/{eid}" if eid else "https://www.flashscore.com"
        return make_evidence(
            NAME, url=url,
            title=clean_text(evt.get("PD") or "") or None,
            evidence_type=etype,
            extracted_data=bullets,
            confidence=65 if bullets else 30,
            freshness="fresh",
        )
    except Exception as exc:
        log.warning("flashscore.fetch crashed: %s", exc)
        return failed_evidence(NAME, reason=f"unexpected:{exc}"[:120])
