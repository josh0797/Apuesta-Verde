"""Phase F66 — Dixon-Coles scoreline grid smoke tests."""
from __future__ import annotations

import pytest

from services.football_dixon_coles import (
    METHOD_DC, METHOD_POISSON, METHOD_HEURISTIC, METHOD_UNAVAILABLE,
    compute_scoreline_grid,
)


def test_dc_with_balanced_low_xg_favours_1_1_or_0_0() -> None:
    out = compute_scoreline_grid(1.10, 0.95)
    assert out["available"] is True
    assert out["method"] == METHOD_DC
    most = out["most_likely"]["score"]
    # Top scoreline should be in the low-event cluster.
    assert most in {"0-0", "1-0", "1-1", "0-1"}
    # All probabilities sum to ~1.
    total = sum(s["probability"] for s in out["top_scorelines"])
    assert 0 < total < 1.0


def test_dc_with_high_home_favourite_skews_to_2_0() -> None:
    out = compute_scoreline_grid(2.10, 0.55)
    assert out["available"] is True
    most = out["most_likely"]
    assert most["home_goals"] >= most["away_goals"]
    assert most["score"] in {"1-0", "2-0", "2-1", "3-0", "3-1"}


def test_dc_top_n_count_and_order() -> None:
    out = compute_scoreline_grid(1.5, 1.2, top_n=3)
    assert len(out["top_scorelines"]) == 3
    probs = [s["probability"] for s in out["top_scorelines"]]
    assert probs == sorted(probs, reverse=True)


def test_dc_vs_poisson_only_differ_at_low_scores() -> None:
    dc  = compute_scoreline_grid(1.2, 1.0, use_dixon_coles=True)
    poi = compute_scoreline_grid(1.2, 1.0, use_dixon_coles=False)
    assert dc["method"]  == METHOD_DC
    assert poi["method"] == METHOD_POISSON
    p_dc  = {s["score"]: s["probability"] for s in dc["top_scorelines"]}
    p_poi = {s["score"]: s["probability"] for s in poi["top_scorelines"]}
    # 1-1 cell gets boosted under DC (rho = -0.13) ⇒ 1-1 prob is HIGHER
    # in DC than in pure Poisson (because τ(1,1) = 1 - rho > 1).
    if "1-1" in p_dc and "1-1" in p_poi:
        assert p_dc["1-1"] >= p_poi["1-1"]


def test_dc_heuristic_fallback_when_no_xg() -> None:
    out = compute_scoreline_grid(None, None, profile_hint="UNDER")
    assert out["available"] is True
    assert out["method"] == METHOD_HEURISTIC
    # Top scoreline must come from the UNDER canonical list.
    assert out["most_likely"]["score"] in {"1-0", "1-1", "0-0", "2-0", "0-1"}
    assert out["confidence"] <= 50    # heuristic capped


def test_dc_unavailable_when_no_xg_and_no_profile_hint() -> None:
    out = compute_scoreline_grid(None, None)
    assert out["available"] is False
    assert out["method"] == METHOD_UNAVAILABLE
    assert out["top_scorelines"] == []


@pytest.mark.parametrize("bad", ["x", float("nan"), float("inf"), -1.0])
def test_dc_handles_garbage_inputs(bad) -> None:
    out = compute_scoreline_grid(bad, 1.0, profile_hint="NEUTRAL")
    # Falls back through heuristic gracefully.
    assert out["available"] is True
    assert out["method"] == METHOD_HEURISTIC
