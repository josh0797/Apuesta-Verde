"""Phase F85.3 — Public xG signals (FBref + Forebet).

Reads the canonical ``xg_recent_averages`` snapshot (from
:mod:`football_xg_public_normalizer`) plus the optional Forebet context
block and emits soft analytical signals. Signals NEVER force a pick —
they only inform editorial copy, fragility scoring and the protected-
market review.
"""
from __future__ import annotations

from typing import Any

# Signal codes — stable.
LOW_RECENT_XG_PROFILE       = "LOW_RECENT_XG_PROFILE"
HIGH_RECENT_XG_PROFILE      = "HIGH_RECENT_XG_PROFILE"
DEFENSIVE_XG_SUPPRESSION    = "DEFENSIVE_XG_SUPPRESSION"
XG_FORM_SHIFT               = "XG_FORM_SHIFT"
XG_SUPPORTS_UNDER           = "XG_SUPPORTS_UNDER"
XG_SUPPORTS_OVER            = "XG_SUPPORTS_OVER"
FOREBET_CONFLICTS_WITH_XG   = "FOREBET_CONFLICTS_WITH_XG"
FOREBET_CONFIRMS_XG         = "FOREBET_CONFIRMS_XG"
PUBLIC_XG_PARTIAL_SAMPLE    = "PUBLIC_XG_PARTIAL_SAMPLE"

# Thresholds (spec).
TH_UNDER_L5_MAX        = 2.35
TH_UNDER_L15_MAX       = 2.50
TH_OVER_L5_MIN         = 2.90
TH_OVER_L15_MIN        = 2.75
TH_DEFENSIVE_L5_MAX    = 1.10
TH_FORM_SHIFT_DELTA    = 0.45


def _get(snap: dict, side: str, window: str, field: str) -> Any:
    if not isinstance(snap, dict):
        return None
    s = snap.get(side)
    if not isinstance(s, dict):
        return None
    w = s.get(window)
    if not isinstance(w, dict):
        return None
    return w.get(field)


def _safe_abs_delta(a: Any, b: Any) -> float | None:
    if a is None or b is None:
        return None
    try:
        return abs(float(a) - float(b))
    except (TypeError, ValueError):
        return None


def _predicted_total_goals(forebet: dict) -> int | None:
    """Forebet's predicted goals = home + away in the predicted score."""
    if not isinstance(forebet, dict):
        return None
    score = forebet.get("predicted_score")
    if not isinstance(score, str):
        return None
    parts = score.replace(":", "-").split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) + int(parts[1])
    except ValueError:
        return None


def derive_public_xg_signals(
    xg_recent_averages: dict,
    forebet_context: dict | None = None,
) -> dict:
    """Return ``{signals, explanations, metrics}`` for the given snapshot.
    Never raises. Returns empty signals when xG snapshot is unavailable.
    """
    out: dict[str, Any] = {"signals": [], "explanations": {}, "metrics": {}}
    if not isinstance(xg_recent_averages, dict) or not xg_recent_averages.get("available"):
        return out

    derived = xg_recent_averages.get("derived") or {}
    combined_l5_for  = derived.get("combined_l5_xg_for")
    combined_l15_for = derived.get("combined_l15_xg_for")
    combined_l5_ag   = derived.get("combined_l5_xga")

    out["metrics"] = {
        "combined_l5_xg_for":  combined_l5_for,
        "combined_l15_xg_for": combined_l15_for,
        "combined_l5_xga":     combined_l5_ag,
    }

    def _emit(code: str, msg: str) -> None:
        out["signals"].append(code)
        out["explanations"][code] = msg

    # Partial-sample signal.
    if xg_recent_averages.get("partial"):
        _emit(PUBLIC_XG_PARTIAL_SAMPLE,
              "Muestra parcial de xG público — usar como contexto, no "
              "como confirmación robusta.")

    supports_under = supports_over = False

    if combined_l5_for is not None and combined_l15_for is not None:
        if combined_l5_for <= TH_UNDER_L5_MAX and combined_l15_for <= TH_UNDER_L15_MAX:
            supports_under = True
            _emit(XG_SUPPORTS_UNDER,
                  f"xG combinado L5={combined_l5_for} y L15={combined_l15_for} "
                  f"apuntan a perfil Under.")
            _emit(LOW_RECENT_XG_PROFILE,
                  "Perfil xG reciente bajo en ambos lados.")
        elif combined_l5_for >= TH_OVER_L5_MIN and combined_l15_for >= TH_OVER_L15_MIN:
            supports_over = True
            _emit(XG_SUPPORTS_OVER,
                  f"xG combinado L5={combined_l5_for} y L15={combined_l15_for} "
                  f"apuntan a perfil Over.")
            _emit(HIGH_RECENT_XG_PROFILE,
                  "Perfil xG reciente alto en ambos lados.")

        # Form shift.
        delta = _safe_abs_delta(combined_l5_for, combined_l15_for)
        if delta is not None and delta >= TH_FORM_SHIFT_DELTA:
            _emit(XG_FORM_SHIFT,
                  f"Cambio relevante de forma: |L5 - L15| = {round(delta, 3)}.")

    # Defensive suppression: xG_against per side both low (L5).
    home_l5_ag = _get(xg_recent_averages, "home", "l5", "xg_against_avg")
    away_l5_ag = _get(xg_recent_averages, "away", "l5", "xg_against_avg")
    if (home_l5_ag is not None and away_l5_ag is not None
            and home_l5_ag < TH_DEFENSIVE_L5_MAX
            and away_l5_ag < TH_DEFENSIVE_L5_MAX):
        _emit(DEFENSIVE_XG_SUPPRESSION,
              "Ambos equipos con xGA L5 bajo — supresión defensiva reciente.")

    # Forebet confirmation / conflict.
    forebet_total = _predicted_total_goals(forebet_context or {})
    if forebet_total is not None:
        if supports_under and forebet_total >= 3:
            _emit(FOREBET_CONFLICTS_WITH_XG,
                  f"xG apoya Under pero Forebet predice {forebet_total}+ goles.")
        elif supports_over and forebet_total >= 3:
            _emit(FOREBET_CONFIRMS_XG,
                  f"xG apoya Over y Forebet predice {forebet_total}+ goles.")
        elif supports_under and forebet_total <= 2:
            _emit(FOREBET_CONFIRMS_XG,
                  f"xG apoya Under y Forebet predice {forebet_total} goles.")
        elif supports_over and forebet_total <= 2:
            _emit(FOREBET_CONFLICTS_WITH_XG,
                  f"xG apoya Over pero Forebet predice solo {forebet_total} goles.")

    return out


__all__ = [
    "derive_public_xg_signals",
    "LOW_RECENT_XG_PROFILE", "HIGH_RECENT_XG_PROFILE",
    "DEFENSIVE_XG_SUPPRESSION", "XG_FORM_SHIFT",
    "XG_SUPPORTS_UNDER", "XG_SUPPORTS_OVER",
    "FOREBET_CONFLICTS_WITH_XG", "FOREBET_CONFIRMS_XG",
    "PUBLIC_XG_PARTIAL_SAMPLE",
    "TH_UNDER_L5_MAX", "TH_UNDER_L15_MAX",
    "TH_OVER_L5_MIN",  "TH_OVER_L15_MIN",
    "TH_DEFENSIVE_L5_MAX", "TH_FORM_SHIFT_DELTA",
]
