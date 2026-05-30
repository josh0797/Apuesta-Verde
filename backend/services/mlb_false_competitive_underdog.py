"""MLB Engine — FALSE_COMPETITIVE_UNDERDOG_SCRIPT detector.

Diagnoses the canonical failure mode reported by the user:

    > Predicción: Underdog competitivo
    > Resultado:  Dominio claro del favorito (8-2)
    > Mercado:    Run Line +1.5
    > Caso real: Yankees (35-22) 8 — Athletics (27-30) 2

The script labelled the match as "underdog competitivo" and recommended
``Run Line +1.5 (underdog)``, but the favourite team had:
    • Top-tier offence (Yankees OPS / wRC+ over thresholds).
    • Underdog with weak bullpen.
    • Large offensive gap between favourite and underdog.

This module detects that combination and either:
    a) Penalises the confidence score of a Run Line +1.5 (underdog) pick.
    b) Blocks the pick outright (when the 3 factors are extreme) and
       proposes alternative markets that capitalise on favourite
       dominance (Favorite -1.5 / Favorite Team Total Over /
       Full Game Over / F5 Over).

User-specified thresholds:

    FAVORITE TOP OFFENCE
        - OPS ≥ 0.770 OR Offensive Score ≥ 65
        - Confirm with wRC+ ≥ 110 OR Top-10 MLB runs/game (when available)
    WEAK BULLPEN
        - ERA 7d ≥ 4.75 OR ERA 15d ≥ 4.50
        - Severity bonus if Bullpen Fatigue ≥ 60 OR Blow-up Rate ≥ 15%
    OFFENSIVE GAP
        - Moderate  ≥ 15
        - High      ≥ 20
        - Extreme   ≥ 25

Application rules:

    - PENALISE always when gap ≥ 15.
    - BLOCK Run Line +1.5 (underdog) only when:
          Favourite top-offence  AND  Underdog weak-bullpen  AND  gap ≥ 25
    - When BLOCKED, propose alternatives in this order:
          1. Favorite -1.5            (Run Line favourite)
          2. Favorite Team Total Over
          3. Full Game Over           (Total Runs Over)
          4. F5 Over                  (First 5 Innings Total Over)

Public surface:

    evaluate_false_competitive_underdog(
        scoring_ctx, chosen_market, *, v2_payload=None, over_discovery=None,
    ) -> dict   # see EVALUATION_PAYLOAD docstring below.

    build_alternative_markets_proposal(
        scoring_ctx, *, v2_payload, over_discovery,
    ) -> list[dict]

    apply_false_competitive_underdog_to_pick(
        chosen_market, scoring_ctx, *, v2_payload=None, over_discovery=None,
    ) -> tuple[dict, dict]   # (updated_chosen_market, evaluation_payload)

Pure functions — no IO, no DB writes.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger("mlb_false_competitive_underdog")


# ════════════════════════════════════════════════════════════════════════════
# Thresholds (user-specified)
# ════════════════════════════════════════════════════════════════════════════
TOP_OFFENCE_OPS                 = 0.770
TOP_OFFENCE_SCORE               = 65
TOP_OFFENCE_WRC                 = 110
TOP_OFFENCE_RUNS_PER_GAME_RANK  = 10     # top 10 in MLB
WEAK_BULLPEN_ERA_7D             = 4.75
WEAK_BULLPEN_ERA_15D            = 4.50
WEAK_BULLPEN_FATIGUE            = 60
WEAK_BULLPEN_BLOWUP_RATE_PCT    = 15.0   # %

GAP_MODERATE = 15.0
GAP_HIGH     = 20.0
GAP_EXTREME  = 25.0

PENALTY_BY_GAP = {
    "MODERATE": -10,
    "HIGH":     -18,
    "EXTREME":  -28,
}


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _market_is_run_line_underdog(chosen_market: Optional[dict]) -> bool:
    """Detect whether the current chosen market is `Run Line +1.5 (underdog)`
    or a close variant.
    """
    if not chosen_market or not isinstance(chosen_market, dict):
        return False
    text = " ".join([
        str(chosen_market.get("market") or ""),
        str(chosen_market.get("selection") or ""),
        str(chosen_market.get("rationale") or ""),
    ]).lower()
    if not text.strip():
        return False
    # Must contain "+1.5" and reference an underdog. We also accept the
    # plain "run line +1.5" wording without the explicit "underdog" tag
    # because the orchestrator sometimes drops the parenthetical.
    has_plus_one_five = bool(re.search(r"\+\s*1\.5", text))
    is_run_line       = "run line" in text or "runline" in text
    is_underdog       = "underdog" in text
    return has_plus_one_five and (is_underdog or is_run_line)


# ════════════════════════════════════════════════════════════════════════════
# Factor evaluators
# ════════════════════════════════════════════════════════════════════════════
def _detect_favorite_side(scoring_ctx: dict) -> str:
    """Return 'home' / 'away' for the favourite — falls back to the
    starter-quality comparison when an explicit ``favorite_side`` is
    missing.
    """
    fav = (scoring_ctx or {}).get("favorite_side")
    if fav in ("home", "away"):
        return fav
    h_q = _f(((scoring_ctx or {}).get("home_pitcher_quality") or {}).get("score", 50), 50)
    a_q = _f(((scoring_ctx or {}).get("away_pitcher_quality") or {}).get("score", 50), 50)
    # Higher starter quality is a proxy when nothing else is provided.
    return "home" if h_q >= a_q else "away"


def _favorite_offense_block(scoring_ctx: dict, favorite_side: str) -> dict:
    return (scoring_ctx or {}).get(f"offense_{favorite_side}") or {}


def _underdog_offense_block(scoring_ctx: dict, favorite_side: str) -> dict:
    underdog_side = "away" if favorite_side == "home" else "home"
    return (scoring_ctx or {}).get(f"offense_{underdog_side}") or {}


def evaluate_favorite_top_offense(scoring_ctx: dict,
                                   favorite_side: Optional[str] = None) -> dict:
    """Detect whether the favourite has a top-tier offence."""
    fav_side = favorite_side or _detect_favorite_side(scoring_ctx)
    off = _favorite_offense_block(scoring_ctx, fav_side)

    ops = _f(off.get("team_ops"))
    score = _f(off.get("score", 50), 50)
    wrc = _f(off.get("wrc_plus") or off.get("wrcPlus") or off.get("wrc"))
    runs_rank = _f(off.get("runs_per_game_rank") or off.get("rpg_rank") or 0)

    primary = ops >= TOP_OFFENCE_OPS or score >= TOP_OFFENCE_SCORE
    confirms: list[str] = []
    if wrc >= TOP_OFFENCE_WRC:
        confirms.append(f"wRC+ {wrc:.0f} ≥ {TOP_OFFENCE_WRC}")
    if 0 < runs_rank <= TOP_OFFENCE_RUNS_PER_GAME_RANK:
        confirms.append(f"Top-{int(runs_rank)} MLB en carreras/juego")
    if ops >= TOP_OFFENCE_OPS:
        confirms.append(f"OPS {ops:.3f} ≥ {TOP_OFFENCE_OPS:.3f}")
    if score >= TOP_OFFENCE_SCORE:
        confirms.append(f"Offensive Score {score:.0f} ≥ {TOP_OFFENCE_SCORE}")

    return {
        "is_top_offense":   bool(primary),
        "favorite_side":    fav_side,
        "ops":              round(ops, 3) if ops else None,
        "offensive_score":  round(score, 1),
        "wrc_plus":         round(wrc, 1) if wrc else None,
        "runs_rank":        int(runs_rank) if runs_rank else None,
        "confirms":         confirms,
    }


def evaluate_underdog_weak_bullpen(scoring_ctx: dict,
                                    favorite_side: Optional[str] = None) -> dict:
    """Detect whether the underdog's bullpen is weak (and how severe)."""
    fav_side = favorite_side or _detect_favorite_side(scoring_ctx)
    underdog_era_7d = _f(scoring_ctx.get("underdog_bullpen_era_7d"))
    underdog_era_15d = _f(scoring_ctx.get("underdog_bullpen_era_15d"))
    # Underdog's bullpen-block (favourite/underdog are stored under home/away in
    # scoring_ctx if available).
    udog_side = "away" if fav_side == "home" else "home"
    udog_bullpen = (scoring_ctx or {}).get(f"{udog_side}_bullpen") or {}
    fatigue = _f(udog_bullpen.get("fatigue_score") or scoring_ctx.get("bullpen", {}).get("fatigue_score"))
    blowup = _f(udog_bullpen.get("blowup_rate_pct") or udog_bullpen.get("blowup_rate"))
    if blowup and blowup <= 1.5:
        # `blowup_rate` was passed as a fraction (0.18). Convert to %.
        blowup = blowup * 100.0

    primary = (
        (underdog_era_7d >= WEAK_BULLPEN_ERA_7D)
        or (underdog_era_15d >= WEAK_BULLPEN_ERA_15D)
    )
    severity = "LOW"
    factors: list[str] = []

    if underdog_era_7d >= WEAK_BULLPEN_ERA_7D:
        factors.append(f"ERA 7d {underdog_era_7d:.2f} ≥ {WEAK_BULLPEN_ERA_7D:.2f}")
        severity = "MEDIUM"
    if underdog_era_15d >= WEAK_BULLPEN_ERA_15D:
        factors.append(f"ERA 15d {underdog_era_15d:.2f} ≥ {WEAK_BULLPEN_ERA_15D:.2f}")
        severity = "MEDIUM"
    if fatigue >= WEAK_BULLPEN_FATIGUE:
        factors.append(f"Bullpen Fatigue {fatigue:.0f} ≥ {WEAK_BULLPEN_FATIGUE}")
        severity = "HIGH"
    if blowup >= WEAK_BULLPEN_BLOWUP_RATE_PCT:
        factors.append(f"Blow-up Rate {blowup:.1f}% ≥ {WEAK_BULLPEN_BLOWUP_RATE_PCT}%")
        severity = "HIGH"

    return {
        "is_weak":   bool(primary),
        "severity":  severity if primary else "NONE",
        "era_7d":    round(underdog_era_7d, 2),
        "era_15d":   round(underdog_era_15d, 2),
        "fatigue":   round(fatigue, 1) if fatigue else None,
        "blowup_pct": round(blowup, 1) if blowup else None,
        "factors":   factors,
    }


def evaluate_offensive_gap(scoring_ctx: dict,
                            favorite_side: Optional[str] = None) -> dict:
    """Compute the signed offensive gap between favourite and underdog.

    Returns the *magnitude* (always positive) when the favourite is the
    stronger side, otherwise the sign flips (negative). We rely on the
    canonical ``offense_*.score`` 0-100 field.
    """
    fav_side = favorite_side or _detect_favorite_side(scoring_ctx)
    fav_off = _favorite_offense_block(scoring_ctx, fav_side)
    udog_off = _underdog_offense_block(scoring_ctx, fav_side)

    fav_score = _f(fav_off.get("score", 50), 50)
    udog_score = _f(udog_off.get("score", 50), 50)
    gap = round(fav_score - udog_score, 1)
    magnitude = abs(gap)

    if magnitude >= GAP_EXTREME:
        category = "EXTREME"
    elif magnitude >= GAP_HIGH:
        category = "HIGH"
    elif magnitude >= GAP_MODERATE:
        category = "MODERATE"
    else:
        category = "NONE"

    return {
        "gap":            gap,
        "magnitude":      magnitude,
        "category":       category,
        "favorite_score": round(fav_score, 1),
        "underdog_score": round(udog_score, 1),
        "favorite_side":  fav_side,
    }


# ════════════════════════════════════════════════════════════════════════════
# Master evaluator
# ════════════════════════════════════════════════════════════════════════════
EVALUATION_PAYLOAD = {
    "is_risk":              False,
    "severity":             "NONE",      # NONE | LOW | MODERATE | HIGH | EXTREME
    "gap_category":         "NONE",
    "gap_magnitude":        0.0,
    "favorite":             {},
    "underdog_bullpen":     {},
    "penalty":              0,
    "block_required":       False,
    "trap_signal_code":     None,
    "alternative_markets":  [],
    "narrative_es":         "",
    "version":              1,
}


def evaluate_false_competitive_underdog(
    scoring_ctx: dict,
    chosen_market: Optional[dict] = None,
    *,
    v2_payload: Optional[dict] = None,
    over_discovery: Optional[dict] = None,
) -> dict:
    """Run the full FALSE_COMPETITIVE_UNDERDOG check.

    The function returns a payload even when ``chosen_market`` is NOT a
    Run Line +1.5 underdog pick; in that case ``is_risk`` is False but
    the underlying factor signals (top_offense / weak_bullpen / gap) are
    still attached so the orchestrator can persist them for later
    feedback-loop calibration.
    """
    scoring_ctx = scoring_ctx or {}
    fav_side = _detect_favorite_side(scoring_ctx)
    fav     = evaluate_favorite_top_offense(scoring_ctx, favorite_side=fav_side)
    bullpen = evaluate_underdog_weak_bullpen(scoring_ctx, favorite_side=fav_side)
    gap     = evaluate_offensive_gap(scoring_ctx, favorite_side=fav_side)

    payload = dict(EVALUATION_PAYLOAD)
    payload["favorite"]          = fav
    payload["underdog_bullpen"]  = bullpen
    payload["gap_category"]      = gap["category"]
    payload["gap_magnitude"]     = gap["magnitude"]
    payload["gap"]               = gap["gap"]

    # Penalty applies whenever gap ≥ 15 AND favourite is the offensively
    # superior side (so we don't punish balanced match-ups).
    if gap["gap"] >= GAP_MODERATE and gap["category"] != "NONE":
        payload["penalty"] = PENALTY_BY_GAP.get(gap["category"], 0)

    # Block rule: must satisfy all 3 conditions AND be on a Run Line +1.5
    # underdog selection.
    is_target_market = _market_is_run_line_underdog(chosen_market)
    all_3_factors    = (
        fav["is_top_offense"]
        and bullpen["is_weak"]
        and gap["category"] == "EXTREME"
    )
    if is_target_market and all_3_factors:
        payload["block_required"] = True
        payload["severity"]       = "EXTREME"
        payload["trap_signal_code"] = "FALSE_COMPETITIVE_UNDERDOG_BLOCK"
    elif is_target_market and (
        (fav["is_top_offense"] and bullpen["is_weak"])
        or gap["category"] in ("HIGH", "EXTREME")
    ):
        payload["severity"] = "HIGH" if gap["category"] == "HIGH" else "MODERATE"
        payload["trap_signal_code"] = "FALSE_COMPETITIVE_UNDERDOG_RISK"
    elif gap["category"] in ("MODERATE", "HIGH", "EXTREME") and is_target_market:
        payload["severity"] = "LOW"
        payload["trap_signal_code"] = "FALSE_COMPETITIVE_UNDERDOG_WARN"

    # is_risk = any actionable severity AND we're looking at a Run Line +1.5 underdog
    payload["is_risk"] = (
        is_target_market
        and payload["severity"] in ("LOW", "MODERATE", "HIGH", "EXTREME")
    )

    # Always propose alternatives when block_required (so the orchestrator
    # can swap immediately). For non-block cases, still attach a shorter
    # list as user-facing advisory.
    if payload["block_required"]:
        payload["alternative_markets"] = build_alternative_markets_proposal(
            scoring_ctx, v2_payload=v2_payload, over_discovery=over_discovery,
            favorite_side=fav_side,
        )
    elif payload["is_risk"]:
        payload["alternative_markets"] = build_alternative_markets_proposal(
            scoring_ctx, v2_payload=v2_payload, over_discovery=over_discovery,
            favorite_side=fav_side,
        )[:2]

    payload["narrative_es"] = _build_narrative(payload, fav_side)
    payload["target_market_detected"] = is_target_market
    payload["favorite_side"]          = fav_side
    return payload


# ════════════════════════════════════════════════════════════════════════════
# Alternative-market proposal
# ════════════════════════════════════════════════════════════════════════════
def build_alternative_markets_proposal(
    scoring_ctx: dict,
    *,
    v2_payload: Optional[dict] = None,
    over_discovery: Optional[dict] = None,
    favorite_side: Optional[str] = None,
) -> list[dict]:
    """Return up to 4 alternative markets in priority order:

        1. Favorite -1.5            (Run Line favourite)
        2. Favorite Team Total Over
        3. Full Game Over
        4. F5 Over

    Each item includes a synthetic score + rationale so the orchestrator
    can pick the highest-confidence alternative for the swap.

    When ``over_discovery`` is supplied (V6 payload), we reuse its
    ``best_over_market`` to rank the Over options.
    """
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}
    fav_side = favorite_side or _detect_favorite_side(scoring_ctx)
    fav_team = (scoring_ctx.get("favorite_team")
                or (scoring_ctx.get("favorite_margin_profile") or {}).get("team")
                or ("local" if fav_side == "home" else "visitante"))

    fav_off = _favorite_offense_block(scoring_ctx, fav_side)
    fav_score = _f(fav_off.get("score", 50), 50)

    expected_runs = _f(v2_payload.get("expectedRuns") or v2_payload.get("expected_runs"))
    smart_line = _f(v2_payload.get("smartTotalsLine"))

    # Use V6 over_discovery payload when present for more accurate ranking.
    od_market = (over_discovery or {}).get("best_over_market") if over_discovery else None
    expl_score = (((over_discovery or {}).get("offensive_explosion") or {}).get("score")) or 50

    out: list[dict] = []

    # 1. Favorite -1.5
    rl_score = 65 + min(20, (fav_score - 60))           # >= 60 → bonus
    rl_score = max(40, min(90, rl_score))
    out.append({
        "market":     f"Run Line -1.5 ({fav_team})",
        "category":   "RUN_LINE_FAVORITE",
        "score":      round(rl_score, 1),
        "rationale":  (
            "Favorito top ofensivo vs underdog con bullpen débil "
            "y gap extremo: el favorito cubre -1.5 con alta frecuencia."
        ),
    })

    # 2. Favorite Team Total Over (use half of expected runs as proxy line).
    if expected_runs > 0:
        tt_line = round(expected_runs / 2.0 - 0.5, 1)
        tt_line = max(2.5, min(5.5, tt_line))
        tt_score = 60 + (fav_score - 50) * 0.6 + (expl_score - 50) * 0.3
        out.append({
            "market":     f"Team Total Over {tt_line} ({fav_team})",
            "category":   "TEAM_TOTAL_OVER_FAVORITE",
            "line":       tt_line,
            "score":      round(max(40, min(90, tt_score)), 1),
            "rationale":  (
                "Sólo el favorito tiene proyección ofensiva alta: su "
                "team-total Over capitaliza el desbalance sin riesgo del "
                "underdog."
            ),
        })

    # 3. Full Game Over (when V6 best_over already proposes it, prefer that line).
    fg_line = (od_market or {}).get("line") if od_market and "FULL_GAME" in (od_market.get("category") or "") \
              else (smart_line or 9.0)
    fg_score = 55 + (expl_score - 50) * 0.5
    if od_market and od_market.get("category") == "OVER_FULL_GAME" and od_market.get("score"):
        fg_score = max(fg_score, _f(od_market.get("score")) * 0.7)
    out.append({
        "market":     f"Total Runs Over {fg_line}",
        "category":   "FULL_GAME_OVER",
        "line":       fg_line,
        "score":      round(max(40, min(90, fg_score)), 1),
        "rationale":  (
            "Bullpen del underdog vulnerable + ofensiva favorita top: "
            "Full Game Over con edge estructural."
        ),
    })

    # 4. F5 Over (favours scenarios where favourite's starter is also strong).
    f5_line = round((expected_runs * 5.0 / 9.0) if expected_runs > 0 else 4.5, 1)
    f5_line = max(3.5, min(5.5, f5_line))
    f5_score = 50 + (expl_score - 50) * 0.4
    out.append({
        "market":     f"F5 Total Over {f5_line}",
        "category":   "F5_OVER",
        "line":       f5_line,
        "score":      round(max(40, min(85, f5_score)), 1),
        "rationale":  (
            "F5 Over si los abridores no contienen la ofensiva top del "
            "favorito en las primeras 5 entradas."
        ),
    })

    # Sort by score so the orchestrator picks the strongest alternative first.
    out.sort(key=lambda m: m.get("score") or 0, reverse=True)
    return out


# ════════════════════════════════════════════════════════════════════════════
# Apply helper — orchestrator-friendly
# ════════════════════════════════════════════════════════════════════════════
def apply_false_competitive_underdog_to_pick(
    chosen_market: Optional[dict],
    scoring_ctx: dict,
    *,
    v2_payload: Optional[dict] = None,
    over_discovery: Optional[dict] = None,
) -> tuple[Optional[dict], dict]:
    """Return ``(updated_chosen_market, evaluation_payload)``.

    Behaviour:
      • When ``block_required``: replace the chosen market with the
        highest-scoring alternative (Favorite -1.5 / Team Total Over /
        Full Game Over / F5 Over) and stamp ``false_competitive_underdog_swap=True``.
      • When ``is_risk`` only: apply ``penalty`` to chosen_market.score
        (clamped to 0..100) and stamp a ``false_competitive_underdog_meta``.
      • When no risk: return the chosen_market untouched.
    """
    evaluation = evaluate_false_competitive_underdog(
        scoring_ctx,
        chosen_market,
        v2_payload=v2_payload,
        over_discovery=over_discovery,
    )

    if not chosen_market:
        return chosen_market, evaluation

    if evaluation["block_required"]:
        alternatives = evaluation["alternative_markets"]
        if alternatives:
            best = alternatives[0]
            new_chosen = {
                "market":     best.get("market"),
                "selection":  best.get("category") or chosen_market.get("selection"),
                "score":      best.get("score", chosen_market.get("score", 0)),
                "rationale":  (
                    (chosen_market.get("rationale") or "")
                    + " | BLOQUEO Run Line +1.5 underdog (false-competitive). "
                    + best.get("rationale", "")
                ).strip(" |"),
                "false_competitive_underdog_swap": True,
                "false_competitive_underdog_meta": evaluation,
                "previous_market":  chosen_market.get("market"),
                "alt_markets":      [a["market"] for a in alternatives[1:]],
            }
            return new_chosen, evaluation

    if evaluation["is_risk"]:
        # Penalty only — keep the chosen_market but lower its score and
        # attach the evaluation meta + trap signal code.
        adj_score = max(0, min(100,
                               _f(chosen_market.get("score"), 0) + evaluation["penalty"]))
        updated = dict(chosen_market)
        updated["score"] = round(adj_score, 1)
        updated["false_competitive_underdog_meta"] = evaluation
        updated["rationale"] = (
            (chosen_market.get("rationale") or "")
            + f" | PENALIZACIÓN false-competitive (gap {evaluation['gap_category']}). "
            + evaluation["narrative_es"]
        ).strip(" |")
        return updated, evaluation

    return chosen_market, evaluation


# ════════════════════════════════════════════════════════════════════════════
# Narrative
# ════════════════════════════════════════════════════════════════════════════
def _build_narrative(payload: dict, favorite_side: str) -> str:
    if not payload or payload["severity"] == "NONE":
        return ""
    fav = payload.get("favorite") or {}
    bp  = payload.get("underdog_bullpen") or {}
    gap = payload.get("gap_magnitude")
    cat = payload.get("gap_category") or "NONE"
    pieces: list[str] = []
    if fav.get("is_top_offense"):
        pieces.append(
            f"Favorito top ofensivo (OPS {fav.get('ops')}, "
            f"score {fav.get('offensive_score')})."
        )
    if bp.get("is_weak"):
        pieces.append(
            f"Bullpen del underdog frágil (ERA7d {bp.get('era_7d')})."
        )
    if cat in ("MODERATE", "HIGH", "EXTREME"):
        pieces.append(f"Gap ofensivo {cat.lower()} ({gap:.0f} pts).")
    if payload["block_required"]:
        pieces.append(
            "BLOQUEO: Run Line +1.5 underdog descartado; el script "
            "no debería sostenerse 9 innings."
        )
    elif payload["is_risk"]:
        pieces.append(
            f"Penalización aplicada ({payload['penalty']} pts) — "
            "la cobertura +1.5 sigue posible pero con menor confianza."
        )
    return " ".join(pieces)


__all__ = [
    "evaluate_false_competitive_underdog",
    "evaluate_favorite_top_offense",
    "evaluate_underdog_weak_bullpen",
    "evaluate_offensive_gap",
    "build_alternative_markets_proposal",
    "apply_false_competitive_underdog_to_pick",
    "PENALTY_BY_GAP",
    "TOP_OFFENCE_OPS",
    "TOP_OFFENCE_SCORE",
    "WEAK_BULLPEN_ERA_7D",
    "WEAK_BULLPEN_ERA_15D",
    "GAP_MODERATE",
    "GAP_HIGH",
    "GAP_EXTREME",
]
