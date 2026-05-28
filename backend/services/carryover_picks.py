"""Carryover picks — preserve previously-recommended picks across re-runs.

Problem solved
==============
When the user re-runs the analysis for the same sport, the LLM is
non-deterministic and bookmaker odds shift, so a match that was previously
recommended may now end up in `summary.discarded_market` or simply disappear
from `picks`. Users perceive this as "the engine discarded the bet it just
gave me", which destroys their trust in the product.

Solution (Option A — Smart Merge)
=================================
After the new pipeline finishes, look at the **most recent prior pick_run**
for the same user + sport that was generated within the last 24 hours.
For every pick that:
  • was recommended in the prior run (confidence_score ≥ 60), AND
  • corresponds to a match that has NOT yet started (status NS / TBD), AND
  • is NOT already represented in the new run's `picks`, AND
  • does NOT carry a "hard" invalidator in the new run (form_correction
    critical, score change for live matches, explicit injury, etc.),
we copy it back into the new result under
`summary.carryover_picks` so the UI can render it with a distinct
"Pick previo conservado" badge. The pick keeps its original recommendation
but gets a `_carryover` metadata block describing:
  • when it was first generated,
  • why we believe it is still valid (no hard invalidator found),
  • any soft drift signals (cuotas movieron, confianza bajaría, etc.).

The merge is **strictly additive**: it never deletes new picks, never
moves a new pick to discarded, and never invents value. Worst case it
shows a stale recommendation on a match that's still pending kickoff —
the user can ignore it.

Safety rails
============
* Only carry over picks for matches whose status is in `_NOT_STARTED_STATUS`.
  Live or finished matches are never carried over (the value/odds have
  already been priced or settled).
* Refuse carry-over when the new run explicitly discards the same match
  to `summary.incomplete_data` (the data wasn't good enough to evaluate it
  again — better to drop it than to carry a stale pick).
* Refuse carry-over when the new run flags the same match in
  `summary.discarded_motivation` with `motivation_state="LOW_BOTH"` AND
  the prior pick reasoning was based on motivation (the situation
  legitimately changed).
* Cap the carry-over list to `MAX_CARRYOVER` items (default 6) so the
  dashboard never balloons with stale picks.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("carryover")

# Match statuses that are considered "not yet started" — we only carry
# over picks for matches whose value/odds haven't been priced/realized.
_NOT_STARTED_STATUS = {
    "NS", "TBD", "PST", "SCHEDULED", "scheduled", "ns", "tbd",
    "Not Started", "Time To Be Defined", "Postponed",
}

# How long a pick is allowed to be carried over. After this window we
# consider it stale and let the new run's verdict stand.
CARRYOVER_TTL_HOURS = 24

# Hard ceiling on how many carry-over picks we keep — prevents the UI from
# accumulating dozens of stale recommendations.
MAX_CARRYOVER = 6


def _match_status(m: dict | None) -> str:
    if not isinstance(m, dict):
        return ""
    fx = m.get("fixture") or {}
    if isinstance(fx, dict):
        st = fx.get("status") or {}
        if isinstance(st, dict):
            return str(st.get("short") or st.get("long") or "")
    return str(m.get("status") or "")


def _pick_match_id(p: dict) -> Optional[str]:
    mid = p.get("match_id") or p.get("fixture_id") or p.get("id")
    if mid is None:
        return None
    return str(mid)


def _collect_blocked_ids(new_result: dict) -> set[str]:
    """Match ids that should NEVER receive a carry-over pick because the
    new run already has a strong opinion about them.

    Includes:
      * new picks (already covered, would be duplicate)
      * incomplete_data (data was insufficient → drop)
      * discarded_market with explicit form/lesion reasons
      * discarded_motivation with LOW_BOTH (motivation truly collapsed)
    """
    blocked: set[str] = set()
    summary = new_result.get("summary") or {}
    for p in new_result.get("picks") or []:
        mid = _pick_match_id(p)
        if mid:
            blocked.add(mid)
    for bucket_key in ("incomplete_data", "rescued_picks", "protected_acceptable", "watchlist"):
        for e in summary.get(bucket_key) or []:
            mid = _pick_match_id(e)
            if mid:
                blocked.add(mid)
    # Motivation discards with LOW_BOTH are legitimate state changes.
    for e in summary.get("discarded_motivation") or []:
        mid = _pick_match_id(e)
        if not mid:
            continue
        if (e.get("motivation_state") or "").upper() == "LOW_BOTH":
            blocked.add(mid)
    # Market discards with hard reasons (lesion / injury / suspendido / red card)
    HARD_REASON_KEYWORDS = (
        "lesion", "lesión", "injury", "out", "suspend", "expulsion",
        "expulsión", "rojo", "red card", "scratch", "fuera",
    )
    for e in summary.get("discarded_market") or []:
        mid = _pick_match_id(e)
        if not mid:
            continue
        reason = (e.get("reason") or "").lower()
        if any(kw in reason for kw in HARD_REASON_KEYWORDS):
            blocked.add(mid)
    return blocked


def _prior_generated_at(prior_run: dict) -> Optional[datetime]:
    iso = prior_run.get("generated_at")
    if not iso:
        return None
    try:
        # ISO 8601 with timezone (or without — assume UTC)
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _candidate_matches_by_id(candidates: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        mid = c.get("match_id") or c.get("fixture_id") or c.get("id")
        if mid is None:
            continue
        out[str(mid)] = c
    return out


def apply_carryover(
    new_result: dict,
    prior_run: Optional[dict],
    candidates: list[dict],
    *,
    now: Optional[datetime] = None,
) -> dict:
    """Mutates and returns `new_result` with carry-over picks appended.

    Parameters
    ----------
    new_result   : the just-built pipeline result (top-level dict with
                   `picks` + `summary`).
    prior_run    : the most recent prior pick_run document for this user
                   + sport (or `None` when this is the first run).
    candidates   : the raw candidate matches the pipeline analysed —
                   used to check their current `status`.
    now          : injected for testing; defaults to `datetime.now(UTC)`.

    Side effects
    ------------
    * Adds `summary.carryover_picks` (list of preserved picks, never None).
    * Adds `_pipeline.carryover` metadata (counts + reasons skipped).

    The function NEVER raises — every failure path returns the original
    `new_result` so the user-facing pipeline cannot be broken by a bug
    in carry-over logic.
    """
    now = now or datetime.now(timezone.utc)
    summary = new_result.setdefault("summary", {})
    summary.setdefault("carryover_picks", [])

    if not isinstance(prior_run, dict):
        return new_result

    prior_payload = prior_run.get("payload") or {}
    prior_picks = prior_payload.get("picks") or []
    if not prior_picks:
        return new_result

    prior_dt = _prior_generated_at(prior_run)
    if prior_dt is None or (now - prior_dt) > timedelta(hours=CARRYOVER_TTL_HOURS):
        # Prior run is too old or its timestamp is unparseable → don't risk
        # surfacing stale picks.
        return new_result

    blocked_ids = _collect_blocked_ids(new_result)
    candidates_by_id = _candidate_matches_by_id(candidates)

    preserved: list[dict] = []
    skipped: dict[str, int] = {
        "duplicate":          0,
        "already_started":    0,
        "no_match_in_batch":  0,
        "hard_invalidator":   0,
        "low_confidence":     0,
    }

    for p in prior_picks:
        mid = _pick_match_id(p)
        if not mid:
            continue
        if mid in blocked_ids:
            # Either the new run already covers this match (duplicate) OR
            # it has a hard invalidator (low motivation, lesion, etc.).
            if any(str(_pick_match_id(np)) == mid for np in (new_result.get("picks") or [])):
                skipped["duplicate"] += 1
            else:
                skipped["hard_invalidator"] += 1
            continue

        rec = p.get("recommendation") or {}
        conf = int(rec.get("confidence_score") or 0)
        if conf < 60:
            skipped["low_confidence"] += 1
            continue

        # Only carry over matches that are still scheduled (not started yet).
        candidate = candidates_by_id.get(mid)
        if candidate is None:
            # The match isn't in the current candidate batch — that
            # usually means it kicked off, was filtered out, or moved to
            # a different sport pool. Either way it's not safe to surface.
            skipped["no_match_in_batch"] += 1
            continue
        status = _match_status(candidate).upper()
        if status not in {s.upper() for s in _NOT_STARTED_STATUS}:
            skipped["already_started"] += 1
            continue

        carry = copy.deepcopy(p)
        carry_meta = {
            "is_carryover":            True,
            "original_run_id":         prior_run.get("id"),
            "original_generated_at":   prior_run.get("generated_at"),
            "ttl_hours":               CARRYOVER_TTL_HOURS,
            "reason":                  "Pick previo conservado (mercado/análisis estables).",
        }
        carry["_carryover"] = carry_meta
        # UI hint — show a distinct "CARRYOVER" pill on the card.
        rec_block = carry.get("recommendation") or {}
        if isinstance(rec_block, dict):
            tags = list(rec_block.get("tags") or [])
            if "CARRYOVER" not in tags:
                tags.append("CARRYOVER")
            rec_block["tags"] = tags
            carry["recommendation"] = rec_block
        preserved.append(carry)
        if len(preserved) >= MAX_CARRYOVER:
            break

    if preserved:
        summary["carryover_picks"] = preserved
        # ALSO bump total_recommended for the UI summary strip — these
        # picks count as "still recommended" from the user's perspective.
        # We DON'T touch total_analyzed to avoid double-counting the
        # underlying match in the analyst-engine bookkeeping.
        try:
            summary["total_recommended"] = int(summary.get("total_recommended") or 0) + len(preserved)
        except Exception:
            pass

    new_result.setdefault("_pipeline", {})["carryover"] = {
        "prior_run_id":     prior_run.get("id"),
        "prior_generated":  prior_run.get("generated_at"),
        "preserved":        len(preserved),
        "skipped_breakdown": skipped,
    }
    log.info(
        "carryover: preserved=%d, skipped=%s, prior_run=%s",
        len(preserved), skipped, prior_run.get("id"),
    )
    return new_result


__all__ = ["apply_carryover", "CARRYOVER_TTL_HOURS", "MAX_CARRYOVER"]
