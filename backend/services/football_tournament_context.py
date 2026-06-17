"""Sprint-D2 · Football Tournament Context Score
================================================

Pure-function module that estimates a **tournament_context_score** in
``[0.0, 1.0]`` measuring how strong the *cooperative-draw incentive*
is for a given match, given the **point-in-time** group standings of
both teams.

The score is consumed by ``football_draw_potential.compute_draw_potential``
to apply a small, conservative boost (capped at +3pp) when the
cooperative-draw incentive is strong.

Why a dedicated module
----------------------
Separating the score-extraction logic from the draw-potential model
keeps the latter pure and easy to unit-test, and lets us evolve the
heuristics here without touching the model.

Strict invariants
-----------------
* PURE FUNCTION — no I/O, no network, no DB.
* FAIL-SOFT — every missing input is tolerated; returns
  ``{"score_0_1": 0.0, "boost_pp": 0.0}`` plus a reason code.
* POINT-IN-TIME — the caller is responsible for providing standings
  built ONLY from prior matches; this module does not look at any
  future data on its own.
* BOUNDED BOOST — booster is conservative: ``+2pp .. +3pp`` max.

Public API
----------
.. code-block:: python

    from services.football_tournament_context import (
        compute_tournament_context_score,
    )
    out = compute_tournament_context_score(
        standings_home={"team":"Spain","played":2,"points":4,"gd":3,"gf":4,"ga":1,...},
        standings_away={"team":"Germany","played":2,"points":4,"gd":2,"gf":3,"ga":1,...},
        match_meta={"matchday":3, "tournament_phase":"GROUP",
                    "group_label":"Group E", "is_group_stage":True},
    )
    # → {"score_0_1": 0.95, "boost_pp": 2.85, "reason_codes": [...]}

Heuristic structure (group stage)
---------------------------------
* matchday 1 → baseline 0.15  (teams play to win, few incentives clear)
* matchday 2 → baseline 0.30  (points starting to matter, slightly
  more cautious play)
* matchday 3 → variable 0.50..1.00 depending on:
    - both already qualified (≥6 pts with 1 game left typically)
      → 0.85 ( gentlemen's draw plausible)
    - both already eliminated → 0.70 (low stakes)
    - both need a draw to qualify (head-to-head points coincide and
      both go through with a draw) → 1.00 (textbook cooperative draw)
    - one needs win, other a draw → 0.55 (asymmetric, lower)

Knockout stage
--------------
Knockout matches have NO cooperative-draw incentive at 90 minutes
(ties continue to extra-time + penalties), so the score is capped at
``KNOCKOUT_MAX_SCORE`` (default 0.25). This is purely a recognition
that very even sides may end 90' tied, but should NOT trigger the
booster.
"""
from __future__ import annotations

from typing import Optional

# ─── Tunables ──────────────────────────────────────────────────────────
# Booster activation threshold & scale.
BOOST_ACTIVATION_THRESHOLD = 0.60   # score must be ≥ this to apply
BOOST_MIN_PP               = 2.0    # at activation threshold
BOOST_MAX_PP               = 3.0    # at score == 1.0
BOOST_CAP_PP               = 3.0    # absolute cap (defensive clamp)

# Group-stage matchday baselines.
MATCHDAY_1_BASE = 0.15
MATCHDAY_2_BASE = 0.30
MATCHDAY_3_BOTH_QUALIFIED      = 0.85
MATCHDAY_3_BOTH_ELIMINATED     = 0.70
MATCHDAY_3_BOTH_NEED_DRAW      = 1.00
MATCHDAY_3_ASYMMETRIC          = 0.55
MATCHDAY_3_GENERIC             = 0.50

# Knockout cap.
KNOCKOUT_MAX_SCORE = 0.25

# Standard group config (4 teams, 3 group matches, top-2 advance).
GROUP_GAMES_PER_TEAM = 3
GROUP_ADVANCE_SLOTS  = 2     # top-2 advance (standard WC / Euro / CA)

# ─── Reason codes ──────────────────────────────────────────────────────
RC_INSUFFICIENT_GROUP_DATA       = "TOURNAMENT_CONTEXT_NO_GROUP_DATA"
RC_KNOCKOUT_CAPPED               = "TOURNAMENT_CONTEXT_KNOCKOUT_CAPPED"
RC_MATCHDAY_1                    = "TOURNAMENT_CONTEXT_MATCHDAY_1"
RC_MATCHDAY_2                    = "TOURNAMENT_CONTEXT_MATCHDAY_2"
RC_MATCHDAY_3_BOTH_QUALIFIED     = "TOURNAMENT_CONTEXT_BOTH_QUALIFIED"
RC_MATCHDAY_3_BOTH_ELIMINATED    = "TOURNAMENT_CONTEXT_BOTH_ELIMINATED"
RC_MATCHDAY_3_BOTH_NEED_DRAW     = "TOURNAMENT_CONTEXT_BOTH_NEED_DRAW"
RC_MATCHDAY_3_ASYMMETRIC         = "TOURNAMENT_CONTEXT_ASYMMETRIC_NEEDS"
RC_MATCHDAY_3_GENERIC            = "TOURNAMENT_CONTEXT_MATCHDAY_3_GENERIC"
RC_BOOSTER_APPLIED               = "TOURNAMENT_CONTEXT_BOOSTER_APPLIED"
RC_BOOSTER_BELOW_THRESHOLD       = "TOURNAMENT_CONTEXT_BELOW_THRESHOLD"
RC_BOTH_NEED_POINTS_INFERRED     = "TOURNAMENT_CONTEXT_BOTH_NEED_POINTS"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_int(x, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _is_likely_qualified(row: dict) -> bool:
    """Heuristic: a team is ‘likely already qualified’ heading into the
    final matchday when it has ≥ 6 points after 2 games (2 wins), OR
    ≥ 4 points and goal-difference is comfortably positive.

    This is a coarse but defensive heuristic — we deliberately avoid
    simulating all permutations of the other 2 group games (which
    would require seeing the *current* matchday's other fixture,
    breaking point-in-time correctness).
    """
    played = _safe_int(row.get("played"))
    pts    = _safe_int(row.get("points"))
    gd     = _safe_int(row.get("gd"))
    if played < 2:
        return False
    if pts >= 6:                          # 2W
        return True
    if pts >= 4 and gd >= 2:              # 1W 1D with good GD
        return True
    return False


def _is_likely_eliminated(row: dict) -> bool:
    """Heuristic: 0 points after 2 games AND poor goal-difference."""
    played = _safe_int(row.get("played"))
    pts    = _safe_int(row.get("points"))
    gd     = _safe_int(row.get("gd"))
    if played < 2:
        return False
    if pts == 0 and gd <= -2:
        return True
    return False


def _both_need_a_draw_to_advance(row_h: dict, row_a: dict) -> bool:
    """Both teams currently tied on points, where a draw plausibly
    sends BOTH through (e.g. both on 3 / 4 pts with similar GD; a
    draw keeps both ahead of trailing rivals)."""
    played_h = _safe_int(row_h.get("played"))
    played_a = _safe_int(row_a.get("played"))
    pts_h    = _safe_int(row_h.get("points"))
    pts_a    = _safe_int(row_a.get("points"))
    if played_h < 2 or played_a < 2:
        return False
    if pts_h != pts_a:
        return False
    # Both on 3 or 4 points → classic "draw is enough" scenario.
    return pts_h in (3, 4)


def compute_tournament_context_score(
    *,
    standings_home: Optional[dict],
    standings_away: Optional[dict],
    match_meta: Optional[dict] = None,
) -> dict:
    """Compute the tournament_context_score in ``[0.0, 1.0]`` and the
    resulting booster (in percentage points, capped).

    Parameters
    ----------
    standings_home, standings_away
        Point-in-time group standings rows for the two teams. Must
        contain at least ``played``, ``points``, ``gd`` keys. Other
        keys are ignored.
    match_meta
        Dict with optional keys: ``matchday`` (int), ``tournament_phase``
        (``"GROUP" | "KNOCKOUT" | "UNKNOWN"``), ``group_label`` (str),
        ``is_group_stage`` (bool).

    Returns
    -------
    dict ::

        {
          "score_0_1":      <float 0..1>,
          "boost_pp":       <float 0..3>,
          "reason_codes":   [...],
          "phase":          "GROUP" | "KNOCKOUT" | "UNKNOWN",
          "matchday":       <int|None>,
          "both_need_points_inferred": <bool>,
          "audit":          {... per-team breakdown ...},
        }
    """
    meta = match_meta or {}
    phase = (meta.get("tournament_phase") or "UNKNOWN").upper()
    matchday = meta.get("matchday")
    reasons: list[str] = []
    audit: dict = {
        "home_played": _safe_int((standings_home or {}).get("played")),
        "away_played": _safe_int((standings_away or {}).get("played")),
        "home_points": _safe_int((standings_home or {}).get("points")),
        "away_points": _safe_int((standings_away or {}).get("points")),
        "home_gd":     _safe_int((standings_home or {}).get("gd")),
        "away_gd":     _safe_int((standings_away or {}).get("gd")),
    }
    both_need_points_inferred = False

    # ── Knockout: capped score, no booster ─────────────────────────
    if phase == "KNOCKOUT" or not meta.get("is_group_stage"):
        if phase == "KNOCKOUT":
            reasons.append(RC_KNOCKOUT_CAPPED)
            score = KNOCKOUT_MAX_SCORE
        elif standings_home is None and standings_away is None:
            reasons.append(RC_INSUFFICIENT_GROUP_DATA)
            score = 0.0
        else:
            # Group flag missing but treat as unknown / minimal.
            reasons.append(RC_INSUFFICIENT_GROUP_DATA)
            score = 0.0
        boost_pp = 0.0
        if score < BOOST_ACTIVATION_THRESHOLD:
            reasons.append(RC_BOOSTER_BELOW_THRESHOLD)
        return {
            "score_0_1": round(score, 3),
            "boost_pp":  round(boost_pp, 3),
            "reason_codes": reasons,
            "phase": phase,
            "matchday": matchday,
            "both_need_points_inferred": both_need_points_inferred,
            "audit": audit,
        }

    # ── Group stage ─────────────────────────────────────────────────
    if standings_home is None or standings_away is None:
        reasons.append(RC_INSUFFICIENT_GROUP_DATA)
        score = 0.0
    elif matchday == 1:
        reasons.append(RC_MATCHDAY_1)
        score = MATCHDAY_1_BASE
    elif matchday == 2:
        reasons.append(RC_MATCHDAY_2)
        score = MATCHDAY_2_BASE
    elif matchday == 3 or (
        matchday is None
        and _safe_int(standings_home.get("played")) >= 2
        and _safe_int(standings_away.get("played")) >= 2
    ):
        # Final matchday — branch on standings.
        if (_is_likely_qualified(standings_home)
                and _is_likely_qualified(standings_away)):
            reasons.append(RC_MATCHDAY_3_BOTH_QUALIFIED)
            score = MATCHDAY_3_BOTH_QUALIFIED
            both_need_points_inferred = False
        elif (_is_likely_eliminated(standings_home)
              and _is_likely_eliminated(standings_away)):
            reasons.append(RC_MATCHDAY_3_BOTH_ELIMINATED)
            score = MATCHDAY_3_BOTH_ELIMINATED
        elif _both_need_a_draw_to_advance(standings_home, standings_away):
            reasons.append(RC_MATCHDAY_3_BOTH_NEED_DRAW)
            reasons.append(RC_BOTH_NEED_POINTS_INFERRED)
            score = MATCHDAY_3_BOTH_NEED_DRAW
            both_need_points_inferred = True
        elif _safe_int(standings_home.get("points")) != _safe_int(
                standings_away.get("points")):
            reasons.append(RC_MATCHDAY_3_ASYMMETRIC)
            score = MATCHDAY_3_ASYMMETRIC
        else:
            reasons.append(RC_MATCHDAY_3_GENERIC)
            score = MATCHDAY_3_GENERIC
    else:
        reasons.append(RC_INSUFFICIENT_GROUP_DATA)
        score = 0.0

    # ── Booster computation ─────────────────────────────────────────
    score = _clamp(score, 0.0, 1.0)
    if score >= BOOST_ACTIVATION_THRESHOLD:
        # Linear ramp: at activation threshold → BOOST_MIN_PP, at 1.0 →
        # BOOST_MAX_PP.
        span_in  = 1.0 - BOOST_ACTIVATION_THRESHOLD
        span_out = BOOST_MAX_PP - BOOST_MIN_PP
        boost_pp = BOOST_MIN_PP + (
            (score - BOOST_ACTIVATION_THRESHOLD) / span_in
        ) * span_out if span_in > 0 else BOOST_MAX_PP
        boost_pp = _clamp(boost_pp, 0.0, BOOST_CAP_PP)
        reasons.append(RC_BOOSTER_APPLIED)
    else:
        boost_pp = 0.0
        reasons.append(RC_BOOSTER_BELOW_THRESHOLD)

    return {
        "score_0_1":   round(score, 3),
        "boost_pp":    round(boost_pp, 3),
        "reason_codes": reasons,
        "phase":       phase,
        "matchday":    matchday,
        "both_need_points_inferred": both_need_points_inferred,
        "audit":       audit,
    }


__all__ = [
    "compute_tournament_context_score",
    "BOOST_ACTIVATION_THRESHOLD",
    "BOOST_MIN_PP",
    "BOOST_MAX_PP",
    "BOOST_CAP_PP",
    "MATCHDAY_1_BASE",
    "MATCHDAY_2_BASE",
    "MATCHDAY_3_BOTH_QUALIFIED",
    "MATCHDAY_3_BOTH_ELIMINATED",
    "MATCHDAY_3_BOTH_NEED_DRAW",
    "MATCHDAY_3_ASYMMETRIC",
    "MATCHDAY_3_GENERIC",
    "KNOCKOUT_MAX_SCORE",
    # Reason codes (for tests / UI):
    "RC_INSUFFICIENT_GROUP_DATA",
    "RC_KNOCKOUT_CAPPED",
    "RC_MATCHDAY_1",
    "RC_MATCHDAY_2",
    "RC_MATCHDAY_3_BOTH_QUALIFIED",
    "RC_MATCHDAY_3_BOTH_ELIMINATED",
    "RC_MATCHDAY_3_BOTH_NEED_DRAW",
    "RC_MATCHDAY_3_ASYMMETRIC",
    "RC_MATCHDAY_3_GENERIC",
    "RC_BOOSTER_APPLIED",
    "RC_BOOSTER_BELOW_THRESHOLD",
    "RC_BOTH_NEED_POINTS_INFERRED",
]
