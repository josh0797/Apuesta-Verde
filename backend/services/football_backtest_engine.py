"""Sprint-D · Football Backtest Engine.

Walks chronologically through historical matches, calling
``compute_draw_potential`` on **point-in-time-only** features, places
simulated bets when the verdict label is VALUE / STRONG_VALUE, settles
against the real outcome, and returns a per-pick log + the running
bankroll curve.

Anti-leakage invariants enforced here
-------------------------------------
* ELO and goal-history are updated AFTER the prediction has been
  locked in (handled inside the historical ingestor).
* Calibration is **walk-forward only**: at step ``i`` the calibrator
  may use ONLY picks settled at steps ``0..i-1``.
* Closing odds are NOT used; only the opening B365 draw odd which is
  available before kickoff.
* The engine never reads ``matches[i]`` outcome fields when building
  features for prediction.
"""
from __future__ import annotations

import logging
from typing import Optional

from .football_draw_potential import (
    compute_draw_potential,
    LABEL_VALUE_DRAW, LABEL_STRONG_VALUE,
)
from .football_historical_ingestor import build_point_in_time_features

log = logging.getLogger("football_backtest_engine")

# Recalibration cadence — every N picks, refit the calibrator from
# settled picks so far. The fitter sees ONLY settled past data, never
# the current pick.
DEFAULT_RECAL_EVERY = 25


def _isotonic_like_calibrator(history: list[tuple[float, int]]):
    """Return a *very* lightweight monotonic calibrator from
    (predicted_prob, outcome) pairs. Sufficient for backtests; the
    production path uses the dedicated ``football_draw_potential_calibrator``
    when wired.

    The fit is a per-decile mean: at predict time we look up the bucket
    of the predicted probability and return the empirical hit rate.
    Falls back to identity when buckets are sparse.
    """
    if not history:
        return lambda p: p
    buckets: list[list[float]] = [[] for _ in range(10)]
    for p, y in history:
        idx = max(0, min(9, int(p * 10)))
        buckets[idx].append(float(y))
    bucket_means = [
        (sum(b) / len(b)) if b else None for b in buckets
    ]
    # Build a monotonic shape by averaging with neighbours where empty.
    for i, m in enumerate(bucket_means):
        if m is None:
            # Look left/right for nearest filled buckets.
            left  = next((bucket_means[k] for k in range(i - 1, -1, -1)
                          if bucket_means[k] is not None), None)
            right = next((bucket_means[k] for k in range(i + 1, 10)
                          if bucket_means[k] is not None), None)
            if left is not None and right is not None:
                bucket_means[i] = (left + right) / 2.0
            elif left is not None:
                bucket_means[i] = left
            elif right is not None:
                bucket_means[i] = right
    return lambda p: float(bucket_means[max(0, min(9, int(float(p) * 10)))]
                            if bucket_means[max(0, min(9, int(float(p) * 10)))] is not None
                            else p)


def _kelly_fraction(p: float, decimal_odd: float) -> float:
    """Kelly criterion fractional stake (clamped to [0, 0.10])."""
    b = decimal_odd - 1.0
    if b <= 0:
        return 0.0
    f = (p * (b + 1) - 1) / b
    return max(0.0, min(0.10, f))


def run_backtest(
    matches_sorted: list[dict],
    *,
    market: str = "DRAW",
    min_edge_pp: float = 4.0,
    use_calibration: bool = False,
    walk_forward: bool = True,
    stake: str = "flat",
    stake_unit: float = 1.0,
    recal_every: int = DEFAULT_RECAL_EVERY,
) -> dict:
    """Run the backtest. Returns a dict with picks + summary.

    Parameters
    ----------
    matches_sorted
        Output of ``parse_football_data_csv`` (sorted ascending by date).
    market
        Currently only ``"DRAW"`` is implemented (Sprint-D MVP).
    min_edge_pp
        Minimum predicted-vs-implied edge (in **percentage points**)
        required to fire a pick. Default 4pp matches the VALUE
        threshold in ``football_draw_potential``.
    use_calibration
        When True, every ``recal_every`` picks the calibrator refits
        on PAST settled picks and is applied to subsequent
        predictions.
    walk_forward
        When True (default), calibration uses only past picks.
    stake
        ``"flat"`` or ``"kelly_fractional"``.
    """
    if market != "DRAW":
        raise NotImplementedError(
            "Sprint-D MVP implements DRAW only. "
            f"Got market={market!r}."
        )

    picks: list[dict] = []
    calib_history: list[tuple[float, int]] = []   # (pred_prob, hit) settled so far
    calibrator = (lambda p: p)
    skipped:  list[dict] = []

    for i, m in enumerate(matches_sorted):
        try:
            features = build_point_in_time_features(matches_sorted, i)
        except Exception as exc:   # noqa: BLE001
            skipped.append({"index": i, "reason": f"ingest_error:{exc}"})
            continue

        # Need at least 3 prior matches per team for any signal.
        if (features["_audit"]["home_hist_n"] < 3
                or features["_audit"]["away_hist_n"] < 3):
            skipped.append({"index": i, "reason": "insufficient_history"})
            continue
        if features["market_implied_draw_prob"] is None:
            skipped.append({"index": i, "reason": "no_draw_odd"})
            continue

        # Strip the audit before passing to the predictor (its signature
        # doesn't accept it).
        pred_kwargs = {k: v for k, v in features.items() if k != "_audit"}
        verdict = compute_draw_potential(**pred_kwargs)

        # Apply walk-forward calibration if enabled.
        prob_pct = verdict["draw_probability"]
        if use_calibration and prob_pct is not None and calib_history:
            try:
                calibrated = calibrator(prob_pct / 100.0) * 100.0
                # Recompute edge against the same market implied.
                market_pct = verdict["market_implied"]
                if market_pct is not None:
                    new_edge = round(calibrated - market_pct, 1)
                    verdict["draw_probability"] = round(calibrated, 1)
                    verdict["edge"]             = new_edge
                    if new_edge >= 8.0:
                        verdict["label"] = LABEL_STRONG_VALUE
                    elif new_edge >= 4.0:
                        verdict["label"] = LABEL_VALUE_DRAW
                    elif new_edge >= 0:
                        verdict["label"] = "FAIR_DRAW_NO_EDGE"
                    else:
                        verdict["label"] = "NO_DRAW_VALUE"
            except Exception as exc:  # noqa: BLE001
                log.debug("calibration failed: %s", exc)

        # Decision: fire a pick only on VALUE/STRONG_VALUE + edge ≥ min.
        label = verdict.get("label")
        edge  = verdict.get("edge")
        fires = (label in (LABEL_VALUE_DRAW, LABEL_STRONG_VALUE)
                  and edge is not None and edge >= min_edge_pp)

        hit = (m["ftr"] == "D")
        if fires:
            odd = m["odd_draw"]
            prob_used = verdict["draw_probability"] / 100.0
            if stake == "kelly_fractional":
                f = _kelly_fraction(prob_used, odd)
                stake_amt = stake_unit * f
            else:
                stake_amt = stake_unit
            if stake_amt > 0:
                pnl = (odd - 1.0) * stake_amt if hit else -stake_amt
                picks.append({
                    "date":           m["date"].isoformat(),
                    "competition":    m.get("competition") or "",
                    "home":           m["home_team"],
                    "away":           m["away_team"],
                    "odd_draw":       odd,
                    "predicted_prob": round(prob_used, 4),
                    "market_prob":    round(verdict["market_implied"] / 100.0, 4),
                    "edge_pp":        edge,
                    "label":          label,
                    "stake":          round(stake_amt, 4),
                    "hit":            hit,
                    "pnl":            round(pnl, 4),
                    "actual_score":   f"{m['fthg']}-{m['ftag']}",
                })

        # Update calibrator history AFTER the pick is recorded.
        # This is the anti-leakage step: future picks may see THIS
        # pick's outcome, but the current pick used calibrator fitted
        # only on STRICTLY past picks.
        if prob_pct is not None:
            calib_history.append((verdict["draw_probability"] / 100.0,
                                   1 if hit else 0))
            if (use_calibration and walk_forward
                    and (len(calib_history) % recal_every == 0)):
                calibrator = _isotonic_like_calibrator(calib_history)

    return {
        "market":           market,
        "min_edge_pp":      min_edge_pp,
        "stake_mode":       stake,
        "use_calibration":  use_calibration,
        "walk_forward":     walk_forward,
        "picks":            picks,
        "skipped":          skipped,
        "n_matches_total":  len(matches_sorted),
    }


__all__ = [
    "run_backtest",
    "_isotonic_like_calibrator",
    "_kelly_fraction",
    "DEFAULT_RECAL_EVERY",
]
