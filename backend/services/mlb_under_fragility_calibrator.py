"""MLB Under Fragility Calibrator — Hidden Under Routes (mirror of the
Over calibrator).

Over picks can keep a fragility too low when the game structure has
multiple "hidden Under routes": two dominant aces, cold weather,
pitcher-friendly park, both offenses cold, low combined OPS, early-lambda
suppression. This module calibrates the base fragility of an OVER pick
**upward only** when those routes are present — the exact mirror of
mlb_fragility_calibrator.

POLARITY GUARD: runs ONLY for market_side == "over". For Under picks it
is a strict no-op — mlb_fragility_calibrator (hidden Over routes) handles
those. This separation keeps the two calibrators from ever touching the
same pick.

Hidden Under Route factors:
    BOTH_ACES_DOMINANT          +8 to +12 (both ERA <= 3.20 or FIP <= 3.40)
    PITCHER_FRIENDLY_PARK       +5 to +8  (park_runs_mult <= 0.95)
    COLD_WEATHER                +5        (temp <= 50°F)
    BOTH_OFFENSES_COLD          +5 to +10 (both L5 runs below league avg)
    LOW_COMBINED_OPS            +5        (combined OPS <= 1.40)
    EARLY_LAMBDA_SUPPRESSED     +5        (λ1-3 < 28% of total)

Hard caps identical to the Over calibrator: +20 delta, 85 ceiling,
never reduces, never flips polarity.
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "mlb_under_fragility_calibrator.1"

RC_UNDER_FRAGILITY_CALIBRATED   = "UNDER_FRAGILITY_CALIBRATED"
RC_HIDDEN_UNDER_ROUTES_DETECTED = "HIDDEN_UNDER_ROUTES_DETECTED"
RC_BOTH_ACES_DOMINANT           = "BOTH_ACES_DOMINANT"
RC_PITCHER_FRIENDLY_PARK        = "PITCHER_FRIENDLY_PARK_RAISES_FRAGILITY"
RC_COLD_WEATHER                 = "COLD_WEATHER_RAISES_FRAGILITY"
RC_BOTH_OFFENSES_COLD           = "BOTH_OFFENSES_COLD_RAISES_FRAGILITY"
RC_LOW_COMBINED_OPS             = "LOW_COMBINED_OPS_RAISES_FRAGILITY"
RC_EARLY_LAMBDA_SUPPRESSED      = "EARLY_LAMBDA_SUPPRESSED_RAISES_FRAGILITY"
RC_UNDER_FRAGILITY_DELTA_CAPPED = "UNDER_FRAGILITY_DELTA_CAPPED"
RC_UNDER_ADJUSTED_CEILING_HIT   = "UNDER_ADJUSTED_FRAGILITY_CEILING_HIT"

HUR_BOTH_ACES_DOMINANT      = "BOTH_ACES_DOMINANT"
HUR_PITCHER_FRIENDLY_PARK   = "PITCHER_FRIENDLY_PARK"
HUR_COLD_WEATHER            = "COLD_WEATHER"
HUR_BOTH_OFFENSES_COLD      = "BOTH_OFFENSES_COLD"
HUR_LOW_COMBINED_OPS        = "LOW_COMBINED_OPS"
HUR_EARLY_LAMBDA_SUPPRESSED = "EARLY_LAMBDA_SUPPRESSED"

MAX_DELTA   = 20
MAX_CEILING = 85


def _safe(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _is_dominant_ace(pitcher: Optional[dict]) -> bool:
    """ERA <= 3.20 OR FIP <= 3.40 → dominant ace."""
    if not isinstance(pitcher, dict):
        return False
    era = _safe(pitcher.get("era"))
    fip = _safe(pitcher.get("fip"))
    if era is not None and era <= 3.20:
        return True
    if fip is not None and fip <= 3.40:
        return True
    return False


def calibrate_under_fragility(
    *,
    base_fragility:            Optional[float],
    market_side:               Optional[str] = None,   # must be "over"
    expected_runs:             Optional[float] = None,
    market_line:               Optional[float] = None,
    inning_lambda_projection:  Optional[dict] = None,
    home_pitcher:              Optional[dict] = None,
    away_pitcher:              Optional[dict] = None,
    park_runs_mult:            Optional[float] = None,
    weather_temp_f:            Optional[float] = None,
    home_recent_runs_l5:       Optional[float] = None,
    away_recent_runs_l5:       Optional[float] = None,
    league_avg_runs:           Optional[float] = 4.3,
    combined_ops:              Optional[float] = None,
) -> dict:
    """Mirror of calibrate_fragility for hidden UNDER routes on an OVER
    pick. Strict no-op when market_side != "over"."""
    base = _safe(base_fragility)
    if base is None:
        base = 0.0
    base_int = int(round(max(0.0, min(100.0, base))))

    side = (market_side or "").lower()
    # ── POLARITY GUARD — strict no-op for anything that isn't an Over ──
    if side != "over":
        return {
            "available":           True,
            "engine_version":      ENGINE_VERSION,
            "base_fragility":      base_int,
            "adjusted_fragility":  base_int,
            "delta":               0,
            "hidden_under_routes": [],
            "component_deltas":    {},
            "narrative_es":        None,
            "reason_codes":        [RC_UNDER_FRAGILITY_CALIBRATED],
            "market_side":         side or None,
            "_skipped_reason":     "not_an_over_pick",
        }

    delta = 0
    routes: list[str] = []
    reason_codes: list[str] = [RC_UNDER_FRAGILITY_CALIBRATED]
    component_deltas: dict[str, int] = {}

    # 1. BOTH_ACES_DOMINANT
    if _is_dominant_ace(home_pitcher) and _is_dominant_ace(away_pitcher):
        best_era = min(
            _safe((home_pitcher or {}).get("era")) or 9.9,
            _safe((away_pitcher or {}).get("era")) or 9.9,
        )
        add = 12 if best_era <= 2.80 else 10 if best_era <= 3.00 else 8
        delta += add
        routes.append(HUR_BOTH_ACES_DOMINANT)
        reason_codes.append(RC_BOTH_ACES_DOMINANT)
        component_deltas["both_aces_dominant"] = add

    # 2. PITCHER_FRIENDLY_PARK
    pm = _safe(park_runs_mult)
    if pm is not None and pm <= 0.95:
        add = 8 if pm <= 0.90 else 5
        delta += add
        routes.append(HUR_PITCHER_FRIENDLY_PARK)
        reason_codes.append(RC_PITCHER_FRIENDLY_PARK)
        component_deltas["pitcher_friendly_park"] = add

    # 3. COLD_WEATHER
    temp = _safe(weather_temp_f)
    if temp is not None and temp <= 50:
        add = 5
        delta += add
        routes.append(HUR_COLD_WEATHER)
        reason_codes.append(RC_COLD_WEATHER)
        component_deltas["cold_weather"] = add

    # 4. BOTH_OFFENSES_COLD
    h_runs = _safe(home_recent_runs_l5)
    a_runs = _safe(away_recent_runs_l5)
    avg = _safe(league_avg_runs) or 4.3
    if h_runs is not None and a_runs is not None and h_runs < avg and a_runs < avg:
        gap = ((avg - h_runs) + (avg - a_runs)) / 2.0
        add = 10 if gap >= 1.0 else 5
        delta += add
        routes.append(HUR_BOTH_OFFENSES_COLD)
        reason_codes.append(RC_BOTH_OFFENSES_COLD)
        component_deltas["both_offenses_cold"] = add

    # 5. LOW_COMBINED_OPS
    cops = _safe(combined_ops)
    if cops is not None and cops <= 1.40:
        add = 5
        delta += add
        routes.append(HUR_LOW_COMBINED_OPS)
        reason_codes.append(RC_LOW_COMBINED_OPS)
        component_deltas["low_combined_ops"] = add

    # 6. EARLY_LAMBDA_SUPPRESSED
    il = inning_lambda_projection or {}
    l_1_3 = _safe(il.get("lambda_1_3"))
    l_4_6 = _safe(il.get("lambda_4_6"))
    l_7_9 = _safe(il.get("lambda_7_9"))
    total = sum(v for v in (l_1_3, l_4_6, l_7_9) if v is not None) or None
    if l_1_3 is not None and total and total > 0 and (l_1_3 / total) < 0.28:
        add = 5
        delta += add
        routes.append(HUR_EARLY_LAMBDA_SUPPRESSED)
        reason_codes.append(RC_EARLY_LAMBDA_SUPPRESSED)
        component_deltas["early_lambda_suppressed"] = add

    if delta > MAX_DELTA:
        delta = MAX_DELTA
        reason_codes.append(RC_UNDER_FRAGILITY_DELTA_CAPPED)

    adjusted = base_int + delta
    if adjusted > MAX_CEILING:
        adjusted = MAX_CEILING
        reason_codes.append(RC_UNDER_ADJUSTED_CEILING_HIT)

    if routes:
        reason_codes.insert(1, RC_HIDDEN_UNDER_ROUTES_DETECTED)

    narrative_es: Optional[str] = None
    if delta > 0:
        frag_map = {
            HUR_BOTH_ACES_DOMINANT:      "ambos abridores dominantes",
            HUR_PITCHER_FRIENDLY_PARK:   "parque pitcher-friendly",
            HUR_COLD_WEATHER:            "clima frío",
            HUR_BOTH_OFFENSES_COLD:      "ambas ofensivas frías",
            HUR_LOW_COMBINED_OPS:        "OPS combinado bajo",
            HUR_EARLY_LAMBDA_SUPPRESSED: "supresión temprana de carreras",
        }
        frags = [frag_map[r] for r in routes if r in frag_map]
        if frags:
            joined = ", ".join(frags[:-1])
            joined = f"{joined} y {frags[-1]}" if len(frags) > 1 else frags[0]
            narrative_es = f"El Over sube fragilidad por {joined}."

    reason_codes = list(dict.fromkeys(reason_codes))

    return {
        "available":           True,
        "engine_version":      ENGINE_VERSION,
        "base_fragility":      base_int,
        "adjusted_fragility":  int(adjusted),
        "delta":               int(delta),
        "max_delta":           MAX_DELTA,
        "ceiling":             MAX_CEILING,
        "hidden_under_routes": routes,
        "component_deltas":    component_deltas,
        "narrative_es":        narrative_es,
        "market_side":         side,
        "reason_codes":        reason_codes,
    }


__all__ = ["ENGINE_VERSION", "MAX_DELTA", "MAX_CEILING", "calibrate_under_fragility"]
