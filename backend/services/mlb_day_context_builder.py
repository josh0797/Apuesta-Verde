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
    "apply_offensive_pressure_base",
    "apply_sabermetrics_layer",
    "apply_market_selection_intelligence",
    "apply_intelligence_warehouse",
    "seal_pipeline_payload_contract",
]


# ─────────────────────────────────────────────────────────────────────
# F100 — Objetivo 2: Offensive Pressure Base
# ─────────────────────────────────────────────────────────────────────
def apply_offensive_pressure_base(
    pick_payload: dict,
    chosen_market: Optional[dict],
) -> dict:
    """MLB OFFENSIVE PRESSURE BASE (Objetivo 2).

    Detect "hidden" offensive pressure: many hits, few runs. Acts as a
    fragility booster for Under picks that would otherwise look safe
    based on raw run counts alone.

    Extracted from ``mlb_day_orchestrator.analyze_mlb_day``
    (lines 2591-2647 pre-F100). No behavioural change.
    """
    try:
        from .mlb_pressure_base import (
            calculate_match_pressure_context,
            derive_pressure_impact_for_under_pick,
        )
        _pressure_ctx = calculate_match_pressure_context(pick_payload)
        pick_payload["pressure_base"] = _pressure_ctx
        if _pressure_ctx.get("available"):
            _mkt_label = (
                (pick_payload.get("recommendation") or {}).get("market")
                or (chosen_market or {}).get("market")
                or ""
            )
            _impact = derive_pressure_impact_for_under_pick(
                _pressure_ctx, pick_market=_mkt_label,
            )
            pick_payload["pressure_base_impact"] = _impact
            if _impact.get("applied"):
                # Apply confidence delta
                _rec_pb = pick_payload.get("recommendation") or {}
                _cur_conf_pb = float(_rec_pb.get("confidence_score") or 0)
                _new_conf_pb = max(0.0, min(
                    100.0, _cur_conf_pb + float(_impact.get("confidence_delta") or 0),
                ))
                _rec_pb["confidence_score"] = round(_new_conf_pb, 2)
                _rec_pb["pressure_confidence_delta"] = float(_impact.get("confidence_delta") or 0)
                pick_payload["recommendation"] = _rec_pb

                # Bump fragility if applicable
                _frag_block_pb = pick_payload.get("fragility") if isinstance(pick_payload.get("fragility"), dict) else None
                if _frag_block_pb is not None and _impact.get("fragility_delta"):
                    _cur_fs_pb = float(_frag_block_pb.get("score") or 0)
                    _new_fs_pb = max(0.0, min(
                        100.0, _cur_fs_pb + float(_impact.get("fragility_delta") or 0),
                    ))
                    _frag_block_pb["score"] = round(_new_fs_pb, 2)
                    _frag_block_pb["pressure_delta"] = float(_impact.get("fragility_delta") or 0)
                    pick_payload["fragility"] = _frag_block_pb

                # Reason codes
                _existing_pb = pick_payload.get("reason_codes") or []
                for _rc in (_impact.get("reason_codes") or []):
                    if _rc not in _existing_pb:
                        _existing_pb.append(_rc)
                pick_payload["reason_codes"] = _existing_pb
                log.debug(
                    "[Objetivo2] pressure_base impact applied tier=%s conf_delta=%s frag_delta=%s",
                    (_pressure_ctx.get("combined") or {}).get("pressure_tier"),
                    _impact.get("confidence_delta"),
                    _impact.get("fragility_delta"),
                )
    except Exception as _exc_pb:
        log.debug("mlb_pressure_base failed (fail-soft): %s", _exc_pb)
    return pick_payload


# ─────────────────────────────────────────────────────────────────────
# F100 — Phase 9.6: Sabermetrics Layer
# ─────────────────────────────────────────────────────────────────────
def apply_sabermetrics_layer(
    pick_payload: dict,
    chosen_market: Optional[dict],
) -> dict:
    """MLB SABERMETRICS LAYER (Phase 9.6 — WAR/OPS/FIP).

    Adds confirmation/risk layer on top of Statcast snapshot. Strictly
    conservative: weight by data_quality (60/35/0), capped at ±15
    weighted. Never converts a weak pick into a strong one.

    Extracted from ``mlb_day_orchestrator.analyze_mlb_day``
    (lines 2649-2729 pre-F100). No behavioural change.
    """
    try:
        from .mlb_sabermetrics_layer import (
            calculate_sabermetric_context,
            derive_sabermetric_recommendation_delta,
        )
        _saber_ctx = calculate_sabermetric_context(pick_payload)
        pick_payload["sabermetrics"] = _saber_ctx.get("sabermetrics") or {}
        _saber_inner = pick_payload["sabermetrics"]
        if _saber_inner.get("available"):
            _mkt_label_sb = (
                (pick_payload.get("recommendation") or {}).get("market")
                or (chosen_market or {}).get("market")
                or ""
            )
            _saber_delta = derive_sabermetric_recommendation_delta(
                _saber_inner, pick_market=_mkt_label_sb,
            )
            # Apply weighted confidence delta
            if _saber_delta.get("used"):
                _rec_sb = pick_payload.get("recommendation") or {}
                _cur_conf_sb = float(_rec_sb.get("confidence_score") or 0)
                _w_delta = float(_saber_delta.get("weighted_conf_delta") or 0)
                _new_conf_sb = max(0.0, min(100.0, _cur_conf_sb + _w_delta))
                _rec_sb["confidence_score"] = round(_new_conf_sb, 2)
                _rec_sb["sabermetrics_confidence_delta"] = _w_delta
                pick_payload["recommendation"] = _rec_sb

                # Optional fragility / survival hooks
                _adj_sb = _saber_inner.get("adjustments") or {}
                _frag_block_sb = pick_payload.get("fragility") if isinstance(pick_payload.get("fragility"), dict) else None
                if _frag_block_sb is not None:
                    _frag_delta_sb = round(
                        float(_adj_sb.get("fragility_adjustment") or 0)
                        * float(_saber_delta.get("weight") or 0), 2,
                    )
                    if _frag_delta_sb:
                        _cur_fs_sb = float(_frag_block_sb.get("score") or 0)
                        _frag_block_sb["score"] = round(
                            max(0.0, min(100.0, _cur_fs_sb + _frag_delta_sb)), 2,
                        )
                        _frag_block_sb["sabermetrics_delta"] = _frag_delta_sb
                        pick_payload["fragility"] = _frag_block_sb

                # Persist reason codes + audit metadata
                _existing_sb = pick_payload.get("reason_codes") or []
                for _rc in (_saber_delta.get("reason_codes") or []):
                    if _rc not in _existing_sb:
                        _existing_sb.append(_rc)
                pick_payload["reason_codes"] = _existing_sb
                pick_payload["sabermetrics_audit"] = {
                    "sabermetrics_used":                True,
                    "sabermetrics_data_quality":        _saber_delta.get("data_quality"),
                    "sabermetrics_adjustment_weight":   _saber_delta.get("weight"),
                    "sabermetrics_raw_adjustment":      _saber_delta.get("raw_conf_delta"),
                    "sabermetrics_weighted_adjustment": _w_delta,
                    "sabermetrics_raw_breakdown":       _saber_delta.get("raw_breakdown"),
                    "sabermetrics_reason_codes":        _saber_delta.get("reason_codes"),
                }
                log.debug(
                    "[Phase9.6] sabermetrics delta applied dq=%s weight=%.2f raw=%.2f weighted=%.2f",
                    _saber_delta.get("data_quality"),
                    _saber_delta.get("weight"),
                    _saber_delta.get("raw_conf_delta"),
                    _w_delta,
                )
        else:
            pick_payload.setdefault("sabermetrics_audit", {
                "sabermetrics_used":                False,
                "sabermetrics_data_quality":        "missing",
                "sabermetrics_adjustment_weight":   0.0,
                "sabermetrics_raw_adjustment":      0.0,
                "sabermetrics_weighted_adjustment": 0.0,
                "sabermetrics_raw_breakdown":       {},
                "sabermetrics_reason_codes":        [],
            })
    except Exception as _exc_sb:
        log.debug("mlb_sabermetrics_layer failed (fail-soft): %s", _exc_sb)
    return pick_payload


# ─────────────────────────────────────────────────────────────────────
# F100 — Phase 13.1: Market Selection Intelligence
# ─────────────────────────────────────────────────────────────────────
def apply_market_selection_intelligence(pick_payload: dict) -> dict:
    """MLB MARKET SELECTION INTELLIGENCE (Phase 13.1).

    Final protective selection layer: picks the most defensible market
    given pressure_base + statcast + sabermetrics + ghost-edges +
    fragility + survival + odds availability. Fail-soft; never overrides
    moneyball guardrails.

    Extracted from ``mlb_day_orchestrator.analyze_mlb_day``
    (lines 2731-2756 pre-F100). No behavioural change.
    """
    try:
        from .mlb_market_selection import select_protected_market
        _ms_out = select_protected_market(pick_payload)
        _ms = _ms_out.get("market_selection") or {}
        pick_payload["market_selection"] = _ms
        # Propagate reason codes
        _existing_ms = pick_payload.get("reason_codes") or []
        for _rc in (_ms.get("reason_codes") or []):
            if _rc not in _existing_ms:
                _existing_ms.append(_rc)
        pick_payload["reason_codes"] = _existing_ms
        log.debug(
            "[Phase13.1] market_selection: recommended=%s alt=%s conf=%s frag=%s watchlist=%s",
            _ms.get("recommended_market"),
            _ms.get("protected_alternative"),
            _ms.get("market_confidence"),
            _ms.get("fragility"),
            _ms.get("watchlist"),
        )
    except Exception as _exc_ms:
        log.debug("mlb_market_selection failed (fail-soft): %s", _exc_ms)
    return pick_payload


# ─────────────────────────────────────────────────────────────────────
# F100 — Fix 3: Intelligence Warehouse (Pattern Memory + Snapshots)
# ─────────────────────────────────────────────────────────────────────
async def apply_intelligence_warehouse(pick_payload: dict, db) -> dict:
    """MLB INTELLIGENCE WAREHOUSE (Fix 3 — Pattern Memory + Snapshots).

    Lookup pattern memory and attach `historical_pattern_match` to the
    pick. Persist the daily game intelligence snapshot for future
    pattern learning. Fail-soft: warehouse disabled → no-op.

    Extracted from ``mlb_day_orchestrator.analyze_mlb_day``
    (lines 2758-2800 pre-F100). No behavioural change.
    """
    try:
        from .mlb_intelligence_warehouse import (
            attach_pattern_match_to_payload,
            persist_game_intelligence_snapshot,
        )
        _pm_summary = await attach_pattern_match_to_payload(db, pick_payload)
        _pm_adj = float(_pm_summary.get("confidence_adjustment") or 0.0)
        if _pm_adj:
            _rec_pm = pick_payload.get("recommendation") or {}
            _cur_conf_pm = float(_rec_pm.get("confidence_score") or 0)
            _new_conf_pm = max(0.0, min(100.0, _cur_conf_pm + _pm_adj))
            _rec_pm["confidence_score"] = round(_new_conf_pm, 2)
            _rec_pm["pattern_memory_confidence_delta"] = round(_pm_adj, 2)
            pick_payload["recommendation"] = _rec_pm
            _existing_pm = pick_payload.get("reason_codes") or []
            for _rc in (_pm_summary.get("reason_codes") or []):
                if _rc not in _existing_pm:
                    _existing_pm.append(_rc)
            pick_payload["reason_codes"] = _existing_pm

        # Persist the snapshot for future analytics (fail-soft).
        def _safe_team_id(v):
            if isinstance(v, dict):
                return v.get("id") or v.get("team_id")
            return None
        await persist_game_intelligence_snapshot(
            db,
            game_pk=pick_payload.get("game_pk")
                      or (pick_payload.get("recommendation") or {}).get("game_pk")
                      or pick_payload.get("match_id"),
            match_id=pick_payload.get("match_id"),
            home_team_id=_safe_team_id(pick_payload.get("home_team"))
                          or pick_payload.get("home_team_id"),
            away_team_id=_safe_team_id(pick_payload.get("away_team"))
                          or pick_payload.get("away_team_id"),
            pick_payload=pick_payload,
        )
    except Exception as _exc_wh:
        log.debug("mlb_intelligence_warehouse failed (fail-soft): %s", _exc_wh)
    return pick_payload


# ─────────────────────────────────────────────────────────────────────
# F100 — Moneyball alignment: Pipeline Payload Contract sealing
# ─────────────────────────────────────────────────────────────────────
def seal_pipeline_payload_contract(pick_payload: dict) -> dict:
    """MLB PIPELINE PAYLOAD CONTRACT (Moneyball alignment).

    Seal the per-game pick_payload with the canonical Moneyball contract:
    every required field is stamped (with ``available: false`` when the
    upstream layer didn't run), plus computed audit blocks
    (ghost_edges, pattern_memory_audit, manual_odds_review). This
    decouples the UI from the orchestrator's internal layer order and
    guarantees fail-soft rendering for any consumer.

    Extracted from ``mlb_day_orchestrator.analyze_mlb_day``
    (lines 2802-2814 pre-F100). No behavioural change.
    """
    try:
        from .mlb_pipeline_payload_contract import seal_pick_payload
        seal_pick_payload(pick_payload)
    except Exception as _exc_seal:
        log.debug("mlb_pipeline_payload_contract seal failed (fail-soft): %s",
                  _exc_seal)
    return pick_payload
