"""
Sprint D12 — NB Recalibration Wiring Tests.

Cubre dos garantías:
  1) `compute_expected_runs_distribution` propaga el
     `overlay_dispersion_multiplier` + `overlay_verdict` al helper
     `_compute_effective_dispersion`, ensanchando las colas NB **solo**
     cuando el veredicto es AVOID/BLOCK.
  2) El orquestador (vía `compute_total_risk_overlay`) entrega un
     contrato consistente con la firma del distribution.

Estos tests son puros (sin DB, sin red).
"""
from __future__ import annotations

import pytest

from services.mlb_expected_runs_distribution import (
    compute_expected_runs_distribution,
)
from services.mlb_total_risk_overlay import compute_total_risk_overlay


def _under_prob(payload: dict, line: float) -> float:
    """Helper: lee P(Under line.5) desde el payload del distribution."""
    assert payload.get("available") is True
    probs = payload.get("probabilities") or {}
    # line e.g. 8.5 → key "under_8_5"
    line_str = str(line).replace(".", "_")
    key = f"under_{line_str}"
    val = probs.get(key)
    if val is None:
        # Fallback to nearest .5 line
        return 0.0
    return float(val)


# ─────────────────────────────────────────────────────────────────────
# B1 — Wire intra-módulo: overlay_* llega a _compute_effective_dispersion
# ─────────────────────────────────────────────────────────────────────
class TestOverlayWiring:
    """El multiplicador se aplica si y solo si verdict ∈ {AVOID, BLOCK}."""

    def _base_kwargs(self) -> dict:
        return dict(
            expected_runs=8.5,
            market="total_runs_under",
            market_line=8.5,
            nb_dispersion_ratio=1.10,
            fragility_score=40,
        )

    def test_baseline_no_overlay(self):
        out = compute_expected_runs_distribution(**self._base_kwargs())
        assert out.get("available") is True
        assert out.get("distribution") == "negative_binomial"
        assert "UNDER_TAIL_RISK_RECALIBRATED" not in (out.get("reason_codes") or [])

    def test_overlay_with_allow_verdict_is_ignored(self):
        out = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=1.80,
            overlay_verdict="ALLOW",
        )
        assert out.get("available") is True
        assert "UNDER_TAIL_RISK_RECALIBRATED" not in (out.get("reason_codes") or [])

    def test_overlay_with_warn_verdict_is_ignored(self):
        # WARN should not trigger recalibration (per spec: AVOID/BLOCK only).
        out = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=1.35,
            overlay_verdict="WARN",
        )
        assert out.get("available") is True
        assert "UNDER_TAIL_RISK_RECALIBRATED" not in (out.get("reason_codes") or [])

    def test_overlay_with_avoid_verdict_widens_tails(self):
        baseline = compute_expected_runs_distribution(**self._base_kwargs())
        widened = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=1.35,
            overlay_verdict="AVOID",
        )
        assert widened.get("available") is True
        # Effective dispersion ratio must be strictly larger.
        eff_base = float(baseline.get("effective_dispersion_ratio") or 0)
        eff_wide = float(widened.get("effective_dispersion_ratio") or 0)
        assert eff_wide > eff_base, (
            f"Expected effective dispersion to widen; got {eff_wide} <= {eff_base}"
        )
        # Variance must increase (NB widening invariant).
        var_base = float(baseline.get("variance") or 0)
        var_wide = float(widened.get("variance") or 0)
        assert var_wide > var_base, (
            f"Expected variance to widen; got {var_wide} <= {var_base}"
        )
        # Tail percentiles: p90 ≥ baseline (right tail extends), p10 ≤
        # baseline (left tail extends). The U-shaped redistribution
        # means P(Under 8.5) may NOT monotonically drop, so we don't
        # assert on it here — see test_overlay_redistribution_signature.
        assert widened.get("p90", 0) >= baseline.get("p90", 0) - 1e-6
        assert widened.get("p10", 0) <= baseline.get("p10", 0) + 1e-6
        assert "UNDER_TAIL_RISK_RECALIBRATED" in (widened.get("reason_codes") or [])

    def test_overlay_with_block_verdict_widens_more_than_avoid(self):
        avoid_out = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=1.35,
            overlay_verdict="AVOID",
        )
        block_out = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=1.80,
            overlay_verdict="BLOCK",
        )
        # Con multiplicador mayor, el p10 baja y el p90 sube (más spread).
        assert block_out.get("p90", 0) >= avoid_out.get("p90", 0) - 1e-6
        assert block_out.get("p10", 0) <= avoid_out.get("p10", 0) + 1e-6

    def test_overlay_multiplier_1_0_is_noop(self):
        # 1.0 multiplier == no recalibration even if verdict gated.
        out = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=1.0,
            overlay_verdict="BLOCK",
        )
        assert out.get("available") is True
        assert "UNDER_TAIL_RISK_RECALIBRATED" not in (out.get("reason_codes") or [])

    def test_overlay_with_none_multiplier_is_noop(self):
        out = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=None,
            overlay_verdict="BLOCK",
        )
        assert out.get("available") is True
        assert "UNDER_TAIL_RISK_RECALIBRATED" not in (out.get("reason_codes") or [])

    def test_overlay_clamps_effective_ratio(self):
        # Even with absurdly large multiplier, effective_ratio is
        # clamped to 3.0 in _compute_effective_dispersion.
        out = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=10.0,
            overlay_verdict="BLOCK",
        )
        assert out.get("available") is True
        # Distribution must remain valid (probabilities present + monotone).
        probs = out.get("probabilities") or {}
        assert probs, "probabilities map must be present"
        # And clamp: effective ratio ≤ 3.0.
        eff = float(out.get("effective_dispersion_ratio") or 0)
        assert eff <= 3.001, f"effective ratio not clamped: {eff}"


    def test_overlay_redistribution_signature(self):
        """Cuando se ensancha la NB, las **dos colas extremas** ganan
        masa (no solo la derecha). Esta es la firma matemática del
        widening: P(Under 5.5) sube Y P(Over 11.5) sube.
        """
        base = compute_expected_runs_distribution(**self._base_kwargs())
        widened = compute_expected_runs_distribution(
            **self._base_kwargs(),
            overlay_dispersion_multiplier=1.80,
            overlay_verdict="BLOCK",
        )
        probs_b = base.get("probabilities") or {}
        probs_w = widened.get("probabilities") or {}
        # Extreme under (5.5) and extreme over (11.5) BOTH gain mass.
        assert probs_w.get("under_5_5", 0) >= probs_b.get("under_5_5", 0) - 1e-6
        assert probs_w.get("over_11_5", 0) >= probs_b.get("over_11_5", 0) - 1e-6


# ─────────────────────────────────────────────────────────────────────
# B2 — Contrato del overlay (dispersion_multiplier por verdict)
# ─────────────────────────────────────────────────────────────────────
class TestOverlayDispersionContract:
    """El overlay D12 produce dispersion_multiplier consistente."""

    def _under_pick(self) -> dict:
        return {"selection": "UNDER", "line": 8.5}

    def test_overlay_allow_returns_dispersion_1_0(self):
        out = compute_total_risk_overlay(
            baseline_expected_runs=8.0,
            pick=self._under_pick(),
        )
        assert out.get("verdict") == "ALLOW"
        assert out.get("dispersion_multiplier") == 1.0

    def test_overlay_extreme_tail_returns_dispersion_1_80(self):
        out = compute_total_risk_overlay(
            baseline_expected_runs=8.0,
            pick=self._under_pick(),
            starter_volatility={
                "home": {"starter_volatility_score": 90, "bucket": "EXTREME"},
                "away": {"starter_volatility_score": 88, "bucket": "EXTREME"},
            },
            first_inning_collapse={
                "home": {"first_inning_collapse_score": 95},
                "away": {"first_inning_collapse_score": 92},
            },
            lineup_explosiveness={
                "home": {"lineup_explosiveness_score": 88, "bucket": "EXPLOSIVE"},
                "away": {"lineup_explosiveness_score": 85, "bucket": "EXPLOSIVE"},
            },
            bullpen_stress={
                "home": {"bullpen_stress_score": 85},
                "away": {"bullpen_stress_score": 82},
            },
            domino_risk={
                "home": {"domino_risk_score": 88},
                "away": {"domino_risk_score": 85},
            },
            recent_offensive_quality={
                "home": {"bucket": "EXPLOSIVE"},
                "away": {"bucket": "EXPLOSIVE"},
            },
        )
        assert out.get("verdict") == "BLOCK"
        assert out.get("dispersion_multiplier") == pytest.approx(1.80)
        assert out.get("explosive_tail_risk") == "EXTREME"
        assert "UNDER_TAIL_RISK_RECALIBRATED" in (out.get("reason_codes") or [])

    def test_overlay_high_tail_returns_dispersion_1_35(self):
        out = compute_total_risk_overlay(
            baseline_expected_runs=8.0,
            pick=self._under_pick(),
            starter_volatility={
                "home": {"starter_volatility_score": 75, "bucket": "HIGH"},
                "away": {"starter_volatility_score": 70, "bucket": "HIGH"},
            },
            lineup_explosiveness={
                "home": {"lineup_explosiveness_score": 75, "bucket": "STRONG"},
                "away": {"lineup_explosiveness_score": 72, "bucket": "STRONG"},
            },
            base_explosive_tail_risk="MEDIUM",
        )
        # Should bump to HIGH and apply 1.35 multiplier.
        assert out.get("explosive_tail_risk") == "HIGH"
        assert out.get("dispersion_multiplier") == pytest.approx(1.35)


# ─────────────────────────────────────────────────────────────────────
# End-to-end: overlay → distribution NB widening (sanity contract)
# ─────────────────────────────────────────────────────────────────────
class TestE2EOverlayNBRecal:
    """Simula el flujo del orquestador: overlay produce multiplicador,
    luego se llama a distribution con overlay_*.
    """

    def test_block_verdict_widens_distribution(self):
        overlay = compute_total_risk_overlay(
            baseline_expected_runs=8.5,
            pick={"selection": "UNDER", "line": 8.5},
            starter_volatility={
                "home": {"starter_volatility_score": 90},
                "away": {"starter_volatility_score": 88},
            },
            first_inning_collapse={
                "home": {"first_inning_collapse_score": 95},
                "away": {"first_inning_collapse_score": 92},
            },
            lineup_explosiveness={
                "home": {"lineup_explosiveness_score": 88, "bucket": "EXPLOSIVE"},
                "away": {"lineup_explosiveness_score": 85, "bucket": "EXPLOSIVE"},
            },
            bullpen_stress={
                "home": {"bullpen_stress_score": 85},
                "away": {"bullpen_stress_score": 82},
            },
            domino_risk={
                "home": {"domino_risk_score": 88},
                "away": {"domino_risk_score": 85},
            },
            recent_offensive_quality={
                "home": {"bucket": "EXPLOSIVE"},
                "away": {"bucket": "EXPLOSIVE"},
            },
        )
        assert overlay["verdict"] == "BLOCK"
        mult = overlay["dispersion_multiplier"]
        verdict = overlay["verdict"]

        base = compute_expected_runs_distribution(
            expected_runs=8.5,
            market="total_runs_under",
            market_line=8.5,
            nb_dispersion_ratio=1.10,
        )
        widened = compute_expected_runs_distribution(
            expected_runs=8.5,
            market="total_runs_under",
            market_line=8.5,
            nb_dispersion_ratio=1.10,
            overlay_dispersion_multiplier=mult,
            overlay_verdict=verdict,
        )

        # Sanity: widened p90 ≥ base p90, widened p10 ≤ base p10
        # (tail widening invariant).
        assert widened.get("p90", 0) >= base.get("p90", 0) - 1e-6
        assert widened.get("p10", 0) <= base.get("p10", 0) + 1e-6
        # Variance widens.
        assert float(widened.get("variance") or 0) > float(base.get("variance") or 0)
