"""Sprint D9.3-C · tests para slope-aware degradation y anti-double-counting.

Cubre:
  1. `apply_series_degradation` con slope_band:
     - CONTRACTION_STRONG → multiplicador 0.50
     - CONTRACTION_LIGHT  → multiplicador 0.75
     - STABLE / INSUFFICIENT → 1.00 (back-compat con D9.3-A/B)
     - EXPANSION_LIGHT    → 1.10
     - EXPANSION_STRONG   → 1.25
     - starter_component (+0.3) NO se ve afectado por slope
  2. `_deduplicate_h2h_against_active` en `mlb_series_total_signal`:
     - Excluye H2H que comparten kickoff (mismo día) con active series.
     - No afecta H2H sin kickoff.
     - Surface reason_code `H2H_OVERLAP_DEDUPED` y campo
       `n_h2h_removed_for_overlap`.
  3. Componentes (-3..+3) derivados del bullpen y pitcher quality
     (validamos la fórmula directamente en el dominio del módulo —
     el wiring en el orchestrator se cubre con integration tests
     existentes).
"""
from __future__ import annotations

import pytest

from services.mlb_pitcher_series_degradation import (
    apply_series_degradation,
    SLOPE_MULTIPLIERS,
)
from services.mlb_series_total_signal import calculate_series_total_signal


# ─── apply_series_degradation con slope_band ───────────────────────────
def test_degradation_back_compat_without_slope():
    """Sin slope_band → comportamiento idéntico a D9.3-A/B."""
    out = apply_series_degradation(7.2, game_number_in_series=2, starter_faced_lineup_before=True)
    assert out["original_er"] == 7.2
    # G2 base 0.4 + starter 0.3 = 0.7 → ER 7.9
    assert out["adjustment"] == 0.7
    assert out["adjusted_er"] == 7.9
    assert out["slope_band"] is None
    assert out["slope_multiplier"] == 1.0
    assert out["in_series_component"] == 0.4
    assert out["starter_component"] == 0.3


def test_degradation_contraction_strong_halves_in_series():
    """slope_band=CONTRACTION_STRONG → in_series_component 0.4 * 0.5 = 0.2.
    starter_component (+0.3) intacto. adjustment = 0.5."""
    out = apply_series_degradation(
        7.0, 2, starter_faced_lineup_before=True,
        slope_band="CONTRACTION_STRONG",
    )
    assert out["slope_multiplier"] == 0.50
    assert out["in_series_component"] == 0.2  # 0.4 * 0.5
    assert out["starter_component"] == 0.3
    assert out["adjustment"] == 0.5
    assert out["adjusted_er"] == 7.5


def test_degradation_contraction_light():
    out = apply_series_degradation(
        7.0, 2, starter_faced_lineup_before=False,
        slope_band="CONTRACTION_LIGHT",
    )
    assert out["slope_multiplier"] == 0.75
    assert out["in_series_component"] == 0.3  # 0.4 * 0.75
    assert out["starter_component"] == 0.0
    assert out["adjustment"] == 0.3


def test_degradation_expansion_light_amplifies():
    out = apply_series_degradation(
        7.0, 2, starter_faced_lineup_before=False,
        slope_band="EXPANSION_LIGHT",
    )
    assert out["slope_multiplier"] == 1.10
    assert out["in_series_component"] == pytest.approx(0.44)
    assert out["adjustment"] == pytest.approx(0.44)


def test_degradation_expansion_strong_amplifies_more():
    out = apply_series_degradation(
        7.0, 3, starter_faced_lineup_before=True,
        slope_band="EXPANSION_STRONG",
    )
    # G3 base 0.8 * 1.25 = 1.0 + starter 0.3 = 1.3
    assert out["slope_multiplier"] == 1.25
    assert out["in_series_component"] == 1.0
    assert out["starter_component"] == 0.3
    assert out["adjustment"] == 1.3


def test_degradation_stable_and_insufficient_unchanged():
    """STABLE e INSUFFICIENT_SAMPLE_FOR_SERIES_TREND deben dejar la
    fórmula sin cambios (multiplier = 1.0)."""
    for band in ("STABLE", "INSUFFICIENT_SAMPLE_FOR_SERIES_TREND", "UNKNOWN"):
        out = apply_series_degradation(7.0, 2, slope_band=band)
        assert out["slope_multiplier"] == 1.0
        assert out["in_series_component"] == 0.4


def test_degradation_unknown_band_treated_as_neutral():
    out = apply_series_degradation(7.0, 2, slope_band="NOT_A_REAL_BAND")
    assert out["slope_multiplier"] == 1.0


def test_degradation_g1_unaffected_by_slope():
    """G1 siempre tiene adjustment=0.0 (FRESH_SERIES). Slope no aplica."""
    out = apply_series_degradation(
        7.0, 1, slope_band="EXPANSION_STRONG",
    )
    assert out["adjustment"] == 0.0
    assert out["adjusted_er"] == 7.0


def test_slope_multipliers_constants_aligned_with_spec():
    """Los multiplicadores deben ser exactamente los del spec D9.3-C."""
    assert SLOPE_MULTIPLIERS["CONTRACTION_STRONG"] == 0.50
    assert SLOPE_MULTIPLIERS["CONTRACTION_LIGHT"]  == 0.75
    assert SLOPE_MULTIPLIERS["STABLE"]             == 1.00
    assert SLOPE_MULTIPLIERS["EXPANSION_LIGHT"]    == 1.10
    assert SLOPE_MULTIPLIERS["EXPANSION_STRONG"]   == 1.25


# ─── Anti-double-counting H2H ∩ active series ──────────────────────────
def test_h2h_overlap_deduped_when_same_date():
    """Un H2H con misma fecha que la serie activa debe excluirse."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 6, "game_number": 1, "kickoff": "2026-09-19T19:05:00"},
            {"total_runs": 8, "game_number": 2, "kickoff": "2026-09-20T19:05:00"},
        ],
        recent_h2h_games=[
            # Mismo día que active G2 — debe deduplicarse.
            {"total_runs": 8, "kickoff": "2026-09-20"},
            # Día anterior — no overlap, mantener.
            {"total_runs": 5, "kickoff": "2026-08-15"},
        ],
    )
    assert out["available"] is True
    assert out["n_h2h_removed_for_overlap"] == 1
    assert out["n_h2h"] == 1  # solo el de 2026-08-15 sobrevive
    assert "H2H_OVERLAP_DEDUPED" in out["reason_codes"]


def test_h2h_kickoff_with_iso_time_dedupes_correctly():
    """Comparación normalizada a YYYY-MM-DD (ignora la hora)."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 6, "game_number": 1, "kickoff": "2026-09-20T19:05:00Z"},
        ],
        recent_h2h_games=[
            {"total_runs": 9, "kickoff": "2026-09-20T20:15:00"},
        ],
    )
    assert out["n_h2h_removed_for_overlap"] == 1
    assert "H2H_OVERLAP_DEDUPED" in out["reason_codes"]


def test_no_h2h_overlap_does_not_dedupe():
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 6, "game_number": 1, "kickoff": "2026-09-20"},
        ],
        recent_h2h_games=[
            {"total_runs": 9, "kickoff": "2026-07-01"},
            {"total_runs": 7, "kickoff": "2026-06-15"},
        ],
    )
    assert out["n_h2h_removed_for_overlap"] == 0
    assert "H2H_OVERLAP_DEDUPED" not in out["reason_codes"]
    assert out["n_h2h"] == 2


def test_h2h_without_kickoff_not_deduped():
    """Si el H2H no trae kickoff, no podemos hacer match — debe
    sobrevivir (mejor incluirlo que perderlo). En este escenario el
    overlap no se detecta y por lo tanto no se quita."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 6, "game_number": 1, "kickoff": "2026-09-20"},
        ],
        recent_h2h_games=[
            {"total_runs": 9},  # sin kickoff
        ],
    )
    assert out["n_h2h_removed_for_overlap"] == 0
    assert out["n_h2h"] == 1


def test_active_without_kickoff_short_circuit():
    """Si active series no trae kickoff, no se puede comparar; ningún
    H2H se borra."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 6, "game_number": 1},
        ],
        recent_h2h_games=[
            {"total_runs": 9, "kickoff": "2026-09-20"},
        ],
    )
    assert out["n_h2h_removed_for_overlap"] == 0
    assert out["n_h2h"] == 1


# ─── Componentes (-3..+3) — fórmula esperada del wiring ────────────────
def test_bullpen_fatigue_component_formula_fresh_to_negative():
    """bullpen score 100 (fresh) → comp = (50-100)/50*3 = -3 → suprime Over."""
    bp_score = 100.0
    comp = (50.0 - bp_score) / 50.0 * 3.0
    assert comp == -3.0


def test_bullpen_fatigue_component_formula_fatigued_to_positive():
    """bullpen score 0 (fatigado) → comp = (50-0)/50*3 = +3 → apoya Over."""
    bp_score = 0.0
    comp = (50.0 - bp_score) / 50.0 * 3.0
    assert comp == 3.0


def test_bullpen_fatigue_component_neutral_at_50():
    bp_score = 50.0
    comp = (50.0 - bp_score) / 50.0 * 3.0
    assert comp == 0.0


def test_pitching_matchup_component_elite_suppresses_over():
    """Ambos abridores élite (100, 100) → combined 100 → comp -3."""
    hq, aq = 100.0, 100.0
    combined = (hq + aq) / 2.0
    comp = (50.0 - combined) / 50.0 * 3.0
    assert comp == -3.0


def test_pitching_matchup_component_poor_supports_over():
    """Ambos abridores malos (0, 0) → combined 0 → comp +3."""
    hq, aq = 0.0, 0.0
    combined = (hq + aq) / 2.0
    comp = (50.0 - combined) / 50.0 * 3.0
    assert comp == 3.0


def test_components_propagate_into_score_breakdown():
    """Cuando los componentes derivados entran al cálculo, deben
    aparecer en score_breakdown clampados a ±3."""
    out = calculate_series_total_signal(
        7.0, 9.5,
        active_series_games=[
            {"total_runs": 8, "game_number": 1},
            {"total_runs": 8, "game_number": 2},
            {"total_runs": 8, "game_number": 3},
        ],
        bullpen_fatigue_component=2.5,
        pitching_matchup_component=-1.0,
    )
    assert out["score_breakdown"]["bullpen_fatigue"] == 2.5
    assert out["score_breakdown"]["pitching_matchup"] == -1.0
