"""Phase F73 — Market identity guard helpers.

Centralised guards that PREVENT the engine from classifying a pick as
``MARKET_TRAP``, ``PROTECTED_BELOW_FLOOR``, ``LOW_ODDS_NO_CUSHION`` or
``EDGE_INSUFFICIENT`` when we don't actually know which market the
quoted odds belong to.

Rationale
---------
A 1.25 quote can be:
  * Great value on Under 3.5 (implied ~80% vs ~88% real)
  * Terrible value on a moneyline (implied ~80% vs ~64% real)
  * Trap on a Double Chance 1X if the model only estimates 75%

Without knowing the family/selection/line we MUST NOT compute edge or
flag a market trap. We instead route the entry to a new bucket
``requires_market_identity`` so the user can manually map it.
"""
from __future__ import annotations

from typing import Any, Optional


# These classifications are forbidden when market_identity is missing.
FORBIDDEN_WHEN_IDENTITY_MISSING = frozenset({
    "MARKET_TRAP",
    "PROTECTED_BELOW_FLOOR",
    "LOW_ODDS_NO_CUSHION",
    "EDGE_INSUFFICIENT",
    "EDGE_BELOW_MIN",
    "EDGE_BELOW_NEG_FLOOR",
    "NO_VALUE",
    "FRAGILE_EDGE",
})


def has_valid_market_identity(market: Any) -> bool:
    """Return True when we know enough to compute a real edge.

    Accepts either a dict (``market_identity`` from
    ``normalize_market_identity``) or a plain identity key string.
    """
    if not market:
        return False
    if isinstance(market, str):
        return bool(market) and not market.startswith("UNKNOWN:")
    if isinstance(market, dict):
        family = market.get("family") or market.get("market_family")
        if not family or str(family).upper() == "UNKNOWN":
            return False
        key = market.get("identity_key") or market.get("market_identity_key")
        if isinstance(key, str) and key.startswith("UNKNOWN:"):
            return False
        # Need a side/selection too (the line is optional for families
        # without a line, like 1X2).
        side = (market.get("side") or market.get("selection")
                or market.get("raw", {}).get("side"))
        if not side:
            return False
        return True
    return False


def gate_classification(classification: str,
                          market_identity: Any) -> tuple[str, list[str]]:
    """Return (gated_classification, extra_reason_codes).

    If the classification is one of the forbidden ones AND market
    identity is missing, replace with ``MARKET_IDENTITY_MISSING`` and
    append the F73 reason codes. Otherwise return the classification
    untouched.
    """
    cls = (classification or "").upper()
    if cls not in FORBIDDEN_WHEN_IDENTITY_MISSING:
        return classification, []
    if has_valid_market_identity(market_identity):
        return classification, []
    return ("MARKET_IDENTITY_MISSING",
            ["MARKET_IDENTITY_MISSING",
             "EDGE_CALCULATION_BLOCKED_UNKNOWN_MARKET"])


__all__ = [
    "FORBIDDEN_WHEN_IDENTITY_MISSING",
    "has_valid_market_identity",
    "gate_classification",
]
