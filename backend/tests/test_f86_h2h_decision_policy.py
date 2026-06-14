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
        # 100% over 2.5 → above 0.70 threshold.
        assert out["points_by_market"].get("OVER_2_5") == 4
        assert "H2H_PROFILE_OVER_2_5" in out["signals"]
        # And over 1.5 should also fire.
        assert "H2H_PROFILE_OVER_1_5" in out["signals"]

    def test_under_2_5_profile(self):
        recent = [_m(date_days_ago=10 * i, score="1-0") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        assert "H2H_PROFILE_UNDER_2_5" in out["signals"]
        assert out["points_by_market"].get("UNDER_2_5") == 4

    def test_btts_no_profile(self):
        # 5 matches without both teams scoring.
        recent = [_m(date_days_ago=10 * i, score="2-0") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        assert "H2H_PROFILE_BTTS_NO" in out["signals"]
        # BTTS_YES must NOT fire — they are mutually exclusive.
        assert "H2H_PROFILE_BTTS_YES" not in out["signals"]

    def test_btts_yes_profile(self):
        recent = [_m(date_days_ago=10 * i, score="2-1") for i in range(5)]
        classified = pol.classify_h2h_context(None, recent)
        out = pol.apply_h2h_decision_points(
            classified, home_name="Argentina", away_name="Brazil",
        )
        assert "H2H_PROFILE_BTTS_YES" in out["signals"]

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
        assert "H2H_HOME_DOMINANT" in out["signals"]
        # Away DNB rate must be lower → no signal.
        assert "H2H_AWAY_DOMINANT" not in out["signals"]

    def test_neutral_profile_does_not_emit_contradictory_signals(self):
        """A balanced sample MUST NOT emit both OVER_X and UNDER_X for
        the same line — those are mutually exclusive market profiles."""
        # 4 partidos con scores que dan over_2_5 = under_2_5 = 0.5 (justo
        # debajo de ambos thresholds), btts_yes = btts_no = 0.5.
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
        # over_2_5 = 2/4 = 0.50 < 0.70 → does NOT fire.
        assert "H2H_PROFILE_OVER_2_5" not in out["signals"]
        # btts_yes = 2/4 = 0.50 < 0.60 → does NOT fire.
        assert "H2H_PROFILE_BTTS_YES" not in out["signals"]
        # btts_no = 0.50 < 0.70 → does NOT fire.
        assert "H2H_PROFILE_BTTS_NO"  not in out["signals"]
        # Mutually-exclusive check: OVER_2_5 and UNDER_2_5 never both fire.
        assert not (
            "H2H_PROFILE_OVER_2_5" in out["signals"]
            and "H2H_PROFILE_UNDER_2_5" in out["signals"]
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
        # 4/5 over 2.5 = 0.80 → above 0.70 threshold.
        assert "H2H_PROFILE_OVER_2_5" in out["signals"]


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
