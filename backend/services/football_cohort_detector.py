"""Sprint-D5 · Football Cohort Detector.

Pure-function module that, given a backtest pick row and (optionally)
the matching features row, tags it with one or more *cohort labels*
that describe the macro-archetype of the bet:

* ``DOMINANT_FAVORITE_DRAW_VALUE``  — classic “Spain vs Cape Verde”
  pattern: an ELO-dominant favourite, defensive underdog, inflated
  draw price, and model edge ≥ 8pp. Often correlates with low-tempo
  matches.
* ``TOURNAMENT_GROUP_STAGE_DRAW_VALUE`` — group-stage match in a
  national tournament (WC / Euro) with positive model edge and a
  meaningful tournament context score.
* ``LOW_GOAL_UNDERDOG_BLOCK`` — low-tempo match (combined xG proxy
  below threshold) where the underdog has a strong defensive profile
  and the model finds value on the draw.
* ``TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS`` — “tail edge” bucket
  (edge_pp ≥ 15) which D4 identified as systematically losing.

Each cohort is independent (a single pick can belong to multiple
cohorts simultaneously), so the function returns a SET of labels +
a per-label audit dict.

Strict invariants
-----------------
* Pure function, no I/O, never raises.
* Conservative thresholds (see TUNABLES).
* All thresholds are configurable via kwargs.
"""
from __future__ import annotations

from typing import Optional

# ─── TUNABLES ───────────────────────────────────────────────────────────
# Dominant-favourite ELO delta: 1740 vs 1450 ≈ 290pts. We require ≥ 150
# as a conservative threshold (covers Spain-Cape Verde and similar).
DOMINANT_FAVORITE_ELO_DELTA: float   = 150.0
DOMINANT_FAVORITE_EDGE_PP:    float  = 8.0

# Group stage: tournament_context_score ≥ 0.30 (matchday 2 or 3 default).
GROUP_STAGE_MIN_CONTEXT_SCORE: float = 0.30
GROUP_STAGE_MIN_EDGE_PP:       float = 4.0

# Low-goal underdog block:
#   * combined xG proxy (home + away L5) < 2.4 OR sum of goal averages
#   * underdog defensive: weaker team's "goals against" L5 ≤ 1.0
LOW_GOAL_TOTAL_XG_MAX:   float = 2.4
LOW_GOAL_UNDERDOG_GA_MAX: float = 1.1

# Tail edge over-confidence (D4 finding).
TAIL_EDGE_PP_MIN: float = 15.0


# ─── Cohort labels ──────────────────────────────────────────────────────
COHORT_DOMINANT_FAVORITE     = "DOMINANT_FAVORITE_DRAW_VALUE"
COHORT_TOURNAMENT_GROUP      = "TOURNAMENT_GROUP_STAGE_DRAW_VALUE"
COHORT_LOW_GOAL_UNDERDOG     = "LOW_GOAL_UNDERDOG_BLOCK"
COHORT_TAIL_EDGE             = "TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS"

ALL_COHORTS: tuple[str, ...] = (
    COHORT_DOMINANT_FAVORITE,
    COHORT_TOURNAMENT_GROUP,
    COHORT_LOW_GOAL_UNDERDOG,
    COHORT_TAIL_EDGE,
)


# ════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════
def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:    # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def _edge_pp_from_pick(pick: dict) -> Optional[float]:
    """Recover the edge in pp from a pick row.

    Picks emitted by the engine carry ``edge_pp`` directly (market mode)
    or, in calibration-only mode, only ``predicted_prob`` and
    ``market_prob``. We reconstruct edge = (pred − market) × 100
    when both are present; otherwise return None.
    """
    e = _safe_float(pick.get("edge_pp"))
    if e is not None:
        return e
    pp = _safe_float(pick.get("predicted_prob"))
    mp = _safe_float(pick.get("market_prob"))
    if pp is not None and mp is not None:
        return (pp - mp) * 100.0
    return None


# ════════════════════════════════════════════════════════════════════════
# Detectors
# ════════════════════════════════════════════════════════════════════════
def is_dominant_favorite_draw_value(
    pick: dict, features: Optional[dict] = None, *,
    min_elo_delta: float = DOMINANT_FAVORITE_ELO_DELTA,
    min_edge_pp:   float = DOMINANT_FAVORITE_EDGE_PP,
) -> bool:
    """Classic 'Spain vs Cape Verde' pattern:
       * dominant favourite by ELO (≥ min_elo_delta)
       * model edge on the draw ≥ min_edge_pp
    """
    edge = _edge_pp_from_pick(pick)
    if edge is None or edge < min_edge_pp:
        return False
    if features is None:
        return False
    eh = _safe_float(features.get("elo_home"))
    ea = _safe_float(features.get("elo_away"))
    if eh is None or ea is None:
        return False
    return abs(eh - ea) >= min_elo_delta


def is_tournament_group_stage_draw_value(
    pick: dict, features: Optional[dict] = None, *,
    min_context_score: float = GROUP_STAGE_MIN_CONTEXT_SCORE,
    min_edge_pp:       float = GROUP_STAGE_MIN_EDGE_PP,
) -> bool:
    if not pick.get("is_group_stage"):
        return False
    # In no-market mode the engine does not compute edge_pp (no
    # market_prob available); fall back to predicted_prob being above
    # the historical league draw baseline (~24%).
    edge = _edge_pp_from_pick(pick)
    if edge is None:
        pp = _safe_float(pick.get("predicted_prob"))
        if pp is not None:
            edge = (pp - 0.24) * 100.0
    if edge is None or edge < min_edge_pp:
        return False
    tcs = _safe_float(pick.get("tournament_context_score"))
    if tcs is None and features is not None:
        tcs = _safe_float(features.get("tournament_context_score"))
    if tcs is None:
        # If we don't have a context score, fall back to is_group_stage.
        return True
    return tcs >= min_context_score


def is_low_goal_underdog_block(
    pick: dict, features: Optional[dict] = None, *,
    total_xg_max:      float = LOW_GOAL_TOTAL_XG_MAX,
    underdog_ga_max:   float = LOW_GOAL_UNDERDOG_GA_MAX,
) -> bool:
    """Low-tempo, defensive-underdog match.

    Uses goal averages (proxy) from the features row when present.
    """
    if features is None:
        return False
    xh = _safe_float(features.get("xg_home_l5"))
    xa = _safe_float(features.get("xg_away_l5"))
    if xh is None or xa is None:
        return False
    total = xh + xa
    if total > total_xg_max:
        return False
    # Identify the weaker team (lower ELO) and check its goals-against
    # average — if the favourite is at home, the weaker side is away.
    eh = _safe_float(features.get("elo_home"))
    ea = _safe_float(features.get("elo_away"))
    if eh is None or ea is None:
        return False
    weaker_ga: Optional[float]
    if eh > ea:
        weaker_ga = _safe_float(features.get("goal_avg_against_away"))
    else:
        weaker_ga = _safe_float(features.get("goal_avg_against_home"))
    if weaker_ga is None:
        return False
    return weaker_ga <= underdog_ga_max


def is_tail_edge_overconfidence(
    pick: dict, features: Optional[dict] = None, *,
    min_edge_pp: float = TAIL_EDGE_PP_MIN,
) -> bool:
    edge = _edge_pp_from_pick(pick)
    return edge is not None and edge >= min_edge_pp


# ════════════════════════════════════════════════════════════════════════
# Top-level
# ════════════════════════════════════════════════════════════════════════
def detect_cohorts(pick: dict,
                   features: Optional[dict] = None) -> dict:
    """Return ``{"cohorts": [...], "audit": {<cohort>: {...}, ...}}``.

    Multiple cohorts may apply to a single pick.
    """
    edge = _edge_pp_from_pick(pick)
    cohorts: list[str] = []
    audit: dict = {
        "edge_pp_used": edge,
        "elo_home":     None if features is None else features.get("elo_home"),
        "elo_away":     None if features is None else features.get("elo_away"),
        "tournament_context_score":
            pick.get("tournament_context_score"),
        "is_group_stage": bool(pick.get("is_group_stage")),
    }

    if is_dominant_favorite_draw_value(pick, features):
        cohorts.append(COHORT_DOMINANT_FAVORITE)
    if is_tournament_group_stage_draw_value(pick, features):
        cohorts.append(COHORT_TOURNAMENT_GROUP)
    if is_low_goal_underdog_block(pick, features):
        cohorts.append(COHORT_LOW_GOAL_UNDERDOG)
    if is_tail_edge_overconfidence(pick, features):
        cohorts.append(COHORT_TAIL_EDGE)

    return {"cohorts": cohorts, "audit": audit}


def summarise_picks_by_cohort(
    picks: list[dict], features_by_index: Optional[list[dict]] = None,
) -> dict:
    """Aggregate the picks into per-cohort statistics::

        {
          "<cohort>": {
            "n": <int>,
            "won": <int>,
            "hit_rate": <float | None>,
            "examples": [<top-5 picks>],
          },
          ...
        }

    ``features_by_index`` is optional and parallel to ``picks``; when
    provided, the detector uses the full features for accurate ELO /
    xG-based cohort assignment.
    """
    out: dict[str, dict] = {c: {"n": 0, "won": 0, "examples": []}
                              for c in ALL_COHORTS}
    for i, p in enumerate(picks):
        feats = features_by_index[i] if features_by_index and i < len(features_by_index) else None
        det = detect_cohorts(p, feats)
        for c in det["cohorts"]:
            row = out[c]
            row["n"] += 1
            row["won"] += int(bool(p.get("hit")))
            if len(row["examples"]) < 5:
                row["examples"].append({
                    "date":           p.get("date"),
                    "home":           p.get("home"),
                    "away":           p.get("away"),
                    "competition":    p.get("competition"),
                    "predicted_prob": p.get("predicted_prob"),
                    "market_prob":    p.get("market_prob"),
                    "edge_pp":        p.get("edge_pp")
                                       or det["audit"]["edge_pp_used"],
                    "hit":            p.get("hit"),
                    "actual_score":   p.get("actual_score"),
                    "label":          p.get("label"),
                    "odd_draw":       p.get("odd_draw"),
                    "is_group_stage": p.get("is_group_stage"),
                    "tournament_context_score":
                        p.get("tournament_context_score"),
                })
    for c, row in out.items():
        row["hit_rate"] = (round(row["won"] / row["n"], 4)
                            if row["n"] else None)
    return out


__all__ = [
    "detect_cohorts", "summarise_picks_by_cohort",
    "is_dominant_favorite_draw_value",
    "is_tournament_group_stage_draw_value",
    "is_low_goal_underdog_block",
    "is_tail_edge_overconfidence",
    "ALL_COHORTS",
    "COHORT_DOMINANT_FAVORITE",
    "COHORT_TOURNAMENT_GROUP",
    "COHORT_LOW_GOAL_UNDERDOG",
    "COHORT_TAIL_EDGE",
    "DOMINANT_FAVORITE_ELO_DELTA",
    "DOMINANT_FAVORITE_EDGE_PP",
    "GROUP_STAGE_MIN_CONTEXT_SCORE",
    "GROUP_STAGE_MIN_EDGE_PP",
    "LOW_GOAL_TOTAL_XG_MAX",
    "LOW_GOAL_UNDERDOG_GA_MAX",
    "TAIL_EDGE_PP_MIN",
]
