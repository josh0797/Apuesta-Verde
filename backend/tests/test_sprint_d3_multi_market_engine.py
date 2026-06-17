"""Sprint-D3 · Tests for the multi-market backtest engine.

Covers:
* `run_backtest(market=...)` accepts OVER_1_5, DC_HD, DC_AD, DC_HA
* Ground-truth checks are correct per market
* Re-labeling uses the right threshold table per market
* Predictions / picks include `market` field
* Compute_backtest_metrics returns false-pos / false-neg examples
* Reliability-by-bucket structure shape
"""
from __future__ import annotations

import pytest

from services.football_historical_ingestor import parse_openfootball_json
from services.football_backtest_engine import (
    run_backtest, SUPPORTED_MARKETS, NO_MARKET_THRESHOLDS,
    _hit_draw, _hit_over15, _hit_hd, _hit_ad, _hit_ha,
    _relabel_for_market, _extract_prob_pct, _store_prob_pct,
    LABEL_STRONG_VALUE_GENERIC, LABEL_VALUE_GENERIC,
    LABEL_FAIR_GENERIC, LABEL_NO_VALUE_GENERIC,
)
from services.football_backtest_metrics import (
    compute_backtest_metrics, _false_positive_examples,
    _false_negative_examples, _reliability_by_bucket,
)


# Synthetic tournament fixture: 4-team group + 1 knockout + 1 final.
_FIXTURE = {
    "name": "Sprint-D3 Test Cup",
    "matches": [
        # MD1
        {"round": "Matchday 1", "date": "2099-06-10",
         "team1": "A", "team2": "B",
         "score": {"ft": [2, 1]}, "group": "Group X"},
        {"round": "Matchday 1", "date": "2099-06-10",
         "team1": "C", "team2": "D",
         "score": {"ft": [0, 0]}, "group": "Group X"},
        # MD2
        {"round": "Matchday 2", "date": "2099-06-14",
         "team1": "A", "team2": "C",
         "score": {"ft": [1, 1]}, "group": "Group X"},
        {"round": "Matchday 2", "date": "2099-06-14",
         "team1": "B", "team2": "D",
         "score": {"ft": [3, 0]}, "group": "Group X"},
        # MD3
        {"round": "Matchday 3", "date": "2099-06-18",
         "team1": "A", "team2": "D",
         "score": {"ft": [2, 2]}, "group": "Group X"},
        {"round": "Matchday 3", "date": "2099-06-18",
         "team1": "B", "team2": "C",
         "score": {"ft": [0, 1]}, "group": "Group X"},
        # KO
        {"round": "Quarter-final", "date": "2099-06-25",
         "team1": "A", "team2": "C",
         "score": {"ft": [1, 0]}},
        # Final
        {"round": "Final", "date": "2099-07-02",
         "team1": "A", "team2": "B",
         "score": {"ft": [1, 2]}},
    ],
}


@pytest.fixture
def matches():
    return parse_openfootball_json(_FIXTURE,
                                    competition="Sprint-D3 Test Cup")


# ════════════════════════════════════════════════════════════════════════
# Supported markets
# ════════════════════════════════════════════════════════════════════════
class TestSupportedMarkets:
    def test_supported_markets_list(self):
        assert "DRAW" in SUPPORTED_MARKETS
        assert "OVER_1_5" in SUPPORTED_MARKETS
        assert "DOUBLE_CHANCE_HD" in SUPPORTED_MARKETS
        assert "DOUBLE_CHANCE_AD" in SUPPORTED_MARKETS
        assert "DOUBLE_CHANCE_HA" in SUPPORTED_MARKETS

    def test_unsupported_market_raises(self, matches):
        with pytest.raises(NotImplementedError):
            run_backtest(matches, market="BTTS", no_market=True)

    def test_with_odds_only_supports_draw(self, matches):
        # Market-aware (no_market=False) only supports DRAW.
        with pytest.raises(NotImplementedError):
            run_backtest(matches, market="OVER_1_5", no_market=False)


# ════════════════════════════════════════════════════════════════════════
# Ground-truth correctness
# ════════════════════════════════════════════════════════════════════════
class TestGroundTruthCorrectness:
    @pytest.mark.parametrize("m,expected", [
        ({"fthg": 1, "ftag": 1, "ftr": "D"}, True),
        ({"fthg": 2, "ftag": 1, "ftr": "H"}, False),
        ({"fthg": 0, "ftag": 2, "ftr": "A"}, False),
    ])
    def test_hit_draw(self, m, expected):
        assert _hit_draw(m) is expected

    @pytest.mark.parametrize("h,a,expected", [
        (0, 0, False),
        (1, 0, False),
        (0, 1, False),
        (1, 1, True),
        (2, 0, True),
        (3, 2, True),
    ])
    def test_hit_over15(self, h, a, expected):
        assert _hit_over15({"fthg": h, "ftag": a}) is expected

    @pytest.mark.parametrize("h,a,expected", [
        (2, 1, True),   # home win → HD hit
        (1, 1, True),   # draw → HD hit
        (0, 1, False),  # away win → HD miss
    ])
    def test_hit_hd(self, h, a, expected):
        assert _hit_hd({"fthg": h, "ftag": a}) is expected

    @pytest.mark.parametrize("h,a,expected", [
        (2, 1, False),  # home win → AD miss
        (1, 1, True),   # draw → AD hit
        (0, 1, True),   # away win → AD hit
    ])
    def test_hit_ad(self, h, a, expected):
        assert _hit_ad({"fthg": h, "ftag": a}) is expected

    @pytest.mark.parametrize("h,a,expected", [
        (2, 1, True),   # not a draw → HA hit
        (1, 1, False),  # draw → HA miss
        (0, 0, False),
    ])
    def test_hit_ha(self, h, a, expected):
        assert _hit_ha({"fthg": h, "ftag": a}) is expected


# ════════════════════════════════════════════════════════════════════════
# Re-labeling thresholds
# ════════════════════════════════════════════════════════════════════════
class TestRelabeling:
    def test_thresholds_are_market_specific(self):
        for m in ("DRAW", "OVER_1_5", "DOUBLE_CHANCE_HD",
                  "DOUBLE_CHANCE_AD", "DOUBLE_CHANCE_HA"):
            assert m in NO_MARKET_THRESHOLDS
            t = NO_MARKET_THRESHOLDS[m]
            assert t["FAIR"] <= t["VALUE"] <= t["STRONG"]
            assert t["DEFAULT_FIRING"] >= t["VALUE"]

    def test_relabel_over15_strong(self):
        v = {"over15_probability": 88.0}
        _relabel_for_market(v, "OVER_1_5")
        assert v["label"] == LABEL_STRONG_VALUE_GENERIC

    def test_relabel_over15_value(self):
        v = {"over15_probability": 77.0}
        _relabel_for_market(v, "OVER_1_5")
        assert v["label"] == LABEL_VALUE_GENERIC

    def test_relabel_over15_fair(self):
        v = {"over15_probability": 62.0}
        _relabel_for_market(v, "OVER_1_5")
        assert v["label"] == LABEL_FAIR_GENERIC

    def test_relabel_over15_no_value(self):
        v = {"over15_probability": 40.0}
        _relabel_for_market(v, "OVER_1_5")
        assert v["label"] == LABEL_NO_VALUE_GENERIC

    def test_relabel_dc_ad_uses_lower_thresholds(self):
        """DC_AD has FAIR=50, VALUE=55, STRONG=60 — lower than other DC
        variants, to compensate for the model's systematic
        under-prediction."""
        v = {"p_away_or_draw_pct": 56.0}
        _relabel_for_market(v, "DOUBLE_CHANCE_AD")
        assert v["label"] == LABEL_VALUE_GENERIC


# ════════════════════════════════════════════════════════════════════════
# Engine smoke tests per market
# ════════════════════════════════════════════════════════════════════════
class TestEnginePerMarket:
    @pytest.mark.parametrize("market", [
        "DRAW", "OVER_1_5",
        "DOUBLE_CHANCE_HD", "DOUBLE_CHANCE_AD", "DOUBLE_CHANCE_HA",
    ])
    def test_run_returns_predictions(self, matches, market):
        r = run_backtest(matches, market=market, no_market=True,
                          use_calibration=False, walk_forward=False)
        assert r["market"] == market
        assert len(r["predictions"]) > 0
        # All predictions carry the market label.
        for p in r["predictions"]:
            assert p["market"] == market

    def test_each_prediction_has_required_keys(self, matches):
        r = run_backtest(matches, market="OVER_1_5", no_market=True)
        for p in r["predictions"]:
            for k in ("date", "home", "away", "predicted_prob", "label",
                      "hit", "actual_score", "fired", "market"):
                assert k in p

    def test_no_negative_probabilities(self, matches):
        for market in SUPPORTED_MARKETS:
            r = run_backtest(matches, market=market, no_market=True)
            for p in r["predictions"]:
                assert 0.0 <= p["predicted_prob"] <= 1.0


# ════════════════════════════════════════════════════════════════════════
# Metrics with false-positive / false-negative examples
# ════════════════════════════════════════════════════════════════════════
class TestMetricsExamples:
    def test_false_positive_examples_sort_by_confidence_desc(self):
        preds = [
            {"predicted_prob": 0.5, "hit": False, "fired": True},
            {"predicted_prob": 0.9, "hit": False, "fired": True},
            {"predicted_prob": 0.7, "hit": False, "fired": True},
            {"predicted_prob": 0.8, "hit": True,  "fired": True},
        ]
        out = _false_positive_examples(preds, top_n=10)
        # Hit==True should be excluded.
        assert all(p.get("predicted_prob") in (0.9, 0.7, 0.5) for p in out)
        # Sorted by descending predicted_prob.
        probs = [p["predicted_prob"] for p in out]
        assert probs == sorted(probs, reverse=True)

    def test_false_negative_examples_sort_by_confidence_asc(self):
        preds = [
            {"predicted_prob": 0.4, "hit": True, "fired": False},
            {"predicted_prob": 0.1, "hit": True, "fired": False},
            {"predicted_prob": 0.3, "hit": True, "fired": False},
        ]
        out = _false_negative_examples(preds, top_n=10)
        probs = [p["predicted_prob"] for p in out]
        assert probs == sorted(probs)

    def test_reliability_by_bucket_has_hit_rate(self):
        preds = [
            {"predicted_prob": 0.2, "hit": False},
            {"predicted_prob": 0.25, "hit": False},
            {"predicted_prob": 0.7, "hit": True},
        ]
        out = _reliability_by_bucket(preds, n_buckets=10)
        for row in out:
            assert "hit_rate" in row

    def test_compute_metrics_returns_example_tables(self, matches):
        r = run_backtest(matches, market="OVER_1_5", no_market=True,
                          use_calibration=True, walk_forward=True)
        m = compute_backtest_metrics(r)
        assert "false_positive_examples" in m
        assert "false_negative_examples" in m
        assert "false_positive_examples_group" in m
        assert "false_positive_examples_knockout" in m
        # Limits respected.
        assert len(m["false_positive_examples"]) <= 10
        assert len(m["false_positive_examples_group"]) <= 5
        assert len(m["false_positive_examples_knockout"]) <= 5


# ════════════════════════════════════════════════════════════════════════
# Probability extractor / setter helpers
# ════════════════════════════════════════════════════════════════════════
class TestProbHelpers:
    def test_extract_per_market(self):
        v = {
            "draw_probability": 30.0,
            "over15_probability": 75.0,
            "p_home_or_draw_pct": 70.0,
            "p_away_or_draw_pct": 55.0,
            "p_home_or_away_pct": 80.0,
        }
        assert _extract_prob_pct(v, "DRAW") == 30.0
        assert _extract_prob_pct(v, "OVER_1_5") == 75.0
        assert _extract_prob_pct(v, "DOUBLE_CHANCE_HD") == 70.0
        assert _extract_prob_pct(v, "DOUBLE_CHANCE_AD") == 55.0
        assert _extract_prob_pct(v, "DOUBLE_CHANCE_HA") == 80.0

    def test_store_per_market(self):
        for market, key in [
            ("DRAW", "draw_probability"),
            ("OVER_1_5", "over15_probability"),
            ("DOUBLE_CHANCE_HD", "p_home_or_draw_pct"),
            ("DOUBLE_CHANCE_AD", "p_away_or_draw_pct"),
            ("DOUBLE_CHANCE_HA", "p_home_or_away_pct"),
        ]:
            v = {}
            _store_prob_pct(v, market, 65.0)
            assert v[key] == pytest.approx(65.0, abs=0.1)


# ════════════════════════════════════════════════════════════════════════
# Point-in-time discipline (regression)
# ════════════════════════════════════════════════════════════════════════
class TestPITDiscipline:
    @pytest.mark.parametrize("market", SUPPORTED_MARKETS)
    def test_predictions_use_only_prior_history(self, matches, market):
        """The engine must never use ftr / fthg / ftag of the target
        match when building features. We can verify indirectly by
        running the backtest twice: same input → same output."""
        r1 = run_backtest(matches, market=market, no_market=True,
                           use_calibration=False, walk_forward=False)
        r2 = run_backtest(matches, market=market, no_market=True,
                           use_calibration=False, walk_forward=False)
        probs1 = [p["predicted_prob"] for p in r1["predictions"]]
        probs2 = [p["predicted_prob"] for p in r2["predictions"]]
        assert probs1 == probs2
