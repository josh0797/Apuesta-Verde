"""MLB Run Profile Cross — L5 vs L15 offensive & run-prevention crossover.

Detects how the **last 5 games** runs-scored / runs-allowed profile of
both teams relates to the **last 15 games** baseline, and classifies
the matchup into one of five combined profiles:

    STRONG_UNDER_CROSS    → both offenses cold & both defenses tight
    LOW_SCORING_CROSS     → both offenses cold + at least one tight defense
    STRONG_OVER_CROSS     → both offenses hot & both defenses leaky
    HIGH_SCORING_CROSS    → both offenses hot OR both defenses leaky
    MIXED_PROFILE         → no clear edge

Design contract (mirrors CAMBIO 3/4 of Phase 58)
------------------------------------------------
* OBSERVE-ONLY at the polarity layer: NEVER overrides the NB
  distribution, NEVER flips Over/Under polarity by itself.
* Operates as a **contextual layer** *after* the pattern-contradiction
  penalty (CAMBIO 4): produces ``confidence_delta`` and
  ``fragility_delta`` that the orchestrator applies symmetrically based
  on whether the cross supports or contradicts the engine's pick side.
* Bonus/penalty clamps:
      max_bonus    = +8
      max_penalty  = -12
      fragility    ∈ [0, 100]
      confidence   ∈ [0, 100]
* Fail-soft: if any input is missing, returns ``available=False`` with
  empty deltas — the orchestrator must short-circuit gracefully.
* Appears as a **visual entry** on ``pick_payload["pattern_alignment"].
  entries`` for transparency, but DOES NOT count toward
  ``supporting_count`` / ``contradicting_count`` (those drive CAMBIO 4
  and must remain independent to prevent double accounting).
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "mlb_run_profile_cross.1"

# ── Hard caps for downstream consumption (orchestrator clamps with these). ──
MAX_CONFIDENCE_BONUS   = 8
MAX_CONFIDENCE_PENALTY = 12

# ── Per-team thresholds (from spec) ─────────────────────────────────
TEAM_COLD_RUNS_L5   = 3.8     # ≤ : offense cooling
TEAM_HOT_RUNS_L5    = 5.0     # ≥ : offense heating
TEAM_DELTA_SHIFT    = 0.5     # |scored_delta| or |allowed_delta| significant
TEAM_LOW_ALLOWED_L5 = 3.8     # ≤ : run prevention improving
TEAM_HIGH_ALLOWED_L5 = 5.0    # ≥ : run prevention weakening

# ── Combined thresholds (from spec) ─────────────────────────────────
PAIR_COLD_RUNS_L5    = 4.0    # both teams scoring ≤ 4.0 L5
PAIR_TIGHT_DEF_L5    = 4.0    # both teams allowing ≤ 4.0 L5
PAIR_HOT_RUNS_L5     = 5.0    # both teams scoring ≥ 5.0 L5
PAIR_LEAKY_DEF_L5    = 5.0    # both teams allowing ≥ 5.0 L5

# ── Per-team reason codes ───────────────────────────────────────────
RC_TEAM_OFFENSE_COOLING            = "TEAM_OFFENSE_COOLING"
RC_TEAM_OFFENSE_HEATING            = "TEAM_OFFENSE_HEATING"
RC_TEAM_RUN_PREVENTION_IMPROVING   = "TEAM_RUN_PREVENTION_IMPROVING"
RC_TEAM_RUN_PREVENTION_WEAKENING   = "TEAM_RUN_PREVENTION_WEAKENING"

# ── Combined reason codes ───────────────────────────────────────────
RC_BOTH_OFFENSES_LOW_L5            = "BOTH_OFFENSES_LOW_L5"
RC_BOTH_OFFENSES_HIGH_L5           = "BOTH_OFFENSES_HIGH_L5"
RC_BOTH_TEAMS_ALLOW_LOW_L5         = "BOTH_TEAMS_ALLOW_LOW_L5"
RC_BOTH_TEAMS_ALLOW_HIGH_L5        = "BOTH_TEAMS_ALLOW_HIGH_L5"
RC_LOW_RUN_ALLOWANCE_SUPPORTS_UNDER = "LOW_RUN_ALLOWANCE_SUPPORTS_UNDER"
RC_LOW_SCORING_CROSS_SUPPORTS_UNDER = "LOW_SCORING_CROSS_SUPPORTS_UNDER"
RC_HIGH_SCORING_CROSS_SUPPORTS_OVER = "HIGH_SCORING_CROSS_SUPPORTS_OVER"
RC_STRONG_UNDER_CROSS              = "STRONG_UNDER_CROSS"
RC_STRONG_OVER_CROSS               = "STRONG_OVER_CROSS"
RC_MIXED_RUN_PROFILE_NO_CLEAR_EDGE = "MIXED_RUN_PROFILE_NO_CLEAR_EDGE"

# ── Profile keys (also used as `profile` field in the payload) ──────
PROFILE_STRONG_UNDER  = "STRONG_UNDER_CROSS"
PROFILE_LOW_SCORING   = "LOW_SCORING_CROSS"
PROFILE_STRONG_OVER   = "STRONG_OVER_CROSS"
PROFILE_HIGH_SCORING  = "HIGH_SCORING_CROSS"
PROFILE_MIXED         = "MIXED_PROFILE"


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


def _round(v: Optional[float], ndigits: int = 2) -> Optional[float]:
    return None if v is None else round(v, ndigits)


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 3)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────
# Per-team interpretation
# ─────────────────────────────────────────────────────────────────────
def classify_team_run_profile(
    *,
    scored_l5:  Optional[float],
    scored_l15: Optional[float],
    allowed_l5:  Optional[float],
    allowed_l15: Optional[float],
) -> dict:
    """Classify a single team's L5 vs L15 run profile.

    Returns
    -------
    {
        "scored_delta":  float | None,
        "allowed_delta": float | None,
        "reason_codes":  [...],   # per-team RCs only
        "is_offense_cold":    bool,
        "is_offense_hot":     bool,
        "is_prevention_up":   bool,   # allowing fewer = "improving"
        "is_prevention_down": bool,   # allowing more  = "weakening"
    }
    """
    s5  = _safe(scored_l5)
    s15 = _safe(scored_l15)
    a5  = _safe(allowed_l5)
    a15 = _safe(allowed_l15)

    scored_delta  = _delta(s5, s15)
    allowed_delta = _delta(a5, a15)

    reason_codes: list[str] = []
    is_offense_cold = False
    is_offense_hot = False
    is_prevention_up = False
    is_prevention_down = False

    # 1. Offense cooling — runs_scored_l5 <= 3.8 AND scored_delta <= -0.5
    if (s5 is not None and s5 <= TEAM_COLD_RUNS_L5
            and scored_delta is not None and scored_delta <= -TEAM_DELTA_SHIFT):
        reason_codes.append(RC_TEAM_OFFENSE_COOLING)
        is_offense_cold = True

    # 2. Offense heating — runs_scored_l5 >= 5.0 AND scored_delta >= +0.5
    if (s5 is not None and s5 >= TEAM_HOT_RUNS_L5
            and scored_delta is not None and scored_delta >= TEAM_DELTA_SHIFT):
        reason_codes.append(RC_TEAM_OFFENSE_HEATING)
        is_offense_hot = True

    # 3. Run prevention improving — runs_allowed_l5 <= 3.8 AND allowed_delta <= -0.5
    if (a5 is not None and a5 <= TEAM_LOW_ALLOWED_L5
            and allowed_delta is not None and allowed_delta <= -TEAM_DELTA_SHIFT):
        reason_codes.append(RC_TEAM_RUN_PREVENTION_IMPROVING)
        is_prevention_up = True

    # 4. Run prevention weakening — runs_allowed_l5 >= 5.0 AND allowed_delta >= +0.5
    if (a5 is not None and a5 >= TEAM_HIGH_ALLOWED_L5
            and allowed_delta is not None and allowed_delta >= TEAM_DELTA_SHIFT):
        reason_codes.append(RC_TEAM_RUN_PREVENTION_WEAKENING)
        is_prevention_down = True

    return {
        "scored_l5":           s5,
        "scored_l15":          s15,
        "allowed_l5":          a5,
        "allowed_l15":         a15,
        "scored_delta":        scored_delta,
        "allowed_delta":       allowed_delta,
        "reason_codes":        reason_codes,
        "is_offense_cold":     is_offense_cold,
        "is_offense_hot":      is_offense_hot,
        "is_prevention_up":    is_prevention_up,
        "is_prevention_down":  is_prevention_down,
    }


# ─────────────────────────────────────────────────────────────────────
# Combined cross
# ─────────────────────────────────────────────────────────────────────
def _narrative_es(profile: str) -> Optional[str]:
    if profile == PROFILE_STRONG_UNDER:
        return ("Ambos equipos vienen generando pocas carreras y también "
                "permiten pocas carreras. Este cruce apoya un entorno de "
                "baja anotación.")
    if profile == PROFILE_LOW_SCORING:
        return ("Ambos equipos generan poco y al menos uno permite poco "
                "recientemente. El cruce inclina a baja anotación.")
    if profile == PROFILE_STRONG_OVER:
        return ("Ambos equipos vienen anotando mucho y permitiendo mucho. "
                "Este cruce eleva el riesgo de partido abierto.")
    if profile == PROFILE_HIGH_SCORING:
        return ("Ambos equipos generan mucho o ambos permiten mucho "
                "recientemente. El cruce eleva la expectativa de carreras.")
    if profile == PROFILE_MIXED:
        return ("El cruce reciente no da una señal limpia; un equipo "
                "apunta a baja anotación y el otro a mayor volatilidad.")
    return None


def compute_combined_run_profile_cross(
    *,
    home_scored_l5:   Optional[float],
    home_scored_l15:  Optional[float],
    home_allowed_l5:  Optional[float],
    home_allowed_l15: Optional[float],
    away_scored_l5:   Optional[float],
    away_scored_l15:  Optional[float],
    away_allowed_l5:  Optional[float],
    away_allowed_l15: Optional[float],
) -> dict:
    """Cross-classify both teams' L5 vs L15 profile.

    Returns a payload safe to attach as
    ``pick_payload["combined_run_profile_cross"]``. Fail-soft when
    inputs are missing — ``available=False`` and zero deltas.
    """
    home = classify_team_run_profile(
        scored_l5=home_scored_l5, scored_l15=home_scored_l15,
        allowed_l5=home_allowed_l5, allowed_l15=home_allowed_l15,
    )
    away = classify_team_run_profile(
        scored_l5=away_scored_l5, scored_l15=away_scored_l15,
        allowed_l5=away_allowed_l5, allowed_l15=away_allowed_l15,
    )

    # Fail-soft if any of the 4 critical L5 inputs missing for either team.
    required = (
        home["scored_l5"], home["allowed_l5"],
        away["scored_l5"], away["allowed_l5"],
    )
    if any(v is None for v in required):
        return {
            "available":           False,
            "engine_version":      ENGINE_VERSION,
            "profile":             None,
            "supports":            "NEUTRAL",
            "confidence_delta":    0,
            "fragility_delta":     0,
            "reason_codes":        [],
            "per_team":            {"home": home, "away": away},
            "narrative_es":        None,
            "_skipped_reason":     "missing_l5_inputs",
        }

    h_s5 = home["scored_l5"]
    h_a5 = home["allowed_l5"]
    a_s5 = away["scored_l5"]
    a_a5 = away["allowed_l5"]

    both_offenses_low   = (h_s5 <= PAIR_COLD_RUNS_L5 and a_s5 <= PAIR_COLD_RUNS_L5)
    both_offenses_high  = (h_s5 >= PAIR_HOT_RUNS_L5  and a_s5 >= PAIR_HOT_RUNS_L5)
    both_teams_allow_low  = (h_a5 <= PAIR_TIGHT_DEF_L5 and a_a5 <= PAIR_TIGHT_DEF_L5)
    both_teams_allow_high = (h_a5 >= PAIR_LEAKY_DEF_L5 and a_a5 >= PAIR_LEAKY_DEF_L5)
    at_least_one_allow_low  = (h_a5 <= PAIR_TIGHT_DEF_L5 or  a_a5 <= PAIR_TIGHT_DEF_L5)

    # Mixed sentinels — used to detect MIXED_PROFILE.
    one_team_low_offense  = ((h_s5 <= PAIR_COLD_RUNS_L5) ^ (a_s5 <= PAIR_COLD_RUNS_L5))
    one_team_high_offense = ((h_s5 >= PAIR_HOT_RUNS_L5)  ^ (a_s5 >= PAIR_HOT_RUNS_L5))

    profile = None
    supports = "NEUTRAL"
    confidence_delta = 0
    fragility_delta = 0
    reason_codes: list[str] = []

    # ── D. STRONG_OVER_CROSS — checked FIRST (most restrictive Over). ──
    if both_offenses_high and both_teams_allow_high:
        profile = PROFILE_STRONG_OVER
        supports = "OVER"
        confidence_delta = 12
        fragility_delta = 8
        reason_codes = [
            RC_BOTH_OFFENSES_HIGH_L5,
            RC_BOTH_TEAMS_ALLOW_HIGH_L5,
            RC_STRONG_OVER_CROSS,
        ]
    # ── B. STRONG_UNDER_CROSS — most restrictive Under. ────────────────
    elif both_offenses_low and both_teams_allow_low:
        profile = PROFILE_STRONG_UNDER
        supports = "UNDER"
        confidence_delta = 10
        fragility_delta = 6
        reason_codes = [
            RC_BOTH_OFFENSES_LOW_L5,
            RC_BOTH_TEAMS_ALLOW_LOW_L5,
            RC_STRONG_UNDER_CROSS,
        ]
    # ── C. HIGH_SCORING_CROSS — softer Over signal. ────────────────────
    elif both_offenses_high or both_teams_allow_high:
        profile = PROFILE_HIGH_SCORING
        supports = "OVER"
        confidence_delta = 8
        fragility_delta = 6
        reason_codes = [RC_HIGH_SCORING_CROSS_SUPPORTS_OVER]
        if both_offenses_high:
            reason_codes.insert(0, RC_BOTH_OFFENSES_HIGH_L5)
        if both_teams_allow_high:
            reason_codes.insert(0, RC_BOTH_TEAMS_ALLOW_HIGH_L5)
    # ── A. LOW_SCORING_CROSS — softer Under signal. ────────────────────
    elif both_offenses_low and at_least_one_allow_low:
        profile = PROFILE_LOW_SCORING
        supports = "UNDER"
        confidence_delta = 6
        fragility_delta = 4
        reason_codes = [
            RC_BOTH_OFFENSES_LOW_L5,
            RC_LOW_RUN_ALLOWANCE_SUPPORTS_UNDER,
            RC_LOW_SCORING_CROSS_SUPPORTS_UNDER,
        ]
    # ── E. MIXED_PROFILE — fallback for split signals. ─────────────────
    elif (one_team_low_offense and one_team_high_offense) \
            or (one_team_low_offense and both_teams_allow_high) \
            or (one_team_high_offense and both_teams_allow_low):
        profile = PROFILE_MIXED
        supports = "NEUTRAL"
        confidence_delta = 0
        fragility_delta = 2
        reason_codes = [RC_MIXED_RUN_PROFILE_NO_CLEAR_EDGE]
    else:
        # No clear cross — explicit NEUTRAL with no deltas (avoids
        # tagging benign games as MIXED).
        profile = None
        supports = "NEUTRAL"
        confidence_delta = 0
        fragility_delta = 0
        reason_codes = []

    # Combined L5/L15 sums (purely informational for UI).
    combined_scored_l5  = _round(h_s5 + a_s5, 2)
    combined_scored_l15 = (
        _round(home["scored_l15"] + away["scored_l15"], 2)
        if (home["scored_l15"] is not None and away["scored_l15"] is not None)
        else None
    )
    combined_allowed_l5  = _round(h_a5 + a_a5, 2)
    combined_allowed_l15 = (
        _round(home["allowed_l15"] + away["allowed_l15"], 2)
        if (home["allowed_l15"] is not None and away["allowed_l15"] is not None)
        else None
    )

    # Carry forward per-team RCs at the top level for downstream consumers.
    combined_rcs = list(reason_codes)
    for rc in home["reason_codes"] + away["reason_codes"]:
        if rc not in combined_rcs:
            combined_rcs.append(rc)

    return {
        "available":            True,
        "engine_version":       ENGINE_VERSION,
        "home_scored_l5":       _round(h_s5, 2),
        "home_scored_l15":      _round(home["scored_l15"], 2),
        "home_allowed_l5":      _round(h_a5, 2),
        "home_allowed_l15":     _round(home["allowed_l15"], 2),
        "away_scored_l5":       _round(a_s5, 2),
        "away_scored_l15":      _round(away["scored_l15"], 2),
        "away_allowed_l5":      _round(a_a5, 2),
        "away_allowed_l15":     _round(away["allowed_l15"], 2),
        "combined_scored_l5":   combined_scored_l5,
        "combined_scored_l15":  combined_scored_l15,
        "combined_allowed_l5":  combined_allowed_l5,
        "combined_allowed_l15": combined_allowed_l15,
        "profile":              profile,
        "supports":             supports,
        "confidence_delta":     int(confidence_delta),
        "fragility_delta":      int(fragility_delta),
        "reason_codes":         combined_rcs,
        "per_team":             {"home": home, "away": away},
        "narrative_es":         _narrative_es(profile),
    }


# ─────────────────────────────────────────────────────────────────────
# Symmetric application helper (orchestrator-friendly)
# ─────────────────────────────────────────────────────────────────────
def apply_run_profile_cross_to_pick(
    *,
    cross_payload:        dict,
    pick_side:            Optional[str],     # "under" | "over" | None
    current_confidence:   Optional[float],
    current_fragility:    Optional[float],
) -> dict:
    """Apply the cross's deltas to the engine's pick, symmetrically.

    Same rules as Phase 58 / CAMBIO 4:
      * If the cross's ``supports`` matches the pick side → bonus to
        confidence (capped at +MAX_CONFIDENCE_BONUS), reduces fragility.
      * If it contradicts (and is not NEUTRAL) → penalty
        (capped at -MAX_CONFIDENCE_PENALTY), increases fragility.
      * NEUTRAL/unavailable → no-op.

    Returns the same shape regardless of outcome:
        {
            applied: bool,
            new_confidence, new_fragility,
            confidence_delta_signed,
            fragility_delta_signed,
            interaction: "SUPPORTS_PICK"|"CONTRADICTS_PICK"|"NEUTRAL"|"SKIPPED",
            reason_codes: list[str],
        }
    """
    base_conf = _safe(current_confidence)
    base_frag = _safe(current_fragility)
    side = (pick_side or "").lower()

    if (not isinstance(cross_payload, dict)
            or not cross_payload.get("available")
            or cross_payload.get("supports") == "NEUTRAL"
            or side not in ("under", "over")
            or base_conf is None):
        return {
            "applied":                  False,
            "new_confidence":           base_conf,
            "new_fragility":            base_frag,
            "confidence_delta_signed":  0,
            "fragility_delta_signed":   0,
            "interaction":              "SKIPPED",
            "reason_codes":             [],
        }

    raw_conf_delta = int(cross_payload.get("confidence_delta") or 0)
    raw_frag_delta = int(cross_payload.get("fragility_delta") or 0)
    supports = (cross_payload.get("supports") or "").upper()
    profile  = cross_payload.get("profile")

    interaction = "NEUTRAL"
    conf_signed = 0
    frag_signed = 0
    rcs: list[str] = []

    if supports == side.upper():
        # Supports pick — bonus.
        interaction = "SUPPORTS_PICK"
        conf_signed = int(min(raw_conf_delta, MAX_CONFIDENCE_BONUS))
        frag_signed = -abs(raw_frag_delta)
        rcs = [
            f"RUN_PROFILE_CROSS_SUPPORTS_{side.upper()}",
            f"PROFILE_{profile}",
        ] if profile else [f"RUN_PROFILE_CROSS_SUPPORTS_{side.upper()}"]
    else:
        # Contradicts pick — penalty (cross supports the opposite side).
        interaction = "CONTRADICTS_PICK"
        conf_signed = -int(min(raw_conf_delta, MAX_CONFIDENCE_PENALTY))
        frag_signed = abs(raw_frag_delta)
        rcs = [
            f"RUN_PROFILE_CROSS_CONTRADICTS_{side.upper()}",
            f"PROFILE_{profile}",
        ] if profile else [f"RUN_PROFILE_CROSS_CONTRADICTS_{side.upper()}"]

    new_conf = _clamp(base_conf + conf_signed, 0.0, 100.0)
    if base_frag is not None:
        new_frag = _clamp(base_frag + frag_signed, 0.0, 100.0)
    else:
        new_frag = None

    return {
        "applied":                  True,
        "new_confidence":           round(new_conf, 2),
        "new_fragility":            None if new_frag is None else round(new_frag, 2),
        "confidence_delta_signed":  conf_signed,
        "fragility_delta_signed":   frag_signed,
        "interaction":              interaction,
        "reason_codes":             rcs,
    }


# ─────────────────────────────────────────────────────────────────────
# Pattern-alignment visual entry
# ─────────────────────────────────────────────────────────────────────
def build_pattern_alignment_entry(cross_payload: dict, pick_side: Optional[str]) -> Optional[dict]:
    """Build the visual-only entry to append into
    ``pick_payload["pattern_alignment"]["entries"]``.

    Returns None when there is nothing meaningful to show (unavailable
    or NEUTRAL profile).

    IMPORTANT: this entry is **visual / auditable only** and MUST NOT
    be counted toward ``supporting_count`` / ``contradicting_count`` —
    those drive the CAMBIO 4 pattern-contradiction penalty and must
    remain independent to prevent double accounting.
    """
    if not isinstance(cross_payload, dict) or not cross_payload.get("available"):
        return None
    supports = (cross_payload.get("supports") or "").upper()
    profile  = cross_payload.get("profile")
    if not profile or supports == "NEUTRAL":
        return None

    side = (pick_side or "").lower()
    supports_pick = (side and supports == side.upper())

    return {
        "pattern":          profile,
        "side":             supports,
        "supports_pick":    bool(supports_pick),
        "message":          cross_payload.get("narrative_es"),
        "source":           "mlb_run_profile_cross",
        "visual_only":      True,  # explicit flag — do NOT add to counts.
    }


__all__ = [
    "ENGINE_VERSION",
    "MAX_CONFIDENCE_BONUS",
    "MAX_CONFIDENCE_PENALTY",
    "PROFILE_STRONG_UNDER",
    "PROFILE_LOW_SCORING",
    "PROFILE_STRONG_OVER",
    "PROFILE_HIGH_SCORING",
    "PROFILE_MIXED",
    "classify_team_run_profile",
    "compute_combined_run_profile_cross",
    "apply_run_profile_cross_to_pick",
    "build_pattern_alignment_entry",
]
