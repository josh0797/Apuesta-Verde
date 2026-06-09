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
    MLB_LAMBDA_DEFENSE_WEIGHT         (default 0.15)  ← Priority 1
    MLB_LAMBDA_FATIGUE_WEIGHT         (default 0.15)  ← Priority 1
    MLB_LAMBDA_HR_WEIGHT              (default 0.10)  ← Priority 1
    MLB_LAMBDA_SERIES_WEIGHT          (default 0.10)  ← Priority 3
    MLB_LAMBDA_MAX_PHASE_ADJUSTMENT   (default 0.35)  — symmetric cap (1-3, 4-6)
    MLB_LAMBDA_MAX_LATE_ADJUSTMENT    (default 0.45)  ← Priority 1: cap for λ_7_9
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

# ── Priority 1 — λ7-9 reactive model ─────────────────────────────────
RC_LATE_LAMBDA_REACTIVE_MODEL_USED     = "LATE_LAMBDA_REACTIVE_MODEL_USED"
RC_BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA  = "BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA"
RC_BULLPEN_DEFENSE_RAISES_LATE_LAMBDA  = "BULLPEN_DEFENSE_RAISES_LATE_LAMBDA"
RC_FATIGUE_RAISES_LATE_LAMBDA          = "FATIGUE_RAISES_LATE_LAMBDA"
RC_HR_RISK_RAISES_LATE_LAMBDA          = "HR_RISK_RAISES_LATE_LAMBDA"
RC_LOW_DEFENSIVE_RISK_LIMITS_LATE_RUNS = "LOW_DEFENSIVE_RISK_LIMITS_LATE_RUNS"
RC_LATE_LAMBDA_CAPPED                  = "LATE_LAMBDA_CAPPED"

# ── Priority 2 — Projection breakdown ────────────────────────────────
RC_PROJECTION_BREAKDOWN_AVAILABLE      = "PROJECTION_BREAKDOWN_AVAILABLE"
RC_STARTER_IMPACT_EXPLAINED            = "STARTER_IMPACT_EXPLAINED"
RC_BULLPEN_IMPACT_EXPLAINED            = "BULLPEN_IMPACT_EXPLAINED"
RC_TRAFFIC_IMPACT_EXPLAINED            = "TRAFFIC_IMPACT_EXPLAINED"
RC_DEFENSE_IMPACT_EXPLAINED            = "DEFENSE_IMPACT_EXPLAINED"

# ── Priority 3 — Series familiarity ──────────────────────────────────
RC_SERIES_FAMILIARITY_DETECTED         = "SERIES_FAMILIARITY_DETECTED"
RC_RECENT_REPEAT_MATCHUP               = "RECENT_REPEAT_MATCHUP"
RC_SERIES_FAMILIARITY_TRAFFIC_BOOST    = "SERIES_FAMILIARITY_TRAFFIC_BOOST"
RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT = "SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT"
RC_SERIES_FAMILIARITY_CAPPED           = "SERIES_FAMILIARITY_CAPPED"

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
    # Priority 1
    RC_LATE_LAMBDA_REACTIVE_MODEL_USED,
    RC_BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA,
    RC_BULLPEN_DEFENSE_RAISES_LATE_LAMBDA,
    RC_FATIGUE_RAISES_LATE_LAMBDA,
    RC_HR_RISK_RAISES_LATE_LAMBDA,
    RC_LOW_DEFENSIVE_RISK_LIMITS_LATE_RUNS,
    RC_LATE_LAMBDA_CAPPED,
    # Priority 2
    RC_PROJECTION_BREAKDOWN_AVAILABLE,
    RC_STARTER_IMPACT_EXPLAINED,
    RC_BULLPEN_IMPACT_EXPLAINED,
    RC_TRAFFIC_IMPACT_EXPLAINED,
    RC_DEFENSE_IMPACT_EXPLAINED,
    # Priority 3
    RC_SERIES_FAMILIARITY_DETECTED,
    RC_RECENT_REPEAT_MATCHUP,
    RC_SERIES_FAMILIARITY_TRAFFIC_BOOST,
    RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT,
    RC_SERIES_FAMILIARITY_CAPPED,
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


def _bullpen_vulnerability_residual(bullpen: dict) -> tuple[float, dict]:
    """Priority 1 — Composite bullpen vulnerability residual in [0, 1].

    Combines (with weights):
        bullpen_era_7d         (60%)  league avg 4.10
        bullpen_whip_7d        (25%)  league avg 1.30
        bullpen_usage_3d       (15%)  trigger at >0.55

    The "residual" is how vulnerable the bullpen is relative to league
    average. ``0.0`` = league average, ``1.0`` = catastrophic (ERA 7+,
    WHIP 1.7+, fully used). This residual is the modulator for the
    traffic / defense interactions below.
    """
    bp_era      = _safe(bullpen.get("bullpen_era_7d"))
    bp_whip     = _safe(bullpen.get("bullpen_whip_7d"))
    bp_usage_3d = _safe(bullpen.get("bullpen_usage_3d"))

    league_era  = 4.10
    league_whip = 1.30
    components: list[tuple[str, float, float]] = []  # (name, raw, weighted_residual)

    era_res = 0.0
    if bp_era is not None:
        era_res = _clamp((bp_era - league_era) / league_era, 0.0, 1.5)
        # Normalize so era_res in [0, 1] when era ∈ [league, league*2.0].
        era_res = min(1.0, era_res / 1.0)
        components.append(("bullpen_era_7d", bp_era, 0.60 * era_res))

    whip_res = 0.0
    if bp_whip is not None:
        whip_res = _clamp((bp_whip - league_whip) / league_whip, 0.0, 1.5)
        whip_res = min(1.0, whip_res / 1.0)
        components.append(("bullpen_whip_7d", bp_whip, 0.25 * whip_res))

    usage_res = 0.0
    if bp_usage_3d is not None and bp_usage_3d > 0.55:
        # Saturates at 1.0 around 90% usage.
        usage_res = _clamp((bp_usage_3d - 0.55) / 0.35, 0.0, 1.0)
        components.append(("bullpen_usage_3d", bp_usage_3d, 0.15 * usage_res))

    if not components:
        return 0.0, {"available": False}

    total_residual = min(1.0, sum(w for _, _, w in components))
    return total_residual, {
        "available":   True,
        "components":  {n: r for n, r, _ in components},
        "weighted":    {n: w for n, _, w in components},
        "vulnerability": round(total_residual, 4),
    }


def _bullpen_traffic_factor(
    bullpen: dict,
    traffic_score: Optional[float],
    defensive_breakdown_score: Optional[float],
    series_familiarity_score: Optional[float],
    weights_cfg: dict,
) -> tuple[float, list[str], dict]:
    """Priority 1 — Reactive λ7-9 multiplier using interaction modifiers.

    Equation:
        late_explosion_factor =
            1
          + traffic_weight  * (vulnerability_residual * normalized_traffic)
          + defense_weight  * (vulnerability_residual * normalized_defense)
          + fatigue_weight  * bullpen_fatigue
          + hr_weight       * hr_risk
          + series_weight   * series_traffic_boost

    Where:
        vulnerability_residual ∈ [0, 1] from bullpen_era_7d + whip_7d + usage_3d
        normalized_traffic     ∈ [0, 1] = traffic_score / 100 (or 0.50 fallback)
        normalized_defense     ∈ [0, 1] = defensive_breakdown_score / 100
        series_traffic_boost   = norm(series) * max(norm(fatigue), norm(traffic))

    Core rule (Priority 1):
        Bullpen risk ALONE doesn't heavily inflate λ7-9. Strong adjustment
        only happens when bullpen vulnerability MEETS high traffic or
        high defensive breakdown.

    Returns ``(factor, reasons, breakdown)``. The breakdown is used by
    Priority 2 (adjustment_breakdown) to explain the deltas to the user.
    """
    reasons: list[str] = []
    breakdown: dict = {}
    reasons.append(RC_LATE_LAMBDA_REACTIVE_MODEL_USED)

    bp_fatigue = _safe(bullpen.get("bullpen_fatigue")) or 0.0
    hr_risk    = _safe(bullpen.get("hr_risk")) or 0.0
    explosion  = _safe(bullpen.get("offensive_explosion_score")) or 0.0
    if explosion >= 0.55:
        reasons.append(RC_LATE_EXPLOSION_RISK_EMBEDDED)

    vulnerability_residual, vuln_brk = _bullpen_vulnerability_residual(bullpen)
    breakdown["vulnerability_residual"] = round(vulnerability_residual, 4)
    breakdown["vulnerability_breakdown"] = vuln_brk

    # Normalized traffic / defense scores.
    if traffic_score is None:
        normalized_traffic = 0.50
        reasons.append(RC_TRAFFIC_SCORE_MISSING_NEUTRAL_USED)
    else:
        normalized_traffic = _clamp(float(traffic_score) / 100.0, 0.0, 1.0)

    if defensive_breakdown_score is None:
        normalized_defense = 0.50
    else:
        normalized_defense = _clamp(float(defensive_breakdown_score) / 100.0, 0.0, 1.0)

    if series_familiarity_score is None:
        normalized_series = 0.0
    else:
        normalized_series = _clamp(float(series_familiarity_score) / 100.0, 0.0, 1.0)

    # Interaction modifiers (these are the heart of Priority 1).
    bullpen_traffic_interaction = vulnerability_residual * normalized_traffic
    bullpen_defense_interaction = vulnerability_residual * normalized_defense
    # Series boost only applies when there's bullpen usage OR traffic to amplify.
    series_traffic_boost = normalized_series * max(
        _clamp(bp_fatigue, 0.0, 1.0), normalized_traffic,
    )

    traffic_weight = weights_cfg["traffic"]
    defense_weight = weights_cfg["defense"]
    fatigue_weight = weights_cfg["fatigue"]
    hr_weight      = weights_cfg["hr"]
    series_weight  = weights_cfg["series"]

    contribution_traffic = traffic_weight * bullpen_traffic_interaction
    contribution_defense = defense_weight * bullpen_defense_interaction
    contribution_fatigue = fatigue_weight * _clamp(bp_fatigue, 0.0, 1.0)
    contribution_hr      = hr_weight      * _clamp(hr_risk, 0.0, 1.0)
    contribution_series  = series_weight  * series_traffic_boost
    # Cap the series contribution explicitly at +0.35 runs worth of multiplier.
    if contribution_series > 0.07:  # ≈ +0.35 runs on a typical λ_7_9 ~5
        contribution_series = 0.07
        reasons.append(RC_SERIES_FAMILIARITY_CAPPED)

    late_explosion_factor = (
        1.0
        + contribution_traffic
        + contribution_defense
        + contribution_fatigue
        + contribution_hr
        + contribution_series
    )

    # Reason annotations — show WHICH levers fired.
    if vulnerability_residual >= 0.15:
        # Back-compat: surface BULLPEN_PHASE_RISK whenever the bullpen
        # itself is meaningfully above league average, independent of
        # the interaction levers.
        reasons.append(RC_BULLPEN_PHASE_RISK)
    if vulnerability_residual >= 0.15 and normalized_traffic >= 0.55:
        reasons.append(RC_BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA)
        reasons.append(RC_HIGH_TRAFFIC_RAISES_LATE_LAMBDA)
    elif vulnerability_residual >= 0.20 and normalized_traffic <= 0.35:
        reasons.append(RC_LOW_TRAFFIC_LIMITS_BULLPEN_RISK)

    if vulnerability_residual >= 0.15 and normalized_defense >= 0.55:
        reasons.append(RC_BULLPEN_DEFENSE_RAISES_LATE_LAMBDA)
    elif vulnerability_residual >= 0.20 and normalized_defense <= 0.35:
        reasons.append(RC_LOW_DEFENSIVE_RISK_LIMITS_LATE_RUNS)

    if bp_fatigue >= 0.55:
        reasons.append(RC_FATIGUE_RAISES_LATE_LAMBDA)
        reasons.append(RC_BULLPEN_FATIGUE_RAISES_LATE_LAMBDA)
    if hr_risk >= 0.55:
        reasons.append(RC_HR_RISK_RAISES_LATE_LAMBDA)
    if normalized_series >= 0.40 and series_traffic_boost >= 0.20:
        reasons.append(RC_SERIES_FAMILIARITY_TRAFFIC_BOOST)

    breakdown.update({
        "normalized_traffic":         round(normalized_traffic, 4),
        "normalized_defense":         round(normalized_defense, 4),
        "normalized_series":          round(normalized_series, 4),
        "bullpen_fatigue":            round(_clamp(bp_fatigue, 0.0, 1.0), 4),
        "hr_risk":                    round(_clamp(hr_risk, 0.0, 1.0), 4),
        "bullpen_traffic_interaction": round(bullpen_traffic_interaction, 4),
        "bullpen_defense_interaction": round(bullpen_defense_interaction, 4),
        "series_traffic_boost":        round(series_traffic_boost, 4),
        "contributions": {
            "traffic": round(contribution_traffic, 4),
            "defense": round(contribution_defense, 4),
            "fatigue": round(contribution_fatigue, 4),
            "hr":      round(contribution_hr, 4),
            "series":  round(contribution_series, 4),
        },
        "weights": dict(weights_cfg),
        "late_explosion_factor": round(late_explosion_factor, 4),
    })
    return late_explosion_factor, reasons, breakdown


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
    # Phase 50 forward-compat — Defensive Breakdown Score (0-100) feeds
    # the bullpen-phase risk when the caller has it. When provided, the
    # score is multiplied into the bullpen vulnerability residual so
    # high defensive breakdown amplifies λ_7_9 alongside traffic.
    defensive_breakdown_score: Optional[float] = None,
    # Priority 3 forward-compat — series familiarity score (0-100). Only
    # applies a boost when combined with bullpen fatigue OR high traffic.
    series_familiarity_score:  Optional[float] = None,
) -> dict:
    """Build the per-phase λ projection.

    Returns dict with ``available``, λ values, F5 + full-game projections,
    phase breakdown, ``adjustment_breakdown`` (Priority 2) and reason
    codes. Always returns even on missing inputs (``available=false`` +
    zeros).
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

    # ── Priority 1 weights (env-driven) ──────────────────────────────
    weights_cfg = {
        "traffic": _env_float("MLB_LAMBDA_TRAFFIC_WEIGHT", 0.25),
        "defense": _env_float("MLB_LAMBDA_DEFENSE_WEIGHT", 0.15),
        "fatigue": _env_float("MLB_LAMBDA_FATIGUE_WEIGHT", 0.15),
        "hr":      _env_float("MLB_LAMBDA_HR_WEIGHT",      0.10),
        "series":  _env_float("MLB_LAMBDA_SERIES_WEIGHT",  0.10),
    }
    max_phase_adjustment = _env_float("MLB_LAMBDA_MAX_PHASE_ADJUSTMENT", 0.35)
    max_late_adjustment  = _env_float("MLB_LAMBDA_MAX_LATE_ADJUSTMENT",  0.45)
    min_phase_value      = _env_float("MLB_LAMBDA_MIN_PHASE_VALUE", 0.05)
    weights = {**DEFAULT_PHASE_WEIGHTS, **(phase_weights or {})}
    # Renormalize weights in case the caller passed a partial override.
    total_w = sum(weights.values()) or 1.0
    weights = {k: v / total_w for k, v in weights.items()}

    # Adjustment breakdown collector — populated as we apply factors so
    # the response payload can explain WHY the total moved (Priority 2).
    adjustments: list[dict] = []
    lambda_base_1_3 = base * weights["lambda_1_3"]
    lambda_base_4_6 = base * weights["lambda_4_6"]
    lambda_base_7_9 = base * weights["lambda_7_9"]

    def _record_adj(phase: str, factor: str, baseline: float, adjusted: float, reason: str):
        delta = round(adjusted - baseline, 4)
        if abs(delta) >= 0.01:
            adjustments.append({
                "phase":  phase,
                "factor": factor,
                "delta":  delta,
                "reason": reason,
            })

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

    raw_1_3 = lambda_base_1_3 * starter_factor * lineup_factor_overall * park_weather
    lambda_1_3 = _apply_cap(lambda_base_1_3, raw_1_3, max_phase_adjustment, min_phase_value)
    reasons_1_3 = sorted(set(r_h + r_a))

    # Breakdown: split λ_1_3 adjustments by sub-factor for transparency.
    _record_adj(
        "1_3", "starter_quality",
        lambda_base_1_3,
        lambda_base_1_3 * starter_factor,
        "Calidad de abridores ajusta carreras tempranas",
    )
    _record_adj(
        "1_3", "lineup",
        lambda_base_1_3 * starter_factor,
        lambda_base_1_3 * starter_factor * lineup_factor_overall,
        "Calidad ofensiva de las alineaciones",
    )
    _record_adj(
        "1_3", "park_weather",
        lambda_base_1_3 * starter_factor * lineup_factor_overall,
        lambda_base_1_3 * starter_factor * lineup_factor_overall * park_weather,
        "Estadio + clima",
    )

    # ── Phase 2 — Transition phase (λ_4_6) ───────────────────────────
    d_factor_h, rd_h = _starter_durability_factor(home_pitcher or {})
    d_factor_a, rd_a = _starter_durability_factor(away_pitcher or {})
    durability_factor = (d_factor_h + d_factor_a) / 2.0
    raw_4_6 = lambda_base_4_6 * durability_factor * lineup_factor_overall * park_weather
    lambda_4_6 = _apply_cap(lambda_base_4_6, raw_4_6, max_phase_adjustment, min_phase_value)
    reasons_4_6 = sorted(set(rd_h + rd_a))

    _record_adj(
        "4_6", "starter_durability",
        lambda_base_4_6,
        lambda_base_4_6 * durability_factor,
        "Durabilidad del abridor en la transición",
    )
    _record_adj(
        "4_6", "lineup",
        lambda_base_4_6 * durability_factor,
        lambda_base_4_6 * durability_factor * lineup_factor_overall,
        "Calidad ofensiva en la transición",
    )

    # ── Phase 3 — Bullpen phase (λ_7_9) — Priority 1 reactive model ──
    # Inject defensive breakdown into the offensive_explosion_score so it
    # feeds the bullpen residual computation. The traffic / defense /
    # fatigue / hr / series modifiers are now first-class citizens.
    if defensive_breakdown_score is not None:
        try:
            _db = max(0.0, min(100.0, float(defensive_breakdown_score))) / 100.0
            for _bp in (bullpen_home or {}, bullpen_away or {}):
                _curr = _bp.get("offensive_explosion_score") or 0.0
                _bp["offensive_explosion_score"] = max(float(_curr), 0.55 + 0.4 * _db)
        except (TypeError, ValueError):
            pass

    bp_factor_h, rb_h, brk_h = _bullpen_traffic_factor(
        bullpen_home or {}, traffic_score, defensive_breakdown_score,
        series_familiarity_score, weights_cfg,
    )
    bp_factor_a, rb_a, brk_a = _bullpen_traffic_factor(
        bullpen_away or {}, traffic_score, defensive_breakdown_score,
        series_familiarity_score, weights_cfg,
    )
    bullpen_factor = (bp_factor_h + bp_factor_a) / 2.0
    raw_7_9 = lambda_base_7_9 * bullpen_factor * lineup_factor_overall * park_weather

    # Priority 1 — use the EXPLICIT late-adjustment cap (default ±45%).
    lambda_7_9_pre_cap = raw_7_9
    lambda_7_9 = _apply_cap(lambda_base_7_9, raw_7_9, max_late_adjustment, min_phase_value)
    capped = abs(lambda_7_9_pre_cap - lambda_7_9) > 0.001
    reasons_7_9 = sorted(set(rb_h + rb_a))
    if capped:
        reasons_7_9.append(RC_LATE_LAMBDA_CAPPED)

    # Breakdown rows: one per contribution lever so the UI can render
    # "¿Por qué esta proyección?" clearly. We use the AVERAGE of home +
    # away bullpen contributions (consistent with bullpen_factor avg).
    avg_contrib = {
        k: (brk_h.get("contributions", {}).get(k, 0.0)
            + brk_a.get("contributions", {}).get(k, 0.0)) / 2.0
        for k in ("traffic", "defense", "fatigue", "hr", "series")
    }
    for lever, label, rc_code in (
        ("traffic",  "traffic_score",             RC_TRAFFIC_IMPACT_EXPLAINED),
        ("defense",  "defensive_breakdown",       RC_DEFENSE_IMPACT_EXPLAINED),
        ("fatigue",  "bullpen_fatigue",           RC_BULLPEN_IMPACT_EXPLAINED),
        ("hr",       "hr_risk",                   RC_BULLPEN_IMPACT_EXPLAINED),
        ("series",   "series_familiarity",        RC_TRAFFIC_IMPACT_EXPLAINED),
    ):
        contrib = avg_contrib.get(lever, 0.0)
        if abs(contrib) >= 0.01:
            # delta in runs ≈ lambda_base_7_9 * contribution (factor offset).
            delta = round(lambda_base_7_9 * contrib, 4)
            adjustments.append({
                "phase":  "7_9",
                "factor": label,
                "delta":  delta,
                "reason": {
                    "traffic":  "Tráfico ofensivo amplifica riesgo del bullpen",
                    "defense":  "Riesgo defensivo eleva carreras del bullpen",
                    "fatigue":  "Bullpen fatigado eleva carreras tardías",
                    "hr":       "Riesgo de HR del bullpen",
                    "series":   "Familiaridad de serie con bullpen usado",
                }[lever],
                "reason_code": rc_code,
            })

    # ── F5 projection — λ_1_3 + half of λ_4_6 (innings 4-5) ──────────
    f5_expected_runs = lambda_1_3 + (lambda_4_6 * (2.0 / 3.0))
    expected_runs_new = lambda_1_3 + lambda_4_6 + lambda_7_9
    delta_vs_baseline = expected_runs_new - base

    # ── Reason codes ─────────────────────────────────────────────────
    reason_codes: list[str] = [RC_INNING_LAMBDA_MODEL_USED]
    reason_codes.extend(reasons_1_3)
    reason_codes.extend(reasons_4_6)
    reason_codes.extend(reasons_7_9)
    reason_codes.append(RC_PROJECTION_BREAKDOWN_AVAILABLE)
    if reasons_1_3:
        reason_codes.append(RC_STARTER_IMPACT_EXPLAINED)
    # Collect breakdown-level reason codes (Priority 2).
    if any(a["phase"] == "7_9" for a in adjustments):
        reason_codes.append(RC_BULLPEN_IMPACT_EXPLAINED)
    # Surface per-lever impact codes even if individual deltas are
    # small — having a traffic / defense score available is enough to
    # explain it in the breakdown UI.
    if traffic_score is not None:
        reason_codes.append(RC_TRAFFIC_IMPACT_EXPLAINED)
    if defensive_breakdown_score is not None:
        reason_codes.append(RC_DEFENSE_IMPACT_EXPLAINED)
    if series_familiarity_score is not None and series_familiarity_score >= 40:
        reason_codes.append(RC_SERIES_FAMILIARITY_DETECTED)
        reason_codes.append(RC_RECENT_REPEAT_MATCHUP)
    elif series_familiarity_score is not None and series_familiarity_score < 40:
        reason_codes.append(RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT)
    if abs(delta_vs_baseline) >= 1.0:
        reason_codes.append(RC_INNING_LAMBDA_SIGNIFICANT_DELTA)
    reason_codes = list(dict.fromkeys(reason_codes))  # preserve order, dedupe

    # ── Priority 2 — adjustment_breakdown payload ────────────────────
    adjustment_breakdown = {
        "base_expected_runs": round(base, 3),
        "lambda_base": {
            "lambda_1_3": round(lambda_base_1_3, 3),
            "lambda_4_6": round(lambda_base_4_6, 3),
            "lambda_7_9": round(lambda_base_7_9, 3),
        },
        "adjustments":         adjustments,
        "final_expected_runs": round(expected_runs_new, 3),
        "total_delta":         round(delta_vs_baseline, 3),
    }

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
            **weights_cfg,
            "max_phase_adjustment": max_phase_adjustment,
            "max_late_adjustment":  max_late_adjustment,
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
                "lambda_pre_cap": round(lambda_7_9_pre_cap, 3),
                "capped":         capped,
                "bullpen_factor": round(bullpen_factor, 4),
                "lineup_factor":  round(lineup_factor_overall, 4),
                "park_weather":   round(park_weather, 4),
                "reason_codes":   reasons_7_9,
                "breakdown_home": brk_h,
                "breakdown_away": brk_a,
                "traffic_score":            traffic_score,
                "defensive_breakdown_score": defensive_breakdown_score,
                "series_familiarity_score":  series_familiarity_score,
            },
        },
        # Priority 2 — flat breakdown for the UI "Por qué" panel.
        "adjustment_breakdown": adjustment_breakdown,
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
    # Priority 1
    "RC_LATE_LAMBDA_REACTIVE_MODEL_USED",
    "RC_BULLPEN_TRAFFIC_RAISES_LATE_LAMBDA",
    "RC_BULLPEN_DEFENSE_RAISES_LATE_LAMBDA",
    "RC_FATIGUE_RAISES_LATE_LAMBDA",
    "RC_HR_RISK_RAISES_LATE_LAMBDA",
    "RC_LOW_DEFENSIVE_RISK_LIMITS_LATE_RUNS",
    "RC_LATE_LAMBDA_CAPPED",
    # Priority 2
    "RC_PROJECTION_BREAKDOWN_AVAILABLE",
    "RC_STARTER_IMPACT_EXPLAINED",
    "RC_BULLPEN_IMPACT_EXPLAINED",
    "RC_TRAFFIC_IMPACT_EXPLAINED",
    "RC_DEFENSE_IMPACT_EXPLAINED",
    # Priority 3
    "RC_SERIES_FAMILIARITY_DETECTED",
    "RC_RECENT_REPEAT_MATCHUP",
    "RC_SERIES_FAMILIARITY_TRAFFIC_BOOST",
    "RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT",
    "RC_SERIES_FAMILIARITY_CAPPED",
    "ALL_REASON_CODES",
    "compute_mlb_inning_lambdas",
]
