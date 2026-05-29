"""MLB Feedback Loop — per-pick outcome storage + automatic weight recalibration.

Architecture (per user decision 2c + 3a)
========================================
Storage layout (HYBRID):
  • `pick_tracking`     — existing collection; we EXTEND each MLB row with
                          {margin, totalRuns, runLineCovered, overHit}.
  • `mlb_pick_feedback` — NEW collection. One doc per settled MLB pick with
                          the FULL v2 metric snapshot (projectedMargin,
                          coverProbability, expectedRuns, lineSelected,
                          pickType, recommendedLine, marginProjection, plus
                          the actual outcome).
  • `mlb_engine_weights` — NEW collection (single doc, _id="active"). Holds
                          the latest auto-recalibrated weights. The v2
                          engine reads from this collection at every analysis
                          call and falls back to `DEFAULT_WEIGHTS` if absent.

Automatic recalibration (3a):
  • Triggered automatically when ≥ FEEDBACK_BATCH_SIZE (50) new picks are
    settled since the last recalibration.
  • Algorithm: per-category Brier-style adjustments — picks with the
    highest accuracy in each category get their factor weights nudged UP
    (×1.02) and the worst get nudged DOWN (×0.98), then re-normalised so
    the sum stays = 1.0 ± 0.05.
  • Bounded: no single weight can drop below 0.05 or exceed 0.65.

Public API
----------
async def get_active_weights(db) -> dict
async def record_mlb_pick_outcome(db, *, pick_id, run_id, match_id, user_id,
                                  outcome, final_home_runs, final_away_runs,
                                  v2_snapshot) -> dict
async def recompute_weights_if_due(db) -> dict | None
async def get_recalibration_status(db) -> dict
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("mlb_feedback_loop")

FEEDBACK_BATCH_SIZE   = 50
WEIGHTS_DOC_ID        = "active"

# Initial weights — mirror the v2 engine's defaults.
# Sum should remain ≈ 1.0. Each represents the contribution of a factor
# to the final Run Line Dominance composite (see mlb_pregame_analytics_v2).
DEFAULT_WEIGHTS: dict[str, float] = {
    "pitcher_edge":       0.30,
    "bullpen":            0.18,
    "fav_offense":        0.14,
    "fav_wins_by_2_rate": 0.18,
    "und_losses_by_2":    0.12,
    "margin_reliability": 0.08,
    # Aggregator weights for parlay scoring (sum ≈ 1.0).
    "parlay_avg_score":   0.45,
    "parlay_frag_inv":    0.20,
    "parlay_correlation": 0.20,
    "parlay_pitcher_conf":0.15,
}

WEIGHT_BOUNDS = (0.05, 0.65)

# Category labels persisted on each feedback record for per-category
# accuracy tracking. Maps each pickType → category bucket.
CATEGORY_FOR_PICK_TYPE: dict[str, str] = {
    "DOMINANT_FAVORITE_RUN_LINE": "run_line_minus_1_5",
    "SMART_LOW_OVER":             "over_low",
    "PITCHER_UNDER":              "under_pitcher_driven",
    "F5_EDGE":                    "f5",
    "TEAM_TOTAL_EDGE":            "team_total",
    "SAME_GAME_CORRELATED_PAIR":  "same_game",
    "GENERIC":                    "other",
}


# ════════════════════════════════════════════════════════════════════════════
# Weights persistence
# ════════════════════════════════════════════════════════════════════════════
async def get_active_weights(db: Any) -> dict:
    """Return the latest persisted weights, or DEFAULT_WEIGHTS if absent.

    Fail-soft: any DB error returns the defaults (engine keeps running).
    """
    if db is None:
        return dict(DEFAULT_WEIGHTS)
    try:
        doc = await db.mlb_engine_weights.find_one({"_id": WEIGHTS_DOC_ID})
    except Exception as exc:
        log.debug("get_active_weights failed: %s — returning defaults", exc)
        return dict(DEFAULT_WEIGHTS)
    if not doc:
        return dict(DEFAULT_WEIGHTS)
    out = dict(DEFAULT_WEIGHTS)
    out.update({k: float(v) for k, v in (doc.get("weights") or {}).items() if isinstance(v, (int, float))})
    out["_version"]            = int(doc.get("version") or 1)
    out["_last_recalibrated"]  = doc.get("last_recalibrated_at")
    out["_picks_in_calibration"] = int(doc.get("picks_in_last_calibration") or 0)
    return out


async def _persist_weights(db: Any, weights: dict, *, picks_used: int) -> None:
    if db is None:
        return
    clean = {k: float(v) for k, v in weights.items()
             if isinstance(v, (int, float)) and not k.startswith("_")}
    try:
        prev = await db.mlb_engine_weights.find_one({"_id": WEIGHTS_DOC_ID})
        version = int((prev or {}).get("version") or 0) + 1
        await db.mlb_engine_weights.update_one(
            {"_id": WEIGHTS_DOC_ID},
            {"$set": {
                "_id":                       WEIGHTS_DOC_ID,
                "weights":                   clean,
                "version":                   version,
                "last_recalibrated_at":      datetime.now(timezone.utc).isoformat(),
                "picks_in_last_calibration": int(picks_used),
            }},
            upsert=True,
        )
        log.info("MLB engine weights v%d persisted (picks=%d): %s",
                 version, picks_used, clean)
    except Exception as exc:
        log.warning("persist_weights failed: %s", exc)


# ════════════════════════════════════════════════════════════════════════════
# Outcome recording
# ════════════════════════════════════════════════════════════════════════════
def _compute_mlb_metrics(
    *,
    selection: str,
    market: str,
    final_home_runs: Optional[int],
    final_away_runs: Optional[int],
    favorite_team: Optional[str],
    home_team: Optional[str],
    away_team: Optional[str],
) -> dict:
    """Compute margin/totalRuns/runLineCovered/overHit from the final score."""
    out = {
        "margin":          None,
        "totalRuns":       None,
        "runLineCovered":  None,
        "overHit":         None,
    }
    if final_home_runs is None or final_away_runs is None:
        return out
    try:
        hr = int(final_home_runs)
        ar = int(final_away_runs)
    except (TypeError, ValueError):
        return out
    out["totalRuns"] = hr + ar
    # Margin from the favourite's perspective.
    fav = (favorite_team or "").strip().lower()
    if fav and fav == (home_team or "").strip().lower():
        out["margin"] = hr - ar
    elif fav and fav == (away_team or "").strip().lower():
        out["margin"] = ar - hr
    else:
        # Default to home margin.
        out["margin"] = hr - ar

    sel_l = (selection or "").lower()
    mkt_l = (market or "").lower()
    if "run line" in mkt_l or "run line" in sel_l:
        if "-1.5" in sel_l:
            out["runLineCovered"] = bool(out["margin"] is not None and out["margin"] >= 2)
        elif "+1.5" in sel_l:
            out["runLineCovered"] = bool(out["margin"] is not None and out["margin"] >= -1)
    # Over/Under hit
    if " over " in f" {sel_l} " or sel_l.startswith("over"):
        try:
            line = float(sel_l.replace("over", "").strip().split()[0])
            out["overHit"] = bool(out["totalRuns"] > line)
        except (ValueError, IndexError):
            pass
    elif " under " in f" {sel_l} " or sel_l.startswith("under"):
        try:
            line = float(sel_l.replace("under", "").strip().split()[0])
            out["overHit"] = bool(out["totalRuns"] < line)
        except (ValueError, IndexError):
            pass
    return out


async def record_mlb_pick_outcome(
    db: Any,
    *,
    pick_id: str,
    run_id: str,
    match_id: str,
    user_id: str,
    outcome: str,            # "won" | "lost" | "push" | "pending"
    final_home_runs: Optional[int] = None,
    final_away_runs: Optional[int] = None,
    v2_snapshot: Optional[dict] = None,
    pick_doc: Optional[dict] = None,
) -> dict:
    """Persist the settled outcome + MLB-specific metrics.

    Writes to BOTH `pick_tracking` (extending with MLB metrics) and the new
    `mlb_pick_feedback` collection (full v2 snapshot). Triggers automatic
    recalibration when the cumulative settled count crosses
    FEEDBACK_BATCH_SIZE since the last calibration.
    """
    if db is None:
        return {"ok": False, "reason": "no_db"}

    v2 = v2_snapshot or {}
    pick_doc = pick_doc or {}
    selection      = pick_doc.get("selection") or v2.get("recommendedLine") or ""
    market         = pick_doc.get("market") or pick_doc.get("recommendation", {}).get("market") or ""
    favorite_team  = v2.get("team") or pick_doc.get("favorite_team")
    home_team      = pick_doc.get("home_team")
    away_team      = pick_doc.get("away_team")

    metrics = _compute_mlb_metrics(
        selection=selection, market=market,
        final_home_runs=final_home_runs, final_away_runs=final_away_runs,
        favorite_team=favorite_team, home_team=home_team, away_team=away_team,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    feedback_doc = {
        "user_id":             user_id,
        "pick_id":             pick_id,
        "run_id":              run_id,
        "match_id":            str(match_id),
        "sport":               "baseball",
        "market":              market,
        "selection":           selection,
        "outcome":             outcome,
        "pickType":            v2.get("pickType"),
        "category":            CATEGORY_FOR_PICK_TYPE.get(v2.get("pickType") or "GENERIC", "other"),
        "projectedMargin":     v2.get("marginProjection"),
        "coverProbability":    v2.get("coverProbability"),
        "expectedRuns":        v2.get("expectedRuns"),
        "lineSelected":        v2.get("recommendedLine"),
        "lineSafetyScore":     v2.get("lineSafetyScore"),
        "fragilityScore":      v2.get("fragilityScore"),
        "sameGameCorrelation": v2.get("sameGameCorrelation"),
        "homeTeam":            home_team,
        "awayTeam":            away_team,
        "favoriteTeam":        favorite_team,
        "finalHomeRuns":       final_home_runs,
        "finalAwayRuns":       final_away_runs,
        "margin":              metrics["margin"],
        "totalRuns":           metrics["totalRuns"],
        "runLineCovered":      metrics["runLineCovered"],
        "overHit":             metrics["overHit"],
        "settled_at":          now_iso,
        "consumed_for_recal":  False,
    }
    try:
        await db.mlb_pick_feedback.update_one(
            {"user_id": user_id, "pick_id": pick_id},
            {"$set": feedback_doc},
            upsert=True,
        )
    except Exception as exc:
        log.warning("mlb_pick_feedback upsert failed: %s", exc)

    # Extend pick_tracking with the MLB metrics (option 2c).
    try:
        await db.pick_tracking.update_one(
            {"user_id": user_id, "pick_id": pick_id},
            {"$set": {
                "outcome":         outcome,
                "mlb_metrics": {
                    "margin":         metrics["margin"],
                    "totalRuns":      metrics["totalRuns"],
                    "runLineCovered": metrics["runLineCovered"],
                    "overHit":        metrics["overHit"],
                },
                "settled_at":      now_iso,
            }},
            upsert=False,
        )
    except Exception as exc:
        log.debug("pick_tracking extension failed (fail-soft): %s", exc)

    # Trigger recalibration if due.
    try:
        recal = await recompute_weights_if_due(db)
    except Exception as exc:
        log.warning("recompute_weights_if_due crashed: %s", exc)
        recal = None
    return {"ok": True, "feedback": feedback_doc, "recalibration": recal}


# ════════════════════════════════════════════════════════════════════════════
# Auto-recalibration
# ════════════════════════════════════════════════════════════════════════════
def _category_accuracy(rows: list[dict]) -> dict[str, dict]:
    """Per-category accuracy from feedback rows."""
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        cat = r.get("category") or "other"
        by_cat.setdefault(cat, []).append(r)
    out: dict[str, dict] = {}
    for cat, group in by_cat.items():
        n = len(group)
        won = sum(1 for r in group if r.get("outcome") == "won")
        lost = sum(1 for r in group if r.get("outcome") == "lost")
        push = sum(1 for r in group if r.get("outcome") == "push")
        decided = won + lost
        acc = (won / decided) if decided else 0.0
        out[cat] = {"count": n, "won": won, "lost": lost, "push": push,
                    "decided": decided, "accuracy": round(acc, 4)}
    return out


def _adjust_weights(current: dict[str, float], accuracy_by_cat: dict[str, dict]) -> dict[str, float]:
    """Nudge weights using per-category accuracy.

    Categories with accuracy ≥ 0.60 push their corresponding factor weights
    UP by 2%; categories with accuracy ≤ 0.45 push them DOWN by 2%. The
    weights are then re-normalised inside their bound [0.05, 0.65] and the
    full vector is re-scaled so its sum stays = 1.0 (within ±0.05).
    """
    # Map each category to the factor weights it most influences.
    cat_to_factors = {
        "run_line_minus_1_5":   ["pitcher_edge", "fav_wins_by_2_rate", "und_losses_by_2", "margin_reliability"],
        "over_low":             ["bullpen", "fav_offense"],
        "under_pitcher_driven": ["pitcher_edge"],
        "f5":                   ["pitcher_edge"],
        "team_total":           ["fav_offense", "bullpen"],
        "same_game":            ["parlay_correlation", "parlay_pitcher_conf"],
        "other":                [],
    }
    new = dict(current)
    for cat, stats in accuracy_by_cat.items():
        if stats["decided"] < 5:
            continue   # not enough data to move weights for this category
        acc = stats["accuracy"]
        if acc >= 0.60:
            factor = 1.02
        elif acc <= 0.45:
            factor = 0.98
        else:
            continue
        for fkey in cat_to_factors.get(cat, []):
            if fkey in new and isinstance(new[fkey], (int, float)):
                low, high = WEIGHT_BOUNDS
                new[fkey] = max(low, min(high, new[fkey] * factor))

    # Re-normalise the "scoring" group and the "parlay" group separately.
    score_keys = ["pitcher_edge", "bullpen", "fav_offense",
                  "fav_wins_by_2_rate", "und_losses_by_2", "margin_reliability"]
    parlay_keys = ["parlay_avg_score", "parlay_frag_inv",
                   "parlay_correlation", "parlay_pitcher_conf"]
    for group in (score_keys, parlay_keys):
        present = [k for k in group if k in new]
        s = sum(new[k] for k in present)
        if s <= 0:
            continue
        scale = 1.0 / s
        for k in present:
            new[k] = round(new[k] * scale, 4)
    return new


async def recompute_weights_if_due(db: Any) -> Optional[dict]:
    """Run a recalibration when ≥FEEDBACK_BATCH_SIZE unconsumed feedback
    rows exist. Returns the new weights doc on success, else None.
    """
    if db is None:
        return None
    try:
        unconsumed_count = await db.mlb_pick_feedback.count_documents(
            {"consumed_for_recal": False,
             "outcome":            {"$in": ["won", "lost", "push"]}}
        )
    except Exception as exc:
        log.debug("count_documents failed: %s", exc)
        return None
    if unconsumed_count < FEEDBACK_BATCH_SIZE:
        return None

    rows = await db.mlb_pick_feedback.find(
        {"consumed_for_recal": False,
         "outcome":            {"$in": ["won", "lost", "push"]}}
    ).limit(FEEDBACK_BATCH_SIZE * 2).to_list(length=FEEDBACK_BATCH_SIZE * 2)

    accuracy = _category_accuracy(rows)
    current  = await get_active_weights(db)
    # Strip metadata before adjusting
    current_clean = {k: v for k, v in current.items() if not k.startswith("_")}
    new_weights = _adjust_weights(current_clean, accuracy)

    await _persist_weights(db, new_weights, picks_used=len(rows))

    # Mark these rows as consumed.
    pick_ids = [r["pick_id"] for r in rows if r.get("pick_id")]
    try:
        await db.mlb_pick_feedback.update_many(
            {"pick_id": {"$in": pick_ids}},
            {"$set": {"consumed_for_recal": True,
                      "consumed_at":        datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as exc:
        log.warning("mark consumed failed: %s", exc)

    log.info("MLB weights recalibrated using %d feedback rows: %s",
             len(rows), accuracy)
    return {"new_weights": new_weights, "accuracy_by_category": accuracy,
            "rows_used": len(rows)}


async def get_recalibration_status(db: Any) -> dict:
    """Lightweight status endpoint for the dashboard.

    Returns:
        {
          "active_weights":         {...},
          "pending_for_next_recal": int,  # rows since last recalibration
          "batch_size_required":    FEEDBACK_BATCH_SIZE,
          "settled_total":          int,
          "last_recalibration_at":  ISO | None,
          "version":                int,
        }
    """
    weights = await get_active_weights(db)
    if db is None:
        return {"active_weights": weights, "pending_for_next_recal": 0,
                "batch_size_required": FEEDBACK_BATCH_SIZE, "settled_total": 0,
                "last_recalibration_at": None, "version": 0}
    try:
        pending = await db.mlb_pick_feedback.count_documents(
            {"consumed_for_recal": False,
             "outcome":            {"$in": ["won", "lost", "push"]}}
        )
        total   = await db.mlb_pick_feedback.count_documents(
            {"outcome": {"$in": ["won", "lost", "push"]}}
        )
    except Exception:
        pending, total = 0, 0
    return {
        "active_weights":         {k: v for k, v in weights.items() if not k.startswith("_")},
        "pending_for_next_recal": pending,
        "batch_size_required":    FEEDBACK_BATCH_SIZE,
        "settled_total":          total,
        "last_recalibration_at":  weights.get("_last_recalibrated"),
        "version":                int(weights.get("_version") or 1),
    }


__all__ = [
    "DEFAULT_WEIGHTS",
    "FEEDBACK_BATCH_SIZE",
    "get_active_weights",
    "record_mlb_pick_outcome",
    "recompute_weights_if_due",
    "get_recalibration_status",
    "CATEGORY_FOR_PICK_TYPE",
]
