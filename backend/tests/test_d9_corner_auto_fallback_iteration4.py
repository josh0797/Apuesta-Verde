"""Sprint-D9-CornerAutoFallback · Tests del módulo puro y del cableado
en apply_moneyball_layer.

Validaciones:
  1. Flag desactivada → nunca promueve.
  2. Sport != "football" → nunca promueve.
  3. Pick en bucket VALUE → no promueve (no es elegible).
  4. Sin asian_book_odds → no promueve (no hay edge medible).
  5. Edge < 8% → no promueve.
  6. Edge ≥ 8% → promueve con shape correcto.
  7. apply_moneyball_layer: pick NO_BET_VALUE football → reemplazado.
  8. apply_moneyball_layer: pick NO_BET_VALUE MLB → NO reemplazado.
"""
from __future__ import annotations

import pytest

from services import football_corner_auto_fallback as caf
from services import moneyball_layer as ml


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
def _dominant_favorite_ctx_with_book_odds(book_odds: dict) -> dict:
    """Contexto Skellam con un favorito dominante (Home) + book odds."""
    return {
        "home_implied_prob":         0.75,
        "away_implied_prob":         0.10,
        "draw_implied_prob":         0.15,
        "abs_implied_prob_diff":     0.65,
        "dominant_favorite_side":    "HOME",
        "dominant_favorite_strength": 0.65,
        "home_corners_for_L15":      6.2,
        "away_corners_for_L15":      3.8,
        "home_corners_against_L15":  3.0,
        "away_corners_against_L15":  5.5,
        "home_xg_for_L15":           2.1,
        "away_xg_for_L15":           0.9,
        "home_deep_allowed_L15":     5.5,
        "away_deep_allowed_L15":     12.0,
        "asian_book_odds":           book_odds,
        "confidence":                75.0,
    }


def _no_value_pick() -> dict:
    return {
        "match_id":    "MATCH-123",
        "match_label": "Real Madrid vs Cádiz",
        "home_team":   "Real Madrid",
        "away_team":   "Cádiz",
        "recommendation": {
            "market":           "1X2",
            "selection":        "HOME",
            "odds_range":       "1.20-1.22",
            "confidence_score": 65,
        },
        "_moneyball": {"classification": "NO_BET_VALUE"},
        "_market_edge": {"edge": -0.05},
    }


# ─────────────────────────────────────────────────────────────────────
# Tests del módulo puro
# ─────────────────────────────────────────────────────────────────────
def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_CORNER_AUTO_FALLBACK", raising=False)
    pick = _no_value_pick()
    pick["corner_engine_context"] = _dominant_favorite_ctx_with_book_odds(
        {"HOME_-1.5": 2.10}
    )
    assert caf.maybe_promote_corner_pick(pick) is None


def test_min_edge_pct_default_is_8():
    """El umbral por defecto debe ser 8.0 (decisión usuario)."""
    assert caf.get_min_edge_pct() == 8.0


def test_min_edge_pct_respects_env(monkeypatch):
    monkeypatch.setenv("CORNER_AUTO_FALLBACK_MIN_EDGE_PCT", "12.5")
    assert caf.get_min_edge_pct() == 12.5


def test_not_eligible_for_non_football(monkeypatch):
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")
    pick = _no_value_pick()
    pick["corner_engine_context"] = _dominant_favorite_ctx_with_book_odds(
        {"HOME_-1.5": 2.10}
    )
    assert caf.maybe_promote_corner_pick(pick, sport="baseball") is None


def test_not_eligible_when_classification_is_value(monkeypatch):
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")
    pick = _no_value_pick()
    pick["_moneyball"]["classification"] = "VALUE_BET"
    pick["corner_engine_context"] = _dominant_favorite_ctx_with_book_odds(
        {"HOME_-1.5": 2.10}
    )
    assert caf.maybe_promote_corner_pick(pick) is None


def test_not_eligible_when_market_is_already_corners(monkeypatch):
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")
    pick = _no_value_pick()
    pick["recommendation"]["market"] = "Asian Corners HOME -1.5"
    pick["corner_engine_context"] = _dominant_favorite_ctx_with_book_odds(
        {"HOME_-1.5": 2.10}
    )
    assert caf.maybe_promote_corner_pick(pick) is None


def test_not_promoted_without_asian_book_odds(monkeypatch):
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")
    pick = _no_value_pick()
    ctx = _dominant_favorite_ctx_with_book_odds({})  # sin book odds
    ctx.pop("asian_book_odds", None)
    pick["corner_engine_context"] = ctx
    assert caf.maybe_promote_corner_pick(pick) is None


def test_not_promoted_when_edge_below_threshold(monkeypatch):
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")
    pick = _no_value_pick()
    # Book odds muy bajas (1.05 → no hay edge sobre ningún Skellam realista)
    pick["corner_engine_context"] = _dominant_favorite_ctx_with_book_odds(
        {"HOME_-0.5": 1.05, "HOME_-1.5": 1.10}
    )
    out = caf.maybe_promote_corner_pick(pick, min_edge_pct=8.0)
    assert out is None


def test_promoted_when_edge_above_threshold(monkeypatch):
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")
    pick = _no_value_pick()
    # Book odds generosas: HOME -1.5 a 2.50 vs un favorito Skellam dominante.
    pick["corner_engine_context"] = _dominant_favorite_ctx_with_book_odds(
        {"HOME_-0.5": 1.80, "HOME_-1.5": 2.50, "HOME_-2.5": 3.50,
         "AWAY_-0.5": 5.00, "AWAY_-1.5": 12.0, "AWAY_-2.5": 25.0,
         "HOME_-1.0": 2.00, "HOME_-2.0": 3.00,
         "AWAY_-1.0": 7.0,  "AWAY_-2.0": 18.0,
         "HOME_-3.0": 5.0,  "HOME_-3.5": 6.0,
         "AWAY_-3.0": 40.0, "AWAY_-3.5": 60.0,
        }
    )
    out = caf.maybe_promote_corner_pick(pick, min_edge_pct=8.0)
    assert out is not None
    assert "Asian Corners" in out["recommendation"]["market"]
    audit = out["_corner_auto_fallback"]
    assert audit["applied"] is True
    assert audit["edge_pct"] >= 8.0
    assert audit["min_edge_pct_used"] == 8.0
    assert audit["promoted_from_market"] == "1X2"
    # Identificador match_id heredado
    assert out["match_id"] == "MATCH-123"


def test_promoted_pick_has_valid_odds_range(monkeypatch):
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")
    pick = _no_value_pick()
    pick["corner_engine_context"] = _dominant_favorite_ctx_with_book_odds(
        {"HOME_-0.5": 1.80, "HOME_-1.5": 2.50, "AWAY_-0.5": 5.0,
         "AWAY_-1.5": 12.0}
    )
    out = caf.maybe_promote_corner_pick(pick, min_edge_pct=8.0)
    assert out is not None
    rng = out["recommendation"]["odds_range"]
    # Forma "X.XX-X.XX"
    assert "-" in rng
    lo, hi = rng.split("-")
    assert float(lo) > 1.0
    assert float(hi) > 1.0


# ─────────────────────────────────────────────────────────────────────
# Tests del cableado en apply_moneyball_layer
# ─────────────────────────────────────────────────────────────────────
def test_apply_moneyball_replaces_pick_when_corner_edge_high(monkeypatch):
    """Football pick NO_BET_VALUE con corner_engine_context → debe ser
    reemplazado por el corner pick promovido en parsed['picks']."""
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")

    # Construimos un pick que el clasificador real va a marcar NO_BET_VALUE
    # porque no tiene odds_range parseable / edge calculable.
    pick = {
        "match_id":    "AUTO-FALLBACK-1",
        "match_label": "Real Madrid vs Cádiz",
        "home_team":   "Real Madrid",
        "away_team":   "Cádiz",
        "recommendation": {
            "market":           "1X2",
            "selection":        "HOME",
            # Odds muy bajas para asegurar edge negativo
            "odds_range":       "1.05-1.07",
            "confidence_score": 60,
        },
        "corner_engine_context":
            _dominant_favorite_ctx_with_book_odds(
                {"HOME_-0.5": 1.80, "HOME_-1.5": 2.50, "HOME_-2.5": 3.50,
                 "AWAY_-0.5": 5.0,  "AWAY_-1.5": 12.0, "AWAY_-2.5": 25.0,
                 "HOME_-1.0": 2.00, "HOME_-2.0": 3.00,
                 "AWAY_-1.0": 7.0,  "AWAY_-2.0": 18.0,
                 "HOME_-3.0": 5.0,  "HOME_-3.5": 6.0,
                 "AWAY_-3.0": 40.0, "AWAY_-3.5": 60.0,
                }
            ),
    }
    parsed = {"picks": [pick]}
    out = ml.apply_moneyball_layer(parsed, sport="football")

    # Verificamos:
    # (a) parsed["picks"] ahora contiene el pick promovido o el original con
    #     _corner_auto_fallback adjunto en algún bucket.
    promoted_found = False
    for p in (out.get("picks") or []):
        if (p.get("_corner_auto_fallback") or {}).get("applied"):
            promoted_found = True
            assert "Asian Corners" in p["recommendation"]["market"]
            assert p["_corner_auto_fallback"]["edge_pct"] >= 8.0
    # También podría estar en discarded_market si el book_odds del corner
    # pick tampoco renta tras moneyball recalc; lo aceptamos siempre que
    # el flag de auto-fallback haya quedado adjunto en algún lado.
    summary = out.get("summary") or {}
    for bucket in ("watchlist", "discarded_market", "protected_acceptable",
                   "requires_market_identity"):
        for entry in (summary.get(bucket) or []):
            if (entry.get("_corner_auto_fallback") or {}).get("applied"):
                promoted_found = True
    assert promoted_found, (
        "El cableado debe haber promovido el pick a córners "
        "y dejado huella en _corner_auto_fallback en algún bucket."
    )


def test_apply_moneyball_does_not_replace_for_mlb(monkeypatch):
    """Sport != football → auto-fallback NO debe activarse."""
    monkeypatch.setenv("ENABLE_CORNER_AUTO_FALLBACK", "true")
    pick = {
        "match_id":    "MLB-1",
        "match_label": "Yankees vs Red Sox",
        "recommendation": {
            "market":           "Run Line",
            "selection":        "NYY -1.5",
            "odds_range":       "1.05-1.10",
            "confidence_score": 60,
        },
        # MLB jamás debe abrir corner engine, aunque traiga el contexto
        "corner_engine_context":
            _dominant_favorite_ctx_with_book_odds(
                {"HOME_-1.5": 2.50, "AWAY_-1.5": 12.0}
            ),
    }
    parsed = {"picks": [pick]}
    out = ml.apply_moneyball_layer(parsed, sport="baseball")
    # Ningún pick / bucket debe tener _corner_auto_fallback.applied
    found_caf = False
    for p in (out.get("picks") or []):
        if (p.get("_corner_auto_fallback") or {}).get("applied"):
            found_caf = True
    summary = out.get("summary") or {}
    for bucket in ("watchlist", "discarded_market", "protected_acceptable",
                   "requires_market_identity"):
        for entry in (summary.get(bucket) or []):
            if (entry.get("_corner_auto_fallback") or {}).get("applied"):
                found_caf = True
    assert found_caf is False
