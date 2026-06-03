"""Tests for the new 2026-06 StatMuse + F5 + NRFI/YRFI + team-total
extensions.

Coverage:
  - StatMuse HTML parser → list[dict] with canonical keys.
  - StatMuse team-row matching with loose token sets.
  - Cross-validation report (>10% threshold).
  - F5 / NRFI / YRFI evaluators in mlb_trend_interpreter.
  - Team-total evaluator with per-side data.
"""
import pytest

from services.statmuse_recent_form import (
    _parse_table_html,
    _row_to_team,
    _normalise_team_name,
    find_team_row,
    compare_forms,
    _TABLE_CACHE,
)
from services.mlb_trend_interpreter import (
    combine_trend_signals,
    _detect_market_kind,
    _evaluate_f5,
    _evaluate_team_total,
    _evaluate_nrfi_yrfi,
)
from services.mlb_recent_form_split import (
    _build_f5_split,
    _build_first_inning_split,
)


@pytest.fixture(autouse=True)
def _clear_statmuse_cache():
    _TABLE_CACHE.clear()
    yield
    _TABLE_CACHE.clear()


# ── StatMuse parser ───────────────────────────────────────────────────
class TestStatMuseParser:
    def test_parse_simple_team_table(self):
        html = """
        <html><body>
          <table>
            <thead>
              <tr><th>TEAM</th><th>G</th><th>R</th><th>H</th><th>BB</th><th>HR</th><th>OBP</th></tr>
            </thead>
            <tbody>
              <tr><td>1. New York Yankees</td><td>15</td><td>5.1</td><td>8.7</td><td>3.2</td><td>1.4</td><td>.328</td></tr>
              <tr><td>2. Boston Red Sox (10-5)</td><td>15</td><td>4.8</td><td>8.3</td><td>3.0</td><td>1.1</td><td>.315</td></tr>
            </tbody>
          </table>
        </body></html>
        """
        rows = _parse_table_html(html)
        assert len(rows) == 2
        assert rows[0]["team"] == "New York Yankees"
        assert rows[0]["R"] == 5.1
        assert rows[0]["H"] == 8.7
        assert rows[0]["BB"] == 3.2
        assert rows[0]["HR"] == 1.4
        assert rows[0]["OBP"] == 0.328
        assert rows[1]["team"] == "Boston Red Sox"   # parenthetical stripped
        assert rows[1]["R"] == 4.8

    def test_normalise_team_name_strips_rank_and_record(self):
        assert _normalise_team_name("1. Yankees") == "Yankees"
        assert _normalise_team_name("12. Red Sox (8-7)") == "Red Sox"
        assert _normalise_team_name("  Dodgers  ") == "Dodgers"

    def test_row_to_team_ignores_unknown_columns(self):
        # An unknown column ("FOO") should be silently ignored.
        headers = ["TEAM", "FOO", "R"]
        row = ["NY Mets", "bar", "4.2"]
        out = _row_to_team(headers, row)
        assert out == {"team": "NY Mets", "R": 4.2}

    def test_parser_handles_empty_table(self):
        assert _parse_table_html("<html><body><div>no tables</div></body></html>") == []
        assert _parse_table_html("") == []

    def test_find_team_row_token_match(self):
        rows = [
            {"team": "New York Yankees", "R": 5.1},
            {"team": "Boston Red Sox",   "R": 4.8},
            {"team": "Los Angeles Dodgers", "R": 5.3},
        ]
        # Different short forms should match.
        assert find_team_row(rows, "Yankees")["team"] == "New York Yankees"
        assert find_team_row(rows, "NY Yankees")["team"] == "New York Yankees"
        assert find_team_row(rows, "Dodgers")["team"] == "Los Angeles Dodgers"
        # No match → None.
        assert find_team_row(rows, "Toronto Blue Jays") is None


# ── Cross-validation report ──────────────────────────────────────────
class TestCompareForms:
    def test_match_when_within_threshold(self):
        primary  = {"runs_scored_avg_last_5": 5.0, "hits_avg_last_15": 8.0}
        secondary = {"runs_scored_avg_last_5": 5.2, "hits_avg_last_15": 7.9}
        rpt = compare_forms(primary, secondary, threshold_pct=10.0)
        assert rpt["match"] is True
        assert rpt["issues"] == []

    def test_discrepancy_detected_when_over_threshold(self):
        primary   = {"runs_scored_avg_last_5": 5.0}
        secondary = {"runs_scored_avg_last_5": 7.0}   # 40% gap
        rpt = compare_forms(primary, secondary, threshold_pct=10.0)
        assert rpt["match"] is False
        assert len(rpt["issues"]) == 1
        issue = rpt["issues"][0]
        assert issue["metric"] == "runs_scored_avg_last_5"
        assert issue["diff_pct"] > 10
        assert issue["primary"] == 5.0
        assert issue["secondary"] == 7.0

    def test_missing_metrics_are_ignored(self):
        primary   = {"runs_scored_avg_last_5": 5.0}
        secondary = {"hits_avg_last_5": 9.0}
        rpt = compare_forms(primary, secondary)
        assert rpt["match"] is True
        assert rpt["issues"] == []


# ── Market kind detection ────────────────────────────────────────────
class TestMarketKindDetection:
    @pytest.mark.parametrize("market,expected", [
        ("NRFI", "nrfi"),
        ("YRFI", "yrfi"),
        ("No Run First Inning", "nrfi"),
        ("Yes Run First Inning", "yrfi"),
        ("F5 Over 4.5", "totals_f5"),
        ("1st 5 innings Under 5", "totals_f5"),
        ("First 5 innings total under", "totals_f5"),
        ("Yankees team total Over 4.5", "team_total"),
        ("Total equipo home over 5.5", "team_total"),
        ("Runline +1.5 home", "runline_plus_15"),
        ("Run line +1,5 away", "runline_plus_15"),
        ("Under 9.5", "totals_full"),
        ("Over 8.5", "totals_full"),
        ("Más de 8.5", "totals_full"),
        ("Menos de 9.5", "totals_full"),
    ])
    def test_detection(self, market, expected):
        assert _detect_market_kind(market) == expected


# ── F5 split builder ─────────────────────────────────────────────────
class TestF5SplitBuilder:
    def test_combined_delta_computed(self):
        home_form = {"f5_runs_avg_last_5": 3.0, "f5_runs_avg_last_15": 2.0}
        away_form = {"f5_runs_avg_last_5": 2.5, "f5_runs_avg_last_15": 2.0}
        out = _build_f5_split(home_form, away_form)
        assert out["combined"]["f5_runs_avg_last_5"]  == 5.5
        assert out["combined"]["f5_runs_avg_last_15"] == 4.0
        assert out["combined"]["f5_runs_delta_5_vs_15"] == 1.5
        assert out["combined"]["trend"] == "RISING_RUN_ENVIRONMENT"

    def test_returns_none_when_missing_data(self):
        out = _build_f5_split({}, {})
        assert out["combined"]["f5_runs_avg_last_5"] is None
        assert out["combined"]["f5_runs_delta_5_vs_15"] is None


# ── First-inning split builder ───────────────────────────────────────
class TestFirstInningSplitBuilder:
    def test_yrfi_rate_via_union_probability(self):
        # p(home scores)=0.4, p(away scores)=0.5
        # YRFI = 1 - (1-0.4)(1-0.5) = 1 - 0.3 = 0.7
        home_form = {
            "first_inning_runs_avg_last_5": 0.5, "first_inning_runs_avg_last_15": 0.4,
            "first_inning_scored_rate_last_5": 0.4, "first_inning_scored_rate_last_15": 0.4,
        }
        away_form = {
            "first_inning_runs_avg_last_5": 0.6, "first_inning_runs_avg_last_15": 0.5,
            "first_inning_scored_rate_last_5": 0.5, "first_inning_scored_rate_last_15": 0.5,
        }
        out = _build_first_inning_split(home_form, away_form)
        assert out["combined"]["yrfi_rate_last_15"] == 0.7
        assert out["combined"]["nrfi_rate_last_15"] == 0.3


# ── F5 evaluator ────────────────────────────────────────────────────
class TestF5Evaluator:
    def _split(self, delta, l5=4.0, l15=3.5):
        return {"combined": {
            "f5_runs_delta_5_vs_15": delta,
            "f5_runs_avg_last_5": l5,
            "f5_runs_avg_last_15": l15,
            "trend": "STABLE_RUN_ENVIRONMENT",
        }}

    def test_f5_under_with_rising_runs_drops_score(self):
        out = _evaluate_f5(
            f5_split=self._split(1.0),
            market="F5 Under 4.5",
            trend_decision="NEUTRAL",
        )
        assert "F5_RUN_ENV_RISING_VS_UNDER" in out["reason_codes"]
        assert out["adjustment"] <= -10

    def test_f5_over_with_declining_runs_drops_score(self):
        out = _evaluate_f5(
            f5_split=self._split(-1.0),
            market="F5 Over 4.5",
            trend_decision="NEUTRAL",
        )
        assert "F5_RUN_ENV_DECLINING_VS_OVER" in out["reason_codes"]
        assert out["adjustment"] <= -10

    def test_f5_under_with_declining_runs_supports(self):
        out = _evaluate_f5(
            f5_split=self._split(-1.0),
            market="F5 Under 4.5",
            trend_decision="NEUTRAL",
        )
        assert "F5_RUN_ENV_DECLINING_VS_UNDER" in out["reason_codes"]
        assert out["adjustment"] >= 6

    def test_f5_no_data(self):
        out = _evaluate_f5(f5_split=None, market="F5 Over 4.5", trend_decision="NEUTRAL")
        assert "F5_NO_DATA" in out["reason_codes"]
        assert out["adjustment"] == 0


# ── Team total evaluator ─────────────────────────────────────────────
class TestTeamTotalEvaluator:
    def _sides(self, home_tob=0.0, away_tob=0.0):
        return (
            {"tob_delta": home_tob, "home_runs_trend": "stable"},
            {"tob_delta": away_tob, "home_runs_trend": "stable"},
        )

    def test_over_when_team_trending_up(self):
        h, a = self._sides(home_tob=2.0)
        out = _evaluate_team_total(
            home_side=h, away_side=a,
            home_form_runs=1.5, away_form_runs=0.0,
            team_side="home", market="Yankees team total Over 4.5",
        )
        assert "TEAM_TOTAL_TREND_SUPPORTS_OVER" in out["reason_codes"]
        assert out["adjustment"] >= 6

    def test_under_when_team_trending_down(self):
        h, a = self._sides(away_tob=-2.0)
        out = _evaluate_team_total(
            home_side=h, away_side=a,
            home_form_runs=0.0, away_form_runs=-1.5,
            team_side="away", market="team total Under 3.5",
        )
        assert "TEAM_TOTAL_TREND_SUPPORTS_UNDER" in out["reason_codes"]
        assert out["adjustment"] >= 6

    def test_side_unknown_returns_neutral(self):
        h, a = self._sides()
        out = _evaluate_team_total(
            home_side=h, away_side=a,
            home_form_runs=0.0, away_form_runs=0.0,
            team_side=None, market="team total Over 4.5",
        )
        assert "TEAM_TOTAL_SIDE_UNKNOWN" in out["reason_codes"]
        assert out["adjustment"] == 0


# ── NRFI / YRFI evaluator ────────────────────────────────────────────
class TestNRFIEvaluator:
    def _split(self, yrfi_l15, yrfi_l5=None):
        return {"combined": {
            "yrfi_rate_last_15": yrfi_l15,
            "yrfi_rate_last_5":  yrfi_l5,
            "nrfi_rate_last_15": 1 - yrfi_l15 if yrfi_l15 is not None else None,
        }}

    def test_nrfi_with_low_yrfi_baseline_supports(self):
        out = _evaluate_nrfi_yrfi(
            first_inning_split=self._split(0.35),
            is_nrfi=True, market="NRFI",
        )
        assert "NRFI_LOW_BASELINE_YRFI" in out["reason_codes"]
        assert out["adjustment"] >= 8

    def test_yrfi_with_high_baseline_supports(self):
        out = _evaluate_nrfi_yrfi(
            first_inning_split=self._split(0.70),
            is_nrfi=False, market="YRFI",
        )
        assert "YRFI_HIGH_BASELINE" in out["reason_codes"]
        assert out["adjustment"] >= 8

    def test_recent_shift_recognised(self):
        # L15 YRFI 0.50, L5 0.80 → +0.30 shift (rising YRFI).
        # For NRFI pick that's a contradiction.
        out = _evaluate_nrfi_yrfi(
            first_inning_split=self._split(0.50, yrfi_l5=0.80),
            is_nrfi=True, market="NRFI",
        )
        # rising_yrfi against NRFI pick → reason code + negative adj.
        assert any("RECENT_SHIFT_RISING" in rc for rc in out["reason_codes"])

    def test_no_data_neutral(self):
        out = _evaluate_nrfi_yrfi(first_inning_split=None, is_nrfi=True, market="NRFI")
        assert "NRFI_NO_DATA" in out["reason_codes"]
        assert out["adjustment"] == 0


# ── End-to-end via combine_trend_signals ─────────────────────────────
class TestEndToEnd:
    def test_full_market_routing_for_f5(self):
        rs = {"total_runs_delta_5_vs_15": 0.0}
        ob = {"combined": {"times_on_base_delta_5_vs_15": 0.0},
              "home": {"times_on_base_delta_5_vs_15": 0.0},
              "away": {"times_on_base_delta_5_vs_15": 0.0}}
        f5 = {"combined": {
            "f5_runs_delta_5_vs_15": 1.0,
            "f5_runs_avg_last_5": 4.0, "f5_runs_avg_last_15": 3.0,
        }}
        out = combine_trend_signals(
            recent_run_split=rs, on_base_profile=ob,
            selected_market="F5 Under 4.5",
            f5_split=f5,
        )
        assert out["market_kind"] == "totals_f5"
        assert "F5_RUN_ENV_RISING_VS_UNDER" in out["reason_codes"]

    def test_nrfi_routing(self):
        rs = {"total_runs_delta_5_vs_15": 0.0}
        ob = {"combined": {"times_on_base_delta_5_vs_15": 0.0},
              "home": {"times_on_base_delta_5_vs_15": 0.0},
              "away": {"times_on_base_delta_5_vs_15": 0.0}}
        fi = {"combined": {
            "yrfi_rate_last_15": 0.32,
            "yrfi_rate_last_5":  0.25,
            "nrfi_rate_last_15": 0.68,
        }}
        out = combine_trend_signals(
            recent_run_split=rs, on_base_profile=ob,
            selected_market="NRFI",
            first_inning_split=fi,
        )
        assert out["market_kind"] == "nrfi"
        assert "NRFI_LOW_BASELINE_YRFI" in out["reason_codes"]
        assert out["score_adjustment"] >= 8
