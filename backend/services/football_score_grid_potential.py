"""Sprint-D7-F · Football Score-Grid Potential (Dixon-Coles bivariate)
================================================================

Pure-function module that estimates probabilities for **goal-total**
and **both-teams-to-score** markets by computing the full score grid
under the Dixon-Coles (1997) bivariate Poisson with low-score
correlation correction τ.

Why a separate module
---------------------
``football_over15_potential`` already implements DC bivariate for the
2×2 low-score subgrid sufficient for OVER 1.5 (P(O1.5) = 1 − P(0,0)
− P(0,1) − P(1,0)).

For OVER_2_5 / UNDER_2_5 we need the **full grid up to ≥ 6 goals per
team** so that the probability mass below the threshold is accurate.
Crucially, **UNDER_2_5 is computed by summing its OWN relevant
cells**:

* P(OVER_2_5)  = Σ over (i, j) with (i + j) ≥ 3
* P(UNDER_2_5) = Σ over (i, j) with (i + j) ≤ 2
              = P(0,0) + P(0,1) + P(1,0) + P(1,1) + P(2,0) + P(0,2)

Both predictors use the SAME τ correction so the result is logically
self-consistent, but P(UNDER_2_5) is **not** computed as
``1 - P(OVER_2_5)``. This preserves all auditing information
contributed by τ at the low-score cells (0–0, 0–1, 1–0, 1–1) and
guarantees that grid truncation never silently inflates either side.

BTTS markets are computed analogously:

* P(BTTS_YES) = Σ over (i, j) with i ≥ 1 AND j ≥ 1
* P(BTTS_NO)  = Σ over (i, j) with i = 0 OR j = 0

Notes
-----
* Grid size defaults to 7 × 7 (0..6 goals per side). Probability mass
  beyond that is negligible for any λ in our allowed range
  (MIN_LAMBDA=0.30 … MAX_LAMBDA=4.50).
* All thresholds for labelling (FAIR / VALUE / STRONG) are NOT
  defined here — they live in the backtest engine.

API
---
::

    out = compute_score_grid_potential(
        xg_home_l5=1.6, xg_away_l5=1.2,
    )
    # → {
    #     "over25_probability":     54.2,
    #     "under25_probability":    45.8,
    #     "btts_yes_probability":   62.7,
    #     "btts_no_probability":    37.3,
    #     "lambda_home": 1.80, "lambda_away": 1.20, "rho_used": -0.13,
    #     "p_score_grid": {"0-0": 0.045, "0-1": 0.054, ...},
    #     "p_total_mass":   0.9999,   # sanity (should be ≈ 1.0)
    #     "label":              None,
    #     "reason_codes":       [...],
    #     "audit":              {...},
    #   }
"""
from __future__ import annotations

import math
from typing import Optional

# Reuse the well-tested helpers from the over15 module so we don't
# duplicate the τ implementation.
from services.football_over15_potential import (    # noqa: F401
    _poisson_pmf, _tau_dixon_coles,
    HOME_ADV_LAMBDA, MIN_LAMBDA, MAX_LAMBDA, LEAGUE_AVG_LAMBDA,
    RHO_DEFAULT, PROB_MIN, PROB_MAX,
)


# ─── Tunables specific to score-grid markets ───────────────────────────
GRID_MAX_GOALS: int = 8          # 0..8 inclusive → 9 × 9 = 81 cells
# Rationale: with λ_h + ha ≤ MAX_LAMBDA=4.5 and λ_a ≤ 4.5, the cumulative
# tail mass past 8 goals per side is < 0.05% (Poisson CDF). At 6 we
# observed losses of ~2.5% with high-scoring inputs.


# ─── Reason codes ──────────────────────────────────────────────────────
RC_GRID_OK                    = "SCOREGRID_DIXON_COLES_OK"
RC_USED_XG                    = "SCOREGRID_USED_XG_PROXY"
RC_USED_GOAL_AVG_FALLBACK     = "SCOREGRID_USED_GOAL_AVG_FALLBACK"
RC_USED_LEAGUE_FALLBACK       = "SCOREGRID_USED_LEAGUE_FALLBACK"
RC_HOME_ADVANTAGE_APPLIED     = "SCOREGRID_HOME_ADVANTAGE_APPLIED"
RC_LAMBDA_FLOORED             = "SCOREGRID_LAMBDA_FLOORED"
RC_LAMBDA_CEILED              = "SCOREGRID_LAMBDA_CEILED"
RC_INSUFFICIENT_INPUTS        = "SCOREGRID_INSUFFICIENT_INPUTS"
RC_NUMERICAL_FALLBACK         = "SCOREGRID_NUMERICAL_FALLBACK"
RC_GRID_MASS_LOSS_WARN        = "SCOREGRID_TRUNCATION_MASS_LOSS"


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


# ════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════
def compute_score_grid_potential(
    *,
    xg_home_l5:              Optional[float] = None,
    xg_away_l5:              Optional[float] = None,
    goal_avg_for_home:       Optional[float] = None,
    goal_avg_for_away:       Optional[float] = None,
    goal_avg_against_home:   Optional[float] = None,
    goal_avg_against_away:   Optional[float] = None,
    home_advantage_lambda:   Optional[float] = None,
    rho:                     Optional[float] = None,
    grid_max_goals:          int = GRID_MAX_GOALS,
) -> dict:
    """Estimate score-grid–derived market probabilities via DC bivariate.

    Each market is computed by summing its OWN relevant cells (not as
    a complement) so the τ-correction at the low-score subgrid is
    fully preserved.
    """
    reasons: list[str] = []
    audit: dict = {
        "inputs": {
            "xg_home_l5": _safe_float(xg_home_l5),
            "xg_away_l5": _safe_float(xg_away_l5),
            "goal_avg_for_home": _safe_float(goal_avg_for_home),
            "goal_avg_for_away": _safe_float(goal_avg_for_away),
        },
        "grid_max_goals": grid_max_goals,
    }

    # ── Estimate per-team λ (same logic as over15) ────────────────────
    xg_h = _safe_float(xg_home_l5)
    xg_a = _safe_float(xg_away_l5)
    ga_h = _safe_float(goal_avg_for_home)
    ga_a = _safe_float(goal_avg_for_away)

    if xg_h is not None and xg_a is not None:
        lam_h = xg_h
        lam_a = xg_a
        reasons.append(RC_USED_XG)
    elif ga_h is not None and ga_a is not None:
        gaa_h = _safe_float(goal_avg_against_home)
        gaa_a = _safe_float(goal_avg_against_away)
        lam_h = (ga_h + gaa_a) / 2.0 if gaa_a is not None else ga_h
        lam_a = (ga_a + gaa_h) / 2.0 if gaa_h is not None else ga_a
        reasons.append(RC_USED_GOAL_AVG_FALLBACK)
    else:
        lam_h = LEAGUE_AVG_LAMBDA
        lam_a = LEAGUE_AVG_LAMBDA
        reasons.append(RC_USED_LEAGUE_FALLBACK)
        reasons.append(RC_INSUFFICIENT_INPUTS)

    # ── Home advantage (additive) ──────────────────────────────────────
    ha = (home_advantage_lambda
            if home_advantage_lambda is not None
            else HOME_ADV_LAMBDA)
    lam_h += ha
    if ha > 0:
        reasons.append(RC_HOME_ADVANTAGE_APPLIED)

    # ── Floor / ceiling ────────────────────────────────────────────────
    if lam_h < MIN_LAMBDA:
        lam_h = MIN_LAMBDA;  reasons.append(RC_LAMBDA_FLOORED)
    elif lam_h > MAX_LAMBDA:
        lam_h = MAX_LAMBDA;  reasons.append(RC_LAMBDA_CEILED)
    if lam_a < MIN_LAMBDA:
        lam_a = MIN_LAMBDA
        if RC_LAMBDA_FLOORED not in reasons:
            reasons.append(RC_LAMBDA_FLOORED)
    elif lam_a > MAX_LAMBDA:
        lam_a = MAX_LAMBDA
        if RC_LAMBDA_CEILED not in reasons:
            reasons.append(RC_LAMBDA_CEILED)

    rho_used = float(rho) if rho is not None else RHO_DEFAULT

    # ── Compute the full grid 0..grid_max_goals on each side ──────────
    grid: dict[tuple[int, int], float] = {}
    try:
        for x in range(grid_max_goals + 1):
            for y in range(grid_max_goals + 1):
                base = _poisson_pmf(x, lam_h) * _poisson_pmf(y, lam_a)
                tau  = _tau_dixon_coles(x, y, lam_h, lam_a, rho_used)
                # Defensive clamp — τ may go slightly negative in
                # pathological corners; we never let a cell be < 0.
                grid[(x, y)] = max(0.0, base * tau)
        reasons.append(RC_GRID_OK)
    except Exception as exc:    # noqa: BLE001
        reasons.append(RC_NUMERICAL_FALLBACK)
        audit["numerical_error"] = str(exc)
        # Fallback: independence (no τ).
        for x in range(grid_max_goals + 1):
            for y in range(grid_max_goals + 1):
                grid[(x, y)] = _poisson_pmf(x, lam_h) * _poisson_pmf(y, lam_a)

    total_mass = sum(grid.values())
    if total_mass < 0.995:
        reasons.append(RC_GRID_MASS_LOSS_WARN)

    # ── Compute each market by summing its OWN relevant cells ─────────
    # OVER 2.5  → total goals ≥ 3
    p_over25 = sum(p for (i, j), p in grid.items() if (i + j) >= 3)
    # UNDER 2.5 → total goals ≤ 2  (NOT 1 - over25)
    p_under25 = sum(p for (i, j), p in grid.items() if (i + j) <= 2)
    # BTTS_YES  → both i ≥ 1 AND j ≥ 1
    p_btts_yes = sum(p for (i, j), p in grid.items() if i >= 1 and j >= 1)
    # BTTS_NO   → i = 0 OR j = 0
    p_btts_no = sum(p for (i, j), p in grid.items() if i == 0 or j == 0)

    # Clamp to PROB_MIN / PROB_MAX for downstream numerical safety.
    over25_pp   = _clamp(p_over25  * 100.0, PROB_MIN, PROB_MAX)
    under25_pp  = _clamp(p_under25 * 100.0, PROB_MIN, PROB_MAX)
    btts_yes_pp = _clamp(p_btts_yes * 100.0, PROB_MIN, PROB_MAX)
    btts_no_pp  = _clamp(p_btts_no  * 100.0, PROB_MIN, PROB_MAX)

    audit["lambda_home"]   = round(lam_h, 4)
    audit["lambda_away"]   = round(lam_a, 4)
    audit["rho_used"]      = rho_used
    audit["p_total_mass"]  = round(total_mass, 6)
    # Only persist the low-score sub-grid in the audit to keep payload
    # compact; the full grid stays in-memory for debugging.
    audit["p_score_grid_lowscore"] = {
        f"{i}-{j}": round(grid[(i, j)], 5)
        for i in range(3) for j in range(3)
    }
    audit["p_under25_complement_check"] = round(
        p_under25 - (total_mass - p_over25), 6,
    )

    return {
        "over25_probability":   round(over25_pp, 2),
        "under25_probability":  round(under25_pp, 2),
        "btts_yes_probability": round(btts_yes_pp, 2),
        "btts_no_probability":  round(btts_no_pp, 2),
        "lambda_home":          round(lam_h, 4),
        "lambda_away":          round(lam_a, 4),
        "rho_used":             rho_used,
        "p_total_mass":         round(total_mass, 6),
        "label":                None,    # engine assigns
        "reason_codes":         reasons,
        "audit":                audit,
    }


__all__ = [
    "compute_score_grid_potential",
    "GRID_MAX_GOALS",
    "RC_GRID_OK", "RC_USED_XG", "RC_USED_GOAL_AVG_FALLBACK",
    "RC_USED_LEAGUE_FALLBACK", "RC_HOME_ADVANTAGE_APPLIED",
    "RC_LAMBDA_FLOORED", "RC_LAMBDA_CEILED",
    "RC_INSUFFICIENT_INPUTS", "RC_NUMERICAL_FALLBACK",
    "RC_GRID_MASS_LOSS_WARN",
]
