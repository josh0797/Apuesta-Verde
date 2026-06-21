"""Sprint-D9 · Manual odds override genérico (cross-sport).

Permite al usuario ingresar manualmente su cuota cuando el book no tiene
precio disponible, persistiendo la cuota en BD para auditoría y
recálculo del análisis Moneyball.

Diseño:
  * Endpoint: ``POST /api/picks/manual-odds``
  * Colección: ``manual_odds_overrides``
      {
        "_id":              ObjectId,
        "match_id":         str,
        "sport":            "football" | "baseball" | "basketball",
        "market_key":       str (ej. "moneyline_home", "over_2.5", "runline_-1.5"),
        "decimal_odds":     float (>1.01),
        "estimated_prob":   float (lo que la app calculó pre-input),
        "implied_prob":     float (= 1/odds, derivado),
        "edge":             float (= estimated_prob - implied_prob),
        "lang":             "es" | "en",
        "user_id":          Optional[str],
        "created_at":       datetime utc,
        "ip_hash":          Optional[str] (telemetría agregada, no PII),
      }

El servicio NO valida que el pick exista — el usuario puede ingresar
cuotas para cualquier match_id. La validación de negocio (¿el match
existe? ¿estaba abierto?) la hace el frontend antes de mostrar el input.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ManualOddsRequest(BaseModel):
    """Cuota manual ingresada por el usuario."""
    match_id: str = Field(..., min_length=1, max_length=128)
    sport: str = Field(default="football")
    market_key: str = Field(..., min_length=1, max_length=80)
    decimal_odds: float = Field(..., gt=1.01, le=1000.0)
    estimated_prob: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    lang: str = Field(default="es")

    @field_validator("sport")
    @classmethod
    def _validate_sport(cls, v: str) -> str:
        v = (v or "football").strip().lower()
        if v not in ("football", "baseball", "basketball", "hockey", "soccer"):
            raise ValueError(f"sport must be football/baseball/basketball, got {v!r}")
        return "football" if v == "soccer" else v

    @field_validator("market_key")
    @classmethod
    def _validate_market_key(cls, v: str) -> str:
        v = (v or "").strip().lower().replace(" ", "_")
        if not v:
            raise ValueError("market_key cannot be empty")
        # Permitimos cualquier key — los frontends usan diversos slugs
        # (ej. ``moneyline_home``, ``over_2.5``, ``runline_-1.5``).
        return v


def compute_edge_from_odds(decimal_odds: float, estimated_prob: Optional[float]) -> dict:
    """Compute the implied probability + edge from a decimal odds value.

    Returns a dict with ``implied_probability``, ``edge`` (if estimate
    available), and ``net_profit_if_win`` (Kelly-style net for stake=1).
    """
    if decimal_odds is None or decimal_odds <= 1.01:
        return {
            "implied_probability": None,
            "edge":                None,
            "net_profit_if_win":   None,
        }
    implied = 1.0 / float(decimal_odds)
    edge = None
    if estimated_prob is not None:
        try:
            edge = float(estimated_prob) - implied
        except (TypeError, ValueError):
            edge = None
    return {
        "implied_probability": round(implied, 6),
        "edge":                None if edge is None else round(edge, 6),
        "net_profit_if_win":   round(float(decimal_odds) - 1.0, 4),
    }


async def persist_manual_odds(
    db,
    req: ManualOddsRequest,
    *,
    user_id: Optional[str] = None,
) -> dict:
    """Persist a manual-odds override and return the canonical record.

    Idempotency: si el usuario re-submite una cuota para el mismo
    (match_id, market_key, user_id), se inserta un nuevo documento
    (timestamp distinto). El "valor actual" siempre es el más reciente
    (cliente lo resuelve via ``GET`` con sort desc por ``created_at``).
    """
    derived = compute_edge_from_odds(req.decimal_odds, req.estimated_prob)
    doc = {
        "match_id":         req.match_id,
        "sport":            req.sport,
        "market_key":       req.market_key,
        "decimal_odds":     float(req.decimal_odds),
        "estimated_prob":   req.estimated_prob,
        "implied_prob":     derived["implied_probability"],
        "edge":             derived["edge"],
        "net_profit_if_win": derived["net_profit_if_win"],
        "lang":             req.lang,
        "user_id":          user_id,
        "created_at":       datetime.now(timezone.utc),
    }
    res = await db.manual_odds_overrides.insert_one(doc)
    doc["_id"] = str(res.inserted_id)
    # Asegurar que el datetime se serializa
    doc["created_at"] = doc["created_at"].isoformat()
    return doc


async def get_latest_manual_odds(
    db,
    match_id: str,
    market_key: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> Optional[dict]:
    """Recupera la cuota manual más reciente para (match_id, [market_key])."""
    q: dict = {"match_id": match_id}
    if market_key:
        q["market_key"] = (market_key or "").strip().lower().replace(" ", "_")
    if user_id:
        q["user_id"] = user_id
    doc = await db.manual_odds_overrides.find_one(q, sort=[("created_at", -1)])
    if doc is None:
        return None
    doc["_id"] = str(doc["_id"])
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc


__all__ = [
    "ManualOddsRequest",
    "compute_edge_from_odds",
    "persist_manual_odds",
    "get_latest_manual_odds",
]
