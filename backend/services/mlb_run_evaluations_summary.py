"""Calibration view for ``mlb_run_evaluations``.

Aggregates settled evaluations into hit-rate breakdowns useful for
tuning the explosive engine and detecting drift. Pure async service —
no HTTP concerns. The endpoint at ``GET /api/mlb/run-evaluations/summary``
calls :func:`compute_run_evaluations_summary` directly.

Filtering rules
---------------
* Only documents with ``result in ("won", "lost", "push")`` are counted
  in the headline buckets — ``pending`` is excluded by definition and
  ``void`` is treated as a legacy backward-compat input only (it never
  reaches new settles).
* The default window is 30 days. Callers can override via ``days``.
* The default ``user_id`` is ``"_slate"`` because that is the cohort
  the orchestrator writes pregame evaluations under. Individual user
  IDs can be passed for per-user views.

Sections of the response
------------------------
``by_risk_tier``                    LOW / MEDIUM / HIGH hit-rate
``by_flip``                         flip_triggered True vs False
``by_market_scope``                 full_game / f5 / inning / team_total
``by_miss_type``                    OVER_BEAT_UNDER / UNDER_BEAT_OVER / PUSH
``high_conservative_won_anyway``    HIGH + should_recommend=False that won
``reference_profile_activations``   docs with REFERENCE_MLB_POWER_BAT_EXPLOSIVE
``dynamic_park_blocks``             docs with veto_source=DYNAMIC_PARK_OFFENSIVE
``central_under_vetoes``            docs with veto_source=CENTRAL_UNDER_VETO
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .mlb_run_storage import REFERENCE_MLB_POWER_BAT_EXPLOSIVE

log = logging.getLogger("mlb_run_evaluations_summary")

SETTLED_OUTCOMES = ("won", "lost", "push")


def _hit_rate_bucket(subset: list[dict]) -> dict:
    """Build a {total, won, lost, push, hit_rate} stats block for a
    subset of settled documents. ``hit_rate`` is the percentage of
    ``won`` over total (push is treated as neutral). Returns
    ``hit_rate=None`` for empty subsets so callers can disambiguate
    "no data" from "0% hit rate"."""
    total = len(subset)
    if total == 0:
        return {"total": 0, "won": 0, "lost": 0, "push": 0, "hit_rate": None}
    won  = sum(1 for d in subset if d.get("result") == "won")
    lost = sum(1 for d in subset if d.get("result") == "lost")
    push = sum(1 for d in subset if d.get("result") == "push")
    return {
        "total":   total,
        "won":     won,
        "lost":    lost,
        "push":    push,
        "hit_rate": round((won / total) * 100, 2),
    }


async def compute_run_evaluations_summary(db,
                                            *,
                                            days: int = 30,
                                            user_id: str = "_slate",
                                            ) -> dict:
    """Compute calibration breakdowns for the last ``days``.

    Args
    ----
    db        : Motor database handle.
    days      : Lookback window. Capped at 365.
    user_id   : Cohort selector. Default ``"_slate"`` (orchestrator
                pregame writes). Pass a user UUID for per-user views.

    Returns
    -------
    dict with the breakdowns described in the module docstring.
    """
    capped_days = max(1, min(365, int(days or 30)))
    cutoff_iso = (datetime.now(timezone.utc)
                  - timedelta(days=capped_days)).isoformat()

    # ── Base settled set ──────────────────────────────────────────
    settled_filter = {
        "user_id":      user_id,
        "sport":        "baseball",
        "generated_at": {"$gte": cutoff_iso},
        "result":       {"$in": list(SETTLED_OUTCOMES)},
    }
    settled_docs = await db.mlb_run_evaluations.find(
        settled_filter, {"_id": 0}
    ).to_list(length=10000)

    overall = _hit_rate_bucket(settled_docs)

    # ── Breakdowns ───────────────────────────────────────────────
    by_risk_tier = {
        tier: _hit_rate_bucket(
            [d for d in settled_docs if d.get("risk_tier") == tier]
        )
        for tier in ("HIGH", "MEDIUM", "LOW")
    }

    by_flip = {
        "flip_true":  _hit_rate_bucket(
            [d for d in settled_docs if d.get("flip_triggered") is True]
        ),
        "flip_false": _hit_rate_bucket(
            [d for d in settled_docs if d.get("flip_triggered") is False]
        ),
    }

    market_scopes = sorted({
        d.get("market_scope") for d in settled_docs if d.get("market_scope")
    })
    by_market_scope = {
        sc: _hit_rate_bucket(
            [d for d in settled_docs if d.get("market_scope") == sc]
        )
        for sc in market_scopes
    }

    miss_types = sorted({
        d.get("miss_type") for d in settled_docs if d.get("miss_type")
    })
    by_miss_type = {
        mt: _hit_rate_bucket(
            [d for d in settled_docs if d.get("miss_type") == mt]
        )
        for mt in miss_types
    }

    # ── HIGH-risk conservative wins (engine refused to recommend
    #    but the natural outcome turned out to be a win anyway) ───
    high_no_rec = [
        d for d in settled_docs
        if d.get("risk_tier") == "HIGH"
        and not bool(d.get("should_recommend"))
    ]
    high_no_rec_won = sum(1 for d in high_no_rec if d.get("result") == "won")

    high_conservative_won_anyway = {
        "total":    len(high_no_rec),
        "won":      high_no_rec_won,
        "hit_rate": (round((high_no_rec_won / len(high_no_rec)) * 100, 2)
                      if high_no_rec else None),
    }

    # ── Reference profile activations + veto counts ──────────────
    reference_activations = sum(
        1 for d in settled_docs
        if d.get("reference_profile_tag") == REFERENCE_MLB_POWER_BAT_EXPLOSIVE
    )

    # Vetoes: count over ALL evaluations in the window (including
    # pending) since a veto is a pregame decision worth tracking even
    # if the game hasn't finished. Build the same time filter without
    # the result constraint.
    veto_filter = {
        "user_id":      user_id,
        "sport":        "baseball",
        "generated_at": {"$gte": cutoff_iso},
    }
    veto_docs = await db.mlb_run_evaluations.find(
        veto_filter,
        {"_id": 0, "veto_source": 1, "result": 1, "blocked_market": 1,
         "explosive_risk_score": 1, "risk_tier": 1},
    ).to_list(length=10000)

    dynamic_park_blocks = sum(
        1 for d in veto_docs if d.get("veto_source") == "DYNAMIC_PARK_OFFENSIVE"
    )
    central_under_vetoes = sum(
        1 for d in veto_docs if d.get("veto_source") == "CENTRAL_UNDER_VETO"
    )

    # "Dynamic Park BLOCK evitaron picks malos" — vetoes that ended
    # in matchups whose final total CONFIRMED the Over (i.e., the Under
    # we would have bet would have lost). For settled docs only.
    park_blocks_saved = sum(
        1 for d in settled_docs
        if d.get("veto_source") == "DYNAMIC_PARK_OFFENSIVE"
        and d.get("blocked_market")
        and "under" in (d.get("blocked_market") or "").lower()
        and d.get("result") == "lost"   # Under would have lost → veto saved us
    )

    return {
        "ok":           True,
        "window_days":  capped_days,
        "user_id":      user_id,
        "evaluated_total":         overall["total"],
        "overall":                 overall,
        "by_risk_tier":            by_risk_tier,
        "by_flip":                 by_flip,
        "by_market_scope":         by_market_scope,
        "by_miss_type":            by_miss_type,
        "high_conservative_won_anyway": high_conservative_won_anyway,
        "reference_profile_activations": reference_activations,
        "dynamic_park_blocks":     dynamic_park_blocks,
        "park_blocks_saved":       park_blocks_saved,
        "central_under_vetoes":    central_under_vetoes,
        "settled_outcomes_filter": list(SETTLED_OUTCOMES),
    }


__all__ = ["compute_run_evaluations_summary", "SETTLED_OUTCOMES"]
