"""End-to-end test for Sprint-D8/E PASO 2 — cards phase-1 evaluation.

Generates a synthetic dataset where the referee carries a real signal
(some refs give many cards, others few) and verifies:

  * The evaluator produces a valid report dict.
  * AUC with referee > AUC without referee (signal recovery).
  * Reliability curves are well-formed.
  * Output JSON contract is stable.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

# Import the helpers we want to test (NOT the CLI main, which is I/O).
from scripts.run_cards_phase1_modelonly import (  # type: ignore
    _ablation_diff,
    _auc,
    _brier,
    _evaluate_dataset,
    _reliability_curve,
    _verdict_for_auc,
    LINES_TO_TEST,
)


# ─────────────────────────────────────────────────────────────────────
# Synthetic dataset generator with a REAL referee signal embedded
# ─────────────────────────────────────────────────────────────────────
REFEREES_HIGH_CARDS = ["Ref_Harsh_1", "Ref_Harsh_2"]   # avg ~6
REFEREES_LOW_CARDS  = ["Ref_Lenient_1", "Ref_Lenient_2"]  # avg ~3

TEAMS = [f"Team_{i}" for i in range(10)]


def _gen_history(seed: int = 42, n: int = 200) -> list[dict]:
    """Build a 200-match history where:
        * Harsh refs produce on average 6 cards/match (Poisson(6))
        * Lenient refs produce on average 3 cards/match (Poisson(3))
    """
    rng = random.Random(seed)
    out: list[dict] = []
    base = datetime(2024, 6, 1, tzinfo=timezone.utc)
    for i in range(n):
        date = base + timedelta(days=i)
        ref_pool = REFEREES_HIGH_CARDS if i % 2 == 0 else REFEREES_LOW_CARDS
        ref = rng.choice(ref_pool)
        lam = 6.0 if ref in REFEREES_HIGH_CARDS else 3.0
        # Sample Poisson by inverse method.
        total = _sample_poisson(rng, lam)
        # Split between teams (~equal).
        h_cards = rng.randint(0, total)
        a_cards = total - h_cards
        home, away = rng.sample(TEAMS, 2)
        out.append({
            "match_id":   f"m{i}",
            "date":       date.isoformat(),
            "league":     "Premier League",
            "home_team":  home,
            "away_team":  away,
            "referee":    ref,
            "home_cards": h_cards,
            "away_cards": a_cards,
            "home_fouls": rng.randint(8, 16),
            "away_fouls": rng.randint(8, 16),
        })
    return out


def _sample_poisson(rng: random.Random, lam: float) -> int:
    """Inverse CDF sampling of Poisson(lam). Pure Python."""
    import math
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1
        if k > 50:  # safety cap
            return k


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────
def test_evaluator_produces_valid_report_with_synthetic_dataset():
    ds = _gen_history(seed=42, n=200)
    report = _evaluate_dataset(ds, use_referee_factor=True)
    assert report["n_matches_used"] == 200
    for line in LINES_TO_TEST:
        L = str(line)
        blk = report["per_line"][L]
        assert blk["n"] == 200
        assert 0.0 <= blk["base_rate"] <= 1.0
        # AUC may be None for degenerate cases but for n=200 we expect a value.
        assert blk["auc_model"] is not None
        assert blk["brier_model"] is not None
        # Reliability has 10 buckets.
        assert len(blk["reliability_curve"]) == 10


def test_ablation_recovers_referee_signal_on_synthetic_dataset():
    """The dataset was built so refs DO carry the signal. The ablation
    must show AUC_with > AUC_without on at least 1 line.
    """
    ds = _gen_history(seed=42, n=200)
    no_ref   = _evaluate_dataset(ds, use_referee_factor=False)
    with_ref = _evaluate_dataset(ds, use_referee_factor=True)
    table = _ablation_diff(no_ref, with_ref)

    # At least one line must show referee_helps=True.
    helps_lines = [L for L, v in table["per_line"].items() if v["referee_helps"]]
    assert helps_lines, (
        f"Referee signal NOT recovered — ablation table: {table}"
    )


def test_auc_metric_helper_handles_perfect_separation():
    # Scores perfectly aligned with labels.
    scores = [0.9, 0.8, 0.7, 0.2, 0.1, 0.05]
    labels = [1,   1,   1,   0,   0,   0]
    assert _auc(scores, labels) == 1.0


def test_auc_metric_helper_returns_half_for_random():
    # Equal scores → ties only → AUC = 0.5.
    scores = [0.5] * 4
    labels = [0, 1, 0, 1]
    assert _auc(scores, labels) == 0.5


def test_auc_metric_returns_none_when_single_class():
    scores = [0.3, 0.7, 0.2]
    labels = [1, 1, 1]
    assert _auc(scores, labels) is None


def test_brier_metric_zero_on_perfect_predictions():
    scores = [1.0, 0.0, 1.0]
    labels = [1, 0, 1]
    assert _brier(scores, labels) == 0.0


def test_verdict_tags_follow_threshold_rubric():
    assert "AUC_GOOD_JUSTIFIES_PHASE_2"  in _verdict_for_auc(0.65)
    assert "AUC_MARGINAL_INVESTIGATE_BEFORE_PHASE_2" in _verdict_for_auc(0.57)
    assert "AUC_WEAK_DO_NOT_PROCEED"      in _verdict_for_auc(0.53)
    assert "AUC_CHANCE_LEVEL_STOP"        in _verdict_for_auc(0.50)
    assert "AUC_NOT_COMPUTABLE"           in _verdict_for_auc(None)


def test_reliability_curve_has_10_buckets_and_handles_empty():
    out = _reliability_curve([], [], n_buckets=10)
    assert len(out) == 10
    for b in out:
        assert b["n"] == 0
        assert b["mean_pred"] is None
        assert b["hit_rate"] is None


def test_higher_line_reduces_base_rate_on_realistic_data():
    """Sanity: more cards needed for over → fewer hits."""
    ds = _gen_history(seed=42, n=200)
    report = _evaluate_dataset(ds, use_referee_factor=True)
    br_3_5 = report["per_line"]["3.5"]["base_rate"]
    br_4_5 = report["per_line"]["4.5"]["base_rate"]
    br_5_5 = report["per_line"]["5.5"]["base_rate"]
    assert br_3_5 >= br_4_5 >= br_5_5
