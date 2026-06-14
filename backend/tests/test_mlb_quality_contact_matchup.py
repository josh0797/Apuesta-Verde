"""Phase F91 — MLB Quality Contact Matchup tests.

Pure-Python coverage for the new module. NO Mongo / HTTP / Statcast
network calls. Every input is synthetic.
"""
from __future__ import annotations

import json
import pytest

from services import mlb_quality_contact_matchup as qcm


# ─────────────────────────────────────────────────────────────────────
# Fixture helpers
# ─────────────────────────────────────────────────────────────────────
def _strong_batter() -> dict:
    return {"xwoba": 0.420, "sweet_spot_pct": 0.45,
            "barrel_pct": 0.15, "hard_hit_pct": 0.55}


def _weak_batter() -> dict:
    return {"xwoba": 0.250, "sweet_spot_pct": 0.25,
            "barrel_pct": 0.04, "hard_hit_pct": 0.30}


def _vulnerable_pitcher() -> dict:
    return {"era": 2.80, "xera": 4.75, "xwoba_allowed": 0.360,
            "barrel_pct_allowed": 0.094, "hard_hit_pct_allowed": 0.46}


def _ace_pitcher() -> dict:
    return {"era": 2.90, "xera": 2.85, "xwoba_allowed": 0.260,
            "barrel_pct_allowed": 0.05, "hard_hit_pct_allowed": 0.32}


def _payload(*, home_team, home_pitcher, away_team, away_pitcher,
              target_side: str = "away") -> dict:
    return {
        "quality_contact_matchup_side": target_side,
        "home_team_advanced": {"team": home_team, "pitcher": home_pitcher},
        "away_team_advanced": {"team": away_team, "pitcher": away_pitcher},
    }


# ─────────────────────────────────────────────────────────────────────
# Constants invariants
# ─────────────────────────────────────────────────────────────────────
class TestConstants:
    def test_lineup_weights_keys_are_1_to_9(self):
        assert sorted(qcm.LINEUP_WEIGHTS.keys()) == list(range(1, 10))

    def test_lineup_weights_decrease_monotonically_from_3(self):
        # Spec ordering: 3 ≥ 2 ≥ 1 ≥ 4 ≥ 5 ≥ 6 ≥ 7 ≥ 8 ≥ 9 — not pure
        # monotonic. We assert the leadoff trio and the tail decrease.
        w = qcm.LINEUP_WEIGHTS
        assert w[1] > w[5] > w[6] > w[7] > w[8] > w[9]

    def test_top_4_weights_sum_more_than_bottom_5(self):
        top = sum(qcm.LINEUP_WEIGHTS[i] for i in range(1, 5))
        bot = sum(qcm.LINEUP_WEIGHTS[i] for i in range(5, 10))
        assert top > bot


# ─────────────────────────────────────────────────────────────────────
# Score primitives
# ─────────────────────────────────────────────────────────────────────
class TestBatterContactScore:
    def test_strong_batter_higher_than_weak(self):
        s = qcm.compute_batter_contact_score(_strong_batter())
        w = qcm.compute_batter_contact_score(_weak_batter())
        assert s > w
        assert 0.0 <= s <= 100.0
        assert 0.0 <= w <= 100.0

    def test_missing_metrics_returns_none(self):
        assert qcm.compute_batter_contact_score({}) is None
        assert qcm.compute_batter_contact_score("not-a-dict") is None

    def test_partial_metrics_still_compute(self):
        """When some metrics are missing the function uses what it has."""
        partial = {"xwoba": 0.350, "barrel_pct": 0.10}
        out = qcm.compute_batter_contact_score(partial)
        assert out is not None
        assert 0.0 <= out <= 100.0


class TestLineupContactQuality:
    def test_full_strong_lineup_scores_high(self):
        block = qcm.compute_lineup_contact_quality([_strong_batter()] * 9)
        assert block["sample_size"] == 9
        assert block["lineup_contact_quality"] >= 75.0

    def test_full_weak_lineup_scores_low(self):
        block = qcm.compute_lineup_contact_quality([_weak_batter()] * 9)
        # Weak batter: xwoba=0.250, sweet_spot=0.25, barrel=0.04,
        # hard_hit=0.30 → scaled batter score ~44 / 100. We just assert
        # the weak lineup scores meaningfully *below* a strong one (the
        # other test asserts strong ≥ 75) so the ordering is enforced.
        assert block["lineup_contact_quality"] <= 50.0

    def test_top_4_ratio_within_expected_band(self):
        block = qcm.compute_lineup_contact_quality([_strong_batter()] * 9)
        # Top-4 weighted sums ~ (1.30+1.25+1.20+1.15)=4.90 of total ~9.65 → ~51%.
        assert 0.40 < block["top_4_ratio"] < 0.60

    def test_empty_lineup_returns_none(self):
        block = qcm.compute_lineup_contact_quality([])
        assert block["lineup_contact_quality"] is None
        assert block["sample_size"] == 0

    def test_clusterstacked_top_order_threat_triggers_when_top_strong_bottom_weak(self):
        """Top 4 strong + bottom 5 weak should make top_4_ratio > 0.55."""
        batters = [_strong_batter()] * 4 + [_weak_batter()] * 5
        block = qcm.compute_lineup_contact_quality(batters)
        assert block["top_4_ratio"] is not None
        assert block["top_4_ratio"] >= 0.55


class TestPitcherVulnerability:
    def test_vulnerable_pitcher_higher_than_ace(self):
        v = qcm.compute_pitcher_vulnerability(_vulnerable_pitcher())
        a = qcm.compute_pitcher_vulnerability(_ace_pitcher())
        assert v > a
        assert 0.0 <= v <= 100.0
        assert 0.0 <= a <= 100.0

    def test_missing_pitcher_returns_none(self):
        assert qcm.compute_pitcher_vulnerability(None) is None
        assert qcm.compute_pitcher_vulnerability({}) is None


class TestEraGap:
    def test_severe_regression(self):
        out = qcm.classify_era_gap(2.80, 4.75)
        assert out["level"] == qcm.REGRESSION_SEVERE
        assert out["gap"] == pytest.approx(1.95)

    def test_high_regression(self):
        out = qcm.classify_era_gap(3.00, 4.10)
        assert out["level"] == qcm.REGRESSION_HIGH

    def test_moderate_regression(self):
        out = qcm.classify_era_gap(3.50, 4.10)
        assert out["level"] == qcm.REGRESSION_MODERATE

    def test_normal_when_xera_lower(self):
        out = qcm.classify_era_gap(4.20, 4.00)
        assert out["level"] == qcm.REGRESSION_NORMAL

    def test_missing_era_returns_normal(self):
        assert qcm.classify_era_gap(None, 4.0)["level"] == qcm.REGRESSION_NORMAL
        assert qcm.classify_era_gap(4.0, None)["level"] == qcm.REGRESSION_NORMAL


# ─────────────────────────────────────────────────────────────────────
# Threshold env override
# ─────────────────────────────────────────────────────────────────────
class TestThresholdsOverride:
    def test_defaults_when_no_env(self, monkeypatch):
        monkeypatch.delenv("QCM_THRESHOLDS", raising=False)
        th = qcm.get_active_thresholds()
        assert th["MATCHUP_CONTACT_ADVANTAGE"] == 70.0
        assert th["PITCHER_BARREL_REGRESSION"] == 0.08

    def test_env_override_merges(self, monkeypatch):
        monkeypatch.setenv("QCM_THRESHOLDS",
                           json.dumps({"MATCHUP_CONTACT_ADVANTAGE": 65.0}))
        th = qcm.get_active_thresholds()
        assert th["MATCHUP_CONTACT_ADVANTAGE"] == 65.0
        # Other thresholds preserved.
        assert th["PITCHER_BARREL_REGRESSION"] == 0.08

    def test_env_invalid_json_falls_back(self, monkeypatch):
        monkeypatch.setenv("QCM_THRESHOLDS", "{not_json")
        th = qcm.get_active_thresholds()
        assert th["MATCHUP_CONTACT_ADVANTAGE"] == 70.0

    def test_env_unknown_key_ignored(self, monkeypatch):
        monkeypatch.setenv("QCM_THRESHOLDS",
                           json.dumps({"UNKNOWN_KEY": 999}))
        th = qcm.get_active_thresholds()
        assert "UNKNOWN_KEY" not in th
        # Defaults intact.
        assert th["MATCHUP_CONTACT_ADVANTAGE"] == 70.0


# ─────────────────────────────────────────────────────────────────────
# per_batter_metrics_enabled
# ─────────────────────────────────────────────────────────────────────
class TestPerBatterFlag:
    def test_default_disabled(self, monkeypatch):
        monkeypatch.delenv("QCM_LINEUP_PER_BATTER", raising=False)
        assert qcm.per_batter_metrics_enabled() is False

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("QCM_LINEUP_PER_BATTER", "true")
        assert qcm.per_batter_metrics_enabled() is True


# ─────────────────────────────────────────────────────────────────────
# Public API: compute_quality_contact_matchup
# ─────────────────────────────────────────────────────────────────────
class TestQualityContactMatchup:
    def test_strong_lineup_vs_vulnerable_pitcher_emits_signals(self, monkeypatch):
        monkeypatch.delenv("QCM_LINEUP_PER_BATTER", raising=False)
        payload = _payload(
            home_team={"xwoba": 0.330, "sweet_spot_pct": 0.35,
                        "barrel_pct": 0.085, "hard_hit_pct": 0.42},
            home_pitcher=_vulnerable_pitcher(),
            away_team={"xwoba": 0.420, "sweet_spot_pct": 0.45,
                        "barrel_pct": 0.15, "hard_hit_pct": 0.55},
            away_pitcher=_ace_pitcher(),
            target_side="away",
        )
        out = qcm.compute_quality_contact_matchup(payload)
        assert out["available"] is True
        assert out["lineup_contact_quality"] is not None
        assert out["pitcher_vulnerability"]  is not None
        # Vulnerable pitcher (era=2.80, xera=4.75) → SEVERE regression.
        assert out["regression_risk"] == qcm.REGRESSION_SEVERE
        assert qcm.SIGNAL_MATCHUP_CONTACT_ADVANTAGE in out["signals"]
        assert qcm.SIGNAL_PITCHER_BARREL_REGRESSION in out["signals"]
        assert qcm.SIGNAL_ERA_UNDERSTATES_DAMAGE in out["signals"]
        # Provenance marked as derived (default flag off).
        assert qcm.RC_BATTER_LEVEL_DERIVED in out["reason_codes"]
        assert out["side"] == "away"

    def test_weak_lineup_vs_ace_no_signals(self, monkeypatch):
        monkeypatch.delenv("QCM_LINEUP_PER_BATTER", raising=False)
        payload = _payload(
            home_team={"xwoba": 0.330, "sweet_spot_pct": 0.35,
                        "barrel_pct": 0.085, "hard_hit_pct": 0.42},
            home_pitcher=_ace_pitcher(),
            away_team={"xwoba": 0.270, "sweet_spot_pct": 0.28,
                        "barrel_pct": 0.05, "hard_hit_pct": 0.32},
            away_pitcher=_vulnerable_pitcher(),
            target_side="away",
        )
        out = qcm.compute_quality_contact_matchup(payload)
        assert out["available"] is True
        # Lineup is weak → contact_advantage NOT in signals.
        assert qcm.SIGNAL_MATCHUP_CONTACT_ADVANTAGE not in out["signals"]
        # Ace pitcher → no barrel regression, no era understates.
        assert qcm.SIGNAL_PITCHER_BARREL_REGRESSION not in out["signals"]
        assert qcm.SIGNAL_ERA_UNDERSTATES_DAMAGE not in out["signals"]

    def test_unavailable_when_payload_empty(self):
        out = qcm.compute_quality_contact_matchup({})
        assert out["available"] is False
        assert qcm.RC_INSUFFICIENT_DATA in out["reason_codes"]
        assert out["signals"] == []

    def test_unavailable_when_payload_not_dict(self):
        out = qcm.compute_quality_contact_matchup("not-a-dict")
        assert out["available"] is False
        assert qcm.RC_INSUFFICIENT_DATA in out["reason_codes"]

    def test_real_batter_path_when_flag_enabled(self, monkeypatch):
        monkeypatch.setenv("QCM_LINEUP_PER_BATTER", "true")
        # Provide a real batter list under lineups.official.away.
        real_batters = [
            {"order": i + 1,
              "statcast": {"xwoba": 0.420, "sweet_spot_pct": 0.45,
                            "barrel_pct": 0.15, "hard_hit_pct": 0.55}}
            for i in range(9)
        ]
        payload = {
            "quality_contact_matchup_side": "away",
            "lineups": {"official": {"away": real_batters}},
            "home_team_advanced": {
                "pitcher": _vulnerable_pitcher(),
            },
        }
        out = qcm.compute_quality_contact_matchup(payload)
        assert out["available"] is True
        # Provenance marked REAL when the flag is on.
        assert qcm.RC_BATTER_LEVEL_REAL in out["reason_codes"]
        assert qcm.RC_BATTER_LEVEL_DERIVED not in out["reason_codes"]

    def test_top_order_threat_signal_fires_when_top4_stacked(self, monkeypatch):
        monkeypatch.setenv("QCM_LINEUP_PER_BATTER", "true")
        rows = (
            [{"statcast": _strong_batter()} for _ in range(4)]
            + [{"statcast": _weak_batter()} for _ in range(5)]
        )
        payload = {
            "quality_contact_matchup_side": "away",
            "lineups": {"official": {"away": rows}},
            "home_team_advanced": {"pitcher": _vulnerable_pitcher()},
        }
        out = qcm.compute_quality_contact_matchup(payload)
        assert qcm.SIGNAL_TOP_ORDER_THREAT in out["signals"]

    def test_over_contact_warning_signal_fires_at_high_mismatch(self, monkeypatch):
        # Override threshold to a low value so the mismatch score crosses it.
        monkeypatch.setenv("QCM_THRESHOLDS",
                           json.dumps({"OVER_CONTACT_WARNING": 30.0}))
        payload = _payload(
            home_team={}, home_pitcher=_vulnerable_pitcher(),
            away_team={"xwoba": 0.420, "sweet_spot_pct": 0.45,
                        "barrel_pct": 0.15, "hard_hit_pct": 0.55},
            away_pitcher=_ace_pitcher(),
            target_side="away",
        )
        out = qcm.compute_quality_contact_matchup(payload)
        assert qcm.SIGNAL_OVER_CONTACT_WARNING in out["signals"]

    def test_score_breakdown_is_auditable(self, monkeypatch):
        monkeypatch.delenv("QCM_LINEUP_PER_BATTER", raising=False)
        payload = _payload(
            home_team={"xwoba": 0.330, "sweet_spot_pct": 0.35,
                        "barrel_pct": 0.085, "hard_hit_pct": 0.42},
            home_pitcher=_vulnerable_pitcher(),
            away_team={"xwoba": 0.380, "sweet_spot_pct": 0.40,
                        "barrel_pct": 0.12, "hard_hit_pct": 0.50},
            away_pitcher=_ace_pitcher(),
            target_side="away",
        )
        out = qcm.compute_quality_contact_matchup(payload)
        sb = out["score_breakdown"]
        # Per-batter rows must total 9.
        assert len(sb["weighted_per_batter"]) == 9
        assert sb["sample_size"] == 9
        # Pitcher metrics echoed for audit.
        assert sb["pitcher_metrics"]["era"]  == pytest.approx(2.80, abs=0.01)
        assert sb["pitcher_metrics"]["xera"] == pytest.approx(4.75, abs=0.01)

    def test_no_picks_are_generated(self, monkeypatch):
        """The module MUST NOT mutate any picks[]; it only returns the
        ``quality_contact_matchup`` block."""
        monkeypatch.delenv("QCM_LINEUP_PER_BATTER", raising=False)
        payload = {
            "quality_contact_matchup_side": "away",
            "home_team_advanced": {
                "team":    {"xwoba": 0.330, "sweet_spot_pct": 0.35,
                              "barrel_pct": 0.085, "hard_hit_pct": 0.42},
                "pitcher": _vulnerable_pitcher(),
            },
            "away_team_advanced": {
                "team":    {"xwoba": 0.380, "sweet_spot_pct": 0.40,
                              "barrel_pct": 0.12, "hard_hit_pct": 0.50},
                "pitcher": _ace_pitcher(),
            },
            "picks": [{"market": "OVER_8_5", "confidence": 60}],
        }
        out = qcm.compute_quality_contact_matchup(payload)
        # picks unchanged.
        assert payload["picks"] == [{"market": "OVER_8_5", "confidence": 60}]
        # output is its own dict, never embedded into picks.
        assert "picks" not in out

    def test_pitcher_missing_marks_provenance_and_keeps_lineup_score(self, monkeypatch):
        monkeypatch.delenv("QCM_LINEUP_PER_BATTER", raising=False)
        payload = _payload(
            home_team={}, home_pitcher={},   # no pitcher metrics at all
            away_team={"xwoba": 0.330, "sweet_spot_pct": 0.35,
                        "barrel_pct": 0.085, "hard_hit_pct": 0.42},
            away_pitcher={},
            target_side="away",
        )
        out = qcm.compute_quality_contact_matchup(payload)
        # Lineup quality computed.
        assert out["lineup_contact_quality"] is not None
        # Pitcher vulnerability None → block flagged unavailable but
        # lineup score preserved.
        assert out["pitcher_vulnerability"] is None
        assert out["available"] is False
        assert qcm.RC_PITCHER_MISSING in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Specific signal-by-signal regression coverage
# ─────────────────────────────────────────────────────────────────────
class TestSignalThresholdEdges:
    def test_matchup_contact_advantage_boundary(self, monkeypatch):
        # Lower threshold so even a moderate lineup crosses it.
        monkeypatch.setenv("QCM_THRESHOLDS",
                           json.dumps({"MATCHUP_CONTACT_ADVANTAGE": 50.0}))
        payload = _payload(
            home_team={}, home_pitcher=_vulnerable_pitcher(),
            away_team={"xwoba": 0.330, "sweet_spot_pct": 0.35,
                        "barrel_pct": 0.085, "hard_hit_pct": 0.42},
            away_pitcher=_ace_pitcher(),
            target_side="away",
        )
        out = qcm.compute_quality_contact_matchup(payload)
        assert qcm.SIGNAL_MATCHUP_CONTACT_ADVANTAGE in out["signals"]

    def test_pitcher_barrel_regression_threshold_via_override(self, monkeypatch):
        # Raise the threshold so the regular vulnerable pitcher no
        # longer triggers the signal.
        monkeypatch.setenv("QCM_THRESHOLDS",
                           json.dumps({"PITCHER_BARREL_REGRESSION": 0.30}))
        payload = _payload(
            home_team={}, home_pitcher=_vulnerable_pitcher(),
            away_team={"xwoba": 0.420, "sweet_spot_pct": 0.45,
                        "barrel_pct": 0.15, "hard_hit_pct": 0.55},
            away_pitcher=_ace_pitcher(),
            target_side="away",
        )
        out = qcm.compute_quality_contact_matchup(payload)
        assert qcm.SIGNAL_PITCHER_BARREL_REGRESSION not in out["signals"]
