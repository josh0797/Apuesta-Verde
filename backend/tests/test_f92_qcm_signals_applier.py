"""Phase F92 — QCM signals applier + payload-contract wiring tests."""
from __future__ import annotations

import json
import pytest

from services import mlb_qcm_signals_applier as appl
from services import mlb_quality_contact_matchup as qcm
from services.mlb_pipeline_payload_contract import seal_pick_payload


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _qcm_block(*, mismatch: float = 75.0,
                regression: str = "HIGH_REGRESSION_RISK",
                signals: tuple[str, ...] = ()) -> dict:
    return {
        "available":               True,
        "lineup_contact_quality":  78.0,
        "pitcher_vulnerability":   74.0,
        "matchup_contact_factor":  82.7,
        "contact_mismatch_score":  mismatch,
        "era_gap":                 1.4,
        "regression_risk":         regression,
        "signals":                 list(signals),
        "reason_codes":            ["QCM_BATTER_LEVEL_DERIVED_FROM_TEAM"],
    }


def _explosion_block(mismatch: float = 75.0,
                      regression: str = "HIGH_REGRESSION_RISK") -> dict:
    """QCM block that *triggers* CONTACT_EXPLOSION_POTENTIAL."""
    return _qcm_block(
        mismatch=mismatch, regression=regression,
        signals=("MATCHUP_CONTACT_ADVANTAGE",
                 "PITCHER_BARREL_REGRESSION_RISK",
                 "ERA_UNDERSTATES_DAMAGE"),
    )


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Each test starts without env contamination."""
    monkeypatch.delenv("QCM_APPLIER_DELTAS", raising=False)
    monkeypatch.delenv("QCM_LINEUP_PER_BATTER", raising=False)
    monkeypatch.delenv("QCM_THRESHOLDS", raising=False)
    yield


# =====================================================================
# A — Applier puro
# =====================================================================
class TestUnderPenalty:
    def test_full_game_under_penalised_when_mismatch_high(self):
        cand = {"market": "UNDER_8_5", "confidence_score": 60, "signals": []}
        res  = appl.apply_qcm_to_candidate(cand, _explosion_block(mismatch=75.0))
        assert res["applied"] is True
        assert res["side"]   == "under"
        assert res["period"] == "full_game"
        # base -4 + severity -2 (HIGH != SEVERE → no severity bonus).
        # HIGH_REGRESSION → no bonus, only base -4.
        assert res["delta"] == -4
        assert cand["confidence_score"] == 56
        assert appl.SIGNAL_UNDER_CONTACT_RISK in cand["signals"]
        # Severe applies an extra -2.
        cand2 = {"market": "UNDER_8_5", "confidence_score": 60, "signals": []}
        res2 = appl.apply_qcm_to_candidate(
            cand2, _explosion_block(regression="SEVERE_REGRESSION_RISK"),
        )
        assert res2["delta"] == -6   # -4 + -2 severe bonus
        assert cand2["confidence_score"] == 54

    def test_full_game_under_not_penalised_when_mismatch_below_threshold(self):
        cand = {"market": "UNDER_8_5", "confidence_score": 60, "signals": []}
        res  = appl.apply_qcm_to_candidate(cand, _qcm_block(mismatch=60.0))
        assert res["applied"] is False
        assert cand["confidence_score"] == 60

    def test_f5_under_not_penalised_without_top_order_threat(self):
        cand = {"market": "UNDER_F5_4_5", "confidence_score": 55, "signals": []}
        block = _explosion_block(mismatch=80.0)  # no TOP_ORDER_THREAT
        res = appl.apply_qcm_to_candidate(cand, block)
        assert res["applied"] is False
        assert cand["confidence_score"] == 55

    def test_f5_under_penalised_when_top_order_threat_active(self):
        cand = {"market": "UNDER_F5_4_5", "confidence_score": 55, "signals": []}
        block = _explosion_block(mismatch=80.0)
        block["signals"].append("TOP_ORDER_THREAT")
        res = appl.apply_qcm_to_candidate(cand, block)
        assert res["applied"] is True
        assert res["period"] == "f5"
        # F5 base penalty is -3 (HIGH regression → no severe bonus).
        assert res["delta"] == -3
        assert cand["confidence_score"] == 52


class TestOverBoost:
    def test_over_full_game_boosted_when_explosion_present(self):
        cand = {"market": "OVER_8_5", "confidence_score": 60, "signals": []}
        res  = appl.apply_qcm_to_candidate(cand, _explosion_block())
        assert res["applied"] is True
        assert res["delta"]   == +3
        assert cand["confidence_score"] == 63
        assert appl.SIGNAL_CONTACT_EXPLOSION_POTENTIAL in cand["signals"]

    def test_over_team_total_gets_higher_boost(self):
        cand = {"market": "OVER_TEAM_TOTAL_3_5",
                 "confidence_score": 60, "signals": []}
        res  = appl.apply_qcm_to_candidate(cand, _explosion_block())
        assert res["delta"] == +4
        assert cand["confidence_score"] == 64

    def test_over_not_boosted_when_one_signal_missing(self):
        # Missing ERA_UNDERSTATES_DAMAGE → no explosion.
        block = _qcm_block(signals=("MATCHUP_CONTACT_ADVANTAGE",
                                      "PITCHER_BARREL_REGRESSION_RISK"))
        cand  = {"market": "OVER_8_5", "confidence_score": 60, "signals": []}
        res = appl.apply_qcm_to_candidate(cand, block)
        assert res["applied"] is False
        assert cand["confidence_score"] == 60


class TestClampAndPolarity:
    def test_clamp_applies_when_override_pushes_delta_above_ceiling(self, monkeypatch):
        monkeypatch.setenv("QCM_APPLIER_DELTAS",
                           json.dumps({"UNDER_FULL_GAME_PENALTY": -20,
                                       "UNDER_SEVERE_BONUS": -10}))
        cand = {"market": "UNDER_8_5", "confidence_score": 60, "signals": []}
        block = _explosion_block(regression="SEVERE_REGRESSION_RISK")
        res = appl.apply_qcm_to_candidate(cand, block)
        # Raw delta = -30 → clamped to MAX_UNDER_PENALTY = -6.
        assert res["delta"] == -6
        assert res["clamped"] is True
        assert appl.RC_QCM_CLAMPED in res["reason_codes"]
        assert cand["confidence_score"] == 54

    def test_market_not_under_or_over_returns_no_op(self):
        cand = {"market": "MONEYLINE", "confidence_score": 60, "signals": []}
        res = appl.apply_qcm_to_candidate(cand, _explosion_block())
        assert res["applied"] is False
        assert appl.RC_QCM_NOT_APPLICABLE in res["reason_codes"]


class TestNoDataPath:
    def test_no_qcm_block_returns_no_op(self):
        cand = {"market": "UNDER_8_5", "confidence_score": 60, "signals": []}
        res = appl.apply_qcm_to_candidate(cand, None)
        assert res["applied"] is False
        assert appl.RC_QCM_NO_DATA in res["reason_codes"]

    def test_qcm_block_unavailable_returns_no_op(self):
        cand = {"market": "UNDER_8_5", "confidence_score": 60, "signals": []}
        res = appl.apply_qcm_to_candidate(
            cand, {"available": False, "reason_codes": ["QCM_INSUFFICIENT_DATA"]},
        )
        assert res["applied"] is False

    def test_pick_with_no_market_returns_no_op(self):
        cand = {"confidence_score": 60, "signals": []}
        res = appl.apply_qcm_to_candidate(cand, _explosion_block())
        assert res["applied"] is False


class TestHardVeto:
    def test_hard_veto_active_when_all_conditions_met(self):
        block = _explosion_block(mismatch=88.0,
                                  regression="SEVERE_REGRESSION_RISK")
        block["signals"].append("TOP_ORDER_THREAT")
        assert appl.qcm_hard_veto_active(block) is True

    def test_hard_veto_false_without_top_order(self):
        block = _explosion_block(mismatch=88.0,
                                  regression="SEVERE_REGRESSION_RISK")
        assert appl.qcm_hard_veto_active(block) is False

    def test_hard_veto_false_when_not_severe(self):
        block = _explosion_block(mismatch=88.0,
                                  regression="HIGH_REGRESSION_RISK")
        block["signals"].append("TOP_ORDER_THREAT")
        assert appl.qcm_hard_veto_active(block) is False


class TestEnvOverride:
    def test_default_deltas(self):
        d = appl.get_active_qcm_deltas()
        assert d["UNDER_FULL_GAME_PENALTY"] == -4
        assert d["OVER_BASE_BOOST"]         == +3

    def test_env_override_merges(self, monkeypatch):
        monkeypatch.setenv("QCM_APPLIER_DELTAS",
                           json.dumps({"OVER_BASE_BOOST": 5}))
        d = appl.get_active_qcm_deltas()
        assert d["OVER_BASE_BOOST"] == 5
        assert d["UNDER_FULL_GAME_PENALTY"] == -4   # unchanged.

    def test_env_invalid_json_falls_back(self, monkeypatch):
        monkeypatch.setenv("QCM_APPLIER_DELTAS", "{not json")
        d = appl.get_active_qcm_deltas()
        assert d["UNDER_FULL_GAME_PENALTY"] == -4


# =====================================================================
# B — Wiring vía seal_pick_payload
# =====================================================================
class TestPayloadWiring:
    def _base_payload(self) -> dict:
        return {
            "sport": "mlb",
            "quality_contact_matchup_side": "away",
            "home_team_advanced": {
                "team":    {"xwoba": 0.330, "sweet_spot_pct": 0.35,
                              "barrel_pct": 0.085, "hard_hit_pct": 0.42},
                "pitcher": {"era": 2.80, "xera": 4.75,
                              "xwoba_allowed": 0.360,
                              "barrel_pct_allowed": 0.094,
                              "hard_hit_pct_allowed": 0.46},
            },
            "away_team_advanced": {
                "team":    {"xwoba": 0.420, "sweet_spot_pct": 0.45,
                              "barrel_pct": 0.15, "hard_hit_pct": 0.55},
                "pitcher": {"era": 2.90, "xera": 2.85,
                              "xwoba_allowed": 0.26,
                              "barrel_pct_allowed": 0.05,
                              "hard_hit_pct_allowed": 0.32},
            },
        }

    def test_seal_applies_over_boost(self):
        p = self._base_payload()
        p["picks"] = [
            {"market": "OVER_8_5", "confidence_score": 60, "signals": []}
        ]
        sealed = seal_pick_payload(p)
        # CONTACT_EXPLOSION_POTENTIAL active → +3 boost.
        assert sealed["picks"][0]["confidence_score"] == 63
        assert appl.SIGNAL_CONTACT_EXPLOSION_POTENTIAL in sealed["picks"][0]["signals"]
        assert sealed["qcm_audit"]["applied_count"] == 1

    def test_seal_applies_under_penalty_when_threshold_crossed(self, monkeypatch):
        # Lower the threshold so the synthetic payload's mismatch
        # crosses it (default mismatch is ~57).
        monkeypatch.setenv("QCM_THRESHOLDS",
                           json.dumps({"UNDER_FULL_GAME_THRESHOLD": 40.0}))
        monkeypatch.setenv("QCM_APPLIER_DELTAS",
                           json.dumps({"UNDER_FULL_GAME_THRESHOLD": 40.0}))
        p = self._base_payload()
        p["picks"] = [
            {"market": "UNDER_8_5", "confidence_score": 60, "signals": []}
        ]
        sealed = seal_pick_payload(p)
        assert sealed["picks"][0]["confidence_score"] < 60
        assert appl.SIGNAL_UNDER_CONTACT_RISK in sealed["picks"][0]["signals"]

    def test_seal_failsoft_when_qcm_unavailable(self):
        p = {"sport": "mlb",
             "picks": [{"market": "UNDER_8_5", "confidence_score": 60,
                          "signals": []}]}
        sealed = seal_pick_payload(p)
        # QCM not available — picks untouched.
        assert sealed["picks"][0]["confidence_score"] == 60
        assert "qcm_audit" not in sealed or sealed.get("qcm_audit", {}).get("applied_count", 0) == 0

    def test_seal_does_not_invent_picks_when_absent(self):
        p = self._base_payload()
        sealed = seal_pick_payload(p)
        # No picks[] supplied — applier must NOT create one.
        assert "picks" not in sealed or sealed["picks"] is None or sealed["picks"] == []

    def test_seal_preserves_ordering_of_picks(self):
        p = self._base_payload()
        p["picks"] = [
            {"market": "OVER_8_5", "confidence_score": 60, "signals": []},
            {"market": "MONEYLINE", "confidence_score": 50, "signals": []},
            {"market": "OVER_TEAM_TOTAL_3_5", "confidence_score": 55,
              "signals": []},
        ]
        sealed = seal_pick_payload(p)
        markets = [p["market"] for p in sealed["picks"]]
        assert markets == ["OVER_8_5", "MONEYLINE", "OVER_TEAM_TOTAL_3_5"]
        # MONEYLINE not adjusted.
        assert sealed["picks"][1]["confidence_score"] == 50

    def test_seal_emits_qcm_audit_block(self):
        p = self._base_payload()
        p["picks"] = [
            {"market": "OVER_8_5", "confidence_score": 60, "signals": []},
        ]
        sealed = seal_pick_payload(p)
        assert "qcm_audit" in sealed
        audit = sealed["qcm_audit"]
        assert audit["applied_count"] >= 1
        assert isinstance(audit["audits"], list)
        # Each pick gets a per-index audit row.
        assert audit["audits"][0]["pick_index"] == 0
        assert audit["audits"][0]["market"] == "OVER_8_5"
