"""
MLB Trend Interpreter — L5 vs L15 → señales accionables
=========================================================

Consume el payload producido por :mod:`mlb_recent_form_split`
(``recent_run_split`` + ``on_base_profile``) y devuelve una capa
**interpretada** que ayuda al engine y al usuario a entender qué
significa cada dato y cómo afecta al pick final.

Outputs principales::

    {
      "trend_decision": "SUPPORTS_UNDER" | "SUPPORTS_OVER" | "MIXED" | "NEUTRAL",
      "score_adjustment":      int,   # -15 .. +12 (clamp)
      "confidence_adjustment": int,   # -12 .. +6
      "over_support_score":    int,   # 0 .. 16
      "under_support_score":   int,   # 0 .. 16
      "volatility_warning":    str | None,
      "human_explanations":    [str, ...],     # 1-3 frases en español
      "decision_notes":        [str, ...],
      "mixed_signals":         {
          "has_mixed_signals": bool,
          "over_signals":      [str, ...],
          "under_signals":     [str, ...],
          "final_resolution":  "LEAN_OVER" | "LEAN_UNDER" | "NEUTRAL" | "MIXED",
      },
      "reason_codes":          [str, ...],
      "impact_on_final_pick":  str,            # frase corta
      "applies_to_market":     str,            # echo del market
    }

Reglas (ver doc del prompt):

* Combined ``times_on_base_delta_5_vs_15 >= +2.0`` → ``strong_rising``
  → ``over_support_score=16`` + ``explosive_risk_boost=12``.
* ``>=+1.5`` → ``moderate_rising`` (+10).
* ``<=-2.0`` → ``strong_declining`` → ``under_support_score=16``.
* ``<=-1.5`` → ``moderate_declining`` (+10).
* Else → stable (under_support=3 default).

Para mercados Runline +1.5 evalúa por separado underdog vs favorite.

Fail-soft: si los profiles vienen vacíos devuelve ``{}`` (el orquestador
y la UI ya saben ocultar el bloque).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────
STRONG_OB_RISE      = 2.0
MOD_OB_RISE         = 1.5
STRONG_OB_DECLINE   = -2.0
MOD_OB_DECLINE      = -1.5

STRONG_RUN_RISE     = 2.0
MOD_RUN_RISE        = 1.25
STRONG_RUN_DECLINE  = -2.0
MOD_RUN_DECLINE     = -1.25

SCORE_CLAMP_LOW   = -15
SCORE_CLAMP_HIGH  = 12

# Confidence Δ caps.
CONF_CLAMP_LOW   = -12
CONF_CLAMP_HIGH  = 6


def _is_under(market: Optional[str]) -> bool:
    if not market:
        return False
    m = market.lower()
    return "under" in m or "menos de" in m


def _is_over(market: Optional[str]) -> bool:
    if not market:
        return False
    m = market.lower()
    return "over" in m or "más de" in m or "mas de" in m


def _is_runline_plus_15(market: Optional[str]) -> bool:
    if not market:
        return False
    m = market.lower().replace(",", ".")
    return "+1.5" in m and ("runline" in m or "run line" in m or "rl" in m)


def _detect_market_kind(market: Optional[str]) -> str:
    """Categorise the market into one of:
      ``totals_full`` (Over/Under full game), ``totals_f5`` (F5 over/under),
      ``team_total``, ``nrfi``, ``yrfi``, ``runline_plus_15``, ``other``.
    """
    if not market:
        return "other"
    m = market.lower().replace(",", ".")
    if _is_runline_plus_15(market):
        return "runline_plus_15"
    if "nrfi" in m or ("no run" in m and "first inning" in m):
        return "nrfi"
    if "yrfi" in m or ("yes run" in m and "first inning" in m):
        return "yrfi"
    # F5 = first 5 innings (a.k.a. "1st 5 innings", "F5")
    if "f5" in m or "first 5" in m or "1st 5" in m or "primeras 5" in m or "5 entradas" in m:
        return "totals_f5"
    # Team total — distinct from full-game totals: market mentions a team name
    # OR contains a "team" qualifier. The orchestrator-side detection will
    # pass an explicit hint when available, but this heuristic catches the
    # common cases.
    if "team total" in m or "total equipo" in m or "team_total" in m:
        return "team_total"
    if "over" in m or "under" in m or "más de" in m or "menos de" in m or "mas de" in m:
        return "totals_full"
    return "other"


def _signal_strength(delta: Optional[float], high: float, mid: float) -> str:
    """Return a label for how strong a delta is (positive direction).

    ``high`` and ``mid`` are positive thresholds; negative behavior is
    symmetric.
    """
    if delta is None:
        return "unknown"
    if delta >= high:    return "strong_rising"
    if delta >= mid:     return "moderate_rising"
    if delta <= -high:   return "strong_declining"
    if delta <= -mid:    return "moderate_declining"
    return "stable"


# ── Per-side interpretation ──────────────────────────────────────────────
def _interpret_side(side_block: dict, label: str,
                    runs_delta: Optional[float] = None) -> dict:
    """Return the strength labels for hits/walks/HR/TOB/run trends for
    a single team side (home/away).

    ``runs_delta`` is the team's ``runs_scored_delta_5_vs_15`` extracted
    from ``recent_run_split`` (the parent block doesn't carry it in
    ``on_base_profile.home`` / ``away``).
    """
    out = {
        "label":            label,
        "run_trend":        "STABLE",
        "on_base_trend":    "STABLE",
        "hits_trend":       "stable",
        "walks_trend":      "stable",
        "home_runs_trend":  "stable",
        "tob_delta":        None,
        "hr_delta":         None,
        "hits_delta":       None,
        "walks_delta":      None,
        "runs_delta":       runs_delta,
    }
    if not side_block and runs_delta is None:
        return out

    out["run_trend"] = _trend_from_delta(
        runs_delta, MOD_RUN_RISE, MOD_RUN_DECLINE,
    )

    tob_delta = (side_block or {}).get("times_on_base_delta_5_vs_15")
    out["tob_delta"] = tob_delta
    out["on_base_trend"] = _trend_from_delta(
        tob_delta, MOD_OB_RISE, MOD_OB_DECLINE,
    )
    out["hits_delta"]  = (side_block or {}).get("hits_delta_5_vs_15")
    out["hits_trend"]  = _signal_strength(out["hits_delta"], STRONG_OB_RISE, MOD_OB_RISE)
    out["walks_delta"] = (side_block or {}).get("walks_delta_5_vs_15")
    out["walks_trend"] = _signal_strength(out["walks_delta"], 1.0, 0.5)
    out["hr_delta"]    = (side_block or {}).get("home_runs_delta_5_vs_15")
    out["home_runs_trend"] = _signal_strength(out["hr_delta"], 0.5, 0.25)
    return out


def _trend_from_delta(delta: Optional[float], up: float, down: float) -> str:
    if delta is None:
        return "STABLE"
    if delta >= up:    return "RISING"
    if delta <= down:  return "DECLINING"
    return "STABLE"


# ── Main interpretation ──────────────────────────────────────────────────
def interpret_recent_form(
    recent_run_split: Optional[dict],
    on_base_profile: Optional[dict],
    selected_market: Optional[str] = None,
    runline_context: Optional[dict] = None,
    *,
    f5_split: Optional[dict] = None,
    first_inning_split: Optional[dict] = None,
    team_total_context: Optional[dict] = None,
) -> dict:
    """Top-level entry point. Returns ``{}`` when no usable input.

    Extended in 2026-06 to support F5, team total and NRFI/YRFI
    markets via the optional kw-only arguments:

    * ``f5_split``           — per-team and combined F5 (innings 1-5) splits.
    * ``first_inning_split`` — per-team first-inning run/scored-rate.
    * ``team_total_context`` — for team-total markets, must contain
      ``team_side`` ("home"|"away") so the engine only weighs that
      team's data instead of the combined block.
    """
    if not recent_run_split and not on_base_profile:
        return {}

    run_split = recent_run_split or {}
    ob_block  = on_base_profile or {}
    combined  = ob_block.get("combined") or {}

    # Strength of the **combined** on-base pressure delta.
    combined_tob_delta = combined.get("times_on_base_delta_5_vs_15")
    pressure_strength  = _signal_strength(combined_tob_delta, STRONG_OB_RISE, MOD_OB_RISE)

    # Strength of the **combined** run-environment delta.
    combined_run_delta = run_split.get("total_runs_delta_5_vs_15")
    run_strength = _signal_strength(combined_run_delta, STRONG_RUN_RISE, MOD_RUN_RISE)

    # ── Base score / support ──────────────────────────────────────
    over_support = 0
    under_support = 3   # baseline favors Under in stable environments
    explosive_risk_boost = 0
    volatility_warning: Optional[str] = None
    reason_codes: list[str] = []
    explanations: list[str] = []
    decision_notes: list[str] = []

    if pressure_strength == "strong_rising":
        over_support += 16
        explosive_risk_boost += 12
        reason_codes.append("PRESSURE_STRONG_RISING")
        explanations.append(
            "Presión en base sube fuerte en los últimos 5 juegos respecto a los 15."
        )
    elif pressure_strength == "moderate_rising":
        over_support += 10
        explosive_risk_boost += 8
        reason_codes.append("PRESSURE_MODERATE_RISING")
        explanations.append(
            "Presión en base con tendencia al alza en los últimos 5 juegos."
        )
    elif pressure_strength == "strong_declining":
        under_support += 16
        reason_codes.append("PRESSURE_STRONG_DECLINING")
        explanations.append(
            "Presión en base cae fuerte recientemente — entorno de bajo scoring."
        )
    elif pressure_strength == "moderate_declining":
        under_support += 10
        reason_codes.append("PRESSURE_MODERATE_DECLINING")
        explanations.append(
            "Presión en base bajando moderadamente — el entorno se contrae."
        )
    else:
        reason_codes.append("PRESSURE_STABLE")

    if run_strength == "strong_rising":
        over_support += 8
        reason_codes.append("RUN_ENVIRONMENT_STRONG_RISING")
        explanations.append(
            "El total de carreras en L5 supera claramente al L15 — entorno ofensivo."
        )
    elif run_strength == "moderate_rising":
        over_support += 5
        reason_codes.append("RUN_ENVIRONMENT_MODERATE_RISING")
    elif run_strength == "strong_declining":
        under_support += 8
        reason_codes.append("RUN_ENVIRONMENT_STRONG_DECLINING")
        explanations.append(
            "El total de carreras en L5 cae frente a L15 — entorno defensivo reciente."
        )
    elif run_strength == "moderate_declining":
        under_support += 5
        reason_codes.append("RUN_ENVIRONMENT_MODERATE_DECLINING")

    # ── Per-side breakdown (used for runline + power-spike checks) ───
    home_run_delta = run_split.get("runs_scored_delta_5_vs_15_home")
    away_run_delta = run_split.get("runs_scored_delta_5_vs_15_away")
    home_side = _interpret_side(ob_block.get("home") or {}, "home", runs_delta=home_run_delta)
    away_side = _interpret_side(ob_block.get("away") or {}, "away", runs_delta=away_run_delta)

    # Power spike heuristic — HR rising on either side.
    if home_side["home_runs_trend"] in ("strong_rising", "moderate_rising") \
       or away_side["home_runs_trend"] in ("strong_rising", "moderate_rising"):
        over_support += 4
        explosive_risk_boost += 4
        reason_codes.append("HR_TREND_RISING")
        explanations.append(
            "Aumento de poder reciente: HR subiendo en al menos un equipo. "
            "Eleva el riesgo de inning explosivo."
        )

    # ── Decision ──────────────────────────────────────────────────────
    diff = over_support - under_support
    if diff >= 8:
        trend_decision = "SUPPORTS_OVER"
    elif diff <= -8:
        trend_decision = "SUPPORTS_UNDER"
    elif over_support > 0 and under_support > 0 and abs(diff) < 8:
        trend_decision = "MIXED"
        volatility_warning = (
            "Señales mixtas: hay datos a favor del Over y a favor del Under."
        )
    else:
        trend_decision = "NEUTRAL"

    # ── Score / confidence adjustment vs the selected market ─────────
    score_adjustment = 0
    confidence_adjustment = 0
    market_kind = _detect_market_kind(selected_market)
    # Allow caller to override detection (orchestrator already knows the type).
    if team_total_context and team_total_context.get("force_kind"):
        market_kind = team_total_context["force_kind"]

    is_runline = market_kind == "runline_plus_15"
    is_under   = _is_under(selected_market) and market_kind == "totals_full"
    is_over    = _is_over(selected_market)  and market_kind == "totals_full"
    is_f5      = market_kind == "totals_f5"
    is_team_total = market_kind == "team_total"
    is_nrfi    = market_kind == "nrfi"
    is_yrfi    = market_kind == "yrfi"

    if is_under:
        if trend_decision == "SUPPORTS_UNDER":
            confidence_adjustment += 6
            score_adjustment += 6
            reason_codes.append("TREND_SUPPORTS_UNDER")
            decision_notes.append("Tendencia reciente apoya el Under.")
        elif trend_decision == "SUPPORTS_OVER":
            confidence_adjustment += -12
            score_adjustment += -12
            reason_codes.append("TREND_CONTRADICTS_UNDER")
            decision_notes.append(
                "Tendencia reciente contradice el Under — revisa el pick."
            )
        elif trend_decision == "MIXED":
            confidence_adjustment += -4
            score_adjustment += -4
            reason_codes.append("MIXED_RECENT_TREND_VS_UNDER")
            decision_notes.append(
                "Señales mixtas vs Under — reduce confianza ligeramente."
            )

    elif is_over:
        if trend_decision == "SUPPORTS_OVER":
            confidence_adjustment += 6
            score_adjustment += 6
            reason_codes.append("TREND_SUPPORTS_OVER")
            decision_notes.append("Tendencia reciente apoya el Over.")
        elif trend_decision == "SUPPORTS_UNDER":
            confidence_adjustment += -12
            score_adjustment += -12
            reason_codes.append("TREND_CONTRADICTS_OVER")
            decision_notes.append(
                "Tendencia reciente contradice el Over — revisa el pick."
            )
        elif trend_decision == "MIXED":
            confidence_adjustment += -4
            score_adjustment += -4
            reason_codes.append("MIXED_RECENT_TREND_VS_OVER")

    elif is_runline:
        # Runline + 1.5 needs both underdog-can-compete and
        # favorite-not-exploding heuristics.
        rl = _evaluate_runline_plus_15(
            home_side, away_side,
            trend_decision=trend_decision,
            runline_context=runline_context or {},
        )
        score_adjustment += rl["adjustment"]
        confidence_adjustment += rl["confidence_delta"]
        reason_codes.extend(rl["reason_codes"])
        decision_notes.extend(rl["decision_notes"])

    elif is_f5:
        # F5 = first 5 innings total. Apply the same Over/Under
        # direction logic but with thresholds calibrated to a
        # smaller run environment (≈ 4 runs through inning 5).
        f5_eval = _evaluate_f5(
            f5_split=f5_split, market=selected_market,
            trend_decision=trend_decision,
        )
        score_adjustment += f5_eval["adjustment"]
        confidence_adjustment += f5_eval["confidence_delta"]
        reason_codes.extend(f5_eval["reason_codes"])
        decision_notes.extend(f5_eval["decision_notes"])

    elif is_team_total:
        # Team total: only weigh the picked team's per-side data.
        tt_eval = _evaluate_team_total(
            home_side=home_side, away_side=away_side,
            home_form_runs=run_split.get("runs_scored_delta_5_vs_15_home"),
            away_form_runs=run_split.get("runs_scored_delta_5_vs_15_away"),
            team_side=(team_total_context or {}).get("team_side"),
            market=selected_market,
        )
        score_adjustment += tt_eval["adjustment"]
        confidence_adjustment += tt_eval["confidence_delta"]
        reason_codes.extend(tt_eval["reason_codes"])
        decision_notes.extend(tt_eval["decision_notes"])

    elif is_nrfi or is_yrfi:
        nf_eval = _evaluate_nrfi_yrfi(
            first_inning_split=first_inning_split,
            is_nrfi=is_nrfi, market=selected_market,
        )
        score_adjustment += nf_eval["adjustment"]
        confidence_adjustment += nf_eval["confidence_delta"]
        reason_codes.extend(nf_eval["reason_codes"])
        decision_notes.extend(nf_eval["decision_notes"])

    # Clamps.
    score_adjustment      = max(SCORE_CLAMP_LOW,  min(SCORE_CLAMP_HIGH, score_adjustment))
    confidence_adjustment = max(CONF_CLAMP_LOW,   min(CONF_CLAMP_HIGH,  confidence_adjustment))

    # Mixed signals payload (UI ribbon).
    mixed = _build_mixed_signals_payload(
        run_strength      = run_strength,
        pressure_strength = pressure_strength,
        home_side         = home_side,
        away_side         = away_side,
        trend_decision    = trend_decision,
    )

    # Human summary — short one-liner combining all the above.
    if trend_decision == "SUPPORTS_OVER":
        human_summary = (
            "La tendencia reciente (carreras L5>L15 y/o presión en base subiendo) "
            "apoya un entorno ofensivo. Cuidado con Unders ajustados."
        )
    elif trend_decision == "SUPPORTS_UNDER":
        human_summary = (
            "La tendencia reciente apunta a entorno de bajo scoring "
            "(carreras y presión en base contrayéndose). Apoya Under."
        )
    elif trend_decision == "MIXED":
        human_summary = (
            "Señales encontradas: hay tendencia ofensiva en una métrica y "
            "defensiva en otra. Reduce confianza del pick estructural."
        )
    else:
        human_summary = (
            "Tendencias estables — no hay impulso reciente claro hacia Over u Under."
        )

    impact_on_final_pick = _impact_phrase(
        selected_market=selected_market,
        trend_decision=trend_decision,
        score_adjustment=score_adjustment,
        confidence_adjustment=confidence_adjustment,
    )

    return {
        "trend_decision":         trend_decision,
        "score_adjustment":       score_adjustment,
        "confidence_adjustment":  confidence_adjustment,
        "over_support_score":     min(over_support, 16),
        "under_support_score":    min(under_support, 16),
        "explosive_risk_boost":   explosive_risk_boost,
        "volatility_warning":     volatility_warning,
        "pressure_strength":      pressure_strength,
        "run_strength":           run_strength,
        "human_summary":          human_summary,
        "human_explanations":     explanations,
        "decision_notes":         decision_notes,
        "mixed_signals":          mixed,
        "reason_codes":           reason_codes,
        "impact_on_final_pick":   impact_on_final_pick,
        "applies_to_market":      selected_market or "",
        "market_kind":            market_kind,
        "per_side": {
            "home": home_side,
            "away": away_side,
        },
    }


# ── F5 evaluator ─────────────────────────────────────────────────────────
def _evaluate_f5(*, f5_split: Optional[dict], market: Optional[str],
                 trend_decision: str) -> dict:
    """Apply Over/Under direction logic on the F5 (first-5-innings)
    aggregated total. Uses a tighter Δ threshold than the full-game
    interpreter because F5 totals sit in the 3–5 run range.
    """
    out = {"adjustment": 0, "confidence_delta": 0,
           "reason_codes": [], "decision_notes": []}
    if not f5_split:
        out["reason_codes"].append("F5_NO_DATA")
        out["decision_notes"].append(
            "No hay datos F5 recientes — el ajuste por tendencia no aplica."
        )
        return out
    combined = f5_split.get("combined") or {}
    delta = combined.get("f5_runs_delta_5_vs_15")
    l5  = combined.get("f5_runs_avg_last_5")
    l15 = combined.get("f5_runs_avg_last_15")
    if delta is None:
        out["reason_codes"].append("F5_NO_DELTA")
        return out

    m = (market or "").lower().replace(",", ".")
    is_under = "under" in m or "menos" in m
    is_over  = "over"  in m or "más"   in m or "mas" in m

    # Calibrated for F5: rising = ≥ +0.8 runs/5-innings.
    if is_under:
        if delta >= 0.8:
            out["adjustment"]       -= 10
            out["confidence_delta"] -= 8
            out["reason_codes"].append("F5_RUN_ENV_RISING_VS_UNDER")
            out["decision_notes"].append(
                f"F5 total subiendo: L5={l5} vs L15={l15} (Δ +{delta}). Riesgo para Under F5."
            )
        elif delta <= -0.8:
            out["adjustment"]       += 6
            out["confidence_delta"] += 4
            out["reason_codes"].append("F5_RUN_ENV_DECLINING_VS_UNDER")
            out["decision_notes"].append(
                f"F5 total cayendo: L5={l5} vs L15={l15} (Δ {delta}). Apoya Under F5."
            )
    elif is_over:
        if delta >= 0.8:
            out["adjustment"]       += 6
            out["confidence_delta"] += 4
            out["reason_codes"].append("F5_RUN_ENV_RISING_VS_OVER")
            out["decision_notes"].append(
                f"F5 total subiendo: L5={l5} vs L15={l15}. Apoya Over F5."
            )
        elif delta <= -0.8:
            out["adjustment"]       -= 10
            out["confidence_delta"] -= 8
            out["reason_codes"].append("F5_RUN_ENV_DECLINING_VS_OVER")
            out["decision_notes"].append(
                f"F5 total cayendo: L5={l5} vs L15={l15}. Riesgo para Over F5."
            )
    if trend_decision == "MIXED":
        out["adjustment"]       -= 3
        out["confidence_delta"] -= 2
        out["reason_codes"].append("F5_MIXED_FULLGAME_SIGNALS")
    return out


# ── Team total evaluator ─────────────────────────────────────────────────
def _evaluate_team_total(*, home_side: dict, away_side: dict,
                         home_form_runs: Optional[float],
                         away_form_runs: Optional[float],
                         team_side: Optional[str],
                         market: Optional[str]) -> dict:
    """Apply directional adjustments only for the picked team's side.

    ``team_side`` must be "home" or "away". Without it, we fall back to
    a soft adjustment using the combined picture.
    """
    out = {"adjustment": 0, "confidence_delta": 0,
           "reason_codes": [], "decision_notes": []}
    if team_side not in ("home", "away"):
        out["reason_codes"].append("TEAM_TOTAL_SIDE_UNKNOWN")
        return out

    side = home_side if team_side == "home" else away_side
    runs_delta = home_form_runs if team_side == "home" else away_form_runs

    m = (market or "").lower().replace(",", ".")
    is_under = "under" in m or "menos" in m
    is_over  = "over"  in m or "más"   in m or "mas" in m

    # On-base pressure for THIS team is the primary signal.
    tob_delta = side.get("tob_delta")
    hr_trend  = side.get("home_runs_trend")

    if is_over:
        if (tob_delta is not None and tob_delta >= MOD_OB_RISE) or \
           (runs_delta is not None and runs_delta >= MOD_RUN_RISE):
            out["adjustment"]       += 6
            out["confidence_delta"] += 4
            out["reason_codes"].append("TEAM_TOTAL_TREND_SUPPORTS_OVER")
            out["decision_notes"].append(
                f"El equipo {team_side} viene subiendo (Δ TOB {tob_delta}, Δ runs {runs_delta})."
            )
        elif (tob_delta is not None and tob_delta <= MOD_OB_DECLINE) or \
             (runs_delta is not None and runs_delta <= MOD_RUN_DECLINE):
            out["adjustment"]       -= 8
            out["confidence_delta"] -= 6
            out["reason_codes"].append("TEAM_TOTAL_TREND_CONTRADICTS_OVER")
            out["decision_notes"].append(
                f"El equipo {team_side} viene cayendo — riesgo para el team-total Over."
            )
        if hr_trend in ("strong_rising", "moderate_rising"):
            out["adjustment"]       += 2
            out["reason_codes"].append("TEAM_TOTAL_HR_RISING")
    elif is_under:
        if (tob_delta is not None and tob_delta <= MOD_OB_DECLINE) or \
           (runs_delta is not None and runs_delta <= MOD_RUN_DECLINE):
            out["adjustment"]       += 6
            out["confidence_delta"] += 4
            out["reason_codes"].append("TEAM_TOTAL_TREND_SUPPORTS_UNDER")
            out["decision_notes"].append(
                f"El equipo {team_side} en baja reciente — apoya team-total Under."
            )
        elif (tob_delta is not None and tob_delta >= MOD_OB_RISE) or \
             (runs_delta is not None and runs_delta >= MOD_RUN_RISE):
            out["adjustment"]       -= 8
            out["confidence_delta"] -= 6
            out["reason_codes"].append("TEAM_TOTAL_TREND_CONTRADICTS_UNDER")
            out["decision_notes"].append(
                f"El equipo {team_side} viene subiendo — contradice team-total Under."
            )
    return out


# ── NRFI / YRFI evaluator ────────────────────────────────────────────────
def _evaluate_nrfi_yrfi(*, first_inning_split: Optional[dict],
                        is_nrfi: bool, market: Optional[str]) -> dict:
    """Adjust score for NRFI/YRFI markets based on 1st-inning history.

    Uses ``yrfi_rate_last_15`` (P(any team scores in 1st) over L15) as
    the baseline and the L5 delta to detect a recent shift.
    """
    out = {"adjustment": 0, "confidence_delta": 0,
           "reason_codes": [], "decision_notes": []}
    if not first_inning_split:
        out["reason_codes"].append("NRFI_NO_DATA")
        return out
    combined = first_inning_split.get("combined") or {}
    yrfi_l15 = combined.get("yrfi_rate_last_15")
    yrfi_l5  = combined.get("yrfi_rate_last_5")
    if yrfi_l15 is None:
        out["reason_codes"].append("NRFI_NO_BASELINE")
        return out

    pct = round(yrfi_l15 * 100.0, 1)
    target = "NRFI" if is_nrfi else "YRFI"

    # Baseline anchor — historically ~50-60% of MLB games are YRFI.
    if is_nrfi:
        if yrfi_l15 <= 0.40:
            out["adjustment"]       += 8
            out["confidence_delta"] += 5
            out["reason_codes"].append("NRFI_LOW_BASELINE_YRFI")
            out["decision_notes"].append(
                f"L15 YRFI = {pct}% — baseline favorable al NRFI."
            )
        elif yrfi_l15 >= 0.65:
            out["adjustment"]       -= 8
            out["confidence_delta"] -= 5
            out["reason_codes"].append("NRFI_HIGH_BASELINE_YRFI")
            out["decision_notes"].append(
                f"L15 YRFI = {pct}% — baseline alto contradice NRFI."
            )
    else:  # YRFI
        if yrfi_l15 >= 0.65:
            out["adjustment"]       += 8
            out["confidence_delta"] += 5
            out["reason_codes"].append("YRFI_HIGH_BASELINE")
            out["decision_notes"].append(
                f"L15 YRFI = {pct}% — baseline apoya YRFI."
            )
        elif yrfi_l15 <= 0.35:
            out["adjustment"]       -= 8
            out["confidence_delta"] -= 5
            out["reason_codes"].append("YRFI_LOW_BASELINE")
            out["decision_notes"].append(
                f"L15 YRFI = {pct}% — baseline contradice YRFI."
            )

    # Recent shift signal — L5 vs L15.
    if yrfi_l5 is not None:
        shift = round(yrfi_l5 - yrfi_l15, 3)
        if abs(shift) >= 0.20:
            direction = "subiendo" if shift > 0 else "bajando"
            out["reason_codes"].append(
                f"{target}_RECENT_SHIFT_{'RISING' if shift > 0 else 'DECLINING'}"
            )
            out["decision_notes"].append(
                f"YRFI rate {direction} en L5 ({round(yrfi_l5*100,1)}%) vs L15 ({pct}%)."
            )
            if (is_nrfi and shift < 0) or (not is_nrfi and shift > 0):
                out["adjustment"]       += 4
                out["confidence_delta"] += 2
            else:
                out["adjustment"]       -= 4
                out["confidence_delta"] -= 2
    return out


# ── Runline +1.5 ─────────────────────────────────────────────────────────
def _evaluate_runline_plus_15(
    home_side: dict,
    away_side: dict,
    *,
    trend_decision: str,
    runline_context: dict,
) -> dict:
    """Apply the user-specified Runline +1.5 rules.

    ``runline_context`` may include ``underdog_side`` ("home"|"away").
    If not provided we assume "away" (typical underdog tag) but it does
    not affect the final adjustment — the rules use both directions.
    """
    underdog_side = (runline_context or {}).get("underdog_side") or "away"
    favorite_side = "home" if underdog_side == "away" else "away"
    underdog = home_side if underdog_side == "home" else away_side
    favorite = home_side if favorite_side == "home" else away_side

    adjustment = 0
    confidence_delta = 0
    reason_codes: list[str] = []
    decision_notes: list[str] = []

    # Positive case — underdog offence stable/rising on both metrics.
    if (
        underdog["run_trend"] in ("STABLE", "RISING")
        and underdog["on_base_trend"] in ("STABLE", "RISING")
    ):
        adjustment      += 6
        confidence_delta += 4
        reason_codes.append("UNDERDOG_OFFENSE_CAN_COMPETE")
        decision_notes.append(
            "El underdog mantiene o mejora su ofensiva — Runline +1.5 con margen."
        )

    # Negative case — both decline.
    if (
        underdog["run_trend"] == "DECLINING"
        and underdog["on_base_trend"] == "DECLINING"
    ):
        adjustment      += -10
        confidence_delta += -8
        reason_codes.append("UNDERDOG_OFFENSE_DECLINING")
        decision_notes.append(
            "Underdog cayendo ofensivamente en ambas métricas — el +1.5 se fragiliza."
        )

    # Favorite surging — runline risk.
    if favorite["run_trend"] == "RISING" and favorite["on_base_trend"] == "RISING":
        adjustment      += -12
        confidence_delta += -10
        reason_codes.append("FAVORITE_OFFENSE_SURGING_AGAINST_RUNLINE")
        decision_notes.append(
            "El favorito viene subiendo en carreras y presión en base — riesgo de separación."
        )

    if favorite.get("home_runs_trend") in ("strong_rising", "moderate_rising"):
        adjustment      += -6
        confidence_delta += -4
        reason_codes.append("FAVORITE_POWER_SPIKE_RUNLINE_RISK")
        decision_notes.append(
            "Aumento de HR en el favorito — riesgo de blowout para el +1.5."
        )

    if trend_decision == "NEUTRAL":
        reason_codes.append("TREND_NEUTRAL_RUNLINE_DEPENDS_ON_MARGIN")
        decision_notes.append(
            "Tendencias neutrales — el Runline depende del margen actual."
        )

    return {
        "adjustment":       adjustment,
        "confidence_delta": confidence_delta,
        "reason_codes":     reason_codes,
        "decision_notes":   decision_notes,
        "underdog_side":    underdog_side,
        "favorite_side":    favorite_side,
    }


# ── Mixed signals payload ───────────────────────────────────────────────
def _build_mixed_signals_payload(
    *,
    run_strength: str,
    pressure_strength: str,
    home_side: dict,
    away_side: dict,
    trend_decision: str,
) -> dict:
    over_signals  = []
    under_signals = []
    if run_strength in ("strong_rising", "moderate_rising"):
        over_signals.append("RUN_ENV_RISING")
    elif run_strength in ("strong_declining", "moderate_declining"):
        under_signals.append("RUN_ENV_DECLINING")
    if pressure_strength in ("strong_rising", "moderate_rising"):
        over_signals.append("PRESSURE_RISING")
    elif pressure_strength in ("strong_declining", "moderate_declining"):
        under_signals.append("PRESSURE_DECLINING")
    if home_side["home_runs_trend"] in ("strong_rising", "moderate_rising") \
       or away_side["home_runs_trend"] in ("strong_rising", "moderate_rising"):
        over_signals.append("HR_RISING")
    if home_side["home_runs_trend"] in ("strong_declining", "moderate_declining") \
       and away_side["home_runs_trend"] in ("strong_declining", "moderate_declining"):
        under_signals.append("HR_DECLINING")
    has_mixed = bool(over_signals) and bool(under_signals)
    resolution = {
        "SUPPORTS_OVER":  "LEAN_OVER",
        "SUPPORTS_UNDER": "LEAN_UNDER",
        "MIXED":          "MIXED",
        "NEUTRAL":        "NEUTRAL",
    }.get(trend_decision, "NEUTRAL")
    return {
        "has_mixed_signals": has_mixed or trend_decision == "MIXED",
        "over_signals":      over_signals,
        "under_signals":     under_signals,
        "final_resolution":  resolution,
    }


def _impact_phrase(
    *,
    selected_market: Optional[str],
    trend_decision: str,
    score_adjustment: int,
    confidence_adjustment: int,
) -> str:
    market = selected_market or "el pick"
    if score_adjustment == 0 and confidence_adjustment == 0:
        return f"La tendencia reciente no altera el score de {market}."
    sign = "+" if score_adjustment >= 0 else ""
    sign_c = "+" if confidence_adjustment >= 0 else ""
    direction = {
        "SUPPORTS_OVER":  "apoya el Over",
        "SUPPORTS_UNDER": "apoya el Under",
        "MIXED":          "muestra señales mixtas",
        "NEUTRAL":        "es neutral",
    }.get(trend_decision, "")
    return (
        f"La tendencia reciente {direction}. "
        f"Ajuste de score {sign}{score_adjustment}, "
        f"ajuste de confianza {sign_c}{confidence_adjustment}."
    )


# ── Public re-exports / helpers for tests ───────────────────────────────
def interpret_run_trend(run_trend_profile: dict, selected_market: Optional[str] = None) -> dict:
    """Convenience wrapper: interprets just the run-environment delta.
    Used by tests; the full ``interpret_recent_form`` already covers
    this case.
    """
    return interpret_recent_form(
        recent_run_split=run_trend_profile,
        on_base_profile=None,
        selected_market=selected_market,
    )


def combine_trend_signals(
    recent_run_split: Optional[dict],
    on_base_profile: Optional[dict],
    selected_market: Optional[str] = None,
    runline_context: Optional[dict] = None,
    *,
    f5_split: Optional[dict] = None,
    first_inning_split: Optional[dict] = None,
    team_total_context: Optional[dict] = None,
) -> dict:
    """Alias for :func:`interpret_recent_form` — matches the name from
    the user's prompt."""
    return interpret_recent_form(
        recent_run_split=recent_run_split,
        on_base_profile=on_base_profile,
        selected_market=selected_market,
        runline_context=runline_context,
        f5_split=f5_split,
        first_inning_split=first_inning_split,
        team_total_context=team_total_context,
    )


__all__ = [
    "interpret_recent_form",
    "interpret_run_trend",
    "combine_trend_signals",
    "STRONG_OB_RISE", "MOD_OB_RISE",
    "STRONG_OB_DECLINE", "MOD_OB_DECLINE",
    "STRONG_RUN_RISE", "MOD_RUN_RISE",
]
