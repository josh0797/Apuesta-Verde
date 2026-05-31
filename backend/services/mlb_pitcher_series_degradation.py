"""
MLB Pitcher / Lineup In-Series Degradation (Module #3)

Applies a +0.0 / +0.4 / +0.8 expected-runs adjustment to the engine's
base projection based on which game of the series this is and whether
the starter already faced the opposing lineup. Pure function, no I/O.

Empirical basis (MLB, regular season):
  - G1: starters fresh, hitters without book → ER base.
  - G2: hitters adjust → +0.3..0.5 runs typical.
  - G3: hitters have full book on starter →  +0.6..1.0 runs typical.
  - Same-starter-faces-same-lineup amplifier: +0.3 (third-time-through).
"""

from __future__ import annotations

from typing import Optional


def apply_series_degradation(
    expected_runs: float,
    game_number_in_series: int,
    starter_faced_lineup_before: bool = False,
) -> dict:
    """Returns a dict ready to attach to the pick payload.

    Output keys:
      original_er, adjusted_er, adjustment, game_in_series, degradation_label,
      starter_faced_lineup_before.
    """
    try:
        base = float(expected_runs)
    except (TypeError, ValueError):
        return {
            "original_er":        None,
            "adjusted_er":        None,
            "adjustment":         0.0,
            "game_in_series":     game_number_in_series,
            "degradation_label":  "NO_INPUT",
        }
    try:
        g = int(game_number_in_series or 1)
    except (TypeError, ValueError):
        g = 1

    if g <= 1:
        adjustment, label = 0.0, "FRESH_SERIES"
    elif g == 2:
        adjustment, label = 0.4, "MID_SERIES"
    else:
        adjustment, label = 0.8, "LATE_SERIES_DEGRADATION"

    if starter_faced_lineup_before and g >= 2:
        adjustment += 0.3

    return {
        "original_er":                  round(base, 2),
        "adjusted_er":                  round(base + adjustment, 2),
        "adjustment":                   round(adjustment, 2),
        "game_in_series":               g,
        "degradation_label":            label,
        "starter_faced_lineup_before":  bool(starter_faced_lineup_before),
    }


__all__ = ["apply_series_degradation"]
