"""Sprint-D7 · The Odds API **historical** client with hard credit cap.

The historical endpoints are *paid* — every events list call costs 1
credit, every event-odds call costs 10 credits. This client implements a
strict in-flight ceiling read from the ``x-requests-used`` header and
**aborts** as soon as ``credits_used >= max_credits``, returning the
partial payload so we never lose data already paid for.

Endpoints used
--------------
* ``GET /v4/historical/sports/{sport_key}/events?date=YYYY-MM-DD``      (1 cred)
* ``GET /v4/historical/sports/{sport_key}/events/{event_id}/odds?...``  (10 cred)

All public callables are async, **fail-soft**, and never raise — they
return an ``{"available": bool, "reason_code": ..., "events": [...]}``
shape so the orchestrator can keep going.

observe_only — never writes / never bets.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable, Iterable, Optional

log = logging.getLogger("theoddsapi_historical")

BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_TIMEOUT_S: float = 30.0
DEFAULT_MAX_CREDITS: int = 3000

RC_TOKEN_MISSING        = "THE_ODDS_API_KEY_MISSING"
RC_CREDITS_EXHAUSTED    = "MAX_CREDITS_REACHED"
RC_HTTP_ERROR           = "HTTP_ERROR"
RC_UNAVAILABLE          = "UNAVAILABLE_NO_COVERAGE"
RC_OK                   = "OK"


def _api_key() -> Optional[str]:
    return (os.environ.get("THE_ODDS_API_KEY")
             or os.environ.get("ODDS_API_KEY"))


def _parse_used(headers) -> Optional[int]:
    try:
        v = headers.get("x-requests-used")
        return int(v) if v is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


class CreditTracker:
    """In-process counter of credits consumed in *this* run.

    The ground truth comes from the ``x-requests-used`` header — we
    refresh ``used`` on every response. ``base`` is the value reported
    on the first response so the "this run" delta is well defined.
    """
    __slots__ = ("base", "used", "max_credits", "aborted")

    def __init__(self, max_credits: int = DEFAULT_MAX_CREDITS):
        self.base: Optional[int] = None
        self.used: Optional[int] = None
        self.max_credits: int    = int(max_credits)
        self.aborted: bool       = False

    def update(self, used_now: Optional[int]) -> None:
        if used_now is None:
            return
        if self.base is None:
            self.base = used_now
        self.used = used_now

    @property
    def delta(self) -> int:
        if self.base is None or self.used is None:
            return 0
        return max(0, self.used - self.base)

    def must_abort(self) -> bool:
        return self.delta >= self.max_credits


async def _http_get(
    url: str, params: dict, *, timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """Lowest-level GET. Returns ``{ok, status, json, headers}``."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(url, params=params)
            try:
                payload = r.json()
            except Exception:    # noqa: BLE001
                payload = None
            return {"ok": (200 <= r.status_code < 300),
                    "status":  r.status_code,
                    "json":    payload,
                    "headers": r.headers}
    except Exception as exc:    # noqa: BLE001
        return {"ok": False, "status": 0, "json": None,
                "headers": {}, "error": str(exc)}


async def fetch_events_for_date(
    *, sport_key: str, date_iso: str,
    tracker: CreditTracker,
    api_key: Optional[str] = None,
    http: Optional[Callable[..., Awaitable[dict]]] = None,
) -> dict:
    """``GET /v4/historical/sports/{sport}/events?date=YYYY-MM-DDT00:00:00Z``.

    Returns ``{"available", "events", "credits_used", "reason_code"}``.
    """
    api_key = api_key or _api_key()
    if not api_key:
        return {"available": False, "reason_code": RC_TOKEN_MISSING,
                "events": [], "credits_used": tracker.delta}
    if tracker.must_abort():
        tracker.aborted = True
        return {"available": False, "reason_code": RC_CREDITS_EXHAUSTED,
                "events": [], "credits_used": tracker.delta}
    url = f"{BASE_URL}/historical/sports/{sport_key}/events"
    params = {"apiKey": api_key, "date": date_iso}
    transport = http or _http_get
    res = await transport(url, params)
    tracker.update(_parse_used(res.get("headers") or {}))
    if not res.get("ok"):
        if res.get("status") == 404:
            # 404 in historical/v4 typically means: no coverage for that
            # sport/date in this plan tier.
            return {"available": False, "reason_code": RC_UNAVAILABLE,
                    "events": [], "credits_used": tracker.delta,
                    "status": 404}
        return {"available": False, "reason_code": RC_HTTP_ERROR,
                "events": [], "credits_used": tracker.delta,
                "status": res.get("status"),
                "_error": res.get("error")}
    body = res.get("json") or {}
    events = (body.get("data") if isinstance(body, dict) else body) or []
    return {"available": True, "reason_code": RC_OK,
            "events": list(events) if isinstance(events, list) else [],
            "credits_used": tracker.delta,
            "snapshot_ts": (body.get("timestamp") if isinstance(body, dict) else None)}


async def fetch_event_odds_pit(
    *, sport_key: str, event_id: str, snapshot_iso: str,
    tracker: CreditTracker,
    regions: str = "eu",
    markets: str = "h2h",
    api_key: Optional[str] = None,
    http: Optional[Callable[..., Awaitable[dict]]] = None,
) -> dict:
    """Fetch ONE event's point-in-time odds. 10 credits per call."""
    api_key = api_key or _api_key()
    if not api_key:
        return {"available": False, "reason_code": RC_TOKEN_MISSING,
                "credits_used": tracker.delta}
    if tracker.must_abort():
        tracker.aborted = True
        return {"available": False, "reason_code": RC_CREDITS_EXHAUSTED,
                "credits_used": tracker.delta}
    url = (f"{BASE_URL}/historical/sports/{sport_key}/events/"
            f"{event_id}/odds")
    params = {"apiKey": api_key, "date": snapshot_iso,
               "regions": regions, "markets": markets,
               "oddsFormat": "decimal"}
    transport = http or _http_get
    res = await transport(url, params)
    tracker.update(_parse_used(res.get("headers") or {}))
    if not res.get("ok"):
        return {"available": False,
                "reason_code": (RC_UNAVAILABLE if res.get("status") == 404
                                  else RC_HTTP_ERROR),
                "credits_used": tracker.delta,
                "status": res.get("status"),
                "_error": res.get("error")}
    body = res.get("json") or {}
    data = (body.get("data") if isinstance(body, dict) else body) or {}
    return {"available": True, "reason_code": RC_OK,
            "credits_used": tracker.delta,
            "odds_timestamp": (body.get("timestamp")
                                 if isinstance(body, dict) else None),
            "event": data}


def _t_minus_3h_iso(commence_time: str) -> Optional[str]:
    """Convert an event's ``commence_time`` to a kickoff − 3h ISO string."""
    if not commence_time:
        return None
    try:
        dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


async def fetch_tournament_pit_odds(
    *, sport_key: str, dates_iso: Iterable[str],
    max_credits: int = DEFAULT_MAX_CREDITS,
    regions: str = "eu", markets: str = "h2h",
    api_key: Optional[str] = None,
    http: Optional[Callable[..., Awaitable[dict]]] = None,
) -> dict:
    """High-level: per tournament, walk every date, list events, then
    fetch each event's odds at ``kickoff - 3h``.

    Strict ``max_credits`` enforcement: as soon as the per-run delta
    reaches ``max_credits`` we stop and return the partial payload.
    """
    tracker = CreditTracker(max_credits=max_credits)
    out_events: list[dict] = []
    reasons: list[str] = []
    # The historical /events?date= endpoint returns ALL events available
    # at the given snapshot timestamp — not just events that kick off
    # on that day. As we sweep daily snapshots the same event_id appears
    # multiple times. We must dedup BEFORE issuing the 10-credit /odds
    # call to avoid burning credits on duplicates.
    seen_event_ids: set[str] = set()
    for date_iso in dates_iso:
        if tracker.must_abort():
            tracker.aborted = True
            reasons.append(RC_CREDITS_EXHAUSTED)
            break
        listing = await fetch_events_for_date(
            sport_key=sport_key, date_iso=date_iso,
            tracker=tracker, api_key=api_key, http=http,
        )
        if not listing.get("available"):
            rc = listing.get("reason_code")
            if rc and rc not in reasons:
                reasons.append(rc)
            if rc == RC_CREDITS_EXHAUSTED:
                break
            continue
        for ev in listing["events"]:
            if tracker.must_abort():
                tracker.aborted = True
                reasons.append(RC_CREDITS_EXHAUSTED)
                break
            ev_id = ev.get("id")
            if ev_id is not None and ev_id in seen_event_ids:
                continue
            if ev_id is not None:
                seen_event_ids.add(ev_id)
            commence = ev.get("commence_time")
            snap = _t_minus_3h_iso(commence) or date_iso
            odds_res = await fetch_event_odds_pit(
                sport_key=sport_key, event_id=ev_id,
                snapshot_iso=snap, tracker=tracker,
                regions=regions, markets=markets,
                api_key=api_key, http=http,
            )
            if odds_res.get("available"):
                out_events.append({
                    "event_id":      ev.get("id"),
                    "sport_key":     sport_key,
                    "home_team":     ev.get("home_team"),
                    "away_team":     ev.get("away_team"),
                    "commence_time": commence,
                    "odds_timestamp": odds_res.get("odds_timestamp"),
                    "event_payload":  odds_res.get("event"),
                })
            else:
                rc = odds_res.get("reason_code")
                if rc and rc not in reasons:
                    reasons.append(rc)
                if rc == RC_CREDITS_EXHAUSTED:
                    break
    return {
        "available":     bool(out_events),
        "events":        out_events,
        "credits_used":  tracker.delta,
        "credits_total_account": tracker.used,
        "max_credits":   max_credits,
        "aborted":       tracker.aborted,
        "reason_codes":  reasons,
        "sport_key":     sport_key,
        "odds_type":     "POINT_IN_TIME_PREMATCH",
    }


async def verify_sport_keys_available(
    sport_keys: Iterable[str],
    *,
    api_key: Optional[str] = None,
    http: Optional[Callable[..., Awaitable[dict]]] = None,
) -> dict:
    """Verify which ``sport_keys`` exist in The Odds API catalog.

    Uses ``GET /v4/sports?all=true`` which is **FREE** (does not consume
    historical credits — the request is metered against the regular
    monthly quota but not against the historical purse).

    Returns:
      ``{"available": bool, "valid_keys": [...], "missing_keys": [...],
         "reason_code": "...", "raw_count": int}``
    """
    api_key = api_key or _api_key()
    if not api_key:
        return {"available": False, "reason_code": RC_TOKEN_MISSING,
                "valid_keys": [], "missing_keys": list(sport_keys),
                "raw_count": 0}
    transport = http or _http_get
    res = await transport(
        f"{BASE_URL}/sports",
        {"apiKey": api_key, "all": "true"},
    )
    if not res.get("ok"):
        return {"available": False,
                "reason_code": RC_HTTP_ERROR,
                "valid_keys": [], "missing_keys": list(sport_keys),
                "raw_count": 0,
                "status": res.get("status"),
                "_error": res.get("error")}
    body = res.get("json") or []
    # /v4/sports returns a flat list of {key, group, title, ...}.
    catalog = {item.get("key") for item in body if isinstance(item, dict)}
    keys = list(sport_keys)
    valid   = [k for k in keys if k in catalog]
    missing = [k for k in keys if k not in catalog]
    return {
        "available":   bool(valid),
        "reason_code": RC_OK if valid else RC_UNAVAILABLE,
        "valid_keys":  valid,
        "missing_keys": missing,
        "raw_count":   len(catalog),
    }


def estimate_credit_cost(*, n_matches: int) -> dict:
    """Estimate the worst-case credit cost for fetching point-in-time
    odds for ``n_matches`` events.

    Pricing (per The Odds API documentation):
      * 1 credit per ``/historical/sports/{key}/events?date=...`` call
      * 10 credits per ``/historical/sports/{key}/events/{id}/odds`` call

    Each unique match-day triggers 1 listing call; each event triggers
    1 odds call. A safe upper bound assumes every match falls on its
    own day (worst case): ``n_matches × 11``.

    Returns ``{n_matches, est_credits_floor, est_credits_ceiling}``.
    """
    n = max(0, int(n_matches))
    return {
        "n_matches":            n,
        "est_credits_floor":    n * 10 + 1,   # all matches on same day
        "est_credits_ceiling":  n * 11,        # each match on its own day
    }


__all__ = [
    "DEFAULT_MAX_CREDITS", "BASE_URL",
    "RC_TOKEN_MISSING", "RC_CREDITS_EXHAUSTED", "RC_HTTP_ERROR",
    "RC_UNAVAILABLE", "RC_OK",
    "CreditTracker", "_parse_used", "_t_minus_3h_iso",
    "fetch_events_for_date", "fetch_event_odds_pit",
    "fetch_tournament_pit_odds",
    "verify_sport_keys_available", "estimate_credit_cost",
]
