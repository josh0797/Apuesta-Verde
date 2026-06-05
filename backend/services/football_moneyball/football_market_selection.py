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
MKT_OVER_05              = "Over 0.5"
MKT_OVER_15              = "Over 1.5"
MKT_OVER_25              = "Over 2.5"
MKT_OVER_35              = "Over 3.5"
MKT_TEAM_TOTAL_OVER      = "Team Total Over"
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

# Over Support reason codes (Phase 33 — P1)
RC_OVER_SUPPORT_CONFIRMED          = "OVER_SUPPORT_CONFIRMED"
RC_OVER_1_5_PROTECTED_SELECTED     = "OVER_1_5_PROTECTED_SELECTED"
RC_OVER_2_5_ALLOWED_LOW_FRAGILITY  = "OVER_2_5_ALLOWED_LOW_FRAGILITY"
RC_OVER_2_5_DOWNGRADED_TO_OVER_1_5 = "OVER_2_5_DOWNGRADED_TO_OVER_1_5"
RC_OVER_2_5_FRAGILE                = "OVER_2_5_FRAGILE"
RC_DC_NB_UNDER_CONFLICT            = "DC_NB_UNDER_CONFLICT"
RC_OVER_SUPPORT_WATCHLIST_ONLY     = "OVER_SUPPORT_WATCHLIST_ONLY"
RC_OVER_LINE_ALREADY_HIT           = "OVER_LINE_ALREADY_HIT"
RC_OVER_BLOCKED_BY_CONTROLLED      = "OVER_BLOCKED_BY_CONTROLLED_MATCH"
RC_OVER_BLOCKED_BY_INJURY          = "OVER_BLOCKED_BY_OFFENSIVE_INJURY"
RC_OVER_DEFENSE_INJURY_BOOST       = "OVER_SUPPORT_BOOSTED_BY_DEFENSIVE_INJURY"
RC_OVER_LIVE_CONFIRMED             = "LIVE_OVER_CONFIRMED_BY_PRESSURE_APPLIED"


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


# ════════════════════════════════════════════════════════════════════════════
# Over Support helpers (Phase 33 — P1)
# ════════════════════════════════════════════════════════════════════════════

# Over X.5 → minimum total goals that already make the line hit.
_OVER_LINE_HIT_THRESHOLDS = {
    "0.5": 1,
    "1.5": 2,
    "2.5": 3,
    "3.5": 4,
    "4.5": 5,
    "5.5": 6,
}


def _extract_total_goals(source: Any) -> int | None:
    """Best-effort extraction of the current total goals from a match doc
    or pregame/live snapshot. Returns None if no signal is available
    (pregame = 0 is also a valid return when score is explicitly 0-0)."""
    if not isinstance(source, dict):
        return None

    # 1) Direct live_stats / score.
    live = source.get("live_stats") if isinstance(source.get("live_stats"), dict) else {}
    score = live.get("score") if isinstance(live.get("score"), dict) else source.get("score")
    if isinstance(score, dict):
        h, a = score.get("home"), score.get("away")
        if h is not None and a is not None:
            try:
                return int(h) + int(a)
            except (TypeError, ValueError):
                pass
        label = score.get("label")
        if isinstance(label, str) and "-" in label:
            parts = label.split("-")
            try:
                return int(parts[0].strip()) + int(parts[1].strip())
            except (TypeError, ValueError):
                pass

    # 2) Flat fields.
    for hk, ak in (
        ("goals_home", "goals_away"),
        ("home_goals", "away_goals"),
        ("score_home", "score_away"),
    ):
        h = source.get(hk) if isinstance(source.get(hk), (int, float, str)) else live.get(hk)
        a = source.get(ak) if isinstance(source.get(ak), (int, float, str)) else live.get(ak)
        if h is not None and a is not None:
            try:
                return int(h) + int(a)
            except (TypeError, ValueError):
                continue

    return None


def is_total_line_already_hit(match_or_snapshot: Any, market_label: str | None) -> bool:
    """Pure: returns True when the requested Over X.5 market has already
    been satisfied by the current total goals on a *live* match.

    Pregame matches with no observed score (or 0-0 not explicitly set)
    return False because the market is still open.

    Examples:
        Over 1.5 with score 1-1 (total=2) → True (line already hit)
        Over 2.5 with score 1-1 (total=2) → False
        Over 3.5 with score 2-2 (total=4) → True

    Unknown markets / non-Over labels return False (fail-soft).
    """
    if not market_label:
        return False
    m = _market_lower(market_label)
    if not ("over" in m or "más de" in m or "mas de" in m):
        return False
    # Find the line embedded in the label.
    line_str: str | None = None
    for cand in _OVER_LINE_HIT_THRESHOLDS.keys():
        if cand in m:
            line_str = cand
            break
    if line_str is None:
        return False
    threshold = _OVER_LINE_HIT_THRESHOLDS[line_str]
    total = _extract_total_goals(match_or_snapshot)
    if total is None:
        return False
    return total >= threshold


def _extract_over_support(pp: dict, snap: dict) -> dict:
    """Locate the football_over_support payload regardless of where it
    was attached (pick, snapshot.pregame, snapshot.live)."""
    for src in (
        pp.get("football_over_support"),
        _safe_get(snap, "pregame", "football_over_support"),
        _safe_get(snap, "live", "football_over_support"),
        snap.get("football_over_support") if isinstance(snap, dict) else None,
    ):
        if isinstance(src, dict) and src.get("available"):
            return src
    # Fall back to ANY dict so consumers can still inspect reason_codes
    # without crashing.
    for src in (
        pp.get("football_over_support"),
        _safe_get(snap, "pregame", "football_over_support"),
    ):
        if isinstance(src, dict):
            return src
    return {}


def _extract_totals_model(pp: dict, snap: dict) -> dict:
    """Locate the football_totals_model payload (DC/NB)."""
    for src in (
        pp.get("football_totals_model"),
        _safe_get(snap, "pregame", "football_totals_model"),
        _safe_get(snap, "live", "football_totals_model"),
        snap.get("football_totals_model") if isinstance(snap, dict) else None,
    ):
        if isinstance(src, dict) and src.get("available"):
            return src
    for src in (
        pp.get("football_totals_model"),
        _safe_get(snap, "pregame", "football_totals_model"),
    ):
        if isinstance(src, dict):
            return src
    return {}


def _odds_available_for_over(pp: dict, snap: dict) -> bool:
    """Best-effort odds availability check for Over markets."""
    digest = _safe_get(snap, "pregame", "odds_digest")
    if isinstance(digest, dict) and digest.get("available"):
        return True
    if pp.get("odds_snapshots"):
        return True
    # Also accept inline odds on the pick payload.
    if isinstance(pp.get("recommendation"), dict):
        if pp["recommendation"].get("odds") or pp["recommendation"].get("odds_range"):
            return True
    return False


def _evaluate_over_support(
    *,
    pp: dict,
    snap: dict,
    current_market: str | None,
    market_confidence: int,
    fragility: int,
    out_reasons: list[str],
    why_not: list[str],
) -> dict:
    """Evaluate the Over Support layer.

    Returns a dict::

        {
            "applied":            bool,
            "recommended_market": str | None,  # overrides current_market
            "protected_alt":      str | None,
            "watchlist":          bool,
            "requires_manual":    bool,
            "confidence_delta":   int,
            "fragility_delta":    int,
        }

    NEVER raises. NEVER forces a pick by itself: the orchestrator decides
    how to combine this with editorial / quality / pressure layers.
    """
    over_support = _extract_over_support(pp, snap)
    totals_model = _extract_totals_model(pp, snap)
    result = {
        "applied":            False,
        "recommended_market": None,
        "protected_alt":      None,
        "watchlist":          False,
        "requires_manual":    False,
        "confidence_delta":   0,
        "fragility_delta":    0,
    }
    if not over_support or not over_support.get("available"):
        return result

    sup_15 = _f(over_support.get("over_1_5_support_score")) or 0.0
    sup_25 = _f(over_support.get("over_2_5_support_score")) or 0.0
    over_fragility = _f(over_support.get("fragility_score"))
    if over_fragility is None:
        over_fragility = float(fragility)
    rec_over_market = (over_support.get("recommended_over_market") or "").upper()
    reasons = set((over_support.get("reason_codes") or []))

    # DC/NB conflict signal.
    dc_nb_under_35 = _f(_safe_get(totals_model, "under_3_5", "dc_nb")) or 0.0
    dc_nb_conflict = dc_nb_under_35 >= 0.70

    # Live confirmation flag.
    live_confirmed = "LIVE_OVER_CONFIRMED_BY_PRESSURE" in reasons

    # Hard blocks.
    controlled = "CONTROLLED_MATCH_BLOCKS_OVER" in reasons
    top_scorer_out = "TOP_SCORER_OUT_WEAKENS_OVER" in reasons
    defense_injury = "INJURY_DEFENSE_WEAKENED_OVER_SUPPORT" in reasons

    # Lambda / offense signals (for Over 2.5 gate).
    lambda_total = _f(over_support.get("lambda_total"))
    if lambda_total is None:
        lambda_total = _f(totals_model.get("lambda_total"))
    early_goal = _f(over_support.get("early_goal_pressure_score")) or 0.0

    # Odds availability.
    odds_ok = _odds_available_for_over(pp, snap)

    # Source for "line already hit" check. Prefer match doc, then live snap.
    match_like = pp.get("_match") or pp or {}

    def _line_hit(market_label: str) -> bool:
        return (
            is_total_line_already_hit(match_like, market_label)
            or is_total_line_already_hit(_safe_get(snap, "live") or {}, market_label)
        )

    current_is_over_25 = "2.5" in (current_market or "").lower() and (
        "over" in (current_market or "").lower() or "más de" in (current_market or "").lower()
    )
    current_is_over_15 = "1.5" in (current_market or "").lower() and (
        "over" in (current_market or "").lower() or "más de" in (current_market or "").lower()
    )

    # ── Hard blockers ────────────────────────────────────────────────
    if controlled:
        out_reasons.append(RC_OVER_BLOCKED_BY_CONTROLLED)
        why_not.append("CONTROLLED_MATCH_BLOCKS_OVER: el contexto bloquea mercados Over.")
        if current_is_over_25 or current_is_over_15:
            # Strip the Over recommendation and bucket to watchlist.
            result.update({
                "applied":            True,
                "recommended_market": MKT_WATCHLIST,
                "protected_alt":      None,
                "watchlist":          True,
                "confidence_delta":   -8,
                "fragility_delta":    +8,
            })
        return result

    # ── Try Over 2.5 first (strict gates) ────────────────────────────
    over_25_supported = (
        sup_25 >= 80
        and over_fragility <= 45
        and (lambda_total is None or lambda_total >= 2.85)
        and early_goal >= 60
        and not dc_nb_conflict
        and not top_scorer_out
    )
    if over_25_supported and not _line_hit(MKT_OVER_25):
        # Final reality check: live confirmation OR strong pregame support.
        out_reasons.append(RC_OVER_SUPPORT_CONFIRMED)
        out_reasons.append(RC_OVER_2_5_ALLOWED_LOW_FRAGILITY)
        if defense_injury:
            out_reasons.append(RC_OVER_DEFENSE_INJURY_BOOST)
        if live_confirmed:
            out_reasons.append(RC_OVER_LIVE_CONFIRMED)
        if not odds_ok:
            result["requires_manual"] = True
            out_reasons.append(RC_MANUAL_ODDS_REVIEW_REQUIRED)
        result.update({
            "applied":            True,
            "recommended_market": MKT_OVER_25,
            "protected_alt":      MKT_OVER_15,
            "confidence_delta":   +5,
            "fragility_delta":    -3,
        })
        return result

    # ── Over 2.5 had support but failed a gate → trace + degrade ─────
    over_25_almost = (sup_25 >= 80) and not over_25_supported
    over_25_was_recommended = current_is_over_25
    will_downgrade_to_15 = False

    if over_25_almost or (over_25_was_recommended and (
        over_fragility > 45 or dc_nb_conflict or top_scorer_out
    )):
        if over_fragility > 45:
            out_reasons.append(RC_OVER_2_5_FRAGILE)
        out_reasons.append(RC_OVER_2_5_DOWNGRADED_TO_OVER_1_5)
        why_not.append("Over 2.5 con soporte pero gates fallidos: se intenta degradar a Over 1.5 o watchlist.")
        will_downgrade_to_15 = True

    if dc_nb_conflict:
        out_reasons.append(RC_DC_NB_UNDER_CONFLICT)
        why_not.append("DC/NB favorece Under 3.5 (≥0.70): se bloquea Over 2.5; Over 1.5 con gates estrictos.")

    if top_scorer_out:
        out_reasons.append(RC_OVER_BLOCKED_BY_INJURY)
        why_not.append("TOP_SCORER_OUT_WEAKENS_OVER: Over 2.5 bloqueado salvo soporte extremo.")
        will_downgrade_to_15 = True

    # ── Over 1.5 evaluation ──────────────────────────────────────────
    # Stricter thresholds when DC/NB conflicts or when degrading from
    # a blocked Over 2.5.
    strict_mode = dc_nb_conflict or top_scorer_out
    if strict_mode:
        sup_threshold = 75
        frag_threshold = 55
    else:
        sup_threshold = 70
        frag_threshold = 60

    # When we're explicitly downgrading from Over 2.5, only require the
    # numeric thresholds — `recommended_over_market` may legitimately be
    # OVER_2_5 even though we now want Over 1.5 as the protected fallback.
    needs_explicit_15_signal = not (will_downgrade_to_15 or dc_nb_conflict)

    over_15_passes_numeric = (
        sup_15 >= sup_threshold and over_fragility <= frag_threshold
    )
    over_15_has_explicit_signal = (
        rec_over_market == "OVER_1_5"
        or "OVER_1_5_PROTECTED" in reasons
        or defense_injury
    )
    over_15_supported = over_15_passes_numeric and (
        (not needs_explicit_15_signal) or over_15_has_explicit_signal
    )

    if over_15_supported and not _line_hit(MKT_OVER_15):
        out_reasons.append(RC_OVER_SUPPORT_CONFIRMED)
        out_reasons.append(RC_OVER_1_5_PROTECTED_SELECTED)
        if defense_injury:
            out_reasons.append(RC_OVER_DEFENSE_INJURY_BOOST)
        if live_confirmed:
            out_reasons.append(RC_OVER_LIVE_CONFIRMED)
        if not odds_ok:
            # Structural support is strong → keep in watchlist + manual.
            out_reasons.append(RC_OVER_SUPPORT_WATCHLIST_ONLY)
            out_reasons.append(RC_MANUAL_ODDS_REVIEW_REQUIRED)
            result.update({
                "applied":            True,
                "recommended_market": MKT_OVER_15,
                "protected_alt":      MKT_OVER_15,
                "watchlist":          True,
                "requires_manual":    True,
                "confidence_delta":   -3,
                "fragility_delta":    +2,
            })
            return result
        result.update({
            "applied":            True,
            "recommended_market": MKT_OVER_15,
            "protected_alt":      MKT_OVER_15,
            "confidence_delta":   +3,
            "fragility_delta":    -2,
        })
        return result

    # ── If we wanted to downgrade Over 2.5 but Over 1.5 didn't pass ──
    # we must still strip the Over 2.5 recommendation (otherwise the
    # caller keeps an unprotected aggressive market).
    if will_downgrade_to_15 and over_25_was_recommended:
        out_reasons.append(RC_OVER_SUPPORT_WATCHLIST_ONLY)
        result.update({
            "applied":            True,
            "recommended_market": MKT_WATCHLIST,
            "protected_alt":      MKT_OVER_15 if sup_15 >= 60 else None,
            "watchlist":          True,
            "confidence_delta":   -5,
            "fragility_delta":    +4,
        })
        return result

    # ── Line already hit → block (return marker) ─────────────────────
    for candidate in (MKT_OVER_25, MKT_OVER_15, MKT_OVER_05, MKT_OVER_35):
        if _line_hit(candidate) and (candidate.lower() in (current_market or "").lower()):
            out_reasons.append(RC_OVER_LINE_ALREADY_HIT)
            why_not.append(f"{candidate}: la línea ya está cumplida; no se recomienda como nueva entrada.")
            result.update({
                "applied":            True,
                "recommended_market": MKT_WATCHLIST,
                "watchlist":          True,
                "confidence_delta":   -5,
                "fragility_delta":    +5,
            })
            return result

    # ── Defensive injury can boost Over 1.5 even below main threshold ─
    if defense_injury and sup_15 >= 60 and over_fragility <= 65 and not _line_hit(MKT_OVER_15):
        out_reasons.append(RC_OVER_DEFENSE_INJURY_BOOST)
        out_reasons.append(RC_OVER_SUPPORT_WATCHLIST_ONLY)
        result.update({
            "applied":            True,
            "protected_alt":      MKT_OVER_15,
            "watchlist":          True,
            "confidence_delta":   0,
            "fragility_delta":    0,
        })
        return result

    return result


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

    # ── Over Support evaluation (Phase 33 — P1) ────────────────────────
    # NEVER forces a pick. It can:
    #   * promote Over 1.5 as protected alternative
    #   * promote Over 2.5 ONLY with strict gates (support+fragility+lambda)
    #   * downgrade Over 2.5 → Over 1.5 / watchlist when fragility blocks it
    #   * block Over recommendations when the line is already hit
    over_eval = _evaluate_over_support(
        pp=pp,
        snap=snap,
        current_market=current_market,
        market_confidence=market_confidence,
        fragility=fragility,
        out_reasons=out_reasons,
        why_not=why_not,
    )
    if over_eval.get("applied"):
        if over_eval.get("recommended_market"):
            recommended = over_eval["recommended_market"]
        if over_eval.get("protected_alt"):
            # Don't overwrite a stronger Under protection already set.
            if not (protected_alt and protected_alt in (MKT_UNDER_35, MKT_BTTS_NO)):
                protected_alt = over_eval["protected_alt"]
        if over_eval.get("watchlist"):
            watchlist = True
        if over_eval.get("requires_manual"):
            requires_manual_odds = True
        market_confidence = max(5, min(95, market_confidence + int(over_eval.get("confidence_delta") or 0)))
        fragility = max(5, min(95, fragility + int(over_eval.get("fragility_delta") or 0)))

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
    "MKT_UNDER_25", "MKT_UNDER_35",
    "MKT_OVER_05", "MKT_OVER_15", "MKT_OVER_25", "MKT_OVER_35",
    "MKT_TEAM_TOTAL_OVER",
    "MKT_BTTS_NO", "MKT_BTTS_YES",
    "MKT_CORNERS_UNDER", "MKT_CORNERS_OVER",
    "MKT_WATCHLIST", "MKT_MANUAL_ODDS",
    # Reason codes (legacy)
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
    # Reason codes (Phase 33 — Over Support)
    "RC_OVER_SUPPORT_CONFIRMED",
    "RC_OVER_1_5_PROTECTED_SELECTED",
    "RC_OVER_2_5_ALLOWED_LOW_FRAGILITY",
    "RC_OVER_2_5_DOWNGRADED_TO_OVER_1_5",
    "RC_OVER_2_5_FRAGILE",
    "RC_DC_NB_UNDER_CONFLICT",
    "RC_OVER_SUPPORT_WATCHLIST_ONLY",
    "RC_OVER_LINE_ALREADY_HIT",
    "RC_OVER_BLOCKED_BY_CONTROLLED",
    "RC_OVER_BLOCKED_BY_INJURY",
    "RC_OVER_DEFENSE_INJURY_BOOST",
    "RC_OVER_LIVE_CONFIRMED",
    # API
    "select_football_market",
    "is_total_line_already_hit",
]
