"""Sprint-D6 · Prove the walk-forward calibrator is NOT a no-op.

The legacy ``test_sprint_d4_walk_forward.py`` asserted
``n_calib_picks_seen >= 0`` — trivially true with any input. This file
fixes the gap by:

1. Building a deterministic synthetic dataset where the *real* Over 1.5
   hit-rate is **exactly** controlled (``over15_rate``).
2. Injecting a constant ``predictor_override`` so the *base* probability
   the engine sees is a knob, not an emergent Dixon-Coles output.
3. Activating the new ``shrinkage_K`` parameter (Sprint-D6 opt-in
   layer) and asserting the calibrator effectively moves the
   prediction toward the empirical rate as ``n`` grows.

Every test fails loudly if the calibrator stops doing its job
(returns base, or fails to clamp, or audit fields disappear).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pytest

from services.football_backtest_engine import (
    PROB_MAX, PROB_MIN, _shrinkage_weight, run_backtest,
)


# ════════════════════════════════════════════════════════════════════════
# Synthetic generator
# ════════════════════════════════════════════════════════════════════════
def _synthetic_matches_with_known_rate(
    n: int, *, over15_rate: float, seed: int = 0,
) -> list[dict]:
    """Build ``n`` synthetic football matches whose **real** Over 1.5
    hit-rate is *exactly* ``over15_rate`` (rounded to the nearest int).

    The dates are strictly increasing so the engine's walk-forward
    history order is well-defined. The teams cycle through a small
    pool so each side accumulates enough prior matches to clear
    ``min_history_per_team``. Every match carries enough fields for
    ``build_point_in_time_features`` to succeed.

    Each "hit" match (``home_goals + away_goals >= 2``) is scored 1-1;
    each "miss" is 0-0. That gives a clean, deterministic outcome
    column independent of any feature signal.
    """
    if not 0.0 <= over15_rate <= 1.0:
        raise ValueError("over15_rate must be in [0, 1]")
    teams = [f"T{i}" for i in range(8)]    # 8-team round-robin pool
    n_hits = int(round(over15_rate * n))
    # ── Uniformly interleave hits and misses (Bresenham-style) ──────────
    # Random shuffles would leak temporal clustering into the
    # walk-forward calibrator. Instead, we walk an "error accumulator"
    # so the empirical rate measured over ANY contiguous late window
    # is statistically close to ``over15_rate`` — the test relies on
    # late-window rate being representative.
    pattern: list[bool] = []
    acc = 0.0
    for _ in range(n):
        acc += over15_rate
        if acc >= 1.0:
            pattern.append(True)
            acc -= 1.0
        else:
            pattern.append(False)
    # Ensure we hit ``n_hits`` exactly (rounding can leave us ±1 off
    # for fractional rates — the test promises an *exact* rate).
    while sum(pattern) > n_hits:
        # Drop the latest True.
        for i in range(len(pattern) - 1, -1, -1):
            if pattern[i]:
                pattern[i] = False
                break
    while sum(pattern) < n_hits:
        for i in range(len(pattern)):
            if not pattern[i]:
                pattern[i] = True
                break
    # Deterministic rotation for seeded variants.
    if seed:
        s = seed % max(n, 1)
        pattern = pattern[s:] + pattern[:s]
    start = date(2024, 1, 1)

    out: list[dict] = []
    for i in range(n):
        h_idx, a_idx = i % len(teams), (i + 1) % len(teams)
        is_hit = pattern[i]
        hg = 1 if is_hit else 0
        ag = 1 if is_hit else 0
        # Final-result code expected by football_historical_ingestor.
        # All synthetic hits are 1-1 draws, all misses are 0-0 draws,
        # so every fixture has FTR="D" (this keeps the ELO walk
        # deterministic and lets ``min_history_per_team=0`` work).
        ftr = "D"
        out.append({
            "date":          start + timedelta(days=i),
            "home_team":     teams[h_idx],
            "away_team":     teams[a_idx],
            "home_goals":    hg,
            "away_goals":    ag,
            # football-data.co.uk schema fields (used by the engine
            # internally; safe to add even in no_market mode).
            "fthg":          hg,
            "ftag":          ag,
            "ftr":           ftr,
            # Goals-against / corners columns expected by the
            # historical ingestor (None ⇒ skipped in averages).
            "home_corners":  None,
            "away_corners":  None,
            # Optional odds columns left as None so ``no_market=True``
            # path is taken throughout the run.
            "odd_home":      None,
            "odd_draw":      None,
            "odd_away":      None,
            "league_id":     "SYN",
            "season":        "2024",
        })
    return out


def _constant_predictor(prob_pct: float):
    """Return a ``predictor_override`` that always emits
    ``over15_probability=prob_pct``. Pure / deterministic."""
    def _p(features: dict) -> dict:  # noqa: ARG001
        return {
            "over15_probability": float(prob_pct),
            "label":              "OVER15_BACKTEST",
            "reason_codes":       ["SYNTHETIC_CONSTANT"],
        }
    return _p


def _run(*, matches, base_pp: float, shrinkage_K: Optional[int],
          use_calibration: bool = True):
    """Helper around ``run_backtest`` with Sprint-D6 defaults."""
    return run_backtest(
        matches,
        market="OVER_1_5",
        no_market=True,
        use_calibration=use_calibration,
        walk_forward=True,
        min_pred_prob_pp=0.0,            # fire on every prediction
        min_history_per_team=0,           # no history gate
        shrinkage_K=shrinkage_K,
        predictor_override=_constant_predictor(base_pp),
        recal_every=10,
    )


# ════════════════════════════════════════════════════════════════════════
# Unit-level guard: shrinkage weight is monotonic in n
# ════════════════════════════════════════════════════════════════════════
class TestShrinkageWeightUnit:
    def test_zero_n_gives_zero(self):
        assert _shrinkage_weight(0, 50) == 0.0

    def test_grows_monotonically(self):
        seq = [_shrinkage_weight(n, 50) for n in (0, 10, 25, 50, 100, 200, 1000)]
        assert all(b > a for a, b in zip(seq, seq[1:]))

    def test_approaches_one_with_large_n(self):
        assert _shrinkage_weight(10_000, 50) > 0.99

    def test_zero_K_disables(self):
        assert _shrinkage_weight(100, 0) == 0.0


# ════════════════════════════════════════════════════════════════════════
# Test 1 — calibrator shifts predictions when there's enough data
# ════════════════════════════════════════════════════════════════════════
def test_calibration_shifts_predictions_with_enough_data():
    matches = _synthetic_matches_with_known_rate(
        n=150, over15_rate=0.80, seed=0,
    )
    res_calib = _run(matches=matches, base_pp=65.0, shrinkage_K=50)
    res_base  = _run(matches=matches, base_pp=65.0, shrinkage_K=None,
                      use_calibration=False)

    # Pick the audit blocks from the LATE half of the run, where the
    # walk-forward history is densest.
    late_calib = [r for r in res_calib["predictions"]
                  if r.get("_calibration_audit", {}).get("n_calib_picks_seen", 0) > 100]
    assert len(late_calib) >= 5, "no late-stage predictions with n>100"

    # At least one late prediction must move > 2pp away from base.
    moves = [abs(r["_calibration_audit"]["calibrated_prob"] - 0.65)
             for r in late_calib]
    assert max(moves) > 0.02, (
        f"Calibrator looks like a no-op: max |Δ|={max(moves):.4f} "
        f"vs base 0.65 (calibrated_prob samples={moves[:5]})"
    )

    # Sanity: the uncalibrated run keeps base = 65 throughout (the
    # ``predictions`` rows expose the firing probability under the
    # ``predicted_prob`` key — already divided by 100).
    base_late = [r for r in res_base["predictions"][50:]]
    base_pcts = {round(r["predicted_prob"] * 100.0, 1) for r in base_late}
    assert base_pcts == {65.0}, base_pcts


# ════════════════════════════════════════════════════════════════════════
# Test 2 — calibration direction matches the empirical rate
# ════════════════════════════════════════════════════════════════════════
def test_calibration_direction_is_correct_when_rate_above_base():
    matches = _synthetic_matches_with_known_rate(
        n=150, over15_rate=0.80, seed=0,
    )
    res = _run(matches=matches, base_pp=65.0, shrinkage_K=50)
    late = [r for r in res["predictions"]
            if r.get("_calibration_audit", {}).get("n_calib_picks_seen", 0) > 100]
    assert late
    # rate (0.80) > base (0.65) → calibrated must be ABOVE base.
    above = [r["_calibration_audit"]["calibrated_prob"] > 0.65 for r in late]
    assert all(above), (
        f"Expected calibrated > 0.65 in every late prediction (rate=0.80 > "
        f"base=0.65); offending samples: "
        f"{[r['_calibration_audit']['calibrated_prob'] for r in late if not (r['_calibration_audit']['calibrated_prob'] > 0.65)][:5]}"
    )


def test_calibration_direction_is_correct_when_rate_below_base():
    matches = _synthetic_matches_with_known_rate(
        n=150, over15_rate=0.30, seed=0,
    )
    res = _run(matches=matches, base_pp=65.0, shrinkage_K=50)
    late = [r for r in res["predictions"]
            if r.get("_calibration_audit", {}).get("n_calib_picks_seen", 0) > 100]
    assert late
    # rate (0.30) < base (0.65) → calibrated must be BELOW base.
    below = [r["_calibration_audit"]["calibrated_prob"] < 0.65 for r in late]
    assert all(below), (
        f"Expected calibrated < 0.65 in every late prediction (rate=0.30 < "
        f"base=0.65); offending samples: "
        f"{[r['_calibration_audit']['calibrated_prob'] for r in late if not (r['_calibration_audit']['calibrated_prob'] < 0.65)][:5]}"
    )


# ════════════════════════════════════════════════════════════════════════
# Test 3 — calib_weight grows monotonically with sample size
# ════════════════════════════════════════════════════════════════════════
def test_calibration_weight_grows_with_sample():
    matches = _synthetic_matches_with_known_rate(
        n=150, over15_rate=0.80, seed=0,
    )
    res = _run(matches=matches, base_pp=65.0, shrinkage_K=50)
    weights = [
        (r["_calibration_audit"]["n_calib_picks_seen"],
         r["_calibration_audit"]["calib_weight"])
        for r in res["predictions"]
        if r.get("_calibration_audit", {}).get("calib_weight") is not None
    ]
    assert len(weights) >= 50

    # Sort by n_picks (already increasing because picks fire in date
    # order) and verify monotonic-non-decreasing on weight.
    weights.sort(key=lambda kv: kv[0])
    deltas = [b - a for (_, a), (_, b) in zip(weights, weights[1:])]
    assert all(d >= -1e-9 for d in deltas), (
        f"calib_weight is not monotonic: deltas[:10]={deltas[:10]}"
    )

    # Spot-check formula: w = n / (n + K).
    for n_seen, w in weights[::25]:
        expected = n_seen / (n_seen + 50.0)
        assert abs(w - expected) < 1e-6, (n_seen, w, expected)


# ════════════════════════════════════════════════════════════════════════
# Test 4 — early predictions stay on the prior (calibrated ≈ base)
# ════════════════════════════════════════════════════════════════════════
def test_early_predictions_use_pure_prior():
    matches = _synthetic_matches_with_known_rate(
        n=150, over15_rate=0.80, seed=0,
    )
    res = _run(matches=matches, base_pp=65.0, shrinkage_K=50)
    # The VERY first prediction has no history at all → audit lacks
    # calib fields. The next few have small n: weight should be tiny
    # and the calibrated probability must remain close to base.
    small_n = [r for r in res["predictions"]
               if r.get("_calibration_audit", {}).get("calib_weight") is not None
               and r["_calibration_audit"]["n_calib_picks_seen"] <= 5]
    assert small_n, "expected some small-sample predictions"
    for r in small_n:
        w = r["_calibration_audit"]["calib_weight"]
        cp = r["_calibration_audit"]["calibrated_prob"]
        assert w <= 0.11, w           # 5/(5+50) ≈ 0.091
        # With w<=0.1 the move from 0.65 base cannot exceed 0.1*1.0 = 0.1.
        assert abs(cp - 0.65) <= 0.10, (w, cp)


# ════════════════════════════════════════════════════════════════════════
# Test 5 — clamp respected even with extreme empirical rates
# ════════════════════════════════════════════════════════════════════════
def test_clamp_respected_with_extreme_data():
    # 100% Over 1.5 — observed_rate=1.0, isotonic bucket also 1.0.
    matches = _synthetic_matches_with_known_rate(
        n=150, over15_rate=1.0, seed=0,
    )
    res = _run(matches=matches, base_pp=65.0, shrinkage_K=50)
    late = [r for r in res["predictions"]
            if r.get("_calibration_audit", {}).get("n_calib_picks_seen", 0) > 100]
    assert late
    cps = [r["_calibration_audit"]["calibrated_prob"] for r in late]
    assert all(cp <= PROB_MAX for cp in cps), cps
    assert max(cps) <= PROB_MAX

    # And the dual: 0% Over 1.5 must hit PROB_MIN as the lower bound.
    matches_zero = _synthetic_matches_with_known_rate(
        n=150, over15_rate=0.0, seed=0,
    )
    res_zero = _run(matches=matches_zero, base_pp=65.0, shrinkage_K=50)
    late_zero = [r for r in res_zero["predictions"]
                  if r.get("_calibration_audit", {}).get("n_calib_picks_seen", 0) > 100]
    cps_zero = [r["_calibration_audit"]["calibrated_prob"] for r in late_zero]
    assert all(cp >= PROB_MIN for cp in cps_zero)


# ════════════════════════════════════════════════════════════════════════
# Test 6 — opt-in semantics: shrinkage_K=None preserves legacy behaviour
# ════════════════════════════════════════════════════════════════════════
def test_shrinkage_K_none_preserves_legacy():
    """When ``shrinkage_K`` is ``None`` the engine must NOT add the
    extra layer — the ``calib_weight`` audit field must be ``None``
    even if ``use_calibration=True``."""
    matches = _synthetic_matches_with_known_rate(
        n=120, over15_rate=0.80, seed=0,
    )
    res = _run(matches=matches, base_pp=65.0, shrinkage_K=None)
    auditable = [r for r in res["predictions"]
                  if r.get("_calibration_audit", {}).get("n_calib_picks_seen", 0) > 50]
    assert auditable
    for r in auditable:
        assert r["_calibration_audit"]["shrinkage_K"] is None
        assert r["_calibration_audit"]["calib_weight"] is None


# ════════════════════════════════════════════════════════════════════════
# Test 7 — audit surface is complete (regression guard)
# ════════════════════════════════════════════════════════════════════════
def test_calibration_audit_exposes_all_fields():
    matches = _synthetic_matches_with_known_rate(
        n=150, over15_rate=0.80, seed=0,
    )
    res = _run(matches=matches, base_pp=65.0, shrinkage_K=50)
    sample = next(r for r in res["predictions"]
                  if r.get("_calibration_audit", {}).get("calib_weight") is not None)
    audit = sample["_calibration_audit"]
    for field in ("n_calib_picks_seen", "calib_weight", "base_prob",
                   "calibrated_prob", "observed_rate", "shrinkage_K",
                   "iso_calibrated", "clamped"):
        assert field in audit, f"missing audit field: {field}"
    # Sanity: structural ranges.
    assert 0.0 < audit["calibrated_prob"] < 1.0
    assert 0.0 < audit["base_prob"]      < 1.0
    assert 0.0 <= audit["calib_weight"]  <= 1.0
    assert 0.0 <= audit["observed_rate"] <= 1.0
