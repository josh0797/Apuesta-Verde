"""Football L5 vs L15 Profile Cross — Phase F58.

Crosses each team's **last 5** vs **last 15** matches across six metrics
(goals_for, goals_against, xg, xga, shots, shots_on_target, corners) and
classifies the matchup into one of seven combined profiles:

    STRONG_UNDER_CROSS         → ambas defensas firmes y ambas ofensivas frías
    LOW_EVENT_UNDER_CROSS      → poco volumen de eventos (tiros, SOT) en ambos
    STRONG_OVER_CROSS          → ambas ofensivas calientes y ambas defensas leaky
    BILATERAL_BTTS_CROSS       → ambos marcan y conceden con frecuencia
    UNILATERAL_DOMINANCE_CROSS → un lado domina (xG+ y xGA−) en L5
    CORNERS_OVER_CROSS         → ambos generando volumen de corners por encima
    MIXED_PROFILE              → señales mixtas, sin edge claro

Design contract (mirrors Phase 58/59 MLB convention)
----------------------------------------------------
* Pure module, no IO. Fail-soft when inputs are missing.
* Emits ``confidence_delta``/``fragility_delta`` (signed magnitudes) plus
  a ``supports`` field ∈ {"OVER","UNDER","BTTS","CORNERS","NEUTRAL"}.
* ``apply_profile_cross_to_pick`` aplica los deltas simétricamente y, en
  perfiles **muy fuertes**, puede recomendar un **override de pick**
  (la decisión final la toma el orchestrator).
* Override gating (confirmado por usuario):
      Solo perfiles {STRONG_UNDER_CROSS, STRONG_OVER_CROSS,
                     CORNERS_OVER_CROSS} pueden disparar override
      y solo si la magnitud del cross es "muy fuerte"
      (``confidence_delta`` >= STRONG_OVERRIDE_THRESHOLD).
* Visual entry (``build_pattern_alignment_entry``) tiene ``visual_only=True``
  para que el contrarbloque CAMBIO 4 (contradiction penalty) no doble-cuente.

Caps simétricos
---------------
* ``MAX_CONFIDENCE_BONUS    = 8``
* ``MAX_CONFIDENCE_PENALTY  = 12``
* fragility / confidence ∈ [0, 100] (los aplica el orchestrator).
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "football_team_profile_cross.v1"

# ── Caps for orchestrator ────────────────────────────────────────────
MAX_CONFIDENCE_BONUS = 8
MAX_CONFIDENCE_PENALTY = 12

# ── Override gating (Fase 4 — confirmed by user) ─────────────────────
STRONG_OVERRIDE_PROFILES = frozenset({
    "STRONG_UNDER_CROSS",
    "STRONG_OVER_CROSS",
    "CORNERS_OVER_CROSS",
})
STRONG_OVERRIDE_THRESHOLD = 10  # confidence_delta >= 10 ⇒ permite override

# ── Per-team L5 thresholds ───────────────────────────────────────────
TEAM_COLD_GF_L5      = 0.9    # goals_for L5 ≤ 0.9 ⇒ ofensiva fría
TEAM_HOT_GF_L5       = 1.8    # goals_for L5 ≥ 1.8 ⇒ ofensiva caliente
TEAM_TIGHT_GA_L5     = 0.9    # goals_against L5 ≤ 0.9 ⇒ defensa firme
TEAM_LEAKY_GA_L5     = 1.7    # goals_against L5 ≥ 1.7 ⇒ defensa permeable

TEAM_COLD_XG_L5      = 1.0
TEAM_HOT_XG_L5       = 1.8
TEAM_TIGHT_XGA_L5    = 1.0
TEAM_LEAKY_XGA_L5    = 1.7

TEAM_LOW_SHOTS_L5    = 9.0
TEAM_HIGH_SHOTS_L5   = 14.0
TEAM_LOW_SOT_L5      = 3.0
TEAM_HIGH_SOT_L5     = 5.5

TEAM_LOW_CORNERS_L5  = 4.0
TEAM_HIGH_CORNERS_L5 = 6.5

# Delta significance (L5 minus L15)
DELTA_GOAL_SHIFT     = 0.30
DELTA_XG_SHIFT       = 0.30
DELTA_SHOTS_SHIFT    = 1.5
DELTA_SOT_SHIFT      = 0.7
DELTA_CORNERS_SHIFT  = 0.8

# BTTS / dominance heuristics
BTTS_GF_FLOOR        = 1.1    # both scoring ≥ 1.1 ⇒ BTTS signal
BTTS_GA_FLOOR        = 1.1    # both conceding ≥ 1.1
DOMINANCE_XG_DIFF    = 0.8    # one side's (xg - xga) advantage over the other in L5
DOMINANCE_GF_DIFF    = 0.6

# ── Reason codes ─────────────────────────────────────────────────────
RC_TEAM_OFFENSE_COOLING       = "TEAM_OFFENSE_COOLING"
RC_TEAM_OFFENSE_HEATING       = "TEAM_OFFENSE_HEATING"
RC_TEAM_DEFENSE_TIGHTENING    = "TEAM_DEFENSE_TIGHTENING"
RC_TEAM_DEFENSE_LEAKING       = "TEAM_DEFENSE_LEAKING"
RC_TEAM_LOW_EVENT_VOLUME      = "TEAM_LOW_EVENT_VOLUME"
RC_TEAM_HIGH_EVENT_VOLUME     = "TEAM_HIGH_EVENT_VOLUME"
RC_TEAM_CORNERS_TRENDING_UP   = "TEAM_CORNERS_TRENDING_UP"

RC_BOTH_OFFENSES_LOW_L5       = "BOTH_OFFENSES_LOW_L5"
RC_BOTH_OFFENSES_HIGH_L5      = "BOTH_OFFENSES_HIGH_L5"
RC_BOTH_DEFENSES_TIGHT_L5     = "BOTH_DEFENSES_TIGHT_L5"
RC_BOTH_DEFENSES_LEAKY_L5     = "BOTH_DEFENSES_LEAKY_L5"
RC_BOTH_LOW_EVENT_L5          = "BOTH_LOW_EVENT_L5"
RC_BOTH_HIGH_CORNERS_L5       = "BOTH_HIGH_CORNERS_L5"
RC_BOTH_BTTS_PROFILE_L5       = "BOTH_BTTS_PROFILE_L5"
RC_UNILATERAL_DOMINANCE       = "UNILATERAL_DOMINANCE_L5"

RC_STRONG_UNDER_CROSS         = "STRONG_UNDER_CROSS"
RC_LOW_EVENT_UNDER_CROSS      = "LOW_EVENT_UNDER_CROSS"
RC_STRONG_OVER_CROSS          = "STRONG_OVER_CROSS"
RC_BILATERAL_BTTS_CROSS       = "BILATERAL_BTTS_CROSS"
RC_UNILATERAL_DOMINANCE_CROSS = "UNILATERAL_DOMINANCE_CROSS"
RC_CORNERS_OVER_CROSS         = "CORNERS_OVER_CROSS"
RC_MIXED_PROFILE              = "MIXED_PROFILE_NO_CLEAR_EDGE"

# ── Profile keys ─────────────────────────────────────────────────────
PROFILE_STRONG_UNDER    = "STRONG_UNDER_CROSS"
PROFILE_LOW_EVENT_UNDER = "LOW_EVENT_UNDER_CROSS"
PROFILE_STRONG_OVER     = "STRONG_OVER_CROSS"
PROFILE_BTTS            = "BILATERAL_BTTS_CROSS"
PROFILE_DOMINANCE       = "UNILATERAL_DOMINANCE_CROSS"
PROFILE_CORNERS_OVER    = "CORNERS_OVER_CROSS"
PROFILE_MIXED           = "MIXED_PROFILE"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 3)


def _round(v: Optional[float], ndigits: int = 2) -> Optional[float]:
    return None if v is None else round(v, ndigits)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────
# Per-team classifier
# ─────────────────────────────────────────────────────────────────────
def classify_team_football_profile(
    *,
    goals_for_l5:       Optional[float],
    goals_for_l15:      Optional[float],
    goals_against_l5:   Optional[float],
    goals_against_l15:  Optional[float],
    xg_l5:              Optional[float] = None,
    xg_l15:             Optional[float] = None,
    xga_l5:             Optional[float] = None,
    xga_l15:            Optional[float] = None,
    shots_l5:           Optional[float] = None,
    shots_l15:          Optional[float] = None,
    sot_l5:             Optional[float] = None,
    sot_l15:            Optional[float] = None,
    corners_l5:         Optional[float] = None,
    corners_l15:        Optional[float] = None,
) -> dict:
    """Clasifica el perfil L5 vs L15 de un solo equipo.

    Devuelve banderas + reason_codes per-team. ``goals_for`` y
    ``goals_against`` son los únicos inputs **obligatorios** para
    considerar al equipo evaluable; el resto es enriquecimiento opcional
    que afina las banderas low_event/corners/etc.
    """
    gf5  = _safe(goals_for_l5)
    gf15 = _safe(goals_for_l15)
    ga5  = _safe(goals_against_l5)
    ga15 = _safe(goals_against_l15)
    xg5  = _safe(xg_l5)
    xg15 = _safe(xg_l15)
    xga5 = _safe(xga_l5)
    xga15 = _safe(xga_l15)
    sh5  = _safe(shots_l5)
    sh15 = _safe(shots_l15)
    sot5 = _safe(sot_l5)
    sot15 = _safe(sot_l15)
    co5  = _safe(corners_l5)
    co15 = _safe(corners_l15)

    gf_delta  = _delta(gf5, gf15)
    ga_delta  = _delta(ga5, ga15)
    xg_delta  = _delta(xg5, xg15)
    xga_delta = _delta(xga5, xga15)
    sh_delta  = _delta(sh5, sh15)
    sot_delta = _delta(sot5, sot15)
    co_delta  = _delta(co5, co15)

    reason_codes: list[str] = []
    is_offense_cold   = False
    is_offense_hot    = False
    is_defense_tight  = False
    is_defense_leaky  = False
    is_low_event      = False
    is_high_event     = False
    is_corners_up     = False

    # Offense cooling — gf_l5 ≤ COLD AND gf_delta ≤ −SHIFT
    if (gf5 is not None and gf5 <= TEAM_COLD_GF_L5
            and gf_delta is not None and gf_delta <= -DELTA_GOAL_SHIFT):
        reason_codes.append(RC_TEAM_OFFENSE_COOLING)
        is_offense_cold = True
    # xG cross-confirm (sin shift mínimo, soft signal)
    if (xg5 is not None and xg5 <= TEAM_COLD_XG_L5
            and (xg_delta is None or xg_delta <= 0)):
        is_offense_cold = True

    # Offense heating
    if (gf5 is not None and gf5 >= TEAM_HOT_GF_L5
            and gf_delta is not None and gf_delta >= DELTA_GOAL_SHIFT):
        reason_codes.append(RC_TEAM_OFFENSE_HEATING)
        is_offense_hot = True
    if (xg5 is not None and xg5 >= TEAM_HOT_XG_L5
            and (xg_delta is None or xg_delta >= 0)):
        is_offense_hot = True

    # Defense tightening
    if (ga5 is not None and ga5 <= TEAM_TIGHT_GA_L5
            and ga_delta is not None and ga_delta <= -DELTA_GOAL_SHIFT):
        reason_codes.append(RC_TEAM_DEFENSE_TIGHTENING)
        is_defense_tight = True
    if (xga5 is not None and xga5 <= TEAM_TIGHT_XGA_L5
            and (xga_delta is None or xga_delta <= 0)):
        is_defense_tight = True

    # Defense leaking
    if (ga5 is not None and ga5 >= TEAM_LEAKY_GA_L5
            and ga_delta is not None and ga_delta >= DELTA_GOAL_SHIFT):
        reason_codes.append(RC_TEAM_DEFENSE_LEAKING)
        is_defense_leaky = True
    if (xga5 is not None and xga5 >= TEAM_LEAKY_XGA_L5
            and (xga_delta is None or xga_delta >= 0)):
        is_defense_leaky = True

    # Event volume — shots + SOT
    low_shots = (sh5 is not None and sh5 <= TEAM_LOW_SHOTS_L5)
    low_sot   = (sot5 is not None and sot5 <= TEAM_LOW_SOT_L5)
    if low_shots and low_sot:
        reason_codes.append(RC_TEAM_LOW_EVENT_VOLUME)
        is_low_event = True
    elif (sh5 is not None and sh5 >= TEAM_HIGH_SHOTS_L5) \
            and (sot5 is not None and sot5 >= TEAM_HIGH_SOT_L5):
        reason_codes.append(RC_TEAM_HIGH_EVENT_VOLUME)
        is_high_event = True

    # Corners trending up
    if (co5 is not None and co5 >= TEAM_HIGH_CORNERS_L5
            and co_delta is not None and co_delta >= DELTA_CORNERS_SHIFT):
        reason_codes.append(RC_TEAM_CORNERS_TRENDING_UP)
        is_corners_up = True

    return {
        "goals_for_l5":      gf5,
        "goals_for_l15":     gf15,
        "goals_for_delta":   gf_delta,
        "goals_against_l5":  ga5,
        "goals_against_l15": ga15,
        "goals_against_delta": ga_delta,
        "xg_l5":             xg5,
        "xg_l15":            xg15,
        "xg_delta":          xg_delta,
        "xga_l5":            xga5,
        "xga_l15":           xga15,
        "xga_delta":         xga_delta,
        "shots_l5":          sh5,
        "shots_delta":       sh_delta,
        "sot_l5":            sot5,
        "sot_delta":         sot_delta,
        "corners_l5":        co5,
        "corners_delta":     co_delta,
        "reason_codes":      reason_codes,
        "is_offense_cold":   is_offense_cold,
        "is_offense_hot":    is_offense_hot,
        "is_defense_tight":  is_defense_tight,
        "is_defense_leaky":  is_defense_leaky,
        "is_low_event":      is_low_event,
        "is_high_event":     is_high_event,
        "is_corners_up":     is_corners_up,
    }


# ─────────────────────────────────────────────────────────────────────
# Combined cross
# ─────────────────────────────────────────────────────────────────────
def _narrative_es(profile: Optional[str]) -> Optional[str]:
    if profile == PROFILE_STRONG_UNDER:
        return ("Ambos equipos vienen con ofensivas frías y defensas firmes "
                "en sus L5. El cruce apoya un escenario de baja anotación.")
    if profile == PROFILE_LOW_EVENT_UNDER:
        return ("Ambos equipos generan poco volumen de eventos (tiros y SOT) "
                "en sus L5. El cruce inclina a partido escaso en oportunidades.")
    if profile == PROFILE_STRONG_OVER:
        return ("Ambos equipos llegan calientes ofensivamente y con defensas "
                "permeables en L5. El cruce eleva el riesgo de partido abierto.")
    if profile == PROFILE_BTTS:
        return ("Ambos equipos marcan y conceden con regularidad en sus L5. "
                "Cruce favorable a BTTS.")
    if profile == PROFILE_DOMINANCE:
        return ("Un lado domina claramente xG y limita xGA en L5. El cruce "
                "sugiere asimetría unilateral más que volumen total.")
    if profile == PROFILE_CORNERS_OVER:
        return ("Ambos equipos vienen acumulando volumen de corners por encima "
                "de su base L15. Cruce favorable a Over de corners.")
    if profile == PROFILE_MIXED:
        return ("El cruce reciente no entrega una señal limpia; los equipos "
                "muestran perfiles divergentes entre L5 y L15.")
    return None


def compute_combined_football_profile_cross(
    *,
    home: dict,
    away: dict,
) -> dict:
    """Cross-clasifica ambos equipos (acepta dicts con keys L5/L15).

    Cada uno de ``home``/``away`` debe ser un dict con keys
    ``goals_for_l5``, ``goals_for_l15``, ``goals_against_l5``,
    ``goals_against_l15`` (obligatorios) y opcionalmente ``xg_l5``,
    ``xg_l15``, ``xga_l5``, ``xga_l15``, ``shots_l5``, ``shots_l15``,
    ``sot_l5``, ``sot_l15``, ``corners_l5``, ``corners_l15``.

    Devuelve payload de cross seguro para adjuntar al pick_payload bajo
    ``combined_football_profile_cross``. ``available=False`` si faltan
    inputs core (goals_for/goals_against L5 de ambos lados).
    """
    if not isinstance(home, dict):
        home = {}
    if not isinstance(away, dict):
        away = {}

    h = classify_team_football_profile(
        goals_for_l5=home.get("goals_for_l5"),
        goals_for_l15=home.get("goals_for_l15"),
        goals_against_l5=home.get("goals_against_l5"),
        goals_against_l15=home.get("goals_against_l15"),
        xg_l5=home.get("xg_l5"), xg_l15=home.get("xg_l15"),
        xga_l5=home.get("xga_l5"), xga_l15=home.get("xga_l15"),
        shots_l5=home.get("shots_l5"), shots_l15=home.get("shots_l15"),
        sot_l5=home.get("sot_l5"), sot_l15=home.get("sot_l15"),
        corners_l5=home.get("corners_l5"), corners_l15=home.get("corners_l15"),
    )
    a = classify_team_football_profile(
        goals_for_l5=away.get("goals_for_l5"),
        goals_for_l15=away.get("goals_for_l15"),
        goals_against_l5=away.get("goals_against_l5"),
        goals_against_l15=away.get("goals_against_l15"),
        xg_l5=away.get("xg_l5"), xg_l15=away.get("xg_l15"),
        xga_l5=away.get("xga_l5"), xga_l15=away.get("xga_l15"),
        shots_l5=away.get("shots_l5"), shots_l15=away.get("shots_l15"),
        sot_l5=away.get("sot_l5"), sot_l15=away.get("sot_l15"),
        corners_l5=away.get("corners_l5"), corners_l15=away.get("corners_l15"),
    )

    # Fail-soft: necesitamos goals_for_l5 + goals_against_l5 de AMBOS
    required = (
        h["goals_for_l5"], h["goals_against_l5"],
        a["goals_for_l5"], a["goals_against_l5"],
    )
    if any(v is None for v in required):
        return {
            "available":         False,
            "engine_version":    ENGINE_VERSION,
            "profile":           None,
            "supports":          "NEUTRAL",
            "confidence_delta":  0,
            "fragility_delta":   0,
            "reason_codes":      [],
            "per_team":          {"home": h, "away": a},
            "narrative_es":      None,
            "_skipped_reason":   "missing_l5_core_inputs",
        }

    # Pair flags
    both_offenses_cold  = h["is_offense_cold"]  and a["is_offense_cold"]
    both_offenses_hot   = h["is_offense_hot"]   and a["is_offense_hot"]
    both_defenses_tight = h["is_defense_tight"] and a["is_defense_tight"]
    both_defenses_leaky = h["is_defense_leaky"] and a["is_defense_leaky"]
    both_low_event      = h["is_low_event"]     and a["is_low_event"]
    both_corners_up     = h["is_corners_up"]    and a["is_corners_up"]

    # BTTS detection (gf y ga decentes en ambos)
    both_btts_profile = (
        h["goals_for_l5"]    >= BTTS_GF_FLOOR and
        a["goals_for_l5"]    >= BTTS_GF_FLOOR and
        h["goals_against_l5"] >= BTTS_GA_FLOOR and
        a["goals_against_l5"] >= BTTS_GA_FLOOR
    )

    # Unilateral dominance: un equipo claramente superior en L5
    def _net(side: dict) -> Optional[float]:
        if side["xg_l5"] is not None and side["xga_l5"] is not None:
            return side["xg_l5"] - side["xga_l5"]
        if side["goals_for_l5"] is not None and side["goals_against_l5"] is not None:
            return side["goals_for_l5"] - side["goals_against_l5"]
        return None

    net_h = _net(h)
    net_a = _net(a)
    dominance_diff = None
    if net_h is not None and net_a is not None:
        dominance_diff = abs(net_h - net_a)
    is_dominance = (
        dominance_diff is not None
        and dominance_diff >= DOMINANCE_GF_DIFF
        and not both_offenses_hot
        and not both_offenses_cold
    )
    # Endurece dominance si tenemos xG
    if (h["xg_l5"] is not None and a["xg_l5"] is not None
            and abs(h["xg_l5"] - a["xg_l5"]) >= DOMINANCE_XG_DIFF):
        is_dominance = True and not (both_offenses_hot and both_defenses_leaky)

    # ── Decisión de perfil (orden importa: más restrictivos primero) ──
    profile: Optional[str] = None
    supports = "NEUTRAL"
    confidence_delta = 0
    fragility_delta = 0
    reason_codes: list[str] = []

    if both_offenses_hot and both_defenses_leaky:
        profile = PROFILE_STRONG_OVER
        supports = "OVER"
        confidence_delta = 12
        fragility_delta = 8
        reason_codes = [
            RC_BOTH_OFFENSES_HIGH_L5,
            RC_BOTH_DEFENSES_LEAKY_L5,
            RC_STRONG_OVER_CROSS,
        ]
    elif both_offenses_cold and both_defenses_tight:
        profile = PROFILE_STRONG_UNDER
        supports = "UNDER"
        confidence_delta = 11
        fragility_delta = 7
        reason_codes = [
            RC_BOTH_OFFENSES_LOW_L5,
            RC_BOTH_DEFENSES_TIGHT_L5,
            RC_STRONG_UNDER_CROSS,
        ]
    elif both_corners_up:
        profile = PROFILE_CORNERS_OVER
        supports = "CORNERS"
        confidence_delta = 10
        fragility_delta = 6
        reason_codes = [
            RC_BOTH_HIGH_CORNERS_L5,
            RC_CORNERS_OVER_CROSS,
        ]
    elif both_low_event and not both_offenses_hot:
        profile = PROFILE_LOW_EVENT_UNDER
        supports = "UNDER"
        confidence_delta = 7
        fragility_delta = 5
        reason_codes = [
            RC_BOTH_LOW_EVENT_L5,
            RC_LOW_EVENT_UNDER_CROSS,
        ]
    elif both_btts_profile and not both_defenses_tight:
        profile = PROFILE_BTTS
        supports = "BTTS"
        confidence_delta = 7
        fragility_delta = 5
        reason_codes = [
            RC_BOTH_BTTS_PROFILE_L5,
            RC_BILATERAL_BTTS_CROSS,
        ]
    elif is_dominance:
        profile = PROFILE_DOMINANCE
        supports = "NEUTRAL"  # dominance unilateral no apoya OVER/UNDER total
        confidence_delta = 0
        fragility_delta = 3
        reason_codes = [
            RC_UNILATERAL_DOMINANCE,
            RC_UNILATERAL_DOMINANCE_CROSS,
        ]
    else:
        # Mixed sentinels: señales divergentes
        mixed_signal = (
            (h["is_offense_cold"] ^ a["is_offense_cold"])
            or (h["is_offense_hot"] ^ a["is_offense_hot"])
            or (h["is_defense_tight"] ^ a["is_defense_tight"])
        )
        if mixed_signal:
            profile = PROFILE_MIXED
            supports = "NEUTRAL"
            confidence_delta = 0
            fragility_delta = 2
            reason_codes = [RC_MIXED_PROFILE]
        else:
            profile = None
            supports = "NEUTRAL"
            confidence_delta = 0
            fragility_delta = 0
            reason_codes = []

    # Forward per-team RCs at top level (no duplicates)
    combined_rcs = list(reason_codes)
    for rc in h["reason_codes"] + a["reason_codes"]:
        if rc not in combined_rcs:
            combined_rcs.append(rc)

    return {
        "available":         True,
        "engine_version":    ENGINE_VERSION,
        "profile":           profile,
        "supports":          supports,
        "confidence_delta":  int(confidence_delta),
        "fragility_delta":   int(fragility_delta),
        "reason_codes":      combined_rcs,
        "per_team":          {"home": h, "away": a},
        "narrative_es":      _narrative_es(profile),
        "dominance_diff":    _round(dominance_diff, 2),
    }


# ─────────────────────────────────────────────────────────────────────
# apply_profile_cross_to_pick  (con OVERRIDE)
# ─────────────────────────────────────────────────────────────────────
# Mapeo de profile → mercado/lado sugerido para override
_OVERRIDE_TARGETS = {
    PROFILE_STRONG_OVER:    {"market": "OVER_2_5",   "side": "OVER"},
    PROFILE_STRONG_UNDER:   {"market": "UNDER_2_5",  "side": "UNDER"},
    PROFILE_CORNERS_OVER:   {"market": "CORNERS_OVER_9_5", "side": "OVER"},
}


def apply_profile_cross_to_pick(
    *,
    cross_payload:        dict,
    pick_side:            Optional[str],     # "OVER" | "UNDER" | "BTTS" | "CORNERS" | "MONEYLINE_HOME" | etc.
    pick_market:          Optional[str] = None,
    current_confidence:   Optional[float] = None,
    current_fragility:    Optional[float] = None,
    allow_override:       bool = True,
) -> dict:
    """Aplica deltas simétricos y, si procede, propone un **override**.

    Reglas:
      * Si ``cross.supports`` coincide con la **dirección** del pick →
        bonus a confidence (capped a MAX_CONFIDENCE_BONUS) y −fragility.
      * Si la contradice (y supports != NEUTRAL) → penalty
        (capped a MAX_CONFIDENCE_PENALTY) y +fragility.
      * NEUTRAL / unavailable → no-op.
      * Override:
        - Solo si ``allow_override=True``.
        - Solo si ``profile in STRONG_OVERRIDE_PROFILES``.
        - Solo si ``confidence_delta >= STRONG_OVERRIDE_THRESHOLD``.
        - Solo si el pick actual NO coincide ya con ``supports`` del cross
          (no tendría sentido "override" para ratificar lo que ya hay).
        - El override propone un nuevo ``market``/``side`` (el caller
          decide si lo aplica).

    Returns
    -------
    dict con ``applied``, ``interaction``, ``new_confidence``,
    ``new_fragility``, ``confidence_delta_signed``, ``fragility_delta_signed``,
    ``reason_codes``, y ``override`` (None o dict con ``enabled``,
    ``recommended_market``, ``recommended_side``, ``reason``).
    """
    base_conf = _safe(current_confidence)
    base_frag = _safe(current_fragility)

    side_norm = (pick_side or "").upper().strip()
    # Mapeo de variantes coloquiales → categoría
    if side_norm.startswith("OVER"):
        side_cat = "OVER"
    elif side_norm.startswith("UNDER"):
        side_cat = "UNDER"
    elif side_norm in ("BTTS", "BTTS_YES", "BOTH_TEAMS_TO_SCORE"):
        side_cat = "BTTS"
    elif "CORNER" in side_norm:
        side_cat = "CORNERS"
    else:
        side_cat = side_norm  # ML, DRAW, etc.

    if (not isinstance(cross_payload, dict)
            or not cross_payload.get("available")
            or cross_payload.get("supports") == "NEUTRAL"
            or base_conf is None):
        return {
            "applied":                 False,
            "new_confidence":          base_conf,
            "new_fragility":           base_frag,
            "confidence_delta_signed": 0,
            "fragility_delta_signed":  0,
            "interaction":             "SKIPPED",
            "reason_codes":            [],
            "override":                None,
        }

    raw_conf_delta = int(cross_payload.get("confidence_delta") or 0)
    raw_frag_delta = int(cross_payload.get("fragility_delta") or 0)
    supports = (cross_payload.get("supports") or "").upper()
    profile  = cross_payload.get("profile")

    # Decidir interacción
    if supports == side_cat and side_cat:
        interaction = "SUPPORTS_PICK"
        conf_signed = int(min(raw_conf_delta, MAX_CONFIDENCE_BONUS))
        frag_signed = -abs(raw_frag_delta)
        rcs = [f"FOOTBALL_PROFILE_CROSS_SUPPORTS_{side_cat}"]
        if profile:
            rcs.append(f"PROFILE_{profile}")
    else:
        # Contradice (o el pick es de otra dimensión, e.g. ML vs OVER cross)
        interaction = "CONTRADICTS_PICK"
        conf_signed = -int(min(raw_conf_delta, MAX_CONFIDENCE_PENALTY))
        frag_signed = abs(raw_frag_delta)
        rcs = [f"FOOTBALL_PROFILE_CROSS_CONTRADICTS_{side_cat or 'PICK'}"]
        if profile:
            rcs.append(f"PROFILE_{profile}")

    new_conf = _clamp(base_conf + conf_signed, 0.0, 100.0)
    new_frag = (_clamp(base_frag + frag_signed, 0.0, 100.0)
                if base_frag is not None else None)

    # ── Override evaluation ──────────────────────────────────────────
    override = None
    if (allow_override
            and profile in STRONG_OVERRIDE_PROFILES
            and raw_conf_delta >= STRONG_OVERRIDE_THRESHOLD
            and interaction == "CONTRADICTS_PICK"):
        target = _OVERRIDE_TARGETS.get(profile)
        if target:
            override = {
                "enabled":             True,
                "profile":             profile,
                "recommended_market":  target["market"],
                "recommended_side":    target["side"],
                "previous_market":     pick_market,
                "previous_side":       pick_side,
                "reason":              f"STRONG_PROFILE_OVERRIDE_{profile}",
            }
            rcs.append(f"FOOTBALL_PROFILE_CROSS_OVERRIDE_{profile}")

    return {
        "applied":                 True,
        "new_confidence":          round(new_conf, 2),
        "new_fragility":           None if new_frag is None else round(new_frag, 2),
        "confidence_delta_signed": conf_signed,
        "fragility_delta_signed":  frag_signed,
        "interaction":             interaction,
        "reason_codes":            rcs,
        "override":                override,
    }


# ─────────────────────────────────────────────────────────────────────
# Visual entry (no count toward contradiction penalty)
# ─────────────────────────────────────────────────────────────────────
def build_pattern_alignment_entry(
    cross_payload: dict,
    pick_side: Optional[str],
) -> Optional[dict]:
    """Construye un entry visual-only para
    ``pick_payload["pattern_alignment"]["entries"]``.

    Retorna ``None`` si no hay perfil util (unavailable / MIXED sin info /
    DOMINANCE-only). El flag ``visual_only=True`` es **obligatorio** para
    que el contrablock CAMBIO 4 (contradiction penalty) NO doble-cuente.
    """
    if not isinstance(cross_payload, dict) or not cross_payload.get("available"):
        return None
    supports = (cross_payload.get("supports") or "").upper()
    profile  = cross_payload.get("profile")
    if not profile or supports == "NEUTRAL":
        return None

    side_norm = (pick_side or "").upper()
    side_cat = "OVER" if side_norm.startswith("OVER") else (
               "UNDER" if side_norm.startswith("UNDER") else (
               "BTTS" if "BTTS" in side_norm else (
               "CORNERS" if "CORNER" in side_norm else side_norm)))
    supports_pick = bool(side_cat and supports == side_cat)

    return {
        "pattern":       profile,
        "side":          supports,
        "supports_pick": supports_pick,
        "message":       cross_payload.get("narrative_es"),
        "source":        "football_team_profile_cross",
        "visual_only":   True,
    }


__all__ = [
    "ENGINE_VERSION",
    "MAX_CONFIDENCE_BONUS",
    "MAX_CONFIDENCE_PENALTY",
    "STRONG_OVERRIDE_PROFILES",
    "STRONG_OVERRIDE_THRESHOLD",
    "PROFILE_STRONG_UNDER",
    "PROFILE_LOW_EVENT_UNDER",
    "PROFILE_STRONG_OVER",
    "PROFILE_BTTS",
    "PROFILE_DOMINANCE",
    "PROFILE_CORNERS_OVER",
    "PROFILE_MIXED",
    "classify_team_football_profile",
    "compute_combined_football_profile_cross",
    "apply_profile_cross_to_pick",
    "build_pattern_alignment_entry",
]
