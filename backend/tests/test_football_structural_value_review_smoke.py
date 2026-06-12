"""Phase F64 — Football Structural Value Review smoke tests.

The 8 mandatory scenarios from the spec, verbatim:

    T1. Edge -18.8% + corner support >= 75   →  WATCHLIST_ODDS_NEEDED
    T2. Edge -20.5% + goals/under support 75 →  WATCHLIST_ODDS_NEEDED
    T3. Edge -26.0% (HARD_DISCARD)           →  NO_STRUCTURAL_VALUE / CONFIRM_DISCARD
    T4. SOFT_DISCARD in [-25, 0) runs the full structural pipeline
        (i.e. all 4 sub-engines are at least invoked / available).
    T5. Structural support in [60, 75)       →  MOVE_TO_WATCHLIST
        + reason ``SCORES24_REVIEW_REQUIRED_BEFORE_FINAL_DISCARD``.
    T6. Structural support < 60              →  CONFIRM_DISCARD / NO_STRUCTURAL_VALUE.
    T7. Edge >= 0 + structural support >= 75 →  VALUE_CANDIDATE.
    T8. Empty / None / fail-soft inputs      →  available=False, no raise.

Plus a 9th test that pins the new ``extract_corner_side_from_match``
helper contract (home_/away_ flat keys → side dict).

The tests are intentionally *synthetic* but use realistic numeric ranges so
the sub-engines (corners_profile_cross, team_profile_cross, under_support,
over_support) actually compute real profiles, not just degrade to
``available=False``.
"""
from __future__ import annotations

import pytest

from services.football_structural_value_review import (
    ENGINE_VERSION,
    RC_DISCARD_CONFIRMED,
    RC_SCORES24_REVIEW_REQUIRED,
    RC_WATCHLIST_ODDS_NEEDED,
    STATE_MOVE_TO_WATCHLIST,
    STATE_NO_STRUCTURAL_VALUE,
    STATE_VALUE_CANDIDATE,
    STATE_WATCHLIST_ODDS_NEEDED,
    compute_structural_value_review,
)


# ─────────────────────────────────────────────────────────────────────
# Fixture builders — small, deterministic match docs.
# ─────────────────────────────────────────────────────────────────────
def _match_strong_corners_over() -> dict:
    """Both teams generate AND concede many corners → STRONG_CORNERS_OVER.

    With L5 totals around 6.5/5.5 we cross both HIGH thresholds
    (>= 5.5 for, >= 5.0 against) on both sides, which the engine maps to
    PROFILE_STRONG_OVER (support ≥ 78/100).
    """
    return {
        "home_corners_for_l5":      6.5,
        "home_corners_for_l15":     6.2,
        "home_corners_against_l5":  5.5,
        "home_corners_against_l15": 5.3,
        "away_corners_for_l5":      6.8,
        "away_corners_for_l15":     6.4,
        "away_corners_against_l5":  5.8,
        "away_corners_against_l15": 5.5,
    }


def _match_strong_corners_under() -> dict:
    """Both teams produce AND concede few corners → STRONG_CORNERS_UNDER.

    L5 values are deliberately below the LOW thresholds (≤4.0 for, ≤4.0
    against) on both sides → ``STRONG_CORNERS_UNDER_CROSS`` (support 80).
    """
    return {
        "home_corners_for_l5":      3.5,
        "home_corners_for_l15":     3.6,
        "home_corners_against_l5":  3.3,
        "home_corners_against_l15": 3.4,
        "away_corners_for_l5":      3.7,
        "away_corners_for_l15":     3.5,
        "away_corners_against_l5":  3.6,
        "away_corners_against_l15": 3.5,
    }


def _match_moderate_corners() -> dict:
    """Asymmetric profile: one team high corners, opponent concedes high.

    Designed to land between the HIGH (78) and STRONG_OVER (78) profiles
    but with only ASYMMETRIC (72/100) support — i.e. in the moderate
    [60, 75) band.
    """
    return {
        "home_corners_for_l5":      6.0,
        "home_corners_for_l15":     5.7,
        "home_corners_against_l5":  3.5,   # home does NOT concede many
        "home_corners_against_l15": 3.6,
        "away_corners_for_l5":      3.5,   # away does NOT generate many
        "away_corners_for_l15":     3.6,
        "away_corners_against_l5":  5.5,   # but DOES concede many
        "away_corners_against_l15": 5.4,
    }


def _match_no_signal() -> dict:
    """Neutral corner volumes → MIXED_CORNERS + no under/over support."""
    return {
        "home_corners_for_l5":      4.5,
        "home_corners_for_l15":     4.6,
        "home_corners_against_l5":  4.5,
        "home_corners_against_l15": 4.6,
        "away_corners_for_l5":      4.6,
        "away_corners_for_l15":     4.7,
        "away_corners_against_l5":  4.5,
        "away_corners_against_l15": 4.6,
    }


# ─────────────────────────────────────────────────────────────────────
# T1 — Edge -18.8% + corner support strong → WATCHLIST_ODDS_NEEDED
# ─────────────────────────────────────────────────────────────────────
def test_t1_negative_edge_with_strong_corner_support_becomes_watchlist() -> None:
    out = compute_structural_value_review(
        _match_strong_corners_over(),
        edge_pct=-18.8,
        discard_strength="SOFT_DISCARD_REVIEW",
    )
    assert out["final_state"] == STATE_WATCHLIST_ODDS_NEEDED
    assert out["decision"]    == "WATCHLIST_ODDS_NEEDED"
    assert out["max_structural_support"] >= 75
    assert RC_WATCHLIST_ODDS_NEEDED in out["reason_codes"]
    # A rescued market must be attached (rescue_market is the top candidate).
    assert out["rescued_market"] is not None
    assert out["rescued_market"]["family"] == "CORNERS"
    # Narrative must mention the rescued market explicitly.
    assert "Total corners" in (out["narrative_es"] or "")
    # Edge round-tripped onto the audit block.
    assert out["edge_pct"] == -18.8
    assert out["engine_version"] == ENGINE_VERSION


# ─────────────────────────────────────────────────────────────────────
# T2 — Edge -20.5% + STRONG UNDER corner support → WATCHLIST_ODDS_NEEDED
# ─────────────────────────────────────────────────────────────────────
def test_t2_negative_edge_with_under_support_becomes_watchlist() -> None:
    out = compute_structural_value_review(
        _match_strong_corners_under(),
        edge_pct=-20.5,
        discard_strength="SOFT_DISCARD_REVIEW",
    )
    assert out["final_state"] == STATE_WATCHLIST_ODDS_NEEDED
    assert out["max_structural_support"] >= 75
    # The structural review must surface the UNDER family as a rescue.
    assert out["rescued_market"] is not None
    assert out["rescued_market"]["family"] == "CORNERS"
    assert "Total corners Under" == out["rescued_market"]["market"]


# ─────────────────────────────────────────────────────────────────────
# T3 — Edge -26.0% → HARD_DISCARD short-circuits to NO_STRUCTURAL_VALUE
# ─────────────────────────────────────────────────────────────────────
def test_t3_hard_discard_shortcircuits_even_with_strong_structural_support() -> None:
    """A terminal HARD_DISCARD MUST NOT be rescued — even when the
    structural engines would otherwise support a candidate market."""
    out = compute_structural_value_review(
        _match_strong_corners_over(),     # would normally yield support 78
        edge_pct=-26.0,
        discard_strength="HARD_DISCARD",
    )
    assert out["final_state"] == STATE_NO_STRUCTURAL_VALUE
    assert out["decision"]    == "CONFIRM_DISCARD"
    assert RC_DISCARD_CONFIRMED in out["reason_codes"]
    # The structural numbers are still exposed for audit, but they MUST
    # NOT change the verdict.
    assert out["max_structural_support"] >= 75
    assert out["rescued_market"] is None


# ─────────────────────────────────────────────────────────────────────
# T4 — SOFT_DISCARD inside [-25, 0) invokes the FULL structural pipeline
# ─────────────────────────────────────────────────────────────────────
def test_t4_soft_discard_invokes_all_sub_engines() -> None:
    """For a SOFT_DISCARD we expect every sub-engine slot to be present
    in the output dict (even if some are ``available=False`` due to
    missing inputs other than corners). This is the contract the
    market_guardrail relies on to build its audit trail."""
    out = compute_structural_value_review(
        _match_strong_corners_over(),
        edge_pct=-12.5,
        discard_strength="SOFT_DISCARD_REVIEW",
    )
    assert out["structural_analysis_completed"] is True
    # All four sub-engine outputs must be on the response.
    for k in ("goal_profile_cross", "corner_profile_cross",
              "under_support", "over_support"):
        assert k in out, f"missing sub-engine slot: {k}"
        assert isinstance(out[k], dict)
    # The corner engine MUST have produced an actual profile (not the
    # fail-soft `available=False` shape) — this is the canonical L5/L15
    # flat-keys path we just enabled.
    assert out["corner_profile_cross"]["available"] is True
    assert out["corner_profile_cross"]["profile"] is not None


# ─────────────────────────────────────────────────────────────────────
# T5 — Structural support in [60, 75) → MOVE_TO_WATCHLIST
# ─────────────────────────────────────────────────────────────────────
def test_t5_moderate_support_routes_to_move_to_watchlist() -> None:
    out = compute_structural_value_review(
        _match_moderate_corners(),
        edge_pct=-10.0,
        discard_strength="SOFT_DISCARD_REVIEW",
    )
    # 60 <= support < 75 → MOVE_TO_WATCHLIST. The asymmetric corner
    # candidate has support=72, the under/over scoring engines are off
    # because we have no recent_fixtures, so the max stays at 72.
    assert 60 <= out["max_structural_support"] < 75
    assert out["final_state"] == STATE_MOVE_TO_WATCHLIST
    assert out["decision"]    == "MOVE_TO_WATCHLIST"
    assert RC_SCORES24_REVIEW_REQUIRED in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# T6 — Structural support < 60 → CONFIRM_DISCARD / NO_STRUCTURAL_VALUE
# ─────────────────────────────────────────────────────────────────────
def test_t6_low_support_confirms_discard() -> None:
    out = compute_structural_value_review(
        _match_no_signal(),
        edge_pct=-10.0,
        discard_strength="SOFT_DISCARD_REVIEW",
    )
    assert out["max_structural_support"] < 60
    assert out["final_state"] == STATE_NO_STRUCTURAL_VALUE
    assert out["decision"]    == "CONFIRM_DISCARD"
    assert RC_DISCARD_CONFIRMED in out["reason_codes"]
    # No rescued market when support is low.
    assert out["rescued_market"] is None


# ─────────────────────────────────────────────────────────────────────
# T7 — Edge >= 0 + strong support → VALUE_CANDIDATE
# ─────────────────────────────────────────────────────────────────────
def test_t7_positive_edge_with_strong_support_is_value_candidate() -> None:
    out = compute_structural_value_review(
        _match_strong_corners_over(),
        edge_pct=4.5,
        discard_strength=None,  # not a discard path
    )
    assert out["final_state"] == STATE_VALUE_CANDIDATE
    assert out["decision"]    == "VALUE_FOUND"
    assert out["max_structural_support"] >= 75


# ─────────────────────────────────────────────────────────────────────
# T8 — Fail-soft: empty / None / malformed inputs
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [None, {}, "not a dict", 42, [], 0])
def test_t8_fail_soft_on_garbage_inputs(bad: object) -> None:
    # MUST NOT raise.
    out = compute_structural_value_review(bad, edge_pct=-5.0,  # type: ignore[arg-type]
                                          discard_strength="SOFT_DISCARD_REVIEW")
    assert isinstance(out, dict)
    assert out["available"] is False
    assert out["structural_analysis_completed"] is False
    assert out["final_state"] == STATE_NO_STRUCTURAL_VALUE
    assert out["decision"]    == "CONFIRM_DISCARD"
    # Sub-engines must still be present with available=False (contract).
    for k in ("goal_profile_cross", "corner_profile_cross",
              "under_support", "over_support"):
        assert out[k]["available"] is False
    assert out["engine_version"] == ENGINE_VERSION


# ─────────────────────────────────────────────────────────────────────
# Bonus — Pin the new flat-keys helper contract (T9, helper unit-test).
# ─────────────────────────────────────────────────────────────────────
def test_extract_corner_side_from_match_flat_keys_contract() -> None:
    from services.football_corner_profile_cross import (
        extract_corner_side_from_match,
    )
    match = {
        "home_corners_for_l5":      4.2,
        "home_corners_for_l15":     4.8,
        "home_corners_against_l5":  3.8,
        "home_corners_against_l15": 4.1,
        "away_corners_for_l5":      5.1,
        "away_corners_for_l15":     4.7,
        "away_corners_against_l5":  4.9,
        "away_corners_against_l15": 5.0,
    }
    home = extract_corner_side_from_match(match, "home")
    away = extract_corner_side_from_match(match, "away")
    assert home == {
        "corners_for_l5":      4.2,
        "corners_for_l15":     4.8,
        "corners_against_l5":  3.8,
        "corners_against_l15": 4.1,
    }
    assert away["corners_for_l5"] == 5.1
    assert away["corners_against_l15"] == 5.0
    # Bad prefix or non-dict match → empty dict, no raise.
    assert extract_corner_side_from_match(match, "weird") == {}
    assert extract_corner_side_from_match(None, "home") == {}
    # Missing keys come back as None (not KeyError).
    partial = extract_corner_side_from_match({"home_corners_for_l5": 5.0}, "home")
    assert partial["corners_for_l5"] == 5.0
    assert partial["corners_for_l15"] is None
    assert partial["corners_against_l5"] is None


# ─────────────────────────────────────────────────────────────────────
# Integration — the helper feeds the corner cross profile end-to-end.
# ─────────────────────────────────────────────────────────────────────
def test_flat_match_keys_drive_strong_corners_under_cross() -> None:
    """Pin that the same flat-key match doc used by T2 actually
    activates ``STRONG_CORNERS_UNDER_CROSS`` inside the structural
    review (not just MIXED_CORNERS by accident)."""
    out = compute_structural_value_review(
        _match_strong_corners_under(),
        edge_pct=-15.0,
        discard_strength="SOFT_DISCARD_REVIEW",
    )
    cross = out["corner_profile_cross"]
    assert cross["available"] is True
    assert cross["profile"] == "STRONG_CORNERS_UNDER_CROSS"
    assert cross["supports"] == "CORNERS_UNDER"
    # And the structural review uses this to route into WATCHLIST_ODDS.
    assert out["final_state"] == STATE_WATCHLIST_ODDS_NEEDED
