"""Phase F84.b — TheStatsAPI head-to-head adapter.

TheStatsAPI does **not** expose a dedicated ``/fixtures/headtohead``
endpoint. Instead we list matches for one team (``team_id={A}``,
``status=finished``) and filter locally for those where the opponent is
team ``B``. The shape we emit mirrors API-Sports v3
``/fixtures/headtohead`` so the consumer
(:func:`services.football_editorial_prediction._build_head_to_head`)
keeps working unchanged.

Caching
-------
H2H is expensive (paginated, two-sided filter) and rarely changes. We
delegate caching to the same Mongo collection used by
:func:`services.api_football.head_to_head` (``cache_h2h``) but with a
DIFFERENT key namespace (``provider="thestatsapi"``) so the two
providers don't clobber each other. TTL is 6 h, identical to the
existing ``CONTEXT_TTL_HOURS`` used in ``api_football``.

Fail-soft contract
------------------
On any failure (disabled, missing IDs, network, malformed response) the
function returns ``[]`` — the same empty shape ``api_football.head_to_head``
returns when it fails. The orchestrator decides whether to fall back to
API-Sports based on the ``ENABLE_API_SPORTS_FALLBACK`` flag (shared
with F84.a).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from . import thestatsapi_client as _ts
from .. import api_football as _af  # for the shared Mongo cache helpers

log = logging.getLogger("thestatsapi_h2h_adapter")

# Per-page fetch cap. TheStatsAPI maxes out at 100 (see
# /docs/api-reference/matches/list). For h2h we typically need < 50
# fixtures across years, so one page is enough for the vast majority
# of teams; we paginate when the response is full to cover edge cases
# (lengthy rivalries, eternal-derby type pairings).
PAGE_SIZE = 100
MAX_PAGES = 3                  # cap at 300 matches per team — enough for any h2h

RC_DISABLED        = "RC_THESTATSAPI_DISABLED"
RC_MISSING_TEAM_ID = "RC_MISSING_TEAM_ID"
RC_NETWORK_ERROR   = "RC_NETWORK_ERROR"
RC_EMPTY_RESPONSE  = "RC_EMPTY_RESPONSE"
RC_NO_MATCHES      = "RC_NO_HEAD_TO_HEAD_MATCHES"
RC_OK              = "RC_OK"


def _parse_iso_to_epoch(value: Any) -> Optional[int]:
    """Convert an ISO-8601 timestamp (e.g. ``"2024-01-15T15:00:00.000Z"``)
    into a UTC epoch int. Returns ``None`` on failure — used as the
    secondary sort key, falling back to lexical comparison."""
    if not isinstance(value, str) or not value:
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return int(dt.timestamp())
    except (OSError, ValueError):
        return None


def _shape_fixture_as_api_sports(
    raw: dict,
    *,
    home_team_id_internal: Any = None,
    away_team_id_internal: Any = None,
) -> Optional[dict]:
    """Convert one TheStatsAPI match dict into the API-Sports v3
    ``/fixtures/headtohead`` item shape."""
    if not isinstance(raw, dict):
        return None
    home = raw.get("home_team") or {}
    away = raw.get("away_team") or {}
    score = raw.get("score") or {}
    utc_date = raw.get("utc_date")
    ts_epoch = _parse_iso_to_epoch(utc_date)

    return {
        "fixture": {
            "id":        raw.get("id"),
            "timestamp": ts_epoch,
            "date":      utc_date,
            "status":    {"short": (raw.get("status") or "")[:2].upper() or "FT"},
        },
        "teams": {
            "home": {"id": home.get("id"), "name": home.get("name")},
            "away": {"id": away.get("id"), "name": away.get("name")},
        },
        "goals": {
            "home": score.get("home"),
            "away": score.get("away"),
        },
        "league": {
            "id":   raw.get("competition_id"),
            "name": None,
        },
        "_provenance": {
            "source":   "thestatsapi",
            "endpoint": "/football/matches",
            # Embed the original internal int IDs so the audit trail
            # can correlate without an extra lookup.
            "home_team_id_internal": home_team_id_internal,
            "away_team_id_internal": away_team_id_internal,
        },
    }


async def _list_finished_matches_for_team(
    client: httpx.AsyncClient,
    team_id_thestatsapi: str,
    *,
    page: int = 1,
    per_page: int = PAGE_SIZE,
) -> list[dict]:
    """Single-page list of finished matches for a team. Fail-soft → []."""
    params = {
        "team_id":  team_id_thestatsapi,
        "status":   "finished",
        "per_page": per_page,
        "page":     page,
    }
    try:
        data = await _ts._request(
            client, "GET", "/football/matches", params=params,
        )
    except Exception as exc:
        log.warning("[%s] team=%s page=%s err=%s",
                    RC_NETWORK_ERROR, team_id_thestatsapi, page, exc)
        return []
    if not isinstance(data, dict):
        return []
    return _ts._extract_list(
        data, candidate_keys=("matches", "data", "response", "results"),
    )


async def fetch_head_to_head(
    client: httpx.AsyncClient,
    *,
    home_team_id_thestatsapi: Optional[str],
    away_team_id_thestatsapi: Optional[str],
    limit: int = 5,
    db=None,
    home_team_id_internal: Any = None,
    away_team_id_internal: Any = None,
) -> list[dict]:
    """Return up to ``limit`` recent head-to-head matches between the
    two teams, shaped like API-Sports v3 ``/fixtures/headtohead``.

    Fail-soft contract: never raises. On any miss/error returns ``[]``.
    """
    if not _ts.is_enabled():
        log.debug("[%s]", RC_DISABLED)
        return []
    if not home_team_id_thestatsapi or not away_team_id_thestatsapi:
        log.debug("[%s] home=%s away=%s", RC_MISSING_TEAM_ID,
                  home_team_id_thestatsapi, away_team_id_thestatsapi)
        return []

    # Stable cache key (order-independent — h2h is symmetric).
    pair_key = "-".join(sorted([
        str(home_team_id_thestatsapi), str(away_team_id_thestatsapi),
    ]))
    cache_key = {"provider": "thestatsapi", "h2h_key": pair_key}
    try:
        cached = await _af._cache_get(db, "cache_h2h", cache_key,
                                       _af.CONTEXT_TTL_HOURS * 60)
        if cached is not None:
            return cached[:limit]
    except Exception:
        # Mongo cache is best-effort. Continue without it.
        pass

    # Pull finished matches for the HOME side, then filter locally.
    raw_matches: list[dict] = []
    try:
        for page in range(1, MAX_PAGES + 1):
            chunk = await _list_finished_matches_for_team(
                client, home_team_id_thestatsapi, page=page,
            )
            if not chunk:
                break
            raw_matches.extend(chunk)
            if len(chunk) < PAGE_SIZE:
                break  # last page reached
    except Exception as exc:
        log.warning("[%s] err=%s", RC_NETWORK_ERROR, exc)
        return []

    if not raw_matches:
        log.debug("[%s] home=%s", RC_EMPTY_RESPONSE, home_team_id_thestatsapi)
        return []

    away_target = str(away_team_id_thestatsapi)
    h2h: list[dict] = []
    for m in raw_matches:
        if not isinstance(m, dict):
            continue
        h_id = (m.get("home_team") or {}).get("id")
        a_id = (m.get("away_team") or {}).get("id")
        # We want fixtures where the OPPONENT is `away_team_id`,
        # regardless of which side our reference team was on.
        if str(h_id) == away_target or str(a_id) == away_target:
            shaped = _shape_fixture_as_api_sports(
                m,
                home_team_id_internal=home_team_id_internal,
                away_team_id_internal=away_team_id_internal,
            )
            if shaped:
                h2h.append(shaped)

    if not h2h:
        log.debug("[%s] home=%s away=%s scanned=%d",
                  RC_NO_MATCHES,
                  home_team_id_thestatsapi, away_team_id_thestatsapi,
                  len(raw_matches))
        # Cache the empty result too so we don't re-scan on every call
        # within the TTL. The orchestrator decides what to do next.
        try:
            await _af._cache_set(db, "cache_h2h", cache_key, [])
        except Exception:
            pass
        return []

    # Sort by epoch desc; missing timestamps go last.
    h2h.sort(
        key=lambda f: (f.get("fixture") or {}).get("timestamp") or 0,
        reverse=True,
    )

    try:
        await _af._cache_set(db, "cache_h2h", cache_key, h2h)
    except Exception:
        pass

    log.debug("[%s] pair=%s items=%d", RC_OK, pair_key, len(h2h))
    return h2h[:limit]


__all__ = [
    "fetch_head_to_head",
    "_shape_fixture_as_api_sports",
    "_list_finished_matches_for_team",
    "_parse_iso_to_epoch",
    "RC_DISABLED", "RC_MISSING_TEAM_ID", "RC_NETWORK_ERROR",
    "RC_EMPTY_RESPONSE", "RC_NO_MATCHES", "RC_OK",
    "PAGE_SIZE", "MAX_PAGES",
]
