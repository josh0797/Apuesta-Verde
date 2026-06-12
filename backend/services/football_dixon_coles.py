"""Phase F66 — Lightweight Dixon-Coles scoreline grid.

Purpose
=======
Given *home_xg* and *away_xg* (expected goals per side), compute the most
likely scorelines using:

  * **Tier 1 — Dixon-Coles**: bivariate Poisson + low-score tau correction
    that downweights independence at 0-0, 1-0, 0-1, 1-1 (D&C, 1997).
  * **Tier 2 — Plain Poisson**: independent Poisson grid when tau is
    unavailable / disabled.
  * **Tier 3 — Heuristic by profile**: when xG is missing, fall back to
    canonical scorelines per match profile (UNDER → 1-0/1-1/2-0, OVER →
    2-1/2-2/3-1, dominant-home → 1-0/2-0/2-1, etc.).

The scorer is purely functional, deterministic, fail-soft, and only depends
on the Python stdlib (math). No numpy / scipy on the hot path so it runs
inside FastAPI without bloat.
"""
from __future__ import annotations

import math
from typing import Optional

MAX_GOALS = 6           # grid is (MAX_GOALS+1) × (MAX_GOALS+1)
DEFAULT_TAU_RHO = -0.13  # standard D&C correlation parameter for football

METHOD_DC          = "DIXON_COLES"
METHOD_POISSON     = "POISSON"
METHOD_HEURISTIC   = "HEURISTIC_BY_PROFILE"
METHOD_UNAVAILABLE = "UNAVAILABLE"


# ─────────────────────────────────────────────────────────────────────
# Math helpers
# ─────────────────────────────────────────────────────────────────────
def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0 or k < 0:
        return 0.0 if k != 0 else 1.0
    try:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _tau(home_goals: int, away_goals: int,
        lam_home: float, lam_away: float, rho: float) -> float:
    """Dixon-Coles low-score correction. Only the four 0-1 cells need it.
    For everything else τ = 1.
    """
    if home_goals == 0 and away_goals == 0:
        return 1.0 - lam_home * lam_away * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 + lam_home * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 + lam_away * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


# ─────────────────────────────────────────────────────────────────────
# Heuristic fallback (when no xG)
# ─────────────────────────────────────────────────────────────────────
HEURISTIC_PROFILES = {
    "UNDER":         ["1-0", "1-1", "0-0", "2-0", "0-1"],
    "OVER":          ["2-1", "2-2", "3-1", "1-2", "3-2"],
    "DOMINANT_HOME": ["2-0", "1-0", "2-1", "3-0", "3-1"],
    "DOMINANT_AWAY": ["0-2", "0-1", "1-2", "0-3", "1-3"],
    "BTTS":          ["1-1", "2-1", "2-2", "1-2", "3-2"],
    "NEUTRAL":       ["1-1", "1-0", "0-1", "2-1", "1-2"],
}


def _heuristic_grid(profile: str) -> list[dict]:
    canon = HEURISTIC_PROFILES.get(profile.upper()) or HEURISTIC_PROFILES["NEUTRAL"]
    # Decreasing geometric weights — purely indicative, NOT real probabilities.
    weights = [0.18, 0.14, 0.11, 0.09, 0.07]
    return [
        {"score": s, "probability": round(w, 4), "home_goals": int(s.split("-")[0]),
         "away_goals": int(s.split("-")[1])}
        for s, w in zip(canon, weights)
    ]


# ─────────────────────────────────────────────────────────────────────
# Public scorer
# ─────────────────────────────────────────────────────────────────────
def compute_scoreline_grid(
    home_xg: Optional[float] = None,
    away_xg: Optional[float] = None,
    *,
    profile_hint: Optional[str] = None,
    use_dixon_coles: bool = True,
    rho: float = DEFAULT_TAU_RHO,
    top_n: int = 5,
) -> dict:
    """Return the *top-N* most likely scorelines.

    Output shape::

      {
        "available":      True | False,
        "method":         "DIXON_COLES" | "POISSON" | "HEURISTIC_BY_PROFILE" | "UNAVAILABLE",
        "home_xg":        float | None,
        "away_xg":        float | None,
        "top_scorelines": [
            {"score": "1-0", "probability": 0.142, "home_goals": 1, "away_goals": 0},
            …
        ],
        "most_likely":    {"score": "1-0", "probability": 0.142, "home_goals": 1, "away_goals": 0},
        "confidence":     int  # 0..100, normalised from the top scoreline's probability
      }

    Fail-soft: invalid inputs degrade through the tiers automatically.
    """
    home_xg_f = _safe(home_xg)
    away_xg_f = _safe(away_xg)

    # Tier 3 fallback — pure heuristic.
    if home_xg_f is None or away_xg_f is None or home_xg_f < 0 or away_xg_f < 0:
        if profile_hint:
            top = _heuristic_grid(profile_hint)
            return {
                "available":      True,
                "method":         METHOD_HEURISTIC,
                "home_xg":        home_xg_f,
                "away_xg":        away_xg_f,
                "top_scorelines": top[:top_n],
                "most_likely":    top[0] if top else None,
                "confidence":     45,        # heuristic ⇒ moderate confidence cap
            }
        return {
            "available":      False,
            "method":         METHOD_UNAVAILABLE,
            "home_xg":        home_xg_f,
            "away_xg":        away_xg_f,
            "top_scorelines": [],
            "most_likely":    None,
            "confidence":     0,
        }

    # Build full grid using DC or plain Poisson.
    grid = []
    method = METHOD_DC if use_dixon_coles else METHOD_POISSON
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            p_h = _poisson_pmf(h, home_xg_f)
            p_a = _poisson_pmf(a, away_xg_f)
            base = p_h * p_a
            if use_dixon_coles:
                base *= _tau(h, a, home_xg_f, away_xg_f, rho)
            base = max(base, 0.0)
            grid.append({
                "home_goals": h, "away_goals": a,
                "score":      f"{h}-{a}",
                "_p":         base,
            })

    total = sum(item["_p"] for item in grid) or 1.0
    for item in grid:
        item["probability"] = round(item["_p"] / total, 4)
        del item["_p"]

    grid.sort(key=lambda r: r["probability"], reverse=True)
    top = grid[:top_n]
    confidence = int(round(min(100.0, top[0]["probability"] * 100 * 4.5))) if top else 0
    return {
        "available":      True,
        "method":         method,
        "home_xg":        round(home_xg_f, 3),
        "away_xg":        round(away_xg_f, 3),
        "top_scorelines": top,
        "most_likely":    top[0] if top else None,
        "confidence":     confidence,
    }


def _safe(v) -> Optional[float]:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


__all__ = [
    "MAX_GOALS", "DEFAULT_TAU_RHO",
    "METHOD_DC", "METHOD_POISSON", "METHOD_HEURISTIC", "METHOD_UNAVAILABLE",
    "compute_scoreline_grid",
]
