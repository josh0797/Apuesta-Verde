"""Phase F74-post v2.5 — Opening Odds → Line Movement Wiring.

TheStatsAPI returns BOTH ``opening`` and ``last_seen`` per selection
(``thestatsapi_normalizer.normalize_thestatsapi_odds_to_apisports_shape``
preserves the opening side-by-side as ``_opening_odds``). This means we
can detect line movement on day one — without needing snapshot history.

This module reads ``match["odds_snapshots"][0]["_opening_odds"]`` (when
present) and matches the pick's recommended market+selection to compute
``detect_line_movement(opening_odds, current_odds, market_side)``. The
result is attached to:

  * ``pick["_line_movement"]``  — full payload (new, opt-in consumers).
  * ``pick["key_data"]["line_movement"]`` — legacy shape that the
    moneyball ``analyze_pick`` already reads when computing
    ``line_movement_favourable``.

Fail-soft everywhere: missing keys, malformed odds, unknown market →
no-op. Never raises.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

# Markets reconocidos por convención del normalizer:
#   "Match Winner", "Both Teams Score", "Goals Over/Under",
#   "Corners Over/Under", "Asian Handicap".
# Aliases ES/EN (suficientes para tomar opening cuando vienen de
# TheStatsAPI o normalizers compatibles).
_MARKET_ALIASES: dict[str, tuple[str, ...]] = {
    "Match Winner":       ("match winner", "1x2", "moneyline", "ganador del partido", "ganador"),
    "Both Teams Score":   ("both teams score", "btts", "ambos equipos anotan"),
    "Goals Over/Under":   ("goals over/under", "total goals", "totals", "over/under",
                           "goles totales", "más/menos", "mas/menos"),
    "Corners Over/Under": ("corners over/under", "total corners", "corners",
                           "córners", "corners totales"),
    "Asian Handicap":     ("asian handicap", "handicap asiático", "handicap asiatico"),
}

# Mapping selection_token → "market_side" hint para detect_line_movement.
_SIDE_HINTS: dict[str, str] = {
    "home":     "favorite",   # convención existente del engine
    "draw":     "underdog",
    "away":     "underdog",
    "yes":      "favorite",
    "no":       "underdog",
    "over":     "over",
    "under":    "under",
    "favorite": "favorite",
    "underdog": "underdog",
}


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


def _strip_accents_lower(s: str) -> str:
    import unicodedata as _u
    if not isinstance(s, str):
        return ""
    nf = _u.normalize("NFD", s)
    return "".join(c for c in nf if _u.category(c) != "Mn").lower().strip()


def _resolve_canonical_market(market_raw: str) -> Optional[str]:
    """Returns the canonical market name used as key in ``_opening_odds``.

    e.g. ``"Goles totales"`` → ``"Goals Over/Under"``.
    """
    if not isinstance(market_raw, str) or not market_raw.strip():
        return None
    norm = _strip_accents_lower(market_raw)
    for canonical, aliases in _MARKET_ALIASES.items():
        if norm == canonical.lower() or norm in aliases or any(a in norm for a in aliases):
            return canonical
    return None


def _resolve_selection_value(canonical_market: str, selection_raw: Any,
                              line: Optional[float] = None) -> Optional[str]:
    """Translate the pick's selection to the canonical value used in
    ``_opening_odds`` keys.

    Examples:
        ("Match Winner", "Home")             → "Home"
        ("Match Winner", "Local")            → "Home"
        ("Goals Over/Under", "Over", 2.5)    → "Over 2.5"
        ("Goals Over/Under", "Over 2.5")     → "Over 2.5"
        ("Both Teams Score", "Sí")           → "Yes"
    """
    if selection_raw is None:
        return None
    sel = str(selection_raw).strip()
    if not sel:
        return None
    sel_lc = _strip_accents_lower(sel)

    if canonical_market == "Match Winner":
        if sel_lc in ("home", "local", "1"):
            return "Home"
        if sel_lc in ("draw", "empate", "x"):
            return "Draw"
        if sel_lc in ("away", "visitante", "2"):
            return "Away"
        return None

    if canonical_market == "Both Teams Score":
        if sel_lc in ("yes", "si", "y"):
            return "Yes"
        if sel_lc in ("no", "n"):
            return "No"
        return None

    if canonical_market in ("Goals Over/Under", "Corners Over/Under"):
        # Detect "Over 2.5" / "Under 2.5" / "Más de 2.5" inline.
        m = re.match(r"^(over|under|m[áa]s\s*de|menos\s*de)\s*([0-9]+(?:\.[0-9]+)?)$",
                       sel_lc)
        if m:
            side = m.group(1)
            ln = m.group(2)
            api_side = ("Over" if side.startswith(("over", "mas", "más")) else "Under")
            return f"{api_side} {ln}"
        # Side-only with separate line.
        if sel_lc in ("over", "mas", "más", "mas de", "más de") and line is not None:
            return f"Over {line}"
        if sel_lc in ("under", "menos", "menos de") and line is not None:
            return f"Under {line}"
        return None

    if canonical_market == "Asian Handicap":
        if sel_lc in ("home", "local"):
            return "Home"
        if sel_lc in ("away", "visitante"):
            return "Away"
        return None

    return None


def _side_hint(canonical_market: str, selection_value: str) -> Optional[str]:
    """Map ('Goals Over/Under', 'Over 2.5') → 'over' for detect_line_movement."""
    sel_lc = selection_value.lower()
    for token, hint in _SIDE_HINTS.items():
        if sel_lc.startswith(token):
            return hint
    return None


def _current_odds_for(match_doc: dict, canonical_market: str,
                       selection_value: str) -> Optional[float]:
    """Look up the current odd for (market, selection) in the latest
    odds snapshot, scanning all bookmakers and picking the best (max).
    """
    snaps = match_doc.get("odds_snapshots") or []
    if not snaps or not isinstance(snaps[0], dict):
        return None
    bookmakers = snaps[0].get("bookmakers") or []
    if not isinstance(bookmakers, list):
        # Some normalizers use {"markets": {market: [...]}} flat shape.
        markets = snaps[0].get("markets") or {}
        rows = markets.get(canonical_market) or []
        best = None
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            o = _safe_float(r.get(selection_value))
            if o and (best is None or o > best):
                best = o
        return best
    best_overall = None
    for bm in bookmakers:
        if not isinstance(bm, dict):
            continue
        for bet in bm.get("bets") or []:
            if not isinstance(bet, dict):
                continue
            if bet.get("name") != canonical_market:
                continue
            for v in bet.get("values") or []:
                if not isinstance(v, dict):
                    continue
                if v.get("value") == selection_value:
                    o = _safe_float(v.get("odd"))
                    if o and (best_overall is None or o > best_overall):
                        best_overall = o
    return best_overall


def _opening_odds_for(opening_map: dict, canonical_market: str,
                       selection_value: str) -> Optional[float]:
    """Find the opening odd for (market, selection) in any bookmaker.

    ``_opening_odds`` keys have the shape ``"<bookmaker>|<market>|<value>"``.
    We pick the FIRST match deterministically (sorted by bookmaker name)
    so the line-movement reading is stable across runs.
    """
    if not isinstance(opening_map, dict) or not opening_map:
        return None
    suffix = f"|{canonical_market}|{selection_value}"
    candidates: list[tuple[str, float]] = []
    for key, val in opening_map.items():
        if not isinstance(key, str):
            continue
        if key.endswith(suffix):
            f = _safe_float(val)
            if f is not None:
                bm = key.split("|", 1)[0]
                candidates.append((bm, f))
    if not candidates:
        return None
    # Deterministic pick: best (highest) opening odd to mirror the
    # "best book" heuristic used downstream. Tie-break by bookmaker name.
    candidates.sort(key=lambda x: (-x[1], x[0]))
    return candidates[0][1]


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def attach_line_movement_from_opening_odds(pick: dict, match_doc: dict) -> bool:
    """Enrich ``pick`` with line-movement payload derived from
    ``match_doc["odds_snapshots"][0]["_opening_odds"]`` (preserved by
    the TheStatsAPI adapter).

    Returns ``True`` if a movement payload was attached, ``False``
    otherwise (no opening odds available, market unresolved, etc.).

    Side effects (mutates ``pick``):
      * ``pick["_line_movement"]``                — full payload.
      * ``pick["key_data"]["line_movement"]``     — legacy compact form
        ({"direction", "movement", "odds_movement", "steam_detected"})
        so ``moneyball_layer.analyze_pick`` reads it natively.
    """
    if not isinstance(pick, dict) or not isinstance(match_doc, dict):
        return False
    snaps = match_doc.get("odds_snapshots") or []
    if not snaps or not isinstance(snaps[0], dict):
        return False
    opening_map = snaps[0].get("_opening_odds") or {}
    if not opening_map:
        return False
    rec = pick.get("recommendation") or {}
    canonical_market = _resolve_canonical_market(rec.get("market") or "")
    if canonical_market is None:
        return False

    # Line if the pick carries it explicitly (Over/Under markets).
    line = _safe_float(rec.get("line"))
    selection_value = _resolve_selection_value(canonical_market,
                                                  rec.get("selection"), line=line)
    if selection_value is None:
        return False

    opening = _opening_odds_for(opening_map, canonical_market, selection_value)
    current = _current_odds_for(match_doc, canonical_market, selection_value)
    if opening is None and current is None:
        return False

    # Late-bound import so this module doesn't cycle with the engine.
    try:
        from .odds_value_engine import detect_line_movement
    except Exception:  # noqa: BLE001
        return False

    side_hint = _side_hint(canonical_market, selection_value)
    payload = detect_line_movement(
        opening_odds=opening, current_odds=current,
        market_side=side_hint,
    )
    payload["source"] = "thestatsapi_opening"
    payload["market"] = canonical_market
    payload["selection"] = selection_value

    pick["_line_movement"] = payload

    # Legacy `key_data.line_movement` compact shape — ``moneyball_layer``
    # reads ``direction`` to compute ``line_movement_favourable``.
    pick.setdefault("key_data", {})
    if isinstance(pick["key_data"], dict):
        pick["key_data"]["line_movement"] = {
            "direction":      payload.get("direction"),
            "movement":       payload.get("movement"),
            "odds_movement":  payload.get("odds_movement"),
            "steam_detected": payload.get("steam_detected"),
            "source":         "thestatsapi_opening",
        }
    return True


def enrich_picks_with_opening_movement(parsed: dict,
                                        matches_payload: list[dict]) -> int:
    """Iterate ``parsed["picks"]`` and attach line movement from each
    pick's match doc (matched by ``match_id``).

    Returns the count of picks enriched. Designed to be called BEFORE
    ``apply_moneyball_layer`` so the new line movement participates in
    the moneyball classification.
    """
    if not isinstance(parsed, dict) or not isinstance(matches_payload, list):
        return 0
    picks = parsed.get("picks") or []
    if not picks:
        return 0
    by_id = {m.get("match_id"): m for m in matches_payload
              if isinstance(m, dict) and m.get("match_id") is not None}
    enriched = 0
    for p in picks:
        if not isinstance(p, dict):
            continue
        match_id = p.get("match_id") or (p.get("recommendation") or {}).get("match_id")
        match_doc = by_id.get(match_id)
        if not match_doc:
            continue
        try:
            if attach_line_movement_from_opening_odds(p, match_doc):
                enriched += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("attach_line_movement_from_opening_odds failed: %s", exc)
    if enriched:
        log.info("[opening_odds_movement] enriched %d / %d picks with opening→line movement",
                 enriched, len(picks))
    return enriched


__all__ = [
    "attach_line_movement_from_opening_odds",
    "enrich_picks_with_opening_movement",
]
