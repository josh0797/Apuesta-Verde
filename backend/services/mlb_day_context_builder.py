"""F99 — MLB Day Context Builder
==================================

Helpers de **enrichment / pipeline_meta** extraídos de
``mlb_day_orchestrator.analyze_mlb_day`` para reducir la complejidad
ciclomática de la función monolito (6,000+ líneas).

## Contrato
- **Sin cambios de lógica de negocio.** Todas las funciones aquí son
  copias 1:1 del código inline del orchestrator, con las mismas
  variables prefijadas (``_adv_summary``, ``_dq``, ``_weight``, etc.)
  para facilitar la comparación line-by-line via ``git diff``.
- **Mutación in-place** del ``pick_payload`` (mismo patrón que el código
  inline original) + ``return pick_payload`` para encadenamiento opcional.
- **Fail-soft total**: cualquier excepción interna queda silenciada (con
  ``log.debug``) tal como hacía el ``try/except`` original.
- **No cambia el shape del response** del orchestrator: los mismos
  reason codes, las mismas claves, los mismos valores.

## Extracciones realizadas
- ``apply_statcast_phase9_adjustments`` ← lines 2579-2689 del orchestrator
  (MLB STATCAST DEEP INTEGRATION Phase 9).

## Cómo agregar una extracción nueva
1. Copiar EXACTAMENTE el bloque (incluyendo nombres de variables locales).
2. Recibir como argumentos solo las variables capturadas del scope
   externo (NO crear DI complicado; mantener fidelidad al original).
3. NO cambiar logging ni reason codes.
4. NO "mejorar" lógica.
5. Añadir test golden que verifique mutación idéntica pre vs post.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("services.mlb_day_context_builder")


# ─────────────────────────────────────────────────────────────────────
# F99 — Phase 9: Statcast deep integration
# ─────────────────────────────────────────────────────────────────────
def apply_statcast_phase9_adjustments(
    pick_payload: dict,
    chosen_market: Optional[dict],
) -> dict:
    """MLB STATCAST DEEP INTEGRATION (Phase 9).

    Apply weighted Statcast-driven adjustments to the recommendation
    confidence. Weight is gated by ``data_quality`` so a thin
    snapshot can't overwhelm the primary engine:

      * strong  → 60% of raw adjustment
      * partial → 35%
      * missing → 0%

    Per-helper adjustments are already capped at ±15 internally;
    weighted deltas can shift confidence by ≈±18 worst-case.

    Args:
      pick_payload: the in-progress pick dict (mutated in place).
      chosen_market: ``{"market": "...", ...}`` block for the selected
        market label — used to detect Over vs Under pick orientation.

    Returns:
      The same ``pick_payload`` dict (allows chaining).

    Notes:
      * Extracted as-is from ``mlb_day_orchestrator.analyze_mlb_day``
        (lines 2579-2689 pre-F99). No behavioural change.
      * Fail-soft: catches every exception and logs at DEBUG level.
    """
    try:
        from .mlb_advanced_stats_helpers import (
            compute_all_advanced_adjustments,
        )
        _adv_summary = compute_all_advanced_adjustments(pick_payload)
        if _adv_summary.get("advanced_stats_used"):
            _dq = _adv_summary.get("advanced_stats_data_quality") or "missing"
            _weight = {"strong": 0.60, "partial": 0.35, "thin": 0.35,
                       "missing": 0.0}.get(_dq, 0.0)
            _adj_summary = _adv_summary.get("advanced_stats_adjustment_summary", {})

            # OU adjustment is the canonical headline (signed):
            #   positive → supports OVER, negative → supports UNDER.
            # We translate it w.r.t. the picked market so it always
            # acts as "confidence in the chosen side":
            #   * Under pick + ou_adj < 0 → +confidence
            #   * Over  pick + ou_adj > 0 → +confidence
            _market_label = (
                (pick_payload.get("recommendation") or {}).get("market")
                or (chosen_market or {}).get("market")
                or ""
            )
            _is_under = "under" in _market_label.lower() and "team total" not in _market_label.lower()
            _is_over  = "over"  in _market_label.lower() and "team total" not in _market_label.lower()

            _raw_ou   = float((_adj_summary.get("over_under") or {}).get("adjustment") or 0.0)
            _raw_frag = float((_adj_summary.get("fragility") or {}).get("adjustment") or 0.0)
            _raw_su   = float((_adj_summary.get("starter_under") or {}).get("adjustment") or 0.0)
            _raw_hpq  = float((_adj_summary.get("home_pitcher_quality") or {}).get("adjustment") or 0.0)
            _raw_apq  = float((_adj_summary.get("away_pitcher_quality") or {}).get("adjustment") or 0.0)

            # Compose conf delta:
            #  * OU: positive supports Over; if pick is Under, flip sign.
            _ou_conf = _raw_ou if _is_over else (-_raw_ou if _is_under else 0.0)
            #  * Starter-Under bonus only when Under
            _su_conf = _raw_su if _is_under else 0.0
            #  * Pitcher quality avg supports BOTH sides modestly
            _pq_conf = (_raw_hpq + _raw_apq) / 2.0 * 0.5
            #  * Fragility (positive = MORE fragile → pick weaker)
            _frag_conf = -_raw_frag * 0.5

            _raw_conf_delta = _ou_conf + _su_conf + _pq_conf + _frag_conf
            _weighted_conf_delta = round(_raw_conf_delta * _weight, 2)

            # Apply (clamped)
            _rec_adv = pick_payload.get("recommendation") or {}
            _cur_conf = float(_rec_adv.get("confidence_score") or 0)
            _new_conf = max(0.0, min(100.0, _cur_conf + _weighted_conf_delta))
            _rec_adv["confidence_score"] = round(_new_conf, 2)
            _rec_adv["statcast_confidence_delta"] = _weighted_conf_delta
            pick_payload["recommendation"] = _rec_adv

            # Optional fragility upgrade (additive)
            _frag_block = pick_payload.get("fragility") if isinstance(pick_payload.get("fragility"), dict) else None
            if _frag_block is not None and _raw_frag:
                _w_frag = round(_raw_frag * _weight, 2)
                _cur_frag_score = float(_frag_block.get("score") or 0)
                _new_frag_score = max(0.0, min(100.0, _cur_frag_score + _w_frag))
                _frag_block["score"] = round(_new_frag_score, 2)
                _frag_block["statcast_delta"] = _w_frag
                pick_payload["fragility"] = _frag_block

            # Persist reason codes + audit metadata on pick payload.
            _adv_rcs = _adv_summary.get("advanced_stats_reason_codes") or []
            _existing = pick_payload.get("reason_codes") or []
            for _rc in _adv_rcs:
                if _rc not in _existing:
                    _existing.append(_rc)
            pick_payload["reason_codes"] = _existing
            pick_payload["advanced_adjustments"] = {
                "data_quality":          _dq,
                "weight_factor_used":    _weight,
                "raw_conf_delta":        round(_raw_conf_delta, 3),
                "weighted_conf_delta":   _weighted_conf_delta,
                "raw_breakdown": {
                    "over_under":           _raw_ou,
                    "starter_under":        _raw_su,
                    "home_pitcher_quality": _raw_hpq,
                    "away_pitcher_quality": _raw_apq,
                    "fragility":            _raw_frag,
                },
                "reason_codes":          list(_adv_rcs),
                "summary":               _adj_summary,
            }
            log.debug(
                "[Phase9] statcast adj applied dq=%s weight=%.2f raw=%.2f weighted=%.2f",
                _dq, _weight, _raw_conf_delta, _weighted_conf_delta,
            )
        else:
            # Always attach the audit field — even when no adjustment
            # is applied — so the UI can display "Statcast: sin datos".
            pick_payload.setdefault("advanced_adjustments", {
                "data_quality":        _adv_summary.get("advanced_stats_data_quality") or "missing",
                "weight_factor_used":  0.0,
                "raw_conf_delta":      0.0,
                "weighted_conf_delta": 0.0,
                "raw_breakdown":       {},
                "reason_codes":        [],
                "summary":             {},
            })
    except Exception as _exc_phase9:
        log.debug("mlb_advanced_stats Phase 9 adjustment failed (fail-soft): %s", _exc_phase9)

    return pick_payload


__all__ = [
    "apply_statcast_phase9_adjustments",
]
