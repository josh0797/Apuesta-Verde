"""
NIVEL 3 — Bloque 2 §4 · Distribution × Threshold-Model Blender (pure).

Combina las probabilidades del **mixer/tail calibration** con las del
**modelo por umbral** (§3) usando confidence-weighted blending.

Entry-point
-----------
    combine_distribution_and_threshold_model(
        distribution_probs: dict,
        threshold_model_probs: dict,
        context: dict,
    ) -> dict

Rules (per spec):
    if confidence >= 70:
        final = 0.55 * threshold + 0.45 * distribution
    elif confidence >= 45:
        final = 0.40 * threshold + 0.60 * distribution
    else:
        final = distribution  # threshold ignored

Extras:
    * High variance context → boost threshold weight (+0.05).
    * Partial-data context  → cut threshold weight in half.
    * Divergence flag when |Δ| > 0.10 in any tail line (10.5+).
"""

from __future__ import annotations

from typing import Any, Dict, List

SUPPORTED_LINES = (7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5)
TAIL_LINES = (10.5, 11.5, 12.5, 13.5, 14.5)

CONFIDENCE_HIGH = 70.0
CONFIDENCE_MED  = 45.0
DIVERGENCE_THRESHOLD = 0.10


def _key_o(line: float) -> str:
    return f"over_{str(line).replace('.', '_')}"


def _key_u(line: float) -> str:
    return f"under_{str(line).replace('.', '_')}"


def _safe_get(d: Any, k: str) -> float:
    if not isinstance(d, dict):
        return 0.5
    v = d.get(k)
    try:
        f = float(v)
        if f != f:
            return 0.5
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return 0.5


def combine_distribution_and_threshold_model(
    distribution_probs: Dict[str, Any],
    threshold_model_probs: Dict[str, Any],
    context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Public entry-point. Always returns a dict, never raises."""
    try:
        context = context or {}
        # Threshold model confidence.
        confidence = 0.0
        try:
            confidence = float(threshold_model_probs.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(100.0, confidence))

        # Decide base blend weights from confidence.
        reasons: List[str] = []
        if confidence >= CONFIDENCE_HIGH:
            w_threshold = 0.55
            reasons.append("THRESHOLD_MODEL_USED")
        elif confidence >= CONFIDENCE_MED:
            w_threshold = 0.40
            reasons.append("THRESHOLD_MODEL_USED")
        else:
            w_threshold = 0.0
            reasons.append("THRESHOLD_MODEL_LOW_CONFIDENCE")

        # High-variance bump.
        tail_risk = (context.get("tail_risk_bucket")
                      or (context.get("tail_risk") or {}).get("bucket"))
        if tail_risk in ("HIGH", "EXTREME") and w_threshold > 0:
            w_threshold = min(0.65, w_threshold + 0.05)

        # Partial-data cut.
        if context.get("partial_data") and w_threshold > 0:
            w_threshold = w_threshold * 0.5

        w_dist = 1.0 - w_threshold

        # ── Extract input probabilities maps ─────────────────────────
        dist_probs = distribution_probs.get("probabilities") if isinstance(distribution_probs, dict) else {}
        if not isinstance(dist_probs, dict):
            dist_probs = {}
        thresh_over = threshold_model_probs.get("threshold_probabilities") if isinstance(threshold_model_probs, dict) else {}
        thresh_under = threshold_model_probs.get("under_probabilities") if isinstance(threshold_model_probs, dict) else {}
        if not isinstance(thresh_over, dict):
            thresh_over = {}
        if not isinstance(thresh_under, dict):
            thresh_under = {}

        # ── Blend per line ──────────────────────────────────────────
        final_over: Dict[str, float] = {}
        final_under: Dict[str, float] = {}
        sources_dist: Dict[str, float] = {}
        sources_thresh: Dict[str, float] = {}
        sources_final: Dict[str, float] = {}
        divergence_flags: List[str] = []

        for line in SUPPORTED_LINES:
            kO = _key_o(line)
            kU = _key_u(line)
            p_dist  = _safe_get(dist_probs,  kO)
            p_thresh = _safe_get(thresh_over, kO)

            if w_threshold <= 0:
                p_final = p_dist
            else:
                p_final = w_threshold * p_thresh + w_dist * p_dist

            p_final = max(0.0, min(1.0, p_final))
            final_over[kO]  = round(p_final, 4)
            final_under[kU] = round(1.0 - p_final, 4)

            sources_dist[kO]  = round(p_dist, 4)
            sources_thresh[kO] = round(p_thresh, 4)
            sources_final[kO] = round(p_final, 4)

            # Divergence check (only on tail lines per spec).
            if line in TAIL_LINES and abs(p_dist - p_thresh) > DIVERGENCE_THRESHOLD:
                divergence_flags.append(f"DIVERGE_{kO}")

        if divergence_flags:
            reasons.append("DISTRIBUTION_THRESHOLD_DIVERGENCE")
        if w_threshold > 0:
            reasons.append("FINAL_PROBABILITY_BLEND_APPLIED")

        return {
            "final_over_probabilities":  final_over,
            "final_under_probabilities": final_under,
            "probability_sources": {
                "distribution":    sources_dist,
                "threshold_model": sources_thresh,
                "final":           sources_final,
            },
            "blend_weights": {
                "distribution":    round(w_dist, 3),
                "threshold_model": round(w_threshold, 3),
            },
            "threshold_confidence": confidence,
            "divergence_flags":    divergence_flags,
            "reason_codes":        reasons,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "final_over_probabilities":  {},
            "final_under_probabilities": {},
            "probability_sources":       {},
            "blend_weights":             {"distribution": 1.0, "threshold_model": 0.0},
            "threshold_confidence":      0.0,
            "divergence_flags":          [],
            "reason_codes":              [f"EXCEPTION:{type(exc).__name__}"],
        }


__all__ = [
    "combine_distribution_and_threshold_model",
    "SUPPORTED_LINES",
    "TAIL_LINES",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MED",
    "DIVERGENCE_THRESHOLD",
]
