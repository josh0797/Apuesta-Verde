"""Line Learning Engine — Push / Near-Miss / Protected-line feedback.

Phase 42.

The platform's prior learning pipeline only ingested ``won`` / ``lost``
outcomes against the engine recommendation. That ignores two pieces of
high-value signal:

  * **The user's ACTUAL bet** — when the user took a more protected line
    (e.g. ``Under 10.0`` while engine said ``Under 9.5``) and the game
    landed at exactly the engine's line, the engine LOST while the user
    pushed. The engine's *profile read* was right, just its *line was
    too aggressive*. We want to learn that.

  * **Line distance from the result** — a half-run miss is statistically
    very different from a 3-run blowout. Both are "lost" but one means
    "tighten the line" and the other means "the model was wrong".

This module is **pure** — no I/O. The server-side caller is responsible
for persisting the samples to mongo and feeding the pattern memory.

The integration philosophy is **observe-only by default**: we save every
sample but the weighted learning that biases new recommendations only
kicks in once we have ≥ ``LINE_LEARNING_MIN_SAMPLES`` for that
``(sport, market_type)`` cohort. Configurable via env::

  LINE_LEARNING_MODEL_WEIGHT     (default 0.7) — engine model weight
  LINE_LEARNING_FEEDBACK_WEIGHT  (default 0.3) — real-world feedback weight
  LINE_LEARNING_MIN_SAMPLES      (default 30)  — observe-only threshold

Sample / classification contract — what we persist per pick::

    {
      "sample_id":           "<uuid>",
      "user_id":             "<user>",
      "match_id":            "<match>",
      "sport":               "football|basketball|baseball",
      "market_type":         "total_runs|total_goals|...",
      "engine": {
        "market":      "total_runs_under",
        "selection":   "Under 9.5",
        "line":        9.5,
        "odds":        1.85,
        "projection":  7.8,
      },
      "user_actual": {
        "market":      "total_runs_under",
        "selection":   "Under 10.0",
        "line":        10.0,
        "odds":        1.26,
      },
      "result": {
        "final_value":     10,
        "engine_outcome":  "lost",
        "user_outcome":    "push",
      },
      "line_distance":   0.5,        # user_line - engine_line
      "classification":  "PUSH_SAVED",
      "reason_codes":    ["PUSH_SAVED_BY_LINE", "LOST_BY_HALF_RUN"],
      "summary_es":      "...",
      "model_weight":    0.7,
      "feedback_weight": 0.3,
      "observe_only":    True,        # → does NOT bias recommendations yet
      "created_at":      "...",
      "engine_version":  "line_learning.1",
    }
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("line_learning")

ENGINE_VERSION = "line_learning.1"

# ─────────────────────────────────────────────────────────────────────
# Knobs (configurable via env so we can adjust without redeploy)
# ─────────────────────────────────────────────────────────────────────
def model_weight() -> float:
    try:
        return float(os.environ.get("LINE_LEARNING_MODEL_WEIGHT", "0.7"))
    except (TypeError, ValueError):
        return 0.7


def feedback_weight() -> float:
    try:
        v = float(os.environ.get("LINE_LEARNING_FEEDBACK_WEIGHT", ""))
        return v if 0.0 <= v <= 1.0 else 1.0 - model_weight()
    except (TypeError, ValueError):
        return 1.0 - model_weight()


def min_samples_threshold() -> int:
    try:
        return max(1, int(os.environ.get("LINE_LEARNING_MIN_SAMPLES", "30")))
    except (TypeError, ValueError):
        return 30


# ─────────────────────────────────────────────────────────────────────
# Classification constants
# ─────────────────────────────────────────────────────────────────────
CLASS_EXACT_HIT             = "EXACT_HIT"
CLASS_NEAR_MISS             = "NEAR_MISS"
CLASS_PUSH_SAVED            = "PUSH_SAVED"
CLASS_AGGRESSIVE_LINE_MISS  = "AGGRESSIVE_LINE_MISS"
CLASS_SAFE_LINE_HIT         = "SAFE_LINE_HIT"
CLASS_PROFILE_WRONG         = "PROFILE_WRONG"   # used with OVERWHELMING_PROJECTION_MISS
CLASS_UNDEFINED             = "UNDEFINED"

ALL_CLASSIFICATIONS = (
    CLASS_EXACT_HIT,
    CLASS_NEAR_MISS,
    CLASS_PUSH_SAVED,
    CLASS_AGGRESSIVE_LINE_MISS,
    CLASS_SAFE_LINE_HIT,
    CLASS_PROFILE_WRONG,
    CLASS_UNDEFINED,
)

# Reason codes (the UI / analytics renders these literal strings).
RC_PUSH_SAVED_BY_LINE         = "PUSH_SAVED_BY_LINE"
RC_LOST_BY_HALF_RUN           = "LOST_BY_HALF_RUN"
RC_SAFE_LINE_SURVIVED         = "SAFE_LINE_SURVIVED"
RC_AGGRESSIVE_LINE_TOO_TIGHT  = "AGGRESSIVE_LINE_TOO_TIGHT"
RC_OVERWHELMING_PROJECTION_MISS = "OVERWHELMING_PROJECTION_MISS"
RC_LINE_BIAS_AGGRESSIVE       = "LINE_BIAS_AGGRESSIVE"
RC_LINE_BIAS_PROTECTED        = "LINE_BIAS_PROTECTED"
RC_USER_AGREED_WITH_ENGINE    = "USER_AGREED_WITH_ENGINE"

ALL_REASON_CODES = (
    RC_PUSH_SAVED_BY_LINE,
    RC_LOST_BY_HALF_RUN,
    RC_SAFE_LINE_SURVIVED,
    RC_AGGRESSIVE_LINE_TOO_TIGHT,
    RC_OVERWHELMING_PROJECTION_MISS,
    RC_LINE_BIAS_AGGRESSIVE,
    RC_LINE_BIAS_PROTECTED,
    RC_USER_AGREED_WITH_ENGINE,
)

# Engine-side outcome states accepted on input.
WON_OUTCOMES    = {"won", "win", "hit", "cashout_win"}
LOST_OUTCOMES   = {"lost", "lose", "loss", "miss", "cashout_loss"}
VOID_OUTCOMES   = {"void", "push", "refund", "refunded", "cancelled", "canceled"}

# A "near-miss" half-step (for totals markets this is 0.5; for spreads
# we keep it 0.5 too as a sane default).
NEAR_MISS_HALF_STEP = 0.5
# "Overwhelming" projection miss when |result - projection| > this many
# *units of line distance*. Calibrated so projection 6 vs result 13
# (delta 7) fires while projection 7.8 vs result 10 (delta 2.2) doesn't
# — the latter is the canonical "engine called Under correctly but
# line was too aggressive" case.
OVERWHELMING_PROJECTION_FACTOR = 6.0


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _is_under_market(selection: str | None, market: str | None) -> Optional[bool]:
    s = (selection or "").lower()
    m = (market or "").lower()
    if "under" in s or "under" in m or "menos" in s:
        return True
    if "over" in s or "over" in m or "más" in s or "mas" in s:
        return False
    return None


def compute_line_distance(
    *, engine_line: Optional[float], user_line: Optional[float],
) -> Optional[float]:
    """Return ``user_line - engine_line`` (signed).

    Positive distance for totals means the user took a MORE protected
    line for an Under (e.g. engine 9.5, user 10.0 → +0.5: user is
    protected). For an Over it would mean the user took an EASIER line
    (engine Over 9.5, user Over 9.0 → -0.5 → easier). The classifier
    interprets the sign in conjunction with the market side.
    """
    a = _safe_float(engine_line)
    b = _safe_float(user_line)
    if a is None or b is None:
        return None
    return round(b - a, 2)


def is_user_more_protected(
    *,
    line_distance: Optional[float],
    is_under: Optional[bool],
) -> Optional[bool]:
    """Was the user's chosen line MORE protected than the engine's?

    For an Under bet: protected = LARGER number (Under 10.0 is safer than
    Under 9.5). So ``line_distance > 0`` means more protected.
    For an Over bet: protected = SMALLER number (Over 9.0 is safer than
    Over 9.5). So ``line_distance < 0`` means more protected.
    """
    if line_distance is None or is_under is None:
        return None
    if abs(line_distance) < 1e-9:
        return False
    return (line_distance > 0) if is_under else (line_distance < 0)


# ─────────────────────────────────────────────────────────────────────
# Single-line outcome resolution from a final result value
# ─────────────────────────────────────────────────────────────────────
def _resolve_line_outcome(
    *,
    line: Optional[float],
    final_value: Optional[float],
    is_under: Optional[bool],
) -> Optional[str]:
    """Return ``won`` / ``lost`` / ``push`` purely from line + result.

    Returns ``None`` when we don't have enough data.
    """
    if line is None or final_value is None or is_under is None:
        return None
    if abs(final_value - line) < 1e-9:
        return "push"
    if is_under:
        return "won" if final_value < line else "lost"
    return "won" if final_value > line else "lost"


# ─────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────
def classify(
    *,
    engine_line: Optional[float],
    user_line: Optional[float],
    engine_projection: Optional[float],
    final_value: Optional[float],
    engine_outcome: Optional[str] = None,
    user_outcome: Optional[str] = None,
    is_under: Optional[bool] = None,
    market_type: Optional[str] = None,
) -> dict:
    """Classify the (engine, user, result) tuple. Pure, never raises.

    ``engine_outcome`` and ``user_outcome`` may be supplied (e.g. when
    the user marks them manually); otherwise we re-derive from
    ``final_value`` + lines.

    Returns ``{"classification": str, "reason_codes": [str, ...]}``.
    """
    out = {"classification": CLASS_UNDEFINED, "reason_codes": []}
    try:
        el = _safe_float(engine_line)
        ul = _safe_float(user_line)
        fv = _safe_float(final_value)
        ep = _safe_float(engine_projection)

        # Re-derive outcomes from lines + final when not supplied.
        eo = (engine_outcome or "").lower() or _resolve_line_outcome(
            line=el, final_value=fv, is_under=is_under,
        )
        uo = (user_outcome or "").lower() or _resolve_line_outcome(
            line=ul, final_value=fv, is_under=is_under,
        )

        line_dist = compute_line_distance(engine_line=el, user_line=ul)
        protected = is_user_more_protected(
            line_distance=line_dist, is_under=is_under,
        )

        reasons: list[str] = []

        # User aligned with engine — line_distance == 0 (or no user line).
        if line_dist is None or abs(line_dist) < 1e-9:
            reasons.append(RC_USER_AGREED_WITH_ENGINE)

        # Engine outcome buckets
        if eo in WON_OUTCOMES:
            classification = (
                CLASS_SAFE_LINE_HIT if (
                    fv is not None and el is not None and abs(fv - el) > NEAR_MISS_HALF_STEP
                ) else CLASS_EXACT_HIT
            )
            if classification == CLASS_SAFE_LINE_HIT:
                reasons.append(RC_SAFE_LINE_SURVIVED)
            if eo == "cashout_win":
                reasons.append("CASHOUT_WIN")

        elif eo in VOID_OUTCOMES:
            classification = CLASS_PUSH_SAVED if (
                uo in WON_OUTCOMES or uo in VOID_OUTCOMES
            ) else CLASS_NEAR_MISS

        elif eo in LOST_OUTCOMES:
            # The engine LOST. Now we look at the user's outcome to
            # decide WHY: aggressive line, profile wrong, or near-miss.
            # ORDER OF PRIORITY:
            #   1) User's protected line saved bankroll (push)  → PUSH_SAVED
            #   2) User's protected line won outright           → AGGRESSIVE_LINE_MISS
            #   3) Engine loss within half-step                 → NEAR_MISS
            #   4) Both engine + user lost AND projection far   → PROFILE_WRONG
            #   5) Everything else                              → AGGRESSIVE_LINE_MISS
            if uo in VOID_OUTCOMES and protected is True:
                classification = CLASS_PUSH_SAVED
                reasons.append(RC_PUSH_SAVED_BY_LINE)
                reasons.append(RC_AGGRESSIVE_LINE_TOO_TIGHT)
            elif uo in WON_OUTCOMES and protected is True:
                classification = CLASS_AGGRESSIVE_LINE_MISS
                reasons.append(RC_AGGRESSIVE_LINE_TOO_TIGHT)
                reasons.append(RC_LINE_BIAS_AGGRESSIVE)
            elif fv is not None and el is not None and abs(fv - el) <= NEAR_MISS_HALF_STEP:
                classification = CLASS_NEAR_MISS
                reasons.append(RC_LOST_BY_HALF_RUN)
            elif uo in LOST_OUTCOMES and ep is not None and fv is not None and \
                 abs(fv - ep) > NEAR_MISS_HALF_STEP * OVERWHELMING_PROJECTION_FACTOR:
                # Both engine + user lost AND projection was way off →
                # the engine read the WRONG SIDE of the game entirely.
                classification = CLASS_PROFILE_WRONG
                reasons.append(RC_OVERWHELMING_PROJECTION_MISS)
            else:
                classification = CLASS_AGGRESSIVE_LINE_MISS

            # Even when we already classified PUSH_SAVED / AGGRESSIVE,
            # flag a profile-wrong reason when projection is far off and
            # NEITHER line survived (information value for line bias).
            if (uo in LOST_OUTCOMES and ep is not None and fv is not None
                    and abs(fv - ep) > NEAR_MISS_HALF_STEP * OVERWHELMING_PROJECTION_FACTOR
                    and RC_OVERWHELMING_PROJECTION_MISS not in reasons):
                reasons.append(RC_OVERWHELMING_PROJECTION_MISS)
        else:
            classification = CLASS_UNDEFINED

        # Line-bias signal: if line_distance ≠ 0 and the user's choice
        # OUT-performed the engine's (push/win where engine lost),
        # flag a systematic aggressive bias.
        if line_dist is not None and abs(line_dist) > 1e-9 and \
           eo in LOST_OUTCOMES and uo in (WON_OUTCOMES | VOID_OUTCOMES):
            if protected is True and RC_LINE_BIAS_AGGRESSIVE not in reasons:
                reasons.append(RC_LINE_BIAS_AGGRESSIVE)
            elif protected is False and RC_LINE_BIAS_PROTECTED not in reasons:
                reasons.append(RC_LINE_BIAS_PROTECTED)

        out["classification"] = classification
        out["reason_codes"] = reasons
        out["line_distance"] = line_dist
        out["user_more_protected"] = protected
        out["engine_outcome_resolved"] = eo
        out["user_outcome_resolved"] = uo
        return out
    except Exception as exc:  # pragma: no cover — fail-soft guard
        log.debug("classify failed: %s", exc)
        return out


# ─────────────────────────────────────────────────────────────────────
# Summary builder (Spanish — UI surfaces this as a learning panel)
# ─────────────────────────────────────────────────────────────────────
def build_summary(classification: dict, *, lang: str = "es") -> str:
    cls = classification.get("classification")
    reasons = classification.get("reason_codes") or []
    ld = classification.get("line_distance")
    if lang == "en":
        return _summary_en(cls, reasons, ld)
    parts: list[str] = []
    if cls == CLASS_EXACT_HIT:
        parts.append("Lectura correcta del partido")
    elif cls == CLASS_SAFE_LINE_HIT:
        parts.append("Lectura correcta; línea segura sobrevivió cómoda")
    elif cls == CLASS_PUSH_SAVED:
        if RC_PUSH_SAVED_BY_LINE in reasons:
            parts.append("Lectura correcta; línea engine demasiado agresiva, push salvó bankroll")
        else:
            parts.append("Push: salió bankroll intacto")
    elif cls == CLASS_NEAR_MISS:
        parts.append("Lectura correcta; perdió por medio punto (near-miss)")
    elif cls == CLASS_AGGRESSIVE_LINE_MISS:
        parts.append("Línea engine demasiado agresiva")
        if RC_LINE_BIAS_AGGRESSIVE in reasons:
            parts.append("la línea del usuario sobrevivió → sesgo agresivo detectado")
    elif cls == CLASS_PROFILE_WRONG:
        parts.append("Lectura del partido incorrecta (overwhelming miss)")
    else:
        parts.append("Sin clasificación clara")

    if ld is not None and abs(ld) > 1e-9:
        parts.append(f"distancia de línea {ld:+.1f}")
    if RC_OVERWHELMING_PROJECTION_MISS in reasons:
        parts.append("proyección muy lejos del resultado real")
    return ". ".join(parts) + "."


def _summary_en(cls: str | None, reasons: list, ld: Optional[float]) -> str:
    if cls == CLASS_EXACT_HIT:
        base = "Correct game read"
    elif cls == CLASS_SAFE_LINE_HIT:
        base = "Correct read; safe line survived comfortably"
    elif cls == CLASS_PUSH_SAVED:
        base = ("Correct profile; engine line too tight — push saved bankroll"
                if RC_PUSH_SAVED_BY_LINE in (reasons or []) else "Push: bankroll preserved")
    elif cls == CLASS_NEAR_MISS:
        base = "Correct read; lost by half a step (near-miss)"
    elif cls == CLASS_AGGRESSIVE_LINE_MISS:
        base = "Engine line too aggressive"
    elif cls == CLASS_PROFILE_WRONG:
        base = "Wrong game read (overwhelming miss)"
    else:
        base = "Unclassified"
    if ld is not None and abs(ld) > 1e-9:
        base += f". Line distance {ld:+.1f}"
    return base + "."


# ─────────────────────────────────────────────────────────────────────
# Public sample builder — what gets persisted to mongo
# ─────────────────────────────────────────────────────────────────────
def build_learning_sample(
    *,
    user_id: Optional[str],
    match_id: Optional[str],
    sport: Optional[str],
    market_type: Optional[str],
    engine_market: Optional[str],
    engine_selection: Optional[str],
    engine_line: Optional[float],
    engine_odds: Optional[float],
    engine_projection: Optional[float] = None,
    engine_outcome: Optional[str] = None,
    user_market: Optional[str] = None,
    user_selection: Optional[str] = None,
    user_line: Optional[float] = None,
    user_odds: Optional[float] = None,
    user_outcome: Optional[str] = None,
    final_value: Optional[float] = None,
    final_score: Optional[dict] = None,
    cohort_sample_count: int = 0,
    sample_id: Optional[str] = None,
) -> dict:
    """Build the persisted sample dict from a settled pick.

    ``cohort_sample_count`` is the # of existing samples for the
    ``(sport, market_type)`` cohort BEFORE this one — used to decide
    ``observe_only`` mode.
    """
    is_under = _is_under_market(engine_selection, engine_market)
    classification = classify(
        engine_line=engine_line,
        user_line=user_line,
        engine_projection=engine_projection,
        final_value=final_value,
        engine_outcome=engine_outcome,
        user_outcome=user_outcome,
        is_under=is_under,
        market_type=market_type,
    )
    threshold = min_samples_threshold()
    observe_only = (cohort_sample_count + 1) < threshold
    summary_es = build_summary(classification, lang="es")
    summary_en = build_summary(classification, lang="en")

    return {
        "sample_id":   sample_id or str(uuid.uuid4()),
        "user_id":     user_id,
        "match_id":    match_id,
        "sport":       sport,
        "market_type": market_type or _infer_market_type(engine_market, engine_selection),
        "engine": {
            "market":     engine_market,
            "selection":  engine_selection,
            "line":       _safe_float(engine_line),
            "odds":       _safe_float(engine_odds),
            "projection": _safe_float(engine_projection),
            "outcome":    (engine_outcome or "").lower() or classification.get("engine_outcome_resolved"),
        },
        "user_actual": {
            "market":    user_market,
            "selection": user_selection,
            "line":      _safe_float(user_line),
            "odds":      _safe_float(user_odds),
            "outcome":   (user_outcome or "").lower() or classification.get("user_outcome_resolved"),
        },
        "result": {
            "final_value":     _safe_float(final_value),
            "final_score":     final_score,
            "engine_outcome":  classification.get("engine_outcome_resolved"),
            "user_outcome":    classification.get("user_outcome_resolved"),
        },
        "line_distance":        classification.get("line_distance"),
        "user_more_protected":  classification.get("user_more_protected"),
        "classification":       classification.get("classification"),
        "reason_codes":         classification.get("reason_codes") or [],
        "summary_es":           summary_es,
        "summary_en":           summary_en,
        "model_weight":         model_weight(),
        "feedback_weight":      feedback_weight(),
        "observe_only":         observe_only,
        "cohort_sample_count":  cohort_sample_count + 1,
        "min_samples_threshold": threshold,
        "engine_version":       ENGINE_VERSION,
        "created_at":           datetime.now(timezone.utc).isoformat(),
    }


def _infer_market_type(market: Optional[str], selection: Optional[str]) -> str:
    blob = ((market or "") + " " + (selection or "")).lower()
    if "runs" in blob:
        return "total_runs"
    if "goals" in blob or "btts" in blob or any(g in blob for g in ("over", "under")):
        return "total_goals"
    if "puntos" in blob or "points" in blob:
        return "total_points"
    if "spread" in blob or "handicap" in blob:
        return "spread"
    return "other"


# ─────────────────────────────────────────────────────────────────────
# Cohort-aware weight adjustment (used by Feature 9 once threshold met)
# ─────────────────────────────────────────────────────────────────────
def compute_weighted_recommendation_bias(
    *,
    cohort_stats: dict,
) -> dict:
    """Convert a cohort's settled history into a recommendation bias.

    ``cohort_stats`` is a precomputed summary like::

        {
          "sample_size":               48,
          "aggressive_line_miss_rate": 0.35,
          "push_saved_rate":           0.12,
          "near_miss_rate":            0.15,
          "safe_line_hit_rate":        0.30,
          "average_line_adjustment":   0.5,
        }

    Returns::

        {
          "active":             True/False,        # depends on threshold
          "line_bias":          float (+/-),       # signed shift to apply
          "model_weight":       float,
          "feedback_weight":    float,
          "recommendation":     "PROTECTED" | "VALUE" | "NEUTRAL",
          "summary_es":         str,
        }

    Always returns a dict — never raises. Caller decides whether to
    apply the bias (during the recommender's line selection step).
    """
    out = {
        "active":          False,
        "line_bias":       0.0,
        "model_weight":    model_weight(),
        "feedback_weight": feedback_weight(),
        "recommendation":  "NEUTRAL",
        "summary_es":      "Sin datos suficientes para sesgar línea.",
    }
    if not isinstance(cohort_stats, dict):
        return out

    n = int(cohort_stats.get("sample_size") or 0)
    if n < min_samples_threshold():
        out["summary_es"] = (
            f"Modo observe-only ({n}/{min_samples_threshold()} muestras). "
            "Aprendizaje todavía no afecta recomendaciones."
        )
        return out

    out["active"] = True
    agg_miss   = float(cohort_stats.get("aggressive_line_miss_rate") or 0.0)
    push_saved = float(cohort_stats.get("push_saved_rate") or 0.0)
    near_miss  = float(cohort_stats.get("near_miss_rate") or 0.0)
    safe_hit   = float(cohort_stats.get("safe_line_hit_rate") or 0.0)
    avg_adj    = float(cohort_stats.get("average_line_adjustment") or 0.0)

    # If aggressive misses + pushes saved dominate, prefer protected line.
    aggressive_share = agg_miss + push_saved + (0.5 * near_miss)
    safe_share       = safe_hit

    fw = feedback_weight()
    if aggressive_share > safe_share + 0.10:
        out["recommendation"] = "PROTECTED"
        out["line_bias"] = round(max(0.0, avg_adj) * fw, 2)
        out["summary_es"] = (
            f"Sesgo protegido ({n} muestras, agresivo={aggressive_share:.0%} "
            f"vs seguro={safe_share:.0%}). Sugerencia: +{out['line_bias']:.1f} a la línea."
        )
    elif safe_share > aggressive_share + 0.10:
        out["recommendation"] = "VALUE"
        out["line_bias"] = round(-abs(avg_adj) * fw, 2)
        out["summary_es"] = (
            f"Sesgo valor ({n} muestras, seguro={safe_share:.0%} "
            f"vs agresivo={aggressive_share:.0%}). Sugerencia: {out['line_bias']:+.1f} a la línea."
        )
    else:
        out["recommendation"] = "NEUTRAL"
        out["summary_es"] = (
            f"Sin sesgo claro ({n} muestras). "
            f"agresivo={aggressive_share:.0%} ≈ seguro={safe_share:.0%}."
        )

    return out


__all__ = [
    "ENGINE_VERSION",
    "model_weight",
    "feedback_weight",
    "min_samples_threshold",
    # Classifications + reason codes
    "CLASS_EXACT_HIT",
    "CLASS_NEAR_MISS",
    "CLASS_PUSH_SAVED",
    "CLASS_AGGRESSIVE_LINE_MISS",
    "CLASS_SAFE_LINE_HIT",
    "CLASS_PROFILE_WRONG",
    "CLASS_UNDEFINED",
    "ALL_CLASSIFICATIONS",
    "RC_PUSH_SAVED_BY_LINE",
    "RC_LOST_BY_HALF_RUN",
    "RC_SAFE_LINE_SURVIVED",
    "RC_AGGRESSIVE_LINE_TOO_TIGHT",
    "RC_OVERWHELMING_PROJECTION_MISS",
    "RC_LINE_BIAS_AGGRESSIVE",
    "RC_LINE_BIAS_PROTECTED",
    "RC_USER_AGREED_WITH_ENGINE",
    "ALL_REASON_CODES",
    # Public functions
    "compute_line_distance",
    "is_user_more_protected",
    "classify",
    "build_summary",
    "build_learning_sample",
    "compute_weighted_recommendation_bias",
]
