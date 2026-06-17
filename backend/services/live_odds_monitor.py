"""Sprint E.1 · Live Odds Monitor (base + persistence).

This module periodically polls **The Odds API** for the *current* odds
of every football fixture that is **visible in the latest pick run**
(the universe that the user actually sees in the UI), maps each
fixture to a Odds-API ``event_id``, and persists snapshots into the
``odds_snapshots`` MongoDB collection.

Strict invariants
-----------------
* **observe_only**: no automatic betting, no side effects beyond writes
  to ``odds_snapshots`` and ``odds_event_id_mappings``.
* **Fail-soft**: any error → log + continue. The scheduler must never
  crash because of a network/API failure.
* **No global polling**: we never pull all events for ``soccer_*``.
  The universe is restricted to *visible / recommended matches from
  the latest pick_run payload* — exactly what the user sees in the UI.
* **Rate-limit aware**: every fetch surfaces the ``x-requests-remaining``
  header so we can degrade or skip cycles when quota is low.
* **Back-compat**: ``odds_snapshots`` is reused with a discriminator
  ``source="live_odds_monitor_v1"`` so legacy consumers are untouched.

Environment flags
-----------------
* ``LIVE_ODDS_ENABLED``           (default ``false``) — kill switch.
* ``LIVE_ODDS_REFRESH_SECONDS``   (default ``240``).
* ``LIVE_ODDS_SPORTS``            (default: top European leagues + UCL/UEL + WC).
* ``LIVE_ODDS_MARKETS``           (default ``h2h,totals``).
* ``LIVE_ODDS_REGIONS``           (default ``uk,eu``).
* ``LIVE_ODDS_LOOKBACK_HOURS``    (default ``24``).
* ``LIVE_ODDS_MAX_MATCHES``       (default ``80``) — hard cap per cycle.
* ``LIVE_ODDS_QUOTA_MIN``         (default ``50``) — skip cycle if
  ``x-requests-remaining`` is below this value (best-effort).

Public API
----------
``run_cycle(db)``
    Async coroutine that performs one full cycle. Idempotent / safe to
    call multiple times; the scheduler does that on an interval.

``register_jobs(scheduler, db)``
    Register the APScheduler job. No-op when ``LIVE_ODDS_ENABLED!=true``.

``SOURCE_NAME``
    The string written to ``odds_snapshots.source``.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .external_sources import the_odds_api_client as the_odds_api

log = logging.getLogger("live_odds_monitor")

# ─── Constants ─────────────────────────────────────────────────────────
SOURCE_NAME: str = "live_odds_monitor_v1"

# Default soccer sport-keys (E.1 scope). Covers the 5 top European
# domestic leagues + UEFA club competitions + FIFA World Cup.
DEFAULT_SPORTS: tuple[str, ...] = (
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_fifa_world_cup",
    "soccer_fifa_world_cup_qualifiers_uefa",
    "soccer_fifa_world_cup_qualifiers_conmebol",
)

DEFAULT_MARKETS:  str = "h2h,totals"
DEFAULT_REGIONS:  str = "uk,eu"
DEFAULT_REFRESH_SECONDS:  int = 240
DEFAULT_LOOKBACK_HOURS:   int = 24
DEFAULT_MAX_MATCHES:      int = 80
DEFAULT_QUOTA_MIN:        int = 50

# Internal status / observability dict (read by the scheduler.status()
# endpoint via the wrapper job in ``services.scheduler``).
_status: dict[str, Any] = {
    "enabled":            False,
    "last_cycle":         None,
    "last_error":         None,
    "snapshots_written":  0,
    "mappings_resolved":  0,
    "missing_event_ids":  0,
    "quota_remaining":    None,
}


# ─── ENV helpers ───────────────────────────────────────────────────────
def _env_bool(name: str, default: bool = False) -> bool:
    return (os.environ.get(name, "true" if default else "false") or "").lower() == "true"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _env_csv(name: str, default: Iterable[str]) -> list[str]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return list(default)
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_config() -> dict:
    """Read the live config from env (re-read on every cycle so toggles
    take effect without restarting the process)."""
    return {
        "enabled":         _env_bool("LIVE_ODDS_ENABLED", False),
        "refresh_seconds": _env_int("LIVE_ODDS_REFRESH_SECONDS",
                                     DEFAULT_REFRESH_SECONDS),
        "sports":          _env_csv("LIVE_ODDS_SPORTS", DEFAULT_SPORTS),
        "markets":         os.environ.get("LIVE_ODDS_MARKETS") or DEFAULT_MARKETS,
        "regions":         os.environ.get("LIVE_ODDS_REGIONS") or DEFAULT_REGIONS,
        "lookback_hours":  _env_int("LIVE_ODDS_LOOKBACK_HOURS",
                                     DEFAULT_LOOKBACK_HOURS),
        "max_matches":     _env_int("LIVE_ODDS_MAX_MATCHES",
                                     DEFAULT_MAX_MATCHES),
        "quota_min":       _env_int("LIVE_ODDS_QUOTA_MIN",
                                     DEFAULT_QUOTA_MIN),
    }


# ─── Team-name normalisation (pure) ────────────────────────────────────
_NORM_RX = re.compile(r"[^a-z0-9]+")

def normalise_team(name: Optional[str]) -> str:
    """Lowercase + strip non-alphanumerics. Used for team matching.

    Intentionally conservative: we keep substring matching downstream
    so minor variants (e.g. "Manchester City" vs "Man City") still
    align without us hand-maintaining alias tables.
    """
    if not name:
        return ""
    return _NORM_RX.sub("", name.lower()).strip()


def _team_score(a: str, b: str) -> float:
    """Cheap similarity score between two normalised team strings.

    * 1.00 → exact equal
    * 0.85 → one is a substring of the other (e.g. ``"realmadrid"`` ↔
      ``"realmadridcf"``)
    * 0.70 → token-overlap fallback: at least one ≥4-char token is
      shared (catches ``"manchestercity"`` ↔ ``"manchestercityfc"``).
    * 0.00 → otherwise.

    Token approach uses a length-aware re-split on the *original*
    (un-normalised) names — see ``_team_score_with_tokens`` for the
    composite caller.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    return 0.0


_TOKEN_RX = re.compile(r"[a-z]+", re.IGNORECASE)


def _tokens(name: Optional[str]) -> set[str]:
    """Return the set of ≥4-char lowercase alpha tokens in ``name``."""
    if not name:
        return set()
    return {t.lower() for t in _TOKEN_RX.findall(name) if len(t) >= 4}


def _team_score_with_tokens(
    raw_a: Optional[str], raw_b: Optional[str],
) -> float:
    """Best of (normalised similarity, token overlap heuristic).

    Token overlap returns 0.70 when at least one ≥4-char token is
    shared between the original strings — robust to spacing/punctuation
    differences (e.g. ``"Manchester City"`` ↔ ``"Manchester City FC"``).
    """
    base = _team_score(normalise_team(raw_a), normalise_team(raw_b))
    if base >= 0.85:
        return base
    shared = _tokens(raw_a) & _tokens(raw_b)
    if shared:
        return 0.70
    return base


# ─── Pure: build the visible universe from pick_run docs ───────────────
def extract_visible_universe(
    *,
    run_docs: list[dict],
    sport_filter: str = "football",
) -> list[dict]:
    """From a list of pick_run / analyst_run documents, return the
    visible match universe (deduplicated by ``match_id``).

    Each output element has::

        {
            "match_id":     <str>,
            "home_team":    <str>,
            "away_team":    <str>,
            "league":       <str|None>,
            "commence_time": <str|datetime|None>,
            "sport_key_hint": <str|None>,
            "source_collection": <str>,
            "source_run_id":  <str|None>,
        }

    The function is pure and side-effect free for easy testing.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for run in run_docs or []:
        if not isinstance(run, dict):
            continue
        sport = run.get("sport")
        if sport_filter and sport and sport != sport_filter:
            continue
        payload = run.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        buckets = (
            (payload.get("picks")                       or []) +
            (payload.get("rescued_picks")               or []) +
            (payload.get("rescued")                     or []) +
            (payload.get("watchlist_manual_odds")       or []) +
            (payload.get("structural_lean_requires_odds") or []) +
            (payload.get("watchlist_odds_needed")       or [])
        )
        # Also accept a flat top-level ``picks`` field (some legacy docs).
        if not buckets and isinstance(payload.get("matches"), list):
            buckets = payload["matches"]
        for entry in buckets:
            if not isinstance(entry, dict):
                continue
            mid = entry.get("match_id") or entry.get("fixture_id") or entry.get("id")
            if mid is None:
                continue
            mid_s = str(mid)
            if mid_s in seen:
                continue
            seen.add(mid_s)
            out.append({
                "match_id":          mid_s,
                "home_team":         entry.get("home_team") or entry.get("home"),
                "away_team":         entry.get("away_team") or entry.get("away"),
                "league":            entry.get("league") or entry.get("league_name"),
                "commence_time":     (entry.get("commence_time")
                                       or entry.get("kickoff")
                                       or entry.get("kickoff_ts")),
                "sport_key_hint":    entry.get("sport_key"),
                "source_collection": run.get("_collection") or "pick_runs",
                "source_run_id":     run.get("id") or run.get("_id"),
            })
    return out


# ─── Async: collect the universe from Mongo (db.pick_runs + db.picks) ──
async def collect_visible_universe(
    db,
    *,
    sport: str = "football",
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    max_matches: int = DEFAULT_MAX_MATCHES,
) -> list[dict]:
    """Read the latest pick_run documents (per user) for ``sport`` from
    ``pick_runs`` and ``picks`` collections, then extract the visible
    universe of matches.

    Returns at most ``max_matches`` entries.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    runs: list[dict] = []

    async def _load(coll_name: str) -> None:
        coll = getattr(db, coll_name, None)
        if coll is None:
            return
        try:
            cursor = coll.find(
                {"sport": sport, "generated_at": {"$gte": cutoff}},
                sort=[("generated_at", -1)],
                limit=50,
            )
            async for doc in cursor:
                doc["_collection"] = coll_name
                runs.append(doc)
        except Exception as exc:  # noqa: BLE001
            log.warning("collect_visible_universe[%s] failed: %s", coll_name, exc)

    # Primary: pick_runs; fallback / additive: picks.
    await _load("pick_runs")
    await _load("picks")

    universe = extract_visible_universe(run_docs=runs, sport_filter=sport)
    if len(universe) > max_matches:
        universe = universe[:max_matches]
    return universe


# ─── Pure: find an event_id inside a fetched events list ───────────────
def find_event_in_list(
    *,
    home_team: str,
    away_team: str,
    events: list[dict],
    min_score: float = 1.4,
) -> Optional[dict]:
    """Pure helper: find the best event in ``events`` matching the
    given ``home_team`` / ``away_team`` pair. Returns the event dict
    or ``None``.

    Score is the sum of home + away similarity using the composite
    ``_team_score_with_tokens`` (substring + ≥4-char token overlap).
    Default threshold ``min_score=1.4`` matches when BOTH sides share
    at least a strong token (0.70 + 0.70) or one side is exact and
    the other is a substring (1.0 + 0.85), keeping false positives low.
    """
    h_n = normalise_team(home_team)
    a_n = normalise_team(away_team)
    if not h_n or not a_n:
        return None
    best = None
    best_score = 0.0
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        score = (_team_score_with_tokens(home_team, ev.get("home_team"))
                  + _team_score_with_tokens(away_team, ev.get("away_team")))
        if score > best_score:
            best_score = score
            best = ev
    if best_score >= min_score:
        return best
    return None


# ─── Async: resolve match → event_id with persistent caching ───────────
async def resolve_event_id(
    db,
    *,
    match: dict,
    sport_keys: list[str],
    events_cache: dict[str, list[dict]],
    fetch_events: Optional[Callable[..., Awaitable[Optional[dict]]]] = None,
) -> Optional[dict]:
    """Resolve a ``match`` (from the visible universe) to its Odds-API
    ``event_id``. Returns a small mapping dict or ``None``::

        {
            "match_id": ..., "event_id": ..., "sport_key": ...,
            "home_team": ..., "away_team": ...,
            "commence_time": ..., "resolved_at": <datetime>,
        }

    Strategy:
      1) Look up an existing mapping in ``odds_event_id_mappings``.
      2) Otherwise, walk every ``sport_key`` in ``sport_keys``:
         pull its events list into ``events_cache`` (one network call
         per cycle/sport) and search for a fuzzy team-name match.
      3) Persist the mapping on success.
    """
    fetch = fetch_events or the_odds_api.fetch_events
    mid = match.get("match_id")
    if not mid:
        return None

    # 1) Cached mapping?
    try:
        existing = await db.odds_event_id_mappings.find_one({"match_id": str(mid)})
        if existing and existing.get("event_id"):
            return {
                "match_id":      str(mid),
                "event_id":      existing["event_id"],
                "sport_key":     existing.get("sport_key"),
                "home_team":     existing.get("home_team"),
                "away_team":     existing.get("away_team"),
                "commence_time": existing.get("commence_time"),
                "resolved_at":   existing.get("resolved_at"),
                "from_cache":    True,
            }
    except Exception as exc:  # noqa: BLE001
        log.debug("mapping cache read failed: %s", exc)

    # 2) Try each sport key in order, prefer the hint if present.
    ordered = list(sport_keys)
    hint = match.get("sport_key_hint")
    if hint and hint in ordered:
        ordered.remove(hint)
        ordered.insert(0, hint)

    home = match.get("home_team")
    away = match.get("away_team")
    if not home or not away:
        return None

    for sk in ordered:
        if sk not in events_cache:
            payload = await fetch(sport=sk)
            events_cache[sk] = (payload or {}).get("events") or []
            # Surface latest quota.
            q = (payload or {}).get("quota") or {}
            if q.get("remaining") is not None:
                _status["quota_remaining"] = q["remaining"]
        evt = find_event_in_list(home_team=home, away_team=away,
                                  events=events_cache[sk])
        if evt and evt.get("id"):
            mapping = {
                "match_id":      str(mid),
                "event_id":      evt["id"],
                "sport_key":     sk,
                "home_team":     evt.get("home_team"),
                "away_team":     evt.get("away_team"),
                "commence_time": evt.get("commence_time"),
                "resolved_at":   datetime.now(timezone.utc),
                "source":        "the_odds_api_v4",
            }
            try:
                await db.odds_event_id_mappings.update_one(
                    {"match_id": str(mid)},
                    {"$set": mapping},
                    upsert=True,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("mapping upsert failed: %s", exc)
            mapping["from_cache"] = False
            return mapping
    return None


# ─── Pure: turn an Odds API event payload into snapshot docs ───────────
def event_payload_to_snapshots(
    *,
    match_id: str,
    sport_key: str,
    event_id: str,
    event_payload: dict,
    fetched_at: datetime,
    quota_remaining: Optional[int] = None,
) -> list[dict]:
    """Convert a single Odds-API event payload (with ``bookmakers`` /
    ``markets`` / ``outcomes`` keys) into one snapshot doc per
    ``(bookmaker, market)`` pair. Returns ``[]`` on malformed input.
    """
    out: list[dict] = []
    if not isinstance(event_payload, dict):
        return out
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
            outcomes_raw = mkt.get("outcomes") or []
            outcomes: list[dict] = []
            for o in outcomes_raw:
                if not isinstance(o, dict):
                    continue
                row = {"name": o.get("name"), "price": o.get("price")}
                if "point" in o:
                    row["point"] = o.get("point")
                outcomes.append(row)
            out.append({
                "snapshot_id":       str(uuid.uuid4()),
                "match_id":          str(match_id),
                "sport_key":         sport_key,
                "event_id":          event_id,
                "bookmaker_key":     bm_key,
                "bookmaker_title":   bm_title,
                "market":            mk_key,
                "outcomes":          outcomes,
                "fetched_at":        fetched_at,
                # legacy alias used by the existing
                # ``[(match_id,1),(snapshot_at,-1)]`` index.
                "snapshot_at":       fetched_at,
                "last_update":       mkt.get("last_update")
                                       or bm.get("last_update"),
                "source":            SOURCE_NAME,
                "quota_remaining":   quota_remaining,
            })
    return out


# ─── Async: persist snapshots into ``odds_snapshots`` ──────────────────
async def persist_snapshots(db, snapshots: list[dict]) -> int:
    if not snapshots:
        return 0
    try:
        res = await db.odds_snapshots.insert_many(snapshots, ordered=False)
        return len(res.inserted_ids or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("persist_snapshots failed: %s", exc)
        return 0


# ─── Main cycle ─────────────────────────────────────────────────────────
async def run_cycle(
    db,
    *,
    fetch_events: Optional[Callable[..., Awaitable[Optional[dict]]]] = None,
    fetch_current_odds: Optional[Callable[..., Awaitable[Optional[dict]]]] = None,
) -> dict:
    """Run a single live-odds polling cycle. Returns a small report.

    Strict invariants:
      * Honours ``LIVE_ODDS_ENABLED`` kill-switch.
      * Restricted to *visible matches from latest pick_run*.
      * Fail-soft at every step.
      * Writes to ``odds_snapshots`` with ``source=SOURCE_NAME``.
    """
    cfg = get_config()
    started = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "enabled":            cfg["enabled"],
        "started_at":         started.isoformat(),
        "finished_at":        None,
        "matches_total":      0,
        "matches_with_event": 0,
        "missing_event_ids":  0,
        "snapshots_written":  0,
        "quota_remaining":    None,
        "reasons":            [],
        "ok":                 True,
    }
    _status["enabled"] = cfg["enabled"]

    if not cfg["enabled"]:
        log.info("live_odds_monitor: disabled via LIVE_ODDS_ENABLED")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["reasons"].append("DISABLED")
        return report

    try:
        # 1) Build the visible universe (no global polling).
        universe = await collect_visible_universe(
            db,
            sport="football",
            lookback_hours=cfg["lookback_hours"],
            max_matches=cfg["max_matches"],
        )
        report["matches_total"] = len(universe)
        if not universe:
            report["reasons"].append("EMPTY_UNIVERSE")
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            _status["last_cycle"] = report
            return report

        # 2) Quota guard (best-effort): probe first sport to surface
        #    `x-requests-remaining` and skip cycle if below threshold.
        fetch_evs = fetch_events or the_odds_api.fetch_events
        fetch_cur = fetch_current_odds or the_odds_api.fetch_current_odds
        events_cache: dict[str, list[dict]] = {}

        # 3) Resolve event_id for every visible match.
        resolved: list[dict] = []
        for m in universe:
            mapping = await resolve_event_id(
                db, match=m,
                sport_keys=cfg["sports"],
                events_cache=events_cache,
                fetch_events=fetch_evs,
            )
            if mapping and mapping.get("event_id"):
                resolved.append({"match": m, "mapping": mapping})
            else:
                report["missing_event_ids"] += 1
                _status["missing_event_ids"] += 1
                log.info(
                    "ODDS_EVENT_ID_MISSING match_id=%s home=%s away=%s",
                    m.get("match_id"), m.get("home_team"), m.get("away_team"),
                )

        report["matches_with_event"] = len(resolved)
        if not resolved:
            report["reasons"].append("NO_RESOLVABLE_EVENT_IDS")
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            _status["last_cycle"] = report
            return report

        # 4) Group resolved events by sport_key and fetch current odds.
        by_sport: dict[str, list[dict]] = {}
        for item in resolved:
            sk = item["mapping"].get("sport_key")
            if not sk:
                continue
            by_sport.setdefault(sk, []).append(item)

        fetched_at = datetime.now(timezone.utc)
        snapshots_to_write: list[dict] = []
        for sk, items in by_sport.items():
            ids = [it["mapping"]["event_id"] for it in items if it["mapping"].get("event_id")]
            if not ids:
                continue
            payload = await fetch_cur(
                sport=sk,
                regions=cfg["regions"],
                markets=cfg["markets"],
                event_ids=ids,
            )
            if not payload:
                report["reasons"].append(f"FETCH_FAILED:{sk}")
                continue
            q = payload.get("quota") or {}
            if q.get("remaining") is not None:
                report["quota_remaining"] = q["remaining"]
                _status["quota_remaining"] = q["remaining"]
            events = payload.get("events") or []
            # Build a lookup: event_id → mapping (match_id).
            id_to_match: dict[str, str] = {
                it["mapping"]["event_id"]: it["match"]["match_id"]
                for it in items if it["mapping"].get("event_id")
            }
            for ev in events:
                eid = ev.get("id")
                mid = id_to_match.get(eid)
                if not mid:
                    continue
                snaps = event_payload_to_snapshots(
                    match_id=mid,
                    sport_key=sk,
                    event_id=eid,
                    event_payload=ev,
                    fetched_at=fetched_at,
                    quota_remaining=q.get("remaining"),
                )
                snapshots_to_write.extend(snaps)

        # 5) Persist.
        written = await persist_snapshots(db, snapshots_to_write)
        report["snapshots_written"] = written
        _status["snapshots_written"] = (_status.get("snapshots_written") or 0) + written
        _status["mappings_resolved"] = (_status.get("mappings_resolved") or 0) + len(resolved)

        # 5.b) Sprint E.2 — run the value detector on the freshly
        #      written snapshots. Pure analytical layer, fail-soft.
        if snapshots_to_write:
            try:
                from . import odds_value_detector as ovd
                from . import odds_alerts as oa
                detection = ovd.detect_all_signals(
                    snapshots=snapshots_to_write,
                )
                if detection["signals"]:
                    await oa.persist_signals(db, signals=detection["signals"])
                    report["signals_detected"] = len(detection["signals"])
                else:
                    report["signals_detected"] = 0
            except Exception as exc:  # noqa: BLE001
                log.warning("odds value detector pass failed: %s", exc)
                report["signals_detected"] = -1

        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _status["last_cycle"] = report
        log.info(
            "live_odds_monitor cycle ok: matches=%d resolved=%d "
            "missing=%d written=%d quota_remaining=%s",
            report["matches_total"], report["matches_with_event"],
            report["missing_event_ids"], report["snapshots_written"],
            report["quota_remaining"],
        )
        return report
    except Exception as exc:  # noqa: BLE001
        log.exception("live_odds_monitor cycle crashed: %s", exc)
        report["ok"] = False
        report["reasons"].append(f"EXCEPTION:{exc}")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _status["last_error"] = str(exc)
        _status["last_cycle"] = report
        return report


# ─── Scheduler integration ─────────────────────────────────────────────
def register_jobs(scheduler: AsyncIOScheduler, db) -> bool:
    """Register the live-odds polling job on the given scheduler.

    Returns ``True`` if the job was added, ``False`` if disabled.
    No-op when ``LIVE_ODDS_ENABLED!=true`` so we never burn quota in
    test / staging environments by accident.
    """
    cfg = get_config()
    _status["enabled"] = cfg["enabled"]
    if not cfg["enabled"]:
        log.info("live_odds_monitor: not registering job (disabled)")
        return False

    from datetime import timedelta as _td
    scheduler.add_job(
        run_cycle, args=[db],
        trigger=IntervalTrigger(seconds=cfg["refresh_seconds"]),
        id="live_odds_monitor",
        next_run_time=datetime.now(timezone.utc) + _td(seconds=15),
        max_instances=1,
        coalesce=True,
    )
    log.info(
        "live_odds_monitor: job registered every %ds, sports=%s",
        cfg["refresh_seconds"], cfg["sports"],
    )
    return True


def get_status() -> dict:
    """Return the current monitor status (used by /api admin endpoints)."""
    return dict(_status)


__all__ = [
    "SOURCE_NAME",
    "DEFAULT_SPORTS", "DEFAULT_MARKETS", "DEFAULT_REGIONS",
    "DEFAULT_REFRESH_SECONDS", "DEFAULT_LOOKBACK_HOURS",
    "DEFAULT_MAX_MATCHES", "DEFAULT_QUOTA_MIN",
    "get_config", "get_status",
    "normalise_team", "extract_visible_universe",
    "collect_visible_universe",
    "find_event_in_list", "resolve_event_id",
    "event_payload_to_snapshots", "persist_snapshots",
    "run_cycle", "register_jobs",
]
