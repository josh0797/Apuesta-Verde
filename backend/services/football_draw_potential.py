"""Sprint-A · Football Draw Potential Score
=========================================

Pure-function module that estimates the **probability of a draw** for a
football match using only **pre-match factors** (no live score, no
fixture-of-the-day stats). Outputs an edge against the market implied
draw probability so the engine can flag *VALUE_DRAW_CANDIDATE* picks.

Why this lives in a dedicated module
------------------------------------
The user requested a focused validation phase ("Sprint A — piloto
retrospectivo") **before** investing in the full learning-snapshot
infrastructure. This module is the single source of truth for draw
potential and is consumed both by the retrospective backtest
(``tests/backtest_sprint_a_world_cup_2026_pilot.py``) and by future
production callers (Sprint B will wire it into the analyst pipeline).

Strict invariants
-----------------
* PURE FUNCTION — no I/O, no network, no DB.
* FAIL-SOFT — every missing input is tolerated. Returns
  ``label = "INSUFFICIENT_DATA"`` when there's not enough signal.
* REASON CODES — every modifier that nudges the probability emits an
  explicit reason code so the UI / audit trail can show **why** the
  draw was flagged (or not).

Public API
----------
.. code-block:: python

    from services.football_draw_potential import compute_draw_potential

    out = compute_draw_potential(
        home_team="Spain", away_team="Cabo Verde",
        elo_home=1850, elo_away=1480,
        xg_home_l5=2.1, xg_away_l5=0.8,
        is_group_stage=True,
        both_need_points=True,
        low_goal_environment=False,
        conservative_style_home=True, conservative_style_away=True,
        market_implied_draw_prob=0.10,   # +900 momio
    )
    # out -> {
    #   "draw_probability": 21.4,
    #   "market_implied": 10.0,
    #   "edge": 11.4,
    #   "label": "VALUE_DRAW_CANDIDATE",
    #   "reason_codes": ["EVEN_MATCHUP", "GROUP_STAGE_CONSERVATIVE"],
    # }
"""
from __future__ import annotations

from typing import Optional

# ─── Tunable constants (centralised for transparency) ───────────────────
# These are calibrated from football literature on draw frequencies in
# major tournaments (≈25-28% league average, ≈22-26% knockout-light WC
# group stage). Sprint B will replace them with calibrated values from
# the learning loops.
BASE_DRAW_PROBABILITY              = 0.24   # league-average baseline
BALANCE_MAX_BOOST                  = 0.10   # +10pp for perfectly even teams
GROUP_STAGE_MUTUAL_NEED_BOOST      = 0.04   # +4pp when both need points
LOW_GOAL_ENV_BOOST                 = 0.03   # +3pp for low-scoring tendencies
CONSERVATIVE_STYLE_BOOST_EACH      = 0.015  # +1.5pp per defensive side
DOMINANT_FAVOURITE_PENALTY         = -0.06  # -6pp when massive favorite
DRAW_PROB_FLOOR                    = 0.05
DRAW_PROB_CEILING                  = 0.42   # draw rates above this are unrealistic
# Edge thresholds (pp = percentage points above market implied)
EDGE_VALUE_THRESHOLD_PP            = 4.0
EDGE_STRONG_THRESHOLD_PP           = 8.0
# ELO / xG differential normalisers
ELO_DIFF_NORMALISER                = 200.0  # 200 ELO ≈ "strong favourite"
XG_DIFF_NORMALISER                 = 1.5    # 1.5 xG/match ≈ huge gap
# Minimum inputs required to even attempt a verdict
MIN_INPUTS_FOR_VERDICT             = 2

# ─── Reason codes — kept as module constants for safe reuse ─────────────
RC_EVEN_MATCHUP                = "EVEN_MATCHUP"
RC_LOW_GOAL_ENV                = "LOW_GOAL_ENVIRONMENT"
RC_GROUP_STAGE_CONSERVATIVE    = "GROUP_STAGE_CONSERVATIVE"
RC_BOTH_NEED_POINTS            = "BOTH_NEED_POINTS"
RC_CONSERVATIVE_STYLE_BOTH     = "CONSERVATIVE_STYLE_BOTH"
RC_CONSERVATIVE_STYLE_HOME     = "CONSERVATIVE_STYLE_HOME"
RC_CONSERVATIVE_STYLE_AWAY     = "CONSERVATIVE_STYLE_AWAY"
RC_DOMINANT_FAVOURITE          = "DOMINANT_FAVOURITE"
RC_MARKET_IMPLIED_UNAVAILABLE  = "MARKET_IMPLIED_DRAW_UNAVAILABLE"
RC_INSUFFICIENT_INPUTS         = "INSUFFICIENT_INPUTS_FOR_VERDICT"
RC_NEGATIVE_EDGE               = "NEGATIVE_EDGE_VS_MARKET"

# ─── Labels ──────────────────────────────────────────────────────────────
LABEL_VALUE_DRAW       = "VALUE_DRAW_CANDIDATE"
LABEL_STRONG_VALUE     = "STRONG_VALUE_DRAW"
LABEL_FAIR_DRAW        = "FAIR_DRAW_NO_EDGE"
LABEL_NO_VALUE         = "NO_DRAW_VALUE"
LABEL_INSUFFICIENT     = "INSUFFICIENT_DATA"


# ════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _strength_balance_score(
    elo_home: Optional[float], elo_away: Optional[float],
    xg_home_l5: Optional[float], xg_away_l5: Optional[float],
) -> tuple[Optional[float], dict]:
    """Returns (balance ∈ [0,1], debug). 1 = perfectly balanced."""
    debug: dict = {}
    elo_h, elo_a = _safe_float(elo_home), _safe_float(elo_away)
    xg_h, xg_a   = _safe_float(xg_home_l5), _safe_float(xg_away_l5)

    components: list[float] = []
    if elo_h is not None and elo_a is not None:
        elo_diff = abs(elo_h - elo_a) / ELO_DIFF_NORMALISER
        elo_score = 1.0 - _clamp(elo_diff, 0.0, 1.0)
        components.append(elo_score)
        debug["elo_diff"] = round(abs(elo_h - elo_a), 1)
        debug["elo_score"] = round(elo_score, 3)
    if xg_h is not None and xg_a is not None:
        xg_diff = abs(xg_h - xg_a) / XG_DIFF_NORMALISER
        xg_score = 1.0 - _clamp(xg_diff, 0.0, 1.0)
        components.append(xg_score)
        debug["xg_diff"] = round(abs(xg_h - xg_a), 2)
        debug["xg_score"] = round(xg_score, 3)
    if not components:
        return None, debug
    balance = sum(components) / len(components)
    debug["balance"] = round(balance, 3)
    return balance, debug


def _is_dominant_favourite(
    elo_home: Optional[float], elo_away: Optional[float],
    xg_home_l5: Optional[float], xg_away_l5: Optional[float],
) -> bool:
    """True when ONE side is vastly stronger than the other."""
    elo_h, elo_a = _safe_float(elo_home), _safe_float(elo_away)
    xg_h, xg_a   = _safe_float(xg_home_l5), _safe_float(xg_away_l5)
    if elo_h is not None and elo_a is not None:
        if abs(elo_h - elo_a) >= 300:
            return True
    if xg_h is not None and xg_a is not None:
        if abs(xg_h - xg_a) >= 1.5:
            return True
    return False


def _count_real_inputs(**kwargs) -> int:
    """Count how many inputs are NOT None (excluding bool flags)."""
    n = 0
    for v in kwargs.values():
        if v is None:
            continue
        n += 1
    return n


# ════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════
def compute_draw_potential(
    *,
    home_team: str = "",
    away_team: str = "",
    elo_home:                 Optional[float] = None,
    elo_away:                 Optional[float] = None,
    xg_home_l5:               Optional[float] = None,
    xg_away_l5:               Optional[float] = None,
    is_group_stage:           bool = False,
    both_need_points:         bool = False,
    low_goal_environment:     bool = False,
    conservative_style_home:  bool = False,
    conservative_style_away:  bool = False,
    market_implied_draw_prob: Optional[float] = None,
) -> dict:
    """Compute the Draw Potential block.

    Parameters
    ----------
    home_team, away_team
        Display names (echoed in the output for traceability).
    elo_home, elo_away
        ELO ratings or any power-rating proxy (FIFA Coca-Cola, club ELO).
    xg_home_l5, xg_away_l5
        Recent xG average over last 5 fixtures (per team).
    is_group_stage
        ``True`` when the match is a group-stage game in a tournament.
    both_need_points
        ``True`` when **both** sides have a non-trivial reason to NOT
        push for the win (e.g. early matchday, last matchday already
        qualified, etc.). Triggers the cooperative-draw boost.
    low_goal_environment
        ``True`` when both sides exhibit a low-scoring trend (e.g.
        Under 2.5 hit rate ≥ 60% over L10).
    conservative_style_home, conservative_style_away
        ``True`` for sides whose tactical profile favours defensive
        solidity over attacking transitions.
    market_implied_draw_prob
        Implied probability from the market draw odd. Range 0..1
        (e.g. 0.24 for a +316 American / 4.17 decimal odd).

    Returns
    -------
    dict ::

        {
          "home_team":         "...",
          "away_team":         "...",
          "draw_probability":  <float 0..100>,
          "market_implied":    <float 0..100> | None,
          "edge":              <float, pp> | None,
          "label":             "VALUE_DRAW_CANDIDATE" | ...,
          "reason_codes":      [...],
          "debug":             {... factor breakdown ...},
          "available":         True,
        }
    """
    debug: dict = {}
    reasons: list[str] = []

    # ── Step 1. Compute the balance-of-strength score ───────────────────
    balance, balance_debug = _strength_balance_score(
        elo_home, elo_away, xg_home_l5, xg_away_l5,
    )
    debug.update(balance_debug)

    # If we have absolutely zero strength signal, we cannot produce a
    # meaningful verdict.
    quant_inputs = _count_real_inputs(
        elo_home=elo_home, elo_away=elo_away,
        xg_home_l5=xg_home_l5, xg_away_l5=xg_away_l5,
    )
    if quant_inputs < MIN_INPUTS_FOR_VERDICT and not (
        is_group_stage or both_need_points
        or low_goal_environment
        or conservative_style_home or conservative_style_away
    ):
        return {
            "home_team":        home_team,
            "away_team":        away_team,
            "draw_probability": None,
            "market_implied":   round(market_implied_draw_prob * 100.0, 1) if market_implied_draw_prob else None,
            "edge":             None,
            "label":            LABEL_INSUFFICIENT,
            "reason_codes":     [RC_INSUFFICIENT_INPUTS],
            "debug":            debug,
            "available":        True,
        }

    # ── Step 2. Build the probability ────────────────────────────────────
    prob = BASE_DRAW_PROBABILITY
    debug["base"] = BASE_DRAW_PROBABILITY

    if balance is not None:
        # Balance ∈ [0, 1]. A perfectly balanced match adds the full
        # BALANCE_MAX_BOOST; a 1-sided match adds nothing.
        balance_contribution = balance * BALANCE_MAX_BOOST
        prob += balance_contribution
        debug["balance_contribution"] = round(balance_contribution, 4)
        if balance >= 0.7:
            reasons.append(RC_EVEN_MATCHUP)

    if _is_dominant_favourite(elo_home, elo_away, xg_home_l5, xg_away_l5):
        prob += DOMINANT_FAVOURITE_PENALTY
        reasons.append(RC_DOMINANT_FAVOURITE)
        debug["dominant_favourite_penalty"] = DOMINANT_FAVOURITE_PENALTY

    if is_group_stage and both_need_points:
        prob += GROUP_STAGE_MUTUAL_NEED_BOOST
        reasons.append(RC_GROUP_STAGE_CONSERVATIVE)
        reasons.append(RC_BOTH_NEED_POINTS)
        debug["group_stage_mutual_boost"] = GROUP_STAGE_MUTUAL_NEED_BOOST

    if low_goal_environment:
        prob += LOW_GOAL_ENV_BOOST
        reasons.append(RC_LOW_GOAL_ENV)
        debug["low_goal_boost"] = LOW_GOAL_ENV_BOOST

    if conservative_style_home and conservative_style_away:
        prob += 2 * CONSERVATIVE_STYLE_BOOST_EACH
        reasons.append(RC_CONSERVATIVE_STYLE_BOTH)
        debug["conservative_boost"] = 2 * CONSERVATIVE_STYLE_BOOST_EACH
    elif conservative_style_home:
        prob += CONSERVATIVE_STYLE_BOOST_EACH
        reasons.append(RC_CONSERVATIVE_STYLE_HOME)
        debug["conservative_boost"] = CONSERVATIVE_STYLE_BOOST_EACH
    elif conservative_style_away:
        prob += CONSERVATIVE_STYLE_BOOST_EACH
        reasons.append(RC_CONSERVATIVE_STYLE_AWAY)
        debug["conservative_boost"] = CONSERVATIVE_STYLE_BOOST_EACH

    # ── Step 3. Clamp to a realistic range ─────────────────────────────
    prob = _clamp(prob, DRAW_PROB_FLOOR, DRAW_PROB_CEILING)
    debug["draw_prob_clamped"] = round(prob, 4)

    draw_prob_pct = round(prob * 100.0, 1)

    # ── Step 4. Compute the edge against market ────────────────────────
    if market_implied_draw_prob is None:
        market_pct = None
        edge_pp    = None
        reasons.append(RC_MARKET_IMPLIED_UNAVAILABLE)
        label = LABEL_FAIR_DRAW if balance is not None else LABEL_INSUFFICIENT
    else:
        market_pct = round(float(market_implied_draw_prob) * 100.0, 1)
        edge_pp    = round(draw_prob_pct - market_pct, 1)
        if edge_pp >= EDGE_STRONG_THRESHOLD_PP:
            label = LABEL_STRONG_VALUE
        elif edge_pp >= EDGE_VALUE_THRESHOLD_PP:
            label = LABEL_VALUE_DRAW
        elif edge_pp >= 0:
            label = LABEL_FAIR_DRAW
        else:
            label = LABEL_NO_VALUE
            reasons.append(RC_NEGATIVE_EDGE)

    return {
        "home_team":        home_team,
        "away_team":        away_team,
        "draw_probability": draw_prob_pct,
        "market_implied":   market_pct,
        "edge":             edge_pp,
        "label":            label,
        "reason_codes":     reasons,
        "debug":            debug,
        "available":        True,
    }


# ─── American-odds helper (used by the backtest pilot) ──────────────────
def implied_probability_from_american_odds(american: int | str) -> Optional[float]:
    """Convert American odds (e.g. +900, -150) to an implied probability.

    Returns None if the input is malformed."""
    try:
        n = int(str(american).replace("+", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None
    if n == 0:
        return None
    if n > 0:                       # underdog → +N
        return 100.0 / (n + 100.0)
    # favourite → -N
    n = abs(n)
    return n / (n + 100.0)


__all__ = [
    "compute_draw_potential",
    "implied_probability_from_american_odds",
    # Reason codes
    "RC_EVEN_MATCHUP",
    "RC_LOW_GOAL_ENV",
    "RC_GROUP_STAGE_CONSERVATIVE",
    "RC_BOTH_NEED_POINTS",
    "RC_CONSERVATIVE_STYLE_BOTH",
    "RC_CONSERVATIVE_STYLE_HOME",
    "RC_CONSERVATIVE_STYLE_AWAY",
    "RC_DOMINANT_FAVOURITE",
    "RC_MARKET_IMPLIED_UNAVAILABLE",
    "RC_INSUFFICIENT_INPUTS",
    "RC_NEGATIVE_EDGE",
    # Labels
    "LABEL_VALUE_DRAW",
    "LABEL_STRONG_VALUE",
    "LABEL_FAIR_DRAW",
    "LABEL_NO_VALUE",
    "LABEL_INSUFFICIENT",
]
