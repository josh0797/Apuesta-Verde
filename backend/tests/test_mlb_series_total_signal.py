"""Sprint D9.3-B · mlb_series_total_signal — tests del módulo puro.

Cubre:
  * Weighted mean con pesos por recencia (1.00/0.75/0.55/0.40/0.30) y
    pesos por bloque (active=1.0, h2h=0.45).
  * Shrinkage `n/(n+3)` con cap del 30 % sobre la proyección base.
  * Clamp final `series_adjustment ∈ [-1.25, +1.25]`.
  * CV bandas: STABLE / MEDIUM / VOLATILE / UNKNOWN.
  * Slope con guard `n<3 → INSUFFICIENT_SAMPLE_FOR_SERIES_TREND`.
  * Score de influencia `[-10, +10]` con desglose por componente.
  * Confidence modifier capeado a `±5`.
  * Fail-soft con muestras vacías / inputs None.
"""
from __future__ import annotations

import math
import pytest

from services.mlb_series_total_signal import (
    calculate_series_total_signal,
    SHRINKAGE_K,
    MAX_INFLUENCE_PCT,
    ADJUSTMENT_CLAMP,
    CONFIDENCE_MODIFIER_CAP,
    SCORE_CAP,
    ACTIVE_SERIES_WEIGHT,
    PREVIOUS_SERIES_H2H_WEIGHT,
)


# ─── Fail-soft ─────────────────────────────────────────────────────────
def test_empty_inputs_returns_unavailable_payload():
    out = calculate_series_total_signal(7.0, 9.5, [], [])
    assert out["available"] is False
    assert out["reason_code"] == "NO_SERIES_SAMPLE"
    assert out["series_context_score"] == 0.0
    assert out["confidence_modifier"] == 0.0
    assert out["adjusted_expected_runs"] is None


def test_none_inputs_handled_gracefully():
    out = calculate_series_total_signal(None, None, None, None)
    assert out["available"] is False


def test_invalid_game_filtered_out():
    """Games sin total_runs numérico o negativos se filtran."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": "not-a-number"},
            {"total_runs": -3},
            {"total_runs": float("nan")},
            {"total_runs": 8},
        ],
    )
    assert out["available"] is True
    assert out["n_active"] == 1
    assert out["weighted_series_runs"] == 8.0


# ─── Weighted mean / pesos de bloque ──────────────────────────────────
def test_recency_weights_most_recent_dominates():
    """Game 3 más reciente debería pesar 1.0; game 1 menos."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 4, "game_number": 1},
            {"total_runs": 5, "game_number": 2},
            {"total_runs": 12, "game_number": 3},   # más reciente
        ],
    )
    # Weighted = (4*0.55 + 5*0.75 + 12*1.00) / (0.55+0.75+1.00) = 17.95/2.30 ≈ 7.804
    assert out["available"] is True
    expected = (4 * 0.55 + 5 * 0.75 + 12 * 1.00) / (0.55 + 0.75 + 1.00)
    assert math.isclose(out["weighted_series_runs"], expected, abs_tol=1e-3)


def test_h2h_block_weight_is_lower_than_active():
    """H2H previos reciben weight 0.45 vs 1.0 de active series."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[{"total_runs": 5, "game_number": 1}],
        recent_h2h_games=[{"total_runs": 15}],
    )
    # Pesos: active recency=1.0 * block=1.0 = 1.0
    #        h2h recency=1.0 * block=0.45 = 0.45
    # Weighted = (5*1.0 + 15*0.45) / (1.0 + 0.45) = 11.75/1.45 ≈ 8.103
    expected = (5 * 1.0 + 15 * 0.45) / (1.0 + 0.45)
    assert math.isclose(out["weighted_series_runs"], expected, abs_tol=1e-3)


# ─── Shrinkage + cap 30 % ─────────────────────────────────────────────
def test_one_game_low_influence_due_to_shrinkage():
    """1 partido: reliability = 1/(1+3)=0.25; cap 0.30; influence = 0.075.
    Series weighted=20 vs base 7.0 → adjusted ≈ 7.0+0.975 = 7.975 (no
    explota la proyección)."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[{"total_runs": 20, "game_number": 1}],
    )
    reliab = 1 / (1 + SHRINKAGE_K)
    assert math.isclose(out["series_reliability"], reliab, abs_tol=1e-4)
    weight_on_series = reliab * MAX_INFLUENCE_PCT  # 0.075
    expected_adj = 7.0 * (1 - weight_on_series) + 20 * weight_on_series
    assert math.isclose(out["adjusted_expected_runs"], expected_adj, abs_tol=1e-3)
    assert abs(out["series_adjustment"]) <= ADJUSTMENT_CLAMP


def test_shrinkage_grows_with_sample():
    """Reliability con n=5 > reliability con n=1."""
    out_small = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[{"total_runs": 8, "game_number": 1}],
    )
    out_large = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 8, "game_number": i+1} for i in range(5)
        ],
    )
    assert out_large["series_reliability"] > out_small["series_reliability"]


def test_extreme_series_adjustment_clamped_to_125():
    """Aunque la serie promedie 25 carreras y la base ER sea 5.0 con
    n=15, el clamp ±1.25 debe activarse."""
    out = calculate_series_total_signal(
        5.0, 9.5,
        active_series_games=[
            {"total_runs": 25, "game_number": i+1} for i in range(15)
        ],
    )
    assert out["series_adjustment"] <=  ADJUSTMENT_CLAMP + 1e-9
    assert out["series_adjustment"] >= -ADJUSTMENT_CLAMP - 1e-9
    # adjusted_expected_runs nunca debe alejarse del base más allá de 1.25.
    assert abs(out["adjusted_expected_runs"] - 5.0) <= ADJUSTMENT_CLAMP + 1e-9


# ─── Edge + bandas ────────────────────────────────────────────────────
def test_edge_runs_and_band_neutral():
    """Base ER alineado con la línea → edge ≈ 0 → NEUTRAL."""
    out = calculate_series_total_signal(
        9.5, 9.5,
        active_series_games=[{"total_runs": 10, "game_number": 1}],
    )
    assert out["edge_band"] == "NEUTRAL"


def test_edge_band_strong_under_when_series_far_below_line():
    """Multiple high-sample games well below the line."""
    out = calculate_series_total_signal(
        5.0, 9.5,
        active_series_games=[
            {"total_runs": 3, "game_number": 1},
            {"total_runs": 4, "game_number": 2},
            {"total_runs": 3, "game_number": 3},
            {"total_runs": 4, "game_number": 4},
            {"total_runs": 3, "game_number": 5},
        ],
    )
    # series_edge_runs ≤ -1.25 → STRONG_UNDER ... pero clamp 1.25 evita
    # que adjusted - market sea muy negativo. Esperamos al menos
    # MODERATE_UNDER.
    assert out["edge_band"] in ("STRONG_UNDER", "MODERATE_UNDER")


def test_edge_none_when_market_total_missing():
    out = calculate_series_total_signal(
        7.0, None,
        active_series_games=[{"total_runs": 8, "game_number": 1}],
    )
    assert out["series_edge_runs"] is None
    assert out["edge_band"] == "UNKNOWN"


# ─── Slope ────────────────────────────────────────────────────────────
def test_slope_insufficient_when_less_than_3_games():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 5, "game_number": 1},
            {"total_runs": 9, "game_number": 2},
        ],
    )
    assert out["series_slope"] is None
    assert out["slope_band"] == "INSUFFICIENT_SAMPLE_FOR_SERIES_TREND"
    assert "INSUFFICIENT_SAMPLE_FOR_SERIES_TREND" in out["reason_codes"]


def test_slope_expansion_strong_when_runs_climb_fast():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 3, "game_number": 1},
            {"total_runs": 6, "game_number": 2},
            {"total_runs": 10, "game_number": 3},
        ],
    )
    # slope (3,6,10) — least squares pendiente ≈ 3.5 → EXPANSION_STRONG.
    assert out["series_slope"] is not None
    assert out["series_slope"] > 1.0
    assert out["slope_band"] == "EXPANSION_STRONG"


def test_slope_contraction_strong_when_runs_collapse():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 14, "game_number": 1},
            {"total_runs": 8, "game_number": 2},
            {"total_runs": 4, "game_number": 3},
        ],
    )
    assert out["series_slope"] < -1.0
    assert out["slope_band"] == "CONTRACTION_STRONG"


# ─── CV bandas ────────────────────────────────────────────────────────
def test_cv_band_stable():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 8, "game_number": i+1} for i in range(5)
        ],
    )
    assert out["variability"]["band"] == "STABLE"


def test_cv_band_volatile():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 2, "game_number": 1},
            {"total_runs": 15, "game_number": 2},
            {"total_runs": 4, "game_number": 3},
            {"total_runs": 12, "game_number": 4},
        ],
    )
    assert out["variability"]["band"] == "VOLATILE"


# ─── Series context score ─────────────────────────────────────────────
def test_score_breakdown_components_present_and_clamped():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 4, "game_number": 1},
            {"total_runs": 4, "game_number": 2},
            {"total_runs": 5, "game_number": 3},
        ],
        bullpen_fatigue_component=2.0,
        pitching_matchup_component=-1.5,
    )
    bd = out["score_breakdown"]
    assert set(bd.keys()) == {"edge_runs", "slope", "bullpen_fatigue",
                                "pitching_matchup", "variance"}
    assert bd["bullpen_fatigue"] == 2.0
    assert bd["pitching_matchup"] == -1.5
    assert abs(out["series_context_score"]) <= SCORE_CAP + 1e-9


def test_score_clamped_to_plus_minus_10():
    """Forzar componentes saturados POSITIVOS: serie muy ofensiva por
    encima de la línea + slope positivo + bullpen/pitching +3 cada uno
    → suma > 10 pero score se clampa a 10."""
    out = calculate_series_total_signal(
        11.0, 8.5,
        active_series_games=[
            {"total_runs": 11, "game_number": 1},
            {"total_runs": 12, "game_number": 2},
            {"total_runs": 13, "game_number": 3},
        ],
        bullpen_fatigue_component=5.0,        # se clampará a 3
        pitching_matchup_component=5.0,       # se clampará a 3
    )
    assert out["series_context_score"] == SCORE_CAP
    # Componentes individuales también respetan sus caps.
    assert out["score_breakdown"]["bullpen_fatigue"] == 3.0
    assert out["score_breakdown"]["pitching_matchup"] == 3.0


def test_score_negative_when_series_below_line_and_dropping():
    """Serie por debajo de la línea + contracción + bullpen descansado
    (componente negativo) → score < 0."""
    out = calculate_series_total_signal(
        6.0, 9.5,
        active_series_games=[
            {"total_runs": 9, "game_number": 1},
            {"total_runs": 6, "game_number": 2},
            {"total_runs": 3, "game_number": 3},
        ],
        bullpen_fatigue_component=-2.0,
    )
    assert out["series_context_score"] < 0
    assert out["confidence_modifier"] < 0


def test_confidence_modifier_capped_to_plus_minus_5():
    """Aunque score=±10, confidence_modifier=±5."""
    out = calculate_series_total_signal(
        11.0, 8.5,
        active_series_games=[
            {"total_runs": 11, "game_number": 1},
            {"total_runs": 12, "game_number": 2},
            {"total_runs": 13, "game_number": 3},
        ],
        bullpen_fatigue_component=5.0,
        pitching_matchup_component=5.0,
    )
    assert out["series_context_score"] == SCORE_CAP
    assert out["confidence_modifier"] == CONFIDENCE_MODIFIER_CAP


def test_confidence_modifier_half_of_score():
    """En el rango lineal, confidence_modifier = score * 0.5."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 8, "game_number": 1},
            {"total_runs": 8, "game_number": 2},
            {"total_runs": 8, "game_number": 3},
        ],
    )
    assert math.isclose(out["confidence_modifier"],
                          out["series_context_score"] * 0.5,
                          abs_tol=1e-3)


# ─── Reason codes ─────────────────────────────────────────────────────
def test_limited_sample_flag_when_total_below_3():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[{"total_runs": 8, "game_number": 1}],
    )
    assert "LIMITED_SAMPLE_SERIES_SIGNAL" in out["reason_codes"]


def test_no_base_er_flag():
    out = calculate_series_total_signal(
        None, 9.5,
        active_series_games=[{"total_runs": 8, "game_number": 1}],
    )
    assert out["adjusted_expected_runs"] is None
    assert "NO_BASE_EXPECTED_RUNS" in out["reason_codes"]


def test_observe_only_flag_always_present():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[{"total_runs": 8, "game_number": 1}],
    )
    assert out["observe_only"] is True


# ─── Block weight constants sanity ────────────────────────────────────
def test_block_weights_constants_align_with_spec():
    assert ACTIVE_SERIES_WEIGHT == 1.0
    assert PREVIOUS_SERIES_H2H_WEIGHT == 0.45


def test_recency_weights_constants_align_with_spec():
    from services.mlb_series_total_signal import RECENCY_WEIGHTS
    assert RECENCY_WEIGHTS == (1.00, 0.75, 0.55, 0.40, 0.30)
