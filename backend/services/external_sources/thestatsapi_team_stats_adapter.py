"""Phase F84.a — TheStatsAPI ``/football/teams/{id}/stats`` adapter.

Fetches team season stats from TheStatsAPI and reshapes the response so
it is **drop-in compatible** with the API-Sports v3
``/teams/statistics`` payload consumed by
:func:`services.normalizer.normalize_team_context`. This is the first
step of the prioridad-inversa migration (TheStatsAPI primaria,
API-Sports fallback detrás de flag ``ENABLE_API_SPORTS_FALLBACK``).

TheStatsAPI response (per `/docs/api-reference/teams/stats`)::

    {
      "data": {
        "team_id":        "tm_8923",
        "season_id":      "sn_7210",
        "competition_id": "comp_3879",
        "matches_played": 38,
        "wins":           23,
        "draws":           6,
        "losses":          9,
        "points":         75,
        "position":        3,
        "goals_for":      58,
        "goals_against":  43,
        "goal_difference":15,
        "form":           "WWDLW"
      }
    }

API-Sports shape this module emits::

    {
      "form": "WWDLW",
      "goals": {
        "for":     {"total": {"total": 58}, "average": {"total": 1.53}},
        "against": {"total": {"total": 43}, "average": {"total": 1.13}},
      },
      "fixtures": {
        "played": {"total": 38},
        "wins":   {"total": 23},
        "draws":  {"total":  6},
        "loses":  {"total":  9},
      },
      "clean_sheet":      {"total": None},
      "failed_to_score":  {"total": None},
      "_provenance": {
        "source":         "thestatsapi",
        "endpoint":       "/football/teams/{id}/stats",
        "competition_id": "comp_3879",
        "season_id":      "sn_7210",
      },
    }

Fail-soft contract: on **any** error (disabled, missing IDs, 404, 5xx,
timeout, malformed payload) the function returns ``{}`` — the same
empty shape ``api_football.team_statistics`` returns when its call
fails. The orchestrator decides whether to fall back to API-Sports
based on the ``ENABLE_API_SPORTS_FALLBACK`` flag.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from . import thestatsapi_client as _ts

log = logging.getLogger("thestatsapi_team_stats_adapter")

# Reason codes — surfaced through logging only; the public return is
# either a fully-shaped stats dict or ``{}`` so the orchestrator branch
# stays simple. Strings are stable across releases for downstream log
# parsing.
RC_DISABLED          = "RC_THESTATSAPI_DISABLED"
RC_MISSING_TEAM_ID   = "RC_MISSING_TEAM_ID"
RC_MISSING_SEASON_ID = "RC_MISSING_SEASON_ID"
RC_NETWORK_ERROR     = "RC_NETWORK_ERROR"
RC_EMPTY_RESPONSE    = "RC_EMPTY_RESPONSE"
RC_BUILD_FAILED      = "RC_BUILD_FAILED"
RC_OK                = "RC_OK"


def _is_enabled() -> bool:
    """Mirror the cliente's own gate so adapters can short-circuit early."""
    return _ts.is_enabled()


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_div(num: Any, den: Any) -> Optional[float]:
    n = _safe_int(num)
    d = _safe_int(den)
    if n is None or not d:
        return None
    try:
        return round(n / d, 3)
    except (TypeError, ZeroDivisionError):
        return None


def _build_api_sports_shape(
    raw: dict,
    *,
    team_id_internal: Any = None,
    season_id_internal: Any = None,
) -> dict:
    """Transform a TheStatsAPI ``team stats`` payload into the
    API-Sports-compatible shape expected by
    :func:`normalize_team_context`."""
    if not isinstance(raw, dict) or not raw:
        return {}

    played = _safe_int(raw.get("matches_played"))
    gf     = _safe_int(raw.get("goals_for"))
    ga     = _safe_int(raw.get("goals_against"))

    out: dict[str, Any] = {
        # API-Sports puts `form` at the root; TheStatsAPI matches.
        "form": raw.get("form") or "",
        "goals": {
            "for": {
                "total":   {"total": gf if gf is not None else None},
                "average": {"total": _safe_div(gf, played)},
            },
            "against": {
                "total":   {"total": ga if ga is not None else None},
                "average": {"total": _safe_div(ga, played)},
            },
        },
        "fixtures": {
            "played": {"total": played if played is not None else 0},
            "wins":   {"total": _safe_int(raw.get("wins"))   or 0},
            "draws":  {"total": _safe_int(raw.get("draws"))  or 0},
            "loses":  {"total": _safe_int(raw.get("losses")) or 0},
        },
        # TheStatsAPI does NOT expose clean_sheet / failed_to_score per
        # season — we surface them as ``{"total": None}`` so downstream
        # code that does ``or 0`` keeps working without inventing data.
        "clean_sheet":     {"total": None},
        "failed_to_score": {"total": None},
        # League-table position + total points are surfaced in case
        # consumers want to skip a separate /standings call later. Kept
        # at the root level (NOT inside `fixtures.points` like
        # API-Sports does at the standings level) under a distinct key
        # to avoid clobbering the API-Sports shape during the merge.
        "_league_table": {
            "position":         _safe_int(raw.get("position")),
            "points":           _safe_int(raw.get("points")),
            "goal_difference":  _safe_int(raw.get("goal_difference")),
        },
        # Provenance — read by `provenance.attach_to_match` and the
        # F84 audit trail so we can confirm at runtime which source
        # served each section.
        "_provenance": {
            "source":           "thestatsapi",
            "endpoint":         "/football/teams/{id}/stats",
            "team_id":          raw.get("team_id"),
            "season_id":        raw.get("season_id"),
            "competition_id":   raw.get("competition_id"),
            "team_id_internal":   team_id_internal,
            "season_id_internal": season_id_internal,
        },
    }
    return out


async def fetch_team_season_stats(
    client: httpx.AsyncClient,
    *,
    team_id_thestatsapi: Optional[str],
    season_id_thestatsapi: Optional[str] = None,
    season: Optional[int | str] = None,
    competition_id: Optional[str] = None,
    team_id_internal: Any = None,
) -> dict:
    """Return team season stats from TheStatsAPI, shaped like
    API-Sports v3 ``/teams/statistics``.

    Parameters
    ----------
    client
        Live ``httpx.AsyncClient``. Tests can pass one backed by a
        ``MockTransport`` (see ``test_f84_a_team_stats_adapter.py``).
    team_id_thestatsapi
        The TheStatsAPI team ID (string like ``tm_8923``). Required.
    season_id_thestatsapi
        The TheStatsAPI season ID (string like ``sn_7210``). If absent,
        the numeric ``season`` is forwarded as-is and TheStatsAPI's
        client layer decides how to query (legacy compatibility).
    season
        Numeric season year used by API-Sports (e.g. ``2024``). Only
        used when ``season_id_thestatsapi`` is missing.
    competition_id
        Optional TheStatsAPI competition ID (passed through as
        ``competition_id`` query param).
    team_id_internal
        The original API-Sports / internal int ID — embedded in the
        provenance block for audit only.

    Returns
    -------
    dict
        The API-Sports-shaped stats block, or ``{}`` on any failure.
    """
    if not _is_enabled():
        log.debug("[%s] %s", RC_DISABLED, team_id_thestatsapi)
        return {}
    if not team_id_thestatsapi:
        log.debug("[%s] team_id_internal=%s", RC_MISSING_TEAM_ID, team_id_internal)
        return {}
    # The endpoint's docs flag `season_id` as REQUIRED. We prefer the
    # TheStatsAPI-native ID but accept the legacy numeric `season`
    # parameter so the orchestrator can keep its single call signature.
    season_param: Any = season_id_thestatsapi or season
    if season_param is None:
        log.debug("[%s] team=%s", RC_MISSING_SEASON_ID, team_id_thestatsapi)
        return {}

    try:
        raw = await _ts.fetch_team_stats(
            client,
            team_id_thestatsapi,
            sport="football",
            season=season_param,
            competition_id=competition_id,
        )
    except Exception as exc:
        log.warning("[%s] team=%s err=%s", RC_NETWORK_ERROR,
                    team_id_thestatsapi, exc)
        return {}

    if not isinstance(raw, dict) or not raw:
        log.debug("[%s] team=%s", RC_EMPTY_RESPONSE, team_id_thestatsapi)
        return {}

    try:
        shaped = _build_api_sports_shape(
            raw,
            team_id_internal=team_id_internal,
            season_id_internal=season,
        )
    except Exception as exc:
        log.exception("[%s] team=%s err=%s", RC_BUILD_FAILED,
                       team_id_thestatsapi, exc)
        return {}

    if not shaped:
        log.debug("[%s] team=%s (empty after shape)",
                  RC_EMPTY_RESPONSE, team_id_thestatsapi)
        return {}

    log.debug("[%s] team=%s shaped form=%s played=%s",
              RC_OK, team_id_thestatsapi,
              shaped.get("form"),
              (shaped.get("fixtures") or {}).get("played", {}).get("total"))
    return shaped


__all__ = [
    "fetch_team_season_stats",
    "_build_api_sports_shape",  # exported for tests
    "RC_DISABLED", "RC_MISSING_TEAM_ID", "RC_MISSING_SEASON_ID",
    "RC_NETWORK_ERROR", "RC_EMPTY_RESPONSE", "RC_BUILD_FAILED", "RC_OK",
]
