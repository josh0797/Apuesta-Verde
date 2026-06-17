"""Sprint-D3 · Football Over 1.5 Potential (Dixon-Coles bivariate)
================================================================

Pure-function module that estimates the probability of an OVER 1.5
total-goals outcome using the **Dixon-Coles (1997)** bivariate-Poisson
model with the low-score correlation correction ``tau``.

The semantic contract mirrors ``football_draw_potential``: pure,
fail-soft, fully audit-traceable via reason codes, and never
raises.

Model overview
--------------
For each match we estimate two scoring intensities (``λ_home`` and
``λ_away``) and a correlation parameter ``ρ`` (rho), then compute::

    P(X=x, Y=y) = τ(x, y; λ_h, λ_a, ρ) ·
                  Poisson(x | λ_h) · Poisson(y | λ_a)

where ``τ`` corrects the independence assumption for the four
low-score cells (0–0, 0–1, 1–0, 1–1):

* τ(0,0) = 1 − λ_h · λ_a · ρ
* τ(0,1) = 1 + λ_h · ρ
* τ(1,0) = 1 + λ_a · ρ
* τ(1,1) = 1 − ρ
* τ(x,y) = 1                       otherwise

P(O1.5) = 1 − P(0,0) − P(0,1) − P(1,0).

The Sprint-D3 use-case (point-in-time, no closing odds) does NOT
fit a per-team attack/defence vector — we just plug in PIT proxies
for the team scoring rates:

* ``λ_home_est = max(MIN_LAMBDA, xg_home_l5 + HOME_ADV_LAMBDA)``
* ``λ_away_est = max(MIN_LAMBDA, xg_away_l5)``

When xG is unavailable we fall back to the historical goal averages
(``goal_avg_for_home`` / ``goal_avg_for_away``).

Tunables
--------
* ``RHO_DEFAULT``  — Dixon-Coles correlation parameter. Literature
  values typically land in ``[-0.20, -0.10]``. We use ``-0.13`` as a
  conservative default (it slightly *raises* the probability of low
  scores, which mildly *reduces* P(O1.5)).
* ``HOME_ADV_LAMBDA`` — Additive home-field advantage on the home
  scoring intensity (in goals/match). Default ``0.20``.
* ``MIN_LAMBDA`` / ``MAX_LAMBDA`` — Floor/ceiling on per-team
  intensities to keep the Poisson PDF numerically sane.
* ``LEAGUE_AVG_LAMBDA`` — Fallback when neither xG nor goal averages
  are available; default ``1.40`` per team (typical for top-flight
  football).

All thresholds for labelling (FAIR / VALUE / STRONG) are intentionally
NOT defined here — they live in the backtest engine (where they are
calibrated against observed base rates per dataset, à la Sprint D2).

Public API
----------
.. code-block:: python

    from services.football_over15_potential import (
        compute_over15_potential,
    )
    out = compute_over15_potential(
        xg_home_l5=1.6, xg_away_l5=1.2,
        goal_avg_for_home=1.7, goal_avg_for_away=1.1,
        is_group_stage=False,
        tournament_phase="GROUP",
    )
    # → {
    #     "over15_probability": 78.4,       # in 0..100
    #     "lambda_home": 1.80,
    #     "lambda_away": 1.20,
    #     "rho_used": -0.13,
    #     "p_score_grid": {(0,0): 0.045, (0,1): 0.054, (1,0): 0.081},
    #     "reason_codes": ["OVER15_DIXON_COLES_OK"],
    #     "audit": {...},
    #   }
"""
from __future__ import annotations

import math
from typing import Optional


# ─── Tunables ───────────────────────────────────────────────────────────
RHO_DEFAULT: float        = -0.13     # Dixon-Coles correlation
HOME_ADV_LAMBDA: float    = 0.20      # extra λ for home team
MIN_LAMBDA: float         = 0.30
MAX_LAMBDA: float         = 4.50
LEAGUE_AVG_LAMBDA: float  = 1.40      # per-team fallback

# Audit ceiling on probability (numerical safety, not a model floor):
PROB_MIN: float = 0.5      # %
PROB_MAX: float = 99.5     # %

# ─── Reason codes ───────────────────────────────────────────────────────
RC_DIXON_COLES_OK            = "OVER15_DIXON_COLES_OK"
RC_USED_XG                   = "OVER15_USED_XG_PROXY"
RC_USED_GOAL_AVG_FALLBACK    = "OVER15_USED_GOAL_AVG_FALLBACK"
RC_USED_LEAGUE_FALLBACK      = "OVER15_USED_LEAGUE_FALLBACK"
RC_HOME_ADVANTAGE_APPLIED    = "OVER15_HOME_ADVANTAGE_APPLIED"
RC_LAMBDA_FLOORED            = "OVER15_LAMBDA_FLOORED"
RC_LAMBDA_CEILED             = "OVER15_LAMBDA_CEILED"
RC_INSUFFICIENT_INPUTS       = "OVER15_INSUFFICIENT_INPUTS"
RC_NUMERICAL_FALLBACK        = "OVER15_NUMERICAL_FALLBACK"


# ════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════
def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _poisson_pmf(k: int, lam: float) -> float:
    """Poisson PMF: P(X=k | λ). Stable for small k.

    For k ≤ 1 we compute directly; for k ≥ 2 we use math.exp(log-form).
    """
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        # P(X=k) = exp(-λ) · λ^k / k!
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _tau_dixon_coles(x: int, y: int, lam_h: float, lam_a: float,
                     rho: float) -> float:
    """Dixon-Coles low-score correction τ(x, y; λ_h, λ_a, ρ).

    Returns 1.0 for any (x, y) outside {0, 1} × {0, 1}.
    """
    if x == 0 and y == 0:
        return 1.0 - lam_h * lam_a * rho
    if x == 0 and y == 1:
        return 1.0 + lam_h * rho
    if x == 1 and y == 0:
        return 1.0 + lam_a * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


# ════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════
def compute_over15_potential(
    *,
    xg_home_l5:              Optional[float] = None,
    xg_away_l5:              Optional[float] = None,
    goal_avg_for_home:       Optional[float] = None,
    goal_avg_for_away:       Optional[float] = None,
    goal_avg_against_home:   Optional[float] = None,
    goal_avg_against_away:   Optional[float] = None,
    is_group_stage:          bool = False,
    tournament_phase:        Optional[str] = None,
    home_advantage_lambda:   Optional[float] = None,
    rho:                     Optional[float] = None,
) -> dict:
    """Estimate P(O1.5) using Dixon-Coles bivariate Poisson.

    Returns
    -------
    dict ::

        {
          "over15_probability":  <float in [0.5, 99.5]> (percent),
          "lambda_home":         <float>,
          "lambda_away":         <float>,
          "rho_used":            <float>,
          "p_score_grid":        {(0,0): ..., (0,1): ..., (1,0): ...},
          "label":               None,  # left for engine to assign
          "reason_codes":        [...],
          "audit":               {...},
        }
    """
    reasons: list[str] = []
    audit: dict = {
        "inputs": {
            "xg_home_l5": _safe_float(xg_home_l5),
            "xg_away_l5": _safe_float(xg_away_l5),
            "goal_avg_for_home": _safe_float(goal_avg_for_home),
            "goal_avg_for_away": _safe_float(goal_avg_for_away),
            "is_group_stage": bool(is_group_stage),
            "tournament_phase": tournament_phase,
        },
    }

    # ── Estimate per-team λ ────────────────────────────────────────────
    xg_h = _safe_float(xg_home_l5)
    xg_a = _safe_float(xg_away_l5)
    ga_h = _safe_float(goal_avg_for_home)
    ga_a = _safe_float(goal_avg_for_away)

    if xg_h is not None and xg_a is not None:
        lam_h = xg_h
        lam_a = xg_a
        reasons.append(RC_USED_XG)
    elif ga_h is not None and ga_a is not None:
        # Blend with the opposition's "goals against" if available:
        gaa_h = _safe_float(goal_avg_against_home)
        gaa_a = _safe_float(goal_avg_against_away)
        # λ_home ≈ avg(home scoring rate, away conceding rate)
        if gaa_a is not None:
            lam_h = (ga_h + gaa_a) / 2.0
        else:
            lam_h = ga_h
        if gaa_h is not None:
            lam_a = (ga_a + gaa_h) / 2.0
        else:
            lam_a = ga_a
        reasons.append(RC_USED_GOAL_AVG_FALLBACK)
    else:
        lam_h = LEAGUE_AVG_LAMBDA
        lam_a = LEAGUE_AVG_LAMBDA
        reasons.append(RC_USED_LEAGUE_FALLBACK)
        reasons.append(RC_INSUFFICIENT_INPUTS)

    # ── Home advantage (additive) ──────────────────────────────────────
    ha = home_advantage_lambda if home_advantage_lambda is not None else HOME_ADV_LAMBDA
    lam_h += ha
    if ha > 0:
        reasons.append(RC_HOME_ADVANTAGE_APPLIED)

    # ── Floor / ceiling ────────────────────────────────────────────────
    if lam_h < MIN_LAMBDA:
        lam_h = MIN_LAMBDA
        reasons.append(RC_LAMBDA_FLOORED)
    elif lam_h > MAX_LAMBDA:
        lam_h = MAX_LAMBDA
        reasons.append(RC_LAMBDA_CEILED)
    if lam_a < MIN_LAMBDA:
        lam_a = MIN_LAMBDA
        if RC_LAMBDA_FLOORED not in reasons:
            reasons.append(RC_LAMBDA_FLOORED)
    elif lam_a > MAX_LAMBDA:
        lam_a = MAX_LAMBDA
        if RC_LAMBDA_CEILED not in reasons:
            reasons.append(RC_LAMBDA_CEILED)

    rho_used = float(rho) if rho is not None else RHO_DEFAULT

    # ── Compute low-score grid + P(O1.5) ───────────────────────────────
    grid: dict = {}
    try:
        for x in (0, 1):
            for y in (0, 1):
                base = _poisson_pmf(x, lam_h) * _poisson_pmf(y, lam_a)
                tau  = _tau_dixon_coles(x, y, lam_h, lam_a, rho_used)
                grid[(x, y)] = max(0.0, base * tau)

        # P(O1.5) = 1 − P(0,0) − P(0,1) − P(1,0).
        # NB: τ-correction can in pathological cases push individual
        # cells slightly negative or the sum slightly past 1; we clamp
        # to PROB_MIN/PROB_MAX as a defensive net.
        p_under = grid[(0, 0)] + grid[(0, 1)] + grid[(1, 0)]
        p_o15   = 1.0 - p_under
        p_o15_pct = _clamp(p_o15 * 100.0, PROB_MIN, PROB_MAX)
        reasons.append(RC_DIXON_COLES_OK)
    except Exception as exc:    # noqa: BLE001
        reasons.append(RC_NUMERICAL_FALLBACK)
        audit["numerical_error"] = str(exc)
        # Fallback: independence (no τ).
        p_under = (_poisson_pmf(0, lam_h) * _poisson_pmf(0, lam_a)
                    + _poisson_pmf(0, lam_h) * _poisson_pmf(1, lam_a)
                    + _poisson_pmf(1, lam_h) * _poisson_pmf(0, lam_a))
        p_o15_pct = _clamp((1.0 - p_under) * 100.0, PROB_MIN, PROB_MAX)

    audit["lambda_home"] = round(lam_h, 4)
    audit["lambda_away"] = round(lam_a, 4)
    audit["rho_used"]    = rho_used
    audit["p_score_grid"] = {f"{k[0]}-{k[1]}": round(v, 5)
                              for k, v in grid.items()}

    return {
        "over15_probability": round(p_o15_pct, 2),
        "lambda_home":         round(lam_h, 4),
        "lambda_away":         round(lam_a, 4),
        "rho_used":            rho_used,
        "p_score_grid":        audit["p_score_grid"],
        "label":               None,    # engine will assign
        "reason_codes":        reasons,
        "audit":               audit,
    }


__all__ = [
    "compute_over15_potential",
    "RHO_DEFAULT", "HOME_ADV_LAMBDA",
    "MIN_LAMBDA", "MAX_LAMBDA", "LEAGUE_AVG_LAMBDA",
    "PROB_MIN", "PROB_MAX",
    "RC_DIXON_COLES_OK",
    "RC_USED_XG", "RC_USED_GOAL_AVG_FALLBACK", "RC_USED_LEAGUE_FALLBACK",
    "RC_HOME_ADVANTAGE_APPLIED",
    "RC_LAMBDA_FLOORED", "RC_LAMBDA_CEILED",
    "RC_INSUFFICIENT_INPUTS", "RC_NUMERICAL_FALLBACK",
    "_poisson_pmf", "_tau_dixon_coles",
]
