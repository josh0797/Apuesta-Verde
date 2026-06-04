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

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("learning_cases")


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
    for case in SEED_CASES + MLB_SEED_CASES:
        res = await db.learning_cases.update_one(
            {"case_id": case["case_id"]}, {"$setOnInsert": case}, upsert=True,
        )
        if res.upserted_id is not None:
            inserted += 1
    return inserted


# ════════════════════════════════════════════════════════════════════════
# MLB seed-cases + pattern detection (FIX #4 — Yankees @ A's Under fails)
# ════════════════════════════════════════════════════════════════════════
# Curated post-mortems from 8 consecutive Under losses in late May 2026.
# Each case carries the trigger context observable BEFORE the game and a
# lesson_es so the LLM-narrative side can quote it. They feed
# `detect_mlb_under_warning_pattern` which short-circuits any Under pick
# whose context matches a previously-lost archetype.

MLB_SEED_CASES: list[dict] = [
    {
        "case_id":     "yankees-athletics-2026-05-31",
        "title":       "Yankees @ A's — Under 9.5 falló por inning explosivo",
        "rule_key":    "power_bat_visiting_avoid_under",
        "match_label": "Yankees @ Athletics",
        "league":      "MLB",
        "sport":       "baseball",
        "date":        "2026-05-31",
        "engine_pick": "Under 9.5",
        "user_pick":   "Under 9.5",
        "final_score": "13-8",
        "outcome":     "lost",
        "trigger_context": {
            "yankees_ops":             0.785,
            "yankees_3rd_inning_runs": 13,
            "pitchers_era":            [3.2, 4.1],
        },
        "lesson_es": (
            "Cuando un equipo con OPS > 0.770 visita, no apostar Under "
            "aunque los pitchers tengan ERA elite. Un solo inning explosivo "
            "(5+ carreras) destruye el pick."
        ),
        "tags": ["mlb", "under", "power_bat", "yankees"],
    },
    {
        "case_id":     "twins-pirates-2026-05-31",
        "title":       "Twins @ Pirates — Under en serie activa con bullpens cargados",
        "rule_key":    "active_series_overs_avoid_under",
        "match_label": "Twins @ Pirates",
        "league":      "MLB",
        "sport":       "baseball",
        "date":        "2026-05-31",
        "engine_pick": "Under 9.5",
        "user_pick":   "Under 9.5",
        "final_score": None,
        "outcome":     "lost",
        "trigger_context": {
            "previous_games_avg":   15.0,
            "bullpen_pitch_stress": [3.24, 3.31],
            "game_in_series":       3,
        },
        "lesson_es": (
            "En el tercer juego de serie con bullpens cargados "
            "(pitch_stress > 1.5) Y promedio de serie > 12 runs, "
            "el Under tiene <30% de hit rate. Evitar."
        ),
        "tags": ["mlb", "under", "series_context", "bullpen_fatigue"],
    },
]


def detect_mlb_under_warning_pattern(
    match: Optional[dict] = None,
    scoring_ctx: Optional[dict] = None,
) -> Optional[dict]:
    """Return rules-fired payload (or None) for the current MLB context.

    Applies the lessons learned from prior MLB Under losses:

      1) `power_bat_visiting_avoid_under` — any team OPS > 0.770.
      2) `active_series_overs_avoid_under` — active-series avg > 12 runs
         AND game ≥ 2 of series AND one bullpen with pitch_stress > 1.5.
      3) Curated rules (``CURATED_UNDER_VETO_RULES``) — qualitative
         patterns that the dispersion model alone cannot capture
         (offensive park + tired bullpens, active-series override,
         late-series hot lineups, ace regression).

    Each fired rule includes ``block`` (True/False) so the caller can
    short-circuit the Under selection. Returns ``None`` when no rule
    applies — caller treats that as "no learned pattern".
    """
    if not scoring_ctx:
        return None
    rules_fired: list[dict] = []

    # Rule 1 — power bat present.
    try:
        home_ops = float(scoring_ctx.get("home_team_ops") or 0)
    except (TypeError, ValueError):
        home_ops = 0.0
    try:
        away_ops = float(scoring_ctx.get("away_team_ops") or 0)
    except (TypeError, ValueError):
        away_ops = 0.0
    if max(home_ops, away_ops) > 0.770:
        rules_fired.append({
            "rule_key":   "power_bat_visiting_avoid_under",
            "case_ref":   "yankees-athletics-2026-05-31",
            "block":      True,
            "evidence":   {"max_ops": round(max(home_ops, away_ops), 3)},
        })

    # Rule 2 — active series overs with fatigued bullpens.
    series_ctx = scoring_ctx.get("active_series_context") or {}
    bp_home    = scoring_ctx.get("home_bullpen_real") or {}
    bp_away    = scoring_ctx.get("away_bullpen_real") or {}
    try:
        series_avg = float(series_ctx.get("total_runs_avg") or 0)
    except (TypeError, ValueError):
        series_avg = 0.0
    games_in_series = int(series_ctx.get("games_in_series") or 0)
    try:
        psi_home = float(bp_home.get("pitch_stress_index") or 0)
    except (TypeError, ValueError):
        psi_home = 0.0
    try:
        psi_away = float(bp_away.get("pitch_stress_index") or 0)
    except (TypeError, ValueError):
        psi_away = 0.0
    if (series_avg > 12.0
            and games_in_series >= 2
            and (psi_home > 1.5 or psi_away > 1.5)):
        rules_fired.append({
            "rule_key":   "active_series_overs_avoid_under",
            "case_ref":   "twins-pirates-2026-05-31",
            "block":      True,
            "evidence":   {
                "series_avg":      round(series_avg, 1),
                "games_in_series": games_in_series,
                "max_pitch_stress": round(max(psi_home, psi_away), 2),
            },
        })

    # ── Rules 3..N — curated post-mortem patterns ───────────────────
    # Each curated rule has its own condition + severity. We evaluate
    # them defensively so a broken predicate never blows up the caller.
    for curated in CURATED_UNDER_VETO_RULES:
        try:
            triggered = bool(curated["condition"](scoring_ctx))
        except Exception as exc:
            log.debug("curated rule %s eval failed: %s",
                       curated.get("rule_key"), exc)
            continue
        if not triggered:
            continue
        rules_fired.append({
            "rule_key":      curated["rule_key"],
            "severity":      curated["severity"],
            "block":         curated["severity"] == "BLOCK",
            "explanation":   curated["explanation"],
            "evidence":      _curated_evidence(curated["rule_key"], scoring_ctx),
        })

    if not rules_fired:
        return None
    return {
        "any_block":   any(r.get("block") for r in rules_fired),
        "rules_fired": rules_fired,
    }


# ════════════════════════════════════════════════════════════════════════
# Curated qualitative rules (Enfoque C)
# ════════════════════════════════════════════════════════════════════════
# These complement the dispersion-ratio feedback loop: they capture
# patterns where the empirical variance is misleading because a few
# qualitative signals (offensive park + tired bullpens, active-series
# override, ace regression) drive the upper tail. The dispersion model
# corrects MAGNITUDE; these rules correct CONTEXT.

def _park_is_offensive(ctx: dict) -> bool:
    """True when the live park context reads as a hitter park.

    Accepts both ``park_factor_live`` (the dynamic block from
    ``mlb_park_factor_live``) and the legacy flat ``park`` dict.
    """
    park = ctx.get("park_factor_live") or ctx.get("park") or {}
    code = (park.get("code") or "").upper()
    if code == "OFFENSIVE":
        return True
    dynamic = park.get("dynamic") or park.get("park_runs_mult") or 1.0
    try:
        return float(dynamic) >= 1.08
    except (TypeError, ValueError):
        return False


def _both_bullpens_fatigued(ctx: dict) -> bool:
    """True when BOTH bullpens are tagged as fatigued via either the
    real-usage index (``pitch_stress_index``) or the legacy 0-100
    ``fatigue_score_0_100``."""
    def _tired(bp):
        bp = bp or {}
        stress = bp.get("pitch_stress_index")
        fatigue = bp.get("fatigue_score_0_100")
        if stress is not None:
            try:
                return float(stress) >= 1.3   # ~58+ pitches in 48h
            except (TypeError, ValueError):
                pass
        if fatigue is not None:
            try:
                return float(fatigue) >= 60   # "high" or worse
            except (TypeError, ValueError):
                pass
        return False
    return _tired(ctx.get("home_bullpen_real")) and _tired(ctx.get("away_bullpen_real"))


def _hot_from_split(side: Optional[dict]) -> bool:
    """True when a recent-form L5 split exposes >+20% delta vs L15."""
    if not isinstance(side, dict):
        return False
    try:
        return float(side.get("delta_pct")) > 20.0
    except (TypeError, ValueError):
        return False


def _both_offenses_hot_l5(ctx: dict) -> bool:
    """Both teams must show >+20% delta in the L5 recent-run split."""
    rr = ctx.get("recent_run_split") or {}
    return _hot_from_split(rr.get("home")) and _hot_from_split(rr.get("away"))


def _any_pitcher_overperforming(ctx: dict) -> bool:
    """True when ``_regression_signal == "PITCHER_OVERPERFORMING"`` is
    set on either starter — flag emitted by the regression layer when
    ERA undershoots xERA by ≥1.0."""
    for key in ("home_pitcher", "away_pitcher"):
        p = ctx.get(key) or {}
        if isinstance(p, dict) and p.get("_regression_signal") == "PITCHER_OVERPERFORMING":
            return True
    return False


def _curated_evidence(rule_key: str, ctx: dict) -> dict:
    """Return a compact evidence dict for the rule that fired so the
    UI / logs can show which specific signals matched."""
    if rule_key == "OFFENSIVE_PARK_PLUS_TIRED_BULLPENS":
        park = ctx.get("park_factor_live") or ctx.get("park") or {}
        return {
            "park_code":       (park.get("code") or "").upper() or None,
            "park_dynamic":    park.get("dynamic") or park.get("park_runs_mult"),
            "home_bp_stress":  (ctx.get("home_bullpen_real") or {}).get("pitch_stress_index"),
            "away_bp_stress":  (ctx.get("away_bullpen_real") or {}).get("pitch_stress_index"),
        }
    if rule_key == "ACTIVE_SERIES_HIGH_SCORING":
        sd = ctx.get("active_series_context") or {}
        return {
            "series_override":  sd.get("series_override"),
            "series_lean":      sd.get("series_lean"),
            "total_runs_avg":   sd.get("total_runs_avg"),
        }
    if rule_key == "LATE_SERIES_BOTH_LINEUPS_HOT":
        sd = ctx.get("series_degradation") or {}
        rr = ctx.get("recent_run_split") or {}
        return {
            "game_in_series": sd.get("game_in_series"),
            "home_delta_pct": (rr.get("home") or {}).get("delta_pct"),
            "away_delta_pct": (rr.get("away") or {}).get("delta_pct"),
        }
    if rule_key == "OVERPERFORMING_ACE_REGRESSION":
        return {
            "home_signal": (ctx.get("home_pitcher") or {}).get("_regression_signal"),
            "away_signal": (ctx.get("away_pitcher") or {}).get("_regression_signal"),
        }
    return {}


CURATED_UNDER_VETO_RULES: list[dict] = [
    {
        "rule_key":   "OFFENSIVE_PARK_PLUS_TIRED_BULLPENS",
        "severity":   "BLOCK",
        "condition":  lambda ctx: (
            _park_is_offensive(ctx)
            and _both_bullpens_fatigued(ctx)
        ),
        "explanation": (
            "Parque ofensivo + ambos bullpens agotados. Combinación de "
            "cola alta que el modelo de runs subestima. Under bloqueado "
            "(caso Colorado @ Angels)."
        ),
    },
    {
        "rule_key":   "ACTIVE_SERIES_HIGH_SCORING",
        "severity":   "BLOCK",
        "condition":  lambda ctx: (
            bool((ctx.get("active_series_context") or {}).get("series_override"))
            and ((ctx.get("active_series_context") or {}).get("series_lean") == "OVER")
        ),
        "explanation": (
            "La serie activa promedia muy por encima de la línea y forzó "
            "override OVER. Under bloqueado (caso Twins @ Pirates 15 runs/juego)."
        ),
    },
    {
        "rule_key":   "LATE_SERIES_BOTH_LINEUPS_HOT",
        "severity":   "WARNING",
        "condition":  lambda ctx: (
            int((ctx.get("series_degradation") or {}).get("game_in_series", 1)) >= 3
            and _both_offenses_hot_l5(ctx)
        ),
        "explanation": (
            "G3 de serie con ambas alineaciones calientes en L5 — los "
            "bateadores ya tienen book completo del abridor. Riesgo de "
            "cola alta elevado."
        ),
    },
    {
        "rule_key":   "OVERPERFORMING_ACE_REGRESSION",
        "severity":   "WARNING",
        "condition":  lambda ctx: _any_pitcher_overperforming(ctx),
        "explanation": (
            "Un abridor está sobre-rindiendo su xERA por ≥1.0 — probable "
            "regresión a la media. El Under apoyado en ese abridor es frágil."
        ),
    },
]


__all__ = [
    "RULE_CLOSE_MODERATE_PRIORITISE_U35",
    "SEED_CASES",
    "MLB_SEED_CASES",
    "detect_close_moderate_pace",
    "detect_mlb_under_warning_pattern",
    "apply_case_rules",
    "save_case",
    "list_cases",
    "seed_cases",
    "CURATED_UNDER_VETO_RULES",
]
