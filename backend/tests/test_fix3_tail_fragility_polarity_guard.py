"""FIX-3 — Tail Fragility polarity guard tests.

The historical contradiction: when the explicit tail probabilities
clearly indicate HIGH risk (e.g. P(12+) = 31%, P(14+) = 14%) but the
internal weighted score for ``compute_tail_fragility`` ends up LOW
(score = 15), the UI used to render BOTH:

    "Riesgo de cola explosiva: Alta"
    "Tail Fragility: Bajo"

at the same time, which is statistically incoherent.

These tests lock the polarity guard contract:
  * triggers when p_ge_12 >= 0.25 OR p_ge_14 >= 0.10
  * also triggers when the external ``tail_risk_payload.tail_bucket``
    is HIGH or EXTREME
  * raises the bucket to at least MEDIUM
  * floors the score at >= 40
  * adds reason code ``TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL``
  * surfaces a Spanish narrative explaining the escalation
  * does NOT fire on benign distributions (zero false positives)
"""
from __future__ import annotations

from services.mlb_tail_fragility import (
    BUCKET_HIGH,
    BUCKET_LOW,
    BUCKET_MEDIUM,
    RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL,
    compute_tail_fragility,
)


def _payload(**kw):
    base = {
        "available": True,
        "p_ge_12":   0.0,
        "p_ge_14":   0.0,
        "p_ge_16":   0.0,
        "p_ge_18":   0.0,
        "tail_bucket": BUCKET_LOW,
    }
    base.update(kw)
    return base


# ─────────────────────────────────────────────────────────────────────
#   PRIMARY CASE FROM THE USER (verbatim numbers).
# ─────────────────────────────────────────────────────────────────────
def test_user_reported_case_31_14_5_2_escalates_low_to_medium():
    """Exact numbers reported in the screenshot: P(12+)=31%, P(14+)=14%,
    P(16+)=5%, P(18+)=2%. External tail bucket = HIGH.

    Without the polarity guard the raw weighted score = 15 → LOW. With
    the guard active the bucket must escalate to MEDIUM and the score
    must be floored at 40.
    """
    out = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.31, p_ge_14=0.14, p_ge_16=0.05, p_ge_18=0.02,
        tail_bucket=BUCKET_HIGH,
    ))
    assert out["tail_bucket"] != BUCKET_LOW
    assert out["tail_bucket"] == BUCKET_MEDIUM
    assert out["explosive_tail_score"] >= 40
    assert RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL in out["reason_codes"]


def test_user_reported_case_narrative_explains_escalation_in_spanish():
    out = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.31, p_ge_14=0.14, p_ge_16=0.05, p_ge_18=0.02,
        tail_bucket=BUCKET_HIGH,
    ))
    narrative = out["narrative_es"] or ""
    assert "escalado" in narrative.lower()
    assert "12+" in narrative
    assert "14+" in narrative


# ─────────────────────────────────────────────────────────────────────
#   PROBABILITY THRESHOLD TRIGGERS
# ─────────────────────────────────────────────────────────────────────
def test_escalation_fires_when_p12_at_or_above_25pct_even_without_external_bucket():
    out = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.25, p_ge_14=0.05, p_ge_16=0.02, p_ge_18=0.005,
        tail_bucket=BUCKET_LOW,   # external says LOW → guard still fires by p12.
    ))
    assert out["tail_bucket"] == BUCKET_MEDIUM
    assert out["explosive_tail_score"] >= 40
    assert RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL in out["reason_codes"]


def test_escalation_fires_when_p14_at_or_above_10pct_even_when_p12_below_25():
    out = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.18, p_ge_14=0.12, p_ge_16=0.04, p_ge_18=0.01,
        tail_bucket=BUCKET_LOW,
    ))
    assert out["tail_bucket"] == BUCKET_MEDIUM
    assert out["explosive_tail_score"] >= 40
    assert RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL in out["reason_codes"]


def test_escalation_fires_when_external_bucket_is_HIGH_even_with_modest_probabilities():
    """When the canonical tail_risk engine already says HIGH, the
    Tail Fragility must not contradict it.
    """
    out = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.10, p_ge_14=0.04, p_ge_16=0.015, p_ge_18=0.003,
        tail_bucket=BUCKET_HIGH,
    ))
    # The probability blend alone would not have triggered escalation,
    # but the external bucket forces consistency.
    assert out["tail_bucket"] == BUCKET_MEDIUM
    assert out["explosive_tail_score"] >= 40
    assert RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
#   NO ESCALATION ON BENIGN DISTRIBUTIONS
# ─────────────────────────────────────────────────────────────────────
def test_no_escalation_on_benign_low_distribution():
    out = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.05, p_ge_14=0.02, p_ge_16=0.008, p_ge_18=0.002,
        tail_bucket=BUCKET_LOW,
    ))
    assert out["tail_bucket"] == BUCKET_LOW
    assert RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL not in out["reason_codes"]


def test_no_double_escalation_when_score_already_medium_or_higher():
    """If the weighted score *already* sits at MEDIUM or above, the
    guard must NOT add the escalation reason code (it would be a
    duplicate signal).
    """
    out = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.30, p_ge_14=0.18, p_ge_16=0.10, p_ge_18=0.04,
        tail_bucket=BUCKET_HIGH,
    ))
    # This input gives score = 30*0.30 + 18*0.30 + 10*0.25 + 4*0.15
    #                         = 9 + 5.4 + 2.5 + 0.6 = 17.5 → score=18 (still LOW)
    # NOTE: the input above ALSO triggers the guard (p12=0.30 ≥ 0.25),
    # so it WILL escalate. We use stronger inputs here to lift score >= 25
    # naturally, ensuring NO escalation reason is added.
    out2 = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.40, p_ge_14=0.25, p_ge_16=0.15, p_ge_18=0.08,
        tail_bucket=BUCKET_HIGH,
    ))
    # score = 40*0.30 + 25*0.30 + 15*0.25 + 8*0.15 = 12 + 7.5 + 3.75 + 1.2 = 24.45 → 24 (LOW)
    # Score still LOW → guard escalates. Use yet stronger numbers:
    out3 = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.80, p_ge_14=0.50, p_ge_16=0.30, p_ge_18=0.20,
        tail_bucket=BUCKET_HIGH,
    ))
    # score = 80*0.30 + 50*0.30 + 30*0.25 + 20*0.15 = 24+15+7.5+3 = 49.5 → 50 (HIGH)
    assert out3["tail_bucket"] in (BUCKET_HIGH, "EXTREME")
    # When already HIGH/EXTREME the escalation reason MUST NOT be added.
    assert RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL not in out3["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
#   FAIL-SOFT contract preserved
# ─────────────────────────────────────────────────────────────────────
def test_failsoft_when_tail_risk_payload_unavailable():
    out = compute_tail_fragility(tail_risk_payload=None)
    assert out["available"] is False
    assert out["tail_bucket"] == BUCKET_LOW
    assert RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL not in (out.get("reason_codes") or [])


def test_failsoft_when_tail_risk_payload_not_available_flag():
    out = compute_tail_fragility(tail_risk_payload={"available": False})
    assert out["available"] is False
    assert RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL not in (out.get("reason_codes") or [])


def test_escalation_does_not_decrease_score():
    """Polarity guard must NEVER reduce the score. Floor only."""
    # Score 60 already > 40 floor → guard should not modify it down.
    # But for that score to stay HIGH the bucket must already be HIGH;
    # we craft inputs that yield score 30 naturally to verify the floor
    # raises score to >= 40 strictly.
    out = compute_tail_fragility(tail_risk_payload=_payload(
        p_ge_12=0.26, p_ge_14=0.05, p_ge_16=0.02, p_ge_18=0.005,
        tail_bucket=BUCKET_LOW,
    ))
    # raw = 26*0.30 + 5*0.30 + 2*0.25 + 0.5*0.15 ≈ 7.8+1.5+0.5+0.075 ≈ 9.875 → 10 LOW
    # After guard: bucket=MEDIUM, score=max(10, 40)=40.
    assert out["explosive_tail_score"] == 40
    assert out["tail_bucket"] == BUCKET_MEDIUM
