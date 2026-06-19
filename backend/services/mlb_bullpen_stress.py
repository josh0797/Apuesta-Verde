"""
MLB Bullpen Stress + Reliever Availability — Sprint D12 (Nivel 2).

Módulo puro. Fail-soft. Sin I/O.

API:
  * `compute_bullpen_stress(bullpen)` → dict con score 0-100, bucket
     FRESH/NORMAL/TIRED/EXHAUSTED, availability del closer/setup/long,
     usage L3/L5/L7, drivers, missing_fields, confidence.
  * `assess_reliever_availability(pitcher)` → dict con role,
     availability AVAILABLE/LIMITED/UNAVAILABLE, last_3_days_pitches,
     back_to_back, reason.

Reglas TIRED (any of):
  - bullpen pitches L3 ≥ 120
  - 4+ relevistas usados ayer
  - 2+ high leverage arms back-to-back

Reglas EXHAUSTED (any of):
  - bullpen pitches L3 ≥ 160
  - closer/setup no disponibles
  - bullpen WHIP L7 ≥ 1.45
  - bullpen ERA L7 ≥ 5.00
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


def _safe_int(v: Any) -> Optional[int]:
    f = _safe_float(v)
    return None if f is None else int(f)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ═════════════════════════════════════════════════════════════════════
# Reliever availability
# ═════════════════════════════════════════════════════════════════════
def assess_reliever_availability(pitcher: Optional[dict]) -> dict:
    """Estima disponibilidad por relevista usando uso reciente.

    Inputs esperados:
      - role: CLOSER/SETUP/MIDDLE/LONG/UNKNOWN
      - pitches_yesterday, pitches_2d_ago, pitches_3d_ago
      - back_to_back: bool (lanzó 2 días seguidos)
      - last_3_days_pitches: int (opcional; si no, se suma)
    """
    p = pitcher or {}
    role_raw = str(p.get("role") or "UNKNOWN").upper()
    role = role_raw if role_raw in ("CLOSER", "SETUP", "MIDDLE", "LONG") else "UNKNOWN"

    pitches_yest = _safe_int(p.get("pitches_yesterday")) or 0
    pitches_2 = _safe_int(p.get("pitches_2d_ago")) or 0
    pitches_3 = _safe_int(p.get("pitches_3d_ago")) or 0
    l3_total = (
        _safe_int(p.get("last_3_days_pitches"))
        if p.get("last_3_days_pitches") is not None
        else (pitches_yest + pitches_2 + pitches_3)
    )

    back_to_back = bool(p.get("back_to_back"))
    if not back_to_back:
        # Inferir: lanzó ayer y antes de ayer.
        if pitches_yest > 0 and pitches_2 > 0:
            back_to_back = True

    # Reglas (spec).
    if back_to_back or pitches_yest >= 35:
        availability = "UNAVAILABLE"
        reason = "BACK_TO_BACK" if back_to_back else "HEAVY_YESTERDAY"
    elif (20 <= pitches_yest < 35) or l3_total >= 45:
        availability = "LIMITED"
        reason = "MODERATE_RECENT_USE" if (20 <= pitches_yest < 35) else "L3_HEAVY_USE"
    else:
        availability = "AVAILABLE"
        reason = "RESTED"

    return {
        "pitcher_id":             p.get("pitcher_id"),
        "name":                   p.get("name"),
        "role":                   role,
        "availability":           availability,
        "reason":                 reason,
        "last_3_days_pitches":    l3_total,
        "back_to_back":           back_to_back,
    }


# ═════════════════════════════════════════════════════════════════════
# Bullpen Stress Index
# ═════════════════════════════════════════════════════════════════════
def compute_bullpen_stress(bullpen: Optional[dict]) -> dict:
    """Score 0-100 + bucket FRESH/NORMAL/TIRED/EXHAUSTED.

    `bullpen` (todos opcionales):
      - usage_l3 / usage_l5 / usage_l7: dicts con
            `pitches`, `innings`, `appearances`, `relievers_used_yesterday`
      - bullpen_era_l7, bullpen_era_l15
      - bullpen_whip_l7, bullpen_whip_l15
      - bullpen_fip, bullpen_xfip
      - bullpen_bb_pct, bullpen_hr_per_9
      - inherited_runners_scored_pct
      - meltdowns_l10
      - high_leverage_back_to_back: int (cuántos altos-leverage en B2B)
      - closer / setup / long_relief: dicts con shape de
            `assess_reliever_availability(...)` o un campo `availability`.
    """
    bp = bullpen or {}
    drivers: list[str] = []
    missing: list[str] = []
    score = 50.0
    contribs = 0

    def _usage_block(window: str) -> dict:
        u = bp.get(f"usage_l{window}") or {}
        if not isinstance(u, dict):
            return {}
        return u

    u3 = _usage_block("3")
    u5 = _usage_block("5")
    u7 = _usage_block("7")

    pitches_l3 = _safe_int(u3.get("pitches"))
    if pitches_l3 is not None:
        contribs += 1
        if pitches_l3 >= 160:
            score += 22
            drivers.append("L3_BULLPEN_PITCHES_EXHAUSTED")
        elif pitches_l3 >= 120:
            score += 14
            drivers.append("L3_BULLPEN_PITCHES_HIGH")
        elif pitches_l3 >= 80:
            score += 5
        elif pitches_l3 <= 40:
            score -= 10
            drivers.append("L3_BULLPEN_FRESH")
    else:
        missing.append("usage_l3.pitches")

    relievers_yest = _safe_int(u3.get("relievers_used_yesterday"))
    if relievers_yest is not None:
        contribs += 1
        if relievers_yest >= 5:
            score += 10
            drivers.append("MANY_RELIEVERS_YESTERDAY")
        elif relievers_yest >= 4:
            score += 6
            drivers.append("FOUR_RELIEVERS_YESTERDAY")

    hl_b2b = _safe_int(bp.get("high_leverage_back_to_back"))
    if hl_b2b is not None:
        contribs += 1
        if hl_b2b >= 2:
            score += 12
            drivers.append("HIGH_LEVERAGE_BACK_TO_BACK")
        elif hl_b2b == 1:
            score += 4

    era_l7 = _safe_float(bp.get("bullpen_era_l7"))
    if era_l7 is not None:
        contribs += 1
        if era_l7 >= 5.0:
            score += 12
            drivers.append("BULLPEN_ERA_L7_HIGH")
        elif era_l7 >= 4.2:
            score += 5
        elif era_l7 <= 3.0:
            score -= 5

    whip_l7 = _safe_float(bp.get("bullpen_whip_l7"))
    if whip_l7 is not None:
        contribs += 1
        if whip_l7 >= 1.45:
            score += 10
            drivers.append("BULLPEN_WHIP_L7_HIGH")
        elif whip_l7 >= 1.30:
            score += 4

    hr_9 = _safe_float(bp.get("bullpen_hr_per_9"))
    if hr_9 is not None:
        contribs += 1
        if hr_9 >= 1.5:
            score += 6

    bb_pct = _safe_float(bp.get("bullpen_bb_pct"))
    if bb_pct is not None:
        contribs += 1
        if bb_pct >= 10:
            score += 5

    meltdowns = _safe_int(bp.get("meltdowns_l10"))
    if meltdowns is not None:
        contribs += 1
        if meltdowns >= 3:
            score += 8
            drivers.append("FREQUENT_MELTDOWNS")
        elif meltdowns >= 2:
            score += 4

    irs = _safe_float(bp.get("inherited_runners_scored_pct"))
    if irs is not None:
        contribs += 1
        if irs >= 40:
            score += 6
            drivers.append("HIGH_INHERITED_RUNNERS_SCORED")

    # ── Availability of key relievers ─────────────────────────────
    def _role_availability(role_key: str) -> str:
        obj = bp.get(role_key)
        if obj is None:
            return "UNKNOWN"
        if isinstance(obj, str):
            up = obj.upper()
            return up if up in ("AVAILABLE", "LIMITED", "UNAVAILABLE") else "UNKNOWN"
        if isinstance(obj, dict):
            if "availability" in obj and isinstance(obj["availability"], str):
                return obj["availability"]
            return assess_reliever_availability(obj)["availability"]
        return "UNKNOWN"

    closer_avail = _role_availability("closer")
    setup_avail = _role_availability("setup")
    long_avail = _role_availability("long_relief")

    if closer_avail == "UNAVAILABLE":
        score += 10
        drivers.append("CLOSER_UNAVAILABLE")
    elif closer_avail == "LIMITED":
        score += 4

    if setup_avail == "UNAVAILABLE":
        score += 8
        drivers.append("SETUP_UNAVAILABLE")
    elif setup_avail == "LIMITED":
        score += 3

    if long_avail == "UNAVAILABLE":
        score += 5
        drivers.append("LONG_RELIEF_UNAVAILABLE")

    score = _clamp(score, 0, 100)

    # ── Bucket ────────────────────────────────────────────────────
    # EXHAUSTED si cualquier "hard trigger":
    exhausted_triggers = []
    if pitches_l3 is not None and pitches_l3 >= 160:
        exhausted_triggers.append("L3_PITCHES_160")
    if closer_avail == "UNAVAILABLE" and setup_avail == "UNAVAILABLE":
        exhausted_triggers.append("CLOSER_AND_SETUP_OUT")
    if whip_l7 is not None and whip_l7 >= 1.45:
        exhausted_triggers.append("WHIP_L7_145")
    if era_l7 is not None and era_l7 >= 5.0:
        exhausted_triggers.append("ERA_L7_5")

    # TIRED si cualquier "soft trigger":
    tired_triggers = []
    if pitches_l3 is not None and pitches_l3 >= 120:
        tired_triggers.append("L3_PITCHES_120")
    if relievers_yest is not None and relievers_yest >= 4:
        tired_triggers.append("RELIEVERS_YEST_4PLUS")
    if hl_b2b is not None and hl_b2b >= 2:
        tired_triggers.append("HIGH_LEVERAGE_B2B")

    if exhausted_triggers or score >= 80:
        bucket = "EXHAUSTED"
    elif tired_triggers or score >= 62:
        bucket = "TIRED"
    elif score <= 35:
        bucket = "FRESH"
    else:
        bucket = "NORMAL"

    max_inputs = 9
    confidence = round(_clamp(contribs / max_inputs * 100, 0, 100), 1)

    return {
        "bullpen_stress_score":   round(score, 2),
        "bucket":                 bucket,
        "availability": {
            "closer":       closer_avail,
            "setup":        setup_avail,
            "long_relief":  long_avail,
        },
        "usage": {
            "last_3_days":  dict(u3) if u3 else {},
            "last_5_days":  dict(u5) if u5 else {},
            "last_7_days":  dict(u7) if u7 else {},
        },
        "tired_triggers":     tired_triggers,
        "exhausted_triggers": exhausted_triggers,
        "drivers":            drivers,
        "missing_fields":     missing,
        "confidence":         confidence,
    }


__all__ = [
    "compute_bullpen_stress",
    "assess_reliever_availability",
]
