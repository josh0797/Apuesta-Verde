"""Phase F67 — Discard Rescue Audit smoke tests.

Pins the audit-row builder, the decision classifier, and the daily
aggregation. Pure functional — no Mongo required.
"""
from __future__ import annotations

import pytest

from services.discard_rescue_audit import (
    ENGINE_VERSION,
    DECISION_ALTERNATIVE,
    DECISION_CONFIRM_DISCARD,
    DECISION_VALUE_CANDIDATE,
    DECISION_WATCHLIST,
    build_audit_entry,
    compute_daily_summary,
)


# ─────────────────────────────────────────────────────────────────────
# build_audit_entry
# ─────────────────────────────────────────────────────────────────────
def test_build_audit_entry_confirms_discard_when_no_rescue() -> None:
    pick = {"match_id": "m1", "match_label": "A vs B", "edge_pct": -30.0,
            "discard_reason": "edge_negative"}
    row = build_audit_entry(pick, original_bucket="discarded_market",
                             was_originally_hard_discard=True)
    body = row["discard_rescue_audit"]
    assert body["editorial_decision"]          == DECISION_CONFIRM_DISCARD
    assert body["rescued_market"]              is None
    assert body["was_originally_hard_discard"] is True
    assert body["edge_pct"]                    == -30.0
    assert body["original_bucket"]             == "discarded_market"


def test_build_audit_entry_detects_watchlist_from_structural_review() -> None:
    pick = {"match_id": "m2", "edge_pct": -18.0}
    structural = {
        "decision": "WATCHLIST_ODDS_NEEDED",
        "rescued_market": {"market": "Under 9.5 córners",
                           "structural_support": 78},
        "discard_strength": "SOFT_DISCARD_REVIEW",
    }
    row = build_audit_entry(pick, original_bucket="discarded_market",
                             structural_review=structural)
    body = row["discard_rescue_audit"]
    assert body["editorial_decision"]        == DECISION_WATCHLIST
    assert body["rescued_market"]            == "Under 9.5 córners"
    assert body["rescued_market_confidence"] == 78


def test_build_audit_entry_detects_alternative_from_editorial() -> None:
    pick = {"match_id": "m3"}
    editorial = {
        "available": True,
        "best_protected_market": {"market": "Under 3.5 goles",
                                   "confidence": 70, "fragility": 26},
        "editorial_sections": {},
    }
    row = build_audit_entry(pick, original_bucket="discarded_motivation",
                             editorial_prediction=editorial)
    body = row["discard_rescue_audit"]
    assert body["editorial_decision"]        == DECISION_ALTERNATIVE
    assert body["rescued_market"]            == "Under 3.5 goles"
    assert body["rescued_market_confidence"] == 70


def test_build_audit_entry_detects_value_candidate_priority() -> None:
    """When both structural says VALUE_FOUND and editorial has a
    best_protected_market, the structural decision wins."""
    pick = {"match_id": "m4"}
    structural = {"decision": "VALUE_FOUND",
                  "rescued_market": {"market": "Some VC market"}}
    editorial = {"available": True,
                 "best_protected_market": {"market": "Some alt market",
                                            "confidence": 50}}
    row = build_audit_entry(pick, original_bucket="discarded_market",
                             structural_review=structural,
                             editorial_prediction=editorial)
    body = row["discard_rescue_audit"]
    assert body["editorial_decision"] == DECISION_VALUE_CANDIDATE


def test_build_audit_entry_fail_soft_on_garbage() -> None:
    row = build_audit_entry(None, original_bucket="x")  # type: ignore[arg-type]
    assert "discard_rescue_audit" in row
    assert row["discard_rescue_audit"]["match_id"] == "_unknown"
    assert row["discard_rescue_audit"]["engine_version"] == ENGINE_VERSION


# ─────────────────────────────────────────────────────────────────────
# compute_daily_summary
# ─────────────────────────────────────────────────────────────────────
def _row(**kw):
    """Quick helper to build an audit row body (the inner dict)."""
    base = {
        "editorial_decision": DECISION_CONFIRM_DISCARD,
        "original_bucket":    "discarded_market",
        "rescued_market":     None,
        "captured_at":        "2026-06-14T10:00:00Z",
    }
    base.update(kw)
    return base


def test_compute_daily_summary_30_18_8_4_example() -> None:
    """Matches the user's example summary verbatim:

        Descartados revisados: 30
        Movidos a watchlist:    8
        Alternativas detectadas: 4
        Descartes confirmados: 18
    """
    rows = (
        [_row(editorial_decision=DECISION_CONFIRM_DISCARD)] * 18 +
        [_row(editorial_decision=DECISION_WATCHLIST,
              rescued_market="Under 9.5 córners")] * 8 +
        [_row(editorial_decision=DECISION_ALTERNATIVE,
              rescued_market="Under 3.5 goles")] * 4
    )
    out = compute_daily_summary(rows)
    assert out["window_n_entries"] == 30
    assert out["by_decision"][DECISION_CONFIRM_DISCARD] == 18
    assert out["by_decision"][DECISION_WATCHLIST]       == 8
    assert out["by_decision"][DECISION_ALTERNATIVE]     == 4
    assert out["rescued_total"]                          == 12
    assert out["rescue_rate_pct"]                        == 40.0
    assert out["noise_flag"]                             is False
    # Per-family breakdown.
    assert out["by_market_family"]["CORNERS"] == 8
    assert out["by_market_family"]["GOALS"]   == 4


def test_compute_daily_summary_flags_over_rescue_noise() -> None:
    """When rescue_rate exceeds 60% the summary marks ``noise_flag=True``."""
    rows = (
        [_row(editorial_decision=DECISION_CONFIRM_DISCARD)] * 3 +
        [_row(editorial_decision=DECISION_WATCHLIST,
              rescued_market="x corner")] * 7
    )
    out = compute_daily_summary(rows)
    assert out["rescue_rate_pct"] == 70.0
    assert out["noise_flag"]      is True
    assert "OVER_RESCUE_NOISE_SUSPECTED" in out["notes"]


def test_compute_daily_summary_empty_window() -> None:
    out = compute_daily_summary([])
    assert out["window_n_entries"] == 0
    assert out["rescue_rate_pct"]  is None
    assert "no_audit_entries_in_window" in out["notes"]


def test_compute_daily_summary_zero_rescues_marks_note() -> None:
    rows = [_row(editorial_decision=DECISION_CONFIRM_DISCARD)] * 5
    out = compute_daily_summary(rows)
    assert out["rescue_rate_pct"] == 0.0
    assert out["noise_flag"]      is False
    assert "zero_rescues_in_window" in out["notes"]


def test_compute_daily_summary_json_serialisable() -> None:
    import json
    rows = [_row(editorial_decision=DECISION_WATCHLIST,
                 rescued_market="Over 1.5 goles")] * 2
    out = compute_daily_summary(rows)
    s = json.dumps(out)
    assert ENGINE_VERSION in s
