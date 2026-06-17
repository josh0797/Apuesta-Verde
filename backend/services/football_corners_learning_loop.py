"""Sprint-B · B2 — Football Corners Learning Loop (Over 8.5).

Thin wrapper around ``football_learning_loop_base`` keyed on the
CORNERS OVER 8.5 market. Explicit field names prevent any
confusion with goals data — a class of bug the user surfaced in
the F58 panel.

The corners loop deliberately reads:
  * probability from pre_match_inputs["<probability_key>"]      (NOT goals)
  * odd from         pre_match_inputs["market_odds"]["<odd_key>"]  (NOT over25)
  * hit from         post_match_outputs["<hit_key>"]              (NOT over25_hit)

This means no key in this module overlaps with the totals/goals loops.
"""
from __future__ import annotations

from .football_learning_loop_base import compute_learning_loop_metrics

MARKET_LABEL    = "CORNERS_OVER_8_5"
# The probability field for corners is derived from expected_corners
# during snapshot creation; pre_match_inputs exposes the explicit
# probability under "corners_over85_probability". When unavailable we
# fall back to None, NOT to over25_probability.
PROBABILITY_KEY = "corners_over85_probability"
MARKET_ODD_KEY  = "over85_corners"
HIT_KEY         = "over85_corners_hit"


def run_corners_learning_loop(snapshots: list[dict]) -> dict:
    return compute_learning_loop_metrics(
        snapshots=snapshots,
        probability_key=PROBABILITY_KEY,
        market_odd_key=MARKET_ODD_KEY,
        hit_key=HIT_KEY,
        market_label=MARKET_LABEL,
    )


__all__ = ["run_corners_learning_loop", "MARKET_LABEL",
           "PROBABILITY_KEY", "MARKET_ODD_KEY", "HIT_KEY"]
