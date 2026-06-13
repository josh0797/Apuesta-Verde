"""Phase F83.2-E3 — Contextual xG signals.

Derives a set of *contextual* signals from the L1/L5/L15 xG averages
produced by ``football_xg_recent_averages.compute_xg_recent_averages``.

These signals are intentionally **non-binding**: they NEVER force a
pick — they only feed the editorial layer with concrete evidence the
operator can read. The thresholds below mirror the product spec.
"""
from __future__ import annotations

from typing import Any, Optional

# ── Signal codes ─────────────────────────────────────────────────────
LOW_RECENT_XG_PROFILE      = "LOW_RECENT_XG_PROFILE"
DEFENSIVE_XG_SUPPRESSION   = "DEFENSIVE_XG_SUPPRESSION"
XG_FORM_SHIFT              = "XG_FORM_SHIFT"
XG_APOYA_UNDER             = "XG_APOYA_UNDER"
XG_APOYA_OVER              = "XG_APOYA_OVER"

# Partial-sample / coverage signals (Phase F83.2-E5).
# These NEVER force a pick; they only describe the analytical confidence
# we can place on the recent xG window. The editorial layer reads them
# to downgrade the strength of Over/Under conclusions when the sample is
# too thin (e.g. only L1 available).
XG_PARTIAL_SAMPLE                = "XG_PARTIAL_SAMPLE"
XG_L1_ONLY                       = "XG_L1_ONLY"
XG_L5_AVAILABLE_L15_MISSING      = "XG_L5_AVAILABLE_L15_MISSING"
XG_L15_AVAILABLE_L5_MISSING      = "XG_L15_AVAILABLE_L5_MISSING"
XG_RECENT_SAMPLE_INSUFFICIENT    = "XG_RECENT_SAMPLE_INSUFFICIENT"

# ── Thresholds (product spec) ────────────────────────────────────────
LOW_XG_PER_SIDE            = 1.25
LOW_COMBINED_L5_XG_FOR     = 2.40
SUPPRESSION_XG_AGAINST_MAX = 1.10

UNDER_COMBINED_L5_MAX      = 2.35
UNDER_COMBINED_L15_MAX     = 2.50
OVER_COMBINED_L5_MIN       = 2.90
OVER_COMBINED_L15_MIN      = 2.75

FORM_SHIFT_DELTA           = 0.60  # |combined_L5 - combined_L15|


def _val(side: dict, window: str, key: str) -> Optional[float]:
    if not isinstance(side, dict):
        return None
    w = side.get(window) or {}
    if not isinstance(w, dict):
        return None
    v = w.get(key)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _side_has(side: dict, window: str) -> bool:
    """Return True iff the side has a usable window block with xg_for_avg."""
    if not isinstance(side, dict):
        return False
    w = side.get(window)
    if not isinstance(w, dict):
        return False
    return w.get("xg_for_avg") is not None


def derive_xg_signals(xg_recent: dict) -> dict:
    """Return ``{signals: [code, ...], explanations: {code: text}}``.

    Always returns a dict — never raises. When the input is unavailable
    or shaped unexpectedly, returns an empty signals list.
    """
    out: dict[str, Any] = {"signals": [], "explanations": {}, "metrics": {}}
    if not isinstance(xg_recent, dict) or not xg_recent.get("available"):
        return out

    home = xg_recent.get("home") or {}
    away = xg_recent.get("away") or {}

    home_l5_for  = _val(home, "l5",  "xg_for_avg")
    away_l5_for  = _val(away, "l5",  "xg_for_avg")
    home_l15_for = _val(home, "l15", "xg_for_avg")
    away_l15_for = _val(away, "l15", "xg_for_avg")
    home_l5_ag   = _val(home, "l5",  "xg_against_avg")
    away_l5_ag   = _val(away, "l5",  "xg_against_avg")

    combined_l5  = (None if home_l5_for is None or away_l5_for is None
                    else round(home_l5_for + away_l5_for, 3))
    combined_l15 = (None if home_l15_for is None or away_l15_for is None
                    else round(home_l15_for + away_l15_for, 3))
    out["metrics"] = {
        "combined_l5_xg_for":  combined_l5,
        "combined_l15_xg_for": combined_l15,
    }

    def _emit(code: str, msg: str) -> None:
        out["signals"].append(code)
        out["explanations"][code] = msg

    # ── Partial-sample / coverage signals ────────────────────────────
    # Compute coverage per window. We consider a window "available" only
    # when BOTH sides have it, since cross-side metrics (combined L5,
    # combined L15) are what drive Over/Under reasoning.
    l1_both  = _side_has(home, "l1")  and _side_has(away, "l1")
    l5_both  = _side_has(home, "l5")  and _side_has(away, "l5")
    l15_both = _side_has(home, "l15") and _side_has(away, "l15")

    out["metrics"]["coverage"] = {
        "l1_both":  l1_both,
        "l5_both":  l5_both,
        "l15_both": l15_both,
    }

    # XG_PARTIAL_SAMPLE — at least one of L5 / L15 is missing on either
    # side. Mirrors the ``partial`` flag in the aggregator output but is
    # emitted explicitly so the editorial layer can pick it up alongside
    # the more granular coverage codes below.
    if xg_recent.get("partial") or not (l5_both and l15_both):
        _emit(XG_PARTIAL_SAMPLE,
              "Muestra parcial: al menos una ventana (L5/L15) no está "
              "disponible para ambos lados — usar el xG como contexto, "
              "no como confirmación robusta.")

    # XG_L1_ONLY — only the most recent fixture is available for both
    # sides. Strongest "limited sample" signal: prohibits using L1 to
    # confirm Over/Under by itself.
    if l1_both and not l5_both and not l15_both:
        _emit(XG_L1_ONLY,
              "Solo hay xG del último partido (L1) en ambos lados — "
              "muestra muy limitada, no usar para apoyar Over/Under.")

    # XG_L5_AVAILABLE_L15_MISSING — short-term form known, long-term not.
    if l5_both and not l15_both:
        _emit(XG_L5_AVAILABLE_L15_MISSING,
              "L5 disponible para ambos lados pero L15 incompleto — "
              "contexto de forma reciente sólido, tendencia larga "
              "incierta.")

    # XG_L15_AVAILABLE_L5_MISSING — long-term form known, short-term not.
    if l15_both and not l5_both:
        _emit(XG_L15_AVAILABLE_L5_MISSING,
              "L15 disponible para ambos lados pero L5 incompleto — "
              "tendencia larga conocida, forma reciente incierta.")

    # XG_RECENT_SAMPLE_INSUFFICIENT — neither L5 nor L15 are available
    # for both sides. The strongest "do not lean on xG" code.
    if not l5_both and not l15_both:
        _emit(XG_RECENT_SAMPLE_INSUFFICIENT,
              "Muestra reciente insuficiente — ni L5 ni L15 cubren a "
              "ambos lados; el xG no puede usarse como evidencia "
              "fuerte para Over/Under.")

    # LOW_RECENT_XG_PROFILE
    if (home_l5_for is not None and away_l5_for is not None
            and home_l5_for < LOW_XG_PER_SIDE and away_l5_for < LOW_XG_PER_SIDE
            and combined_l5 is not None and combined_l5 < LOW_COMBINED_L5_XG_FOR):
        _emit(LOW_RECENT_XG_PROFILE,
              f"Ambos equipos generan poco xG en L5 (home={home_l5_for}, "
              f"away={away_l5_for}, combined={combined_l5}).")

    # DEFENSIVE_XG_SUPPRESSION
    if (home_l5_ag is not None and away_l5_ag is not None
            and home_l5_ag < SUPPRESSION_XG_AGAINST_MAX
            and away_l5_ag < SUPPRESSION_XG_AGAINST_MAX):
        _emit(DEFENSIVE_XG_SUPPRESSION,
              f"Ambas defensas conceden poco xG en L5 (home_against={home_l5_ag}, "
              f"away_against={away_l5_ag}).")

    # XG_FORM_SHIFT — L5 and L15 diverge significantly.
    if combined_l5 is not None and combined_l15 is not None:
        delta = combined_l5 - combined_l15
        if abs(delta) >= FORM_SHIFT_DELTA:
            direction = "alta reciente" if delta > 0 else "baja reciente"
            _emit(XG_FORM_SHIFT,
                  f"L5 ({combined_l5}) divergente de L15 ({combined_l15}) → {direction}.")

    # XG_APOYA_UNDER
    if (combined_l5 is not None and combined_l5 <= UNDER_COMBINED_L5_MAX
            and combined_l15 is not None and combined_l15 <= UNDER_COMBINED_L15_MAX):
        _emit(XG_APOYA_UNDER,
              "xG reciente apoya perfil conservador/Under "
              f"(L5={combined_l5} ≤ {UNDER_COMBINED_L5_MAX}, "
              f"L15={combined_l15} ≤ {UNDER_COMBINED_L15_MAX}).")

    # XG_APOYA_OVER
    if (combined_l5 is not None and combined_l5 >= OVER_COMBINED_L5_MIN
            and combined_l15 is not None and combined_l15 >= OVER_COMBINED_L15_MIN):
        _emit(XG_APOYA_OVER,
              "xG reciente sugiere apertura ofensiva "
              f"(L5={combined_l5} ≥ {OVER_COMBINED_L5_MIN}, "
              f"L15={combined_l15} ≥ {OVER_COMBINED_L15_MIN}).")

    return out


__all__ = [
    "derive_xg_signals",
    "LOW_RECENT_XG_PROFILE", "DEFENSIVE_XG_SUPPRESSION",
    "XG_FORM_SHIFT", "XG_APOYA_UNDER", "XG_APOYA_OVER",
    "XG_PARTIAL_SAMPLE", "XG_L1_ONLY",
    "XG_L5_AVAILABLE_L15_MISSING", "XG_L15_AVAILABLE_L5_MISSING",
    "XG_RECENT_SAMPLE_INSUFFICIENT",
    "LOW_XG_PER_SIDE", "LOW_COMBINED_L5_XG_FOR",
    "SUPPRESSION_XG_AGAINST_MAX",
    "UNDER_COMBINED_L5_MAX", "UNDER_COMBINED_L15_MAX",
    "OVER_COMBINED_L5_MIN",  "OVER_COMBINED_L15_MIN",
    "FORM_SHIFT_DELTA",
]
