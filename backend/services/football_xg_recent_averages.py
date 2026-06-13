"""Phase F83.2-E1b — xG recent averages aggregator (L1 / L5 / L15).

Aggregates non-penalty xG ``for`` and ``against`` for each side using
the TheStatsAPI shotmap endpoint as the canonical source.

Architectural notes
-------------------
* **Background-first.** Designed to be invoked from the manual
  ``/run-now`` endpoint or the queued worker — never inline from the
  pick generator. The shotmap endpoint costs N requests per side (one
  per recent fixture); we cap at 15.
* **Cache.** A per-match in-memory cache (TTL 6h) avoids duplicate
  shotmap fetches when the user mashes the refresh button.
* **Partial fallback.** Returns ``partial=True`` when one of L5 / L15 is
  missing — the UI still renders what IS available.
* **No mocks.** When the recent-match ids are unavailable, returns
  ``available=False`` with a precise reason code; never invents data.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from services.external_sources.thestatsapi_shotmap_client import (
    fetch_shotmap_xg,
)

log = logging.getLogger("football_xg_recent_averages")

# ── Reason codes ─────────────────────────────────────────────────────
RC_FROM_SHOTMAP            = "XG_RECENT_AVERAGES_FROM_SHOTMAP"
RC_PARTIAL_SAMPLE          = "XG_PARTIAL_SAMPLE"
RC_L1_AVAILABLE            = "XG_L1_AVAILABLE"
RC_L5_AVAILABLE            = "XG_L5_AVAILABLE"
RC_L5_MISSING              = "XG_L5_MISSING"
RC_L15_AVAILABLE           = "XG_L15_AVAILABLE"
RC_L15_MISSING             = "XG_L15_MISSING"
RC_NO_RECENT_MATCH_IDS     = "XG_RECENT_MATCH_IDS_NOT_AVAILABLE"
RC_SHOTMAP_UNAVAILABLE     = "THESTATSAPI_SHOTMAP_UNAVAILABLE"
RC_BUILD_FAILED            = "XG_RECENT_AVERAGES_BUILD_FAILED"

# ── In-memory cache (per match_id of the *target* fixture) ───────────
_CACHE_TTL_S    = 6 * 3600
_CACHE: dict[str, tuple[float, dict]] = {}
_DEFAULT_TIMEOUT_S = 6.0
_MAX_RECENT_PER_SIDE = 15


def _now() -> float:
    return time.monotonic()


def _cache_get(key: str) -> Optional[dict]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, value = entry
    if _now() - ts > _CACHE_TTL_S:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: dict) -> None:
    _CACHE[key] = (_now(), value)


def _extract_recent_match_ids(team_block: Any, *, limit: int = _MAX_RECENT_PER_SIDE) -> list:
    """Pull the most recent TheStatsAPI match ids for a team.

    Accepts the various shapes the codebase uses:
      * ``team.context.recent_fixtures[].match_id``
      * ``team.recent_fixtures[].match_id``
      * ``team.recent_fixtures[].id``
      * ``team.thestatsapi_recent_match_ids[]``  (flat list)
    """
    if not isinstance(team_block, dict):
        return []
    candidates: list = []

    flat = team_block.get("thestatsapi_recent_match_ids")
    if isinstance(flat, list):
        candidates = [m for m in flat if m is not None]
    if not candidates:
        ctx = team_block.get("context") or {}
        rf = (ctx.get("recent_fixtures") if isinstance(ctx, dict) else None) \
             or team_block.get("recent_fixtures") or []
        if isinstance(rf, list):
            for row in rf:
                if not isinstance(row, dict):
                    continue
                mid = (row.get("match_id") or row.get("id")
                        or row.get("thestatsapi_id"))
                if mid is not None:
                    candidates.append(mid)

    # De-dup preserving order, cap to limit.
    seen = set()
    out: list = []
    for m in candidates:
        m_str = str(m)
        if m_str in seen:
            continue
        seen.add(m_str)
        out.append(m)
        if len(out) >= limit:
            break
    return out


def _safe_div(numer: float, denom: int) -> Optional[float]:
    if denom <= 0:
        return None
    return round(numer / denom, 3)


def _build_window(rows: list[dict], *, side: str, window: int) -> Optional[dict]:
    """Compute ``xg_for_avg`` / ``xg_against_avg`` from the first
    ``window`` rows in ``rows``. Each row already carries ``home_np_xg``,
    ``away_np_xg`` and ``side_is_home`` (set by the caller)."""
    if not rows or window <= 0:
        return None
    take = rows[:window]
    if not take:
        return None
    xg_for_sum = 0.0
    xg_ag_sum  = 0.0
    counted = 0
    for r in take:
        h = r.get("home_np_xg")
        a = r.get("away_np_xg")
        if h is None or a is None:
            continue
        if r.get("side_is_home"):
            xg_for_sum += float(h); xg_ag_sum += float(a)
        else:
            xg_for_sum += float(a); xg_ag_sum += float(h)
        counted += 1
    if counted == 0:
        return None
    return {
        "xg_for_avg":     _safe_div(xg_for_sum, counted),
        "xg_against_avg": _safe_div(xg_ag_sum,  counted),
        "sample_size":    counted,
    }


async def _fetch_one(client: httpx.AsyncClient, match_id: Any,
                      team_id: Any, timeout_s: float) -> Optional[dict]:
    """Fetch one shotmap and annotate which side the *target* team was."""
    data = await fetch_shotmap_xg(client, match_id, timeout=timeout_s)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    h_id = data.get("home_team_id")
    a_id = data.get("away_team_id")
    side_is_home: Optional[bool] = None
    if team_id is not None:
        if h_id is not None and team_id == h_id:
            side_is_home = True
        elif a_id is not None and team_id == a_id:
            side_is_home = False
    if side_is_home is None:
        # Can't tell which side our team was on → skip this row.
        return None
    return {
        "home_np_xg":   data.get("home_np_xg"),
        "away_np_xg":   data.get("away_np_xg"),
        "side_is_home": side_is_home,
    }


async def _aggregate_side(client: httpx.AsyncClient, team_block: dict,
                           timeout_s: float) -> dict:
    """Aggregate L1 / L5 / L15 for one side. Returns the side payload
    shaped per the product spec."""
    out: dict[str, Any] = {
        "team": (team_block.get("name") if isinstance(team_block, dict) else None),
        "l1":   None,
        "l5":   None,
        "l15":  None,
        "reason_codes": [],
    }
    team_id = (team_block.get("id") if isinstance(team_block, dict)
                else None) or (team_block.get("team_id") if isinstance(team_block, dict)
                              else None)
    recent_ids = _extract_recent_match_ids(team_block, limit=_MAX_RECENT_PER_SIDE)
    if not recent_ids:
        out["reason_codes"].append(RC_NO_RECENT_MATCH_IDS)
        return out

    # Fetch shotmaps in parallel (cap concurrency to be polite).
    sem = asyncio.Semaphore(4)
    async def _one(mid):
        async with sem:
            return await _fetch_one(client, mid, team_id, timeout_s)
    results = await asyncio.gather(*[_one(m) for m in recent_ids],
                                    return_exceptions=False)
    rows = [r for r in results if isinstance(r, dict)]

    if not rows:
        out["reason_codes"].append(RC_SHOTMAP_UNAVAILABLE)
        return out

    # L1 — most recent fixture only (no averaging).
    if rows:
        r0 = rows[0]
        out["l1"] = {
            "xg_for_avg":     (float(r0["home_np_xg"]) if r0["side_is_home"]
                                else float(r0["away_np_xg"])),
            "xg_against_avg": (float(r0["away_np_xg"]) if r0["side_is_home"]
                                else float(r0["home_np_xg"])),
            "sample_size":    1,
        }
        out["reason_codes"].append(RC_L1_AVAILABLE)

    # L5
    l5 = _build_window(rows, side="self", window=5)
    if l5:
        out["l5"] = l5
        out["reason_codes"].append(RC_L5_AVAILABLE)
    else:
        out["reason_codes"].append(RC_L5_MISSING)

    # L15
    l15 = _build_window(rows, side="self", window=15)
    if l15:
        out["l15"] = l15
        out["reason_codes"].append(RC_L15_AVAILABLE)
    else:
        out["reason_codes"].append(RC_L15_MISSING)

    return out


async def compute_xg_recent_averages(
    match_doc: dict,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    use_cache: bool = True,
) -> dict:
    """Compute the L1/L5/L15 non-penalty xG averages for both sides.

    Never raises. Returns a dict shaped per the product spec.

    When ``use_cache=True`` and a recent entry exists, returns it
    immediately. The cache key is the *target match's* match_id.
    """
    if not isinstance(match_doc, dict):
        return {
            "available":    False,
            "reason_codes": [RC_BUILD_FAILED, "MATCH_DOC_INVALID"],
        }
    target_mid = (match_doc.get("match_id") or match_doc.get("fixture_id")
                  or match_doc.get("id"))
    cache_key = str(target_mid) if target_mid is not None else None

    if use_cache and cache_key:
        cached = _cache_get(cache_key)
        if cached:
            return cached

    home_block = match_doc.get("home_team") or {}
    away_block = match_doc.get("away_team") or {}
    if not isinstance(home_block, dict) or not isinstance(away_block, dict):
        return {
            "available":    False,
            "reason_codes": [RC_NO_RECENT_MATCH_IDS],
        }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        home_payload, away_payload = await asyncio.gather(
            _aggregate_side(client, home_block, timeout_s),
            _aggregate_side(client, away_block, timeout_s),
        )
    finally:
        if owns_client:
            try: await client.aclose()
            except Exception: pass  # noqa: BLE001

    home_has_any = any(home_payload.get(k) for k in ("l1", "l5", "l15"))
    away_has_any = any(away_payload.get(k) for k in ("l1", "l5", "l15"))

    if not home_has_any and not away_has_any:
        return {
            "available":    False,
            "source":       None,
            "reason_codes": [RC_SHOTMAP_UNAVAILABLE],
            "home":          home_payload,
            "away":          away_payload,
        }

    partial = not (
        home_payload.get("l5") and home_payload.get("l15")
        and away_payload.get("l5") and away_payload.get("l15")
    )

    reason_codes: list[str] = [RC_FROM_SHOTMAP]
    if partial:
        reason_codes.append(RC_PARTIAL_SAMPLE)

    out = {
        "available":    True,
        "partial":      partial,
        "source":       "thestatsapi_shotmap",
        "home":          home_payload,
        "away":          away_payload,
        "reason_codes": reason_codes,
    }
    if use_cache and cache_key:
        _cache_put(cache_key, out)
    return out


def reset_cache() -> None:
    """Test hook."""
    _CACHE.clear()


__all__ = [
    "compute_xg_recent_averages",
    "reset_cache",
    "RC_FROM_SHOTMAP", "RC_PARTIAL_SAMPLE",
    "RC_L1_AVAILABLE", "RC_L5_AVAILABLE", "RC_L5_MISSING",
    "RC_L15_AVAILABLE", "RC_L15_MISSING",
    "RC_NO_RECENT_MATCH_IDS", "RC_SHOTMAP_UNAVAILABLE",
    "RC_BUILD_FAILED",
]
