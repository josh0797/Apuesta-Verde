"""Live Match Lifecycle — strict validation + expiration of live matches.

Why this exists
---------------
Live picks rely on a STREAM of matches that are genuinely in-play. If the
DB still says "is_live=True" for a match that ended five days ago we
end up rendering stale 90' cards, recommending picks that can't be
placed, and burning user trust.

This module is the single source of truth for "is this match still
live, *right now*?" used by:

  • GET /api/matches/live            (server.py)
  • POST /api/live/reevaluate         (server.py)
  • ingest_live()                     (services/data_ingestion.py)
  • The live sweeper job              (services/job_queue.py / scheduler)

Public API
----------
    LIVE_STATUSES[sport]               → set[str]   in-play statuses
    FINISHED_STATUSES[sport]           → set[str]   terminal statuses
    LIVE_CACHE_TTL_SEC[sport]          → int        client TTL hint
    HEARTBEAT_STALE_SEC[sport]         → int        backend stale threshold

    is_match_live(match, *, now=None)        → bool
    is_match_expired(match, *, now=None)     → bool
    compute_live_state(match, *, now=None)   → dict (state + reason + minute)
    compute_freshness(match, *, now=None)    → dict (score 0-100 + label)
    sweep_expired_live(db, *, sport=None)    → int  (async) — mark stale matches `is_live=False`
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

log = logging.getLogger(__name__)


# ─── Status enums per sport ────────────────────────────────────────────────
# Sources: API-Sports football fixture status codes (v3).
LIVE_STATUSES: dict[str, set[str]] = {
    "football": {"1H", "HT", "2H", "ET", "BT", "P", "LIVE"},
    "basketball": {"Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT", "LIVE", "IN_PLAY", "in_play"},
    "baseball": {
        "IN1", "IN2", "IN3", "IN4", "IN5", "IN6", "IN7", "IN8", "IN9",
        "BT", "MID", "END", "BRK", "LIVE", "in_play", "IN_PLAY",
        "Top 1st", "Bottom 1st", "Top 2nd", "Bottom 2nd", "Top 3rd", "Bottom 3rd",
        "Top 4th", "Bottom 4th", "Top 5th", "Bottom 5th", "Top 6th", "Bottom 6th",
        "Top 7th", "Bottom 7th", "Top 8th", "Bottom 8th", "Top 9th", "Bottom 9th",
    },
}

FINISHED_STATUSES: dict[str, set[str]] = {
    "football": {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO", "NS", "TBD", "SUSP", "INT"},
    "basketball": {"FT", "FINAL", "Final", "ENDED", "POST", "CANC", "AOT", "AWD"},
    "baseball": {"FT", "FINAL", "Final", "ENDED", "POST", "CANC", "AWD", "SUSP", "Completed"},
}

# How long does the frontend cache an `/api/matches/live` response?
LIVE_CACHE_TTL_SEC: dict[str, int] = {
    "football":   60,
    "basketball": 30,
    "baseball":   45,
}

# How long since `updated_at` before the backend considers a match stale
# regardless of stored is_live flag.
HEARTBEAT_STALE_SEC: dict[str, int] = {
    "football":   10 * 60,   # 10 min
    "basketball":  5 * 60,   # 5  min  (NBA clock changes fast)
    "baseball":   10 * 60,   # 10 min  (slower-paced)
}

# Football overtime safety net (regulation + injury + 100% buffer).
FOOTBALL_HARD_MINUTE_CAP = 105   # past this we consider it over even in ET


def _norm_sport(sport: Optional[str]) -> str:
    s = (sport or "football").strip().lower()
    return s if s in LIVE_STATUSES else "football"


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _heartbeat_age_sec(match: dict, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds since the doc's last `updated_at` heartbeat."""
    n = _now(now)
    ts = _parse_iso(match.get("updated_at"))
    if ts is None:
        return None
    return max(0.0, (n - ts).total_seconds())


def _live_minute(match: dict) -> Optional[int]:
    live = match.get("live_stats") or {}
    m = live.get("minute")
    if isinstance(m, (int, float)):
        return int(m)
    # Some basketball/baseball feeds use period/inning instead of minute.
    return None


def _status_short(match: dict) -> Optional[str]:
    return match.get("status_short") or (match.get("live_stats") or {}).get("status")


# ─── Core predicates ────────────────────────────────────────────────────────

def is_match_live(match: dict, *, now: Optional[datetime] = None) -> bool:
    """True iff the match is GENUINELY in-play right now.

    A match passes ONLY when ALL of the following hold:
      1. `status_short` ∈ LIVE_STATUSES[sport]                 (status valid)
      2. `status_short` ∉ FINISHED_STATUSES[sport]             (not terminal)
      3. football: minute is None OR minute < FOOTBALL_HARD_MINUTE_CAP
      4. football: not (status=='2H' and minute >= 95)         (95'+ stale)
      5. heartbeat age ≤ HEARTBEAT_STALE_SEC[sport]
      6. `is_live` flag explicitly True (defensive — required so a
         stored-but-finished doc never leaks)
    """
    if not isinstance(match, dict):
        return False
    sport = _norm_sport(match.get("sport"))
    status = _status_short(match)
    if not status:
        return False

    finished = FINISHED_STATUSES.get(sport, set())
    if status in finished:
        return False

    live_set = LIVE_STATUSES.get(sport, set())
    if status not in live_set:
        return False

    # Heartbeat freshness — primary defence against zombie records.
    age = _heartbeat_age_sec(match, now)
    if age is None:
        # No heartbeat means we have no proof it's alive → treat as stale.
        return False
    if age > HEARTBEAT_STALE_SEC.get(sport, 600):
        return False

    # Football minute caps
    if sport == "football":
        minute = _live_minute(match)
        if minute is not None:
            if minute >= FOOTBALL_HARD_MINUTE_CAP:
                return False
            # 2H + minute>=95 is "stale 90'" — historical bug we explicitly fix.
            if status == "2H" and minute >= 95:
                return False
            # NEW (P0-3 hardening): 2H + minute>=90 with NO recent heartbeat
            # is effectively a ghost-FT — API-Sports dropped the match without
            # flipping status to FT. We tighten the heartbeat threshold at
            # end-game to 3 min (vs the regular 10 min) so these don't linger.
            if status == "2H" and minute >= 90 and age is not None and age > 180:
                return False
            # ET endgame — same logic but 105' minute cap is already enforced.
            if status == "ET" and minute >= 105 and age is not None and age > 180:
                return False

    # Final defensive check on the persisted flag — if we never marked it
    # live (e.g. it was ingested as upcoming) we should not surface it.
    if match.get("is_live") is False:
        return False

    return True


def is_match_expired(match: dict, *, now: Optional[datetime] = None) -> bool:
    """Opposite of is_match_live(), but also includes matches that simply
    aren't tagged as live (e.g. upcoming) — used by the sweeper to decide
    whether to flip `is_live=True → False` in Mongo.
    """
    if not isinstance(match, dict):
        return True
    if not match.get("is_live"):
        return True
    return not is_match_live(match, now=now)


# ─── Composite live state for the UI ───────────────────────────────────────

def compute_live_state(match: dict, *, now: Optional[datetime] = None) -> dict:
    """Return rich live-state metadata for the frontend.

    Output:
        {
          "state":  "LIVE_ACTIVE" | "LIVE_LATE" | "GARBAGE_TIME"
                    | "HT" | "LIVE_STALE" | "FINISHED" | "NOT_STARTED",
          "minute": int | None,
          "minute_label": "67'"|"HT"|"ET 103'"|"Q2 04:22"|"Top 5th"|None,
          "valid":  bool,         # is_match_live() result
          "reason": str           # human-readable explanation for logs/UI
        }
    """
    sport = _norm_sport(match.get("sport"))
    status = _status_short(match) or ""
    minute = _live_minute(match)
    n = _now(now)
    age = _heartbeat_age_sec(match, n)

    label: Optional[str] = None
    state = "NOT_STARTED"
    reason = ""

    finished = FINISHED_STATUSES.get(sport, set())
    live_set = LIVE_STATUSES.get(sport, set())

    if status in finished:
        state = "FINISHED"
        reason = f"status={status} (terminal)"
    elif status not in live_set:
        state = "NOT_STARTED"
        reason = f"status={status} (not in-play)"
    else:
        # Heartbeat
        if age is None or age > HEARTBEAT_STALE_SEC.get(sport, 600):
            state = "LIVE_STALE"
            reason = f"stale heartbeat ({int(age) if age else '∞'}s)"
        elif sport == "football":
            if status == "HT":
                state, label = "HT", "HT"
                reason = "halftime"
            elif status == "ET":
                state = "LIVE_LATE"
                label = f"ET {minute}'" if minute else "ET"
                reason = "extra time"
            elif minute is not None and minute >= 88:
                state = "GARBAGE_TIME" if minute >= 90 else "LIVE_LATE"
                label = f"{minute}'"
                reason = "near full time"
            else:
                state = "LIVE_ACTIVE"
                label = f"{minute}'" if minute is not None else status
                reason = "in play"
            # Hard caps
            if minute is not None and minute >= FOOTBALL_HARD_MINUTE_CAP:
                state = "FINISHED"
                reason = f"minute {minute} >= cap {FOOTBALL_HARD_MINUTE_CAP}"
                label = None
            elif status == "2H" and minute is not None and minute >= 95:
                state = "LIVE_STALE"
                reason = "stale 2H @ 95+ — feed not refreshing"
                label = f"{minute}' (stale)"
            elif status == "2H" and minute is not None and minute >= 90 and age is not None and age > 180:
                # Ghost-FT: API dropped the match but never sent a FT update.
                # Heartbeat age > 3 min at 90'+ → assume the match has ended.
                state = "LIVE_STALE"
                reason = f"ghost-FT @ 2H {minute}' (heartbeat {int(age)}s)"
                label = f"{minute}' (stale)"
        elif sport == "basketball":
            # Basketball: use period + clock if present
            clock = (match.get("live_stats") or {}).get("clock")
            label = f"{status} {clock}" if clock else status
            state = "LIVE_ACTIVE"
            reason = "in play"
        elif sport == "baseball":
            label = status
            state = "LIVE_ACTIVE"
            reason = "in play"

    valid = (state in ("LIVE_ACTIVE", "LIVE_LATE", "GARBAGE_TIME", "HT"))
    return {
        "state":   state,
        "minute":  minute,
        "minute_label": label,
        "valid":   valid,
        "reason":  reason,
        "status_short": status,
        "heartbeat_age_sec": int(age) if age is not None else None,
    }


# ─── Freshness score ───────────────────────────────────────────────────────

def compute_freshness(match: dict, *, now: Optional[datetime] = None) -> dict:
    """0-100 freshness score with badge label.

    Components:
      • heartbeat age   (50 pts)
      • live stats present (20 pts)
      • odds present (15 pts)
      • status not stale-90 (15 pts)
    """
    sport = _norm_sport(match.get("sport"))
    age = _heartbeat_age_sec(match, now)
    stale_threshold = HEARTBEAT_STALE_SEC.get(sport, 600)

    # Heartbeat — linear decay from 50 (age=0) to 0 (age=stale_threshold)
    if age is None:
        hb_score = 0
    else:
        hb_score = max(0, int(round(50 * (1 - (age / stale_threshold)))))

    # Live stats present
    ls = match.get("live_stats") or {}
    has_score = bool(ls.get("score"))
    has_stats = bool(ls.get("home_stats") or ls.get("away_stats"))
    ls_score = (10 if has_score else 0) + (10 if has_stats else 0)

    # Odds present
    snaps = match.get("odds_snapshots") or []
    odds_score = 15 if (snaps and (snaps[-1] or {}).get("markets")) else 0

    # Not stale-90
    status = _status_short(match) or ""
    minute = _live_minute(match)
    stale_pen = 0
    if sport == "football" and status == "2H" and minute is not None and minute >= 95:
        stale_pen = 15
    status_score = 15 - stale_pen

    total = hb_score + ls_score + odds_score + status_score
    total = max(0, min(100, total))

    if total >= 70:
        label = "DATOS_FRESCOS"
    elif total >= 50:
        label = "DATOS_RETRASADOS"
    elif total >= 30:
        label = "LIVE_STALE"
    else:
        label = "EXPIRED"

    return {
        "score": total,
        "label": label,
        "components": {
            "heartbeat": hb_score,
            "live_stats": ls_score,
            "odds": odds_score,
            "status": status_score,
        },
        "heartbeat_age_sec": int(age) if age is not None else None,
    }


# ─── Sweeper ────────────────────────────────────────────────────────────────

async def sweep_expired_live(db, *, sport: Optional[str] = None) -> int:
    """Flip `is_live=True → False` for matches that are no longer in-play.

    This is the back-stop that prevents stale "90'" cards from showing up
    when API-Sports drops the match from the live feed without us catching
    the FT event.

    Args:
        db:    Motor database.
        sport: optional sport filter ("football" | "basketball" | "baseball").

    Returns:
        Number of matches flipped to is_live=False.
    """
    if db is None:
        return 0
    now = datetime.now(timezone.utc)
    query: dict[str, Any] = {"is_live": True}
    if sport:
        query["sport"] = _norm_sport(sport)
    cursor = db.matches.find(query, {
        "_id": 0, "match_id": 1, "sport": 1, "status_short": 1,
        "live_stats": 1, "updated_at": 1, "is_live": 1,
    })
    to_expire: list[Any] = []
    archived_records: list[dict] = []
    async for m in cursor:
        if is_match_expired(m, now=now):
            to_expire.append(m.get("match_id"))
            archived_records.append({
                "match_id":    m.get("match_id"),
                "sport":       m.get("sport"),
                "status_short": m.get("status_short"),
                "minute":      _live_minute(m),
                "expired_at":  now.isoformat(),
                "reason":      compute_live_state(m, now=now).get("reason"),
            })
    if not to_expire:
        return 0
    res = await db.matches.update_many(
        {"match_id": {"$in": to_expire}, "is_live": True},
        {"$set": {"is_live": False, "expired_at": now.isoformat()}},
    )
    # Best-effort archive — don't fail the sweep if collection is missing.
    try:
        if archived_records:
            await db.archived_live_matches.insert_many(archived_records, ordered=False)
    except Exception:
        pass
    # ── Settlement hook (M2 + M1 alimentación) ────────────────────────
    # Right after a match closes we try to persist the official
    # box-score (final_score + bullpen pitch counts). Best-effort: a
    # failure here NEVER aborts the lifecycle sweep.
    try:
        from .mlb_finished_game_settler import settle_match
        finished_ids = [m for m in to_expire]
        if finished_ids:
            cursor2 = db.matches.find(
                {"match_id": {"$in": finished_ids}, "sport": "baseball"},
                {"match_id": 1, "sport": 1, "game_pk": 1, "settled_at": 1},
            )
            async for doc in cursor2:
                try:
                    await settle_match(db, doc)
                except Exception as exc:
                    log.debug("settle_match in sweep failed: %s", exc)
    except Exception as exc:
        log.debug("settlement hook unavailable: %s", exc)
    return int(res.modified_count or 0)
