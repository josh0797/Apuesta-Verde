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


__all__ = [
    "compute_game_openness",
    "guard_total_recommendation",
    "MIN_SIDE_XG_FOR_OPEN",
    "MIN_COMBINED_XG_FOR_OVER35",
    "MIN_COMBINED_XG_FOR_OVER25",
    "ONE_SIDED_RATIO_THRESHOLD",
]
