"""Live Market Ranker.

Produces a dynamic ranking of live markets given:
  • the territorial-control payload (`evaluate_live_territorial_control`),
  • the corner-pressure payload (`evaluate_corner_pressure`),
  • the underlying metrics (xG, shots, score, minute).

The ranker enforces the user's CRITICAL RULE::

    > NO recomendar OVER_GOALS / BTTS / NEXT_GOAL solo porque exista
    > posesión alta. Debe existir además xG suficiente + tiros suficientes
    > + presión creciente. De lo contrario priorizar Corners / Esperar.

Pure function — no IO.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("live_market_ranker")


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


def _team_name(metrics: dict, side: Optional[str]) -> Optional[str]:
    if side == "home":
        return metrics.get("home_team") or "Local"
    if side == "away":
        return metrics.get("away_team") or "Visitante"
    return None


def _wait_card() -> dict:
    return {
        "market":    "Esperar",
        "category":  "WAIT",
        "score":     45,
        "rationale": "No hay evidencia suficiente para recomendar un mercado live ahora.",
    }


def rank_live_markets(
    metrics: dict,
    territorial: dict,
    corner: dict,
) -> list[dict]:
    """Return an ordered list of live markets (highest score first).

    Each item shape::
        {
          "market":     str,
          "category":   str,
          "score":      0..100,
          "side":       'home'|'away'|None,
          "rationale":  str,
        }
    """
    metrics     = metrics     or {}
    territorial = territorial or {}
    corner      = corner      or {}

    state = territorial.get("state") or "NO_CLEAR_DOMINANCE"
    controlling = territorial.get("controlling_side")
    strong_conv = bool(territorial.get("strong_conversion"))
    indicators = territorial.get("indicators") or {}
    ctrl_xg = _f(indicators.get("controlling_xg"))
    ctrl_shots = _i(indicators.get("controlling_shots"))
    ctrl_sot = _i(indicators.get("controlling_sot"))
    pressure_score = _f(indicators.get("pressure_score"))

    out: list[dict] = []

    # ── Corner markets — always first when corner-pressure surfaces ─────
    if corner.get("surface_recommendation") and corner.get("market_candidates"):
        out.extend(corner["market_candidates"])

    # ── Goal markets — STRICT gate per the user's CRITICAL RULE ─────────
    # Only allow Over 2.5 / BTTS / Next Goal when the controlling side has
    # genuine offensive output, NOT just possession.
    can_recommend_goal_markets = (
        controlling is not None
        and strong_conv
        and state in ("CONTROL_WITH_PRESSURE", "NO_CLEAR_DOMINANCE")
    )

    if can_recommend_goal_markets:
        team = _team_name(metrics, controlling)
        # Next goal — bias towards controlling side when it has clear
        # offensive momentum.
        next_goal_score = 50 + min(25, ctrl_xg * 35) + min(15, ctrl_shots * 2) + min(10, pressure_score / 8)
        out.append({
            "market":    f"Siguiente gol: {team}",
            "category":  "NEXT_GOAL",
            "side":      controlling,
            "score":     round(min(85, next_goal_score), 1),
            "rationale": (
                f"{team} controla con xG {ctrl_xg:.2f} y {ctrl_shots} tiros — "
                "presión real, no solo posesión."
            ),
        })
        # Over 2.5 — needs even stronger conversion signals.
        if ctrl_xg >= 0.80 or ctrl_sot >= 3:
            score_h = _i(metrics.get("score_home"))
            score_a = _i(metrics.get("score_away"))
            goals_so_far = score_h + score_a
            o25_score = 45 + (ctrl_xg * 30) + (goals_so_far * 8) + min(10, pressure_score / 10)
            out.append({
                "market":    "Over 2.5 goles",
                "category":  "OVER_GOALS",
                "side":      None,
                "score":     round(min(80, o25_score), 1),
                "rationale": (
                    f"xG {ctrl_xg:.2f} del lado dominante + ritmo ofensivo "
                    "sostienen un Over 2.5."
                ),
            })
        # BTTS — needs both teams to threaten.
        other_side = "away" if controlling == "home" else "home"
        other_xg = _f(metrics.get(f"xg_{other_side}"))
        if other_xg >= 0.30 and ctrl_xg >= 0.50:
            btts_score = 50 + (ctrl_xg * 20) + (other_xg * 25)
            out.append({
                "market":    "Ambos equipos anotan: Sí",
                "category":  "BTTS",
                "side":      None,
                "score":     round(min(78, btts_score), 1),
                "rationale": (
                    f"Ambos lados generan ocasiones (xG {ctrl_xg:.2f} vs {other_xg:.2f})."
                ),
            })

    # ── Always include a Wait card so the user can never end up with an
    #    empty ranking. Position is computed by score. ───────────────────
    out.append(_wait_card())

    # ── If we are in pure TERRITORIAL_CONTROL (no depth), elevate Corners
    #    above Wait and make sure the controlling team's next goal is NOT
    #    pushed onto the user (rule: don't recommend goals only by
    #    possession). ─────────────────────────────────────────────────────
    if state == "TERRITORIAL_CONTROL" and not strong_conv:
        out = [
            m for m in out
            if m["category"] not in ("NEXT_GOAL", "OVER_GOALS", "BTTS")
        ]

    # ── Sort by score descending; ties keep insertion order (corners come
    #    first by design when surfaced). ──────────────────────────────────
    out.sort(key=lambda m: m.get("score") or 0, reverse=True)

    # Deduplicate by market label.
    seen: set = set()
    dedup: list[dict] = []
    for m in out:
        k = (m.get("market") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            dedup.append(m)

    return dedup


__all__ = [
    "rank_live_markets",
]
