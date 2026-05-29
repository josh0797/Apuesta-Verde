"""MLB Pre-game Analytics Engine **v2 — Margin & Total Script**.

Goal
====
Evolve the v1 engine into a specialised **MLB script reader**:

  • Predict **margin of victory** (Run Line -1.5 dominance, not just "who wins").
  • Pick the **safest Over/Under line** (6.5/7.5/8/8.5/9 …) instead of a generic
    Over/Under verdict.
  • Be **pitcher-centred**: confirmed pitchers are a hard gate. No confirmed
    pitchers → no recommendation.
  • Build **MLB-only parlays** with positive same-game correlation.

This module is a *layer on top of* `mlb_pregame_analytics.py` — it imports the
base functions and **never replaces** them. Activation is wired into
`mlb_day_orchestrator.py` only when `sport == "baseball"`.

Public API
----------
- ``favorite_margin_profile(recent_games)``
- ``run_line_dominance_model(ctx)``
- ``smart_total_line_selector(expected_runs, ctx, market_lines=None)``
- ``pitcher_centered_evaluation(ctx)``
- ``same_game_correlation_rule(pair_ctx)``
- ``classify_pick_type(pick_ctx)``
- ``mlb_parlay_builder(candidates, max_size=4, min_correlation=60)``
- ``build_v2_payload(scoring_ctx, expected_runs, run_line_v1, over_under_v1, book_total)``
- ``emit_v2_signals(v2_payload)``

All functions are **pure** (dict in → dict out), exactly like v1.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

# Re-use base building blocks. The orchestrator already calls them; v2 only
# composes new derived metrics from their outputs.
from .mlb_pregame_analytics import (
    LEAGUE_AVG_RUNS_PER_GAME,
    PARK_FACTORS,
)

log = logging.getLogger("mlb_pregame_analytics_v2")


# ════════════════════════════════════════════════════════════════════════════
# 1. FAVORITE MARGIN PROFILE — last 15 games of the favourite team
# ════════════════════════════════════════════════════════════════════════════
def favorite_margin_profile(recent_games: list[dict]) -> dict:
    """Compute margin-of-victory reliability over a team's last 15 games.

    Args
    ----
    recent_games : list of dicts. Each dict should contain at least::

        {
            "team_runs":     int,   # runs scored by THIS team
            "opp_runs":      int,   # runs scored by opponent
            "win":           bool,  # True if this team won
        }

    Returns
    -------
    dict with::

        {
            "games_analyzed":     int,
            "wins":               int,
            "wins_by_2_plus":     int,
            "wins_by_3_plus":     int,
            "losses_by_2_plus":   int,
            "avg_run_diff":       float,
            "runs_scored_avg":    float,
            "runs_allowed_avg":   float,
            "winsBy2Rate":        float,   # 0-100
            "lossesBy2Rate":      float,   # 0-100
            "marginReliability":  float,   # 0-100
            "dominanceTrend":     "STRONG" | "MODERATE" | "WEAK",
        }
    """
    games = [g for g in (recent_games or []) if isinstance(g, dict)][:15]
    n = len(games)
    if n == 0:
        return {
            "games_analyzed":     0,
            "wins":               0,
            "wins_by_2_plus":     0,
            "wins_by_3_plus":     0,
            "losses_by_2_plus":   0,
            "avg_run_diff":       0.0,
            "runs_scored_avg":    0.0,
            "runs_allowed_avg":   0.0,
            "winsBy2Rate":        0.0,
            "lossesBy2Rate":      0.0,
            "marginReliability":  0.0,
            "dominanceTrend":     "WEAK",
        }

    wins = sum(1 for g in games if g.get("win"))
    wins_by_2 = sum(1 for g in games if g.get("win") and (g.get("team_runs", 0) - g.get("opp_runs", 0)) >= 2)
    wins_by_3 = sum(1 for g in games if g.get("win") and (g.get("team_runs", 0) - g.get("opp_runs", 0)) >= 3)
    losses_by_2 = sum(1 for g in games if (not g.get("win")) and (g.get("opp_runs", 0) - g.get("team_runs", 0)) >= 2)

    diffs = [g.get("team_runs", 0) - g.get("opp_runs", 0) for g in games]
    runs_for = sum(g.get("team_runs", 0) for g in games) / n
    runs_against = sum(g.get("opp_runs", 0) for g in games) / n
    avg_diff = sum(diffs) / n

    wins_by_2_rate = round(wins_by_2 / n * 100.0, 1)
    losses_by_2_rate = round(losses_by_2 / n * 100.0, 1)

    # marginReliability blends winsBy2Rate, avg_diff, and run differential
    # consistency. A team that wins 60% but always by 1 run scores LOW here.
    reliability = (
        (wins_by_2_rate * 0.55)
        + (max(0.0, avg_diff) * 8.0)
        + (min(60.0, runs_for * 6.0))
    )
    reliability = round(max(0.0, min(100.0, reliability)), 1)

    if wins_by_2_rate >= 55 and avg_diff >= 1.5:
        trend = "STRONG"
    elif wins_by_2_rate >= 40 and avg_diff >= 0.7:
        trend = "MODERATE"
    else:
        trend = "WEAK"

    return {
        "games_analyzed":    n,
        "wins":              wins,
        "wins_by_2_plus":    wins_by_2,
        "wins_by_3_plus":    wins_by_3,
        "losses_by_2_plus":  losses_by_2,
        "avg_run_diff":      round(avg_diff, 2),
        "runs_scored_avg":   round(runs_for, 2),
        "runs_allowed_avg":  round(runs_against, 2),
        "winsBy2Rate":       wins_by_2_rate,
        "lossesBy2Rate":     losses_by_2_rate,
        "marginReliability": reliability,
        "dominanceTrend":    trend,
    }


# ════════════════════════════════════════════════════════════════════════════
# 2. RUN LINE DOMINANCE MODEL — predict win-by-2+ likelihood
# ════════════════════════════════════════════════════════════════════════════
def run_line_dominance_model(ctx: dict) -> dict:
    """Predict whether the favourite can cover -1.5 (win by 2+).

    Required ``ctx`` keys (populated upstream by `mlb_day_orchestrator`)::

        favorite_side:                  "home" | "away"
        favorite_team:                  str
        pitcher_edge:                   {"score": 0-100, "edge_type": "STRONG"|...}
        bullpen:                        {"score": 0-100}                # combined
        favorite_bullpen_era_7d:        float
        favorite_bullpen_ip_48h:        float
        underdog_bullpen_era_7d:        float
        offense_home:                   {"score": 0-100}
        offense_away:                   {"score": 0-100}
        park:                           {"park_runs_mult": float, "weather_score": 0-100}
        favorite_margin_profile:        dict (output of favorite_margin_profile)
        underdog_margin_profile:        dict (output of favorite_margin_profile)
        lineup_status:                  "confirmed" | "projected" | "missing"
        _weights:                       optional dict from mlb_feedback_loop.get_active_weights()

    Returns the structured Run Line dominance payload described in the user's
    spec (market, marginProjection, runLineScore, coverProbability, …).
    """
    # MLB-V4 — Feedback Loop: when the orchestrator pre-resolved the active
    # weights (auto-recalibrated every 50 settled picks), honour them.
    # Otherwise use module defaults — identical to v2.1 behaviour.
    w = ctx.get("_weights") or {}
    w_pitcher_edge   = float(w.get("pitcher_edge",       0.30))
    w_bullpen        = float(w.get("bullpen",            0.18))
    w_fav_offense    = float(w.get("fav_offense",        0.14))
    w_wins_by_2      = float(w.get("fav_wins_by_2_rate", 0.18))
    w_und_losses_by_2= float(w.get("und_losses_by_2",    0.12))
    w_margin_rel     = float(w.get("margin_reliability", 0.08))
    fav_side = (ctx.get("favorite_side") or "home").lower()
    fav_team = ctx.get("favorite_team") or "Favorito"
    edge_score = (ctx.get("pitcher_edge") or {}).get("score", 50)
    edge_type  = (ctx.get("pitcher_edge") or {}).get("edge_type", "NEUTRAL")
    bull       = (ctx.get("bullpen")      or {}).get("score", 60)
    park       = ctx.get("park")          or {}
    park_mult  = park.get("park_runs_mult", 1.0)
    fav_bp_era_7d = float(ctx.get("favorite_bullpen_era_7d") or 0)
    fav_bp_ip_48h = float(ctx.get("favorite_bullpen_ip_48h") or 0)
    und_bp_era_7d = float(ctx.get("underdog_bullpen_era_7d") or 0)

    if fav_side == "home":
        fav_offense = (ctx.get("offense_home") or {}).get("score", 50)
        und_offense = (ctx.get("offense_away") or {}).get("score", 50)
    else:
        fav_offense = (ctx.get("offense_away") or {}).get("score", 50)
        und_offense = (ctx.get("offense_home") or {}).get("score", 50)

    fav_profile = ctx.get("favorite_margin_profile") or {}
    und_profile = ctx.get("underdog_margin_profile") or {}
    fav_wins_by_2_rate  = float(fav_profile.get("winsBy2Rate") or 50.0)
    und_losses_by_2_rate = float(und_profile.get("lossesBy2Rate") or 40.0)
    fav_avg_diff        = float(fav_profile.get("avg_run_diff") or 0.5)
    margin_reliability  = float(fav_profile.get("marginReliability") or 50.0)
    lineup_status       = (ctx.get("lineup_status") or "missing").lower()

    # ── Margin projection (expected favourite run differential) ──────────
    # Anchored on the favourite's historical avg diff plus offsets for
    # pitcher edge, bullpen edge, offence mismatch, park.
    margin_projection = (
        fav_avg_diff * 0.65
        + (edge_score - 50) / 50.0 * 1.40       # pitcher edge ±1.4 runs
        + ((bull - 60) / 60.0) * 0.45            # bullpen edge ±0.45
        + ((fav_offense - und_offense) / 50.0) * 1.10
        + (park_mult - 1.0) * 0.55
    )
    margin_projection = round(max(-2.5, min(4.5, margin_projection)), 2)

    # ── Run Line score 0-100 ─────────────────────────────────────────────
    score = (
        edge_score        * w_pitcher_edge
        + bull            * w_bullpen
        + fav_offense     * w_fav_offense
        + fav_wins_by_2_rate * w_wins_by_2
        + und_losses_by_2_rate * w_und_losses_by_2
        + margin_reliability   * w_margin_rel
    )
    # Park bonus: hitter-friendly parks slightly help RL -1.5 if fav is the
    # better hitting team; pitcher parks slightly hurt it.
    score += (park_mult - 1.0) * 6.0
    score = max(0.0, min(100.0, score))

    # ── Trap detection ───────────────────────────────────────────────────
    risks: list[str] = []
    if fav_bp_era_7d > 4.75:
        risks.append("Bullpen del favorito con ERA>4.75 últimos 7d.")
        score -= 8
    if fav_bp_ip_48h > 8:
        risks.append("Bullpen del favorito con >8 IP en 48h (fatiga).")
        score -= 6
    if und_bp_era_7d < 3.20:
        risks.append("Bullpen del underdog es élite (ERA<3.20) — riesgo de cierre cerrado.")
        score -= 5
    if margin_projection < 1.5:
        risks.append(f"projectedMargin={margin_projection} debajo del umbral 1.8.")
    if lineup_status not in ("confirmed", "projected"):
        risks.append("Lineup del favorito no confirmado ni proyectado.")
        score -= 7
    if fav_wins_by_2_rate < 40 and fav_avg_diff < 0.8:
        risks.append("Favorito histórico gana por margen corto (mucho 1-run).")
        score -= 6

    score = max(0.0, min(100.0, round(score, 1)))

    # ── Cover probability (calibrated heuristic) ─────────────────────────
    # MLB league baseline cover -1.5 ≈ 38%. Strong dominance lifts that.
    base_cover = 38.0
    cover_prob = (
        base_cover
        + (margin_projection - 1.0) * 9.5
        + (fav_wins_by_2_rate - 50.0) * 0.30
        + (und_losses_by_2_rate - 40.0) * 0.20
        + (edge_score - 50) * 0.10
    )
    cover_prob = round(max(5.0, min(85.0, cover_prob)), 1)

    # ── Fragility ────────────────────────────────────────────────────────
    fragility = 0.0
    if fav_bp_era_7d > 4.75:
        fragility += 18
    if fav_bp_ip_48h > 8:
        fragility += 12
    if lineup_status != "confirmed":
        fragility += 14
    if abs(margin_projection - 1.5) < 0.4:
        fragility += 16  # close to the line = fragile
    if edge_type not in ("STRONG", "MODERATE"):
        fragility += 12
    if und_bp_era_7d < 3.20:
        fragility += 8
    fragility = round(max(0.0, min(100.0, fragility)), 1)

    # ── Recommendation gate ──────────────────────────────────────────────
    confidence = round(max(0.0, min(100.0, (score * 0.55) + ((100 - fragility) * 0.35) + (cover_prob * 0.10))), 1)
    recommend = bool(
        score >= 72
        and margin_projection >= 1.8
        and fav_wins_by_2_rate >= 50.0
        and und_losses_by_2_rate >= 45.0
        and fav_bp_ip_48h <= 8
        and lineup_status in ("confirmed", "projected")
        and fragility <= 60
    )

    reasons: list[str] = []
    if edge_score >= 65:
        reasons.append(f"Ventaja real de abridor (score={edge_score}/100).")
    if bull >= 70:
        reasons.append("Bullpen del favorito en buen estado.")
    if fav_offense - und_offense >= 15:
        reasons.append("Ofensiva favorita claramente superior.")
    if fav_wins_by_2_rate >= 55:
        reasons.append(f"Favorito gana por 2+ el {fav_wins_by_2_rate:.0f}% últimas 15.")
    if und_losses_by_2_rate >= 50:
        reasons.append(f"Underdog pierde por 2+ el {und_losses_by_2_rate:.0f}% últimas 15.")
    if margin_projection >= 2.0:
        reasons.append(f"projectedMargin={margin_projection} (modelo dominante).")

    return {
        "market":            "Run Line -1.5",
        "team":              fav_team,
        "favorite_side":     fav_side,
        "marginProjection":  margin_projection,
        "runLineScore":      round(score, 1),
        "coverProbability":  cover_prob,
        "confidence":        confidence,
        "fragilityScore":    fragility,
        "reasons":           reasons,
        "risks":             risks,
        "recommend":         recommend,
        "signalTag":         "RUN_LINE_MARGIN_EDGE" if recommend else None,
    }


# ════════════════════════════════════════════════════════════════════════════
# 3. SMART TOTAL LINE SELECTOR
# ════════════════════════════════════════════════════════════════════════════
# Default line ladder (most common MLB totals).
_DEFAULT_OVER_LINES  = (6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5)
_DEFAULT_UNDER_LINES = (7.5, 8.0, 8.5, 9.0, 9.5)


# ────────────────────────────────────────────────────────────────────────────
# BUGFIX: Poisson-based totals probability model.
#
# Previously the v2 payload surfaced `coverProbability` from
# `runLineDominance` even when the recommended market was a totals (Over/
# Under) line. That produced UI rows like "UNDER 9.5 · cover 33%" — which
# is the favourite's probability of covering -1.5, NOT the probability the
# total runs land under 9.5. With expected_runs = 6.7 and line = 9.5, the
# true probability the Under hits is ~87%.
#
# We use a Poisson model on expected total runs (λ = expected_runs) and
# compute, for an Under X.5 line, P(total <= floor(X.5)) = poisson_cdf(
# floor(X.5), λ). For integer lines (rare in MLB), we treat the push
# probability as 50/50 between Under and Over for display purposes.
# ────────────────────────────────────────────────────────────────────────────
def _poisson_cdf(k: int, lam: float) -> float:
    """Cumulative P(X ≤ k) for X ~ Poisson(lam). Numerically stable for
    lam ≤ ~50 which is far above any realistic MLB total."""
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    if k < 0:
        return 0.0
    term = math.exp(-lam)
    total = term                      # i = 0
    for i in range(1, int(k) + 1):
        term *= lam / i
        total += term
    return max(0.0, min(1.0, total))


def totals_probability(
    expected_runs: float,
    line: float,
    *,
    model: str = "Poisson",
) -> dict:
    """Return ``{prob_under, prob_over, model}`` for an MLB totals line.

    Half-line example (most common, line=8.5):
        Under 8.5 → P(total ≤ 8)  = poisson_cdf(8, λ)
        Over  8.5 → P(total ≥ 9)  = 1 - poisson_cdf(8, λ)

    Integer-line example (rare, line=8.0):
        We compute P(push) = poisson_pmf(8, λ), then assign half of it to
        each side so the displayed Cover Probability never includes the
        push leg (the user is told elsewhere when push risk is non-trivial).
    """
    try:
        lam = float(expected_runs)
    except (TypeError, ValueError):
        lam = 0.0
    try:
        ln = float(line)
    except (TypeError, ValueError):
        ln = 0.0

    lam = max(0.05, min(25.0, lam))
    is_half = abs(ln - round(ln)) > 1e-9
    if is_half:
        k = int(math.floor(ln))
        p_under = _poisson_cdf(k, lam)
    else:
        # Integer line: split the push 50/50.
        n = int(round(ln))
        # Poisson pmf at n
        log_term = -lam + n * math.log(lam) - sum(math.log(i) for i in range(1, n + 1)) if n > 0 else -lam
        p_eq = math.exp(log_term)
        p_under = _poisson_cdf(n - 1, lam) + p_eq * 0.5
    p_under = max(0.0, min(1.0, p_under))
    p_over  = max(0.0, min(1.0, 1.0 - p_under))
    return {
        "prob_under": round(p_under, 4),
        "prob_over":  round(p_over, 4),
        "model":      model,
        "lambda":     round(lam, 3),
        "line":       ln,
    }


def smart_total_line_selector(
    expected_runs: float,
    ctx: Optional[dict] = None,
    market_lines: Optional[list[float]] = None,
) -> dict:
    """Pick the safest, best-value O/U line for a given expected_runs.

    Returns a dict with bestLine / safeLine / aggressiveLine /
    recommendedLine + lineSafetyScore + fragilityScore.
    """
    ctx = ctx or {}
    try:
        er = float(expected_runs)
    except (TypeError, ValueError):
        er = 9.0
    er = max(3.0, min(15.0, er))

    pitcher_quality_combined = (
        (ctx.get("home_pitcher_quality") or {}).get("score", 50)
        + (ctx.get("away_pitcher_quality") or {}).get("score", 50)
    ) / 2.0
    park_mult    = (ctx.get("park") or {}).get("park_runs_mult", 1.0)
    weather      = (ctx.get("park") or {}).get("weather_score", 50)
    offense_avg  = (
        (ctx.get("offense_home") or {}).get("score", 50)
        + (ctx.get("offense_away") or {}).get("score", 50)
    ) / 2.0

    side = "OVER" if er >= 8.0 else "UNDER"
    ladder = list(market_lines) if market_lines else (
        list(_DEFAULT_OVER_LINES) if side == "OVER" else list(_DEFAULT_UNDER_LINES)
    )
    ladder = sorted(set(float(x) for x in ladder))

    # For Over: best line is the *highest* line we still clear by ≥0.8.
    # Safe line is the *lowest* line we clear by ≥1.5 (protected).
    # Aggressive is the highest line within 0.3 of expected (max payout).
    def _score_line(line: float, side_: str) -> tuple[float, float]:
        if side_ == "OVER":
            diff = er - line
        else:
            diff = line - er
        # Safety = how far above/below the line we are (in runs).
        safety = max(0.0, min(100.0, 40.0 + diff * 18.0))
        # Penalise fragility when diff is small or pitchers are elite.
        fragility = max(0.0, min(100.0, 60.0 - diff * 15.0))
        if pitcher_quality_combined >= 70 and side_ == "OVER":
            fragility += 10
        if pitcher_quality_combined <= 35 and side_ == "UNDER":
            fragility += 12
        if park_mult >= 1.10 and side_ == "UNDER":
            fragility += 8
        if park_mult <= 0.95 and side_ == "OVER":
            fragility += 8
        if weather >= 70 and side_ == "UNDER":
            fragility += 5
        if offense_avg >= 65 and side_ == "UNDER":
            fragility += 6
        fragility = max(0.0, min(100.0, fragility))
        return safety, fragility

    candidates: list[dict] = []
    for ln in ladder:
        s, f = _score_line(ln, side)
        probs = totals_probability(er, ln)
        candidates.append({
            "line":              ln,
            "side":              side,
            "safety":            round(s, 1),
            "fragility":         round(f, 1),
            "diff":              round(er - ln if side == "OVER" else ln - er, 2),
            # BUGFIX — Poisson totals model.
            "prob_under":        probs["prob_under"],
            "prob_over":         probs["prob_over"],
            "prob_model":        probs["model"],
        })
    # Sort by safety desc to pick safeLine; sort by line desc to pick aggressive.
    by_safety = sorted(candidates, key=lambda c: c["safety"], reverse=True)
    by_line   = sorted(candidates, key=lambda c: c["line"],   reverse=True)

    # Best line: highest line we still beat by ≥0.8.
    best = next(
        (c for c in by_line if c["diff"] >= 0.8 and c["fragility"] <= 65),
        by_safety[0] if by_safety else None,
    )
    # Safe line: comfortable buffer ≥1.5.
    safe = next(
        (c for c in by_line if c["diff"] >= 1.5),
        by_safety[0] if by_safety else None,
    )
    # Aggressive: highest line within 0.3 of expected (just above).
    aggressive = next(
        (c for c in by_line if -0.3 <= c["diff"] <= 0.6),
        by_line[0] if by_line else None,
    )

    # Final recommendation = best (balanced). If fragility of best > 55,
    # downgrade to safe.
    recommended = best
    if recommended and recommended["fragility"] > 55 and safe and safe["line"] < recommended["line"]:
        recommended = safe

    # Reason heuristics.
    if recommended is None:
        return {
            "expectedRuns":     round(er, 1),
            "side":             side,
            "bestLine":         None,
            "safeLine":         None,
            "aggressiveLine":   None,
            "recommendedLine":  None,
            "lineSafetyScore":  0.0,
            "fragilityScore":   100.0,
            "reason":           "Sin líneas viables en el rango.",
            "signalTag":        None,
            # BUGFIX — totals probability fields (None when no candidate).
            "coverProbability":   None,
            "edgeVsLine":         None,
            "probabilityUnder":   None,
            "probabilityOver":    None,
            "probabilityModel":   None,
        }

    reason_parts = [
        f"expected_runs≈{er:.1f}",
        f"línea recomendada {side} {recommended['line']}",
        f"diff={recommended['diff']:+.1f}",
    ]
    if best and safe and best["line"] != safe["line"]:
        reason_parts.append(f"agresiva={side} {aggressive['line'] if aggressive else best['line']}")
        reason_parts.append(f"protegida={side} {safe['line']}")

    # BUGFIX — surface the Poisson probability for the RECOMMENDED side.
    rec_prob_under = recommended.get("prob_under") or 0.0
    rec_prob_over  = recommended.get("prob_over")  or 0.0
    cover_probability = (rec_prob_over if side == "OVER" else rec_prob_under) * 100.0
    edge_vs_line = round(er - recommended["line"], 2)   # positive ⇒ Over has value

    return {
        "expectedRuns":     round(er, 1),
        "side":             side,
        "bestLine":         f"{side} {best['line']}" if best else None,
        "safeLine":         f"{side} {safe['line']}" if safe else None,
        "aggressiveLine":   f"{side} {aggressive['line']}" if aggressive else None,
        "recommendedLine":  f"{side} {recommended['line']}",
        "lineSafetyScore":  recommended["safety"],
        "fragilityScore":   recommended["fragility"],
        "reason":           " · ".join(reason_parts),
        "signalTag":        "SMART_OVER_LINE_SELECTED" if abs(recommended["diff"]) >= 0.8 else None,
        "ladder":           candidates,
        # BUGFIX — Poisson-derived totals probabilities (used by the UI
        # whenever the final pickType is a totals market).
        "coverProbability":  round(cover_probability, 1),
        "edgeVsLine":        edge_vs_line,
        "probabilityUnder":  round(rec_prob_under * 100.0, 1),
        "probabilityOver":   round(rec_prob_over  * 100.0, 1),
        "probabilityModel":  "Poisson",
    }


# ════════════════════════════════════════════════════════════════════════════
# 4. PITCHER-CENTRED EVALUATION
# ════════════════════════════════════════════════════════════════════════════
def pitcher_centered_evaluation(ctx: dict) -> dict:
    """Hard gate: confirmed pitchers + matchup vs lineup evaluation.

    Emits up to three signal tags:
      * STRONG_STARTING_PITCHER_EDGE
      * PITCHER_MISMATCH_DETECTED
      * LINEUP_VS_PITCHER_EDGE
    """
    home_p = ctx.get("home_pitcher_stats") or {}
    away_p = ctx.get("away_pitcher_stats") or {}
    home_q = (ctx.get("home_pitcher_quality") or {}).get("score", 0)
    away_q = (ctx.get("away_pitcher_quality") or {}).get("score", 0)
    home_p_name = home_p.get("name") or ctx.get("home_pitcher_name") or ""
    away_p_name = away_p.get("name") or ctx.get("away_pitcher_name") or ""
    home_lineup = ctx.get("home_lineup") or []
    away_lineup = ctx.get("away_lineup") or []

    confirmed = bool(home_p_name) and bool(away_p_name)
    tags: list[str] = []
    rationale: list[str] = []

    if not confirmed:
        return {
            "bothConfirmed":  False,
            "homePitcher":    home_p_name or None,
            "awayPitcher":    away_p_name or None,
            "pitcherEdge":    None,
            "mismatch":       False,
            "lineupVsPitcherEdge": False,
            "tags":           ["PITCHERS_NOT_CONFIRMED"],
            "explanation":    "Sin ambos abridores confirmados — NO recomendar.",
        }

    diff = abs(home_q - away_q)
    edge_side = "home" if home_q > away_q else "away"
    edge_qual_score = max(home_q, away_q)

    strong_edge = (diff >= 20 and edge_qual_score >= 65)
    mismatch    = (diff >= 30)

    if strong_edge:
        tags.append("STRONG_STARTING_PITCHER_EDGE")
        rationale.append(f"Diferencia de calidad {diff:.0f} pts a favor de {edge_side}.")
    if mismatch:
        tags.append("PITCHER_MISMATCH_DETECTED")
        rationale.append(f"Mismatch fuerte: {edge_side} {edge_qual_score:.0f} vs rival {min(home_q, away_q):.0f}.")

    # Lineup-vs-pitcher edge: top-3 OPS of one team comfortably beats the
    # opposing pitcher's WHIP/Hard Hit profile.
    def _top3_ops(lineup: list[dict]) -> float:
        ops_vals = [float(b.get("ops") or 0) for b in lineup[:3]]
        return sum(ops_vals) / len(ops_vals) if ops_vals else 0.0

    home_top3 = _top3_ops(home_lineup)
    away_top3 = _top3_ops(away_lineup)
    lineup_vs_pitcher = False
    if home_top3 >= 0.800 and away_q <= 55:
        lineup_vs_pitcher = True
        rationale.append(f"Top-3 home OPS {home_top3:.3f} vs pitcher rival score {away_q:.0f}.")
    if away_top3 >= 0.800 and home_q <= 55:
        lineup_vs_pitcher = True
        rationale.append(f"Top-3 away OPS {away_top3:.3f} vs pitcher rival score {home_q:.0f}.")
    if lineup_vs_pitcher:
        tags.append("LINEUP_VS_PITCHER_EDGE")

    return {
        "bothConfirmed":      True,
        "homePitcher":        home_p_name,
        "awayPitcher":        away_p_name,
        "homeQualityScore":   round(home_q, 1),
        "awayQualityScore":   round(away_q, 1),
        "pitcherEdgeSide":    edge_side,
        "edgeDiff":           round(diff, 1),
        "strongEdge":         strong_edge,
        "mismatch":           mismatch,
        "lineupVsPitcherEdge": lineup_vs_pitcher,
        "tags":               tags,
        "explanation":        " · ".join(rationale) or "Calidad de abridores balanceada.",
    }


# ════════════════════════════════════════════════════════════════════════════
# 5. SAME-GAME CORRELATION: Run Line -1.5 + Over (positive)
# ════════════════════════════════════════════════════════════════════════════
def same_game_correlation_rule(pair_ctx: dict) -> dict:
    """Evaluate same-game pair (Run Line -1.5 + Over Total).

    Required ``pair_ctx`` keys::
        favorite_team:           str
        marginProjection:        float
        favorite_team_runs_proj: float   # projected runs by the favourite team
        expected_runs:           float   # combined expected runs
        underdog_bullpen_era_7d: float
        over_line:               float | None
        run_line_recommend:      bool
        over_recommend:          bool
    """
    margin = float(pair_ctx.get("marginProjection") or 0.0)
    fav_team_runs = float(pair_ctx.get("favorite_team_runs_proj") or 0.0)
    er = float(pair_ctx.get("expected_runs") or 0.0)
    und_bp_era_7d = float(pair_ctx.get("underdog_bullpen_era_7d") or 0.0)
    over_line = pair_ctx.get("over_line")
    rl_ok = bool(pair_ctx.get("run_line_recommend"))
    ov_ok = bool(pair_ctx.get("over_recommend"))

    if not (rl_ok and ov_ok):
        return {
            "sameGameCorrelation": "NEUTRAL",
            "correlationReason":   "Solo uno de los picks aplica — no aplica regla de correlación.",
            "correlationBonus":    0,
            "signalTag":           None,
        }

    positive = (
        margin >= 2.0
        and fav_team_runs >= 4.5
        and er >= 8.0
        and und_bp_era_7d >= 4.20
    )
    negative = (
        (margin >= 2.0 and er <= 7.0)       # depende de pitching dominante
        or (fav_team_runs <= 3.5 and over_line and float(over_line) >= 8.5)
    )

    if positive:
        return {
            "sameGameCorrelation": "POSITIVE",
            "correlationReason":   (
                "Si el favorito cubre -1.5, probablemente produce suficientes "
                "carreras para apoyar el Over (bullpen rival vulnerable + ofensiva fuerte)."
            ),
            "correlationBonus":    10,
            "signalTag":           "SAME_GAME_CORRELATED_PAIR",
        }
    if negative:
        return {
            "sameGameCorrelation": "NEGATIVE",
            "correlationReason":   (
                "El Run Line depende de pitching dominante o la ofensiva del "
                "favorito no proyecta suficientes carreras para apoyar el Over."
            ),
            "correlationBonus":    -15,
            "signalTag":           None,
        }
    return {
        "sameGameCorrelation": "NEUTRAL",
        "correlationReason":   "Los dos picks pueden coexistir pero sin apoyo claro.",
        "correlationBonus":    0,
        "signalTag":           None,
    }


# ════════════════════════════════════════════════════════════════════════════
# 6. PICK TYPE CLASSIFICATION
# ════════════════════════════════════════════════════════════════════════════
def classify_pick_type(pick_ctx: dict) -> dict:
    """Classify a pick into one of:
        DOMINANT_FAVORITE_RUN_LINE | SMART_LOW_OVER | PITCHER_UNDER
        | F5_EDGE | TEAM_TOTAL_EDGE | SAME_GAME_CORRELATED_PAIR | GENERIC

    Inputs (extracted from the pick's full payload)::
        market:               "Run Line -1.5" | "Total Runs Over" | ...
        marginProjection:     float
        runLineScore:         float | None
        expectedRuns:         float | None
        recommendedLine:      str | None    (e.g. "Over 7.5")
        sameGameCorrelation:  "POSITIVE" | "NEGATIVE" | "NEUTRAL"
        underProfileScore:    float | None  (from base under_profile)
    """
    market = (pick_ctx.get("market") or "").lower()
    margin = float(pick_ctx.get("marginProjection") or 0.0)
    rls    = float(pick_ctx.get("runLineScore") or 0.0)
    er     = float(pick_ctx.get("expectedRuns") or 0.0)
    line_s = (pick_ctx.get("recommendedLine") or "").lower()
    sgc    = (pick_ctx.get("sameGameCorrelation") or "").upper()
    up_s   = float(pick_ctx.get("underProfileScore") or 0.0)

    if sgc == "POSITIVE":
        return {"type": "SAME_GAME_CORRELATED_PAIR",
                "selection": pick_ctx.get("selection") or pick_ctx.get("market"),
                "reason": "Par mismo juego con correlación positiva validada."}

    if "run line -1.5" in market and rls >= 72 and margin >= 1.8:
        return {"type": "DOMINANT_FAVORITE_RUN_LINE",
                "selection": pick_ctx.get("team") or pick_ctx.get("selection"),
                "reason": (
                    "Favorito proyecta ganar por margen real (no solo ganar): "
                    f"projectedMargin={margin}, runLineScore={rls:.0f}."
                )}

    if "over" in market or line_s.startswith("over"):
        # Smart low Over: expected_runs supera la línea con buen buffer pero la línea elegida no es la más alta posible.
        if er >= 8.5 and "over 7.5" in line_s:
            return {"type": "SMART_LOW_OVER",
                    "selection": line_s.title(),
                    "reason": (
                        f"Modelo proyecta {er:.1f} carreras; la línea Over 7.5 ofrece "
                        "mayor protección que Over 8.5 manteniendo valor."
                    )}
        if er >= 7.5 and "over 6.5" in line_s:
            return {"type": "SMART_LOW_OVER",
                    "selection": line_s.title(),
                    "reason": (
                        f"Modelo proyecta {er:.1f} carreras; Over 6.5 reduce fragilidad."
                    )}
        return {"type": "TEAM_TOTAL_EDGE" if "team" in market else "SMART_LOW_OVER",
                "selection": line_s.title() or pick_ctx.get("selection"),
                "reason": f"Edge en mercado Over (expected_runs={er:.1f})."}

    if "under" in market and up_s >= 70:
        return {"type": "PITCHER_UNDER",
                "selection": pick_ctx.get("selection") or pick_ctx.get("recommendedLine"),
                "reason": (
                    f"Perfil Under sólido (underProfileScore={up_s:.0f}): "
                    "ambos abridores dominantes + lineups silenciosos."
                )}

    if "f5" in market or "1st 5" in market:
        return {"type": "F5_EDGE",
                "selection": pick_ctx.get("selection") or pick_ctx.get("market"),
                "reason": "Edge en mercado First 5 Innings."}
    if "team total" in market:
        return {"type": "TEAM_TOTAL_EDGE",
                "selection": pick_ctx.get("selection") or pick_ctx.get("market"),
                "reason": "Edge específico en team total."}

    return {"type": "GENERIC",
            "selection": pick_ctx.get("selection") or pick_ctx.get("market"),
            "reason": "Pick MLB sin categoría especializada."}


# ════════════════════════════════════════════════════════════════════════════
# 7. MLB-ONLY PARLAY BUILDER
# ════════════════════════════════════════════════════════════════════════════
MLB_ALLOWED_MARKETS = (
    "run line -1.5", "run line +1.5",
    "total runs over", "total runs under",
    "team total over", "team total under",
    "f5 moneyline", "f5 total",
    "nrfi", "yrfi",
    "over",  # smart line selector outputs already include "Over X"
    "under",
)


def _is_mlb_market(market: str) -> bool:
    m = (market or "").lower()
    return any(allowed in m for allowed in MLB_ALLOWED_MARKETS)


def _pick_fragility(pick: dict) -> float:
    """Lookup fragility from any of the known nested locations."""
    cand = (
        (pick.get("_mlb_script_v2") or {}).get("fragilityScore")
        or (pick.get("fragility") or {}).get("score")
        or (pick.get("recommendation") or {}).get("fragilityScore")
    )
    try:
        return float(cand) if cand is not None else 50.0
    except (TypeError, ValueError):
        return 50.0


def _pick_score(pick: dict) -> float:
    cand = (
        (pick.get("recommendation") or {}).get("score")
        or (pick.get("_mlb_script_v2") or {}).get("runLineScore")
        or (pick.get("scores") or {}).get("run_line")
        or 0
    )
    try:
        return float(cand)
    except (TypeError, ValueError):
        return 0.0


def _pick_pitcher_confidence(pick: dict) -> float:
    """Higher when both pitchers are confirmed and quality scores are healthy."""
    v2 = pick.get("_mlb_script_v2") or {}
    pcen = v2.get("pitcherCentered") or {}
    if not pcen.get("bothConfirmed", False):
        return 20.0
    q = max(0.0, min(100.0, (pcen.get("homeQualityScore", 50) + pcen.get("awayQualityScore", 50)) / 2.0))
    edge_bonus = 8.0 if pcen.get("strongEdge") else 0.0
    return min(100.0, q + edge_bonus)


def mlb_parlay_builder(
    candidates: list[dict],
    *,
    max_size: int = 4,
    min_correlation: int = 60,
    weights: Optional[dict] = None,
) -> dict:
    """Build an MLB-only parlay (2–max_size legs) from the candidate picks.

    `weights` (optional) honours the auto-recalibrated values from
    `mlb_feedback_loop.get_active_weights()`.

    Returns::
        {
          "parlayType":         "MLB_ONLY",
          "picks":              [<full pick payload>, ...],
          "size":               int,
          "correlationScore":   0-100,
          "averagePickScore":   0-100,
          "averageFragility":   0-100,
          "pitcherConfidence":  0-100,
          "finalParlayScore":   0-100,
          "estimatedOdds":      str | "",
          "riskLevel":          "LOW" | "MEDIUM" | "HIGH",
          "whyThisParlayWorks": [str, ...],
          "whyThisParlayCanFail":[str, ...],
          "rejected_reasons":   [str, ...],
        }
    """
    rejected_reasons: list[str] = []

    # Step 1 — filter to MLB-only + market allowlist + confirmed pitchers.
    eligible: list[dict] = []
    for p in (candidates or []):
        if (p.get("sport") or "baseball").lower() != "baseball":
            rejected_reasons.append(f"Excluido (no-baseball): {p.get('match_label') or p.get('game_pk')}")
            continue
        rec = p.get("recommendation") or {}
        market = rec.get("market") or rec.get("selection") or ""
        if not _is_mlb_market(market):
            rejected_reasons.append(f"Mercado no permitido en parlay MLB: {market}")
            continue
        # Confirmed pitchers gate
        pcen = (p.get("_mlb_script_v2") or {}).get("pitcherCentered") or {}
        if not pcen.get("bothConfirmed", False) and not (p.get("home_pitcher") and p.get("away_pitcher")):
            rejected_reasons.append(f"Pitchers no confirmados — descartado del parlay: {p.get('match_label')}")
            continue
        eligible.append(p)

    if len(eligible) < 2:
        return {
            "parlayType":         "MLB_ONLY",
            "picks":              [],
            "size":               0,
            "correlationScore":   0,
            "averagePickScore":   0,
            "averageFragility":   100,
            "pitcherConfidence":  0,
            "finalParlayScore":   0,
            "estimatedOdds":      "",
            "riskLevel":          "HIGH",
            "whyThisParlayWorks": [],
            "whyThisParlayCanFail":["Menos de 2 picks elegibles para parlay MLB."],
            "rejected_reasons":   rejected_reasons,
        }

    # Step 2 — rank candidates by composite score.
    def _composite(p: dict) -> float:
        s = _pick_score(p)
        f = _pick_fragility(p)
        pc = _pick_pitcher_confidence(p)
        return s * 0.55 + (100 - f) * 0.25 + pc * 0.20

    eligible.sort(key=_composite, reverse=True)
    top = eligible[:max_size]
    # Always pick 2-4 legs; prefer 3-4 if available with high composite.
    legs = top if len(top) <= max_size else top[:max_size]
    if len(legs) > max_size:
        legs = legs[:max_size]

    # Step 3 — correlation analysis among selected legs.
    pos_corr: list[str] = []
    neg_corr: list[str] = []
    correlation_score = 60  # neutral baseline

    by_game: dict[Any, list[dict]] = {}
    for leg in legs:
        gid = leg.get("game_pk") or leg.get("match_id") or leg.get("match_label")
        by_game.setdefault(gid, []).append(leg)
    for gid, group in by_game.items():
        if len(group) >= 2:
            v2_pair_label = " + ".join(((g.get("recommendation") or {}).get("market") or "") for g in group)
            # Check if the engine flagged same-game positive correlation.
            same_game_pos = any(
                ((g.get("_mlb_script_v2") or {}).get("sameGameCorrelation") == "POSITIVE")
                for g in group
            )
            if same_game_pos:
                pos_corr.append(f"Mismo juego con correlación positiva: {v2_pair_label}")
                correlation_score += 12
            else:
                neg_corr.append(f"Mismo juego sin correlación validada: {v2_pair_label}")
                correlation_score -= 15

    # Penalise 3+ Overs that all depend on weather/park.
    overs_with_weather_dep = sum(
        1 for leg in legs
        if "over" in ((leg.get("recommendation") or {}).get("market") or "").lower()
        and (leg.get("all_components") or {}).get("park", {}).get("park_runs_mult", 1.0) >= 1.08
    )
    if overs_with_weather_dep >= 3:
        neg_corr.append("3+ Overs dependientes de parque/clima — riesgo de overlap negativo.")
        correlation_score -= 10

    # Pitcher-tagged picks should not share the same pitcher.
    seen_pitchers: set[str] = set()
    for leg in legs:
        for p_name in (leg.get("home_pitcher"), leg.get("away_pitcher")):
            if p_name and p_name in seen_pitchers:
                neg_corr.append(f"Pitcher repetido entre legs: {p_name}")
                correlation_score -= 8
            elif p_name:
                seen_pitchers.add(p_name)

    correlation_score = max(0, min(100, correlation_score))

    avg_score = sum(_pick_score(p) for p in legs) / len(legs)
    avg_frag  = sum(_pick_fragility(p) for p in legs) / len(legs)
    avg_pc    = sum(_pick_pitcher_confidence(p) for p in legs) / len(legs)

    final_score = round(
        avg_score    * float((weights or {}).get("parlay_avg_score",    0.45))
        + (100 - avg_frag) * float((weights or {}).get("parlay_frag_inv",    0.20))
        + correlation_score * float((weights or {}).get("parlay_correlation", 0.20))
        + avg_pc          * float((weights or {}).get("parlay_pitcher_conf", 0.15)),
        1,
    )

    risk = "LOW" if final_score >= 72 and correlation_score >= 70 else \
           "MEDIUM" if final_score >= 60 else "HIGH"

    # If correlation is below threshold, surface as "blocked" but keep
    # the analysis (the orchestrator decides what to render).
    if correlation_score < min_correlation:
        return {
            "parlayType":         "MLB_ONLY",
            "picks":              [],   # blocked
            "size":               0,
            "correlationScore":   correlation_score,
            "averagePickScore":   round(avg_score, 1),
            "averageFragility":   round(avg_frag, 1),
            "pitcherConfidence":  round(avg_pc, 1),
            "finalParlayScore":   final_score,
            "estimatedOdds":      "",
            "riskLevel":          "HIGH",
            "whyThisParlayWorks": pos_corr,
            "whyThisParlayCanFail": neg_corr + [
                f"correlationScore={correlation_score} < umbral {min_correlation}.",
            ],
            "rejected_reasons":   rejected_reasons,
        }

    why_works: list[str] = list(pos_corr)
    if avg_pc >= 65:
        why_works.append(f"Pitchers confirmados con calidad sólida (avg={avg_pc:.0f}).")
    if avg_frag <= 35:
        why_works.append(f"Fragilidad promedio baja ({avg_frag:.0f}/100).")
    if avg_score >= 75:
        why_works.append(f"Score promedio de picks alto ({avg_score:.0f}/100).")
    why_works = why_works or ["Combinación equilibrada de scores, baja fragilidad y pitchers sólidos."]

    why_fails = list(neg_corr)
    if avg_frag >= 55:
        why_fails.append(f"Fragilidad promedio elevada ({avg_frag:.0f}/100).")
    if avg_pc <= 45:
        why_fails.append(f"Confianza de pitchers baja ({avg_pc:.0f}/100).")

    return {
        "parlayType":         "MLB_ONLY",
        "picks":              legs,
        "size":               len(legs),
        "correlationScore":   correlation_score,
        "averagePickScore":   round(avg_score, 1),
        "averageFragility":   round(avg_frag, 1),
        "pitcherConfidence":  round(avg_pc, 1),
        "finalParlayScore":   final_score,
        "estimatedOdds":      "",   # left empty per spec (we don't optimise for odds)
        "riskLevel":          risk,
        "whyThisParlayWorks": why_works,
        "whyThisParlayCanFail": why_fails,
        "rejected_reasons":   rejected_reasons,
    }


# ════════════════════════════════════════════════════════════════════════════
# 8. CONVENIENCE: build the full v2 payload for a single game
# ════════════════════════════════════════════════════════════════════════════
def build_v2_payload(
    scoring_ctx: dict,
    *,
    expected_runs: Optional[float] = None,
    run_line_v1: Optional[dict] = None,
    over_under_v1: Optional[dict] = None,
    book_total: Optional[float] = None,
    weights: Optional[dict] = None,
) -> dict:
    """Combine v2 functions into a single per-game payload.

    The orchestrator already computed run_line_v1 and over_under_v1 (from the
    base module); we use them as inputs so we don't recompute the same blocks.
    `weights` (optional) — auto-recalibrated weights from the feedback loop.
    """
    # Inject weights into scoring_ctx so the dominance model reads them.
    if weights:
        scoring_ctx = {**scoring_ctx, "_weights": weights}
    if expected_runs is None and over_under_v1:
        expected_runs = over_under_v1.get("expected_runs")

    pcen = pitcher_centered_evaluation(scoring_ctx)
    rldom = run_line_dominance_model(scoring_ctx)
    line_sel = smart_total_line_selector(expected_runs or 9.0, scoring_ctx)

    # Same-game correlation requires both legs to be recommended.
    ov_side = (over_under_v1 or {}).get("verdict")
    pair_corr = same_game_correlation_rule({
        "favorite_team":           scoring_ctx.get("favorite_team"),
        "marginProjection":        rldom.get("marginProjection") or 0.0,
        "favorite_team_runs_proj": (rldom.get("marginProjection") or 0.0) / 2.0 + (expected_runs or 8.0) / 2.0,
        "expected_runs":           expected_runs or 0.0,
        "underdog_bullpen_era_7d": scoring_ctx.get("underdog_bullpen_era_7d") or 0.0,
        "over_line":               book_total,
        "run_line_recommend":      rldom.get("recommend", False),
        "over_recommend":          (ov_side == "OVER"),
    })

    pick_type_ctx = {
        "market":              ((run_line_v1 or {}).get("verdict") or rldom.get("market") or "").strip(),
        "selection":           rldom.get("team") if rldom.get("recommend") else line_sel.get("recommendedLine"),
        "team":                rldom.get("team"),
        "marginProjection":    rldom.get("marginProjection"),
        "runLineScore":        rldom.get("runLineScore"),
        "expectedRuns":        expected_runs,
        "recommendedLine":     line_sel.get("recommendedLine"),
        "sameGameCorrelation": pair_corr.get("sameGameCorrelation"),
        "underProfileScore":   (scoring_ctx.get("under_profile") or {}).get("underProfileScore"),
    }
    if rldom.get("recommend"):
        pick_type_ctx["market"] = "Run Line -1.5"
    elif ov_side in ("OVER", "UNDER"):
        pick_type_ctx["market"] = f"Total Runs {ov_side.title()}"
    pick_type = classify_pick_type(pick_type_ctx)

    # ────────────────────────────────────────────────────────────────────
    # BUGFIX — resolve `coverProbability` to MATCH the recommended market.
    # Previously the v2 payload always exposed the Run Line cover
    # probability, even when the recommended market was an Over/Under
    # totals line. Now totals markets use the Poisson totals probability;
    # Run Line markets keep the runLineDominance cover probability.
    # ────────────────────────────────────────────────────────────────────
    pt_code = (pick_type.get("type") or "").upper()
    totals_pick = pt_code in {"SMART_LOW_OVER", "PITCHER_UNDER", "TEAM_TOTAL_EDGE", "F5_EDGE"} \
                  or (line_sel.get("recommendedLine") and rldom.get("recommend") is False)

    if totals_pick and line_sel.get("coverProbability") is not None:
        resolved_cover_prob   = line_sel.get("coverProbability")
        resolved_probability_model = line_sel.get("probabilityModel") or "Poisson"
        resolved_edge_vs_line = line_sel.get("edgeVsLine")
        resolved_prob_under   = line_sel.get("probabilityUnder")
        resolved_prob_over    = line_sel.get("probabilityOver")
        resolved_market_for_ui = line_sel.get("recommendedLine")
    elif rldom.get("recommend"):
        resolved_cover_prob   = rldom.get("coverProbability")
        resolved_probability_model = "RunLineDominance"
        resolved_edge_vs_line = rldom.get("marginProjection")
        resolved_prob_under   = None
        resolved_prob_over    = None
        resolved_market_for_ui = rldom.get("market")
    else:
        # No clear recommendation yet — surface totals probability if
        # smart selector ran, otherwise leave Run Line cover as fallback.
        if line_sel.get("coverProbability") is not None:
            resolved_cover_prob   = line_sel.get("coverProbability")
            resolved_probability_model = line_sel.get("probabilityModel") or "Poisson"
            resolved_edge_vs_line = line_sel.get("edgeVsLine")
            resolved_prob_under   = line_sel.get("probabilityUnder")
            resolved_prob_over    = line_sel.get("probabilityOver")
            resolved_market_for_ui = line_sel.get("recommendedLine")
        else:
            resolved_cover_prob   = rldom.get("coverProbability")
            resolved_probability_model = "RunLineDominance"
            resolved_edge_vs_line = rldom.get("marginProjection")
            resolved_prob_under   = None
            resolved_prob_over    = None
            resolved_market_for_ui = rldom.get("market")

    # Debug provenance — visible in backend logs AND surfaced to the UI
    # via `probabilityDebug` so the user can audit each recommendation.
    debug_provenance = {
        "projected_runs":         expected_runs,
        "recommended_market":     resolved_market_for_ui,
        "probability_model":      resolved_probability_model,
        "prob_under":             resolved_prob_under,
        "prob_over":              resolved_prob_over,
        "edge_vs_line":           resolved_edge_vs_line,
        "cover_probability":      resolved_cover_prob,
        "pick_type":              pt_code or None,
    }
    log.info(
        "MLB v2 prob_debug: market=%s expRuns=%s probUnder=%s probOver=%s edge=%s cover=%s model=%s",
        resolved_market_for_ui, expected_runs,
        resolved_prob_under, resolved_prob_over,
        resolved_edge_vs_line, resolved_cover_prob,
        resolved_probability_model,
    )

    return {
        "pitcherCentered":      pcen,
        "runLineDominance":     rldom,
        "smartTotalLine":       line_sel,
        "sameGameCorrelation":  pair_corr.get("sameGameCorrelation"),
        "sameGameCorrelationReason": pair_corr.get("correlationReason"),
        "sameGameCorrelationBonus":  pair_corr.get("correlationBonus", 0),
        "pickType":             pick_type.get("type"),
        "pickTypeReason":       pick_type.get("reason"),
        "marginProjection":     rldom.get("marginProjection"),
        # BUGFIX — top-level `coverProbability` now matches the recommended
        # market (totals → Poisson P(side), Run Line → dominance cover).
        "coverProbability":     resolved_cover_prob,
        "runLineScore":         rldom.get("runLineScore"),
        "fragilityScore":       rldom.get("fragilityScore"),
        "expectedRuns":         expected_runs,
        "bestLine":             line_sel.get("bestLine"),
        "safeLine":             line_sel.get("safeLine"),
        "aggressiveLine":       line_sel.get("aggressiveLine"),
        "recommendedLine":      line_sel.get("recommendedLine"),
        "lineSafetyScore":      line_sel.get("lineSafetyScore"),
        # BUGFIX — new fields surfaced for UI / audit.
        "edgeVsLine":           resolved_edge_vs_line,
        "probabilityModel":     resolved_probability_model,
        "probabilityUnder":     resolved_prob_under,
        "probabilityOver":      resolved_prob_over,
        "probabilityDebug":     debug_provenance,
        "runLineCoverProbability": rldom.get("coverProbability"),   # legacy reference
        "reasons":              rldom.get("reasons") or [],
        "risks":                rldom.get("risks") or [],
    }


# ════════════════════════════════════════════════════════════════════════════
# 9. SIGNAL EMISSION (v2-specific)
# ════════════════════════════════════════════════════════════════════════════
def emit_v2_signals(v2_payload: dict) -> list[dict]:
    """Translate v2 outcomes into editorial_context signals using the catalog."""
    from .signal_catalog import make_signal

    out: list[dict] = []
    seen: set[str] = set()

    def _add(code: str, extra_explanation: Optional[str] = None) -> None:
        if code in seen:
            return
        sig = make_signal(code, sport="baseball")
        if not sig:
            return
        if extra_explanation:
            sig["extra_explanation"] = extra_explanation
        seen.add(code)
        out.append(sig)

    rldom = v2_payload.get("runLineDominance") or {}
    if rldom.get("signalTag") == "RUN_LINE_MARGIN_EDGE":
        _add("RUN_LINE_MARGIN_EDGE",
             extra_explanation=(
                 f"projectedMargin={rldom.get('marginProjection')}, "
                 f"runLineScore={rldom.get('runLineScore')}, "
                 f"cover≈{rldom.get('coverProbability')}%"
             ))

    line_sel = v2_payload.get("smartTotalLine") or {}
    if line_sel.get("signalTag") == "SMART_OVER_LINE_SELECTED":
        _add("SMART_OVER_LINE_SELECTED",
             extra_explanation=(
                 f"recommendedLine={line_sel.get('recommendedLine')} · "
                 f"safety={line_sel.get('lineSafetyScore')}/100"
             ))

    pcen = v2_payload.get("pitcherCentered") or {}
    for tag in (pcen.get("tags") or []):
        if tag in ("STRONG_STARTING_PITCHER_EDGE",
                    "PITCHER_MISMATCH_DETECTED",
                    "LINEUP_VS_PITCHER_EDGE"):
            _add(tag, extra_explanation=pcen.get("explanation"))

    if v2_payload.get("sameGameCorrelation") == "POSITIVE":
        _add("SAME_GAME_CORRELATED_PAIR",
             extra_explanation=v2_payload.get("sameGameCorrelationReason"))

    return out


__all__ = [
    "favorite_margin_profile",
    "run_line_dominance_model",
    "smart_total_line_selector",
    "totals_probability",
    "pitcher_centered_evaluation",
    "same_game_correlation_rule",
    "classify_pick_type",
    "mlb_parlay_builder",
    "build_v2_payload",
    "emit_v2_signals",
    "mlb_structural_data_quality",
    "MLB_ALLOWED_MARKETS",
]


# ════════════════════════════════════════════════════════════════════════════
# 10. STRUCTURAL DATA QUALITY (MLB-V5)
# ════════════════════════════════════════════════════════════════════════════
def mlb_structural_data_quality(scoring_ctx: dict, v2_payload: Optional[dict] = None) -> dict:
    """Score the structural completeness of an MLB game's data.

    The orchestrator uses this score to decide whether a game with **missing
    odds** can still be routed to ``structural_lean_requires_odds`` (manual
    review) instead of being silently discarded.

    Buckets
    -------
        ≥ 70 → COMPLETE         (full analysis allowed; picks/rescue path)
        50–69 → STRUCTURAL_OK   (manual-review path eligible)
        < 50 → INSUFFICIENT     (discard after full analysis)
    """
    reasons: list[str] = []
    score = 0
    v2_payload = v2_payload or {}

    pcen = v2_payload.get("pitcherCentered") or {}
    both_confirmed = bool(pcen.get("bothConfirmed"))
    if both_confirmed or (scoring_ctx.get("home_pitcher_stats") and scoring_ctx.get("away_pitcher_stats")):
        score += 35
        reasons.append("Ambos abridores confirmados (+35).")
    home_q = (scoring_ctx.get("home_pitcher_quality") or {}).get("score", 0)
    away_q = (scoring_ctx.get("away_pitcher_quality") or {}).get("score", 0)
    if home_q >= 30 and away_q >= 30:
        score += 15
        reasons.append("Pitcher stats suficientes (xERA/FIP/WHIP) (+15).")

    bull = (scoring_ctx.get("bullpen") or {}).get("score", 0)
    if bull >= 30:
        score += 15
        reasons.append("Bullpen data disponible (+15).")

    off_h = (scoring_ctx.get("offense_home") or {}).get("score", 0)
    off_a = (scoring_ctx.get("offense_away") or {}).get("score", 0)
    if off_h >= 30 and off_a >= 30:
        score += 15
        reasons.append("Splits ofensivos disponibles (+15).")

    park = scoring_ctx.get("park") or {}
    if park.get("park_runs_mult") not in (None, 1.0) or park.get("weather_score") not in (None, 50):
        score += 10
        reasons.append("Park factor + clima disponibles (+10).")

    # Historical detail enrichment availability bumps score too.
    if (scoring_ctx.get("baseball_historical_profile") or {}).get("available"):
        score += 10
        reasons.append("Perfil histórico últimos-15 disponible (+10).")

    score = max(0, min(100, score))
    if score >= 70:
        level = "COMPLETE"
    elif score >= 50:
        level = "STRUCTURAL_OK"
    else:
        level = "INSUFFICIENT"
    return {"score": score, "level": level, "reasons": reasons}
