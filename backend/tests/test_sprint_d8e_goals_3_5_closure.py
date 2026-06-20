"""Tests for Sprint-D8/E PASO 0 — goals-3.5 closure (pure module).

Validates the decision rubric on:
  * the **real** model-only outputs from Sprint-D8 Fase 1 (golden test,
    closes the market definitively); and
  * synthetic fixtures that exercise the KEEP_OPEN_CANDIDATE branch,
    the dispersion threshold, and the max-AUC threshold independently.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.football_goals_3_5_closure import (
    AUC_DISPERSION_MAX_ROBUST,
    AUC_MAX_THRESHOLD_FOR_CHASE,
    RC_AUC_DISPERSION_HIGH,
    RC_AUC_MAX_BELOW_THRESHOLD,
    RC_CANDIDATE_KEEP_OPEN,
    RC_LEAGUE_GOALS_3_5_CLOSED,
    RC_MARKET_DATA_UNAVAILABLE,
    RC_MODEL_DISCRIMINATION_NOT_ROBUST,
    evaluate_goals_3_5_closure,
)


DIAG_DIR = Path("/app/diagnostics")


# ── Helpers ──────────────────────────────────────────────────────────
def _rec(market: str, scope: str, auc: float, n: int = 1000) -> dict:
    return {
        "market":      market,
        "scope":       scope,
        "auc_model":   auc,
        "n_records":   n,
        "base_rate":   0.32,
        "brier_model": 0.23,
        "verdict_tags": ["MODEL_ONLY"],
    }


# ── Golden test against real Sprint-D8 Fase 1 outputs ────────────────
@pytest.mark.skipif(
    not (DIAG_DIR / "calibration_over_3_5_top5_2425_modelonly.json").exists(),
    reason="Sprint-D8 Fase 1 outputs not present in this checkout",
)
def test_closure_against_real_d8_phase1_outputs_closes_definitively():
    records = []
    for market in ("over", "under"):
        for scope in ("premier_2425", "top5_2425", "premier_multiseason"):
            with (DIAG_DIR / f"calibration_{market}_3_5_{scope}_modelonly.json").open() as fh:
                doc = json.load(fh)
            doc["scope"] = scope
            records.append(doc)

    out = evaluate_goals_3_5_closure(records)

    assert out["verdict"] == "CLOSED"
    assert RC_LEAGUE_GOALS_3_5_CLOSED in out["reason_codes"]
    assert RC_MARKET_DATA_UNAVAILABLE in out["reason_codes"]
    assert out["market_data_available"] is False
    # Robustness flag should be present.
    assert RC_MODEL_DISCRIMINATION_NOT_ROBUST in out["reason_codes"]
    assert out["over_summary"]["n_scopes"]  == 3
    assert out["under_summary"]["n_scopes"] == 3


# ── Dispersion threshold (BOTH sides not robust → CLOSED) ────────────
def test_high_dispersion_in_both_sides_closes_market():
    recs = [
        _rec("OVER_3_5",  "premier_2425",        0.48),
        _rec("OVER_3_5",  "top5_2425",           0.56),  # disp = 0.08 > 0.05
        _rec("OVER_3_5",  "premier_multiseason", 0.53),
        _rec("UNDER_3_5", "premier_2425",        0.46),
        _rec("UNDER_3_5", "top5_2425",           0.56),  # disp = 0.10 > 0.05
        _rec("UNDER_3_5", "premier_multiseason", 0.53),
    ]
    out = evaluate_goals_3_5_closure(recs)
    assert out["verdict"] == "CLOSED"
    assert RC_AUC_DISPERSION_HIGH in out["over_summary"]["reason_codes"]
    assert RC_AUC_DISPERSION_HIGH in out["under_summary"]["reason_codes"]


# ── Max-AUC threshold (both sides ceiling < 0.58 → CLOSED) ───────────
def test_max_auc_below_chase_threshold_closes_market():
    recs = [
        _rec("OVER_3_5",  "premier_2425",        0.55),
        _rec("OVER_3_5",  "top5_2425",           0.56),  # disp 0.01 but ceiling < 0.58
        _rec("OVER_3_5",  "premier_multiseason", 0.55),
        _rec("UNDER_3_5", "premier_2425",        0.55),
        _rec("UNDER_3_5", "top5_2425",           0.57),
        _rec("UNDER_3_5", "premier_multiseason", 0.56),
    ]
    out = evaluate_goals_3_5_closure(recs)
    assert out["verdict"] == "CLOSED"
    assert RC_AUC_MAX_BELOW_THRESHOLD in out["over_summary"]["reason_codes"]
    assert RC_AUC_MAX_BELOW_THRESHOLD in out["under_summary"]["reason_codes"]


# ── KEEP_OPEN_CANDIDATE if only ONE side is not robust ───────────────
def test_keep_open_when_only_one_side_not_robust():
    recs = [
        # OVER is robust (low dispersion AND high enough AUC)
        _rec("OVER_3_5",  "premier_2425",        0.60),
        _rec("OVER_3_5",  "top5_2425",           0.61),
        _rec("OVER_3_5",  "premier_multiseason", 0.62),
        # UNDER is NOT robust (high dispersion)
        _rec("UNDER_3_5", "premier_2425",        0.46),
        _rec("UNDER_3_5", "top5_2425",           0.59),
        _rec("UNDER_3_5", "premier_multiseason", 0.53),
    ]
    out = evaluate_goals_3_5_closure(recs)
    assert out["verdict"] == "KEEP_OPEN_CANDIDATE"
    assert RC_CANDIDATE_KEEP_OPEN in out["reason_codes"]
    assert RC_LEAGUE_GOALS_3_5_CLOSED not in out["reason_codes"]


# ── Constants / sanity guards ────────────────────────────────────────
def test_thresholds_are_sane_constants():
    assert 0.0 < AUC_DISPERSION_MAX_ROBUST < 0.2
    assert 0.5 < AUC_MAX_THRESHOLD_FOR_CHASE < 0.7


# ── Robustness to malformed inputs (fail-soft) ───────────────────────
def test_fail_soft_on_garbage_records():
    recs = [
        None,                                    # type: ignore
        {"market": "OVER_2_5", "scope": "X", "auc_model": 0.6, "n_records": 10},
        {"market": "OVER_3_5", "scope": "ok",   "auc_model": "not-a-float",
         "n_records": 10},
        _rec("OVER_3_5",  "premier_2425",        0.48),
        _rec("UNDER_3_5", "premier_2425",        0.46),
    ]
    out = evaluate_goals_3_5_closure(recs)
    # Only the last two records survived validation.
    assert out["input_records_validated"] == 2
    # With a single scope per side, dispersion is 0 → use the chase ceiling rule.
    assert out["over_summary"]["n_scopes"] == 1
    assert out["under_summary"]["n_scopes"] == 1


# ── Empty input handling ─────────────────────────────────────────────
def test_empty_records_keeps_open_with_market_unavailable_only():
    out = evaluate_goals_3_5_closure([])
    # We never have data, so we cannot affirm closure. Stay open.
    assert out["verdict"] == "KEEP_OPEN_CANDIDATE"
    assert RC_MARKET_DATA_UNAVAILABLE in out["reason_codes"]
    assert out["over_summary"]["n_scopes"] == 0
    assert out["under_summary"]["n_scopes"] == 0


# ── Structural contract on the output ────────────────────────────────
def test_output_contract_has_required_keys():
    recs = [
        _rec("OVER_3_5",  "premier_2425",        0.48),
        _rec("OVER_3_5",  "top5_2425",           0.56),
        _rec("UNDER_3_5", "premier_2425",        0.46),
        _rec("UNDER_3_5", "top5_2425",           0.56),
    ]
    out = evaluate_goals_3_5_closure(recs)
    for key in ("verdict", "reason_codes", "market_data_available",
                "constraint_reason", "decision_rubric",
                "over_summary", "under_summary",
                "input_records_validated"):
        assert key in out, f"missing key {key} from output contract"
    for key in ("n_scopes", "auc_values", "auc_max", "auc_min",
                "auc_dispersion", "reason_codes", "per_scope"):
        assert key in out["over_summary"]
        assert key in out["under_summary"]
