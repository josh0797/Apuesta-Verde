"""Sprint-D8/E PASO 2 · Football cards potential predictor (model-only).

Objetivo
========
Modelar la probabilidad over/under de **tarjetas totales** en un
partido prematch usando un único modelo Poisson sobre una expectativa
``λ_cards`` compuesta por:

  * el **promedio histórico del árbitro** (señal dominante),
  * tarjetas que ambos equipos suelen recibir,
  * faltas como proxy de agresividad.

Disciplina
==========
* **Función pura** — sin I/O, sin estado global. Recibe valores ya
  calculados point-in-time (PIT) por el ingestor.
* **Pesos fijos documentados** (decisión del usuario): no se hace
  grid-search en Fase 1.
* **Fail-soft sobre missing data**: cualquier feature ``None`` se
  excluye del cálculo, y los pesos restantes se re-normalizan
  manteniendo proporciones relativas.
* **Liga-fallback explícito** cuando el árbitro tiene muestra
  insuficiente (la decisión PIT la toma el ingestor; este módulo
  solo recibe el promedio ya saneado o ``None``).
* **Reason codes auditables** para cada componente que entró o salió
  del cálculo.

Filosofía de Fase 1
===================
El predictor NO necesita batir al mercado en esta fase. La pregunta
binaria es: **¿el AUC vs el resultado real supera 0.55-0.60?** Sólo si
sí, se justifica gastar créditos de The Odds API en Fase 2 para
benchmark vs cuotas de-vigged + CLV.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

log = logging.getLogger("services.football_cards_potential")


# ── Pesos fijos (decisión del usuario: pesos fijos documentados) ────
# El árbitro DEBE dominar el peso. Los pesos suman 1.0; cuando una
# feature está missing, el módulo re-normaliza sobre el resto.
W_REFEREE          = 0.55   # Promedio histórico del árbitro (señal #1)
W_TEAMS_CARDS_FOR  = 0.30   # Tarjetas que ambos equipos suelen recibir
W_TEAMS_FOULS      = 0.10   # Faltas como proxy de agresividad
W_DERBY_BUMP       = 0.05   # Bump si is_derby=True (en Fase 1 derby=False por defecto)

assert math.isclose(W_REFEREE + W_TEAMS_CARDS_FOR + W_TEAMS_FOULS
                    + W_DERBY_BUMP, 1.0), "weights must sum to 1.0"

# Promedio liga-default cuando TODOS los inputs son None (último recurso).
LEAGUE_DEFAULT_LAMBDA = 4.2   # ~ Premier League cards-per-match media histórica.
LOW_SAMPLE_REFEREE_FALLBACK_LAMBDA = 4.2   # mismo valor que liga (alias semántico)

# ── Reason codes ────────────────────────────────────────────────────
RC_REFEREE_AVG_USED         = "REFEREE_AVG_USED"
RC_REFEREE_AVG_MISSING      = "REFEREE_AVG_MISSING"
RC_LOW_REFEREE_SAMPLE       = "LOW_REFEREE_SAMPLE_FALLBACK_USED"
RC_TEAM_CARDS_USED          = "TEAM_CARDS_FOR_USED"
RC_TEAM_CARDS_PARTIAL       = "TEAM_CARDS_FOR_PARTIAL"
RC_TEAM_CARDS_MISSING       = "TEAM_CARDS_FOR_MISSING"
RC_TEAM_FOULS_USED          = "TEAM_FOULS_USED"
RC_TEAM_FOULS_MISSING       = "TEAM_FOULS_MISSING"
RC_DERBY_BUMP_APPLIED       = "DERBY_BUMP_APPLIED"
RC_LEAGUE_DEFAULT_USED      = "LEAGUE_DEFAULT_LAMBDA_USED"
RC_ALL_FEATURES_MISSING     = "ALL_FEATURES_MISSING_FAIL_SOFT"


# ── Poisson CDF + survival, pure Python (no scipy) ──────────────────
def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 0.0 if k > 0 else 1.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X ≤ k) for Poisson(λ). k is an integer."""
    if lam <= 0:
        return 1.0
    s = 0.0
    for i in range(k + 1):
        s += _poisson_pmf(i, lam)
    # Numerical clipping.
    return max(0.0, min(1.0, s))


def _over_under_at_line(lam: float, line: float) -> tuple[float, float]:
    """Return (P_over, P_under) for ``total_cards > line`` vs ``< line``.

    For half-lines (e.g. 4.5) there is no push — under uses
    ``floor(line)`` and over uses ``floor(line)+1``.
    """
    k_floor = int(math.floor(line))
    p_under = _poisson_cdf(k_floor, lam)
    p_over  = 1.0 - p_under
    return p_over, p_under


# ── Public API ──────────────────────────────────────────────────────
def compute_cards_potential(
    *,
    referee_cards_avg:     Optional[float] = None,
    referee_n_prior:       Optional[int]   = None,
    home_cards_for_avg:    Optional[float] = None,
    away_cards_for_avg:    Optional[float] = None,
    home_fouls_avg:        Optional[float] = None,
    away_fouls_avg:        Optional[float] = None,
    is_derby:              bool            = False,
    line:                  float           = 4.5,
    min_referee_sample:    int             = 5,
    use_referee_factor:    bool            = True,   # ablation switch
) -> dict:
    """Compute the over/under cards probability and expected total.

    Args:
      referee_cards_avg: PIT promedio histórico de tarjetas del árbitro.
        Se asume ya saneado por el ingestor (fallback a liga aplicado
        upstream si la muestra era baja). Si es ``None``, el factor
        árbitro se omite del cálculo aquí.
      referee_n_prior: muestra de partidos previos del árbitro. Cuando
        es menor a ``min_referee_sample`` y ``referee_cards_avg`` está
        presente, se levanta ``LOW_REFEREE_SAMPLE_FALLBACK_USED`` como
        reason code (auditoría informativa; el peso no cambia).
      home_cards_for_avg / away_cards_for_avg: tarjetas promedio por
        partido que cada equipo RECIBE (no las que provoca).
      home_fouls_avg / away_fouls_avg: faltas promedio por partido.
      is_derby: cuando True, aplica el bump ``W_DERBY_BUMP``.
      line: línea de total de tarjetas (3.5 / 4.5 / 5.5).
      min_referee_sample: umbral por debajo del cual marca low-sample.
      use_referee_factor: switch para ablation — si False, ignora el
        factor árbitro y re-normaliza pesos sobre los restantes
        (auditoría: ``REFEREE_AVG_MISSING`` se emite con razón
        ``ablation_disabled``).

    Returns:
      Dict canónico con:
        - over_cards_probability, under_cards_probability
        - expected_total_cards (λ)
        - reason_codes (list[str])
        - audit (pesos efectivos, breakdown por componente, sample sizes)
    """
    reason_codes: list[str] = []
    components: list[tuple[str, float, float]] = []
    # Each component = (name, value, weight_intended)

    # ── Referee (señal dominante)
    if use_referee_factor and referee_cards_avg is not None:
        try:
            ref_val = float(referee_cards_avg)
        except (TypeError, ValueError):
            ref_val = None
        if ref_val is not None and ref_val > 0:
            components.append(("referee", ref_val, W_REFEREE))
            reason_codes.append(RC_REFEREE_AVG_USED)
            if (referee_n_prior is not None
                    and referee_n_prior < min_referee_sample):
                reason_codes.append(RC_LOW_REFEREE_SAMPLE)
        else:
            reason_codes.append(RC_REFEREE_AVG_MISSING)
    else:
        reason_codes.append(RC_REFEREE_AVG_MISSING)

    # ── Team cards-for (combined home+away avg per team → sum)
    home_cf = _safe_float(home_cards_for_avg)
    away_cf = _safe_float(away_cards_for_avg)
    if home_cf is not None and away_cf is not None:
        team_cards_for_lambda = home_cf + away_cf
        components.append(("team_cards_for", team_cards_for_lambda,
                           W_TEAMS_CARDS_FOR))
        reason_codes.append(RC_TEAM_CARDS_USED)
    elif home_cf is not None or away_cf is not None:
        # Use the single side ×2 as a fail-soft proxy (audit flagged).
        only_side = home_cf if home_cf is not None else away_cf
        team_cards_for_lambda = (only_side or 0.0) * 2.0
        components.append(("team_cards_for_partial", team_cards_for_lambda,
                           W_TEAMS_CARDS_FOR * 0.5))
        reason_codes.append(RC_TEAM_CARDS_PARTIAL)
    else:
        reason_codes.append(RC_TEAM_CARDS_MISSING)

    # ── Team fouls (proxy)
    home_f = _safe_float(home_fouls_avg)
    away_f = _safe_float(away_fouls_avg)
    if home_f is not None and away_f is not None:
        # Convert fouls→cards via a soft ratio. Premier baseline:
        # ~22 fouls/match yields ~4 cards. Ratio ≈ 0.18.
        fouls_to_cards_ratio = 0.18
        team_fouls_lambda = (home_f + away_f) * fouls_to_cards_ratio
        components.append(("team_fouls", team_fouls_lambda, W_TEAMS_FOULS))
        reason_codes.append(RC_TEAM_FOULS_USED)
    else:
        reason_codes.append(RC_TEAM_FOULS_MISSING)

    # ── Derby bump (additive small λ)
    derby_lambda = 0.0
    if is_derby:
        # Add 0.6 to λ via the derby slot (independent of weight
        # normalisation — it's a direct λ injection).
        derby_lambda = 0.6
        reason_codes.append(RC_DERBY_BUMP_APPLIED)

    # ── Fail-soft last-resort: no usable components
    if not components and derby_lambda == 0.0:
        lam = LEAGUE_DEFAULT_LAMBDA
        reason_codes.append(RC_LEAGUE_DEFAULT_USED)
        reason_codes.append(RC_ALL_FEATURES_MISSING)
        weights_audit: dict[str, float] = {}
        breakdown: list[dict] = []
    else:
        # Renormalise weights of present components so they sum to 1.0
        # of the (1.0 − W_DERBY_BUMP) bucket, leaving room for derby.
        total_weight_intended = sum(w for _, _, w in components)
        if total_weight_intended <= 0:
            lam = LEAGUE_DEFAULT_LAMBDA
            reason_codes.append(RC_LEAGUE_DEFAULT_USED)
        else:
            # The base components share 1.0 of effective weight; derby
            # is additive in lambda space.
            lam = 0.0
            breakdown = []
            weights_audit = {}
            for name, val, w in components:
                effective_w = w / total_weight_intended
                contribution = val * effective_w
                lam += contribution
                breakdown.append({
                    "component":       name,
                    "value":           round(val, 4),
                    "weight_intended": round(w, 4),
                    "weight_effective": round(effective_w, 4),
                    "contribution":    round(contribution, 4),
                })
                weights_audit[name] = round(effective_w, 4)

    lam += derby_lambda
    # Numerical clamps.
    if lam <= 0:
        lam = LEAGUE_DEFAULT_LAMBDA
        reason_codes.append(RC_LEAGUE_DEFAULT_USED)
    lam = max(0.5, min(15.0, lam))

    p_over, p_under = _over_under_at_line(lam, line)

    return {
        "over_cards_probability":  round(p_over, 4),
        "under_cards_probability": round(p_under, 4),
        "expected_total_cards":    round(lam, 3),
        "line":                    line,
        "reason_codes":            reason_codes,
        "audit": {
            "weights_intended": {
                "referee":         W_REFEREE,
                "team_cards_for":  W_TEAMS_CARDS_FOR,
                "team_fouls":      W_TEAMS_FOULS,
                "derby_bump":      W_DERBY_BUMP,
            },
            "weights_effective": locals().get("weights_audit", {}),
            "breakdown":         locals().get("breakdown", []),
            "derby_lambda":      derby_lambda,
            "min_referee_sample": min_referee_sample,
            "use_referee_factor": use_referee_factor,
            "league_default_lambda": LEAGUE_DEFAULT_LAMBDA,
        },
    }


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


__all__ = [
    "compute_cards_potential",
    "W_REFEREE", "W_TEAMS_CARDS_FOR", "W_TEAMS_FOULS", "W_DERBY_BUMP",
    "LEAGUE_DEFAULT_LAMBDA",
    "RC_REFEREE_AVG_USED",
    "RC_REFEREE_AVG_MISSING",
    "RC_LOW_REFEREE_SAMPLE",
    "RC_TEAM_CARDS_USED",
    "RC_TEAM_CARDS_PARTIAL",
    "RC_TEAM_CARDS_MISSING",
    "RC_TEAM_FOULS_USED",
    "RC_TEAM_FOULS_MISSING",
    "RC_DERBY_BUMP_APPLIED",
    "RC_LEAGUE_DEFAULT_USED",
    "RC_ALL_FEATURES_MISSING",
]
