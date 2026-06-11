"""Tests for Phase 56 — MLB Layer Interaction Audit (observe-only)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from services.mlb_fragility_calibrator import calibrate_fragility
from services.mlb_layer_interaction_audit import (
    ENGINE_VERSION,
    FAMILY_BULLPEN,
    FAMILY_DEFENSE,
    FAMILY_SERIES,
    FAMILY_STARTER,
    FAMILY_TAIL,
    FAMILY_TRAFFIC,
    RC_DISTRIBUTION_MARKET_AGREEMENT,
    RC_DISTRIBUTION_MARKET_DISAGREEMENT,
    RC_DOUBLE_COUNT_DETECTED,
    RC_FRAGILITY_SWING_DETECTED,
    RC_LAYER_AUDIT_USED,
    RC_LAYER_AUDIT_UNAVAILABLE,
    RC_TAIL_FRAGILITY_SOURCE,
    build_distribution_market_selection_effect,
    build_layer_interaction_audit,
    summarise_for_pipeline_meta,
)
from services.mlb_tail_fragility import compute_tail_fragility


# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────
def _make_tail_risk_high() -> dict:
    """High-tail risk payload (forces tail_fragility into HIGH bucket)."""
    return {
        "available":   True,
        # Strong tail probabilities — score = 30*0.3 + 25*0.3 + 20*0.25 + 15*0.15
        # which exceeds the HIGH threshold of 50.
        "p_ge_12":     0.95,
        "p_ge_14":     0.85,
        "p_ge_16":     0.75,
        "p_ge_18":     0.65,
        "tail_bucket": "EXTREME",
        "market_line": 9.5,
        "side":        "under",
        "under_probability": 0.62,
        "over_probability":  0.38,
    }


def _make_tail_risk_low() -> dict:
    return {
        "available":   True,
        "p_ge_12":     0.05,
        "p_ge_14":     0.02,
        "p_ge_16":     0.01,
        "p_ge_18":     0.005,
        "tail_bucket": "LOW",
        "market_line": 9.5,
        "side":        "under",
        "under_probability": 0.55,
        "over_probability":  0.45,
    }


def _make_calibration_with_overlaps(tail_fragility_payload):
    return calibrate_fragility(
        base_fragility=25,
        market_side="under",
        expected_runs=8.0,
        market_line=9.5,
        inning_lambda_projection={"lambda_1_3": 2.5, "lambda_4_6": 2.5, "lambda_7_9": 3.0},
        home_pitcher={"era": 4.80, "whip": 1.40},
        away_pitcher={"era": 5.20, "whip": 1.45},
        bullpen_home={"bullpen_usage_3d": 0.85, "bullpen_fatigue": 0.85},
        bullpen_away={"bullpen_usage_3d": 0.80, "bullpen_fatigue": 0.80},
        series_familiarity={"series_familiarity_score": 60, "bucket": "HIGH"},
        traffic_score=72,
        defensive_breakdown_score=68,
        tail_risk=None,
        tail_fragility=tail_fragility_payload,
    )


# ───────────────────────────────────────────────────────────────────────
# Tests — Layer audit happy path
# ───────────────────────────────────────────────────────────────────────
class TestLayerInteractionAudit:
    def test_returns_unavailable_when_all_layers_missing(self):
        out = build_layer_interaction_audit()
        assert out["available"] is False
        assert RC_LAYER_AUDIT_UNAVAILABLE in out["reason_codes"]
        assert out["engine_version"] == ENGINE_VERSION

    def test_detects_double_count_for_bullpen_series_defense_starter(self):
        tail_risk = _make_tail_risk_high()
        tf = compute_tail_fragility(
            tail_risk_payload=tail_risk,
            bullpen_fatigue_high=True,
            defensive_breakdown_bucket="HIGH",
            series_familiarity_bucket="HIGH",
            starter_era=5.0,
            starter_whip=1.45,
            market_side="under",
        )
        cal = _make_calibration_with_overlaps(tf)

        audit = build_layer_interaction_audit(
            expected_runs_distribution={"available": True, "mean": 8.0,
                                         "effective_dispersion_ratio": 1.8},
            tail_risk=tail_risk,
            tail_fragility=tf,
            fragility_calibration=cal,
            raw_traffic_score=72,
            raw_defensive_breakdown_score=68,
            raw_defensive_breakdown_bucket="HIGH",
            raw_series_familiarity_score=60,
            raw_series_familiarity_bucket="HIGH",
            raw_bullpen_fatigue_high=True,
            raw_bullpen_usage_3d_home=0.85,
            raw_bullpen_usage_3d_away=0.80,
            raw_starter_era_worst=5.20,
            raw_starter_whip_worst=1.45,
        )
        assert audit["available"] is True
        assert RC_LAYER_AUDIT_USED in audit["reason_codes"]
        assert RC_DOUBLE_COUNT_DETECTED in audit["reason_codes"]

        risk_families = {r["family"] for r in audit["double_counting_risks"]}
        assert FAMILY_BULLPEN  in risk_families
        assert FAMILY_DEFENSE  in risk_families
        assert FAMILY_SERIES   in risk_families
        assert FAMILY_STARTER  in risk_families
        # Traffic only fires in calibrator — not double-count.
        assert FAMILY_TRAFFIC not in risk_families
        # Tail is consumed exactly once (via tf), not double-count.
        assert FAMILY_TAIL not in risk_families

    def test_no_double_count_when_tail_low(self):
        tail_risk = _make_tail_risk_low()
        tf = compute_tail_fragility(
            tail_risk_payload=tail_risk,
            bullpen_fatigue_high=True,
            defensive_breakdown_bucket="HIGH",
            series_familiarity_bucket="HIGH",
            starter_era=5.0,
            starter_whip=1.5,
            market_side="under",
        )
        # tf interactions only fire when tail bucket >= HIGH; tail_risk is LOW.
        assert tf["tail_bucket"] == "LOW"
        cal = _make_calibration_with_overlaps(tf)
        audit = build_layer_interaction_audit(
            expected_runs_distribution={"available": True, "mean": 7.5,
                                         "effective_dispersion_ratio": 1.1},
            tail_risk=tail_risk,
            tail_fragility=tf,
            fragility_calibration=cal,
        )
        assert audit["available"] is True
        # No overlaps because tf interactions are 0 with LOW tail bucket.
        assert audit["double_counting_risks"] == []
        assert RC_DOUBLE_COUNT_DETECTED not in audit["reason_codes"]

    def test_tail_fragility_consumed_via_phase55_emits_reason_code(self):
        tail_risk = _make_tail_risk_high()
        tf = compute_tail_fragility(
            tail_risk_payload=tail_risk,
            bullpen_fatigue_high=False,
            defensive_breakdown_bucket="LOW",
            series_familiarity_bucket="LOW",
            starter_era=3.5, starter_whip=1.1,
            market_side="under",
        )
        cal = calibrate_fragility(
            base_fragility=20,
            tail_risk=None,
            tail_fragility=tf,
        )
        audit = build_layer_interaction_audit(
            expected_runs_distribution={"available": True, "mean": 8.0},
            tail_risk=tail_risk,
            tail_fragility=tf,
            fragility_calibration=cal,
        )
        # Calibrator consumed tf component, not legacy fallback.
        assert RC_TAIL_FRAGILITY_SOURCE in audit["reason_codes"]
        assert "LEGACY_TAIL_FALLBACK_USED" not in audit["reason_codes"]


# ───────────────────────────────────────────────────────────────────────
# Tests — Distribution / Market Selection Effect
# ───────────────────────────────────────────────────────────────────────
class TestDistributionMarketSelectionEffect:
    def test_agreement_under(self):
        out = build_distribution_market_selection_effect(
            expected_runs_distribution={"available": True, "mean": 8.0},
            tail_risk={"available": True, "under_probability": 0.62,
                       "over_probability": 0.38, "tail_bucket": "LOW"},
            fragility_calibration={"available": True, "base_fragility": 30,
                                    "adjusted_fragility": 32, "delta": 2},
            market="Total Runs Under 9.5", market_line=9.5, market_side="under",
            chosen_market_score=72,
            fragility_score_pre=30,
        )
        assert out["available"] is True
        assert out["distribution_natural_side"] == "under"
        assert out["engine_chosen_side"] == "under"
        assert out["agreement"] is True
        assert RC_DISTRIBUTION_MARKET_AGREEMENT in out["reason_codes"]
        assert out["fragility_swing_detected"] is False

    def test_disagreement_over_vs_under(self):
        out = build_distribution_market_selection_effect(
            expected_runs_distribution={"available": True, "mean": 10.0},
            tail_risk={"available": True, "under_probability": 0.35,
                       "over_probability": 0.65, "tail_bucket": "HIGH"},
            fragility_calibration={"available": True, "base_fragility": 20,
                                    "adjusted_fragility": 38, "delta": 18},
            market="Total Runs Under 9.5", market_line=9.5, market_side="under",
            chosen_market_score=68,
            fragility_score_pre=20,
        )
        assert out["agreement"] is False
        assert RC_DISTRIBUTION_MARKET_DISAGREEMENT in out["reason_codes"]
        # delta=18 ≥ 8 swing_delta_threshold → swing detected.
        assert out["fragility_swing_detected"] is True
        assert RC_FRAGILITY_SWING_DETECTED in out["reason_codes"]

    def test_swing_across_kill_threshold(self):
        out = build_distribution_market_selection_effect(
            expected_runs_distribution={"available": True, "mean": 9.0},
            fragility_calibration={"available": True, "base_fragility": 55,
                                    "adjusted_fragility": 75, "delta": 20},
            market="Total Runs Under 9.5", market_side="under",
            fragility_score_pre=55,
        )
        # base 55 ≤ 60 and adjusted 75 > 60 → crosses the kill threshold.
        assert out["fragility_swing_crosses_kill_threshold"] is True
        assert out["fragility_swing_detected"] is True


# ───────────────────────────────────────────────────────────────────────
# Tests — Pipeline meta summarisation
# ───────────────────────────────────────────────────────────────────────
class TestSummariseForPipelineMeta:
    def test_unavailable_input_returns_unavailable(self):
        assert summarise_for_pipeline_meta({}) == {"available": False}
        assert summarise_for_pipeline_meta(None) == {"available": False}
        assert summarise_for_pipeline_meta({"available": False}) == {"available": False}

    def test_summary_propagates_double_count_count(self):
        tail_risk = _make_tail_risk_high()
        tf = compute_tail_fragility(
            tail_risk_payload=tail_risk,
            bullpen_fatigue_high=True,
            defensive_breakdown_bucket="HIGH",
            series_familiarity_bucket="HIGH",
            starter_era=5.0, starter_whip=1.5,
            market_side="under",
        )
        cal = _make_calibration_with_overlaps(tf)
        audit = build_layer_interaction_audit(
            expected_runs_distribution={"available": True, "mean": 8.0},
            tail_risk=tail_risk, tail_fragility=tf,
            fragility_calibration=cal,
        )
        meta = summarise_for_pipeline_meta(audit)
        assert meta["available"] is True
        assert meta["double_count_count"] >= 3
        assert meta["tail_consumed_via_phase55"] is True
        assert meta["tail_consumed_via_legacy"] is False


# ───────────────────────────────────────────────────────────────────────
# Tests — Script: synthetic mode (subprocess)
# ───────────────────────────────────────────────────────────────────────
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_mlb_layer_interactions.py"


class TestAuditScript:
    def test_script_exists(self):
        assert SCRIPT_PATH.exists()

    def test_runs_synthetic_creates_json(self, tmp_path):
        out_file = tmp_path / "audit.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--mode", "synthetic", "--n", "120",
             "--out", str(out_file), "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr={result.stderr}"
        assert out_file.exists()
        payload = json.loads(out_file.read_text())
        assert payload["script_version"].startswith("phase56.")
        assert set(payload["modes"].keys()) == {
            "FULL_CURRENT",
            "NO_DIRECT_TRAFFIC_DEFENSE_IN_CALIBRATOR",
            "NO_DISPERSION_SIGNAL_MODULATION",
            "LEGACY_SCALAR",
        }
        assert payload["guardrails"]["sample_size"] == 120
        assert payload["guardrails"]["sample_size_label"] == "VALIDATED_SAMPLE"

    def test_synthetic_small_sample_emits_low_sample_warning(self, tmp_path):
        out_file = tmp_path / "audit_small.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--mode", "synthetic", "--n", "20",
             "--out", str(out_file), "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr={result.stderr}"
        payload = json.loads(out_file.read_text())
        assert payload["guardrails"]["sample_size_label"] == "LOW_SAMPLE_WARNING"
        assert payload["guardrails"]["promote_changes_allowed"] is False

    def test_synthetic_tiny_sample_emits_high_risk_warning(self, tmp_path):
        out_file = tmp_path / "audit_tiny.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--mode", "synthetic", "--n", "5",
             "--out", str(out_file), "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stderr={result.stderr}"
        payload = json.loads(out_file.read_text())
        assert payload["guardrails"]["sample_size_label"] == "HIGH_RISK_WARNING"
        assert "TAIL_SAMPLE_TOO_LOW" in payload["guardrails"]["labels"]

    def test_script_prints_stdout_summary_when_not_quiet(self, tmp_path):
        out_file = tmp_path / "audit_stdout.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--mode", "synthetic", "--n", "60",
             "--out", str(out_file)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0
        assert "Phase 56" in result.stdout or "PHASE 56" in result.stdout
        assert "Mode-level summary" in result.stdout
        assert "Double-counting hot-spots" in result.stdout

    def test_synthetic_determinism_same_seed(self, tmp_path):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        for out in (a, b):
            r = subprocess.run(
                [sys.executable, str(SCRIPT_PATH),
                 "--mode", "synthetic", "--n", "50",
                 "--seed", "56", "--out", str(out), "--quiet"],
                capture_output=True, text=True, timeout=60,
            )
            assert r.returncode == 0
        pa = json.loads(a.read_text())
        pb = json.loads(b.read_text())
        # Compare mode-level summaries — generated_at differs but
        # modes/guardrails/n_cases must be identical.
        assert pa["modes"] == pb["modes"]
        assert pa["guardrails"] == pb["guardrails"]
        assert pa["n_cases"] == pb["n_cases"]


# ───────────────────────────────────────────────────────────────────────
# Tests — orchestrator integration is opt-in (full E2E covered elsewhere).
# We assert here only that the audit helper can be invoked from inside
# the orchestrator's module path without raising on fail-soft inputs.
# ───────────────────────────────────────────────────────────────────────
class TestOrchestratorIntegrationFailSoft:
    def test_audit_helpers_handle_all_none_gracefully(self):
        audit = build_layer_interaction_audit(
            expected_runs_distribution=None,
            tail_risk=None, tail_fragility=None, fragility_calibration=None,
        )
        assert audit["available"] is False
        assert audit["reason_codes"] == [RC_LAYER_AUDIT_UNAVAILABLE]

        dist = build_distribution_market_selection_effect(
            expected_runs_distribution=None, tail_risk=None,
            fragility_calibration=None,
        )
        assert dist["available"] is False

    def test_engine_version_is_stable(self):
        out = build_layer_interaction_audit(
            fragility_calibration={"available": True, "base_fragility": 20,
                                    "adjusted_fragility": 25, "delta": 5},
        )
        assert out["engine_version"] == ENGINE_VERSION
