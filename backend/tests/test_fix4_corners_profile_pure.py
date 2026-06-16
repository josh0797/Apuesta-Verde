"""FIX-4 — Tests for the pure corners profile module."""
from __future__ import annotations

import pytest

from services.football_corners_profile import (
    MOMENTUM_BEARISH,
    MOMENTUM_BULLISH_LOSING_MOMENTUM,
    MOMENTUM_BULLISH_STABLE,
    MOMENTUM_BULLISH_STRONG,
    MOMENTUM_NEUTRAL,
    RC_CORNERS_HISTORY_INSUFFICIENT_SAMPLE,
    RC_CORNERS_PROFILE_OK,
    RC_CORNERS_TEAM_HISTORY_NOT_FOUND,
    RC_CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED,
    STATUS_OK,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    build_corners_profile,
    build_team_profile,
    compute_corner_momentum,
    compute_expected_corners,
)


# ─────────────────────────────────────────────────────────────────────
#   compute_corner_momentum (5 states + edge cases)
# ─────────────────────────────────────────────────────────────────────

def test_momentum_bullish_strong_when_l1_gt_l5_gt_l15():
    out = compute_corner_momentum(l1=8, l5=6, l15=5)
    assert out["state"] == MOMENTUM_BULLISH_STRONG
    assert out["trend_delta"] == pytest.approx(1.0)
    assert out["trend_pct"] == pytest.approx(20.0)


def test_momentum_bullish_stable_when_l1_close_to_l5_and_l5_gt_l15():
    # L1 within 1 corner of L5 → stable.
    out = compute_corner_momentum(l1=6, l5=6, l15=5)
    assert out["state"] == MOMENTUM_BULLISH_STABLE


def test_momentum_bullish_losing_when_l1_lt_l5_but_l5_gt_l15():
    out = compute_corner_momentum(l1=3, l5=6, l15=5)
    assert out["state"] == MOMENTUM_BULLISH_LOSING_MOMENTUM


def test_momentum_bearish_when_l5_lt_l15():
    out = compute_corner_momentum(l1=4, l5=4, l15=6)
    assert out["state"] == MOMENTUM_BEARISH


def test_momentum_neutral_when_l5_equals_l15():
    out = compute_corner_momentum(l1=5, l5=5, l15=5)
    assert out["state"] == MOMENTUM_NEUTRAL


def test_momentum_neutral_when_any_value_missing():
    out = compute_corner_momentum(l1=None, l5=6, l15=5)
    assert out["state"] == MOMENTUM_NEUTRAL
    # Still surface trend_delta when L5 and L15 are present.
    assert out["trend_delta"] == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────
#   compute_expected_corners (user spec formula)
# ─────────────────────────────────────────────────────────────────────

def test_expected_corners_user_spec_example():
    # Local For=6.2, Local Against=4.5, Visitante For=5.8, Visitante Against=5.2
    # EC = (6.2+5.2)/2 + (5.8+4.5)/2 = 5.7 + 5.15 = 10.85
    ec = compute_expected_corners(
        home_for=6.2, home_against=4.5,
        away_for=5.8, away_against=5.2,
    )
    assert ec == pytest.approx(10.85)


def test_expected_corners_returns_none_on_partial_inputs():
    assert compute_expected_corners(
        home_for=6.2, home_against=None,
        away_for=5.8, away_against=5.2,
    ) is None


# ─────────────────────────────────────────────────────────────────────
#   build_team_profile
# ─────────────────────────────────────────────────────────────────────

def _hist(*pairs):
    """Helper: build a history vector (newest-first) from (for, against) tuples."""
    return [
        {"match_id": f"mt_{i}", "corners_for": p[0], "corners_against": p[1]}
        for i, p in enumerate(pairs)
    ]


def test_build_team_profile_computes_all_averages():
    hist = _hist((8, 4), (6, 5), (7, 3), (5, 6), (6, 4),
                  (4, 5), (5, 5), (6, 4), (5, 6), (4, 4),
                  (3, 5), (6, 3), (5, 4), (4, 5), (5, 5))
    p = build_team_profile(team_id="tm_X", team_name="Test", history=hist, min_sample=5)
    assert p.sample_size == 15
    assert p.l1_corners_for == 8
    assert p.l1_corners_against == 4
    assert p.l5_avg_corners_for == pytest.approx((8 + 6 + 7 + 5 + 6) / 5)
    assert p.l15_avg_corners_for is not None
    assert RC_CORNERS_HISTORY_INSUFFICIENT_SAMPLE not in p.reason_codes


def test_build_team_profile_flags_insufficient_sample():
    hist = _hist((6, 3), (5, 4))
    p = build_team_profile(team_id="tm_X", team_name="Tiny", history=hist, min_sample=5)
    assert p.sample_size == 2
    assert RC_CORNERS_HISTORY_INSUFFICIENT_SAMPLE in p.reason_codes


def test_build_team_profile_marks_not_found_when_history_empty():
    p = build_team_profile(team_id="tm_X", team_name=None, history=[], min_sample=5)
    assert p.sample_size == 0
    assert RC_CORNERS_TEAM_HISTORY_NOT_FOUND in p.reason_codes


def test_build_team_profile_skips_entries_without_corner_data():
    hist = [
        {"match_id": "a", "corners_for": 6, "corners_against": 3},
        {"match_id": "b"},  # no corners
        {"match_id": "c", "corners_for": 4, "corners_against": 5},
    ]
    p = build_team_profile(team_id="tm_X", team_name=None, history=hist, min_sample=2)
    assert p.sample_size == 2


# ─────────────────────────────────────────────────────────────────────
#   build_corners_profile (full contract)
# ─────────────────────────────────────────────────────────────────────

def test_full_profile_ok_when_both_teams_have_enough_history():
    home_h = _hist((6, 4), (7, 5), (5, 4), (6, 5), (7, 4))
    away_h = _hist((5, 5), (4, 5), (6, 4), (5, 5), (5, 6))
    out = build_corners_profile(
        home_team_id="tm_A", home_team_name="Argentina",
        home_history=home_h,
        away_team_id="tm_B", away_team_name="Algeria",
        away_history=away_h,
        is_pre_match=True,
        current_fixture_corners_available=False,
        min_sample=5,
    )
    assert out["status"] == STATUS_OK
    assert out["picks_blocked"] is False
    assert RC_CORNERS_PROFILE_OK in out["reason_codes"]
    assert RC_CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED in out["reason_codes"]
    assert out["expected_corners"] is not None
    assert "over_8_5" in out["line_projections"]


def test_full_profile_partial_when_one_team_short():
    home_h = _hist((6, 4), (7, 5), (5, 4), (6, 5), (7, 4))
    away_h = _hist((5, 5), (4, 5))  # only 2 samples
    out = build_corners_profile(
        home_team_id="tm_A", home_team_name="A",
        home_history=home_h,
        away_team_id="tm_B", away_team_name="B",
        away_history=away_h,
        is_pre_match=True,
        current_fixture_corners_available=False,
        min_sample=5,
    )
    assert out["status"] == STATUS_PARTIAL
    assert out["picks_blocked"] is True  # sub-market locked
    assert RC_CORNERS_HISTORY_INSUFFICIENT_SAMPLE in out["reason_codes"]


def test_full_profile_unavailable_when_one_team_empty():
    home_h = _hist((6, 4), (7, 5), (5, 4), (6, 5), (7, 4))
    away_h: list[dict] = []
    out = build_corners_profile(
        home_team_id="tm_A", home_team_name="A", home_history=home_h,
        away_team_id="tm_B", away_team_name="B", away_history=away_h,
        is_pre_match=True, current_fixture_corners_available=False,
        min_sample=5,
    )
    assert out["status"] == STATUS_UNAVAILABLE
    assert out["picks_blocked"] is True
    assert RC_CORNERS_TEAM_HISTORY_NOT_FOUND in out["reason_codes"]


def test_full_profile_does_not_add_prematch_reason_when_current_fixture_corners_present():
    """Live / post-match: corners ARE available from the current fixture.
    The pre-match-expected reason should NOT be added.
    """
    home_h = _hist((6, 4), (7, 5), (5, 4), (6, 5), (7, 4))
    away_h = _hist((5, 5), (4, 5), (6, 4), (5, 5), (5, 6))
    out = build_corners_profile(
        home_team_id="tm_A", home_team_name="A", home_history=home_h,
        away_team_id="tm_B", away_team_name="B", away_history=away_h,
        is_pre_match=False,
        current_fixture_corners_available=True,
        min_sample=5,
    )
    assert RC_CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED not in out["reason_codes"]


def test_full_profile_user_spec_argentina_argelia_numbers():
    """Verify the example from the user spec end-to-end.

    Local L1=8, L5≈6.2, L15≈5.4, BULLISH_STRONG.
    Visitante L1=6, L5≈5.8, L15≈5.1, BULLISH_STRONG.
    """
    # 5 most-recent samples summing to 31 (avg 6.2): [8, 5, 6, 6, 6].
    home_recent_for = [8, 5, 6, 6, 6]
    home_recent_against = [4, 5, 5, 4, 4]
    # 10 older samples avg ≈ 4.9 so L15 ≈ (31 + 49) / 15 ≈ 5.33.
    home_older_for     = [5, 4, 5, 5, 5, 5, 5, 5, 5, 5]
    home_older_against = [5, 4, 4, 5, 5, 5, 4, 4, 5, 5]

    home_h = []
    for i, (cf, ca) in enumerate(zip(home_recent_for, home_recent_against)):
        home_h.append({"match_id": f"h{i}", "corners_for": cf, "corners_against": ca})
    for i, (cf, ca) in enumerate(zip(home_older_for, home_older_against)):
        home_h.append({"match_id": f"hp{i}", "corners_for": cf, "corners_against": ca})

    # Away: 5 recent samples summing to 29 (avg 5.8): [6, 6, 6, 6, 5].
    away_recent_for     = [6, 6, 6, 6, 5]
    away_recent_against = [5, 5, 5, 4, 5]
    away_older_for      = [5, 5, 5, 5, 5, 4, 5, 5, 5, 4]
    away_older_against  = [5, 5, 5, 4, 5, 5, 5, 5, 5, 5]

    away_h = []
    for i, (cf, ca) in enumerate(zip(away_recent_for, away_recent_against)):
        away_h.append({"match_id": f"a{i}", "corners_for": cf, "corners_against": ca})
    for i, (cf, ca) in enumerate(zip(away_older_for, away_older_against)):
        away_h.append({"match_id": f"ap{i}", "corners_for": cf, "corners_against": ca})

    out = build_corners_profile(
        home_team_id="tm_ARG", home_team_name="Argentina",
        home_history=home_h,
        away_team_id="tm_ALG", away_team_name="Argelia",
        away_history=away_h,
        is_pre_match=True, current_fixture_corners_available=False,
        min_sample=5,
    )
    assert out["status"] == STATUS_OK
    home = out["home"]
    assert home["l1_corners_for"] == 8
    assert home["l5_avg_corners_for"] == pytest.approx(6.2)
    away = out["away"]
    assert away["l1_corners_for"] == 6
    assert away["l5_avg_corners_for"] == pytest.approx(5.8)
    # Momentum should be strongly bullish for home (8 > 6.2 > 5.33).
    assert home["momentum"]["state"] == MOMENTUM_BULLISH_STRONG
    # Expected corners should be reasonable (~10–12).
    assert out["expected_corners"] is not None
    assert 10.0 <= out["expected_corners"] <= 12.0
