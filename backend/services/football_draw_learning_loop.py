"""Sprint-B · B2 — Football Draw Learning Loop.

Thin wrapper around ``football_learning_loop_base`` keyed on the
DRAW market. Explicit field names prevent any cross-market data leak
(e.g. reading goals data into the draw analyser).
"""
from __future__ import annotations

from .football_learning_loop_base import compute_learning_loop_metrics

MARKET_LABEL    = "DRAW"
PROBABILITY_KEY = "draw_probability"
MARKET_ODD_KEY  = "draw"
HIT_KEY         = "draw_hit"


def run_draw_learning_loop(snapshots: list[dict]) -> dict:
    return compute_learning_loop_metrics(
        snapshots=snapshots,
        probability_key=PROBABILITY_KEY,
        market_odd_key=MARKET_ODD_KEY,
        hit_key=HIT_KEY,
        market_label=MARKET_LABEL,
    )


__all__ = ["run_draw_learning_loop", "MARKET_LABEL",
           "PROBABILITY_KEY", "MARKET_ODD_KEY", "HIT_KEY"]
