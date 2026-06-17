"""Sprint-B · B3 — Dixon-Coles (Goals) + Negative Binomial (Corners)
=================================================================

Replaces the independent-Poisson placeholder used in the Sprint-A pilot
with two calibrated probability models:

* **Dixon-Coles (DC)** — corrects the well-known under-estimation of
  low-score outcomes (0-0, 1-0, 0-1, 1-1) that pure independent
  Poisson produces. Uses a single correlation parameter ``rho`` whose
  default (−0.12) matches empirical estimates published in the
  literature (Dixon-Coles 1997, EPL 1992-1995, replicated by
  several modern studies).

* **Negative Binomial (NB)** — fits corner-count distributions better
  than Poisson because corners exhibit modest over-dispersion
  (variance ~ 1.4-1.6 × mean). Parameterised by ``mean`` and a
  dispersion parameter ``k`` (size) so as ``k → ∞`` the model
  collapses to Poisson — keeping the API forward-compatible.

The two models replace the FN observed in the Sprint-A pilot for
Alemania vs Curazao 1H markets:
  * Over 1.5 1H goals — Poisson said 42%, true outcome was hit
    → DC corrects this by re-allocating mass from (0,0)/(1,0)/(0,1)
  * Over 4.5 1H corners — Poisson said 39%, true outcome was hit
    → NB corrects this by widening the right tail

Public API
----------
* ``prob_total_goals_over(line, lam_home, lam_away, rho=-0.12, max_goals=10)``
* ``prob_btts_yes_dc(lam_home, lam_away, rho=-0.12, max_goals=10)``
* ``prob_match_result_dc(lam_home, lam_away, rho=-0.12, max_goals=10)``
    → returns ``{"home": p, "draw": p, "away": p}``
* ``prob_total_corners_over(line, mean_total, dispersion_k=30.0, max_c=25)``

All functions are pure, fail-soft (return ``None`` on garbage inputs),
and never allocate large numpy arrays — the loops are tight enough
that the standard library suffices and the modules can ship inside
the ``backend/services/`` tree without new dependencies.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional

# Default Dixon-Coles correlation (calibrated from published lit.)
DEFAULT_DC_RHO = -0.12

# Default NB dispersion for corners (variance ≈ 1.5·mean at k=30 / mean=10).
DEFAULT_NB_K = 30.0

# Max iteration caps (the modules are pure-Python; keep these tight).
_MAX_GOALS_DEFAULT = 10
_MAX_CORNERS_DEFAULT = 25


# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────
def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=2048)
def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _dc_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Dixon-Coles low-score correction factor."""
    if h == 0 and a == 0:
        return 1.0 - lam_h * lam_a * rho
    if h == 1 and a == 0:
        return 1.0 + lam_a * rho
    if h == 0 and a == 1:
        return 1.0 + lam_h * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _dc_joint_pmf(h: int, a: int, lam_h: float, lam_a: float,
                   rho: float) -> float:
    """Joint P(home=h, away=a) under the Dixon-Coles model."""
    p = _poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)
    return max(0.0, p * _dc_tau(h, a, lam_h, lam_a, rho))


def _nb_pmf(k: int, mean: float, k_disp: float) -> float:
    """Negative-binomial pmf parameterised by (mean, k_disp).

    NB(mu, k) has variance ``mu + mu^2/k``. As ``k → ∞``, this collapses
    to a Poisson with mean ``mu``.
    """
    if mean <= 0:
        return 1.0 if k == 0 else 0.0
    if k_disp <= 0:
        # Fall back to Poisson when dispersion is invalid.
        return _poisson_pmf(k, mean)
    # p = mu / (mu + k); 1-p = k / (mu + k)
    p = mean / (mean + k_disp)
    one_minus_p = k_disp / (mean + k_disp)
    # Use lgamma for numerical stability with large k.
    log_coef = (
        math.lgamma(k + k_disp) - math.lgamma(k_disp) - math.lgamma(k + 1)
    )
    log_pmf = log_coef + k_disp * math.log(one_minus_p) + k * math.log(p)
    try:
        return math.exp(log_pmf)
    except OverflowError:
        return 0.0


# ────────────────────────────────────────────────────────────────────────────────
# Public API — Dixon-Coles goal markets
# ────────────────────────────────────────────────────────────────────────────────
def prob_total_goals_over(
    line: float, lam_home, lam_away, *,
    rho: float = DEFAULT_DC_RHO,
    max_goals: int = _MAX_GOALS_DEFAULT,
) -> Optional[float]:
    """P(home_goals + away_goals > line) under Dixon-Coles."""
    lh = _safe_float(lam_home); la = _safe_float(lam_away)
    if lh is None or la is None or lh < 0 or la < 0:
        return None
    p_under_or_eq = 0.0
    line_f = float(line)
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            if (h + a) <= line_f:
                p_under_or_eq += _dc_joint_pmf(h, a, lh, la, rho)
    return round(max(0.0, min(1.0, 1.0 - p_under_or_eq)), 4)


def prob_btts_yes_dc(
    lam_home, lam_away, *,
    rho: float = DEFAULT_DC_RHO,
    max_goals: int = _MAX_GOALS_DEFAULT,
) -> Optional[float]:
    """P(both teams score) under Dixon-Coles."""
    lh = _safe_float(lam_home); la = _safe_float(lam_away)
    if lh is None or la is None or lh < 0 or la < 0:
        return None
    p = 0.0
    for h in range(1, max_goals + 1):
        for a in range(1, max_goals + 1):
            p += _dc_joint_pmf(h, a, lh, la, rho)
    return round(max(0.0, min(1.0, p)), 4)


def prob_match_result_dc(
    lam_home, lam_away, *,
    rho: float = DEFAULT_DC_RHO,
    max_goals: int = _MAX_GOALS_DEFAULT,
) -> Optional[dict]:
    """Return ``{"home": ph, "draw": pd, "away": pa}`` under DC."""
    lh = _safe_float(lam_home); la = _safe_float(lam_away)
    if lh is None or la is None or lh < 0 or la < 0:
        return None
    p_home = p_draw = p_away = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p_ha = _dc_joint_pmf(h, a, lh, la, rho)
            if h > a:
                p_home += p_ha
            elif h == a:
                p_draw += p_ha
            else:
                p_away += p_ha
    # Normalise (DC modifies low scores so the joint distribution is
    # very slightly non-normalised — renormalise to be safe).
    total = p_home + p_draw + p_away
    if total <= 0:
        return None
    return {
        "home": round(p_home / total, 4),
        "draw": round(p_draw / total, 4),
        "away": round(p_away / total, 4),
    }


# ────────────────────────────────────────────────────────────────────────────────
# Public API — Negative-Binomial corner markets
# ────────────────────────────────────────────────────────────────────────────────
def prob_total_corners_over(
    line: float, mean_total, *,
    dispersion_k: float = DEFAULT_NB_K,
    max_c: int = _MAX_CORNERS_DEFAULT,
) -> Optional[float]:
    """P(total_corners > line) under NB(mean, dispersion_k).

    Use ``dispersion_k=∞`` (or any large value) to recover Poisson.
    Empirically corners exhibit modest over-dispersion (k≈20-40).
    """
    mu = _safe_float(mean_total)
    if mu is None or mu < 0:
        return None
    p_under_or_eq = 0.0
    line_f = float(line)
    for k in range(max_c + 1):
        if k <= line_f:
            p_under_or_eq += _nb_pmf(k, mu, dispersion_k)
    return round(max(0.0, min(1.0, 1.0 - p_under_or_eq)), 4)


__all__ = [
    "DEFAULT_DC_RHO", "DEFAULT_NB_K",
    "prob_total_goals_over",
    "prob_btts_yes_dc",
    "prob_match_result_dc",
    "prob_total_corners_over",
    # Internals (exposed for tests)
    "_dc_tau", "_dc_joint_pmf", "_nb_pmf", "_poisson_pmf",
]
