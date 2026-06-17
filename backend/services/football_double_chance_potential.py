"""Sprint-D3 · Football Double Chance Potential (ELO-based 1X2)
==============================================================

Pure-function module that estimates the three 1X2 probabilities
(home win / draw / away win) using an **ELO-based** model and a
plug-in ``P(draw)`` estimate (typically from
``compute_draw_potential``).

It then derives the three Double-Chance probabilities:

* ``HOME_OR_DRAW (HD)`` = P(home) + P(draw)
* ``AWAY_OR_DRAW (AD)`` = P(away) + P(draw)
* ``HOME_OR_AWAY (HA)`` = P(home) + P(away) = 1 - P(draw)

Why this design
---------------
* Keeps the model **simple, auditable, and back-compat** with
  ``compute_draw_potential``: we reuse its ``draw_probability`` rather
  than re-inventing a 3-way classifier.
* The standard **ELO expected score** is ``E_home = 1 / (1 + 10^(-Δ/400))``
  where ``Δ = elo_home − elo_away + HOME_ADV_ELO``. Crucially,
  ``E_home`` accounts for draws as half-points:

      E_home = 1·P(home) + 0.5·P(draw) + 0·P(away)
      E_away = 0·P(home) + 0.5·P(draw) + 1·P(away)
      1      = P(home) + P(draw) + P(away)

  Solving with the given ``P(draw) = p_D``:

      P(home) = E_home − 0.5·p_D
      P(away) = (1 − E_home) − 0.5·p_D = 1 − P(home) − P(draw)

* If solving produces a slightly negative ``P(home)`` or ``P(away)``
  (rare; happens when ``p_D`` is very high relative to the ELO
  expectation), we **renormalize** by clamping negatives to ``0`` and
  rescaling the residual mass.

Strict invariants
-----------------
* Pure function, no I/O.
* Fail-soft: every missing input is tolerated; defaults to the league
  baseline draw rate and a symmetric ELO if needed.
* Bounded: all probabilities clamped to ``[0, 1]``; sum guaranteed to
  be ``1`` (up to floating-point rounding).
* Never raises.

Public API
----------
.. code-block:: python

    from services.football_double_chance_potential import (
        compute_double_chance_potential,
    )
    out = compute_double_chance_potential(
        elo_home=1740, elo_away=1620,
        draw_probability_pct=26.5,
    )
    # → {
    #     "p_home": 0.522, "p_draw": 0.265, "p_away": 0.213,
    #     "p_home_or_draw": 0.787,
    #     "p_away_or_draw": 0.478,
    #     "p_home_or_away": 0.735,
    #     "reason_codes": [...],
    #     "audit": {...},
    #   }

Tunables
--------
* ``HOME_ADV_ELO`` — Additive home-field advantage in ELO points.
  Default ``65`` (literature standard).
* ``DEFAULT_DRAW_RATE`` — fallback draw probability when not provided.
  Default ``0.24`` (typical league baseline).
"""
from __future__ import annotations

import math
from typing import Optional


# ─── Tunables ───────────────────────────────────────────────────────────
HOME_ADV_ELO: float       = 65.0
DEFAULT_DRAW_RATE: float  = 0.24

# Probability floors/ceilings (defensive).
PROB_MIN_PCT: float = 0.5
PROB_MAX_PCT: float = 99.5


# ─── Reason codes ───────────────────────────────────────────────────────
RC_ELO_OK                     = "DC_ELO_OK"
RC_ELO_MISSING_SYMMETRIC      = "DC_ELO_MISSING_USED_SYMMETRIC"
RC_DRAW_PROB_FALLBACK         = "DC_DRAW_PROB_FALLBACK"
RC_DRAW_PROB_CLAMPED          = "DC_DRAW_PROB_CLAMPED"
RC_NEGATIVE_HOME_RENORMALIZED = "DC_NEGATIVE_HOME_RENORMALIZED"
RC_NEGATIVE_AWAY_RENORMALIZED = "DC_NEGATIVE_AWAY_RENORMALIZED"
RC_HOME_ADVANTAGE_APPLIED     = "DC_HOME_ADVANTAGE_APPLIED"


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


def _elo_expected_score(elo_h: float, elo_a: float,
                        home_adv: float = HOME_ADV_ELO) -> float:
    """Standard ELO expected score for the home team.

    Returns a number in [0, 1]:
      E_home = 1 / (1 + 10 ** (-Δ / 400))
    where Δ = elo_h − elo_a + home_adv.
    """
    delta = (elo_h - elo_a) + home_adv
    return 1.0 / (1.0 + 10.0 ** (-delta / 400.0))


# ════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════
def compute_double_chance_potential(
    *,
    elo_home:              Optional[float] = None,
    elo_away:              Optional[float] = None,
    draw_probability_pct:  Optional[float] = None,
    home_advantage_elo:    Optional[float] = None,
) -> dict:
    """Compute the 1X2 + Double-Chance probability vector.

    Parameters
    ----------
    elo_home, elo_away
        ELO ratings just before kickoff. If either is missing we
        default to a symmetric pair (1500 vs 1500), which makes
        ``E_home`` depend only on the home advantage.
    draw_probability_pct
        Draw probability in **percent** (i.e., ``26.5`` not ``0.265``).
        When ``None`` we fall back to ``DEFAULT_DRAW_RATE``.
    home_advantage_elo
        Override for ``HOME_ADV_ELO`` (e.g., neutral-venue tournaments
        may pass ``0``).

    Returns
    -------
    dict ::

        {
          "p_home":            <float 0..1>,
          "p_draw":            <float 0..1>,
          "p_away":            <float 0..1>,
          "p_home_or_draw":    <float 0..1>,
          "p_away_or_draw":    <float 0..1>,
          "p_home_or_away":    <float 0..1>,
          # Percent-form for engine consumption (0..100):
          "p_home_pct":        <float>,
          "p_draw_pct":        <float>,
          "p_away_pct":        <float>,
          "p_home_or_draw_pct":   <float>,
          "p_away_or_draw_pct":   <float>,
          "p_home_or_away_pct":   <float>,
          "elo_delta":         <float>,
          "expected_score_home": <float 0..1>,
          "reason_codes":      [...],
          "audit":             {...},
        }
    """
    reasons: list[str] = []
    audit: dict = {
        "inputs": {
            "elo_home": _safe_float(elo_home),
            "elo_away": _safe_float(elo_away),
            "draw_probability_pct": _safe_float(draw_probability_pct),
        },
    }

    # ── ELO inputs ─────────────────────────────────────────────────────
    eh = _safe_float(elo_home)
    ea = _safe_float(elo_away)
    if eh is None or ea is None:
        eh = 1500.0
        ea = 1500.0
        reasons.append(RC_ELO_MISSING_SYMMETRIC)
    else:
        reasons.append(RC_ELO_OK)

    ha = (home_advantage_elo if home_advantage_elo is not None
          else HOME_ADV_ELO)
    if ha != 0:
        reasons.append(RC_HOME_ADVANTAGE_APPLIED)

    e_home = _elo_expected_score(eh, ea, home_adv=ha)

    # ── Draw probability ───────────────────────────────────────────────
    p_d_pct = _safe_float(draw_probability_pct)
    if p_d_pct is None:
        p_d = DEFAULT_DRAW_RATE
        reasons.append(RC_DRAW_PROB_FALLBACK)
    else:
        p_d = p_d_pct / 100.0
        # Defensive clamp.
        if p_d < 0.0 or p_d > 1.0:
            p_d = _clamp(p_d, 0.0, 1.0)
            reasons.append(RC_DRAW_PROB_CLAMPED)

    # ── Solve for P(home), P(away) ─────────────────────────────────────
    p_h = e_home - 0.5 * p_d
    p_a = (1.0 - e_home) - 0.5 * p_d

    # Negative-mass renormalization (rare).
    if p_h < 0.0:
        deficit = -p_h
        p_h = 0.0
        # Push the deficit onto p_a (it must come from somewhere; we
        # never reduce p_d here because the user passed it explicitly).
        p_a = max(0.0, p_a - deficit)
        reasons.append(RC_NEGATIVE_HOME_RENORMALIZED)
    if p_a < 0.0:
        deficit = -p_a
        p_a = 0.0
        p_h = max(0.0, p_h - deficit)
        reasons.append(RC_NEGATIVE_AWAY_RENORMALIZED)

    # Final renormalization to ensure exact sum = 1 (tiny rounding only).
    total = p_h + p_d + p_a
    if total > 0:
        p_h /= total
        p_d /= total
        p_a /= total

    # Double-Chance.
    p_hd = p_h + p_d
    p_ad = p_a + p_d
    p_ha = p_h + p_a  # = 1 - p_d

    out = {
        "p_home":         round(p_h, 5),
        "p_draw":         round(p_d, 5),
        "p_away":         round(p_a, 5),
        "p_home_or_draw": round(p_hd, 5),
        "p_away_or_draw": round(p_ad, 5),
        "p_home_or_away": round(p_ha, 5),

        # Percent-form for engine consumption.
        "p_home_pct":           round(p_h * 100.0, 2),
        "p_draw_pct":           round(p_d * 100.0, 2),
        "p_away_pct":           round(p_a * 100.0, 2),
        "p_home_or_draw_pct":   round(p_hd * 100.0, 2),
        "p_away_or_draw_pct":   round(p_ad * 100.0, 2),
        "p_home_or_away_pct":   round(p_ha * 100.0, 2),

        # Diagnostics.
        "elo_delta":            round((eh - ea) + ha, 2),
        "expected_score_home":  round(e_home, 5),
        "label":                None,
        "reason_codes":         reasons,
        "audit":                audit,
    }
    audit["p_home"] = out["p_home"]
    audit["p_draw"] = out["p_draw"]
    audit["p_away"] = out["p_away"]
    return out


__all__ = [
    "compute_double_chance_potential",
    "HOME_ADV_ELO", "DEFAULT_DRAW_RATE",
    "PROB_MIN_PCT", "PROB_MAX_PCT",
    "RC_ELO_OK", "RC_ELO_MISSING_SYMMETRIC",
    "RC_DRAW_PROB_FALLBACK", "RC_DRAW_PROB_CLAMPED",
    "RC_NEGATIVE_HOME_RENORMALIZED", "RC_NEGATIVE_AWAY_RENORMALIZED",
    "RC_HOME_ADVANTAGE_APPLIED",
    "_elo_expected_score",
]
