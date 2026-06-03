"""Tests for mlb_pipeline_payload_contract — Moneyball payload sealing."""
from __future__ import annotations

import pytest

from services.mlb_pipeline_payload_contract import (
    seal_pick_payload,
    build_manual_odds_review,
    build_ghost_edges_summary,
    build_pattern_memory_audit,
    merge_pipeline_external_sources,
    CONTRACT_FIELDS,
)


# ─────────────────────────────────────────────────────────────────────
# seal_pick_payload
# ─────────────────────────────────────────────────────────────────────
def test_seal_payload_all_contract_fields_present_on_empty_input():
    """Empty payload must end up with every contract field stamped as
    ``available:false`` so the UI never crashes on missing data."""
    payload: dict = {}
    seal_pick_payload(payload)
    for field in CONTRACT_FIELDS:
        assert field in payload, f"contract field missing: {field}"
        block = payload[field]
        # `fragility_score` and `script_survival_score` may be either a
        # scalar number (legacy) or a structured dict. The audit blocks
        # (`fragility_audit` / `script_survival_audit`) ALWAYS carry the
        # structured form.
        if field in ("fragility_score", "script_survival_score"):
            assert isinstance(block, (dict, int, float)) or block is None
            continue
        assert isinstance(block, dict)
        assert "available" in block, f"{field} missing 'available'"


def test_seal_payload_non_dict_input_returns_dict():
    """Fail-soft: a non-dict input is converted into a fresh dict."""
    out = seal_pick_payload("not a dict")  # type: ignore[arg-type]
    assert isinstance(out, dict)
    for field in CONTRACT_FIELDS:
        assert field in out


def test_seal_payload_preserves_legacy_fragility_score_scalar():
    """Existing scalar fragility_score must NOT be overwritten by the
    structured contract block (frontend depends on `score != null`)."""
    payload = {"fragility_score": 42.0}
    seal_pick_payload(payload)
    assert payload["fragility_score"] == 42.0
    # The structured audit must be available under the new key.
    assert "fragility_audit" in payload
    assert isinstance(payload["fragility_audit"], dict)


def test_seal_payload_preserves_existing_market_selection():
    """When market_selection already has a recommended_market, the
    contract must NOT downgrade it to unavailable."""
    payload = {
        "market_selection": {
            "recommended_market": "F5 Under",
            "market_confidence":  72,
            "fragility":          18,
            "requires_manual_odds": False,
            "reason_codes":       ["SABERMETRICS_CONFIRMED_EDGE"],
        }
    }
    seal_pick_payload(payload)
    assert payload["market_selection"]["available"] is True
    assert payload["market_selection"]["recommended_market"] == "F5 Under"


# ─────────────────────────────────────────────────────────────────────
# build_ghost_edges_summary
# ─────────────────────────────────────────────────────────────────────
def test_ghost_edges_detected_from_discrepancies():
    payload = {
        "model_verification": {
            "discrepancies": [
                {"flag": "ERA_UNDERSTATES_RISK"},
                {"flag": "PITCHER_XWOBA_WARNING"},
                {"flag": "irrelevant_other_flag"},  # filtered out
            ]
        },
        "market_selection": {"reason_codes": ["GHOST_EDGE_BLOCKED_PICK"]},
    }
    g = build_ghost_edges_summary(payload)
    assert g["available"] is True
    assert "ERA_UNDERSTATES_RISK" in g["flags"]
    assert "PITCHER_XWOBA_WARNING" in g["flags"]
    assert "irrelevant_other_flag" not in g["flags"]
    assert g["blocked_pick"] is True
    assert g["count"] == 2


def test_ghost_edges_empty_when_no_signals():
    g = build_ghost_edges_summary({})
    assert g["available"] is False
    assert g["count"] == 0
    assert g["blocked_pick"] is False


# ─────────────────────────────────────────────────────────────────────
# build_pattern_memory_audit
# ─────────────────────────────────────────────────────────────────────
def test_pattern_memory_low_sample():
    payload = {
        "historical_pattern_match": {
            "sample_size": 8,
            "historical_hit_rate": 0.62,
            "historical_roi": 0.10,
        }
    }
    a = build_pattern_memory_audit(payload)
    assert a["sample_size"] == 8
    assert a["sample_tier"] == "LOW_SAMPLE"
    # The UI should not let the user think this is a high-confidence pattern.
    assert a["available"] is False


def test_pattern_memory_validated_when_strong_sample_and_positive_roi():
    payload = {
        "historical_pattern_match": {
            "sample_size": 80,
            "historical_hit_rate": 0.61,
            "historical_roi": 0.15,
            "best_historical_market": "F5 Under",
        }
    }
    a = build_pattern_memory_audit(payload)
    assert a["available"] is True
    assert a["sample_tier"] == "VALIDATED"
    assert a["best_historical_market"] == "F5 Under"


def test_pattern_memory_empty():
    a = build_pattern_memory_audit({})
    assert a["available"] is False
    assert a["sample_size"] == 0
    assert a["sample_tier"] == "NONE"


# ─────────────────────────────────────────────────────────────────────
# build_manual_odds_review
# ─────────────────────────────────────────────────────────────────────
def test_manual_review_not_required_when_odds_present():
    payload = {
        "recommendation": {"odds_range": "1.85-2.00", "confidence_score": 65},
        "market_selection": {
            "recommended_market": "F5 Under",
            "requires_manual_odds": False,
        },
    }
    m = build_manual_odds_review(payload)
    assert m["required"] is False
    assert m["available"] is False


def test_manual_review_required_when_market_selection_demands_it():
    payload = {
        "recommendation": {},
        "market_selection": {
            "recommended_market": "Manual Odds Review",
            "requires_manual_odds": True,
            "fragility": 22,
            "market_confidence": 0,
        },
    }
    m = build_manual_odds_review(payload)
    assert m["required"] is True
    assert m["available"] is True
    assert "Revisión manual" in m["user_action_es"]


def test_manual_review_structural_lean_user_message_is_paste_odds():
    payload = {
        "_bucket": "structural_lean_requires_odds",
        "recommendation": {},
        "market_selection": {"recommended_market": "F5 Under"},
    }
    m = build_manual_odds_review(payload)
    assert m["required"] is True
    assert "Pega la cuota" in m["user_action_es"]


# ─────────────────────────────────────────────────────────────────────
# merge_pipeline_external_sources
# ─────────────────────────────────────────────────────────────────────
def test_external_sources_default_block_always_present():
    meta = {"date": "2026-06-01"}
    merge_pipeline_external_sources(meta)
    ext = meta["external_sources"]
    for k in ("statcast", "sabermetrics", "editorial", "warehouse", "statsapi"):
        assert k in ext
        assert "used" in ext[k]
        assert "status" in ext[k]


def test_external_sources_statsapi_marked_ok_when_orchestrator_used_it():
    meta = {
        "source_used": "statsapi",
        "statsapi_url": "https://statsapi.mlb.com/v1/schedule",
        "cache_status": "hit",
    }
    merge_pipeline_external_sources(meta)
    ext = meta["external_sources"]
    assert ext["statsapi"]["used"] is True
    assert ext["statsapi"]["status"] == "ok"
    # cache hit → warehouse ok
    assert ext["warehouse"]["status"] == "ok"
    assert ext["warehouse"]["used"] is True


def test_external_sources_editorial_status_propagated():
    meta = {}
    merge_pipeline_external_sources(
        meta,
        editorial_status={"used": True, "status": "ok", "sources_count": 5},
    )
    ext = meta["external_sources"]
    assert ext["editorial"]["used"] is True
    assert ext["editorial"]["status"] == "ok"
    assert ext["editorial"].get("sources_count") == 5
