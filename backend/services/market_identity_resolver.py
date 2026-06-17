"""Sprint E.1.1 · Market Identity Resolver (vía The Odds API).

When the football engine surfaces ``REQUIRES_MARKET_IDENTIFICATION``
(i.e. it observed a price but doesn't know which market it belongs
to — DNB / 1X2 / Over-Under / BTTS / Handicap / etc.), this module
queries The Odds API for the matching event and tries to find a
*nearby* price across every supported market.

Contract (pure-ish; the Mongo bits are isolated):

    >>> result = await resolve_market_identity(
    ...     db=db,
    ...     match={"match_id": "m1",
    ...            "home_team": "Portugal",
    ...            "away_team": "Congo DR",
    ...            "commence_time": "2026-06-15T18:00:00Z"},
    ...     detected_price=1.25,
    ... )
    >>> result["candidates"]            # list, sorted by abs(api-detected)
    >>> result["best"]                  # highest-confidence (or None)
    >>> result["resolution_status"]     # RESOLVED|AMBIGUOUS|NOT_FOUND|...

Strict invariants
-----------------
* **observe_only / fail-soft**: any error → returns a structured
  ``NOT_FOUND``/``ERROR`` payload, never raises.
* **No side effects** other than:
    - upsert into ``market_identity_resolutions`` (audit + reuse cache).
* The original detected odd is **never** mutated. We only *propose*
  what market it likely belongs to.
* Tolerances (the user-approved ladder):
    HIGH    abs(api - detected) <= 0.02
    MEDIUM  <= 0.03
    LOW     <= 0.05
    above 0.05 → not a candidate.

This resolver does NOT replace ``manual_market_identity``; it adds an
*automatic* proposal layer. The operator can still override manually.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

from .external_sources import the_odds_api_client as the_odds_api
from . import live_odds_monitor as lom

log = logging.getLogger("market_identity_resolver")

# ─── Constants ─────────────────────────────────────────────────────────
COLLECTION_NAME = "market_identity_resolutions"
SOURCE_NAME     = "the_odds_api_v4"

# Tolerance ladder (user-approved).
TOL_HIGH:   float = 0.02
TOL_MEDIUM: float = 0.03
TOL_LOW:    float = 0.05

# Default Odds API markets to query. Order matters — earlier markets
# are listed first in the resulting candidate list when the score is
# equal. ``team_totals`` is included on a best-effort basis (some
# regions / sports do not expose it; the client just returns whatever
# the API provides).
DEFAULT_MARKETS_PRIORITY: tuple[str, ...] = (
    "h2h",
    "draw_no_bet",
    "totals",
    "alternate_totals",
    "spreads",
    "alternate_spreads",
    "btts",
    "team_totals",
)

DEFAULT_REGIONS: str = os.environ.get("MARKET_RESOLVER_REGIONS",
                                       "uk,eu,us") or "uk,eu,us"

# Cache TTL for cached resolutions, in seconds (default 6h). When a
# match is re-resolved before TTL expiry we short-circuit. ``0`` =
# always re-resolve.
CACHE_TTL_SECONDS: int = int(os.environ.get("MARKET_RESOLVER_CACHE_TTL", "21600"))


# ─── Confidence ladder ─────────────────────────────────────────────────
def _confidence_for_delta(delta: float) -> Optional[str]:
    """Map ``abs(api - detected)`` to a confidence label, or ``None``
    when the delta is outside the LOW threshold.

    Deltas are rounded to 4 decimals BEFORE the ladder comparison so
    floating-point noise (e.g. ``1.27 - 1.25 == 0.020000000000000018``)
    cannot push an obvious HIGH match into MEDIUM.
    """
    try:
        d = round(float(delta), 4)
    except (TypeError, ValueError):
        return None
    if d <= TOL_HIGH:
        return "HIGH"
    if d <= TOL_MEDIUM:
        return "MEDIUM"
    if d <= TOL_LOW:
        return "LOW"
    return None


# ─── Market mapping (Odds API → MANUAL_MARKET_TYPES) ───────────────────
def _map_outcome_to_manual_identity(
    *,
    api_market: str,
    outcome: dict,
    home_team: str,
    away_team: str,
) -> Optional[dict]:
    """Translate a single (market, outcome) pair from The Odds API into
    the canonical ``manual_market_identity`` schema.

    Returns ``None`` if we cannot map it (e.g. unknown market key).
    """
    if not isinstance(outcome, dict):
        return None
    name  = (outcome.get("name") or "").strip()
    price = outcome.get("price")
    point = outcome.get("point")
    if price is None:
        return None

    h_norm  = lom.normalise_team(home_team)
    a_norm  = lom.normalise_team(away_team)
    n_norm  = lom.normalise_team(name)
    name_lc = name.lower().strip()

    out: Optional[dict] = None
    if api_market == "h2h":
        if name_lc in ("draw", "tie", "empate"):
            out = {"market_type": "MATCH_WINNER", "selection": "DRAW",
                   "line": None}
        elif n_norm == h_norm or h_norm in n_norm or n_norm in h_norm:
            out = {"market_type": "MATCH_WINNER", "selection": "HOME",
                   "line": None}
        elif n_norm == a_norm or a_norm in n_norm or n_norm in a_norm:
            out = {"market_type": "MATCH_WINNER", "selection": "AWAY",
                   "line": None}
    elif api_market == "draw_no_bet":
        if n_norm == h_norm or h_norm in n_norm or n_norm in h_norm:
            out = {"market_type": "DNB", "selection": "HOME", "line": None}
        elif n_norm == a_norm or a_norm in n_norm or n_norm in a_norm:
            out = {"market_type": "DNB", "selection": "AWAY", "line": None}
    elif api_market in ("totals", "alternate_totals"):
        if name_lc.startswith("over"):
            sel = "OVER"
        elif name_lc.startswith("under"):
            sel = "UNDER"
        else:
            sel = None
        if sel is not None and point is not None:
            out = {"market_type": "TOTAL_GOALS", "selection": sel,
                   "line": float(point)}
    elif api_market in ("spreads", "alternate_spreads"):
        sel = None
        if n_norm == h_norm or h_norm in n_norm or n_norm in h_norm:
            sel = "HOME"
        elif n_norm == a_norm or a_norm in n_norm or n_norm in a_norm:
            sel = "AWAY"
        if sel and point is not None:
            out = {"market_type": "ASIAN_HANDICAP", "selection": sel,
                   "line": float(point)}
    elif api_market == "btts":
        if name_lc in ("yes", "si", "sí"):
            out = {"market_type": "BTTS", "selection": "YES", "line": None}
        elif name_lc == "no":
            out = {"market_type": "BTTS", "selection": "NO", "line": None}
    elif api_market == "team_totals":
        # team_totals: outcome name encodes "<team> Over X.5" / similar
        # — keep raw so we can render in UI as a candidate but flag
        # market as TOTAL_GOALS for the calculator.
        if "over" in name_lc:
            sel = "OVER"
        elif "under" in name_lc:
            sel = "UNDER"
        else:
            sel = None
        if sel is not None and point is not None:
            out = {"market_type": "TOTAL_GOALS",
                   "selection": sel,
                   "line": float(point),
                   "scope": "team_totals",
                   "team_hint": name}
    return out


# ─── Pure: candidate extraction from a single event payload ────────────
def extract_candidates_from_event(
    *,
    event_payload: dict,
    detected_price: float,
    home_team: str,
    away_team: str,
    markets_priority: Iterable[str] = DEFAULT_MARKETS_PRIORITY,
) -> list[dict]:
    """Walk every (bookmaker, market, outcome) in ``event_payload`` and
    return the ones whose price is within the LOW tolerance from
    ``detected_price``.

    Pure helper (no Mongo, no HTTP). Returns a list sorted by
    ``(delta_asc, market_priority_asc)``.
    """
    out: list[dict] = []
    if not isinstance(event_payload, dict):
        return out
    try:
        detected_price = float(detected_price)
    except (TypeError, ValueError):
        return out
    priority_index = {m: i for i, m in enumerate(markets_priority)}

    bookmakers = event_payload.get("bookmakers") or []
    for bm in bookmakers:
        if not isinstance(bm, dict):
            continue
        bm_key   = bm.get("key")
        bm_title = bm.get("title") or bm_key
        for mkt in bm.get("markets") or []:
            if not isinstance(mkt, dict):
                continue
            mk_key = mkt.get("key")
            if mk_key not in priority_index:
                continue
            for o in mkt.get("outcomes") or []:
                if not isinstance(o, dict):
                    continue
                price = o.get("price")
                if price is None:
                    continue
                try:
                    delta = abs(float(price) - detected_price)
                except (TypeError, ValueError):
                    continue
                conf = _confidence_for_delta(delta)
                if conf is None:
                    continue
                mapped = _map_outcome_to_manual_identity(
                    api_market=mk_key, outcome=o,
                    home_team=home_team, away_team=away_team,
                )
                # Unknown mappings still surface as candidates (with
                # market_type=None) so UI can show "unmapped".
                candidate = {
                    "api_market":       mk_key,
                    "api_outcome_name": o.get("name"),
                    "api_price":        float(price),
                    "api_point":        o.get("point"),
                    "detected_price":   detected_price,
                    "delta":            round(delta, 4),
                    "confidence":       conf,
                    "bookmaker_key":    bm_key,
                    "bookmaker_title":  bm_title,
                    "market_priority":  priority_index[mk_key],
                    # Mapped (manual_market_identity) fields:
                    "resolved_market":     (mapped or {}).get("market_type"),
                    "resolved_selection":  (mapped or {}).get("selection"),
                    "resolved_line":       (mapped or {}).get("line"),
                    "resolved_scope":      (mapped or {}).get("scope"),
                    "team_hint":           (mapped or {}).get("team_hint"),
                }
                out.append(candidate)

    out.sort(key=lambda c: (c["delta"], c["market_priority"]))
    return out


# ─── Pure: pick best + summarise ───────────────────────────────────────
_CONF_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def summarise_candidates(candidates: list[dict]) -> dict:
    """Decide a top-level ``resolution_status`` + best candidate.

    Returns::

        {
          "resolution_status": "RESOLVED" | "AMBIGUOUS" | "NOT_FOUND",
          "best":              <candidate | None>,
          "ambiguous":         <list[candidate]>,   # >= 2 distinct families
        }

    A resolution counts as ``RESOLVED`` only when the best family is
    unambiguously the lowest delta across all distinct ``resolved_market``
    families. Otherwise we surface every distinct family as
    ``AMBIGUOUS`` and the UI lets the operator pick.
    """
    if not candidates:
        return {"resolution_status": "NOT_FOUND",
                "best": None, "ambiguous": []}

    # Group by (market_type, selection, line). Keep the lowest-delta
    # representative per group.
    by_family: dict[tuple, dict] = {}
    for c in candidates:
        key = (c.get("resolved_market"),
               c.get("resolved_selection"),
               c.get("resolved_line"))
        prev = by_family.get(key)
        if prev is None or c["delta"] < prev["delta"]:
            by_family[key] = c

    family_list = sorted(
        by_family.values(),
        key=lambda c: (c["delta"], -_CONF_RANK.get(c["confidence"] or "", 0)),
    )
    best = family_list[0]

    # Ambiguous = there is at least one OTHER family within the same
    # confidence tier as ``best``.
    best_conf = best.get("confidence")
    rivals = [c for c in family_list[1:]
              if c.get("confidence") == best_conf
              and (c.get("resolved_market"),
                    c.get("resolved_selection"),
                    c.get("resolved_line"))
                  != (best.get("resolved_market"),
                       best.get("resolved_selection"),
                       best.get("resolved_line"))]
    if rivals:
        return {"resolution_status": "AMBIGUOUS",
                "best": best,
                "ambiguous": [best] + rivals}
    return {"resolution_status": "RESOLVED",
            "best": best, "ambiguous": []}


# ─── Async helpers (Mongo + HTTP) ──────────────────────────────────────
async def _read_cached_resolution(db, match_id: str,
                                    detected_price: float) -> Optional[dict]:
    if CACHE_TTL_SECONDS <= 0 or not match_id:
        return None
    try:
        doc = await db[COLLECTION_NAME].find_one(
            {"match_id": str(match_id),
             "detected_price": float(detected_price)},
            sort=[("resolved_at", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("resolution cache read failed: %s", exc)
        return None
    if not doc:
        return None
    ts = doc.get("resolved_at")
    if hasattr(ts, "timestamp"):
        age = (datetime.now(timezone.utc) -
                ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else
                datetime.now(timezone.utc) - ts).total_seconds()
        if age > CACHE_TTL_SECONDS:
            return None
    return doc


async def _persist_resolution(db, *, payload: dict) -> None:
    try:
        await db[COLLECTION_NAME].insert_one(payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("persist_resolution failed: %s", exc)


async def _ensure_event_id_for_match(
    db,
    *,
    match: dict,
    sport_keys: list[str],
    fetch_events: Optional[Callable[..., Awaitable[Optional[dict]]]],
) -> Optional[dict]:
    """Reuse ``live_odds_monitor.resolve_event_id`` so we share the
    persistent ``odds_event_id_mappings`` cache (Sprint E.1)."""
    return await lom.resolve_event_id(
        db, match=match, sport_keys=list(sport_keys),
        events_cache={}, fetch_events=fetch_events,
    )


# ─── Main resolver ─────────────────────────────────────────────────────
async def resolve_market_identity(
    db,
    *,
    match: dict,
    detected_price: float,
    sport_keys: Optional[Iterable[str]] = None,
    markets_priority: Iterable[str] = DEFAULT_MARKETS_PRIORITY,
    regions: str = DEFAULT_REGIONS,
    use_cache: bool = True,
    fetch_events: Optional[Callable[..., Awaitable[Optional[dict]]]] = None,
    fetch_current_odds: Optional[Callable[..., Awaitable[Optional[dict]]]] = None,
) -> dict:
    """Resolve which market a detected price likely belongs to using
    The Odds API. Fail-soft: returns a structured payload even when
    every step fails.

    Always returns the same top-level shape::

        {
            "resolution_status": "RESOLVED|AMBIGUOUS|NOT_FOUND|"
                                  "MATCH_NOT_FOUND|API_UNAVAILABLE|"
                                  "INVALID_INPUT|CACHED",
            "reason_code":       <string|None>,
            "match_id":          <str|None>,
            "event_id":          <str|None>,
            "sport_key":         <str|None>,
            "detected_price":    <float|None>,
            "tolerance_ladder":  {"high": 0.02, "medium": 0.03, "low": 0.05},
            "candidates":        <list>,
            "best":              <candidate|None>,
            "ambiguous":         <list>,
            "resolved_at":       <iso8601>,
            "source":            "the_odds_api_v4",
            "from_cache":        <bool>,
        }
    """
    started = datetime.now(timezone.utc)
    base: dict[str, Any] = {
        "resolution_status": "NOT_FOUND",
        "reason_code":       None,
        "match_id":          (match or {}).get("match_id"),
        "event_id":          None,
        "sport_key":         None,
        "detected_price":    None,
        "tolerance_ladder":  {"high": TOL_HIGH, "medium": TOL_MEDIUM,
                               "low": TOL_LOW},
        "candidates":        [],
        "best":              None,
        "ambiguous":         [],
        "resolved_at":       started.isoformat(),
        "source":            SOURCE_NAME,
        "from_cache":        False,
    }

    # ── Input validation ───────────────────────────────────────────────
    try:
        dp = float(detected_price)
    except (TypeError, ValueError):
        base["resolution_status"] = "INVALID_INPUT"
        base["reason_code"]       = "DETECTED_PRICE_INVALID"
        return base
    if dp <= 1.0:
        base["resolution_status"] = "INVALID_INPUT"
        base["reason_code"]       = "DETECTED_PRICE_BELOW_MIN"
        return base
    if not isinstance(match, dict) or not match.get("home_team") or not match.get("away_team"):
        base["resolution_status"] = "INVALID_INPUT"
        base["reason_code"]       = "MATCH_INFO_INCOMPLETE"
        return base
    base["detected_price"] = dp

    # ── Cache short-circuit ────────────────────────────────────────────
    mid = base["match_id"]
    if use_cache and mid:
        cached = await _read_cached_resolution(db, str(mid), dp)
        if cached:
            cached.pop("_id", None)
            cached["from_cache"] = True
            cached.setdefault("resolution_status", "CACHED")
            return cached

    # ── Event ID resolution (Sprint E.1 cache + Odds API events) ───────
    fetch_evs = fetch_events or the_odds_api.fetch_events
    fetch_cur = fetch_current_odds or the_odds_api.fetch_current_odds
    sport_keys_list = list(sport_keys) if sport_keys else list(lom.DEFAULT_SPORTS)

    mapping = await _ensure_event_id_for_match(
        db, match=match, sport_keys=sport_keys_list, fetch_events=fetch_evs,
    )
    if not mapping or not mapping.get("event_id"):
        base["resolution_status"] = "MATCH_NOT_FOUND"
        base["reason_code"]       = "ODDS_EVENT_ID_MISSING"
        if mid:
            await _persist_resolution(db, payload={
                **base, "_id": str(uuid.uuid4()),
                "resolved_at": datetime.now(timezone.utc),
            })
        return base
    base["event_id"]  = mapping["event_id"]
    base["sport_key"] = mapping.get("sport_key")

    # ── Fetch current odds for the resolved event ──────────────────────
    payload = await fetch_cur(
        sport=base["sport_key"],
        regions=regions,
        markets=",".join(markets_priority),
        event_ids=[base["event_id"]],
    )
    if not payload:
        base["resolution_status"] = "API_UNAVAILABLE"
        base["reason_code"]       = "FETCH_CURRENT_ODDS_FAILED"
        if mid:
            await _persist_resolution(db, payload={
                **base, "_id": str(uuid.uuid4()),
                "resolved_at": datetime.now(timezone.utc),
            })
        return base

    events = payload.get("events") or []
    event_payload = next((ev for ev in events if ev.get("id") == base["event_id"]),
                          None)
    if not event_payload:
        base["resolution_status"] = "API_UNAVAILABLE"
        base["reason_code"]       = "EVENT_NOT_IN_RESPONSE"
        if mid:
            await _persist_resolution(db, payload={
                **base, "_id": str(uuid.uuid4()),
                "resolved_at": datetime.now(timezone.utc),
            })
        return base

    # ── Candidate extraction + summary ─────────────────────────────────
    candidates = extract_candidates_from_event(
        event_payload=event_payload,
        detected_price=dp,
        home_team=match.get("home_team") or "",
        away_team=match.get("away_team") or "",
        markets_priority=markets_priority,
    )
    summary = summarise_candidates(candidates)
    base["candidates"]        = candidates
    base["best"]              = summary["best"]
    base["ambiguous"]         = summary["ambiguous"]
    base["resolution_status"] = summary["resolution_status"]
    if base["resolution_status"] == "RESOLVED":
        base["reason_code"] = "MARKET_IDENTITY_RESOLVED_BY_THE_ODDS_API"
    elif base["resolution_status"] == "AMBIGUOUS":
        base["reason_code"] = "MARKET_IDENTITY_AMBIGUOUS_REQUIRES_USER_CHOICE"
    else:
        base["reason_code"] = "NO_CANDIDATE_WITHIN_TOLERANCE"

    # ── Persist for audit + reuse cache ────────────────────────────────
    if mid:
        persist_doc = {
            **base,
            "_id":         str(uuid.uuid4()),
            "resolved_at": datetime.now(timezone.utc),
        }
        await _persist_resolution(db, payload=persist_doc)
    return base


__all__ = [
    "COLLECTION_NAME", "SOURCE_NAME",
    "TOL_HIGH", "TOL_MEDIUM", "TOL_LOW",
    "DEFAULT_MARKETS_PRIORITY", "DEFAULT_REGIONS", "CACHE_TTL_SECONDS",
    "_confidence_for_delta",
    "_map_outcome_to_manual_identity",
    "extract_candidates_from_event",
    "summarise_candidates",
    "resolve_market_identity",
]
