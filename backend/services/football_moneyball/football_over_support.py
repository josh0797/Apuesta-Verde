"""Football Over Support Layer (pure).

Produces a structured score quantifying how much the pregame context
supports each Over market (Over 1.5 / Over 2.5 / 1H Over 0.5 / Team
Total Over). The output is consumed by ``football_market_selection`` as
one signal among several—it never forces an Over pick by itself.

Design principles (NON-NEGOTIABLE):
  * Pure (no IO). Reads only what the match dict carries.
  * Fail-soft. Missing signals degrade the score toward 0 instead of
    raising. Defaults assume an average match.
  * Conservative. Over 2.5 requires **both** strong lambda_total and
    strong first-30-goal presence; otherwise we down-shift to Over 1.5.
  * ``mode`` is always ``observe_only`` until the calibration loop
    promotes it; the orchestrator decides whether to surface the
    suggested market.
  * Reason codes are CANONICAL strings the UI / tests can rely on.

SYMMETRY TODO (Phase F61 — Under Support cross-check):
-------------------------------------------------------
There is NOT currently a ``compute_over_profile_score`` analogue to
``under_market_scan.compute_under_profile_score``. The Under scanner
DOES apply a cross-check vs ``football_over_support`` and
``football_under_support``: when ``football_over_support.score >= 75``
it penalises the Under profile by -15 (with reason codes
``OVER_SUPPORT_CONTRADICTS_UNDER_PROFILE`` +
``OVER_SUPPORT_STRONG_PENALTY_APPLIED``).

When (if ever) a symmetric ``compute_over_profile_score`` /
``over_market_scan`` module is introduced, it MUST apply the mirror
rule against ``football_under_support``:

    under_support_score >= 75  → score -= 15, RC:
        UNDER_SUPPORT_CONTRADICTS_OVER_PROFILE +
        UNDER_SUPPORT_STRONG_PENALTY_APPLIED
    under_support_score >= 60  → score -= 8,  RC:
        UNDER_SUPPORT_CONTRADICTS_OVER_PROFILE
    over_support_score  >= 70  → score += 5,  RC:
        OVER_SUPPORT_CONFIRMS_OVER_PROFILE

Building the Over selector WITHOUT this cross-check is not optional —
it re-introduces the very asymmetry Phase F61 was designed to remove.
This comment exists so the next implementer cannot miss the contract.

Dependencies:
  * ``derived_early_goal`` (per-team early_goal_30 metrics in context)
  * ``statsbomb_features.derive_offense_bucket`` (offense tier)
  * ``injury_intelligence`` (OPTIONAL; only used when present)
  * ``football_goal_pressure_profile`` reason codes (cross-reference)

Inputs read (all optional):
  * ``match['home_team']['context']['recent_fixtures']`` /
    ``seasonal_form.early_goal_profile`` for 0–30 metrics.
  * ``match['match_features']`` or ``match['_statsbomb_features']`` for
    lambda_total / lam_h / lam_a / xG / xGA.
  * ``match['live_stats']`` (only used when present — NEVER blocks
    pregame).
  * ``match['injury_intelligence']`` (optional; only ``TOP_SCORER_OUT``
    / ``PRIMARY_CREATOR_OUT`` / ``KEY_DEFENDER_OUT`` are consumed).
  * ``match['football_totals_model']`` (DC/NB telemetry from Feature 1).
  * ``match['goal_pressure_profile']`` (existing layer).
  * ``match['_form_guard']`` / ``match['_football_quality']``.
"""

from __future__ import annotations

from typing import Any

from ..statsbomb_features import derive_offense_bucket

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
# Markets
MKT_OVER_1_5         = "OVER_1_5"
MKT_OVER_2_5         = "OVER_2_5"
MKT_1H_OVER_0_5      = "1H_OVER_0_5"
MKT_TEAM_TOTAL_OVER  = "TEAM_TOTAL_OVER"
MKT_NONE             = "NONE"

# Score caps
MAX_SCORE = 100
MIN_SCORE = 0

# Lambda gates
LAMBDA_TOTAL_OVER_1_5_GATE = 2.35
LAMBDA_TOTAL_OVER_2_5_GATE = 2.85

# First-30 gates
FIRST_30_PRESENCE_OVER_1_5  = 0.60
FIRST_30_PRESENCE_OVER_2_5  = 0.70
TEAM_SCORED_0_30_GATE       = 0.35
TEAM_CONCEDED_0_30_GATE     = 0.30

# Defensive leak thresholds (xGA / goals_against_avg).
DEF_LEAK_LOW_CAP    = 0.90
DEF_LEAK_HIGH_MIN   = 1.40

# Fragility floor for Over 2.5 acceptance.
OVER_2_5_FRAGILITY_BLOCK_MIN = 70

# Reason codes (canonical, exported).
RC_EARLY_GOAL_30_SUPPORT             = "EARLY_GOAL_30_SUPPORT"
RC_BOTH_TEAMS_SCORE_EARLY            = "BOTH_TEAMS_SCORE_EARLY"
RC_EARLY_CONCEDE_RISK                = "EARLY_CONCEDE_RISK"
RC_FIRST_HALF_GOAL_PROFILE_STRONG    = "FIRST_HALF_GOAL_PROFILE_STRONG"
RC_HIGH_GOAL_PRESSURE_SUPPORTS_OVER  = "HIGH_GOAL_PRESSURE_SUPPORTS_OVER"
RC_DEFENSIVE_LEAK_SUPPORTS_OVER      = "DEFENSIVE_LEAK_SUPPORTS_OVER"
RC_INJURY_DEFENSE_WEAKENED_OVER_SUPPORT = "INJURY_DEFENSE_WEAKENED_OVER_SUPPORT"
RC_CONTROLLED_MATCH_BLOCKS_OVER      = "CONTROLLED_MATCH_BLOCKS_OVER"
RC_LOW_DEPTH_BLOCKS_OVER             = "LOW_DEPTH_BLOCKS_OVER"
RC_TOP_SCORER_OUT_WEAKENS_OVER       = "TOP_SCORER_OUT_WEAKENS_OVER"
RC_LOW_VALUE_OVER_ODDS               = "LOW_VALUE_OVER_ODDS"
RC_OVER_1_5_PROTECTED                = "OVER_1_5_PROTECTED"
RC_OVER_2_5_FRAGILE                  = "OVER_2_5_FRAGILE"
RC_LIVE_OVER_CONFIRMED_BY_PRESSURE   = "LIVE_OVER_CONFIRMED_BY_PRESSURE"
RC_NO_INPUTS_AVAILABLE               = "FOOTBALL_OVER_SUPPORT_NO_INPUTS"
RC_DC_NB_PREFERS_UNDER               = "DC_NB_MODEL_PREFERS_UNDER"


# ─────────────────────────────────────────────────────────────────────
# Helpers (pure)
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _safe_get(d: Any, *path, default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _team_early_30(team_block: dict | None) -> dict:
    """Pull canonical 0–30 metrics from a team block."""
    if not isinstance(team_block, dict):
        return {}
    ctx = team_block.get("context") if isinstance(team_block.get("context"), dict) else team_block
    if not isinstance(ctx, dict):
        return {}
    recent = ctx.get("recent_fixtures") if isinstance(ctx.get("recent_fixtures"), dict) else {}
    seasonal = ctx.get("seasonal_form") if isinstance(ctx.get("seasonal_form"), dict) else {}
    early = seasonal.get("early_goal_profile") if isinstance(seasonal.get("early_goal_profile"), dict) else {}
    return {
        "early_goal_30_pct":          _f(recent.get("early_goal_30_pct") or early.get("early_goal_30_pct")),
        "early_concede_30_pct":       _f(recent.get("early_concede_30_pct") or early.get("early_concede_30_pct")),
        "team_scored_0_30_pct":       _f(recent.get("team_scored_0_30_pct") or early.get("team_scored_0_30_pct")),
        "team_conceded_0_30_pct":     _f(recent.get("team_conceded_0_30_pct") or early.get("team_conceded_0_30_pct")),
        "first_30_goal_presence_pct": _f(recent.get("first_30_goal_presence_pct") or early.get("first_30_goal_presence_pct")),
        "goals_against_avg":          _f(ctx.get("goals_against_avg")),
        "goals_for_avg":              _f(ctx.get("goals_for_avg")),
        "xga":                        _f(recent.get("xga") or seasonal.get("xga")),
    }


def _defensive_leak_score(team_signals: dict) -> tuple[int, list[str]]:
    """Map goals_against_avg / xGA to a 0–100 defensive leak score.

    Returns (score, reason_codes).
      * <= 0.90 → 30 (solid)
      * 0.90 < x ≤ 1.40 → 55 (medium)
      * > 1.40 → 80 (leaky)
    xGA, when present, can shift the score by ±10.
    """
    reasons: list[str] = []
    ga = team_signals.get("goals_against_avg")
    xga = team_signals.get("xga")
    if ga is None and xga is None:
        return 50, reasons  # neutral
    base = 50
    if ga is not None:
        if ga <= DEF_LEAK_LOW_CAP:
            base = 30
        elif ga <= DEF_LEAK_HIGH_MIN:
            base = 55
        else:
            base = 80
            reasons.append(RC_DEFENSIVE_LEAK_SUPPORTS_OVER)
    if xga is not None:
        if xga > 1.40:
            base = min(100, base + 10)
            if RC_DEFENSIVE_LEAK_SUPPORTS_OVER not in reasons:
                reasons.append(RC_DEFENSIVE_LEAK_SUPPORTS_OVER)
        elif xga < 0.90:
            base = max(0, base - 10)
    return base, reasons


def _injury_signals(match: dict) -> tuple[int, int, list[str]]:
    """Return (offensive_penalty, defensive_bonus, reason_codes)."""
    pen, bon = 0, 0
    reasons: list[str] = []
    inj = match.get("injury_intelligence")
    if not isinstance(inj, dict) or not inj.get("available"):
        return pen, bon, reasons
    codes = inj.get("reason_codes") or []
    if not isinstance(codes, list):
        return pen, bon, reasons
    if "TOP_SCORER_OUT" in codes or "PRIMARY_CREATOR_OUT" in codes:
        pen = 12
        reasons.append(RC_TOP_SCORER_OUT_WEAKENS_OVER)
    if "KEY_DEFENDER_OUT" in codes or "GOALKEEPER_OUT" in codes:
        bon = 8
        reasons.append(RC_INJURY_DEFENSE_WEAKENED_OVER_SUPPORT)
    return pen, bon, reasons


def _dc_nb_preference(totals_model: dict | None) -> tuple[int, list[str]]:
    """Read the DC/NB Under 3.5 delta. A strongly POSITIVE delta means
    the model prefers Unders; that subtracts a few points from Over."""
    if not isinstance(totals_model, dict) or not totals_model.get("available"):
        return 0, []
    delta = _f((totals_model.get("under_3_5") or {}).get("delta_pts"))
    if delta is None:
        return 0, []
    if delta >= 3.0:
        return -8, [RC_DC_NB_PREFERS_UNDER]
    return 0, []


# ─────────────────────────────────────────────────────────────────────
# Public — main entry point
# ─────────────────────────────────────────────────────────────────────
def calculate_football_over_support(match: dict | None) -> dict:
    """Build the canonical ``football_over_support`` block. Always
    returns the canonical shape (never raises)."""
    if not isinstance(match, dict):
        return _unavailable("no_match")

    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    home_sig = _team_early_30(home)
    away_sig = _team_early_30(away)

    # Features from statsbomb / match_features (already attached upstream)
    feats = (match.get("match_features")
              or match.get("_statsbomb_features")
              or {})
    if not isinstance(feats, dict):
        feats = {}

    lam_total = _f(feats.get("lambda_total"))
    totals_model = match.get("football_totals_model")

    # If nothing usable AT ALL → unavailable.
    if (
        lam_total is None
        and not any(home_sig.values())
        and not any(away_sig.values())
    ):
        return _unavailable("no_inputs", reasons=[RC_NO_INPUTS_AVAILABLE])

    reasons: list[str] = []

    # ── First-30 combined metrics ──────────────────────────────────────
    presence_h = home_sig.get("first_30_goal_presence_pct") or 0.0
    presence_a = away_sig.get("first_30_goal_presence_pct") or 0.0
    combined_presence = round((presence_h + presence_a) / 2.0, 3) if (presence_h or presence_a) else 0.0

    # Both teams scoring early?
    both_scored_early = (
        (home_sig.get("team_scored_0_30_pct") or 0) >= TEAM_SCORED_0_30_GATE
        and (away_sig.get("team_scored_0_30_pct") or 0) >= TEAM_SCORED_0_30_GATE
    )
    both_concede_early = (
        (home_sig.get("team_conceded_0_30_pct") or 0) >= TEAM_CONCEDED_0_30_GATE
        and (away_sig.get("team_conceded_0_30_pct") or 0) >= TEAM_CONCEDED_0_30_GATE
    )

    # ── Defensive leak (both teams) ────────────────────────────────────
    leak_h, leak_reasons_h = _defensive_leak_score(home_sig)
    leak_a, leak_reasons_a = _defensive_leak_score(away_sig)
    leak_combined = (leak_h + leak_a) // 2
    for r in leak_reasons_h + leak_reasons_a:
        if r not in reasons:
            reasons.append(r)

    # ── Injury signals ─────────────────────────────────────────────────
    inj_penalty, inj_bonus, inj_reasons = _injury_signals(match)
    for r in inj_reasons:
        if r not in reasons:
            reasons.append(r)

    # ── DC/NB tilt ─────────────────────────────────────────────────────
    dc_nb_delta_score, dc_nb_reasons = _dc_nb_preference(totals_model)
    for r in dc_nb_reasons:
        if r not in reasons:
            reasons.append(r)

    # ── Pressure / Controlled match check ──────────────────────────────
    pressure = match.get("goal_pressure_profile") or {}
    pressure_tier = (pressure.get("combined") or {}).get("pressure_tier") if isinstance(pressure, dict) else None
    controlled = pressure_tier == "LOW_PRESSURE" or (
        isinstance(match.get("_form_guard"), dict)
        and match["_form_guard"].get("verdict") == "CONTROLLED"
    )

    # ── Offense bucket ────────────────────────────────────────────────
    offense = derive_offense_bucket(lam_total)

    # ── Live confirmation (optional) ───────────────────────────────────
    live = match.get("live_stats") if isinstance(match.get("live_stats"), dict) else {}
    live_confirmation = False
    if live:
        h_live = live.get("home_stats") or {}
        a_live = live.get("away_stats") or {}
        sot = (h_live.get("shots_on_goal") or 0) + (a_live.get("shots_on_goal") or 0)
        da = (h_live.get("dangerous_attacks") or 0) + (a_live.get("dangerous_attacks") or 0)
        if sot >= 8 or da >= 50:
            live_confirmation = True
            reasons.append(RC_LIVE_OVER_CONFIRMED_BY_PRESSURE)

    # ─────────────────────────────────────────────────────────────────
    # Over 1.5 support score
    # ─────────────────────────────────────────────────────────────────
    o15 = 0
    if lam_total is not None and lam_total >= LAMBDA_TOTAL_OVER_1_5_GATE:
        o15 += 15
    if combined_presence >= FIRST_30_PRESENCE_OVER_1_5:
        o15 += 15
        if RC_EARLY_GOAL_30_SUPPORT not in reasons:
            reasons.append(RC_EARLY_GOAL_30_SUPPORT)
    if both_scored_early:
        o15 += 10
        if RC_BOTH_TEAMS_SCORE_EARLY not in reasons:
            reasons.append(RC_BOTH_TEAMS_SCORE_EARLY)
    if both_concede_early:
        o15 += 10
        if RC_EARLY_CONCEDE_RISK not in reasons:
            reasons.append(RC_EARLY_CONCEDE_RISK)
    if leak_combined >= 70:
        o15 += 10
    if inj_bonus:
        o15 += inj_bonus
    if inj_penalty:
        o15 -= inj_penalty
    if live_confirmation:
        o15 += 5

    # ─────────────────────────────────────────────────────────────────
    # Over 2.5 support score (stricter)
    # ─────────────────────────────────────────────────────────────────
    o25 = 0
    if lam_total is not None and lam_total >= LAMBDA_TOTAL_OVER_2_5_GATE:
        o25 += 20
        if RC_HIGH_GOAL_PRESSURE_SUPPORTS_OVER not in reasons:
            reasons.append(RC_HIGH_GOAL_PRESSURE_SUPPORTS_OVER)
    if combined_presence >= FIRST_30_PRESENCE_OVER_2_5:
        o25 += 15
    if pressure_tier == "HIGH_PRESSURE":
        o25 += 15
        if RC_HIGH_GOAL_PRESSURE_SUPPORTS_OVER not in reasons:
            reasons.append(RC_HIGH_GOAL_PRESSURE_SUPPORTS_OVER)
    if leak_combined >= 70:
        o25 += 10
    if inj_bonus:
        o25 += inj_bonus
    if inj_penalty:
        o25 -= inj_penalty
    o25 += dc_nb_delta_score

    # First-half goal support (mirror of Over 1.5 but uses first_half_goal_pct).
    fhg = 0
    fhg_h = _f(_safe_get(home, "context", "recent_fixtures", "first_half_goal_pct"))
    fhg_a = _f(_safe_get(away, "context", "recent_fixtures", "first_half_goal_pct"))
    if fhg_h is not None and fhg_a is not None:
        avg = (fhg_h + fhg_a) / 2.0
        if avg >= 0.55:
            fhg = 70
            reasons.append(RC_FIRST_HALF_GOAL_PROFILE_STRONG)
        elif avg >= 0.40:
            fhg = 50
        else:
            fhg = 30
    egp = max(0, min(MAX_SCORE, int(round(combined_presence * 100))))

    # ─────────────────────────────────────────────────────────────────
    # Controlled match blocks aggressive Over.
    # ─────────────────────────────────────────────────────────────────
    if controlled:
        o25 = min(o25, 30)  # cap Over 2.5 aggressively
        reasons.append(RC_CONTROLLED_MATCH_BLOCKS_OVER)

    # ─────────────────────────────────────────────────────────────────
    # Fragility score: Over 2.5 picks are fragile when:
    #   * lambda_total is borderline AND combined_presence is low, OR
    #   * DC/NB prefers Under, OR
    #   * controlled match.
    # ─────────────────────────────────────────────────────────────────
    fragility = 30
    if lam_total is not None and lam_total < LAMBDA_TOTAL_OVER_2_5_GATE:
        fragility += 15
    if combined_presence < FIRST_30_PRESENCE_OVER_2_5:
        fragility += 10
    if dc_nb_delta_score < 0:
        fragility += 15
    if controlled:
        fragility += 25
    if inj_penalty:
        fragility += 10
    fragility = max(0, min(100, fragility))

    if fragility >= OVER_2_5_FRAGILITY_BLOCK_MIN:
        reasons.append(RC_OVER_2_5_FRAGILE)

    # Clamp Over 1.5/2.5 scores.
    o15 = max(MIN_SCORE, min(MAX_SCORE, o15))
    o25 = max(MIN_SCORE, min(MAX_SCORE, o25))
    egp = max(MIN_SCORE, min(MAX_SCORE, egp))

    # Recommended Over market.
    recommended_over = MKT_NONE
    if o25 >= 65 and fragility < OVER_2_5_FRAGILITY_BLOCK_MIN and not controlled:
        recommended_over = MKT_OVER_2_5
    elif o15 >= 50:
        recommended_over = MKT_OVER_1_5
        if RC_OVER_1_5_PROTECTED not in reasons:
            reasons.append(RC_OVER_1_5_PROTECTED)
    elif fhg >= 60:
        recommended_over = MKT_1H_OVER_0_5

    summary = _build_summary(
        o15=o15, o25=o25, egp=egp, fhg=fhg, fragility=fragility,
        controlled=controlled, recommended=recommended_over,
        offense=offense,
    )

    return {
        "football_over_support": {
            "available":                       True,
            "mode":                            "observe_only",
            "over_1_5_support_score":          int(o15),
            "over_2_5_support_score":          int(o25),
            "first_half_goal_support_score":   int(fhg),
            "early_goal_pressure_score":       int(egp),
            "home_early_goal_30_pct":          home_sig.get("early_goal_30_pct"),
            "away_early_goal_30_pct":          away_sig.get("early_goal_30_pct"),
            "home_early_concede_30_pct":       home_sig.get("early_concede_30_pct"),
            "away_early_concede_30_pct":       away_sig.get("early_concede_30_pct"),
            "combined_first_30_goal_presence": combined_presence,
            "offense_bucket":                  offense,
            "defensive_leak_score":            leak_combined,
            "recommended_over_market":         recommended_over,
            "fragility_score":                 fragility,
            "reason_codes":                    reasons,
            "source_status": {
                "home_early_goal_profile": bool(any(home_sig.values())),
                "away_early_goal_profile": bool(any(away_sig.values())),
                "injury_intelligence":     bool(isinstance(match.get("injury_intelligence"), dict)
                                                  and match["injury_intelligence"].get("available")),
                "dc_nb_totals_model":      bool(isinstance(totals_model, dict)
                                                  and totals_model.get("available")),
                "live_stats":              bool(live),
            },
            "provenance": {
                "lambda_total_source":     "statsbomb_features" if lam_total is not None else None,
                "early_goal_30_source":    "recent_fixtures+seasonal_form",
                "version":                 "football_over_support.1",
            },
            "summary":  summary,
        }
    }


def _unavailable(reason: str, *, reasons: list[str] | None = None) -> dict:
    return {
        "football_over_support": {
            "available":              False,
            "reason":                 reason,
            "mode":                   "observe_only",
            "recommended_over_market": MKT_NONE,
            "reason_codes":           list(reasons or []),
            "summary":                "Sin señales suficientes para soporte de Over.",
        }
    }


def _build_summary(*, o15, o25, egp, fhg, fragility, controlled, recommended, offense) -> str:
    parts: list[str] = []
    parts.append(f"Over 1.5 support: {o15}/100.")
    parts.append(f"Over 2.5 support: {o25}/100.")
    parts.append(f"Presencia gol 0–30: {egp}/100.")
    parts.append(f"Perfil 1T: {fhg}/100.")
    parts.append(f"Fragilidad Over 2.5: {fragility}/100.")
    if controlled:
        parts.append("Partido controlado detectado: se desaconseja Over 2.5.")
    if recommended == MKT_OVER_2_5:
        parts.append("Soporte fuerte para Over 2.5.")
    elif recommended == MKT_OVER_1_5:
        parts.append("Over 1.5 como mercado protegido.")
    elif recommended == MKT_1H_OVER_0_5:
        parts.append("Perfil de gol en 1T sostiene Over 0.5 de primer tiempo.")
    else:
        parts.append("Sin soporte suficiente para Over agresivo.")
    if offense:
        parts.append(f"Bucket ofensivo: {offense}.")
    return " ".join(parts)


__all__ = [
    "calculate_football_over_support",
    # Markets
    "MKT_OVER_1_5", "MKT_OVER_2_5", "MKT_1H_OVER_0_5",
    "MKT_TEAM_TOTAL_OVER", "MKT_NONE",
    # Reason codes
    "RC_EARLY_GOAL_30_SUPPORT", "RC_BOTH_TEAMS_SCORE_EARLY",
    "RC_EARLY_CONCEDE_RISK", "RC_FIRST_HALF_GOAL_PROFILE_STRONG",
    "RC_HIGH_GOAL_PRESSURE_SUPPORTS_OVER",
    "RC_DEFENSIVE_LEAK_SUPPORTS_OVER",
    "RC_INJURY_DEFENSE_WEAKENED_OVER_SUPPORT",
    "RC_CONTROLLED_MATCH_BLOCKS_OVER",
    "RC_LOW_DEPTH_BLOCKS_OVER",
    "RC_TOP_SCORER_OUT_WEAKENS_OVER",
    "RC_LOW_VALUE_OVER_ODDS",
    "RC_OVER_1_5_PROTECTED", "RC_OVER_2_5_FRAGILE",
    "RC_LIVE_OVER_CONFIRMED_BY_PRESSURE",
    "RC_NO_INPUTS_AVAILABLE",
    "RC_DC_NB_PREFERS_UNDER",
]
