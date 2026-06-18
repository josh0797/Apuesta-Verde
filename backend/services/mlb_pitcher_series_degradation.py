"""
MLB Pitcher / Lineup In-Series Degradation (Module #3).

Applies an expected-runs adjustment to the engine's base projection based
on which game of the series this is and whether the starter already faced
the opposing lineup.

D9.3-C extension — Slope-aware policy
-------------------------------------
Optionally accepts the active-series slope band (from
`mlb_series_total_signal.calculate_series_total_signal.slope_band`) and
modulates the in-series component multiplicatively:

  CONTRACTION_STRONG (<-1.0)         → 0.50  (the series is collapsing —
                                              halve the upward bias)
  CONTRACTION_LIGHT  (-1.0..-0.30)   → 0.75
  STABLE / UNKNOWN / INSUFFICIENT    → 1.00
  EXPANSION_LIGHT    (+0.30..+1.0)   → 1.10  (slight amplification)
  EXPANSION_STRONG   (>+1.0)         → 1.25  (hard amplification)

The third-time-through (starter_faced_lineup_before) component is NOT
multiplied — it is a physiological "book on the starter" effect that the
slope shouldn't override.

The whole module remains a pure function with no I/O.

Empirical basis (MLB, regular season):
  - G1: starters fresh, hitters without book → ER base.
  - G2: hitters adjust → +0.3..0.5 runs typical.
  - G3: hitters have full book on starter →  +0.6..1.0 runs typical.
  - Same-starter-faces-same-lineup amplifier: +0.3 (third-time-through).
"""

from __future__ import annotations

from typing import Optional


# Slope-band → multiplier on the IN-SERIES component (G2/G3).
# Keys match `mlb_series_total_signal.slope_band` outputs.
SLOPE_MULTIPLIERS: dict[str, float] = {
    "CONTRACTION_STRONG": 0.50,
    "CONTRACTION_LIGHT":  0.75,
    "STABLE":             1.00,
    "INSUFFICIENT_SAMPLE_FOR_SERIES_TREND": 1.00,
    "UNKNOWN":            1.00,
    "EXPANSION_LIGHT":    1.10,
    "EXPANSION_STRONG":   1.25,
}


def _slope_multiplier(slope_band: Optional[str]) -> float:
    if not slope_band:
        return 1.0
    return SLOPE_MULTIPLIERS.get(slope_band, 1.0)


def apply_series_degradation(
    expected_runs: float,
    game_number_in_series: int,
    starter_faced_lineup_before: bool = False,
    slope_band: Optional[str] = None,
) -> dict:
    """Returns a dict ready to attach to the pick payload.

    Output keys:
      original_er, adjusted_er, adjustment, game_in_series, degradation_label,
      starter_faced_lineup_before, slope_band, slope_multiplier.
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
            "starter_faced_lineup_before": bool(starter_faced_lineup_before),
            "slope_band":         slope_band,
            "slope_multiplier":   _slope_multiplier(slope_band),
        }
    try:
        g = int(game_number_in_series or 1)
    except (TypeError, ValueError):
        g = 1

    if g <= 1:
        in_series_component, label = 0.0, "FRESH_SERIES"
    elif g == 2:
        in_series_component, label = 0.4, "MID_SERIES"
    else:
        in_series_component, label = 0.8, "LATE_SERIES_DEGRADATION"

    mult = _slope_multiplier(slope_band)
    # Apply slope multiplier ONLY to the in-series component. The
    # third-time-through additive is a physiological book-on-starter
    # effect that the slope doesn't override.
    in_series_component *= mult

    starter_component = 0.0
    if starter_faced_lineup_before and g >= 2:
        starter_component = 0.3

    adjustment = in_series_component + starter_component

    return {
        "original_er":                  round(base, 2),
        "adjusted_er":                  round(base + adjustment, 2),
        "adjustment":                   round(adjustment, 2),
        "in_series_component":          round(in_series_component, 4),
        "starter_component":            round(starter_component, 4),
        "game_in_series":               g,
        "degradation_label":            label,
        "starter_faced_lineup_before":  bool(starter_faced_lineup_before),
        "slope_band":                   slope_band,
        "slope_multiplier":             round(mult, 4),
    }


__all__ = ["apply_series_degradation", "SLOPE_MULTIPLIERS"]
