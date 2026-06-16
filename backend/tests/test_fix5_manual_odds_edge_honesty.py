"""FIX-NEW-2 (Football manual odds) — Edge-honesty regression tests.

The historical bug: ``recalculate_with_manual_market`` fabricated a
model probability as ``implied_prob * 1.05`` when no base pick was
available. That guaranteed a positive ``manual_edge`` for EVERY odd,
even silly ones like 1.01 (where the fake model probability would
land at 103.95%, which is impossible).

The fix locks the following contract:
  * If a base pick has ``model_probability`` → use it (exact behaviour
    preserved).
  * Else if a base pick has ``_moneyball.confidence`` ∈ (0, 100] →
    use ``confidence / 100`` clamped to [0.05, 0.95] as a *weak proxy*
    with ``model_prob_source = "confidence_weak_proxy"`` and
    ``status = "MANUAL_WEAK_PROXY"``.
  * Else → DO NOT fabricate anything. Return ``manual_edge=None``,
    ``model_probability=None``, ``status="MODEL_PROBABILITY_UNAVAILABLE"``.
"""
from __future__ import annotations

from services.manual_market_identity import recalculate_with_manual_market


def _payload(market="MATCH_WINNER", selection="HOME", odd=1.275, line=None):
    return {
        "match_id":    "fx_test",
        "market_type": market,
        "selection":   selection,
        "manual_odd":  odd,
        "line":        line,
    }


# ─────────────────────────────────────────────────────────────────────
#   No base pick: never fabricate edge
# ─────────────────────────────────────────────────────────────────────

def test_no_base_pick_does_not_fabricate_positive_edge():
    out = recalculate_with_manual_market(_payload(odd=1.275), base_pick=None)
    rp = out["recalculated_pick"]
    assert rp["manual_edge"] is None
    assert rp["model_probability"] is None
    assert rp["status"] == "MODEL_PROBABILITY_UNAVAILABLE"
    assert rp["model_prob_source"] == "missing"


def test_silly_low_odd_no_longer_lies_about_value():
    """Cuota 1.01 (implícita ≈ 99%) used to show a fake +5% edge.
    Now it must clearly state the absence of model probability."""
    out = recalculate_with_manual_market(_payload(odd=1.01), base_pick=None)
    rp = out["recalculated_pick"]
    assert rp["manual_edge"] is None
    assert rp["model_probability"] is None
    assert rp["status"] == "MODEL_PROBABILITY_UNAVAILABLE"
    assert "no se puede calcular edge" in (rp["verdict"] or "").lower()


def test_warnings_block_explains_missing_model():
    out = recalculate_with_manual_market(_payload(odd=2.0), base_pick=None)
    warnings = out.get("warnings") or []
    joined = " ".join(warnings).lower()
    assert "no fue posible calcular edge" in joined


# ─────────────────────────────────────────────────────────────────────
#   Base pick with model probability — exact behaviour preserved
# ─────────────────────────────────────────────────────────────────────

def test_base_pick_with_model_probability_computes_negative_edge_for_bad_odd():
    base_pick = {
        "_market_edge": {"estimated_probability": 0.70},
        "_moneyball":   {"fragility": {"score": 25}, "confidence": 65},
    }
    out = recalculate_with_manual_market(_payload(odd=1.275), base_pick=base_pick)
    rp = out["recalculated_pick"]
    # implied = 78.43% > model 70% → edge negative.
    assert rp["manual_edge"] == -8.43
    assert rp["model_probability"] == 70.0
    assert rp["status"] == "MANUAL_NO_VALUE"
    assert rp["model_prob_source"] == "base_pick"


def test_base_pick_with_model_probability_computes_positive_edge_when_market_overprices():
    base_pick = {"_market_edge": {"estimated_probability": 0.82}}
    out = recalculate_with_manual_market(_payload(odd=1.275), base_pick=base_pick)
    rp = out["recalculated_pick"]
    # implied = 78.43%; model 82% → edge ≈ +3.57.
    assert rp["manual_edge"] is not None
    assert rp["manual_edge"] > 0
    assert rp["status"] in ("MANUAL_VALUE_REVIEW", "MANUAL_THIN_VALUE")


def test_silly_low_odd_with_base_pick_correctly_shows_huge_negative_edge():
    base_pick = {"_market_edge": {"estimated_probability": 0.70}}
    out = recalculate_with_manual_market(_payload(odd=1.01), base_pick=base_pick)
    rp = out["recalculated_pick"]
    assert rp["manual_edge"] < -20.0          # implied 99% vs model 70%.
    assert rp["status"] == "MANUAL_NO_VALUE"


# ─────────────────────────────────────────────────────────────────────
#   Confidence as soft proxy
# ─────────────────────────────────────────────────────────────────────

def test_confidence_used_as_weak_proxy_when_no_model_probability():
    base_pick = {"_moneyball": {"confidence": 65, "fragility": {"score": 25}}}
    out = recalculate_with_manual_market(_payload(odd=1.275), base_pick=base_pick)
    rp = out["recalculated_pick"]
    assert rp["model_probability"] == 65.0
    assert rp["model_prob_source"] == "confidence_weak_proxy"
    assert rp["status"] == "MANUAL_WEAK_PROXY"
    # Verdict must include the proxy disclaimer (case-insensitive).
    assert "proxy" in (rp["verdict"] or "").lower()


def test_confidence_clamped_to_safe_range():
    # Confidence = 100 → would be 1.0 → clamp to 0.95.
    base_pick = {"_moneyball": {"confidence": 100}}
    out = recalculate_with_manual_market(_payload(odd=2.0), base_pick=base_pick)
    rp = out["recalculated_pick"]
    assert rp["model_probability"] == 95.0


def test_confidence_zero_or_negative_is_ignored():
    base_pick = {"_moneyball": {"confidence": 0}}
    out = recalculate_with_manual_market(_payload(odd=1.275), base_pick=base_pick)
    rp = out["recalculated_pick"]
    # Zero confidence → no proxy → no edge.
    assert rp["manual_edge"] is None
    assert rp["status"] == "MODEL_PROBABILITY_UNAVAILABLE"
