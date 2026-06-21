"""SofaScore scraper — Sprint-D9-HOTFIX3 · Migrado a Scrape.do.

SofaScore es **fuertemente protegido por Cloudflare**. Antes este módulo
ruteaba sus llamadas vía Bright Data, pero el usuario decidió mover
TODO el scraping a Scrape.do (single-provider strategy, mejor visibilidad
de costos y políticas de gambling). Este archivo ahora consume
``services.scrape_do_client.fetch_via_scrapedo_result`` con
``render=False`` (los endpoints ``api.sofascore.com`` devuelven JSON
puro, no requieren JS rendering).

Fail-soft end-to-end:
  * Si ``SCRAPEDO_TOKEN`` no está configurado → ``skipped_evidence``.
  * Si el fetch falla (status != 2xx, timeout, etc.) → ``failed_evidence``.
  * Si el JSON no parsea o ``id`` no se resuelve → ``failed_evidence``.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .base import clean_text
from .schema import failed_evidence, make_evidence, skipped_evidence

log = logging.getLogger("external_sources.sofascore")

NAME = "sofascore"
APPLICABLE_SPORTS = {"football", "basketball", "baseball"}
REQUIRES_UNLOCKER = True  # Cloudflare-protected; debemos pasar por Scrape.do.
UNLOCKER_PROVIDER = "scrapedo"  # Sprint-D9-HOTFIX3: migrado de Bright Data

# ─────────────────────────────────────────────────────────────────────
# Endpoints (api.sofascore.com — JSON puro, no requiere render JS)
# ─────────────────────────────────────────────────────────────────────
_SEARCH_URL  = "https://api.sofascore.com/api/v1/search/events?q={q}"
_EVENT_URL   = "https://api.sofascore.com/api/v1/event/{eid}"
_LINEUPS_URL = "https://api.sofascore.com/api/v1/event/{eid}/lineups"
_H2H_URL     = "https://api.sofascore.com/api/v1/event/{eid}/h2h/events"

# Timeout default conservador para endpoints JSON; cada fetch < 30s.
_SCRAPEDO_TIMEOUT_S = 30.0


# ─────────────────────────────────────────────────────────────────────
# Scrape.do helpers (fail-soft)
# ─────────────────────────────────────────────────────────────────────
async def _scrapedo_available() -> bool:
    try:
        from services.scrape_do_client import is_enabled
        return bool(is_enabled())
    except Exception:  # noqa: BLE001
        return False


async def _scrapedo_fetch(url: str) -> Optional[str]:
    """Fetch JSON body via Scrape.do (no render). Returns ``None`` si
    el fetch falla — el caller decide el reason_code."""
    try:
        from services.scrape_do_client import fetch_via_scrapedo_result
        res = await fetch_via_scrapedo_result(
            url, timeout=_SCRAPEDO_TIMEOUT_S, render=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("sofascore scrape.do fetch crashed for %s: %s", url, exc)
        return None
    if not res or not res.get("ok"):
        log.info(
            "sofascore scrape.do non-ok response url=%s status=%s reason=%s",
            url, res.get("status_code") if res else None,
            res.get("reason_code") if res else "no_result",
        )
        return None
    return res.get("html") or None


# ─────────────────────────────────────────────────────────────────────
# Event ID resolver
# ─────────────────────────────────────────────────────────────────────
async def _resolve_event_id(home: str, away: str, sport: str) -> Optional[int]:
    if not home or not away:
        return None
    q = f"{home} {away}".replace(" ", "%20")
    body = await _scrapedo_fetch(_SEARCH_URL.format(q=q))
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    for item in (data.get("results") or [])[:5]:
        entity = item.get("entity") or {}
        if entity.get("type") != "event":
            continue
        # Filter by sport — Sofascore usa slug "football"/"basketball"/"baseball"
        sp = (
            (entity.get("tournament") or {})
            .get("category", {}).get("sport", {})
            .get("slug", "")
            .lower()
        )
        if sport == "football" and sp not in ("football", "soccer"):
            continue
        if sport == "basketball" and sp != "basketball":
            continue
        if sport == "baseball" and sp != "baseball":
            continue
        eid = entity.get("id")
        if eid:
            try:
                return int(eid)
            except (TypeError, ValueError):
                continue
    return None


# ─────────────────────────────────────────────────────────────────────
# Evidence builders
# ─────────────────────────────────────────────────────────────────────
def _bullets_from_event(payload: dict, sport: str) -> tuple[list[str], str]:
    bullets: list[str] = []
    evt = payload.get("event") or payload
    status = (evt.get("status") or {}).get("type", "")
    if status:
        bullets.append(f"Estado SofaScore: {status}")
    for side in ("homeTeam", "awayTeam"):
        team = evt.get(side) or {}
        form = team.get("form") or []
        if form:
            bullets.append(
                f"Forma {team.get('shortName') or team.get('name')}: "
                f"{' '.join(form[:5])}"
            )
    pred = evt.get("winnerCode") or {}
    if isinstance(pred, dict) and pred:
        bullets.append(f"Predicción ganador SofaScore: {pred}")
    if sport == "football":
        if evt.get("hasXg"):
            bullets.append("xG disponible en SofaScore para este partido.")
    elif sport == "basketball":
        if evt.get("hasEventPlayerStatistics"):
            bullets.append("Stats por jugador disponibles en SofaScore.")
    elif sport == "baseball":
        if evt.get("seasonType"):
            bullets.append(f"Fase MLB: {evt.get('seasonType')}")
    etype = "recent_form" if any("Forma" in b for b in bullets) else "news_context"
    return bullets, etype


# ─────────────────────────────────────────────────────────────────────
# Public fetcher
# ─────────────────────────────────────────────────────────────────────
async def fetch(home: str, away: str, *, league: str = "",
                  sport: str = "football", **_) -> dict:
    if sport not in APPLICABLE_SPORTS:
        return skipped_evidence(NAME, reason="sport_not_supported")
    if not await _scrapedo_available():
        return skipped_evidence(NAME, reason="scrapedo_not_configured")
    try:
        eid = await _resolve_event_id(home, away, sport)
        if not eid:
            return failed_evidence(NAME, reason="event_id_not_resolved")
        body = await _scrapedo_fetch(_EVENT_URL.format(eid=eid))
        if not body:
            return failed_evidence(NAME, reason="event_blocked")
        try:
            payload = json.loads(body)
        except Exception as exc:  # noqa: BLE001
            return failed_evidence(NAME, reason=f"json_parse:{exc}"[:120])
        bullets, etype = _bullets_from_event(payload, sport)
        # H2H — fail-soft (no abortamos la evidencia principal si falla).
        h2h_body = await _scrapedo_fetch(_H2H_URL.format(eid=eid))
        if h2h_body:
            try:
                h2h = json.loads(h2h_body)
                count = len((h2h.get("events") or [])[:5])
                if count:
                    bullets.append(
                        f"H2H reciente disponible en SofaScore ({count} encuentros)."
                    )
                    if etype == "news_context":
                        etype = "h2h"
            except Exception:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
        log.warning("sofascore.fetch crashed: %s", exc)
        return failed_evidence(NAME, reason=f"unexpected:{exc}"[:120])
