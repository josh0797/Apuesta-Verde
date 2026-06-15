"""Fixture time/status gate — hard barrier before analysis.

Critical bugfix: pre-match analysis must NEVER process matches that are
already finished, postponed, cancelled, in-play, or about to kick off
within the configured buffer. This module is the **single source of
truth** for that decision — `server._is_match_upcoming` and any future
ingest pipeline call ``check_fixture_gate`` instead of re-implementing
the heuristics.

Spec (per user request, F93-followup):

* Block all matches whose ``status_short`` / ``status`` is in
  :data:`FINAL_STATUSES`.
* Require ``start_time > now + PREMATCH_BUFFER_MINUTES`` (default 10 min,
  override via env ``PREMATCH_BUFFER_MINUTES``).
* Always emit a structured discard payload usable by the orchestrator /
  UI / audit log::

      {
        "discard_reason": "FIXTURE_ALREADY_FINISHED" | ...,
        "stage":          "fixture_time_status_gate",
        "status":         "FT",
        "start_time":     "<iso>",
        "now":            "<iso>",
        "match_id":       "...",
        "home":           "...",
        "away":           "..."
      }

Apply this gate BEFORE any of:
  * market identity resolver
  * SportyTrader / Forebet lookup
  * odds enrichment
  * fragility scoring
  * ranking
  * payload ``picks[]`` generation
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("services.fixture_time_status_gate")

# ─────────────────────────────────────────────────────────────────────
# Spec constants — DO NOT add live / not-started states to this set.
# ─────────────────────────────────────────────────────────────────────
FINAL_STATUSES: frozenset[str] = frozenset({
    # API-Sports short codes (football).
    "FT", "AET", "PEN", "FT_PEN",
    "CANC", "PST", "ABD", "AWD", "WO", "SUSP", "INT",
    # Long / generic forms (TheStatsAPI, ESPN, MLB Stats API, basketball).
    "FINAL", "FINISHED", "COMPLETED", "ENDED", "CLOSED",
    "POSTPONED", "CANCELLED", "CANCELED",
    "ABANDONED", "WALKOVER", "SUSPENDED", "DELAYED",
    # Verbose API-Sports labels.
    "MATCH FINISHED", "MATCH CANCELLED", "MATCH CANCELED",
    "MATCH POSTPONED", "MATCH ABANDONED",
})

# Live-game states that must NEVER be analysed as pre-match (independent
# of timing). Exposed as a separate set so callers can distinguish
# "finished" from "already started".
LIVE_STATUSES: frozenset[str] = frozenset({
    # API-Sports short codes.
    "1H", "HT", "2H", "ET", "BT", "P", "LIVE",
    # Long / generic.
    "IN_PLAY", "IN PLAY", "PLAYING", "RUNNING",
    "FIRST HALF", "HALF TIME", "SECOND HALF",
    "EXTRA TIME", "BREAK TIME", "PENALTIES",
})

# Reason codes — kept as constants so call-sites pattern-match cleanly.
RC_ALREADY_FINISHED       = "FIXTURE_ALREADY_FINISHED"
RC_ALREADY_STARTED        = "FIXTURE_ALREADY_STARTED"
RC_KICKOFF_TOO_SOON       = "FIXTURE_KICKOFF_TOO_SOON"
RC_KICKOFF_TIME_MISSING   = "FIXTURE_KICKOFF_TIME_MISSING"
RC_INVALID_INPUT          = "FIXTURE_INVALID_INPUT"
RC_PASSED                 = "FIXTURE_PASSED_GATE"

STAGE = "fixture_time_status_gate"

DEFAULT_PREMATCH_BUFFER_MIN = 10


# ─────────────────────────────────────────────────────────────────────
# Env access
# ─────────────────────────────────────────────────────────────────────
def get_prematch_buffer_minutes() -> int:
    """Read ``PREMATCH_BUFFER_MINUTES`` env at call time (override-friendly).

    Falls back to :data:`DEFAULT_PREMATCH_BUFFER_MIN` for any invalid /
    missing value. Negative values are clamped to 0 (no buffer) to keep
    behaviour predictable.
    """
    raw = os.environ.get("PREMATCH_BUFFER_MINUTES")
    if raw is None or not str(raw).strip():
        return DEFAULT_PREMATCH_BUFFER_MIN
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        log.debug("[fixture_gate] PREMATCH_BUFFER_MINUTES=%r invalid → default %s",
                  raw, DEFAULT_PREMATCH_BUFFER_MIN)
        return DEFAULT_PREMATCH_BUFFER_MIN
    return max(0, v)


# ─────────────────────────────────────────────────────────────────────
# Status canonicalisation
# ─────────────────────────────────────────────────────────────────────
def _norm_status(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def _doc_status_tokens(match_doc: dict) -> list[str]:
    """Collect every status string we can extract from the document, in
    canonical (uppercase, stripped) form. Multiple fields are merged so a
    single guard covers API-Sports ``status_short``, TheStatsAPI ``status``,
    and the nested MLB / basketball ``{"abstract": "Final", ...}`` shape.
    """
    out: list[str] = []
    for key in ("status_short", "status", "fixture_status",
                "match_status", "abstractGameState"):
        v = match_doc.get(key)
        if isinstance(v, str) and v.strip():
            out.append(_norm_status(v))
        elif isinstance(v, dict):
            for nested in v.values():
                if isinstance(nested, str) and nested.strip():
                    out.append(_norm_status(nested))
    # Some legacy docs nest under ``fixture.status.short`` (API-Sports raw).
    fixture = match_doc.get("fixture")
    if isinstance(fixture, dict):
        st = fixture.get("status")
        if isinstance(st, dict):
            for k in ("short", "long"):
                v = st.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(_norm_status(v))
    return out


def _is_terminal_status(tokens: list[str]) -> Optional[str]:
    for t in tokens:
        if t in FINAL_STATUSES:
            return t
    return None


def _is_live_status(tokens: list[str]) -> Optional[str]:
    for t in tokens:
        if t in LIVE_STATUSES:
            return t
    return None


# ─────────────────────────────────────────────────────────────────────
# Start time extraction
# ─────────────────────────────────────────────────────────────────────
def _extract_start_time(match_doc: dict) -> tuple[Optional[datetime], Optional[str]]:
    """Resolve the canonical kickoff datetime (UTC) for the match.

    Returns ``(dt, iso_str)`` where either both are populated or both are
    ``None`` (when no kickoff is found).

    Resolution priority:
      1. ``kickoff_ts`` (UNIX seconds — preferred, the pipeline keeps it
         in sync across ingest paths).
      2. ``kickoff_iso`` / ``date`` / ``commence_time`` / ``start_time`` /
         ``gameDate`` (ISO 8601 strings).
      3. ``fixture.date`` / ``fixture.timestamp`` (API-Sports raw shape).
    """
    # 1) UNIX seconds first.
    kt = match_doc.get("kickoff_ts")
    if kt is None and isinstance(match_doc.get("fixture"), dict):
        kt = match_doc["fixture"].get("timestamp")
    if kt is not None:
        try:
            ts = float(kt)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt, dt.isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            pass

    # 2) ISO 8601 strings.
    iso_candidates: list[Any] = [
        match_doc.get("kickoff_iso"),
        match_doc.get("commence_time"),
        match_doc.get("start_time"),
        match_doc.get("gameDate"),
        match_doc.get("date"),
    ]
    fixture = match_doc.get("fixture")
    if isinstance(fixture, dict):
        iso_candidates.append(fixture.get("date"))

    for cand in iso_candidates:
        if not isinstance(cand, str) or not cand.strip():
            continue
        try:
            normalised = cand.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalised)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt, dt.isoformat()
        except (TypeError, ValueError):
            continue

    return None, None


# ─────────────────────────────────────────────────────────────────────
# Score-based safety net
# ─────────────────────────────────────────────────────────────────────
def _has_persisted_final_score(match_doc: dict) -> bool:
    """Heuristic last-line-of-defence: a match with both team scores
    persisted as integers is definitely past the analysis window even if
    its status field was never refreshed.
    """
    candidates: list[Any] = [
        (match_doc.get("home_score"), match_doc.get("away_score")),
    ]
    home_team = match_doc.get("home_team")
    away_team = match_doc.get("away_team")
    if isinstance(home_team, dict) and isinstance(away_team, dict):
        candidates.append((home_team.get("score"), away_team.get("score")))
    goals = match_doc.get("goals")
    if isinstance(goals, dict):
        candidates.append((goals.get("home"), goals.get("away")))
    for h, a in candidates:
        if isinstance(h, (int, float)) and isinstance(a, (int, float)):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def _team_name(match_doc: dict, side: str) -> Optional[str]:
    val = match_doc.get(f"{side}_team")
    if isinstance(val, dict):
        return val.get("name")
    if isinstance(val, str):
        return val
    return None


def check_fixture_gate(
    match_doc: Any,
    *,
    now: Optional[datetime] = None,
    buffer_minutes: Optional[int] = None,
) -> dict:
    """Decide whether a fixture is eligible for pre-match analysis.

    Returns a structured dict::

        {
          "ok":             bool,
          "discard_reason": str | None,
          "stage":          "fixture_time_status_gate",
          "status":         "<canonical>",
          "start_time":     "<iso>" | None,
          "now":            "<iso>",
          "match_id":       <id>,
          "home":           "...",
          "away":           "...",
          "buffer_minutes": int,
        }

    The gate NEVER raises — bad input returns ``ok=False`` with
    ``discard_reason=FIXTURE_INVALID_INPUT``.
    """
    now_dt = now or datetime.now(timezone.utc)
    buf = buffer_minutes if buffer_minutes is not None else get_prematch_buffer_minutes()

    base: dict = {
        "ok":             False,
        "discard_reason": None,
        "stage":          STAGE,
        "status":         None,
        "start_time":     None,
        "now":            now_dt.astimezone(timezone.utc).isoformat(),
        "match_id":       None,
        "home":           None,
        "away":           None,
        "buffer_minutes": int(buf),
    }

    if not isinstance(match_doc, dict):
        base["discard_reason"] = RC_INVALID_INPUT
        return base

    base["match_id"] = match_doc.get("match_id") or match_doc.get("id")
    base["home"]     = _team_name(match_doc, "home")
    base["away"]     = _team_name(match_doc, "away")

    tokens = _doc_status_tokens(match_doc)
    base["status"] = tokens[0] if tokens else None

    # 1) Terminal status guard.
    finished = _is_terminal_status(tokens)
    if finished is not None:
        base["status"]         = finished
        base["discard_reason"] = RC_ALREADY_FINISHED
        return base

    # 2) Score-based safety net (catches "stuck NS" docs).
    if _has_persisted_final_score(match_doc):
        base["discard_reason"] = RC_ALREADY_FINISHED
        base["status"]         = base["status"] or "FINISHED"
        return base

    # 3) Live status guard.
    live = _is_live_status(tokens)
    if live is not None:
        base["status"]         = live
        base["discard_reason"] = RC_ALREADY_STARTED
        return base

    # 4) Kickoff time guard.
    start_dt, start_iso = _extract_start_time(match_doc)
    base["start_time"] = start_iso

    if start_dt is None:
        base["discard_reason"] = RC_KICKOFF_TIME_MISSING
        return base

    if start_dt <= now_dt:
        base["discard_reason"] = RC_ALREADY_STARTED
        return base

    minutes_to_kickoff = (start_dt - now_dt).total_seconds() / 60.0
    if minutes_to_kickoff < buf:
        base["discard_reason"] = RC_KICKOFF_TOO_SOON
        return base

    base["ok"]             = True
    base["discard_reason"] = None
    base["status"]         = base["status"] or "NS"
    return base


def filter_fixtures_through_gate(
    matches: list[dict],
    *,
    now: Optional[datetime] = None,
    buffer_minutes: Optional[int] = None,
    audit_sink: Optional[list[dict]] = None,
) -> tuple[list[dict], list[dict]]:
    """Apply :func:`check_fixture_gate` to every match.

    Returns ``(kept, dropped)``.  When ``audit_sink`` is provided, every
    discard payload is appended to it (caller-owned — handy for the
    pipeline_meta diagnostics block / Mongo log).
    """
    if not matches:
        return ([], [])

    now = now or datetime.now(timezone.utc)
    buf = buffer_minutes if buffer_minutes is not None else get_prematch_buffer_minutes()

    kept: list[dict] = []
    dropped: list[dict] = []

    for m in matches:
        decision = check_fixture_gate(m, now=now, buffer_minutes=buf)
        if decision["ok"]:
            kept.append(m)
            continue
        dropped.append(m)
        if audit_sink is not None:
            audit_sink.append({
                **decision,
                # Attach a small subset of the doc so downstream UI can
                # show the user "we dropped these and why" without
                # leaking the entire fixture blob.
                "audit": {
                    "match_id": decision["match_id"],
                    "home":     decision["home"],
                    "away":     decision["away"],
                    "status":   decision["status"],
                    "reason":   decision["discard_reason"],
                },
            })

    if dropped:
        sample = [
            {
                "match_id":       d.get("match_id") or d.get("id"),
                "status":         (d.get("status_short") or d.get("status")),
                "kickoff_iso":    d.get("kickoff_iso") or d.get("date"),
                "home":           _team_name(d, "home"),
                "away":           _team_name(d, "away"),
            }
            for d in dropped[:5]
        ]
        log.info(
            "[fixture_gate] dropped %d/%d fixture(s) before analysis (buffer=%dm) — sample=%s",
            len(dropped), len(matches), buf, sample,
        )
    return kept, dropped


__all__ = [
    "FINAL_STATUSES", "LIVE_STATUSES",
    "RC_ALREADY_FINISHED", "RC_ALREADY_STARTED", "RC_KICKOFF_TOO_SOON",
    "RC_KICKOFF_TIME_MISSING", "RC_INVALID_INPUT", "RC_PASSED",
    "STAGE", "DEFAULT_PREMATCH_BUFFER_MIN",
    "get_prematch_buffer_minutes",
    "check_fixture_gate", "filter_fixtures_through_gate",
]
