"""Sprint-D9.1 — Tests for the goal-xG overperformance feature + the
offset logistic regression residual model.

Determinism, fail-soft and point-in-time guarantees are verified with
synthetic datasets.
"""
from __future__ import annotations

import math
import random

import pytest

from services.football_goal_xg_overperformance import (
    compute_goal_xg_overperformance,
)
from services.football_residual_model import (
    fit_residual_model, identity_model, standardise_features,
    standardise_one, _sigmoid, _logit,
)


# ════════════════════════════════════════════════════════════════════════
# 1) Goal-xG overperformance — point-in-time discipline
# ════════════════════════════════════════════════════════════════════════
def _mk(home, away, fthg, ftag, date_str):
    from datetime import datetime
    return {"home_team": home, "away_team": away,
             "fthg": fthg, "ftag": ftag,
             "date": datetime.strptime(date_str, "%Y-%m-%d")}


def test_goal_xg_overperformance_none_with_short_history():
    matches = [
        _mk("A", "B", 1, 0, "2024-08-01"),
        _mk("A", "C", 2, 1, "2024-08-08"),
        _mk("A", "D", 0, 2, "2024-08-15"),
        _mk("A", "E", 1, 1, "2024-08-22"),
        _mk("E", "F", 1, 1, "2024-08-29"),       # target
    ]
    # Team A has only 4 prior matches < min_l15=6 → expect None.
    val = compute_goal_xg_overperformance("A", matches, target_index=4)
    assert val is None


def test_goal_xg_overperformance_zero_for_stationary_team():
    """A team that always scores exactly its rolling-mean of goals
    should produce overperformance close to zero."""
    matches = []
    # 25 matches where team A scores exactly 2 every time.
    for i in range(25):
        d = f"2024-0{(i // 10) + 1}-{(i % 10) + 1:02d}"
        if i % 2 == 0:
            matches.append(_mk("A", f"X{i}", 2, 0, d))
        else:
            matches.append(_mk(f"X{i}", "A", 0, 2, d))
    val = compute_goal_xg_overperformance("A", matches, target_index=24)
    assert val is not None
    assert abs(val) < 0.05


def test_goal_xg_overperformance_positive_for_improving_team():
    """A team scoring more in recent matches than in its earlier
    rolling average should yield POSITIVE overperformance."""
    matches = []
    # 10 weak matches (0 goals), then 15 strong matches (3 goals).
    for i in range(10):
        matches.append(_mk("A", f"X{i}", 0, 0, f"2024-01-{i+1:02d}"))
    for i in range(15):
        matches.append(_mk("A", f"Y{i}", 3, 1, f"2024-02-{i+1:02d}"))
    # Add target match.
    matches.append(_mk("Z", "A", 1, 1, "2024-03-01"))
    val = compute_goal_xg_overperformance("A", matches, target_index=25)
    assert val is not None
    # Expected: most of the L15 window is "3 goals while proxy ~0 then ~3".
    # So overperformance should be POSITIVE.
    assert val > 0.5


def test_goal_xg_overperformance_excludes_target_match():
    """Adding goals AT the target index must NOT change the past-only
    feature (anti-look-ahead invariant)."""
    base = []
    for i in range(20):
        base.append(_mk("A", f"X{i}", 1, 0, f"2024-01-{i+1:02d}"))
    matches_v1 = base + [_mk("A", "Z", 5, 0, "2024-02-01")]      # target=20
    val_v1 = compute_goal_xg_overperformance("A", matches_v1, 20)
    matches_v2 = base + [_mk("A", "Z", 0, 5, "2024-02-01")]      # different score
    val_v2 = compute_goal_xg_overperformance("A", matches_v2, 20)
    assert val_v1 == val_v2


# ════════════════════════════════════════════════════════════════════════
# 2) Offset logistic regression
# ════════════════════════════════════════════════════════════════════════
def test_residual_model_learns_when_signal_present():
    """When the data follows y ~ Bernoulli(σ(logit(prior) + β·x)) with
    β=0.7, the fitted weight must be close to 0.7."""
    random.seed(13)
    n = 1500
    X = [[random.gauss(0, 1)] for _ in range(n)]
    mk = [max(0.05, min(0.95, 0.5 + 0.1 * random.gauss(0, 1)))
            for _ in range(n)]
    y = []
    for i in range(n):
        z = _logit(mk[i]) + 0.7 * X[i][0]
        y.append(1 if random.random() < _sigmoid(z) else 0)
    model = fit_residual_model(X, y, mk, ["x"], lambda_l2=0.01,
                                  lr=0.1, n_iter=800)
    assert 0.55 <= model.weights[0] <= 0.85
    assert abs(model.bias) < 0.15


def test_residual_model_l2_shrinks_weights_when_no_signal():
    random.seed(17)
    n = 1500
    X = [[random.gauss(0, 1)] for _ in range(n)]
    mk = [max(0.05, min(0.95, 0.5 + 0.1 * random.gauss(0, 1)))
            for _ in range(n)]
    # y is independent of X — purely the prior.
    y = [1 if random.random() < mk[i] else 0 for i in range(n)]
    model = fit_residual_model(X, y, mk, ["x"], lambda_l2=2.0,
                                  lr=0.1, n_iter=800)
    assert abs(model.weights[0]) < 0.10


def test_residual_model_brier_match_at_zero_correction():
    """When the optimum is no-correction (weights=0), the train Brier
    of the model must approach the market's Brier."""
    random.seed(23)
    n = 600
    X = [[random.gauss(0, 1)] for _ in range(n)]
    mk = [max(0.10, min(0.90, 0.5 + 0.15 * random.gauss(0, 1)))
            for _ in range(n)]
    y = [1 if random.random() < mk[i] else 0 for i in range(n)]
    model = fit_residual_model(X, y, mk, ["x"], lambda_l2=5.0,
                                  lr=0.1, n_iter=800)
    # Brier model ≈ Brier market (large L2 squashes the weight).
    assert abs(model.train_brier_model - model.train_brier_market) < 0.005


def test_identity_model_predicts_market_prior_exactly():
    im = identity_model(["a", "b"])
    p = im.predict([1.0, 2.0], 0.42)
    assert abs(p - 0.42) < 1e-4
    p2 = im.predict([10.0, -50.0], 0.65)
    assert abs(p2 - 0.65) < 1e-4


def test_standardisation_handles_missing_values():
    rows = [[1.0, None], [2.0, 5.0], [3.0, None], [None, 7.0]]
    X, means, stds = standardise_features(rows, ["a", "b"])
    # column a: mean=(1+2+3)/3=2; column b: mean=(5+7)/2=6.
    assert abs(means[0] - 2.0) < 1e-9
    assert abs(means[1] - 6.0) < 1e-9
    # All None rows in column a become 0 (mean in std space).
    assert X[3][0] == 0.0
    # Non-None values are z-standardised.
    assert abs(X[0][0] - (1.0 - 2.0) / stds[0]) < 1e-9


def test_residual_model_serialisation_roundtrip():
    """Model can be dumped and rebuilt without losing prediction
    parity."""
    from services.football_residual_model import ResidualModel
    random.seed(31)
    n = 300
    X = [[random.gauss(0, 1)] for _ in range(n)]
    mk = [0.5] * n
    y = [1 if random.random() < 0.5 else 0 for i in range(n)]
    m1 = fit_residual_model(X, y, mk, ["x"], lambda_l2=0.5, lr=0.1, n_iter=400)
    d = m1.to_dict()
    m2 = ResidualModel.from_dict(d)
    for r, p in zip(X[:30], mk[:30]):
        assert abs(m1.predict(r, p) - m2.predict(r, p)) < 1e-9


# ════════════════════════════════════════════════════════════════════════
# 3) Diagnostic JSON existence (smoke if regenerated)
# ════════════════════════════════════════════════════════════════════════
def test_d9_1_reports_have_required_sections():
    from pathlib import Path
    report_dir = Path("/app/diagnostics")
    if not (report_dir / "_d9_1_index.json").exists():
        pytest.skip("D9.1 reports not pre-generated.")
    import json
    for fn in report_dir.glob("residual_d9_1_*.json"):
        rep = json.loads(fn.read_text())
        assert "verdict" in rep and "tags" in rep["verdict"]
        assert "diag_residual" in rep
        assert "diag_market_devig" in rep
        assert "diag_dc_original" in rep
        assert "bootstrap_brier_resid_vs_market" in rep
        assert "rows" in rep and len(rep["rows"]) > 0
        # Each row carries the three probability tracks.
        for r in rep["rows"][:5]:
            assert "p_dc_original" in r
            assert "p_market_devig" in r
            assert "p_residual_final" in r
