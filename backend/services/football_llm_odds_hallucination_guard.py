"""Phase F94.4 — Suppress LLM-hallucinated odds when no upstream odds exist.

## Why this exists
The user reported a recurring bug where two completely different fixtures
(e.g. *France vs Senegal* and *Austria vs Jordan*) displayed **identical
``Cuota detectada: 1.275``** values in the dashboard's
``REQUIRES_MARKET_IDENTIFICATION`` cards.

## Root-cause investigation
1. ``MatchDoc.odds_snapshots`` was empty for both matches because the
   primary providers (Sportytrader / API-Sports odds) failed for them.
2. The LLM (Stage-2 analyst) still produced a ``recommendation`` for each
   match — including a ``recommendation.odds_range`` value such as
   ``"1.25-1.30"`` because the prompt schema requested one.
3. Without any real odds context, the LLM **hallucinates** a generic
   placeholder range. Different matches end up with the same midpoint
   (e.g. ``1.275``), which then flows into:
     • ``_market_edge.odds_used``
     • ``market_trace.odds``
     • the manual-market-identity panel ``detectedOdd``
   making the dashboard show the *same* "Cuota detectada" across matches.

## Fix
This module exposes a single function:

    suppress_llm_hallucinated_odds(parsed, matches_payload) -> dict

It scans every pick (top-level + summary buckets) and, for each pick whose
underlying match has **no real ``odds_snapshots``**, **scrubs** the
``recommendation.odds_range`` field to ``None`` and stamps an audit
reason code ``LLM_ODDS_HALLUCINATION_SUPPRESSED_NO_UPSTREAM_ODDS``.

After this step runs, downstream consumers (``moneyball_layer``,
``football_market_trace``) compute ``odds_used = None`` and the UI
correctly renders **"Sin cuota detectada"** instead of an identical
fake value across cards.

## Strict invariants
* Never raises (fail-soft).
* Never mutates anything when the match HAS real odds available
  (back-compat).
* Idempotent: running it twice produces the same output as once.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

REASON_CODE = "LLM_ODDS_HALLUCINATION_SUPPRESSED_NO_UPSTREAM_ODDS"

# Buckets inside ``parsed["summary"]`` that can carry recommendations with
# odds_range. We scrub all of them so the bug cannot leak through a
# secondary bucket.
_SUMMARY_BUCKETS = (
    "discarded_market",
    "watchlist",
    "protected_acceptable",
    "requires_market_identity",
    "incomplete_data",
)


def _match_has_real_odds(match: Optional[dict]) -> bool:
    """Return True iff the match doc carries at least one usable odds
    snapshot containing market data.

    A snapshot is "usable" when it exposes a non-empty ``markets`` dict.
    Empty lists, ``None``, or snapshots with no markets count as missing.
    """
    if not isinstance(match, dict):
        return False
    snaps = match.get("odds_snapshots") or []
    if not isinstance(snaps, list) or not snaps:
        return False
    for s in snaps:
        if not isinstance(s, dict):
            continue
        markets = s.get("markets")
        # ``markets`` can be a dict (canonical) or sometimes a list. Treat
        # any non-empty container as "real odds present".
        if isinstance(markets, dict) and markets:
            return True
        if isinstance(markets, list) and markets:
            return True
    return False


def _build_match_index(matches_payload: list[dict]) -> dict[Any, dict]:
    """Index matches by match_id (str AND int variants) for tolerant lookup."""
    idx: dict[Any, dict] = {}
    for m in matches_payload or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("match_id")
        if mid is None:
            continue
        idx[mid] = m
        # Also store the string form so picks emitted with a string id
        # (LLM-stringified) still resolve to the same match.
        idx[str(mid)] = m
        # And the int form when the id is a digit-only string.
        if isinstance(mid, str) and mid.isdigit():
            try:
                idx[int(mid)] = m
            except (TypeError, ValueError):
                pass
    return idx


def _scrub_pick(entry: dict, reason_codes_log: list[str]) -> bool:
    """Scrub a single pick / discarded entry in-place. Returns True when
    the entry was actually modified."""
    if not isinstance(entry, dict):
        return False
    rec = entry.get("recommendation")
    changed = False
    if isinstance(rec, dict) and rec.get("odds_range") is not None:
        rec["odds_range"] = None
        # Some LLM outputs ALSO carry a ``recommendation.odds`` numeric
        # field — wipe it too so the downstream trace cannot pick it up
        # as a fallback path.
        if "odds" in rec:
            rec["odds"] = None
        entry["recommendation"] = rec
        changed = True
    # Some legacy producers store the odd at the entry top-level (used
    # by `_detect_odds_in_entry`). Wipe those as well.
    for legacy_key in ("odds", "detected_odds", "odds_used", "decimal_odds"):
        if entry.get(legacy_key) is not None:
            entry[legacy_key] = None
            changed = True
    # Stamp the reason code so the audit layer can surface it.
    if changed:
        existing = entry.get("_odds_provenance") or {}
        if not isinstance(existing, dict):
            existing = {}
        existing["status"]        = "SUPPRESSED_NO_UPSTREAM"
        existing["reason_code"]   = REASON_CODE
        existing["llm_hallucinated"] = True
        entry["_odds_provenance"] = existing
        # Make the suppression visible inside the reason_codes array for
        # downstream consumers that already iterate them.
        rc = entry.get("reason_codes")
        if isinstance(rc, list):
            if REASON_CODE not in rc:
                rc.append(REASON_CODE)
        else:
            entry["reason_codes"] = [REASON_CODE]
        reason_codes_log.append(
            f"{entry.get('match_id')}|{entry.get('match_label')}"
        )
    return changed


def suppress_llm_hallucinated_odds(parsed: dict,
                                    matches_payload: list[dict]) -> dict:
    """Public entry. Mutates ``parsed`` in place.

    Parameters
    ----------
    parsed : dict
        Output of the LLM analyst (with ``picks`` + ``summary`` buckets).
    matches_payload : list[dict]
        Original match docs handed to the analyst. Used to detect which
        matches lack real ``odds_snapshots``.

    Returns
    -------
    dict ::

        {
          "available":           bool,
          "suppressed_picks":    int,
          "suppressed_summary":  {bucket: int, ...},
          "matches_no_odds":     int,
          "reason_code":         str,
        }
    """
    if not isinstance(parsed, dict) or not isinstance(matches_payload, list):
        return {"available": False, "suppressed_picks": 0,
                "suppressed_summary": {}, "matches_no_odds": 0,
                "reason_code": REASON_CODE}

    match_index = _build_match_index(matches_payload)
    matches_no_odds = sum(
        1 for m in matches_payload
        if isinstance(m, dict) and not _match_has_real_odds(m)
    )

    log_buf: list[str] = []
    suppressed_picks   = 0
    bucket_suppressed: dict[str, int] = {}

    # ── Top-level picks ──────────────────────────────────────────────
    picks = parsed.get("picks")
    if isinstance(picks, list):
        for entry in picks:
            mid = entry.get("match_id") if isinstance(entry, dict) else None
            match = match_index.get(mid) or match_index.get(str(mid))
            if match is None:
                # Defensive: when we can't find the upstream match, do
                # nothing — silence is safer than a spurious mutation.
                continue
            if _match_has_real_odds(match):
                continue
            if _scrub_pick(entry, log_buf):
                suppressed_picks += 1

    # ── Summary buckets ──────────────────────────────────────────────
    summary = parsed.get("summary")
    if isinstance(summary, dict):
        for bucket_key in _SUMMARY_BUCKETS:
            bucket = summary.get(bucket_key)
            if not isinstance(bucket, list):
                continue
            count = 0
            for entry in bucket:
                mid = entry.get("match_id") if isinstance(entry, dict) else None
                match = match_index.get(mid) or match_index.get(str(mid))
                if match is None:
                    continue
                if _match_has_real_odds(match):
                    continue
                if _scrub_pick(entry, log_buf):
                    count += 1
            if count:
                bucket_suppressed[bucket_key] = count

    total_suppressed = suppressed_picks + sum(bucket_suppressed.values())

    if total_suppressed:
        log.info(
            "[F94.4_HALLUCINATION_GUARD] suppressed_picks=%d summary=%s "
            "matches_without_real_odds=%d sample=%s",
            suppressed_picks, bucket_suppressed, matches_no_odds,
            log_buf[:5],
        )

    return {
        "available":          True,
        "suppressed_picks":   suppressed_picks,
        "suppressed_summary": bucket_suppressed,
        "matches_no_odds":    matches_no_odds,
        "reason_code":        REASON_CODE,
    }


__all__ = [
    "REASON_CODE",
    "suppress_llm_hallucinated_odds",
    "_match_has_real_odds",  # exported for tests
    "_build_match_index",    # exported for tests
]
