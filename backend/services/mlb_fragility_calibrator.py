"""MLB Fragility Calibrator — Hidden Over Routes.

The engine's ``fragility_score`` (0-100) can remain too low for picks
whose mean projection supports Under but whose game structure has
multiple "hidden Over routes": tired bullpens, volatile starters,
recent repeat matchups, etc. This module calibrates the base fragility
**upward only** when those hidden routes are present.

Hidden Over Route factors:
    BOTH_BULLPENS_TIRED           +8 to +12
    MEDIOCRE_OR_VOLATILE_STARTER  +5 to +10 (ERA >= 4.50 or WHIP >= 1.35)
    SERIES_FAMILIARITY_ACTIVE     +5         (familiarity >= 40)
    LATE_LAMBDA_ELEVATED          +5 to +8   (λ7-9 > 35% of total)
    TAIL_RISK_PRESENT             +5 (p_ge_12 >= 12%) / +10 (>= 22%)
    TRAFFIC_OR_DEFENSE_RISK       +5 to +10

Hard caps:
    • Max ADDITIVE delta per call:     +20.
    • Max final adjusted_fragility:    85.
    • Never reduces fragility.
    • Never flips Over/Under polarity (this module is purely additive).

The Gerrit Cole vs. Cecconi-style case (Under, ER ~8.0, line 10.5, both
bullpens tired, one starter ERA>=4.50 WHIP>=1.35, familiarity>=40)
should land in the 30-35 band starting from base 20.
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "mlb_fragility_calibrator.1"

# ── Reason codes ────────────────────────────────────────────────────
RC_FRAGILITY_CALIBRATED                = "FRAGILITY_CALIBRATED"
RC_HIDDEN_OVER_ROUTES_DETECTED         = "HIDDEN_OVER_ROUTES_DETECTED"
RC_BOTH_BULLPENS_TIRED                 = "BOTH_BULLPENS_TIRED"
RC_VOLATILE_STARTER_RAISES_FRAGILITY   = "VOLATILE_STARTER_RAISES_FRAGILITY"
RC_SERIES_FAMILIARITY_RAISES_FRAGILITY = "SERIES_FAMILIARITY_RAISES_FRAGILITY"
RC_LATE_LAMBDA_RAISES_FRAGILITY        = "LATE_LAMBDA_RAISES_FRAGILITY"
RC_TAIL_RISK_RAISES_FRAGILITY          = "TAIL_RISK_RAISES_FRAGILITY"
RC_TRAFFIC_RISK_RAISES_FRAGILITY       = "TRAFFIC_RISK_RAISES_FRAGILITY"
RC_DEFENSIVE_RISK_RAISES_FRAGILITY     = "DEFENSIVE_RISK_RAISES_FRAGILITY"
RC_FRAGILITY_DELTA_CAPPED              = "FRAGILITY_DELTA_CAPPED"
RC_ADJUSTED_FRAGILITY_CEILING_HIT      = "ADJUSTED_FRAGILITY_CEILING_HIT"

# Hidden-Over-Route tag set (also emitted in `hidden_over_routes`).
HOR_BOTH_BULLPENS_TIRED          = "BOTH_BULLPENS_TIRED"
HOR_MEDIOCRE_VOLATILE_STARTER    = "MEDIOCRE_OR_VOLATILE_STARTER"
HOR_SERIES_FAMILIARITY_ACTIVE    = "SERIES_FAMILIARITY_ACTIVE"
HOR_LATE_LAMBDA_ELEVATED         = "LATE_LAMBDA_ELEVATED"
HOR_TAIL_RISK_PRESENT            = "TAIL_RISK_PRESENT"
HOR_TRAFFIC_RISK                 = "TRAFFIC_RISK"
HOR_DEFENSIVE_RISK               = "DEFENSIVE_RISK"

MAX_DELTA   = 20
MAX_CEILING = 85


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
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


def _is_tired(usage: Optional[float], fatigue: Optional[float]) -> bool:
    """Bullpen considered tired when 3-day usage >= 0.55 OR
    explicit fatigue >= 0.60."""
    if usage is not None and usage >= 0.55:
        return True
    if fatigue is not None and fatigue >= 0.60:
        return True
    return False


def _is_volatile_starter(pitcher: Optional[dict]) -> bool:
    """ERA >= 4.50 OR WHIP >= 1.35 → volatile / mediocre."""
    if not isinstance(pitcher, dict):
        return False
    era  = _safe(pitcher.get("era"))
    whip = _safe(pitcher.get("whip"))
    if era is not None and era >= 4.50:
        return True
    if whip is not None and whip >= 1.35:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def calibrate_fragility(
    *,
    base_fragility:            Optional[float],
    market_side:               Optional[str] = None,   # "under" | "over"
    expected_runs:             Optional[float] = None,
    market_line:               Optional[float] = None,
    inning_lambda_projection:  Optional[dict] = None,
    home_pitcher:              Optional[dict] = None,
    away_pitcher:              Optional[dict] = None,
    bullpen_home:              Optional[dict] = None,
    bullpen_away:              Optional[dict] = None,
    series_familiarity:        Optional[dict] = None,
    traffic_score:             Optional[float] = None,
    defensive_breakdown_score: Optional[float] = None,
    tail_risk:                 Optional[dict] = None,
) -> dict:
    """Calibrate the base fragility upward when hidden Over routes
    exist. Returns the full audit trail.

    Polarity is NEVER changed: this is a continuous additive score that
    feeds the same market chosen by the engine.
    """
    base = _safe(base_fragility)
    if base is None:
        base = 0.0
    base_int = int(round(max(0.0, min(100.0, base))))

    delta = 0
    routes: list[str] = []
    reason_codes: list[str] = [RC_FRAGILITY_CALIBRATED]
    component_deltas: dict[str, int] = {}

    # ── 1. BOTH_BULLPENS_TIRED ───────────────────────────────────────
    bh = bullpen_home or {}
    ba = bullpen_away or {}
    h_tired = _is_tired(
        _safe(bh.get("bullpen_usage_3d") or bh.get("usage_3d")),
        _safe(bh.get("bullpen_fatigue") or bh.get("fatigue")),
    )
    a_tired = _is_tired(
        _safe(ba.get("bullpen_usage_3d") or ba.get("usage_3d")),
        _safe(ba.get("bullpen_fatigue") or ba.get("fatigue")),
    )
    if h_tired and a_tired:
        # Severity scales with the worst usage seen.
        worst_usage = max(
            _safe(bh.get("bullpen_usage_3d")) or _safe(bh.get("bullpen_fatigue")) or 0.0,
            _safe(ba.get("bullpen_usage_3d")) or _safe(ba.get("bullpen_fatigue")) or 0.0,
        )
        if worst_usage >= 0.80:
            add = 12
        elif worst_usage >= 0.70:
            add = 10
        else:
            add = 8
        delta += add
        routes.append(HOR_BOTH_BULLPENS_TIRED)
        reason_codes.append(RC_BOTH_BULLPENS_TIRED)
        component_deltas["both_bullpens_tired"] = add

    # ── 2. MEDIOCRE_OR_VOLATILE_STARTER ──────────────────────────────
    h_vol = _is_volatile_starter(home_pitcher)
    a_vol = _is_volatile_starter(away_pitcher)
    if h_vol or a_vol:
        # +5 for one starter, +10 if BOTH are volatile.
        add = 10 if (h_vol and a_vol) else 5
        # Bump further when extremely bad (ERA>=5.50 OR WHIP>=1.50).
        for p in (home_pitcher, away_pitcher):
            era  = _safe((p or {}).get("era"))
            whip = _safe((p or {}).get("whip"))
            if (era is not None and era >= 5.50) or (whip is not None and whip >= 1.50):
                add = min(10, add + 2)
        delta += add
        routes.append(HOR_MEDIOCRE_VOLATILE_STARTER)
        reason_codes.append(RC_VOLATILE_STARTER_RAISES_FRAGILITY)
        component_deltas["volatile_starter"] = add

    # ── 3. SERIES_FAMILIARITY_ACTIVE ─────────────────────────────────
    sf = series_familiarity or {}
    sf_score = _safe(sf.get("series_familiarity_score"))
    if sf_score is not None and sf_score >= 40:
        add = 5
        delta += add
        routes.append(HOR_SERIES_FAMILIARITY_ACTIVE)
        reason_codes.append(RC_SERIES_FAMILIARITY_RAISES_FRAGILITY)
        component_deltas["series_familiarity"] = add

    # ── 4. LATE_LAMBDA_ELEVATED ──────────────────────────────────────
    il = inning_lambda_projection or {}
    l_1_3 = _safe(il.get("lambda_1_3"))
    l_4_6 = _safe(il.get("lambda_4_6"))
    l_7_9 = _safe(il.get("lambda_7_9"))
    total_lambda = sum(v for v in (l_1_3, l_4_6, l_7_9) if v is not None) or None
    if l_7_9 is not None and total_lambda and total_lambda > 0:
        share = l_7_9 / total_lambda
        is_highest = all(
            (l_7_9 >= (v or 0))
            for v in (l_1_3, l_4_6)
        )
        if share > 0.35 or is_highest:
            if share > 0.40:
                add = 8
            else:
                add = 5
            delta += add
            routes.append(HOR_LATE_LAMBDA_ELEVATED)
            reason_codes.append(RC_LATE_LAMBDA_RAISES_FRAGILITY)
            component_deltas["late_lambda"] = add

    # ── 5. TAIL_RISK_MEDIUM_OR_HIGH ──────────────────────────────────
    tr = tail_risk or {}
    p_ge_12 = _safe(tr.get("p_ge_12"))
    if p_ge_12 is not None:
        if p_ge_12 >= 0.22:
            add = 10
        elif p_ge_12 >= 0.12:
            add = 5
        else:
            add = 0
        if add > 0:
            delta += add
            routes.append(HOR_TAIL_RISK_PRESENT)
            reason_codes.append(RC_TAIL_RISK_RAISES_FRAGILITY)
            component_deltas["tail_risk"] = add

    # ── 6. TRAFFIC_OR_DEFENSE_RISK ───────────────────────────────────
    ts = _safe(traffic_score)
    if ts is not None and ts >= 55:
        add = 5 if ts < 70 else 8
        delta += add
        if HOR_TRAFFIC_RISK not in routes:
            routes.append(HOR_TRAFFIC_RISK)
        reason_codes.append(RC_TRAFFIC_RISK_RAISES_FRAGILITY)
        component_deltas["traffic"] = add

    ds = _safe(defensive_breakdown_score)
    if ds is not None and ds >= 55:
        add = 5 if ds < 70 else 7
        delta += add
        if HOR_DEFENSIVE_RISK not in routes:
            routes.append(HOR_DEFENSIVE_RISK)
        reason_codes.append(RC_DEFENSIVE_RISK_RAISES_FRAGILITY)
        component_deltas["defense"] = add

    # ── Caps ─────────────────────────────────────────────────────────
    if delta > MAX_DELTA:
        delta = MAX_DELTA
        reason_codes.append(RC_FRAGILITY_DELTA_CAPPED)

    adjusted = base_int + delta
    if adjusted > MAX_CEILING:
        adjusted = MAX_CEILING
        reason_codes.append(RC_ADJUSTED_FRAGILITY_CEILING_HIT)

    if routes:
        reason_codes.insert(1, RC_HIDDEN_OVER_ROUTES_DETECTED)

    # Build a short Spanish narrative for the UI.
    narrative_es: Optional[str] = None
    if delta > 0:
        fragments: list[str] = []
        if HOR_BOTH_BULLPENS_TIRED in routes:
            fragments.append("bullpens agotados")
        if HOR_MEDIOCRE_VOLATILE_STARTER in routes:
            fragments.append("abridor vulnerable")
        if HOR_SERIES_FAMILIARITY_ACTIVE in routes:
            fragments.append("familiaridad de serie")
        if HOR_LATE_LAMBDA_ELEVATED in routes:
            fragments.append("λ7-9 elevado")
        if HOR_TAIL_RISK_PRESENT in routes:
            fragments.append("cola explosiva")
        if HOR_TRAFFIC_RISK in routes:
            fragments.append("tráfico ofensivo")
        if HOR_DEFENSIVE_RISK in routes:
            fragments.append("riesgo defensivo")
        if fragments:
            joined = ", ".join(fragments[:-1])
            if len(fragments) > 1:
                joined = f"{joined} y {fragments[-1]}"
            else:
                joined = fragments[0]
            narrative_es = f"Sube por {joined}."

    reason_codes = list(dict.fromkeys(reason_codes))

    return {
        "available":         True,
        "engine_version":    ENGINE_VERSION,
        "base_fragility":    base_int,
        "adjusted_fragility": int(adjusted),
        "delta":             int(delta),
        "max_delta":         MAX_DELTA,
        "ceiling":           MAX_CEILING,
        "hidden_over_routes": routes,
        "component_deltas":   component_deltas,
        "narrative_es":      narrative_es,
        "market_side":       (market_side or "").lower() or None,
        "reason_codes":      reason_codes,
    }


__all__ = [
    "ENGINE_VERSION",
    "MAX_DELTA",
    "MAX_CEILING",
    "RC_FRAGILITY_CALIBRATED",
    "RC_HIDDEN_OVER_ROUTES_DETECTED",
    "RC_BOTH_BULLPENS_TIRED",
    "RC_VOLATILE_STARTER_RAISES_FRAGILITY",
    "RC_SERIES_FAMILIARITY_RAISES_FRAGILITY",
    "RC_LATE_LAMBDA_RAISES_FRAGILITY",
    "RC_TAIL_RISK_RAISES_FRAGILITY",
    "RC_TRAFFIC_RISK_RAISES_FRAGILITY",
    "RC_DEFENSIVE_RISK_RAISES_FRAGILITY",
    "RC_FRAGILITY_DELTA_CAPPED",
    "RC_ADJUSTED_FRAGILITY_CEILING_HIT",
    "HOR_BOTH_BULLPENS_TIRED",
    "HOR_MEDIOCRE_VOLATILE_STARTER",
    "HOR_SERIES_FAMILIARITY_ACTIVE",
    "HOR_LATE_LAMBDA_ELEVATED",
    "HOR_TAIL_RISK_PRESENT",
    "HOR_TRAFFIC_RISK",
    "HOR_DEFENSIVE_RISK",
    "calibrate_fragility",
]
