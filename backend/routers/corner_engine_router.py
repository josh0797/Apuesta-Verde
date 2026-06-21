"""Sprint Corner Fase B · Endpoint del Corner Engine.

Endpoint **aislado** que expone el motor de córners (Most Corners + Asian
Corners) detrás de **feature flags** vía variables de entorno:

  * ``ENABLE_CORNER_MOST_MODEL``    → si False, devuelve 200 con
                                      ``enabled=false`` y razón "DISABLED".
  * ``ENABLE_ASIAN_CORNERS_MODEL``  → idem para Asian Corners.

Diseño aislado: este módulo **no toca** el endpoint de picks principal,
ni modifica los modelos existentes. Es un router FastAPI standalone que se
incluye en ``server.py`` con ``app.include_router(router)``.

Si algo falla dentro del motor, se devuelve 200 con ``ok=false`` y un
``message_user`` legible (fail-soft del brief). El sistema sigue
operando normalmente.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.football.corners import (
    build_asian_corner_markets,
    build_corner_diff_distribution,
    compute_expected_corner_diff,
    predict_most_corners,
    predict_skellam_corner_diff,
    skellam_most_corners,
    skellam_to_asian_corners,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/football/corner-engine", tags=["corner-engine"])


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on", "enabled")


# ============================================================
# Request/Response models
# ============================================================

class CornerEngineContext(BaseModel):
    """Context prematch — todas las features son opcionales (fail-soft)."""
    home_team:                 Optional[str]   = None
    away_team:                 Optional[str]   = None
    league:                    Optional[str]   = None
    season:                    Optional[str]   = None
    match_date:                Optional[str]   = None
    # Odds
    home_implied_prob:         Optional[float] = Field(None, ge=0, le=1)
    away_implied_prob:         Optional[float] = Field(None, ge=0, le=1)
    draw_implied_prob:         Optional[float] = Field(None, ge=0, le=1)
    abs_implied_prob_diff:     Optional[float] = Field(None, ge=0, le=1)
    dominant_favorite_side:    Optional[str]   = None
    dominant_favorite_strength: Optional[float] = None
    # Corners L15
    home_corners_for_L15:      Optional[float] = None
    away_corners_for_L15:      Optional[float] = None
    home_corners_against_L15:  Optional[float] = None
    away_corners_against_L15:  Optional[float] = None
    # Rich (Understat)
    home_xg_for_L15:           Optional[float] = None
    away_xg_for_L15:           Optional[float] = None
    home_deep_allowed_L15:     Optional[float] = None
    away_deep_allowed_L15:     Optional[float] = None
    # Shots
    home_shots_total_L15:      Optional[float] = None
    away_shots_total_L15:      Optional[float] = None
    home_shots_against_L15:    Optional[float] = None
    away_shots_against_L15:    Optional[float] = None
    # Venue split + series
    home_venue_corner_split:   Optional[float] = None
    away_venue_corner_split:   Optional[float] = None
    series_familiarity_score:  Optional[float] = None
    # Optional book odds for Asian markets (HOME_-0.5 → decimal odds, etc.)
    asian_book_odds:           Optional[dict[str, float]] = None
    # Model selector
    use_skellam:               bool = False


class CornerEnginePredictRequest(BaseModel):
    context: CornerEngineContext


class CornerEnginePredictResponse(BaseModel):
    ok: bool
    enabled: bool
    model: str
    reason: Optional[str] = None
    most_corners: Optional[dict[str, Any]] = None
    asian_corners: Optional[list[dict[str, Any]]] = None
    expected_corner_diff: Optional[float] = None
    debug: Optional[dict[str, Any]] = None


# ============================================================
# Endpoint
# ============================================================

@router.post("/predict", response_model=CornerEnginePredictResponse)
async def predict_corner_engine(req: CornerEnginePredictRequest):
    """Predice mercados de córners (Most Corners + Asian Corners).

    Feature flags:
      - ``ENABLE_CORNER_MOST_MODEL=true``   → habilita Most Corners
      - ``ENABLE_ASIAN_CORNERS_MODEL=true`` → habilita Asian Corners

    Cualquier excepción interna se traduce a ``ok=False`` con
    ``message_user`` legible para no romper el flujo del frontend.
    """
    enable_most  = _flag("ENABLE_CORNER_MOST_MODEL", default=False)
    enable_asian = _flag("ENABLE_ASIAN_CORNERS_MODEL", default=False)

    if not enable_most and not enable_asian:
        return CornerEnginePredictResponse(
            ok=True, enabled=False, model="none",
            reason="FEATURE_FLAGS_DISABLED",
        )

    ctx = req.context.model_dump()
    use_skellam = bool(ctx.pop("use_skellam", False))
    asian_book_odds = ctx.pop("asian_book_odds", None) or {}

    try:
        # ---- Most Corners ----
        most = None
        edcd = None
        model_name = "skellam" if use_skellam else "linear_sigmoid"

        if use_skellam:
            sk = predict_skellam_corner_diff(ctx)
            sk_most = skellam_most_corners(sk)
            recommended_side = _pick_skellam_side(sk_most)
            most = {
                "home_most_corners_prob": sk_most["home_most_corners_prob"],
                "away_most_corners_prob": sk_most["away_most_corners_prob"],
                "tie_corners_prob":       sk_most["tie_corners_prob"],
                "recommended_side":       recommended_side,
                "edge_score":             _edge_score_from_probs(sk_most),
                "confidence":             _skellam_confidence(ctx),
                "expected_corner_diff":   sk_most["expected_corner_diff"],
                "reason_codes":           list(sk["reason_codes"]),
                "drivers":                {
                    "home": sk["drivers_home"],
                    "away": sk["drivers_away"],
                    "lambda_h": sk["lambda_h"],
                    "lambda_a": sk["lambda_a"],
                },
                "debug": {
                    "model":                "skellam",
                    "expected_total_corners": sk["expected_total_corners"],
                },
            }
            edcd = sk_most["expected_corner_diff"]
        else:
            most = predict_most_corners(ctx)
            edcd = most["expected_corner_diff"]

        # ---- Asian Corners ----
        asian = None
        if enable_asian:
            if use_skellam:
                # Skellam ya tiene la distribución
                asian = skellam_to_asian_corners(
                    sk,
                    book_odds=asian_book_odds,
                    real_odds_available=bool(asian_book_odds),
                    confidence=float(most.get("confidence", 60.0)),
                )
            else:
                dist = build_corner_diff_distribution(
                    {"expected_corner_diff": edcd},
                    bucket_stats=None,  # usará la aproximación normal-discreta
                )
                asian = build_asian_corner_markets(
                    dist,
                    book_odds=asian_book_odds,
                    real_odds_available=bool(asian_book_odds),
                )

        # Si solo está habilitado uno, anulamos el otro
        if not enable_most:
            most = None
        if not enable_asian:
            asian = None

        return CornerEnginePredictResponse(
            ok=True, enabled=True, model=model_name,
            most_corners=most,
            asian_corners=asian,
            expected_corner_diff=edcd,
            debug={
                "feature_flags": {
                    "ENABLE_CORNER_MOST_MODEL":   enable_most,
                    "ENABLE_ASIAN_CORNERS_MODEL": enable_asian,
                },
            },
        )

    except Exception as exc:  # noqa: BLE001 — fail-soft
        log.exception("[corner-engine] prediction failed: %s", exc)
        return CornerEnginePredictResponse(
            ok=False, enabled=True, model="error",
            reason=f"PREDICTION_FAILED: {type(exc).__name__}: {exc}",
        )


@router.get("/health")
async def corner_engine_health():
    """Health check del motor (no consume créditos, no toca DB)."""
    return {
        "ok": True,
        "feature_flags": {
            "ENABLE_CORNER_MOST_MODEL":   _flag("ENABLE_CORNER_MOST_MODEL"),
            "ENABLE_ASIAN_CORNERS_MODEL": _flag("ENABLE_ASIAN_CORNERS_MODEL"),
        },
        "modules": {
            "corner_diff_model":         "ok",
            "corner_most_model":         "ok",
            "corner_diff_distribution":  "ok",
            "skellam_corner_model":      "ok",
        },
    }


# ============================================================
# Helpers
# ============================================================

def _pick_skellam_side(most: dict[str, Any]) -> str:
    p_home = float(most["home_most_corners_prob"])
    p_away = float(most["away_most_corners_prob"])
    if max(p_home, p_away) < 0.58:
        return "NO_BET"
    return "HOME" if p_home > p_away else "AWAY"


def _edge_score_from_probs(most: dict[str, Any]) -> float:
    p_home = float(most["home_most_corners_prob"])
    p_away = float(most["away_most_corners_prob"])
    p_tie  = float(most["tie_corners_prob"])
    if p_tie >= 0.999:
        return 0.0
    max_excl_tie = max(p_home, p_away) / (1.0 - p_tie)
    return round(max(0.0, min(100.0, 200.0 * (max_excl_tie - 0.5))), 2)


def _skellam_confidence(ctx: dict[str, Any]) -> float:
    """Confidence para Skellam: cuenta drivers ricos disponibles."""
    required = ("home_implied_prob", "away_implied_prob",
                 "home_corners_for_L15", "away_corners_for_L15",
                 "home_xg_for_L15", "away_xg_for_L15",
                 "home_deep_allowed_L15", "away_deep_allowed_L15")
    present = sum(1 for k in required if ctx.get(k) is not None)
    return round(100.0 * present / len(required), 2)
