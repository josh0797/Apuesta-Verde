"""Football Live Pressure Score — Fix 2 (Phase X).

Continuous 0-100 score that quantifies how strong a team's siege is on its
opponent in a live football match. Replaces the boolean "trigger" model
of ``football_siege_pressure_guard`` for cases where we need a numeric
signal (e.g. to weight Under / Over probability, to drive UI badges,
or to feed ML-style decision rules).

Score breakdown (deterministic, sums to 100):
    shots_ratio_component        : 0-25 pts   (ratio dominant/weak)
    sot_ratio_component          : 0-20 pts   (shots on target ratio)
    xg_component                 : 0-20 pts   (dominant team xG)
    dangerous_attacks_component  : 0-15 pts   (dangerous attacks ratio)
    possession_component         : 0-10 pts   (dominant possession %)
    corners_big_chances_component: 0-10 pts   (combined ratio)

Verdict mapping (caller may enforce or surface):
    score >= 75                            → BLOCK_UNDER (any market, any minute)
    score >= 60 + low_score + late_game    → BLOCK_UNDER (Under 0.5/1.5/2.5)
    score >= 60 + low_score + 20_min_left  → BLOCK_UNDER (Under 0.5/1.5/2.5)
    score >= 45 + targets_under            → DOWNGRADE_UNDER (cap confidence)
    score <  45                            → ALLOW_UNDER

The new module is **pure** (no I/O). It is consumed by:
    1. ``football_siege_pressure_guard.evaluate_siege_pressure`` — adds
       ``pressure_score`` + ``pressure_components`` to its existing
       output WITHOUT mutating its verdict / triggers (back-compat).
    2. Any future caller that wants the raw 0-100 number.

Author: rationale + thresholds calibrated against the same scenarios
the boolean siege guard was validated on (Flamengo 0-0 → 3-0 at min
90+1, Manchester City 0-0 → 1-0 at min 88, etc.). The thresholds were
tuned so the score crosses 60 in the SAME scenarios where the legacy
triggers fire ``full_profile`` or ``high_xg``.
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "live_pressure_score.1"

# ─────────────────────────────────────────────────────────────────────
# Component thresholds (numerator caps).
# Tuned so the legacy siege scenarios cross score=60 cleanly.
# ─────────────────────────────────────────────────────────────────────
SHOTS_RATIO_MAX        = 4.0   # ratio >=4.0 → full 25pts
SOT_RATIO_MAX          = 4.0   # ratio >=4.0 → full 20pts
DANGEROUS_RATIO_MAX    = 3.0   # ratio >=3.0 → full 15pts
CORNERS_BC_RATIO_MAX   = 3.0   # ratio >=3.0 → full 10pts
XG_MAX                 = 2.5   # xG >=2.5 → full 20pts
POSSESSION_MIN_FLOOR   = 50.0  # below 50% → 0 component pts
POSSESSION_MAX_CEILING = 70.0  # >=70% → full 10pts

# Component weights (sum = 100).
W_SHOTS        = 25.0
W_SOT          = 20.0
W_XG           = 20.0
W_DANGEROUS    = 15.0
W_POSSESSION   = 10.0
W_CORNERS_BC   = 10.0

# Verdict thresholds.
PRESSURE_SCORE_BLANKET_BLOCK   = 75   # any-context block
PRESSURE_SCORE_CONTEXT_BLOCK   = 60   # + low_score + (late_game or 20_min_left)
PRESSURE_SCORE_DOWNGRADE       = 45   # cap Under confidence

# Late-game windows (mirrors the legacy guard).
LATE_GAME_MINUTE             = 70
TWENTY_MIN_REMAINING_LIMIT   = 70
LOW_SCORE_TOTAL_MAX          = 1

VERDICT_BLOCK_UNDER       = "BLOCK_UNDER"
VERDICT_DOWNGRADE_UNDER_3 = "DOWNGRADE_UNDER_3_5"
VERDICT_ALLOW_UNDER       = "ALLOW_UNDER"

# Reason codes (additive — coexist with the legacy guard codes).
RC_PRESSURE_SCORE_HIGH       = "PRESSURE_SCORE_HIGH"
RC_PRESSURE_SCORE_MODERATE   = "PRESSURE_SCORE_MODERATE"
RC_PRESSURE_BLOCKS_UNDER     = "PRESSURE_BLOCKS_UNDER"
RC_PRESSURE_DOWNGRADES_UNDER = "PRESSURE_DOWNGRADES_UNDER"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> Optional[float]:
    """Safe float cast: returns None on missing / NaN / un-parseable."""
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def _stat(stats: dict, *keys, default: Optional[float] = None) -> Optional[float]:
    """First non-None numeric stat across candidate keys."""
    for k in keys:
        v = stats.get(k)
        f = _f(v)
        if f is not None:
            return f
    return default


def _ratio(strong: Optional[float], weak: Optional[float]) -> Optional[float]:
    """Strong/weak ratio. Floors weak at 1 to avoid inf."""
    s = _f(strong)
    w = _f(weak)
    if s is None or w is None:
        return None
    if w <= 0:
        w = 1.0
    return s / w


def _linear_component(value: Optional[float], floor: float, ceiling: float, max_pts: float) -> float:
    """Map [floor, ceiling] linearly to [0, max_pts]. Clamps outside.

    Examples:
        _linear_component(1.0, 1.0, 4.0, 25)  → 0.0
        _linear_component(2.5, 1.0, 4.0, 25)  → 12.5
        _linear_component(4.0, 1.0, 4.0, 25)  → 25.0
        _linear_component(5.0, 1.0, 4.0, 25)  → 25.0 (clamped)
        _linear_component(None, ...)          → 0.0
    """
    if value is None:
        return 0.0
    if value <= floor:
        return 0.0
    if value >= ceiling:
        return max_pts
    return round(max_pts * (value - floor) / (ceiling - floor), 3)


# ─────────────────────────────────────────────────────────────────────
# Core API
# ─────────────────────────────────────────────────────────────────────
def compute_pressure_score(
    *,
    home_stats: Optional[dict] = None,
    away_stats: Optional[dict] = None,
) -> dict:
    """Compute the continuous 0-100 pressure score.

    Returns:
        {
            "pressure_score": float (0-100),
            "dominant_side": "home" | "away" | None,
            "components": {
                "shots_ratio":         {"value": ..., "points": ...},
                "sot_ratio":           {"value": ..., "points": ...},
                "xg":                  {"value": ..., "points": ...},
                "dangerous_attacks":   {"value": ..., "points": ...},
                "possession":          {"value": ..., "points": ...},
                "corners_big_chances": {"value": ..., "points": ...},
            },
            "metrics": {...},          # raw per-side stats
            "engine_version": str,
        }
    """
    h = home_stats or {}
    a = away_stats or {}

    h_poss = _stat(h, "possession", "possession_pct", "ball_possession")
    a_poss = _stat(a, "possession", "possession_pct", "ball_possession")
    h_shots = _stat(h, "shots", "total_shots", "shots_total")
    a_shots = _stat(a, "shots", "total_shots", "shots_total")
    h_sot = _stat(h, "shots_on_target", "sot", "shots_on_goal")
    a_sot = _stat(a, "shots_on_target", "sot", "shots_on_goal")
    h_dang = _stat(h, "dangerous_attacks", "dangerousAttacks")
    a_dang = _stat(a, "dangerous_attacks", "dangerousAttacks")
    h_corn = _stat(h, "corners", "corner_kicks")
    a_corn = _stat(a, "corners", "corner_kicks")
    h_xg = _stat(h, "xg", "expected_goals", "x_g")
    a_xg = _stat(a, "xg", "expected_goals", "x_g")
    h_bc = _stat(h, "big_chances", "bigChances")
    a_bc = _stat(a, "big_chances", "bigChances")

    # Dominance via possession; fallback to shots if possession missing.
    if h_poss is not None and a_poss is not None:
        if h_poss >= a_poss + 5:
            dominant = "home"
        elif a_poss >= h_poss + 5:
            dominant = "away"
        else:
            dominant = None
    elif h_shots is not None and a_shots is not None:
        if h_shots > a_shots:
            dominant = "home"
        elif a_shots > h_shots:
            dominant = "away"
        else:
            dominant = None
    else:
        dominant = None

    def _side(stat_h, stat_a):
        if dominant == "home":
            return stat_h, stat_a
        if dominant == "away":
            return stat_a, stat_h
        return None, None

    dom_poss, _ = _side(h_poss, a_poss)
    dom_shots, weak_shots = _side(h_shots, a_shots)
    dom_sot, weak_sot = _side(h_sot, a_sot)
    dom_dang, weak_dang = _side(h_dang, a_dang)
    dom_corn, weak_corn = _side(h_corn, a_corn)
    dom_bc, weak_bc = _side(h_bc, a_bc)
    dom_xg, _ = _side(h_xg, a_xg)

    # Components.
    shots_ratio = _ratio(dom_shots, weak_shots)
    sot_ratio = _ratio(dom_sot, weak_sot)
    dang_ratio = _ratio(dom_dang, weak_dang)

    # Combined corners + big chances ratio (sum both → ratio).
    combo_dom = (dom_corn or 0.0) + (dom_bc or 0.0)
    combo_weak = (weak_corn or 0.0) + (weak_bc or 0.0)
    combo_ratio: Optional[float] = None
    if (dom_corn is not None or dom_bc is not None) and (weak_corn is not None or weak_bc is not None):
        combo_ratio = _ratio(combo_dom, combo_weak)

    shots_pts = _linear_component(shots_ratio, 1.0, SHOTS_RATIO_MAX, W_SHOTS)
    sot_pts = _linear_component(sot_ratio, 1.0, SOT_RATIO_MAX, W_SOT)
    xg_pts = _linear_component(dom_xg, 0.5, XG_MAX, W_XG)
    dang_pts = _linear_component(dang_ratio, 1.0, DANGEROUS_RATIO_MAX, W_DANGEROUS)
    poss_pts = _linear_component(dom_poss, POSSESSION_MIN_FLOOR, POSSESSION_MAX_CEILING, W_POSSESSION)
    combo_pts = _linear_component(combo_ratio, 1.0, CORNERS_BC_RATIO_MAX, W_CORNERS_BC)

    # Defensive: if no dominant side could be inferred, score must be 0.
    if dominant is None:
        shots_pts = sot_pts = xg_pts = dang_pts = poss_pts = combo_pts = 0.0

    raw_score = shots_pts + sot_pts + xg_pts + dang_pts + poss_pts + combo_pts
    pressure_score = round(max(0.0, min(100.0, raw_score)), 2)

    return {
        "engine_version": ENGINE_VERSION,
        "pressure_score": pressure_score,
        "dominant_side": dominant,
        "components": {
            "shots_ratio":         {"value": shots_ratio, "points": round(shots_pts, 2), "weight": W_SHOTS},
            "sot_ratio":           {"value": sot_ratio,   "points": round(sot_pts, 2),   "weight": W_SOT},
            "xg":                  {"value": dom_xg,      "points": round(xg_pts, 2),    "weight": W_XG},
            "dangerous_attacks":   {"value": dang_ratio,  "points": round(dang_pts, 2),  "weight": W_DANGEROUS},
            "possession":          {"value": dom_poss,    "points": round(poss_pts, 2),  "weight": W_POSSESSION},
            "corners_big_chances": {"value": combo_ratio, "points": round(combo_pts, 2), "weight": W_CORNERS_BC},
        },
        "metrics": {
            "dominant_shots":       dom_shots,
            "weak_shots":           weak_shots,
            "dominant_sot":         dom_sot,
            "weak_sot":             weak_sot,
            "dominant_xg":          dom_xg,
            "dominant_possession":  dom_poss,
            "dominant_dangerous_attacks": dom_dang,
            "weak_dangerous_attacks":     weak_dang,
            "dominant_corners":     dom_corn,
            "weak_corners":         weak_corn,
            "dominant_big_chances": dom_bc,
            "weak_big_chances":     weak_bc,
        },
    }


def evaluate_pressure_verdict(
    *,
    pressure_score: float,
    market: Optional[str],
    minute: Optional[int],
    home_score: int,
    away_score: int,
    regulation_minutes: int = 90,
    low_score_max: int = LOW_SCORE_TOTAL_MAX,
) -> dict:
    """Map a pressure_score to (verdict, reason_codes, prefer_markets).

    Coexists with the legacy ``evaluate_siege_pressure``: it returns the
    SAME shape (verdict family) so wrappers can map cleanly. Caller is
    free to keep or override the legacy verdict.
    """
    market_l = (market or "").lower().strip()
    targets_under = market_l.startswith("under")
    under_line: Optional[float] = None
    if targets_under:
        try:
            under_line = float(
                "".join(c for c in market_l.replace("under", "")
                        if c.isdigit() or c == ".").strip(".")
            )
        except (ValueError, TypeError):
            under_line = None

    current_total = int(home_score) + int(away_score)
    low_score = current_total <= low_score_max
    minute_int = int(minute) if minute is not None else None
    is_late_game = minute_int is not None and minute_int >= LATE_GAME_MINUTE
    has_20_min_left = (
        minute_int is not None and minute_int <= TWENTY_MIN_REMAINING_LIMIT
        and (regulation_minutes - minute_int) >= 20
    )

    reason_codes: list[str] = []
    prefer_markets: list[str] = []
    verdict = VERDICT_ALLOW_UNDER
    ui_message_es: Optional[str] = None

    if pressure_score >= PRESSURE_SCORE_BLANKET_BLOCK:
        reason_codes.append(RC_PRESSURE_SCORE_HIGH)
        if targets_under and (under_line is None or under_line <= 2.5):
            verdict = VERDICT_BLOCK_UNDER
            reason_codes.append(RC_PRESSURE_BLOCKS_UNDER)
            prefer_markets.extend(["Over 0.5 (live)", "Próximo gol: equipo dominante"])
            ui_message_es = (
                "Score de presión muy alto (>= 75/100). El Under aquí es "
                "de muy alto riesgo: el modelo proyecta gol inminente."
            )
        elif targets_under and under_line is not None and under_line >= 3.5:
            verdict = VERDICT_DOWNGRADE_UNDER_3
            reason_codes.append(RC_PRESSURE_DOWNGRADES_UNDER)
    elif (
        pressure_score >= PRESSURE_SCORE_CONTEXT_BLOCK
        and low_score
        and (is_late_game or has_20_min_left)
    ):
        reason_codes.append(RC_PRESSURE_SCORE_HIGH)
        if targets_under and (under_line is None or under_line <= 2.5):
            verdict = VERDICT_BLOCK_UNDER
            reason_codes.append(RC_PRESSURE_BLOCKS_UNDER)
            prefer_markets.extend(["Over 0.5 (live)", "Próximo gol: equipo dominante"])
            ui_message_es = (
                "Score de presión alto + marcador bajo en tramo final. "
                "El Under es de alto riesgo aquí — mercado más protegido: "
                "Over 0.5 en vivo."
            )
        elif targets_under and under_line is not None and under_line >= 3.5:
            verdict = VERDICT_DOWNGRADE_UNDER_3
            reason_codes.append(RC_PRESSURE_DOWNGRADES_UNDER)
    elif pressure_score >= PRESSURE_SCORE_DOWNGRADE:
        reason_codes.append(RC_PRESSURE_SCORE_MODERATE)
        if targets_under and low_score:
            # Soft warning: downgrade Under regardless of line.
            verdict = VERDICT_DOWNGRADE_UNDER_3
            reason_codes.append(RC_PRESSURE_DOWNGRADES_UNDER)

    return {
        "verdict": verdict,
        "reason_codes": reason_codes,
        "prefer_markets": prefer_markets,
        "ui_message_es": ui_message_es,
        "context": {
            "low_score":       low_score,
            "is_late_game":    is_late_game,
            "has_20_min_left": has_20_min_left,
            "current_total":   current_total,
            "minute":          minute_int,
            "targets_under":   targets_under,
            "under_line":      under_line,
        },
    }


__all__ = [
    "ENGINE_VERSION",
    "VERDICT_ALLOW_UNDER",
    "VERDICT_BLOCK_UNDER",
    "VERDICT_DOWNGRADE_UNDER_3",
    "PRESSURE_SCORE_BLANKET_BLOCK",
    "PRESSURE_SCORE_CONTEXT_BLOCK",
    "PRESSURE_SCORE_DOWNGRADE",
    "RC_PRESSURE_SCORE_HIGH",
    "RC_PRESSURE_SCORE_MODERATE",
    "RC_PRESSURE_BLOCKS_UNDER",
    "RC_PRESSURE_DOWNGRADES_UNDER",
    "compute_pressure_score",
    "evaluate_pressure_verdict",
]
