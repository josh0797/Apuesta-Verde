"""Tests for services.mlb_tail_fragility (Phase 55 — Tail Fragility Engine).

Coverage:
  • _explosive_tail_score formula matches the spec weights.
  • Bucket thresholds: LOW [0..24], MEDIUM [25..49], HIGH [50..74], EXTREME [75..100].
  • Base adjustment: LOW=0 / MEDIUM=5 / HIGH=10 / EXTREME=15.
  • Interactions only fire on tail_bucket ≥ HIGH.
  • Bullpen / Defense / Series / Starter interactions and their deltas.
  • Cap at +20 total adjustment, with TAIL_FRAGILITY_CAP_HIT code.
  • The Cole-vs-Cecconi case from the user spec produces base +10 +
    bullpen(+5) + series(+3) + starter(+5) = +20 (clamped from +23).
  • Fail-soft: missing payload → available=False with no penalty.
  • NEVER mutates polarity (no "pick", "side flip", etc keys).
  • apply_to_fragility envelope correctness.
"""
from __future__ import annotations

import pytest

from services.mlb_tail_fragility import (
    compute_tail_fragility,
    apply_to_fragility,
    BUCKET_LOW, BUCKET_MEDIUM, BUCKET_HIGH, BUCKET_EXTREME,
    CAP_TOTAL_ADJUSTMENT,
    RC_TAIL_FRAGILITY_USED, RC_TAIL_FRAGILITY_UNAVAILABLE,
    RC_TAIL_FRAGILITY_CAP_HIT,
    RC_TAIL_BULLPEN_INTERACTION, RC_TAIL_DEFENSE_INTERACTION,
    RC_TAIL_SERIES_INTERACTION, RC_TAIL_STARTER_INTERACTION,
    RC_EXPLOSIVE_TAIL_HIGH, RC_EXPLOSIVE_TAIL_EXTREME,
    RC_EXPLOSIVE_TAIL_LOW,
)


def _tail(p12=0.0, p14=0.0, p16=0.0, p18=0.0):
    return {
        "available": True,
        "p_ge_12": p12, "p_ge_14": p14, "p_ge_16": p16, "p_ge_18": p18,
    }


# ─── Score formula ────────────────────────────────────────────────────────────
class TestExplosiveTailScore:

    def test_all_zero_low_bucket(self):
        out = compute_tail_fragility(tail_risk_payload=_tail())
        assert out["available"] is True
        assert out["explosive_tail_score"] == 0
        assert out["tail_bucket"] == BUCKET_LOW
        assert out["base_adjustment"] == 0

    def test_weight_sum_matches_spec(self):
        # p12=0.40, p14=0.30, p16=0.20, p18=0.10
        # → 0.40*0.30 + 0.30*0.30 + 0.20*0.25 + 0.10*0.15
        # = 0.120 + 0.090 + 0.050 + 0.015 = 0.275 → 28 (MEDIUM)
        out = compute_tail_fragility(tail_risk_payload=_tail(0.40, 0.30, 0.20, 0.10))
        assert out["explosive_tail_score"] == 28
        assert out["tail_bucket"] == BUCKET_MEDIUM
        assert out["base_adjustment"] == 5

    def test_high_bucket(self):
        # p12=0.90, p14=0.65, p16=0.40, p18=0.20
        # = 0.27 + 0.195 + 0.10 + 0.03 = 0.595 → 60 (HIGH)
        out = compute_tail_fragility(tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20))
        assert out["tail_bucket"] == BUCKET_HIGH
        assert out["base_adjustment"] == 10
        assert RC_EXPLOSIVE_TAIL_HIGH in out["reason_codes"]

    def test_extreme_bucket(self):
        # Pathological blow-up: all near 1.0
        out = compute_tail_fragility(tail_risk_payload=_tail(0.95, 0.85, 0.70, 0.50))
        assert out["tail_bucket"] == BUCKET_EXTREME
        assert out["base_adjustment"] == 15
        assert RC_EXPLOSIVE_TAIL_EXTREME in out["reason_codes"]


# ─── Interactions gating ──────────────────────────────────────────────────────
class TestInteractionsGating:

    def test_interactions_skipped_when_tail_low(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.05, 0.01, 0.0, 0.0),
            bullpen_fatigue_high=True,
            defensive_breakdown_bucket="HIGH",
            series_familiarity_bucket="HIGH",
            starter_era=5.50, starter_whip=1.60,
        )
        assert out["tail_bucket"] == BUCKET_LOW
        assert out["interactions"] == []
        assert out["interaction_total"] == 0
        assert out["total_adjustment"] == 0

    def test_interactions_skipped_when_tail_medium(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.40, 0.30, 0.20, 0.10),
            bullpen_fatigue_high=True,
            starter_era=5.0,
        )
        # Score=28 → MEDIUM. Interactions must NOT fire.
        assert out["tail_bucket"] == BUCKET_MEDIUM
        assert out["interactions"] == []

    def test_only_bullpen_interaction_fires(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),
            bullpen_fatigue_high=True,
        )
        codes = {i["code"] for i in out["interactions"]}
        assert codes == {RC_TAIL_BULLPEN_INTERACTION}
        assert out["interaction_total"] == 5
        assert out["total_adjustment"] == 15  # base 10 + 5

    def test_defense_interaction_threshold(self):
        # MEDIUM defense bucket triggers; LOW does not.
        for buck, expected in [("MEDIUM", True), ("HIGH", True), ("LOW", False), (None, False)]:
            out = compute_tail_fragility(
                tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),
                defensive_breakdown_bucket=buck,
            )
            codes = {i["code"] for i in out["interactions"]}
            assert (RC_TAIL_DEFENSE_INTERACTION in codes) is expected, buck

    def test_starter_only_era_threshold(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),
            starter_era=4.51,  # > 4.50 triggers
        )
        codes = {i["code"] for i in out["interactions"]}
        assert RC_TAIL_STARTER_INTERACTION in codes

    def test_starter_only_whip_threshold(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),
            starter_whip=1.36,  # > 1.35 triggers
        )
        codes = {i["code"] for i in out["interactions"]}
        assert RC_TAIL_STARTER_INTERACTION in codes

    def test_starter_below_thresholds_does_not_fire(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),
            starter_era=4.50, starter_whip=1.35,
        )
        codes = {i["code"] for i in out["interactions"]}
        assert RC_TAIL_STARTER_INTERACTION not in codes


# ─── Cap behaviour ────────────────────────────────────────────────────────────
class TestCap:

    def test_cap_caps_at_20(self):
        # EXTREME (+15) + ALL interactions (+5+4+3+5 = +17) = +32 → clamp to +20.
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.95, 0.85, 0.70, 0.50),
            bullpen_fatigue_high=True,
            defensive_breakdown_bucket="HIGH",
            series_familiarity_bucket="HIGH",
            starter_era=5.50, starter_whip=1.60,
        )
        assert out["base_adjustment"] == 15
        assert out["interaction_total"] == 17
        assert out["total_adjustment"] == CAP_TOTAL_ADJUSTMENT == 20
        assert out["cap_hit"] is True
        assert RC_TAIL_FRAGILITY_CAP_HIT in out["reason_codes"]

    def test_no_cap_when_under_threshold(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),  # HIGH
            bullpen_fatigue_high=True,                         # +5
        )
        # base 10 + 5 = 15 < cap; no cap_hit.
        assert out["total_adjustment"] == 15
        assert out["cap_hit"] is False
        assert RC_TAIL_FRAGILITY_CAP_HIT not in out["reason_codes"]


# ─── Spec case: Gerrit Cole vs Cecconi ────────────────────────────────────────
class TestSpecCaseColeVsCecconi:
    """Spec excerpt:
        ER 8.0, Line 10.5, tail bucket HIGH.
        Bullpen fatiga ambos lados, series familiarity activa.
        Cecconi: ERA 4.92, WHIP 1.43.
        Expected: tail_adjustment = +13 → fragility 20 + 13 = 33.

    Our base for HIGH is +10 (per spec), interactions: +5 (bullpen) +3
    (series) +5 (starter) = +13. Total = +23 → clamped to +20.
    """

    def test_cole_vs_cecconi_adds_at_cap(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.85, 0.60, 0.35, 0.18),  # HIGH (~55)
            bullpen_fatigue_high=True,
            series_familiarity_bucket="HIGH",
            starter_era=4.92, starter_whip=1.43,
        )
        assert out["tail_bucket"] == BUCKET_HIGH
        # Interactions: bullpen +5, series +3, starter +5 = +13.
        assert out["interaction_total"] == 13
        # Base 10 + 13 = 23 → cap at 20.
        assert out["total_adjustment"] == 20
        assert out["cap_hit"] is True


# ─── Fail-soft ────────────────────────────────────────────────────────────────
class TestFailSoft:

    def test_none_payload_returns_available_false(self):
        out = compute_tail_fragility(tail_risk_payload=None)
        assert out["available"] is False
        assert out["total_adjustment"] == 0
        assert RC_TAIL_FRAGILITY_UNAVAILABLE in out["reason_codes"]

    def test_unavailable_payload_returns_available_false(self):
        out = compute_tail_fragility(tail_risk_payload={"available": False})
        assert out["available"] is False
        assert out["total_adjustment"] == 0

    def test_malformed_payload_does_not_raise(self):
        out = compute_tail_fragility(
            tail_risk_payload={"available": True, "p_ge_12": "bad", "p_ge_14": None},
        )
        # Inputs unusable → score=0, bucket LOW, no penalty.
        assert isinstance(out, dict)
        assert out["available"] is True
        assert out["tail_bucket"] == BUCKET_LOW
        assert out["total_adjustment"] == 0


# ─── Polarity safety ──────────────────────────────────────────────────────────
class TestNoPolarityFlip:

    def test_payload_never_contains_pick_polarity(self):
        out = compute_tail_fragility(
            tail_risk_payload=_tail(0.80, 0.50, 0.25, 0.10),
            market_side="under",
            bullpen_fatigue_high=True,
        )
        forbidden = {"pick", "selection", "market_side", "recommendation", "side_flip", "polarity"}
        # The payload may include the read-only "market_side" hint via
        # narrative but must NOT recommend or flip anything.
        for key in forbidden - {"market_side"}:
            assert key not in out, f"forbidden key {key} present in payload"


# ─── apply_to_fragility envelope ─────────────────────────────────────────────
class TestApplyToFragility:

    def test_applies_delta_correctly(self):
        tf = compute_tail_fragility(
            tail_risk_payload=_tail(0.90, 0.65, 0.40, 0.20),
            bullpen_fatigue_high=True,
        )
        env = apply_to_fragility(current_fragility=20, tail_fragility_payload=tf)
        assert env["applied"] is True
        assert env["delta"] == 15
        assert env["fragility_before"] == 20
        assert env["fragility_after"] == 35

    def test_no_op_when_unavailable(self):
        env = apply_to_fragility(current_fragility=20, tail_fragility_payload=None)
        assert env["applied"] is False
        assert env["delta"] == 0
        assert env["fragility_after"] == 20

    def test_clamps_at_100(self):
        tf = compute_tail_fragility(
            tail_risk_payload=_tail(0.95, 0.85, 0.70, 0.50),
            bullpen_fatigue_high=True,
            defensive_breakdown_bucket="HIGH",
            series_familiarity_bucket="HIGH",
            starter_era=5.50, starter_whip=1.60,
        )
        env = apply_to_fragility(current_fragility=95, tail_fragility_payload=tf)
        # +20 max delta capped, then result clamped at 100.
        assert env["fragility_after"] == 100
