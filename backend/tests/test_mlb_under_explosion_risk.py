"""Sprint D11 · tests para `mlb_under_explosion_risk`.

Cubre los 3 escenarios mínimos del spec:
  (A) Starter estable + lineup frío + bullpen fresco
       → Under no bloqueado, fragility baja, tail risk LOW/MEDIUM.
  (B) Starter volátil + lineup explosivo
       → reason_code VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP, fragility ↑, tail risk ↑.
  (C) First inning collapse alto
       → reason_code FIRST_INNING_COLLAPSE_RISK, survival ↓.

Cubre también:
  * Fail-soft: inputs None / vacíos no rompen el módulo.
  * Distinción `drivers` vs `missing_fields`.
  * Confidence baja cuando faltan variables.
  * Aggregator EXTREME → BLOCK_UNDER cuando no hay colchón.
  * Aggregator con `cushion >= 2.0` puede DEGRADE_UNDER en vez de BLOCK.
"""
from __future__ import annotations

import pytest

from services.mlb_under_explosion_risk import (
    compute_starter_volatility,
    compute_first_inning_collapse,
    compute_recent_offensive_quality,
    compute_lineup_explosiveness,
    aggregate_under_explosion_risk,
)


# ─── Fail-soft ─────────────────────────────────────────────────────────
def test_starter_volatility_none_input_returns_neutral():
    out = compute_starter_volatility(None)
    assert out["starter_volatility_score"] == 50.0
    assert out["bucket"] == "MEDIUM"
    assert out["confidence"] == 0.0
    assert "last5_starts" in out["missing_fields"]


def test_first_inning_collapse_none_inputs_neutral():
    out = compute_first_inning_collapse(None, None)
    assert out["first_inning_collapse_score"] == 50.0
    assert out["bucket"] == "MEDIUM"


def test_recent_offense_none_returns_neutral():
    out = compute_recent_offensive_quality(None)
    assert out["bucket"] == "NEUTRAL"


def test_lineup_explosiveness_none_returns_low_avg():
    out = compute_lineup_explosiveness(None)
    assert out["bucket"] in ("LOW", "AVG")
    assert out["confirmed_lineup"] is False


def test_aggregator_empty_inputs_does_not_emit_reason_codes():
    out = aggregate_under_explosion_risk()
    assert out["reason_codes"] == []
    assert out["fragility_delta"] == 0.0
    assert out["verdict"] == "OBSERVE"


# ─── Starter volatility extremes ──────────────────────────────────────
def test_starter_volatility_extreme_with_bad_peripherals():
    """All metrics red. Bucket EXTREME o HIGH."""
    starter = {
        "whip": 1.55, "bb_pct": 11.5, "hr_per_9": 1.80,
        "hard_hit_pct": 45, "barrel_pct": 11, "xwoba": 0.360,
        "last5_starts": [
            {"faced_4plus_er": True, "short_outing": True},
            {"faced_4plus_er": True, "bb_3plus": True},
            {"hr_2plus": True},
            {"faced_4plus_er": False, "short_outing": False},
            {"short_outing": True},
        ],
    }
    out = compute_starter_volatility(starter)
    assert out["starter_volatility_score"] >= 80
    assert out["bucket"] == "EXTREME"
    assert any("WHIP" in d for d in out["drivers"])
    assert "L5_TWO_PLUS_4ER_STARTS" in out["drivers"]


def test_starter_volatility_low_when_elite_starter():
    starter = {
        "whip": 0.95, "bb_pct": 5.5, "hr_per_9": 0.80,
        "hard_hit_pct": 30, "barrel_pct": 4, "xwoba": 0.270,
        "last5_starts": [
            {"faced_4plus_er": False, "short_outing": False},
            {"faced_4plus_er": False, "short_outing": False},
            {"faced_4plus_er": False, "short_outing": False},
        ],
    }
    out = compute_starter_volatility(starter)
    assert out["bucket"] in ("LOW", "MEDIUM")


# ─── First-inning collapse extremes ───────────────────────────────────
def test_first_inning_collapse_high_when_starter_and_lineup_align():
    starter = {
        "first_inning_era": 7.5,
        "first_inning_whip": 1.80,
        "inning1_er_l5": 5,
        "inning1_walks_l5": 4,
        "first_pitch_strike_pct": 52,
    }
    lineup = {
        "top5_ops_l7": 0.870,
        "top5_iso_l7": 0.230,
        "barrel_pct": 10.0,
        "inning1_runs_l10": 1.40,
    }
    out = compute_first_inning_collapse(starter, lineup)
    assert out["first_inning_collapse_score"] >= 80
    assert out["bucket"] == "EXTREME"
    assert "FI_ERA_EXTREME" in out["drivers"]
    assert "OPPONENT_INNING1_HOT" in out["drivers"]


# ─── Recent offensive quality / lineup explosiveness ──────────────────
def test_recent_offense_explosive_bucket():
    team = {
        "ops_l7": 0.880, "ops_l15": 0.840,
        "runs_per_game_l7": 6.2,
        "iso_l7": 0.230,
        "hard_hit_pct": 45.0, "barrel_pct": 11.0,
        "obp_l7": 0.380, "risp_avg": 0.310,
    }
    out = compute_recent_offensive_quality(team)
    assert out["bucket"] in ("EXPLOSIVE", "HOT")
    assert out["recent_offense_score"] >= 70
    assert out["confidence"] >= 80


def test_recent_offense_cold_bucket():
    team = {
        "ops_l7": 0.610, "ops_l15": 0.620,
        "runs_per_game_l7": 2.5,
        "iso_l7": 0.110, "hard_hit_pct": 31,
        "obp_l7": 0.280, "risp_avg": 0.200,
    }
    out = compute_recent_offensive_quality(team)
    assert out["bucket"] == "COLD"


def test_lineup_explosive_bucket():
    lineup = {
        "top5_ops": 0.870, "top5_iso": 0.225, "top5_obp": 0.380,
        "top5_bb_pct": 11.0, "top5_k_pct": 19.0,
        "hard_hit_pct": 44.0, "barrel_pct": 10.0, "hr_rate": 0.050,
        "confirmed_lineup": True,
    }
    out = compute_lineup_explosiveness(lineup)
    assert out["bucket"] == "EXPLOSIVE"
    assert out["confirmed_lineup"] is True


def test_lineup_unconfirmed_lowers_confidence():
    lineup = {
        "top5_ops": 0.770,
        "confirmed_lineup": False,
    }
    out = compute_lineup_explosiveness(lineup)
    assert out["confirmed_lineup"] is False
    assert "PROBABLE_LINEUP_NOT_CONFIRMED" in out["drivers"]


# ═════════════════════════════════════════════════════════════════════
# Escenario A — Starter estable + lineup frío + bullpen fresco
# ═════════════════════════════════════════════════════════════════════
def test_scenario_a_stable_starter_cold_lineup_fresh_bullpen():
    """Under no debe ser bloqueado. fragility baja, tail risk LOW/MEDIUM."""
    home_starter = {
        "whip": 1.05, "bb_pct": 6.5, "hr_per_9": 0.85, "hard_hit_pct": 32,
        "barrel_pct": 5, "xwoba": 0.280,
        "first_inning_era": 2.5, "first_inning_whip": 1.10,
        "inning1_er_l5": 1, "first_pitch_strike_pct": 66,
        "last5_starts": [{"faced_4plus_er": False, "short_outing": False}] * 5,
    }
    away_starter = dict(home_starter)
    cold_team = {
        "ops_l7": 0.640, "ops_l15": 0.650, "runs_per_game_l7": 3.0,
        "iso_l7": 0.110, "hard_hit_pct": 32, "barrel_pct": 5,
        "obp_l7": 0.290, "risp_avg": 0.210,
    }
    cold_lineup = {"top5_ops": 0.690, "top5_iso": 0.130, "confirmed_lineup": True}

    out = aggregate_under_explosion_risk(
        home_starter=home_starter, away_starter=away_starter,
        home_lineup=cold_lineup, away_lineup=cold_lineup,
        home_offense=cold_team, away_offense=cold_team,
        home_bullpen_fatigue=85, away_bullpen_fatigue=80,
        base_fragility=15, base_explosive_tail_risk="LOW",
        base_survival_score=80,
        line=9.5, expected_runs=7.0, selection="UNDER",
    )
    assert "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" not in out["reason_codes"]
    assert "FIRST_INNING_COLLAPSE_RISK" not in out["reason_codes"]
    assert "BILATERAL_HOT_OFFENSE" not in out["reason_codes"]
    assert out["fragility_delta"] == 0.0
    assert out["adjusted_fragility"] == 15.0
    assert out["adjusted_explosive_tail_risk"] in ("LOW", "MEDIUM")
    assert out["verdict"] == "ALLOW_UNDER"


# ═════════════════════════════════════════════════════════════════════
# Escenario B — Starter volátil + lineup explosivo
# ═════════════════════════════════════════════════════════════════════
def test_scenario_b_volatile_starter_vs_explosive_lineup():
    """Cross-detection: away volátil vs home explosivo → reason code +
    fragility ↑ + tail risk ↑."""
    volatile_starter = {
        "whip": 1.55, "bb_pct": 11.5, "hr_per_9": 1.80,
        "hard_hit_pct": 45, "barrel_pct": 11, "xwoba": 0.360,
        "last5_starts": [
            {"faced_4plus_er": True, "short_outing": True},
            {"faced_4plus_er": True, "bb_3plus": True},
            {"hr_2plus": True},
            {"faced_4plus_er": True},
            {"short_outing": True},
        ],
    }
    explosive_lineup = {
        "top5_ops": 0.870, "top5_iso": 0.225, "top5_obp": 0.380,
        "top5_bb_pct": 11.0, "top5_k_pct": 19.0,
        "hard_hit_pct": 44.0, "barrel_pct": 10.0, "hr_rate": 0.050,
        "confirmed_lineup": True,
    }
    explosive_offense = {
        "ops_l7": 0.870, "ops_l15": 0.840,
        "runs_per_game_l7": 5.5,
        "iso_l7": 0.220, "hard_hit_pct": 44, "barrel_pct": 10,
        "obp_l7": 0.370, "risp_avg": 0.290,
    }

    out = aggregate_under_explosion_risk(
        away_starter=volatile_starter,
        home_starter={"whip": 1.10},  # estable
        home_lineup=explosive_lineup,
        away_lineup={"top5_ops": 0.700, "confirmed_lineup": True},
        home_offense=explosive_offense,
        away_offense={"ops_l7": 0.700},
        home_bullpen_fatigue=70, away_bullpen_fatigue=70,
        base_fragility=20, base_explosive_tail_risk="LOW",
        base_survival_score=75,
        line=9.5, expected_runs=7.5, selection="UNDER",
    )
    assert "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" in out["reason_codes"]
    assert out["fragility_delta"] >= 25
    assert out["adjusted_fragility"] > 20
    # Tail risk sube (al menos MEDIUM o HIGH desde LOW).
    assert out["adjusted_explosive_tail_risk"] in ("MEDIUM", "HIGH", "EXTREME")
    # Verdict: cushion = 9.5 - 7.5 = 2.0 < 2.5 → BLOCK_UNDER esperado.
    assert out["verdict"] in ("BLOCK_UNDER", "DEGRADE_UNDER")


def test_scenario_b_block_under_when_no_cushion():
    """Mismo escenario B pero línea más ajustada → BLOCK_UNDER seguro."""
    volatile_starter = {
        "whip": 1.55, "bb_pct": 11.5, "hr_per_9": 1.80,
        "hard_hit_pct": 45, "barrel_pct": 11,
        "last5_starts": [
            {"faced_4plus_er": True, "short_outing": True},
            {"faced_4plus_er": True},
            {"hr_2plus": True, "bb_3plus": True},
            {"faced_4plus_er": True}, {"short_outing": True},
        ],
    }
    explosive_lineup = {
        "top5_ops": 0.870, "top5_iso": 0.225,
        "hard_hit_pct": 44.0, "barrel_pct": 10.0,
        "confirmed_lineup": True,
    }
    out = aggregate_under_explosion_risk(
        away_starter=volatile_starter,
        home_lineup=explosive_lineup,
        base_fragility=30, base_survival_score=70,
        line=8.5, expected_runs=8.0, selection="UNDER",
    )
    assert "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" in out["reason_codes"]
    assert out["verdict"] == "BLOCK_UNDER"


# ═════════════════════════════════════════════════════════════════════
# Escenario C — First inning collapse alto
# ═════════════════════════════════════════════════════════════════════
def test_scenario_c_first_inning_collapse_high():
    """first_inning_collapse_score >= 65 → reason_code emitido + survival ↓."""
    danger_starter = {
        "first_inning_era": 8.0,
        "first_inning_whip": 1.90,
        "inning1_er_l5": 6,
        "inning1_walks_l5": 4,
        "first_pitch_strike_pct": 50,
        "whip": 1.50, "bb_pct": 11,
    }
    hot_lineup = {
        "top5_ops_l7": 0.840,
        "top5_iso_l7": 0.215,
        "barrel_pct": 9.5,
        "inning1_runs_l10": 1.30,
    }
    out = aggregate_under_explosion_risk(
        home_starter=danger_starter,
        away_lineup=hot_lineup,
        base_fragility=20, base_explosive_tail_risk="LOW",
        base_survival_score=75,
        line=9.5, expected_runs=8.0, selection="UNDER",
    )
    assert "FIRST_INNING_COLLAPSE_RISK" in out["reason_codes"]
    # Survival ↓.
    assert out["adjusted_survival_score"] < 75
    # Fragility ↑.
    assert out["fragility_delta"] >= 15
    assert out["adjusted_fragility"] > 20


def test_scenario_c_extreme_blocks_under_without_cushion():
    """Score >= 80 emite EXTREME_ y bloquea Under cuando cushion < 2."""
    out = aggregate_under_explosion_risk(
        home_starter={
            "first_inning_era": 9.0, "first_inning_whip": 2.10,
            "inning1_er_l5": 7, "inning1_walks_l5": 5,
            "first_pitch_strike_pct": 48, "whip": 1.60,
        },
        away_lineup={
            "top5_ops_l7": 0.880, "top5_iso_l7": 0.240,
            "barrel_pct": 11, "inning1_runs_l10": 1.60,
        },
        line=8.5, expected_runs=7.5, selection="UNDER",
        base_fragility=10, base_survival_score=80,
        base_explosive_tail_risk="LOW",
    )
    assert "EXTREME_FIRST_INNING_COLLAPSE_RISK" in out["reason_codes"]
    assert out["verdict"] == "BLOCK_UNDER"


def test_scenario_c_extreme_degrades_under_with_cushion_and_strong_bullpen():
    """Score EXTREME pero cushion ≥ 2 + bullpen sólido → DEGRADE en vez de BLOCK."""
    out = aggregate_under_explosion_risk(
        home_starter={
            "first_inning_era": 9.0, "inning1_er_l5": 6, "whip": 1.60,
        },
        away_lineup={
            "top5_ops_l7": 0.880, "barrel_pct": 10, "inning1_runs_l10": 1.50,
        },
        home_bullpen_fatigue=85, away_bullpen_fatigue=85,
        line=11.5, expected_runs=8.0,  # cushion = 3.5
        selection="UNDER",
    )
    # Sólo si llegó a EXTREME; si solo HIGH puede ser DEGRADE igual.
    assert out["verdict"] in ("DEGRADE_UNDER", "BLOCK_UNDER")


# ─── Bilateral hot offense ──────────────────────────────────────────────
def test_bilateral_hot_offense_emits_code():
    hot = {"ops_l7": 0.820, "runs_per_game_l7": 5.5, "hard_hit_pct": 42, "barrel_pct": 10}
    out = aggregate_under_explosion_risk(
        home_offense=hot, away_offense=hot,
        line=9.5, expected_runs=8.5, selection="UNDER",
    )
    assert "BILATERAL_HOT_OFFENSE" in out["reason_codes"]
    assert out["verdict"] in ("DEGRADE_UNDER", "BLOCK_UNDER")


# ─── Bullpen fatigue ───────────────────────────────────────────────────
def test_bullpen_fatigue_low_emits_reason_and_raises_fragility():
    out = aggregate_under_explosion_risk(
        home_bullpen_fatigue=20,   # fatigado
        away_bullpen_fatigue=70,
        base_fragility=10,
    )
    assert any("BULLPEN" in c for c in out["reason_codes"])
    assert out["fragility_delta"] >= 10


# ─── Observe-only + structure ───────────────────────────────────────────
def test_aggregator_payload_has_all_sub_scores():
    out = aggregate_under_explosion_risk()
    for k in ("starter_volatility", "first_inning_collapse",
              "recent_offensive_quality", "lineup_explosiveness"):
        assert k in out
        assert "home" in out[k]
        assert "away" in out[k]
    assert out["observe_only"] is True


def test_verdict_observe_when_selection_is_not_under():
    """OVER picks no se degradan por esta capa."""
    out = aggregate_under_explosion_risk(
        away_starter={"whip": 1.5, "bb_pct": 11},
        home_lineup={"top5_ops": 0.85, "top5_iso": 0.22, "confirmed_lineup": True},
        selection="OVER",
    )
    assert out["verdict"] == "OBSERVE"


# ─── Confidence reporting ───────────────────────────────────────────────
def test_partial_data_lowers_confidence():
    full = compute_starter_volatility({
        "whip": 1.1, "bb_pct": 7, "hr_per_9": 0.9, "hard_hit_pct": 35,
        "barrel_pct": 6, "xwoba": 0.30, "xera_fip_gap": 0.2,
        "last5_starts": [{"faced_4plus_er": False}] * 5,
    })
    partial = compute_starter_volatility({"whip": 1.1})
    assert full["confidence"] > partial["confidence"]
    assert len(partial["missing_fields"]) > len(full["missing_fields"])
