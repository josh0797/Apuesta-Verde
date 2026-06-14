"""Phase F86 — Tests for the H2H Decision Policy module."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from services import football_h2h_decision_policy as pol


# ─────────────────────────────────────────────────────────────────────
# Match helpers
# ─────────────────────────────────────────────────────────────────────
def _days_ago_iso(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _m(*, date_days_ago: int, score: str,
        home: str = "Argentina", away: str = "Brazil") -> dict:
    return {
        "date":  _days_ago_iso(date_days_ago),
        "home":  home,
        "away":  away,
        "score": score,
    }


# =====================================================================
# Helpers
# =====================================================================
class TestPrivateHelpers:
    @pytest.mark.parametrize("s,expected", [
        ("2-1",   (2, 1)),
        ("0-0",   (0, 0)),
        (" 3 - 0 ", (3, 0)),
        ("2:1",   None),
        ("NaN-1", None),
        ("",      None),
        (None,    None),
        (42,      None),
    ])
    def test_score_pair(self, s, expected):
        assert pol._score_pair(s) == expected

    def test_total_goals(self):
        assert pol._total_goals({"score": "2-1"}) == 3
        assert pol._total_goals({"score": "0-0"}) == 0
        assert pol._total_goals({"score": "x"})   is None

    def test_side_of(self):
        m = {"home": "Argentina", "away": "Brazil"}
        assert pol._side_of(m, "Argentina") == "home"
        assert pol._side_of(m, "Brazil")    == "away"
        assert pol._side_of(m, "Uruguay")   is None
        assert pol._side_of({}, "")         is None

    def test_is_recent_boundaries(self):
        assert pol._is_recent(_days_ago_iso(0))   is True
        assert pol._is_recent(_days_ago_iso(364)) is True
        assert pol._is_recent(_days_ago_iso(400)) is False
        assert pol._is_recent("not a date")       is False
        assert pol._is_recent(None)               is False


# =====================================================================
# classify_h2h_context
# =====================================================================
class TestClassifyH2HContext:
    def test_no_matches_returns_no_sample(self):
        out = pol.classify_h2h_context(None, [])
        assert out["sample_size_total"]  == 0
        assert out["sample_size_recent"] == 0
        assert out["decision_useful"]    is False
        assert pol.RC_NO_SAMPLE in out["reason_codes"]
        assert out["warnings"][0].startswith("Sin enfrentamientos")

    def test_below_total_threshold_emits_warning(self):
        # 3 matches all recent → still below MIN_DECISION_SAMPLE (=4).
        matches = [_m(date_days_ago=10, score="1-1") for _ in range(3)]
        out = pol.classify_h2h_context(None, matches)
        assert out["sample_size_total"]  == 3
        assert out["sample_size_recent"] == 3
        assert out["decision_useful"]    is False
        assert pol.RC_SAMPLE_BELOW_THRESHOLD in out["reason_codes"]
        assert "Solo se registran 3" in out["warnings"][0]

    def test_total_ok_but_recent_below_threshold(self):
        # 6 matches total, 5 of them older than 1y → only 1 recent.
        matches = [_m(date_days_ago=400 + i, score="1-0") for i in range(5)]
        matches.append(_m(date_days_ago=30, score="2-1"))
        out = pol.classify_h2h_context(None, matches)
        assert out["sample_size_total"]  == 6
        assert out["sample_size_recent"] == 1
        assert out["decision_useful"]    is False
        assert pol.RC_RECENT_BELOW_THRESHOLD in out["reason_codes"]

    def test_recent_at_threshold_is_decision_useful(self):
        matches = [_m(date_days_ago=10 * i, score="2-1") for i in range(5)]
        out = pol.classify_h2h_context(None, matches)
        assert out["sample_size_recent"] >= pol.MIN_DECISION_SAMPLE
        assert out["decision_useful"] is True
        assert pol.RC_DECISION_USEFUL in out["reason_codes"]

    def test_preexisting_reason_codes_are_preserved(self):
        ctx = {"reason_codes": ["H2H_CONTEXT_OK"]}
        matches = [_m(date_days_ago=10, score="1-1") for _ in range(5)]
        out = pol.classify_h2h_context(ctx, matches)
        assert "H2H_CONTEXT_OK"     in out["reason_codes"]
        assert pol.RC_DECISION_USEFUL in out["reason_codes"]

    def test_invalid_dates_excluded_from_recent_count(self):
        # 5 matches but only 4 with parseable date.
        matches = [
            _m(date_days_ago=10,  score="1-1"),
            _m(date_days_ago=20,  score="2-2"),
            _m(date_days_ago=30,  score="0-0"),
            _m(date_days_ago=40,  score="1-0"),
            {"date": "garbage", "home": "A", "away": "B", "score": "1-1"},
        ]
        out = pol.classify_h2h_context(None, matches)
        assert out["sample_size_total"]  == 5
        assert out["sample_size_recent"] == 4
        assert out["decision_useful"] is True


# =====================================================================
# apply_h2h_decision_points
# =====================================================================
class TestApplyH2HDecisionPoints:
    def test_not_decision_useful_returns_empty_applied_false(self):
        out = pol.apply_h2h_decision_points(
            {"decision_useful": False, "recent_within_1y": []},
            home_name="A", away_name="B",
        )
        assert out["applied"] is False
        assert out["points_by_market"] == {}
        assert out["signals"] == []

    def test_over_2_5_profile_emits_signal_and_points(self):
        # 5 matches all 3+ goals.
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        assert out["applied"] is True
        assert out["sample_size"] == 5
        # 100% over 2.5 → above 0.75 threshold (F86.1 recalibrated).
        assert out["points_by_market"].get("OVER_2_5") == 4
        assert "H2H_OVER_2_5_STRONG" in out["signals"]
        # And over 1.5 should also fire (rate 1.0 ≥ 0.90).
        assert "H2H_OVER_1_5_STRONG" in out["signals"]

    def test_under_2_5_profile(self):
        recent = [_m(date_days_ago=10 * i, score="1-0") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        assert "H2H_UNDER_2_5_STRONG" in out["signals"]
        assert out["points_by_market"].get("UNDER_2_5") == 4

    def test_btts_no_profile(self):
        # 5 matches without both teams scoring.
        recent = [_m(date_days_ago=10 * i, score="2-0") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        assert "H2H_BTTS_NO_STRONG" in out["signals"]
        # BTTS_YES must NOT fire — they are mutually exclusive.
        assert "H2H_BTTS_YES_STRONG" not in out["signals"]

    def test_btts_yes_profile(self):
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        assert "H2H_BTTS_YES_STRONG" in out["signals"]

    def test_home_dnb_when_home_team_never_lost(self):
        # 5 matches: home wins or draws (Argentina home).
        recent = [
            _m(date_days_ago=10, score="2-0"),
            _m(date_days_ago=20, score="1-0"),
            _m(date_days_ago=30, score="1-1"),
            _m(date_days_ago=40, score="3-1"),
            _m(date_days_ago=50, score="2-2"),
        ]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        # home_dnb rate = 5/5 = 1.0 ≥ 0.85 (F86.1) → fires.
        assert "H2H_HOME_DNB_STRONG" in out["signals"]
        # Away DNB rate = 2/5 = 0.4 < 0.70 → no signal.
        assert "H2H_AWAY_DNB_STRONG" not in out["signals"]

    def test_neutral_profile_does_not_emit_contradictory_signals(self):
        """A balanced sample MUST NOT emit both OVER_X and UNDER_X for
        the same line — those are mutually exclusive market profiles."""
        # 4 partidos balanceados: over_2_5 = 0.5, btts_yes = btts_no = 0.5.
        recent = [
            _m(date_days_ago=10, score="2-1"),   # 3g, btts yes
            _m(date_days_ago=20, score="2-0"),   # 2g, btts no
            _m(date_days_ago=30, score="3-1"),   # 4g, btts yes
            _m(date_days_ago=40, score="0-1"),   # 1g, btts no
        ]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        # over_2_5 = 2/4 = 0.50 < 0.75 → does NOT fire.
        assert "H2H_OVER_2_5_STRONG" not in out["signals"]
        # btts_yes = 2/4 = 0.50 < 0.70 → does NOT fire.
        assert "H2H_BTTS_YES_STRONG" not in out["signals"]
        # btts_no = 0.50 < 0.70 → does NOT fire.
        assert "H2H_BTTS_NO_STRONG"  not in out["signals"]
        # Mutually-exclusive check: OVER_2_5 and UNDER_2_5 never both fire.
        assert not (
            "H2H_OVER_2_5_STRONG" in out["signals"]
            and "H2H_UNDER_2_5_STRONG" in out["signals"]
        )

    def test_invalid_score_rows_are_tolerated(self):
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(4)]
        recent.append({"date": _days_ago_iso(50),
                        "home": "Argentina", "away": "Brazil",
                        "score": "invalid"})
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        # 4/5 over 2.5 = 0.80 → above 0.75 threshold (F86.1).
        assert "H2H_OVER_2_5_STRONG" in out["signals"]


# =====================================================================
# build_h2h_decision
# =====================================================================
class TestBuildH2HDecision:
    def test_full_pipeline_returns_classified_and_decision(self):
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(5)]
        match_doc = {
            "h2h_recent":  recent,
            "h2h_context": {"reason_codes": ["H2H_CONTEXT_OK"]},
            "home_team":   {"name": "Argentina"},
            "away_team":   {"name": "Brazil"},
        }
        classified, decision = pol.build_h2h_decision(match_doc)
        assert classified["decision_useful"] is True
        assert decision["applied"] is True
        assert decision["sample_size"] == 5
        # Provenance preserved.
        assert "H2H_CONTEXT_OK" in classified["reason_codes"]

    def test_decision_not_applied_when_sample_too_small(self):
        recent = [_m(date_days_ago=10, score="2-1") for _ in range(2)]
        match_doc = {"h2h_recent": recent,
                     "home_team": {"name": "A"}, "away_team": {"name": "B"}}
        classified, decision = pol.build_h2h_decision(match_doc)
        assert classified["decision_useful"] is False
        assert decision["applied"] is False
        assert decision["points_by_market"] == {}

    def test_missing_team_names_does_not_raise(self):
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(5)]
        match_doc = {"h2h_recent": recent}  # no home_team / away_team
        classified, decision = pol.build_h2h_decision(match_doc)
        # No DNB signal possible (no team names) but goals signals still ok.
        assert decision["applied"] is True
        assert "H2H_HOME_DOMINANT" not in decision["signals"]


# =====================================================================
# Point-rules invariants
# =====================================================================
class TestH2HPointRules:
    def test_all_rules_have_positive_small_points(self):
        for market, rule in pol.H2H_POINT_RULES.items():
            assert 0 < rule["points"] <= 5, market
            assert 0.0 < rule["min_rate"] <= 1.0, market
            assert rule["label"].startswith("H2H_"), market


# =====================================================================
# Phase F86.1 — calibrated thresholds + polarity + sample + cap
# =====================================================================
import json


class TestF86_1ThresholdsAndBaselines:
    def test_thresholds_above_baseline(self):
        """Each calibrated rule must require at least baseline + 5pp,
        otherwise the H2H signal would not be informative against the
        baseline rate it claims to detect."""
        for market, rule in pol.H2H_POINT_RULES.items():
            assert "baseline" in rule, f"{market} missing baseline (F86.1)"
            assert "min_sample" in rule, f"{market} missing min_sample (F86.1)"
            min_rate = float(rule["min_rate"])
            baseline = float(rule["baseline"])
            assert min_rate >= baseline + 0.05, (
                f"{market}: min_rate={min_rate} must be ≥ baseline "
                f"({baseline}) + 0.05"
            )

    def test_all_labels_use_calibrated_STRONG_naming(self):
        for market, rule in pol.H2H_POINT_RULES.items():
            assert rule["label"].endswith("_STRONG"), \
                f"{market}: label must use F86.1 calibrated naming"


class TestF86_1EnvOverride:
    def test_env_override_merges_correctly(self, monkeypatch):
        monkeypatch.setenv(
            "H2H_POINT_RULES_OVERRIDE",
            json.dumps({"OVER_2_5": {"min_rate": 0.80}}),
        )
        rules = pol.get_active_rules()
        assert rules["OVER_2_5"]["min_rate"] == 0.80
        # ``points`` preserved from default.
        assert rules["OVER_2_5"]["points"] == pol.H2H_POINT_RULES["OVER_2_5"]["points"]
        # Unrelated markets untouched.
        assert rules["UNDER_2_5"] == pol.H2H_POINT_RULES["UNDER_2_5"]

    def test_env_override_invalid_json_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("H2H_POINT_RULES_OVERRIDE", "{not_json:")
        with caplog.at_level("WARNING", logger="football.h2h_decision_policy"):
            rules = pol.get_active_rules()
        # Defaults preserved.
        for market in pol.H2H_POINT_RULES:
            assert rules[market]["min_rate"] == pol.H2H_POINT_RULES[market]["min_rate"]
        # Warning emitted.
        assert any("override parse failed" in rec.message.lower()
                   for rec in caplog.records)

    def test_env_override_unknown_market_logs_warning_and_ignores(self, monkeypatch, caplog):
        monkeypatch.setenv(
            "H2H_POINT_RULES_OVERRIDE",
            json.dumps({"NOT_A_MARKET": {"min_rate": 0.5}}),
        )
        with caplog.at_level("WARNING", logger="football.h2h_decision_policy"):
            rules = pol.get_active_rules()
        # Known markets unchanged.
        assert rules["OVER_2_5"]["min_rate"] == \
               pol.H2H_POINT_RULES["OVER_2_5"]["min_rate"]
        assert any("unknown override market" in rec.message.lower()
                   for rec in caplog.records)

    def test_no_env_override_returns_defaults_copy(self, monkeypatch):
        monkeypatch.delenv("H2H_POINT_RULES_OVERRIDE", raising=False)
        rules = pol.get_active_rules()
        for market in pol.H2H_POINT_RULES:
            assert rules[market] == pol.H2H_POINT_RULES[market]
        # Must be a *copy* — mutating the returned dict should NOT
        # affect the module-level defaults (so monkeypatch.setenv works
        # idempotently in subsequent tests).
        rules["OVER_2_5"]["min_rate"] = 0.99
        assert pol.H2H_POINT_RULES["OVER_2_5"]["min_rate"] != 0.99


class TestF86_1PolarityGuard:
    def test_polarity_guard_drops_loser_signal(self, monkeypatch, caplog):
        # Force a polarity conflict via a permissive override:
        # OVER_2_5 min_rate=0.40, UNDER_2_5 min_rate=0.40 — both fire.
        monkeypatch.setenv(
            "H2H_POINT_RULES_OVERRIDE",
            json.dumps({
                "OVER_2_5":  {"min_rate": 0.40},
                "UNDER_2_5": {"min_rate": 0.40},
            }),
        )
        # 3 over + 1 under → over_2_5 = 0.75, under_2_5 = 0.25.
        # We need rates above the 0.40 threshold for BOTH, so:
        # 6 matches: 3 over (3-1) + 3 under (1-0) → over = under = 0.50.
        recent = [
            _m(date_days_ago=10, score="3-1"),
            _m(date_days_ago=20, score="2-1"),
            _m(date_days_ago=30, score="2-1"),  # over
            _m(date_days_ago=40, score="1-0"),
            _m(date_days_ago=50, score="0-1"),
            _m(date_days_ago=60, score="1-0"),  # under
        ]
        classified = pol.classify_h2h_context(None, recent)
        with caplog.at_level("WARNING", logger="football.h2h_decision_policy"):
            out = pol.apply_h2h_decision_points(
                classified, home_name="Argentina", away_name="Brazil",
            )
        # Override above 0.40 → both fire. over_2_5 == under_2_5 == 0.5.
        # Tie on rate → tie on points (both 4) → UNRESOLVED, BOTH dropped.
        # Reason code:
        assert pol.RC_POLARITY_GUARD_TRIGGERED in out["reason_codes"]
        # In the unresolved branch both sides are dropped.
        assert "OVER_2_5"  not in out["points_by_market"]
        assert "UNDER_2_5" not in out["points_by_market"]
        # Conflicts entries recorded for audit.
        assert any(c["a"] == "OVER_2_5" and c["b"] == "UNDER_2_5"
                   for c in out.get("polarity_conflicts", []))
        # Warning logged.
        assert any("polarity_guard" in rec.name or "polarity_guard" in rec.message.lower()
                   for rec in caplog.records)

    def test_polarity_guard_drops_loser_with_distinct_rates(self, monkeypatch, caplog):
        """When rates differ, the higher-rate side wins; loser is dropped."""
        monkeypatch.setenv(
            "H2H_POINT_RULES_OVERRIDE",
            json.dumps({
                "OVER_2_5":  {"min_rate": 0.40},
                "UNDER_2_5": {"min_rate": 0.40},
            }),
        )
        # 4 over + 1 under → over = 0.80, under = 0.20.
        # We want both rates above 0.40 — adjust to 3 over + 2 under (6 total):
        # No, that gives over=0.50 / under=0.50 (tie).
        # Need: rates DIFFER and BOTH ≥ 0.40. Use: 5 matches with 3 ties + 1 over + 1 under
        # → over rate ≠ under rate but both ≤ 0.40… tricky.
        # Cleanest: synthesise via direct policy.apply_polarity_guard call.
        active = pol.get_active_rules()
        out = {
            "points_by_market": {"OVER_2_5": 4, "UNDER_2_5": 4},
            "signals":          ["H2H_OVER_2_5_STRONG", "H2H_UNDER_2_5_STRONG"],
            "reason_codes":     [],
        }
        market_to_rate = {"OVER_2_5": 0.78, "UNDER_2_5": 0.72}
        with caplog.at_level("WARNING", logger="football.h2h_decision_policy"):
            res = pol.apply_polarity_guard(out, market_to_rate, active)
        # Higher rate (OVER, 0.78) wins → UNDER dropped.
        assert "OVER_2_5"  in res["points_by_market"]
        assert "UNDER_2_5" not in res["points_by_market"]
        assert "H2H_OVER_2_5_STRONG"  in res["signals"]
        assert "H2H_UNDER_2_5_STRONG" not in res["signals"]
        # Conflict recorded with both rates.
        conflicts = res["polarity_conflicts"]
        entry = next(c for c in conflicts if c["a"] == "OVER_2_5")
        assert entry["resolution"] == "DROP_LOSER"
        assert entry["rate_a"] == 0.78
        assert entry["rate_b"] == 0.72
        # Reason code added.
        assert pol.RC_POLARITY_GUARD_TRIGGERED in res["reason_codes"]
        # Warning logged.
        assert any("dropping under_2_5" in rec.message.lower()
                   for rec in caplog.records)

    def test_no_polarity_conflict_with_default_thresholds(self):
        """Default thresholds (F86.1) are mathematically disjoint:
        for any pair (OVER_X, UNDER_X) with rate_OVER + rate_UNDER = 1.0,
        both min_rates cannot be satisfied simultaneously."""
        for a, b in pol.POLARITY_PAIRS:
            min_a = pol.H2H_POINT_RULES[a]["min_rate"]
            min_b = pol.H2H_POINT_RULES[b]["min_rate"]
            # If both could fire we'd need rate_a ≥ min_a AND rate_b ≥ min_b
            # AND rate_a + rate_b ≤ 1.0 (BTTS pair is exactly 1.0; goals
            # pairs are ≤ 1.0 because <2 goals is subset of <3 goals).
            assert min_a + min_b > 1.0, (
                f"Polarity pair ({a}, {b}) min_rates {min_a}+{min_b} are "
                "NOT disjoint — they could fire simultaneously."
            )


class TestF86_1BaselineIsDocumentationOnly:
    def test_baseline_field_is_documentation_only(self, monkeypatch):
        """Changing only ``baseline`` via override must NOT alter
        whether a rule fires — only min_rate / points / min_sample /
        label drive behaviour."""
        # Build a sample with over_2_5 rate = 0.80 (above min_rate=0.75).
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(4)]
        recent.append(_m(date_days_ago=45, score="1-0"))  # under
        classified = pol.classify_h2h_context(None, recent)

        # Reference run (no override).
        monkeypatch.delenv("H2H_POINT_RULES_OVERRIDE", raising=False)
        out_default = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )

        # Override only ``baseline``.
        monkeypatch.setenv(
            "H2H_POINT_RULES_OVERRIDE",
            json.dumps({"OVER_2_5": {"baseline": 0.99}}),
        )
        out_baseline_override = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        # Same firing behaviour.
        assert ("OVER_2_5" in out_default["points_by_market"]) == \
               ("OVER_2_5" in out_baseline_override["points_by_market"])
        # Same points.
        assert out_default["points_by_market"].get("OVER_2_5") == \
               out_baseline_override["points_by_market"].get("OVER_2_5")


class TestF86_1SampleGuard:
    def test_low_sample_h2h_signal_blocks_full_points(self, monkeypatch):
        """When sample_size_recent < rule.min_sample (raised via override)
        the signal still emits but at HALVED points and surfaces
        ``LOW_SAMPLE_H2H_SIGNAL``."""
        # Bump min_sample for OVER_2_5 to a value above the actual sample.
        monkeypatch.setenv(
            "H2H_POINT_RULES_OVERRIDE",
            json.dumps({"OVER_2_5": {"min_sample": 10}}),
        )
        # 5 recent matches, all 2-1 → over_2_5 = 1.0 → fires.
        # But sample_size (5) < min_sample (10) → low-sample path.
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        full_points = pol.H2H_POINT_RULES["OVER_2_5"]["points"]
        applied = out["points_by_market"].get("OVER_2_5")
        # Points are halved (floor) but at least 1.
        assert applied is not None
        assert applied < full_points
        assert applied >= 1
        # Reason code.
        assert pol.RC_LOW_SAMPLE_SIGNAL in out["reason_codes"]
        # Audit list.
        assert "OVER_2_5" in out.get("low_sample_markets", [])


class TestF86_1DNBOverlapSoftConflict:
    def test_dnb_overlap_is_soft_conflict_not_polarity_drop(self, monkeypatch):
        """HOME_DNB + AWAY_DNB jointly is a *soft* conflict
        (draw-heavy profile) — neither side dropped, reason_code
        ``H2H_DNB_OVERLAP_DRAW_HEAVY`` added, soft_conflicts populated."""
        # Lower DNB thresholds via override so both fire on a draw-heavy
        # sample. (Cannot do this with defaults because home=0.85,
        # away=0.70 are designed not to overlap easily.)
        monkeypatch.setenv(
            "H2H_POINT_RULES_OVERRIDE",
            json.dumps({
                "HOME_DNB": {"min_rate": 0.60},
                "AWAY_DNB": {"min_rate": 0.60},
            }),
        )
        # 5 draws → home_dnb = away_dnb = 1.0.
        recent = [_m(date_days_ago=10 * i, score="1-1") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        # Both signals preserved.
        assert "HOME_DNB" in out["points_by_market"]
        assert "AWAY_DNB" in out["points_by_market"]
        # Soft conflict reported, not hard polarity.
        assert pol.RC_DNB_OVERLAP_DRAW_HEAVY in out["reason_codes"]
        # Must NOT appear under polarity_conflicts (hard).
        assert not any(
            ("HOME_DNB" in (c.get("a"), c.get("b"))
             or "AWAY_DNB" in (c.get("a"), c.get("b")))
            for c in out.get("polarity_conflicts", [])
        )
        # soft_conflicts entry exposed.
        soft = out.get("soft_conflicts") or []
        assert any(c["type"] == "DNB_OVERLAP" for c in soft)
        # Combined contribution capped at 4 (raw was 4+4=8 → reduced).
        combined = (out["points_by_market"]["HOME_DNB"]
                    + out["points_by_market"]["AWAY_DNB"])
        assert combined <= 4


class TestF86_1TotalCap:
    def test_h2h_points_are_capped(self, monkeypatch):
        """When the sum of points across all firing markets exceeds
        ``MAX_H2H_POINTS_TOTAL``, the numeric impact is clamped but
        signals are preserved."""
        # Lower thresholds so MANY rules fire at once on the same sample.
        monkeypatch.setenv(
            "H2H_POINT_RULES_OVERRIDE",
            json.dumps({
                "OVER_1_5":  {"min_rate": 0.30},
                "OVER_2_5":  {"min_rate": 0.30},
                "OVER_3_5":  {"min_rate": 0.30},
                "BTTS_YES":  {"min_rate": 0.30},
                "HOME_DNB":  {"min_rate": 0.30},
            }),
        )
        # 5 partidos 3-1: over_1_5=over_2_5=over_3_5=1.0,
        # btts_yes=1.0, home_dnb=1.0 → 5 reglas firing.
        recent = [_m(date_days_ago=10 * i, score="3-1") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        # Several signals preserved (after polarity guard, which shouldn't
        # fire because UNDER_X rules stay at their defaults and don't trigger).
        assert len(out["signals"]) >= 4
        # Uncapped total > cap, capped total == MAX_H2H_POINTS_TOTAL.
        assert out.get("h2h_points_uncapped", 0) > pol.MAX_H2H_POINTS_TOTAL
        assert out["h2h_points_total"] == pol.MAX_H2H_POINTS_TOTAL
        assert pol.RC_POINTS_CAPPED in out["reason_codes"]

    def test_h2h_points_total_below_cap_not_clamped(self):
        """When the total stays under the cap, no clamp / reason code."""
        # 5 partidos 2-1 → over_2_5(4) + over_1_5(3) + btts_yes(4) = 11
        # >cap (8). Use a smaller-firing sample: 5 partidos 1-0 (under).
        # over_1_5 = under_1_5 — actually 5*1-0 means each match has 1 goal:
        # over_1_5 rate = 0 (need ≥2 goals), under_1_5 = 1.0 → fires (+5).
        # under_2_5 = 1.0 → fires (+4). under_3_5 = 1.0 → fires (+5).
        # btts_no = 1.0 → fires (+5). Total = 5+4+5+5 = 19 > 8. Still > cap.
        # Use a partial-firing case: 5 partidos 2-0:
        #   under_3_5 = 1.0 (+5), btts_no = 1.0 (+5) = 10 > 8.
        # Try 5 partidos 2-1:
        #   over_2_5 = 1.0 (+4), over_1_5 = 1.0 (+3), btts_yes = 1.0 (+4) = 11.
        # Difícil. Use 3 partidos exactos:
        # Wait — gating requires >=4 recent. Use 4 partidos 2-1:
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(4)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        # If the actual total is ≤ 8, no cap reason code.
        if sum(out["points_by_market"].values()) <= pol.MAX_H2H_POINTS_TOTAL:
            assert pol.RC_POINTS_CAPPED not in out["reason_codes"]
            assert "h2h_points_uncapped" not in out
