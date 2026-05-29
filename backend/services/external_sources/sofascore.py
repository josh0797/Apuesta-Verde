"""SofaScore scraper — uses the public `/api/v1/search/all/?q=...` endpoint
and per-event details.

SofaScore is heavily Cloudflare-protected → routes through Bright Data.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .base import brightdata_fetch, brightdata_available, clean_text
from .schema import failed_evidence, make_evidence, skipped_evidence

log = logging.getLogger("external_sources.sofascore")

NAME = "sofascore"
APPLICABLE_SPORTS = {"football", "basketball", "baseball"}
REQUIRES_UNLOCKER = True

_SEARCH_URL    = "https://api.sofascore.com/api/v1/search/events?q={q}"
_EVENT_URL     = "https://api.sofascore.com/api/v1/event/{eid}"
_LINEUPS_URL   = "https://api.sofascore.com/api/v1/event/{eid}/lineups"
_H2H_URL       = "https://api.sofascore.com/api/v1/event/{eid}/h2h/events"


async def _resolve_event_id(home: str, away: str, sport: str) -> Optional[int]:
    if not home or not away:
        return None
    q = f"{home} {away}".replace(" ", "%20")
    body = await brightdata_fetch(_SEARCH_URL.format(q=q))
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    for item in (data.get("results") or [])[:5]:
        entity = item.get("entity") or {}
        if entity.get("type") != "event":
            continue
        # filter by sport
        sp = (entity.get("tournament") or {}).get("category", {}).get("sport", {}).get("slug", "").lower()
        if sport == "football" and sp not in ("football", "soccer"):
            continue
        if sport == "basketball" and sp != "basketball":
            continue
        if sport == "baseball" and sp != "baseball":
            continue
        eid = entity.get("id")
        if eid:
            return int(eid)
    return None


def _bullets_from_event(payload: dict, sport: str) -> tuple[list[str], str]:
    bullets: list[str] = []
    evt = payload.get("event") or payload
    status = (evt.get("status") or {}).get("type", "")
    if status:
        bullets.append(f"Estado SofaScore: {status}")
    # Form streak (last 5 results emoji'd by SofaScore)
    for side in ("homeTeam", "awayTeam"):
        team = evt.get(side) or {}
        form = team.get("form") or []
        if form:
            bullets.append(f"Forma {team.get('shortName') or team.get('name')}: {' '.join(form[:5])}")
    # Predictions
    pred = evt.get("winnerCode") or {}
    if isinstance(pred, dict) and pred:
        bullets.append(f"Predicción ganador SofaScore: {pred}")
    # Sport-aware extras
    if sport == "football":
        if evt.get("hasXg"): bullets.append("xG disponible en SofaScore para este partido.")
    elif sport == "basketball":
        if evt.get("hasEventPlayerStatistics"):
            bullets.append("Stats por jugador disponibles en SofaScore.")
    elif sport == "baseball":
        if evt.get("seasonType"):
            bullets.append(f"Fase MLB: {evt.get('seasonType')}")
    return bullets, "recent_form" if any("Forma" in b for b in bullets) else "news_context"


async def fetch(home: str, away: str, *, league: str = "", sport: str = "football", **_) -> dict:
    if sport not in APPLICABLE_SPORTS:
        return skipped_evidence(NAME, reason="sport_not_supported")
    if not brightdata_available():
        return skipped_evidence(NAME, reason="brightdata_not_configured")
    try:
        eid = await _resolve_event_id(home, away, sport)
        if not eid:
            return failed_evidence(NAME, reason="event_id_not_resolved")
        body = await brightdata_fetch(_EVENT_URL.format(eid=eid))
        if not body:
            return failed_evidence(NAME, reason="event_blocked")
        try:
            payload = json.loads(body)
        except Exception as exc:
            return failed_evidence(NAME, reason=f"json_parse:{exc}"[:120])
        bullets, etype = _bullets_from_event(payload, sport)
        # Try H2H — fail-soft
        h2h_body = await brightdata_fetch(_H2H_URL.format(eid=eid))
        if h2h_body:
            try:
                h2h = json.loads(h2h_body)
                count = len((h2h.get("events") or [])[:5])
                if count:
                    bullets.append(f"H2H reciente disponible en SofaScore ({count} encuentros).")
                    if etype == "news_context":
                        etype = "h2h"
            except Exception:
                pass
        url = f"https://www.sofascore.com/event/{eid}"
        return make_evidence(
            NAME, url=url,
            title=clean_text(((payload.get("event") or payload).get("slug") or "")) or None,
            evidence_type=etype,
            extracted_data=bullets,
            confidence=80 if bullets else 40,
            freshness="fresh",
        )
    except Exception as exc:
        log.warning("sofascore.fetch crashed: %s", exc)
        return failed_evidence(NAME, reason=f"unexpected:{exc}"[:120])
