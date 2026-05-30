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


# ════════════════════════════════════════════════════════════════════════════
# V2 — LIVE CORNER MARKET INTELLIGENCE
# ════════════════════════════════════════════════════════════════════════════
# User-specified extension (May 2026): convert the raw corner pressure
# score into an actionable corner-market recommendation that:
#   • detects TERRITORIAL_CONTROL_WITH_CORNER_PRESSURE
#   • detects CONTROL_WITHOUT_GOAL_DEPTH
#   • implements the PSG-vs-Arsenal benchmark rule
#   • DOWNGRADES corner continuation when the controlling team is already
#     winning (Test D)
#   • picks the safest Over line that still reflects the live script
#   • exposes `avoid_markets` so the UI can warn the user away from
#     goal/winner markets that the script does NOT support
#   • returns structured `reason_codes` for downstream learning
# ════════════════════════════════════════════════════════════════════════════

# Pressure / market thresholds.
TC_CORNER_MIN_POSSESSION  = 65.0
TC_CORNER_MIN_POSS_GAP    = 35.0
TC_CORNER_MIN_CORNER_GAP  = 3
TC_CORNER_MIN_MINUTE      = 25
TC_CORNER_MAX_MINUTE      = 75
TC_CORNER_MIN_SHOTS       = 5
TC_CORNER_MIN_XT_ADV      = 15

CWGD_MIN_POSSESSION       = 65.0
CWGD_MIN_CORNERS          = 3
CWGD_MIN_SHOTS            = 5
CWGD_MAX_SOT              = 2

BENCHMARK_MIN_POSSESSION  = 70.0
BENCHMARK_MIN_CORNERS     = 4
BENCHMARK_MIN_MINUTE      = 40

# Risk levels.
RISK_LOW    = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH   = "HIGH"


def _select_safe_over_line(current_corners_total: int,
                             projected_corner_total: float,
                             minute: int,
                             available_lines: Optional[list[float]] = None) -> Optional[float]:
    """Pick the safest Over line that still reflects the script.

    Strategy: prefer the half-line ≤ projected − 1 (so it still has cushion
    against variance) but always ≥ current_corners + 0.5.
    """
    floor = current_corners_total + 0.5
    target = max(floor, projected_corner_total - 1.0)
    if available_lines:
        candidates = [ln for ln in available_lines if ln >= floor]
        if not candidates:
            return None
        # Pick closest to target.
        return min(candidates, key=lambda ln: abs(ln - target))
    # No bookie lines — synthesize half-line.
    half_line = round(target * 2) / 2.0
    if half_line < floor:
        half_line = floor
    # Always finish on .5.
    if abs(half_line - round(half_line)) < 0.1:
        half_line += 0.5
    return half_line


def _classify_corner_state(metrics: dict,
                            corner_payload: dict) -> dict:
    """Detect TERRITORIAL_CONTROL_WITH_CORNER_PRESSURE / CONTROL_WITHOUT_GOAL_DEPTH /
    PSG benchmark, plus downgrade flag when the controlling side is leading.
    """
    minute = max(1, _i(metrics.get("minute")))
    side = corner_payload.get("side")
    if side is None:
        return {
            "tc_with_corner_pressure": False,
            "control_without_goal_depth": False,
            "psg_benchmark":           False,
            "downgrade_due_to_lead":   False,
            "score_state":             "draw",
            "controlling_score_state": None,
        }

    other = "away" if side == "home" else "home"
    poss = _f(metrics.get(f"possession_{side}"))
    poss_other = _f(metrics.get(f"possession_{other}"))
    poss_gap = poss - poss_other
    corners_side = _i(metrics.get(f"corners_{side}"))
    corners_other = _i(metrics.get(f"corners_{other}"))
    corner_gap = corners_side - corners_other
    shots = _i(metrics.get(f"shots_{side}"))
    sot = _i(metrics.get(f"shots_on_target_{side}"))
    xg = _f(metrics.get(f"xg_{side}"))
    xt = _f(metrics.get(f"xt_{side}"))
    xt_other = _f(metrics.get(f"xt_{other}"))
    xt_adv = xt - xt_other

    score_side = _i(metrics.get(f"score_{side}"))
    score_other = _i(metrics.get(f"score_{other}"))
    if score_side < score_other:
        ctrl_state = "losing"
    elif score_side == score_other:
        ctrl_state = "tied"
    else:
        ctrl_state = "leading"

    # TERRITORIAL_CONTROL_WITH_CORNER_PRESSURE
    tc_with_corner = (
        poss >= TC_CORNER_MIN_POSSESSION
        and poss_gap >= TC_CORNER_MIN_POSS_GAP
        and corner_gap >= TC_CORNER_MIN_CORNER_GAP
        and ctrl_state in ("losing", "tied")
        and TC_CORNER_MIN_MINUTE <= minute <= TC_CORNER_MAX_MINUTE
        and (shots >= TC_CORNER_MIN_SHOTS or xt_adv >= TC_CORNER_MIN_XT_ADV)
    )

    # CONTROL_WITHOUT_GOAL_DEPTH — possession dominates but conversion is poor.
    cwgd = (
        poss >= CWGD_MIN_POSSESSION
        and corners_side >= CWGD_MIN_CORNERS
        and shots >= CWGD_MIN_SHOTS
        and sot <= CWGD_MAX_SOT
        and xg < 0.80
    )

    # PSG vs Arsenal benchmark rule.
    psg_benchmark = (
        poss >= BENCHMARK_MIN_POSSESSION
        and corners_side >= BENCHMARK_MIN_CORNERS
        and corners_other <= 1
        and ctrl_state in ("losing", "tied")
        and minute >= BENCHMARK_MIN_MINUTE
    )

    # Downgrade when the controlling team is leading — corners may stop.
    downgrade_due_to_lead = (ctrl_state == "leading" and minute >= 60)

    return {
        "tc_with_corner_pressure":    tc_with_corner,
        "control_without_goal_depth": cwgd,
        "psg_benchmark":              psg_benchmark,
        "downgrade_due_to_lead":      downgrade_due_to_lead,
        "score_state":                "draw" if score_side == score_other else (
            "home_lead" if score_side > score_other else "away_lead"
        ),
        "controlling_score_state":    ctrl_state,
    }


def _build_reason_codes(metrics: dict,
                         corner_payload: dict,
                         classification: dict,
                         team_name: Optional[str]) -> tuple[list[str], list[str]]:
    """Return (reason_codes, human_reasons_es)."""
    codes: list[str] = []
    human: list[str] = []
    side = corner_payload.get("side")
    if side is None:
        return codes, human

    other = "away" if side == "home" else "home"
    poss = _f(metrics.get(f"possession_{side}"))
    corners_side = _i(metrics.get(f"corners_{side}"))
    corners_other = _i(metrics.get(f"corners_{other}"))
    shots = _i(metrics.get(f"shots_{side}"))
    sot = _i(metrics.get(f"shots_on_target_{side}"))
    xg = _f(metrics.get(f"xg_{side}"))
    ctrl_state = classification["controlling_score_state"]

    if poss >= 70:
        codes.append("HIGH_TERRITORIAL_DOMINANCE")
        human.append(f"{team_name} {poss:.0f}% posesión")
    if corners_side - corners_other >= 3:
        codes.append("CORNER_DIFFERENTIAL_LARGE")
        human.append(f"{team_name} {corners_side} córners vs {corners_other}")
    if ctrl_state in ("losing", "tied"):
        codes.append("CONTROLLING_TEAM_NEEDS_GOAL")
        human.append(f"{team_name} {'perdiendo' if ctrl_state == 'losing' else 'empatado'} y forzado a atacar")
    if shots >= 5 and sot <= 2:
        codes.append("VOLUME_WITHOUT_QUALITY_SHOTS")
        human.append("Volumen de tiros alto pero poca calidad")
    if corners_other <= 1 and corners_side >= 4:
        codes.append("DEFENSIVE_BLOCK_OPPONENT")
        human.append(f"Rival defendiendo profundo (solo {corners_other} córner{'es' if corners_other != 1 else ''})")
    if classification["control_without_goal_depth"]:
        codes.append("CONTROL_WITHOUT_GOAL_DEPTH")
        human.append("Control sin profundidad de gol clara")
    if classification["tc_with_corner_pressure"]:
        codes.append("TERRITORIAL_CONTROL_WITH_CORNER_PRESSURE")
    if classification["psg_benchmark"]:
        codes.append("PSG_ARSENAL_BENCHMARK_MATCH")
        human.append("Encaja con el perfil PSG-Arsenal (benchmark)")
    if classification["downgrade_due_to_lead"]:
        codes.append("CONTROLLING_TEAM_LEADING_DOWNGRADE")
        human.append("Equipo dominante va ganando — riesgo de que baje el ritmo")
    if xg < 0.30 and corners_side >= 3:
        codes.append("LOW_XG_DESPITE_PRESENCE")
    return codes, human


def _build_avoid_markets(metrics: dict,
                          corner_payload: dict,
                          classification: dict) -> list[str]:
    """Return markets the engine warns the user to AVOID given the script."""
    avoid: list[str] = []
    side = corner_payload.get("side")
    if side is None:
        return avoid
    other = "away" if side == "home" else "home"
    xg = _f(metrics.get(f"xg_{side}"))
    sot = _i(metrics.get(f"shots_on_target_{side}"))
    score_side = _i(metrics.get(f"score_{side}"))
    score_other = _i(metrics.get(f"score_{other}"))
    goals_total = score_side + score_other

    high_pressure_no_quality = (
        classification.get("control_without_goal_depth")
        or (xg < 0.70 and sot <= 2 and goals_total <= 2)
    )

    if high_pressure_no_quality:
        avoid.extend([
            "Over 2.5 goles",
            "Ambos equipos anotan: Sí",
            "Siguiente gol",
            "Remontada del equipo dominante (ML)",
        ])
    return avoid


def _compute_confidence_and_risk(corner_payload: dict,
                                   classification: dict,
                                   reason_codes: list[str],
                                   minute: int) -> tuple[int, str]:
    """Map the pressure score + classification into (confidence 0-100, risk)."""
    score = _f(corner_payload.get("score"))
    # Base confidence from raw score.
    confidence = score
    if classification["psg_benchmark"]:
        confidence += 12
    if classification["tc_with_corner_pressure"]:
        confidence += 8
    if classification["control_without_goal_depth"]:
        confidence += 5
    if "DEFENSIVE_BLOCK_OPPONENT" in reason_codes:
        confidence += 4
    if classification["downgrade_due_to_lead"]:
        confidence -= 18
    if minute >= 80:
        confidence -= 8       # less time left = less variance margin
    confidence = max(0, min(100, int(round(confidence))))

    if confidence >= 75 and not classification["downgrade_due_to_lead"]:
        risk = RISK_LOW
    elif confidence >= 55:
        risk = RISK_MEDIUM
    else:
        risk = RISK_HIGH
    return confidence, risk


def evaluate_live_corner_market(metrics: dict,
                                  *,
                                  available_lines: Optional[list[float]] = None,
                                  current_corner_odds: Optional[dict] = None,
                                  surface_threshold: int = DEFAULT_SURFACE_THRESHOLD) -> dict:
    """User-facing entry point for the corner-intelligence layer.

    Returns the full ``corner_recommendation`` object specified by the user::

        {
          "should_recommend":      bool,
          "recommended_market":    str | None,        # e.g. "Total Corners Over 6.5"
          "recommended_team":      str | None,        # 'home' / 'away' / None
          "recommended_line":      float | None,
          "recommended_odds":      float | None,      # when current_corner_odds provided
          "confidence":            int  0..100,
          "risk":                  "LOW" | "MEDIUM" | "HIGH",
          "corner_pressure_score": int  0..100,
          "state":                 str,
          "classification":        dict,              # sub-flags
          "reason_codes":          list[str],
          "human_reasons":         list[str],
          "explanation":           str,
          "avoid_markets":         list[str],
          "current_corners":       {"home": int, "away": int, "total": int},
          "corner_pace":           float,             # corners per minute (controlling side)
          "projected_corner_total": float,
          "market_candidates":     list[dict],        # ranked alternates
          "version":               2,
        }
    """
    metrics = metrics or {}
    corner_payload = evaluate_corner_pressure(
        metrics, surface_threshold=surface_threshold)
    classification = _classify_corner_state(metrics, corner_payload)
    minute = _i(metrics.get("minute"))
    side = corner_payload.get("side")
    side_team = corner_payload.get("side_team")

    reason_codes, human_reasons = _build_reason_codes(
        metrics, corner_payload, classification, side_team)
    avoid_markets = _build_avoid_markets(metrics, corner_payload, classification)
    confidence, risk = _compute_confidence_and_risk(
        corner_payload, classification, reason_codes, minute)

    # Decide whether to recommend.
    should_recommend = (
        corner_payload.get("surface_recommendation")
        or classification["psg_benchmark"]
        or classification["tc_with_corner_pressure"]
        or classification["control_without_goal_depth"]
    )
    if classification["downgrade_due_to_lead"]:
        # Per the user's Test D: corner continuation is NOT auto-recommended
        # when the controlling team is already winning.
        should_recommend = False

    # Per the user's Test C / Rule 6: when xG is HIGH and shot quality is
    # genuine (SoT >= 4), the goal markets are more appropriate than
    # corners. We only override should_recommend to False here when the
    # PSG-Arsenal benchmark is NOT triggered (the benchmark explicitly
    # overrides this rule because it represents the "high pressure, low
    # conversion" pattern). This implements the user's directive:
    #
    #   "If possession high, corners high BUT xG low, shots on target low
    #    → Prefer corners. ELSE (xG high + shots quality high) → goals win."
    if (
        side is not None
        and not classification["psg_benchmark"]
        and _f(metrics.get(f"xg_{side}")) > 0.80
        and _i(metrics.get(f"shots_on_target_{side}")) >= 4
    ):
        should_recommend = False

    # Pick the safest Total Corners Over line.
    corners_home = _i(metrics.get("corners_home"))
    corners_away = _i(metrics.get("corners_away"))
    current_corners_total = corners_home + corners_away
    corners_side = _i(metrics.get(f"corners_{side}")) if side else 0
    pace = round(corners_side / max(1, minute), 3)
    # Project the TOTAL not just the controlling side — both sides could
    # still produce corners.
    pace_total = (corners_home + corners_away) / max(1, minute)
    projected_total = round(pace_total * 90.0, 1)

    recommended_market = None
    recommended_line = None
    recommended_odds = None
    if should_recommend:
        line = _select_safe_over_line(
            current_corners_total, projected_total, minute,
            available_lines=available_lines,
        )
        if line is not None:
            recommended_line = line
            recommended_market = f"Total Corners Over {line:.1f}"
            if isinstance(current_corner_odds, dict):
                # Try total_over_<line>, then total_over_*.
                key = f"total_over_{line:.1f}"
                recommended_odds = current_corner_odds.get(key)
                if recommended_odds is None:
                    # Fallback: any total_over.
                    for k, v in current_corner_odds.items():
                        if k.startswith("total_over"):
                            recommended_odds = v
                            break

    # Build explanation.
    if should_recommend and recommended_market:
        explanation = (
            f"{side_team} domina territorialmente al min {minute} "
            f"(córners {corners_side} vs {corners_away if side == 'home' else corners_home}, "
            f"presión {corner_payload['score']:.0f}/100). "
            "Los córners reflejan mejor el guion que los mercados de gol."
        )
    elif classification["downgrade_due_to_lead"]:
        explanation = (
            f"{side_team} ya va ganando con dominio de córners; el ritmo "
            "de córners puede frenar (downgrade aplicado)."
        )
    else:
        explanation = (
            "No hay presión sostenida en córners suficiente para "
            "recomendar un mercado de córners."
        )

    return {
        "should_recommend":       bool(should_recommend),
        "recommended_market":     recommended_market,
        "recommended_team":       side,
        "recommended_team_name":  side_team,
        "recommended_line":       recommended_line,
        "recommended_odds":       recommended_odds,
        "confidence":             confidence,
        "risk":                   risk,
        "corner_pressure_score":  int(round(_f(corner_payload.get("score")))),
        "state":                  corner_payload.get("state"),
        "classification":         classification,
        "reason_codes":           reason_codes,
        "human_reasons":          human_reasons,
        "explanation":            explanation,
        "avoid_markets":          avoid_markets,
        "current_corners": {
            "home":  corners_home,
            "away":  corners_away,
            "total": current_corners_total,
        },
        "corner_pace":            pace,
        "corner_pace_total":      round(pace_total, 3),
        "projected_corner_total": projected_total,
        "market_candidates":      corner_payload.get("market_candidates") or [],
        "minute":                 minute,
        "score": {
            "home": _i(metrics.get("score_home")),
            "away": _i(metrics.get("score_away")),
        },
        "narrative_es":           corner_payload.get("narrative_es"),
        "version":                2,
    }


__all__ = [
    "DEFAULT_SURFACE_THRESHOLD",
    "evaluate_corner_pressure",
    "evaluate_live_corner_market",
    "TC_CORNER_MIN_POSSESSION",
    "TC_CORNER_MIN_POSS_GAP",
    "TC_CORNER_MIN_CORNER_GAP",
    "TC_CORNER_MIN_MINUTE",
    "TC_CORNER_MAX_MINUTE",
    "BENCHMARK_MIN_POSSESSION",
    "BENCHMARK_MIN_CORNERS",
    "BENCHMARK_MIN_MINUTE",
    "RISK_LOW",
    "RISK_MEDIUM",
    "RISK_HIGH",
]
