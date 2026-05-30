"""F6B — MLB Script Breaks Storage Hook.

Persists *script break events* to the ``mlb_script_breaks`` collection
whenever a pick is settled (via ``/api/mlb/picks/{pick_id}/settle``).

A script break event is a record of how the *pregame script* compared
against the *actual final outcome*. This is the data the future learning
loop (Phase 7) will consume to teach the engine which pregame scripts
fail most often (e.g., "LOW_SCORING_PITCHERS_DUEL collapses 28 % of the
time when away starter's xERA > 4.00").

This module is *read-mostly-pure*: it consumes the snapshot of the pick
that the settle endpoint already collects and writes a single document
per settled pick. It does NOT modify pick_tracking or mlb_pick_feedback
(those are written by ``mlb_feedback_loop.record_mlb_pick_outcome``).

Collection: ``mlb_script_breaks``
Schema (all fields optional except *id*, *pick_id*, *match_id*, *outcome*):

    id:                 str (uuid)
    pick_id:            str
    run_id:             str
    match_id:           str
    user_id:            str
    sport:              "baseball"
    settled_at:         datetime (UTC, tz-aware)
    outcome:            "won" | "lost" | "push" | "pending"

    pregame_script:     str              # e.g. "LOW_SCORING_PITCHERS_DUEL"
    pregame_label_es:   str
    expected_runs:      float            # pregame
    projected_margin:   float            # pregame
    recommended_line:   str              # e.g. "UNDER 9.5"
    market:             str              # e.g. "Total Runs Under"
    selection:          str
    confidence_score:   float            # pregame

    final_home_runs:    int | None
    final_away_runs:    int | None
    total_runs_actual:  int | None
    margin_actual:      int | None       # |home - away|
    runs_diff_vs_expected: float | None  # actual - expected_runs

    script_broken:      bool             # True when reality diverged
    severity:           "NONE" | "MILD" | "STRONG"
    break_reasons:      list[str]

    starter_runs_allowed_home: int | None
    starter_runs_allowed_away: int | None
    starter_blowup:     bool

    bullpen_swap_applied: bool           # whether F6A swap was active

    learning_event_codes: list[str]      # eg ["UNDER_BUSTED_BY_STARTER_BLOWUP"]

Helpers
-------
``store_script_break_event(db, ..., outcome, ...)`` is the main entry
point used by the settle endpoint. ``query_recent_script_breaks(db,
sport, days, limit)`` is a thin reader for inspection / future
learning.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("mlb_script_breaks_storage")

COLLECTION_NAME = "mlb_script_breaks"


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _derive_break_assessment(
    pregame_script_code: str,
    expected_runs: float,
    total_runs_actual: Optional[int],
    starter_runs_home: Optional[int],
    starter_runs_away: Optional[int],
    outcome: str,
    selection: str,
) -> dict:
    """Compute whether the pregame script broke based on final result.

    Returns
    -------
    {"script_broken": bool, "severity": str, "reasons": list[str],
     "starter_blowup": bool, "learning_codes": list[str]}
    """
    reasons: list[str] = []
    learning_codes: list[str] = []
    script_broken = False
    severity = "NONE"

    sel_lower = (selection or "").lower()
    is_under_pick = "under" in sel_lower
    is_over_pick  = "over"  in sel_lower and not is_under_pick

    starter_blowup = False
    sr_home = _i(starter_runs_home)
    sr_away = _i(starter_runs_away)
    if (sr_home is not None and sr_home >= 5) or (sr_away is not None and sr_away >= 5):
        starter_blowup = True
        reasons.append(
            f"Abridor permitió 5+ carreras (home={sr_home}, away={sr_away})."
        )
        script_broken = True
        severity = "STRONG"

    if total_runs_actual is not None and expected_runs is not None and expected_runs > 0:
        diff = total_runs_actual - expected_runs
        # Low-scoring scripts that exploded.
        if pregame_script_code in ("LOW_SCORING_PITCHERS_DUEL", "LOW_VARIANCE_GAME",
                                    "FAVORITE_DOMINANCE", "UNDERDOG_CAN_COMPETE"):
            if diff >= 4.0:
                script_broken = True
                severity = "STRONG"
                reasons.append(
                    f"Total real ({total_runs_actual}) {diff:+.1f} carreras "
                    f"sobre lo proyectado ({expected_runs:.1f})."
                )
            elif diff >= 2.5:
                if severity != "STRONG":
                    severity = "MILD"
                script_broken = True
                reasons.append(
                    f"Total real ({total_runs_actual}) supera el ER pregame "
                    f"({expected_runs:.1f}) por {diff:+.1f}."
                )
        # Shootout scripts that fizzled.
        if pregame_script_code in ("OFFENSIVE_SHOOTOUT", "OFFENSIVE_BREAKOUT") and diff <= -3.0:
            script_broken = True
            if severity != "STRONG":
                severity = "MILD"
            reasons.append(
                f"Shootout proyectado no se materializó: total {total_runs_actual} "
                f"vs ER {expected_runs:.1f}."
            )

    # Learning codes — useful for the Phase 7 negative learning loop.
    if outcome == "lost" and is_under_pick and starter_blowup:
        learning_codes.append("UNDER_BUSTED_BY_STARTER_BLOWUP")
    if outcome == "lost" and is_under_pick and total_runs_actual is not None \
            and expected_runs and total_runs_actual >= expected_runs + 3:
        learning_codes.append("UNDER_BUSTED_BY_OFFENSIVE_BREAKOUT")
    if outcome == "won" and is_under_pick and starter_blowup is False \
            and total_runs_actual is not None and expected_runs \
            and total_runs_actual <= expected_runs:
        learning_codes.append("UNDER_HELD_AS_SCRIPTED")
    if outcome == "lost" and is_over_pick and total_runs_actual is not None \
            and expected_runs and total_runs_actual <= expected_runs - 2:
        learning_codes.append("OVER_FIZZLED")
    if script_broken and outcome == "won":
        # The script broke but we still won — likely cashout or lucky.
        learning_codes.append("WON_DESPITE_BROKEN_SCRIPT")

    if not reasons:
        reasons.append("Script pregame se mantuvo coherente con el resultado.")

    return {
        "script_broken":  script_broken,
        "severity":       severity,
        "reasons":        reasons,
        "starter_blowup": starter_blowup,
        "learning_codes": learning_codes,
    }


# ════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════
async def store_script_break_event(
    db: Any,
    *,
    pick_id: str,
    run_id: str,
    match_id: str,
    user_id: str,
    outcome: str,
    final_home_runs: Optional[int] = None,
    final_away_runs: Optional[int] = None,
    pick_doc: Optional[dict] = None,
    v2_snapshot: Optional[dict] = None,
) -> dict:
    """Persist one ``mlb_script_breaks`` document for the settled pick.

    Idempotent on (pick_id, outcome): re-settling the same pick with the
    same outcome overwrites the previous document via upsert.
    """
    pick_doc    = pick_doc or {}
    v2_snapshot = v2_snapshot or {}
    # The frontend usually sends the *full* pregame pick payload, including
    # _mlb_script_v3 → script. Extract it defensively.
    v3 = pick_doc.get("_mlb_script_v3") or {}
    pregame_script = v3.get("script") or pick_doc.get("game_script") or {}
    pregame_script_code = (pregame_script.get("script_code")
                            or pick_doc.get("script_code")
                            or "UNKNOWN").upper()

    expected_runs = _f(
        pregame_script.get("expected_runs")
        or v2_snapshot.get("expectedRuns")
        or pick_doc.get("expected_runs"),
        0.0,
    )
    projected_margin = _f(
        pregame_script.get("projected_margin")
        or v2_snapshot.get("marginProjection"),
        0.0,
    )
    recommendation = pick_doc.get("recommendation") or {}
    market         = recommendation.get("market") or pick_doc.get("market") or ""
    selection      = recommendation.get("selection") or pick_doc.get("selection") or ""
    recommended_line = (
        v2_snapshot.get("recommendedLine")
        or recommendation.get("recommendedLine")
        or pick_doc.get("recommended_line")
        or ""
    )
    confidence_score = _f(
        recommendation.get("confidence_score")
        or recommendation.get("score")
        or pick_doc.get("confidence_score"),
        0,
    )

    sr_home = _i(
        pick_doc.get("home_starter_runs_allowed")
        or (pick_doc.get("teams", {}).get("home", {}).get("starter_runs_allowed") if isinstance(pick_doc.get("teams"), dict) else None)
    )
    sr_away = _i(
        pick_doc.get("away_starter_runs_allowed")
        or (pick_doc.get("teams", {}).get("away", {}).get("starter_runs_allowed") if isinstance(pick_doc.get("teams"), dict) else None)
    )

    fr_home = _i(final_home_runs)
    fr_away = _i(final_away_runs)
    total_runs_actual = None
    margin_actual     = None
    if fr_home is not None and fr_away is not None:
        total_runs_actual = fr_home + fr_away
        margin_actual     = abs(fr_home - fr_away)

    assessment = _derive_break_assessment(
        pregame_script_code=pregame_script_code,
        expected_runs=expected_runs,
        total_runs_actual=total_runs_actual,
        starter_runs_home=sr_home,
        starter_runs_away=sr_away,
        outcome=outcome,
        selection=str(selection) or str(recommended_line),
    )

    bullpen_swap_applied = bool(recommendation.get("bullpen_swap")
                                 or pick_doc.get("bullpen_swap"))

    doc = {
        "id":                  str(uuid.uuid4()),
        "pick_id":             str(pick_id),
        "run_id":              str(run_id),
        "match_id":            str(match_id),
        "user_id":             str(user_id),
        "sport":               "baseball",
        "settled_at":          datetime.now(timezone.utc),
        "outcome":             outcome,

        "pregame_script":      pregame_script_code,
        "pregame_label_es":    pregame_script.get("label_es"),
        "expected_runs":       expected_runs,
        "projected_margin":    projected_margin,
        "recommended_line":    recommended_line,
        "market":              market,
        "selection":           selection,
        "confidence_score":    confidence_score,

        "final_home_runs":     fr_home,
        "final_away_runs":     fr_away,
        "total_runs_actual":   total_runs_actual,
        "margin_actual":       margin_actual,
        "runs_diff_vs_expected":
            None if total_runs_actual is None or not expected_runs
            else round(total_runs_actual - expected_runs, 2),

        "script_broken":       assessment["script_broken"],
        "severity":            assessment["severity"],
        "break_reasons":       assessment["reasons"],

        "starter_runs_allowed_home": sr_home,
        "starter_runs_allowed_away": sr_away,
        "starter_blowup":            assessment["starter_blowup"],

        "bullpen_swap_applied":      bullpen_swap_applied,

        "learning_event_codes":      assessment["learning_codes"],
    }

    try:
        # Upsert keyed on pick_id so re-settles overwrite cleanly.
        await db[COLLECTION_NAME].update_one(
            {"pick_id": str(pick_id)},
            {"$set": doc},
            upsert=True,
        )
    except Exception as exc:
        log.exception("Failed to upsert mlb_script_breaks doc for pick %s: %s",
                       pick_id, exc)
        return {"ok": False, "error": str(exc)}

    return {
        "ok":                     True,
        "id":                     doc["id"],
        "pick_id":                doc["pick_id"],
        "script_broken":          doc["script_broken"],
        "severity":               doc["severity"],
        "learning_event_codes":   doc["learning_event_codes"],
        "bullpen_swap_applied":   bullpen_swap_applied,
    }


async def query_recent_script_breaks(
    db: Any,
    *,
    user_id: Optional[str] = None,
    days: int = 30,
    limit: int = 100,
    only_broken: bool = False,
    script_code: Optional[str] = None,
) -> list[dict]:
    """Return the most recent script break documents (newest first)."""
    q: dict = {}
    if user_id:
        q["user_id"] = str(user_id)
    if only_broken:
        q["script_broken"] = True
    if script_code:
        q["pregame_script"] = script_code.upper()
    if days and days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        q["settled_at"] = {"$gte": cutoff}
    try:
        cursor = db[COLLECTION_NAME].find(q).sort("settled_at", -1).limit(limit)
        docs = await cursor.to_list(length=limit)
    except Exception as exc:
        log.exception("query_recent_script_breaks failed: %s", exc)
        return []
    # Strip Mongo's _id for clean JSON.
    out = []
    for d in docs:
        d.pop("_id", None)
        # Ensure datetimes serialise cleanly.
        if isinstance(d.get("settled_at"), datetime):
            d["settled_at"] = d["settled_at"].isoformat()
        out.append(d)
    return out


async def aggregate_break_stats(db: Any, *, days: int = 60) -> dict:
    """Summary statistics for the inspection UI / learning loop.

    Returns
    -------
    {
        "total":             int,
        "broken":            int,
        "broken_rate":       float (0-1),
        "by_script":         {script_code: {total, broken, rate}},
        "top_learning_codes": [{code, count}, ...],
    }
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    try:
        docs = await db[COLLECTION_NAME].find(
            {"settled_at": {"$gte": cutoff}}
        ).to_list(length=10_000)
    except Exception as exc:
        log.exception("aggregate_break_stats failed: %s", exc)
        return {"total": 0, "broken": 0, "broken_rate": 0.0,
                "by_script": {}, "top_learning_codes": []}

    total = len(docs)
    broken = 0
    by_script: dict[str, dict] = {}
    code_counts: dict[str, int] = {}
    for d in docs:
        sc = (d.get("pregame_script") or "UNKNOWN").upper()
        by_script.setdefault(sc, {"total": 0, "broken": 0})
        by_script[sc]["total"] += 1
        if d.get("script_broken"):
            broken += 1
            by_script[sc]["broken"] += 1
        for code in d.get("learning_event_codes") or []:
            code_counts[code] = code_counts.get(code, 0) + 1
    for sc, v in by_script.items():
        v["rate"] = round(v["broken"] / v["total"], 3) if v["total"] else 0.0

    top_codes = sorted(
        ({"code": c, "count": n} for c, n in code_counts.items()),
        key=lambda x: -x["count"],
    )[:10]

    return {
        "window_days":       days,
        "total":             total,
        "broken":            broken,
        "broken_rate":       round(broken / total, 3) if total else 0.0,
        "by_script":         by_script,
        "top_learning_codes": top_codes,
    }


__all__ = [
    "store_script_break_event",
    "query_recent_script_breaks",
    "aggregate_break_stats",
    "COLLECTION_NAME",
]
