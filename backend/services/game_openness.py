"""Game Openness — bilateral live-threat metric for total-goals markets.

Why this exists
---------------
The existing `_momentum_score` in `live_reevaluation.py` computes
`h_idx + a_idx` (combined threat) but only returns the **direction**
(`delta = h_idx - a_idx`). That is correct for *who wins* markets, but
for **total** markets (Over 2.5 / Over 3.5 / BTTS) the signal that
matters is exactly the part that gets discarded: how much *both* teams
are threatening at the same time.

Real-world failure this fixes
-----------------------------
France 1-1 Ivory Coast (2026-06-04): at min 54 the engine recommended
"Over 3.5" with 79% confidence because the home side's live xG (1.85)
was high. But the away side contributed almost no xG, so the *combined*
expected total never supported a 4-goal game. Final: 1-2 (3 goals) —
Over 3.5 missed by exactly one goal.

A high one-sided xG should support **Match Winner / team Over / Next
Goal**, not a high **total** line. This module makes that distinction
explicit so `human_live_interpreter` can pick BTTS or a *lower* Over
line when only one side is generating threat.

Pure, no IO, fail-soft. Mirrors the live_xg_proxy.extract_side shape
but never imports it at module load (lazy, so tests can stub).
"""
from __future__ import annotations

from typing import Optional


# ── Tunables (kept module-level so a calibration layer can override) ─────

# A game is "bilaterally open" only when BOTH sides clear a minimum live
# xG floor. 0.55 ≈ "at least one clear chance created" by the 45–60' mark.
MIN_SIDE_XG_FOR_OPEN          = 0.55
# Combined live xG required to even consider an aggressive TOTAL (Over 3.5).
MIN_COMBINED_XG_FOR_OVER35    = 2.40
# Combined live xG required for a moderate TOTAL (Over 2.5 / BTTS).
MIN_COMBINED_XG_FOR_OVER25    = 1.60
# Below this ratio the game is "one-sided" — the weaker side is being
# dominated and a TOTAL line is risky even if the combined xG looks ok.
ONE_SIDED_RATIO_THRESHOLD     = 0.22   # weaker_xg / (stronger_xg + weaker_xg)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def compute_game_openness(
    home_stats: dict,
    away_stats: dict,
    *,
    minute: Optional[int] = None,
    current_total: int = 0,
) -> dict:
    """Return a bilateral-openness report for total-goals markets.

    Output keys
    -----------
    ``combined_xg``       home live xG + away live xG.
    ``weaker_side_xg``    the smaller of the two live xG values.
    ``one_sided_ratio``   weaker / combined  (0 = totally one-sided,
                          0.5 = perfectly balanced).
    ``is_bilateral``      both sides clear MIN_SIDE_XG_FOR_OPEN.
    ``supports_over_35``  combined ≥ floor AND bilateral AND not one-sided.
    ``supports_over_25``  combined ≥ lower floor AND not extremely one-sided.
    ``supports_btts``     both sides have created (proxy via xG floor) and
                          BTTS not already achieved.
    ``recommended_total`` best total market given the openness, or None.
    ``reason_es``         human string for the copilot card.
    """
    # Lazy import keeps this module test-friendly and avoids a hard
    # dependency cycle with live_xg_proxy.
    try:
        from . import live_xg_proxy as lxp
        home_side = lxp.extract_side(home_stats)
        away_side = lxp.extract_side(away_stats)
        h_xg = _f(getattr(home_side, "xg_live", 0.0))
        a_xg = _f(getattr(away_side, "xg_live", 0.0))
    except Exception:
        # Fail-soft: fall back to raw stat keys if the proxy is unavailable.
        h_xg = _f(home_stats.get("xg_live") or home_stats.get("xg"))
        a_xg = _f(away_stats.get("xg_live") or away_stats.get("xg"))

    combined_xg = round(h_xg + a_xg, 3)
    weaker_xg   = round(min(h_xg, a_xg), 3)
    stronger_xg = round(max(h_xg, a_xg), 3)
    one_sided_ratio = round(weaker_xg / combined_xg, 3) if combined_xg > 0 else 0.0

    is_bilateral = (h_xg >= MIN_SIDE_XG_FOR_OPEN and a_xg >= MIN_SIDE_XG_FOR_OPEN)
    is_one_sided = one_sided_ratio < ONE_SIDED_RATIO_THRESHOLD

    supports_over_35 = (
        combined_xg >= MIN_COMBINED_XG_FOR_OVER35
        and is_bilateral
        and not is_one_sided
    )
    supports_over_25 = (
        combined_xg >= MIN_COMBINED_XG_FOR_OVER25
        and not is_one_sided
    )

    # BTTS proxy: both sides creating chances and at least one team still
    # needs to score for BTTS to be live (can't recommend BTTS if it's
    # already 1-1+). We don't know the per-side score here, so the caller
    # passes current_total only as a sanity gate — BTTS makes most sense
    # when the match is open and total is still low.
    supports_btts = is_bilateral and not is_one_sided

    # Pick the safest total that the openness actually supports. Prefer the
    # *lower* line — the France case taught us not to over-reach.
    recommended_total: Optional[str] = None
    if supports_over_35:
        recommended_total = "Over 3.5"
    elif supports_over_25:
        recommended_total = "Over 2.5"
    elif supports_btts:
        recommended_total = "BTTS (Ambos marcan)"

    # Human reason
    if is_one_sided and stronger_xg >= MIN_SIDE_XG_FOR_OPEN:
        reason_es = (
            f"Amenaza desbalanceada: un solo equipo genera el peligro "
            f"(xG {stronger_xg:.2f} vs {weaker_xg:.2f}). Mejor un mercado de "
            f"equipo (gana / su Over) que un Over total alto."
        )
    elif supports_over_35:
        reason_es = (
            f"Apertura bilateral fuerte: xG combinado {combined_xg:.2f} con "
            f"ambos lados creando. El Over 3.5 tiene respaldo real."
        )
    elif supports_over_25:
        reason_es = (
            f"Partido de ida y vuelta: xG combinado {combined_xg:.2f}. "
            f"Over 2.5 / BTTS es la línea con respaldo, no Over 3.5."
        )
    elif supports_btts:
        reason_es = (
            f"Ambos equipos generan ocasiones (xG {h_xg:.2f} / {a_xg:.2f}). "
            f"BTTS es el mercado ofensivo con respaldo."
        )
    else:
        reason_es = (
            f"Sin apertura bilateral suficiente (xG combinado {combined_xg:.2f}). "
            f"Evitar Over total agresivo."
        )

    return {
        "combined_xg":        combined_xg,
        "home_xg":            round(h_xg, 3),
        "away_xg":            round(a_xg, 3),
        "weaker_side_xg":     weaker_xg,
        "stronger_side_xg":   stronger_xg,
        "one_sided_ratio":    one_sided_ratio,
        "is_bilateral":       is_bilateral,
        "is_one_sided":       is_one_sided,
        "supports_over_35":   supports_over_35,
        "supports_over_25":   supports_over_25,
        "supports_btts":      supports_btts,
        "recommended_total":  recommended_total,
        "reason_es":          reason_es,
    }


def guard_total_recommendation(
    proposed_market: str,
    openness: dict,
) -> dict:
    """Down-shift an over-reaching TOTAL recommendation to a line the
    bilateral openness actually supports.

    Returns ``{"market", "downgraded", "reason_es"}``. If the proposed
    market is already supported (or isn't a total market) it passes through
    unchanged with ``downgraded=False``.
    """
    m = (proposed_market or "").lower()
    is_over_35 = "over 3.5" in m or "más de 3.5" in m or "mas de 3.5" in m
    is_over_25 = "over 2.5" in m or "más de 2.5" in m or "mas de 2.5" in m

    # Only intervene on aggressive totals.
    if is_over_35 and not openness.get("supports_over_35"):
        fallback = openness.get("recommended_total")
        if fallback and fallback != proposed_market:
            return {
                "market":     fallback,
                "downgraded": True,
                "reason_es":  (
                    f"Over 3.5 sin respaldo bilateral → ajustado a {fallback}. "
                    + openness.get("reason_es", "")
                ),
            }
        # No safe fallback → mark as not actionable.
        return {
            "market":     proposed_market,
            "downgraded": False,
            "not_actionable": not openness.get("supports_over_25"),
            "reason_es":  openness.get("reason_es", ""),
        }

    if is_over_25 and not openness.get("supports_over_25"):
        return {
            "market":     proposed_market,
            "downgraded": False,
            "not_actionable": True,
            "reason_es":  openness.get("reason_es", ""),
        }

    # Passes through unchanged.
    return {"market": proposed_market, "downgraded": False, "reason_es": ""}


# ─────────────────────────────────────────────────────────────────────
# Unilateral Dominance Over profile
# ─────────────────────────────────────────────────────────────────────
# The Mexico 5-1 Serbia case taught us that some Over 3.5 games are
# legitimately driven by *unilateral* dominance plus defensive collapse —
# NOT by bilateral openness. The two paths are philosophically distinct:
#
#   * BILATERAL_OPENNESS_OVER: both sides create — natural high-total.
#   * UNILATERAL_DOMINANCE_OVER: one side crushes; opponent collapses
#     (own goals, errors, red card, GK saves overload, set-piece flood,
#     late fatigue, or game-state forcing them to chase).
#
# This module emits the dominance profile so downstream layers can
# decide whether to surface a *team total* or a (gated) match Over high.
# It never recommends BTTS — the dominated side is, by definition,
# being shut down.
# ─────────────────────────────────────────────────────────────────────

# Dominance gates (calibrated against Mexico 5-1 Serbia + similar games).
MIN_DOMINANT_XG_FOR_DOMINANCE     = 1.75
MIN_DOMINANT_SHOTS_FOR_DOMINANCE  = 14
MIN_DOMINANT_SOT_FOR_DOMINANCE    = 5
MAX_OPPONENT_SHOTS_FOR_DOMINANCE  = 5


def compute_unilateral_dominance_over_profile(
    home_stats: dict,
    away_stats: dict,
    match_context: dict | None = None,
) -> dict:
    """Detect unilateral-dominance games that can sustain Over 3.5
    *without* requiring bilateral openness.

    The function returns a structured profile; it does NOT replace
    ``compute_game_openness``. Callers may consult both: if openness
    says ``is_one_sided=True``, they can fall back to this profile to
    see whether the dominant side carries enough collapse signals to
    justify a *team total* or a gated match Over high.

    Output keys
    -----------
    ``profile_type``       always "UNILATERAL_DOMINANCE_OVER" when
                           ``is_dominant`` is True; otherwise "NONE".
    ``is_dominant``        bool — passes all dominance gates.
    ``has_collapse``       bool — at least one collapse indicator.
    ``supports_team_total``      ``True`` if dominance gates pass.
    ``supports_match_over_high`` ``True`` ONLY if dominance + at least
                                 one collapse indicator are present.
    ``dominant_side``      "home" / "away" / None.
    ``collapse_indicators`` list of detected codes.
    ``reason_codes``       canonical codes.
    ``reason_es``          human Spanish explanation.

    Pure, fail-soft.
    """
    ctx = match_context or {}
    try:
        from . import live_xg_proxy as lxp
        h_side = lxp.extract_side(home_stats)
        a_side = lxp.extract_side(away_stats)
        h_xg, a_xg = _f(getattr(h_side, "xg_live", 0)), _f(getattr(a_side, "xg_live", 0))
        h_shots, a_shots = int(getattr(h_side, "shots", 0)), int(getattr(a_side, "shots", 0))
        h_sot, a_sot     = int(getattr(h_side, "shots_on_target", 0)), int(getattr(a_side, "shots_on_target", 0))
    except Exception:
        h_xg = _f(home_stats.get("xg_live") or home_stats.get("xg") or home_stats.get("expected_goals"))
        a_xg = _f(away_stats.get("xg_live") or away_stats.get("xg") or away_stats.get("expected_goals"))
        h_shots = int(_f(home_stats.get("shots") or 0))
        a_shots = int(_f(away_stats.get("shots") or 0))
        h_sot   = int(_f(home_stats.get("shots_on_target") or 0))
        a_sot   = int(_f(away_stats.get("shots_on_target") or 0))

    # Identify the dominant side.
    if h_xg > a_xg:
        dom_side = "home"
        dom_xg, dom_shots, dom_sot = h_xg, h_shots, h_sot
        opp_shots = a_shots
        dom_stats, opp_stats = home_stats, away_stats
    else:
        dom_side = "away"
        dom_xg, dom_shots, dom_sot = a_xg, a_shots, a_sot
        opp_shots = h_shots
        dom_stats, opp_stats = away_stats, home_stats

    # Dominance gates (numeric, must ALL pass).
    is_dominant = (
        dom_xg    >= MIN_DOMINANT_XG_FOR_DOMINANCE
        and dom_shots >= MIN_DOMINANT_SHOTS_FOR_DOMINANCE
        and dom_sot   >= MIN_DOMINANT_SOT_FOR_DOMINANCE
        and opp_shots <= MAX_OPPONENT_SHOTS_FOR_DOMINANCE
    )

    # Defensive-collapse indicators (any one triggers ``has_collapse``).
    collapse: list[str] = []
    own_goals = int(_f(opp_stats.get("own_goals") or ctx.get("opponent_own_goals") or 0))
    if own_goals >= 1:
        collapse.append("OPPONENT_OWN_GOAL")

    err_to_shot = int(_f(opp_stats.get("errors_leading_to_shot")
                          or opp_stats.get("errors_to_shot") or 0))
    if err_to_shot >= 1:
        collapse.append("OPPONENT_ERROR_TO_SHOT")

    err_to_goal = int(_f(opp_stats.get("errors_leading_to_goal")
                          or opp_stats.get("errors_to_goal") or 0))
    if err_to_goal >= 1:
        collapse.append("OPPONENT_ERROR_TO_GOAL")

    red_cards = int(_f(opp_stats.get("red_cards") or ctx.get("opponent_red_cards") or 0))
    if red_cards >= 1:
        collapse.append("OPPONENT_RED_CARD")

    saves = int(_f(opp_stats.get("saves") or opp_stats.get("goalkeeper_saves") or 0))
    if saves >= 4:
        collapse.append("OPPONENT_GK_OVERLOAD")

    # Set-piece flood: dominant side corners ≥ 6 AND opponent corners ≤ 2.
    dom_corners = int(_f(dom_stats.get("corners") or 0))
    opp_corners = int(_f(opp_stats.get("corners") or 0))
    if dom_corners >= 6 and opp_corners <= 2:
        collapse.append("DOMINANT_SET_PIECE_FLOOD")

    # Late fatigue: minute >= 70 AND opponent shots ≤ 3.
    minute = ctx.get("minute")
    if isinstance(minute, (int, float)) and minute >= 70 and opp_shots <= 3:
        collapse.append("OPPONENT_LATE_FATIGUE")

    # Game-state forcing the opponent to open up (chasing >= 2 goals).
    score_diff = ctx.get("score_diff")
    if isinstance(score_diff, (int, float)) and score_diff >= 2:
        collapse.append("OPPONENT_CHASING_GAME_STATE")

    # Already-high total snowball: total ≥ 3 and minute ≤ 70 (still time).
    current_total = ctx.get("current_total")
    if isinstance(current_total, (int, float)) and current_total >= 3:
        if not minute or minute <= 80:
            collapse.append("HIGH_TOTAL_SNOWBALL")

    has_collapse = len(collapse) >= 1

    supports_team_total = is_dominant
    supports_match_over_high = is_dominant and has_collapse

    reason_codes: list[str] = []
    if is_dominant:
        reason_codes.append("UNILATERAL_DOMINANCE_DETECTED")
        reason_codes.append(f"DOMINANT_SIDE_{dom_side.upper()}")
        reason_codes.extend(collapse)
    if supports_match_over_high:
        reason_codes.append("MATCH_OVER_HIGH_VIA_DOMINANCE")
    elif is_dominant:
        reason_codes.append("DOMINANCE_WITHOUT_COLLAPSE_TEAM_TOTAL_ONLY")

    if not is_dominant:
        reason_es = (
            "Sin dominancia unilateral suficiente para Over alto: "
            f"xG dominante={dom_xg:.2f}, tiros={dom_shots}, "
            f"a puerta={dom_sot}, oponente tiros={opp_shots}."
        )
    elif supports_match_over_high:
        reason_es = (
            f"Dominancia unilateral del {dom_side} (xG {dom_xg:.2f}, "
            f"tiros {dom_shots}, SOT {dom_sot}) con colapso defensivo "
            f"({', '.join(collapse)}). El Over alto se respalda por "
            f"colapso, no por apertura bilateral."
        )
    else:
        reason_es = (
            f"Dominancia unilateral del {dom_side} sin señales de colapso. "
            "Considerar **team total** del lado dominante, no Over de partido."
        )

    return {
        "profile_type":               "UNILATERAL_DOMINANCE_OVER" if is_dominant else "NONE",
        "is_dominant":                is_dominant,
        "has_collapse":               has_collapse,
        "supports_team_total":        supports_team_total,
        "supports_match_over_high":   supports_match_over_high,
        "dominant_side":              dom_side if is_dominant else None,
        "dominant_xg":                round(dom_xg, 3),
        "dominant_shots":             dom_shots,
        "dominant_sot":               dom_sot,
        "opponent_shots":             opp_shots,
        "collapse_indicators":        collapse,
        "reason_codes":               reason_codes,
        "reason_es":                  reason_es,
    }


__all__ = [
    "compute_game_openness",
    "guard_total_recommendation",
    "compute_unilateral_dominance_over_profile",
    "MIN_SIDE_XG_FOR_OPEN",
    "MIN_COMBINED_XG_FOR_OVER35",
    "MIN_COMBINED_XG_FOR_OVER25",
    "ONE_SIDED_RATIO_THRESHOLD",
    "MIN_DOMINANT_XG_FOR_DOMINANCE",
    "MIN_DOMINANT_SHOTS_FOR_DOMINANCE",
    "MIN_DOMINANT_SOT_FOR_DOMINANCE",
    "MAX_OPPONENT_SHOTS_FOR_DOMINANCE",
]
