"""Sprint-F99.5 · Multi-market odds aggregator (opt-in).

Extiende — **sin duplicar** — la cascada existente
``services.external_sources.odds_cascade`` para producir un payload
canónico de **mercados múltiples** con:

  * Vista **canónica** por mercado (consistencia de bookmaker + snapshot
    — única fuente válida para overround / probabilidad implícita).
  * Vista **best_prices** por selección (mejor precio disponible —
    solo para EV de ejecución, NO para vig removal).
  * **Movement tracking** opcional (opening / latest / change).

Binding del usuario (F99.5):

  1. **No duplicar** ``odds_cascade.py``: el aggregator consume el
     resultado existente (``fetch_direct_match_odds_cascade``) para H2H
     y delega a los adapters por mercado para el resto.
  2. **Providers + orden por defecto**:
       1. ``the_odds_api``
       2. ``thestatsapi``
       3. ``sofascore``
       4. ``oddsportal``
       5. ``manual``
  3. **Líneas reales conservadas** (no se forzan 2.5/3.5): el
     ``MarketSnapshot`` lleva la línea como float.
  4. **Cero leak a F74/editorial**: este módulo **no escribe** ningún
     campo bajo ``football_data_enrichment``. La presencia de odds NO
     puede degradar ``data_quality``.
  5. **Reason codes**: ``F99_ODDS_AGGREGATOR_USED``, ``F99_ODDS_PRIMARY_USED``,
     ``F99_ODDS_FALLBACK_USED``, ``F99_ODDS_NO_PRIMARY``,
     ``F99_ODDS_STALE_FALLBACK``, ``F99_ODDS_MARKET_NORMALIZED``,
     ``F99_ODDS_MARKET_UNSUPPORTED``, ``F99_ODDS_SCHEMA_INVALID``,
     ``F99_ODDS_ALL_SOURCES_EXHAUSTED``, ``F99_ODDS_MANUAL_REQUIRED``,
     ``F99_ODDS_BEST_PRICE_SELECTED``, ``F99_ODDS_MOVEMENT_RECORDED``.
  6. **Feature flag**: ``ENABLE_F99_ODDS_AGGREGATOR`` (opt-in).

Public API::

    aggregate_match_odds(
        home: str,
        away: str,
        *,
        sport_key: str = "soccer_epl",
        requested_markets: Iterable[str] | None = None,
        snapshots_from: dict | None = None,   # provider → list[quote dict]
        previous_snapshot: dict | None = None, # for movement tracking
        provider_priority: tuple[str, ...] | None = None,
    ) -> dict

The function is **synchronous and pure**: it does NOT perform IO. The
caller is responsible for fetching ``snapshots_from`` from the existing
adapters (the H2H cascade for ``the_odds_api``/``oddsportal``; TheStatsAPI
odds adapter; etc.) and feeding them in. This keeps the aggregator
testable, fail-soft, and easy to wire into background jobs.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

FLAG_ENV_VAR = "ENABLE_F99_ODDS_AGGREGATOR"

# Provider ranking — binding F99.5 g).
DEFAULT_PROVIDER_PRIORITY: tuple[str, ...] = (
    "the_odds_api",
    "thestatsapi",
    "sofascore",
    "oddsportal",
    "manual",
)

# Canonical market families — binding F99.5 h).
MARKET_FAMILIES: tuple[str, ...] = (
    "MATCH_WINNER",
    "DOUBLE_CHANCE",
    "DRAW_NO_BET",
    "ASIAN_HANDICAP",
    "TOTAL_GOALS",
    "BOTH_TEAMS_TO_SCORE",
    "TOTAL_CORNERS",
    "TEAM_CORNERS",
    "ASIAN_CORNERS",
    "TOTAL_CARDS",
)

# Reason codes — binding F99.5 l).
RC_AGGREGATOR_USED        = "F99_ODDS_AGGREGATOR_USED"
RC_PRIMARY_USED           = "F99_ODDS_PRIMARY_USED"
RC_FALLBACK_USED          = "F99_ODDS_FALLBACK_USED"
RC_NO_PRIMARY             = "F99_ODDS_NO_PRIMARY"
RC_STALE_FALLBACK         = "F99_ODDS_STALE_FALLBACK"
RC_MARKET_NORMALIZED      = "F99_ODDS_MARKET_NORMALIZED"
RC_MARKET_UNSUPPORTED     = "F99_ODDS_MARKET_UNSUPPORTED"
RC_SCHEMA_INVALID         = "F99_ODDS_SCHEMA_INVALID"
RC_ALL_SOURCES_EXHAUSTED  = "F99_ODDS_ALL_SOURCES_EXHAUSTED"
RC_MANUAL_REQUIRED        = "F99_ODDS_MANUAL_REQUIRED"
RC_BEST_PRICE_SELECTED    = "F99_ODDS_BEST_PRICE_SELECTED"
RC_MOVEMENT_RECORDED      = "F99_ODDS_MOVEMENT_RECORDED"


def is_enabled() -> bool:
    raw = os.environ.get(FLAG_ENV_VAR, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


# ─────────────────────────────────────────────────────────────────────
# Quote / Snapshot normalization
# ─────────────────────────────────────────────────────────────────────
_MARKET_ALIASES = {
    "match_winner":         "MATCH_WINNER",
    "1x2":                  "MATCH_WINNER",
    "h2h":                  "MATCH_WINNER",
    "moneyline":            "MATCH_WINNER",
    "winner":               "MATCH_WINNER",
    "double_chance":        "DOUBLE_CHANCE",
    "draw_no_bet":          "DRAW_NO_BET",
    "asian_handicap":       "ASIAN_HANDICAP",
    "handicap":             "ASIAN_HANDICAP",
    "total":                "TOTAL_GOALS",
    "total_goals":          "TOTAL_GOALS",
    "totals":               "TOTAL_GOALS",
    "over_under":           "TOTAL_GOALS",
    "btts":                 "BOTH_TEAMS_TO_SCORE",
    "both_teams_to_score":  "BOTH_TEAMS_TO_SCORE",
    "total_corners":        "TOTAL_CORNERS",
    "corners_total":        "TOTAL_CORNERS",
    "team_corners":         "TEAM_CORNERS",
    "asian_corners":        "ASIAN_CORNERS",
    "total_cards":          "TOTAL_CARDS",
}


def _normalize_family(raw: Any) -> Optional[str]:
    if not raw:
        return None
    key = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    return _MARKET_ALIASES.get(key) or (key.upper() if key.upper() in MARKET_FAMILIES else None)


def _normalize_selection(family: str, raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    # Common single-token normalizers.
    SHORT = {
        "h": "HOME", "home": "HOME", "1": "HOME",
        "a": "AWAY", "away": "AWAY", "2": "AWAY",
        "d": "DRAW", "draw": "DRAW", "x": "DRAW",
        "over": "OVER", "under": "UNDER", "o": "OVER", "u": "UNDER",
        "yes": "YES", "no": "NO",
    }
    if s in SHORT:
        return SHORT[s]
    if family == "DOUBLE_CHANCE":
        m = {"1x": "1X", "12": "12", "x2": "X2", "home/draw": "1X",
              "home/away": "12", "draw/away": "X2"}
        return m.get(s)
    if family == "DRAW_NO_BET":
        return "HOME" if s in ("home", "1") else "AWAY" if s in ("away", "2") else None
    if family in ("ASIAN_HANDICAP", "ASIAN_CORNERS"):
        return "HOME" if s in ("home", "1") else "AWAY" if s in ("away", "2") else None
    return None


def _coerce_price(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 1.0 else None  # decimal odds must be > 1
    except (TypeError, ValueError):
        return None


def _coerce_line(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalize_quote(quote: Any, *, provider: str) -> Optional[dict]:
    """Coerce a quote-like input into the canonical Quote shape.

    Canonical Quote::

        {
          "market_family":  str,        # one of MARKET_FAMILIES
          "selection":      str,        # HOME/DRAW/AWAY/OVER/UNDER/YES/NO/1X/...
          "line":           float|None,
          "price":          float,      # decimal > 1.0
          "bookmaker":      str|None,
          "provider":       str,
          "snapshot_at":    str (ISO-8601 UTC),
        }
    """
    if not isinstance(quote, dict):
        return None
    family = _normalize_family(quote.get("market_family") or quote.get("market"))
    if not family:
        return None
    selection = _normalize_selection(family, quote.get("selection"))
    if selection is None:
        return None
    price = _coerce_price(quote.get("price"))
    if price is None:
        return None
    return {
        "market_family":  family,
        "selection":      selection,
        "line":           _coerce_line(quote.get("line")),
        "price":          price,
        "bookmaker":      str(quote.get("bookmaker")) if quote.get("bookmaker") else None,
        "provider":       provider,
        "snapshot_at":    quote.get("snapshot_at") or datetime.now(timezone.utc).isoformat(),
    }


def _quotes_by_provider(
    snapshots_from: Optional[dict],
) -> dict[str, list[dict]]:
    """Coerce caller-supplied raw quotes into normalised lists per provider."""
    out: dict[str, list[dict]] = {}
    if not isinstance(snapshots_from, dict):
        return out
    for provider, raw_list in snapshots_from.items():
        if not isinstance(raw_list, list):
            continue
        cleaned: list[dict] = []
        for q in raw_list:
            n = _normalize_quote(q, provider=str(provider))
            if n is not None:
                cleaned.append(n)
        if cleaned:
            out[str(provider)] = cleaned
    return out


# ─────────────────────────────────────────────────────────────────────
# Canonical market view (consistencia bookmaker + snapshot)
# ─────────────────────────────────────────────────────────────────────
def _market_signature(q: dict) -> tuple:
    """Canonical key that identifies a *market line* across selections.

    Two quotes belong to the SAME market line iff they share
    (family, line, bookmaker, snapshot_at, provider). This lets us
    deduce overround using consistent inputs (binding F99.5 i).
    """
    return (
        q["market_family"],
        q.get("line") if q.get("line") is not None else "_",
        q.get("bookmaker") or "_",
        q.get("provider"),
        q.get("snapshot_at"),
    )


def _select_canonical_per_market(
    quotes_by_provider: dict[str, list[dict]],
    *,
    provider_priority: tuple[str, ...],
    requested: set[str],
) -> tuple[dict, list[str]]:
    """For each requested market family pick the BEST canonical market line.

    Best = highest-ranked provider that has a complete line (i.e. has
    all the required selections to compute overround). We pick the
    market line with the smallest overround within that provider.

    Returns ``(canonical_dict, reason_codes)``.
    """
    REQUIRED = {
        "MATCH_WINNER":         {"HOME", "DRAW", "AWAY"},
        "DOUBLE_CHANCE":        {"1X", "X2", "12"},
        "DRAW_NO_BET":          {"HOME", "AWAY"},
        "ASIAN_HANDICAP":       {"HOME", "AWAY"},
        "TOTAL_GOALS":          {"OVER", "UNDER"},
        "BOTH_TEAMS_TO_SCORE":  {"YES", "NO"},
        "TOTAL_CORNERS":        {"OVER", "UNDER"},
        "TEAM_CORNERS":         {"OVER", "UNDER"},
        "ASIAN_CORNERS":        {"HOME", "AWAY"},
        "TOTAL_CARDS":          {"OVER", "UNDER"},
    }
    canonical: dict[str, dict] = {}
    codes: list[str] = []

    for family in requested:
        if family not in MARKET_FAMILIES:
            codes.append(RC_MARKET_UNSUPPORTED)
            continue

        chosen_market: Optional[dict] = None
        winner_provider: Optional[str] = None

        for provider in provider_priority:
            quotes = [q for q in quotes_by_provider.get(provider, [])
                       if q["market_family"] == family]
            if not quotes:
                continue
            # Group quotes by market signature.
            groups: dict[tuple, list[dict]] = {}
            for q in quotes:
                groups.setdefault(_market_signature(q), []).append(q)
            # Find groups that satisfy REQUIRED selections.
            req_selections = REQUIRED[family]
            valid_groups = []
            for sig, grp in groups.items():
                sels = {q["selection"] for q in grp}
                if req_selections.issubset(sels):
                    # Pick at most one quote per selection within the group.
                    chosen_quotes = {q["selection"]: q for q in grp}
                    # Compute overround for ranking.
                    try:
                        overround = sum(1.0 / chosen_quotes[s]["price"]
                                          for s in req_selections)
                    except (KeyError, ZeroDivisionError):
                        continue
                    valid_groups.append((overround, sig, chosen_quotes))
            if not valid_groups:
                continue
            # Pick group with the smallest overround (= sharpest line).
            valid_groups.sort(key=lambda t: t[0])
            overround, sig, chosen_quotes = valid_groups[0]
            chosen_market = {
                "market_family": family,
                "provider":      provider,
                "bookmaker":     sig[2] if sig[2] != "_" else None,
                "snapshot_at":   sig[4],
                "line":          chosen_quotes[next(iter(req_selections))].get("line"),
                "selections":    {sel: {"price": q["price"], "line": q.get("line")}
                                    for sel, q in chosen_quotes.items()},
                "overround":     overround,
            }
            winner_provider = provider
            break  # binding: first viable provider wins, no cross-mix.

        if chosen_market is None:
            codes.append(RC_NO_PRIMARY)
            canonical[family] = {
                "market_family": family,
                "provider":      None,
                "bookmaker":     None,
                "snapshot_at":   None,
                "line":          None,
                "selections":    {},
                "overround":     None,
                "reason_codes":  [RC_NO_PRIMARY],
            }
            continue

        rc_local = [RC_MARKET_NORMALIZED]
        if winner_provider == provider_priority[0]:
            rc_local.append(RC_PRIMARY_USED)
        else:
            rc_local.append(RC_FALLBACK_USED)
        chosen_market["reason_codes"] = rc_local
        canonical[family] = chosen_market

    return canonical, codes


# ─────────────────────────────────────────────────────────────────────
# Best-available-price view (vista adicional, NUNCA usada para vig)
# ─────────────────────────────────────────────────────────────────────
def _select_best_prices(
    quotes_by_provider: dict[str, list[dict]],
    *,
    requested: set[str],
) -> dict:
    """For each requested family + selection, pick the HIGHEST decimal price.

    The result is purely informational (EV-of-execution view). The
    binding forbids using it to compute overround.
    """
    out: dict[str, dict] = {}
    for provider, quotes in quotes_by_provider.items():
        for q in quotes:
            family = q["market_family"]
            if family not in requested:
                continue
            sel = q["selection"]
            entry = out.setdefault(family, {})
            best = entry.get(sel)
            if best is None or q["price"] > best["price"]:
                entry[sel] = {
                    "price":       q["price"],
                    "line":        q.get("line"),
                    "bookmaker":   q.get("bookmaker"),
                    "provider":    provider,
                    "snapshot_at": q.get("snapshot_at"),
                }
    return out


# ─────────────────────────────────────────────────────────────────────
# Movement tracking (snapshot vs previous)
# ─────────────────────────────────────────────────────────────────────
def _compute_movement(
    current_canonical: dict,
    previous_snapshot: Optional[dict],
) -> dict:
    """Compute opening / latest / change for each canonical market.

    Returns ``{family: {opening_price, latest_price, absolute_change, ...}}``.
    Empty dict when there's nothing to compare.
    """
    if not isinstance(previous_snapshot, dict):
        return {}
    out: dict[str, dict] = {}
    prev_canon = (previous_snapshot.get("canonical_markets") or {})
    snapshots_count = int(previous_snapshot.get("snapshots_count", 0)) + 1
    for family, market in current_canonical.items():
        prev = prev_canon.get(family) or {}
        prev_sels = prev.get("selections") or {}
        cur_sels  = market.get("selections")  or {}
        # Track movement per selection.
        per_sel: dict[str, dict] = {}
        for sel, cur in cur_sels.items():
            p_open = (prev.get("opening_selections") or {}).get(sel, {}).get("price")
            p_prev = prev_sels.get(sel, {}).get("price")
            p_cur  = cur.get("price")
            if p_cur is None:
                continue
            opening = p_open if p_open is not None else p_prev if p_prev is not None else p_cur
            absolute = round(p_cur - opening, 4)
            percentage = round((absolute / opening) * 100.0, 2) if opening else 0.0
            per_sel[sel] = {
                "opening_price":     opening,
                "latest_price":      p_cur,
                "absolute_change":   absolute,
                "percentage_change": percentage,
            }
        if per_sel:
            out[family] = {
                "movement_per_selection": per_sel,
                "snapshots_count":        snapshots_count,
            }
    return out


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def aggregate_match_odds(
    home: str,
    away: str,
    *,
    sport_key: str = "soccer_epl",
    requested_markets: Optional[Iterable[str]] = None,
    snapshots_from: Optional[dict] = None,
    previous_snapshot: Optional[dict] = None,
    provider_priority: Optional[tuple[str, ...]] = None,
) -> dict:
    """Aggregate per-market odds for a single match.

    Pure function. Caller passes already-fetched raw quotes per provider
    in ``snapshots_from``. The aggregator normalises, picks canonical
    markets, computes best-available-prices and (optionally) movement.

    The returned shape is INDEPENDENT from ``football_data_enrichment``;
    callers must store it under separate keys (e.g. ``odds_snapshots`` /
    ``odds_status`` / ``market_evaluation``) — binding guard #10.
    """
    if not isinstance(home, str) or not isinstance(away, str) or not home or not away:
        return {
            "available":         False,
            "home":               home or "",
            "away":               away or "",
            "sport_key":          sport_key,
            "canonical_markets":  {},
            "best_prices":        {},
            "movement":           {},
            "providers_consulted": [],
            "reason_codes":       [RC_SCHEMA_INVALID],
            "produced_at":        datetime.now(timezone.utc).isoformat(),
        }

    priority = provider_priority or DEFAULT_PROVIDER_PRIORITY
    if requested_markets:
        requested = {f for f in (_normalize_family(m) for m in requested_markets) if f}
    else:
        requested = set(MARKET_FAMILIES)

    quotes_by_provider = _quotes_by_provider(snapshots_from)
    providers_consulted = list(quotes_by_provider.keys())

    canonical, codes = _select_canonical_per_market(
        quotes_by_provider,
        provider_priority=priority,
        requested=requested,
    )
    best_prices = _select_best_prices(
        quotes_by_provider,
        requested=requested,
    )

    movement = _compute_movement(canonical, previous_snapshot)

    reason_codes: list[str] = [RC_AGGREGATOR_USED] + codes
    if any(market.get("provider") is not None for market in canonical.values()):
        if RC_MARKET_NORMALIZED not in reason_codes:
            reason_codes.append(RC_MARKET_NORMALIZED)
    if not quotes_by_provider:
        reason_codes.append(RC_ALL_SOURCES_EXHAUSTED)
    if best_prices:
        reason_codes.append(RC_BEST_PRICE_SELECTED)
    if movement:
        reason_codes.append(RC_MOVEMENT_RECORDED)
    if not any(market.get("provider") for market in canonical.values()):
        reason_codes.append(RC_MANUAL_REQUIRED)

    has_any_market = any(market.get("provider") for market in canonical.values())
    return {
        "available":          has_any_market,
        "home":               home,
        "away":               away,
        "sport_key":          sport_key,
        "canonical_markets":  canonical,
        "best_prices":        best_prices,
        "movement":           movement,
        "providers_consulted": providers_consulted,
        "reason_codes":       reason_codes,
        "produced_at":        datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "FLAG_ENV_VAR",
    "DEFAULT_PROVIDER_PRIORITY",
    "MARKET_FAMILIES",
    "RC_AGGREGATOR_USED",
    "RC_PRIMARY_USED",
    "RC_FALLBACK_USED",
    "RC_NO_PRIMARY",
    "RC_STALE_FALLBACK",
    "RC_MARKET_NORMALIZED",
    "RC_MARKET_UNSUPPORTED",
    "RC_SCHEMA_INVALID",
    "RC_ALL_SOURCES_EXHAUSTED",
    "RC_MANUAL_REQUIRED",
    "RC_BEST_PRICE_SELECTED",
    "RC_MOVEMENT_RECORDED",
    "is_enabled",
    "aggregate_match_odds",
]


# ════════════════════════════════════════════════════════════════════════════
# Sprint-D9-followup-2 (Jun-2026) — High-level façade with the new contract
# requested by the user.
#
# Signature (binding):
#
#     async def fetch_football_odds(
#         match: dict,
#         source_ids: dict,
#         *,
#         client,
#         db=None,
#     ) -> dict
#
# Cascade order:
#   1. Oddspedia (exotic markets primary — mercados ex\u00f3ticos + selecciones)
#   2. TheStatsAPI
#   3. SofaScore
#   4. OddsPortal
#   5. Manual odds
#
# Advance on:
#   - request failed
#   - empty response
#   - empty bookmakers
#   - empty markets
#   - schema not recognized
#   - invalid odds
#
# Output (success)::
#
#     {
#         "available": True,
#         "source": "oddspedia" | "thestatsapi" | "sofascore" | "oddsportal" | "manual",
#         "markets": {...},
#         "snapshot_at": iso8601,
#         "reason_codes": ["ODDSPEDIA_HIT", ...],
#     }
#
# Output (no odds)::
#
#     {
#         "available": False,
#         "state": "NO_ODDS_AVAILABLE",
#         "reason_codes": [
#             "NO_ODDS_AVAILABLE_FROM_ALL_SOURCES",
#             "MANUAL_ODDS_REQUIRED",
#         ],
#     }
# ════════════════════════════════════════════════════════════════════════════

STATE_NO_ODDS = "NO_ODDS_AVAILABLE"
RC_NO_ODDS_ALL = "NO_ODDS_AVAILABLE_FROM_ALL_SOURCES"
RC_MANUAL_REQUIRED_USER = "MANUAL_ODDS_REQUIRED"


def _names_from_match(match: dict) -> Tuple[str, str, Optional[str], Optional[str]]:
    """Best-effort extraction of (home_name, away_name, league, kickoff_iso)."""
    teams = (match or {}).get("teams") or {}
    home = (teams.get("home") or {}).get("name") if isinstance(teams, dict) else ""
    away = (teams.get("away") or {}).get("name") if isinstance(teams, dict) else ""
    home = home or (match or {}).get("home_name") or (match or {}).get("home", "") or ""
    away = away or (match or {}).get("away_name") or (match or {}).get("away", "") or ""
    league = ((match or {}).get("league") or {}).get("name") if isinstance(match.get("league"), dict) else (match or {}).get("league_name")
    kickoff_iso = (match or {}).get("kickoff_iso") or (match or {}).get("date")
    if hasattr(kickoff_iso, "isoformat"):
        try:
            kickoff_iso = kickoff_iso.isoformat()
        except Exception:
            kickoff_iso = None
    return str(home or ""), str(away or ""), league, kickoff_iso


async def _try_oddspedia(match, source_ids, client, db) -> Optional[dict]:
    try:
        from .external_sources import oddspedia_scraper as _op
    except Exception:
        return None
    home, away, league, kickoff_iso = _names_from_match(match)
    if not home or not away:
        return None
    try:
        r = await _op.fetch_match_odds(
            home, away, client=client, db=db,
            league_name=league, kickoff_iso=kickoff_iso,
        )
    except Exception:
        return None
    if isinstance(r, dict) and r.get("available") and r.get("markets"):
        return r
    return None


async def _try_cuotasahora(match, source_ids, client, db) -> Optional[dict]:
    # DEPRECATED Jun-2026: replaced by _try_oddspedia. Kept as no-op shim so
    # existing tests/imports continue to compile; will be removed once all
    # callers have migrated.
    return None


async def _try_thestatsapi(match, source_ids, client, db) -> Optional[dict]:
    try:
        from .external_sources import thestatsapi_odds_adapter as _ts
    except Exception:
        return None
    home, away, league, kickoff_iso = _names_from_match(match)
    try:
        shape, norm, mid = await _ts.fetch_odds_api_sports_shape(
            client, match, home_name=home, away_name=away,
            kickoff=kickoff_iso, league_name=league,
        )
    except Exception:
        return None
    if not (isinstance(norm, dict) and norm.get("available")):
        return None
    # Build a "markets" dict from the normalised bookmakers list.
    markets: Dict[str, Any] = {}
    bms = norm.get("bookmakers") or []
    if bms:
        # h2h: take the first bookmaker exposing 1X2
        for bm in bms:
            for bet in (bm.get("bets") or []):
                bname = (bet.get("name") or "").lower()
                if "match winner" in bname or "moneyline" in bname or bname == "1x2":
                    h = d = a = None
                    for v in (bet.get("values") or []):
                        vn = (v.get("value") or "").lower()
                        try:
                            odd = float(v.get("odd") or 0)
                        except (ValueError, TypeError):
                            continue
                        if vn in ("home", "1"): h = odd
                        elif vn in ("draw", "x"): d = odd
                        elif vn in ("away", "2"): a = odd
                    if h and d and a:
                        markets["h2h"] = {"home": h, "draw": d, "away": a}
                        break
            if markets.get("h2h"):
                break
    if not markets:
        return None
    return {
        "available": True,
        "source": "thestatsapi",
        "markets": markets,
        "snapshot_at": _utcnow().isoformat(),
        "reason_codes": ["THESTATSAPI_ODDS_USED"],
        "_match_id": mid,
    }


async def _try_sofascore(match, source_ids, client, db) -> Optional[dict]:
    """SofaScore odds proxy (uses existing hydrator if available)."""
    try:
        from . import football_sofascore_hydrator as _sofa  # type: ignore
    except Exception:
        return None
    home, away, league, kickoff_iso = _names_from_match(match)
    if not hasattr(_sofa, "fetch_match_odds"):
        return None
    try:
        r = await _sofa.fetch_match_odds(
            home, away, client=client, db=db,
            league_name=league, kickoff_iso=kickoff_iso,
        )
    except Exception:
        return None
    if isinstance(r, dict) and r.get("available") and r.get("markets"):
        if not r.get("source"):
            r["source"] = "sofascore"
        if "reason_codes" not in r:
            r["reason_codes"] = ["SOFASCORE_ODDS_USED"]
        return r
    return None


async def _try_oddsportal(match, source_ids, client, db) -> Optional[dict]:
    try:
        from .external_sources import odds_portal_client as _op
    except Exception:
        return None
    home, away, league, kickoff_iso = _names_from_match(match)
    if not hasattr(_op, "fetch_match_odds"):
        return None
    try:
        r = await _op.fetch_match_odds(
            home, away, client=client, db=db,
            league_name=league, kickoff_iso=kickoff_iso,
        )
    except Exception:
        return None
    if isinstance(r, dict) and r.get("available") and (r.get("markets") or r.get("home_odds")):
        markets = r.get("markets") or {}
        if not markets and r.get("home_odds") and r.get("away_odds"):
            markets = {"h2h": {
                "home": r.get("home_odds"),
                "draw": r.get("draw_odds"),
                "away": r.get("away_odds"),
            }}
        return {
            "available": True,
            "source": "oddsportal",
            "markets": markets,
            "snapshot_at": _utcnow().isoformat(),
            "reason_codes": ["ODDSPORTAL_ODDS_USED"],
        }
    return None


async def _try_manual_odds(match, source_ids, client, db) -> Optional[dict]:
    """Look up manual odds previously entered by the user in Mongo."""
    if db is None:
        return None
    try:
        match_id = (match or {}).get("match_id") or (match or {}).get("id")
        if not match_id:
            return None
        coll = db.get_collection("manual_odds_overrides") if hasattr(db, "get_collection") else db["manual_odds_overrides"]
        doc = await coll.find_one({"match_id": str(match_id)})
        if not doc or not doc.get("markets"):
            return None
        return {
            "available": True,
            "source": "manual",
            "markets": doc["markets"],
            "snapshot_at": _utcnow().isoformat(),
            "reason_codes": ["MANUAL_ODDS_USED"],
        }
    except Exception:
        return None


_FETCH_CASCADE_NAMES = (
    ("oddspedia",   "_try_oddspedia",    "ODDSPEDIA_TRIED"),
    ("thestatsapi", "_try_thestatsapi",  "THESTATSAPI_TRIED"),
    ("sofascore",   "_try_sofascore",    "SOFASCORE_TRIED"),
    ("oddsportal",  "_try_oddsportal",   "ODDSPORTAL_TRIED"),
    ("manual",      "_try_manual_odds",  "MANUAL_ODDS_TRIED"),
)


async def fetch_football_odds(
    match: dict,
    source_ids: dict,
    *,
    client,
    db=None,
) -> dict:
    """High-level cascade entry point.  See module docstring for contract."""
    import sys as _sys
    _mod = _sys.modules[__name__]  # late binding so monkeypatched funcs are picked up
    reason_trail: List[str] = []
    for name, fn_name, tried_code in _FETCH_CASCADE_NAMES:
        reason_trail.append(tried_code)
        fn = getattr(_mod, fn_name, None)
        if fn is None:
            continue
        try:
            result = await fn(match, source_ids or {}, client, db)
        except Exception as exc:  # noqa: BLE001
            log.debug("[fetch_football_odds] %s raised: %s", name, exc)
            reason_trail.append(f"{name.upper()}_RAISED")
            continue
        if result and isinstance(result, dict) and result.get("available"):
            rc = list(result.get("reason_codes") or [])
            for code in reason_trail:
                if code not in rc:
                    rc.append(code)
            result["reason_codes"] = rc
            return result

    # All sources exhausted → emit canonical NO_ODDS_AVAILABLE envelope.
    return {
        "available": False,
        "state": STATE_NO_ODDS,
        "reason_codes": reason_trail + [RC_NO_ODDS_ALL, RC_MANUAL_REQUIRED_USER],
    }


# Re-export
__all__ += [
    "fetch_football_odds",
    "STATE_NO_ODDS",
    "RC_NO_ODDS_ALL",
    "RC_MANUAL_REQUIRED_USER",
]
