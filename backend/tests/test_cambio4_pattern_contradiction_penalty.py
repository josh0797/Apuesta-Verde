"""Tests for CAMBIO 4 — Symmetric pattern-contradiction confidence penalty.

Verifies that when the pick_payload has a pattern_alignment with enough
contradiction, the orchestrator (or in this case a direct simulation of
the inserted block) correctly produces:
    - confidence_pre_pattern_penalty
    - pick_conflict_state
    - pattern_penalty_applied
    - PATTERN_CONTRADICTION_CONFIDENCE_PENALTY in reason_codes
    - mlb_source_of_truth.pattern_penalty_applied
    - mlb_source_of_truth.conflict_state
"""
from __future__ import annotations


def _safe_float(v):
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _apply_pattern_penalty(pick_payload: dict, log_fn=None) -> dict:
    """Replica directa del bloque CAMBIO 4 insertado en
    mlb_day_orchestrator.py. Lo aislamos para tests deterministas."""
    _pat = pick_payload.get("pattern_alignment") or {}
    _supporting = int(_pat.get("supporting_count")
                      or len(_pat.get("supporting") or []))
    _contradicting = int(_pat.get("contradicting_count")
                         or len(_pat.get("contradicting") or []))
    _rec = pick_payload.get("recommendation") or {}
    _conf = _safe_float(_rec.get("confidence_score"))

    if _conf is not None and (_supporting + _contradicting) >= 3:
        _ratio = _contradicting / max(1, _supporting + _contradicting)
        if _ratio >= 0.80:
            _pen, _state = 18, "VALUE_CON_CONFLICTO"
        elif _ratio >= 0.65:
            _pen, _state = 12, "VALUE_CON_CONFLICTO"
        elif _ratio >= 0.55:
            _pen, _state = 8, "VALUE_REVISAR"
        else:
            _pen, _state = 0, None

        if _pen > 0:
            _new_conf = max(0.0, _conf - _pen)
            pick_payload["confidence_pre_pattern_penalty"] = _conf
            _rec["confidence_score"] = round(_new_conf, 2)
            _rec.setdefault("reason_codes", []).append(
                "PATTERN_CONTRADICTION_CONFIDENCE_PENALTY"
            )
            pick_payload["recommendation"] = _rec
            pick_payload["pick_conflict_state"] = _state
            pick_payload["pattern_penalty_applied"] = {
                "supporting":    _supporting,
                "contradicting": _contradicting,
                "ratio":         round(_ratio, 2),
                "penalty":       _pen,
                "market_side":   pick_payload.get("mlb_source_of_truth", {}).get("market_side"),
            }

    if isinstance(pick_payload.get("mlb_source_of_truth"), dict):
        pick_payload["mlb_source_of_truth"]["pattern_penalty_applied"] = bool(
            pick_payload.get("pattern_penalty_applied")
        )
        pick_payload["mlb_source_of_truth"]["conflict_state"] = pick_payload.get("pick_conflict_state")

    return pick_payload


# ── Test cases ───────────────────────────────────────────────────────


def _base_pick(conf, sup, con, market_side="under"):
    return {
        "recommendation": {"market": "F5 Under 4.5", "confidence_score": conf},
        "pattern_alignment": {
            "supporting_count":    sup,
            "contradicting_count": con,
            "supporting":          [{"pattern": f"s{i}"} for i in range(sup)],
            "contradicting":       [{"pattern": f"c{i}"} for i in range(con)],
        },
        "mlb_source_of_truth": {"market_side": market_side},
    }


def test_cambio4_strong_conflict_ratio_80_penalty_18():
    """5 contradicting, 0 supporting → ratio 1.0 → -18 → VALUE_CON_CONFLICTO."""
    p = _base_pick(conf=82, sup=0, con=5)
    out = _apply_pattern_penalty(p)
    assert out["pick_conflict_state"] == "VALUE_CON_CONFLICTO"
    assert out["recommendation"]["confidence_score"] == 64.0
    assert out["confidence_pre_pattern_penalty"] == 82.0
    assert out["pattern_penalty_applied"]["penalty"] == 18
    assert out["pattern_penalty_applied"]["ratio"] == 1.0
    assert "PATTERN_CONTRADICTION_CONFIDENCE_PENALTY" in out["recommendation"]["reason_codes"]


def test_cambio4_moderate_conflict_ratio_65_penalty_12():
    """2 supporting, 5 contradicting → ratio 0.714 → -12 → VALUE_CON_CONFLICTO."""
    p = _base_pick(conf=82, sup=2, con=5)
    out = _apply_pattern_penalty(p)
    assert out["pick_conflict_state"] == "VALUE_CON_CONFLICTO"
    assert out["recommendation"]["confidence_score"] == 70.0
    assert out["pattern_penalty_applied"]["penalty"] == 12


def test_cambio4_mild_conflict_ratio_55_penalty_8():
    """2 supporting, 3 contradicting → ratio 0.6 → -8 → VALUE_REVISAR."""
    p = _base_pick(conf=82, sup=2, con=3)
    out = _apply_pattern_penalty(p)
    assert out["pick_conflict_state"] == "VALUE_REVISAR"
    assert out["recommendation"]["confidence_score"] == 74.0
    assert out["pattern_penalty_applied"]["penalty"] == 8


def test_cambio4_below_threshold_no_change():
    """3 supporting, 2 contradicting → ratio 0.4 → 0 → no change."""
    p = _base_pick(conf=82, sup=3, con=2)
    out = _apply_pattern_penalty(p)
    assert out.get("pick_conflict_state") is None
    assert out["recommendation"]["confidence_score"] == 82
    assert "pattern_penalty_applied" not in out
    assert "confidence_pre_pattern_penalty" not in out


def test_cambio4_min_total_patterns_3():
    """1 supporting, 1 contradicting (total 2) → below min threshold → no change."""
    p = _base_pick(conf=82, sup=1, con=1)
    out = _apply_pattern_penalty(p)
    assert out.get("pick_conflict_state") is None
    assert out["recommendation"]["confidence_score"] == 82


def test_cambio4_symmetric_polarity_under_vs_over():
    """SIMETRÍA: Under con 0/5 y Over con 0/5 reciben IDÉNTICA penalización."""
    under = _apply_pattern_penalty(_base_pick(conf=82, sup=0, con=5, market_side="under"))
    over  = _apply_pattern_penalty(_base_pick(conf=82, sup=0, con=5, market_side="over"))
    # Mismo state, misma confidence final, misma penalty.
    assert under["pick_conflict_state"] == over["pick_conflict_state"]
    assert under["recommendation"]["confidence_score"] == over["recommendation"]["confidence_score"]
    assert under["pattern_penalty_applied"]["penalty"] == over["pattern_penalty_applied"]["penalty"]
    # Solo difiere el market_side en el log de telemetría.
    assert under["pattern_penalty_applied"]["market_side"] == "under"
    assert over["pattern_penalty_applied"]["market_side"] == "over"


def test_cambio4_sot_telemetry_completed():
    """mlb_source_of_truth gets `pattern_penalty_applied` and `conflict_state` updated."""
    p = _base_pick(conf=82, sup=0, con=5)
    out = _apply_pattern_penalty(p)
    sot = out["mlb_source_of_truth"]
    assert sot["pattern_penalty_applied"] is True
    assert sot["conflict_state"] == "VALUE_CON_CONFLICTO"


def test_cambio4_sot_telemetry_unchanged_when_no_penalty():
    """Without trigger, SOT shows pattern_penalty_applied=False, conflict_state=None."""
    p = _base_pick(conf=82, sup=3, con=2)
    out = _apply_pattern_penalty(p)
    sot = out["mlb_source_of_truth"]
    assert sot["pattern_penalty_applied"] is False
    assert sot["conflict_state"] is None


def test_cambio4_confidence_never_negative():
    """Edge case: very low conf + max penalty should clamp at 0, not go negative."""
    p = _base_pick(conf=10, sup=0, con=5)
    out = _apply_pattern_penalty(p)
    assert out["recommendation"]["confidence_score"] == 0.0


def test_cambio4_high_confidence_full_drop():
    """conf=95, max penalty (-18) → 77 (still VALUE_CON_CONFLICTO state preserved)."""
    p = _base_pick(conf=95, sup=0, con=5)
    out = _apply_pattern_penalty(p)
    assert out["pick_conflict_state"] == "VALUE_CON_CONFLICTO"
    assert out["recommendation"]["confidence_score"] == 77.0
    assert out["confidence_pre_pattern_penalty"] == 95.0
