"""Sprint-D8-Fase2 (cascada) · TheSportsDB → fixtures discovery adapter.

Decisión del usuario: **TheSportsDB primario** para descubrimiento de
fixtures de fútbol (sustituye a TheStatsAPI como Step 0 en
``_discover_football_fixtures``). The Odds API se mantiene como
secundario **solo para enrichment de odds** (no de fixtures), y
API-Sports (api_football) es el último fallback.

Este módulo:
  * Llama ``thesportsdb_client.fetch_upcoming_events_by_date`` para
    la fecha de hoy en UTC y mañana (para cubrir el rollover de
    medianoche en zonas horarias del usuario).
  * Convierte cada evento al shape compacto ``{home_team, away_team,
    league, kickoff_iso, status}`` que ``ensure_api_football_fixture_shape``
    consume y normaliza al contrato API-Football final.
  * Fail-soft total: cualquier excepción se atrapa y se reporta en
    ``reason_codes``; nunca raise.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx

from . import thesportsdb_client as tsdb

log = logging.getLogger("services.external_sources.thesportsdb_fixtures_adapter")

RC_OK              = "THESPORTSDB_FIXTURES_OK"
RC_DISABLED        = "THESPORTSDB_DISABLED"
RC_EMPTY           = "THESPORTSDB_FIXTURES_EMPTY"
RC_EXCEPTION       = "THESPORTSDB_FIXTURES_EXCEPTION"
RC_FILTERED_FINISHED = "THESPORTSDB_FILTERED_FINISHED_OR_LIVE"


# Mapeo de status TheSportsDB → API-Football short codes canónicos.
# Lo crítico: cualquier estado NO presente en VALID_UPCOMING_STATUSES se
# descarta antes de entrar a la cascada (evita que Ecuador vs Curaçao,
# Tunisia vs Japan, etc. aparezcan como upcoming cuando ya terminaron).
_STATUS_MAP_TSDB = {
    # Upcoming válidos
    "Not Started":   "NS",
    "NS":            "NS",
    "TBD":           "TBD",
    "Time to be defined": "TBD",
    "Postponed":     "PST",
    "PST":           "PST",
    # Live (descartar — no son upcoming)
    "1H": "1H", "First Half": "1H",
    "HT": "HT", "Halftime": "HT",
    "2H": "2H", "Second Half": "2H",
    "ET": "ET", "Extra Time": "ET",
    "BT": "BT", "Break Time": "BT",
    "P":  "P",  "Penalty In Progress": "P",
    "SUSP": "SUSP", "Suspended": "SUSP",
    "INT": "INT", "Interrupted": "INT",
    "LIVE": "1H",
    # Finished / cancelled (descartar — partidos ya jugados)
    "FT": "FT", "Match Finished": "FT", "Finished": "FT", "Full Time": "FT",
    "AET": "AET", "After Extra Time": "AET",
    "PEN": "PEN", "Penalty Shootout": "PEN",
    "AWD": "AWD", "Technical Loss": "AWD",
    "CANC": "CANC", "Cancelled": "CANC", "Canceled": "CANC",
    "ABD": "ABD", "Abandoned": "ABD",
    "WO":  "WO",  "Walkover": "WO",
}

VALID_UPCOMING_STATUSES: frozenset[str] = frozenset({"NS", "TBD"})


def _canonical_status(raw_status: Optional[str]) -> str:
    """Mapea un status crudo TheSportsDB al short code API-Football."""
    if not raw_status:
        return "NS"
    return _STATUS_MAP_TSDB.get(raw_status.strip(), raw_status.strip())


def _today_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _tomorrow_utc_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")


def _build_kickoff_iso(date_event: Optional[str],
                         event_time: Optional[str],
                         timestamp: Optional[str]) -> Optional[str]:
    """Compose an ISO-8601 kickoff timestamp.

    Prefer ``strTimestamp`` when present (TheSportsDB usually offers it
    with no timezone); fall back to combining ``dateEvent + strTime``
    as UTC.
    """
    if timestamp:
        ts = timestamp.strip()
        if "T" not in ts and " " in ts:
            ts = ts.replace(" ", "T", 1)
        if not ts.endswith("Z") and "+" not in ts and "-" not in ts[10:]:
            ts = ts + "Z"
        return ts
    if date_event:
        t = (event_time or "00:00:00").strip()
        # Some payloads use "HH:MM" — pad with seconds.
        if t.count(":") == 1:
            t = t + ":00"
        return f"{date_event}T{t}Z"
    return None


def _normalize_to_apifootball_shape(ev: dict) -> Optional[dict]:
    """Map TheSportsDB canonical event → minimal API-Football shape.

    Returns ``None`` cuando el status indica que el partido ya terminó
    (FT, AET, PEN, CANC, ABD) o está en vivo (1H, HT, 2H, ET, …). Esto
    evita que upcoming-fixtures discovery emita partidos jugados — el bug
    histórico ``Ecuador vs Curaçao status=FT`` apareciendo como upcoming.
    """
    home = ev.get("home_team") or {}
    away = ev.get("away_team") or {}
    home_name = (home.get("name") or "").strip()
    away_name = (away.get("name") or "").strip()
    if not home_name or not away_name:
        return None
    # ── Filtro de status: solo NS / TBD pasan como upcoming ──
    status_canon = _canonical_status(ev.get("status"))
    if status_canon not in VALID_UPCOMING_STATUSES:
        log.debug("[thesportsdb_fixtures] drop %s vs %s status=%s",
                   home_name, away_name, status_canon)
        return None
    league_name = ev.get("league_name") or ""
    league_id   = ev.get("league_id")
    kickoff_iso = _build_kickoff_iso(ev.get("date_event"),
                                       ev.get("event_time"),
                                       ev.get("timestamp"))
    return {
        "fixture": {
            "id":        ev.get("event_id"),
            "date":      kickoff_iso,
            "timestamp": None,  # FFC will derive from .date if missing
            "status": {
                "short": status_canon,
                "long":  "Not Started" if status_canon == "NS" else status_canon,
            },
            "venue": {"name": None, "city": None},
        },
        "league": {
            "id":   league_id,
            "name": league_name,
            "country": None,
            "season": ev.get("season"),
        },
        "teams": {
            "home": {"id": home.get("id"), "name": home_name,
                     "logo": home.get("badge")},
            "away": {"id": away.get("id"), "name": away_name,
                     "logo": away.get("badge")},
        },
        "_discovery_source": "thesportsdb",
    }


async def fetch_fixtures_next_48h(
    client: Optional[httpx.AsyncClient] = None,
    *,
    sport: str = "Soccer",
) -> tuple[list[dict], list[str]]:
    """Return ``(fixtures, reason_codes)`` for today + tomorrow (UTC).

    Never raises. ``fixtures`` is a list of dicts in the API-Football
    shape, ready for ``ensure_api_football_fixture_shape``.
    """
    if not tsdb.is_enabled():
        return [], [RC_DISABLED]

    fixtures: list[dict] = []
    codes: list[str] = []
    own_client = False
    filtered_count = 0
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        own_client = True
    try:
        for d in (_today_utc_iso(), _tomorrow_utc_iso()):
            try:
                res = await tsdb.fetch_upcoming_events_by_date(
                    date=d, sport=sport, client=client,
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("[thesportsdb_fixtures] %s raised: %s", d, exc)
                codes.append(RC_EXCEPTION)
                continue
            codes.extend(res.get("reason_codes") or [])
            if not res.get("available"):
                continue
            for it in res.get("items") or []:
                fx = _normalize_to_apifootball_shape(it)
                if fx is None:
                    filtered_count += 1
                    continue
                fixtures.append(fx)
        if filtered_count > 0:
            log.info(
                "[thesportsdb_fixtures] filtered %d finished/live events "
                "(only NS/TBD kept as upcoming)", filtered_count,
            )
            codes.append(f"{RC_FILTERED_FINISHED}={filtered_count}")
        if not fixtures:
            codes.append(RC_EMPTY)
        else:
            codes.append(RC_OK)
    finally:
        if own_client:
            await client.aclose()
    return fixtures, codes


__all__ = [
    "fetch_fixtures_next_48h",
    "_normalize_to_apifootball_shape",
    "_canonical_status",
    "VALID_UPCOMING_STATUSES",
    "RC_OK", "RC_DISABLED", "RC_EMPTY", "RC_EXCEPTION",
    "RC_FILTERED_FINISHED",
]
