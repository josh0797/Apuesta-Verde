"""MLB Defensive Breakdown Score — Phase 50.

Detect when defense can create "free runs" via errors, passed balls,
wild pitches, stolen-base mistakes and poor fielding profile.

Two modes:
    * ``pregame`` — uses season-to-date fielding profile (errors/game,
      fielding %, DRS, OAA, catcher metrics, SB allowed/game).
    * ``live``    — uses in-game events (current errors, PBs, WPs, SBs
      allowed, runners advancing on errors, unearned runs).

Pure module — no I/O. ``compute_defensive_breakdown_score()`` and the
``classify_combined_explosion_risk()`` helper are the public API.

Composition (each component capped at the Phase-49 LIVE-component
25%-cap convention so no single signal can dominate the score):

    errors                  20
    passed_balls            10
    wild_pitches            10
    stolen_bases_allowed     8
    fielding_pct_penalty    20
    drs_penalty             18
    catcher_mistakes_live   14   (live-only top-up, otherwise 0)

Total weight = 100. Live mode reweights catcher_mistakes_live + raw
events; pregame mode reweights fielding_pct_penalty + drs_penalty.

Buckets:
    0-39   ⇒ LOW_DEFENSIVE_RISK
    40-69  ⇒ MEDIUM_DEFENSIVE_RISK
    70-100 ⇒ HIGH_DEFENSIVE_RISK
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "mlb_defensive_breakdown.1"

# ── Component weights (cap per component) ─────────────────────────────
COMPONENT_WEIGHTS = {
    "errors":                20,
    "passed_balls":          10,
    "wild_pitches":          10,
    "stolen_bases_allowed":   8,
    "fielding_pct_penalty":  20,
    "drs_penalty":           18,
    "catcher_mistakes_live": 14,
}
COMPONENT_CAP_PCT = 25  # safety: no single signal > 25% of score

# ── Buckets ──────────────────────────────────────────────────────────
BUCKET_LOW_MAX    = 39
BUCKET_MEDIUM_MAX = 69
BUCKET_LOW    = "LOW_DEFENSIVE_RISK"
BUCKET_MEDIUM = "MEDIUM_DEFENSIVE_RISK"
BUCKET_HIGH   = "HIGH_DEFENSIVE_RISK"

# ── Reason codes ─────────────────────────────────────────────────────
RC_DEFENSIVE_MELTDOWN_RISK         = "DEFENSIVE_MELTDOWN_RISK"
RC_LIVE_ERRORS_RAISE_RUN_RISK      = "LIVE_ERRORS_RAISE_RUN_RISK"
RC_PASSED_BALL_PRESSURE            = "PASSED_BALL_PRESSURE"
RC_WILD_PITCH_PRESSURE             = "WILD_PITCH_PRESSURE"
RC_STOLEN_BASE_DEFENSIVE_FAILURE   = "STOLEN_BASE_DEFENSIVE_FAILURE"
RC_POOR_FIELDING_PROFILE           = "POOR_FIELDING_PROFILE"
RC_UNEARNED_RUN_RISK               = "UNEARNED_RUN_RISK"
RC_BULLPEN_TRAFFIC_DEFENSE_EXPLOSION_RISK = "BULLPEN_TRAFFIC_DEFENSE_EXPLOSION_RISK"

ALL_REASON_CODES = (
    RC_DEFENSIVE_MELTDOWN_RISK,
    RC_LIVE_ERRORS_RAISE_RUN_RISK,
    RC_PASSED_BALL_PRESSURE,
    RC_WILD_PITCH_PRESSURE,
    RC_STOLEN_BASE_DEFENSIVE_FAILURE,
    RC_POOR_FIELDING_PROFILE,
    RC_UNEARNED_RUN_RISK,
    RC_BULLPEN_TRAFFIC_DEFENSE_EXPLOSION_RISK,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _score_capped(pts: float, weight: int) -> int:
    cap = min(weight, COMPONENT_CAP_PCT)
    return int(round(max(0.0, min(cap, pts))))


def _bucket_from_score(score: int) -> str:
    if score <= BUCKET_LOW_MAX:
        return BUCKET_LOW
    if score <= BUCKET_MEDIUM_MAX:
        return BUCKET_MEDIUM
    return BUCKET_HIGH


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def compute_defensive_breakdown_score(
    *,
    mode: str = "pregame",
    # Pregame season-to-date profile (per game averages).
    fielding_pct:           Optional[float] = None,   # 0.0-1.0
    errors_per_game:        Optional[float] = None,   # rate
    drs:                    Optional[float] = None,   # defensive runs saved (-/+)
    oaa:                    Optional[float] = None,   # outs above average (-/+)
    passed_balls_per_game:  Optional[float] = None,
    sb_allowed_per_game:    Optional[float] = None,
    wp_allowed_per_game:    Optional[float] = None,
    # Live in-game raw events.
    live_errors:            Optional[int]   = None,
    live_passed_balls:      Optional[int]   = None,
    live_wild_pitches:      Optional[int]   = None,
    live_stolen_bases:      Optional[int]   = None,
    live_catcher_mistakes:  Optional[int]   = None,
    runners_advanced_on_errors: Optional[int] = None,
    unearned_runs:          Optional[int]   = None,
    innings_played:         Optional[float] = None,
) -> dict:
    """Build a composite 0-100 defensive-breakdown score.

    ``mode`` selects how component points are computed:
      * pregame: rates are converted into per-game expectations and
        scaled against league-average baselines.
      * live: raw event counts scale directly with a per-event point
        value (errors ≈ 7 pts, PB ≈ 4 pts, WP ≈ 4 pts, SB ≈ 2 pts).
    """
    mode = (mode or "pregame").lower()
    if mode not in ("pregame", "live"):
        mode = "pregame"

    components: dict[str, int] = {k: 0 for k in COMPONENT_WEIGHTS}
    reason_codes: list[str] = []

    if mode == "live":
        # Live mode — raw events.
        errs = _safe(live_errors) or 0.0
        pbs  = _safe(live_passed_balls) or 0.0
        wps  = _safe(live_wild_pitches) or 0.0
        sbs  = _safe(live_stolen_bases) or 0.0
        cms  = _safe(live_catcher_mistakes) or 0.0
        adv  = _safe(runners_advanced_on_errors) or 0.0
        urs  = _safe(unearned_runs) or 0.0

        # Each component = (events × per-event value), capped at its weight.
        components["errors"]               = _score_capped(errs * 7.0 + adv * 3.0,
                                                          COMPONENT_WEIGHTS["errors"])
        components["passed_balls"]         = _score_capped(pbs * 4.5,
                                                          COMPONENT_WEIGHTS["passed_balls"])
        components["wild_pitches"]         = _score_capped(wps * 4.5,
                                                          COMPONENT_WEIGHTS["wild_pitches"])
        components["stolen_bases_allowed"] = _score_capped(sbs * 2.5,
                                                          COMPONENT_WEIGHTS["stolen_bases_allowed"])
        components["catcher_mistakes_live"] = _score_capped(cms * 5.0,
                                                          COMPONENT_WEIGHTS["catcher_mistakes_live"])
        # Unearned runs heavily increase risk; route into fielding penalty.
        components["fielding_pct_penalty"] = _score_capped(urs * 5.0,
                                                          COMPONENT_WEIGHTS["fielding_pct_penalty"])
        # drs_penalty not used in live; provider rarely streams a live DRS.

        if errs >= 1:
            reason_codes.append(RC_LIVE_ERRORS_RAISE_RUN_RISK)
        if pbs >= 1:
            reason_codes.append(RC_PASSED_BALL_PRESSURE)
        if wps >= 1:
            reason_codes.append(RC_WILD_PITCH_PRESSURE)
        if sbs >= 2:
            reason_codes.append(RC_STOLEN_BASE_DEFENSIVE_FAILURE)
        if urs >= 1:
            reason_codes.append(RC_UNEARNED_RUN_RISK)
    else:
        # Pregame mode — season profile.
        fp  = _safe(fielding_pct)
        epg = _safe(errors_per_game) or 0.0
        d   = _safe(drs)
        o   = _safe(oaa)
        pbg = _safe(passed_balls_per_game) or 0.0
        sbg = _safe(sb_allowed_per_game)   or 0.0
        wpg = _safe(wp_allowed_per_game)   or 0.0

        # Errors per game: league avg ≈ 0.55. 1.0+ ⇒ full weight.
        components["errors"] = _score_capped((epg / 1.0) * COMPONENT_WEIGHTS["errors"],
                                              COMPONENT_WEIGHTS["errors"])
        components["passed_balls"] = _score_capped((pbg / 0.30) * COMPONENT_WEIGHTS["passed_balls"],
                                                    COMPONENT_WEIGHTS["passed_balls"])
        components["wild_pitches"] = _score_capped((wpg / 0.60) * COMPONENT_WEIGHTS["wild_pitches"],
                                                    COMPONENT_WEIGHTS["wild_pitches"])
        components["stolen_bases_allowed"] = _score_capped(
            (sbg / 1.0) * COMPONENT_WEIGHTS["stolen_bases_allowed"],
            COMPONENT_WEIGHTS["stolen_bases_allowed"])

        # Fielding %: league avg ≈ .985. .975 ⇒ full penalty; .988+ ⇒ 0.
        if fp is not None:
            penalty = (0.988 - fp) / (0.988 - 0.972) * COMPONENT_WEIGHTS["fielding_pct_penalty"]
            components["fielding_pct_penalty"] = _score_capped(
                penalty, COMPONENT_WEIGHTS["fielding_pct_penalty"])

        # DRS: positive ⇒ no penalty; negative ⇒ scale. -20 ⇒ full weight.
        if d is not None:
            drs_pen = max(0.0, (-d) / 20.0) * COMPONENT_WEIGHTS["drs_penalty"]
            components["drs_penalty"] = _score_capped(
                drs_pen, COMPONENT_WEIGHTS["drs_penalty"])
        elif o is not None:
            # Fallback to OAA scale (-15 ⇒ full).
            oaa_pen = max(0.0, (-o) / 15.0) * COMPONENT_WEIGHTS["drs_penalty"]
            components["drs_penalty"] = _score_capped(
                oaa_pen, COMPONENT_WEIGHTS["drs_penalty"])

        # Reason codes — pregame.
        if epg >= 0.85 or (fp is not None and fp <= 0.978):
            reason_codes.append(RC_POOR_FIELDING_PROFILE)
        if pbg >= 0.20:
            reason_codes.append(RC_PASSED_BALL_PRESSURE)
        if wpg >= 0.50:
            reason_codes.append(RC_WILD_PITCH_PRESSURE)
        if sbg >= 1.0:
            reason_codes.append(RC_STOLEN_BASE_DEFENSIVE_FAILURE)

    score = max(0, min(100, sum(components.values())))
    bucket = _bucket_from_score(score)

    if bucket == BUCKET_HIGH:
        reason_codes.append(RC_DEFENSIVE_MELTDOWN_RISK)

    return {
        "engine_version":           ENGINE_VERSION,
        "defensive_breakdown_score": score,
        "defensive_bucket":         bucket,
        "mode":                     mode,
        "components":               components,
        "reason_codes":             list(dict.fromkeys(reason_codes)),
        "inputs": {
            "innings_played": innings_played,
        },
    }


def classify_combined_explosion_risk(
    *,
    bullpen_era_7d_max:   Optional[float],
    live_traffic_bucket:  Optional[str],
    defensive_bucket:     Optional[str],
    is_under_pick:        bool = True,
    bullpen_high_threshold: float = 5.50,
) -> dict:
    """Compound rule — the strongest warning fires when:

        bullpen vulnerable
        + live_traffic_score HIGH
        + defensive_breakdown_score MEDIUM or HIGH
    """
    reason_codes: list[str] = []
    bp_vuln = (bullpen_era_7d_max or 0.0) > bullpen_high_threshold
    traffic_high = live_traffic_bucket == "HIGH_TRAFFIC"
    defense_alerts = defensive_bucket in (BUCKET_MEDIUM, BUCKET_HIGH)

    if bp_vuln and traffic_high and defense_alerts:
        reason_codes.append(RC_BULLPEN_TRAFFIC_DEFENSE_EXPLOSION_RISK)
        ui_message_es = (
            "El Under está en riesgo real: bullpen vulnerable, tráfico "
            "ofensivo alto y errores defensivos están creando condiciones "
            "para una entrada explosiva."
        )
        verdict = "penalize_under" if is_under_pick else "no_signal"
    else:
        ui_message_es = None
        verdict = "no_signal"

    return {
        "engine_version":      ENGINE_VERSION,
        "verdict":             verdict,
        "reason_codes":        reason_codes,
        "ui_message_es":       ui_message_es,
        "components_present":  {
            "bullpen_vulnerable":      bp_vuln,
            "live_traffic_high":       traffic_high,
            "defensive_breakdown":     defense_alerts,
        },
    }


__all__ = [
    "ENGINE_VERSION",
    "COMPONENT_WEIGHTS",
    "BUCKET_LOW", "BUCKET_MEDIUM", "BUCKET_HIGH",
    "RC_DEFENSIVE_MELTDOWN_RISK",
    "RC_LIVE_ERRORS_RAISE_RUN_RISK",
    "RC_PASSED_BALL_PRESSURE",
    "RC_WILD_PITCH_PRESSURE",
    "RC_STOLEN_BASE_DEFENSIVE_FAILURE",
    "RC_POOR_FIELDING_PROFILE",
    "RC_UNEARNED_RUN_RISK",
    "RC_BULLPEN_TRAFFIC_DEFENSE_EXPLOSION_RISK",
    "ALL_REASON_CODES",
    "compute_defensive_breakdown_score",
    "classify_combined_explosion_risk",
]
