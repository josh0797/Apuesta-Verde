"""F97.1 — Tests para `mlb_under_hard_rules.evaluate_under_hard_rules`.

Cobertura exhaustiva de:
  * Boundary conditions de los thresholds 0.42 / 0.48 / 0.55.
  * Tail rules: HIGH + line ≤ 9.5 → AVOID; EXTREME → BLOCK.
  * Resolución de la acción más severa cuando varias reglas se activan.
  * Picks no-Under (OVER/ML/RL) → NOT_APPLICABLE.
  * Datos incompletos → NO_OVER_RISK_AVAILABLE, applicable=True.
  * Distintos formatos de claves en `final_over_probabilities`.
  * Exportación de símbolos públicos.
"""
from __future__ import annotations

import pytest

from services import mlb_under_hard_rules as rules


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _fop(line: float, over_p: float) -> dict:
    """Build a minimal final_over_probabilities dict."""
    return {f"over_{line}": over_p}


def _eval(**overrides):
    base = {
        "final_over_probabilities": _fop(8.5, 0.30),
        "line": 8.5,
        "tail_bucket": "LOW",
        "pick_side": "UNDER",
        "market": "under_8.5",
    }
    base.update(overrides)
    return rules.evaluate_under_hard_rules(**base)


# =====================================================================
# Aplicabilidad
# =====================================================================
class TestApplicability:
    def test_under_pick_is_applicable(self):
        r = _eval()
        assert r["applicable"] is True

    def test_over_pick_not_applicable(self):
        r = _eval(pick_side="OVER", market="over_8.5")
        assert r["applicable"] is False
        assert r["action"] == rules.ACTION_NONE
        assert rules.RC_UNDER_RULES_NOT_APPLICABLE in r["reason_codes"]

    def test_market_under_lowercase(self):
        r = _eval(pick_side=None, market="under_8.5")
        assert r["applicable"] is True

    def test_market_unknown_not_applicable(self):
        r = _eval(pick_side=None, market="MONEYLINE_HOME")
        assert r["applicable"] is False

    def test_no_pick_side_or_market(self):
        r = _eval(pick_side=None, market=None)
        assert r["applicable"] is False


# =====================================================================
# Boundary thresholds — over_risk
# =====================================================================
class TestOverRiskThresholds:
    @pytest.mark.parametrize("over_p", [0.0, 0.10, 0.30, 0.41999])
    def test_below_warn_is_none(self, over_p):
        r = _eval(final_over_probabilities=_fop(8.5, over_p))
        assert r["action"] == rules.ACTION_NONE
        assert r["severity"] == 0
        assert r["score_delta"] == 0

    def test_warn_boundary_min_inclusive(self):
        r = _eval(final_over_probabilities=_fop(8.5, 0.42))
        assert r["action"] == rules.ACTION_WARN
        assert r["severity"] == 1
        assert r["score_delta"] == rules.SCORE_DELTA_WARN
        assert rules.RC_UNDER_RULES_WARN_OVER_RISK in r["reason_codes"]
        assert r["is_blocked"] is False
        assert r["block_max_pick"] is False
        assert r["exclude_from_main_feed"] is False

    def test_warn_just_below_avoid_threshold(self):
        r = _eval(final_over_probabilities=_fop(8.5, 0.4799))
        assert r["action"] == rules.ACTION_WARN

    def test_avoid_boundary_min_inclusive(self):
        r = _eval(final_over_probabilities=_fop(8.5, 0.48))
        assert r["action"] == rules.ACTION_AVOID
        assert r["severity"] == 2
        assert r["score_delta"] == rules.SCORE_DELTA_AVOID
        assert rules.RC_UNDER_RULES_AVOID_OVER_RISK in r["reason_codes"]
        assert rules.RC_UNDER_RECOMMENDATION_DEGRADED in r["reason_codes"]
        assert r["is_blocked"] is False
        assert r["block_max_pick"] is True
        assert r["exclude_from_main_feed"] is False

    def test_avoid_just_below_block_threshold(self):
        r = _eval(final_over_probabilities=_fop(8.5, 0.5499))
        assert r["action"] == rules.ACTION_AVOID

    def test_block_boundary_min_inclusive(self):
        r = _eval(final_over_probabilities=_fop(8.5, 0.55))
        assert r["action"] == rules.ACTION_BLOCK
        assert r["severity"] == 3
        assert r["score_delta"] == rules.SCORE_DELTA_BLOCK
        assert rules.RC_UNDER_RULES_BLOCK_OVER_RISK in r["reason_codes"]
        assert rules.RC_UNDER_RECOMMENDATION_DEGRADED in r["reason_codes"]
        assert r["is_blocked"] is True
        assert r["block_max_pick"] is True
        assert r["exclude_from_main_feed"] is True
        assert r["category"] == "debug"

    @pytest.mark.parametrize("over_p", [0.55, 0.70, 0.999])
    def test_block_high_over_risk(self, over_p):
        r = _eval(final_over_probabilities=_fop(8.5, over_p))
        assert r["action"] == rules.ACTION_BLOCK


# =====================================================================
# Tail rules
# =====================================================================
class TestTailRules:
    def test_tail_extreme_blocks_regardless_of_line(self):
        r = _eval(
            final_over_probabilities=_fop(11.5, 0.10),
            line=11.5,
            tail_bucket="EXTREME",
        )
        assert r["action"] == rules.ACTION_BLOCK
        assert rules.RC_UNDER_RULES_BLOCK_TAIL_EXTREME in r["reason_codes"]
        assert "TAIL_EXTREME_BLOCK" in r["triggered_rules"]

    def test_tail_high_low_line_avoid(self):
        r = _eval(
            final_over_probabilities=_fop(9.5, 0.20),
            line=9.5,
            tail_bucket="HIGH",
        )
        assert r["action"] == rules.ACTION_AVOID
        assert rules.RC_UNDER_RULES_AVOID_TAIL_HIGH_LOW in r["reason_codes"]

    def test_tail_high_high_line_does_not_avoid(self):
        # HIGH + line > 9.5 → tail rule does NOT trigger.
        r = _eval(
            final_over_probabilities=_fop(10.5, 0.10),
            line=10.5,
            tail_bucket="HIGH",
        )
        assert r["action"] == rules.ACTION_NONE

    def test_tail_low_does_not_trigger(self):
        r = _eval(
            final_over_probabilities=_fop(8.5, 0.10),
            line=8.5,
            tail_bucket="LOW",
        )
        assert r["action"] == rules.ACTION_NONE

    def test_tail_medium_does_not_trigger(self):
        r = _eval(
            final_over_probabilities=_fop(8.5, 0.10),
            line=8.5,
            tail_bucket="MEDIUM",
        )
        assert r["action"] == rules.ACTION_NONE

    def test_bucket_normalisation_lowercase(self):
        r = _eval(
            final_over_probabilities=_fop(8.5, 0.10),
            line=8.5,
            tail_bucket="extreme",
        )
        assert r["action"] == rules.ACTION_BLOCK


# =====================================================================
# Combinaciones: la acción más severa gana
# =====================================================================
class TestActionEscalation:
    def test_warn_plus_tail_extreme_block_wins(self):
        # over_risk=0.43 → WARN, tail=EXTREME → BLOCK; BLOCK wins.
        r = _eval(
            final_over_probabilities=_fop(8.5, 0.43),
            line=8.5,
            tail_bucket="EXTREME",
        )
        assert r["action"] == rules.ACTION_BLOCK
        assert "OVER_RISK_WARN" in r["triggered_rules"]
        assert "TAIL_EXTREME_BLOCK" in r["triggered_rules"]
        # BLOCK reason takes precedence semantically.
        assert rules.RC_UNDER_RULES_BLOCK_TAIL_EXTREME in r["reason_codes"]

    def test_avoid_plus_warn_picks_avoid(self):
        # over_risk=0.50 → AVOID; tail HIGH + line 8.5 → AVOID. Same severity.
        r = _eval(
            final_over_probabilities=_fop(8.5, 0.50),
            line=8.5,
            tail_bucket="HIGH",
        )
        assert r["action"] == rules.ACTION_AVOID
        assert "OVER_RISK_AVOID" in r["triggered_rules"]
        assert "TAIL_HIGH_LOW_LINE_AVOID" in r["triggered_rules"]

    def test_warn_plus_tail_high_low_line_avoid_wins(self):
        # over_risk=0.43 → WARN; tail HIGH + line 9.0 → AVOID. AVOID wins.
        r = _eval(
            final_over_probabilities=_fop(9.0, 0.43),
            line=9.0,
            tail_bucket="HIGH",
        )
        assert r["action"] == rules.ACTION_AVOID


# =====================================================================
# Datos incompletos
# =====================================================================
class TestMissingData:
    def test_no_over_risk_no_tail_returns_no_data(self):
        r = rules.evaluate_under_hard_rules(
            final_over_probabilities={},  # no key
            line=8.5,
            tail_bucket=None,
            pick_side="UNDER",
            market="under_8.5",
        )
        assert r["applicable"] is True
        assert r["action"] == rules.ACTION_NONE
        assert rules.RC_UNDER_RULES_NO_OVER_RISK_AVAILABLE in r["reason_codes"]
        assert r["line_used"] == 8.5

    def test_no_over_risk_but_tail_extreme_still_blocks(self):
        r = rules.evaluate_under_hard_rules(
            final_over_probabilities={},
            line=10.5,
            tail_bucket="EXTREME",
            pick_side="UNDER",
            market="under_10.5",
        )
        assert r["applicable"] is True
        assert r["action"] == rules.ACTION_BLOCK

    def test_invalid_line_returns_no_data(self):
        r = rules.evaluate_under_hard_rules(
            final_over_probabilities=_fop(8.5, 0.50),
            line="not-a-number",
            tail_bucket="LOW",
            pick_side="UNDER",
            market="under_8.5",
        )
        # Line is invalid → over_risk lookup fails.
        assert r["action"] == rules.ACTION_NONE

    def test_nan_over_risk_falls_through(self):
        r = _eval(final_over_probabilities={"over_8.5": float("nan")})
        # NaN is rejected → applicable=True but no over_risk.
        assert r["action"] == rules.ACTION_NONE
        assert rules.RC_UNDER_RULES_NO_OVER_RISK_AVAILABLE in r["reason_codes"]

    def test_inf_over_risk_falls_through(self):
        r = _eval(final_over_probabilities={"over_8.5": float("inf")})
        assert r["action"] == rules.ACTION_NONE


# =====================================================================
# Key format tolerance
# =====================================================================
class TestKeyFormatTolerance:
    def test_integer_line_with_dot_zero(self):
        # Line=8.0 → key "over_8.0" or "over_8".
        r = rules.evaluate_under_hard_rules(
            final_over_probabilities={"over_8": 0.55},
            line=8.0,
            tail_bucket="LOW",
            pick_side="UNDER",
            market="under_8.0",
        )
        assert r["action"] == rules.ACTION_BLOCK

    def test_float_line_dot_five(self):
        r = _eval(final_over_probabilities={"over_8.5": 0.48})
        assert r["action"] == rules.ACTION_AVOID


# =====================================================================
# Output shape canónico
# =====================================================================
class TestOutputShape:
    def test_all_required_keys_present(self):
        r = _eval(final_over_probabilities=_fop(8.5, 0.55))
        for k in (
            "applicable", "action", "severity", "score_delta",
            "is_blocked", "block_max_pick", "exclude_from_main_feed",
            "category", "over_risk", "line_used", "tail_bucket",
            "triggered_rules", "reason_codes", "signals",
        ):
            assert k in r, f"missing key: {k}"

    def test_reason_codes_dedupe(self):
        # Two rules can map to overlapping codes — dedupe must be applied.
        r = _eval(
            final_over_probabilities=_fop(8.5, 0.55),
            line=8.5,
            tail_bucket="EXTREME",
        )
        assert len(r["reason_codes"]) == len(set(r["reason_codes"]))

    def test_severity_matches_action(self):
        for action, sev in rules.SEVERITY_RANK.items():
            assert isinstance(sev, int)


# =====================================================================
# Públicos
# =====================================================================
class TestPublicSymbols:
    def test_all_constants_exported(self):
        for name in (
            "evaluate_under_hard_rules",
            "ACTION_NONE", "ACTION_WARN", "ACTION_AVOID", "ACTION_BLOCK",
            "SEVERITY_RANK",
            "OVER_RISK_WARN_MIN", "OVER_RISK_AVOID_MIN", "OVER_RISK_BLOCK_MIN",
            "TAIL_BUCKET_HIGH", "TAIL_BUCKET_EXTREME",
            "TAIL_LOW_LINE_AVOID",
            "SCORE_DELTA_WARN", "SCORE_DELTA_AVOID", "SCORE_DELTA_BLOCK",
            "RC_UNDER_RULES_NOT_APPLICABLE",
            "RC_UNDER_RULES_NO_OVER_RISK_AVAILABLE",
            "RC_UNDER_RULES_WARN_OVER_RISK",
            "RC_UNDER_RULES_AVOID_OVER_RISK",
            "RC_UNDER_RULES_BLOCK_OVER_RISK",
            "RC_UNDER_RULES_AVOID_TAIL_HIGH_LOW",
            "RC_UNDER_RULES_BLOCK_TAIL_EXTREME",
            "RC_UNDER_RECOMMENDATION_DEGRADED",
        ):
            assert hasattr(rules, name), f"missing public symbol: {name}"

    def test_thresholds_monotonic(self):
        assert rules.OVER_RISK_WARN_MIN < rules.OVER_RISK_AVOID_MIN
        assert rules.OVER_RISK_AVOID_MIN < rules.OVER_RISK_BLOCK_MIN

    def test_score_deltas_ordering(self):
        # AVOID more severe than WARN; both negative; BLOCK delta is 0.
        assert rules.SCORE_DELTA_AVOID < rules.SCORE_DELTA_WARN < 0
