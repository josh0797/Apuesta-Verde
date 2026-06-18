"""
Football Manual-Odds Repricing — Sprint D10-C orquestador.

Combina los módulos puros `football_total_signal` y
`football_total_market_value` en un único pipeline orientado al UI de
cuotas manuales. Toda lógica matemática queda DELEGADA a los módulos
puros — este orquestador sólo:

  1. Normaliza inputs (selection, line, odds, lambdas, contexto).
  2. Llama a `calculate_football_total_signal(...)`.
  3. Llama a `calculate_football_total_market_value(...)` con las
     lambdas AJUSTADAS por la señal contextual.
  4. Combina ambos contratos en un payload unificado para el FE.

Estados de salida (status)
--------------------------
  * FOOTBALL_REPRICED                — signal + odds + line presentes.
  * FOOTBALL_TOTAL_SIGNAL_READY      — signal calculada, falta odds.
  * FOOTBALL_BASE_MODEL_ONLY         — sin contexto válido, sólo modelo base.
  * FOOTBALL_MARKET_LINE_MISSING     — falta línea.
  * FOOTBALL_INVALID_INPUTS          — inputs inutilizables.

Reglas
------
* La cuota nunca modifica lambdas, expected goals ni score contextual.
* La línea nunca modifica las proyecciones (sólo el edge/EV).
* `observe_only: True` siempre.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .football_total_market_value import (
    calculate_football_total_market_value,
)
from .football_total_signal import calculate_football_total_signal

log = logging.getLogger(__name__)


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def reprice_football_total_with_manual_odds(
    *,
    selection: Optional[str],
    line: Optional[float],
    decimal_odds: Optional[float],
    base_lambda_home: Optional[float],
    base_lambda_away: Optional[float],
    base_expected_goals: Optional[float] = None,
    recent_h2h_games: Optional[list] = None,
    home_recent_matches: Optional[list] = None,
    away_recent_matches: Optional[list] = None,
    home_xg_recent: Optional[dict] = None,
    away_xg_recent: Optional[dict] = None,
    lineup_context: Optional[dict] = None,
    competition_context: Optional[dict] = None,
    current_match_context: Optional[dict] = None,
    contextual_home_xg: Optional[float] = None,
    contextual_away_xg: Optional[float] = None,
) -> dict:
    """Orquesta el pipeline D10 completo. Devuelve un payload unificado.

    Cuando faltan inputs críticos, degrada gracefully a estados
    inferiores documentados en el módulo.
    """
    lam_h = _safe_float(base_lambda_home)
    lam_a = _safe_float(base_lambda_away)
    L = _safe_float(line)
    odds = _safe_float(decimal_odds)

    if lam_h is None or lam_a is None or lam_h < 0 or lam_a < 0:
        return {
            "status": "FOOTBALL_INVALID_INPUTS",
            "reason": "missing_base_lambdas",
            "signal":   None,
            "valuation": None,
            "observe_only": True,
        }

    base_eg = _safe_float(base_expected_goals)
    if base_eg is None:
        base_eg = lam_h + lam_a

    # ── 1) Señal contextual ────────────────────────────────────────
    signal = calculate_football_total_signal(
        base_expected_goals=base_eg,
        base_lambda_home=lam_h,
        base_lambda_away=lam_a,
        market_total=L,
        selection=selection,
        recent_h2h_games=recent_h2h_games,
        home_recent_matches=home_recent_matches,
        away_recent_matches=away_recent_matches,
        home_xg_recent=home_xg_recent,
        away_xg_recent=away_xg_recent,
        lineup_context=lineup_context,
        competition_context=competition_context,
        current_match_context=current_match_context,
        contextual_home_xg=contextual_home_xg,
        contextual_away_xg=contextual_away_xg,
    )

    # ── 2) Resolver lambdas a usar para la valoración ──────────────
    adj_h = signal.get("adjustment", {}).get("adjusted_lambda_home") or lam_h
    adj_a = signal.get("adjustment", {}).get("adjusted_lambda_away") or lam_a

    # ── 3) Estado preliminar ──────────────────────────────────────
    if L is None:
        return {
            "status":         "FOOTBALL_MARKET_LINE_MISSING",
            "signal":         signal,
            "valuation":      None,
            "observe_only":   True,
        }

    # ── 4) Valoración de mercado ──────────────────────────────────
    valuation = calculate_football_total_market_value(
        selection=selection,
        line=L,
        decimal_odds=odds,
        adjusted_lambda_home=adj_h,
        adjusted_lambda_away=adj_a,
        influence_score=signal.get("market_context", {}).get("influence_score"),
        confidence_delta=signal.get("market_context", {}).get("confidence_delta"),
    )

    # ── 5) Determinar estado final ─────────────────────────────────
    sig_status = signal.get("status")
    val_status = valuation.get("status")

    if sig_status == "BASE_MODEL_ONLY":
        unified = "FOOTBALL_BASE_MODEL_ONLY"
    elif val_status == "INVALID_INPUTS":
        unified = "FOOTBALL_INVALID_INPUTS"
    elif val_status == "MARKET_LINE_MISSING":
        unified = "FOOTBALL_MARKET_LINE_MISSING"
    elif val_status == "REPRICED":
        unified = "FOOTBALL_REPRICED"
    elif val_status == "BASE_MODEL_ONLY":
        unified = "FOOTBALL_TOTAL_SIGNAL_READY"
    else:
        unified = "FOOTBALL_INVALID_INPUTS"

    return {
        "status":        unified,
        "signal":        signal,
        "valuation":     valuation,
        "observe_only":  True,
    }


__all__ = ["reprice_football_total_with_manual_odds"]
