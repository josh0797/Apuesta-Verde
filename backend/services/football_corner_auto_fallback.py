"""Sprint-D9-CornerAutoFallback · Lógica pura para promover picks
de córners cuando los mercados directos (H2H / DNB) no tienen edge
suficiente.

Filosofía
---------
El usuario quiere que el motor de córners se integre automáticamente
en el flujo de recomendación: si los mercados directos no entregan
edge ≥ X%, y el corner engine encuentra un mercado Asian Corners con
edge ≥ X% (vs book odds REALES), promovemos ese pick como
recomendación primaria.

Restricciones (zero-touch):
  * Solo activo cuando ``ENABLE_CORNER_AUTO_FALLBACK=true``.
  * Solo aplica para ``sport == "football"``.
  * Solo promueve cuando hay ``asian_book_odds`` REALES en el contexto;
    sin book odds reales, hablar de "edge" sería deshonesto.
  * Edge mínimo configurable vía ``CORNER_AUTO_FALLBACK_MIN_EDGE_PCT``
    (default 8.0%; equivale a ``ev ≥ 0.08`` en el Asian market).

Public API
----------
``maybe_promote_corner_pick(pick, *, corner_engine_context, ...)``
    Returns ``None`` si no aplica, o un dict pick reemplazo con shape::

        {
          "match_id":   ...,
          "match_label": ...,
          "recommendation": {
              "market":    "Asian Corners HOME -1.5",
              "selection": "HOME -1.5",
              "odds_range": "1.95-1.95",
              "confidence_score": 72.0,
          },
          "_corner_auto_fallback": {
              "original_market":  "1X2",
              "original_selection": "HOME",
              "promoted_market":  "HOME_CORNERS_-1.5",
              "ev":               0.115,
              "edge_pct":         11.5,
              "min_edge_pct_used": 8.0,
              "reason_codes":     [...],
              "drivers":          {...},
          },
        }
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

log = logging.getLogger("corner_auto_fallback")


def _flag_enabled(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or ("true" if default else "false")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_min_edge_pct() -> float:
    """Lee el umbral mínimo de edge desde env (default 8.0%)."""
    try:
        return float(os.environ.get("CORNER_AUTO_FALLBACK_MIN_EDGE_PCT", "8.0"))
    except (TypeError, ValueError):
        return 8.0


def is_eligible_for_corner_promotion(
    pick: dict[str, Any], sport: str = "football",
) -> bool:
    """¿Este pick debería intentar promoverse a córners?

    Reglas (zero-touch):
      * Solo football.
      * Solo si la clasificación moneyball es NO-VALUE
        (NO_BET_VALUE / WATCHLIST / MARKET_TRAP / PUBLIC_OVERREACTION /
        MARKET_IDENTITY_MISSING / WAIT_FOR_BETTER_LINE), porque entonces
        NO hay edge real en el mercado directo.
      * El pick original NO debe ser ya un mercado de córners (anti
        bucle).
    """
    if sport != "football":
        return False

    rec = (pick.get("recommendation") or {}) if isinstance(pick, dict) else {}
    market = (rec.get("market") or "").lower()
    if "corner" in market or "córner" in market:
        return False

    mb = pick.get("_moneyball") or {}
    cls = mb.get("classification") or ""
    # Lista cerrada de clases que indican "no hay edge en mercado directo"
    NO_VALUE_CLASSES = {
        "NO_BET_VALUE", "WATCHLIST", "MARKET_TRAP",
        "PUBLIC_OVERREACTION", "WAIT_FOR_BETTER_LINE",
        "MARKET_IDENTITY_MISSING",
    }
    return cls in NO_VALUE_CLASSES


def _build_skellam_inputs_from_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Filtra el contexto a las llaves que `predict_skellam_corner_diff`
    espera. Cualquier extra se ignora (fail-soft)."""
    keys = (
        "home_implied_prob", "away_implied_prob", "draw_implied_prob",
        "abs_implied_prob_diff",
        "dominant_favorite_side", "dominant_favorite_strength",
        "home_corners_for_L15", "away_corners_for_L15",
        "home_corners_against_L15", "away_corners_against_L15",
        "home_xg_for_L15", "away_xg_for_L15",
        "home_deep_allowed_L15", "away_deep_allowed_L15",
        "home_shots_total_L15", "away_shots_total_L15",
        "home_shots_against_L15", "away_shots_against_L15",
        "home_venue_corner_split", "away_venue_corner_split",
        "series_familiarity_score",
    )
    out: dict[str, Any] = {}
    for k in keys:
        if k in ctx:
            out[k] = ctx[k]
    return out


def find_best_corner_edge(
    corner_engine_context: dict[str, Any],
    *,
    min_edge_pct: float = 8.0,
    min_confidence: float = 50.0,
) -> Optional[dict[str, Any]]:
    """Llama al corner engine (Skellam + Asian markets) y devuelve el
    mejor mercado con ``ev ≥ min_edge_pct/100``.

    Returns ``None`` si:
      * El contexto no trae ``asian_book_odds`` (no se puede medir edge).
      * Ningún mercado supera el threshold.
      * El motor falla por dentro (fail-soft).
    """
    if not isinstance(corner_engine_context, dict):
        return None

    asian_book_odds = corner_engine_context.get("asian_book_odds")
    if not asian_book_odds or not isinstance(asian_book_odds, dict):
        # Sin book odds reales no hay edge medible: NO promovemos.
        return None

    try:
        from services.football.corners.skellam_corner_model import (
            predict_skellam_corner_diff,
            skellam_to_asian_corners,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[corner_auto_fallback] import skellam falló: %s", exc)
        return None

    try:
        skellam_inputs = _build_skellam_inputs_from_context(corner_engine_context)
        sk = predict_skellam_corner_diff(skellam_inputs)
        markets = skellam_to_asian_corners(
            sk,
            book_odds=asian_book_odds,
            real_odds_available=True,
            confidence=float(corner_engine_context.get("confidence", 70.0)),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[corner_auto_fallback] predict_skellam falló: %s", exc)
        return None

    if not markets:
        return None

    threshold_ev = float(min_edge_pct) / 100.0
    best: Optional[dict[str, Any]] = None
    for m in markets:
        ev = m.get("ev")
        if ev is None:
            continue
        conf = float(m.get("confidence") or 0.0)
        if conf < min_confidence:
            continue
        if ev < threshold_ev:
            continue
        if best is None or float(ev) > float(best.get("ev") or 0.0):
            best = m

    if best is None:
        return None

    # Adjuntar drivers + threshold para auditoría.
    best = dict(best)  # copia defensiva
    best["edge_pct"] = round(float(best["ev"]) * 100.0, 2)
    best["min_edge_pct_used"] = float(min_edge_pct)
    best["min_confidence_used"] = float(min_confidence)
    best["drivers"] = {
        "lambda_h":    sk.get("lambda_h"),
        "lambda_a":    sk.get("lambda_a"),
        "drivers_home": sk.get("drivers_home"),
        "drivers_away": sk.get("drivers_away"),
        "expected_corner_diff": sk.get("expected_corner_diff"),
    }
    return best


def _format_corner_market_label(best_market: dict[str, Any]) -> tuple[str, str]:
    """Devuelve (market_label, selection_label) legibles para UI."""
    side = best_market.get("side") or "HOME"
    line = best_market.get("line")
    if line is None:
        return "Asian Corners", side
    return f"Asian Corners {side} -{line}", f"{side} -{line}"


def maybe_promote_corner_pick(
    pick: dict[str, Any],
    *,
    corner_engine_context: Optional[dict[str, Any]] = None,
    sport: str = "football",
    min_edge_pct: Optional[float] = None,
    min_confidence: float = 50.0,
) -> Optional[dict[str, Any]]:
    """Intenta promover el pick a un mercado Asian Corners con edge alto.

    Parameters
    ----------
    pick : dict
        Pick original (post-moneyball, con ``_moneyball.classification``).
    corner_engine_context : dict | None
        Contexto Skellam (L15 corners + xG + deep + implied probs +
        ``asian_book_odds``). Si es ``None``, se intenta tomarlo desde
        ``pick.get("corner_engine_context")``.
    sport : str
        Solo "football" es elegible.
    min_edge_pct : float | None
        Umbral mínimo de edge. Si es ``None``, se lee de la env var
        ``CORNER_AUTO_FALLBACK_MIN_EDGE_PCT`` (default 8.0).
    min_confidence : float
        Confianza mínima del modelo para considerar el promoción.

    Returns
    -------
    dict | None
        ``None`` si no hay promoción posible. Si hay, devuelve un pick
        REEMPLAZO con su propia ``recommendation`` y un bloque de
        auditoría ``_corner_auto_fallback``. El pick reemplazo debe
        ser re-evaluado por ``apply_moneyball_layer`` para obtener su
        ``_moneyball`` + ``_market_edge`` actualizados.
    """
    if not _flag_enabled("ENABLE_CORNER_AUTO_FALLBACK", default=False):
        return None

    if not isinstance(pick, dict):
        return None

    if not is_eligible_for_corner_promotion(pick, sport=sport):
        return None

    if corner_engine_context is None:
        corner_engine_context = pick.get("corner_engine_context")
    if not isinstance(corner_engine_context, dict):
        return None

    threshold = get_min_edge_pct() if min_edge_pct is None else float(min_edge_pct)

    best = find_best_corner_edge(
        corner_engine_context,
        min_edge_pct=threshold,
        min_confidence=min_confidence,
    )
    if best is None:
        return None

    market_label, selection_label = _format_corner_market_label(best)

    book_price = best.get("book_odds")
    if book_price is None or book_price <= 1.0:
        return None

    odds_range = f"{book_price:.2f}-{book_price:.2f}"

    rec_block = (pick.get("recommendation") or {})
    promoted_pick: dict[str, Any] = {
        # Heredamos identificadores del pick original
        "match_id":    pick.get("match_id"),
        "match_label": pick.get("match_label"),
        "fixture":     pick.get("fixture"),
        "teams":       pick.get("teams"),
        "home_team":   pick.get("home_team"),
        "away_team":   pick.get("away_team"),
        # Nuevo recommendation orientado a córners
        "recommendation": {
            "market":           market_label,
            "selection":        selection_label,
            "odds_range":       odds_range,
            "confidence_score": float(best.get("confidence") or 70.0),
            "market_identity":  best.get("market"),  # canonical id
        },
        "risks": [
            "Mercado promovido automáticamente desde el motor de córners; "
            "verificar disponibilidad de cuotas en la casa real.",
        ],
        "_corner_auto_fallback": {
            "applied":              True,
            "promoted_from_market": rec_block.get("market"),
            "promoted_from_selection": rec_block.get("selection"),
            "promoted_market":      best.get("market"),
            "ev":                   float(best.get("ev") or 0.0),
            "edge_pct":             float(best.get("edge_pct") or 0.0),
            "min_edge_pct_used":    threshold,
            "min_confidence_used":  min_confidence,
            "book_odds":            book_price,
            "fair_odds":            best.get("fair_odds"),
            "prob_win":             best.get("prob_win"),
            "prob_push":            best.get("prob_push"),
            "prob_lose":            best.get("prob_lose"),
            "reason_codes":         list(best.get("reason_codes") or []),
            "drivers":              best.get("drivers"),
        },
    }
    return promoted_pick


__all__ = [
    "is_eligible_for_corner_promotion",
    "find_best_corner_edge",
    "maybe_promote_corner_pick",
    "get_min_edge_pct",
]
