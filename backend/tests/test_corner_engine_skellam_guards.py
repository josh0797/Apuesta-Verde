"""Sprint Corner Fase B · Guards defensivos del modelo Skellam.

Verifica:
  * ``_compute_lambda`` reporta ``LAMBDA_SATURATED`` cuando λ alcanza el cap.
  * ``_compute_lambda`` reporta ``LAMBDA_HIGH_WARNING`` para λ ∈ [12, 18).
  * ``_compute_lambda`` reporta ``DRIVER_DOMINANT_<feature>`` cuando un
    driver individual contribuye >2 al exponente z.
  * ``validate_skellam_coefs`` detecta:
      - Coeficientes con |β| > 2 (excl. intercept) → ``SKELLAM_COEFS_SUSPICIOUS_<...>_ABS_<v>``
      - Signos opuestos no triviales entre home/away para la misma
        feature → ``SKELLAM_COEFS_SUSPICIOUS_OPPOSITE_SIGNS_<feature>``
  * Con los coefs persistidos en ``calibrated_defaults.json`` y un rango
    realista de features, λ ∈ [1, 12] (sanity check anti-explosión).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.football.corners.skellam_corner_model import (
    DEFAULT_LAMBDA_COEFS_HOME,
    DEFAULT_LAMBDA_COEFS_AWAY,
    LAMBDA_MAX,
    LAMBDA_WARNING_THRESHOLD,
    _compute_lambda,
    predict_skellam_corner_diff,
    validate_skellam_coefs,
)


# --------------------------------------------------------------
# Guards en _compute_lambda
# --------------------------------------------------------------

def test_compute_lambda_normal_no_warnings():
    """Caso normal: features típicos + defaults → sin warnings de saturación."""
    lam, drivers, warns = _compute_lambda(
        corners_for_L15=5.5,
        corners_against_opp_L15=5.0,
        xg_for_L15=1.5,
        deep_allowed_opp_L15=7.0,
        implied_prob=0.45,
        coefs=DEFAULT_LAMBDA_COEFS_HOME,
    )
    assert 1.0 <= lam <= LAMBDA_WARNING_THRESHOLD
    assert warns == []


def test_compute_lambda_saturates_with_huge_coefs():
    """Coefs explosivos + features grandes → λ saturado y reason code."""
    huge_coefs = {
        "intercept": 1.0,
        "corners_for_L15": 0.5,           # 0.5 * 12 = 6 → exp(7)≈1100 ⇒ cap a 18
        "corners_against_L15": 0.1,
        "xg_for_L15": 0.5,
        "deep_allowed_L15": 0.1,
        "implied_prob": 1.0,
        "xg_deep_interaction": 0.0,
    }
    lam, drivers, warns = _compute_lambda(
        corners_for_L15=12.0, corners_against_opp_L15=2.0,
        xg_for_L15=3.5, deep_allowed_opp_L15=2.0,
        implied_prob=0.95, coefs=huge_coefs,
    )
    assert lam == LAMBDA_MAX
    assert "LAMBDA_SATURATED" in warns
    # corners_for_L15 * 12 = 6.0 → debe ser reportado como driver dominante
    assert any(w.startswith("DRIVER_DOMINANT_CORNERS_FOR_L15") for w in warns)


def test_compute_lambda_high_warning_below_max():
    """λ ∈ [12, 18) → LAMBDA_HIGH_WARNING pero no SATURATED."""
    # Construir un caso donde z ≈ log(13) ≈ 2.565
    coefs = {
        "intercept": 1.5,
        "corners_for_L15": 0.1,           # 0.1 * 10 = 1.0
        "corners_against_L15": 0.0,
        "xg_for_L15": 0.0,
        "deep_allowed_L15": 0.0,
        "implied_prob": 0.0,
        "xg_deep_interaction": 0.0,
    }
    lam, _, warns = _compute_lambda(
        corners_for_L15=10.0,
        corners_against_opp_L15=None,
        xg_for_L15=None, deep_allowed_opp_L15=None,
        implied_prob=None, coefs=coefs,
    )
    # exp(2.5) ≈ 12.18 → entre threshold y max
    assert LAMBDA_WARNING_THRESHOLD <= lam < LAMBDA_MAX
    assert "LAMBDA_HIGH_WARNING" in warns
    assert "LAMBDA_SATURATED" not in warns


def test_compute_lambda_driver_dominant_warning():
    """Driver con |contribución| > 2.0 → DRIVER_DOMINANT_<feature>."""
    coefs = {
        "intercept": 0.5,
        "corners_for_L15": 0.0,
        "corners_against_L15": 0.0,
        "xg_for_L15": 0.0,
        "deep_allowed_L15": 0.0,
        "implied_prob": 2.5,              # 2.5 * 0.9 = 2.25 → >DRIVER_DOMINANT_ABS
        "xg_deep_interaction": 0.0,
    }
    lam, drivers, warns = _compute_lambda(
        corners_for_L15=None,
        corners_against_opp_L15=None,
        xg_for_L15=None, deep_allowed_opp_L15=None,
        implied_prob=0.9, coefs=coefs,
    )
    assert any(w.startswith("DRIVER_DOMINANT_IMPLIED_PROB") for w in warns)


# --------------------------------------------------------------
# Validación de coefs
# --------------------------------------------------------------

def test_validate_coefs_detects_large_magnitude():
    """|β| > 2 en feature no-intercept → warning con magnitud."""
    bad = dict(DEFAULT_LAMBDA_COEFS_HOME)
    bad["xg_for_L15"] = 3.0
    issues = validate_skellam_coefs(bad, DEFAULT_LAMBDA_COEFS_AWAY)
    assert any("SUSPICIOUS_HOME_XG_FOR_L15" in i for i in issues)
    assert any("ABS_3.00" in i for i in issues)


def test_validate_coefs_detects_opposite_signs():
    """Signos opuestos no-triviales (|β|>0.05) → warning."""
    ch = dict(DEFAULT_LAMBDA_COEFS_HOME)
    ca = dict(DEFAULT_LAMBDA_COEFS_AWAY)
    ch["deep_allowed_L15"] = -0.5
    ca["deep_allowed_L15"] = +1.2
    issues = validate_skellam_coefs(ch, ca)
    assert any("OPPOSITE_SIGNS_DEEP_ALLOWED_L15" in i for i in issues)


def test_validate_coefs_ignores_trivial_opposite_signs():
    """Coefs casi-cero con signos opuestos no deben disparar warning."""
    ch = dict(DEFAULT_LAMBDA_COEFS_HOME)
    ca = dict(DEFAULT_LAMBDA_COEFS_AWAY)
    ch["xg_for_L15"] = -0.01    # magnitud trivial
    ca["xg_for_L15"] = +0.02
    issues = validate_skellam_coefs(ch, ca)
    assert not any("OPPOSITE_SIGNS_XG_FOR_L15" in i for i in issues)


def test_validate_coefs_clean_defaults():
    """Los defaults limpios no deben disparar issues (todos sub-2.0 y mismos signos)."""
    issues = validate_skellam_coefs(DEFAULT_LAMBDA_COEFS_HOME,
                                      DEFAULT_LAMBDA_COEFS_AWAY)
    assert issues == []


# --------------------------------------------------------------
# Sanity: coefs persistidos en producción no producen λ irreal
# --------------------------------------------------------------

CALIB_PATH = (Path(__file__).resolve().parents[1]
              / "services" / "football" / "corners" / "calibrated_defaults.json")


@pytest.fixture(scope="module")
def calibrated_skellam_coefs():
    if not CALIB_PATH.exists():
        pytest.skip("calibrated_defaults.json no existe")
    d = json.loads(CALIB_PATH.read_text())
    return d.get("skellam_coefs_home"), d.get("skellam_coefs_away")


@pytest.mark.parametrize("ctx", [
    # Gran favorito local
    {"home_implied_prob": 0.75, "away_implied_prob": 0.10,
     "home_corners_for_L15": 8.0, "home_corners_against_L15": 3.0,
     "away_corners_for_L15": 3.5, "away_corners_against_L15": 7.0,
     "home_xg_for_L15": 2.5, "away_xg_for_L15": 0.8,
     "home_deep_allowed_L15": 3.0, "away_deep_allowed_L15": 11.0},
    # Partido parejo
    {"home_implied_prob": 0.40, "away_implied_prob": 0.32,
     "home_corners_for_L15": 5.2, "home_corners_against_L15": 4.8,
     "away_corners_for_L15": 4.8, "away_corners_against_L15": 5.0,
     "home_xg_for_L15": 1.5, "away_xg_for_L15": 1.3,
     "home_deep_allowed_L15": 6.5, "away_deep_allowed_L15": 7.0},
    # Favorito visitante extremo
    {"home_implied_prob": 0.12, "away_implied_prob": 0.72,
     "home_corners_for_L15": 3.5, "home_corners_against_L15": 7.0,
     "away_corners_for_L15": 7.5, "away_corners_against_L15": 3.5,
     "home_xg_for_L15": 0.9, "away_xg_for_L15": 2.3,
     "home_deep_allowed_L15": 10.0, "away_deep_allowed_L15": 3.5},
])
def test_calibrated_coefs_produce_realistic_lambdas(calibrated_skellam_coefs, ctx):
    """Con los coefs persistidos en disco, ningún contexto realista produce
    λ saturado. Cap del rango esperado: [1, 12]."""
    ch, ca = calibrated_skellam_coefs
    if not ch or not ca:
        pytest.skip("calibrated coefs vacíos")
    res = predict_skellam_corner_diff(ctx, coefs_home=ch, coefs_away=ca)
    assert 1.0 <= res["lambda_h"] <= 12.0, f"λ_h fuera de rango: {res['lambda_h']}"
    assert 1.0 <= res["lambda_a"] <= 12.0, f"λ_a fuera de rango: {res['lambda_a']}"
    # Tampoco esperamos saturated warning con coefs producción + ctx realistas
    sat_codes = [c for c in res["reason_codes"]
                  if "LAMBDA_SATURATED" in c]
    assert not sat_codes, f"λ saturado inesperado: {sat_codes}"


def test_calibrated_coefs_known_multicollinearity_documented(calibrated_skellam_coefs):
    """Documenta el warning conocido de coefs persistidos: signo opuesto
    en deep_allowed_L15 (-0.569 home vs +1.329 away). Este test deja
    constancia de que el sistema lo reporta correctamente sin romper
    el flujo."""
    ch, ca = calibrated_skellam_coefs
    if not ch or not ca:
        pytest.skip("calibrated coefs vacíos")
    issues = validate_skellam_coefs(ch, ca)
    # Cualquiera de estos warnings es esperado: NO debe romper el sistema
    # pero SÍ debe estar reportado.
    has_signs_warn = any("OPPOSITE_SIGNS" in i for i in issues)
    has_large_warn = any("SUSPICIOUS" in i for i in issues)
    # Al menos uno está presente con los coefs actuales de producción.
    assert has_signs_warn or has_large_warn, (
        "Los coefs persistidos tienen multicolinealidad documentada — "
        "se espera que validate_skellam_coefs la reporte."
    )
