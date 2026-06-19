"""MLB Expected Runs Distribution — Priority 4.

Converts the engine's point estimate ``expected_runs`` into a full
probability distribution so the UI can surface:

  • Mean / median / p10 / p25 / p75 / p90.
  • Under/Over probabilities at the standard MLB lines.
  • A "value line" / "protected line" / "ultra-safe line" suggestion.
  • An uncertainty bucket (LOW / MEDIUM / HIGH).

The module is **pure Python** (no scipy / numpy dependency) — it
implements Poisson and Negative-Binomial PMFs/CDFs iteratively on the
0..50 runs range, which is more than enough for MLB.

Modeling:
    - When a Negative-Binomial dispersion ratio ``r = variance/mean > 1``
      is provided, the model uses NB(n, p) with:
          p = 1 / r
          n = mean / (r - 1)
      This captures over-dispersion driven by bullpen meltdowns, traffic
      spikes, defensive cascades, etc.
    - Otherwise (or when ``r ≤ 1``) we fall back to Poisson(mean).

Uncertainty modulation:
    The base dispersion ratio (from caller) is FURTHER widened/narrowed
    by qualitative signals (fragility, traffic, defense, fatigue, series
    familiarity) capped between 0.9× and 2.5× of the base ratio.

The module is fail-soft: missing inputs → ``available=false`` with a
``DISTRIBUTION_POISSON_FALLBACK`` reason code.

Polarity guarantee:
    The protected-line logic operates ONLY on the market the engine
    already chose (Under stays Under, Over stays Over). It NEVER flips
    polarity — it only suggests safer/looser variants of the same side.
"""
from __future__ import annotations

import math
from typing import Any, Optional

ENGINE_VERSION = "mlb_expected_runs_distribution.1"

# ── Domain ──────────────────────────────────────────────────────────
MAX_K = 50                  # P(X > 50 runs) ≈ 0 for any realistic mean
DEFAULT_LINES = (
    5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5,
)

# Buckets.
BUCKET_LOW    = "LOW"
BUCKET_MEDIUM = "MEDIUM"
BUCKET_HIGH   = "HIGH"

# ── Reason codes ────────────────────────────────────────────────────
RC_DISTRIBUTION_USED                    = "EXPECTED_RUNS_DISTRIBUTION_USED"
RC_NEGATIVE_BINOMIAL_USED               = "DISTRIBUTION_NEGATIVE_BINOMIAL_USED"
RC_POISSON_FALLBACK                     = "DISTRIBUTION_POISSON_FALLBACK"
RC_HIGH_UNCERTAINTY_BULLPEN_TRAFFIC     = "HIGH_UNCERTAINTY_BULLPEN_TRAFFIC"
RC_HIGH_UNCERTAINTY_DEFENSIVE_BREAKDOWN = "HIGH_UNCERTAINTY_DEFENSIVE_BREAKDOWN"
RC_HIGH_UNCERTAINTY_SERIES_FAMILIARITY  = "HIGH_UNCERTAINTY_SERIES_FAMILIARITY"
RC_HIGH_UNCERTAINTY_FRAGILITY           = "HIGH_UNCERTAINTY_FRAGILITY"
RC_LOW_UNCERTAINTY_STABLE_SCRIPT        = "LOW_UNCERTAINTY_STABLE_SCRIPT"
RC_PROTECTED_LINE_RECOMMENDED           = "PROTECTED_LINE_RECOMMENDED"
RC_ULTRA_SAFE_LINE_AVAILABLE            = "ULTRA_SAFE_LINE_AVAILABLE"

# ── Tail Risk Panel reason codes ────────────────────────────────────
RC_TAIL_RISK_PANEL_USED                 = "TAIL_RISK_PANEL_USED"
RC_PURE_PYTHON_PMF_CDF_USED             = "PURE_PYTHON_PMF_CDF_USED"
RC_UNDER_SUPPORTED_BY_MEAN              = "UNDER_SUPPORTED_BY_MEAN"
RC_UNDER_SUPPORTED_BY_LOW_TAIL          = "UNDER_SUPPORTED_BY_LOW_TAIL"
RC_UNDER_MEAN_SUPPORTED_TAIL_FRAGILE    = "UNDER_MEAN_SUPPORTED_TAIL_FRAGILE"
RC_OVER_TAIL_RISK_PRESENT               = "OVER_TAIL_RISK_PRESENT"
RC_EXTREME_TAIL_RISK                    = "EXTREME_TAIL_RISK"
RC_TAIL_RISK_PRESENT                    = "TAIL_RISK_PRESENT"
# Feature 4 — Market interpretation reason codes.
RC_CLEAN_UNDER_PROFILE                  = "CLEAN_UNDER_PROFILE"
RC_MEAN_SUPPORTED_FRAGILE_UNDER         = "MEAN_SUPPORTED_FRAGILE_UNDER"
RC_OVER_LIVES_THROUGH_TAIL              = "OVER_LIVES_THROUGH_TAIL"
RC_PROTECTED_LINE_PREFERRED_DUE_TO_TAIL = "PROTECTED_LINE_PREFERRED_DUE_TO_TAIL"

# ── Tail buckets ────────────────────────────────────────────────────
TAIL_BUCKET_LOW     = "LOW"
TAIL_BUCKET_MEDIUM  = "MEDIUM"
TAIL_BUCKET_HIGH    = "HIGH"
TAIL_BUCKET_EXTREME = "EXTREME"

# ── Under-quality labels ────────────────────────────────────────────
UQ_MEAN_AND_TAIL_SUPPORTED       = "MEAN_AND_TAIL_SUPPORTED"
UQ_MEAN_SUPPORTED_TAIL_FRAGILE   = "MEAN_SUPPORTED_BUT_TAIL_FRAGILE"
UQ_TAIL_DOMINATES                = "TAIL_DOMINATES"
UQ_NOT_SUPPORTED                 = "NOT_SUPPORTED"


# ─────────────────────────────────────────────────────────────────────
# Math primitives
# ─────────────────────────────────────────────────────────────────────
def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _poisson_pmf(k: int, mu: float) -> float:
    """e^{-μ} μ^k / k!, computed in log space for numerical stability."""
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    if k < 0:
        return 0.0
    log_pmf = -mu + k * math.log(mu) - math.lgamma(k + 1)
    return math.exp(log_pmf)


def _poisson_cdf(k: int, mu: float) -> float:
    """P(X <= k) for X ~ Poisson(μ)."""
    if k < 0:
        return 0.0
    total = 0.0
    for i in range(k + 1):
        total += _poisson_pmf(i, mu)
    return min(1.0, total)


def _nb_pmf(k: int, n: float, p: float) -> float:
    """Negative-Binomial PMF using gamma for non-integer ``n``.

        P(X=k) = Γ(k+n) / (Γ(n) · k!) · p^n · (1-p)^k

    Stable in log space.
    """
    if k < 0 or n <= 0 or not (0.0 < p < 1.0):
        return 0.0
    log_pmf = (
        math.lgamma(k + n) - math.lgamma(n) - math.lgamma(k + 1)
        + n * math.log(p) + k * math.log(1.0 - p)
    )
    return math.exp(log_pmf)


def _nb_cdf(k: int, n: float, p: float) -> float:
    if k < 0:
        return 0.0
    total = 0.0
    for i in range(k + 1):
        total += _nb_pmf(i, n, p)
    return min(1.0, total)


def _distribution_quantile(cdf_values: list[float], q: float) -> float:
    """Inverse CDF on the integer grid 0..MAX_K. Returns the smallest k
    such that CDF(k) >= q. Returns ``MAX_K`` when q is unreachable."""
    for k, cv in enumerate(cdf_values):
        if cv >= q:
            return float(k)
    return float(MAX_K)


def _distribution_median(cdf_values: list[float]) -> float:
    return _distribution_quantile(cdf_values, 0.5)


# ─────────────────────────────────────────────────────────────────────
# Uncertainty modulation
# ─────────────────────────────────────────────────────────────────────
def _compute_effective_dispersion(
    base_ratio: Optional[float],
    *,
    fragility_score: Optional[float],
    script_survival: Optional[float],
    traffic_score: Optional[float],
    defensive_breakdown_score: Optional[float],
    bullpen_fatigue: Optional[float],
    series_familiarity_score: Optional[float],
    overlay_dispersion_multiplier: Optional[float] = None,
    overlay_verdict: Optional[str] = None,
) -> tuple[float, list[str]]:
    """Apply qualitative signals to widen/narrow the dispersion ratio.

    Returns ``(effective_ratio, reason_codes)``. The effective ratio is
    clamped to [0.90, 3.00] so the distribution never collapses to a
    delta nor explodes to nonsense widths.

    D12 hook: `overlay_dispersion_multiplier` (from
    `mlb_total_risk_overlay.compute_total_risk_overlay`) is applied
    last when the overlay verdict is `AVOID` or `BLOCK`. This ensures
    the tail-risk recalibration only kicks in for Under picks that
    triggered the high-risk gate.
    """
    reasons: list[str] = []
    # Base ratio: Poisson if missing.
    r = _safe_float(base_ratio) or 1.0
    if r <= 0:
        r = 1.0

    # Widen on high-risk signals.
    if (fragility_score is not None
        and _safe_float(fragility_score) is not None
        and _safe_float(fragility_score) >= 65):
        r *= 1.20
        reasons.append(RC_HIGH_UNCERTAINTY_FRAGILITY)

    if (traffic_score is not None
        and _safe_float(traffic_score) is not None
        and _safe_float(traffic_score) >= 65):
        r *= 1.15
        reasons.append(RC_HIGH_UNCERTAINTY_BULLPEN_TRAFFIC)

    if (defensive_breakdown_score is not None
        and _safe_float(defensive_breakdown_score) is not None
        and _safe_float(defensive_breakdown_score) >= 60):
        r *= 1.10
        reasons.append(RC_HIGH_UNCERTAINTY_DEFENSIVE_BREAKDOWN)

    if (bullpen_fatigue is not None
        and _safe_float(bullpen_fatigue) is not None
        and _safe_float(bullpen_fatigue) >= 0.65):
        r *= 1.10

    if (series_familiarity_score is not None
        and _safe_float(series_familiarity_score) is not None
        and _safe_float(series_familiarity_score) >= 40):
        r *= 1.07
        reasons.append(RC_HIGH_UNCERTAINTY_SERIES_FAMILIARITY)

    # Narrow on stable signals.
    if (script_survival is not None
        and _safe_float(script_survival) is not None
        and _safe_float(script_survival) >= 0.75):
        r *= 0.92
        reasons.append(RC_LOW_UNCERTAINTY_STABLE_SCRIPT)

    # ── D12 — Total Risk Overlay recalibration ─────────────────────
    # Only apply when the overlay verdict gated the pick (AVOID/BLOCK).
    # `overlay_dispersion_multiplier` is the dispersion bump proposed
    # by `mlb_total_risk_overlay.compute_total_risk_overlay`:
    #   1.0 LOW · 1.15 MEDIUM · 1.35 HIGH · 1.80 EXTREME.
    mult = _safe_float(overlay_dispersion_multiplier)
    verdict = (overlay_verdict or "").upper()
    if mult is not None and mult > 1.0 and verdict in ("AVOID", "BLOCK"):
        r *= float(mult)
        reasons.append("UNDER_TAIL_RISK_RECALIBRATED")

    r = _clamp(r, 0.90, 3.00)
    return r, reasons


def _bucket_from_ratio(r: float) -> str:
    if r >= 1.55:
        return BUCKET_HIGH
    if r >= 1.15:
        return BUCKET_MEDIUM
    return BUCKET_LOW


# ─────────────────────────────────────────────────────────────────────
# Protected line logic
# ─────────────────────────────────────────────────────────────────────
def _select_value_line(
    *, mean: float, side: str, cdf_at: dict[float, float]
) -> Optional[float]:
    """Choose the closest standard MLB line where the projected side has
    positive edge vs the implied 50% baseline (P(side) > 0.50). Returns
    the line value or None if none qualifies.

    For Under: side == "under" → P(X ≤ line) > 0.50 (line rounded down).
    For Over : side == "over"  → P(X >= line+1) ≥ 0.50.
    """
    if side == "under":
        best: Optional[tuple[float, float]] = None  # (edge_above_0.5, line)
        for line in DEFAULT_LINES:
            p = cdf_at.get(line, 0.0)
            edge = p - 0.5
            if edge > 0 and (best is None or edge < best[0]):
                # We want the CLOSEST line above 50% (smallest positive edge).
                best = (edge, line)
        return best[1] if best else None
    if side == "over":
        # P(over X.5) = 1 - CDF(floor(X.5)) = 1 - CDF(X)
        # We pick the closest line with P(Over) > 0.5.
        best = None
        for line in DEFAULT_LINES:
            p_under = cdf_at.get(line, 0.0)
            p_over = 1.0 - p_under
            edge = p_over - 0.5
            if edge > 0 and (best is None or edge < best[0]):
                best = (edge, line)
        return best[1] if best else None
    return None


def _protected_lines_for_side(
    *, value_line: Optional[float], side: str
) -> dict:
    """Return the protected / ultra-safe variants of the chosen side."""
    if value_line is None:
        return {"value_line": None, "protected_line": None, "ultra_safe_line": None}
    if side == "under":
        return {
            "value_line":      f"Under {value_line}",
            "protected_line":  f"Under {value_line + 1.0}",   # +1 full run safer
            "ultra_safe_line": f"Under {value_line + 2.0}",
        }
    if side == "over":
        return {
            "value_line":      f"Over {value_line}",
            "protected_line":  f"Over {max(0.5, value_line - 1.0)}",
            "ultra_safe_line": f"Over {max(0.5, value_line - 2.0)}",
        }
    return {"value_line": None, "protected_line": None, "ultra_safe_line": None}


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def _unavailable(reason: str) -> dict:
    return {
        "available":       False,
        "engine_version":  ENGINE_VERSION,
        "reason":          reason,
        "reason_codes":    [RC_POISSON_FALLBACK],
    }


def compute_expected_runs_distribution(
    *,
    expected_runs:             Optional[float],
    inning_lambda_projection:  Optional[dict] = None,
    market:                    Optional[str] = None,   # e.g. "total_runs_under" / "over_8_5"
    market_line:               Optional[float] = None,
    nb_dispersion_ratio:       Optional[float] = None,
    fragility_score:           Optional[float] = None,
    script_survival:           Optional[float] = None,
    traffic_score:             Optional[float] = None,
    defensive_breakdown_score: Optional[float] = None,
    bullpen_fatigue:           Optional[float] = None,
    series_familiarity_score:  Optional[float] = None,
    park_factor:               Optional[float] = None,
    weather_factor:            Optional[float] = None,
    line_learning_feedback:    Optional[dict] = None,
    overlay_dispersion_multiplier: Optional[float] = None,
    overlay_verdict:           Optional[str] = None,
) -> dict:
    """Build the full expected-runs distribution payload.

    Returns the contract documented at the module head. Fail-soft on any
    missing mean: returns ``available=false`` with a Poisson fallback
    reason code.
    """
    # If the inning-lambda projection is available and richer than the
    # caller's expected_runs, prefer it (consistent with the rest of the
    # engine).
    mean = _safe_float(expected_runs)
    if (mean is None or mean <= 0) and inning_lambda_projection:
        mean = _safe_float(inning_lambda_projection.get("expected_runs"))
    if mean is None or mean <= 0:
        return _unavailable("missing_or_invalid_expected_runs")

    # Apply qualitative modulation to the dispersion ratio.
    effective_ratio, modulation_reasons = _compute_effective_dispersion(
        nb_dispersion_ratio,
        fragility_score=fragility_score,
        script_survival=script_survival,
        traffic_score=traffic_score,
        defensive_breakdown_score=defensive_breakdown_score,
        bullpen_fatigue=bullpen_fatigue,
        series_familiarity_score=series_familiarity_score,
    )

    # Choose distribution.
    distribution_label = "poisson"
    reason_codes: list[str] = [RC_DISTRIBUTION_USED]

    pmf: list[float]
    if effective_ratio > 1.001:
        # Negative Binomial parameterized via mean + dispersion.
        # variance = mean * effective_ratio
        # p = mean / variance = 1 / effective_ratio
        # n = mean^2 / (variance - mean) = mean / (effective_ratio - 1)
        p = 1.0 / effective_ratio
        n = mean / (effective_ratio - 1.0)
        pmf = [_nb_pmf(k, n, p) for k in range(MAX_K + 1)]
        distribution_label = "negative_binomial"
        reason_codes.append(RC_NEGATIVE_BINOMIAL_USED)
    else:
        pmf = [_poisson_pmf(k, mean) for k in range(MAX_K + 1)]
        reason_codes.append(RC_POISSON_FALLBACK)

    # Build CDF.
    cdf: list[float] = []
    cum = 0.0
    for v in pmf:
        cum = min(1.0, cum + v)
        cdf.append(cum)

    # Variance / std from PMF (for transparency).
    actual_variance = sum((k - mean) ** 2 * pmf[k] for k in range(len(pmf)))
    std = math.sqrt(max(0.0, actual_variance))

    # Quantiles.
    median = _distribution_median(cdf)
    p10 = _distribution_quantile(cdf, 0.10)
    p25 = _distribution_quantile(cdf, 0.25)
    p75 = _distribution_quantile(cdf, 0.75)
    p90 = _distribution_quantile(cdf, 0.90)

    # Probabilities at fixed standard lines. P(Under X.5) = CDF(X);
    # P(Over X.5) = 1 - CDF(X).
    probabilities: dict[str, float] = {}
    cdf_at: dict[float, float] = {}
    for line in DEFAULT_LINES:
        k_floor = int(math.floor(line))
        c = cdf[k_floor] if k_floor < len(cdf) else 1.0
        cdf_at[line] = c
        probabilities[f"under_{str(line).replace('.', '_')}"] = round(c, 4)
        probabilities[f"over_{str(line).replace('.', '_')}"]  = round(1.0 - c, 4)

    # Determine the engine's chosen side.
    side: Optional[str] = None
    m_lower = (market or "").lower()
    if "under" in m_lower:
        side = "under"
    elif "over" in m_lower:
        side = "over"

    # Anchor the value line on the user-provided market_line when the
    # market is explicit (so we recommend AROUND that line). Otherwise
    # pick the closest line where the engine's side has positive edge.
    value_line: Optional[float] = None
    if market_line is not None and side is not None:
        ml = _safe_float(market_line)
        if ml is not None:
            value_line = ml
    if value_line is None and side is not None:
        value_line = _select_value_line(mean=mean, side=side, cdf_at=cdf_at)

    protected_lines = _protected_lines_for_side(
        value_line=value_line, side=side or "",
    )
    if protected_lines.get("protected_line"):
        reason_codes.append(RC_PROTECTED_LINE_RECOMMENDED)
    if protected_lines.get("ultra_safe_line"):
        reason_codes.append(RC_ULTRA_SAFE_LINE_AVAILABLE)

    # Build the human-readable explanation in Spanish.
    explanation_es: Optional[str] = None
    if value_line is not None and side:
        verbose_side = "Under" if side == "under" else "Over"
        # Find the probability of the SAFER protected line for the same side.
        if side == "under":
            try:
                k_floor_p = int(math.floor(value_line + 1.0))
                p_protected = cdf[k_floor_p] if k_floor_p < len(cdf) else 1.0
                k_floor_v = int(math.floor(value_line))
                p_value = cdf[k_floor_v] if k_floor_v < len(cdf) else 1.0
                explanation_es = (
                    f"{verbose_side} {value_line} tiene {round(p_value*100)}%, "
                    f"pero {verbose_side} {value_line + 1.0} sube a "
                    f"{round(p_protected*100)}% y reduce el riesgo de una carrera."
                )
            except Exception:
                pass
        else:
            try:
                safer = max(0.5, value_line - 1.0)
                k_floor_p = int(math.floor(safer))
                p_protected_under = cdf[k_floor_p] if k_floor_p < len(cdf) else 1.0
                p_protected = 1.0 - p_protected_under
                k_floor_v = int(math.floor(value_line))
                p_value_under = cdf[k_floor_v] if k_floor_v < len(cdf) else 1.0
                p_value = 1.0 - p_value_under
                explanation_es = (
                    f"{verbose_side} {value_line} tiene {round(p_value*100)}%, "
                    f"pero {verbose_side} {safer} sube a "
                    f"{round(p_protected*100)}% y reduce el riesgo de una carrera."
                )
            except Exception:
                pass

    bucket = _bucket_from_ratio(effective_ratio)
    reason_codes.extend(modulation_reasons)
    # Dedupe while preserving order.
    reason_codes = list(dict.fromkeys(reason_codes))

    return {
        "available":       True,
        "engine_version":  ENGINE_VERSION,
        "distribution":    distribution_label,
        "mean":            round(mean, 3),
        "median":          round(median, 2),
        "std":             round(std, 3),
        "variance":        round(actual_variance, 3),
        "p10":             round(p10, 2),
        "p25":             round(p25, 2),
        "p75":             round(p75, 2),
        "p90":             round(p90, 2),
        "effective_dispersion_ratio": round(effective_ratio, 4),
        "uncertainty_bucket":  bucket,
        "probabilities":       probabilities,
        "protected_lines":     protected_lines,
        "side":                side,
        "explanation_es":      explanation_es,
        "reason_codes":        reason_codes,
        # Echo a tight feature snapshot for downstream auditing.
        "inputs": {
            "expected_runs":             round(mean, 3),
            "nb_dispersion_ratio":       _safe_float(nb_dispersion_ratio),
            "fragility_score":           _safe_float(fragility_score),
            "script_survival":           _safe_float(script_survival),
            "traffic_score":             _safe_float(traffic_score),
            "defensive_breakdown_score": _safe_float(defensive_breakdown_score),
            "bullpen_fatigue":           _safe_float(bullpen_fatigue),
            "series_familiarity_score":  _safe_float(series_familiarity_score),
            "park_factor":               _safe_float(park_factor),
            "weather_factor":            _safe_float(weather_factor),
            "market_line":               _safe_float(market_line),
            "market":                    market,
        },
    }


__all__ = [
    "ENGINE_VERSION",
    "MAX_K",
    "DEFAULT_LINES",
    "BUCKET_LOW",
    "BUCKET_MEDIUM",
    "BUCKET_HIGH",
    "TAIL_BUCKET_LOW",
    "TAIL_BUCKET_MEDIUM",
    "TAIL_BUCKET_HIGH",
    "TAIL_BUCKET_EXTREME",
    "UQ_MEAN_AND_TAIL_SUPPORTED",
    "UQ_MEAN_SUPPORTED_TAIL_FRAGILE",
    "UQ_TAIL_DOMINATES",
    "UQ_NOT_SUPPORTED",
    "RC_DISTRIBUTION_USED",
    "RC_NEGATIVE_BINOMIAL_USED",
    "RC_POISSON_FALLBACK",
    "RC_HIGH_UNCERTAINTY_BULLPEN_TRAFFIC",
    "RC_HIGH_UNCERTAINTY_DEFENSIVE_BREAKDOWN",
    "RC_HIGH_UNCERTAINTY_SERIES_FAMILIARITY",
    "RC_HIGH_UNCERTAINTY_FRAGILITY",
    "RC_LOW_UNCERTAINTY_STABLE_SCRIPT",
    "RC_PROTECTED_LINE_RECOMMENDED",
    "RC_ULTRA_SAFE_LINE_AVAILABLE",
    "RC_TAIL_RISK_PANEL_USED",
    "RC_PURE_PYTHON_PMF_CDF_USED",
    "RC_UNDER_SUPPORTED_BY_MEAN",
    "RC_UNDER_SUPPORTED_BY_LOW_TAIL",
    "RC_UNDER_MEAN_SUPPORTED_TAIL_FRAGILE",
    "RC_OVER_TAIL_RISK_PRESENT",
    "RC_EXTREME_TAIL_RISK",
    "RC_TAIL_RISK_PRESENT",
    "RC_CLEAN_UNDER_PROFILE",
    "RC_MEAN_SUPPORTED_FRAGILE_UNDER",
    "RC_OVER_LIVES_THROUGH_TAIL",
    "RC_PROTECTED_LINE_PREFERRED_DUE_TO_TAIL",
    "compute_expected_runs_distribution",
    "compute_tail_risk",
    "interpret_market_profile",
]


# ─────────────────────────────────────────────────────────────────────
# Tail Risk Panel — Feature 1
# ─────────────────────────────────────────────────────────────────────
def _tail_bucket_from(p_ge_12: float, p_ge_14: float, p_ge_16: float) -> str:
    """Categorical bucket for the explosive tail probability.

    EXTREME: p_ge_14 > 15% OR p_ge_16 > 8% (catastrophic blow-ups).
    HIGH:    p_ge_12 > 22%.
    MEDIUM:  p_ge_12 in [12%, 22%].
    LOW:     p_ge_12 < 12%.
    """
    if p_ge_14 > 0.15 or p_ge_16 > 0.08:
        return TAIL_BUCKET_EXTREME
    if p_ge_12 > 0.22:
        return TAIL_BUCKET_HIGH
    if p_ge_12 >= 0.12:
        return TAIL_BUCKET_MEDIUM
    return TAIL_BUCKET_LOW


def _tail_risk_score(p_ge_12: float, p_ge_14: float, p_ge_16: float) -> int:
    """Composite 0-100 tail score weighted toward catastrophic outcomes.

    Roughly: 60 pts from p_ge_12 (saturates at 30%), 30 pts from p_ge_14
    (saturates at 15%), 10 pts from p_ge_16 (saturates at 8%). Pre-clamp.
    """
    s_12 = min(60.0, (p_ge_12 / 0.30) * 60.0)
    s_14 = min(30.0, (p_ge_14 / 0.15) * 30.0)
    s_16 = min(10.0, (p_ge_16 / 0.08) * 10.0)
    return int(round(min(100.0, s_12 + s_14 + s_16)))


def _classify_under_quality(
    under_prob: float,
    p_ge_12: float,
    tail_bucket: str,
) -> str:
    """Distinguishes WHY an Under works (mean vs. low tail) so the UI
    can communicate "clean under" vs "fragile under"."""
    if under_prob < 0.55:
        return UQ_NOT_SUPPORTED
    if tail_bucket == TAIL_BUCKET_LOW and p_ge_12 < 0.12:
        return UQ_MEAN_AND_TAIL_SUPPORTED
    if tail_bucket in (TAIL_BUCKET_HIGH, TAIL_BUCKET_EXTREME):
        return UQ_TAIL_DOMINATES
    return UQ_MEAN_SUPPORTED_TAIL_FRAGILE


def compute_tail_risk(
    *,
    distribution_payload: dict,
    market_line: Optional[float] = None,
    market_side: Optional[str] = None,   # "under" | "over" | None
) -> dict:
    """Extract Tail Risk Panel from an existing distribution payload.

    Reuses the same Python-only PMF/CDF computed in
    ``compute_expected_runs_distribution`` — no scipy needed.

    Args:
        distribution_payload: output of compute_expected_runs_distribution.
        market_line: line being evaluated. When omitted, falls back to
            the ``market_line`` echoed inside ``inputs``.
        market_side: "under" or "over" (engine's chosen side).

    Returns the tail_risk dict documented in the spec, always including
    P(>=12), P(>=14), P(>=16), tail_bucket, under_quality, reason_codes.
    Fail-soft: when the input is unavailable, returns
    ``{"available": False, "reason": "..."}``.
    """
    if not distribution_payload or not distribution_payload.get("available"):
        return {
            "available":    False,
            "reason":       "distribution_unavailable",
            "reason_codes": [RC_TAIL_RISK_PANEL_USED],
        }

    # Recover the PMF from the source mean + dispersion. The original
    # function already exposes ``probabilities`` (per-line CDF/over),
    # but to compute P(X>=k) for arbitrary k we need the underlying
    # CDF. Reconstruct it deterministically from the cached params.
    mean = _safe_float(distribution_payload.get("mean")) or 0.0
    effective_ratio = _safe_float(
        distribution_payload.get("effective_dispersion_ratio")
    ) or 1.0
    distribution_label = distribution_payload.get("distribution") or "poisson"

    if distribution_label == "negative_binomial" and effective_ratio > 1.001:
        p_nb = 1.0 / effective_ratio
        n_nb = mean / (effective_ratio - 1.0)
        pmf = [_nb_pmf(k, n_nb, p_nb) for k in range(MAX_K + 1)]
    else:
        pmf = [_poisson_pmf(k, mean) for k in range(MAX_K + 1)]
    cdf: list[float] = []
    cum = 0.0
    for v in pmf:
        cum = min(1.0, cum + v)
        cdf.append(cum)

    # Market-line probabilities (when provided).
    ml = _safe_float(market_line)
    if ml is None:
        ml = _safe_float(
            (distribution_payload.get("inputs") or {}).get("market_line")
        )
    under_prob: Optional[float] = None
    over_prob:  Optional[float] = None
    if ml is not None:
        k_floor = int(math.floor(ml))
        c = cdf[k_floor] if k_floor < len(cdf) else 1.0
        under_prob = round(c, 4)
        over_prob  = round(1.0 - c, 4)

    # P(X >= K) tail probabilities.
    def _p_ge(k_target: int) -> float:
        # P(X >= k) = 1 - CDF(k-1). For k=12 → 1 - CDF(11).
        idx = max(0, k_target - 1)
        return round(1.0 - (cdf[idx] if idx < len(cdf) else 1.0), 4)

    p_ge_12 = _p_ge(12)
    p_ge_14 = _p_ge(14)
    p_ge_16 = _p_ge(16)
    p_ge_18 = _p_ge(18)

    tail_bucket   = _tail_bucket_from(p_ge_12, p_ge_14, p_ge_16)
    tail_score    = _tail_risk_score(p_ge_12, p_ge_14, p_ge_16)

    # Side resolution: prefer the explicit argument, else use the side
    # already recorded by the distribution payload.
    side = (market_side or distribution_payload.get("side") or "").lower()

    under_quality: Optional[str] = None
    if side == "under" and under_prob is not None:
        under_quality = _classify_under_quality(
            under_prob=under_prob, p_ge_12=p_ge_12, tail_bucket=tail_bucket,
        )

    # ── Reason codes ─────────────────────────────────────────────────
    reason_codes: list[str] = [
        RC_TAIL_RISK_PANEL_USED,
        RC_PURE_PYTHON_PMF_CDF_USED,
    ]

    # Tail bucket emits at least the matching code.
    if tail_bucket == TAIL_BUCKET_EXTREME:
        reason_codes.append(RC_EXTREME_TAIL_RISK)
    if tail_bucket in (TAIL_BUCKET_MEDIUM, TAIL_BUCKET_HIGH, TAIL_BUCKET_EXTREME):
        reason_codes.append(RC_TAIL_RISK_PRESENT)
        reason_codes.append(RC_OVER_TAIL_RISK_PRESENT)

    # Under-side semantics.
    if side == "under" and under_prob is not None:
        if under_prob >= 0.55:
            reason_codes.append(RC_UNDER_SUPPORTED_BY_MEAN)
        if tail_bucket == TAIL_BUCKET_LOW:
            reason_codes.append(RC_UNDER_SUPPORTED_BY_LOW_TAIL)
        if under_quality == UQ_MEAN_SUPPORTED_TAIL_FRAGILE:
            reason_codes.append(RC_UNDER_MEAN_SUPPORTED_TAIL_FRAGILE)

    # Build a short Spanish interpretation for the UI.
    interpretation_es = _under_interpretation_es(
        side=side, under_prob=under_prob, p_ge_12=p_ge_12,
        p_ge_14=p_ge_14, tail_bucket=tail_bucket,
        under_quality=under_quality,
    )

    reason_codes = list(dict.fromkeys(reason_codes))

    return {
        "available":        True,
        "engine_version":   ENGINE_VERSION,
        "market_line":      ml,
        "mean":             round(mean, 3),
        "under_probability": under_prob,
        "over_probability":  over_prob,
        "p_ge_12":          p_ge_12,
        "p_ge_14":          p_ge_14,
        "p_ge_16":          p_ge_16,
        "p_ge_18":          p_ge_18,
        "tail_bucket":      tail_bucket,
        "tail_risk_score":  tail_score,
        "under_quality":    under_quality,
        "interpretation_es": interpretation_es,
        "side":             side or None,
        "reason_codes":     reason_codes,
    }


def _under_interpretation_es(
    *, side: str, under_prob: Optional[float],
    p_ge_12: float, p_ge_14: float, tail_bucket: str,
    under_quality: Optional[str],
) -> Optional[str]:
    """Plain-Spanish 1-line interpretation surfaced to the UI."""
    if side == "under" and under_prob is not None:
        if under_quality == UQ_MEAN_AND_TAIL_SUPPORTED:
            return (
                "Under limpio: la media y la cola explosiva apoyan "
                "la misma dirección."
            )
        if under_quality == UQ_MEAN_SUPPORTED_TAIL_FRAGILE:
            return (
                "El Under tiene valor por media proyectada baja, pero "
                "no es un Under limpio: hay rutas alternativas al Over."
            )
        if under_quality == UQ_TAIL_DOMINATES:
            return (
                "El Under está respaldado por la media, pero la cola "
                "explosiva es relevante: considera línea protegida."
            )
        if under_quality == UQ_NOT_SUPPORTED:
            return "El Under no está respaldado por la media en esta línea."
    if side == "over":
        if tail_bucket in (TAIL_BUCKET_HIGH, TAIL_BUCKET_EXTREME):
            return (
                "El Over no domina por media, pero vive por cola "
                "explosiva."
            )
    return None


def interpret_market_profile(
    *,
    distribution_payload: dict,
    tail_risk_payload: dict,
) -> dict:
    """Feature 4 — Combine distribution + tail_risk into a single
    market-interpretation header for the UI.

    Returns:
        {
            "headline_es": str | None,
            "profile":     "CLEAN_UNDER" | "MEAN_SUPPORTED_FRAGILE_UNDER"
                            | "OVER_LIVES_THROUGH_TAIL" | "NEUTRAL",
            "reason_codes": [...],
        }
    """
    reason_codes: list[str] = []
    if not tail_risk_payload or not tail_risk_payload.get("available"):
        return {"headline_es": None, "profile": "NEUTRAL", "reason_codes": []}

    side = (tail_risk_payload.get("side") or "").lower()
    uq   = tail_risk_payload.get("under_quality")
    tb   = tail_risk_payload.get("tail_bucket")
    headline = tail_risk_payload.get("interpretation_es")

    profile = "NEUTRAL"
    if side == "under":
        if uq == UQ_MEAN_AND_TAIL_SUPPORTED:
            profile = "CLEAN_UNDER"
            reason_codes.append(RC_CLEAN_UNDER_PROFILE)
        elif uq == UQ_MEAN_SUPPORTED_TAIL_FRAGILE:
            profile = "MEAN_SUPPORTED_FRAGILE_UNDER"
            reason_codes.append(RC_MEAN_SUPPORTED_FRAGILE_UNDER)
            reason_codes.append(RC_PROTECTED_LINE_PREFERRED_DUE_TO_TAIL)
        elif uq == UQ_TAIL_DOMINATES:
            profile = "MEAN_SUPPORTED_FRAGILE_UNDER"
            reason_codes.append(RC_MEAN_SUPPORTED_FRAGILE_UNDER)
            reason_codes.append(RC_PROTECTED_LINE_PREFERRED_DUE_TO_TAIL)
    elif side == "over" and tb in (TAIL_BUCKET_HIGH, TAIL_BUCKET_EXTREME):
        profile = "OVER_LIVES_THROUGH_TAIL"
        reason_codes.append(RC_OVER_LIVES_THROUGH_TAIL)

    return {
        "headline_es":  headline,
        "profile":      profile,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }
