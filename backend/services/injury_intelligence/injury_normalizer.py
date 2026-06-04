"""Normalize raw injury records from heterogeneous sources.

Responsibilities:
  * Map free-form status strings → canonical STATUS_VALUES.
  * Dedupe player records across sources, picking the most CONSERVATIVE
    status when sources conflict (out > doubtful > questionable > probable).
  * Compute freshness based on the most-recent updated_at.

All helpers are pure and fail-soft.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from .injury_schema import STATUS_VALUES

# Conservative ordering — higher index = more severe.
_STATUS_SEVERITY = {
    "unknown":               0,
    "probable":              1,
    "rest":                  2,
    "day_to_day":            3,
    "questionable":          4,
    "minutes_restriction":   5,
    "doubtful":              6,
    "suspended":             7,
    "out":                   8,
}

# Common synonyms across sources (regexed to lowercase ASCII).
_STATUS_PATTERNS = (
    # OUT
    (re.compile(r"\b(out|outage|inactive|sidelined|will not play|did not play|dnp|ruled out)\b"), "out"),
    # SUSPENDED
    (re.compile(r"\b(suspended|suspension|league discipline|banned)\b"), "suspended"),
    # DOUBTFUL
    (re.compile(r"\b(doubtful|highly doubtful|unlikely|probably out)\b"), "doubtful"),
    # QUESTIONABLE
    (re.compile(r"\b(questionable|gtd|game[- ]time decision|fifty[- ]fifty|game time decision|coin flip)\b"), "questionable"),
    # MINUTES RESTRICTION
    (re.compile(r"\b(minutes restriction|minutes limit|minute limit|minute restriction|limited minutes)\b"), "minutes_restriction"),
    # REST / LOAD MANAGEMENT
    (re.compile(r"\b(rest|load management|maintenance|coach.?s decision|cd)\b"), "rest"),
    # PROBABLE
    (re.compile(r"\b(probable|expected to play|likely|will play|active)\b"), "probable"),
    # DAY TO DAY
    (re.compile(r"\b(day[- ]to[- ]day|d2d)\b"), "day_to_day"),
)


def _ascii_lower(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = s.encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def normalize_status(raw: Optional[str]) -> str:
    """Map a free-form status string to one of STATUS_VALUES.

    Returns ``"unknown"`` when nothing matches. Never raises.
    """
    if not raw:
        return "unknown"
    text = _ascii_lower(raw)
    if text in STATUS_VALUES:
        return text
    for pattern, status in _STATUS_PATTERNS:
        if pattern.search(text):
            return status
    return "unknown"


def _player_key(name: str) -> str:
    """Stable dedupe key for a player name (ascii-lower, no spaces/dots)."""
    n = _ascii_lower(name or "")
    return re.sub(r"[^a-z0-9]", "", n)


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if isinstance(s, datetime):
            return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
        if str(s).endswith("Z"):
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return datetime.fromisoformat(str(s))
    except Exception:
        return None


def merge_player_records(records: Iterable[dict]) -> list[dict]:
    """Dedupe a list of player records keyed by name.

    When the same player appears in multiple sources, the MORE SEVERE
    status wins. Sources are appended in a ``sources`` list so the UI
    can show provenance.
    """
    out: dict[str, dict] = {}
    for r in records or []:
        if not isinstance(r, dict):
            continue
        name = r.get("player_name") or r.get("name") or ""
        if not name:
            continue
        key = _player_key(name)
        canonical_status = normalize_status(r.get("status"))
        current = out.get(key)
        record = {
            "player_name":     str(name).strip(),
            "player_id":       r.get("player_id"),
            "status":          canonical_status,
            "injury_type":     r.get("injury_type") or r.get("reason") or "",
            "expected_return": r.get("expected_return") or r.get("return_date") or "",
            "source":          r.get("source") or "",
            "source_url":      r.get("source_url") or r.get("url") or "",
            "updated_at":      r.get("updated_at") or r.get("date") or "",
            "confidence":      float(r.get("confidence") or 0.0),
            "position":        r.get("position") or "",
            "role":            r.get("role") or "unknown",
        }
        if current is None:
            record["sources"] = [record["source"]] if record["source"] else []
            out[key] = record
            continue
        # Conflict — keep the more severe status, but record both sources.
        prev_sev = _STATUS_SEVERITY.get(current["status"], 0)
        new_sev  = _STATUS_SEVERITY.get(canonical_status, 0)
        merged_sources = list(set((current.get("sources") or []) + [record["source"]] if record["source"] else current.get("sources") or []))
        if new_sev > prev_sev:
            record["sources"] = merged_sources
            # Preserve role/position from previous if richer.
            for k in ("role", "position"):
                if not record.get(k) or record[k] == "unknown":
                    record[k] = current.get(k) or record[k]
            out[key] = record
        else:
            current["sources"] = merged_sources
    return list(out.values())


def compute_freshness(
    records: Iterable[dict],
    *,
    sport: str = "basketball",
    is_game_day: bool = False,
) -> str:
    """Compute the overall freshness based on the most recent update.

    TTL policy:
      * basketball pregame: fresh < 2h, partial < 4h, else stale
      * basketball game-day: fresh < 30min, partial < 1h, else stale
    """
    if not records:
        return "unknown"
    now = datetime.now(timezone.utc)
    most_recent: Optional[datetime] = None
    for r in records:
        dt = _parse_dt((r or {}).get("updated_at"))
        if dt is None:
            continue
        if most_recent is None or dt > most_recent:
            most_recent = dt
    if most_recent is None:
        return "unknown"
    age = now - most_recent
    if sport == "basketball":
        fresh_t = timedelta(minutes=30) if is_game_day else timedelta(hours=2)
        partial_t = timedelta(hours=1) if is_game_day else timedelta(hours=4)
    else:
        fresh_t = timedelta(hours=1) if is_game_day else timedelta(hours=4)
        partial_t = timedelta(hours=2) if is_game_day else timedelta(hours=8)
    if age <= fresh_t:
        return "fresh"
    if age <= partial_t:
        return "partial"
    return "stale"


__all__ = [
    "normalize_status",
    "merge_player_records",
    "compute_freshness",
]
