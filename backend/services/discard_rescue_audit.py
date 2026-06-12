"""Phase F67 — Discard Rescue Audit.

Pure functional + persistence layer for measuring how often the F66
editorial engine rescues a discarded pick (moves it to watchlist or
surfaces an alternative market) instead of confirming the discard.

Goal
====
The user wants to detect *over-rescue noise* (engine being too kind on
weak signals). The audit records, per pick, the original discard
context PLUS the editorial verdict, so a daily summary can answer:

    Descartados revisados:   30
    Movidos a watchlist:      8
    Alternativas detectadas:  4
    Descartes confirmados:   18

Architecture
============
* In-memory:  :func:`build_audit_entry` turns the original pick +
              editorial output into a single audit row.
* Persistence: :func:`persist_audit_entry` upserts to Mongo
              ``discard_rescue_audit`` (TTL 90 days).
* Aggregation: :func:`compute_daily_summary` aggregates over a time
              window (rolling 24h / 7d / 30d).

The persistence layer is fail-soft — it NEVER raises and never blocks
the analyst run. Aggregation is pure (works on a list of dicts), so
tests can run without Mongo.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

log = logging.getLogger("discard_rescue_audit")

ENGINE_VERSION    = "discard_rescue_audit.v1"
AUDIT_COLLECTION  = "discard_rescue_audit"
AUDIT_TTL_DAYS    = 90

DECISION_CONFIRM_DISCARD = "CONFIRM_DISCARD"
DECISION_WATCHLIST       = "WATCHLIST"
DECISION_ALTERNATIVE     = "RESCUE_ALTERNATIVE_MARKET"
DECISION_VALUE_CANDIDATE = "VALUE_CANDIDATE"
DECISION_UNKNOWN         = "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────
# Audit entry builder — pure function.
# ─────────────────────────────────────────────────────────────────────
def build_audit_entry(
    pick: dict,
    *,
    original_bucket: str,
    original_reason: Optional[str] = None,
    was_originally_hard_discard: bool = False,
    editorial_prediction: Optional[dict] = None,
    structural_review: Optional[dict] = None,
) -> dict:
    """Distil a pick + its editorial / structural verdicts into one
    audit row ready for persistence and aggregation.

    Returns a dict shaped per the spec::

      {
        "discard_rescue_audit": {
          "match_id":                     "...",
          "match_label":                  "Brazil vs Morocco",
          "original_bucket":              "discarded_market",
          "original_reason":              "edge_negative",
          "editorial_decision":           "WATCHLIST",
          "rescued_market":               "Under 9.5 corners",
          "rescued_market_confidence":    64,
          "was_originally_hard_discard":  false,
          "captured_at":                  "2026-..."
        }
      }
    """
    if not isinstance(pick, dict):
        pick = {}

    edit_dec = _editorial_decision(editorial_prediction, structural_review)
    rescued_market, confidence = _rescued_market_info(editorial_prediction,
                                                       structural_review)

    return {
        "discard_rescue_audit": {
            "match_id":                    pick.get("match_id") or "_unknown",
            "match_label":                 pick.get("match_label")
                                            or pick.get("match")
                                            or "_unknown",
            "league":                      pick.get("league"),
            "sport":                       pick.get("sport") or "football",
            "original_bucket":             original_bucket,
            "original_reason":             original_reason
                                            or pick.get("discard_reason")
                                            or pick.get("reason")
                                            or "unknown",
            "editorial_decision":          edit_dec,
            "rescued_market":              rescued_market,
            "rescued_market_confidence":   confidence,
            "was_originally_hard_discard": bool(was_originally_hard_discard),
            "edge_pct":                    pick.get("edge_pct"),
            "captured_at":                 datetime.now(timezone.utc),
            "engine_version":              ENGINE_VERSION,
        },
    }


def _editorial_decision(editorial: Optional[dict],
                         structural: Optional[dict]) -> str:
    """Map the editorial + structural outputs to a single decision.

    Priority:
      1. Structural ``decision`` field when it returns VALUE / WATCHLIST.
      2. Editorial ``best_protected_market`` → ALTERNATIVE.
      3. Editorial sections all WATCHLIST → WATCHLIST.
      4. Otherwise CONFIRM_DISCARD.
    """
    if isinstance(structural, dict):
        d = structural.get("decision") or ""
        if d in ("VALUE_FOUND",):
            return DECISION_VALUE_CANDIDATE
        if d in ("WATCHLIST_ODDS_NEEDED", "MOVE_TO_WATCHLIST"):
            return DECISION_WATCHLIST

    if isinstance(editorial, dict) and editorial.get("available"):
        best = editorial.get("best_protected_market")
        if isinstance(best, dict) and best.get("market"):
            return DECISION_ALTERNATIVE
        # Any section in WATCHLIST status?
        secs = editorial.get("editorial_sections") or {}
        if any((s or {}).get("status") == "WATCHLIST"
               for s in secs.values() if isinstance(s, dict)):
            return DECISION_WATCHLIST

    return DECISION_CONFIRM_DISCARD


def _rescued_market_info(editorial: Optional[dict],
                          structural: Optional[dict]) -> tuple[Optional[str], Optional[int]]:
    """Pick the best market that the engines surfaced (if any)."""
    if isinstance(editorial, dict) and editorial.get("available"):
        best = editorial.get("best_protected_market")
        if isinstance(best, dict) and best.get("market"):
            return best.get("market"), best.get("confidence")
    if isinstance(structural, dict):
        rm = structural.get("rescued_market")
        if isinstance(rm, dict) and rm.get("market"):
            return rm.get("market"), rm.get("structural_support")
    return (None, None)


# ─────────────────────────────────────────────────────────────────────
# Persistence — fail-soft.
# ─────────────────────────────────────────────────────────────────────
async def persist_audit_entry(db, audit_row: dict) -> bool:
    """Insert one audit row to Mongo. Returns True on success, False otherwise.
    Never raises."""
    if db is None or not isinstance(audit_row, dict):
        return False
    body = audit_row.get("discard_rescue_audit") if "discard_rescue_audit" in audit_row \
           else audit_row
    if not isinstance(body, dict):
        return False
    try:
        await db[AUDIT_COLLECTION].insert_one(body)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("[RESCUE_AUDIT_INSERT_FAIL] %s", exc)
        return False


async def persist_bulk_for_summary(db, summary: dict, sport: str) -> int:
    """Walk every bucket of an analyst-run summary and persist one
    audit row per entry that has an editorial / structural verdict
    attached. Returns the number of rows written.

    Fail-soft: per-entry crashes are swallowed.
    """
    if db is None or not isinstance(summary, dict):
        return 0
    written = 0
    bucket_to_reason = {
        "discarded_market":      "edge_negative",
        "discarded_motivation":  "motivation_filter",
        "incomplete_data":       "incomplete_data",
        "watchlist_odds_needed": "edge_negative_but_structural_support",
    }
    for bucket_key, reason in bucket_to_reason.items():
        bucket = summary.get(bucket_key) or []
        if not isinstance(bucket, list):
            continue
        for entry in bucket:
            if not isinstance(entry, dict):
                continue
            try:
                editorial = entry.get("editorial_prediction") or \
                            (entry.get("structural_review") or {}).get("editorial_prediction")
                structural = entry.get("structural_review")
                was_hard = (
                    structural is not None and
                    isinstance(structural, dict) and
                    (structural.get("discard_strength") == "HARD_DISCARD"
                     or entry.get("discard_strength") == "HARD_DISCARD")
                )
                row = build_audit_entry(
                    entry,
                    original_bucket=bucket_key,
                    original_reason=entry.get("discard_reason") or reason,
                    was_originally_hard_discard=was_hard,
                    editorial_prediction=editorial,
                    structural_review=structural,
                )
                row["discard_rescue_audit"]["sport"] = sport
                if await persist_audit_entry(db, row):
                    written += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("[RESCUE_AUDIT_BULK_FAIL] %s", exc)
                continue
    return written


# ─────────────────────────────────────────────────────────────────────
# Aggregation — pure function over a list of audit dicts.
# ─────────────────────────────────────────────────────────────────────
def compute_daily_summary(audit_rows: Iterable[dict]) -> dict:
    """Aggregate a list of audit rows into the operator-facing summary.

    Output shape::

      {
        "engine_version":         "discard_rescue_audit.v1",
        "generated_at":           "2026-...",
        "window_n_entries":       30,
        "by_decision": {
          "CONFIRM_DISCARD":      18,
          "WATCHLIST":            8,
          "RESCUE_ALTERNATIVE_MARKET": 4,
          "VALUE_CANDIDATE":      0,
        },
        "rescue_rate_pct":        40.0,       # (WATCHLIST + ALT + VC) / total
        "by_bucket":              {...},
        "by_market_family":       {...},
        "noise_flag":             False,       # True if rescue_rate > 60%
        "notes":                  [...]
      }
    """
    rows = list(audit_rows or [])
    n = len(rows)
    by_dec: dict[str, int] = defaultdict(int)
    by_bucket: dict[str, int] = defaultdict(int)
    by_family: dict[str, int] = defaultdict(int)

    rescued_count = 0
    for r in rows:
        d = (r.get("editorial_decision") or DECISION_UNKNOWN).upper()
        by_dec[d] += 1
        if d in (DECISION_WATCHLIST, DECISION_ALTERNATIVE, DECISION_VALUE_CANDIDATE):
            rescued_count += 1
        b = r.get("original_bucket") or "unknown"
        by_bucket[b] += 1
        market = (r.get("rescued_market") or "").lower()
        # Phase F67 — match both English ("corner") and Spanish ("córner")
        # spellings; covers "Total corners Over" and "Under 9.5 córners".
        if "corner" in market or "córner" in market:
            by_family["CORNERS"]  += 1
        elif "goal" in market or "gol" in market:
            by_family["GOALS"]    += 1
        elif "btts" in market:
            by_family["BTTS"]     += 1
        elif market:
            by_family["OTHER"]    += 1

    rescue_rate = round(100.0 * rescued_count / n, 2) if n else None
    noise_flag = bool(rescue_rate is not None and rescue_rate > 60.0)

    notes: list[str] = []
    if n == 0:
        notes.append("no_audit_entries_in_window")
    if noise_flag:
        notes.append("OVER_RESCUE_NOISE_SUSPECTED")
    if by_dec.get(DECISION_CONFIRM_DISCARD, 0) == n and n > 0:
        notes.append("zero_rescues_in_window")

    return {
        "engine_version":   ENGINE_VERSION,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "window_n_entries": n,
        "by_decision":      dict(by_dec),
        "rescued_total":    rescued_count,
        "rescue_rate_pct":  rescue_rate,
        "by_bucket":        dict(by_bucket),
        "by_market_family": dict(by_family),
        "noise_flag":       noise_flag,
        "notes":            notes,
    }


async def fetch_audit_rows(db, *, hours: int = 24) -> list[dict]:
    """Read audit rows from Mongo for the last ``hours`` hours."""
    if db is None:
        return []
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return await db[AUDIT_COLLECTION].find(
            {"captured_at": {"$gte": cutoff}},
        ).to_list(length=50_000)
    except Exception as exc:  # noqa: BLE001
        log.debug("[RESCUE_AUDIT_FETCH_FAIL] %s", exc)
        return []


__all__ = [
    "ENGINE_VERSION", "AUDIT_COLLECTION", "AUDIT_TTL_DAYS",
    "DECISION_CONFIRM_DISCARD", "DECISION_WATCHLIST",
    "DECISION_ALTERNATIVE", "DECISION_VALUE_CANDIDATE",
    "build_audit_entry",
    "persist_audit_entry",
    "persist_bulk_for_summary",
    "compute_daily_summary",
    "fetch_audit_rows",
]
