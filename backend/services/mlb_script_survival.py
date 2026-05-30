"""MLB V5 — Script Survival & Fragility Model.

This module answers a question the engine could not answer before:

    "How likely is the original pregame script to SURVIVE all 9 innings?"

It produces three new metrics:

  1. ``calculate_script_survival_score(...)`` — 0-100
        100 = script highly likely to survive
          0 = script highly likely to break
     Sub-factors: starting pitchers, bullpen quality + fatigue,
     offensive variance, environment, historical run variance.

  2. ``calculate_fragility_score(...)`` — 0-100
        0 = extremely stable
      100 = extremely fragile
     NOT a simple ``100 - survival``: this score weights *near-line*
     proximity, lineup volatility, and starter/bullpen blowup risk
     independently of survival, so the two complement each other.

  3. ``classify_script_stability(survival, fragility)`` → one of:
        ELITE_STABLE | STABLE | MODERATELY_STABLE | FRAGILE | HIGHLY_FRAGILE

  4. ``build_script_survival_payload(...)`` — convenience aggregator that
     bundles the three above for direct UI consumption.

The module is *pure* (no IO, no DB). It does not change the existing
expected_runs model, the V3 confidence breakdown, the buckets, the
router, or Moneyball.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("mlb_script_survival")


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════════════════════
# 1. SCRIPT SURVIVAL SCORE
# ════════════════════════════════════════════════════════════════════════════
def calculate_script_survival_score(
    scoring_ctx: Optional[dict] = None,
    v2_payload:  Optional[dict] = None,
    *,
    hist_profile: Optional[dict] = None,
    volatility_home: Optional[dict] = None,
    volatility_away: Optional[dict] = None,
) -> dict:
    """Compute the Script Survival Score (0-100).

    Returns
    -------
    {
        "score":      float (0-100, rounded 1dp),
        "components": {pitchers, bullpen, offense, environment, historical},
        "weights":    {pitchers, bullpen, offense, environment, historical},
        "rationale":  list[str],
    }
    """
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}
    hist_profile = hist_profile or scoring_ctx.get("hist_profile") or {}

    # ── Sub-factor: Starting Pitchers ───────────────────────────────────
    h_q = _f((scoring_ctx.get("home_pitcher_quality") or {}).get("score", 50), 50)
    a_q = _f((scoring_ctx.get("away_pitcher_quality") or {}).get("score", 50), 50)
    pitcher_quality = (h_q + a_q) / 2.0

    # Volatility penalty (per-starter) — uses precomputed if available,
    # otherwise computes lazily.
    if volatility_home is None or volatility_away is None:
        try:
            from .mlb_live_intelligence import pitcher_volatility_score
            volatility_home = volatility_home or pitcher_volatility_score(
                scoring_ctx.get("home_pitcher_stats") or {})
            volatility_away = volatility_away or pitcher_volatility_score(
                scoring_ctx.get("away_pitcher_stats") or {})
        except Exception:
            volatility_home = volatility_home or {"score": 25, "penalty": 0, "level": "LOW"}
            volatility_away = volatility_away or {"score": 25, "penalty": 0, "level": "LOW"}
    vol_avg = (
        _f(volatility_home.get("score"), 25) + _f(volatility_away.get("score"), 25)
    ) / 2.0
    # pitcher_survival = quality baseline minus volatility EXCESS (above 25 baseline).
    # When both pitchers are LOW volatility, no penalty is applied — quality
    # alone drives the survival contribution.
    vol_excess = max(0.0, vol_avg - 25.0)
    pitcher_survival = max(0.0, min(100.0, pitcher_quality + 6.0 - vol_excess * 0.55))

    # ── Sub-factor: Bullpen ─────────────────────────────────────────────
    bp_score = _f((scoring_ctx.get("bullpen") or {}).get("score", 60), 60)
    bp_fatigue = _f((scoring_ctx.get("bullpen") or {}).get("fatigue_score", 30), 30)
    fav_era_7d = _f(scoring_ctx.get("favorite_bullpen_era_7d"), 4.10)
    und_era_7d = _f(scoring_ctx.get("underdog_bullpen_era_7d"), 4.10)
    worst_era_7d = max(fav_era_7d, und_era_7d)
    # ERA-7d ⇒ stability proxy.  3.20 → ~80; 4.50 → ~45; 5.50 → ~25
    era_stability = max(0.0, min(100.0, 100.0 - (worst_era_7d - 3.20) * 22.0))
    bullpen_survival = (bp_score * 0.45 + era_stability * 0.40
                         + max(0.0, 100.0 - bp_fatigue) * 0.15)

    # Blown-save / inherited-runners (optional, fall back to neutral 50).
    blown_save_rate = _f((scoring_ctx.get("bullpen") or {}).get("blown_save_rate"), 0.0)
    if blown_save_rate > 0:
        # 0% blown ⇒ +10; 30% blown ⇒ -10
        bullpen_survival += (15.0 - blown_save_rate * 100.0) * 0.4
    bullpen_survival = max(0.0, min(100.0, bullpen_survival))

    # ── Sub-factor: Offense (lineup consistency / variance) ─────────────
    off_h = _f((scoring_ctx.get("offense_home") or {}).get("score", 50), 50)
    off_a = _f((scoring_ctx.get("offense_away") or {}).get("score", 50), 50)
    off_var = _f((scoring_ctx.get("offense_variance") or scoring_ctx.get("team_total_variance")), 30)
    # High offense variance hurts survival (boom-or-bust offenses crack Unders).
    offense_consistency = 100.0 - off_var          # 30 var ⇒ 70 consistency
    offense_survival = max(0.0, min(100.0, offense_consistency - max(0, ((off_h + off_a)/2.0) - 60) * 0.4))

    # ── Sub-factor: Environment (park + weather) ────────────────────────
    park = scoring_ctx.get("park") or {}
    park_mult = _f(park.get("park_runs_mult"), 1.0)
    weather = _f(park.get("weather_score"), 50)
    # Pitcher-friendly park (mult < 1.0) AND benign weather ⇒ high survival.
    park_survival = max(0.0, min(100.0, 70.0 - (park_mult - 1.0) * 65.0))
    if 35 <= weather <= 65:
        # benign weather adds 5 pts
        park_survival += 5
    elif weather >= 75 or weather <= 25:
        # extreme weather (boost to scoring or cold) — drop a bit
        park_survival -= 6
    park_survival = max(0.0, min(100.0, park_survival))

    # ── Sub-factor: Historical (last 15 games run variance) ─────────────
    hist_total_var = _f((hist_profile.get("home") or {}).get("total_runs_variance"), None)
    if hist_total_var is None or hist_total_var == 0:
        hist_total_var = _f((hist_profile.get("away") or {}).get("total_runs_variance"), 3.5)
    # var around 2.5 ⇒ 80; var around 6 ⇒ 30
    hist_survival = max(0.0, min(100.0, 100.0 - (hist_total_var - 2.5) * 14.0))
    # Default neutral when no history.
    if not hist_profile.get("available"):
        hist_survival = 55.0

    # ── Weighted aggregation ────────────────────────────────────────────
    weights = {
        "pitchers":    0.36,
        "bullpen":     0.26,
        "offense":     0.16,
        "environment": 0.12,
        "historical":  0.10,
    }
    components = {
        "pitchers":    round(pitcher_survival, 1),
        "bullpen":     round(bullpen_survival, 1),
        "offense":     round(offense_survival, 1),
        "environment": round(park_survival, 1),
        "historical":  round(hist_survival, 1),
    }
    score = sum(components[k] * weights[k] for k in weights)
    score = max(0.0, min(100.0, score))

    rationale: list[str] = []
    if pitcher_survival >= 70:
        rationale.append("Abridores estables y de calidad sostenida.")
    elif pitcher_survival <= 45:
        rationale.append("Abridores con perfil volátil o calidad limitada.")
    if bullpen_survival >= 70:
        rationale.append("Bullpens sólidos (ERA 7d controlado).")
    elif bullpen_survival <= 45:
        rationale.append(f"Bullpens vulnerables (peor ERA 7d {worst_era_7d:.2f}).")
    if park_survival >= 70:
        rationale.append("Parque/clima respaldan el script bajo scoring.")
    elif park_survival <= 45:
        rationale.append("Parque/clima introducen volatilidad de carreras.")
    if hist_survival >= 70:
        rationale.append("Histórico (últimos 15) con varianza baja.")
    elif hist_survival <= 45:
        rationale.append("Histórico (últimos 15) con varianza alta.")

    return {
        "score":      round(score, 1),
        "components": components,
        "weights":    {k: round(v * 100, 0) for k, v in weights.items()},
        "rationale":  rationale,
        "volatility": {
            "home": volatility_home,
            "away": volatility_away,
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 2. FRAGILITY SCORE
# ════════════════════════════════════════════════════════════════════════════
def calculate_fragility_score(
    scoring_ctx: Optional[dict] = None,
    v2_payload:  Optional[dict] = None,
    *,
    survival_payload: Optional[dict] = None,
    hist_profile:     Optional[dict] = None,
) -> dict:
    """Compute the Fragility Score (0-100). Higher = more fragile.

    NOT just (100 - survival): this metric explicitly weights *near-line*
    proximity (how close the projected runs are to the line) and the
    starter/bullpen blowup risk, which are amplifiers of fragility.
    """
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}
    survival_payload = survival_payload or calculate_script_survival_score(
        scoring_ctx, v2_payload, hist_profile=hist_profile)

    survival = _f(survival_payload.get("score"), 50)
    base = max(0.0, 100.0 - survival)   # baseline inverse

    # Near-line proximity — if expected_runs is within 1.0 of the line,
    # the Under/Over is statistically more fragile.
    er = _f(v2_payload.get("expectedRuns") or v2_payload.get("expected_runs"), 0)
    line = _f(v2_payload.get("smartTotalsLine") or v2_payload.get("recommendedLineNumber"), 0)
    if line <= 0:
        # try to parse from recommendedLine text
        import re
        rl = str(v2_payload.get("recommendedLine") or "")
        m = re.search(r"(\d+(?:\.\d+)?)", rl)
        if m:
            try:
                line = float(m.group(1))
            except (TypeError, ValueError):
                line = 0.0
    near_line_penalty = 0.0
    if er > 0 and line > 0:
        gap = line - er
        if 0 <= gap < 1.0:
            near_line_penalty = (1.0 - gap) * 18.0   # gap 0 ⇒ +18
        elif gap < 0:
            near_line_penalty = 22.0                  # ER above line — very fragile
        elif gap >= 2.5:
            near_line_penalty = -8.0                  # comfortable buffer ⇒ less fragile

    # Blowup risk from per-starter volatility.
    vol_h = (survival_payload.get("volatility") or {}).get("home") or {}
    vol_a = (survival_payload.get("volatility") or {}).get("away") or {}
    starter_blowup_risk = max(_f(vol_h.get("penalty")), _f(vol_a.get("penalty")))
    # penalty is already a small 0-15 number; scale to fragility.
    blowup_amplifier = starter_blowup_risk * 0.9

    # Bullpen ERA-7d amplifier.
    worst_era_7d = max(
        _f(scoring_ctx.get("favorite_bullpen_era_7d")),
        _f(scoring_ctx.get("underdog_bullpen_era_7d")),
    )
    bullpen_amplifier = 0.0
    if worst_era_7d >= 5.0:
        bullpen_amplifier = 12.0
    elif worst_era_7d >= 4.5:
        bullpen_amplifier = 7.0
    elif worst_era_7d >= 4.0:
        bullpen_amplifier = 3.0

    fragility = base + near_line_penalty + blowup_amplifier + bullpen_amplifier
    fragility = max(0.0, min(100.0, fragility))

    drivers: list[str] = []
    if near_line_penalty >= 10:
        drivers.append(f"ER ({er:.1f}) muy cerca de la línea ({line:.1f}).")
    elif near_line_penalty <= -5:
        drivers.append(f"Buffer cómodo: línea {line:.1f} bien sobre ER {er:.1f}.")
    if blowup_amplifier >= 8:
        drivers.append("Riesgo de blowup en abridores (alta volatilidad).")
    if bullpen_amplifier >= 7:
        drivers.append(f"Bullpen 7d vulnerable (ERA {worst_era_7d:.2f}).")

    return {
        "score":     round(fragility, 1),
        "drivers":   drivers,
        "base_from_survival": round(base, 1),
        "near_line_penalty":  round(near_line_penalty, 1),
        "blowup_amplifier":   round(blowup_amplifier, 1),
        "bullpen_amplifier":  round(bullpen_amplifier, 1),
    }


# ════════════════════════════════════════════════════════════════════════════
# 3. SCRIPT STABILITY CLASSIFICATION
# ════════════════════════════════════════════════════════════════════════════
STABILITY_LEVELS = ("ELITE_STABLE", "STABLE", "MODERATELY_STABLE",
                    "FRAGILE", "HIGHLY_FRAGILE")

STABILITY_LABELS_ES = {
    "ELITE_STABLE":     "Elite estable",
    "STABLE":           "Estable",
    "MODERATELY_STABLE": "Moderadamente estable",
    "FRAGILE":          "Frágil",
    "HIGHLY_FRAGILE":   "Altamente frágil",
}

STABILITY_TONES = {
    "ELITE_STABLE":      "emerald",
    "STABLE":            "emerald",
    "MODERATELY_STABLE": "sky",
    "FRAGILE":           "amber",
    "HIGHLY_FRAGILE":    "rose",
}


def classify_script_stability(survival: float, fragility: float) -> dict:
    """Classify into ELITE_STABLE / STABLE / MODERATELY_STABLE / FRAGILE /
    HIGHLY_FRAGILE based on the survival & fragility scores."""
    s = max(0.0, min(100.0, _f(survival)))
    f = max(0.0, min(100.0, _f(fragility)))

    if s >= 85 and f <= 15:
        code = "ELITE_STABLE"
    elif s >= 75 and f <= 25:
        code = "STABLE"
    elif s >= 60 and f <= 40:
        code = "MODERATELY_STABLE"
    elif s >= 45 and f <= 60:
        code = "FRAGILE"
    else:
        code = "HIGHLY_FRAGILE"

    return {
        "code":         code,
        "label_es":     STABILITY_LABELS_ES[code],
        "tone":         STABILITY_TONES[code],
        "survival":     round(s, 1),
        "fragility":    round(f, 1),
    }


# ════════════════════════════════════════════════════════════════════════════
# 4. CONVENIENCE AGGREGATOR
# ════════════════════════════════════════════════════════════════════════════
def build_script_survival_payload(
    scoring_ctx: Optional[dict] = None,
    v2_payload:  Optional[dict] = None,
    *,
    hist_profile:     Optional[dict] = None,
    volatility_home:  Optional[dict] = None,
    volatility_away:  Optional[dict] = None,
) -> dict:
    """Bundle survival + fragility + stability classification.

    Returns
    -------
    {
        "version":            5,
        "survival":           {... full payload from calculate_script_survival_score ...},
        "fragility":          {... full payload from calculate_fragility_score ...},
        "stability":          {... output of classify_script_stability ...},
        "confidence_contribution": float,   # signed delta (+ or -) for V3
        "reference_profile":  bool,          # True for benchmark-tagged profiles
        "narrative_es":       str,
    }
    """
    survival = calculate_script_survival_score(
        scoring_ctx, v2_payload,
        hist_profile=hist_profile,
        volatility_home=volatility_home,
        volatility_away=volatility_away,
    )
    fragility = calculate_fragility_score(
        scoring_ctx, v2_payload,
        survival_payload=survival,
        hist_profile=hist_profile,
    )
    stability = classify_script_stability(survival["score"], fragility["score"])

    # ── Confidence contribution for V3 confidence_breakdown ─────────────
    # Maps survival score onto a signed contribution:
    #   survival 92 ⇒ +15
    #   survival 75 ⇒  +7
    #   survival 50 ⇒  0
    #   survival 30 ⇒ -8
    s = survival["score"]
    if s >= 90:
        contrib = 15.0
    elif s >= 80:
        contrib = 10.0
    elif s >= 70:
        contrib = 6.0
    elif s >= 55:
        contrib = 2.0
    elif s >= 40:
        contrib = -4.0
    elif s >= 25:
        contrib = -8.0
    else:
        contrib = -12.0

    # Reference Stable Under Profile tagging (Phillies @ Dodgers benchmark):
    # - Expected Runs <= 6.5
    # - Survival >= 85
    # - Fragility <= 15
    # - Pitcher quality >= 65
    # - park is pitcher-friendly or neutral
    er = _f((v2_payload or {}).get("expectedRuns") or (v2_payload or {}).get("expected_runs"))
    pitcher_q = (
        _f(((scoring_ctx or {}).get("home_pitcher_quality") or {}).get("score", 50), 50)
        + _f(((scoring_ctx or {}).get("away_pitcher_quality") or {}).get("score", 50), 50)
    ) / 2.0
    park_mult = _f(((scoring_ctx or {}).get("park") or {}).get("park_runs_mult"), 1.0)
    reference_profile = (
        er and er <= 6.5
        and survival["score"] >= 85
        and fragility["score"] <= 15
        and pitcher_q >= 65
        and park_mult <= 1.02
    )

    narrative_parts: list[str] = []
    narrative_parts.append(
        f"Script Survival {survival['score']:.0f}/100 · Fragilidad {fragility['score']:.0f}/100 · "
        f"{stability['label_es']}."
    )
    if reference_profile:
        narrative_parts.append("Benchmark estable tipo Phillies@Dodgers — Under defendible.")
    if stability["code"] in ("FRAGILE", "HIGHLY_FRAGILE"):
        narrative_parts.append(
            "Script frágil: tesis basada en ER puede romperse en innings 6-9."
        )

    return {
        "version":                 5,
        "survival":                survival,
        "fragility":               fragility,
        "stability":               stability,
        "confidence_contribution": round(contrib, 1),
        "reference_profile":       bool(reference_profile),
        "narrative_es":            " ".join(narrative_parts),
    }


__all__ = [
    "calculate_script_survival_score",
    "calculate_fragility_score",
    "classify_script_stability",
    "build_script_survival_payload",
    "STABILITY_LEVELS",
    "STABILITY_LABELS_ES",
]
