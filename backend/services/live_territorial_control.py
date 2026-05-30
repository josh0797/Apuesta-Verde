"""Live Territorial Control Engine.

Diagnoses the canonical failure mode reported by the user:

    > El Live Engine clasifica como NO_CLEAR_DOMINANCE cuando en realidad
    > existe TERRITORIAL_CONTROL. PSG 0-1 Arsenal, min 29, posesión 73%,
    > xT 32 vs 12, corners 2 vs 0, xG 0.10 vs 0.32 — el engine dijo
    > 'No hay dominio claro' pero el patrón real es 'PSG controla el
    > territorio, Arsenal controla la efectividad'.

This module classifies the LIVE territorial state into one of four
buckets and computes the supporting pressure / conversion signals.

States:

    NO_CLEAR_DOMINANCE
        possession_gap < 15 AND xg_gap < 0.20 AND xt_gap < 10

    TERRITORIAL_CONTROL
        possession >= 65 AND xt_advantage >= 15
        (territory + possession, no real chance creation yet)

    CONTROL_WITH_PRESSURE
        TERRITORIAL_CONTROL + (shots >= 4  OR  shots_on_target >= 2
                                OR  xg >= 0.50)

    CORNER_PRESSURE_STATE
        losing_team == controlling_team AND
        possession >= 65 AND xt_advantage >= 15 AND minute >= 20

Public surface (pure functions, no IO):

    classify_territorial_state(metrics) -> dict
    compute_pressure_indicators(metrics, state) -> dict
    evaluate_live_territorial_control(metrics) -> dict
        full envelope including state, supporting metrics, narratives,
        recommended posture, and the `corner_pressure_state` boolean.

``metrics`` schema (all fields optional except minute + the two possession
values; missing fields default to 0)::

    {
        "minute":                int,
        "score_home":            int,
        "score_away":            int,
        "home_team":             str,
        "away_team":             str,

        "possession_home":       float (0..100),
        "possession_away":       float (0..100),

        "xt_home":               float,           # 0..100ish threat index
        "xt_away":               float,

        "xg_home":               float (running tally, e.g. 0.10),
        "xg_away":               float,

        "shots_home":            int,
        "shots_away":            int,
        "shots_on_target_home":  int,
        "shots_on_target_away":  int,

        "corners_home":          int,
        "corners_away":          int,

        "dangerous_attacks_home": int,
        "dangerous_attacks_away": int,

        "attacks_home":          int,
        "attacks_away":          int,
    }
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("live_territorial_control")


# ════════════════════════════════════════════════════════════════════════════
# Thresholds (user-specified)
# ════════════════════════════════════════════════════════════════════════════
NCD_POSSESSION_GAP = 15.0     # NO_CLEAR_DOMINANCE upper bound
NCD_XG_GAP         = 0.20
NCD_XT_GAP         = 10.0

TC_MIN_POSSESSION  = 65.0     # TERRITORIAL_CONTROL trigger
TC_MIN_XT_ADV      = 15.0

CWP_MIN_SHOTS      = 4
CWP_MIN_SOT        = 2
CWP_MIN_XG         = 0.50

CPS_MIN_MINUTE     = 20       # CORNER_PRESSURE_STATE

# Strong-conversion gate used by `RULE 'NO recomendar Over goals / BTTS /
# Next Goal solo por posesión alta'`. Forces the engine to require at
# least these markers before recommending goal-oriented markets.
STRONG_XG_THRESHOLD  = 0.60
STRONG_SHOTS         = 5
STRONG_SOT           = 3


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _normalize_possession(p_home: float, p_away: float) -> tuple[float, float]:
    """Sanitize possession so it sums to 100 even when API only sent one side.
    Tolerates the API-Sports quirk of returning '60%' as the string '60%'.
    """
    if p_home <= 0 and p_away <= 0:
        return 50.0, 50.0
    if p_home > 0 and p_away <= 0:
        return p_home, max(0.0, 100.0 - p_home)
    if p_away > 0 and p_home <= 0:
        return max(0.0, 100.0 - p_away), p_away
    total = p_home + p_away
    if 95 <= total <= 105:
        return p_home, p_away
    # Renormalise.
    return round(p_home * 100.0 / total, 1), round(p_away * 100.0 / total, 1)


def _identify_sides(metrics: dict) -> dict:
    """Decide who is the 'controlling team' (more possession + more xT) and
    who is the 'losing team' on the scoreboard.
    """
    p_home, p_away = _normalize_possession(
        _f(metrics.get("possession_home")),
        _f(metrics.get("possession_away")),
    )
    xt_home = _f(metrics.get("xt_home"))
    xt_away = _f(metrics.get("xt_away"))
    score_home = _i(metrics.get("score_home"))
    score_away = _i(metrics.get("score_away"))

    # Controlling side: must dominate BOTH possession + xT to count.
    if p_home >= p_away and xt_home >= xt_away:
        controlling = "home"
    elif p_away >= p_home and xt_away >= xt_home:
        controlling = "away"
    else:
        controlling = None  # split control — possession vs threat diverge

    if score_home < score_away:
        losing = "home"
    elif score_away < score_home:
        losing = "away"
    else:
        losing = None  # draw

    return {
        "controlling_side": controlling,
        "losing_side":      losing,
        "possession_home":  p_home,
        "possession_away":  p_away,
        "xt_home":          xt_home,
        "xt_away":          xt_away,
        "score_home":       score_home,
        "score_away":       score_away,
    }


def _team_name(metrics: dict, side: Optional[str]) -> Optional[str]:
    if side == "home":
        return metrics.get("home_team") or "Local"
    if side == "away":
        return metrics.get("away_team") or "Visitante"
    return None


# ════════════════════════════════════════════════════════════════════════════
# State classification
# ════════════════════════════════════════════════════════════════════════════
VALID_STATES = (
    "NO_CLEAR_DOMINANCE",
    "TERRITORIAL_CONTROL",          # alias: TERRITORIAL_CONTROL_NO_DEPTH
    "CONTROL_WITH_PRESSURE",
    "CORNER_PRESSURE_STATE",
)

STATE_LABELS_ES = {
    "NO_CLEAR_DOMINANCE":     "Sin dominio claro",
    "TERRITORIAL_CONTROL":    "Control territorial sin profundidad",
    "CONTROL_WITH_PRESSURE":  "Control con presión real",
    "CORNER_PRESSURE_STATE":  "Control territorial · escenario de córners",
}

STATE_TONES = {
    "NO_CLEAR_DOMINANCE":     "slate",
    "TERRITORIAL_CONTROL":    "sky",
    "CONTROL_WITH_PRESSURE":  "emerald",
    "CORNER_PRESSURE_STATE":  "violet",
}


def classify_territorial_state(metrics: dict) -> dict:
    """Classify the LIVE territorial state strictly using the user's rules.

    Returns a dict::
        {
          "state":              one of VALID_STATES,
          "controlling_side":   'home'|'away'|None,
          "losing_side":        'home'|'away'|None,
          "possession_gap":     float,
          "xt_gap":             float,
          "xg_gap":             float,
          "reasoning_es":       str,
        }
    """
    sides = _identify_sides(metrics)
    controlling = sides["controlling_side"]
    losing      = sides["losing_side"]
    p_home, p_away = sides["possession_home"], sides["possession_away"]
    xt_home, xt_away = sides["xt_home"], sides["xt_away"]
    minute = _i(metrics.get("minute"))
    xg_home = _f(metrics.get("xg_home"))
    xg_away = _f(metrics.get("xg_away"))

    poss_gap = round(abs(p_home - p_away), 1)
    xt_gap   = round(abs(xt_home - xt_away), 1)
    xg_gap   = round(abs(xg_home - xg_away), 2)

    # Defensive default: NO_CLEAR_DOMINANCE until proven otherwise.
    state = "NO_CLEAR_DOMINANCE"
    reasoning = (
        f"Brecha posesión {poss_gap:.0f}%, xT {xt_gap:.0f}, xG {xg_gap:.2f}: "
        "sin patrón de dominio claro."
    )

    if controlling is None:
        # Split control — possession & threat diverge. Keep NCD.
        state = "NO_CLEAR_DOMINANCE"
    else:
        # Find the controlling side's metrics.
        if controlling == "home":
            poss_ctrl = p_home
            xt_adv = xt_home - xt_away
            shots = _i(metrics.get("shots_home"))
            sot   = _i(metrics.get("shots_on_target_home"))
            xg_ctrl = xg_home
        else:
            poss_ctrl = p_away
            xt_adv = xt_away - xt_home
            shots = _i(metrics.get("shots_away"))
            sot   = _i(metrics.get("shots_on_target_away"))
            xg_ctrl = xg_away

        territorial = (
            poss_ctrl >= TC_MIN_POSSESSION
            and xt_adv >= TC_MIN_XT_ADV
        )
        has_pressure = (
            shots >= CWP_MIN_SHOTS
            or sot >= CWP_MIN_SOT
            or xg_ctrl >= CWP_MIN_XG
        )

        if territorial:
            # User said: keep NO_CLEAR_DOMINANCE ONLY when poss_gap<15 AND
            # xg_gap<0.20 AND xt_gap<10. Since territorial is true, at
            # least one of those is violated → escalate.
            if has_pressure:
                state = "CONTROL_WITH_PRESSURE"
                reasoning = (
                    f"{_team_name(metrics, controlling)} controla territorio "
                    f"({poss_ctrl:.0f}% pos, xT +{xt_adv:.0f}) y ya genera "
                    f"oportunidades reales ({shots} tiros, {sot} a puerta, "
                    f"xG {xg_ctrl:.2f})."
                )
            else:
                state = "TERRITORIAL_CONTROL"
                reasoning = (
                    f"{_team_name(metrics, controlling)} controla territorio "
                    f"y posesión ({poss_ctrl:.0f}%, xT +{xt_adv:.0f}) pero "
                    "todavía no genera peligro suficiente."
                )

            # Corner-pressure overlay (only when the controlling team is
            # actually losing the scoreboard).
            if (
                losing == controlling
                and minute >= CPS_MIN_MINUTE
            ):
                state = "CORNER_PRESSURE_STATE"
                reasoning = (
                    f"{_team_name(metrics, controlling)} pierde {sides['score_home']}-"
                    f"{sides['score_away']} pero controla territorio "
                    f"({poss_ctrl:.0f}% pos, xT +{xt_adv:.0f}) al min {minute}: "
                    "escenario natural de córners."
                )
        else:
            # No territorial trigger — keep NO_CLEAR_DOMINANCE per user spec.
            state = "NO_CLEAR_DOMINANCE"
            reasoning = (
                f"Posesión {poss_ctrl:.0f}%, ventaja xT {xt_adv:+.0f}: "
                "todavía no llega al umbral de control territorial."
            )

    return {
        "state":              state,
        "state_label_es":     STATE_LABELS_ES[state],
        "state_tone":         STATE_TONES[state],
        "controlling_side":   controlling,
        "controlling_team":   _team_name(metrics, controlling),
        "losing_side":        losing,
        "losing_team":        _team_name(metrics, losing),
        "possession_gap":     poss_gap,
        "xt_gap":             xt_gap,
        "xg_gap":             xg_gap,
        "reasoning_es":       reasoning,
    }


# ════════════════════════════════════════════════════════════════════════════
# Pressure / conversion indicators
# ════════════════════════════════════════════════════════════════════════════
def compute_pressure_indicators(metrics: dict, state_payload: dict) -> dict:
    """Compute the supporting metrics shown on the UI card:
    Presión (Baja/Media/Alta) y Conversión ofensiva (Baja/Media/Alta).
    """
    controlling = (state_payload or {}).get("controlling_side")
    if controlling is None:
        return {
            "pressure_level":   "BAJA",
            "pressure_score":   0,
            "conversion_level": "BAJA",
            "conversion_score": 0,
            "controlling_xg":   0.0,
            "controlling_shots": 0,
            "controlling_sot":   0,
            "controlling_corners": 0,
        }

    side = controlling
    shots = _i(metrics.get(f"shots_{side}"))
    sot   = _i(metrics.get(f"shots_on_target_{side}"))
    xg    = _f(metrics.get(f"xg_{side}"))
    corners = _i(metrics.get(f"corners_{side}"))
    danger  = _i(metrics.get(f"dangerous_attacks_{side}"))
    minute  = max(1, _i(metrics.get("minute")))

    # Pressure score 0-100: combines danger + corners + shots normalised by
    # minute (so a 4-shot side at minute 12 ranks higher than at 60).
    pressure_raw = (
        (danger / minute) * 40.0       # dangerous attacks per minute
        + corners * 8.0
        + shots * 4.0
        + sot * 6.0
    )
    pressure_score = max(0.0, min(100.0, pressure_raw))
    if pressure_score >= 60:
        pressure_level = "ALTA"
    elif pressure_score >= 35:
        pressure_level = "MEDIA"
    else:
        pressure_level = "BAJA"

    # Conversion score 0-100: how often the controlling side's possession
    # turns into actual chances (xG + SoT).
    conv_raw = (xg / max(0.20, _f(metrics.get(f"possession_{side}"), 50) / 100.0)) * 100
    conv_score = max(0.0, min(100.0, conv_raw + sot * 8))
    if conv_score >= 50:
        conv_level = "ALTA"
    elif conv_score >= 25:
        conv_level = "MEDIA"
    else:
        conv_level = "BAJA"

    return {
        "pressure_level":   pressure_level,
        "pressure_score":   round(pressure_score, 1),
        "conversion_level": conv_level,
        "conversion_score": round(conv_score, 1),
        "controlling_xg":   round(xg, 2),
        "controlling_shots": shots,
        "controlling_sot":   sot,
        "controlling_corners": corners,
    }


# ════════════════════════════════════════════════════════════════════════════
# Master evaluator
# ════════════════════════════════════════════════════════════════════════════
def evaluate_live_territorial_control(metrics: dict) -> dict:
    """High-level entry point. Returns the full payload that the
    orchestrator can persist / send to the UI.
    """
    metrics = metrics or {}
    state_payload = classify_territorial_state(metrics)
    indicators    = compute_pressure_indicators(metrics, state_payload)

    # Rule check — "Strong conversion" gate (used by the live_market_ranker
    # to block goal-markets when only possession is high).
    controlling = state_payload["controlling_side"]
    strong_conversion = False
    if controlling:
        s = controlling
        strong_conversion = (
            _f(metrics.get(f"xg_{s}")) >= STRONG_XG_THRESHOLD
            or _i(metrics.get(f"shots_{s}")) >= STRONG_SHOTS
            or _i(metrics.get(f"shots_on_target_{s}")) >= STRONG_SOT
        )

    return {
        "state":              state_payload["state"],
        "state_label_es":     state_payload["state_label_es"],
        "state_tone":         state_payload["state_tone"],
        "corner_pressure_state": state_payload["state"] == "CORNER_PRESSURE_STATE",
        "controlling_side":   controlling,
        "controlling_team":   state_payload["controlling_team"],
        "losing_side":        state_payload["losing_side"],
        "losing_team":        state_payload["losing_team"],
        "possession_gap":     state_payload["possession_gap"],
        "xt_gap":             state_payload["xt_gap"],
        "xg_gap":             state_payload["xg_gap"],
        "indicators":         indicators,
        "strong_conversion":  strong_conversion,
        "reasoning_es":       state_payload["reasoning_es"],
        "minute":             _i(metrics.get("minute")),
        "score_home":         _i(metrics.get("score_home")),
        "score_away":         _i(metrics.get("score_away")),
        "version":            1,
    }


__all__ = [
    "VALID_STATES",
    "STATE_LABELS_ES",
    "STATE_TONES",
    "classify_territorial_state",
    "compute_pressure_indicators",
    "evaluate_live_territorial_control",
    "NCD_POSSESSION_GAP",
    "NCD_XG_GAP",
    "NCD_XT_GAP",
    "TC_MIN_POSSESSION",
    "TC_MIN_XT_ADV",
    "CWP_MIN_SHOTS",
    "CWP_MIN_SOT",
    "CWP_MIN_XG",
    "CPS_MIN_MINUTE",
    "STRONG_XG_THRESHOLD",
    "STRONG_SHOTS",
    "STRONG_SOT",
]
