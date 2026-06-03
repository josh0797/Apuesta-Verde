"""
Odds Value Engine
=================

Centralised value-layer used by the engine to normalise odds and derive
the **edge / EV / line-movement / market-status** primitives that every
sport-specific layer (MLB / Football / Basketball) relies on.

Inspired by ``odds-api/odds-api`` shapes — but kept fully decoupled
from any vendor SDK so the engine still works when no external API key
is configured.

This module is *deliberately* read-only: it does not call HTTP. It
operates on whatever odds snapshots the upstream scrapers / providers
have already attached to a pick. The function signatures match the user
spec from 2026-06.

Glossary
--------
``decimal_odds``     Standard European-style price (e.g. 1.85, 2.10).
``american_odds``    -110, +130 conventions used in US books.
``fractional_odds``  "9/4", "11/8" — used by some UK feeds.
``implied_p``        1 / decimal_odds (no overround removal).
``model_p``          The engine's estimated probability for the side.
``edge``             ``model_p - implied_p`` (additive, in [0,1]).
``edge_pct``         Edge expressed as %.
``EV``               Expected value at unit stake = p × (o−1) − (1−p).

All functions are fail-soft — invalid inputs yield ``None`` /
``"market_status":"no_odds"`` instead of raising.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

log = logging.getLogger(__name__)


# ── Decimal normalisation ────────────────────────────────────────────────
_AMERICAN_RE   = re.compile(r"^\s*([+-]\d{3,5})\s*$")
_FRACTIONAL_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
_DECIMAL_RE    = re.compile(r"^\s*\d{1,4}(?:[.,]\d{1,4})?\s*$")


def normalize_decimal_odds(raw_odds: Any) -> Optional[float]:
    """Accept decimal / American / fractional input → return a clean
    decimal float ≥ 1.01. Anything else returns ``None``.

    Accepts both ``"1.85"`` and ``"1,85"`` (Spanish locale).
    """
    if raw_odds is None:
        return None
    if isinstance(raw_odds, (int, float)):
        v = float(raw_odds)
        return v if v >= 1.01 else None
    s = str(raw_odds).strip()
    if not s:
        return None
    # Fractional first ("3/2") — '/' would otherwise fail decimal parse.
    m = _FRACTIONAL_RE.match(s)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den <= 0:
            return None
        v = round(1.0 + num / den, 4)
        return v if v >= 1.01 else None
    # American next ("+150", "-110").
    m = _AMERICAN_RE.match(s)
    if m:
        a = int(m.group(1))
        if a == 0:
            return None
        if a > 0:
            return round(1.0 + a / 100.0, 4)
        return round(1.0 + 100.0 / abs(a), 4)
    # Decimal — accept comma separator.
    s2 = s.replace(",", ".")
    if _DECIMAL_RE.match(s):
        try:
            v = float(s2)
            return v if v >= 1.01 else None
        except ValueError:
            return None
    # Fallback: try float() with relaxed regex extraction.
    try:
        v = float(s2)
        return v if v >= 1.01 else None
    except ValueError:
        return None


def parse_midpoint_odds(odds_range: Optional[str]) -> Optional[float]:
    """Extract the midpoint of a range like ``"1.80-1.95"`` or
    ``"1,80 / 1,95"`` and return a normalised decimal float.
    """
    if not odds_range:
        return None
    nums = re.findall(r"\d+[.,]?\d*", str(odds_range))
    parsed: list[float] = []
    for n in nums:
        v = normalize_decimal_odds(n)
        if v is not None and 1.01 <= v <= 30.0:
            parsed.append(v)
    if not parsed:
        return None
    if len(parsed) >= 2:
        return round((parsed[0] + parsed[1]) / 2.0, 3)
    return parsed[0]


# ── Implied / edge / EV ──────────────────────────────────────────────────
def implied_probability(decimal_odds: Any) -> Optional[float]:
    """1 / decimal_odds. Accepts raw input and normalises first."""
    o = normalize_decimal_odds(decimal_odds)
    if o is None:
        return None
    return round(1.0 / o, 4)


def calculate_edge(
    model_probability: Optional[float],
    decimal_odds: Any,
) -> dict:
    """Return a structured edge report ``{model_p, implied_p, edge,
    edge_pct, verdict}``.

    ``verdict`` enum:
      * ``"VALUE"``      — edge ≥ +3%
      * ``"FAIR_VALUE"`` — |edge| < 3%
      * ``"NO_VALUE"``   — edge ≤ -3%
      * ``"UNKNOWN"``    — missing inputs
    """
    o = normalize_decimal_odds(decimal_odds)
    try:
        p = float(model_probability) if model_probability is not None else None
    except (TypeError, ValueError):
        p = None
    if o is None or p is None or not (0.0 <= p <= 1.0):
        return {
            "model_probability":  p,
            "implied_probability": (round(1.0 / o, 4) if o else None),
            "edge":      None,
            "edge_pct":  None,
            "verdict":   "UNKNOWN",
        }
    imp = 1.0 / o
    edge = round(p - imp, 4)
    edge_pct = round(edge * 100.0, 2)
    if edge >= 0.03:
        verdict = "VALUE"
    elif edge <= -0.03:
        verdict = "NO_VALUE"
    else:
        verdict = "FAIR_VALUE"
    return {
        "model_probability":   round(p, 4),
        "implied_probability": round(imp, 4),
        "edge":                edge,
        "edge_pct":            edge_pct,
        "verdict":             verdict,
    }


def calculate_expected_value(
    model_probability: Optional[float],
    decimal_odds: Any,
    stake: float = 1.0,
) -> dict:
    """``EV = p × (o − 1) × stake − (1 − p) × stake``. Returns the EV
    and a normalised ROI projection (% of stake)."""
    o = normalize_decimal_odds(decimal_odds)
    try:
        p = float(model_probability) if model_probability is not None else None
        s = float(stake) if stake is not None else 1.0
    except (TypeError, ValueError):
        return {
            "stake": stake, "expected_value": None,
            "net_profit_if_win": None,
            "roi_projection_pct": None,
            "is_positive_ev":   False,
        }
    if o is None or p is None or s <= 0:
        return {
            "stake": s, "expected_value": None,
            "net_profit_if_win": None,
            "roi_projection_pct": None,
            "is_positive_ev":   False,
        }
    net_if_win = s * (o - 1.0)
    ev = p * net_if_win - (1.0 - p) * s
    roi = (ev / s) * 100.0
    return {
        "stake":               round(s, 4),
        "net_profit_if_win":   round(net_if_win, 4),
        "expected_value":      round(ev, 4),
        "roi_projection_pct":  round(roi, 2),
        "is_positive_ev":      ev > 0,
    }


# ── Line movement ────────────────────────────────────────────────────────
# ``opening_line`` / ``current_line`` refer to the **handicap line** (e.g.
# total runs 9.5 → 9.0 means the line moved toward Under). ``odds`` refer
# to the price on a side (Over/Under). Either pair can be omitted —
# anything missing is reported as ``None`` and the verdict adapts.

# Direction codes (canonical for cross-module use):
DIR_TOWARD_OVER       = "toward_over"
DIR_TOWARD_UNDER      = "toward_under"
DIR_TOWARD_FAVORITE   = "toward_favorite"
DIR_TOWARD_UNDERDOG   = "toward_underdog"
DIR_STABLE            = "stable"


def detect_line_movement(
    opening_line:  Optional[float] = None,
    current_line:  Optional[float] = None,
    opening_odds:  Optional[Any]   = None,
    current_odds:  Optional[Any]   = None,
    *,
    market_side:   Optional[str]   = None,
) -> dict:
    """Compute the movement of either the handicap or the odds.

    ``market_side``: "over"/"under"/"favorite"/"underdog" (optional). Used
    to disambiguate the direction of an odds-only movement.

    Returns a dict with ``movement`` (Δ line), ``odds_movement`` (Δ odds),
    ``direction`` and ``steam_detected``.
    """
    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    ol_line, cl_line = _f(opening_line), _f(current_line)
    ol_odds = normalize_decimal_odds(opening_odds) if opening_odds is not None else None
    cl_odds = normalize_decimal_odds(current_odds) if current_odds is not None else None

    move_line = None
    if ol_line is not None and cl_line is not None:
        move_line = round(cl_line - ol_line, 3)

    move_odds = None
    if ol_odds is not None and cl_odds is not None:
        move_odds = round(cl_odds - ol_odds, 3)

    # Direction inference:
    direction = DIR_STABLE
    side_lc = (market_side or "").lower()
    if move_line is not None:
        if move_line >= 0.5:
            direction = DIR_TOWARD_OVER
        elif move_line <= -0.5:
            direction = DIR_TOWARD_UNDER
    if direction == DIR_STABLE and move_odds is not None:
        # Odds dropping for a side → market thinks that side is more
        # likely → "steam" in that direction.
        if "over"  in side_lc and move_odds <= -0.10:
            direction = DIR_TOWARD_OVER
        elif "under" in side_lc and move_odds <= -0.10:
            direction = DIR_TOWARD_UNDER
        elif side_lc == "favorite" and move_odds <= -0.10:
            direction = DIR_TOWARD_FAVORITE
        elif side_lc == "underdog" and move_odds <= -0.10:
            direction = DIR_TOWARD_UNDERDOG

    # Steam = sharp + sudden move.
    steam = False
    if move_line is not None and abs(move_line) >= 1.0:
        steam = True
    if move_odds is not None and abs(move_odds) >= 0.25:
        steam = True

    return {
        "opening_line":   ol_line,
        "current_line":   cl_line,
        "movement":       move_line,
        "opening_odds":   ol_odds,
        "current_odds":   cl_odds,
        "odds_movement":  move_odds,
        "direction":      direction,
        "steam_detected": steam,
    }


# ── Multi-bookmaker comparator ───────────────────────────────────────────
def compare_bookmaker_odds(markets: list[dict]) -> dict:
    """Compare a list of per-bookmaker quotes for the SAME market.

    Each item should be ``{"bookmaker": str, "odds": Any}``. Anything
    invalid is filtered. Returns the best price + an array of all the
    normalised entries (sorted descending) so the caller can render a
    comparison table.
    """
    cleaned: list[dict] = []
    for m in markets or []:
        if not isinstance(m, dict):
            continue
        o = normalize_decimal_odds(m.get("odds"))
        if o is None:
            continue
        cleaned.append({
            "bookmaker": str(m.get("bookmaker") or "unknown"),
            "odds":      o,
            "implied":   round(1.0 / o, 4),
        })
    if not cleaned:
        return {
            "best_odds":       None,
            "best_bookmaker":  None,
            "avg_odds":        None,
            "median_odds":     None,
            "spread_pct":      None,
            "entries":         [],
        }
    cleaned.sort(key=lambda e: e["odds"], reverse=True)
    best = cleaned[0]
    odds_arr = [e["odds"] for e in cleaned]
    avg = round(sum(odds_arr) / len(odds_arr), 4)
    median = round(sorted(odds_arr)[len(odds_arr) // 2], 4)
    spread_pct = None
    if len(odds_arr) >= 2:
        spread_pct = round((odds_arr[0] - odds_arr[-1]) / odds_arr[-1] * 100.0, 2)
    return {
        "best_odds":       best["odds"],
        "best_bookmaker":  best["bookmaker"],
        "avg_odds":        avg,
        "median_odds":     median,
        "spread_pct":      spread_pct,
        "entries":         cleaned,
    }


# ── Top-level evaluator (the user-spec payload) ──────────────────────────
def evaluate_market(
    *,
    decimal_odds:       Optional[Any]   = None,
    odds_range:         Optional[str]   = None,
    bookmaker_quotes:   Optional[list[dict]] = None,
    model_probability:  Optional[float] = None,
    opening_line:       Optional[float] = None,
    current_line:       Optional[float] = None,
    opening_odds:       Optional[Any]   = None,
    current_odds:       Optional[Any]   = None,
    market_side:        Optional[str]   = None,
    stake:              float           = 1.0,
) -> dict:
    """One-shot evaluator returning the canonical payload defined in the
    2026-06 spec. Use this from any sport layer that wants the consolidated
    value report attached to a pick.
    """
    # Resolve best odds — multi-book first, then explicit decimal, then range.
    best_book = None
    best_odds = None
    cmp_payload: dict = {}
    if bookmaker_quotes:
        cmp_payload = compare_bookmaker_odds(bookmaker_quotes)
        best_odds = cmp_payload.get("best_odds")
        best_book = cmp_payload.get("best_bookmaker")
    if best_odds is None and decimal_odds is not None:
        best_odds = normalize_decimal_odds(decimal_odds)
    if best_odds is None and odds_range:
        best_odds = parse_midpoint_odds(odds_range)

    edge_payload = calculate_edge(model_probability, best_odds)
    ev_payload   = calculate_expected_value(model_probability, best_odds, stake=stake)
    move_payload = detect_line_movement(
        opening_line=opening_line, current_line=current_line,
        opening_odds=opening_odds, current_odds=current_odds,
        market_side=market_side,
    )

    if best_odds is None and model_probability is not None:
        market_status = "manual_odds_required"
    elif best_odds is None:
        market_status = "no_odds"
    else:
        market_status = "priced"

    return {
        "best_odds":            best_odds,
        "best_bookmaker":       best_book,
        "implied_probability":  edge_payload["implied_probability"],
        "model_probability":    edge_payload["model_probability"],
        "edge":                 edge_payload["edge"],
        "edge_pct":             edge_payload["edge_pct"],
        "value_verdict":        edge_payload["verdict"],
        "expected_value":       ev_payload["expected_value"],
        "roi_projection_pct":   ev_payload["roi_projection_pct"],
        "is_positive_ev":       ev_payload["is_positive_ev"],
        "line_movement":        move_payload,
        "bookmaker_comparison": cmp_payload if cmp_payload else None,
        "market_status":        market_status,
    }


# ── Helpers re-exported for backward-compat with moneyball_layer ─────────
__all__ = [
    "normalize_decimal_odds",
    "parse_midpoint_odds",
    "implied_probability",
    "calculate_edge",
    "calculate_expected_value",
    "detect_line_movement",
    "compare_bookmaker_odds",
    "evaluate_market",
    "DIR_TOWARD_OVER", "DIR_TOWARD_UNDER",
    "DIR_TOWARD_FAVORITE", "DIR_TOWARD_UNDERDOG", "DIR_STABLE",
]
