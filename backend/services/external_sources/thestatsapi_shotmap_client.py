"""Phase F83.2-E1 — TheStatsAPI shotmap client.

Wraps ``GET /football/matches/{match_id}/shotmap`` and returns a
normalised non-penalty xG snapshot per team::

    {
      "available":    True,
      "source":       "thestatsapi_shotmap",
      "match_id":     "mt_14502",
      "home_team_id": 123,
      "away_team_id": 456,
      "home_np_xg":   1.42,
      "away_np_xg":   0.94,
      "reason_codes": ["XG_FROM_STORED_SUMMARY"],
    }

Resolution order (per product spec):
  1. ``np_xg_summary.stored.{home_team,away_team}``  (preferred — canonical).
  2. ``np_xg_summary.live.{home_team,away_team}``    (live mirror).
  3. Fallback: sum ``data[i].expected_goals`` per team_id, skipping rows
     whose ``is_penalty=True`` so we end up with non-penalty xG.

Never raises. Returns ``{"available": False, ...}`` on any failure.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from services.external_sources import thestatsapi_client as _tsa

log = logging.getLogger("thestatsapi_shotmap_client")

# Reason codes — surfaced both in client output and aggregator output.
RC_FROM_STORED   = "XG_FROM_STORED_SUMMARY"
RC_FROM_LIVE     = "XG_FROM_LIVE_SUMMARY"
RC_FROM_FALLBACK = "XG_FROM_DATA_FALLBACK"
RC_NO_SHOTMAP    = "THESTATSAPI_SHOTMAP_UNAVAILABLE"
RC_NO_TEAM_IDS   = "SHOTMAP_TEAM_IDS_MISSING"


def _coerce_xg(value: Any) -> Optional[float]:
    """Accept ints/floats/strings; reject NaN and out-of-range."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    if f < 0 or f > 12:  # sanity cap (no team ever produces >12 npxG in 1 match)
        return None
    return round(f, 3)


def _extract_from_summary(summary: dict) -> tuple[Optional[float], Optional[float]]:
    """Pull (home_np_xg, away_np_xg) from a ``stored`` or ``live`` dict.

    The team blocks may be either a single number (TheStatsAPI new
    shape) or a dict carrying ``expected_goals`` / ``np_xg`` keys.
    """
    if not isinstance(summary, dict):
        return None, None
    home = summary.get("home_team")
    away = summary.get("away_team")
    # Direct numeric form.
    h = _coerce_xg(home if not isinstance(home, dict) else
                    home.get("np_xg") or home.get("expected_goals") or home.get("xg"))
    a = _coerce_xg(away if not isinstance(away, dict) else
                    away.get("np_xg") or away.get("expected_goals") or away.get("xg"))
    return h, a


def _fallback_from_data(rows: list, home_team_id: Any,
                         away_team_id: Any) -> tuple[Optional[float], Optional[float]]:
    """Sum non-penalty expected_goals per team_id from the raw event rows."""
    if not isinstance(rows, list) or not rows:
        return None, None
    sums = {"home": 0.0, "away": 0.0}
    counts = {"home": 0, "away": 0}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("is_penalty") is True:
            continue
        xg = _coerce_xg(row.get("expected_goals") or row.get("xg") or row.get("np_xg"))
        if xg is None:
            continue
        tid = row.get("team_id") or (row.get("team") or {}).get("id")
        if tid is None:
            continue
        if home_team_id is not None and tid == home_team_id:
            sums["home"] += xg; counts["home"] += 1
        elif away_team_id is not None and tid == away_team_id:
            sums["away"] += xg; counts["away"] += 1
    h = round(sums["home"], 3) if counts["home"] > 0 else None
    a = round(sums["away"], 3) if counts["away"] > 0 else None
    return h, a


async def fetch_shotmap_xg(
    client: Optional[httpx.AsyncClient],
    match_id: str | int,
    *,
    timeout: float = 4.0,
) -> dict:
    """Fetch and normalise non-penalty xG for a single match.

    The function tries the stored summary first, then live, then a
    manual sum over the raw events. Returns a fail-soft dict — never
    raises, never bubbles up HTTP errors.
    """
    if match_id is None or str(match_id).strip() == "":
        return {
            "available":    False,
            "source":       None,
            "match_id":     None,
            "reason_codes": [RC_NO_SHOTMAP, "MATCH_ID_MISSING"],
        }
    mid = str(match_id).strip()

    # Reuse the shared low-level _request from the canonical client so
    # we inherit retry/backoff + rate limiting + bearer auth + the
    # is_enabled() short-circuit.
    if not _tsa.is_enabled():
        return {
            "available":    False,
            "source":       None,
            "match_id":     mid,
            "reason_codes": [RC_NO_SHOTMAP, "THESTATSAPI_DISABLED"],
        }
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        payload = await _tsa._request(
            client, "GET",
            f"/football/matches/{mid}/shotmap",
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - defensive
        log.debug("[shotmap] fetch crashed match=%s: %s", mid, exc)
        payload = {}
    finally:
        if owns_client:
            try: await client.aclose()
            except Exception: pass  # noqa: BLE001

    if not isinstance(payload, dict) or not payload:
        return {
            "available":    False,
            "source":       None,
            "match_id":     mid,
            "reason_codes": [RC_NO_SHOTMAP],
        }

    summary = payload.get("np_xg_summary") or {}
    home_team_id = (payload.get("home_team") or {}).get("id") if isinstance(
        payload.get("home_team"), dict) else payload.get("home_team_id")
    away_team_id = (payload.get("away_team") or {}).get("id") if isinstance(
        payload.get("away_team"), dict) else payload.get("away_team_id")

    # 1) stored
    h, a = _extract_from_summary(summary.get("stored"))
    if h is not None and a is not None:
        return {
            "available":    True,
            "source":       "thestatsapi_shotmap",
            "match_id":     mid,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_np_xg":   h,
            "away_np_xg":   a,
            "reason_codes": [RC_FROM_STORED],
        }
    # 2) live
    h, a = _extract_from_summary(summary.get("live"))
    if h is not None and a is not None:
        return {
            "available":    True,
            "source":       "thestatsapi_shotmap",
            "match_id":     mid,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_np_xg":   h,
            "away_np_xg":   a,
            "reason_codes": [RC_FROM_LIVE],
        }
    # 3) manual sum over data[]
    h, a = _fallback_from_data(payload.get("data") or [], home_team_id, away_team_id)
    if h is not None and a is not None:
        return {
            "available":    True,
            "source":       "thestatsapi_shotmap",
            "match_id":     mid,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_np_xg":   h,
            "away_np_xg":   a,
            "reason_codes": [RC_FROM_FALLBACK],
        }
    # Nothing usable.
    codes = [RC_NO_SHOTMAP]
    if home_team_id is None or away_team_id is None:
        codes.append(RC_NO_TEAM_IDS)
    return {
        "available":    False,
        "source":       None,
        "match_id":     mid,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "reason_codes": codes,
    }


__all__ = [
    "fetch_shotmap_xg",
    "RC_FROM_STORED", "RC_FROM_LIVE", "RC_FROM_FALLBACK",
    "RC_NO_SHOTMAP", "RC_NO_TEAM_IDS",
]
