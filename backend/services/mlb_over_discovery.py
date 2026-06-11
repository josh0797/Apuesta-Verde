"""MLB V6 — Over Discovery Engine & Market Competition.

The pregame engine evolved heavily around Under detection (Script
Survival, Fragility, Bullpen-aware Under, Cashout). This module
re-balances the engine by *actively* searching for OVER opportunities
and forcing a market-agnostic competition where the highest structural
edge wins — not the default Under.

Public surface (all pure functions, no IO):

  1. ``calculate_offensive_explosion_score(scoring_ctx, v2_payload)``
     → 0-100 (100 = extreme over profile).

  2. ``classify_offensive_script(explosion_score, scoring_ctx)``
     → OFFENSIVE_EXPLOSION | HIGH_SCORING | ABOVE_AVERAGE_SCORING
     | NEUTRAL | LOW_SCORING | PITCHERS_DUEL

  3. ``calculate_over_survival_score(scoring_ctx, v2_payload)``
     → 0-100 (probability the offensive script survives all 9 innings).

  4. ``evaluate_over_markets(scoring_ctx, v2_payload, *, over_lines)``
     → list of {market, line, edge, confidence, fragility, script_survival,
                offensive_explosion_score, category}.

  5. ``over_discovery_engine(scoring_ctx, v2_payload, *, over_lines)``
     → high-level result with proposed_market ∈
        {OVER_FULL_GAME | OVER_F5 | TEAM_TOTAL_OVER | YRFI |
         OFFENSIVE_EXPLOSION_SCRIPT | NO_OVER_EDGE}.

  6. ``market_competition(under_candidate, over_candidate, *, current)``
     → winner dict + decision rationale (used by orchestrator to swap
     Under → Over when the offensive edge dominates).

  7. ``daily_market_audit(picks)`` → audit report dict with histograms,
     bias detector, diversity score (Phases 8-9).

The module never modifies the Expected Runs engine, Script Survival
engine, Fragility engine, Bullpen selector or Cashout engine — it only
proposes a (possibly different) market selection.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger("mlb_over_discovery")


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _extract_line(s: Any) -> Optional[float]:
    if s is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(s))
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


# ════════════════════════════════════════════════════════════════════════════
# 1. OFFENSIVE EXPLOSION SCORE
# ════════════════════════════════════════════════════════════════════════════
def calculate_offensive_explosion_score(
    scoring_ctx: Optional[dict] = None,
    v2_payload:  Optional[dict] = None,
) -> dict:
    """Score how likely the game is to "explode" offensively (0-100).

    100 = extreme over profile (Coors-light park + wind out + weak BPs
    + hot lineups + soft starters).
    0   = strong under profile (elite pitchers + cold weather + pitcher park).

    Sub-factors weighted: Lineups 28 % · Pitchers 26 % · Bullpens 22 % ·
    Park 14 % · Weather 10 %.
    """
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}

    off_h = _f((scoring_ctx.get("offense_home") or {}).get("score", 50), 50)
    off_a = _f((scoring_ctx.get("offense_away") or {}).get("score", 50), 50)
    off_recent_h = _f((scoring_ctx.get("offense_home") or {}).get("last7_score"), off_h)
    off_recent_a = _f((scoring_ctx.get("offense_away") or {}).get("last7_score"), off_a)
    ops_avg = (
        _f((scoring_ctx.get("offense_home") or {}).get("team_ops"))
        + _f((scoring_ctx.get("offense_away") or {}).get("team_ops"))
    ) / 2.0
    iso_avg = (
        _f((scoring_ctx.get("offense_home") or {}).get("team_iso"))
        + _f((scoring_ctx.get("offense_away") or {}).get("team_iso"))
    ) / 2.0
    hr_per_g = (
        _f((scoring_ctx.get("offense_home") or {}).get("hr_per_game"))
        + _f((scoring_ctx.get("offense_away") or {}).get("hr_per_game"))
    ) / 2.0

    # Lineup contribution (0-100). Recent form weighted more than season.
    lineup_raw = (off_recent_h + off_recent_a) / 2.0 * 0.6 + (off_h + off_a) / 2.0 * 0.4
    # OPS / ISO / HR bonuses (rough thresholds).
    if ops_avg >= 0.770:
        lineup_raw += 10
    elif ops_avg >= 0.730:
        lineup_raw += 5
    if iso_avg >= 0.170:
        lineup_raw += 5
    if hr_per_g >= 1.40:
        lineup_raw += 5
    lineup_raw = max(0.0, min(100.0, lineup_raw))

    # Pitchers — INVERTED for offense (weak pitchers = high explosion potential).
    h_q = _f((scoring_ctx.get("home_pitcher_quality") or {}).get("score", 50), 50)
    a_q = _f((scoring_ctx.get("away_pitcher_quality") or {}).get("score", 50), 50)
    pitcher_avg_quality = (h_q + a_q) / 2.0
    # 50 quality ⇒ 50 explosion; 30 quality ⇒ 75 explosion; 75 quality ⇒ 25 explosion.
    pitcher_explosion = max(0.0, min(100.0, 100.0 - pitcher_avg_quality))
    # HR/9 + Hard Hit% + Barrel% bonuses.
    hp = scoring_ctx.get("home_pitcher_stats") or {}
    ap = scoring_ctx.get("away_pitcher_stats") or {}
    hr_per_9_avg = (_f(hp.get("hr_per_9")) + _f(ap.get("hr_per_9"))) / 2.0
    hard_hit_avg = (_f(hp.get("hard_hit_pct")) + _f(ap.get("hard_hit_pct"))) / 2.0
    if hard_hit_avg and hard_hit_avg < 1.5:
        hard_hit_avg *= 100.0
    if hr_per_9_avg >= 1.40:
        pitcher_explosion += 12
    elif hr_per_9_avg >= 1.15:
        pitcher_explosion += 6
    if hard_hit_avg >= 40:
        pitcher_explosion += 10
    elif hard_hit_avg >= 36:
        pitcher_explosion += 5
    pitcher_explosion = max(0.0, min(100.0, pitcher_explosion))

    # Bullpens — high ERA last 7d ⇒ high explosion risk for late innings.
    fav_era_7d = _f(scoring_ctx.get("favorite_bullpen_era_7d"))
    und_era_7d = _f(scoring_ctx.get("underdog_bullpen_era_7d"))
    worst_bp = max(fav_era_7d, und_era_7d)
    bp_explosion = 50.0
    if worst_bp >= 5.20:
        bp_explosion = 90
    elif worst_bp >= 4.75:
        bp_explosion = 80
    elif worst_bp >= 4.30:
        bp_explosion = 65
    elif worst_bp >= 3.80:
        bp_explosion = 50
    elif worst_bp > 0:
        bp_explosion = 35
    bp_fatigue = _f((scoring_ctx.get("bullpen") or {}).get("fatigue_score", 30), 30)
    if bp_fatigue >= 65:
        bp_explosion += 8
    bp_explosion = max(0.0, min(100.0, bp_explosion))

    # Park — hitter-friendly multiplier raises explosion.
    park = scoring_ctx.get("park") or {}
    park_mult = _f(park.get("park_runs_mult"), 1.0)
    park_explosion = max(0.0, min(100.0, 50.0 + (park_mult - 1.0) * 70.0))

    # Weather — hot temp + wind out raises explosion.
    weather_score = _f(park.get("weather_score"), 50)
    # 50 = neutral; ≥75 ⇒ Over-friendly (hot/wind out); ≤25 ⇒ Under-friendly.
    weather_explosion = max(0.0, min(100.0, weather_score))

    weights = {
        "lineups":  0.28,
        "pitchers": 0.26,
        "bullpens": 0.22,
        "park":     0.14,
        "weather":  0.10,
    }
    components = {
        "lineups":  round(lineup_raw,      1),
        "pitchers": round(pitcher_explosion, 1),
        "bullpens": round(bp_explosion,    1),
        "park":     round(park_explosion,  1),
        "weather":  round(weather_explosion, 1),
    }
    score = sum(components[k] * weights[k] for k in weights)
    score = max(0.0, min(100.0, score))

    drivers: list[str] = []
    if lineup_raw >= 65:
        drivers.append(f"Lineups en forma (OPS combinado ≈ {ops_avg:.3f}).")
    if pitcher_avg_quality <= 45:
        drivers.append("Abridores con calidad por debajo del promedio.")
    if hr_per_9_avg >= 1.30:
        drivers.append(f"HR/9 elevado de abridores ({hr_per_9_avg:.2f}).")
    if worst_bp >= 4.75:
        drivers.append(f"Bullpen vulnerable (peor ERA7d {worst_bp:.2f}).")
    if park_mult >= 1.06:
        drivers.append(f"Parque hitter-friendly (×{park_mult:.2f}).")
    if weather_score >= 70:
        drivers.append("Clima/viento a favor del Over.")
    if not drivers:
        drivers.append("Sin drivers ofensivos claros.")

    return {
        "score":      round(score, 1),
        "components": components,
        "weights":    {k: round(v * 100, 0) for k, v in weights.items()},
        "drivers":    drivers[:6],
        "raw_inputs": {
            "ops_avg":            round(ops_avg, 3),
            "iso_avg":            round(iso_avg, 3),
            "hr_per_g":           round(hr_per_g, 2),
            "hr_per_9":           round(hr_per_9_avg, 2),
            "hard_hit_pct":       round(hard_hit_avg, 1),
            "worst_bullpen_era7": round(worst_bp, 2),
            "park_runs_mult":     round(park_mult, 3),
            "weather_score":      round(weather_score, 1),
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 2. OFFENSIVE SCRIPT CLASSIFICATION
# ════════════════════════════════════════════════════════════════════════════
OFFENSIVE_SCRIPTS = (
    "OFFENSIVE_EXPLOSION",
    "HIGH_SCORING",
    "ABOVE_AVERAGE_SCORING",
    "NEUTRAL",
    "LOW_SCORING",
    "PITCHERS_DUEL",
)

OFFENSIVE_SCRIPT_LABELS_ES = {
    "OFFENSIVE_EXPLOSION":  "Explosión ofensiva",
    "HIGH_SCORING":         "Alto scoring",
    "ABOVE_AVERAGE_SCORING": "Sobre el promedio",
    "NEUTRAL":              "Neutral",
    "LOW_SCORING":          "Bajo scoring",
    "PITCHERS_DUEL":        "Duelo de pitchers",
}

OFFENSIVE_SCRIPT_TONES = {
    "OFFENSIVE_EXPLOSION":  "rose",
    "HIGH_SCORING":         "amber",
    "ABOVE_AVERAGE_SCORING": "sky",
    "NEUTRAL":              "slate",
    "LOW_SCORING":          "emerald",
    "PITCHERS_DUEL":        "emerald",
}


def classify_offensive_script(explosion_score: float,
                               scoring_ctx: Optional[dict] = None) -> dict:
    """Convert the Offensive Explosion Score into a categorical script."""
    s = max(0.0, min(100.0, _f(explosion_score)))
    if s >= 80:
        code = "OFFENSIVE_EXPLOSION"
    elif s >= 65:
        code = "HIGH_SCORING"
    elif s >= 55:
        code = "ABOVE_AVERAGE_SCORING"
    elif s >= 45:
        code = "NEUTRAL"
    elif s >= 30:
        code = "LOW_SCORING"
    else:
        code = "PITCHERS_DUEL"
    return {
        "code":     code,
        "label_es": OFFENSIVE_SCRIPT_LABELS_ES[code],
        "tone":     OFFENSIVE_SCRIPT_TONES[code],
        "score":    round(s, 1),
    }


# ════════════════════════════════════════════════════════════════════════════
# 3. OVER SURVIVAL SCORE
# ════════════════════════════════════════════════════════════════════════════
def calculate_over_survival_score(
    scoring_ctx: Optional[dict] = None,
    v2_payload:  Optional[dict] = None,
    *,
    explosion_payload: Optional[dict] = None,
) -> dict:
    """Estimate the probability that an offensive script survives 9 innings.

    Symmetric to V5 Script Survival but for Over picks.
    """
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}
    explosion_payload = explosion_payload or calculate_offensive_explosion_score(
        scoring_ctx, v2_payload)
    expl = explosion_payload.get("score", 50)

    # Bullpens — vulnerable bullpens help an Over hold.
    fav_era_7d = _f(scoring_ctx.get("favorite_bullpen_era_7d"))
    und_era_7d = _f(scoring_ctx.get("underdog_bullpen_era_7d"))
    worst_bp = max(fav_era_7d, und_era_7d)
    if worst_bp >= 4.75:
        bp_contrib = 25
    elif worst_bp >= 4.30:
        bp_contrib = 18
    elif worst_bp >= 3.80:
        bp_contrib = 10
    else:
        bp_contrib = 0

    # Park amplifier.
    park_mult = _f((scoring_ctx.get("park") or {}).get("park_runs_mult"), 1.0)
    park_contrib = max(-15, min(20, (park_mult - 1.0) * 80))

    # Weather amplifier.
    weather = _f((scoring_ctx.get("park") or {}).get("weather_score"), 50)
    if weather >= 75:
        wx_contrib = 12
    elif weather >= 60:
        wx_contrib = 6
    elif weather <= 30:
        wx_contrib = -8
    else:
        wx_contrib = 0

    # Lineup depth (avg score) — top-to-bottom lineups sustain offense.
    off_h = _f((scoring_ctx.get("offense_home") or {}).get("score", 50), 50)
    off_a = _f((scoring_ctx.get("offense_away") or {}).get("score", 50), 50)
    lineup_depth = ((off_h + off_a) / 2.0 - 50) * 0.5  # +/-25 range

    base = expl * 0.65 + 20  # 50 expl ⇒ ~52.5 base; 80 expl ⇒ ~72 base
    survival = base + bp_contrib + park_contrib + wx_contrib + lineup_depth
    survival = max(0.0, min(100.0, survival))

    drivers: list[str] = []
    if bp_contrib >= 15:
        drivers.append("Bullpens vulnerables sostienen el Over.")
    if park_contrib >= 8:
        drivers.append("Parque hitter-friendly mantiene el ritmo de carreras.")
    if wx_contrib >= 6:
        drivers.append("Clima caliente/viento a favor sostienen el Over.")
    elif wx_contrib <= -5:
        drivers.append("Clima frío puede frenar el ritmo ofensivo.")
    if lineup_depth >= 5:
        drivers.append("Lineups profundos: 9 turnos productivos.")

    return {
        "score":   round(survival, 1),
        "drivers": drivers,
        "components": {
            "explosion_base":  round(expl * 0.65 + 20, 1),
            "bullpens":        bp_contrib,
            "park":            round(park_contrib, 1),
            "weather":         wx_contrib,
            "lineup_depth":    round(lineup_depth, 1),
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 4. OVER MARKET EVALUATION
# ════════════════════════════════════════════════════════════════════════════
def evaluate_over_markets(
    scoring_ctx: Optional[dict] = None,
    v2_payload:  Optional[dict] = None,
    *,
    over_lines: Optional[dict] = None,
    explosion_payload: Optional[dict] = None,
    over_survival_payload: Optional[dict] = None,
    fragility_payload: Optional[dict] = None,
) -> list[dict]:
    """Evaluate all candidate Over markets and rank by edge.

    over_lines (optional)::
        {
            "full_game":         9.5,
            "f5":                4.5,
            "team_total_home":   4.5,
            "team_total_away":   4.5,
            "yrfi":              True,   # marker for YRFI availability
        }
    """
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}
    over_lines  = over_lines  or {}
    expl = explosion_payload or calculate_offensive_explosion_score(scoring_ctx, v2_payload)
    surv = over_survival_payload or calculate_over_survival_score(
        scoring_ctx, v2_payload, explosion_payload=expl)
    frag_score = (fragility_payload or {}).get("score", 50)

    er = _f(v2_payload.get("expectedRuns") or v2_payload.get("expected_runs"))
    out: list[dict] = []
    base_conf = max(0.0, min(100.0, surv["score"] * 0.7 + expl["score"] * 0.3 - frag_score * 0.15))

    # 1. Full Game Over (line in over_lines or use v2 line if any).
    fg_line = _f(over_lines.get("full_game") or v2_payload.get("smartTotalsLine"))
    if fg_line > 0:
        edge = er - fg_line
        out.append({
            "market":   f"Full Game Over {fg_line}",
            "category": "OVER_FULL_GAME",
            "line":     fg_line,
            "edge":     round(edge, 2),
            "score":    round(base_conf + max(0, edge) * 8, 1),
            "expected_runs": er,
            "offensive_explosion_score": expl["score"],
            "over_survival_score":       surv["score"],
            "fragility":                 frag_score,
        })

    # 2. F5 Over (use ~5/9 of expected runs).
    f5_line = _f(over_lines.get("f5"))
    if f5_line > 0:
        f5_er = er * 5.0 / 9.0 if er > 0 else 0
        edge = f5_er - f5_line
        out.append({
            "market":   f"F5 Total Over {f5_line}",
            "category": "OVER_F5",
            "line":     f5_line,
            "edge":     round(edge, 2),
            "score":    round(base_conf * 0.95 + max(0, edge) * 9, 1),  # F5 less variance
            "f5_expected": round(f5_er, 2),
            "offensive_explosion_score": expl["score"],
            "over_survival_score":       surv["score"],
        })

    # 3. Team Total Over (home / away). Approx each team gets half of ER.
    for side_key, side_label in (("team_total_home", "home"),
                                  ("team_total_away", "away")):
        line = _f(over_lines.get(side_key))
        if line <= 0:
            continue
        side_score = _f((scoring_ctx.get(f"offense_{side_label}") or {}).get("score", 50), 50)
        team_er = (er / 2.0) * (0.85 + side_score / 100.0)
        edge = team_er - line
        out.append({
            "market":   f"Team Total {side_label.title()} Over {line}",
            "category": "TEAM_TOTAL_OVER",
            "side":     side_label,
            "line":     line,
            "edge":     round(edge, 2),
            "score":    round(base_conf * 0.85 + max(0, edge) * 12, 1),
            "team_expected": round(team_er, 2),
            "offensive_explosion_score": expl["score"],
        })

    # 4. YRFI — uses pitcher quality & first-inning susceptibility heuristic.
    if over_lines.get("yrfi"):
        h_q = _f((scoring_ctx.get("home_pitcher_quality") or {}).get("score", 50), 50)
        a_q = _f((scoring_ctx.get("away_pitcher_quality") or {}).get("score", 50), 50)
        avg_q = (h_q + a_q) / 2.0
        # YRFI score scales inversely with pitcher quality.
        yrfi_score = max(20.0, min(85.0, 95.0 - avg_q))
        # Lineup boost.
        off_avg = (
            _f((scoring_ctx.get("offense_home") or {}).get("score", 50), 50)
            + _f((scoring_ctx.get("offense_away") or {}).get("score", 50), 50)
        ) / 2.0
        if off_avg >= 60:
            yrfi_score += 6
        out.append({
            "market":   "YRFI",
            "category": "YRFI",
            "line":     None,
            "edge":     round((yrfi_score - 55) / 8, 2),  # synthetic edge
            "score":    round(yrfi_score, 1),
            "offensive_explosion_score": expl["score"],
        })

    out.sort(key=lambda m: _f(m.get("edge")), reverse=True)
    return out


# ════════════════════════════════════════════════════════════════════════════
# 5. OVER DISCOVERY ENGINE
# ════════════════════════════════════════════════════════════════════════════
OVER_DISCOVERY_OUTCOMES = (
    "OVER_FULL_GAME",
    "OVER_F5",
    "TEAM_TOTAL_OVER",
    "YRFI",
    "OFFENSIVE_EXPLOSION_SCRIPT",
    "NO_OVER_EDGE",
)


def over_discovery_engine(
    scoring_ctx: Optional[dict] = None,
    v2_payload:  Optional[dict] = None,
    *,
    over_lines: Optional[dict] = None,
    fragility_payload: Optional[dict] = None,
) -> dict:
    """High-level Over discovery.

    Returns
    -------
    {
        "outcome":               OVER_DISCOVERY_OUTCOMES,
        "best_over_market":      dict | None,
        "offensive_explosion":   {...},
        "offensive_script":      {...},
        "over_survival":         {...},
        "over_markets":          [... ranked ...],
        "narrative_es":          str,
    }
    """
    expl = calculate_offensive_explosion_score(scoring_ctx, v2_payload)
    offensive_script = classify_offensive_script(expl["score"], scoring_ctx)
    over_survival = calculate_over_survival_score(
        scoring_ctx, v2_payload, explosion_payload=expl)
    markets = evaluate_over_markets(
        scoring_ctx, v2_payload,
        over_lines=over_lines,
        explosion_payload=expl,
        over_survival_payload=over_survival,
        fragility_payload=fragility_payload,
    )

    best = None
    for m in markets:
        if _f(m.get("edge")) >= 0.5:
            best = m
            break

    if expl["score"] >= 80:
        outcome = "OFFENSIVE_EXPLOSION_SCRIPT" if not best else best["category"]
    elif not best:
        outcome = "NO_OVER_EDGE"
    else:
        outcome = best["category"]

    narrative = (
        f"Offensive Explosion {expl['score']:.0f}/100 · "
        f"{offensive_script['label_es']} · Over Survival "
        f"{over_survival['score']:.0f}/100."
    )
    if best:
        narrative += (
            f" Mejor Over: {best['market']} · edge {best['edge']:+.1f}."
        )
    else:
        narrative += " Sin Over con edge accionable."

    return {
        "outcome":             outcome,
        "best_over_market":    best,
        "offensive_explosion": expl,
        "offensive_script":    offensive_script,
        "over_survival":       over_survival,
        "over_markets":        markets,
        "narrative_es":        narrative,
        "version":             6,
    }


# ════════════════════════════════════════════════════════════════════════════
# 6. MARKET COMPETITION (Under vs Over)
# ════════════════════════════════════════════════════════════════════════════
def market_competition(
    under_candidate: Optional[dict] = None,
    over_candidate:  Optional[dict] = None,
    *,
    current: Optional[dict] = None,
) -> dict:
    """Choose the market with the highest structural edge.

    Inputs
    ------
    under_candidate : {"market","line","edge","score"} from the existing
                       Under selector or v2 chosen_market.
    over_candidate  : {"market","line","edge","score"} from
                       over_discovery_engine().best_over_market.
    current         : the orchestrator's current chosen_market.

    Returns
    -------
    {
        "winner":                  dict | None,
        "winner_side":             "UNDER" | "OVER" | "CURRENT" | "NONE",
        "edge_gap":                float (|over.edge - under.edge|),
        "swap_required":           bool,
        "symmetric_swap_applied":  bool   # CAMBIO 1: telemetría de simetría
        "explanation":             str (es),
    }
    """
    u_edge = _f((under_candidate or {}).get("edge"))
    o_edge = _f((over_candidate  or {}).get("edge"))
    u_score = _f((under_candidate or {}).get("score"))
    o_score = _f((over_candidate  or {}).get("score"))
    c_market = (current or {}).get("market") or ""
    current_is_under = "under" in c_market.lower() and "team total" not in c_market.lower()

    edge_gap = round(abs(o_edge - u_edge), 2)

    # CAMBIO 1 — Swap simétrico (anti-bias estructural).
    # Antes: Over requería superar a Under por >=2.0 edge o >=8.0 score
    # mientras que Under sólo necesitaba >=1.0 / >=6.0. Esa asimetría
    # generaba un sesgo sistemático a favor del Under (inercia incumbente).
    # Ahora ambos lados comparten umbral: >=1.0 edge o >=6.0 confidence.
    # Telemetría: `symmetric_swap_applied=True` para trazabilidad downstream.
    SWAP_EDGE_THRESHOLD = 1.0
    SWAP_SCORE_THRESHOLD = 6.0

    # Over wins by symmetric margin → propose swap (real si incumbente es Under).
    if over_candidate and (
        o_edge - u_edge >= SWAP_EDGE_THRESHOLD
        or o_score - u_score >= SWAP_SCORE_THRESHOLD
    ):
        return {
            "winner":                  over_candidate,
            "winner_side":             "OVER",
            "edge_gap":                edge_gap,
            "swap_required":           current_is_under,
            "symmetric_swap_applied":  True,
            "explanation":             (
                f"Over con mayor edge ({o_edge:+.1f} vs Under {u_edge:+.1f}) "
                f"y confianza {o_score:.0f} — preferir mercado ofensivo "
                f"(umbral simétrico 1.0/6.0)."
            ),
        }

    # Under wins by symmetric margin — mismo umbral, sin privilegio incumbente.
    if under_candidate and (
        u_edge - o_edge >= SWAP_EDGE_THRESHOLD
        or u_score - o_score >= SWAP_SCORE_THRESHOLD
    ):
        return {
            "winner":                  under_candidate,
            "winner_side":             "UNDER",
            "edge_gap":                edge_gap,
            "swap_required":           False,
            "symmetric_swap_applied":  True,
            "explanation":             (
                f"Under conserva edge superior ({u_edge:+.1f} vs Over {o_edge:+.1f}) "
                f"(umbral simétrico 1.0/6.0)."
            ),
        }

    # Tie — keep current selection.
    return {
        "winner":                  current,
        "winner_side":             "CURRENT" if current else "NONE",
        "edge_gap":                edge_gap,
        "swap_required":           False,
        "symmetric_swap_applied":  True,
        "explanation":             "Edges similares — mantener selección actual (umbral simétrico 1.0/6.0).",
    }


# ════════════════════════════════════════════════════════════════════════════
# 7. DAILY MARKET AUDIT (Phases 8 + 9)
# ════════════════════════════════════════════════════════════════════════════
_MARKET_CATEGORIES_DAILY = (
    "FULL_GAME_UNDER", "F5_UNDER", "PROTECTED_FULL_GAME_UNDER",
    "FULL_GAME_OVER",  "F5_OVER",  "TEAM_TOTAL_OVER", "TEAM_TOTAL_UNDER",
    "YRFI", "NRFI", "RUN_LINE", "MONEYLINE", "OTHER",
)


def _normalise_market_label(market_text: str) -> str:
    m = (market_text or "").lower().strip()
    if not m:
        return "OTHER"
    if "f5" in m or "first 5" in m:
        if "over" in m:
            return "F5_OVER"
        return "F5_UNDER"
    if "team total" in m:
        if "over" in m:
            return "TEAM_TOTAL_OVER"
        return "TEAM_TOTAL_UNDER"
    if "yrfi" in m:
        return "YRFI"
    if "nrfi" in m:
        return "NRFI"
    if "run line" in m or "runline" in m:
        return "RUN_LINE"
    if "moneyline" in m or m.endswith(" ml") or m.startswith("ml "):
        return "MONEYLINE"
    if "under" in m:
        # Protected = line >> 9.5
        line = _extract_line(m)
        if line and line >= 10.5:
            return "PROTECTED_FULL_GAME_UNDER"
        return "FULL_GAME_UNDER"
    if "over" in m:
        return "FULL_GAME_OVER"
    return "OTHER"


def daily_market_audit(picks: list[dict],
                        *,
                        evaluated_count: Optional[int] = None) -> dict:
    """Generate the Daily Market Audit report (Phases 8 + 9).

    Inputs
    ------
    picks : the recommended picks for the day (orchestrator output —
            typically picks + rescued + structural_lean).
    evaluated_count : number of games the engine actually evaluated
            (so the audit can report markets per game ratio).

    Returns
    -------
    {
        "report":                 {markets_evaluated, recommended_markets},
        "bias":                   {warning_codes, dominant_market, dominant_share},
        "diversity":              {score 0-100, level},
        "narrative_es":           str,
        "version":                6,
    }
    """
    picks = picks or []
    n = len(picks)

    # Histogram of recommended markets.
    histogram: dict[str, int] = {k: 0 for k in _MARKET_CATEGORIES_DAILY}
    for p in picks:
        rec = (p.get("recommendation") or {}).get("market") \
              or (p.get("_mlb_script_v2") or {}).get("recommendedLine") or ""
        key = _normalise_market_label(rec)
        histogram[key] = histogram.get(key, 0) + 1

    # Bias detection.
    warnings: list[str] = []
    dominant_market = max(histogram, key=lambda k: histogram[k]) if histogram else None
    dominant_count = histogram.get(dominant_market, 0) if dominant_market else 0
    dominant_share = (dominant_count / float(n)) if n > 0 else 0.0

    under_total = sum(histogram.get(k, 0) for k in (
        "FULL_GAME_UNDER", "F5_UNDER", "PROTECTED_FULL_GAME_UNDER", "TEAM_TOTAL_UNDER", "NRFI"))
    over_total = sum(histogram.get(k, 0) for k in (
        "FULL_GAME_OVER", "F5_OVER", "TEAM_TOTAL_OVER", "YRFI"))

    if n >= 5 and over_total == 0 and under_total >= 5:
        warnings.append("UNDER_BIAS_WARNING")
    if n >= 5 and under_total == 0 and over_total >= 5:
        warnings.append("OVER_BIAS_WARNING")
    if dominant_share >= 0.70 and n >= 3:
        warnings.append("MARKET_CONCENTRATION_WARNING")
    if n >= 5 and (under_total + over_total) > 0:
        ratio = under_total / float(under_total + over_total)
        if ratio >= 0.90:
            warnings.append("OVER_STARVATION")

    # Diversity score 0-100.
    distinct_used = sum(1 for v in histogram.values() if v > 0)
    if n == 0:
        diversity_score = 100.0
    else:
        # Entropy-based: more categories used + balanced distribution = higher score.
        import math
        ent = 0.0
        for v in histogram.values():
            if v <= 0:
                continue
            p = v / n
            ent -= p * math.log(p)
        max_ent = math.log(min(len(histogram), n)) if min(len(histogram), n) > 1 else 1.0
        diversity_score = (ent / max_ent) * 100 if max_ent > 0 else 100.0
        # Penalty when one market dominates.
        if dominant_share >= 0.70:
            diversity_score *= 0.55
        elif dominant_share >= 0.55:
            diversity_score *= 0.75
    diversity_score = max(0.0, min(100.0, diversity_score))

    if diversity_score >= 75:
        diversity_level = "HEALTHY"
    elif diversity_score >= 50:
        diversity_level = "MODERATE"
    elif diversity_score >= 30:
        diversity_level = "POOR"
    else:
        diversity_level = "CRITICAL"

    narrative = (
        f"{n} picks · {distinct_used} mercados distintos · diversidad "
        f"{diversity_score:.0f}/100 ({diversity_level}). "
        f"Mercado dominante: {dominant_market} ({dominant_count}/{n})."
    )
    if warnings:
        narrative += " ⚠ " + ", ".join(warnings)

    return {
        "report": {
            "total_picks":            n,
            "evaluated_games":        evaluated_count,
            "histogram":              histogram,
            "under_total":            under_total,
            "over_total":             over_total,
            "under_over_ratio":       (
                round(under_total / float(under_total + over_total), 2)
                if (under_total + over_total) > 0 else None
            ),
        },
        "bias": {
            "warning_codes":  warnings,
            "dominant_market": dominant_market,
            "dominant_count":  dominant_count,
            "dominant_share":  round(dominant_share, 2),
        },
        "diversity": {
            "score":          round(diversity_score, 1),
            "level":          diversity_level,
            "distinct_used":  distinct_used,
        },
        "narrative_es":      narrative,
        "version":           6,
    }


__all__ = [
    "calculate_offensive_explosion_score",
    "classify_offensive_script",
    "calculate_over_survival_score",
    "evaluate_over_markets",
    "over_discovery_engine",
    "market_competition",
    "daily_market_audit",
    "OFFENSIVE_SCRIPTS",
    "OFFENSIVE_SCRIPT_LABELS_ES",
    "OVER_DISCOVERY_OUTCOMES",
]
