"""FotMob scraper — uses the public `/api/matchDetails` JSON endpoint.

FotMob is JS-heavy, but they expose JSON via a public API used by their
own SPA. Datacenter IPs are frequently challenged → we route through
Bright Data Web Unlocker.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .base import brightdata_fetch, brightdata_available, clean_text
from .schema import failed_evidence, make_evidence, skipped_evidence

log = logging.getLogger("external_sources.fotmob")

NAME = "fotmob"
APPLICABLE_SPORTS = {"football"}
REQUIRES_UNLOCKER = True

_SEARCH_URL  = "https://www.fotmob.com/api/searchapi/suggest?term={q}&lang=es&maxResults=5"
_MATCH_URL   = "https://www.fotmob.com/api/matchDetails?matchId={mid}"


async def _resolve_match_id(home: str, away: str) -> Optional[str]:
    if not home or not away:
        return None
    q = f"{home} vs {away}"
    body = await brightdata_fetch(_SEARCH_URL.format(q=q.replace(" ", "%20")))
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    # FotMob suggest returns lists keyed by category: 'leagues','teams','matches'
    suggestions = (data.get("suggestions") or {}).get("matchSuggest") or []
    if not suggestions:
        return None
    # Pick first match suggestion (most relevant).
    first = suggestions[0]
    if isinstance(first, dict):
        return str(first.get("id") or first.get("matchId") or "")
    return None


def _bullets_from_match(payload: dict) -> tuple[list[str], str]:
    """Extract human bullets + best `evidence_type` from a FotMob payload."""
    bullets: list[str] = []
    # 1. Header insights
    header = payload.get("header") or {}
    status = header.get("status") or {}
    if status.get("started") and status.get("finished"):
        bullets.append(f"Resultado FotMob: {status.get('scoreStr','—')}")
    # 2. Form
    teams_form = (payload.get("content") or {}).get("h2h") or {}
    if isinstance(teams_form, dict):
        last5_home = teams_form.get("teams", [{}])[0].get("form")
        last5_away = teams_form.get("teams", [{}])[-1].get("form")
        if last5_home:
            bullets.append(f"Forma local (FotMob): {clean_text(' '.join(last5_home[:5]))}")
        if last5_away:
            bullets.append(f"Forma visitante (FotMob): {clean_text(' '.join(last5_away[:5]))}")
    # 3. Lineups
    lineup = (payload.get("content") or {}).get("lineup") or {}
    if lineup and (lineup.get("lineup") or lineup.get("matchFacts")):
        bullets.append("Alineaciones probables disponibles en FotMob.")
    # 4. Injuries / suspensions
    inj = (payload.get("content") or {}).get("playerInjuries") or []
    if inj and isinstance(inj, list):
        first_few = [clean_text(str(i.get("player") or i.get("name") or ""))[:60] for i in inj[:3] if i]
        if first_few:
            bullets.append("Bajas reportadas en FotMob: " + ", ".join(filter(None, first_few)))
    # 5. Predictions widget (publicly visible)
    pred = (payload.get("content") or {}).get("matchFacts", {}).get("predictions") or {}
    if pred and isinstance(pred, dict):
        p_home = pred.get("homeWin")
        p_draw = pred.get("draw")
        p_away = pred.get("awayWin")
        if all(isinstance(x, (int, float)) for x in (p_home, p_draw, p_away)):
            bullets.append(f"Predicción FotMob: L{p_home}% / X{p_draw}% / V{p_away}%")

    evidence_type = "news_context"
    if inj:                  evidence_type = "injuries"
    elif lineup:             evidence_type = "probable_lineups"
    elif teams_form:         evidence_type = "recent_form"
    return bullets, evidence_type


async def fetch(home: str, away: str, *, league: str = "", sport: str = "football", **_) -> dict:
    if sport not in APPLICABLE_SPORTS:
        return skipped_evidence(NAME, reason="sport_not_supported")
    if not brightdata_available():
        return skipped_evidence(NAME, reason="brightdata_not_configured")
    try:
        mid = await _resolve_match_id(home, away)
        if not mid:
            return failed_evidence(NAME, reason="match_id_not_resolved")
        body = await brightdata_fetch(_MATCH_URL.format(mid=mid))
        if not body:
            return failed_evidence(NAME, reason="match_details_blocked")
        try:
            payload = json.loads(body)
        except Exception as exc:
            return failed_evidence(NAME, reason=f"json_parse:{exc}"[:120])
        bullets, etype = _bullets_from_match(payload)
        url = f"https://www.fotmob.com/match/{mid}"
        return make_evidence(
            NAME, url=url,
            title=(payload.get("header") or {}).get("teams", [{}])[0].get("name", "") or None,
            evidence_type=etype,
            extracted_data=bullets,
            confidence=78 if bullets else 40,
            freshness="fresh",
        )
    except Exception as exc:
        log.warning("fotmob.fetch crashed: %s", exc)
        return failed_evidence(NAME, reason=f"unexpected:{exc}"[:120])
