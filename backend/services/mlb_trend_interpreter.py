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
) -> dict:
    """Top-level entry point. Returns ``{}`` when no usable input."""
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
    is_runline = _is_runline_plus_15(selected_market)
    is_under   = _is_under(selected_market) and not is_runline
    is_over    = _is_over(selected_market)  and not is_runline

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
        "per_side": {
            "home": home_side,
            "away": away_side,
        },
    }


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
) -> dict:
    """Alias for :func:`interpret_recent_form` — matches the name from
    the user's prompt."""
    return interpret_recent_form(
        recent_run_split=recent_run_split,
        on_base_profile=on_base_profile,
        selected_market=selected_market,
        runline_context=runline_context,
    )


__all__ = [
    "interpret_recent_form",
    "interpret_run_trend",
    "combine_trend_signals",
    "STRONG_OB_RISE", "MOD_OB_RISE",
    "STRONG_OB_DECLINE", "MOD_OB_DECLINE",
    "STRONG_RUN_RISE", "MOD_RUN_RISE",
]
