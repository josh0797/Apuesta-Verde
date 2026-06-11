"""Integration tests for Phase 59 — L5 vs L15 Run Profile Cross
applied within the orchestrator block (CAMBIO 4 → Phase 59 sequencing).

These tests simulate the orchestrator block in isolation to verify the
correct ORDER OF APPLICATION:
    1) CAMBIO 4 penalty (pattern contradiction) applied first.
    2) Phase 59 run-profile-cross applied second on the already-penalized
       confidence/fragility.
"""
from __future__ import annotations

from services.mlb_run_profile_cross import (
    compute_combined_run_profile_cross,
    apply_run_profile_cross_to_pick,
    build_pattern_alignment_entry,
)


def _simulate_orchestrator_phase59(pick_payload: dict) -> dict:
    """Replicates the Phase 59 block inside mlb_day_orchestrator.py
    so we can exercise the wiring without firing the whole pipeline."""
    rrs = pick_payload.get("recent_run_split") or {}
    cross = compute_combined_run_profile_cross(
        home_scored_l5=rrs.get("runs_scored_avg_last_5_home"),
        home_scored_l15=rrs.get("runs_scored_avg_last_15_home"),
        home_allowed_l5=rrs.get("runs_allowed_avg_last_5_home"),
        home_allowed_l15=rrs.get("runs_allowed_avg_last_15_home"),
        away_scored_l5=rrs.get("runs_scored_avg_last_5_away"),
        away_scored_l15=rrs.get("runs_scored_avg_last_15_away"),
        away_allowed_l5=rrs.get("runs_allowed_avg_last_5_away"),
        away_allowed_l15=rrs.get("runs_allowed_avg_last_15_away"),
    )
    pick_payload["combined_run_profile_cross"] = cross

    if cross.get("available") and cross.get("supports") != "NEUTRAL":
        rec = pick_payload.get("recommendation") or {}
        market = (rec.get("market") or "").lower()
        side = (
            "under" if "under" in market and "team total" not in market
            else "over" if "over" in market and "team total" not in market
            else None
        )
        conf = rec.get("confidence_score")
        frag = (pick_payload.get("fragility") or {}).get("score") \
            or pick_payload.get("fragility_score")
        applied = apply_run_profile_cross_to_pick(
            cross_payload=cross, pick_side=side,
            current_confidence=conf, current_fragility=frag,
        )
        if applied.get("applied"):
            if applied.get("new_confidence") is not None:
                rec["confidence_score"] = round(float(applied["new_confidence"]), 2)
                rec.setdefault("reason_codes", [])
                for rc in applied.get("reason_codes") or []:
                    if rc not in rec["reason_codes"]:
                        rec["reason_codes"].append(rc)
                pick_payload["recommendation"] = rec
            if applied.get("new_fragility") is not None:
                if not isinstance(pick_payload.get("fragility"), dict):
                    pick_payload["fragility"] = {}
                pick_payload["fragility"]["score"] = float(applied["new_fragility"])
                pick_payload["fragility_score"] = float(applied["new_fragility"])
            pick_payload["run_profile_cross_applied"] = {
                "profile":                 cross.get("profile"),
                "supports":                cross.get("supports"),
                "interaction":             applied.get("interaction"),
                "confidence_delta_signed": applied.get("confidence_delta_signed"),
                "fragility_delta_signed":  applied.get("fragility_delta_signed"),
                "pick_side":               side,
                "reason_codes":            applied.get("reason_codes") or [],
            }
        entry = build_pattern_alignment_entry(cross, side)
        if entry:
            pa = pick_payload.get("pattern_alignment") or {}
            entries = list(pa.get("entries") or [])
            entries.append(entry)
            pa["entries"] = entries
            pick_payload["pattern_alignment"] = pa
    return pick_payload


def _base_under_payload(conf=64.0, frag=20.0):
    """Fake post-CAMBIO-4 payload: an UNDER pick that already received
    a pattern_contradiction_penalty (conf dropped from 82 to 64)."""
    return {
        "recommendation": {
            "market":          "F5 Under 4.5",
            "confidence_score": conf,
            "reason_codes":    ["PATTERN_CONTRADICTION_CONFIDENCE_PENALTY"],
        },
        "fragility": {"score": frag, "source": "distribution_calibrated"},
        "fragility_score": frag,
        "pick_conflict_state": "VALUE_CON_CONFLICTO",
        "pattern_penalty_applied": {"penalty": 18, "ratio": 0.85},
        "confidence_pre_pattern_penalty": 82,
        "pattern_alignment": {
            "supporting_count":    1,
            "contradicting_count": 4,
            "entries":             [],
        },
        "mlb_source_of_truth": {"market_side": "under", "pattern_penalty_applied": True},
    }


def _base_over_payload(conf=70.0, frag=30.0):
    p = _base_under_payload(conf=conf, frag=frag)
    p["recommendation"]["market"] = "Total Runs Over 9.5"
    p["mlb_source_of_truth"]["market_side"] = "over"
    return p


# ─────────────────────────────────────────────────────────────────────


def test_phase59_strong_under_cross_supports_under_pick_after_cambio4():
    """Order check: CAMBIO 4 already dropped conf to 64. Phase 59 sees
    a STRONG_UNDER cross + UNDER pick → bonus +8 (capped) → 72."""
    p = _base_under_payload(conf=64.0, frag=20.0)
    p["recent_run_split"] = {
        "runs_scored_avg_last_5_home":   3.2, "runs_scored_avg_last_15_home": 4.1,
        "runs_allowed_avg_last_5_home":  3.5, "runs_allowed_avg_last_15_home": 4.2,
        "runs_scored_avg_last_5_away":   3.4, "runs_scored_avg_last_15_away": 4.5,
        "runs_allowed_avg_last_5_away":  3.6, "runs_allowed_avg_last_15_away": 3.8,
    }
    out = _simulate_orchestrator_phase59(p)
    assert out["combined_run_profile_cross"]["profile"] == "STRONG_UNDER_CROSS"
    assert out["run_profile_cross_applied"]["interaction"] == "SUPPORTS_PICK"
    assert out["recommendation"]["confidence_score"] == 72.0   # 64 + 8 (capped)
    assert out["fragility"]["score"] == 14.0                   # 20 - 6
    # Reason codes preserved AND new ones appended (order-independent).
    rcs = out["recommendation"]["reason_codes"]
    assert "PATTERN_CONTRADICTION_CONFIDENCE_PENALTY" in rcs
    assert "RUN_PROFILE_CROSS_SUPPORTS_UNDER" in rcs


def test_phase59_strong_over_cross_contradicts_under_pick():
    """STRONG_OVER cross against an UNDER pick → penalty -12 (capped at
    MAX_CONFIDENCE_PENALTY since raw delta is 12)."""
    p = _base_under_payload(conf=64.0, frag=20.0)
    p["recent_run_split"] = {
        "runs_scored_avg_last_5_home":   5.8, "runs_scored_avg_last_15_home": 4.3,
        "runs_allowed_avg_last_5_home":  5.4, "runs_allowed_avg_last_15_home": 4.2,
        "runs_scored_avg_last_5_away":   5.2, "runs_scored_avg_last_15_away": 4.5,
        "runs_allowed_avg_last_5_away":  5.7, "runs_allowed_avg_last_15_away": 4.0,
    }
    out = _simulate_orchestrator_phase59(p)
    assert out["combined_run_profile_cross"]["profile"] == "STRONG_OVER_CROSS"
    assert out["run_profile_cross_applied"]["interaction"] == "CONTRADICTS_PICK"
    assert out["recommendation"]["confidence_score"] == 52.0   # 64 - 12 (penalty cap)
    assert out["fragility"]["score"] == 28.0                   # 20 + 8


def test_phase59_visual_entry_does_NOT_change_counts():
    """The visual entry must NOT touch supporting_count/contradicting_count
    so it doesn't double-count via the CAMBIO 4 ratio."""
    p = _base_under_payload()
    p["recent_run_split"] = {
        "runs_scored_avg_last_5_home":   3.2, "runs_scored_avg_last_15_home": 4.1,
        "runs_allowed_avg_last_5_home":  3.5, "runs_allowed_avg_last_15_home": 4.2,
        "runs_scored_avg_last_5_away":   3.4, "runs_scored_avg_last_15_away": 4.5,
        "runs_allowed_avg_last_5_away":  3.6, "runs_allowed_avg_last_15_away": 3.8,
    }
    pa_before = p["pattern_alignment"]
    out = _simulate_orchestrator_phase59(p)
    pa_after = out["pattern_alignment"]
    # Counts MUST be unchanged.
    assert pa_after["supporting_count"] == pa_before["supporting_count"]
    assert pa_after["contradicting_count"] == pa_before["contradicting_count"]
    # But a visual entry was appended.
    assert len(pa_after["entries"]) == 1
    assert pa_after["entries"][0]["visual_only"] is True
    assert pa_after["entries"][0]["pattern"] == "STRONG_UNDER_CROSS"


def test_phase59_neutral_profile_skips_all_writes():
    """A benign cross (all teams near average) → no changes whatsoever."""
    p = _base_under_payload(conf=64.0, frag=20.0)
    p["recent_run_split"] = {
        "runs_scored_avg_last_5_home":   4.5, "runs_scored_avg_last_15_home": 4.5,
        "runs_allowed_avg_last_5_home":  4.5, "runs_allowed_avg_last_15_home": 4.5,
        "runs_scored_avg_last_5_away":   4.5, "runs_scored_avg_last_15_away": 4.5,
        "runs_allowed_avg_last_5_away":  4.5, "runs_allowed_avg_last_15_away": 4.5,
    }
    out = _simulate_orchestrator_phase59(p)
    assert out["combined_run_profile_cross"]["available"] is True
    assert out["combined_run_profile_cross"]["supports"] == "NEUTRAL"
    assert "run_profile_cross_applied" not in out
    assert out["recommendation"]["confidence_score"] == 64.0  # unchanged
    assert out["fragility"]["score"] == 20.0                  # unchanged


def test_phase59_failsoft_missing_recent_run_split():
    """Without recent_run_split, the cross should be unavailable and
    no fields should be modified."""
    p = _base_under_payload(conf=64.0, frag=20.0)
    # No recent_run_split at all.
    out = _simulate_orchestrator_phase59(p)
    assert out["combined_run_profile_cross"]["available"] is False
    assert "run_profile_cross_applied" not in out
    assert out["recommendation"]["confidence_score"] == 64.0
    assert out["fragility"]["score"] == 20.0


def test_phase59_over_pick_strong_over_cross_bonus():
    """Symmetric mirror: STRONG_OVER cross + OVER pick → bonus +8 capped."""
    p = _base_over_payload(conf=70.0, frag=30.0)
    p["recent_run_split"] = {
        "runs_scored_avg_last_5_home":   5.8, "runs_scored_avg_last_15_home": 4.3,
        "runs_allowed_avg_last_5_home":  5.4, "runs_allowed_avg_last_15_home": 4.2,
        "runs_scored_avg_last_5_away":   5.2, "runs_scored_avg_last_15_away": 4.5,
        "runs_allowed_avg_last_5_away":  5.7, "runs_allowed_avg_last_15_away": 4.0,
    }
    out = _simulate_orchestrator_phase59(p)
    assert out["run_profile_cross_applied"]["interaction"] == "SUPPORTS_PICK"
    assert out["recommendation"]["confidence_score"] == 78.0   # 70 + 8 (bonus cap)
    assert out["fragility"]["score"] == 22.0                   # 30 - 8


def test_phase59_polarity_never_flipped():
    """Even when contradicting, the recommendation.market must remain
    the same — Phase 59 only modulates confidence/fragility."""
    p = _base_under_payload(conf=64.0, frag=20.0)
    p["recent_run_split"] = {
        "runs_scored_avg_last_5_home":   5.8, "runs_scored_avg_last_15_home": 4.3,
        "runs_allowed_avg_last_5_home":  5.4, "runs_allowed_avg_last_15_home": 4.2,
        "runs_scored_avg_last_5_away":   5.2, "runs_scored_avg_last_15_away": 4.5,
        "runs_allowed_avg_last_5_away":  5.7, "runs_allowed_avg_last_15_away": 4.0,
    }
    original_market = p["recommendation"]["market"]
    out = _simulate_orchestrator_phase59(p)
    assert out["recommendation"]["market"] == original_market   # untouched
