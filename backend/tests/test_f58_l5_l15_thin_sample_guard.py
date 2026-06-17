"""Sprint-B prereq · Tests for the L5/L15 thin-sample transparency
guard in ``football_phaseF58_integration``.

Reproduces the user-reported bug from the INTELIGENCIA F58 · CROSS &
PROPS panel where a national team with only ~5 recent fixtures showed
identical L5 and L15 averages (e.g. *Goles+ 2.33 vs 2.33*) without
any indication that the two windows had collapsed onto the same data.

The fix exposes a ``_sample`` block on the L5/L15 derivation output so
the UI can render sample-size subscripts and a "muestra delgada"
banner. These tests pin down the contract.
"""
from __future__ import annotations

import pytest

from services.football_phaseF58_integration import (
    _slice_avg,
    _slice_avg_with_n,
    _derive_l5_l15_from_recent,
    L5_FULL_SAMPLE_N,
    L15_FULL_SAMPLE_N,
)


# ─────────────────────────────────────────────────────────────────────
# 1. _slice_avg_with_n contract
# ─────────────────────────────────────────────────────────────────────
class TestSliceAvgWithN:
    def test_empty_returns_none_zero(self):
        assert _slice_avg_with_n([], 5) == (None, 0)
        assert _slice_avg_with_n(None, 5) == (None, 0)

    def test_full_sample(self):
        avg, n = _slice_avg_with_n([1, 2, 3, 4, 5], 5)
        assert avg == 3.0
        assert n == 5

    def test_partial_sample_returns_actual_n(self):
        # 3 fixtures asked-for-5 → average over 3 with n=3.
        avg, n = _slice_avg_with_n([2, 3, 2], 5)
        assert avg == pytest.approx(2.333, abs=1e-3)
        assert n == 3

    def test_l5_and_l15_collapse_when_only_5_fixtures(self):
        """The exact pathology the user observed: same average for
        both L5 and L15 because the team has only 5 fixtures."""
        gf_list = [3, 2, 4, 1, 2]  # avg = 2.4
        avg5, n5    = _slice_avg_with_n(gf_list, 5)
        avg15, n15  = _slice_avg_with_n(gf_list, 15)
        assert avg5 == avg15
        assert n5 == n15 == 5

    def test_l5_and_l15_differ_when_team_has_more_than_5_fixtures(self):
        # 7 fixtures: L5 = avg of newest 5, L15 = avg of all 7.
        gf_list = [3, 2, 4, 1, 2, 1, 0]   # avg5=2.4, avg7=1.857
        avg5, n5   = _slice_avg_with_n(gf_list, 5)
        avg15, n15 = _slice_avg_with_n(gf_list, 15)
        assert avg5 != avg15
        assert n5 == 5 and n15 == 7

    def test_coerces_string_inputs(self):
        avg, n = _slice_avg_with_n(["1.5", "2.5"], 5)
        assert avg == 2.0
        assert n == 2

    def test_silently_skips_garbage_inputs(self):
        avg, n = _slice_avg_with_n([1, "garbage", 2, None, 3], 5)
        assert avg == 2.0
        assert n == 3

    def test_backward_compat_old_slice_avg_still_works(self):
        """``_slice_avg`` (old API) must keep returning a bare float so
        downstream callers that haven't migrated don't break."""
        assert _slice_avg([1, 2, 3], 5) == 2.0


# ─────────────────────────────────────────────────────────────────────
# 2. _derive_l5_l15_from_recent — sample block contract
# ─────────────────────────────────────────────────────────────────────
class TestDeriveSampleBlock:
    def _side_with_n_fixtures(self, n: int) -> dict:
        return {
            "context": {
                "recent_fixtures": {
                    "gf":      [2] * n,
                    "ga":      [1] * n,
                    "corners": [5] * n,
                }
            }
        }

    def test_emits_sample_block(self):
        out = _derive_l5_l15_from_recent(self._side_with_n_fixtures(5))
        assert "_sample" in out
        sample = out["_sample"]
        assert sample["goals_for_l5_n"] == 5
        assert sample["goals_for_l15_n"] == 5
        assert sample["corners_l5_n"] == 5

    def test_collapsed_flag_set_when_only_3_fixtures(self):
        """User-screenshot scenario: team has ~3 fixtures, both L5 and
        L15 collapse onto the same data. The flag MUST flip True."""
        out = _derive_l5_l15_from_recent(self._side_with_n_fixtures(3))
        sample = out["_sample"]
        # L5 and L15 averages are identical (no surprise) but the flag
        # surfaces the degeneracy.
        assert out["goals_for_l5"] == out["goals_for_l15"]
        assert sample["l5_eq_l15_collapsed"] is True
        assert sample["l5_thin_sample"]      is True
        assert sample["l15_thin_sample"]     is True

    def test_collapsed_flag_set_when_exactly_5_fixtures(self):
        """5 fixtures → L5 == L15 by construction (still degenerate)."""
        out = _derive_l5_l15_from_recent(self._side_with_n_fixtures(5))
        sample = out["_sample"]
        assert out["goals_for_l5"] == out["goals_for_l15"]
        assert sample["l5_eq_l15_collapsed"] is True

    def test_collapsed_flag_false_when_team_has_7_fixtures(self):
        """7 fixtures → L5 uses newest 5, L15 uses all 7. They differ
        AND the collapsed flag is False."""
        out = _derive_l5_l15_from_recent({
            "context": {"recent_fixtures": {
                "gf": [3, 2, 4, 1, 2, 1, 0],  # avg5=2.4, avg7≈1.857
                "ga": [1, 1, 1, 1, 1, 1, 1],
            }}
        })
        sample = out["_sample"]
        assert out["goals_for_l5"] != out["goals_for_l15"]
        assert sample["l5_eq_l15_collapsed"] is False
        # L15 sample size below threshold remains "thin".
        assert sample["l15_thin_sample"] is True
        assert sample["goals_for_l15_n"] == 7

    def test_full_sample_clears_thin_flags(self):
        """When the team has ≥10 fixtures, no thin-sample flags."""
        out = _derive_l5_l15_from_recent(self._side_with_n_fixtures(15))
        sample = out["_sample"]
        assert sample["l5_thin_sample"]  is False
        assert sample["l15_thin_sample"] is False
        # L5 still averages over newest 5; L15 over all 15.
        assert out["goals_for_l5"]  == 2.0
        assert out["goals_for_l15"] == 2.0
        # Same value, BUT collapsed_flag must be False because
        # n_15 > L5_FULL_SAMPLE_N+1.
        assert sample["l5_eq_l15_collapsed"] is False

    def test_empty_recent_fixtures_does_not_crash(self):
        out = _derive_l5_l15_from_recent({})
        assert out["goals_for_l5"] is None
        assert out["_sample"]["goals_for_l5_n"] == 0
        assert out["_sample"]["l5_eq_l15_collapsed"] is False


# ─────────────────────────────────────────────────────────────────────
# 3. The exact user-screenshot reproduction
# ─────────────────────────────────────────────────────────────────────
class TestUserScreenshotRepro:
    """The user reported LOCAL Goles+ 2.33 vs 2.33 in the F58 panel.
    With 3 fixtures averaging (3+2+2)/3 = 2.333, both L5 and L15
    collapse to the same value. Reproduce exactly and assert the new
    transparency surfaces it."""

    def test_3_fixtures_reproduces_233_collapse(self):
        side = {
            "context": {
                "recent_fixtures": {
                    # 3 fixtures with goals 3, 2, 2 → avg = 2.333
                    "gf": [3, 2, 2],
                    "ga": [0, 1, 0],   # avg = 0.333 (matches Goles− 0.33)
                    "corners": [6, 4, 5],
                }
            }
        }
        out = _derive_l5_l15_from_recent(side)
        # Numerical reproduction of the screenshot.
        assert out["goals_for_l5"]     == pytest.approx(2.333, abs=1e-3)
        assert out["goals_for_l15"]    == pytest.approx(2.333, abs=1e-3)
        assert out["goals_against_l5"]  == pytest.approx(0.333, abs=1e-3)
        assert out["goals_against_l15"] == pytest.approx(0.333, abs=1e-3)
        # The transparency block MUST flag this case.
        s = out["_sample"]
        assert s["l5_eq_l15_collapsed"] is True
        assert s["l5_thin_sample"]      is True
        assert s["l15_thin_sample"]     is True
        assert s["goals_for_l5_n"]      == 3
        assert s["goals_for_l15_n"]     == 3
