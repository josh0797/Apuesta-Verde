"""
NIVEL 3 — Bloque 2 §2 · Tail Calibration (pure module).

Objetivo (spec):
Evitar que el modelo subestime eventos de 12, 14, 16, 18+ carreras.
Ajustar:
  - p90, p95, p99
  - P(Over 10.5), P(Over 11.5), P(Over 12.5), P(Over 13.5), P(Over 14.5)

**No inflar todo de forma ciega.** Solo aumentar cuando hay señales
reales de alta varianza. La calibración **conserva ∑probabilidades=1**
redistribuyendo masa desde la zona central hacia la cola alta.

Entry-point
-----------
    calibrate_tail_probabilities(distribution, context) -> dict

Inputs:
  distribution : output del mixer (mlb_run_distribution_mixer)
                 con keys probabilities (dict over_/under_) y
                 percentiles (p10..p99).
  context      : mismo formato del mixer (señales D11/D12 + bullpen,
                 starter HR/BB, lineup ISO/Barrel/HardHit, park,
                 weather).

Output (extiende el `distribution` recibido):
    {
      ...distribution echoed...,
      "tail_calibration_applied": bool,
      "tail_multiplier":          float,
      "tail_shift_runs":          float,
      "tail_risk_bucket":         "LOW|MEDIUM|HIGH|EXTREME",
      "before": {
          "probabilities": {...},
          "percentiles":   {...},
      },
      "after": {
          "probabilities": {...},
          "percentiles":   {...},
      },
      "reason_codes": [...],
      "drivers":      [...],
    }

Design rules
------------
* Pure: no I/O, deterministic, fail-soft (never raises).
* Conserves: ∑P_over+P_under (per .5 line) = 1 (within float epsilon).
* Conserves: ∑P_under (line) → cdf monotone. We don't re-derive PMF,
  we operate directly on the over/under-by-line probability table by
  bumping the tail (P_over above threshold) and removing equivalent
  mass from the body (P_under near the mean).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_LINES = (6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5)

BUCKET_LOW     = "LOW"
BUCKET_MEDIUM  = "MEDIUM"
BUCKET_HIGH    = "HIGH"
BUCKET_EXTREME = "EXTREME"

# Multiplier ranges per spec.
MULT_LOW     = 1.00
MULT_MEDIUM  = (1.10, 1.20)
MULT_HIGH    = (1.25, 1.45)
MULT_EXTREME = (1.50, 1.90)

# Lines we recalibrate (per spec — the upper tail).
TAIL_LINES = (10.5, 11.5, 12.5, 13.5, 14.5)
# Body lines we draw mass from when redistributing.
BODY_LINES = (6.5, 7.5, 8.5, 9.5)


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


def _peak(block: Any, *keys: str) -> Optional[float]:
    if not isinstance(block, dict):
        return None
    out: Optional[float] = None
    for side in ("home", "away"):
        sb = block.get(side)
        if not isinstance(sb, dict):
            continue
        cur: Any = sb
        for k in keys:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is not None:
            try:
                v = float(cur)
                if v == v:  # not NaN
                    out = v if out is None else max(out, v)
            except (TypeError, ValueError):
                pass
    return out


def _count_risk_signals(context: Dict[str, Any]) -> Tuple[int, List[str]]:
    """Count critical risk signals + return drivers.

    Per spec critical signals:
      * starter volatility >= 65
      * first inning collapse >= 65
      * lineup explosiveness >= 70
      * recent offense HOT/EXPLOSIVE (either side)
      * bullpen stress >= 65
      * domino risk >= 65
      * both bullpens fatigued (each side bullpen_fatigue >= 0.55)
      * pitcher HR/BB high (hr9 >= 1.5 OR bb_pct >= 0.10)
      * lineup top-5 high ISO/Barrel/HardHit
      * park factor >= 1.05 (hitter-friendly)
      * weather hitter-friendly (runs_multiplier >= 1.05)
    """
    drivers: List[str] = []

    def _add(d: str):
        if d not in drivers:
            drivers.append(d)

    sv = _peak(context.get("starter_volatility"), "starter_volatility_score")
    if sv is None:
        sv_h = _safe_float((context.get("starter_volatility_home") or {}).get("starter_volatility_score") if isinstance(context.get("starter_volatility_home"), dict) else None)
        sv_a = _safe_float((context.get("starter_volatility_away") or {}).get("starter_volatility_score") if isinstance(context.get("starter_volatility_away"), dict) else None)
        sv = max(sv_h, sv_a) if (sv_h or sv_a) else None
    if sv is not None and sv >= 65:
        _add("HIGH_STARTER_VOLATILITY")

    fi = _peak(context.get("first_inning_collapse"), "first_inning_collapse_score")
    if fi is not None and fi >= 65:
        _add("HIGH_FIRST_INNING_COLLAPSE")

    le = _peak(context.get("lineup_explosiveness"), "lineup_explosiveness_score")
    if le is not None and le >= 70:
        _add("EXPLOSIVE_LINEUP")

    bs = _peak(context.get("bullpen_stress"), "bullpen_stress_score")
    if bs is not None and bs >= 65:
        _add("BULLPEN_STRESS_HIGH")

    dr = _peak(context.get("domino_risk"), "domino_risk_score")
    if dr is not None and dr >= 65:
        _add("DOMINO_RISK_HIGH")

    ro_h = (context.get("recent_offense_home") or {}).get("bucket") if isinstance(context.get("recent_offense_home"), dict) else None
    ro_a = (context.get("recent_offense_away") or {}).get("bucket") if isinstance(context.get("recent_offense_away"), dict) else None
    if ro_h in ("HOT", "EXPLOSIVE") or ro_a in ("HOT", "EXPLOSIVE"):
        _add("RECENT_OFFENSE_HOT")

    # Both bullpens fatigued.
    bp = context.get("bullpen_usage") or {}
    fat_h = _safe_float((bp.get("home") or {}).get("bullpen_fatigue") if isinstance(bp.get("home"), dict) else None)
    fat_a = _safe_float((bp.get("away") or {}).get("bullpen_fatigue") if isinstance(bp.get("away"), dict) else None)
    if fat_h >= 0.55 and fat_a >= 0.55:
        _add("BOTH_BULLPENS_FATIGUED")

    # Pitcher control problems.
    si = context.get("starter_info") or {}
    for side in ("home", "away"):
        sb = si.get(side) or {}
        if not isinstance(sb, dict):
            continue
        hr9 = _safe_float(sb.get("hr9"))
        bb_pct = _safe_float(sb.get("bb_pct"))
        if hr9 >= 1.5 or bb_pct >= 0.10:
            _add("PITCHER_CONTROL_RISK")
            break

    # Lineup quality (top-5 ISO/Barrel/HardHit).
    lq = context.get("lineup_quality") or {}
    for side in ("home", "away"):
        sb = lq.get(side) or {}
        if not isinstance(sb, dict):
            continue
        iso = _safe_float(sb.get("top5_iso"))
        barrel = _safe_float(sb.get("top5_barrel_pct"))
        hardhit = _safe_float(sb.get("top5_hardhit_pct"))
        if iso >= 0.200 or barrel >= 0.10 or hardhit >= 0.45:
            _add("ELITE_LINEUP_TOP5")
            break

    # Park factor.
    pf = context.get("park_factor") or {}
    if isinstance(pf, dict):
        mult = _safe_float(pf.get("dynamic") or pf.get("park_runs_mult") or pf.get("runFactor"), default=1.0)
    else:
        mult = _safe_float(pf, default=1.0)
    if mult >= 1.05:
        _add("HITTER_FRIENDLY_PARK")

    # Weather.
    wx = context.get("weather") or {}
    if isinstance(wx, dict):
        wxm = _safe_float(wx.get("runs_multiplier"), default=1.0)
        if wxm >= 1.05:
            _add("HITTER_FRIENDLY_WEATHER")

    return len(drivers), drivers


def _bucket_from_signals(n_signals: int) -> Tuple[str, float]:
    """Map signal count to (bucket, tail_multiplier)."""
    if n_signals == 0:
        return BUCKET_LOW, MULT_LOW
    if n_signals == 1:
        return BUCKET_MEDIUM, MULT_MEDIUM[0]
    if n_signals == 2:
        return BUCKET_MEDIUM, MULT_MEDIUM[1]
    if n_signals == 3:
        return BUCKET_HIGH, MULT_HIGH[0]
    if n_signals == 4:
        return BUCKET_HIGH, MULT_HIGH[1]
    if n_signals == 5:
        return BUCKET_EXTREME, MULT_EXTREME[0]
    # 6+
    return BUCKET_EXTREME, MULT_EXTREME[1]


def _key_o(line: float) -> str:
    return f"over_{str(line).replace('.', '_')}"


def _key_u(line: float) -> str:
    return f"under_{str(line).replace('.', '_')}"


def _apply_tail_calibration(
    probs: Dict[str, float],
    multiplier: float,
) -> Tuple[Dict[str, float], float]:
    """Multiply over-probabilities of TAIL_LINES by `multiplier`, then
    redistribute the **same total delta** from BODY_LINES so total
    mass is conserved per line (P_over + P_under = 1).

    Returns (new_probs, total_shifted_mass).
    """
    new_probs = dict(probs)

    if multiplier == 1.0:
        return new_probs, 0.0

    # Step 1: compute target tail bump (per-line delta).
    deltas_tail: Dict[float, float] = {}
    total_delta = 0.0
    for ln in TAIL_LINES:
        kO = _key_o(ln)
        if kO not in new_probs:
            continue
        p_over = new_probs[kO]
        target = min(1.0, p_over * multiplier)
        d = target - p_over
        if d > 0:
            deltas_tail[ln] = d
            total_delta += d

    if total_delta <= 0:
        return new_probs, 0.0

    # Step 2: redistribute total_delta by SUBTRACTING from body lines'
    # under-probabilities proportionally. Equivalently: increase
    # P_over of body lines so their CDF moves up consistently.
    # For each body line we subtract a share of total_delta from P_under
    # (capped at p_under - 0.005 to avoid degenerate negatives).
    body_under = {}
    body_capacity = 0.0
    for ln in BODY_LINES:
        kU = _key_u(ln)
        if kU in new_probs:
            cap = max(0.0, new_probs[kU] - 0.005)
            body_under[ln] = (new_probs[kU], cap)
            body_capacity += cap
    if body_capacity <= 0:
        # No room — apply tail bump partially up to capacity.
        return new_probs, 0.0

    scale = min(1.0, body_capacity / total_delta) if total_delta > 0 else 0.0
    actual_shift = 0.0
    for ln in BODY_LINES:
        if ln not in body_under:
            continue
        _, cap = body_under[ln]
        share = (cap / body_capacity) * total_delta * scale
        kU = _key_u(ln)
        kO = _key_o(ln)
        new_probs[kU] = max(0.0, new_probs[kU] - share)
        new_probs[kO] = min(1.0, new_probs.get(kO, 1 - new_probs[kU]) + share)
        actual_shift += share

    # Step 3: apply the (possibly scaled-down) tail bumps.
    tail_scale = min(1.0, actual_shift / total_delta) if total_delta > 0 else 0.0
    for ln, d in deltas_tail.items():
        bump = d * tail_scale
        kO = _key_o(ln)
        kU = _key_u(ln)
        new_probs[kO] = min(1.0, new_probs[kO] + bump)
        new_probs[kU] = max(0.0, 1.0 - new_probs[kO])

    return new_probs, actual_shift


def _recalibrate_percentiles(
    percentiles: Dict[str, int],
    tail_shift_runs: float,
) -> Dict[str, int]:
    """Bump p90/p95/p99 by tail_shift_runs (integer rounded)."""
    out = dict(percentiles)
    for key in ("p90", "p95", "p99"):
        if key in out:
            try:
                out[key] = int(out[key]) + int(round(tail_shift_runs))
            except (TypeError, ValueError):
                pass
    return out


def calibrate_tail_probabilities(
    distribution: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Public entry-point. Always returns a dict, never raises."""
    try:
        if not isinstance(distribution, dict):
            return {
                "tail_calibration_applied": False,
                "tail_multiplier":          1.0,
                "tail_shift_runs":          0.0,
                "tail_risk_bucket":         BUCKET_LOW,
                "before":                   {},
                "after":                    {},
                "reason_codes":             ["INVALID_DISTRIBUTION_INPUT"],
                "drivers":                  [],
            }
        if not isinstance(context, dict):
            context = {}

        probs_before = dict(distribution.get("probabilities") or {})
        perc_before  = dict(distribution.get("percentiles") or {})

        n_signals, drivers = _count_risk_signals(context)
        bucket, multiplier = _bucket_from_signals(n_signals)

        reasons: List[str] = []
        # Apply tail bump if multiplier > 1.0.
        if multiplier <= 1.0 + 1e-6 or not probs_before:
            return {
                **distribution,
                "tail_calibration_applied": False,
                "tail_multiplier":          1.0,
                "tail_shift_runs":          0.0,
                "tail_risk_bucket":         bucket,
                "before":                   {"probabilities": probs_before,
                                              "percentiles":   perc_before},
                "after":                    {"probabilities": probs_before,
                                              "percentiles":   perc_before},
                "reason_codes":             reasons,
                "drivers":                  drivers,
            }

        probs_after, actual_shift = _apply_tail_calibration(probs_before, multiplier)

        # Estimate shift in runs from shifted mass (heuristic: 0.04
        # carreras por punto porcentual de masa desplazada).
        tail_shift_runs = round(actual_shift / 0.04 / 100.0, 2)
        # Cap.
        tail_shift_runs = max(0.0, min(3.0, tail_shift_runs))

        perc_after = _recalibrate_percentiles(perc_before, tail_shift_runs)

        reasons.append("TAIL_CALIBRATION_APPLIED")
        if bucket == BUCKET_HIGH:
            reasons.append("HIGH_VARIANCE_TAIL_EXPANSION")
        elif bucket == BUCKET_EXTREME:
            reasons.append("EXTREME_TAIL_EXPANSION")

        # P90 too compressed: critical rule per spec — if baseline p90
        # <=10 but ≥3 risk signals, recalibrate p90 explicitly.
        if (perc_before.get("p90") is not None
                and int(perc_before["p90"]) <= 10
                and n_signals >= 3):
            new_p90 = max(int(perc_after.get("p90", perc_before["p90"])),
                          int(perc_before["p90"]) + 1)
            perc_after["p90"] = new_p90
            reasons.append("P90_TOO_COMPRESSED_FOR_CONTEXT")
            reasons.append("P90_RECALIBRATED")
            reasons.append("CENTRAL_MEAN_NOT_ENOUGH")

        return {
            **distribution,
            "probabilities":            probs_after,
            "percentiles":              perc_after,
            "tail_calibration_applied": True,
            "tail_multiplier":          round(multiplier, 3),
            "tail_shift_runs":          tail_shift_runs,
            "tail_risk_bucket":         bucket,
            "before": {
                "probabilities": probs_before,
                "percentiles":   perc_before,
            },
            "after": {
                "probabilities": probs_after,
                "percentiles":   perc_after,
            },
            "reason_codes": reasons,
            "drivers":      drivers,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "tail_calibration_applied": False,
            "tail_multiplier":          1.0,
            "tail_shift_runs":          0.0,
            "tail_risk_bucket":         BUCKET_LOW,
            "before":                   {},
            "after":                    {},
            "reason_codes":             [f"EXCEPTION:{type(exc).__name__}"],
            "drivers":                  [],
        }


__all__ = [
    "calibrate_tail_probabilities",
    "SUPPORTED_LINES",
    "BUCKET_LOW", "BUCKET_MEDIUM", "BUCKET_HIGH", "BUCKET_EXTREME",
    "MULT_LOW", "MULT_MEDIUM", "MULT_HIGH", "MULT_EXTREME",
    "TAIL_LINES", "BODY_LINES",
]
