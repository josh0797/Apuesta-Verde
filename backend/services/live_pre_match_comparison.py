"""
LivePreMatchComparisonLayer
===========================

Compares the **real live state** of a match against the **pregame
analysis / pick** so the UI can classify whether the game is following
the engine's script, deviating, or breaking it — and decide if the
pregame pick is still actionable.

Output (canonical `script_comparison` payload)::

    {
        "script_status":         "on_script" | "soft_deviation"
                               | "hard_deviation" | "broken_script"
                               | "insufficient_data",
        "pregame_pick_status":   "pending" | "already_won" | "already_lost"
                               | "still_playable" | "not_actionable",
        "live_recommendation_status": "actionable" | "wait" | "avoid"
                                    | "hedge" | "cashout_watch",
        "score_delta":           number   # actual_total - expected_total
        "pace_delta":            number   # +pos = faster than expected
        "risk_delta":            number   # +pos = riskier than pregame
        "fragility_delta":       number,
        "confidence_adjustment": int      # delta to apply to LLM conf
        "reason_codes":          list[str],
        "human_summary_es":      str,
        "human_summary_en":      str,
        "validator":             { ...market state validator output... },
    }

Design
------
* Pure / deterministic — receives `pregame_pick` + `live_state` + `sport`
  and produces the payload. No I/O, easy to unit-test.
* Sport-aware: only baseball is fully wired today (we have explicit
  expected_runs / inning / outs). Football & basketball fall back to the
  market-state validator + a coarse `score_delta` so the UI still has
  *something* to render.
* Fail-soft: every branch is guarded; on error we return
  `script_status="insufficient_data"` with the original pregame pick
  untouched so the page never breaks.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from services.live_market_state_validator import validate_market_state

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Sport-specific helpers
# ──────────────────────────────────────────────────────────────────────────

def _baseball_expected_total_through(inning_n: Optional[int], expected_runs: Optional[float]) -> Optional[float]:
    """Linearly interpolate `expected_runs` through `inning_n` of 9.

    e.g. ER=8.0 in the 7th → expected 8 × 7/9 ≈ 6.22.
    """
    if inning_n is None or expected_runs is None:
        return None
    try:
        return round(float(expected_runs) * max(0, min(9, int(inning_n))) / 9.0, 2)
    except (TypeError, ValueError):
        return None


def _football_expected_total_through(minute: Optional[int], expected_goals: Optional[float]) -> Optional[float]:
    if minute is None or expected_goals is None:
        return None
    try:
        return round(float(expected_goals) * max(0, min(90, int(minute))) / 90.0, 2)
    except (TypeError, ValueError):
        return None


def _basketball_expected_total_through(quarter: Optional[int], expected_total: Optional[float]) -> Optional[float]:
    if quarter is None or expected_total is None:
        return None
    try:
        # quarters → minutes (12 ea), totals scale linearly.
        return round(float(expected_total) * max(0, min(4, int(quarter))) / 4.0, 2)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────────────
# Script-status classifier
# ──────────────────────────────────────────────────────────────────────────

def _classify_script(score_delta: Optional[float], sport: str) -> str:
    """Map a `score_delta` (actual - expected) to a script-status label.

    The thresholds reflect the natural variance of each sport.
    """
    if score_delta is None:
        return "insufficient_data"
    abs_d = abs(score_delta)
    if sport == "baseball":
        if abs_d <= 1.5: return "on_script"
        if abs_d <= 3.0: return "soft_deviation"
        if abs_d <= 5.0: return "hard_deviation"
        return "broken_script"
    if sport == "football":
        if abs_d <= 0.5: return "on_script"
        if abs_d <= 1.0: return "soft_deviation"
        if abs_d <= 2.0: return "hard_deviation"
        return "broken_script"
    if sport == "basketball":
        if abs_d <= 10.0: return "on_script"
        if abs_d <= 20.0: return "soft_deviation"
        if abs_d <= 30.0: return "hard_deviation"
        return "broken_script"
    # Fallback — be conservative.
    if abs_d <= 1.0: return "on_script"
    if abs_d <= 2.5: return "soft_deviation"
    return "hard_deviation"


# ──────────────────────────────────────────────────────────────────────────
# P4 — Pick-direction-aware deviation classifier
# ──────────────────────────────────────────────────────────────────────────
def _pick_direction_vs_deviation(
    *,
    market: str,
    selection: str,
    score_delta: Optional[float],
    sport: str,
) -> str:
    """Decide whether ``score_delta`` is FAVOURABLE or ADVERSE for the pick.

    Returns one of ``"favorable" | "adverse" | "neutral"``.

    Logic (universal across sports — uses "Over/Under/Mas/Menos" tokens):

    * ``Over``   pick + ``score_delta < 0`` → adverse  (pace below expected)
    * ``Over``   pick + ``score_delta > 0`` → favorable
    * ``Under``  pick + ``score_delta < 0`` → favorable (game running cold)
    * ``Under``  pick + ``score_delta > 0`` → adverse
    * ML / spread / no-direction → neutral
    """
    if score_delta is None:
        return "neutral"
    text = f"{market or ''} {selection or ''}".lower()
    is_over  = ("over" in text) or ("más de" in text) or ("mas de" in text)
    is_under = ("under" in text) or ("menos de" in text)
    # Run-Line / spread / moneyline don't depend on pace.
    if not (is_over or is_under):
        return "neutral"
    # Team-total Over/Under works the same way as game total wrt pace,
    # since the script's expected_total compares to the GAME total. For
    # team totals we'd ideally check the team's contribution alone, but
    # the legacy script doesn't track per-side projections — keep neutral
    # in that case to avoid mislabeling.
    if "team" in text and ("total" in text or "tt" in text):
        return "neutral"
    if is_over:
        return "favorable" if score_delta > 0 else "adverse"
    # Under
    return "favorable" if score_delta < 0 else "adverse"


# ──────────────────────────────────────────────────────────────────────────
# Live verdict — the actionable chip the user sees on the live page
# ──────────────────────────────────────────────────────────────────────────
# Verdicts canonicalised per user spec (2026-06):
#     PICK_ALREADY_LOST       — pregame pick is dead (validator resolved loss)
#     PICK_ALREADY_WON        — pregame pick already cashed (resolved win)
#     AVOID_UNDER_OR_LOOK_OVER — pregame Under, live offensive script
#     AVOID_OVER_OR_CASHOUT   — pregame Over, live offence collapsed
#     MAINTAIN                — on-script + pick still playable
#     CASHOUT                 — soft/hard deviation but still playable
#     NO_ACTIONABLE           — final w/o resolution or no pregame pick
def _derive_live_verdict(
    *,
    pregame_status: str,
    script_status: str,
    market: str,
    actual_total: Optional[int],
    expected_through: Optional[float],
    is_final: bool,
) -> str:
    if pregame_status == "already_won":
        return "PICK_ALREADY_WON"
    if pregame_status == "already_lost":
        return "PICK_ALREADY_LOST"
    if pregame_status == "not_evaluable":
        # P4: no valid pregame base → tell user to use live reading only.
        return "USE_LIVE_READ_ONLY"
    if is_final:
        return "NO_ACTIONABLE"
    if pregame_status == "not_actionable":
        return "NO_ACTIONABLE"
    if pregame_status == "at_risk":
        # P4: adverse hard-dev / broken → CASHOUT first, then directional advice.
        market_lc = (market or "").lower()
        is_under = "under" in market_lc or "menos de" in market_lc
        is_over  = "over"  in market_lc or "más de"   in market_lc or "mas de" in market_lc
        if is_under and actual_total is not None and expected_through is not None and actual_total > expected_through:
            return "AVOID_UNDER_OR_LOOK_OVER"
        if is_over and actual_total is not None and expected_through is not None and actual_total < expected_through:
            return "AVOID_OVER_OR_CASHOUT"
        return "CASHOUT"

    market_lc = (market or "").lower()
    is_under = "under" in market_lc or "menos de" in market_lc
    is_over  = "over"  in market_lc or "más de"   in market_lc or "mas de" in market_lc

    # P4: favourable hard-deviation variants — pick is alive, just don't add exposure.
    if script_status in ("hard_deviation_favorable", "broken_script_favorable"):
        return "MAINTAIN"

    if script_status == "broken_script":
        # Game blown out vs pregame projection — if pregame was Under and
        # actual >> expected the user should pivot Over, and vice-versa.
        if is_under and actual_total is not None and expected_through is not None and actual_total > expected_through:
            return "AVOID_UNDER_OR_LOOK_OVER"
        if is_over and actual_total is not None and expected_through is not None and actual_total < expected_through:
            return "AVOID_OVER_OR_CASHOUT"
        return "CASHOUT"
    if script_status == "hard_deviation":
        if is_under and actual_total is not None and expected_through is not None and actual_total > expected_through:
            return "AVOID_UNDER_OR_LOOK_OVER"
        if is_over and actual_total is not None and expected_through is not None and actual_total < expected_through:
            return "AVOID_OVER_OR_CASHOUT"
        return "CASHOUT"
    if script_status == "soft_deviation":
        return "CASHOUT"
    if script_status == "on_script":
        return "MAINTAIN"
    return "NO_ACTIONABLE"


def _live_reco_from(
    *,
    pregame_status: str,
    script_status: str,
    market_state: str,
) -> str:
    """Decide what to tell the user to DO right now."""
    if pregame_status in ("already_won", "already_lost"):
        # Pick is settled — no action needed beyond reviewing the result.
        return "avoid"
    if pregame_status == "not_actionable":
        return "wait"
    if pregame_status == "not_evaluable":
        # P4: no valid pregame base → user must rely on live reading only.
        return "wait"
    if pregame_status == "at_risk":
        # Adverse hard-deviation / broken script → suggest cashout / hedge.
        return "hedge"
    if script_status == "broken_script":
        return "avoid"
    if script_status in ("hard_deviation_favorable", "broken_script_favorable"):
        # Favourable strong deviation → keep but don't add exposure.
        return "actionable"
    if script_status == "hard_deviation":
        return "hedge"
    if script_status == "soft_deviation" and market_state == "still_playable":
        return "cashout_watch"
    if script_status == "on_script" and market_state == "still_playable":
        return "actionable"
    return "wait"


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def compare_pregame_vs_live(
    *,
    pregame_pick: Optional[dict],
    live_state: Optional[dict],
    sport: str = "baseball",
) -> dict:
    """Produce the canonical `script_comparison` payload.

    `pregame_pick` shape (subset)::
        {
            "recommendation": {"market": "Más de 7.5 carreras", "confidence_score": 68, ...},
            "_mlb_script_v2": {"expectedRuns": 8.4, ...},
            "fragility_score": 30,
            ...
        }
    `live_state` shape (matches mlb_live_state.fetch_live_state output).
    """
    base = {
        "script_status":              "insufficient_data",
        "pregame_pick_status":        "pending",
        "live_recommendation_status": "wait",
        "score_delta":                None,
        "pace_delta":                 None,
        "risk_delta":                 0,
        "fragility_delta":            0,
        "confidence_adjustment":      0,
        "reason_codes":               [],
        "human_summary_es":           "Datos live insuficientes para comparar contra el análisis previo.",
        "human_summary_en":           "Insufficient live data to compare against the pregame analysis.",
        "validator":                  None,
    }

    if not pregame_pick or not isinstance(pregame_pick, dict):
        base["reason_codes"].append("NO_PREGAME_PICK")
        base["human_summary_es"] = "No hay análisis pregame disponible. Recomendación basada solo en datos live."
        base["human_summary_en"] = "No pregame analysis available. Recommendation based on live data only."
        return base

    rec        = pregame_pick.get("recommendation") or {}
    market     = rec.get("market") or rec.get("selection") or ""
    selection  = rec.get("selection") or ""
    v2         = pregame_pick.get("_mlb_script_v2") or {}
    expected_total = (
        v2.get("expectedRuns")  # baseball
        or pregame_pick.get("expected_goals")          # football pregame ER analogue
        or pregame_pick.get("expected_points_total")   # basketball
    )

    # ── Pull current live snapshot in a sport-agnostic shape ─────────────
    ls = live_state or {}
    score   = ls.get("score") or {}
    home_s  = score.get("home")
    away_s  = score.get("away")
    is_live = bool(ls.get("is_live"))
    is_final = (ls.get("state") == "final" or
                str(ls.get("status") or "").lower() == "final")

    # Sport-specific period (inning / minute / quarter).
    period_n: Optional[int] = None
    if sport == "baseball":
        inning = ls.get("inning") or {}
        period_n = inning.get("number")
        expected_through = _baseball_expected_total_through(period_n, expected_total)
    elif sport == "football":
        period_n = ls.get("minute")
        expected_through = _football_expected_total_through(period_n, expected_total)
    elif sport == "basketball":
        period_n = ls.get("quarter")
        expected_through = _basketball_expected_total_through(period_n, expected_total)
    else:
        expected_through = None

    # ── 1) Market-state validator (Layer A) ──────────────────────────────
    validator = validate_market_state(
        market,
        selection_label=selection,
        home_score=home_s, away_score=away_s,
        sport=sport, inning_or_minute=period_n,
        is_final=is_final,
    )

    # ── 2) Score / pace deltas ───────────────────────────────────────────
    if home_s is not None and away_s is not None:
        actual_total = home_s + away_s
        if expected_through is not None:
            score_delta = round(actual_total - expected_through, 2)
            pace_delta  = round(actual_total - expected_through, 2)
        else:
            score_delta, pace_delta = None, None
    else:
        score_delta, pace_delta = None, None
        actual_total = None

    script_status = _classify_script(score_delta, sport)
    # When the game is FINAL and we have a final score, we should NOT
    # render "insufficient_data" — the validator can resolve the pick
    # (won/lost) even without per-inning pace data. Promote the script
    # status so the panel renders the resolution instead of the
    # confusing "no data" banner. Same when we have a clearly resolved
    # validator state (already_resolved_*) — that's actionable info.
    validator_state = validator.get("state") or "still_playable"
    if script_status == "insufficient_data" and actual_total is not None:
        if is_final or validator_state.startswith("already_resolved"):
            # Final-settled bucket — we know the outcome but not the per
            # inning trajectory. UI treats this as a non-confusing state.
            script_status = "final_settled"

    # ── 3) Pregame pick status (uses validator state) ────────────────────
    #
    # P4 polish: the legacy mapping below produced contradictory chips
    # when the script was in HARD_DEVIATION / BROKEN against the pick
    # (e.g. "Over 5.99" with 1 run in inning 7 → still_playable yet the
    # script is "hard_deviation"). We now classify whether the deviation
    # is FAVOURABLE or ADVERSE for the pick and:
    #
    #   * adverse + (hard_deviation | broken_script) → ``at_risk``
    #     (the UI renders "EN RIESGO" instead of "AÚN JUGABLE")
    #   * favourable + (hard_deviation | broken_script) → keeps
    #     ``still_playable`` but tags the script as
    #     ``hard_deviation_favorable`` / ``broken_script_favorable``
    #     so the UI shows the favourable variant.
    #   * pregame pick lacking odds/market → ``not_evaluable``
    #     (the UI renders "NO EVALUABLE" instead of an actionable chip).
    #
    # The function `_pick_direction_vs_deviation` returns:
    #   * "favorable"    — the deviation helps the pick
    #   * "adverse"      — the deviation hurts the pick
    #   * "neutral"      — couldn't decide / no directional pick
    deviation_dir = _pick_direction_vs_deviation(
        market=market, selection=selection,
        score_delta=score_delta, sport=sport,
    )

    # Detect an incomplete / unusable pregame base: no odds, no market, or
    # the pick is a structural lean awaiting odds. These must NOT be
    # rendered as "Aún jugable" — the user wants "NO EVALUABLE".
    has_market   = bool(market)
    has_odds     = bool(rec.get("odds_range") or rec.get("odds")
                         or rec.get("recommended_odds"))
    structural_only = bool(pregame_pick.get("manual_odds_review", {}).get("required")) \
                      or pregame_pick.get("_bucket") in (
                          "structural_lean_requires_odds",
                          "watchlist_manual_odds",
                      )
    pregame_incomplete = (not has_market) or (
        structural_only and not has_odds and not is_final
    )

    if validator.get("state") == "already_resolved_win":
        pregame_status = "already_won"
    elif validator.get("state") == "already_resolved_loss":
        pregame_status = "already_lost"
    elif validator.get("state") == "already_resolved_unknown":
        pregame_status = "not_actionable"
    elif is_final and validator.get("state") == "still_playable":
        # Final but validator can't be sure → mark as not_actionable.
        pregame_status = "not_actionable"
    elif pregame_incomplete and is_live:
        # P4: surface the "no valid pregame base" state — the UI maps
        # this to "NO EVALUABLE" and shows only the live reading.
        pregame_status = "not_evaluable"
    elif is_live and validator.get("actionable") and \
         script_status in ("broken_script", "hard_deviation") and \
         deviation_dir == "adverse":
        # Adverse hard-dev / broken script → the chip must reflect risk,
        # not "Aún jugable". UI renders "EN RIESGO".
        pregame_status = "at_risk"
    elif is_live and validator.get("actionable"):
        pregame_status = "still_playable"
    elif is_live and not validator.get("actionable"):
        pregame_status = "not_actionable"
    else:
        pregame_status = "pending"

    # P4: promote the script_status to its FAVORABLE variant when the
    # deviation is favourable to the pick. This avoids confusing chips
    # like "DESVIACIÓN FUERTE → SIGUE VIVO" without context.
    if deviation_dir == "favorable" and script_status in (
        "broken_script", "hard_deviation",
    ):
        script_status = f"{script_status}_favorable"

    # ── 4) Confidence adjustment + risk / fragility deltas ───────────────
    confidence_adjustment = 0
    risk_delta            = 0
    fragility_delta       = 0
    if script_status == "broken_script":
        confidence_adjustment = -25
        risk_delta            = +20
        fragility_delta       = +15
    elif script_status == "hard_deviation":
        confidence_adjustment = -12
        risk_delta            = +10
        fragility_delta       = +8
    elif script_status == "soft_deviation":
        confidence_adjustment = -5
        risk_delta            = +3
        fragility_delta       = +3

    # ── 5) Live recommendation status ────────────────────────────────────
    live_status = _live_reco_from(
        pregame_status=pregame_status,
        script_status=script_status,
        market_state=validator.get("state", "still_playable"),
    )

    # ── 6) Reason codes + human summary ──────────────────────────────────
    reason_codes: list[str] = []
    if validator.get("reason_code") and validator.get("reason_code") != "STILL_LIVE":
        reason_codes.append(validator["reason_code"])
    if script_status != "on_script":
        reason_codes.append(f"SCRIPT_{script_status.upper()}")
    if pregame_status in ("already_won", "already_lost"):
        reason_codes.append(f"PICK_{pregame_status.upper()}")
    if abs(score_delta or 0) > 0 and score_delta is not None:
        reason_codes.append("OVERPACE" if score_delta > 0 else "UNDERPACE")

    summary_es, summary_en = _human_summary(
        sport=sport, market=market, validator=validator,
        actual_total=actual_total, expected_through=expected_through,
        period_n=period_n, script_status=script_status,
        pregame_status=pregame_status, live_status=live_status,
    )

    # ── 7) Live verdict chip (canonical user spec) ────────────────────────
    live_verdict = _derive_live_verdict(
        pregame_status=pregame_status,
        script_status=script_status,
        market=market,
        actual_total=actual_total,
        expected_through=expected_through,
        is_final=is_final,
    )

    # ── 8) Surface raw live box-score so the UI can render hits/BB/HR/etc.
    # Reads the standard keys produced by `mlb_live_state.fetch_live_state`
    # and falls back gracefully when only the score is available. The UI
    # treats this as informational chips — anything absent simply hides.
    live_box = ls.get("box_score") or ls.get("linescore") or {}
    live_data = {
        "is_live":         is_live,
        "is_final":        is_final,
        "score_home":      home_s,
        "score_away":      away_s,
        "total_runs":      actual_total,
        "inning":          period_n,
        "inning_half":     (ls.get("inning") or {}).get("half") if sport == "baseball" else None,
        "hits_home":       (live_box.get("hits")    or {}).get("home")    if isinstance(live_box.get("hits"), dict)    else live_box.get("hits_home"),
        "hits_away":       (live_box.get("hits")    or {}).get("away")    if isinstance(live_box.get("hits"), dict)    else live_box.get("hits_away"),
        "errors_home":     (live_box.get("errors")  or {}).get("home")    if isinstance(live_box.get("errors"), dict)  else live_box.get("errors_home"),
        "errors_away":     (live_box.get("errors")  or {}).get("away")    if isinstance(live_box.get("errors"), dict)  else live_box.get("errors_away"),
        "walks_home":      live_box.get("walks_home")      or (live_box.get("walks")      or {}).get("home") if isinstance(live_box.get("walks"), dict)      else None,
        "walks_away":      live_box.get("walks_away")      or (live_box.get("walks")      or {}).get("away") if isinstance(live_box.get("walks"), dict)      else None,
        "home_runs_home":  live_box.get("home_runs_home")  or (live_box.get("home_runs")  or {}).get("home") if isinstance(live_box.get("home_runs"), dict)  else None,
        "home_runs_away":  live_box.get("home_runs_away")  or (live_box.get("home_runs")  or {}).get("away") if isinstance(live_box.get("home_runs"), dict)  else None,
        "pitches_home":    live_box.get("pitches_home"),
        "pitches_away":    live_box.get("pitches_away"),
        "strikeouts_home": live_box.get("strikeouts_home") or (live_box.get("strikeouts") or {}).get("home") if isinstance(live_box.get("strikeouts"), dict) else None,
        "strikeouts_away": live_box.get("strikeouts_away") or (live_box.get("strikeouts") or {}).get("away") if isinstance(live_box.get("strikeouts"), dict) else None,
    }
    # Hide all-None keys to keep the payload lean.
    live_data = {k: v for k, v in live_data.items() if v is not None}

    return {
        "script_status":              script_status,
        "pregame_pick_status":        pregame_status,
        "live_recommendation_status": live_status,
        "live_verdict":               live_verdict,
        "score_delta":                score_delta,
        "pace_delta":                 pace_delta,
        "risk_delta":                 risk_delta,
        "fragility_delta":            fragility_delta,
        "confidence_adjustment":      confidence_adjustment,
        "expected_total_through":     expected_through,
        "actual_total":               actual_total,
        "period_n":                   period_n,
        "reason_codes":               reason_codes,
        "human_summary_es":           summary_es,
        "human_summary_en":           summary_en,
        "validator":                  validator,
        "suggested_alternatives":     validator.get("suggested_alternatives") or [],
        "live_data":                  live_data,
    }


def _period_label(sport: str, n: Optional[int]) -> str:
    if n is None:
        return ""
    if sport == "baseball":
        return f"en el {n}º inning"
    if sport == "football":
        return f"en el minuto {n}"
    if sport == "basketball":
        return f"en el Q{n}"
    return f"({n})"


def _human_summary(
    *, sport: str, market: str, validator: dict,
    actual_total: Optional[int], expected_through: Optional[float],
    period_n: Optional[int], script_status: str,
    pregame_status: str, live_status: str,
) -> tuple[str, str]:
    """Build the Spanish / English narrative."""
    unit_es = {"baseball": "carreras", "football": "goles", "basketball": "puntos"}.get(sport, "puntos")
    unit_en = {"baseball": "runs", "football": "goals", "basketball": "points"}.get(sport, "points")
    period_es = _period_label(sport, period_n)

    # Case A — pick already settled.
    if pregame_status == "already_won":
        alts = validator.get("suggested_alternatives") or []
        alt_es = (" Evalúa líneas live superiores: " + ", ".join(alts) + ".") if alts else ""
        alt_en = (" Look at higher live lines: " + ", ".join(alts) + ".") if alts else ""
        return (
            f"El pick pregame {market!r} ya se cumplió {period_es} con {actual_total} {unit_es}. "
            f"No es accionable como apuesta activa.{alt_es}",
            f"Pregame pick {market!r} already settled with {actual_total} {unit_en}. "
            f"Not actionable as a live bet anymore.{alt_en}",
        )
    if pregame_status == "already_lost":
        return (
            f"El pick pregame {market!r} ya perdió ({actual_total} {unit_es} {period_es}).",
            f"Pregame pick {market!r} already lost ({actual_total} {unit_en}).",
        )

    # Case B — pick still playable / pending.
    delta_es = ""
    delta_en = ""
    if expected_through is not None and actual_total is not None:
        diff = actual_total - expected_through
        sign = "+" if diff > 0 else ""
        delta_es = f" Pace: {actual_total} vs esperado {expected_through} ({sign}{round(diff,2)})."
        delta_en = f" Pace: {actual_total} vs expected {expected_through} ({sign}{round(diff,2)})."

    script_label_es = {
        "on_script":         "El partido sigue el guion esperado.",
        "soft_deviation":    "Desviación leve respecto al guion.",
        "hard_deviation":    "Desviación fuerte respecto al guion.",
        "broken_script":     "El guion del partido está roto — no entrar live.",
        "final_settled":     "Partido finalizado — pick resuelto.",
        "insufficient_data": "Datos live insuficientes para confirmar el guion.",
    }.get(script_status, "")
    script_label_en = {
        "on_script":         "Game following the script.",
        "soft_deviation":    "Slight deviation from the script.",
        "hard_deviation":    "Strong deviation from the script.",
        "broken_script":     "Script broken — avoid live entry.",
        "final_settled":     "Game finished — pick settled.",
        "insufficient_data": "Insufficient live data to confirm the script.",
    }.get(script_status, "")

    reco_es = {
        "actionable":      "Pick aún jugable.",
        "wait":            "Esperar mejor línea o más datos.",
        "avoid":           "Evitar entrada live.",
        "hedge":           "Considerar cobertura/hedge.",
        "cashout_watch":   "Vigilar cashout — el guion se está moviendo.",
    }.get(live_status, "")
    reco_en = {
        "actionable":      "Pick still playable.",
        "wait":            "Wait for a better line or more data.",
        "avoid":           "Avoid live entry.",
        "hedge":           "Consider hedging.",
        "cashout_watch":   "Watch cashout — script is drifting.",
    }.get(live_status, "")

    return (
        f"{script_label_es} {reco_es}{delta_es}".strip(),
        f"{script_label_en} {reco_en}{delta_en}".strip(),
    )


__all__ = ["compare_pregame_vs_live"]
