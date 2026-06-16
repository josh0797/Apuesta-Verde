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
from .football_world_cup_aliases import is_world_cup

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

    # F94.2 — Senior FIFA World Cup detector. Takes precedence over the
    # standard tier ladder so the tournament can NEVER be classified as
    # EXOTIC / LOW_PRIORITY, regardless of how the provider tags it.
    is_wc = bool(
        f.get("_is_world_cup")
        or is_world_cup(league_name, country=league_country)
    )

    secondary: list[str] = []
    analysis_status = "ANALYZABLE"
    discard_reason: Optional[str] = None
    competition_meta: Optional[dict] = None

    if is_wc:
        # F94.2 hard bypass — World Cup is always ANALYZABLE.
        # The downstream odds / sportytrader cascade may still flag it
        # as "VISIBLE_PENDING_MARKET" once we evaluate market presence
        # below, but the fixture is NEVER hidden by the visibility filter.
        competition_meta = dict(meta) if meta else {
            "tier":           "tier_1",
            "priority":       100,
            "canonical_name": "FIFA World Cup",
            "type":           "international",
            "region":         league_country or "World",
            "_synthetic_world_cup": True,
        }
    elif meta and meta.get("tier") in fc.ALLOWED_TIERS:
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

    # F94.2 — World Cup pending market signal. The fixture is ALWAYS
    # visible+analyzable; this just tells the UI to render the manual
    # odds CTA (F93-style) instead of waiting silently for sportytrader.
    if is_wc and (not league_id or "SPORTYTRADER_NOT_FOUND" in secondary):
        secondary.append("VISIBLE_PENDING_MARKET")

    return {
        "visibility_status": "VISIBLE",
        "analysis_status":   analysis_status,
        "discard_reason":    discard_reason,
        "secondary_reasons": secondary,
        "competition_meta":  competition_meta,
        "_is_national_team": bool(is_nt_by_id or is_nt_by_detector),
        "_is_world_cup":     is_wc,
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
        # F94.2 — surface World Cup flag so the UI can pin/highlight it
        # and render the manual-odds CTA when VISIBLE_PENDING_MARKET.
        "_is_world_cup":     classification.get("_is_world_cup", False),
        "_external_source":  f.get("_external_source") or f.get("_discovery_source"),
    }


async def _thestatsapi_world_cup_fallback(
    client: Optional[httpx.AsyncClient], db,
) -> tuple[list[dict], dict]:
    """F94.2 — TheStatsAPI fallback for World Cup live fixtures only.

    Called when API-Football's live feed does NOT contain any World Cup
    fixture but the tournament is presumed to be in progress (e.g. the
    user-reported case ``Iran vs New Zealand``). We query TheStatsAPI's
    live endpoint, filter through :func:`is_world_cup`, and normalise
    each match into the API-Football shape so downstream code is
    provider-agnostic.

    Returns ``(world_cup_fixtures, diag)`` — diag carries:
      provider, status, raw_count, reason, endpoint, http_status,
      sample_payload_keys.

    Never raises (fail-soft).
    """
    diag: dict = {
        "provider":            "thestatsapi",
        "status":              "SKIPPED",
        "raw_count":           0,
        "reason":              "DISABLED_OR_PRIMARY_OK",
        "endpoint":            "/football/matches?status=live",
        "http_status":         None,
        "sample_payload_keys": [],
    }
    try:
        from .external_sources import thestatsapi_client as ts_client
        from .external_sources import thestatsapi_normalizer as ts_norm
    except Exception as exc:
        diag.update(status="ERROR", reason=f"IMPORT_FAILED: {exc}")
        return ([], diag)

    if not ts_client.is_enabled():
        diag.update(status="DISABLED", reason="THESTATSAPI_DISABLED")
        return ([], diag)

    try:
        raw = await ts_client.fetch_live_matches(client)
    except Exception as exc:
        diag.update(status="ERROR", reason=f"FETCH_FAILED: {exc}")
        return ([], diag)

    diag["raw_count"] = len(raw or [])
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        diag["sample_payload_keys"] = sorted(list(raw[0].keys()))[:20]

    if not raw:
        diag.update(status="EMPTY", reason="ADAPTER_RETURNED_EMPTY")
        return ([], diag)

    try:
        normalised = ts_norm.normalize_matches(raw, competitions_index={})
    except Exception as exc:
        diag.update(status="ERROR", reason=f"NORMALIZE_FAILED: {exc}")
        return ([], diag)

    wc_fixtures: list[dict] = []
    for fx in normalised or []:
        if not isinstance(fx, dict):
            continue
        league = (fx.get("league") or {}) if isinstance(fx.get("league"), dict) else {}
        if is_world_cup(league.get("name"), country=league.get("country")):
            fx["_is_world_cup"] = True
            fx["_external_source"] = fx.get("_external_source") or "thestatsapi"
            fx["_discovery_source"] = "thestatsapi_world_cup_fallback"
            wc_fixtures.append(fx)

    diag.update(
        status="OK" if wc_fixtures else "NO_WORLD_CUP_LIVE",
        reason="WORLD_CUP_FOUND" if wc_fixtures else "NO_WORLD_CUP_IN_FEED",
    )
    diag["world_cup_count"] = len(wc_fixtures)
    return (wc_fixtures, diag)


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
            # F94.2 — explicit World Cup audit counters.
            "world_cup_live_detected":       bool,
            "world_cup_live_count":          int,
            "world_cup_hidden_by_filter":    int,   # must be 0 by contract
            "world_cup_examples":            list[str],  # up to 8
            "world_cup_fallback_used":       bool,
            "thestatsapi_diag": {                   # diagnostic only
              "provider": "thestatsapi",
              "status":   "OK|EMPTY|DISABLED|ERROR|SKIPPED_PRIMARY_HAS_WC|NO_WORLD_CUP_LIVE",
              "raw_count": int,
              "reason":   str,
              "endpoint": str,
              "http_status": int | None,
              "sample_payload_keys": list[str],
              "world_cup_count": int | None,
            } | None,
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
        # F94.2 — explicit World Cup audit counters.
        "world_cup_live_detected":       False,
        "world_cup_live_count":          0,
        "world_cup_hidden_by_filter":    0,
        "world_cup_examples":            [],
        "world_cup_fallback_used":       False,
        "thestatsapi_diag":              None,
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

    # F94.2 — Check if API-Football's live feed already contains a
    # senior World Cup match. If not, ask TheStatsAPI as a targeted
    # fallback. This is scoped to World Cup ONLY for this sprint per
    # the user spec.
    primary_has_wc = False
    for f in live_raw or []:
        if not isinstance(f, dict):
            continue
        league = (f.get("league") or {}) if isinstance(f.get("league"), dict) else {}
        if is_world_cup(league.get("name"), country=league.get("country")):
            primary_has_wc = True
            break

    if not primary_has_wc:
        wc_extra, wc_diag = await _thestatsapi_world_cup_fallback(client, db)
        debug["thestatsapi_diag"] = wc_diag
        if wc_extra:
            debug["world_cup_fallback_used"] = True
            # Append (provider already tagged so dedupe upstream is safe).
            live_raw = list(live_raw or []) + wc_extra
    else:
        debug["thestatsapi_diag"] = {
            "provider":  "thestatsapi",
            "status":    "SKIPPED_PRIMARY_HAS_WC",
            "reason":    "API_FOOTBALL_ALREADY_RETURNED_WORLD_CUP",
            "raw_count": 0,
            "endpoint":  "/football/matches?status=live",
            "http_status": None,
            "sample_payload_keys": [],
        }

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
                "_is_world_cup":     False,
            }
        flat = _flatten_fixture(f, classification)
        items.append(flat)
        status = classification["analysis_status"]
        by_status[status] = by_status.get(status, 0) + 1
        rc = classification["discard_reason"]
        if rc:
            by_reason[rc] = by_reason.get(rc, 0) + 1

        # F94.2 — World Cup audit counters.
        if classification.get("_is_world_cup"):
            debug["world_cup_live_count"] += 1
            debug["world_cup_live_detected"] = True
            if status == "DISCARDED":
                # This should NEVER happen with the bypass in place; if
                # it does, surface it so we catch regressions early.
                debug["world_cup_hidden_by_filter"] += 1
            teams = flat.get("teams") or {}
            h = (teams.get("home") or {}).get("name") or "?"
            a = (teams.get("away") or {}).get("name") or "?"
            if len(debug["world_cup_examples"]) < 8:
                debug["world_cup_examples"].append(f"{h} vs {a}")

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
    "_thestatsapi_world_cup_fallback",  # exposed for tests
]
