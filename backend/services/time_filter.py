"""Time-based match filter — last-line defence against past/finished games
leaking into the analyst pipeline.

This module is the user's hard requirement after the regression where
"Cubs vs Pirates" appeared as a pick even though the match was finished
(Cubs 7 – Pirates 2 the previous day).

Public helpers
==============
    STATUS_FINISHED           : set[str]
    is_match_upcoming(m, buffer_minutes=15) -> bool
    is_match_finished(m) -> bool
    hours_to_kickoff(m) -> float | None
    filter_upcoming(matches) -> (kept, dropped)
    validate_pick_before_output(pick, match_doc) -> pick (with `blocked` / `block_reasons`)

Every helper is **defensive**: when in doubt (missing kickoff_iso, bad
status string), it errs on the side of CAUTION and rejects the match.
We would rather drop a real upcoming match than show a past one again.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

log = logging.getLogger("time_filter")

# Status codes that mean the match has been DECIDED. Aggregated across
# football (FT/AET/PEN/POST/CANC/ABD/AWD), basketball (FT/NS-final),
# baseball (Final, Postponed, Suspended).
STATUS_FINISHED: set[str] = {
    # Football (API-Sports)
    "FT", "AET", "PEN", "POST", "CANC", "ABD", "AWD", "WO",
    # Basketball / NBA
    "Final", "Final/OT", "Final/2OT", "Final/3OT", "Postponed",
    # Baseball / MLB
    "F", "FR", "DR", "Suspended", "Game Over",
    # Lowercase aliases (the various scrapers/normalizers use different
    # casing inconsistently)
    "ft", "aet", "pen", "post", "canc", "final", "postponed", "f",
}


# ────────────────────────────────────────────────────────────────────────────
# Time helpers
# ────────────────────────────────────────────────────────────────────────────
def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string into a tz-aware UTC datetime.
    Returns None when the input is missing or malformed."""
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError):
        return None


def _extract_status(match_doc: dict) -> str:
    """Best-effort extraction of the current status string.
    Handles the variety of shapes our matches use:
      • fixture.status.short (API-Sports football/basketball)
      • live_stats.status (MLB normalizer + custom)
      • status (top-level alias)
    """
    if not isinstance(match_doc, dict):
        return ""
    # Top-level alias first
    s = match_doc.get("status")
    if isinstance(s, str):
        return s
    # API-Sports nested
    fx = match_doc.get("fixture") or {}
    if isinstance(fx, dict):
        st = fx.get("status") or {}
        if isinstance(st, dict):
            short = st.get("short") or st.get("long")
            if short:
                return str(short)
    # MLB-style nested
    live = match_doc.get("live_stats") or {}
    if isinstance(live, dict):
        st = live.get("status")
        if st:
            return str(st)
    return ""


def is_match_finished(match_doc: dict) -> bool:
    """True when the match status is in `STATUS_FINISHED`. Defensive: if
    we can't parse the status, returns False (caller still has
    `is_match_upcoming` as the second check).
    """
    return _extract_status(match_doc) in STATUS_FINISHED


def hours_to_kickoff(match_doc: dict, *, now: Optional[datetime] = None) -> Optional[float]:
    kickoff = _parse_iso(match_doc.get("kickoff_iso"))
    if kickoff is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (kickoff - now).total_seconds() / 3600.0


def is_match_upcoming(
    match_doc: dict,
    *,
    buffer_minutes: int = 15,
    now: Optional[datetime] = None,
) -> bool:
    """True only when the match has NOT started yet (with a small safety
    buffer).

    Rejects when:
      • kickoff_iso is missing OR unparseable.
      • status is one of STATUS_FINISHED.
      • status indicates the match is already live (1H, 2H, IN, HT, BT, ET,
        Live, Top X, Bot X, Inning X) — these are not 'upcoming'.
      • kickoff_iso is in the past (with `buffer_minutes` slack).
    """
    if is_match_finished(match_doc):
        return False
    status = _extract_status(match_doc)
    LIVE_STATUSES = {
        # Football
        "1H", "HT", "2H", "ET", "BT", "P", "INT", "LIVE", "Live",
        # Basketball
        "Q1", "Q2", "Q3", "Q4", "OT", "BT2", "BT3", "BT4",
        # Baseball — anything that starts with "Inn" or numeric digit
    }
    if status in LIVE_STATUSES:
        return False
    if status and (status.lower().startswith("inn")
                    or status.lower().startswith("top")
                    or status.lower().startswith("bot")
                    or status.lower() == "in progress"
                    or status.lower() == "live"):
        return False

    kickoff = _parse_iso(match_doc.get("kickoff_iso"))
    if kickoff is None:
        # Defensive: drop the match. The user explicitly asked for this
        # ("sin fecha → descartar por seguridad").
        return False
    now = now or datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=buffer_minutes)
    return kickoff > cutoff


def filter_upcoming(
    matches: Iterable[dict], *, buffer_minutes: int = 15,
) -> tuple[list[dict], list[dict]]:
    """Split an iterable of match dicts into (upcoming, dropped).
    Returns the same dict refs (no copying). Annotates each `dropped`
    item with a `_filter_drop_reason` for debugging.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    now = datetime.now(timezone.utc)
    for m in matches:
        if not isinstance(m, dict):
            continue
        if is_match_finished(m):
            m["_filter_drop_reason"] = f"finished:{_extract_status(m)}"
            dropped.append(m)
            continue
        if not is_match_upcoming(m, buffer_minutes=buffer_minutes, now=now):
            m["_filter_drop_reason"] = "not_upcoming_or_in_progress_or_no_kickoff"
            dropped.append(m)
            continue
        # Annotate hours_to_kickoff so downstream layers can use it.
        m["hours_to_kickoff"] = round(hours_to_kickoff(m, now=now) or 0.0, 2)
        kept.append(m)
    if dropped:
        reasons = {m.get("_filter_drop_reason", "?") for m in dropped}
        log.info("time_filter: dropped %d / %d matches; reasons=%s",
                 len(dropped), len(dropped) + len(kept), reasons)
    return kept, dropped


# ────────────────────────────────────────────────────────────────────────────
# Pick-level last-line defence
# ────────────────────────────────────────────────────────────────────────────
def validate_pick_before_output(
    pick: dict,
    match_doc: Optional[dict] = None,
    *,
    buffer_minutes: int = 15,
) -> dict:
    """Annotate a single pick with `blocked` / `block_reasons` if it
    violates any of the safety rules. Returns the same `pick` dict
    (mutated). NEVER raises.
    """
    if not isinstance(pick, dict):
        return pick
    blocks: list[str] = []

    ref = match_doc or pick

    # 1. Match already played / finished
    if is_match_finished(ref):
        blocks.append("MATCH_FINISHED")
    elif not is_match_upcoming(ref, buffer_minutes=buffer_minutes):
        blocks.append("MATCH_ALREADY_PLAYED")

    # 2. Under recommended for a game with an OVERPERFORMING pitcher.
    rec    = pick.get("recommendation") or {}
    market = str(rec.get("market") or pick.get("market") or "").lower()
    is_under = ("under" in market) or market in ("nrfi", "team total under")
    if is_under:
        pitcher_signals = pick.get("_pitcher_signals") or []
        # signals can also live inside editorial_context_signals
        codes = {s.get("code") for s in (pick.get("editorial_context_signals") or [])
                  if isinstance(s, dict)}
        codes.update(pitcher_signals or [])
        if "PITCHER_OVERPERFORMING" in codes:
            blocks.append("UNDER_BLOCKED_OVERPERFORMING_PITCHER")

    # 3. Fragility > 60
    frag_obj = pick.get("fragility")
    if isinstance(frag_obj, dict):
        frag_score = frag_obj.get("score")
    else:
        frag_score = pick.get("fragility_score")
    try:
        if frag_score is not None and float(frag_score) > 60:
            blocks.append("HIGH_FRAGILITY")
    except (TypeError, ValueError):
        pass

    pick["blocked"] = len(blocks) > 0
    pick["block_reasons"] = blocks
    return pick


def filter_blocked_picks(
    picks: list[dict],
    matches_by_id: Optional[dict[Any, dict]] = None,
    *,
    buffer_minutes: int = 15,
) -> tuple[list[dict], list[dict]]:
    """Validate every pick, return (kept, blocked_picks).
    `matches_by_id` is an optional lookup of {match_id: match_doc}.
    """
    kept: list[dict] = []
    blocked: list[dict] = []
    for p in picks or []:
        mid = p.get("match_id") or p.get("game_pk") or p.get("id")
        ref = (matches_by_id or {}).get(mid) if matches_by_id else None
        v = validate_pick_before_output(p, ref, buffer_minutes=buffer_minutes)
        (blocked if v.get("blocked") else kept).append(v)
    return kept, blocked


__all__ = [
    "STATUS_FINISHED",
    "is_match_upcoming",
    "is_match_finished",
    "hours_to_kickoff",
    "filter_upcoming",
    "validate_pick_before_output",
    "filter_blocked_picks",
]
