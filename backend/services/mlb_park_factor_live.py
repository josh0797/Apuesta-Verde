"""
MLB Park Factor (Live-Adjusted) (Module #5)

Classical park factor is a long-term constant (PNC ≈ 1.00). The live
version blends 60% of the historical factor with 40% of the home team's
recent RPG ratio so the engine catches teams that are temporarily
over/underperforming their park.

Pure function, no I/O.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_LEAGUE_AVG_RPG = 4.50


def get_dynamic_park_factor(
    historical_park_factor: Optional[float],
    home_rpg_last_15: Optional[float],
    league_avg_rpg: float = DEFAULT_LEAGUE_AVG_RPG,
    *,
    historical_weight: float = 0.60,
) -> dict:
    """Returns a dict with `historical`, `recent_ratio`, `dynamic`,
    `delta_vs_historical` and `code`."""
    try:
        hist = float(historical_park_factor) if historical_park_factor is not None else 1.0
    except (TypeError, ValueError):
        hist = 1.0
    try:
        home_rpg = float(home_rpg_last_15) if home_rpg_last_15 is not None else None
    except (TypeError, ValueError):
        home_rpg = None
    try:
        avg = float(league_avg_rpg) if league_avg_rpg else DEFAULT_LEAGUE_AVG_RPG
        if avg <= 0:
            avg = DEFAULT_LEAGUE_AVG_RPG
    except (TypeError, ValueError):
        avg = DEFAULT_LEAGUE_AVG_RPG
    hw = max(0.0, min(1.0, historical_weight))
    if home_rpg is None:
        dynamic = hist
        recent_ratio = None
    else:
        recent_ratio = home_rpg / avg
        dynamic = hw * hist + (1.0 - hw) * recent_ratio
    delta = round(dynamic - hist, 3)
    if dynamic >= 1.08:
        code = "OFFENSIVE"
    elif dynamic <= 0.92:
        code = "PITCHER_FRIENDLY"
    else:
        code = "NEUTRAL"
    return {
        "historical":            round(hist, 3),
        "home_rpg_last_15":      None if home_rpg is None else round(home_rpg, 2),
        "league_avg_rpg":        round(avg, 2),
        "recent_ratio":          None if recent_ratio is None else round(recent_ratio, 3),
        "dynamic":               round(dynamic, 3),
        "delta_vs_historical":   delta,
        "code":                  code,
    }


__all__ = ["get_dynamic_park_factor"]
