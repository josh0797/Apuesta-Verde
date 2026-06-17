"""Sprint-B · B2 — Football Totals Learning Loop (Over 2.5 Goles).

Thin wrapper around ``football_learning_loop_base`` keyed on the
OVER 2.5 GOALS market. Intentionally separated from the corners loop
so the user's reported confusion (corners-vs-goals) cannot recur.
"""
from __future__ import annotations

from .football_learning_loop_base import compute_learning_loop_metrics

MARKET_LABEL    = "OVER_2_5_GOALS"
PROBABILITY_KEY = "over25_probability"
MARKET_ODD_KEY  = "over25"
HIT_KEY         = "over25_hit"


def run_totals_learning_loop(snapshots: list[dict]) -> dict:
    return compute_learning_loop_metrics(
        snapshots=snapshots,
        probability_key=PROBABILITY_KEY,
        market_odd_key=MARKET_ODD_KEY,
        hit_key=HIT_KEY,
        market_label=MARKET_LABEL,
    )


__all__ = ["run_totals_learning_loop", "MARKET_LABEL",
           "PROBABILITY_KEY", "MARKET_ODD_KEY", "HIT_KEY"]
