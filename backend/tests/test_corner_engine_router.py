"""Sprint Corner Fase B — Tests del endpoint corner_engine_router.

Cubre:
  1. Health check sin feature flags.
  2. Health check con feature flags activas.
  3. Predict con flags off → enabled=False, reason=FEATURE_FLAGS_DISABLED.
  4. Predict con flags on + dominant_fav_home → most_corners recomienda HOME.
  5. Predict con use_skellam=True → model="skellam" en response.
  6. Predict con context vacío (fail-soft, no crash).
  7. Asian corners sin book_odds → REAL_ODDS_NOT_AVAILABLE en reason_codes.
  8. Asian corners con book_odds → ev calculado.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Import el app FastAPI
from server import app  # noqa: E402

client = TestClient(app)


def _ctx_dominant_home():
    return {
        "home_team":             "Manchester City",
        "away_team":             "Sheffield United",
        "league":                "EPL",
        "season":                "2324",
        "home_implied_prob":     0.75,
        "away_implied_prob":     0.10,
        "home_corners_for_L15":  7.0,
        "away_corners_for_L15":  4.2,
        "home_corners_against_L15": 3.6,
        "away_corners_against_L15": 5.5,
        "home_xg_for_L15":       2.3,
        "away_xg_for_L15":       1.0,
        "home_deep_allowed_L15": 150,
        "away_deep_allowed_L15": 340,
    }


def _set_flags(most: bool, asian: bool):
    env = {}
    if most:
        env["ENABLE_CORNER_MOST_MODEL"] = "true"
    else:
        env["ENABLE_CORNER_MOST_MODEL"] = "false"
    if asian:
        env["ENABLE_ASIAN_CORNERS_MODEL"] = "true"
    else:
        env["ENABLE_ASIAN_CORNERS_MODEL"] = "false"
    return patch.dict(os.environ, env, clear=False)


def test_corner_engine_health_endpoint_returns_ok():
    r = client.get("/api/football/corner-engine/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "feature_flags" in body
    assert "modules" in body
    for mod in ("corner_diff_model", "corner_most_model",
                  "corner_diff_distribution", "skellam_corner_model"):
        assert body["modules"][mod] == "ok"


def test_corner_engine_predict_with_flags_off_returns_disabled():
    with _set_flags(most=False, asian=False):
        r = client.post(
            "/api/football/corner-engine/predict",
            json={"context": _ctx_dominant_home()},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["enabled"] is False
    assert body["reason"] == "FEATURE_FLAGS_DISABLED"
    assert body["most_corners"] is None
    assert body["asian_corners"] is None


def test_corner_engine_predict_dominant_favorite_home_recommends_home():
    with _set_flags(most=True, asian=True):
        r = client.post(
            "/api/football/corner-engine/predict",
            json={"context": _ctx_dominant_home()},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["enabled"] is True
    assert body["model"] == "linear_sigmoid"
    mc = body["most_corners"]
    assert mc is not None
    assert mc["home_most_corners_prob"] > mc["away_most_corners_prob"]
    # Como es dominant fav fuerte, el motor debería recomendar HOME (no NO_BET)
    assert mc["recommended_side"] == "HOME"
    assert "DOMINANT_FAVORITE_CORNER_EDGE" in mc["reason_codes"]
    # Asian corners debe tener 14 entries
    assert body["asian_corners"] is not None
    assert len(body["asian_corners"]) == 14


def test_corner_engine_predict_skellam_returns_skellam_model_label():
    with _set_flags(most=True, asian=True):
        r = client.post(
            "/api/football/corner-engine/predict",
            json={"context": {**_ctx_dominant_home(), "use_skellam": True}},
        )
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == "skellam"
    assert body["most_corners"] is not None
    # Skellam expone lambdas en drivers
    drv = body["most_corners"]["drivers"]
    assert "lambda_h" in drv
    assert "lambda_a" in drv


def test_corner_engine_predict_empty_context_no_crash():
    with _set_flags(most=True, asian=True):
        r = client.post(
            "/api/football/corner-engine/predict",
            json={"context": {}},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True  # fail-soft
    # Most Corners debería ser NO_BET con LOW data quality
    if body["most_corners"]:
        assert body["most_corners"]["recommended_side"] == "NO_BET"


def test_corner_engine_predict_asian_without_book_odds_marks_unavailable():
    with _set_flags(most=False, asian=True):
        r = client.post(
            "/api/football/corner-engine/predict",
            json={"context": _ctx_dominant_home()},
        )
    body = r.json()
    assert body["asian_corners"] is not None
    # Cada market debe tener REAL_ODDS_NOT_AVAILABLE
    for m in body["asian_corners"]:
        rc = m.get("reason_codes", [])
        assert "ASIAN_CORNERS_REAL_ODDS_NOT_AVAILABLE" in rc
        # Sin book_odds, recommendation no puede ser BET
        assert m["recommendation"] != "BET"
        assert m["book_odds"] is None


def test_corner_engine_predict_asian_with_book_odds_calculates_ev():
    book_odds = {
        "HOME_-0.5": 1.50, "HOME_-1.5": 1.85, "HOME_-2.5": 2.50,
        "AWAY_-0.5": 2.80, "AWAY_-1.5": 4.50, "AWAY_-2.5": 7.00,
    }
    with _set_flags(most=False, asian=True):
        r = client.post(
            "/api/football/corner-engine/predict",
            json={
                "context": {**_ctx_dominant_home(),
                             "asian_book_odds": book_odds},
            },
        )
    body = r.json()
    assert body["asian_corners"] is not None
    # Encontrar HOME -1.5 (debe tener ev calculado)
    h15 = next(m for m in body["asian_corners"] if m["market"] == "HOME_CORNERS_-1.5")
    assert h15["book_odds"] == 1.85
    assert h15["ev"] is not None


def test_corner_engine_health_does_not_consume_credits():
    """Smoke test: el endpoint health solo debería ser CPU/memoria."""
    for _ in range(5):
        r = client.get("/api/football/corner-engine/health")
        assert r.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
