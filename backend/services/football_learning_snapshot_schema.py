"""Sprint-B · B1.a — Football Match Learning Snapshot Schema
=========================================================

Defines the canonical shape of documents stored in the new MongoDB
collection ``football_match_learning_snapshots``. Each document
captures the **pre-match factors** the engine saw before kickoff
(2-6h ahead, refreshed 30-60min before with lineups) and is later
completed with the **post-match outcome** so the learning loops can
measure hit_rate / ROI / edge-realised vs edge-predicted.

Design choices
--------------
* PURE module — no Mongo, no I/O. Validation lives here, persistence
  lives in ``football_learning_snapshot_manager.py``.
* UUID v4 as ``_id`` (no Mongo ObjectId).
* Timezone-aware UTC datetimes (mandated by environment rules).
* Strict reason-code vocabulary so the audit trail is grep-able.

Reason codes (used by manager / aggregator)
-------------------------------------------
PRE_MATCH_SNAPSHOT_CREATED        — first persist for this fixture
PRE_MATCH_SNAPSHOT_REFRESHED      — second persist with lineups
PRE_MATCH_DATA_PARTIAL            — at least one input missing
PRE_MATCH_SOURCE_UNAVAILABLE      — a primary source failed (audited)
POST_MATCH_RESULT_SETTLED         — full final score + stats hydrated
POST_MATCH_STATS_PARTIAL          — final score OK but stats partial
POST_MATCH_CORNERS_MISSING        — corners count unavailable
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# ── Snapshot type tags ───────────────────────────────────────────────────────
SNAPSHOT_PRE_MATCH    = "PRE_MATCH"
SNAPSHOT_POST_MATCH   = "POST_MATCH"

# ── Scrape status ───────────────────────────────────────────────────────────────────
SCRAPE_PENDING        = "PENDING"
SCRAPE_PARTIAL        = "PARTIAL"
SCRAPE_COMPLETE       = "COMPLETE"
SCRAPE_FAILED         = "FAILED"

# ── Reason codes ─────────────────────────────────────────────────────────────────────
RC_PRE_MATCH_SNAPSHOT_CREATED        = "PRE_MATCH_SNAPSHOT_CREATED"
RC_PRE_MATCH_SNAPSHOT_REFRESHED      = "PRE_MATCH_SNAPSHOT_REFRESHED"
RC_PRE_MATCH_DATA_PARTIAL            = "PRE_MATCH_DATA_PARTIAL"
RC_PRE_MATCH_SOURCE_UNAVAILABLE      = "PRE_MATCH_SOURCE_UNAVAILABLE"
RC_POST_MATCH_RESULT_SETTLED         = "POST_MATCH_RESULT_SETTLED"
RC_POST_MATCH_STATS_PARTIAL          = "POST_MATCH_STATS_PARTIAL"
RC_POST_MATCH_CORNERS_MISSING        = "POST_MATCH_CORNERS_MISSING"

# Required pre-match keys; missing any of these flips status to PARTIAL.
REQUIRED_PRE_MATCH_KEYS = (
    "home_xg_l5", "away_xg_l5",
    "home_corners_l5", "away_corners_l5",
    "btts_probability", "over25_probability",
)

REQUIRED_POST_MATCH_KEYS = (
    "final_score", "home_goals", "away_goals", "total_goals",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_empty_pre_match_inputs() -> dict:
    """Return a brand-new pre-match inputs block with the canonical
    key set, all values set to ``None``."""
    return {
        # xG inputs (L5 + L15 windows)
        "home_xg_l5":            None,
        "away_xg_l5":            None,
        "home_xg_l15":           None,
        "away_xg_l15":           None,
        # Corners L1 / L5 / L15 (per spec)
        "home_corners_l1":       None,
        "away_corners_l1":       None,
        "home_corners_l5":       None,
        "away_corners_l5":       None,
        "home_corners_l15":      None,
        "away_corners_l15":      None,
        # Derived expectations
        "expected_corners":      None,
        "btts_probability":      None,
        "over25_probability":    None,
        "draw_probability":      None,
        # Pre-match market odds
        "market_odds": {
            "over25":          None,
            "under25":         None,
            "btts_yes":        None,
            "btts_no":         None,
            "over85_corners":  None,
            "draw":            None,
            "home_ml":         None,
            "away_ml":         None,
            "double_chance_1x": None,
            "double_chance_x2": None,
        },
        # Forma / lesiones / lineup (refrescados 30-60min antes del partido)
        "recent_form_home":      None,
        "recent_form_away":      None,
        "injuries_home":         [],
        "injuries_away":         [],
        "lineup_home":           None,
        "lineup_away":           None,
    }


def build_empty_post_match_outputs() -> dict:
    """Return a brand-new post-match outputs block."""
    return {
        "final_score":          None,
        "home_goals":           None,
        "away_goals":           None,
        "total_goals":          None,
        "btts_hit":             None,    # True/False/None
        "over25_hit":           None,
        "draw_hit":             None,
        "total_corners":        None,
        "home_corners":         None,
        "away_corners":         None,
        "over85_corners_hit":   None,
        "real_home_xg":         None,
        "real_away_xg":         None,
    }


def build_empty_source_audit() -> dict:
    return {
        "pre_match_sources":   [],     # list of {source, status, fetched_at, fields_filled}
        "post_match_sources":  [],
        "scrape_status":       SCRAPE_PENDING,
    }


def new_snapshot_doc(
    *,
    match_id: int | str,
    home_team: str,
    away_team: str,
    competition: str = "",
    match_date: Optional[datetime] = None,
    snapshot_type: str = SNAPSHOT_PRE_MATCH,
) -> dict:
    """Factory: brand-new learning snapshot document.

    Returns a dict ready to insert into
    ``football_match_learning_snapshots``.
    """
    now = _utcnow()
    return {
        "_id":                   str(uuid.uuid4()),
        "match_id":              match_id,
        "home_team":             home_team,
        "away_team":             away_team,
        "competition":           competition or "",
        "match_date":            match_date,
        "snapshot_type":         snapshot_type,
        "snapshot_taken_at":     now,
        "snapshot_refreshed_at": None,
        "pre_match_inputs":      build_empty_pre_match_inputs(),
        "post_match_outputs":    build_empty_post_match_outputs(),
        "source_audit":          build_empty_source_audit(),
        "reason_codes":          [RC_PRE_MATCH_SNAPSHOT_CREATED],
        "engine_version":        "learning_snapshot.v1",
        "created_at":            now,
        "updated_at":            now,
    }


def validate_pre_match_completeness(inputs: dict) -> tuple[bool, list[str]]:
    """Return ``(is_complete, missing_keys)``."""
    if not isinstance(inputs, dict):
        return False, list(REQUIRED_PRE_MATCH_KEYS)
    missing = [k for k in REQUIRED_PRE_MATCH_KEYS if inputs.get(k) is None]
    return (not missing), missing


def validate_post_match_completeness(outputs: dict) -> tuple[bool, list[str]]:
    """Return ``(is_complete, missing_keys)``."""
    if not isinstance(outputs, dict):
        return False, list(REQUIRED_POST_MATCH_KEYS)
    missing = [k for k in REQUIRED_POST_MATCH_KEYS if outputs.get(k) is None]
    return (not missing), missing


def stamp_source_audit_entry(
    audit: dict,
    *,
    bucket: str,
    source: str,
    status: str,
    fields_filled: list[str] | None = None,
    error: Optional[str] = None,
) -> dict:
    """Append a per-source audit entry. ``bucket`` is one of
    "pre_match_sources" / "post_match_sources".
    """
    if not isinstance(audit, dict):
        audit = build_empty_source_audit()
    if bucket not in ("pre_match_sources", "post_match_sources"):
        return audit
    entry: dict[str, Any] = {
        "source":        source,
        "status":        status,
        "fetched_at":    _utcnow().isoformat(),
        "fields_filled": list(fields_filled or []),
    }
    if error:
        entry["error"] = str(error)[:300]
    audit.setdefault(bucket, []).append(entry)
    return audit


__all__ = [
    # Tags
    "SNAPSHOT_PRE_MATCH", "SNAPSHOT_POST_MATCH",
    "SCRAPE_PENDING", "SCRAPE_PARTIAL", "SCRAPE_COMPLETE", "SCRAPE_FAILED",
    # Reason codes
    "RC_PRE_MATCH_SNAPSHOT_CREATED",
    "RC_PRE_MATCH_SNAPSHOT_REFRESHED",
    "RC_PRE_MATCH_DATA_PARTIAL",
    "RC_PRE_MATCH_SOURCE_UNAVAILABLE",
    "RC_POST_MATCH_RESULT_SETTLED",
    "RC_POST_MATCH_STATS_PARTIAL",
    "RC_POST_MATCH_CORNERS_MISSING",
    # Required keys
    "REQUIRED_PRE_MATCH_KEYS",
    "REQUIRED_POST_MATCH_KEYS",
    # Factories
    "new_snapshot_doc",
    "build_empty_pre_match_inputs",
    "build_empty_post_match_outputs",
    "build_empty_source_audit",
    "validate_pre_match_completeness",
    "validate_post_match_completeness",
    "stamp_source_audit_entry",
]
