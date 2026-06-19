"""
MLB Domino Risk — Sprint D12 (Nivel 2).

Score 0-100 que mide qué pasa si el abridor sale temprano: cuán mal
puede romperse el partido cuando el bullpen tiene que cubrir más
innings de lo planeado contra un lineup explosivo.

Inputs:
  * starter_volatility_score (0-100) — del módulo Nivel 1.
  * prob_short_exit (0-1) — probabilidad de salida <5 IP.
  * pitch_count_recent_avg
  * bullpen_stress_score (0-100) — del módulo bullpen_stress.
  * long_relief_availability — "AVAILABLE" / "LIMITED" / "UNAVAILABLE".
  * middle_relief_quality (0-100)
  * lineup_explosiveness_score (0-100) — del módulo Nivel 1.
  * blowout_risk (0-100, opt)
  * park_factor (1.0 neutral)
  * weather_score (0-100, opt)

Reglas:
  - HIGH si starter_volatility >= 65 y bullpen_stress >= 60.
  - EXTREME si starter_volatility >= 75, bullpen_stress >= 70 y
    long_relief no AVAILABLE.
  - Para Under: HIGH/EXTREME → sube fragility, sube tail risk, baja
    survival, agrega DOMINO_RISK_STARTER_TO_BULLPEN. Bloquea Under si
    también hay offense EXPLOSIVE.
"""

from __future__ import annotations

from typing import Any, Optional


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_domino_risk(
    *,
    starter_volatility_score: Optional[float] = None,
    prob_short_exit: Optional[float] = None,
    pitch_count_recent_avg: Optional[float] = None,
    bullpen_stress_score: Optional[float] = None,
    long_relief_availability: Optional[str] = None,
    middle_relief_quality: Optional[float] = None,
    lineup_explosiveness_score: Optional[float] = None,
    blowout_risk: Optional[float] = None,
    park_factor: Optional[float] = None,
    weather_score: Optional[float] = None,
) -> dict:
    """Devuelve el contrato del spec."""
    drivers: list[str] = []
    missing: list[str] = []
    score = 50.0
    contribs = 0

    sv = _safe_float(starter_volatility_score)
    if sv is not None:
        contribs += 1
        if sv >= 75:
            score += 18
            drivers.append("STARTER_VOLATILITY_EXTREME")
        elif sv >= 65:
            score += 12
            drivers.append("STARTER_VOLATILITY_HIGH")
        elif sv >= 50:
            score += 5
        elif sv <= 30:
            score -= 8
    else:
        missing.append("starter_volatility_score")

    short_exit = _safe_float(prob_short_exit)
    if short_exit is not None:
        contribs += 1
        if short_exit >= 0.45:
            score += 12
            drivers.append("HIGH_SHORT_EXIT_PROB")
        elif short_exit >= 0.30:
            score += 6
        elif short_exit <= 0.15:
            score -= 5
    else:
        missing.append("prob_short_exit")

    pitch_avg = _safe_float(pitch_count_recent_avg)
    if pitch_avg is not None:
        contribs += 1
        if pitch_avg >= 105:
            score += 6
        elif pitch_avg <= 85:
            score -= 4

    bps = _safe_float(bullpen_stress_score)
    if bps is not None:
        contribs += 1
        if bps >= 80:
            score += 16
            drivers.append("BULLPEN_EXHAUSTED")
        elif bps >= 70:
            score += 12
            drivers.append("BULLPEN_TIRED")
        elif bps >= 60:
            score += 6
        elif bps <= 35:
            score -= 8
    else:
        missing.append("bullpen_stress_score")

    if long_relief_availability is not None:
        contribs += 1
        avail = long_relief_availability.upper()
        if avail == "UNAVAILABLE":
            score += 10
            drivers.append("LONG_RELIEF_OUT")
        elif avail == "LIMITED":
            score += 5
            drivers.append("LONG_RELIEF_LIMITED")
        elif avail == "AVAILABLE":
            score -= 4

    middle = _safe_float(middle_relief_quality)
    if middle is not None:
        contribs += 1
        if middle <= 35:
            score += 8
            drivers.append("MIDDLE_RELIEF_WEAK")
        elif middle >= 70:
            score -= 5

    lineup_exp = _safe_float(lineup_explosiveness_score)
    if lineup_exp is not None:
        contribs += 1
        if lineup_exp >= 78:
            score += 12
            drivers.append("EXPLOSIVE_LINEUP_OPPOSING")
        elif lineup_exp >= 62:
            score += 6

    bo = _safe_float(blowout_risk)
    if bo is not None:
        contribs += 1
        if bo >= 60:
            score += 4

    pf = _safe_float(park_factor)
    if pf is not None and pf >= 1.10:
        score += 3

    wt = _safe_float(weather_score)
    if wt is not None and wt >= 70:
        score += 3

    score = _clamp(score, 0, 100)

    # Bucket — hard rules sobreescriben el score baseline.
    long_avail = (long_relief_availability or "UNKNOWN").upper()
    is_extreme = (
        sv is not None and sv >= 75 and
        bps is not None and bps >= 70 and
        long_avail in ("UNAVAILABLE", "LIMITED", "UNKNOWN")
    )
    is_high = (
        sv is not None and sv >= 65 and
        bps is not None and bps >= 60
    )

    if is_extreme or score >= 80:
        bucket = "EXTREME"
        scenario = "starter_short_exit_to_tired_bullpen_with_no_relief"
    elif is_high or score >= 65:
        bucket = "HIGH"
        scenario = "starter_short_exit_to_tired_bullpen"
    elif score >= 45:
        bucket = "MEDIUM"
        scenario = "moderate_domino_risk"
    else:
        bucket = "LOW"
        scenario = "low_domino_risk"

    max_inputs = 10
    confidence = round(_clamp(contribs / max_inputs * 100, 0, 100), 1)

    return {
        "domino_risk_score": round(score, 2),
        "bucket":            bucket,
        "scenario":          scenario,
        "drivers":           drivers,
        "missing_fields":    missing,
        "confidence":        confidence,
    }


__all__ = ["compute_domino_risk"]
