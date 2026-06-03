"""MLB Market Selection Intelligence (Phase 13.1).

Final selection layer that picks the **most protected MLB market** given
all the upstream signals already attached to ``pick_payload``:

    * pressure_base                (Objetivo 2)
    * advanced_stats_snapshot       (Statcast adapter)
    * sabermetrics_audit / sabermetrics (Phase 9.6)
    * model_verification.discrepancies + ghost-edge flags (Phase 11)
    * pitcher_quality_score / fragility / script_survival
    * advanced_adjustments         (Phase 9)
    * recommendation.odds_range / market_lean

The module is **pure** (no IO) and **fail-soft**: missing inputs always
degrade gracefully to a watchlist / manual-odds bucket rather than
throwing.

Markets evaluated (Moneyball-aligned):
    - Moneyline
    - Run Line -1.5 / +1.5
    - Full Game Under / Over
    - F5 Under / Over
    - Team Total Over / Under
    - NRFI / YRFI (informational only — requires explicit first-inning data)
    - Watchlist / Manual Odds Review (terminal)

Returns canonical schema::

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

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Market labels
# ─────────────────────────────────────────────────────────────────────
MKT_MONEYLINE         = "Moneyline"
MKT_RUN_LINE_FAVORITE = "Run Line -1.5"
MKT_RUN_LINE_DOG      = "Run Line +1.5"
MKT_FULL_UNDER        = "Full Game Under"
MKT_FULL_OVER         = "Full Game Over"
MKT_F5_UNDER          = "F5 Under"
MKT_F5_OVER           = "F5 Over"
MKT_TEAM_TOTAL_UNDER  = "Team Total Under"
MKT_TEAM_TOTAL_OVER   = "Team Total Over"
MKT_NRFI              = "NRFI"
MKT_YRFI              = "YRFI"
MKT_WATCHLIST         = "Watchlist"
MKT_MANUAL_ODDS       = "Manual Odds Review"

# Reason codes (canonical, exported)
RC_PROTECTED_MARKET_SELECTED        = "PROTECTED_MARKET_SELECTED"
RC_F5_UNDER_PREFERRED_OVER_FULL     = "F5_UNDER_PREFERRED_OVER_FULL_GAME"
RC_RUN_LINE_NOT_SUPPORTED           = "RUN_LINE_NOT_SUPPORTED_BY_MARGIN"
RC_MONEYLINE_SAFER_THAN_RUN_LINE    = "MONEYLINE_SAFER_THAN_RUN_LINE"
RC_FULL_GAME_UNDER_FRAGILE          = "FULL_GAME_UNDER_FRAGILE"
RC_OVER_REQUIRES_ODDS_CONFIRMATION  = "OVER_REQUIRES_ODDS_CONFIRMATION"
RC_GHOST_EDGE_BLOCKED_PICK          = "GHOST_EDGE_BLOCKED_PICK"
RC_PRESSURE_BASE_CHANGED_MARKET     = "PRESSURE_BASE_CHANGED_MARKET"
RC_SABERMETRICS_CONFIRMED_EDGE      = "SABERMETRICS_CONFIRMED_EDGE"
RC_STATCAST_CONFIRMED_EDGE          = "STATCAST_CONFIRMED_EDGE"
RC_MANUAL_ODDS_REVIEW_REQUIRED      = "MANUAL_ODDS_REVIEW_REQUIRED"
RC_NO_INPUTS_AVAILABLE              = "MARKET_SELECTION_NO_INPUTS"
RC_TEAM_TOTAL_UNDER_PREFERRED       = "TEAM_TOTAL_UNDER_PREFERRED"
RC_BULLPEN_RISK_FAVORS_F5           = "BULLPEN_RISK_FAVORS_F5"

# Ghost-edge flags from upstream layers (Phase 11)
_GHOST_EDGE_FLAGS = {
    "GHOST_EDGE_UNDER_VS_L5_HIGH_SCORING",
    "GHOST_EDGE_OVER_VS_L5_LOW_SCORING",
    "GHOST_EDGE_F5_UNDER_VS_L5",
    "GHOST_EDGE_RISING_ON_BASE_VS_UNDER",
    "GHOST_EDGE_HARD_CONTACT_VS_UNDER",
    "GHOST_EDGE_TEAM_XWOBA_VS_UNDER",
    "ERA_UNDERSTATES_RISK",
    "ERA_OVERSTATES_RISK",
    "PITCHER_XWOBA_WARNING",
    "RECENT_RUN_TREND_CONTRADICTS_UNDER",
    "RECENT_RUN_TREND_CONTRADICTS_OVER",
}


# ─────────────────────────────────────────────────────────────────────
# Coercion helpers
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _is_under_market(market: str) -> bool:
    return "under" in market.lower() and "team total" not in market.lower()


def _is_over_market(market: str) -> bool:
    return "over" in market.lower() and "team total" not in market.lower()


# ─────────────────────────────────────────────────────────────────────
# Signal extractors (all fail-soft)
# ─────────────────────────────────────────────────────────────────────
def _extract_signals(payload: dict) -> dict:
    """Pull every relevant signal from the pick_payload."""
    rec = payload.get("recommendation") or {}
    current_market = rec.get("market") or (payload.get("chosen_market") or {}).get("market") or ""
    confidence = _f(rec.get("confidence_score")) or 0.0
    odds_range = rec.get("odds_range") or rec.get("odds")
    has_odds = bool(odds_range)

    # Pressure base (Objetivo 2)
    pb = payload.get("pressure_base") or {}
    pb_combined = pb.get("combined") or {}
    pb_tier = pb_combined.get("pressure_tier") or "UNAVAILABLE"
    pb_flags = pb_combined.get("flags") or {}

    # Advanced (Statcast / Phase 9)
    adv_audit = payload.get("advanced_adjustments") or {}
    adv_dq = adv_audit.get("data_quality") or "missing"
    adv_breakdown = adv_audit.get("raw_breakdown") or {}
    adv_reason_codes = adv_audit.get("reason_codes") or []

    # Sabermetrics (Phase 9.6)
    saber = payload.get("sabermetrics") or {}
    saber_avail = bool(saber.get("available"))
    saber_dq = saber.get("data_quality") or "missing"
    saber_edges = saber.get("match_edges") or {}
    saber_adj = saber.get("adjustments") or {}

    # Ghost edges (Phase 11 + L5/L15)
    verify = payload.get("model_verification") or {}
    discrepancies = verify.get("discrepancies") or []
    ghost_flags = [
        d.get("flag") for d in discrepancies
        if d.get("flag") in _GHOST_EDGE_FLAGS
    ]

    # Fragility (V5) + script survival
    frag = payload.get("fragility") or {}
    fragility_score = _f(frag.get("score")) or 0.0
    survival = _f((payload.get("script_survival") or {}).get("score")) or 0.0

    # Pitcher quality
    pitcher_quality = _f(payload.get("pitcher_quality_score")) or 0.0

    # Run line / margin projection (from v2 payload)
    v2 = payload.get("_mlb_script_v2") or {}
    projected_margin = _f(v2.get("marginProjection") or v2.get("projectedMargin"))
    cover_prob_rl    = _f(v2.get("runLineCoverProb") or v2.get("rl_cover_prob"))
    favorite_side    = v2.get("favoriteSide") or v2.get("favorite_side")

    # Bullpen risk (F6A)
    bullpen_risk = payload.get("bullpen_risk") or {}
    bullpen_risky = bool(bullpen_risk.get("risky") or bullpen_risk.get("high_risk"))

    return {
        "current_market":   current_market,
        "confidence":       confidence,
        "has_odds":         has_odds,
        "odds_range":       odds_range,
        "pb_tier":          pb_tier,
        "pb_flags":         pb_flags,
        "adv_dq":           adv_dq,
        "adv_breakdown":    adv_breakdown,
        "adv_reason_codes": adv_reason_codes,
        "saber_avail":      saber_avail,
        "saber_dq":         saber_dq,
        "saber_edges":      saber_edges,
        "saber_adj":        saber_adj,
        "ghost_flags":      ghost_flags,
        "fragility":        fragility_score,
        "survival":         survival,
        "pitcher_quality":  pitcher_quality,
        "projected_margin": projected_margin,
        "cover_prob_rl":    cover_prob_rl,
        "favorite_side":    favorite_side,
        "bullpen_risky":    bullpen_risky,
    }


# ─────────────────────────────────────────────────────────────────────
# Rule engine
# ─────────────────────────────────────────────────────────────────────
def select_protected_market(payload: dict | None) -> dict:
    """Pick the most protected market given all upstream signals."""
    if not isinstance(payload, dict):
        return _empty_selection([RC_NO_INPUTS_AVAILABLE])

    s = _extract_signals(payload)
    current = s["current_market"] or ""
    reason_codes: list[str] = []
    why_not: list[str] = []

    # ─── 0. No data — degrade to watchlist ───────────────────────────
    if not current and not s["pb_tier"] and s["adv_dq"] == "missing" \
            and not s["saber_avail"]:
        return _empty_selection([RC_NO_INPUTS_AVAILABLE])

    # ─── 1. Ghost-edge against the current pick → degrade ──────────
    # If the current side is Under and any UNDER-killing ghost edge fires,
    # we must NOT recommend Under strong.
    under_ghosts = [
        g for g in s["ghost_flags"]
        if g in {
            "GHOST_EDGE_UNDER_VS_L5_HIGH_SCORING",
            "GHOST_EDGE_HARD_CONTACT_VS_UNDER",
            "GHOST_EDGE_TEAM_XWOBA_VS_UNDER",
            "GHOST_EDGE_RISING_ON_BASE_VS_UNDER",
            "ERA_UNDERSTATES_RISK",
            "PITCHER_XWOBA_WARNING",
            "RECENT_RUN_TREND_CONTRADICTS_UNDER",
        }
    ]
    over_ghosts = [
        g for g in s["ghost_flags"]
        if g in {
            "GHOST_EDGE_OVER_VS_L5_LOW_SCORING",
            "ERA_OVERSTATES_RISK",
            "RECENT_RUN_TREND_CONTRADICTS_OVER",
        }
    ]
    if _is_under_market(current) and under_ghosts:
        reason_codes.append(RC_GHOST_EDGE_BLOCKED_PICK)
        why_not.append(
            f"{current} bloqueado por ghost-edges: {', '.join(under_ghosts)}"
        )
        # Try F5 Under as protected alternative if pitchers are strong
        if _pitchers_strong(s) and not s["bullpen_risky"]:
            return _selection(
                recommended=MKT_F5_UNDER,
                alternative=MKT_WATCHLIST,
                confidence=max(0.0, s["confidence"] - 8),
                fragility=s["fragility"] + 6,
                reasons=reason_codes + [RC_F5_UNDER_PREFERRED_OVER_FULL],
                why=("Cambio a F5 Under: los ghost-edges atacan al Full "
                     "Game pero los abridores tienen perfil sólido."),
                why_not=why_not,
            )
        # Otherwise → watchlist
        return _watchlist(reason_codes + [RC_GHOST_EDGE_BLOCKED_PICK],
                          why_not=why_not,
                          fragility=s["fragility"] + 10)
    if _is_over_market(current) and over_ghosts:
        reason_codes.append(RC_GHOST_EDGE_BLOCKED_PICK)
        why_not.append(
            f"{current} bloqueado por ghost-edges: {', '.join(over_ghosts)}"
        )
        return _watchlist(reason_codes, why_not=why_not,
                          fragility=s["fragility"] + 10)

    # ─── 2. High hidden pressure + Under pick → swap or degrade ────
    if _is_under_market(current) and s["pb_tier"] == "HIGH_PRESSURE":
        reason_codes.append(RC_PRESSURE_BASE_CHANGED_MARKET)
        if _pitchers_strong(s) and not s["bullpen_risky"]:
            # Move to F5 Under — first-5 protected from hit accumulation
            return _selection(
                recommended=MKT_F5_UNDER,
                alternative=MKT_WATCHLIST,
                confidence=max(0.0, s["confidence"] - 6),
                fragility=s["fragility"] + 5,
                reasons=reason_codes + [RC_F5_UNDER_PREFERRED_OVER_FULL,
                                         RC_FULL_GAME_UNDER_FRAGILE],
                why=("Presión oculta de hits es alta; F5 Under protege "
                     "del acumulado de carreras en bullpen."),
                why_not=["Full Game Under expuesto a HIGH_PRESSURE."],
            )
        return _watchlist(
            reason_codes + [RC_FULL_GAME_UNDER_FRAGILE],
            why_not=["Under con alta presión de hits — riesgo de bomba "
                     "de tiempo."],
            fragility=s["fragility"] + 8,
        )

    # ─── 3. Run Line not supported by margin ───────────────────────
    if "run line -1.5" in current.lower() or "rl -1.5" in current.lower():
        margin = s["projected_margin"]
        cover  = s["cover_prob_rl"]
        if (margin is not None and margin < 2.0) or (cover is not None and cover < 0.50):
            reason_codes.append(RC_RUN_LINE_NOT_SUPPORTED)
            why_not.append(
                f"Run Line -1.5: margen proyectado {margin} y cover prob "
                f"{cover} no soportan victoria por 2+."
            )
            return _selection(
                recommended=MKT_MONEYLINE,
                alternative=MKT_WATCHLIST,
                confidence=max(0.0, s["confidence"] - 5),
                fragility=s["fragility"] + 4,
                reasons=reason_codes + [RC_MONEYLINE_SAFER_THAN_RUN_LINE],
                why=("Moneyline es la opción protegida: favorito "
                     "razonable pero sin margen claro para -1.5."),
                why_not=why_not,
            )

    # ─── 4. Over pick without odds confirmation ─────────────────────
    if _is_over_market(current) and not s["has_odds"]:
        reason_codes.append(RC_OVER_REQUIRES_ODDS_CONFIRMATION)
        return _manual_odds_review(
            reason_codes + [RC_MANUAL_ODDS_REVIEW_REQUIRED],
            why=("Over solo se confirma con cuota razonable. Esperando "
                 "odds para validar valor."),
            why_not=["Sin odds no se puede medir edge en Over (no forzar)."],
            fragility=s["fragility"] + 2,
        )

    # ─── 5. Bullpen risky + Under → prefer F5 Under ──────────────────
    if _is_under_market(current) and s["bullpen_risky"] \
            and "f5" not in current.lower() and _pitchers_strong(s):
        return _selection(
            recommended=MKT_F5_UNDER,
            alternative=current,
            confidence=max(0.0, s["confidence"] - 3),
            fragility=s["fragility"] + 3,
            reasons=[RC_F5_UNDER_PREFERRED_OVER_FULL,
                     RC_BULLPEN_RISK_FAVORS_F5],
            why=("Abridores sólidos pero bullpen frágil; F5 Under "
                 "limita exposición a tarde-juego."),
            why_not=["Full Game Under expuesto a bullpen riesgoso."],
        )

    # ─── 6. Missing odds → manual review (any side) ─────────────────
    if current and not s["has_odds"]:
        reason_codes.append(RC_MANUAL_ODDS_REVIEW_REQUIRED)
        return _manual_odds_review(
            reason_codes,
            why=f"Mercado {current} sin odds — requiere revisión manual.",
            why_not=["Sin odds no se calcula edge moneyball."],
            fragility=s["fragility"],
        )

    # ─── 7. Sabermetrics + Statcast + pressure_base alignment ───────
    # If the three layers agree with the current direction → boost (small)
    aligned = _check_layer_alignment(current, s)
    if aligned:
        reason_codes.extend(aligned["codes"])
        return _selection(
            recommended=current,
            alternative=None,
            confidence=min(100.0, s["confidence"] + aligned["boost"]),
            fragility=max(0.0, s["fragility"] - 2),
            reasons=reason_codes + [RC_PROTECTED_MARKET_SELECTED],
            why=aligned["why"],
            why_not=why_not,
        )

    # ─── 8. Default: keep current market with no boost ─────────────
    if current:
        return _selection(
            recommended=current,
            alternative=None,
            confidence=s["confidence"],
            fragility=s["fragility"],
            reasons=reason_codes + [RC_PROTECTED_MARKET_SELECTED],
            why=(f"Mercado actual {current} mantiene el guion "
                 "esperado del partido."),
            why_not=why_not,
        )

    # ─── 9. No current market → watchlist ──────────────────────────
    return _watchlist(reason_codes + [RC_NO_INPUTS_AVAILABLE],
                      why_not=["Sin mercado vigente — requiere análisis."],
                      fragility=s["fragility"])


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _pitchers_strong(s: dict) -> bool:
    """Both starters look strong per sabermetrics/statcast."""
    saber = s.get("saber_adj") or {}
    pq_adj = _f(saber.get("pitcher_quality_adjustment")) or 0.0
    survival = s.get("survival") or 0.0
    if pq_adj >= 4:
        return True
    if survival >= 65 and s.get("pitcher_quality", 0) >= 65:
        return True
    return False


def _check_layer_alignment(current: str, s: dict) -> dict | None:
    """Check if Statcast + Sabermetrics + pressure_base all support
    the chosen side. Returns ``{codes, boost, why}`` or None.
    """
    is_under = _is_under_market(current)
    is_over  = _is_over_market(current)
    if not (is_under or is_over):
        return None
    saber = s.get("saber_adj") or {}
    saber_runs = _f(saber.get("total_runs_adjustment")) or 0.0
    pb_tier = s.get("pb_tier")
    has_adv = s.get("adv_dq") in ("strong", "partial")

    if is_under:
        saber_supports = saber_runs <= -2
        pb_supports = pb_tier in ("LOW_PRESSURE",)
        statcast_supports = (
            has_adv and "LOW_HARD_CONTACT_ENVIRONMENT" in (s.get("adv_reason_codes") or [])
        )
        agreement = sum([saber_supports, pb_supports, statcast_supports])
        if agreement >= 2:
            codes = []
            if saber_supports:
                codes.append(RC_SABERMETRICS_CONFIRMED_EDGE)
            if statcast_supports:
                codes.append(RC_STATCAST_CONFIRMED_EDGE)
            return {
                "codes": codes,
                "boost": 3.0 if agreement == 2 else 5.0,
                "why":  ("Sabermetría + Statcast + presión confirman "
                         "Under (ambiente controlado)."),
            }

    if is_over:
        saber_supports = saber_runs >= 2
        pb_supports = pb_tier in ("HIGH_PRESSURE", "MODERATE_PRESSURE")
        statcast_supports = has_adv and (
            "STATCAST_OVER_SUPPORT" in (s.get("adv_reason_codes") or [])
            or "POWER_BAT_STATCAST_SUPPORT" in (s.get("adv_reason_codes") or [])
        )
        agreement = sum([saber_supports, pb_supports, statcast_supports])
        # Over requires odds AND alignment — guardrail
        if agreement >= 2 and s.get("has_odds"):
            codes = []
            if saber_supports:
                codes.append(RC_SABERMETRICS_CONFIRMED_EDGE)
            if statcast_supports:
                codes.append(RC_STATCAST_CONFIRMED_EDGE)
            return {
                "codes": codes,
                "boost": 3.0 if agreement == 2 else 5.0,
                "why":  ("Sabermetría + Statcast + presión confirman "
                         "Over con odds disponibles."),
            }
    return None


# ─────────────────────────────────────────────────────────────────────
# Output builders
# ─────────────────────────────────────────────────────────────────────
def _selection(*, recommended: str, alternative: str | None,
                confidence: float, fragility: float,
                reasons: list[str], why: str,
                why_not: list[str]) -> dict:
    return {
        "market_selection": {
            "recommended_market":    recommended,
            "protected_alternative": alternative,
            "market_confidence":     int(round(max(0.0, min(100.0, confidence)))),
            "fragility":             int(round(max(0.0, min(100.0, fragility)))),
            "reason_codes":          list(dict.fromkeys(reasons)),  # dedupe
            "why_this_market":       why,
            "why_not_other_markets": why_not,
            "requires_manual_odds":  False,
            "watchlist":             False,
        }
    }


def _watchlist(reasons: list[str], *,
                why_not: list[str], fragility: float) -> dict:
    return {
        "market_selection": {
            "recommended_market":    MKT_WATCHLIST,
            "protected_alternative": None,
            "market_confidence":     0,
            "fragility":             int(round(max(0.0, min(100.0, fragility)))),
            "reason_codes":          list(dict.fromkeys(reasons)),
            "why_this_market":       ("Mercado movido a watchlist por "
                                       "señales de riesgo no confirmadas."),
            "why_not_other_markets": why_not,
            "requires_manual_odds":  False,
            "watchlist":             True,
        }
    }


def _manual_odds_review(reasons: list[str], *,
                         why: str, why_not: list[str],
                         fragility: float) -> dict:
    return {
        "market_selection": {
            "recommended_market":    MKT_MANUAL_ODDS,
            "protected_alternative": None,
            "market_confidence":     0,
            "fragility":             int(round(max(0.0, min(100.0, fragility)))),
            "reason_codes":          list(dict.fromkeys(reasons)),
            "why_this_market":       why,
            "why_not_other_markets": why_not,
            "requires_manual_odds":  True,
            "watchlist":             False,
        }
    }


def _empty_selection(reasons: list[str]) -> dict:
    return {
        "market_selection": {
            "recommended_market":    MKT_WATCHLIST,
            "protected_alternative": None,
            "market_confidence":     0,
            "fragility":             0,
            "reason_codes":          list(dict.fromkeys(reasons)),
            "why_this_market":       "Sin inputs suficientes — watchlist.",
            "why_not_other_markets": [],
            "requires_manual_odds":  False,
            "watchlist":             True,
        }
    }


__all__ = [
    # Market labels
    "MKT_MONEYLINE", "MKT_RUN_LINE_FAVORITE", "MKT_RUN_LINE_DOG",
    "MKT_FULL_UNDER", "MKT_FULL_OVER", "MKT_F5_UNDER", "MKT_F5_OVER",
    "MKT_TEAM_TOTAL_UNDER", "MKT_TEAM_TOTAL_OVER",
    "MKT_NRFI", "MKT_YRFI", "MKT_WATCHLIST", "MKT_MANUAL_ODDS",
    # Reason codes
    "RC_PROTECTED_MARKET_SELECTED", "RC_F5_UNDER_PREFERRED_OVER_FULL",
    "RC_RUN_LINE_NOT_SUPPORTED", "RC_MONEYLINE_SAFER_THAN_RUN_LINE",
    "RC_FULL_GAME_UNDER_FRAGILE", "RC_OVER_REQUIRES_ODDS_CONFIRMATION",
    "RC_GHOST_EDGE_BLOCKED_PICK", "RC_PRESSURE_BASE_CHANGED_MARKET",
    "RC_SABERMETRICS_CONFIRMED_EDGE", "RC_STATCAST_CONFIRMED_EDGE",
    "RC_MANUAL_ODDS_REVIEW_REQUIRED", "RC_BULLPEN_RISK_FAVORS_F5",
    # API
    "select_protected_market",
]
