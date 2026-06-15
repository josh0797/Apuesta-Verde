"""F87.b — Sofascore fixture discovery via Playwright.

Thin wrapper around the existing ``playwright_scraper.sofascore_via_playwright``.
Normalises Sofascore events into the API-Football "next-48h" shape so
``data_ingestion._discover_football_fixtures`` can merge them with
TheStatsAPI / API-Football outputs without per-source conditionals.

Public surface
--------------
* :func:`fetch_fixtures_today` — pull today's Sofascore schedule.
* :func:`_normalise_sofascore_event` — shared normaliser (also used by
  the scrape.do adapter).

Fail-soft: never raises; returns ``[]`` on any failure.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("services.sofascore_fixtures_adapter")

_INT_NAME_RX = re.compile(
    r"(?i)(world cup|nations league|conmebol|africa cup|asian cup|"
    r"copa america|euro\s|qualifi|friendl|club world|libertadores|"
    r"sudamericana|gold cup|afcon|concacaf|caf champions|"
    r"asian champions)",
)


def _flag_enabled() -> bool:
    raw = (os.environ.get("ENABLE_SOFASCORE_PW_FALLBACK") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _coerce_ts(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        try:
            return int(datetime.fromisoformat(s).timestamp())
        except (TypeError, ValueError):
            return None
    return None


def _normalise_sofascore_event(ev: dict, *,
                                source_tag: str = "sofascore_pw",
                                ) -> Optional[dict]:
    """Convert a Sofascore event dict (either from playwright_scraper or
    the raw sofascore JSON via scrape.do) into the API-Football "next-48h"
    shape consumed by ``data_ingestion``. Returns ``None`` if the row
    cannot be parsed.

    ``source_tag`` propagates into ``_external_source`` so callers can
    distinguish the playwright path from the scrape.do path.
    """
    if not isinstance(ev, dict):
        return None

    # Two shapes:
    #   playwright_scraper output: {"id": "sofa-12345", "league": "X - Y",
    #                                "kickoff_iso": ..., "home_team": {...}}
    #   raw Sofascore JSON event:  {"id": 12345, "tournament": {...},
    #                                "startTimestamp": int, "homeTeam": ...}
    raw_id = ev.get("id")
    if raw_id is None:
        return None
    if isinstance(raw_id, str) and raw_id.startswith("sofa-"):
        sofa_id = raw_id[len("sofa-"):]
    else:
        sofa_id = str(raw_id)
    canon_id = f"sofa-{sofa_id}"

    # Tournament / league name.
    league_name: str
    league_country: Optional[str] = None
    tournament = ev.get("tournament")
    if isinstance(tournament, dict):
        league_name = (tournament.get("name") or "").strip()
        cat = tournament.get("category") or {}
        if isinstance(cat, dict):
            league_country = cat.get("name") or cat.get("slug")
            if cat.get("name") and league_name and cat["name"] not in league_name:
                # Mirror playwright_scraper's "<league> - <country>" join.
                pass
    else:
        league_str = (ev.get("league") or "").strip()
        if " - " in league_str:
            league_name, _, league_country = league_str.rpartition(" - ")
            league_name = league_name.strip()
            league_country = (league_country or "").strip() or None
        else:
            league_name = league_str

    # Status + kickoff.
    ts = _coerce_ts(
        ev.get("startTimestamp")
        or ev.get("kickoff_ts")
        or ev.get("timestamp")
    )
    iso = ev.get("kickoff_iso")
    if not iso and ts is not None:
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    if ts is None and isinstance(iso, str):
        ts = _coerce_ts(iso)
    if ts is None or iso is None:
        return None

    raw_status = ev.get("status")
    if isinstance(raw_status, dict):
        stype = (raw_status.get("type") or "").lower()
        if stype == "inprogress":
            short = "1H"
        elif stype == "finished":
            short = "FT"
        else:
            short = "NS"
    else:
        is_live = bool(ev.get("is_live"))
        short = "1H" if is_live else "NS"

    # Teams.
    home_raw = ev.get("home_team") or ev.get("homeTeam") or {}
    away_raw = ev.get("away_team") or ev.get("awayTeam") or {}
    if not isinstance(home_raw, dict):
        home_raw = {"name": str(home_raw)}
    if not isinstance(away_raw, dict):
        away_raw = {"name": str(away_raw)}
    home_name = home_raw.get("name") or "Home"
    away_name = away_raw.get("name") or "Away"

    is_intl = bool(_INT_NAME_RX.search(league_name or ""))
    is_nt = bool(re.search(
        r"(?i)(world cup|nations league|copa america|africa cup|"
        r"euro\s|gold cup|asian cup|qualifi|friendl)",
        league_name or "",
    ))

    return {
        "id":        canon_id,
        "fixture": {
            "id":        canon_id,
            "date":      iso,
            "timestamp": ts,
            "status":    {"short": short, "long": short},
            "venue":     {"name": None, "city": None},
        },
        "league": {
            "id":      None,
            "name":    league_name or "",
            "country": league_country,
        },
        "teams": {
            "home": {"id": None, "name": home_name},
            "away": {"id": None, "name": away_name},
        },
        "_external_source":    source_tag,
        "_external_source_id": sofa_id,
        "_is_national_team":   is_nt,
        "_is_international":   is_intl,
        # Top-level mirrors.
        "date":      iso,
        "timestamp": ts,
        "status":    {"short": short},
    }


async def fetch_fixtures_today(date_iso: Optional[str] = None) -> list[dict]:
    """Fetch today's Sofascore football schedule via Playwright.

    Returns an empty list on any failure (import error, network, parsing).
    """
    if not _flag_enabled():
        return []
    try:
        from ..playwright_scraper import sofascore_via_playwright
    except Exception as exc:  # noqa: BLE001
        log.warning("[sofascore_fixtures] import failed: %s", exc)
        return []

    try:
        raw_events = await sofascore_via_playwright(date_iso=date_iso)
    except Exception as exc:  # noqa: BLE001
        log.warning("[sofascore_fixtures] playwright call failed: %s", exc)
        return []

    out: list[dict] = []
    for ev in (raw_events or []):
        normalised = _normalise_sofascore_event(ev, source_tag="sofascore_pw")
        if normalised is not None:
            out.append(normalised)
    log.info("[sofascore_fixtures] normalised %d/%d events",
              len(out), len(raw_events or []))
    return out


__all__ = [
    "fetch_fixtures_today",
    "_normalise_sofascore_event",
    "_flag_enabled",
]
