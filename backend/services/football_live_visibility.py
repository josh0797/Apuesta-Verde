"""F94 — Football Live Visibility (independiente de filtros de prioridad).

Single source of truth for *all* football live fixtures the provider
returns, regardless of competition tier / league priority / sportytrader
identity / market availability.

Visibility != Ranking. The dashboard currently shows ``EN CURSO AHORA: 0``
because :func:`services.data_ingestion.ingest_live` filters live raw rows
by ``ALLOWED_TIERS`` and the national-team detector, persisting only the
"analyzable" subset to ``db.matches``. That is correct for analysis
prioritisation, but it makes exotic / friendly / low-priority live matches
invisible.

This module:
  * Pulls the raw live feed from the football aggregator (no filters).
  * Computes ``visibility_status`` (always ``"VISIBLE"``) +
    ``analysis_status`` (``"ANALYZABLE"`` | ``"DISCARDED"``) +
    ``discard_reason`` + ``secondary_reasons``.
  * Returns a ``live_debug`` block with the per-stage counts.
  * NEVER raises (fail-soft).

The endpoint :func:`server.football_live_visibility` consumes it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from . import football_competitions as fc
from .api_sports import is_national_team_league
from .external_sources import national_team_detector as ntd

log = logging.getLogger("services.football_live_visibility")


def _fx_league(f: dict) -> dict:
    league = f.get("league") if isinstance(f, dict) else None
    return league if isinstance(league, dict) else {}


def _fx_teams(f: dict) -> tuple[Optional[str], Optional[str]]:
    teams = f.get("teams") if isinstance(f, dict) else None
    if not isinstance(teams, dict):
        return (None, None)
    h = teams.get("home") or {}
    a = teams.get("away") or {}
    home = h.get("name") if isinstance(h, dict) else None
    away = a.get("name") if isinstance(a, dict) else None
    return (home, away)


def classify_live_fixture(f: dict) -> dict:
    """Return ``{discard_reason, secondary_reasons, analysis_status,
    competition_meta}`` for a raw live fixture.

    Mirror of the logic inside ``ingest_live`` but with the *decision*
    flipped: the fixture is never dropped — only annotated.

    Reasons surfaced:
      * ``EXOTIC_LEAGUE``      — league is not in ALLOWED_TIERS and not a
        recognised national-team competition.
      * ``LOW_PRIORITY_LEAGUE`` — competition meta exists but its tier
        falls below ALLOWED_TIERS.
      * ``NO_MARKET_IDENTITY``  — competition meta unknown AND the
        fixture has no league_id / league_name we can normalise.
      * ``SPORTYTRADER_NOT_FOUND`` — placeholder; the upstream enrich
        cascade would fail to attach a SportyTrader card. We approximate
        by flagging fixtures missing ``league.id`` since SportyTrader
        lookups require it.
    """
    league       = _fx_league(f)
    league_name  = (league.get("name") or "").strip() if league else ""
    league_id    = league.get("id") if league else None
    league_country = league.get("country") if league else None

    meta = fc.get_competition_meta(league_name) if league_name else None
    is_nt_by_id = bool(league_id and is_national_team_league(league_id))

    home_name, away_name = _fx_teams(f)
    is_nt_by_detector = bool(
        f.get("_is_national_team")
        or ntd.is_national_team_match(
            home_name=home_name, away_name=away_name,
            league_name=league_name, league_country=league_country,
        )
    )

    secondary: list[str] = []
    analysis_status = "ANALYZABLE"
    discard_reason: Optional[str] = None
    competition_meta: Optional[dict] = None

    if meta and meta.get("tier") in fc.ALLOWED_TIERS:
        competition_meta = dict(meta)
    elif is_nt_by_id or is_nt_by_detector:
        competition_meta = {
            "tier":           "tier_2",
            "priority":       72,
            "canonical_name": league_name or "National Team Competition",
            "type":           "international",
            "region":         league_country or "World",
            "_synthetic_national_team": True,
            "_detector_source": (
                "id" if is_nt_by_id else "national_team_detector"
            ),
        }
    elif meta and meta.get("tier") not in fc.ALLOWED_TIERS:
        analysis_status = "DISCARDED"
        discard_reason  = "LOW_PRIORITY_LEAGUE"
        competition_meta = dict(meta)
    elif not league_name and not league_id:
        analysis_status = "DISCARDED"
        discard_reason  = "NO_MARKET_IDENTITY"
        secondary.append("MISSING_LEAGUE_FIELDS")
    else:
        analysis_status = "DISCARDED"
        discard_reason  = "EXOTIC_LEAGUE"

    # SportyTrader proxy: lookups in our enrich cascade key off league_id;
    # without one, sportytrader card is guaranteed to miss.
    if not league_id:
        secondary.append("SPORTYTRADER_NOT_FOUND")

    if not league_name:
        secondary.append("LEAGUE_NAME_MISSING")
    if not (home_name and away_name):
        secondary.append("TEAM_NAMES_MISSING")

    return {
        "visibility_status": "VISIBLE",
        "analysis_status":   analysis_status,
        "discard_reason":    discard_reason,
        "secondary_reasons": secondary,
        "competition_meta":  competition_meta,
        "_is_national_team": bool(is_nt_by_id or is_nt_by_detector),
    }


def _flatten_fixture(f: dict, classification: dict) -> dict:
    """Project a raw aggregator fixture into a lightweight visibility row
    suitable for the UI. Keeps the original ``f`` untouched."""
    league  = _fx_league(f)
    teams   = f.get("teams") or {}
    fixture = f.get("fixture") or {}
    status_block = (fixture.get("status") or {}) if isinstance(fixture, dict) else {}
    home_block   = teams.get("home") or {}
    away_block   = teams.get("away") or {}

    return {
        "fixture_id":     fixture.get("id") if isinstance(fixture, dict) else (f.get("id")),
        "kickoff_iso":    fixture.get("date") if isinstance(fixture, dict) else f.get("date"),
        "kickoff_ts":     fixture.get("timestamp") if isinstance(fixture, dict) else f.get("timestamp"),
        "status_short":   status_block.get("short") or f.get("status_short") or "LIVE",
        "elapsed":        status_block.get("elapsed") or fixture.get("elapsed") if isinstance(fixture, dict) else None,
        "league": {
            "id":      league.get("id"),
            "name":    league.get("name"),
            "country": league.get("country"),
        },
        "teams": {
            "home": {"id": home_block.get("id"), "name": home_block.get("name")},
            "away": {"id": away_block.get("id"), "name": away_block.get("name")},
        },
        # F94 — visibility annotations.
        "visibility_status": classification["visibility_status"],
        "analysis_status":   classification["analysis_status"],
        "discard_reason":    classification["discard_reason"],
        "secondary_reasons": classification["secondary_reasons"],
        "competition_meta":  classification["competition_meta"],
        "_is_national_team": classification["_is_national_team"],
        "_external_source":  f.get("_external_source") or f.get("_discovery_source"),
    }


async def compute_football_live_visibility(
    client: Optional[httpx.AsyncClient], db,
) -> dict:
    """F94 — Build the visibility payload for the football live tab.

    Always returns the contract::

        {
          "ok": True,
          "items": [ ...visible fixtures, exotics included... ],
          "live_debug": {
            "provider_live_count":           int,
            "after_sport_filter_count":      int,
            "after_league_filter_count":     int,
            "visible_live_count":            int,
            "analysis_eligible_live_count":  int,
            "hidden_by_priority_filter":     int,   # must be 0
          },
          "by_status_counts": {
            "ANALYZABLE":          int,
            "DISCARDED":           int,
          },
          "by_reason_counts": {
            "EXOTIC_LEAGUE":        int,
            "LOW_PRIORITY_LEAGUE":  int,
            "NO_MARKET_IDENTITY":   int,
          },
          "computed_at": ISO,
        }
    """
    debug = {
        "provider_live_count":           0,
        "after_sport_filter_count":      0,
        "after_league_filter_count":     0,
        "visible_live_count":            0,
        "analysis_eligible_live_count":  0,
        "hidden_by_priority_filter":     0,
    }
    by_status: dict[str, int] = {"ANALYZABLE": 0, "DISCARDED": 0}
    by_reason: dict[str, int] = {}
    items: list[dict] = []

    try:
        from .football_live_aggregator import fetch_live_football_fixtures
        live_raw, agg_meta = await fetch_live_football_fixtures(client, db)
    except Exception as exc:
        log.warning("[live_visibility] aggregator failed: %s", exc)
        # Fail-soft fallback to API-Sports direct.
        try:
            from . import api_sports as aps
            live_raw = await aps.fixtures_live("football", client)
            agg_meta = {"fallback": "api_sports_direct"}
        except Exception as exc2:
            log.error("[live_visibility] api_sports fallback failed: %s", exc2)
            live_raw = []
            agg_meta = {"fallback": "empty"}

    debug["provider_live_count"] = len(live_raw or [])
    debug["after_sport_filter_count"] = debug["provider_live_count"]  # already football-only

    for f in live_raw or []:
        if not isinstance(f, dict):
            continue
        try:
            classification = classify_live_fixture(f)
        except Exception as exc:
            log.warning("[live_visibility] classify failed for %r: %s",
                        f.get("fixture") or f.get("id"), exc)
            classification = {
                "visibility_status": "VISIBLE",
                "analysis_status":   "DISCARDED",
                "discard_reason":    "CLASSIFICATION_FAILED",
                "secondary_reasons": [],
                "competition_meta":  None,
                "_is_national_team": False,
            }
        items.append(_flatten_fixture(f, classification))
        status = classification["analysis_status"]
        by_status[status] = by_status.get(status, 0) + 1
        rc = classification["discard_reason"]
        if rc:
            by_reason[rc] = by_reason.get(rc, 0) + 1

    debug["after_league_filter_count"]    = len(items)
    debug["visible_live_count"]           = len(items)
    debug["analysis_eligible_live_count"] = by_status.get("ANALYZABLE", 0)
    # F94 contract: visibility filter must NEVER hide. Always 0.
    debug["hidden_by_priority_filter"]    = 0

    return {
        "ok":           True,
        "items":        items,
        "live_debug":   debug,
        "by_status_counts": by_status,
        "by_reason_counts": by_reason,
        "agg_meta":     agg_meta,
        "computed_at":  datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "classify_live_fixture",
    "compute_football_live_visibility",
]
