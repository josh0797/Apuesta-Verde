"""Learning Cases — persistent knowledge base of validated picks that
encode patterns the engine should learn from.

A "case" represents a real-world outcome that taught us something about
how to interpret live matches. The first seeded case is the Pumas vs
Cruz Azul (May 24, 2026, Liga MX) where:

  • Score favoured the home side mid-game (Pumas 1-0)
  • xG / shots / threat / pressure stayed near-equal (visitor pushing)
  • Engine suggested Under 2.5 (correct for the moment)
  • User chose Under 3.5 — the *protected* line
  • Final: 2-1 → Under 3.5 won, Under 2.5 lost on the 87th-min late goal

The lesson: in CLOSE matches with MODERATE pace (some pressure but no
true open game), Under 3.5 is a sturdier read than Under 2.5 because
the protection of the extra goal cushion matters more than the 0.4
points of edge that U2.5 normally has on paper.

This module exposes:

    detect_close_moderate_pace(match, analysis)  → bool
        Heuristic that returns True iff the case-1 rule applies.

    apply_case_rules(picked_line, alt_line, match, analysis)
        Re-orders the under_market_scan picker so U3.5 is preferred
        when the rule fires.

    SEED_CASES: list[dict]
        Initial knowledge base shipped with the app.

    save_case(db, case_dict) (async)
    list_cases(db, *, limit=50, rule_key=None) (async)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


# Rule keys are stable identifiers used by:
#   • the scan to know which case fired
#   • the interpreter to cite the case in `why` bullets
#   • the UI to look up the human-readable label
RULE_CLOSE_MODERATE_PRIORITISE_U35 = "close_match_moderate_pace_prefer_u35"


SEED_CASES: list[dict] = [
    {
        "case_id":      "pumas-cruzazul-2026-05-24",
        "title":        "Pumas vs Cruz Azul — Under 3.5 protegido sobre Under 2.5",
        "rule_key":     RULE_CLOSE_MODERATE_PRIORITISE_U35,
        "match_label":  "U.N.A.M. - Pumas vs Cruz Azul",
        "league":       "Liga MX",
        "date":         "2026-05-24",
        "engine_pick":  "Under 2.5",
        "user_pick":    "Under 3.5",
        "user_odds":    1.25,
        "stake":        400.00,
        "payout":       500.00,
        "final_score":  "2-1",
        "outcome":      "won",
        "trigger_context": {
            "score_diff_max":     1,    # final lead margin
            "minute_when_called": 79,
            "live_xg_home":       1.36,
            "live_xg_away":       1.90,
            "shots_home":         9,
            "shots_away":        14,
            "shots_on_target_home": 3,
            "shots_on_target_away": 5,
            "possession_home":   48,
            "possession_away":   52,
        },
        "lesson_es": (
            "En partidos cerrados (diferencia ≤ 1 gol después del minuto 60) "
            "con ritmo moderado (xG combinado entre 1.5 y 4.0 y ninguna defensa "
            "colapsada), Under 3.5 representa mejor la lectura real que Under 2.5: "
            "la supervivencia del pick supera al edge agresivo de la línea baja. "
            "El sistema debe priorizar supervivencia del pick sobre cuota agresiva."
        ),
        "lesson_en": (
            "In tight matches (lead ≤ 1 after minute 60) with moderate pace "
            "(combined xG between 1.5 and 4.0 and no collapsed defence), Under 3.5 "
            "better reflects the game than Under 2.5: pick survival beats the "
            "aggressive edge of the lower line."
        ),
        "tags": ["under_3_5", "protected_market", "close_match", "moderate_pace", "liga_mx"],
        "created_at": "2026-05-24T22:00:00+00:00",
        "_source":    "user_validated",
    },
]


def detect_close_moderate_pace(match: dict, analysis: Optional[dict] = None) -> Optional[dict]:
    """Heuristic for the case-1 rule.

    Returns a dict {"applies": True, "rule_key": ..., "context": ...} when
    the match is in the same regime as Pumas-Cruz Azul; None otherwise.

    Conditions (ALL must hold):
      1. live + minute >= 60 (we need late-game data)
      2. abs(score_diff) <= 1 (tight match)
      3. total goals so far ≤ 2 (room for U3.5 to still survive 1 more goal)
      4. combined live xG between 1.0 and 3.0 (moderate pace — not goalless,
         not a shootout)
      5. neither side completely outclassing on threat (no >2.5x ratio)
    """
    live = match.get("live_stats") or {}
    minute = live.get("minute")
    if not isinstance(minute, (int, float)) or minute < 60:
        return None

    score = live.get("score") or {}
    h_score = int(score.get("home") or 0)
    a_score = int(score.get("away") or 0)
    diff = abs(h_score - a_score)
    total = h_score + a_score
    if diff > 1:
        return None
    if total > 2:
        # Already past Under 3.5 cushion threshold — case rule doesn't fit.
        return None

    # Pull live metrics from `analysis` (output of live_xg_proxy.compute_live_analysis)
    if not analysis:
        return None
    home = analysis.get("home") or {}
    away = analysis.get("away") or {}
    xg_h = float(home.get("xg_live") or 0)
    xg_a = float(away.get("xg_live") or 0)
    xg_combined = xg_h + xg_a
    if xg_combined < 1.0 or xg_combined > 3.0:
        return None

    th_h = max(0.01, float(home.get("threat_index") or 0))
    th_a = max(0.01, float(away.get("threat_index") or 0))
    threat_ratio = max(th_h, th_a) / max(th_h, th_a, 0.01)  # always 1 if both same
    threat_ratio = max(th_h, th_a) / min(th_h, th_a) if min(th_h, th_a) > 0 else 1.0
    if threat_ratio > 2.5:
        return None

    return {
        "applies":  True,
        "rule_key": RULE_CLOSE_MODERATE_PRIORITISE_U35,
        "case_id":  "pumas-cruzazul-2026-05-24",
        "context":  {
            "minute":       int(minute),
            "score_diff":   diff,
            "current_total": total,
            "xg_combined": round(xg_combined, 2),
            "threat_ratio": round(threat_ratio, 2),
        },
    }


def apply_case_rules(profile_3_5: dict, profile_2_5: dict, match: dict,
                     analysis: Optional[dict]) -> tuple[str, dict, Optional[dict]]:
    """Wrap the under_market_scan's `_select_preferred_under` selection
    with knowledge-base overrides.

    Args:
        profile_3_5: existing scan profile for Under 3.5
        profile_2_5: existing scan profile for Under 2.5
        match: hydrated match doc
        analysis: live_xg_proxy.compute_live_analysis output (optional)

    Returns:
        (line_label, picked_profile, applied_rule | None)

    The picked profile carries the rule's annotation so the interpreter
    can mention "Regla aprendida: caso Pumas-Cruz Azul" in the `why`
    bullets.
    """
    rule = detect_close_moderate_pace(match, analysis)
    if not rule or not rule.get("applies"):
        return None, None, None  # let the caller fall through

    # Only fire if BOTH lines are eligible. If U3.5 is INSUFFICIENT
    # there's nothing to switch to.
    if profile_3_5.get("state") == "INSUFFICIENT":
        return None, None, None

    # Always prefer Under 3.5 when the rule fires.
    picked = dict(profile_3_5)
    picked["_applied_rule"] = rule
    return "Under 3.5", picked, rule


async def save_case(db, case: dict) -> dict:
    """Persist a learning case to Mongo. Returns the inserted/updated doc."""
    if db is None or not case:
        return case
    case = dict(case)
    case.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    cid = case.get("case_id")
    if cid:
        await db.learning_cases.update_one({"case_id": cid}, {"$set": case}, upsert=True)
    else:
        await db.learning_cases.insert_one(case)
    return case


async def list_cases(db, *, limit: int = 50, rule_key: Optional[str] = None) -> list[dict]:
    """Return cases sorted by created_at descending."""
    if db is None:
        return []
    q: dict = {}
    if rule_key:
        q["rule_key"] = rule_key
    cur = db.learning_cases.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cur.to_list(length=limit)


async def seed_cases(db) -> int:
    """Idempotent seed of the initial case library. Returns the count inserted."""
    if db is None:
        return 0
    inserted = 0
    for case in SEED_CASES:
        res = await db.learning_cases.update_one(
            {"case_id": case["case_id"]}, {"$setOnInsert": case}, upsert=True,
        )
        if res.upserted_id is not None:
            inserted += 1
    return inserted


__all__ = [
    "RULE_CLOSE_MODERATE_PRIORITISE_U35",
    "SEED_CASES",
    "detect_close_moderate_pace",
    "apply_case_rules",
    "save_case",
    "list_cases",
    "seed_cases",
]
