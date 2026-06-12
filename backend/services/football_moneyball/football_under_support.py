"""Football Under Support — pregame structural support for Under goal lines.

Mirror of football_over_support.py. Produces a structured score
quantifying how much the pregame context supports an Under outcome.

Design principles (identical to over_support):
  * Fail-soft. Missing signals do NOT default to neutral — they degrade
    available=False with _skipped="insufficient_signals" if the signal
    floor isn't met.
  * Pure function. No DB I/O, no mutations.
  * Symmetric output shape to football_over_support so the pipeline
    can consume both as equivalent inputs.
  * Context only — does NOT generate tickets. The selector decides.

Phase-1 policy on dc_nb_delta
-----------------------------
The DC/NB calibration delta is recorded as TELEMETRY only. The sign
convention is not yet validated for football (a positive delta might
mean "model raises P(Under)" or the opposite). Until the schema is
confirmed in production, the delta is surfaced in ``dc_nb_telemetry``
but contributes 0 points to the score. Promotion path is documented
inside :func:`_dc_nb_telemetry`.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football_under_support")

ENGINE_VERSION = "football_under_support.1"

# Reason codes.
RC_DEFENSIVE_SOLIDITY_DETECTED  = "DEFENSIVE_SOLIDITY_DETECTED"
RC_BOTH_OFFENSES_COLD           = "BOTH_OFFENSES_COLD"
RC_LOW_COMBINED_XG              = "LOW_COMBINED_XG"
RC_HIGH_CLEAN_SHEET_RATE        = "HIGH_CLEAN_SHEET_RATE"
RC_LOW_MOTIVATION_CONTEXT       = "LOW_MOTIVATION_CONTEXT_MILD"
RC_ATTACKING_INJURIES_BONUS     = "ATTACKING_INJURIES_BONUS"
RC_COLD_WEATHER_BONUS           = "COLD_WEATHER_BONUS"
RC_DC_NB_DELTA_TELEMETRY        = "DC_NB_DELTA_TELEMETRY_ONLY"
RC_SIGNAL_MISSING               = "SIGNAL_MISSING"

# Minimum signals required to produce a meaningful score. Below this we
# return available=False rather than a misleadingly neutral 50.
MIN_SIGNALS_FLOOR = 3

# Thresholds.
SOLID_XGA_MAX            = 0.95
SOLID_GA_AVG_MAX         = 1.10
COLD_RECENT_GOALS_MAX    = 1.10
LOW_COMBINED_XG_MAX      = 2.30
HIGH_CLEAN_SHEET_RATE    = 0.40


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _defensive_solidity_score(team_signals: dict) -> tuple[Optional[int], list[str], int]:
    """Mirror of _defensive_leak_score in over_support, rewarding solid
    defenses. Returns (score|None, reason_codes, signals_used).

    Returns None for score when BOTH xga and ga_avg are missing — caller
    counts this as zero signals contributed.
    """
    reasons: list[str] = []
    xga = _f(team_signals.get("xga"))
    ga_avg = _f(team_signals.get("goals_against_avg"))
    signals_used = 0

    if xga is None and ga_avg is None:
        reasons.append(RC_SIGNAL_MISSING)
        return None, reasons, 0

    base = 50
    if ga_avg is not None:
        signals_used += 1
        if ga_avg <= 0.80:
            base = 80
        elif ga_avg <= SOLID_GA_AVG_MAX:
            base = 65
        elif ga_avg >= 1.60:
            base = 20
    if xga is not None:
        signals_used += 1
        if xga <= 0.80:
            base = min(100, base + 10)
        elif xga >= 1.40:
            base = max(0, base - 10)
    if base >= 65:
        reasons.append(RC_DEFENSIVE_SOLIDITY_DETECTED)
    return base, reasons, signals_used


def _both_offenses_cold(home_signals: dict, away_signals: dict) -> tuple[int, list[str], int]:
    h = _f(home_signals.get("recent_gf_per_match"))
    a = _f(away_signals.get("recent_gf_per_match"))
    if h is None or a is None:
        return 0, [RC_SIGNAL_MISSING], 0
    if h <= COLD_RECENT_GOALS_MAX and a <= COLD_RECENT_GOALS_MAX:
        return 12, [RC_BOTH_OFFENSES_COLD], 2
    return 0, [], 2


def _low_combined_xg(home_signals: dict, away_signals: dict) -> tuple[int, list[str], int]:
    h_xg = _f(home_signals.get("xg"))
    a_xg = _f(away_signals.get("xg"))
    if h_xg is None or a_xg is None:
        return 0, [RC_SIGNAL_MISSING], 0
    if h_xg + a_xg <= LOW_COMBINED_XG_MAX:
        return 10, [RC_LOW_COMBINED_XG], 2
    return 0, [], 2


def _high_clean_sheet_rate(home_signals: dict, away_signals: dict) -> tuple[int, list[str], int]:
    h_cs = _f(home_signals.get("clean_sheet_rate"))
    a_cs = _f(away_signals.get("clean_sheet_rate"))
    if h_cs is None or a_cs is None:
        return 0, [], 0
    if (h_cs + a_cs) / 2.0 >= HIGH_CLEAN_SHEET_RATE:
        return 8, [RC_HIGH_CLEAN_SHEET_RATE], 2
    return 0, [], 2


def _attacking_injuries(match: dict) -> tuple[int, list[str], int]:
    injuries = (match.get("injuries") or {})
    if not isinstance(injuries, dict):
        return 0, [], 0
    attackers_out = int(injuries.get("attackers_out_top3") or 0)
    if attackers_out >= 2:
        return 10, [RC_ATTACKING_INJURIES_BONUS], 1
    if attackers_out == 1:
        return 5, [RC_ATTACKING_INJURIES_BONUS], 1
    return 0, [], 1


def _cold_weather(match: dict) -> tuple[int, list[str], int]:
    weather = (match.get("weather") or {})
    temp_c = _f(weather.get("temp_c"))
    condition = str(weather.get("condition") or "").lower()
    if temp_c is None and not condition:
        return 0, [], 0
    if temp_c is not None and temp_c <= 5.0:
        return 5, [RC_COLD_WEATHER_BONUS], 1
    if any(kw in condition for kw in ("snow", "heavy rain", "storm")):
        return 5, [RC_COLD_WEATHER_BONUS], 1
    return 0, [], 1


def _low_motivation_conservative(
    match: dict,
    *,
    has_corroborating_signal: bool,
) -> tuple[int, list[str], int]:
    """Conservative motivation signal.

    A "dead-rubber" match can go EITHER way (relaxed defenses, rotations,
    less tactical intensity → more goals; or low intensity overall → fewer
    goals). To avoid pushing Under purely on motivation, this signal:
      * Caps at +3 (down from +8).
      * Only contributes if there is at least one corroborating low-scoring
        signal (low combined xG, defensive solidity, both offenses cold,
        high clean-sheet rate). Without corroboration, returns 0.
    """
    motivation = (match.get("motivation_context") or {})
    is_dead_or_low = bool(motivation.get("dead_rubber")) or bool(motivation.get("low_stakes"))
    if not is_dead_or_low:
        return 0, [], 0
    if not has_corroborating_signal:
        # Signal present but not used — flag for telemetry but no points.
        return 0, [RC_LOW_MOTIVATION_CONTEXT + "_NOT_CORROBORATED"], 1
    return 3, [RC_LOW_MOTIVATION_CONTEXT], 1


def _dc_nb_telemetry(totals_model: dict | None) -> tuple[int, list[str], dict]:
    """Phase-1 policy: dc_nb_delta is recorded as TELEMETRY only.

    The sign convention is not yet validated in the football engine —
    a positive delta might mean "new model raises P(Under)" or the
    opposite depending on how statsbomb_features stores it. Until the
    schema is confirmed in production, do NOT use this signal to score.

    Once validated, this function can be promoted to add +8 when the
    delta strongly favours Under. The placeholder return surface keeps
    the integration point ready for that promotion.
    """
    if not totals_model:
        return 0, [], {"dc_nb_delta_2_5_pts": None, "dc_nb_delta_3_5_pts": None}
    return 0, [RC_DC_NB_DELTA_TELEMETRY], {
        "dc_nb_delta_2_5_pts": totals_model.get("dc_nb_delta_2_5_pts"),
        "dc_nb_delta_3_5_pts": totals_model.get("dc_nb_delta_3_5_pts"),
        "_policy": "telemetry_only_until_sign_validated",
    }


def calculate_football_under_support(match: dict | None) -> dict:
    """Build the canonical football_under_support block.

    Returns available=False with _skipped="insufficient_signals" when
    fewer than MIN_SIGNALS_FLOOR (3) raw signals contributed to the score.
    This prevents a near-empty match doc from producing a misleading 50.
    """
    if not isinstance(match, dict):
        return {
            "football_under_support": {
                "available":   False,
                "score":       0,
                "reason_codes": [],
                "version":     ENGINE_VERSION,
                "_skipped":    "no_match_doc",
            }
        }

    try:
        home_signals = (match.get("home_team_signals") or {})
        away_signals = (match.get("away_team_signals") or {})
        totals_model = (match.get("statsbomb_features")
                         or match.get("totals_model") or {})

        signals_available = 0

        # Defensive solidity per side (may return None each).
        h_def, h_reasons, h_used = _defensive_solidity_score(home_signals)
        a_def, a_reasons, a_used = _defensive_solidity_score(away_signals)
        signals_available += h_used + a_used

        # Average only the sides that produced a score.
        side_scores = [s for s in (h_def, a_def) if s is not None]
        def_score = (
            int(round((sum(side_scores) / len(side_scores)) * 0.6))
            if side_scores else 0
        )

        # Bonuses + their signal counts.
        cold_b, cold_r, cold_n  = _both_offenses_cold(home_signals, away_signals)
        xg_b,   xg_r,   xg_n    = _low_combined_xg(home_signals, away_signals)
        cs_b,   cs_r,   cs_n    = _high_clean_sheet_rate(home_signals, away_signals)
        inj_b,  inj_r,  inj_n   = _attacking_injuries(match)
        wx_b,   wx_r,   wx_n    = _cold_weather(match)
        # dc_nb is telemetry-only — contributes 0 score but valid signal.
        dc_b,   dc_r,   dc_tele = _dc_nb_telemetry(totals_model)

        signals_available += cold_n + xg_n + cs_n + inj_n + wx_n

        # Motivation requires corroboration from another low-scoring signal.
        has_corroborating = (cold_b > 0 or xg_b > 0 or cs_b > 0 or def_score >= 30)
        mot_b, mot_r, mot_n = _low_motivation_conservative(
            match, has_corroborating_signal=has_corroborating,
        )
        signals_available += mot_n

        # Floor check — refuse to score when too few real signals.
        if signals_available < MIN_SIGNALS_FLOOR:
            return {
                "football_under_support": {
                    "available":          False,
                    "score":              0,
                    "signals_available":  signals_available,
                    "min_signals_floor":  MIN_SIGNALS_FLOOR,
                    "reason_codes":       [RC_SIGNAL_MISSING],
                    "version":            ENGINE_VERSION,
                    "_skipped":           "insufficient_signals",
                }
            }

        total = def_score + cold_b + xg_b + cs_b + inj_b + wx_b + mot_b + dc_b
        total = max(0, min(100, total))

        reason_codes = list(dict.fromkeys(
            h_reasons + a_reasons + cold_r + xg_r + cs_r
            + inj_r + wx_r + mot_r + dc_r
        ))

        # Spanish narrative.
        fragments = []
        if cold_b:
            fragments.append("ambas ofensivas frías recientemente")
        if xg_b:
            fragments.append("xG combinado bajo")
        if cs_b:
            fragments.append("alto porcentaje de clean sheets")
        if inj_b:
            fragments.append("bajas en jugadores ofensivos")
        if mot_b:
            fragments.append("motivación baja (corroborada)")
        if wx_b:
            fragments.append("clima frío/adverso")
        if def_score >= 35:
            fragments.append("defensas sólidas")
        if fragments:
            joined = ", ".join(fragments[:-1])
            joined = f"{joined} y {fragments[-1]}" if len(fragments) > 1 else fragments[0]
            narrative_es = f"Contexto apoya Under: {joined}."
        else:
            narrative_es = "Contexto neutral o débil para Under."

        return {
            "football_under_support": {
                "available":             True,
                "score":                 int(total),
                "signals_available":     signals_available,
                "defensive_solidity":    int(def_score),
                "bonuses": {
                    "both_offenses_cold":   cold_b,
                    "low_combined_xg":      xg_b,
                    "high_clean_sheet":     cs_b,
                    "attacking_injuries":   inj_b,
                    "cold_weather":         wx_b,
                    "low_motivation":       mot_b,
                },
                "dc_nb_telemetry":       dc_tele,
                "reason_codes":          reason_codes,
                "narrative_es":          narrative_es,
                "version":               ENGINE_VERSION,
            }
        }
    except Exception as exc:
        log.debug("calculate_football_under_support failed: %s", exc)
        return {
            "football_under_support": {
                "available":   False,
                "score":       0,
                "reason_codes": [],
                "version":     ENGINE_VERSION,
                "_error":      str(exc),
            }
        }


__all__ = ["calculate_football_under_support", "ENGINE_VERSION", "MIN_SIGNALS_FLOOR"]
