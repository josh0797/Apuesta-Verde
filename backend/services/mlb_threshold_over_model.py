"""
NIVEL 3 — Bloque 2 §3 · Threshold Over Model (pure, heuristic fallback).

Predice DIRECTAMENTE las probabilidades Over por umbral (7.5 .. 14.5)
sin derivarlas únicamente desde lambda. Esta primera versión es un
**fallback heurístico determinístico** (no entrenado) que respeta el
contrato del spec — el modelo real (LR/GBM/RF) se entrenará en una
entrega futura cuando exista dataset histórico point-in-time.

Confidence se mantiene ≤ 60 para que el blender (§4) lo pondere bajo
hasta que se reemplace por un modelo entrenado.

Entry-point
-----------
    predict_threshold_probabilities(features: dict) -> dict

Output (per spec):
    {
      "model_version":           "mlb-threshold-over-v0-heuristic",
      "threshold_probabilities": {"over_7_5": …, …, "over_14_5": …},
      "under_probabilities":     {"under_7_5": …, …, "under_14_5": …},
      "confidence":              float 0..100,  # ≤ 60 for heuristic
      "features_used":           [str],
      "missing_fields":          [str],
      "debug":                   {...},
    }
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

SUPPORTED_LINES = (7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5)
MODEL_VERSION = "mlb-threshold-over-v0-heuristic"

# Maximum confidence a heuristic fallback can report (per spec — leaves
# headroom for a future trained model to clearly dominate).
MAX_HEURISTIC_CONFIDENCE = 60.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        f = float(value)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def _logistic(x: float) -> float:
    """Numerically stable logistic sigmoid."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _key_o(line: float) -> str:
    return f"over_{str(line).replace('.', '_')}"


def _key_u(line: float) -> str:
    return f"under_{str(line).replace('.', '_')}"


def _heuristic_logit(line: float, mu: float, vol_boost: float) -> float:
    """Map (line, mu, vol_boost) → logit of P(total > line).

    Calibrated so that:
      * mu == line  → logit ≈ 0 (P ≈ 0.5).
      * Slope per +1 carrera ≈ 0.55 (sigmoidal width).
      * `vol_boost ∈ [0, 1]` shifts the curve toward higher tail mass
        in high-variance regimes (max +0.6 logit at extreme tail).
    """
    delta = mu - line
    # Base logit.
    z = 0.55 * delta
    # Volatility-aware additive bump (favours higher tails when boost > 0).
    if line >= 10.5:
        z += 0.60 * vol_boost
    elif line >= 8.5:
        z += 0.30 * vol_boost
    else:
        # Lower lines barely affected by volatility (the floor mass is
        # high regardless).
        z += 0.10 * vol_boost
    return z


def predict_threshold_probabilities(
    features: Dict[str, Any],
) -> Dict[str, Any]:
    """Public entry-point. Always returns a dict, never raises."""
    try:
        if not isinstance(features, dict):
            return _neutral_payload(["INVALID_FEATURES_TYPE"])

        # ── 1. Core inputs ───────────────────────────────────────────
        mu = _safe_float(features.get("baseline_expected_runs"), default=0.0)
        if mu <= 0:
            # Fallback to industry average.
            mu = 8.5
        # market_total is captured for downstream reference but not
        # currently used by the heuristic logits (the trained model
        # in v1 will consume it as a calibration anchor).
        _ = _safe_float(features.get("market_total"), default=mu)

        # ── 2. Volatility composite ──────────────────────────────────
        # Aggregate the available context signals into vol_boost ∈ [0,1].
        sig_scores = []
        used: List[str] = []
        missing: List[str] = []
        for k in (
            "starter_volatility_home", "starter_volatility_away",
            "first_inning_collapse_home", "first_inning_collapse_away",
            "lineup_explosiveness_home", "lineup_explosiveness_away",
            "bullpen_stress_home", "bullpen_stress_away",
            "domino_risk_home", "domino_risk_away",
        ):
            v = features.get(k)
            if v is None:
                missing.append(k)
                continue
            try:
                v_f = float(v)
                if v_f == v_f:
                    sig_scores.append(v_f)
                    used.append(k)
            except (TypeError, ValueError):
                missing.append(k)

        # Recent offense buckets (categorical → numeric).
        for k in ("recent_offense_home", "recent_offense_away"):
            v = features.get(k)
            if v is None:
                missing.append(k)
                continue
            bucket = v.get("bucket") if isinstance(v, dict) else v
            mapping = {"COLD": 0.0, "NEUTRAL": 30.0, "HOT": 70.0, "EXPLOSIVE": 95.0}
            if bucket in mapping:
                sig_scores.append(mapping[bucket])
                used.append(k)
            else:
                missing.append(k)

        vol_score = sum(sig_scores) / len(sig_scores) if sig_scores else 30.0
        # vol_boost: 0 at vol_score=30 (neutral), 1 at vol_score=85+.
        vol_boost = max(0.0, min(1.0, (vol_score - 30.0) / 55.0))

        # ── 3. Lineup quality / park / weather (optional bumps) ──────
        iso_h = _safe_float(features.get("home_iso_l7"))
        iso_a = _safe_float(features.get("away_iso_l7"))
        if iso_h > 0 or iso_a > 0:
            used.append("iso_l7")
        if max(iso_h, iso_a) >= 0.180:
            vol_boost = min(1.0, vol_boost + 0.10)
        barrel_h = _safe_float(features.get("home_barrel_l7"))
        barrel_a = _safe_float(features.get("away_barrel_l7"))
        if max(barrel_h, barrel_a) >= 0.10:
            vol_boost = min(1.0, vol_boost + 0.10)
            used.append("barrel_l7")
        hh_h = _safe_float(features.get("home_hardhit_l7"))
        hh_a = _safe_float(features.get("away_hardhit_l7"))
        if max(hh_h, hh_a) >= 0.45:
            vol_boost = min(1.0, vol_boost + 0.05)
            used.append("hardhit_l7")

        # Starter HR9 / BB%.
        for s in ("starter_home_hr9", "starter_away_hr9"):
            v = _safe_float(features.get(s))
            if v >= 1.5:
                vol_boost = min(1.0, vol_boost + 0.08)
                used.append(s)
        for s in ("starter_home_bb_pct", "starter_away_bb_pct"):
            v = _safe_float(features.get(s))
            if v >= 0.10:
                vol_boost = min(1.0, vol_boost + 0.05)
                used.append(s)

        # Park / weather.
        pf = _safe_float(features.get("park_factor"), default=1.0)
        if pf >= 1.05:
            mu = mu * pf
            used.append("park_factor")
        wx = _safe_float(features.get("weather_run_factor"), default=1.0)
        if wx >= 1.05:
            mu = mu * wx
            used.append("weather_run_factor")

        # Always count baseline + market_total as used.
        used = ["baseline_expected_runs", "market_total"] + used

        # ── 4. Build over/under probabilities by line ────────────────
        over_probs: Dict[str, float] = {}
        under_probs: Dict[str, float] = {}
        prev_over = 1.0
        for line in SUPPORTED_LINES:
            z = _heuristic_logit(line, mu, vol_boost)
            p_over = _logistic(z)
            # Enforce monotone decreasing over higher lines.
            p_over = min(p_over, prev_over - 1e-4) if line > SUPPORTED_LINES[0] else p_over
            p_over = max(0.0, min(1.0, p_over))
            over_probs[_key_o(line)] = round(p_over, 4)
            under_probs[_key_u(line)] = round(1.0 - p_over, 4)
            prev_over = p_over

        # ── 5. Confidence (heuristic ceiling) ────────────────────────
        # Higher when we have more features used.
        n_used = len([u for u in used if u not in ("baseline_expected_runs", "market_total")])
        n_total_expected = 12  # rough denominator of optional features
        coverage = min(1.0, n_used / n_total_expected)
        confidence = MAX_HEURISTIC_CONFIDENCE * (0.60 + 0.40 * coverage)
        # Penalize missing core signals.
        if len(missing) > 6:
            confidence *= 0.80
        confidence = round(max(0.0, min(MAX_HEURISTIC_CONFIDENCE, confidence)), 1)

        return {
            "model_version":           MODEL_VERSION,
            "threshold_probabilities": over_probs,
            "under_probabilities":     under_probs,
            "confidence":              confidence,
            "features_used":           sorted(set(used)),
            "missing_fields":          sorted(set(missing)),
            "debug": {
                "mu_effective": round(mu, 3),
                "vol_boost":    round(vol_boost, 3),
                "vol_score":    round(vol_score, 1),
            },
        }
    except Exception as exc:  # noqa: BLE001
        return _neutral_payload([f"EXCEPTION:{type(exc).__name__}"])


def _neutral_payload(missing: List[str]) -> Dict[str, Any]:
    """Returned when the input is malformed."""
    over_probs = {}
    under_probs = {}
    for line in SUPPORTED_LINES:
        p = 0.5
        over_probs[_key_o(line)] = p
        under_probs[_key_u(line)] = 1.0 - p
    return {
        "model_version":           MODEL_VERSION,
        "threshold_probabilities": over_probs,
        "under_probabilities":     under_probs,
        "confidence":              0.0,
        "features_used":           [],
        "missing_fields":          missing,
        "debug":                   {},
    }


__all__ = [
    "predict_threshold_probabilities",
    "SUPPORTED_LINES",
    "MODEL_VERSION",
    "MAX_HEURISTIC_CONFIDENCE",
]
