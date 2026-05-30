"""Live Corner Intelligence Engine.

Runs ALWAYS when live metrics are present (per the user's spec) and
produces a corner_pressure_score 0-100 + a categorical state. The UI
then filters whether to surface a recommendation or just keep the
score internal ("detect rising pressure before the market loses value").

Pure functions — no IO.

Inputs (same metrics dict consumed by live_territorial_control):

    minute, score_home, score_away,
    possession_home, possession_away,
    xt_home, xt_away,
    corners_home, corners_away,
    shots_home, shots_away,
    shots_on_target_home, shots_on_target_away,
    dangerous_attacks_home, dangerous_attacks_away,
    attacks_home, attacks_away,

Outputs::

    {
        "score":                  float 0..100,
        "state":                  str (RISING_PRESSURE / CORNER_PRESSURE_STATE /
                                       LOW / NONE),
        "side":                   'home'|'away'|None,
        "side_team":              str,
        "projected_corner_total": float,  # extrapolation by minute
        "reasons":                list[str],
        "market_candidates":      list[dict],
        "surface_recommendation": bool,   # whether the UI should show it
        "surface_threshold":      int,    # the threshold used
        "narrative_es":           str,
    }
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("live_corner_engine")

# Surfacing threshold — the score below which the UI should NOT show a
# corner recommendation even though the score is computed internally.
DEFAULT_SURFACE_THRESHOLD = 55


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


def _identify_pressure_side(metrics: dict) -> Optional[str]:
    """Who is generating the corner pressure?
    Priority: more corners → more dangerous attacks → more possession.
    """
    c_h = _i(metrics.get("corners_home"))
    c_a = _i(metrics.get("corners_away"))
    if c_h != c_a:
        return "home" if c_h > c_a else "away"
    d_h = _i(metrics.get("dangerous_attacks_home"))
    d_a = _i(metrics.get("dangerous_attacks_away"))
    if d_h != d_a:
        return "home" if d_h > d_a else "away"
    p_h = _f(metrics.get("possession_home"))
    p_a = _f(metrics.get("possession_away"))
    if p_h != p_a:
        return "home" if p_h > p_a else "away"
    return None


def _team_name(metrics: dict, side: Optional[str]) -> Optional[str]:
    if side == "home":
        return metrics.get("home_team") or "Local"
    if side == "away":
        return metrics.get("away_team") or "Visitante"
    return None


def evaluate_corner_pressure(
    metrics: dict,
    *,
    surface_threshold: int = DEFAULT_SURFACE_THRESHOLD,
) -> dict:
    """Compute the corner-pressure payload. ALWAYS runs.

    `surface_threshold` controls the boolean `surface_recommendation`
    so the front-end can keep the score internal while gating the
    recommendation card. Defaults to 55/100.
    """
    metrics = metrics or {}
    minute = max(1, _i(metrics.get("minute")))
    side = _identify_pressure_side(metrics)

    if side is None:
        return {
            "score":                  0.0,
            "state":                  "NONE",
            "side":                   None,
            "side_team":              None,
            "projected_corner_total": 0.0,
            "reasons":                [],
            "market_candidates":      [],
            "surface_recommendation": False,
            "surface_threshold":      surface_threshold,
            "narrative_es":           "Sin lado dominante en córners ni ataques peligrosos.",
        }

    other = "away" if side == "home" else "home"
    corners_side = _i(metrics.get(f"corners_{side}"))
    corners_other = _i(metrics.get(f"corners_{other}"))
    danger = _i(metrics.get(f"dangerous_attacks_{side}"))
    shots = _i(metrics.get(f"shots_{side}"))
    possession = _f(metrics.get(f"possession_{side}"))
    xt = _f(metrics.get(f"xt_{side}"))

    # Project total corners by extrapolating the per-minute rate to 90.
    rate = corners_side / max(1, minute)
    proj = round(rate * 90.0, 1)

    reasons: list[str] = []
    score = 0.0

    # Corners ratio — strongest signal.
    ratio = corners_side / max(1, corners_side + corners_other)
    score += ratio * 28
    if ratio >= 0.70:
        reasons.append(f"{ratio*100:.0f}% de los córners del partido.")

    # Per-minute rate vs league baseline (~0.06 corners/min from a side).
    if rate >= 0.18:
        score += 22
        reasons.append(f"Ritmo de córners alto ({rate:.2f}/min).")
    elif rate >= 0.10:
        score += 12
        reasons.append(f"Ritmo de córners por encima del promedio ({rate:.2f}/min).")

    # Territorial signals (possession + xT).
    if possession >= 60:
        score += min(20, (possession - 55) * 1.2)
    if xt >= 25:
        score += 14
        reasons.append(f"xT alto ({xt:.0f}) sostiene presión territorial.")
    elif xt >= 15:
        score += 8

    # Late-game urgency multiplier.
    if minute >= 70:
        score += 8
    elif minute >= 55:
        score += 4

    # Dangerous attacks + shots converted into corners.
    if danger and shots and corners_side >= 2:
        score += min(10, (danger / max(1, minute)) * 30)

    # Cap.
    score = max(0.0, min(100.0, score))

    # State.
    if score >= 70:
        state = "CORNER_PRESSURE_STATE"
    elif score >= surface_threshold:
        state = "RISING_PRESSURE"
    elif score >= 25:
        state = "LOW"
    else:
        state = "NONE"

    team = _team_name(metrics, side)
    # Market candidates — Over córners team + total + next córner side.
    candidates: list[dict] = []
    if state in ("CORNER_PRESSURE_STATE", "RISING_PRESSURE"):
        # Over team corners — line ≈ corners_side + 1 (closest half-line).
        team_over_line = corners_side + 1.5
        candidates.append({
            "market":     f"Over córners {team} {team_over_line:.1f}",
            "category":   "OVER_TEAM_CORNERS",
            "line":       team_over_line,
            "score":      round(score, 1),
            "side":       side,
            "rationale":  (
                f"{team} ya tiene {corners_side} córners al min {minute}; "
                "el ritmo proyecta más sin necesidad de gol."
            ),
        })
        candidates.append({
            "market":     f"{team} más córners que el rival",
            "category":   "TEAM_MOST_CORNERS",
            "score":      round(score * 0.95, 1),
            "side":       side,
            "rationale":  f"Diferencial de córners {corners_side}-{corners_other}.",
        })
        candidates.append({
            "market":     "Siguiente córner: " + team,
            "category":   "NEXT_CORNER",
            "score":      round(score * 0.85, 1),
            "side":       side,
            "rationale":  "Presión territorial sostenida favorece próximo córner.",
        })

    if not reasons:
        reasons.append(f"{team} acumula {corners_side} córners y presión menor.")

    narrative = (
        f"{team} acumula {corners_side} córners al min {minute} (rate "
        f"{rate:.2f}/min, proyección final {proj}). Score "
        f"{score:.0f}/100 — {state.replace('_', ' ').lower()}."
    )

    return {
        "score":                  round(score, 1),
        "state":                  state,
        "side":                   side,
        "side_team":              team,
        "projected_corner_total": proj,
        "reasons":                reasons,
        "market_candidates":      candidates,
        "surface_recommendation": score >= surface_threshold,
        "surface_threshold":      surface_threshold,
        "narrative_es":           narrative,
    }


__all__ = [
    "DEFAULT_SURFACE_THRESHOLD",
    "evaluate_corner_pressure",
]
