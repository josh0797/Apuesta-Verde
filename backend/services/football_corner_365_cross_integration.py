"""Football Corner 365Scores Cross Integration — Phase F82.2.

Replaces Scores24/Bright Data as the external confirmator for the
``combined_football_corner_profile_cross`` block. The engine still
computes the L5-vs-L15 corner cross internally; this module now layers
*365Scores* on top to confirm or contradict the engine's verdict.

Design contract
---------------
* **Sync, fail-soft, always-attaches.** Mutates ``pick_payload`` (or the
  match doc if no pick is provided) in place. Never raises.
* **Pull-based.** Reads ``match_doc["corners_snapshot"]`` (written by
  the corners provider — fast tier or via /run-now / /background
  365Scores enrichment). No HTTP is performed here.
* **Audit-first.** Writes the confirmation outcome inside the existing
  ``combined_football_corner_profile_cross`` block AND a top-level
  ``football_corner_365_cross_applied`` audit dict for observability.

Confirmation rules (product spec)
---------------------------------
::

    if profile.supports == "UNDER":
        confirms if  combined_avg_for <= 8.5  OR  over_9_5_rate <= 0.40
        conflicts if combined_avg_for >= 10.0 OR  over_9_5_rate >= 0.58

    if profile.supports == "OVER":
        confirms if  combined_avg_for >= 9.5  OR  over_9_5_rate >= 0.55
        conflicts if combined_avg_for <= 8.0  OR  over_9_5_rate <= 0.38

When neither side triggers, the cross stays unconfirmed (neither
confirmation nor conflict).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football_corner_365_cross_integration")

ENGINE_VERSION = "football_corner_365_cross_integration.v1"

# ── Reason codes ─────────────────────────────────────────────────────
RC_CONFIRMS_UNDER       = "365SCORES_CONFIRMS_UNDER_PROFILE"
RC_CONFIRMS_OVER        = "365SCORES_CONFIRMS_OVER_PROFILE"
RC_CONFLICTS_UNDER      = "365SCORES_CONFLICTS_UNDER_PROFILE"
RC_CONFLICTS_OVER       = "365SCORES_CONFLICTS_OVER_PROFILE"
RC_NEUTRAL              = "365SCORES_NEUTRAL_VS_PROFILE"
RC_NO_PROFILE           = "NO_CROSS_PROFILE_AVAILABLE"
RC_NO_EXTERNAL          = "NO_365SCORES_CONFIRMATION_AVAILABLE"
RC_PENDING_BG           = "365SCORES_PENDING_BACKGROUND_ENRICHMENT"

# ── Confirmation thresholds (per product spec) ───────────────────────
UNDER_CONFIRM_AVG_MAX   = 8.5
UNDER_CONFIRM_RATE_MAX  = 0.40
UNDER_CONFLICT_AVG_MIN  = 10.0
UNDER_CONFLICT_RATE_MIN = 0.58

OVER_CONFIRM_AVG_MIN    = 9.5
OVER_CONFIRM_RATE_MIN   = 0.55
OVER_CONFLICT_AVG_MAX   = 8.0
OVER_CONFLICT_RATE_MAX  = 0.38


def _as_float(v: Any) -> Optional[float]:
    """Best-effort float coercion (None on failure / NaN-ish values)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _extract_external_metrics(snapshot: dict) -> dict:
    """Project the relevant numeric metrics out of the corners snapshot.

    The corners_snapshot shape from 365Scores varies slightly depending
    on which extractor populated it (current_match block vs aggregated
    averages). This helper consolidates the values we care about into a
    flat dict.
    """
    cm = snapshot.get("current_match") or {}

    # Per-team averages — try several common key spellings.
    avg_for_home = _as_float(
        snapshot.get("avg_for_home")
        or snapshot.get("home_avg_for")
        or (cm.get("home") if isinstance(cm.get("home"), (int, float)) else None)
        or (snapshot.get("home") or {}).get("avg_for") if isinstance(snapshot.get("home"), dict) else None,
    )
    avg_for_away = _as_float(
        snapshot.get("avg_for_away")
        or snapshot.get("away_avg_for")
        or (cm.get("away") if isinstance(cm.get("away"), (int, float)) else None)
        or (snapshot.get("away") or {}).get("avg_for") if isinstance(snapshot.get("away"), dict) else None,
    )

    # Combined average — prefer explicit; otherwise sum of the two
    # per-team averages; otherwise the current_match total.
    combined = _as_float(
        snapshot.get("combined_avg_for")
        or snapshot.get("combined_for")
        or snapshot.get("total_avg_for"),
    )
    if combined is None and avg_for_home is not None and avg_for_away is not None:
        combined = round(avg_for_home + avg_for_away, 2)
    if combined is None:
        combined = _as_float(cm.get("total"))

    over_rate = _as_float(
        snapshot.get("over_9_5_rate")
        or snapshot.get("over95_rate")
        or snapshot.get("over_rate_9_5"),
    )
    if over_rate is not None and over_rate > 1.0:
        # Allow callers to pass percentages (e.g. 47 instead of 0.47).
        over_rate = over_rate / 100.0

    return {
        "avg_for_home":    avg_for_home,
        "avg_for_away":    avg_for_away,
        "combined_avg_for": combined,
        "over_9_5_rate":   over_rate,
    }


def _evaluate_under(metrics: dict) -> tuple[bool, bool, list[str]]:
    """Return (confirms, conflicts, reason_codes) for UNDER profile."""
    combined = metrics.get("combined_avg_for")
    rate     = metrics.get("over_9_5_rate")
    codes: list[str] = []

    confirms = (
        (combined is not None and combined <= UNDER_CONFIRM_AVG_MAX)
        or (rate is not None and rate <= UNDER_CONFIRM_RATE_MAX)
    )
    conflicts = (
        (combined is not None and combined >= UNDER_CONFLICT_AVG_MIN)
        or (rate is not None and rate >= UNDER_CONFLICT_RATE_MIN)
    )
    # A snapshot CANNOT both confirm and conflict — if both triggered
    # (e.g. low combined but high rate), prefer the stricter signal.
    if confirms and conflicts:
        # Stricter takes priority. Use the rate when present (it's the
        # more outcome-anchored metric), else combined avg.
        if rate is not None:
            confirms  = rate <= UNDER_CONFIRM_RATE_MAX
            conflicts = rate >= UNDER_CONFLICT_RATE_MIN
        else:
            confirms  = combined is not None and combined <= UNDER_CONFIRM_AVG_MAX
            conflicts = combined is not None and combined >= UNDER_CONFLICT_AVG_MIN

    if confirms:
        codes.append(RC_CONFIRMS_UNDER)
    elif conflicts:
        codes.append(RC_CONFLICTS_UNDER)
    else:
        codes.append(RC_NEUTRAL)
    return confirms, conflicts, codes


def _evaluate_over(metrics: dict) -> tuple[bool, bool, list[str]]:
    """Return (confirms, conflicts, reason_codes) for OVER profile."""
    combined = metrics.get("combined_avg_for")
    rate     = metrics.get("over_9_5_rate")
    codes: list[str] = []

    confirms = (
        (combined is not None and combined >= OVER_CONFIRM_AVG_MIN)
        or (rate is not None and rate >= OVER_CONFIRM_RATE_MIN)
    )
    conflicts = (
        (combined is not None and combined <= OVER_CONFLICT_AVG_MAX)
        or (rate is not None and rate <= OVER_CONFLICT_RATE_MAX)
    )
    if confirms and conflicts:
        if rate is not None:
            confirms  = rate >= OVER_CONFIRM_RATE_MIN
            conflicts = rate <= OVER_CONFLICT_RATE_MAX
        else:
            confirms  = combined is not None and combined >= OVER_CONFIRM_AVG_MIN
            conflicts = combined is not None and combined <= OVER_CONFLICT_AVG_MAX

    if confirms:
        codes.append(RC_CONFIRMS_OVER)
    elif conflicts:
        codes.append(RC_CONFLICTS_OVER)
    else:
        codes.append(RC_NEUTRAL)
    return confirms, conflicts, codes


def attach_365_corner_confirmation(match_doc: dict,
                                    pick_payload: Optional[dict] = None) -> dict:
    """Confirm or contradict the cross profile using 365Scores metrics.

    Inputs
    ------
    match_doc
        Must contain ``corners_snapshot`` (from the corners provider —
        TheStatsAPI fast tier or 365Scores manual/background) AND
        ``combined_football_corner_profile_cross`` (from the engine).
        Both fields are also accepted nested under
        ``football_data_enrichment.corners`` for compat.
    pick_payload
        Optional. When provided, the helper ALSO mutates the pick's own
        ``combined_football_corner_profile_cross`` block (so the UI can
        read it directly from the pick payload, without traversing the
        match doc).

    Returns
    -------
    dict — audit block (also stored on the match doc and pick payload).
    Never raises.

    Logs::

        [corner_cross_365] fixture=123 profile=STRONG_CORNERS_UNDER_CROSS \
            supports=UNDER confirmation=true conflict=false
        [corner_cross_365] fixture=456 no_external_confirmation \
            reason=NO_365SCORES_CONFIRMATION_AVAILABLE
    """
    fid = (
        (match_doc or {}).get("fixture_id")
        or (match_doc or {}).get("match_id")
        or "?"
    )
    audit: dict[str, Any] = {
        "available":         False,
        "engine_version":    ENGINE_VERSION,
        "external_source":   None,
        "external_confirmation": False,
        "external_conflict":     False,
        "external_reason_codes": [],
        "external_snapshot": None,
    }
    if not isinstance(match_doc, dict):
        return audit

    cross = match_doc.get("combined_football_corner_profile_cross") or {}
    if not isinstance(cross, dict) or not cross.get("available"):
        # No engine cross profile to confirm — UI must still surface a
        # clean "no external confirmation" state.
        audit["external_reason_codes"] = [RC_NO_PROFILE, RC_NO_EXTERNAL]
        _persist(match_doc, pick_payload, cross, audit)
        log.info("[corner_cross_365] fixture=%s no_profile_cross", fid)
        return audit

    supports = (cross.get("supports") or "NEUTRAL").upper()

    snapshot = (match_doc.get("corners_snapshot")
                or (match_doc.get("football_data_enrichment") or {}).get("corners")
                or {})
    if not isinstance(snapshot, dict):
        snapshot = {}

    snap_available = bool(snapshot.get("available"))
    snap_source    = snapshot.get("source")
    snap_status    = snapshot.get("status")

    # When the snapshot is still in the deferred state, surface the
    # PENDING reason code so the UI can show the refresh button.
    if not snap_available or snap_source != "365scores":
        codes = [RC_NO_EXTERNAL]
        if snap_status == "PENDING_BACKGROUND_ENRICHMENT":
            codes.append(RC_PENDING_BG)
        audit["external_reason_codes"] = codes
        _persist(match_doc, pick_payload, cross, audit)
        log.info("[corner_cross_365] fixture=%s no_external_confirmation reason=%s",
                 fid, RC_NO_EXTERNAL)
        return audit

    metrics = _extract_external_metrics(snapshot)

    if supports == "UNDER":
        confirms, conflicts, codes = _evaluate_under(metrics)
    elif supports == "OVER":
        confirms, conflicts, codes = _evaluate_over(metrics)
    else:
        # NEUTRAL / MIXED / ASYMMETRIC profile — no directional bet to
        # confirm. We still attach the metrics for UI display.
        confirms, conflicts, codes = False, False, [RC_NEUTRAL]

    audit.update({
        "available":              True,
        "external_source":        "365scores",
        "external_confirmation":  confirms,
        "external_conflict":      conflicts,
        "external_reason_codes":  codes,
        "external_snapshot": {
            "source":           "365scores",
            "avg_for_home":     metrics["avg_for_home"],
            "avg_for_away":     metrics["avg_for_away"],
            "combined_avg_for": metrics["combined_avg_for"],
            "over_9_5_rate":    metrics["over_9_5_rate"],
        },
    })
    _persist(match_doc, pick_payload, cross, audit)
    log.info(
        "[corner_cross_365] fixture=%s profile=%s supports=%s "
        "confirmation=%s conflict=%s",
        fid, cross.get("profile"), supports,
        str(confirms).lower(), str(conflicts).lower(),
    )
    return audit


def _persist(match_doc: dict, pick_payload: Optional[dict],
              cross: dict, audit: dict) -> None:
    """Write the 365Scores confirmation back onto both the cross block
    (so UI consumers see it) and a top-level audit field on the match
    doc / pick payload."""
    # Update the cross block in place with the external_* keys so the
    # existing UI code paths pick them up.
    if isinstance(cross, dict):
        cross["external_source"]        = audit["external_source"]
        cross["external_confirmation"]  = audit["external_confirmation"]
        cross["external_conflict"]      = audit["external_conflict"]
        cross["external_reason_codes"]  = audit["external_reason_codes"]
        if audit.get("external_snapshot"):
            cross["external_snapshot"] = audit["external_snapshot"]
        match_doc["combined_football_corner_profile_cross"] = cross
        # Mirror into the camelCase alias used by the React UI.
        fhp = match_doc.get("footballHistoricalProfile") or {}
        fhp["combinedFootballCornerProfileCross"] = cross
        match_doc["footballHistoricalProfile"] = fhp

    match_doc["football_corner_365_cross_applied"] = audit

    if isinstance(pick_payload, dict):
        pick_payload["combined_football_corner_profile_cross"] = cross
        pick_payload["football_corner_365_cross_applied"]      = audit
        fhp = pick_payload.get("footballHistoricalProfile") or {}
        fhp["combinedFootballCornerProfileCross"] = cross
        pick_payload["footballHistoricalProfile"] = fhp


__all__ = [
    "ENGINE_VERSION",
    "attach_365_corner_confirmation",
    "RC_CONFIRMS_UNDER", "RC_CONFIRMS_OVER",
    "RC_CONFLICTS_UNDER", "RC_CONFLICTS_OVER",
    "RC_NEUTRAL", "RC_NO_PROFILE", "RC_NO_EXTERNAL", "RC_PENDING_BG",
    "UNDER_CONFIRM_AVG_MAX", "UNDER_CONFIRM_RATE_MAX",
    "UNDER_CONFLICT_AVG_MIN", "UNDER_CONFLICT_RATE_MIN",
    "OVER_CONFIRM_AVG_MIN", "OVER_CONFIRM_RATE_MIN",
    "OVER_CONFLICT_AVG_MAX", "OVER_CONFLICT_RATE_MAX",
]
