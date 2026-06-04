"""Football Market Selection Layer (pure).

Given all upstream Moneyball signals (goal_pressure_profile,
recent_fixtures / under-profile, corner_form, form_guard, football_quality,
historical_pattern_match) decide the **most protected** football market.

Design principles (NON-NEGOTIABLE):
  * Pure (no IO).
  * Fail-soft: missing inputs degrade to watchlist / manual-odds buckets
    rather than throwing.
  * Conservative: defaults bias toward protected markets (Under 3.5 over
    Under 2.5 when volatility is suspected). NEVER auto-pick Over /
    aggressive markets without explicit support.
  * Decoupled from LLM: the LLM still emits a recommendation; this layer
    annotates / nudges via `protected_alternative` and `reason_codes`
    and only escalates to ``Watchlist`` / ``Manual Odds Review`` when the
    raw recommendation is unsupported.

Markets evaluated (Moneyball-aligned, football-specific):
  - Moneyline (home / away / draw)
  - Double Chance (1X / X2 / 12)
  - Under 2.5 / Under 3.5 (with Under 3.5 as the canonical protected
    alternative when volatility is suspected)
  - Over 2.5 (only when explicitly supported)
  - Corners total (only as protected alt when corner data is reliable)
  - Watchlist / Manual Odds Review (terminal)
"""

from __future__ import annotations

from typing import Any

from .football_goal_pressure_profile import (
    HIGH_PRESSURE, MODERATE_PRESSURE, LOW_PRESSURE, NEUTRAL_PRESSURE,
    UNAVAILABLE,
)

# Market labels
MKT_MONEYLINE_HOME       = "Moneyline Home"
MKT_MONEYLINE_AWAY       = "Moneyline Away"
MKT_MONEYLINE_DRAW       = "Moneyline Draw"
MKT_DOUBLE_CHANCE_1X     = "Double Chance 1X"
MKT_DOUBLE_CHANCE_X2     = "Double Chance X2"
MKT_DOUBLE_CHANCE_12     = "Double Chance 12"
MKT_UNDER_25             = "Under 2.5"
MKT_UNDER_35             = "Under 3.5"
MKT_OVER_25              = "Over 2.5"
MKT_BTTS_NO              = "BTTS No"
MKT_BTTS_YES             = "BTTS Yes"
MKT_CORNERS_UNDER        = "Corners Under"
MKT_CORNERS_OVER         = "Corners Over"
MKT_WATCHLIST            = "Watchlist"
MKT_MANUAL_ODDS          = "Manual Odds Review"

# Reason codes (canonical, exported)
RC_PROTECTED_MARKET_SELECTED       = "PROTECTED_FOOTBALL_MARKET_SELECTED"
RC_UNDER_3_5_PREFERRED_OVER_2_5    = "UNDER_3_5_PREFERRED_OVER_UNDER_2_5"
RC_OVER_REQUIRES_EXPLICIT_SUPPORT  = "OVER_REQUIRES_EXPLICIT_SUPPORT"
RC_DOUBLE_CHANCE_SAFER_THAN_ML     = "DOUBLE_CHANCE_SAFER_THAN_MONEYLINE"
RC_MONEYLINE_FRAGILE               = "FOOTBALL_MONEYLINE_FRAGILE"
RC_BTTS_NO_PROTECTED               = "BTTS_NO_PROTECTED_BY_CLEAN_SHEETS"
RC_CORNERS_TRAP_BLOCKS_PICK        = "CORNERS_TRAP_BLOCKS_PICK"
RC_FORM_GUARD_FRAGILE              = "FORM_GUARD_FRAGILE"
RC_LEAGUE_QUALITY_LOW              = "FOOTBALL_LEAGUE_QUALITY_LOW"
RC_MANUAL_ODDS_REVIEW_REQUIRED     = "FOOTBALL_MANUAL_ODDS_REVIEW_REQUIRED"
RC_WATCHLIST_INSUFFICIENT_SUPPORT  = "FOOTBALL_WATCHLIST_INSUFFICIENT_SUPPORT"
RC_NO_INPUTS_AVAILABLE             = "FOOTBALL_MARKET_SELECTION_NO_INPUTS"
RC_PATTERN_MEMORY_PREFERRED_MARKET = "PATTERN_MEMORY_SUGGESTED_PROTECTED_ALT"


def _safe_get(d: Any, *path, default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _market_lower(s: str | None) -> str:
    return (s or "").strip().lower()


def _is_under_market(m: str | None) -> bool:
    m = _market_lower(m)
    return ("under" in m or "menos de" in m) and "team total" not in m and "corner" not in m


def _is_over_market(m: str | None) -> bool:
    m = _market_lower(m)
    return ("over" in m or "más de" in m or "mas de" in m) and "team total" not in m and "corner" not in m


def _is_moneyline(m: str | None) -> bool:
    m = _market_lower(m)
    return "moneyline" in m or m in {"1", "x", "2", "home", "away", "draw"}


def select_football_market(
    pick_payload: dict | None,
    *,
    pregame_snapshot: dict | None = None,
    pattern_match: dict | None = None,
) -> dict:
    """Pure: select the most protected football market for this pick.

    Returns canonical shape::

        {"market_selection": {
            "recommended_market":    str,
            "protected_alternative": str | None,
            "market_confidence":     int,
            "fragility":             int,
            "reason_codes":          [str, ...],
            "why_this_market":       str,
            "why_not_other_markets": [str, ...],
            "requires_manual_odds":  bool,
            "watchlist":             bool,
        }}
    """
    pp = pick_payload if isinstance(pick_payload, dict) else {}
    snap = pregame_snapshot if isinstance(pregame_snapshot, dict) else {}
    pm = pattern_match if isinstance(pattern_match, dict) else (
        pp.get("historical_pattern_match") or {}
    )

    rec = pp.get("recommendation") if isinstance(pp.get("recommendation"), dict) else {}
    current_market = rec.get("market") or pp.get("market")

    # Locate the pressure + form blocks.
    pressure = (pp.get("goal_pressure_profile")
                  or snap.get("goal_pressure_profile")
                  or _safe_get(snap, "pregame", "goal_pressure_profile")
                  or {})
    combined = pressure.get("combined") if isinstance(pressure, dict) else {}
    tier = combined.get("pressure_tier") if isinstance(combined, dict) else None
    flags = combined.get("flags") if isinstance(combined, dict) else {}

    home_form = _safe_get(snap, "pregame", "home", "form") or {}
    away_form = _safe_get(snap, "pregame", "away", "form") or {}

    fq = (pp.get("_football_quality")
          or _safe_get(snap, "pregame", "football_quality")
          or {})
    fg = (pp.get("_form_guard")
          or _safe_get(snap, "pregame", "form_guard")
          or {})
    corner = (pp.get("_corner_form")
              or _safe_get(snap, "pregame", "corner_form")
              or {})

    out_reasons: list[str] = []
    why_not: list[str] = []
    market_confidence = int(_f(rec.get("confidence_score") or rec.get("score")) or 50)
    fragility = int(_f(rec.get("fragility") or pp.get("fragility")) or 50)

    requires_manual_odds = False
    watchlist = False
    recommended = current_market
    protected_alt: str | None = None
    why = ""

    # ── No inputs at all → degrade gracefully ──
    if not (current_market or pressure or fq or fg):
        out_reasons.append(RC_NO_INPUTS_AVAILABLE)
        return _wrap_output(
            recommended_market=current_market or MKT_WATCHLIST,
            protected_alternative=None,
            market_confidence=market_confidence,
            fragility=fragility,
            reason_codes=out_reasons,
            why_this_market="Sin señales suficientes para selección Moneyball; se mantiene la salida del motor base.",
            why_not_other_markets=why_not,
            requires_manual_odds=False,
            watchlist=False,
        )

    # ── League quality gate ──
    fq_class = fq.get("classification") if isinstance(fq, dict) else None
    if fq_class in {"EXOTIC_LEAGUE_WARNING", "LOW_MARKET_SUPPORT",
                     "LOW_DATA_QUALITY", "SKIPPED_LOW_RELEVANCE"}:
        out_reasons.append(RC_LEAGUE_QUALITY_LOW)
        why_not.append("Liga con calidad/soporte de mercado bajo: evita mercados agresivos.")
        watchlist = True
        fragility = min(95, fragility + 5)

    # ── Form guard fragility ──
    if isinstance(fg, dict) and (fg.get("fragile") or fg.get("verdict") == "FRAGILE"):
        out_reasons.append(RC_FORM_GUARD_FRAGILE)
        why_not.append("Form Guard marca forma frágil: rebajar confianza y evitar ML.")
        fragility = min(95, fragility + 5)
        market_confidence = max(5, market_confidence - 5)

    # ── Pressure-driven nudges ──
    if tier == HIGH_PRESSURE or flags.get("both_teams_high"):
        # Strong volatility → block UNDER 2.5 outright; require Under 3.5
        if _is_under_market(current_market):
            if "2.5" in (current_market or ""):
                protected_alt = MKT_UNDER_35
                out_reasons.append(RC_UNDER_3_5_PREFERRED_OVER_2_5)
                why_not.append("Presión combinada alta: Under 2.5 frágil, recomienda Under 3.5 como protección.")
                fragility = min(95, fragility + 8)
                market_confidence = max(5, market_confidence - 6)
        elif _is_over_market(current_market):
            out_reasons.append(RC_OVER_REQUIRES_EXPLICIT_SUPPORT)
            why_not.append("Over depende de cuotas + edge explícito; no se promueve por defecto.")
    elif tier == MODERATE_PRESSURE or flags.get("any_team_high"):
        if _is_under_market(current_market) and "2.5" in (current_market or ""):
            protected_alt = MKT_UNDER_35
            out_reasons.append(RC_UNDER_3_5_PREFERRED_OVER_2_5)
            why_not.append("Presión moderada: ofrece Under 3.5 como alternativa protegida.")
            fragility = min(95, fragility + 4)
    elif tier == LOW_PRESSURE and flags.get("both_teams_low"):
        # Both teams low pressure: keep current under-leaning market.
        if _is_under_market(current_market):
            out_reasons.append(RC_PROTECTED_MARKET_SELECTED)
            market_confidence = min(95, market_confidence + 3)
    elif tier == UNAVAILABLE:
        # No pressure signal → no override.
        pass

    # ── Moneyline vs Double Chance safety ──
    if _is_moneyline(current_market):
        # If we have any fragility hint, propose Double Chance as protected_alt.
        if (fragility >= 60
                or (isinstance(fg, dict) and fg.get("fragile"))
                or flags.get("any_team_high")):
            protected_alt = protected_alt or MKT_DOUBLE_CHANCE_1X
            out_reasons.append(RC_DOUBLE_CHANCE_SAFER_THAN_ML)
            why_not.append("Moneyline frágil ante señales de presión / forma; Double Chance ofrece protección.")
        if _market_lower(current_market) in ("moneyline", ""):
            out_reasons.append(RC_MONEYLINE_FRAGILE)

    # ── BTTS_NO / clean sheet protection ──
    if (
        (home_form.get("clean_sheet_rate") or 0) >= 0.40
        and (away_form.get("clean_sheet_rate") or 0) >= 0.40
        and _is_under_market(current_market)
    ):
        out_reasons.append(RC_BTTS_NO_PROTECTED)
        protected_alt = protected_alt or MKT_BTTS_NO

    # ── Corner trap (defensive only) ──
    if isinstance(corner, dict) and corner.get("data_quality") in {"thin", "insufficient"}:
        if _market_lower(current_market).startswith("corner"):
            out_reasons.append(RC_CORNERS_TRAP_BLOCKS_PICK)
            why_not.append("Datos de corners insuficientes; se desaconseja apostar al mercado de corners.")
            watchlist = True

    # ── Pattern memory hint (conservative) ──
    best_hist = (pm.get("best_historical_market") or pm.get("best_market")
                  if isinstance(pm, dict) else None)
    pm_sample = int(_f(pm.get("sample_size")) or 0) if isinstance(pm, dict) else 0
    pm_roi = _f(pm.get("historical_roi") or pm.get("roi")) if isinstance(pm, dict) else None
    if best_hist and pm_sample >= 20 and (pm_roi is not None and pm_roi > 0):
        if best_hist != current_market and not protected_alt:
            protected_alt = best_hist
            out_reasons.append(RC_PATTERN_MEMORY_PREFERRED_MARKET)

    # ── Manual odds / watchlist final escalation ──
    if current_market is None:
        recommended = MKT_WATCHLIST
        watchlist = True
        out_reasons.append(RC_WATCHLIST_INSUFFICIENT_SUPPORT)
    elif watchlist:
        # We don't replace the recommended market with watchlist by default;
        # the orchestrator decides if it should bucket into manual review.
        out_reasons.append(RC_WATCHLIST_INSUFFICIENT_SUPPORT)

    # If we still have no odds attached → manual review.
    if not _safe_get(snap, "pregame", "odds_digest", "available") and not pp.get("odds_snapshots"):
        requires_manual_odds = True
        out_reasons.append(RC_MANUAL_ODDS_REVIEW_REQUIRED)

    why = _explain_choice(
        recommended=recommended or MKT_WATCHLIST,
        protected_alt=protected_alt,
        tier=tier,
        flags=flags,
        market_confidence=market_confidence,
        fragility=fragility,
    )

    return _wrap_output(
        recommended_market=recommended or MKT_WATCHLIST,
        protected_alternative=protected_alt,
        market_confidence=int(max(0, min(100, market_confidence))),
        fragility=int(max(0, min(100, fragility))),
        reason_codes=out_reasons,
        why_this_market=why,
        why_not_other_markets=why_not,
        requires_manual_odds=requires_manual_odds,
        watchlist=watchlist,
    )


def _explain_choice(
    *,
    recommended: str,
    protected_alt: str | None,
    tier: str | None,
    flags: dict,
    market_confidence: int,
    fragility: int,
) -> str:
    parts: list[str] = []
    parts.append(f"Mercado base: {recommended}.")
    if protected_alt:
        parts.append(f"Protección recomendada: {protected_alt}.")
    if tier and tier != UNAVAILABLE:
        parts.append(f"Goal-pressure tier combinado: {tier}.")
    if flags.get("both_teams_low"):
        parts.append("Ambos equipos en perfil bajo de presión → favorece Unders protegidos.")
    if flags.get("any_team_high"):
        parts.append("Al menos un equipo en perfil alto de presión → exige protección extra.")
    parts.append(
        f"Confianza estimada: {market_confidence}/100, fragilidad: {fragility}/100."
    )
    return " ".join(parts)


def _wrap_output(
    *,
    recommended_market: str,
    protected_alternative: str | None,
    market_confidence: int,
    fragility: int,
    reason_codes: list[str],
    why_this_market: str,
    why_not_other_markets: list[str],
    requires_manual_odds: bool,
    watchlist: bool,
) -> dict:
    # De-dup reason codes preserving order.
    seen: set[str] = set()
    out_codes: list[str] = []
    for rc in reason_codes:
        if rc and rc not in seen:
            seen.add(rc)
            out_codes.append(rc)
    return {
        "market_selection": {
            "recommended_market":    recommended_market,
            "protected_alternative": protected_alternative,
            "market_confidence":     int(market_confidence),
            "fragility":             int(fragility),
            "reason_codes":          out_codes,
            "why_this_market":       why_this_market,
            "why_not_other_markets": list(why_not_other_markets),
            "requires_manual_odds":  bool(requires_manual_odds),
            "watchlist":             bool(watchlist),
            "engine_version":        "football_moneyball.market_selection.1",
        }
    }


__all__ = [
    # Market labels
    "MKT_MONEYLINE_HOME", "MKT_MONEYLINE_AWAY", "MKT_MONEYLINE_DRAW",
    "MKT_DOUBLE_CHANCE_1X", "MKT_DOUBLE_CHANCE_X2", "MKT_DOUBLE_CHANCE_12",
    "MKT_UNDER_25", "MKT_UNDER_35", "MKT_OVER_25",
    "MKT_BTTS_NO", "MKT_BTTS_YES",
    "MKT_CORNERS_UNDER", "MKT_CORNERS_OVER",
    "MKT_WATCHLIST", "MKT_MANUAL_ODDS",
    # Reason codes
    "RC_PROTECTED_MARKET_SELECTED",
    "RC_UNDER_3_5_PREFERRED_OVER_2_5",
    "RC_OVER_REQUIRES_EXPLICIT_SUPPORT",
    "RC_DOUBLE_CHANCE_SAFER_THAN_ML",
    "RC_MONEYLINE_FRAGILE",
    "RC_BTTS_NO_PROTECTED",
    "RC_CORNERS_TRAP_BLOCKS_PICK",
    "RC_FORM_GUARD_FRAGILE",
    "RC_LEAGUE_QUALITY_LOW",
    "RC_MANUAL_ODDS_REVIEW_REQUIRED",
    "RC_WATCHLIST_INSUFFICIENT_SUPPORT",
    "RC_NO_INPUTS_AVAILABLE",
    "RC_PATTERN_MEMORY_PREFERRED_MARKET",
    # API
    "select_football_market",
]
