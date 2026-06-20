"""Sprint-D8/E PASO 0 · Cierre estructural de **goles 3.5** (model-only).

Contexto
========
``football-data.co.uk`` (nuestro origen de odds histórico cacheado) **no
publica** odds 3.5 over/under en sus CSVs de temporada; sólo 2.5. Por
eso el pipeline D9 (que compara modelo contra mercado de-vigged + CLV)
**no se puede correr** para el mercado 3.5 con la infraestructura
actual sin gastar créditos de The Odds API.

El usuario aprobó explícitamente la opción **(a) — cerrar 3.5 como
``MARKET_DATA_UNAVAILABLE_FOR_3_5`` usando el AUC model-only ya
obtenido**, basándose en dos heurísticas independientes:

1. **Dispersión inter-scope del AUC**: si la variación entre cohortes
   (premier_2425 / top5_2425 / premier_multiseason) excede ``0.05``,
   el modelo no es robusto — un AUC alto en un scope no se reproduce
   en los demás, igual que pasó con el mercado DRAW en SPRINT D7.
2. **Tope absoluto del AUC máximo**: si ningún scope alcanza
   ``0.58`` (el umbral pragmático usado en D9 para justificar
   inversión de créditos contra el mercado), el modelo no discrimina
   lo suficiente para que el chase contra odds sea económico.

**Regla de cierre**: si CUALQUIERA de las dos heurísticas se cumple
para **ambos** lados (OVER y UNDER), se declara:
``LEAGUE_GOALS_3_5_CLOSED_DEFINITIVELY``.

Este módulo es **puro** (sin I/O): toma los registros model-only
parseados como dicts y devuelve el veredicto. El I/O (lectura de los
JSON de ``/app/diagnostics``) lo hace el script CLI.

No hay aprendizaje de máquina aquí — esto es housekeeping de
investigación. La función principal compone la decisión a partir de
los outputs existentes del Sprint-D8 Fase 1.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any

log = logging.getLogger("services.football_goals_3_5_closure")

# Heurísticas de cierre (las dos independientes).
AUC_DISPERSION_MAX_ROBUST = 0.05   # Δ(max-min) ≤ 0.05 ⇒ robusto.
AUC_MAX_THRESHOLD_FOR_CHASE = 0.58 # cualquier scope ≥ 0.58 justifica chase.

# Tags / reason codes canónicos (consumidos por consumers externos).
RC_MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE_FOR_3_5"
RC_AUC_DISPERSION_HIGH     = "AUC_DISPERSION_HIGH_ACROSS_SCOPES"
RC_AUC_MAX_BELOW_THRESHOLD = "MAX_AUC_BELOW_CHASE_THRESHOLD"
RC_MODEL_DISCRIMINATION_NOT_ROBUST = "MODEL_DISCRIMINATION_NOT_ROBUST"
RC_LEAGUE_GOALS_3_5_CLOSED = "LEAGUE_GOALS_3_5_CLOSED_DEFINITIVELY"
RC_CANDIDATE_KEEP_OPEN     = "CANDIDATE_KEEP_OPEN_FOR_MARKET_CHASE"


def _validate_records(records: list[dict]) -> list[dict]:
    """Filter records that have the minimal fields needed for the rubric.

    Each record must expose ``market``, ``scope``, ``auc_model``,
    ``n_records``. Records missing any of these are skipped with a
    ``log.debug`` (fail-soft).
    """
    out: list[dict] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        market = r.get("market")
        scope  = r.get("scope")
        auc    = r.get("auc_model")
        n      = r.get("n_records")
        if market not in ("OVER_3_5", "UNDER_3_5"):
            continue
        if not scope or auc is None or n is None:
            log.debug("closure: skipping incomplete record %s/%s", market, scope)
            continue
        try:
            out.append({
                "market":     market,
                "scope":      scope,
                "auc_model":  float(auc),
                "n_records":  int(n),
                "base_rate":  float(r.get("base_rate") or 0.0),
                "brier":      float(r.get("brier_model") or 0.0),
                "verdict_tags_input": list(r.get("verdict_tags") or []),
            })
        except (TypeError, ValueError) as exc:
            log.debug("closure: parse failed for %s/%s: %s", market, scope, exc)
    return out


def _summarise_side(records_side: list[dict]) -> dict:
    """Compute dispersion + max AUC for ONE market side (OVER or UNDER)."""
    if not records_side:
        return {
            "n_scopes":             0,
            "auc_values":           [],
            "auc_max":              None,
            "auc_min":              None,
            "auc_dispersion":       None,
            "auc_mean":             None,
            "auc_dispersion_high":  False,
            "auc_max_below_chase":  False,
            "reason_codes":         [],
        }
    aucs = [r["auc_model"] for r in records_side]
    auc_max = max(aucs)
    auc_min = min(aucs)
    auc_disp = round(auc_max - auc_min, 4)
    auc_mean = round(statistics.mean(aucs), 4)
    auc_dispersion_high = auc_disp > AUC_DISPERSION_MAX_ROBUST
    auc_max_below_chase = auc_max < AUC_MAX_THRESHOLD_FOR_CHASE

    codes: list[str] = []
    if auc_dispersion_high:
        codes.append(RC_AUC_DISPERSION_HIGH)
    if auc_max_below_chase:
        codes.append(RC_AUC_MAX_BELOW_THRESHOLD)
    if auc_dispersion_high or auc_max_below_chase:
        codes.append(RC_MODEL_DISCRIMINATION_NOT_ROBUST)

    return {
        "n_scopes":            len(records_side),
        "auc_values":          [round(a, 4) for a in aucs],
        "auc_max":             round(auc_max, 4),
        "auc_min":             round(auc_min, 4),
        "auc_dispersion":      auc_disp,
        "auc_mean":            auc_mean,
        "auc_dispersion_high": auc_dispersion_high,
        "auc_max_below_chase": auc_max_below_chase,
        "reason_codes":        codes,
        "per_scope": [
            {
                "scope":     r["scope"],
                "n":         r["n_records"],
                "auc_model": round(r["auc_model"], 4),
                "base_rate": round(r["base_rate"], 4),
                "brier":     round(r["brier"], 4),
            }
            for r in records_side
        ],
    }


def evaluate_goals_3_5_closure(records: list[dict]) -> dict:
    """Pure decision function for the goals-3.5 closure.

    Inputs
    ------
    records : list of dicts, each with at least:
        - market: "OVER_3_5" | "UNDER_3_5"
        - scope:  str (e.g., "premier_2425", "top5_2425", "premier_multiseason")
        - auc_model: float in [0, 1]
        - n_records: int
        - base_rate, brier_model, verdict_tags (optional)

    Output
    ------
    dict with:
        - verdict:        "CLOSED" | "KEEP_OPEN_CANDIDATE"
        - reason_codes:   list[str] (canonical RC_* codes above)
        - over_summary:   per-side stats (dispersion, max AUC, …)
        - under_summary:  idem
        - market_data_available: bool   (always False here for 3.5)
        - constraint_reason: human-readable text
        - decision_rubric:  rubric metadata for audit
        - input_records_validated: count of usable records after validation
    """
    parsed = _validate_records(records)
    over_recs  = [r for r in parsed if r["market"] == "OVER_3_5"]
    under_recs = [r for r in parsed if r["market"] == "UNDER_3_5"]

    over_summary  = _summarise_side(over_recs)
    under_summary = _summarise_side(under_recs)

    # Closure rule: discriminación no robusta en AMBOS lados → cerrar.
    over_not_robust  = (over_summary.get("auc_dispersion_high")
                         or over_summary.get("auc_max_below_chase"))
    under_not_robust = (under_summary.get("auc_dispersion_high")
                         or under_summary.get("auc_max_below_chase"))

    reason_codes: list[str] = [RC_MARKET_DATA_UNAVAILABLE]
    if over_not_robust and under_not_robust:
        reason_codes.append(RC_LEAGUE_GOALS_3_5_CLOSED)
        verdict = "CLOSED"
    else:
        # If only one side is not robust we keep open as candidate.
        reason_codes.append(RC_CANDIDATE_KEEP_OPEN)
        verdict = "KEEP_OPEN_CANDIDATE"

    # Append the per-side codes for audit (deduplicated, ordered).
    for code in (over_summary.get("reason_codes") or []):
        if code not in reason_codes:
            reason_codes.append(code)
    for code in (under_summary.get("reason_codes") or []):
        if code not in reason_codes:
            reason_codes.append(code)

    return {
        "verdict":                 verdict,
        "reason_codes":            reason_codes,
        "market_data_available":   False,
        "constraint_reason": (
            "football-data.co.uk does not publish 3.5 over/under odds in "
            "the cached season CSVs. Closure relies on model-only AUC "
            "robustness across scopes."
        ),
        "decision_rubric": {
            "auc_dispersion_max_robust":   AUC_DISPERSION_MAX_ROBUST,
            "auc_max_threshold_for_chase": AUC_MAX_THRESHOLD_FOR_CHASE,
            "rule": (
                "If, for BOTH OVER and UNDER, the AUC range across scopes "
                f"exceeds {AUC_DISPERSION_MAX_ROBUST} OR the maximum AUC "
                f"across scopes is below {AUC_MAX_THRESHOLD_FOR_CHASE}, "
                "the league-goals-3.5 market is closed definitively."
            ),
        },
        "over_summary":              over_summary,
        "under_summary":             under_summary,
        "input_records_validated":   len(parsed),
    }


__all__ = [
    "evaluate_goals_3_5_closure",
    "AUC_DISPERSION_MAX_ROBUST",
    "AUC_MAX_THRESHOLD_FOR_CHASE",
    "RC_MARKET_DATA_UNAVAILABLE",
    "RC_AUC_DISPERSION_HIGH",
    "RC_AUC_MAX_BELOW_THRESHOLD",
    "RC_MODEL_DISCRIMINATION_NOT_ROBUST",
    "RC_LEAGUE_GOALS_3_5_CLOSED",
    "RC_CANDIDATE_KEEP_OPEN",
]
