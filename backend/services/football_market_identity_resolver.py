"""Phase F74-post — Football Market Identity Resolver.

Cuando el moneyball/guard recibe una entry descartada SIN ``market_identity``
válida, este módulo intenta reconstruirla a partir de:

  1. ``discarded_entry.evaluated_market`` / ``market_trace.evaluated_market``
  2. ``recommendation.market`` + ``recommendation.selection``
  3. ``protected_alternative``
  4. **Búsqueda por cuota** en ``odds_snapshots[-1].markets`` (con tolerancia
     ±0.01). Si la cuota tiene **una sola coincidencia** → market_identity
     resuelto. Si tiene **varias** → AMBIGUOUS (bucket separado, con
     ``candidate_markets`` para input manual del usuario).

Buckets posibles
================
  * ``state = "RESOLVED"``                          → market_identity normal
  * ``state = "REQUIRES_MANUAL_MARKET_SELECTION"``  → usuario debe elegir
                                                       entre candidates
  * ``state = "UNKNOWN"``                           → no se encontró
                                                       ninguna pista
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import market_identity as _mi

log = logging.getLogger(__name__)

STATE_RESOLVED                 = "RESOLVED"
STATE_REQUIRES_MANUAL          = "REQUIRES_MANUAL_MARKET_SELECTION"
STATE_UNKNOWN                  = "UNKNOWN"

RC_RESOLVED_FROM_RECOMMENDATION = "MARKET_IDENTITY_RESOLVED_FROM_RECOMMENDATION"
RC_RESOLVED_FROM_TRACE          = "MARKET_IDENTITY_RESOLVED_FROM_MARKET_TRACE"
RC_RESOLVED_FROM_PROTECTED_ALT  = "MARKET_IDENTITY_RESOLVED_FROM_PROTECTED_ALTERNATIVE"
RC_RESOLVED_FROM_ODDS           = "MARKET_IDENTITY_RESOLVED_FROM_ODDS_SNAPSHOT"
RC_AMBIGUOUS_ODDS               = "MARKET_IDENTITY_AMBIGUOUS_ODDS_MATCH"
RC_NO_PISTAS                    = "MARKET_IDENTITY_NO_HINTS"

DEFAULT_ODDS_TOLERANCE = 0.01


# ─────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────
def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _normalize_via_market_identity(market: Optional[str],
                                    selection: Optional[str],
                                    line: Any = None) -> Optional[dict]:
    """Wrapper over `market_identity.normalize_market_identity` con guard."""
    try:
        if not (market or selection):
            return None
        mi = _mi.normalize_market_identity({
            "market": market, "side": selection, "line": line,
        })
        if not isinstance(mi, dict):
            return None
        key = mi.get("identity_key") or ""
        # Reject UNKNOWN identities — el caller los debe filtrar y, si
        # quiere, llamar a este resolver con más pistas.
        if not key or key.startswith("UNKNOWN:"):
            return None
        # Si el resolver normalizó pero NO consiguió family, el identity_key
        # es basura ("?", "x", etc.). No lo aceptamos.
        if not mi.get("family"):
            return None
        return mi
    except Exception as exc:  # noqa: BLE001
        log.debug("market_identity normalisation failed: %s", exc)
        return None


def _detect_odds_in_entry(entry: dict) -> Optional[float]:
    """Best-effort: encuentra la cuota detectada en la entry descartada."""
    if not isinstance(entry, dict):
        return None
    # Direct fields
    for k in ("odds", "detected_odds", "odds_used", "decimal_odds"):
        v = _safe_float(entry.get(k))
        if v and v > 1.01:
            return v
    # Inside recommendation.odds_range "1.85-1.95"
    rec = entry.get("recommendation") or {}
    rg = rec.get("odds_range")
    if isinstance(rg, str) and "-" in rg:
        try:
            lo, hi = rg.split("-", 1)
            lo_f, hi_f = _safe_float(lo), _safe_float(hi)
            if lo_f and hi_f:
                return round((lo_f + hi_f) / 2, 2)
        except Exception:  # noqa: BLE001
            pass
    return None


def _iter_market_offers(markets: dict):
    """Yield ``(market_name, selection, line, odds_float)`` desde el dict
    de markets en ``odds_snapshots[-1].markets``.

    Estructura típica::

        markets = {
          "Match Winner": [{"home": 2.10, "draw": 3.30, "away": 3.40, ...}],
          "Over/Under":  [{"lines":[{"value":"Over 2.5","odd":1.85},...]}],
          "Double Chance": [{"home_draw":1.24, "draw_away":1.45, ...}],
          ...
        }
    """
    if not isinstance(markets, dict):
        return
    for market_name, rows in markets.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Pattern A: flat keys (home/draw/away/...).
            for sel_key, val in row.items():
                if sel_key == "lines" or sel_key == "bookmaker":
                    continue
                o = _safe_float(val)
                if o and o > 1.01:
                    yield (market_name, sel_key, None, o)
            # Pattern B: lines list (Over/Under, AH, ...).
            lines = row.get("lines")
            if isinstance(lines, list):
                for ln in lines:
                    if not isinstance(ln, dict):
                        continue
                    val = ln.get("value")
                    odd = _safe_float(ln.get("odd"))
                    if odd and odd > 1.01:
                        yield (market_name, str(val) if val is not None else None,
                                str(val) if val is not None else None, odd)
            elif isinstance(lines, dict):
                for line_label, odd in lines.items():
                    o = _safe_float(odd)
                    if o and o > 1.01:
                        yield (market_name, line_label, line_label, o)


def _market_identity_from_offer(market: str, selection: Optional[str],
                                  line: Optional[str]) -> Optional[dict]:
    """Intenta resolver una identity por (market, selection, line)."""
    # Si selection viene con la línea pegada ("Over 2.5"), partir.
    raw_line = None
    if isinstance(selection, str):
        s = selection.strip()
        # "Over 2.5" / "Under 1.5"
        import re as _re
        m = _re.match(r"^(over|under|m[áa]s\s*de|menos\s*de)\s*([0-9.+\-]+)$",
                      s, flags=_re.IGNORECASE)
        if m:
            raw_line = m.group(2)
    # Phase F74-post — normalisar variantes ES/EN de selección Doble
    # Oportunidad ("Local/Empate" → "1X").
    try:
        from .alternative_rescue import DOUBLE_CHANCE_SELECTION_ALIASES
        if isinstance(selection, str):
            selection = DOUBLE_CHANCE_SELECTION_ALIASES.get(selection, selection)
    except Exception:  # noqa: BLE001
        pass
    return _normalize_via_market_identity(market, selection, raw_line or line)


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def resolve_market_identity_for_discarded_entry(
    match: dict,
    discarded_entry: dict,
    *,
    odds_tolerance: float = DEFAULT_ODDS_TOLERANCE,
) -> dict:
    """Resuelve la market_identity para una entry descartada.

    Returns
    -------
    dict ::

        # Caso RESOLVED:
        {
          "state": "RESOLVED",
          "market_identity": {... formato F71 ...},
          "resolved_from":   "recommendation|market_trace|protected_alternative|odds_snapshot_match",
          "odds":            float|None,
          "reason_codes":    [str, ...],
        }

        # Caso AMBIGUOUS (bucket separado en summary):
        {
          "state": "REQUIRES_MANUAL_MARKET_SELECTION",
          "market_identity": None,
          "odds":            float,
          "candidate_markets": [
            {"identity_key":"DOUBLE_CHANCE:1X","market":"Doble Op.","selection":"1X","line":None,"odds":1.24},
            {"identity_key":"TOTAL_GOALS:OVER:1.5","market":"Over/Under","selection":"Over","line":1.5,"odds":1.24},
          ],
          "reason_codes": [...],
        }

        # Caso UNKNOWN:
        {
          "state": "UNKNOWN",
          "market_identity": None,
          "odds":            None,
          "reason_codes":    [str, ...],
        }
    """
    if not isinstance(discarded_entry, dict):
        return {"state": STATE_UNKNOWN, "market_identity": None, "odds": None,
                "reason_codes": [RC_NO_PISTAS]}
    reason_codes: list[str] = []

    # ── 1) Direct: discarded_entry.evaluated_market ─────────────────
    ev = discarded_entry.get("evaluated_market")
    if isinstance(ev, str) and ev.strip():
        mi = _normalize_via_market_identity(ev, discarded_entry.get("evaluated_selection")
                                              or discarded_entry.get("selection"))
        if mi:
            reason_codes.append(RC_RESOLVED_FROM_TRACE)
            return {"state": STATE_RESOLVED, "market_identity": mi,
                    "resolved_from": "evaluated_market",
                    "odds": _detect_odds_in_entry(discarded_entry),
                    "reason_codes": reason_codes}

    # ── 2) market_trace.evaluated_market ────────────────────────────
    trace = discarded_entry.get("market_trace") or {}
    if isinstance(trace, dict):
        ev2 = trace.get("evaluated_market")
        if isinstance(ev2, str) and ev2.strip():
            mi = _normalize_via_market_identity(ev2, trace.get("evaluated_selection"))
            if mi:
                reason_codes.append(RC_RESOLVED_FROM_TRACE)
                return {"state": STATE_RESOLVED, "market_identity": mi,
                        "resolved_from": "market_trace",
                        "odds": _detect_odds_in_entry(discarded_entry),
                        "reason_codes": reason_codes}

    # ── 3) recommendation.market + recommendation.selection ─────────
    rec = discarded_entry.get("recommendation") or {}
    if isinstance(rec, dict) and (rec.get("market") or rec.get("selection")):
        mi = _normalize_via_market_identity(rec.get("market"), rec.get("selection"))
        if mi:
            reason_codes.append(RC_RESOLVED_FROM_RECOMMENDATION)
            return {"state": STATE_RESOLVED, "market_identity": mi,
                    "resolved_from": "recommendation",
                    "odds": _detect_odds_in_entry(discarded_entry),
                    "reason_codes": reason_codes}

    # ── 4) protected_alternative ─────────────────────────────────────
    palt = discarded_entry.get("protected_alternative") or {}
    if isinstance(palt, dict) and (palt.get("market") or palt.get("selection")):
        mi = _normalize_via_market_identity(palt.get("market"), palt.get("selection"),
                                              palt.get("line"))
        if mi:
            reason_codes.append(RC_RESOLVED_FROM_PROTECTED_ALT)
            return {"state": STATE_RESOLVED, "market_identity": mi,
                    "resolved_from": "protected_alternative",
                    "odds": _detect_odds_in_entry(discarded_entry),
                    "reason_codes": reason_codes}

    # ── 5) Búsqueda por cuota detectada ─────────────────────────────
    detected_odds = _detect_odds_in_entry(discarded_entry)
    if detected_odds is not None:
        snaps = match.get("odds_snapshots") or []
        markets = (snaps[-1] or {}).get("markets") if snaps else None
        candidate_identities: list[dict] = []
        seen_keys: set[str] = set()
        for market_name, sel, line_str, odd in _iter_market_offers(markets or {}):
            if abs(odd - detected_odds) <= odds_tolerance:
                mi = _market_identity_from_offer(market_name, sel, line_str)
                if not mi:
                    continue
                key = mi.get("identity_key")
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                candidate_identities.append({
                    "identity_key": key,
                    "market":       market_name,
                    "selection":    sel,
                    "line":         mi.get("line"),
                    "odds":         odd,
                    "family":       mi.get("family"),
                    "_market_identity": mi,
                })

        if len(candidate_identities) == 1:
            chosen = candidate_identities[0]
            reason_codes.append(RC_RESOLVED_FROM_ODDS)
            return {
                "state": STATE_RESOLVED,
                "market_identity": chosen["_market_identity"],
                "resolved_from":   "odds_snapshot_match",
                "odds":            detected_odds,
                "reason_codes":    reason_codes,
            }
        if len(candidate_identities) >= 2:
            reason_codes.append(RC_AMBIGUOUS_ODDS)
            return {
                "state": STATE_REQUIRES_MANUAL,
                "market_identity": None,
                "odds":            detected_odds,
                "candidate_markets": [
                    {k: v for k, v in c.items() if k != "_market_identity"}
                    for c in candidate_identities
                ],
                "reason_codes":    reason_codes,
            }

    # ── 6) Nada funcionó ─────────────────────────────────────────────
    reason_codes.append(RC_NO_PISTAS)
    return {
        "state": STATE_UNKNOWN,
        "market_identity": None,
        "odds":            detected_odds,
        "reason_codes":    reason_codes,
    }


__all__ = [
    "STATE_RESOLVED", "STATE_REQUIRES_MANUAL", "STATE_UNKNOWN",
    "RC_RESOLVED_FROM_RECOMMENDATION", "RC_RESOLVED_FROM_TRACE",
    "RC_RESOLVED_FROM_PROTECTED_ALT", "RC_RESOLVED_FROM_ODDS",
    "RC_AMBIGUOUS_ODDS", "RC_NO_PISTAS",
    "resolve_market_identity_for_discarded_entry",
]
