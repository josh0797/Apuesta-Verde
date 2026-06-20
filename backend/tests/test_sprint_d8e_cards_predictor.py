"""Tests for Sprint-D8/E PASO 2 — cards predictor (model-only).

Covers:
  * compute_cards_potential: in-range probabilities, monotonicity in λ,
    line sensitivity, derby bump, fail-soft, ablation switch.
  * build_cards_features_pit: PIT non-leakage (CRITICAL), low-sample
    referee fallback, partial features.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from services.football_cards_potential import (
    LEAGUE_DEFAULT_LAMBDA,
    RC_ALL_FEATURES_MISSING,
    RC_DERBY_BUMP_APPLIED,
    RC_LEAGUE_DEFAULT_USED,
    RC_LOW_REFEREE_SAMPLE,
    RC_REFEREE_AVG_MISSING,
    RC_REFEREE_AVG_USED,
    RC_TEAM_CARDS_USED,
    compute_cards_potential,
)
from services.football_cards_ingestor import (
    LOW_REFEREE_SAMPLE_RC,
    NO_REFEREE_RC,
    REFEREE_FALLBACK_RC,
    REFEREE_OK_RC,
    build_cards_features_pit,
    referee_cards_avg_pit,
    team_cards_for_avg_pit,
)


# ─────────────────────────────────────────────────────────────────────
# Section 1 — compute_cards_potential: in-range & monotonicity
# ─────────────────────────────────────────────────────────────────────
def test_compute_cards_probabilities_are_in_unit_range_and_sum_to_one():
    out = compute_cards_potential(
        referee_cards_avg=4.5,
        home_cards_for_avg=2.0, away_cards_for_avg=2.0,
        home_fouls_avg=10, away_fouls_avg=10,
        line=4.5,
    )
    p_over  = out["over_cards_probability"]
    p_under = out["under_cards_probability"]
    assert 0.0 <= p_over  <= 1.0
    assert 0.0 <= p_under <= 1.0
    assert abs(p_over + p_under - 1.0) < 1e-3


def test_compute_cards_monotonic_in_referee_avg():
    """Higher referee_cards_avg → higher P(over)."""
    low = compute_cards_potential(
        referee_cards_avg=3.0,
        home_cards_for_avg=2.0, away_cards_for_avg=2.0, line=4.5,
    )
    high = compute_cards_potential(
        referee_cards_avg=6.0,
        home_cards_for_avg=2.0, away_cards_for_avg=2.0, line=4.5,
    )
    assert high["over_cards_probability"] > low["over_cards_probability"]
    assert high["expected_total_cards"] > low["expected_total_cards"]


def test_compute_cards_monotonic_in_team_cards_for():
    low = compute_cards_potential(
        referee_cards_avg=4.5,
        home_cards_for_avg=1.5, away_cards_for_avg=1.5, line=4.5,
    )
    high = compute_cards_potential(
        referee_cards_avg=4.5,
        home_cards_for_avg=3.5, away_cards_for_avg=3.5, line=4.5,
    )
    assert high["over_cards_probability"] > low["over_cards_probability"]


def test_compute_cards_higher_line_reduces_over_prob():
    """At fixed λ, P(over X.5) decreases as X grows."""
    args = dict(
        referee_cards_avg=4.5,
        home_cards_for_avg=2.0, away_cards_for_avg=2.0,
        home_fouls_avg=11, away_fouls_avg=11,
    )
    p_3_5 = compute_cards_potential(line=3.5, **args)["over_cards_probability"]
    p_4_5 = compute_cards_potential(line=4.5, **args)["over_cards_probability"]
    p_5_5 = compute_cards_potential(line=5.5, **args)["over_cards_probability"]
    assert p_3_5 > p_4_5 > p_5_5


def test_compute_cards_derby_bump_increases_lambda_when_applied():
    args = dict(
        referee_cards_avg=4.0,
        home_cards_for_avg=2.0, away_cards_for_avg=2.0,
        home_fouls_avg=10, away_fouls_avg=10,
        line=4.5,
    )
    base = compute_cards_potential(is_derby=False, **args)
    bump = compute_cards_potential(is_derby=True,  **args)
    assert bump["expected_total_cards"] > base["expected_total_cards"]
    assert RC_DERBY_BUMP_APPLIED in bump["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Section 2 — Ablation switch (CRITICAL: entregable estrella)
# ─────────────────────────────────────────────────────────────────────
def test_ablation_disabling_referee_removes_referee_signal_completely():
    """When use_referee_factor=False, the referee_cards_avg input must
    NOT influence the prediction. Two runs with very different referee
    values but ``use_referee_factor=False`` should yield identical λ.
    """
    args_team = dict(
        home_cards_for_avg=2.5, away_cards_for_avg=2.5,
        home_fouls_avg=12, away_fouls_avg=12,
        line=4.5, use_referee_factor=False,
    )
    out_low_ref  = compute_cards_potential(referee_cards_avg=2.0, **args_team)
    out_high_ref = compute_cards_potential(referee_cards_avg=8.0, **args_team)
    # With referee disabled, both runs must agree exactly.
    assert out_low_ref["expected_total_cards"] == out_high_ref["expected_total_cards"]
    # And both must emit REFEREE_AVG_MISSING (ablation reason).
    assert RC_REFEREE_AVG_MISSING in out_low_ref["reason_codes"]
    assert RC_REFEREE_AVG_MISSING in out_high_ref["reason_codes"]


def test_ablation_with_referee_enabled_uses_signal():
    """Sanity: when ablation is OFF (referee enabled), the runs differ."""
    args_team = dict(
        home_cards_for_avg=2.5, away_cards_for_avg=2.5,
        home_fouls_avg=12, away_fouls_avg=12,
        line=4.5, use_referee_factor=True,
    )
    out_low_ref  = compute_cards_potential(referee_cards_avg=2.0, **args_team)
    out_high_ref = compute_cards_potential(referee_cards_avg=8.0, **args_team)
    assert out_high_ref["expected_total_cards"] != out_low_ref["expected_total_cards"]
    assert RC_REFEREE_AVG_USED in out_low_ref["reason_codes"]
    assert RC_REFEREE_AVG_USED in out_high_ref["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Section 3 — fail-soft
# ─────────────────────────────────────────────────────────────────────
def test_compute_cards_all_features_missing_falls_back_to_league_default():
    out = compute_cards_potential(line=4.5)
    assert out["expected_total_cards"] == LEAGUE_DEFAULT_LAMBDA
    assert RC_LEAGUE_DEFAULT_USED in out["reason_codes"]
    assert RC_ALL_FEATURES_MISSING in out["reason_codes"]


def test_compute_cards_handles_negative_or_garbage_referee_avg():
    out = compute_cards_potential(referee_cards_avg=-3.0, line=4.5)
    assert RC_REFEREE_AVG_MISSING in out["reason_codes"]

    out2 = compute_cards_potential(referee_cards_avg="not-a-number", line=4.5)
    assert RC_REFEREE_AVG_MISSING in out2["reason_codes"]


def test_compute_cards_partial_team_data_still_returns_valid_dict():
    out = compute_cards_potential(
        referee_cards_avg=4.0,
        home_cards_for_avg=2.0, away_cards_for_avg=None,   # partial
        line=4.5,
    )
    assert 0.0 <= out["over_cards_probability"] <= 1.0
    assert out["expected_total_cards"] > 0


def test_compute_cards_low_referee_sample_emits_reason_code():
    out = compute_cards_potential(
        referee_cards_avg=4.5,
        referee_n_prior=2,
        home_cards_for_avg=2.0, away_cards_for_avg=2.0,
        line=4.5, min_referee_sample=5,
    )
    assert RC_LOW_REFEREE_SAMPLE in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Section 4 — PIT discipline (CRITICAL: no leakage of target match)
# ─────────────────────────────────────────────────────────────────────
def _make_match(date, referee=None, home="A", away="B",
                 home_cards=2, away_cards=2,
                 home_fouls=10, away_fouls=10, league="Premier League"):
    return {
        "date": date,
        "referee": referee,
        "home_team": home,
        "away_team": away,
        "home_cards": home_cards,
        "away_cards": away_cards,
        "home_fouls": home_fouls,
        "away_fouls": away_fouls,
        "league": league,
    }


def test_referee_avg_is_strictly_point_in_time_never_includes_target():
    """The CORE PIT guarantee: the target match must NEVER enter its
    own referee average, even when it appears in the history list.
    """
    target_dt = datetime(2024, 10, 1, tzinfo=timezone.utc)

    history = [
        # 5 prior matches with this referee (each total = 4)
        _make_match(datetime(2024, 8, 1, tzinfo=timezone.utc),
                    referee="Ref X", home_cards=2, away_cards=2),
        _make_match(datetime(2024, 8, 8, tzinfo=timezone.utc),
                    referee="Ref X", home_cards=2, away_cards=2),
        _make_match(datetime(2024, 8, 15, tzinfo=timezone.utc),
                    referee="Ref X", home_cards=2, away_cards=2),
        _make_match(datetime(2024, 9, 1, tzinfo=timezone.utc),
                    referee="Ref X", home_cards=2, away_cards=2),
        _make_match(datetime(2024, 9, 15, tzinfo=timezone.utc),
                    referee="Ref X", home_cards=2, away_cards=2),
        # The TARGET match itself — referee SAME, but cards=99 → would
        # massively distort avg if leaked in.
        _make_match(target_dt, referee="Ref X",
                    home_cards=99, away_cards=99),
        # A POSTERIOR match (date > target). Must NOT contribute.
        _make_match(datetime(2024, 11, 1, tzinfo=timezone.utc),
                    referee="Ref X", home_cards=100, away_cards=100),
    ]

    info = referee_cards_avg_pit(
        target_date=target_dt, referee="Ref X", history=history,
        min_sample=5,
    )
    # 5 prior totals, all = 4. Avg must be exactly 4.0.
    assert info["n_prior"] == 5
    assert info["value"] == 4.0
    assert REFEREE_OK_RC in info["reason_codes"]
    # The high-cards future rows did NOT contaminate.
    assert info["used_fallback"] is False


def test_referee_low_sample_falls_back_to_league_avg_pit():
    target_dt = datetime(2024, 10, 1, tzinfo=timezone.utc)

    history = [
        # Only 2 prior matches with this referee → below min_sample=5
        _make_match(datetime(2024, 8, 1, tzinfo=timezone.utc),
                    referee="Rookie", home_cards=2, away_cards=2),
        _make_match(datetime(2024, 8, 8, tzinfo=timezone.utc),
                    referee="Rookie", home_cards=2, away_cards=2),
        # Many other-referee prior matches in same league → league avg
        _make_match(datetime(2024, 8, 2, tzinfo=timezone.utc),
                    referee="Other", home_cards=3, away_cards=3),
        _make_match(datetime(2024, 8, 3, tzinfo=timezone.utc),
                    referee="Other", home_cards=3, away_cards=3),
        _make_match(datetime(2024, 8, 4, tzinfo=timezone.utc),
                    referee="Other", home_cards=3, away_cards=3),
    ]

    info = referee_cards_avg_pit(
        target_date=target_dt, referee="Rookie", history=history,
        league="Premier League", min_sample=5,
    )
    assert info["used_fallback"] is True
    assert LOW_REFEREE_SAMPLE_RC in info["reason_codes"]
    assert REFEREE_FALLBACK_RC in info["reason_codes"]
    # n_prior tracks what the referee actually had (2), not the fallback sample.
    assert info["n_prior"] == 2
    # Value should equal the league avg of all prior matches:
    # (4+4 + 6+6+6) / 5 = 5.2
    assert info["value"] == 5.2


def test_referee_missing_from_fixture_uses_league_avg():
    target_dt = datetime(2024, 10, 1, tzinfo=timezone.utc)
    history = [
        _make_match(datetime(2024, 8, 1, tzinfo=timezone.utc),
                    referee="X", home_cards=3, away_cards=3),
    ]
    info = referee_cards_avg_pit(
        target_date=target_dt, referee=None, history=history,
    )
    assert NO_REFEREE_RC in info["reason_codes"]
    assert info["used_fallback"] is True
    assert info["value"] == 6.0


def test_team_cards_for_avg_is_pit_correct():
    target_dt = datetime(2024, 10, 1, tzinfo=timezone.utc)
    history = [
        # Prior — counts
        _make_match(datetime(2024, 8, 1, tzinfo=timezone.utc),
                    home="Liverpool", away="X", home_cards=2, away_cards=0),
        _make_match(datetime(2024, 9, 1, tzinfo=timezone.utc),
                    home="X", away="Liverpool", home_cards=0, away_cards=4),
        # Future — must NOT count
        _make_match(datetime(2024, 11, 1, tzinfo=timezone.utc),
                    home="Liverpool", away="Y", home_cards=99, away_cards=0),
    ]
    avg = team_cards_for_avg_pit(
        target_date=target_dt, team="Liverpool", history=history,
    )
    assert avg == 3.0   # (2 + 4) / 2


# ─────────────────────────────────────────────────────────────────────
# Section 5 — build_cards_features_pit integration
# ─────────────────────────────────────────────────────────────────────
def test_build_cards_features_pit_returns_kwargs_compatible_with_predictor():
    target_dt = datetime(2024, 10, 1, tzinfo=timezone.utc)
    target = _make_match(target_dt, referee="Ref X",
                          home="Liverpool", away="Arsenal")
    history = []
    for i in range(6):
        history.append(_make_match(
            target_dt - timedelta(days=10 + i), referee="Ref X",
            home="Liverpool" if i % 2 == 0 else "Other A",
            away="Arsenal"   if i % 2 == 1 else "Other B",
            home_cards=2, away_cards=2,
        ))

    out = build_cards_features_pit(target, history, min_referee_sample=5)
    feats = out["features"]

    # Now pass straight into the predictor.
    pred = compute_cards_potential(**{k: v for k, v in feats.items()
                                      if k != "min_referee_sample"
                                      and k != "referee_n_prior"})
    assert 0.0 <= pred["over_cards_probability"] <= 1.0
    # The predictor should have used the referee.
    assert RC_REFEREE_AVG_USED in pred["reason_codes"]


def test_build_cards_features_pit_audit_includes_referee_metadata():
    target_dt = datetime(2024, 10, 1, tzinfo=timezone.utc)
    target = _make_match(target_dt, referee="Ref X",
                          home="A", away="B")
    history = [
        _make_match(target_dt - timedelta(days=k+1), referee="Ref X",
                    home="A", away="C", home_cards=2, away_cards=2)
        for k in range(6)
    ]
    out = build_cards_features_pit(target, history)
    audit = out["audit"]["source_audit"]
    assert audit["referee_name"] == "Ref X"
    assert audit["n_referee_prior"] == 6
    assert audit["referee_fallback"] is False
