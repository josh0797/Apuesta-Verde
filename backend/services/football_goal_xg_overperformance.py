"""Sprint-D9.1 · Goal-minus-xG overperformance feature (point-in-time).

A pure, deterministic feature that captures *recent goal-creation
overperformance* of a team versus its own historical attacking
baseline. The semantic contract is::

    overperformance(team, t)
       = mean_{i in last_L15_matches_of(team, before t)}
             (goals_for_i  −  xg_proxy_i)

where ``xg_proxy_i`` is the team's own goals-for moving average over
the **L5 matches that strictly precede match i** (no peeking).

Why this proxy
--------------
football-data.co.uk does not publish xG. The Poisson rate ``xg_proxy``
is the maximum-likelihood point estimate of the team's scoring
intensity from its recent past, conditional on the data we actually
have. ``actual_goals − xg_proxy`` is therefore a noisy estimator of
*unusual finishing variance*, but it is computed exclusively from
data available BEFORE match i, which preserves the point-in-time
contract of the rest of the engine.

If the rolling window does not contain enough matches for a stable
estimate (``< MIN_L15_FOR_XG_OVERPERF`` matches in the trailing
window of size L15), the function returns ``None`` so downstream
consumers can fail soft.

Strictly point-in-time
----------------------
* No match data at ``t`` enters the computation for any team's
  features at ``t``.
* For each match in the L15 window, the xg_proxy uses ONLY the L5
  matches before that match.
* All ordering relies on the canonical ``matches_sorted`` slice that
  ``build_point_in_time_features`` already maintains.
"""
from __future__ import annotations

from typing import Optional

L5_PROXY_WINDOW: int = 5
L15_HISTORY_WINDOW: int = 15
MIN_L15_FOR_XG_OVERPERF: int = 6
MIN_L5_FOR_XG_PROXY: int  = 3


def _team_goals_for_in(slice_: list[dict], team: str) -> list[int]:
    """Goals scored by ``team`` across the ordered ``slice_``."""
    out: list[int] = []
    for m in slice_:
        if m.get("home_team") == team:
            out.append(int(m.get("fthg", 0) or 0))
        elif m.get("away_team") == team:
            out.append(int(m.get("ftag", 0) or 0))
    return out


def _team_history_indices(team: str, matches_sorted: list[dict],
                             target_index: int) -> list[int]:
    """Indices ``i < target_index`` where ``team`` participates."""
    return [
        i for i in range(target_index)
        if (matches_sorted[i].get("home_team") == team
            or matches_sorted[i].get("away_team") == team)
    ]


def _goal_for_at(match: dict, team: str) -> Optional[int]:
    if match.get("home_team") == team:
        return int(match.get("fthg", 0) or 0)
    if match.get("away_team") == team:
        return int(match.get("ftag", 0) or 0)
    return None


def compute_goal_xg_overperformance(
    team: str, matches_sorted: list[dict], target_index: int,
    *,
    l15: int = L15_HISTORY_WINDOW,
    l5_proxy: int = L5_PROXY_WINDOW,
    min_l15: int = MIN_L15_FOR_XG_OVERPERF,
    min_l5_proxy: int = MIN_L5_FOR_XG_PROXY,
) -> Optional[float]:
    """Return the mean ``goals_for − xg_proxy`` of ``team`` over its
    last ``l15`` matches before ``target_index``.

    Returns ``None`` when:
      * the team has fewer than ``min_l15`` prior matches, OR
      * NONE of those matches has a stable xg_proxy (i.e. all of them
        had fewer than ``min_l5_proxy`` preceding matches).

    Strictly point-in-time — the xg_proxy of match ``j`` uses ONLY
    matches with index < j.
    """
    hist_idx = _team_history_indices(team, matches_sorted, target_index)
    if len(hist_idx) < min_l15:
        return None
    # Take the last ``l15`` matches.
    window_idx = hist_idx[-l15:]
    deltas: list[float] = []
    for j in window_idx:
        prior_idx = [k for k in hist_idx if k < j]
        if len(prior_idx) < min_l5_proxy:
            continue
        proxy_slice = [matches_sorted[k] for k in prior_idx[-l5_proxy:]]
        proxy_gf = _team_goals_for_in(proxy_slice, team)
        if len(proxy_gf) < min_l5_proxy:
            continue
        xg_proxy = sum(proxy_gf) / len(proxy_gf)
        actual = _goal_for_at(matches_sorted[j], team)
        if actual is None:
            continue
        deltas.append(actual - xg_proxy)
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 4)


__all__ = [
    "compute_goal_xg_overperformance",
    "L5_PROXY_WINDOW",
    "L15_HISTORY_WINDOW",
    "MIN_L15_FOR_XG_OVERPERF",
    "MIN_L5_FOR_XG_PROXY",
]
