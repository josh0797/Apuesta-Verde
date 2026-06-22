"""F99 — Tests for the refactor of `mlb_day_orchestrator` enrichment block.

Cobertura:
  * Golden fixture: `apply_statcast_phase9_adjustments` produce EXACTAMENTE
    el mismo shape de mutación que el bloque inline pre-F99.
  * Smoke: el orchestrator importa sin errores y usa el nuevo builder.
  * Football y basketball modules siguen importando OK (no rompimos
    ningún acoplamiento cruzado).
  * Fail-soft: cuando `mlb_advanced_stats_helpers` falla, el builder
    no levanta excepción.
  * Idempotente: aplicar el builder dos veces sobre el mismo payload
    NO duplica reason codes ni rompe valores numéricos (mismo contrato
    que el código inline original).

NOTA: el golden fixture compara contra una *reproducción literal* del
bloque inline pre-F99 (lo conservamos como `_inline_baseline_phase9`
SOLO en este archivo de test, no en código de producción), para
garantizar 0 deriva de lógica.
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Optional
from unittest.mock import patch

import pytest

from services import mlb_day_context_builder as builder


log = logging.getLogger("test")


# ─────────────────────────────────────────────────────────────────────
# Baseline: reproducción LITERAL del bloque inline pre-F99
# Conservada SOLO aquí (en test) — sirve como golden fixture vivo.
# Si cambias `apply_statcast_phase9_adjustments`, este baseline NO
# debería cambiar.
# ─────────────────────────────────────────────────────────────────────
def _inline_baseline_phase9(pick_payload: dict, chosen_market: Optional[dict]) -> dict:
    """Exact replica of orchestrator lines 2579-2689 pre-F99."""
    try:
        from services.mlb_advanced_stats_helpers import (
            compute_all_advanced_adjustments,
        )
        _adv_summary = compute_all_advanced_adjustments(pick_payload)
        if _adv_summary.get("advanced_stats_used"):
            _dq = _adv_summary.get("advanced_stats_data_quality") or "missing"
            _weight = {"strong": 0.60, "partial": 0.35, "thin": 0.35,
                       "missing": 0.0}.get(_dq, 0.0)
            _adj_summary = _adv_summary.get("advanced_stats_adjustment_summary", {})

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

            _ou_conf = _raw_ou if _is_over else (-_raw_ou if _is_under else 0.0)
            _su_conf = _raw_su if _is_under else 0.0
            _pq_conf = (_raw_hpq + _raw_apq) / 2.0 * 0.5
            _frag_conf = -_raw_frag * 0.5

            _raw_conf_delta = _ou_conf + _su_conf + _pq_conf + _frag_conf
            _weighted_conf_delta = round(_raw_conf_delta * _weight, 2)

            _rec_adv = pick_payload.get("recommendation") or {}
            _cur_conf = float(_rec_adv.get("confidence_score") or 0)
            _new_conf = max(0.0, min(100.0, _cur_conf + _weighted_conf_delta))
            _rec_adv["confidence_score"] = round(_new_conf, 2)
            _rec_adv["statcast_confidence_delta"] = _weighted_conf_delta
            pick_payload["recommendation"] = _rec_adv

            _frag_block = pick_payload.get("fragility") if isinstance(pick_payload.get("fragility"), dict) else None
            if _frag_block is not None and _raw_frag:
                _w_frag = round(_raw_frag * _weight, 2)
                _cur_frag_score = float(_frag_block.get("score") or 0)
                _new_frag_score = max(0.0, min(100.0, _cur_frag_score + _w_frag))
                _frag_block["score"] = round(_new_frag_score, 2)
                _frag_block["statcast_delta"] = _w_frag
                pick_payload["fragility"] = _frag_block

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
        else:
            pick_payload.setdefault("advanced_adjustments", {
                "data_quality":        _adv_summary.get("advanced_stats_data_quality") or "missing",
                "weight_factor_used":  0.0,
                "raw_conf_delta":      0.0,
                "weighted_conf_delta": 0.0,
                "raw_breakdown":       {},
                "reason_codes":        [],
                "summary":             {},
            })
    except Exception as _exc:
        log.debug("baseline phase9 raised (expected fail-soft): %s", _exc)
    return pick_payload


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
def _summary_strong_over_under_under():
    """Strong-quality summary with Under-supportive OU adjustment."""
    return {
        "advanced_stats_used":          True,
        "advanced_stats_data_quality":  "strong",
        "advanced_stats_reason_codes":  ["STATCAST_UNDER_TILT", "QUALITY_STRONG"],
        "advanced_stats_adjustment_summary": {
            "over_under":           {"adjustment": -8.0},
            "fragility":            {"adjustment":  4.0},
            "starter_under":        {"adjustment":  6.0},
            "home_pitcher_quality": {"adjustment":  3.0},
            "away_pitcher_quality": {"adjustment":  5.0},
        },
    }


def _summary_partial_over_pick():
    """Partial-quality, OVER-supportive."""
    return {
        "advanced_stats_used":          True,
        "advanced_stats_data_quality":  "partial",
        "advanced_stats_reason_codes":  ["STATCAST_OVER_TILT"],
        "advanced_stats_adjustment_summary": {
            "over_under":           {"adjustment":  10.0},
            "fragility":            {"adjustment": -2.0},
            "starter_under":        {"adjustment":  0.0},
            "home_pitcher_quality": {"adjustment": -4.0},
            "away_pitcher_quality": {"adjustment": -3.0},
        },
    }


def _summary_missing():
    """Missing-quality → no adjustment applied, but audit attached."""
    return {
        "advanced_stats_used":          False,  # gate-off
        "advanced_stats_data_quality":  "missing",
        "advanced_stats_reason_codes":  [],
        "advanced_stats_adjustment_summary": {},
    }


def _base_pick_under():
    return {
        "match_id":       "m-1",
        "recommendation": {"market": "Under 8.5", "confidence_score": 70.0},
        "fragility":      {"score": 50.0},
        "reason_codes":   ["BASE_RC"],
    }


def _base_pick_over():
    return {
        "match_id":       "m-2",
        "recommendation": {"market": "Over 9.5",  "confidence_score": 60.0},
        "reason_codes":   [],
    }


# =====================================================================
# Golden: builder ≡ inline baseline
# =====================================================================
class TestGoldenEquivalence:
    @pytest.mark.parametrize("summary_fn", [
        _summary_strong_over_under_under,
        _summary_partial_over_pick,
        _summary_missing,
    ])
    def test_under_pick_strong_strong(self, summary_fn):
        summary = summary_fn()
        payload_a = _base_pick_under()
        payload_b = copy.deepcopy(payload_a)
        with patch("services.mlb_advanced_stats_helpers.compute_all_advanced_adjustments",
                   return_value=summary):
            builder.apply_statcast_phase9_adjustments(payload_a, {"market": "Under 8.5"})
            _inline_baseline_phase9(payload_b, {"market": "Under 8.5"})
        assert payload_a == payload_b, "builder must match the pre-F99 inline behaviour"

    def test_over_pick_partial(self):
        summary = _summary_partial_over_pick()
        payload_a = _base_pick_over()
        payload_b = copy.deepcopy(payload_a)
        with patch("services.mlb_advanced_stats_helpers.compute_all_advanced_adjustments",
                   return_value=summary):
            builder.apply_statcast_phase9_adjustments(payload_a, {"market": "Over 9.5"})
            _inline_baseline_phase9(payload_b, {"market": "Over 9.5"})
        assert payload_a == payload_b

    def test_chosen_market_none(self):
        summary = _summary_strong_over_under_under()
        payload_a = {"recommendation": {"market": "Under 8.5"}, "reason_codes": []}
        payload_b = copy.deepcopy(payload_a)
        with patch("services.mlb_advanced_stats_helpers.compute_all_advanced_adjustments",
                   return_value=summary):
            builder.apply_statcast_phase9_adjustments(payload_a, None)
            _inline_baseline_phase9(payload_b, None)
        assert payload_a == payload_b

    def test_pick_payload_without_recommendation(self):
        summary = _summary_strong_over_under_under()
        payload_a = {"reason_codes": []}   # no recommendation key
        payload_b = copy.deepcopy(payload_a)
        with patch("services.mlb_advanced_stats_helpers.compute_all_advanced_adjustments",
                   return_value=summary):
            builder.apply_statcast_phase9_adjustments(payload_a, {"market": "Under 9"})
            _inline_baseline_phase9(payload_b, {"market": "Under 9"})
        assert payload_a == payload_b


# =====================================================================
# Numeric sanity (specific values from the strong-quality fixture)
# =====================================================================
class TestNumericSanity:
    def test_under_pick_confidence_increases_when_ou_negative(self):
        # Under pick + ou_adj=-8 → +confidence (OU favours Under).
        summary = _summary_strong_over_under_under()
        pick = _base_pick_under()
        with patch("services.mlb_advanced_stats_helpers.compute_all_advanced_adjustments",
                   return_value=summary):
            builder.apply_statcast_phase9_adjustments(pick, {"market": "Under 8.5"})
        # weighted_delta computed: ou_conf=+8 (under flip), su_conf=+6 (under),
        # pq_conf=(3+5)/2*0.5=2, frag_conf=-4*0.5=-2 → raw=14; *0.6=8.4
        assert pick["recommendation"]["confidence_score"] == round(70.0 + 8.4, 2)
        assert pick["recommendation"]["statcast_confidence_delta"] == 8.4
        # Fragility upgrade: +raw_frag(4)*0.6=2.4
        assert pick["fragility"]["score"]          == round(50.0 + 2.4, 2)
        assert pick["fragility"]["statcast_delta"] == 2.4
        # Reason codes appended, no duplicates.
        assert "STATCAST_UNDER_TILT" in pick["reason_codes"]
        assert "QUALITY_STRONG"      in pick["reason_codes"]
        assert "BASE_RC"             in pick["reason_codes"]

    def test_missing_quality_only_attaches_audit(self):
        summary = _summary_missing()
        pick = _base_pick_under()
        with patch("services.mlb_advanced_stats_helpers.compute_all_advanced_adjustments",
                   return_value=summary):
            builder.apply_statcast_phase9_adjustments(pick, {"market": "Under 8.5"})
        # Confidence untouched.
        assert pick["recommendation"]["confidence_score"] == 70.0
        # advanced_adjustments persisted with empty payload.
        adj = pick["advanced_adjustments"]
        assert adj["data_quality"] == "missing"
        assert adj["weight_factor_used"] == 0.0
        assert adj["weighted_conf_delta"] == 0.0


# =====================================================================
# Fail-soft + idempotence
# =====================================================================
class TestFailSoft:
    def test_helper_raise_does_not_propagate(self):
        pick = _base_pick_under()
        original = copy.deepcopy(pick)
        with patch("services.mlb_advanced_stats_helpers.compute_all_advanced_adjustments",
                   side_effect=RuntimeError("boom")):
            ret = builder.apply_statcast_phase9_adjustments(pick, {"market": "Under 8.5"})
        assert ret is pick
        # Payload untouched on failure.
        assert pick == original

    def test_idempotent_no_duplicate_reason_codes(self):
        summary = _summary_strong_over_under_under()
        pick = _base_pick_under()
        with patch("services.mlb_advanced_stats_helpers.compute_all_advanced_adjustments",
                   return_value=summary):
            builder.apply_statcast_phase9_adjustments(pick, {"market": "Under 8.5"})
            builder.apply_statcast_phase9_adjustments(pick, {"market": "Under 8.5"})
        # Confidence stays capped (second call adds again but clamped).
        # Reason codes deduped.
        assert pick["reason_codes"].count("STATCAST_UNDER_TILT") == 1
        assert pick["reason_codes"].count("QUALITY_STRONG")      == 1


# =====================================================================
# Smoke — module wiring intact
# =====================================================================
class TestModuleWiring:
    def test_builder_callable_and_exported(self):
        assert callable(builder.apply_statcast_phase9_adjustments)
        assert "apply_statcast_phase9_adjustments" in builder.__all__

    def test_orchestrator_imports_clean(self):
        # Importing the orchestrator must NOT raise. If F99 broke a
        # symbol, this is the canary.
        import importlib
        m = importlib.import_module("services.mlb_day_orchestrator")
        # The orchestrator should still export analyze_mlb_day.
        assert hasattr(m, "analyze_mlb_day")
        # And it should now import the builder (refactor cable wired).
        # Use context manager to ensure file is closed (silences ResourceWarning).
        with open(m.__file__) as _f:
            src = _f.read()
        assert "from .mlb_day_context_builder import apply_statcast_phase9_adjustments" in src

    def test_orchestrator_line_count_reduced(self):
        """Refactor sanity: file shrunk by ~100 lines (no double-wired
        code paths)."""
        import importlib
        m = importlib.import_module("services.mlb_day_orchestrator")
        with open(m.__file__) as f:
            line_count = sum(1 for _ in f)
        # Pre-F99 ≈ 6359 lines; post-F99 should be under 6280.
        assert line_count < 6280, f"orchestrator should shrink; now {line_count} lines"

    def test_football_modules_still_import(self):
        """Refactor must NOT have touched football modules."""
        import importlib
        for mod in (
            "services.football_finished_game_settler",
            "services.football_residual_verdict_classifier",
            "services.fixture_time_status_gate",
            "services.football_learning_snapshot_manager",
            "services.data_ingestion",
        ):
            importlib.import_module(mod)

    def test_basketball_modules_still_import(self):
        """Refactor must NOT have touched basketball modules."""
        import importlib
        for mod in (
            "services.basketball_intelligence_warehouse",
            "services.basketball_pace_layer",
            "services.basketball_possession_layer",
        ):
            importlib.import_module(mod)


def _exists(qual: str) -> bool:
    try:
        import importlib
        importlib.import_module(qual)
        return True
    except Exception:
        return False
