"""
Sprint NIVEL 3 — Bloque 1 · Dynamic Run Distribution Mixer (pure module).

Capa adicional al motor MLB Totals: estima la **probabilidad real** de
superar líneas O/U específicas (6.5, 7.5, 8.5, …, 14.5) mezclando
dinámicamente distribuciones Poisson y Negative Binomial según el
contexto del partido (volatilidad starter, explosividad lineup,
estrés bullpen, riesgo dominó, etc.).

**No reemplaza** `mlb_expected_runs_distribution.py` — corre en
paralelo y publica su payload en `pick_payload["run_distribution_mixer"]`
como capa observe-only auditable.

Entry-point
-----------
    build_dynamic_run_distribution(context: dict) -> dict

Output (siempre dict, nunca raise):
    {
      "available":         bool,
      "lambda":            float,
      "distribution_family": "POISSON" | "NEGATIVE_BINOMIAL" | "MIXTURE",
      "mixture_weights":   {"poisson": w_p, "negative_binomial": w_nb},
      "dispersion":        float,
      "probabilities":     {"over_6_5": ..., "under_6_5": ..., …},
      "percentiles":       {"p10": int, …, "p99": int},
      "tail_risk": {
          "score":     float 0..100,
          "bucket":    "LOW" | "MEDIUM" | "HIGH" | "EXTREME",
          "drivers":   [str],
      },
      "reason_codes":      [str],
      "debug":             {…}  (only when context.debug=True),
    }

Design rules
------------
  * Pure: no I/O, no globals, no time. Deterministic.
  * Fail-soft: any unexpected input is logged into `reason_codes` and
    a NEUTRAL Poisson distribution is returned — never raises.
  * Observe-only: the mixer NEVER mutates the baseline distribution or
    the pick.

Selection heuristic
-------------------
A volatility score 0..100 is computed from the sub-blocks. Decision:
  * score ≤ 25 → POISSON (low-variance regime).
  * score ≥ 65 → NEGATIVE_BINOMIAL (high-variance regime).
  * 25 < score < 65 → MIXTURE w(NB) = (score - 25) / 40.

Tail risk
---------
Computed independently from the mixture's right-tail mass (P(X > p90 + 1)).

Probabilities
-------------
The supported lines for keys are .5 increments from 6.5 to 14.5. The
returned `probabilities` dict carries BOTH the over and under keys so
consumers can read either side directly.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# ── Constants ─────────────────────────────────────────────────────────
SUPPORTED_LINES = (6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 14.5)
PERCENTILE_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)

FAMILY_POISSON = "POISSON"
FAMILY_NB      = "NEGATIVE_BINOMIAL"
FAMILY_MIXTURE = "MIXTURE"

TAIL_LOW     = "LOW"
TAIL_MEDIUM  = "MEDIUM"
TAIL_HIGH    = "HIGH"
TAIL_EXTREME = "EXTREME"

# NIVEL 3 — Bloque 2 (§1): explicit weight formula per spec.
#   nb_weight = clamp((risk_score - 30) / 50, 0.0, 0.90)
# Selection rule kept as backward-compatible buckets:
#   * nb_weight <= 0.05  → POISSON   (low-variance regime).
#   * nb_weight >= 0.85  → NB        (high-variance regime).
#   * otherwise           → MIXTURE.
NB_WEIGHT_OFFSET     = 30.0
NB_WEIGHT_SCALE      = 50.0
NB_WEIGHT_MAX        = 0.90
NB_WEIGHT_POISSON_TH = 0.05
NB_WEIGHT_NB_TH      = 0.85

# Legacy thresholds (kept for back-compat with consumers reading
# `volatility_score`; not used for selection anymore).
VOLATILITY_POISSON_MAX = 25.0
VOLATILITY_NB_MIN      = 65.0

# Dispersion guardrails.
MIN_DISPERSION    = 1.00   # Poisson lower bound.
MAX_DISPERSION    = 3.00   # cap to avoid absurd tails.
BASELINE_NB_DISP  = 1.30   # default NB variance/mean ratio.

# PMF truncation point (we evaluate X in {0, 1, …, MAX_RUNS}).
MAX_RUNS = 40


# ─────────────────────────────────────────────────────────────────────
# Helpers — safe getters & numerics
# ─────────────────────────────────────────────────────────────────────
def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        f = float(value)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_side(block: Any, side: str, *keys: str) -> Optional[float]:
    """Navigate block[side][k1][k2]… and return numeric or None."""
    if not isinstance(block, dict):
        return None
    side_block = block.get(side)
    if side_block is None:
        # Maybe the block IS already the side-level dict.
        side_block = block
    if not isinstance(side_block, dict):
        return None
    cur: Any = side_block
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
    if cur is None:
        return None
    try:
        f = float(cur)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _peak_score(block: Any, *keys: str) -> Optional[float]:
    """For two-sided blocks (home/away), return the higher score."""
    h = _get_side(block, "home", *keys)
    a = _get_side(block, "away", *keys)
    if h is None and a is None:
        return None
    return max(h or 0.0, a or 0.0)


# ─────────────────────────────────────────────────────────────────────
# Volatility scoring
# ─────────────────────────────────────────────────────────────────────
def _compute_volatility_score(context: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Returns (risk_score 0..100, drivers list).

    Per NIVEL 3 — Bloque 2 §1 spec:
        risk_score = weighted_average of the 6 peak(home, away) signals:
            - starter_volatility
            - first_inning_collapse
            - lineup_explosiveness
            - recent_offense  (categorical bucket → numeric mapping)
            - bullpen_stress
            - domino_risk

    Weights are equal across pillars (per spec "weighted_average" —
    we treat as uniform mean of the available pillars, with missing
    pillars excluded from the denominator so partial-data contexts
    don't get artificially deflated).
    """
    drivers: List[str] = []

    # ── Peak per pillar ─────────────────────────────────────────────
    def _peak_or_none(*keys_chain: Tuple[Any, str]) -> Optional[float]:
        """Try several lookups, return first non-None numeric."""
        return None

    # Starter volatility (two-sided block + per-side fallback).
    sv = _peak_score(context.get("starter_volatility"),
                       "starter_volatility_score")
    if sv is None:
        sv_h = _get_side(context.get("starter_volatility_home"), "home",
                          "starter_volatility_score")
        sv_a = _get_side(context.get("starter_volatility_away"), "away",
                          "starter_volatility_score")
        sv = max(sv_h or 0.0, sv_a or 0.0) if (sv_h is not None or sv_a is not None) else None

    # First inning collapse.
    fi = _peak_score(context.get("first_inning_collapse"),
                       "first_inning_collapse_score")
    if fi is None:
        fi_h = _get_side(context.get("first_inning_collapse_home"), "home",
                          "first_inning_collapse_score")
        fi_a = _get_side(context.get("first_inning_collapse_away"), "away",
                          "first_inning_collapse_score")
        fi = max(fi_h or 0.0, fi_a or 0.0) if (fi_h is not None or fi_a is not None) else None

    # Lineup explosiveness.
    le = _peak_score(context.get("lineup_explosiveness"),
                       "lineup_explosiveness_score")
    if le is None:
        le_h = _get_side(context.get("lineup_explosiveness_home"), "home",
                          "lineup_explosiveness_score")
        le_a = _get_side(context.get("lineup_explosiveness_away"), "away",
                          "lineup_explosiveness_score")
        le = max(le_h or 0.0, le_a or 0.0) if (le_h is not None or le_a is not None) else None

    # Recent offense: map categorical bucket → numeric (COLD=0,
    # NEUTRAL=30, HOT=70, EXPLOSIVE=95).
    ro_h = (context.get("recent_offense_home") or {}).get("bucket") \
                if isinstance(context.get("recent_offense_home"), dict) else None
    ro_a = (context.get("recent_offense_away") or {}).get("bucket") \
                if isinstance(context.get("recent_offense_away"), dict) else None
    _bucket_to_score = {"COLD": 0.0, "NEUTRAL": 30.0, "HOT": 70.0, "EXPLOSIVE": 95.0}
    ro_peak = None
    for b in (ro_h, ro_a):
        if b in _bucket_to_score:
            v = _bucket_to_score[b]
            ro_peak = v if ro_peak is None else max(ro_peak, v)
    if ro_h == "EXPLOSIVE" or ro_a == "EXPLOSIVE":
        drivers.append("BOTH_OFFENSES_HOT")

    # Bullpen stress.
    bs = _peak_score(context.get("bullpen_stress"),
                       "bullpen_stress_score")
    if bs is None:
        bs_h = _get_side(context.get("bullpen_stress_home"), "home",
                          "bullpen_stress_score")
        bs_a = _get_side(context.get("bullpen_stress_away"), "away",
                          "bullpen_stress_score")
        bs = max(bs_h or 0.0, bs_a or 0.0) if (bs_h is not None or bs_a is not None) else None

    # Domino risk.
    dr = _peak_score(context.get("domino_risk"),
                       "domino_risk_score")
    if dr is None:
        dr_h = _get_side(context.get("domino_risk_home"), "home",
                          "domino_risk_score")
        dr_a = _get_side(context.get("domino_risk_away"), "away",
                          "domino_risk_score")
        dr = max(dr_h or 0.0, dr_a or 0.0) if (dr_h is not None or dr_a is not None) else None

    # ── Weighted (uniform) average of available pillars ──────────────
    pillars = [sv, fi, le, ro_peak, bs, dr]
    available = [p for p in pillars if p is not None]
    if not available:
        return 0.0, drivers
    risk_score = sum(available) / len(available)

    # ── Pillar-specific drivers ─────────────────────────────────────
    if sv is not None and sv >= 70:
        drivers.append("HIGH_STARTER_VOLATILITY")
    if fi is not None and fi >= 80:
        drivers.append("EXTREME_FIRST_INNING_COLLAPSE_RISK")
    if le is not None and le >= 80:
        drivers.append("EXPLOSIVE_LINEUP")
    if bs is not None and bs >= 75:
        drivers.append("BULLPEN_STRESS")
    if dr is not None and dr >= 75:
        drivers.append("DOMINO_RISK")

    # Clamp.
    risk_score = max(0.0, min(risk_score, 100.0))
    return risk_score, drivers


# ─────────────────────────────────────────────────────────────────────
# Lambda computation
# ─────────────────────────────────────────────────────────────────────
def _compute_lambda(context: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Derive the run-distribution lambda (mean) from baseline +
    contextual multipliers (park factor, weather)."""
    reasons: List[str] = []
    lam = _safe_float(context.get("baseline_expected_runs"), default=0.0)
    if lam <= 0:
        # Try baseline_distribution.mean.
        bd = context.get("baseline_distribution") or {}
        lam = _safe_float(bd.get("mean"), default=0.0)
    if lam <= 0:
        # Last resort: industry average for MLB totals.
        lam = 8.5
        reasons.append("LAMBDA_FALLBACK_INDUSTRY_AVG")

    # Park factor (multiplicative on lambda).
    pf = context.get("park_factor")
    if isinstance(pf, dict):
        # Use `dynamic` or `park_runs_mult` keys.
        mult = pf.get("dynamic") or pf.get("park_runs_mult") or pf.get("runFactor")
    else:
        mult = pf
    mult_f = _safe_float(mult, default=1.0) or 1.0
    if mult_f > 0:
        lam = lam * mult_f
        if abs(mult_f - 1.0) > 0.04:
            reasons.append("PARK_FACTOR_APPLIED")

    # Weather (multiplicative, conservative — only when explicit
    # boost/suppression hint is provided).
    wx = context.get("weather") or {}
    if isinstance(wx, dict):
        # Optional explicit multipliers.
        wx_mult = _safe_float(wx.get("runs_multiplier"), default=1.0) or 1.0
        if wx_mult > 0 and abs(wx_mult - 1.0) > 0.03:
            lam *= wx_mult
            reasons.append("WEATHER_APPLIED")

    # Clamp.
    lam = max(2.0, min(lam, 22.0))
    return lam, reasons


# ─────────────────────────────────────────────────────────────────────
# Distribution PMF computation
# ─────────────────────────────────────────────────────────────────────
def _poisson_pmf(lam: float, k_max: int = MAX_RUNS) -> List[float]:
    """Numerically stable Poisson PMF for k=0..k_max."""
    if lam <= 0:
        # Degenerate — all mass on 0.
        pmf = [0.0] * (k_max + 1)
        pmf[0] = 1.0
        return pmf
    pmf = [0.0] * (k_max + 1)
    pmf[0] = math.exp(-lam)
    for k in range(1, k_max + 1):
        pmf[k] = pmf[k - 1] * lam / k
    return pmf


def _nb_pmf(mean: float, dispersion: float, k_max: int = MAX_RUNS) -> List[float]:
    """Numerically stable Negative Binomial PMF for k=0..k_max.

    Parametrization: variance = mean * dispersion, so:
        var  = mean * d  =>  mean + mean^2 / r = mean * d
        =>  r = mean / (d - 1)
        p_success (X = number of failures before r successes) = r / (r + mean)
    """
    if dispersion <= 1.0 or mean <= 0:
        return _poisson_pmf(mean, k_max=k_max)
    r = mean / (dispersion - 1.0)
    p_succ = r / (r + mean)
    # PMF(k) = C(k+r-1, k) * p^r * (1-p)^k
    # Use log-gamma for numerical stability.
    pmf = [0.0] * (k_max + 1)
    log_p = math.log(p_succ)
    log_q = math.log(1.0 - p_succ)
    for k in range(0, k_max + 1):
        # log gamma form: lgamma(k+r) - lgamma(k+1) - lgamma(r) + r*log_p + k*log_q
        try:
            log_pmf = (
                math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r)
                + r * log_p + k * log_q
            )
            pmf[k] = math.exp(log_pmf)
        except (ValueError, OverflowError):
            pmf[k] = 0.0
    # Renormalize (truncation correction).
    s = sum(pmf)
    if s > 0:
        pmf = [p / s for p in pmf]
    return pmf


def _mixture_pmf(
    pmf_poisson: List[float],
    pmf_nb: List[float],
    w_nb: float,
) -> List[float]:
    """Convex combination of two PMFs."""
    w_nb_clamped = max(0.0, min(1.0, w_nb))
    w_p = 1.0 - w_nb_clamped
    n = max(len(pmf_poisson), len(pmf_nb))
    return [
        (pmf_poisson[k] if k < len(pmf_poisson) else 0.0) * w_p
        + (pmf_nb[k]      if k < len(pmf_nb)      else 0.0) * w_nb_clamped
        for k in range(n)
    ]


def _cdf_from_pmf(pmf: List[float]) -> List[float]:
    cdf = []
    cum = 0.0
    for p in pmf:
        cum += p
        cdf.append(cum)
    return cdf


def _probabilities_by_line(pmf: List[float]) -> Dict[str, float]:
    """For each .5 line, compute P(over) and P(under).
    For line k.5 (integer k):
      P(under k.5) = sum_{x=0}^{k} pmf(x)
      P(over k.5)  = 1 - P(under k.5)
    """
    out: Dict[str, float] = {}
    cdf = _cdf_from_pmf(pmf)
    for ln in SUPPORTED_LINES:
        k_int = int(ln)  # floor of .5 line.
        if k_int < len(cdf):
            p_under = cdf[k_int]
        else:
            p_under = 1.0
        p_over = max(0.0, min(1.0, 1.0 - p_under))
        p_under = max(0.0, min(1.0, p_under))
        key_o = f"over_{str(ln).replace('.', '_')}"
        key_u = f"under_{str(ln).replace('.', '_')}"
        out[key_o] = round(p_over, 4)
        out[key_u] = round(p_under, 4)
    return out


def _percentiles_from_pmf(pmf: List[float]) -> Dict[str, int]:
    """Return integer percentiles p10..p99 from the PMF."""
    cdf = _cdf_from_pmf(pmf)
    out: Dict[str, int] = {}
    for q in PERCENTILE_LEVELS:
        # First index where cdf ≥ q.
        idx = 0
        for k, c in enumerate(cdf):
            if c >= q:
                idx = k
                break
        else:
            idx = len(cdf) - 1
        out[f"p{int(q * 100)}"] = idx
    return out


def _compute_tail_risk(pmf: List[float], lam: float) -> Dict[str, Any]:
    """Estimate the right-tail risk: P(X ≥ ceil(lambda) + 5).

    Translates into a bucketed score 0..100. Calibrated so that
    Poisson at the league-average lambda (~8.5) lands in LOW, and
    NB widening with high dispersion lands in HIGH/EXTREME.
    """
    threshold = math.ceil(lam) + 5
    tail_mass = sum(pmf[threshold:]) if threshold < len(pmf) else 0.0
    # Score scaling: 0.20 of mass beyond threshold ≈ EXTREME (100).
    raw = min(1.0, tail_mass / 0.20)
    score = round(raw * 100.0, 1)
    if score < 20:
        bucket = TAIL_LOW
    elif score < 45:
        bucket = TAIL_MEDIUM
    elif score < 75:
        bucket = TAIL_HIGH
    else:
        bucket = TAIL_EXTREME
    return {
        "score":   score,
        "bucket":  bucket,
        "tail_mass_raw": round(tail_mass, 4),
        "threshold":     int(threshold),
    }


# ─────────────────────────────────────────────────────────────────────
# Family selection
# ─────────────────────────────────────────────────────────────────────
def _select_family(
    risk_score: float,
    has_partial_data: bool,
) -> Tuple[str, Dict[str, float], List[str]]:
    """Per NIVEL 3 §1 spec — explicit weight formula:
        nb_weight = clamp((risk_score - 30) / 50, 0.0, 0.90)
        poisson_weight = 1.0 - nb_weight

    Family selection from the resulting weights:
        * nb_weight <= NB_WEIGHT_POISSON_TH  → POISSON   (clean Poisson regime).
        * nb_weight >= NB_WEIGHT_NB_TH       → NB        (dominant variance).
        * otherwise                          → MIXTURE.

    When `has_partial_data=True` we never select pure POISSON or pure
    NB — always MIXTURE so partial information can't drive a regime
    decision (per spec).
    """
    reasons: List[str] = []

    # Explicit formula.
    nb_weight = (risk_score - NB_WEIGHT_OFFSET) / NB_WEIGHT_SCALE
    nb_weight = max(0.0, min(NB_WEIGHT_MAX, nb_weight))
    poisson_weight = 1.0 - nb_weight
    weights = {
        "poisson":           round(poisson_weight, 4),
        "negative_binomial": round(nb_weight, 4),
    }

    if has_partial_data:
        family = FAMILY_MIXTURE
        reasons.append("MIXTURE_DUE_TO_PARTIAL_DATA")
        reasons.append("DISTRIBUTION_MIXTURE_SELECTED")
        return family, weights, reasons

    if nb_weight <= NB_WEIGHT_POISSON_TH:
        family = FAMILY_POISSON
        # Snap to pure Poisson.
        weights = {"poisson": 1.0, "negative_binomial": 0.0}
        reasons.append("DISTRIBUTION_POISSON_SELECTED")
    elif nb_weight >= NB_WEIGHT_NB_TH:
        family = FAMILY_NB
        # Use the formula's weight (cap is 0.90) so the NB regime isn't
        # absolute — preserves a 10% Poisson tail by design.
        reasons.append("DISTRIBUTION_NEGATIVE_BINOMIAL_SELECTED")
        reasons.append("HIGH_VARIANCE_DISTRIBUTION_USED")
    else:
        family = FAMILY_MIXTURE
        reasons.append("DISTRIBUTION_MIXTURE_SELECTED")
        if nb_weight >= 0.50:
            reasons.append("HIGH_VARIANCE_DISTRIBUTION_USED")

    return family, weights, reasons


def _has_partial_data(context: Dict[str, Any]) -> bool:
    """Heuristic: if a critical sub-block is fully missing, mark
    partial-data regime to force MIXTURE."""
    critical_keys_two_sided = (
        "starter_volatility",
        "lineup_explosiveness",
        "bullpen_stress",
    )
    critical_keys_per_side = (
        ("starter_volatility_home", "starter_volatility_away"),
        ("lineup_explosiveness_home", "lineup_explosiveness_away"),
        ("bullpen_stress_home", "bullpen_stress_away"),
    )
    n_missing = 0
    for ck in critical_keys_two_sided:
        block = context.get(ck)
        per_side_pair = critical_keys_per_side[critical_keys_two_sided.index(ck)]
        per_side_present = (
            context.get(per_side_pair[0]) or context.get(per_side_pair[1])
        )
        if not block and not per_side_present:
            n_missing += 1
    return n_missing >= 1


def _compute_dispersion(volatility_score: float) -> float:
    """Map volatility score 0..100 → dispersion ratio.
    Score 0 → 1.0 (Poisson). Score 100 → cap (3.0).
    """
    if volatility_score <= 5:
        return 1.0
    # Linear interpolation 5..100 → 1.05..2.5.
    raw = 1.05 + (volatility_score - 5) / 95.0 * 1.45
    return float(max(MIN_DISPERSION, min(MAX_DISPERSION, raw)))


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def _neutral_payload(reasons: List[str]) -> Dict[str, Any]:
    """Returned on critical failure / no usable input."""
    lam = 8.5
    pmf = _poisson_pmf(lam)
    probs = _probabilities_by_line(pmf)
    percentiles = _percentiles_from_pmf(pmf)
    tail = _compute_tail_risk(pmf, lam)
    return {
        "available":           False,
        "lambda":              lam,
        "distribution_family": FAMILY_POISSON,
        "mixture_weights":     {"poisson": 1.0, "negative_binomial": 0.0},
        "dispersion":          1.0,
        "probabilities":       probs,
        "percentiles":         percentiles,
        "tail_risk":           tail,
        "reason_codes":        list(reasons or []) + ["NEUTRAL_FALLBACK"],
        "debug":               {},
    }


def build_dynamic_run_distribution(
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Public entry-point. Always returns a dict, never raises."""
    try:
        if not isinstance(context, dict):
            return _neutral_payload(["INVALID_CONTEXT_TYPE"])

        debug_on = bool(context.get("debug"))

        # 1) Compute lambda.
        lam, lam_reasons = _compute_lambda(context)

        # 2) Compute volatility score.
        vol_score, vol_drivers = _compute_volatility_score(context)

        # 3) Detect partial data → force MIXTURE.
        partial = _has_partial_data(context)

        # 4) Select family + weights.
        family, weights, family_reasons = _select_family(vol_score, partial)

        # 5) Dispersion (used for NB component).
        dispersion = _compute_dispersion(vol_score)

        # 6) Build component PMFs.
        pmf_poisson = _poisson_pmf(lam)
        pmf_nb      = _nb_pmf(lam, dispersion) if (family != FAMILY_POISSON) else pmf_poisson

        # 7) Combine into final PMF based on family.
        if family == FAMILY_POISSON:
            pmf = pmf_poisson
        elif family == FAMILY_NB:
            pmf = pmf_nb
        else:
            pmf = _mixture_pmf(pmf_poisson, pmf_nb, weights["negative_binomial"])

        # 8) Compute outputs.
        probs       = _probabilities_by_line(pmf)
        percentiles = _percentiles_from_pmf(pmf)
        tail        = _compute_tail_risk(pmf, lam)

        # 9) Tail risk drivers — promote from volatility drivers when
        #     the bucket is HIGH/EXTREME so downstream UI knows why.
        tail_drivers = list(vol_drivers)
        if tail["bucket"] in (TAIL_HIGH, TAIL_EXTREME) and "EXPLOSIVE_TAIL_RISK" not in tail_drivers:
            tail_drivers.append("EXPLOSIVE_TAIL_RISK")
        tail_block = {**tail, "drivers": tail_drivers}

        reason_codes = list(family_reasons) + list(lam_reasons)
        for d in vol_drivers:
            if d not in reason_codes:
                reason_codes.append(d)

        result = {
            "available":           True,
            "lambda":              round(lam, 3),
            "distribution_family": family,
            "mixture_weights":     weights,
            "dispersion":          round(dispersion, 3),
            "volatility_score":    round(vol_score, 1),
            "probabilities":       probs,
            "percentiles":         percentiles,
            "tail_risk":           tail_block,
            "reason_codes":        reason_codes,
        }
        if debug_on:
            result["debug"] = {
                "pmf_head":    [round(p, 5) for p in pmf[:20]],
                "lam":         lam,
                "vol_score":   vol_score,
                "partial":     partial,
                "weights":     weights,
                "dispersion":  dispersion,
            }
        else:
            result["debug"] = {}
        return result
    except Exception as exc:  # noqa: BLE001 — fail-soft contract
        return _neutral_payload([f"EXCEPTION:{type(exc).__name__}:{exc}"])


__all__ = [
    "build_dynamic_run_distribution",
    "SUPPORTED_LINES",
    "PERCENTILE_LEVELS",
    "FAMILY_POISSON",
    "FAMILY_NB",
    "FAMILY_MIXTURE",
    "TAIL_LOW",
    "TAIL_MEDIUM",
    "TAIL_HIGH",
    "TAIL_EXTREME",
    "VOLATILITY_POISSON_MAX",
    "VOLATILITY_NB_MIN",
    "MIN_DISPERSION",
    "MAX_DISPERSION",
    "MAX_RUNS",
]
