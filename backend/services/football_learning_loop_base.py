"""Sprint-B · B2 — Football Learning Loop Base
=============================================

Generic, parametrised aggregator that computes hit-rate, ROI and
calibration metrics for **one market** from the settled snapshots in
``football_match_learning_snapshots``.

Design rationale
----------------
Draw, Corners (Over 8.5), BTTS and Over-2.5 all share the same math:

    For each settled snapshot:
      estimated_probability = pre_match_inputs[<prob_key>]
      market_implied_prob   = 1 / market_odds[<odd_key>]   (or None)
      actual_result         = post_match_outputs[<hit_key>]  (bool)

Aggregating over the population:

    sample_size           = count
    hit_rate              = sum(hit) / count
    mean_estimated_prob   = avg(estimated_probability)
    mean_market_implied   = avg(market_implied_prob)     when available
    mean_edge_predicted   = avg(estimated - market_implied)
    mean_edge_realised    = hit_rate - mean_market_implied
    calibration_gap       = mean_estimated_prob - hit_rate
    brier_score           = avg((estimated - hit)^2)
    roi_if_bet            = sum(profit) / count    (unit stake)
        where profit = (odd-1) when hit else -1

Making each loop a **thin wrapper** around this base prevents the
corners-confused-with-goals class of bugs the user reported: each
market explicitly names its own keys in ``pre_match_inputs`` and
``post_match_outputs``, so the loops cannot accidentally read
cross-market data.

Fail-soft invariants
--------------------
* Never raises.
* Skips snapshots missing required keys (counted under ``skipped``).
* Returns a result dict even when sample_size == 0.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

log = logging.getLogger("football_learning_loop_base")


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _implied_prob_from_odd(odd: Any) -> Optional[float]:
    """Return ``1/odd`` for decimal odd ``odd`` (e.g. 1.85 → 0.541)."""
    f = _safe_float(odd)
    if f is None or f <= 1.0:
        return None
    return 1.0 / f


def compute_learning_loop_metrics(
    *,
    snapshots: list[dict],
    probability_key: str,
    market_odd_key: str,
    hit_key: str,
    market_label: str,
) -> dict:
    """Aggregate hit-rate/ROI/calibration for one market.

    Parameters
    ----------
    snapshots
        Iterable of settled snapshot documents (POST_MATCH stage).
    probability_key
        Name of the probability field inside ``pre_match_inputs``
        (e.g. ``"draw_probability"``).
    market_odd_key
        Name of the odd field inside ``pre_match_inputs.market_odds``
        (e.g. ``"draw"``, ``"over25"``, ``"over85_corners"``).
    hit_key
        Name of the boolean outcome field inside
        ``post_match_outputs`` (e.g. ``"draw_hit"``, ``"over25_hit"``).
    market_label
        Display label (echoed in the result dict).

    Returns
    -------
    dict
        ``{
            "market":               str,
            "sample_size":          int,
            "skipped":              int,
            "hit_rate":             float | None,
            "mean_estimated_prob":  float | None,
            "mean_market_implied":  float | None,
            "mean_edge_predicted":  float | None,
            "mean_edge_realised":   float | None,
            "calibration_gap":      float | None,
            "brier_score":          float | None,
            "roi_if_bet":           float | None,
            "available":            bool,
        }``
    """
    counted    = 0
    hits       = 0
    skipped    = 0
    est_probs: list[float] = []
    implied_probs: list[float] = []
    edges_predicted: list[float] = []
    brier_sum  = 0.0
    profits: list[float] = []

    if not isinstance(snapshots, list):
        snapshots = list(snapshots or [])

    for snap in snapshots:
        if not isinstance(snap, dict):
            skipped += 1
            continue
        pre = snap.get("pre_match_inputs") or {}
        post = snap.get("post_match_outputs") or {}
        hit  = post.get(hit_key)
        if hit is None:
            skipped += 1
            continue
        # Coerce hit to int 0/1.
        hit_i = 1 if bool(hit) else 0

        est = _safe_float(pre.get(probability_key))
        # Probability values can be in [0,1] or in [0,100]; normalise.
        if est is not None and est > 1.5:
            est = est / 100.0
        if est is not None and 0.0 <= est <= 1.0:
            est_probs.append(est)
            brier_sum += (est - hit_i) ** 2
        else:
            est = None

        odd = (pre.get("market_odds") or {}).get(market_odd_key)
        impl = _implied_prob_from_odd(odd)
        if impl is not None:
            implied_probs.append(impl)
            if est is not None:
                edges_predicted.append(est - impl)
            # Profit accounting (unit stake).
            f = _safe_float(odd)
            if f is not None and f > 1.0:
                profits.append((f - 1.0) if hit_i else -1.0)

        counted += 1
        if hit_i:
            hits += 1

    if counted == 0:
        return {
            "market":              market_label,
            "sample_size":         0,
            "skipped":             skipped,
            "hit_rate":            None,
            "mean_estimated_prob": None,
            "mean_market_implied": None,
            "mean_edge_predicted": None,
            "mean_edge_realised":  None,
            "calibration_gap":     None,
            "brier_score":         None,
            "roi_if_bet":          None,
            "available":           False,
        }

    hit_rate = hits / counted
    mean_est = (sum(est_probs) / len(est_probs)) if est_probs else None
    mean_imp = (sum(implied_probs) / len(implied_probs)) if implied_probs else None
    mean_edge_pred = (sum(edges_predicted) / len(edges_predicted)
                       if edges_predicted else None)
    mean_edge_real = (hit_rate - mean_imp) if mean_imp is not None else None
    calibration_gap = (mean_est - hit_rate) if mean_est is not None else None
    brier = (brier_sum / len(est_probs)) if est_probs else None
    roi = (sum(profits) / len(profits)) if profits else None

    return {
        "market":              market_label,
        "sample_size":         counted,
        "skipped":             skipped,
        "hit_rate":            round(hit_rate, 4),
        "mean_estimated_prob": round(mean_est, 4) if mean_est is not None else None,
        "mean_market_implied": round(mean_imp, 4) if mean_imp is not None else None,
        "mean_edge_predicted": round(mean_edge_pred, 4) if mean_edge_pred is not None else None,
        "mean_edge_realised":  round(mean_edge_real, 4) if mean_edge_real is not None else None,
        "calibration_gap":     round(calibration_gap, 4) if calibration_gap is not None else None,
        "brier_score":         round(brier, 4) if brier is not None else None,
        "roi_if_bet":          round(roi, 4) if roi is not None else None,
        "available":           True,
    }


__all__ = [
    "compute_learning_loop_metrics",
    "_implied_prob_from_odd",
    "_safe_float",
]
