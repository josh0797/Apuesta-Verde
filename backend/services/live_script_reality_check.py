"""Live Script Reality Check — MLB totals (Phase 44 / P3).

Compares the pre-match projection vs the live game script and tells
the user whether the game is *validating* or *contradicting* the
original model. Drives cashout / line-adjustment decisions and post-
match learning.

Four canonical classifications:

  1. ``LIVE_UNDER_CONFIRMATION`` — engine picked Under, game is even
     slower than projected. Reinforces the position.
  2. ``LIVE_OVER_WARNING``      — engine picked Under, but live signals
     (HR, base traffic, bullpen pressure) hint at fragility.
  3. ``LIVE_OVER_CONFIRMATION`` — engine picked Over, game supports it.
  4. ``LIVE_OVER_DANGER``       — Over pick (engine or user) but the
     game is way slower than projected; only a bullpen collapse or
     extra innings saves it.

Pure module — no I/O. Caller is responsible for snapshotting the live
state and persisting the output. NEVER raises.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("live_script_reality_check")

ENGINE_VERSION = "live_script_reality_check.1"

# Classifications
CLASS_UNDER_CONFIRMATION = "LIVE_UNDER_CONFIRMATION"
CLASS_OVER_WARNING       = "LIVE_OVER_WARNING"
CLASS_OVER_CONFIRMATION  = "LIVE_OVER_CONFIRMATION"
CLASS_OVER_DANGER        = "LIVE_OVER_DANGER"
CLASS_NEUTRAL            = "LIVE_NEUTRAL"

ALL_CLASSIFICATIONS = (
    CLASS_UNDER_CONFIRMATION,
    CLASS_OVER_WARNING,
    CLASS_OVER_CONFIRMATION,
    CLASS_OVER_DANGER,
    CLASS_NEUTRAL,
)

# Reason codes (exact strings the spec asked for)
RC_STARTERS_DOMINATING        = "STARTERS_DOMINATING_ABOVE_EXPECTATION"
RC_LOW_RUN_CONVERSION         = "LOW_RUN_CONVERSION"
RC_NO_HOME_RUN_SIGNAL         = "NO_HOME_RUN_SIGNAL"
RC_LIVE_UNDER_SCRIPT_CONFIRMED = "LIVE_UNDER_SCRIPT_CONFIRMED"
RC_UNDER_SCRIPT_FRAGILE_LIVE  = "UNDER_SCRIPT_FRAGILE_LIVE"
RC_HIGH_BASE_TRAFFIC          = "HIGH_BASE_TRAFFIC"
RC_BULLPEN_PRESSURE_RISING    = "BULLPEN_PRESSURE_RISING"
RC_EXPLOSIVE_INNING_RISK      = "EXPLOSIVE_INNING_RISK"
RC_LIVE_OVER_SCRIPT_CONFIRMED = "LIVE_OVER_SCRIPT_CONFIRMED"
RC_BASE_TRAFFIC_SUPPORTS_OVER = "BASE_TRAFFIC_SUPPORTS_OVER"
RC_PITCH_COUNT_PRESSURE       = "PITCH_COUNT_PRESSURE"
RC_BULLPEN_ENTRY_SUPPORTS_OVER = "BULLPEN_ENTRY_SUPPORTS_OVER"
RC_OVER_SCRIPT_NOT_MATERIALIZING = "OVER_SCRIPT_NOT_MATERIALIZING"
RC_NEEDS_BULLPEN_COLLAPSE     = "NEEDS_BULLPEN_COLLAPSE_OR_EXTRA_INNINGS"

# Tunables (calibrated for MLB 9-inning games)
INNING_LATE_THRESHOLD       = 7      # ≥ 7th inning = "late game"
HITS_HIGH_THRESHOLD         = 12     # combined hits considered "high"
WALKS_HIGH_THRESHOLD        = 6
HR_RISK_THRESHOLD           = 1      # any HR is a signal
ERRORS_HIGH_THRESHOLD       = 2
LOB_HIGH_THRESHOLD          = 10
RUN_PACE_BELOW_PCT          = 0.65   # live pace ≤ 65% of expected = slow
RUN_PACE_ABOVE_PCT          = 1.20   # ≥ 120% = hot


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _is_under_pick(text: Optional[str]) -> bool:
    return bool(text) and "under" in str(text).lower()


def _is_over_pick(text: Optional[str]) -> bool:
    return bool(text) and "over" in str(text).lower()


def evaluate_live_script(
    *,
    pre_match_expected_runs: Optional[float],
    recommended_market:      Optional[str],
    user_market:             Optional[str] = None,
    current_inning:          Optional[int]   = None,
    current_score_total:     Optional[int]   = None,
    combined_hits:           Optional[int]   = None,
    combined_walks:          Optional[int]   = None,
    combined_home_runs:      Optional[int]   = None,
    combined_errors:         Optional[int]   = None,
    combined_left_on_base:   Optional[int]   = None,
    pitchers_current_status: Optional[str]   = None,
    bullpen_usage:           Optional[float] = None,
    live_run_rate:           Optional[float] = None,
    projected_final_runs_live: Optional[float] = None,
) -> dict:
    """Build the live reality-check payload. Pure + fail-soft."""
    out = {
        "engine_version":   ENGINE_VERSION,
        "classification":   CLASS_NEUTRAL,
        "reason_codes":     [],
        "summary_es":       "Sin datos suficientes para validar el guion live.",
        "supports_pick":    None,    # True/False/None
        "fragility_live":   None,
        "live_projected_final_runs": None,
        "expected_runs":    None,
    }

    try:
        proj = _safe_float(pre_match_expected_runs)
        inning = _safe_int(current_inning)
        score = _safe_int(current_score_total)
        hits  = _safe_int(combined_hits)
        walks = _safe_int(combined_walks)
        hrs   = _safe_int(combined_home_runs)
        errs  = _safe_int(combined_errors)
        lob   = _safe_int(combined_left_on_base)
        bullpen_load = _safe_float(bullpen_usage)
        live_proj = _safe_float(projected_final_runs_live)

        # Determine WHICH side is the active position (user > engine).
        pick = user_market or recommended_market
        is_under = _is_under_pick(pick)
        is_over  = _is_over_pick(pick)

        # Live projection: prefer caller's value, else extrapolate.
        if live_proj is None and proj is not None and inning and score is not None and inning > 0:
            # Linearly extrapolate score to 9 innings (very simple but fine).
            live_proj = round(score * (9.0 / max(1, min(9, inning))), 2)
        out["live_projected_final_runs"] = live_proj
        out["expected_runs"] = proj

        if proj is None or pick is None:
            return out

        # ── Signal accumulators ───────────────────────────────────
        reasons: list[str] = []
        is_late = (inning is not None and inning >= INNING_LATE_THRESHOLD)
        run_pace_ratio = None
        if proj > 0 and live_proj is not None:
            run_pace_ratio = round(live_proj / proj, 3)

        below_pace = (run_pace_ratio is not None and run_pace_ratio <= RUN_PACE_BELOW_PCT)
        above_pace = (run_pace_ratio is not None and run_pace_ratio >= RUN_PACE_ABOVE_PCT)
        low_hits   = (hits is not None and hits <= 8)
        high_hits  = (hits is not None and hits >= HITS_HIGH_THRESHOLD)
        high_walks = (walks is not None and walks >= WALKS_HIGH_THRESHOLD)
        no_hrs     = (hrs is not None and hrs == 0)
        has_hrs    = (hrs is not None and hrs >= HR_RISK_THRESHOLD)
        high_lob   = (lob is not None and lob >= LOB_HIGH_THRESHOLD)
        bullpen_in_early = bool(bullpen_load is not None and bullpen_load >= 0.5
                                and inning is not None and inning < 7)
        starters_strong = (pitchers_current_status or "").lower() in (
            "dominating", "strong", "on_fire", "ace_form",
        )

        # ── 1) LIVE_UNDER_CONFIRMATION ────────────────────────────
        if is_under and is_late and below_pace and low_hits and no_hrs:
            reasons += [
                RC_STARTERS_DOMINATING, RC_LOW_RUN_CONVERSION,
                RC_NO_HOME_RUN_SIGNAL,  RC_LIVE_UNDER_SCRIPT_CONFIRMED,
            ]
            out["classification"] = CLASS_UNDER_CONFIRMATION
            out["supports_pick"] = True
            out["summary_es"] = (
                "El partido está siendo más cerrado que la proyección original. "
                "Los abridores han dominado y la ofensiva no ha convertido oportunidades."
            )

        # ── 2) LIVE_OVER_WARNING (Under pick, but fragility rising) ──
        elif is_under and (high_hits or high_walks or has_hrs or bullpen_in_early or high_lob):
            if high_hits or high_walks:
                reasons.append(RC_HIGH_BASE_TRAFFIC)
            if bullpen_in_early:
                reasons.append(RC_BULLPEN_PRESSURE_RISING)
            if has_hrs:
                reasons.append(RC_EXPLOSIVE_INNING_RISK)
            reasons.append(RC_UNDER_SCRIPT_FRAGILE_LIVE)
            out["classification"] = CLASS_OVER_WARNING
            out["supports_pick"] = False
            out["summary_es"] = (
                "El Under sigue vivo, pero el script se está volviendo frágil. "
                "Hay señales de explosión ofensiva o bullpen vulnerable."
            )

        # ── 3) LIVE_OVER_CONFIRMATION (Over pick + supporting signals) ──
        elif is_over and (above_pace or high_hits or has_hrs or bullpen_in_early):
            reasons.append(RC_LIVE_OVER_SCRIPT_CONFIRMED)
            if high_hits or high_walks:
                reasons.append(RC_BASE_TRAFFIC_SUPPORTS_OVER)
            if bullpen_in_early:
                reasons.append(RC_BULLPEN_ENTRY_SUPPORTS_OVER)
            if has_hrs:
                reasons.append(RC_PITCH_COUNT_PRESSURE)
            out["classification"] = CLASS_OVER_CONFIRMATION
            out["supports_pick"] = True
            out["summary_es"] = (
                "El Over está siendo respaldado por el partido real: hay tráfico en bases, "
                "presión ofensiva y ritmo de carreras suficiente."
            )

        # ── 4) LIVE_OVER_DANGER (Over pick + game running cold) ──
        elif is_over and is_late and below_pace and low_hits and no_hrs:
            reasons += [
                RC_OVER_SCRIPT_NOT_MATERIALIZING, RC_STARTERS_DOMINATING,
                RC_LOW_RUN_CONVERSION, RC_NO_HOME_RUN_SIGNAL,
                RC_NEEDS_BULLPEN_COLLAPSE,
            ]
            out["classification"] = CLASS_OVER_DANGER
            out["supports_pick"] = False
            out["summary_es"] = (
                "El partido está siendo mucho más cerrado de lo proyectado. "
                "El Over todavía puede vivir por bullpen o extra innings, pero ya "
                "depende más de un colapso tardío que del guion normal."
            )
        else:
            out["classification"] = CLASS_NEUTRAL
            out["summary_es"] = "Guion live alineado con la proyección original."

        # Fragility (0..1): closer to 1 = more risky given the pick.
        fragility = 0.0
        if is_under:
            if high_hits:          fragility += 0.25
            if high_walks:         fragility += 0.15
            if has_hrs:            fragility += 0.25
            if bullpen_in_early:   fragility += 0.20
            if high_lob:           fragility += 0.10
            if errs is not None and errs >= ERRORS_HIGH_THRESHOLD: fragility += 0.05
        elif is_over:
            if below_pace and is_late: fragility += 0.40
            if low_hits and is_late:   fragility += 0.20
            if no_hrs and is_late:     fragility += 0.15
            if starters_strong:        fragility += 0.10
        out["fragility_live"] = round(min(1.0, fragility), 3)
        out["reason_codes"] = reasons
        return out

    except Exception as exc:  # pragma: no cover — fail-soft guard
        log.debug("evaluate_live_script failed: %s", exc)
        return out


__all__ = [
    "ENGINE_VERSION",
    "CLASS_UNDER_CONFIRMATION",
    "CLASS_OVER_WARNING",
    "CLASS_OVER_CONFIRMATION",
    "CLASS_OVER_DANGER",
    "CLASS_NEUTRAL",
    "ALL_CLASSIFICATIONS",
    # Reason codes
    "RC_STARTERS_DOMINATING",
    "RC_LOW_RUN_CONVERSION",
    "RC_NO_HOME_RUN_SIGNAL",
    "RC_LIVE_UNDER_SCRIPT_CONFIRMED",
    "RC_UNDER_SCRIPT_FRAGILE_LIVE",
    "RC_HIGH_BASE_TRAFFIC",
    "RC_BULLPEN_PRESSURE_RISING",
    "RC_EXPLOSIVE_INNING_RISK",
    "RC_LIVE_OVER_SCRIPT_CONFIRMED",
    "RC_BASE_TRAFFIC_SUPPORTS_OVER",
    "RC_PITCH_COUNT_PRESSURE",
    "RC_BULLPEN_ENTRY_SUPPORTS_OVER",
    "RC_OVER_SCRIPT_NOT_MATERIALIZING",
    "RC_NEEDS_BULLPEN_COLLAPSE",
    "evaluate_live_script",
]
