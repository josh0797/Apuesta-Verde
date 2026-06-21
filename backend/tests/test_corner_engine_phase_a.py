"""Sprint Corner-1 + Corner-2 · Fase A — Tests obligatorios.

Cubre los 8 escenarios del brief:
  1. Dominant favorite HOME → diff > 0, home_most_prob > away.
  2. Dominant favorite AWAY → diff < 0, away_most_prob > home.
  3. Sin favorito dominante → diff ~ 0, NO_BET si confidence baja.
  4. Missing data → no crash, fallback neutral, LOW data_quality.
  5. Asian -1.5 con diff fuerte positivo → home_-1.5_prob > home_-3.5_prob.
  6. Línea entera con push → calcula win/push/lose, fair_odds ajustadas.
  7. Backtest sin cuotas reales → REAL_ODDS_NOT_AVAILABLE en warnings.
  8. Seguridad → módulos no modifican código de producción ni picks
     existentes (test estructural).

Run:
    cd /app/backend && pytest tests/test_corner_engine_phase_a.py -v
"""
from __future__ import annotations

import pytest

from services.football.corners import (
    build_asian_corner_markets,
    build_corner_diff_distribution,
    compute_expected_corner_diff,
    predict_most_corners,
    run_corner_backtest,
)
from services.football.corners.corner_diff_distribution import fit_bucket_distributions


# ============================================================
# Tests 1-4: corner_diff_model + corner_most_model
# ============================================================

def test_01_dominant_favorite_home_produces_positive_diff_and_home_pick():
    """Caso clásico: Manchester City local (implied 0.75) recibe a equipo débil."""
    ctx = {
        "home_implied_prob": 0.75,
        "away_implied_prob": 0.10,
        "home_corners_for_L15":     6.2,
        "away_corners_for_L15":     4.1,
        "home_corners_against_L15": 3.8,
        "away_corners_against_L15": 5.5,
        "home_deep_allowed_L15":    180,
        "away_deep_allowed_L15":    320,
    }
    diff = compute_expected_corner_diff(ctx)
    assert diff["expected_corner_diff"] > 0.5, (
        f"diff should be positive, got {diff['expected_corner_diff']}")
    assert diff["favored_corner_side"] == "HOME"
    assert diff["is_dominant_favorite"] is True
    assert diff["dominant_favorite_side"] == "HOME"
    assert "DOMINANT_FAVORITE_CORNER_EDGE" in diff["reason_codes"]

    pred = predict_most_corners(ctx)
    assert pred["home_most_corners_prob"] > pred["away_most_corners_prob"]
    # Con un diff > 0.5 y confidence alta, debe recomendar HOME (no NO_BET)
    # PERO si confidence < 55 o prob < 0.58, será NO_BET — válido también
    assert pred["recommended_side"] in ("HOME", "NO_BET")
    if pred["recommended_side"] == "HOME":
        assert pred["home_most_corners_prob"] >= 0.58


def test_02_dominant_favorite_away_produces_negative_diff_and_away_pick():
    """Espejo: visitante muy favorito (Liverpool en cancha de equipo débil)."""
    ctx = {
        "home_implied_prob": 0.10,
        "away_implied_prob": 0.75,
        "home_corners_for_L15":     4.0,
        "away_corners_for_L15":     6.3,
        "home_corners_against_L15": 5.6,
        "away_corners_against_L15": 3.5,
        "home_deep_allowed_L15":    310,
        "away_deep_allowed_L15":    180,
    }
    diff = compute_expected_corner_diff(ctx)
    assert diff["expected_corner_diff"] < -0.5
    assert diff["favored_corner_side"] == "AWAY"
    assert diff["is_dominant_favorite"] is True
    assert diff["dominant_favorite_side"] == "AWAY"

    pred = predict_most_corners(ctx)
    assert pred["away_most_corners_prob"] > pred["home_most_corners_prob"]


def test_03_no_dominant_favorite_produces_near_zero_diff_and_likely_no_bet():
    """Partido parejo: ambos con probs ~ 0.40, sin DOM_FAV."""
    ctx = {
        "home_implied_prob": 0.40,
        "away_implied_prob": 0.35,
        "home_corners_for_L15":     5.0,
        "away_corners_for_L15":     5.0,
        "home_corners_against_L15": 4.5,
        "away_corners_against_L15": 4.5,
        "home_deep_allowed_L15":    250,
        "away_deep_allowed_L15":    250,
    }
    diff = compute_expected_corner_diff(ctx)
    assert abs(diff["expected_corner_diff"]) < 1.5
    assert diff["is_dominant_favorite"] is False
    assert diff["dominant_favorite_side"] == "NONE"

    pred = predict_most_corners(ctx)
    # Con diff cercano a 0 y confidence baja, probable NO_BET
    # El probabilidad del lado favorito debe ser < 0.58 (regla NO_BET)
    max_side_prob = max(pred["home_most_corners_prob"], pred["away_most_corners_prob"])
    if pred["recommended_side"] == "NO_BET":
        assert (pred["confidence"] < 55
                or max_side_prob < 0.58
                or "CORNER_DIFF_LOW_DATA_QUALITY" in pred["reason_codes"])


def test_04_missing_data_no_crash_low_data_quality():
    """Solo pasamos implied_prob — todo lo demás missing."""
    ctx = {
        "home_implied_prob": 0.45,
        "away_implied_prob": 0.35,
        # nada más
    }
    diff = compute_expected_corner_diff(ctx)
    assert "missing_fields" in diff
    assert len(diff["missing_fields"]) >= 2
    # Con solo ip_diff disponible: contrib_count=1 → LOW
    assert diff["data_quality"] == "LOW"
    assert "CORNER_DIFF_LOW_DATA_QUALITY" in diff["reason_codes"]

    pred = predict_most_corners(ctx)
    # Con LOW data quality, NO_BET
    assert pred["recommended_side"] == "NO_BET"
    assert "CORNER_DIFF_LOW_DATA_QUALITY" in pred["reason_codes"]
    # No crashea
    assert pred["home_most_corners_prob"] >= 0
    assert pred["away_most_corners_prob"] >= 0
    assert pred["tie_corners_prob"] >= 0


def test_04b_no_inputs_does_not_crash():
    """Caso extremo: context vacío."""
    ctx = {}
    diff = compute_expected_corner_diff(ctx)
    assert diff["data_quality"] == "LOW"
    pred = predict_most_corners(ctx)
    assert pred["recommended_side"] == "NO_BET"


# ============================================================
# Tests 5-6: Asian Corners
# ============================================================

def _make_bucket_stats():
    """Construye bucket_stats sintéticos para los tests de Asian Corners.
    Distribución calibrada: para bucket con expected_diff ~ +3, P(home_diff > 2) alta."""
    fake_rows = []
    # Bucket "diff <= -4": muchos diffs muy negativos
    for d in [-8, -7, -6, -5, -5, -4]:
        fake_rows.append({"expected_corner_diff": -5.0,
                            "home_corners": 3, "away_corners": 3 - d})
    # Bucket "-4 < diff <= -2"
    for d in [-4, -3, -3, -2, -2]:
        fake_rows.append({"expected_corner_diff": -3.0,
                            "home_corners": 4, "away_corners": 4 - d})
    # Bucket "-2 < diff <= -1"
    for d in [-2, -1, -1, -1, 0]:
        fake_rows.append({"expected_corner_diff": -1.5,
                            "home_corners": 5, "away_corners": 5 - d})
    # Bucket "-1 < diff < 1"
    for d in [-1, 0, 0, 0, 1]:
        fake_rows.append({"expected_corner_diff": 0.0,
                            "home_corners": 5, "away_corners": 5 - d})
    # Bucket "1 <= diff < 2"
    for d in [1, 1, 1, 2]:
        fake_rows.append({"expected_corner_diff": 1.5,
                            "home_corners": 6, "away_corners": 6 - d})
    # Bucket "2 <= diff < 4"
    for d in [2, 2, 3, 3, 3]:
        fake_rows.append({"expected_corner_diff": 3.0,
                            "home_corners": 7, "away_corners": 7 - d})
    # Bucket "diff >= 4": ample positive diffs
    for d in [4, 5, 5, 6, 7, 8, 4, 5, 6]:
        fake_rows.append({"expected_corner_diff": 5.0,
                            "home_corners": 8, "away_corners": 8 - d})
    return fit_bucket_distributions(fake_rows)


def test_05_asian_minus_1_5_higher_prob_than_minus_3_5_for_strong_fav():
    """expected_corner_diff fuerte positivo (~+5):
    Home -1.5 prob > Home -3.5 prob (más fácil cubrir el menor handicap)."""
    bucket_stats = _make_bucket_stats()
    ctx = {"expected_corner_diff": 5.0}
    dist = build_corner_diff_distribution(ctx, bucket_stats=bucket_stats)
    p_h_15 = dist["probabilities"]["home_minus_1_5"]
    p_h_35 = dist["probabilities"]["home_minus_3_5"]
    assert p_h_15 >= p_h_35, (
        f"home_-1.5 ({p_h_15}) should be >= home_-3.5 ({p_h_35})")
    assert p_h_15 > 0.5  # con expected_diff +5, +1.5 debería ser muy probable

    # Build markets, sin cuotas reales
    markets = build_asian_corner_markets(dist, book_odds=None,
                                          real_odds_available=False)
    home_15 = next(m for m in markets if m["market"] == "HOME_CORNERS_-1.5")
    home_35 = next(m for m in markets if m["market"] == "HOME_CORNERS_-3.5")
    assert home_15["prob_win"] >= home_35["prob_win"]
    # Sin cuotas reales: recommendation == NO_BET o WATCH, nunca BET
    assert home_15["recommendation"] in ("NO_BET", "WATCH")


def test_06_integer_line_calculates_win_push_lose_and_fair_odds():
    """Línea entera (Home -2.0): debe haber prob_push > 0 y fair_odds
    ajustadas por push: fair = (1 - push) / win."""
    bucket_stats = _make_bucket_stats()
    ctx = {"expected_corner_diff": 3.0}
    dist = build_corner_diff_distribution(ctx, bucket_stats=bucket_stats)

    markets = build_asian_corner_markets(dist, book_odds=None,
                                          real_odds_available=False)
    home_2 = next(m for m in markets if m["market"] == "HOME_CORNERS_-2.0")
    # Push debe estar entre 0 y 1
    assert 0.0 <= home_2["prob_push"] <= 1.0
    # Win + Push + Lose ≈ 1
    total = home_2["prob_win"] + home_2["prob_push"] + home_2["prob_lose"]
    assert abs(total - 1.0) < 0.01
    # Si hay win probability, fair_odds debe ser >= 1
    if home_2["prob_win"] > 0.01:
        assert home_2["fair_odds"] is not None
        assert home_2["fair_odds"] >= 1.0
        # Verificar fórmula: fair = (1 - push) / win
        expected_fair = (1.0 - home_2["prob_push"]) / home_2["prob_win"]
        assert abs(home_2["fair_odds"] - expected_fair) < 0.01


# ============================================================
# Tests 7: backtest sin cuotas reales
# ============================================================

def test_07_backtest_without_real_odds_marks_real_odds_not_available():
    """Backtest mínimo sobre 200 partidos sintéticos sin cuotas reales."""
    rows = []
    rng = _make_rng(seed=42)
    for season in ("2122", "2223", "2324"):
        for i in range(80):  # 80 partidos por temporada
            iph = round(0.20 + 0.6 * rng(), 2)
            ipa = round(1.0 - iph - 0.10 - 0.1 * rng(), 2)  # rough draw
            ipa = max(0.05, min(0.85, ipa))
            # Generar features históricas
            hcf = 4.5 + 2.0 * rng()
            acf = 4.5 + 2.0 * rng()
            hca = 4.5 + 2.0 * rng()
            aca = 4.5 + 2.0 * rng()
            # actual corners: dominante = más córners
            home_extra = 2.0 if iph > 0.6 else (-2.0 if ipa > 0.6 else 0.0)
            home_c = max(0, int(hcf + home_extra + rng() * 3))
            away_c = max(0, int(acf - home_extra + rng() * 3))
            rows.append({
                "match_id":   f"synth_{season}_{i}",
                "season":     season,
                "date":       f"20{season[:2]}-{((i % 9) + 1):02d}-15",
                "league":     "TEST_LEAGUE",
                "home_team":  f"Home_{i}",
                "away_team":  f"Away_{i}",
                "home_corners": home_c,
                "away_corners": away_c,
                "home_implied_prob": iph,
                "away_implied_prob": ipa,
                "home_corners_for_L15":     hcf,
                "away_corners_for_L15":     acf,
                "home_corners_against_L15": hca,
                "away_corners_against_L15": aca,
                "home_deep_allowed_L15":    200 + 100 * rng(),
                "away_deep_allowed_L15":    200 + 100 * rng(),
            })
    result = run_corner_backtest(rows, odds_lookup=None)
    assert result["real_odds_available"] is False
    assert "REAL_ODDS_NOT_AVAILABLE" in result["warnings"]
    assert result["n_total_predictions"] > 0
    # Las métricas deben estar presentes
    gm = result["global_metrics"]
    assert "brier_score" in gm
    assert "log_loss" in gm
    assert gm["brier_score"] > 0


# ============================================================
# Test 8: estructural — los módulos no modifican código de producción
# ============================================================

def test_08_modules_isolation_from_production():
    """No deben importar nada del motor de goles o de los flows actuales
    de picks."""
    import services.football.corners.corner_diff_model as m1
    import services.football.corners.corner_most_model as m2
    import services.football.corners.corner_diff_distribution as m3
    import services.football.corners.corner_backtest as m4

    # Verificar que ninguno importa de los motores existentes
    forbidden = ("football_corner_pregame",
                 "football_corners_provider",
                 "football_corner_cross_integration",
                 "corner_market_layer",
                 "live_corner_engine")
    for mod in (m1, m2, m3, m4):
        for name in forbidden:
            # No deben tener referencias a estos módulos
            assert not hasattr(mod, name), (
                f"{mod.__name__} should not reference {name}")


# ============================================================
# Helpers
# ============================================================

def _make_rng(seed: int = 42):
    """RNG determinista sin numpy import (para tests rápidos)."""
    state = [seed]
    def _next():
        # LCG
        state[0] = (state[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return state[0] / 0x7FFFFFFF
    return _next


# Extra sanity tests (no obligatorios pero útiles)

def test_extra_caps_enforced():
    """Verifica que expected_corner_diff está clamped a ±5.5 incluso con
    inputs extremos."""
    ctx = {
        "home_implied_prob": 0.95,
        "away_implied_prob": 0.02,
        "home_corners_for_L15":     12.0,
        "away_corners_for_L15":     2.0,
        "home_corners_against_L15": 1.0,
        "away_corners_against_L15": 10.0,
        "home_deep_allowed_L15":    50,
        "away_deep_allowed_L15":    400,
    }
    diff = compute_expected_corner_diff(ctx)
    assert -5.5 <= diff["expected_corner_diff"] <= 5.5


def test_extra_probabilities_sum_to_one():
    ctx = {
        "home_implied_prob": 0.50,
        "away_implied_prob": 0.30,
        "home_corners_for_L15":     5.0,
        "away_corners_for_L15":     4.5,
        "home_corners_against_L15": 4.0,
        "away_corners_against_L15": 5.0,
    }
    pred = predict_most_corners(ctx)
    total = (pred["home_most_corners_prob"] + pred["away_most_corners_prob"] +
              pred["tie_corners_prob"])
    assert abs(total - 1.0) < 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
