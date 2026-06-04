"""Football Feedback Loop — settle outcomes + pattern memory updates.

Wires settle events from the football flow into the warehouse:
  * Inserts a row into ``football_market_results``.
  * Updates ``football_pattern_memory`` aggregates (sample_size, wins,
    hit_rate, roi, market_ledger, best_market).

Design principles (NON-NEGOTIABLE):
  * Fail-soft: any DB error is swallowed; returns ``{available:false}``
    so the caller still completes the settle response normally.
  * Idempotency at the result row is intentionally NOT enforced (a user
    can re-settle if needed); the warehouse will count each event.
  * Conservative outcome interpretation: ``won=False`` is the default
    when the outcome string is ambiguous.
"""

from __future__ import annotations

import logging
from typing import Any

from .football_intelligence_warehouse import (
    persist_football_market_result,
)

log = logging.getLogger("football_moneyball.feedback")


_WON_OUTCOMES = {"won", "win", "hit", "covered", "green", "acertado"}
_LOST_OUTCOMES = {"lost", "loss", "miss", "red", "fallado", "failed"}
_VOID_OUTCOMES = {"void", "push", "refund", "cancelled", "canceled", "nulo"}


def _interpret_outcome(outcome: str | None, won_flag: Any) -> tuple[bool, str]:
    """Return (won_bool, normalized_outcome)."""
    if isinstance(won_flag, bool):
        return won_flag, ("won" if won_flag else "lost")
    norm = (outcome or "").strip().lower()
    if norm in _WON_OUTCOMES:
        return True, "won"
    if norm in _LOST_OUTCOMES:
        return False, "lost"
    if norm in _VOID_OUTCOMES:
        return False, "void"
    # Unknown → conservative: not won.
    return False, norm or "unknown"


async def record_football_pick_outcome(
    db,
    *,
    match_id: str | int,
    user_id: str | None,
    market: str | None,
    selection: str | None = None,
    odds: float | None = None,
    stake: float = 1.0,
    outcome: str | None = None,
    won: Any = None,
    payout: float = 0.0,
    pattern_keys: list[str] | None = None,
    final_score: dict | None = None,
    snapshot_ref: dict | None = None,
) -> dict:
    """Persist a football pick outcome + update pattern memory.

    Returns ``{available: bool, persisted: bool, won: bool, outcome: str,
    pattern_keys: [str, ...]}``.
    """
    if db is None:
        return {"available": False, "reason": "db_unavailable"}

    won_bool, normalized = _interpret_outcome(outcome, won)
    # Compute payout if it wasn't passed and we have odds.
    if payout in (0, 0.0, None):
        try:
            if won_bool and odds is not None:
                payout = float(stake) * float(odds)
            elif normalized == "void":
                payout = float(stake)
            else:
                payout = 0.0
        except Exception:
            payout = 0.0

    persisted = await persist_football_market_result(
        db,
        match_id=match_id,
        user_id=user_id,
        market=market,
        selection=selection,
        odds=odds,
        pattern_keys=pattern_keys or [],
        stake=float(stake),
        won=won_bool,
        payout=float(payout),
        result=normalized,
        final_score=final_score,
        snapshot_ref=snapshot_ref,
    )
    return {
        "available":    True,
        "persisted":    bool(persisted),
        "won":          won_bool,
        "outcome":      normalized,
        "pattern_keys": list(pattern_keys or []),
        "payout":       float(payout),
        "stake":        float(stake),
    }


__all__ = ["record_football_pick_outcome"]
