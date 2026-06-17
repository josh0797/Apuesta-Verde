"""Sprint-B · B2 — Football BTTS Learning Loop.

Thin wrapper around ``football_learning_loop_base`` keyed on the
BTTS (Both Teams To Score) YES market.
"""
from __future__ import annotations

from .football_learning_loop_base import compute_learning_loop_metrics

MARKET_LABEL    = "BTTS_YES"
PROBABILITY_KEY = "btts_probability"
MARKET_ODD_KEY  = "btts_yes"
HIT_KEY         = "btts_hit"


def run_btts_learning_loop(snapshots: list[dict]) -> dict:
    return compute_learning_loop_metrics(
        snapshots=snapshots,
        probability_key=PROBABILITY_KEY,
        market_odd_key=MARKET_ODD_KEY,
        hit_key=HIT_KEY,
        market_label=MARKET_LABEL,
    )


__all__ = ["run_btts_learning_loop", "MARKET_LABEL",
           "PROBABILITY_KEY", "MARKET_ODD_KEY", "HIT_KEY"]
