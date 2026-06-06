"""Friendly Internationals — Draw-No-Bet (DNB) preference rule.

Phase 39 — Fix 2 (`football_intelligence`).

Why this exists
---------------
International friendlies have a very different competitive context than
qualifiers, league matches, or knockout fixtures. They show:

  * Mass squad rotation (often 10+ subs available).
  * Reduced competitive urgency for the favorite.
  * More tactical experimentation.
  * Higher draw frequency vs comparable strength gaps in league play.

The product rule we encode here is the explicit user heuristic:

  "International friendly + favorite (clear odds gap) + Moneyline only
   marginally better than Draw-No-Bet  ⇒  prefer DNB."

Two surfaces consume this module:

  1) ``human_live_interpreter`` (HARD-CODED rule):
       deterministic, predictable. Always active. Surfaces a
       ``recommended_action: SWITCH_TO_DNB`` plus a reason code that the
       UI renders as a yellow "preferir DNB" badge.

  2) ``football_intelligence_warehouse`` (DYNAMIC pattern):
       feeds the warehouse with one pattern row per match labelled
       ``friendly_intl_dnb_preferred``. Once we collect ≥
       ``DYNAMIC_PATTERN_MIN_SAMPLES`` resolved matches it becomes a
       proper *learned* pattern. The interpreter consults the learned
       hit-rate to amplify or dampen the hard rule.

The two layers cooperate via ``evaluate_friendly_dnb_preference()`` which
takes both the match context and the (optional) learned pattern stats
and returns a single, structured decision payload.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football.friendly_dnb")

# ─────────────────────────────────────────────────────────────────────
# Tunables — calibrated for international friendlies
# ─────────────────────────────────────────────────────────────────────
DYNAMIC_PATTERN_MIN_SAMPLES = 60   # warehouse "activate" threshold
PATTERN_NAME = "friendly_intl_dnb_preferred"

# Favorite is "clear" when ML decimal odds are ≤ this. (≈ -160 american)
FAVORITE_ODDS_MAX = 1.65

# We trigger DNB preference when ML's value-add over DNB is below this
# threshold. Concretely: (ml_implied_prob - dnb_implied_prob_breakeven) is
# small enough that paying the DNB premium is better risk-adjusted.
ML_PREMIUM_MAX_PCT = 0.10   # ML gives ≤10% extra implied prob → prefer DNB

# Minimum DNB odds we accept (otherwise the protection is too expensive).
DNB_MIN_ODDS = 1.18


# ─────────────────────────────────────────────────────────────────────
# Reason codes (consumed by the UI / interpreter)
# ─────────────────────────────────────────────────────────────────────
RC_FRIENDLY_INTL_DETECTED      = "FRIENDLY_INTL_DETECTED"
RC_FAVORITE_NO_MARGIN_EDGE     = "FAVORITE_NO_MARGIN_EDGE"
RC_DNB_PROTECTION_ATTRACTIVE   = "DNB_PROTECTION_ATTRACTIVE"
RC_PATTERN_LEARNED_AMPLIFIES   = "PATTERN_LEARNED_AMPLIFIES"
RC_PATTERN_LEARNED_DAMPENS     = "PATTERN_LEARNED_DAMPENS"
RC_PATTERN_NOT_YET_ACTIVE      = "PATTERN_NOT_YET_ACTIVE"

ALL_REASON_CODES = (
    RC_FRIENDLY_INTL_DETECTED,
    RC_FAVORITE_NO_MARGIN_EDGE,
    RC_DNB_PROTECTION_ATTRACTIVE,
    RC_PATTERN_LEARNED_AMPLIFIES,
    RC_PATTERN_LEARNED_DAMPENS,
    RC_PATTERN_NOT_YET_ACTIVE,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers — context detection
# ─────────────────────────────────────────────────────────────────────
_FRIENDLY_LEAGUE_HINTS = (
    "friendlies", "friendly", "amistos", "amistoso",
    "international friendly", "club friendly",
)

_NATIONAL_HINTS = (
    # Loose hints that a team is "international" (national team / FA).
    "national", "selección", "seleccion", "team usa",
    "fa ",  # e.g. "FA cup" — careful, but in friendly context it helps.
)


def _safe_str(v: Any) -> str:
    return str(v or "").strip().lower()


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
            return None
        return f
    except (TypeError, ValueError):
        return None


def is_international_friendly(match: dict | None) -> bool:
    """Heuristic detection of a true international friendly.

    Accepts a ``match`` doc shaped like the rest of the engine consumes:
    has either a flat ``league`` string OR a nested ``league: {name,
    type, season, country}``.

    Two positive signals are required (AND):
       (a) league name contains a "friendly/amistoso" hint, OR
           league type / round contain "friendly";
       (b) we are NOT in a domestic league (no league_id pointing at the
           usual leagues, OR explicit ``competition_kind == 'international'``).
    """
    if not isinstance(match, dict):
        return False

    league = match.get("league")
    if isinstance(league, dict):
        league_name = _safe_str(league.get("name"))
        league_type = _safe_str(league.get("type") or league.get("kind"))
        round_name  = _safe_str(league.get("round"))
    else:
        league_name = _safe_str(league)
        league_type = ""
        round_name  = _safe_str(match.get("round"))

    competition_kind = _safe_str(match.get("competition_kind") or match.get("competition_type"))
    is_friendly_text = any(h in league_name for h in _FRIENDLY_LEAGUE_HINTS) or \
                       any(h in round_name for h in _FRIENDLY_LEAGUE_HINTS) or \
                       "friendly" in league_type or "amistoso" in league_type
    if not is_friendly_text:
        return False

    # If the engine pre-labelled the match as international, accept
    # directly. Otherwise require absence of typical club-league markers.
    if competition_kind in ("international", "national_team", "selection"):
        return True

    # Reject "club friendly" or "pre-season" (often labelled this way).
    if "club" in league_name or "pre-season" in league_name or "pre season" in league_name:
        return False

    # If the league name explicitly mentions a national-team identifier
    # ("internazionali", "international friendly", "Copa de naciones"…)
    # treat as international.
    if "international" in league_name or "internacional" in league_name \
       or "nation" in league_name:
        return True

    # Default: treat unbranded friendlies as international when no club
    # context is present (best-effort, fail-soft).
    return True


def detect_favorite_side(
    *, odds_home: Optional[float], odds_away: Optional[float],
) -> Optional[str]:
    """Return 'home' / 'away' / None based on decimal odds."""
    if odds_home is None or odds_away is None:
        return None
    if odds_home <= FAVORITE_ODDS_MAX and odds_home < odds_away:
        return "home"
    if odds_away <= FAVORITE_ODDS_MAX and odds_away < odds_home:
        return "away"
    return None


def _implied_prob(odds: Optional[float]) -> Optional[float]:
    if odds is None or odds <= 1.01:
        return None
    return 1.0 / odds


# ─────────────────────────────────────────────────────────────────────
# Main rule entry point
# ─────────────────────────────────────────────────────────────────────
def evaluate_friendly_dnb_preference(
    *,
    match: dict | None,
    odds_home: Optional[float] = None,
    odds_draw: Optional[float] = None,
    odds_away: Optional[float] = None,
    odds_dnb_home: Optional[float] = None,
    odds_dnb_away: Optional[float] = None,
    learned_pattern: Optional[dict] = None,
) -> dict:
    """Hard-coded rule + learned-pattern integration.

    Returns a structured decision::

        {
          "applies":        bool,
          "favorite":       "home" | "away" | None,
          "ml_odds":        float | None,
          "dnb_odds":       float | None,
          "ml_premium_pct": float | None,   # ml_prob - dnb_breakeven_prob
          "reason_codes":   [str, ...],
          "summary":        str_es,
          "engine_version": "friendly_dnb.1",
          "pattern_active": bool,
          "pattern_hit_rate": float | None,
          "pattern_sample":  int | None,
        }

    ``applies=True`` is the signal the interpreter consumes to surface a
    "preferir DNB" recommendation. ``applies=False`` means: ignore.
    NEVER raises.
    """
    out = {
        "applies":          False,
        "favorite":         None,
        "ml_odds":          None,
        "dnb_odds":         None,
        "ml_premium_pct":   None,
        "reason_codes":     [],
        "summary":          "",
        "engine_version":   "friendly_dnb.1",
        "pattern_active":   False,
        "pattern_hit_rate": None,
        "pattern_sample":   None,
    }
    try:
        if not is_international_friendly(match):
            return out
        out["reason_codes"].append(RC_FRIENDLY_INTL_DETECTED)

        oh = _safe_float(odds_home)
        oa = _safe_float(odds_away)
        favorite = detect_favorite_side(odds_home=oh, odds_away=oa)
        out["favorite"] = favorite
        if favorite is None:
            # No clear favorite ⇒ the rule does not fire (no asymmetric edge to protect).
            return out

        ml_odds = oh if favorite == "home" else oa
        dnb_odds = (
            _safe_float(odds_dnb_home) if favorite == "home"
            else _safe_float(odds_dnb_away)
        )
        out["ml_odds"]  = ml_odds
        out["dnb_odds"] = dnb_odds

        # Without explicit DNB odds, we conservatively decline. The UI
        # can still show the *suggestion* but we don't fire ``applies``.
        if dnb_odds is None or dnb_odds < DNB_MIN_ODDS:
            return out

        # Compute the marginal extra implied probability ML gives over
        # DNB. If it's small enough → DNB is the better-value protection.
        ml_prob   = _implied_prob(ml_odds) or 0.0
        dnb_prob  = _implied_prob(dnb_odds) or 0.0
        # Use raw implied prob delta as the metric. (We could regress the
        # bookie margin out, but keeping it raw matches what the user
        # eyeballs on the bookie screen.)
        premium = ml_prob - dnb_prob
        out["ml_premium_pct"] = round(premium, 4)

        if premium > ML_PREMIUM_MAX_PCT:
            # ML adds significant value vs DNB ⇒ no protection trade.
            return out

        out["reason_codes"].append(RC_FAVORITE_NO_MARGIN_EDGE)
        out["reason_codes"].append(RC_DNB_PROTECTION_ATTRACTIVE)

        # ── Learned-pattern integration ─────────────────────────────
        pat = learned_pattern if isinstance(learned_pattern, dict) else {}
        sample = int(pat.get("sample_size") or pat.get("sample") or 0)
        hit_rate = _safe_float(pat.get("hit_rate"))
        out["pattern_sample"]   = sample
        out["pattern_hit_rate"] = hit_rate

        if sample >= DYNAMIC_PATTERN_MIN_SAMPLES and hit_rate is not None:
            out["pattern_active"] = True
            # Amplify when learned hit_rate ≥ 0.55, dampen below 0.45.
            if hit_rate >= 0.55:
                out["reason_codes"].append(RC_PATTERN_LEARNED_AMPLIFIES)
                out["applies"] = True
            elif hit_rate < 0.45:
                out["reason_codes"].append(RC_PATTERN_LEARNED_DAMPENS)
                out["applies"] = False
                return out
            else:
                # Neutral hit_rate: hard rule still fires.
                out["applies"] = True
        else:
            out["reason_codes"].append(RC_PATTERN_NOT_YET_ACTIVE)
            out["applies"] = True  # hard rule applies regardless

        out["summary"] = _build_summary(out, favorite)
        return out

    except Exception as exc:  # pragma: no cover — fail-soft guard
        log.debug("evaluate_friendly_dnb_preference failed: %s", exc)
        return out


def _build_summary(decision: dict, favorite: Optional[str]) -> str:
    side = "local" if favorite == "home" else "visitante" if favorite == "away" else "?"
    parts = [
        f"Amistoso internacional con favorito {side}",
    ]
    ml = decision.get("ml_odds")
    dnb = decision.get("dnb_odds")
    if ml is not None and dnb is not None:
        parts.append(f"ML {ml:.2f} vs DNB {dnb:.2f}")
    parts.append("priorizar DNB por rotación / volatilidad táctica")
    if decision.get("pattern_active"):
        rate = decision.get("pattern_hit_rate")
        sample = decision.get("pattern_sample")
        if rate is not None and sample is not None:
            parts.append(f"patrón validado ({rate:.0%} en {sample} muestras)")
    return ". ".join(parts) + "."


# ─────────────────────────────────────────────────────────────────────
# Warehouse-side helper — build a learning payload from a settled match
# ─────────────────────────────────────────────────────────────────────
def build_learning_record(
    *,
    match: dict | None,
    final_outcome: Optional[str],   # "home" / "draw" / "away"
    favorite: Optional[str],
    used_dnb: bool,
) -> Optional[dict]:
    """Translate a resolved international-friendly match into a record
    suitable for ``football_intelligence_warehouse.update_pattern_memory``.

    Hit semantics:
      * If the favorite *won*: any recommendation hits.
      * If draw: ML loses, DNB returns push (no degradation).
      * If favorite lost: both lose.

    The DNB recommendation *hits* whenever the favorite wins, and
    *pushes* on a draw (caller MUST pass ``outcome="push"`` so the
    warehouse void path applies and the sample is not penalised).
    """
    if not is_international_friendly(match):
        return None
    if final_outcome is None or favorite is None:
        return None

    fav_won  = (favorite == "home" and final_outcome == "home") or \
               (favorite == "away" and final_outcome == "away")
    is_draw  = (final_outcome == "draw")

    if used_dnb:
        if fav_won:
            outcome = "won"
        elif is_draw:
            outcome = "push"      # protected by DNB
        else:
            outcome = "lost"
    else:
        # Plain Moneyline pick on the favorite.
        outcome = "won" if fav_won else "lost"

    return {
        "pattern_name":   PATTERN_NAME,
        "market":         "DNB" if used_dnb else "Moneyline",
        "selection":      favorite,
        "outcome":        outcome,
    }


__all__ = [
    "DYNAMIC_PATTERN_MIN_SAMPLES",
    "PATTERN_NAME",
    "FAVORITE_ODDS_MAX",
    "ML_PREMIUM_MAX_PCT",
    "DNB_MIN_ODDS",
    "RC_FRIENDLY_INTL_DETECTED",
    "RC_FAVORITE_NO_MARGIN_EDGE",
    "RC_DNB_PROTECTION_ATTRACTIVE",
    "RC_PATTERN_LEARNED_AMPLIFIES",
    "RC_PATTERN_LEARNED_DAMPENS",
    "RC_PATTERN_NOT_YET_ACTIVE",
    "ALL_REASON_CODES",
    "is_international_friendly",
    "detect_favorite_side",
    "evaluate_friendly_dnb_preference",
    "build_learning_record",
]
