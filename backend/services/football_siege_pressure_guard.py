"""Football Siege Pressure Guard — Phase 45.

Live-football safety layer that prevents the engine from recommending an
``Under`` purely because the scoreboard is still low late in the match.

Context (motivating example — Flamengo 0-0 → 3-0 at minute 90+1):
    27 total shots, 14 on target, 68% possession, 3:1 attack ratio.
    The Under was wide open at minute 80 and exploded with three goals
    inside the final 10 minutes. A naïve Under recommendation here is
    a delayed-conversion / siege profile, not a safe low-event game.

This module ingests the live-stats payload we already attach to the
match doc (``live_stats.home_stats`` / ``live_stats.away_stats``) and
returns a structured verdict. **Pure module — no I/O.**

Detection model:
    A dominant side triggers ``SIEGE_PRESSURE_HIGH`` if any of:

    Trigger 1 — Full siege profile (AND chain):
        possession ≥ 65%
        AND shots ratio ≥ 3:1
        AND shots-on-target ratio ≥ 4:1
        AND total shots by dominant side ≥ 15

    Trigger 2 — High xG (single-stat shortcut):
        dominant team xG ≥ 1.8

    Trigger 3 — Relentless dangerous attacks:
        dangerous-attacks ratio ≥ 3:1
        AND dominant team shots ≥ 10  (guard against possession-only false positives)
        AND dominant team possession ≥ 55%

Action rules (caller decides whether to enforce or merely surface them):
    Late game     — minute ≥ 70 + low score + siege  → BLOCK_UNDER on
                    Under 1.5 / 2.5, downgrade Under 3.5 confidence.
    20-min window — minute ≤ 70 + ≥ 20 min remaining + low score + siege
                    → ALSO block Under and prefer Over 0.5 live as the
                    "protected" alternative.
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "siege_guard.1"

# Tunables — kept as module constants so they're discoverable + overridable.
POSSESSION_DOMINANT          = 65.0
POSSESSION_PRESSURE          = 55.0
SHOTS_RATIO_TRIGGER          = 3.0
SOT_RATIO_TRIGGER            = 4.0
SHOTS_MIN_DOMINANT           = 15
SHOTS_MIN_PRESSURE_PROFILE   = 10
XG_DOMINANT_TRIGGER          = 1.8
DANGEROUS_ATTACKS_RATIO_TRIGGER = 3.0

# Late-game windows.
LATE_GAME_MINUTE             = 70    # minute >= 70 ⇒ late-game lockdown
TWENTY_MIN_REMAINING_LIMIT   = 70    # minute <= 70 ⇒ ≥ 20 min remaining (regulation 90)
LOW_SCORE_TOTAL_MAX          = 1     # total goals ≤ 1 ⇒ low_score

# Reason codes.
RC_SIEGE_PRESSURE_HIGH            = "SIEGE_PRESSURE_HIGH"
RC_DELAYED_CONVERSION_RISK        = "DELAYED_CONVERSION_RISK"
RC_DOMINANT_TEAM_ASSEDIO          = "DOMINANT_TEAM_ASSEDIO"
RC_LOW_SCORE_MISLEADING           = "LOW_SCORE_MISLEADING"
RC_UNDER_BLOCKED_BY_PRESSURE      = "UNDER_BLOCKED_BY_PRESSURE"
RC_LATE_GOAL_RISK_HIGH            = "LATE_GOAL_RISK_HIGH"
RC_LOW_SCORE_WITH_SIEGE           = "LOW_SCORE_WITH_SIEGE"
RC_TWENTY_MINUTES_LEFT            = "TWENTY_MINUTES_LEFT"
RC_OVER_0_5_LIVE_SUPPORTED        = "OVER_0_5_LIVE_SUPPORTED"
RC_DOMINANT_PRESSURE_GOAL_EXPECTED = "DOMINANT_PRESSURE_GOAL_EXPECTED"
RC_UNDER_REJECTED_DESPITE_LOW_SCORE = "UNDER_REJECTED_DESPITE_LOW_SCORE"

ALL_REASON_CODES = (
    RC_SIEGE_PRESSURE_HIGH,
    RC_DELAYED_CONVERSION_RISK,
    RC_DOMINANT_TEAM_ASSEDIO,
    RC_LOW_SCORE_MISLEADING,
    RC_UNDER_BLOCKED_BY_PRESSURE,
    RC_LATE_GOAL_RISK_HIGH,
    RC_LOW_SCORE_WITH_SIEGE,
    RC_TWENTY_MINUTES_LEFT,
    RC_OVER_0_5_LIVE_SUPPORTED,
    RC_DOMINANT_PRESSURE_GOAL_EXPECTED,
    RC_UNDER_REJECTED_DESPITE_LOW_SCORE,
)

# Verdicts.
VERDICT_BLOCK_UNDER       = "BLOCK_UNDER"
VERDICT_DOWNGRADE_UNDER_3 = "DOWNGRADE_UNDER_3_5"
VERDICT_ALLOW_UNDER       = "ALLOW_UNDER"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _ratio(strong: Optional[float], weak: Optional[float]) -> Optional[float]:
    """Strong-over-weak ratio. Returns None when either side missing.

    Floors weak at 1 to keep the ratio finite and consistent with how
    "infinite dominance" is typically interpreted in betting context.
    """
    s = _f(strong)
    w = _f(weak)
    if s is None or w is None:
        return None
    if w <= 0:
        w = 1.0  # treat 0 as 1 for ratio purposes (avoids inf)
    return round(s / w, 3)


def _stat(stats: dict, *keys, default: Optional[float] = None) -> Optional[float]:
    """Read the first non-None numeric stat from a list of candidate keys.

    Live-stats payloads coming from different providers use different
    field names (``shots`` vs ``total_shots`` vs ``shots_total``). The
    candidate-list pattern keeps the call site readable and robust.
    """
    for k in keys:
        v = stats.get(k)
        f = _f(v)
        if f is not None:
            return f
    return default


# ─────────────────────────────────────────────────────────────────────
# Core API
# ─────────────────────────────────────────────────────────────────────
def evaluate_siege_pressure(
    *,
    minute:        Optional[int],
    home_score:    int,
    away_score:    int,
    home_stats:    Optional[dict] = None,
    away_stats:    Optional[dict] = None,
    market:        Optional[str]  = None,
    regulation_minutes: int = 90,
    low_score_max:      int = LOW_SCORE_TOTAL_MAX,
) -> dict:
    """Run the full siege-pressure analysis.

    Args:
        minute: current match minute (0-90+).
        home_score / away_score: live scoreboard.
        home_stats / away_stats: live stat bags from the provider.
        market: the market the engine is considering (e.g. "Under 2.5",
            "Over 0.5"). Used to drive the ``verdict`` field.
        regulation_minutes: regulation time, defaults to 90 (football).
        low_score_max: total-goals threshold for ``low_score``. Default 1.

    Returns:
        See module docstring for the response shape.
    """
    h_stats = home_stats or {}
    a_stats = away_stats or {}

    h_possession = _stat(h_stats, "possession", "possession_pct", "ball_possession")
    a_possession = _stat(a_stats, "possession", "possession_pct", "ball_possession")
    h_shots      = _stat(h_stats, "shots", "total_shots", "shots_total")
    a_shots      = _stat(a_stats, "shots", "total_shots", "shots_total")
    h_sot        = _stat(h_stats, "shots_on_target", "sot", "shots_on_goal")
    a_sot        = _stat(a_stats, "shots_on_target", "sot", "shots_on_goal")
    h_dang       = _stat(h_stats, "dangerous_attacks", "dangerousAttacks")
    a_dang       = _stat(a_stats, "dangerous_attacks", "dangerousAttacks")
    h_corners    = _stat(h_stats, "corners", "corner_kicks")
    a_corners    = _stat(a_stats, "corners", "corner_kicks")
    h_xg         = _stat(h_stats, "xg", "expected_goals", "x_g")
    a_xg         = _stat(a_stats, "xg", "expected_goals", "x_g")
    h_big_chances = _stat(h_stats, "big_chances", "bigChances")
    a_big_chances = _stat(a_stats, "big_chances", "bigChances")
    h_field_tilt  = _stat(h_stats, "field_tilt", "fieldTilt")
    a_field_tilt  = _stat(a_stats, "field_tilt", "fieldTilt")
    h_attacks_3rd = _stat(h_stats, "attacks_final_third", "attacksFinalThird")
    a_attacks_3rd = _stat(a_stats, "attacks_final_third", "attacksFinalThird")

    # Determine the candidate dominant side via possession (the most
    # widely available signal). Ties / missing data → no dominant side.
    if h_possession is not None and a_possession is not None:
        if h_possession >= a_possession + 5:
            dominant = "home"
        elif a_possession >= h_possession + 5:
            dominant = "away"
        else:
            dominant = None
    else:
        # Fallback to shots when possession is absent.
        if h_shots is not None and a_shots is not None and h_shots > a_shots:
            dominant = "home"
        elif a_shots is not None and h_shots is not None and a_shots > h_shots:
            dominant = "away"
        else:
            dominant = None

    def _side(stat_h, stat_a):
        if dominant == "home":
            return stat_h, stat_a
        if dominant == "away":
            return stat_a, stat_h
        return None, None

    dom_possession, weak_possession = _side(h_possession, a_possession)
    dom_shots,      weak_shots      = _side(h_shots,      a_shots)
    dom_sot,        weak_sot        = _side(h_sot,        a_sot)
    dom_dang,       weak_dang       = _side(h_dang,       a_dang)
    dom_xg,         weak_xg         = _side(h_xg,         a_xg)
    dom_corners,    weak_corners    = _side(h_corners,    a_corners)
    dom_big_chances, weak_big_chances = _side(h_big_chances, a_big_chances)
    dom_field_tilt, weak_field_tilt   = _side(h_field_tilt,  a_field_tilt)
    dom_attacks_3rd, weak_attacks_3rd = _side(h_attacks_3rd, a_attacks_3rd)

    shots_ratio = _ratio(dom_shots, weak_shots)
    sot_ratio   = _ratio(dom_sot,   weak_sot)
    dang_ratio  = _ratio(dom_dang,  weak_dang)

    # ── Trigger evaluation ──────────────────────────────────────────
    triggers: list[str] = []
    trigger_full_profile = (
        dom_possession is not None and dom_possession >= POSSESSION_DOMINANT
        and shots_ratio is not None and shots_ratio >= SHOTS_RATIO_TRIGGER
        and sot_ratio   is not None and sot_ratio   >= SOT_RATIO_TRIGGER
        and dom_shots   is not None and dom_shots   >= SHOTS_MIN_DOMINANT
    )
    if trigger_full_profile:
        triggers.append("full_profile")

    trigger_high_xg = (dom_xg is not None and dom_xg >= XG_DOMINANT_TRIGGER)
    if trigger_high_xg:
        triggers.append("high_xg")

    trigger_dangerous_attacks = (
        dang_ratio is not None and dang_ratio >= DANGEROUS_ATTACKS_RATIO_TRIGGER
        and dom_shots is not None and dom_shots >= SHOTS_MIN_PRESSURE_PROFILE
        and dom_possession is not None and dom_possession >= POSSESSION_PRESSURE
    )
    if trigger_dangerous_attacks:
        triggers.append("dangerous_attacks")

    siege_high = bool(triggers)

    # ── Score + minute context ──────────────────────────────────────
    current_total = int(home_score) + int(away_score)
    low_score = current_total <= low_score_max
    minute_int = int(minute) if minute is not None else None
    is_late_game = minute_int is not None and minute_int >= LATE_GAME_MINUTE
    has_20_min_left = (
        minute_int is not None and minute_int <= TWENTY_MIN_REMAINING_LIMIT
        and (regulation_minutes - minute_int) >= 20
    )

    # ── Verdict on the *current* market under consideration ─────────
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

    verdict: str = VERDICT_ALLOW_UNDER
    reason_codes: list[str] = []
    prefer_markets: list[str] = []
    ui_message_es: Optional[str] = None

    if siege_high:
        reason_codes.append(RC_SIEGE_PRESSURE_HIGH)
        if dominant:
            reason_codes.append(RC_DOMINANT_TEAM_ASSEDIO)
            reason_codes.append(RC_DELAYED_CONVERSION_RISK)
        if low_score:
            reason_codes.append(RC_LOW_SCORE_MISLEADING)

    # Late-game block: minute >= 70 + low score + siege.
    if siege_high and low_score and is_late_game:
        reason_codes.append(RC_LATE_GOAL_RISK_HIGH)
        if targets_under and (under_line is None or under_line <= 2.5):
            verdict = VERDICT_BLOCK_UNDER
            reason_codes.append(RC_UNDER_BLOCKED_BY_PRESSURE)
        elif targets_under and under_line is not None and under_line >= 3.5:
            verdict = VERDICT_DOWNGRADE_UNDER_3
        # Always prefer Over 0.5 live + dominant-team next goal.
        prefer_markets.extend([
            "Over 0.5 (live)",
            f"Próximo gol: {dominant or 'equipo dominante'}",
            f"{dominant or 'Equipo dominante'} Over 0.5",
        ])
        ui_message_es = (
            "Marcador bajo, pero hay asedio fuerte y quedan minutos finales. "
            "El Under es de alto riesgo aquí — el mercado más protegido es "
            "Over 0.5 en vivo o próximo gol del equipo dominante."
        )

    # 20-min-left rule: minute ≤ 70 + ≥ 20 min remaining + low score + siege.
    elif siege_high and low_score and has_20_min_left:
        reason_codes.extend([
            RC_LOW_SCORE_WITH_SIEGE,
            RC_TWENTY_MINUTES_LEFT,
            RC_OVER_0_5_LIVE_SUPPORTED,
            RC_DOMINANT_PRESSURE_GOAL_EXPECTED,
        ])
        if targets_under:
            verdict = VERDICT_BLOCK_UNDER
            reason_codes.append(RC_UNDER_BLOCKED_BY_PRESSURE)
            reason_codes.append(RC_UNDER_REJECTED_DESPITE_LOW_SCORE)
        prefer_markets.extend([
            "Over 0.5 (live)",
            f"{dominant or 'Equipo dominante'} Over 0.5",
            f"Próximo gol: {dominant or 'equipo dominante'}",
        ])
        ui_message_es = (
            "Marcador bajo, pero todavía quedan al menos 20 minutos y hay "
            "asedio fuerte. El mercado más protegido no es Under, sino "
            "Over 0.5 goles en vivo."
        )

    return {
        "engine_version":     ENGINE_VERSION,
        "siege_pressure_high": siege_high,
        "dominant_side":      dominant,
        "triggers":           triggers,
        "verdict":            verdict,
        "low_score":          low_score,
        "is_late_game":       is_late_game,
        "has_20_min_left":    has_20_min_left,
        "reason_codes":       reason_codes,
        "prefer_markets":     prefer_markets,
        "ui_message_es":      ui_message_es,
        "metrics": {
            "minute":               minute_int,
            "current_total":        current_total,
            "dominant_possession":  dom_possession,
            "weak_possession":      weak_possession,
            "dominant_shots":       dom_shots,
            "weak_shots":           weak_shots,
            "dominant_sot":         dom_sot,
            "weak_sot":             weak_sot,
            "shots_ratio":          shots_ratio,
            "sot_ratio":            sot_ratio,
            "dangerous_attacks_ratio": dang_ratio,
            "dominant_xg":          dom_xg,
            "weak_xg":              weak_xg,
            "dominant_big_chances": dom_big_chances,
            "weak_big_chances":     weak_big_chances,
            "dominant_field_tilt":  dom_field_tilt,
            "weak_field_tilt":      weak_field_tilt,
            "dominant_corners":     dom_corners,
            "weak_corners":         weak_corners,
            "dominant_attacks_final_third": dom_attacks_3rd,
            "weak_attacks_final_third":     weak_attacks_3rd,
        },
        # Fix 2 — Continuous 0-100 Live Pressure Score (additive output).
        # Computed by the new `football_live_pressure_score` module; the
        # existing boolean verdict/triggers above are preserved so the
        # whole Phase 45 suite (30+ tests) keeps passing. Downstream
        # consumers can use `pressure_score` for fine-grained weighting
        # without losing the legacy contract.
        **_attach_pressure_score(
            home_stats=h_stats,
            away_stats=a_stats,
            market=market,
            minute=minute_int,
            home_score=home_score,
            away_score=away_score,
            regulation_minutes=regulation_minutes,
        ),
    }


def _attach_pressure_score(
    *,
    home_stats: dict,
    away_stats: dict,
    market: Optional[str],
    minute: Optional[int],
    home_score: int,
    away_score: int,
    regulation_minutes: int,
) -> dict:
    """Helper: fail-soft. Returns the new pressure_score fields or
    ``{}`` when the new module is unavailable (keeps the wrapper
    100 % retro-compatible).
    """
    try:
        from . import football_live_pressure_score as flps
        ps = flps.compute_pressure_score(home_stats=home_stats, away_stats=away_stats)
        verdict_new = flps.evaluate_pressure_verdict(
            pressure_score=ps.get("pressure_score", 0.0),
            market=market,
            minute=minute,
            home_score=home_score,
            away_score=away_score,
            regulation_minutes=regulation_minutes,
        )
        return {
            "pressure_score":        ps.get("pressure_score", 0.0),
            "pressure_components":   ps.get("components", {}),
            "pressure_verdict":      verdict_new.get("verdict"),
            "pressure_reason_codes": verdict_new.get("reason_codes", []),
            "pressure_prefer_markets": verdict_new.get("prefer_markets", []),
            "pressure_ui_message_es":  verdict_new.get("ui_message_es"),
            "pressure_engine_version": ps.get("engine_version"),
        }
    except Exception:
        return {}


__all__ = [
    "ENGINE_VERSION",
    "VERDICT_BLOCK_UNDER",
    "VERDICT_DOWNGRADE_UNDER_3",
    "VERDICT_ALLOW_UNDER",
    "RC_SIEGE_PRESSURE_HIGH",
    "RC_DELAYED_CONVERSION_RISK",
    "RC_DOMINANT_TEAM_ASSEDIO",
    "RC_LOW_SCORE_MISLEADING",
    "RC_UNDER_BLOCKED_BY_PRESSURE",
    "RC_LATE_GOAL_RISK_HIGH",
    "RC_LOW_SCORE_WITH_SIEGE",
    "RC_TWENTY_MINUTES_LEFT",
    "RC_OVER_0_5_LIVE_SUPPORTED",
    "RC_DOMINANT_PRESSURE_GOAL_EXPECTED",
    "RC_UNDER_REJECTED_DESPITE_LOW_SCORE",
    "ALL_REASON_CODES",
    "evaluate_siege_pressure",
]
