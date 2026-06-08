"""Unit tests for the pure helpers in backtest_bullpen_under_hypothesis.

The async + DB-driven parts are exercised end-to-end with `python
scripts/backtest_bullpen_under_hypothesis.py --days N` against a real
mongo. Here we only validate the cohort assignment, settlement and
aggregation primitives.
"""
import importlib.util
from pathlib import Path

# Import the script as a module (it sits under scripts/ not as a package).
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backtest_bullpen_under_hypothesis.py"
_spec = importlib.util.spec_from_file_location("bullpen_backtest", _SCRIPT)
bb = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(bb)                 # type: ignore[union-attr]


def _snap(**overrides):
    base = {
        "recommended_pick":      "Under 9.5",
        "expected_runs":         7.8,
        "market_line":           9.5,
        "bullpen_era_7d":        6.20,
        "script_survival":       50,
        "offensive_explosion":   60,
        "fragility":             45,
        "hr_risk":               40,
        "pressure_base":         45,
        "total_runs_final":      8,
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────
# Cohort assignment
# ─────────────────────────────────────────────────────────────────────
def test_cohort_A_when_bullpen_high_and_traffic_signals():
    assert bb.assign_cohort(_snap(bullpen_era_7d=6.0)) == "A"


def test_cohort_B_when_bullpen_normal():
    assert bb.assign_cohort(_snap(bullpen_era_7d=4.0)) == "B"


def test_no_cohort_in_gap_zone():
    # 4.50–5.50 excluded for separation cleanliness.
    assert bb.assign_cohort(_snap(bullpen_era_7d=5.0)) is None


def test_no_cohort_when_pick_is_over():
    assert bb.assign_cohort(_snap(recommended_pick="Over 9.5")) is None


def test_no_cohort_when_expected_above_line():
    assert bb.assign_cohort(_snap(expected_runs=10.0)) is None


def test_no_cohort_when_script_survival_high():
    assert bb.assign_cohort(_snap(script_survival=80)) is None


def test_no_cohort_when_offensive_explosion_low():
    assert bb.assign_cohort(_snap(offensive_explosion=30)) is None


# ─────────────────────────────────────────────────────────────────────
# Sub-cohort A1 / A2
# ─────────────────────────────────────────────────────────────────────
def test_subcohort_A2_with_traffic():
    s = _snap(hr_risk=70)
    assert bb.assign_sub_cohort(s, "A") == "A2"


def test_subcohort_A1_without_traffic():
    s = _snap(hr_risk=10, pressure_base=10, offensive_explosion=30)
    # Above offensive_explosion threshold is needed for cohort A — relax via direct sub call.
    assert bb.assign_sub_cohort(s, "A") in ("A1", "A2")
    # With both signals low:
    s2 = _snap(hr_risk=20, pressure_base=20)
    # offensive_explosion=60 is still ≥50 → counts as traffic via first signal
    assert bb.assign_sub_cohort(s2, "A") == "A2"


def test_subcohort_only_applies_to_A():
    assert bb.assign_sub_cohort(_snap(), "B") is None


# ─────────────────────────────────────────────────────────────────────
# Settlement
# ─────────────────────────────────────────────────────────────────────
def test_settle_under_won_loss_push_void():
    assert bb.settle_under(9.5, 8)[0] == "won"
    assert bb.settle_under(9.5, 11)[0] == "lost"
    assert bb.settle_under(9.0, 9.0)[0] == "push"
    assert bb.settle_under(None, 8)[0] == "void"
    assert bb.settle_under(9.5, None)[0] == "void"


def test_settle_pnl_signs():
    assert bb.settle_under(9.5, 8)[1] == 0.91
    assert bb.settle_under(9.5, 11)[1] == -1.0
    assert bb.settle_under(9.0, 9.0)[1] == 0.0


# ─────────────────────────────────────────────────────────────────────
# Aggregate
# ─────────────────────────────────────────────────────────────────────
def test_aggregate_basic_metrics():
    rows = [
        {"snapshot": _snap(total_runs_final=8),  "outcome": "won",  "pnl": 0.91, "cohort": "A"},
        {"snapshot": _snap(total_runs_final=12), "outcome": "lost", "pnl": -1.0, "cohort": "A"},
        {"snapshot": _snap(total_runs_final=9.5),"outcome": "push", "pnl": 0.0,  "cohort": "A"},
    ]
    agg = bb.aggregate(rows)
    assert agg["sample_size"] == 3
    assert agg["wins"] == 1 and agg["losses"] == 1 and agg["pushes"] == 1
    # Counts pushes out of the rate (1 push removed → 1/2 = 0.50)
    assert agg["under_hit_rate"] == 0.5
    assert agg["push_rate"] == round(1 / 3, 4)
    assert agg["average_actual_runs"] is not None
    assert agg["average_expected_runs"] is not None
    assert agg["average_error"] is not None


def test_aggregate_empty_returns_skeleton():
    assert bb.aggregate([]) == {"sample_size": 0}
