"""F97.1 — NIVEL 3 Bloque 3 (§5-§6) · MLB Under Hard Rules
=========================================================

Reglas duras para picks de **Under** basadas en la distribución
final post-NIVEL 3 (Bloque 2). Este módulo NO tiene side effects: es
puro, fail-soft y testeable.

Decisión del usuario (mapeo de thresholds → acciones):

  * ``over_risk >= 0.55``                  → **BLOCK**
  * ``0.48 <= over_risk < 0.55``           → **AVOID**
  * ``0.42 <= over_risk < 0.48``           → **WARN**
  * ``tail == "HIGH"`` y ``line <= 9.5``   → **AVOID**
  * ``tail == "EXTREME"``                  → **BLOCK**

  → Gana la acción más severa cuando varias reglas se disparan.

Impacto en scoring + feed (aplicado por el orchestrator en M5.8.4):

  * **WARN**  → `score -= 3`, warning visible.
  * **AVOID** → `score -= 10`, no puede salir como MÁXIMA, flag
                ``under_recommendation_degraded = True``.
  * **BLOCK** → ``is_blocked = True``, excluido del feed principal,
                preservado en categoría ``"debug"``.

Output canónico (siempre, NEVER raises)::

    {
      "applicable":           bool,   # False cuando no aplica (ML/RL/OVER pick)
      "action":               "NONE" | "WARN" | "AVOID" | "BLOCK",
      "severity":             int,    # 0/1/2/3
      "score_delta":          int,    # 0 / -3 / -10 / 0 (BLOCK no usa delta)
      "is_blocked":           bool,
      "block_max_pick":       bool,   # AVOID/BLOCK → True
      "exclude_from_main_feed": bool, # BLOCK → True
      "category":             None | "debug",
      "over_risk":            float | None,
      "line_used":            float | None,
      "tail_bucket":          str | None,
      "triggered_rules":      list[str],
      "reason_codes":         list[str],
      "signals":              list[str],
    }

Reason codes:

  * ``UNDER_RULES_NOT_APPLICABLE``
  * ``UNDER_RULES_NO_OVER_RISK_AVAILABLE``
  * ``UNDER_RULES_WARN_OVER_RISK``
  * ``UNDER_RULES_AVOID_OVER_RISK``
  * ``UNDER_RULES_BLOCK_OVER_RISK``
  * ``UNDER_RULES_AVOID_TAIL_HIGH_LOW_LINE``
  * ``UNDER_RULES_BLOCK_TAIL_EXTREME``
  * ``UNDER_RECOMMENDATION_DEGRADED``
"""
from __future__ import annotations

import math
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────
# Public constants
# ─────────────────────────────────────────────────────────────────────
ACTION_NONE  = "NONE"
ACTION_WARN  = "WARN"
ACTION_AVOID = "AVOID"
ACTION_BLOCK = "BLOCK"

SEVERITY_RANK = {
    ACTION_NONE:  0,
    ACTION_WARN:  1,
    ACTION_AVOID: 2,
    ACTION_BLOCK: 3,
}

# Thresholds for over_risk (probabilities from final_over_probabilities).
OVER_RISK_WARN_MIN  = 0.42
OVER_RISK_AVOID_MIN = 0.48
OVER_RISK_BLOCK_MIN = 0.55

# Tail buckets recognised (case-insensitive).
TAIL_BUCKET_HIGH    = "HIGH"
TAIL_BUCKET_EXTREME = "EXTREME"
TAIL_LOW_LINE_AVOID = 9.5

# Score deltas.
SCORE_DELTA_WARN  = -3
SCORE_DELTA_AVOID = -10
SCORE_DELTA_BLOCK = 0   # BLOCK excludes from feed; we don't double-penalise score.

# Reason codes.
RC_UNDER_RULES_NOT_APPLICABLE          = "UNDER_RULES_NOT_APPLICABLE"
RC_UNDER_RULES_NO_OVER_RISK_AVAILABLE  = "UNDER_RULES_NO_OVER_RISK_AVAILABLE"
RC_UNDER_RULES_WARN_OVER_RISK          = "UNDER_RULES_WARN_OVER_RISK"
RC_UNDER_RULES_AVOID_OVER_RISK         = "UNDER_RULES_AVOID_OVER_RISK"
RC_UNDER_RULES_BLOCK_OVER_RISK         = "UNDER_RULES_BLOCK_OVER_RISK"
RC_UNDER_RULES_AVOID_TAIL_HIGH_LOW     = "UNDER_RULES_AVOID_TAIL_HIGH_LOW_LINE"
RC_UNDER_RULES_BLOCK_TAIL_EXTREME      = "UNDER_RULES_BLOCK_TAIL_EXTREME"
RC_UNDER_RECOMMENDATION_DEGRADED       = "UNDER_RECOMMENDATION_DEGRADED"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _resolve_over_risk(
    final_over_probabilities: Any, line: Optional[float],
) -> Optional[float]:
    """Extract ``over_<line>`` probability from final_over_probabilities.

    Accepts either flat keyed dict (``{"over_8.5": 0.5, ...}``) or the
    ``probabilities`` block from ``expected_runs_distribution`` (which
    contains both ``over_X`` and ``under_X``). Returns None on any
    failure.
    """
    if not isinstance(final_over_probabilities, dict):
        return None
    if line is None:
        return None
    line_f = _safe_float(line)
    if line_f is None:
        return None
    # Several key formats may exist (over_8.5 vs over_8_5).
    candidates = [
        f"over_{line_f}",
        f"over_{line_f:.1f}",
        f"over_{int(line_f)}_{int(round((line_f - int(line_f)) * 10))}",
    ]
    # Also accept plain "OVER_X.Y" / "Over_X.Y" / int variants.
    if line_f == int(line_f):
        candidates.extend([f"over_{int(line_f)}", f"over_{int(line_f)}.0"])
    seen: set[str] = set()
    for k in candidates:
        if k in seen:
            continue
        seen.add(k)
        v = final_over_probabilities.get(k)
        f = _safe_float(v)
        if f is not None:
            return f
    return None


def _normalise_bucket(bucket: Any) -> Optional[str]:
    if not isinstance(bucket, str):
        return None
    b = bucket.strip().upper()
    return b if b else None


def _is_under_pick(pick_side: Any, market: Any) -> bool:
    """Heuristic: only Under picks are subject to these hard rules.

    Accepts these inputs:
      - ``pick_side`` ∈ {"UNDER", "Under", "under"} → True.
      - ``market`` ∈ {"under_X.Y", "UNDER_8.5", "UNDER"} → True.
    """
    if isinstance(pick_side, str):
        s = pick_side.strip().upper()
        if s == "UNDER":
            return True
        if s == "OVER":
            return False
    if isinstance(market, str):
        m = market.strip().lower()
        if m.startswith("under") or m == "u":
            return True
    return False


# ─────────────────────────────────────────────────────────────────────
# Default / empty result builder
# ─────────────────────────────────────────────────────────────────────
def _empty_result(reason_code: Optional[str] = None,
                  *, applicable: bool = False) -> dict:
    return {
        "applicable":            applicable,
        "action":                ACTION_NONE,
        "severity":              SEVERITY_RANK[ACTION_NONE],
        "score_delta":           0,
        "is_blocked":            False,
        "block_max_pick":        False,
        "exclude_from_main_feed": False,
        "category":              None,
        "over_risk":             None,
        "line_used":             None,
        "tail_bucket":           None,
        "triggered_rules":       [],
        "reason_codes":          [reason_code] if reason_code else [],
        "signals":               [],
    }


# ─────────────────────────────────────────────────────────────────────
# Public — evaluate_under_hard_rules
# ─────────────────────────────────────────────────────────────────────
def evaluate_under_hard_rules(
    *,
    final_over_probabilities: Any = None,
    line: Any = None,
    tail_bucket: Any = None,
    pick_side: Any = None,
    market: Any = None,
) -> dict:
    """Evaluate the WARN / AVOID / BLOCK rules for an Under pick.

    All parameters are keyword-only for safety; this function NEVER
    raises.
    """
    # 1) Applicability — only Under picks are eligible.
    if not _is_under_pick(pick_side, market):
        return _empty_result(RC_UNDER_RULES_NOT_APPLICABLE, applicable=False)

    line_f = _safe_float(line)
    over_risk = _resolve_over_risk(final_over_probabilities, line_f)
    bucket = _normalise_bucket(tail_bucket)

    if over_risk is None and bucket not in (TAIL_BUCKET_HIGH, TAIL_BUCKET_EXTREME):
        # Nothing to evaluate — applicable but no data.
        out = _empty_result(RC_UNDER_RULES_NO_OVER_RISK_AVAILABLE, applicable=True)
        out["line_used"]   = line_f
        out["tail_bucket"] = bucket
        return out

    triggered: list[str] = []
    reason_codes: list[str] = []
    signals: list[str] = []

    candidate_actions: list[str] = []

    # Rule 1 — over_risk threshold buckets.
    if over_risk is not None:
        if over_risk >= OVER_RISK_BLOCK_MIN:
            candidate_actions.append(ACTION_BLOCK)
            triggered.append("OVER_RISK_BLOCK")
            reason_codes.append(RC_UNDER_RULES_BLOCK_OVER_RISK)
            signals.append(
                f"over_risk={over_risk:.3f} ≥ {OVER_RISK_BLOCK_MIN:.2f} → BLOCK"
            )
        elif over_risk >= OVER_RISK_AVOID_MIN:
            candidate_actions.append(ACTION_AVOID)
            triggered.append("OVER_RISK_AVOID")
            reason_codes.append(RC_UNDER_RULES_AVOID_OVER_RISK)
            signals.append(
                f"over_risk={over_risk:.3f} ∈ "
                f"[{OVER_RISK_AVOID_MIN:.2f}, {OVER_RISK_BLOCK_MIN:.2f}) → AVOID"
            )
        elif over_risk >= OVER_RISK_WARN_MIN:
            candidate_actions.append(ACTION_WARN)
            triggered.append("OVER_RISK_WARN")
            reason_codes.append(RC_UNDER_RULES_WARN_OVER_RISK)
            signals.append(
                f"over_risk={over_risk:.3f} ∈ "
                f"[{OVER_RISK_WARN_MIN:.2f}, {OVER_RISK_AVOID_MIN:.2f}) → WARN"
            )

    # Rule 2 — tail == EXTREME → BLOCK (always).
    if bucket == TAIL_BUCKET_EXTREME:
        candidate_actions.append(ACTION_BLOCK)
        triggered.append("TAIL_EXTREME_BLOCK")
        reason_codes.append(RC_UNDER_RULES_BLOCK_TAIL_EXTREME)
        signals.append("tail_bucket=EXTREME → BLOCK")

    # Rule 3 — tail == HIGH and line <= 9.5 → AVOID.
    if bucket == TAIL_BUCKET_HIGH and line_f is not None and line_f <= TAIL_LOW_LINE_AVOID:
        candidate_actions.append(ACTION_AVOID)
        triggered.append("TAIL_HIGH_LOW_LINE_AVOID")
        reason_codes.append(RC_UNDER_RULES_AVOID_TAIL_HIGH_LOW)
        signals.append(
            f"tail_bucket=HIGH and line={line_f:.1f} ≤ {TAIL_LOW_LINE_AVOID:.1f} → AVOID"
        )

    # If no rule triggered → applicable but NONE.
    if not candidate_actions:
        out = _empty_result(applicable=True)
        out["over_risk"]   = over_risk
        out["line_used"]   = line_f
        out["tail_bucket"] = bucket
        return out

    # Pick the most severe action.
    candidate_actions.sort(key=lambda a: SEVERITY_RANK[a], reverse=True)
    action = candidate_actions[0]

    is_blocked      = (action == ACTION_BLOCK)
    block_max_pick  = (action in (ACTION_AVOID, ACTION_BLOCK))
    exclude_feed    = is_blocked
    category        = "debug" if is_blocked else None

    score_delta_map = {
        ACTION_WARN:  SCORE_DELTA_WARN,
        ACTION_AVOID: SCORE_DELTA_AVOID,
        ACTION_BLOCK: SCORE_DELTA_BLOCK,
    }
    score_delta = score_delta_map.get(action, 0)

    # AVOID + BLOCK both degrade the recommendation.
    if action in (ACTION_AVOID, ACTION_BLOCK):
        reason_codes.append(RC_UNDER_RECOMMENDATION_DEGRADED)

    return {
        "applicable":             True,
        "action":                 action,
        "severity":               SEVERITY_RANK[action],
        "score_delta":            score_delta,
        "is_blocked":             is_blocked,
        "block_max_pick":         block_max_pick,
        "exclude_from_main_feed": exclude_feed,
        "category":               category,
        "over_risk":              over_risk,
        "line_used":              line_f,
        "tail_bucket":            bucket,
        "triggered_rules":        triggered,
        "reason_codes":           list(dict.fromkeys(reason_codes)),  # dedupe preserving order
        "signals":                signals,
    }


__all__ = [
    "evaluate_under_hard_rules",
    "ACTION_NONE",
    "ACTION_WARN",
    "ACTION_AVOID",
    "ACTION_BLOCK",
    "SEVERITY_RANK",
    "OVER_RISK_WARN_MIN",
    "OVER_RISK_AVOID_MIN",
    "OVER_RISK_BLOCK_MIN",
    "TAIL_BUCKET_HIGH",
    "TAIL_BUCKET_EXTREME",
    "TAIL_LOW_LINE_AVOID",
    "SCORE_DELTA_WARN",
    "SCORE_DELTA_AVOID",
    "SCORE_DELTA_BLOCK",
    "RC_UNDER_RULES_NOT_APPLICABLE",
    "RC_UNDER_RULES_NO_OVER_RISK_AVAILABLE",
    "RC_UNDER_RULES_WARN_OVER_RISK",
    "RC_UNDER_RULES_AVOID_OVER_RISK",
    "RC_UNDER_RULES_BLOCK_OVER_RISK",
    "RC_UNDER_RULES_AVOID_TAIL_HIGH_LOW",
    "RC_UNDER_RULES_BLOCK_TAIL_EXTREME",
    "RC_UNDER_RECOMMENDATION_DEGRADED",
]
