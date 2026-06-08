"""MLB Pregame Inning-Lambda Model — Phase 47.

Decompose pregame expected runs into three phase-specific Poisson means
(λ_1_3, λ_4_6, λ_7_9) so the engine can reason about F5, full-game,
bullpen fatigue, traffic score, and explosive-inning risk as part of
the *core projection* — not as cosmetic corrections applied afterwards.

Headline equation:

    expected_runs ≈ λ_1_3 + λ_4_6 + λ_7_9

Each phase is independently adjusted by domain factors before being
recombined. Continuous interaction is preferred over binary vetoes so
the model degrades gracefully when only partial inputs are available.

This module is **pure** (no I/O, no DB access). The caller hydrates
the inputs from whichever provider/pipeline they trust. ``available``
is False when the model cannot produce a useful projection (e.g.,
``expected_runs`` is missing).

Feature flags (env-driven):
    MLB_INNING_LAMBDA_ENABLED         (default true)
    MLB_LAMBDA_TRAFFIC_WEIGHT         (default 0.25)
    MLB_LAMBDA_MAX_PHASE_ADJUSTMENT   (default 0.35)  — symmetric cap
    MLB_LAMBDA_MIN_PHASE_VALUE        (default 0.05)
"""
from __future__ import annotations

import os
from typing import Any, Optional

ENGINE_VERSION = "mlb_inning_lambda.1"

# ─────────────────────────────────────────────────────────────────────
# Phase weights (sum to 1.0). Slightly back-weighted to reflect that
# the late innings carry more runs on average due to bullpen variance.
# ─────────────────────────────────────────────────────────────────────
DEFAULT_PHASE_WEIGHTS = {
    "lambda_1_3": 0.32,
    "lambda_4_6": 0.34,
    "lambda_7_9": 0.34,
}

# ── Reason codes ─────────────────────────────────────────────────────
RC_INNING_LAMBDA_MODEL_USED            = "INNING_LAMBDA_MODEL_USED"
RC_STARTER_SUPPRESSES_EARLY_RUNS       = "STARTER_SUPPRESSES_EARLY_RUNS"
RC_STARTER_EARLY_RISK                  = "STARTER_EARLY_RISK"
RC_TRANSITION_PHASE_RISK               = "TRANSITION_PHASE_RISK"
RC_STARTER_DURABILITY_LOW              = "STARTER_DURABILITY_LOW"
RC_BULLPEN_PHASE_RISK                  = "BULLPEN_PHASE_RISK"
RC_BULLPEN_FATIGUE_RAISES_LATE_LAMBDA  = "BULLPEN_FATIGUE_RAISES_LATE_LAMBDA"
RC_HIGH_TRAFFIC_RAISES_LATE_LAMBDA     = "HIGH_TRAFFIC_RAISES_LATE_LAMBDA"
RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK     = "LOW_TRAFFIC_LIMITS_BULLPEN_RISK"
RC_TRAFFIC_SCORE_MISSING_NEUTRAL_USED  = "TRAFFIC_SCORE_MISSING_NEUTRAL_USED"
RC_LATE_EXPLOSION_RISK_EMBEDDED        = "LATE_EXPLOSION_RISK_EMBEDDED"
RC_INNING_LAMBDA_SIGNIFICANT_DELTA     = "INNING_LAMBDA_SIGNIFICANT_DELTA"

ALL_REASON_CODES = (
    RC_INNING_LAMBDA_MODEL_USED,
    RC_STARTER_SUPPRESSES_EARLY_RUNS,
    RC_STARTER_EARLY_RISK,
    RC_TRANSITION_PHASE_RISK,
    RC_STARTER_DURABILITY_LOW,
    RC_BULLPEN_PHASE_RISK,
    RC_BULLPEN_FATIGUE_RAISES_LATE_LAMBDA,
    RC_HIGH_TRAFFIC_RAISES_LATE_LAMBDA,
    RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK,
    RC_TRAFFIC_SCORE_MISSING_NEUTRAL_USED,
    RC_LATE_EXPLOSION_RISK_EMBEDDED,
    RC_INNING_LAMBDA_SIGNIFICANT_DELTA,
)


# ─────────────────────────────────────────────────────────────────────
# Env helpers
# ─────────────────────────────────────────────────────────────────────
def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _safe(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────
# Component adjusters — each returns (multiplicative_factor, reason_codes)
# ─────────────────────────────────────────────────────────────────────
def _starter_factor_phase_1_3(pitcher: dict) -> tuple[float, list[str]]:
    """How much the starter suppresses (or amplifies) early-inning runs.

    Inputs (any subset):
      era, whip, fip, xera, k_per_9, bb_per_9, hr_per_9, recent_form.
    Returns a multiplicative factor centered on 1.0.
    """
    factor = 1.0
    reasons: list[str] = []
    era  = _safe(pitcher.get("era"))
    whip = _safe(pitcher.get("whip"))
    fip  = _safe(pitcher.get("fip")) or _safe(pitcher.get("xera"))

    # Composite quality score: lower is better. League-average ≈ 4.1 ERA.
    quality_score = None
    components = [v for v in (era, whip and whip * 4.0, fip) if v is not None]
    if components:
        quality_score = sum(components) / len(components)
        # Map quality to factor: 3.0 → 0.80 (suppress 20%),
        # 4.1 → 1.00 (neutral), 5.5 → 1.20 (amplify 20%).
        delta = (quality_score - 4.1) / 4.1
        factor = 1.0 + 0.25 * delta  # capped via global clamp downstream
        if quality_score <= 3.50:
            reasons.append(RC_STARTER_SUPPRESSES_EARLY_RUNS)
        elif quality_score >= 5.00:
            reasons.append(RC_STARTER_EARLY_RISK)
    return factor, reasons


def _starter_durability_factor(pitcher: dict) -> tuple[float, list[str]]:
    """Affects λ_4_6 — short outings shove run-scoring into the
    transition phase, raising λ_4_6.
    """
    factor = 1.0
    reasons: list[str] = []
    avg_ip = (
        _safe(pitcher.get("avg_innings_pitched"))
        or _safe(pitcher.get("ip_avg"))
        or _safe(pitcher.get("starter_durability"))
    )
    pitch_stress = _safe(pitcher.get("pitch_stress_index"))
    if avg_ip is not None and avg_ip < 5.2:
        # Short starter → 4_6 sees more bullpen / 3rd-time-through risk.
        gap = (5.2 - avg_ip) / 5.2  # 0..1
        factor *= 1.0 + 0.20 * gap
        reasons.append(RC_STARTER_DURABILITY_LOW)
    if pitch_stress is not None and pitch_stress >= 0.70:
        factor *= 1.0 + 0.10 * (pitch_stress - 0.70) / 0.30
    if (avg_ip is not None and avg_ip < 5.0) or (pitch_stress and pitch_stress >= 0.80):
        reasons.append(RC_TRANSITION_PHASE_RISK)
    return factor, reasons


def _lineup_factor(lineup: dict) -> float:
    """Generic lineup-quality factor (-20% to +20%) used as a small bump
    on every phase to ground the projection in lineup reality. Uses OPS
    or wRC+ when available.
    """
    ops    = _safe(lineup.get("ops")) or _safe(lineup.get("team_ops_7d"))
    wrc    = _safe(lineup.get("wrc_plus")) or _safe(lineup.get("wRC_plus"))
    recent = _safe(lineup.get("recent_runs_per_game"))
    if ops is None and wrc is None and recent is None:
        return 1.0
    # OPS 0.700 → neutral; 0.800 → +10%; 0.600 → -10%.
    if ops is not None:
        return 1.0 + (ops - 0.700) * 0.5
    if wrc is not None:
        return 1.0 + (wrc - 100) / 100.0 * 0.20
    return 1.0 + (recent - 4.4) * 0.04


def _bullpen_traffic_factor(
    bullpen: dict,
    traffic_score: Optional[float],
    traffic_weight: float,
) -> tuple[float, list[str], dict]:
    """Continuous bullpen + traffic interaction for λ_7_9.

    Formula:
        factor = (1 + bullpen_term) * (1 + traffic_weight * normalized_traffic * bullpen_residual)

    Where:
        bullpen_term      = 0.30 * normalized(bullpen_era_7d, league_avg=4.10)
                            + 0.15 * normalized(bullpen_whip_7d, league_avg=1.30)
                            + 0.10 * usage_3d_above_threshold
        bullpen_residual  = normalized(bullpen_era_7d - 4.10) clipped to [0, 1.5]
        normalized_traffic = traffic_score / 100  (or 0.50 when missing)

    Returns ``(factor, reasons, breakdown)`` where breakdown carries the
    intermediate values for transparency in the response payload.
    """
    reasons: list[str] = []
    bp_era  = _safe(bullpen.get("bullpen_era_7d"))
    bp_whip = _safe(bullpen.get("bullpen_whip_7d"))
    bp_usage_3d = _safe(bullpen.get("bullpen_usage_3d"))  # 0..1
    bp_fatigue  = _safe(bullpen.get("bullpen_fatigue"))   # 0..1
    hr_risk     = _safe(bullpen.get("hr_risk"))           # 0..1
    explosion   = _safe(bullpen.get("offensive_explosion_score"))  # 0..1

    league_era  = 4.10
    league_whip = 1.30

    bullpen_term = 0.0
    if bp_era is not None:
        bullpen_term += 0.30 * ((bp_era - league_era) / league_era)
    if bp_whip is not None:
        bullpen_term += 0.15 * ((bp_whip - league_whip) / league_whip)
    if bp_usage_3d is not None and bp_usage_3d > 0.55:
        bullpen_term += 0.10 * (bp_usage_3d - 0.55) / 0.45
    if bp_fatigue is not None and bp_fatigue > 0.50:
        bullpen_term += 0.10 * (bp_fatigue - 0.50)
        if bp_fatigue >= 0.65:
            reasons.append(RC_BULLPEN_FATIGUE_RAISES_LATE_LAMBDA)
    if hr_risk is not None and hr_risk > 0.55:
        bullpen_term += 0.05 * (hr_risk - 0.55)
    if explosion is not None and explosion > 0.55:
        bullpen_term += 0.05 * (explosion - 0.55)
        reasons.append(RC_LATE_EXPLOSION_RISK_EMBEDDED)

    # Bullpen vulnerability residual (0 when bullpen is league-average,
    # rising as it gets worse). Clipped at 1.5 to prevent extreme single
    # outliers (e.g. ERA 9.0 in a 5-game window) from blowing up λ_7_9.
    bullpen_residual = max(0.0, ((bp_era or league_era) - league_era) / league_era)
    bullpen_residual = min(1.5, bullpen_residual)

    if traffic_score is None:
        normalized_traffic = 0.50  # neutral fallback
        reasons.append(RC_TRAFFIC_SCORE_MISSING_NEUTRAL_USED)
    else:
        normalized_traffic = _clamp(float(traffic_score) / 100.0, 0.0, 1.0)

    interaction = traffic_weight * normalized_traffic * bullpen_residual

    if bullpen_residual >= 0.10:
        if normalized_traffic >= 0.65:
            reasons.append(RC_HIGH_TRAFFIC_RAISES_LATE_LAMBDA)
        elif normalized_traffic <= 0.35:
            reasons.append(RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK)

    if bullpen_term > 0.05 or interaction > 0.05:
        reasons.append(RC_BULLPEN_PHASE_RISK)

    factor = (1.0 + bullpen_term) * (1.0 + interaction)
    return factor, reasons, {
        "bullpen_term":       round(bullpen_term, 4),
        "bullpen_residual":   round(bullpen_residual, 4),
        "normalized_traffic": round(normalized_traffic, 4),
        "interaction":        round(interaction, 4),
        "traffic_weight":     traffic_weight,
    }


def _park_weather_factor(env: dict) -> float:
    """Park + weather as a single multiplicative factor.

    ``env`` accepts:
        park_factor (1.0 = neutral; >1 = hitters' park)
        weather_factor (multiplicative; e.g., 0.95 cold/wet, 1.05 wind out)
    """
    pf = _safe(env.get("park_factor"))
    wf = _safe(env.get("weather_factor"))
    factor = 1.0
    if pf is not None:
        factor *= pf
    if wf is not None:
        factor *= wf
    return factor


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def compute_mlb_inning_lambdas(
    *,
    expected_runs:       Optional[float],
    home_pitcher:        Optional[dict] = None,
    away_pitcher:        Optional[dict] = None,
    home_lineup:         Optional[dict] = None,
    away_lineup:         Optional[dict] = None,
    bullpen_home:        Optional[dict] = None,
    bullpen_away:        Optional[dict] = None,
    traffic_score:       Optional[float] = None,
    park_factor:         Optional[float] = None,
    weather_factor:      Optional[float] = None,
    market_line:         Optional[float] = None,
    phase_weights:       Optional[dict]  = None,
    observe_only:        bool            = True,
) -> dict:
    """Build the per-phase λ projection.

    Returns dict with ``available``, λ values, F5 + full-game projections,
    phase breakdown and reason codes. Always returns even on missing
    inputs (``available=false`` + zeros).
    """
    if not _env_bool("MLB_INNING_LAMBDA_ENABLED", True):
        return {
            "available":       False,
            "engine_version":  ENGINE_VERSION,
            "reason":          "feature_flag_disabled",
            "observe_only":    observe_only,
            "reason_codes":    [],
        }

    base = _safe(expected_runs)
    if base is None or base <= 0:
        return {
            "available":       False,
            "engine_version":  ENGINE_VERSION,
            "reason":          "missing_or_invalid_expected_runs",
            "observe_only":    observe_only,
            "reason_codes":    [],
        }

    traffic_weight       = _env_float("MLB_LAMBDA_TRAFFIC_WEIGHT", 0.25)
    max_phase_adjustment = _env_float("MLB_LAMBDA_MAX_PHASE_ADJUSTMENT", 0.35)
    min_phase_value      = _env_float("MLB_LAMBDA_MIN_PHASE_VALUE", 0.05)
    weights = {**DEFAULT_PHASE_WEIGHTS, **(phase_weights or {})}
    # Renormalize weights in case the caller passed a partial override.
    total_w = sum(weights.values()) or 1.0
    weights = {k: v / total_w for k, v in weights.items()}

    # ── Phase 1 — Starter phase (λ_1_3) ──────────────────────────────
    # Use both starters: defender = the team currently pitching. Average
    # the factors so the projection captures both halves of innings 1-3.
    s_factor_h, r_h = _starter_factor_phase_1_3(home_pitcher or {})
    s_factor_a, r_a = _starter_factor_phase_1_3(away_pitcher or {})
    starter_factor = (s_factor_h + s_factor_a) / 2.0
    lineup_factor_overall = (
        _lineup_factor(home_lineup or {}) + _lineup_factor(away_lineup or {})
    ) / 2.0
    park_weather = _park_weather_factor({
        "park_factor": park_factor, "weather_factor": weather_factor,
    })

    raw_1_3 = base * weights["lambda_1_3"] * starter_factor * lineup_factor_overall * park_weather
    lambda_1_3 = _apply_cap(base * weights["lambda_1_3"], raw_1_3, max_phase_adjustment, min_phase_value)
    reasons_1_3 = sorted(set(r_h + r_a))

    # ── Phase 2 — Transition phase (λ_4_6) ───────────────────────────
    d_factor_h, rd_h = _starter_durability_factor(home_pitcher or {})
    d_factor_a, rd_a = _starter_durability_factor(away_pitcher or {})
    durability_factor = (d_factor_h + d_factor_a) / 2.0
    raw_4_6 = base * weights["lambda_4_6"] * durability_factor * lineup_factor_overall * park_weather
    lambda_4_6 = _apply_cap(base * weights["lambda_4_6"], raw_4_6, max_phase_adjustment, min_phase_value)
    reasons_4_6 = sorted(set(rd_h + rd_a))

    # ── Phase 3 — Bullpen phase (λ_7_9) ──────────────────────────────
    bp_factor_h, rb_h, brk_h = _bullpen_traffic_factor(bullpen_home or {}, traffic_score, traffic_weight)
    bp_factor_a, rb_a, brk_a = _bullpen_traffic_factor(bullpen_away or {}, traffic_score, traffic_weight)
    bullpen_factor = (bp_factor_h + bp_factor_a) / 2.0
    raw_7_9 = base * weights["lambda_7_9"] * bullpen_factor * lineup_factor_overall * park_weather
    lambda_7_9 = _apply_cap(base * weights["lambda_7_9"], raw_7_9, max_phase_adjustment, min_phase_value)
    reasons_7_9 = sorted(set(rb_h + rb_a))

    # ── F5 projection — λ_1_3 + half of λ_4_6 (innings 4-5) ──────────
    # 2 of the 3 transition innings count toward F5, so 2/3 of λ_4_6.
    f5_expected_runs = lambda_1_3 + (lambda_4_6 * (2.0 / 3.0))

    expected_runs_new = lambda_1_3 + lambda_4_6 + lambda_7_9
    delta_vs_baseline = expected_runs_new - base

    reason_codes: list[str] = [RC_INNING_LAMBDA_MODEL_USED]
    reason_codes.extend(reasons_1_3)
    reason_codes.extend(reasons_4_6)
    reason_codes.extend(reasons_7_9)
    if abs(delta_vs_baseline) >= 1.0:
        reason_codes.append(RC_INNING_LAMBDA_SIGNIFICANT_DELTA)
    reason_codes = list(dict.fromkeys(reason_codes))  # preserve order, dedupe

    return {
        "available":       True,
        "engine_version":  ENGINE_VERSION,
        "observe_only":    observe_only,
        "lambda_1_3":      round(lambda_1_3, 3),
        "lambda_4_6":      round(lambda_4_6, 3),
        "lambda_7_9":      round(lambda_7_9, 3),
        "expected_runs":   round(expected_runs_new, 3),
        "f5_expected_runs": round(f5_expected_runs, 3),
        "baseline_expected_runs": round(base, 3),
        "delta_vs_baseline":      round(delta_vs_baseline, 3),
        "phase_weights":   {k: round(v, 4) for k, v in weights.items()},
        "config": {
            "traffic_weight":       traffic_weight,
            "max_phase_adjustment": max_phase_adjustment,
            "min_phase_value":      min_phase_value,
        },
        "phase_breakdown": {
            "starter_phase": {
                "lambda":         round(lambda_1_3, 3),
                "starter_factor": round(starter_factor, 4),
                "lineup_factor":  round(lineup_factor_overall, 4),
                "park_weather":   round(park_weather, 4),
                "reason_codes":   reasons_1_3,
            },
            "transition_phase": {
                "lambda":            round(lambda_4_6, 3),
                "durability_factor": round(durability_factor, 4),
                "lineup_factor":     round(lineup_factor_overall, 4),
                "park_weather":      round(park_weather, 4),
                "reason_codes":      reasons_4_6,
            },
            "bullpen_phase": {
                "lambda":         round(lambda_7_9, 3),
                "bullpen_factor": round(bullpen_factor, 4),
                "lineup_factor":  round(lineup_factor_overall, 4),
                "park_weather":   round(park_weather, 4),
                "reason_codes":   reasons_7_9,
                "breakdown_home": brk_h,
                "breakdown_away": brk_a,
                "traffic_score":  traffic_score,
            },
        },
        "market_line":  market_line,
        "reason_codes": reason_codes,
    }


def _apply_cap(baseline: float, raw_value: float, max_adj: float, min_value: float) -> float:
    """Clamp ``raw_value`` so it never deviates from ``baseline`` by
    more than ``max_adj`` (proportional). Floors at ``min_value``.
    """
    if baseline <= 0:
        return max(min_value, raw_value)
    hi = baseline * (1.0 + max_adj)
    lo = baseline * (1.0 - max_adj)
    return max(min_value, min(hi, max(lo, raw_value)))


__all__ = [
    "ENGINE_VERSION",
    "DEFAULT_PHASE_WEIGHTS",
    "RC_INNING_LAMBDA_MODEL_USED",
    "RC_STARTER_SUPPRESSES_EARLY_RUNS",
    "RC_STARTER_EARLY_RISK",
    "RC_TRANSITION_PHASE_RISK",
    "RC_STARTER_DURABILITY_LOW",
    "RC_BULLPEN_PHASE_RISK",
    "RC_BULLPEN_FATIGUE_RAISES_LATE_LAMBDA",
    "RC_HIGH_TRAFFIC_RAISES_LATE_LAMBDA",
    "RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK",
    "RC_TRAFFIC_SCORE_MISSING_NEUTRAL_USED",
    "RC_LATE_EXPLOSION_RISK_EMBEDDED",
    "RC_INNING_LAMBDA_SIGNIFICANT_DELTA",
    "ALL_REASON_CODES",
    "compute_mlb_inning_lambdas",
]
