"""Football Goal Pressure Profile (pure).

Detects whether a football match has structural **goal pressure**—
i.e. teams that systematically produce volatile, high-variance scoring
patterns vs. teams that operate as controlled / low-scoring profiles.
Mirrors the design of ``mlb_pressure_base.py`` but with football-only
signals.

Signals consumed (all optional, all fail-soft):
  Pre-match (from ``home_team.context`` / ``away_team.context`` produced
  by ``normalizer.normalize_team_context`` + ``normalize_recent_fixtures``):
    * ``goals_for_avg`` / ``goals_against_avg``        season aggregates
    * ``recent_fixtures`` / ``seasonal_form``:
        - ``goals_for_avg`` / ``goals_against_avg``    (L15)
        - ``under_2_5_rate`` / ``under_3_5_rate``
        - ``btts_rate``
        - ``clean_sheet_rate``
    * ``early_goal_profile`` / ``early_goal_pct``       (derived_early_goal)
  Live (from ``live_stats`` if available):
    * ``goals_home`` / ``goals_away``  current score
    * ``shots_total`` / ``shots_on_goal``
    * ``possession`` / ``corners``
    * ``minute``  (for risk weighting)

Design principles (NON-NEGOTIABLE):
  * Pure: no IO. Reads only the dict you give it.
  * Fail-soft: missing inputs degrade to ``UNAVAILABLE`` tier rather than
    raising.
  * Deterministic: thresholds are module-level constants exported for
    tests.
  * Explicable: each side and the ``combined`` block carries
    ``reason_codes`` (canonical strings) so the UI can render warnings.

Tiers:
  * ``HIGH_PRESSURE``     — high-scoring profile, low under-rate, both
                            sides volatile (raises fragility of UNDER picks).
  * ``MODERATE_PRESSURE`` — mixed signals leaning toward goals.
  * ``LOW_PRESSURE``      — controlled under-profile (favors protected
                            UNDER picks, esp. Under 3.5).
  * ``NEUTRAL_PRESSURE``  — neither extreme.
  * ``UNAVAILABLE``       — missing data.
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Tier constants
# ─────────────────────────────────────────────────────────────────────
HIGH_PRESSURE     = "HIGH_PRESSURE"
MODERATE_PRESSURE = "MODERATE_PRESSURE"
LOW_PRESSURE      = "LOW_PRESSURE"
NEUTRAL_PRESSURE  = "NEUTRAL_PRESSURE"
UNAVAILABLE       = "UNAVAILABLE"

# Per-team thresholds (football-specific, not copied from MLB).
# All rates are 0..1, all averages are goals per match.
HIGH_GF_AVG       = 1.70   # >=  goals scored per match
HIGH_BTTS_RATE    = 0.65
HIGH_UNDER_25_CAP = 0.40   # under_2_5_rate must be <= for HIGH

MOD_GF_AVG        = 1.30
MOD_BTTS_RATE     = 0.50
MOD_UNDER_25_CAP  = 0.55

LOW_GF_AVG_CAP    = 1.05   # <= 1.05 goals scored per match
LOW_GA_AVG_CAP    = 1.05   # <= 1.05 goals conceded per match
LOW_UNDER_25_MIN  = 0.60
LOW_CLEAN_SHEET_MIN = 0.35

# Early-goal risk gates
EARLY_GOAL_RISK_PCT      = 0.25   # >= 25% of goals in 0..15
EARLY_GOAL_PROTECT_PCT   = 0.10   # <= 10% → early-protect bias

# Live override deltas
LIVE_SHOT_PRESSURE_THRESHOLD = 6   # >=6 shots on goal vs L5 baseline

# Reason codes (canonical, exported)
RC_HIGH_PRESSURE_PROFILE        = "FOOTBALL_HIGH_PRESSURE_PROFILE"
RC_MODERATE_PRESSURE_PROFILE    = "FOOTBALL_MODERATE_PRESSURE_PROFILE"
RC_LOW_PRESSURE_CONTROLLED      = "FOOTBALL_LOW_PRESSURE_CONTROLLED"
RC_NEUTRAL_PRESSURE_SIGNAL      = "FOOTBALL_NEUTRAL_PRESSURE_SIGNAL"
RC_PRESSURE_DATA_MISSING        = "FOOTBALL_PRESSURE_DATA_MISSING"
RC_EARLY_GOAL_RISK              = "FOOTBALL_EARLY_GOAL_RISK"
RC_EARLY_GOAL_PROTECT           = "FOOTBALL_EARLY_GOAL_PROTECT"
RC_LIVE_PRESSURE_ACCELERATION   = "FOOTBALL_LIVE_PRESSURE_ACCELERATION"
RC_UNDER_PICK_HIGH_PRESSURE     = "UNDER_PICK_AT_HIGH_PRESSURE_RISK"
RC_UNDER_PICK_MODERATE_PRESSURE = "UNDER_PICK_AT_MODERATE_PRESSURE_RISK"
RC_PROTECTED_UNDER_3_5_BIAS     = "PROTECTED_UNDER_3_5_PREFERRED"

_TIER_SCORES = {
    HIGH_PRESSURE:     80,
    MODERATE_PRESSURE: 55,
    NEUTRAL_PRESSURE:  35,
    LOW_PRESSURE:      15,
    UNAVAILABLE:       0,
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _pick_first(d: dict, *keys) -> Any:
    """Return the first non-None / non-empty value among ``keys``."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return None


def _extract_team_signals(team_block: dict | None) -> dict:
    """Pull canonical signals out of a team block.

    Accepts the football match shape ``team['context']`` or the team
    block itself. Falls back gracefully when sub-fields are missing.
    """
    if not isinstance(team_block, dict):
        return {}

    ctx = team_block.get("context") if isinstance(team_block.get("context"), dict) else team_block
    if not isinstance(ctx, dict):
        return {}

    seasonal = ctx.get("seasonal_form") if isinstance(ctx.get("seasonal_form"), dict) else {}
    recent = ctx.get("recent_fixtures") if isinstance(ctx.get("recent_fixtures"), dict) else {}
    early = (seasonal.get("early_goal_profile")
             if isinstance(seasonal.get("early_goal_profile"), dict) else {})

    return {
        "goals_for_avg":      _f(_pick_first(ctx, "goals_for_avg")
                                  or _pick_first(recent, "goals_for_avg")
                                  or _pick_first(seasonal, "goals_for_avg")),
        "goals_against_avg":  _f(_pick_first(ctx, "goals_against_avg")
                                  or _pick_first(recent, "goals_against_avg")
                                  or _pick_first(seasonal, "goals_against_avg")),
        "under_2_5_rate":     _f(_pick_first(recent, "under_2_5_rate")
                                  or _pick_first(seasonal, "under_2_5_rate")),
        "under_3_5_rate":     _f(_pick_first(recent, "under_3_5_rate")
                                  or _pick_first(seasonal, "under_3_5_rate")),
        "btts_rate":          _f(_pick_first(recent, "btts_rate")
                                  or _pick_first(seasonal, "btts_rate")),
        "clean_sheet_rate":   _f(_pick_first(recent, "clean_sheet_rate")
                                  or _pick_first(seasonal, "clean_sheet_rate")),
        "early_goal_pct":     _f(_pick_first(early, "early_goal_pct")
                                  or _pick_first(ctx, "early_goal_pct")),
        "early_goal_against_pct": _f(_pick_first(early, "early_goal_against_pct")),
    }


def _classify_tier(s: dict) -> tuple[str, list[str]]:
    """Pure tier classifier based on the extracted signal dict."""
    if not isinstance(s, dict):
        return UNAVAILABLE, [RC_PRESSURE_DATA_MISSING]

    gf  = s.get("goals_for_avg")
    ga  = s.get("goals_against_avg")
    u25 = s.get("under_2_5_rate")
    btts = s.get("btts_rate")
    cs  = s.get("clean_sheet_rate")

    # If we have NO usable signal at all → UNAVAILABLE.
    if all(v is None for v in (gf, ga, u25, btts, cs)):
        return UNAVAILABLE, [RC_PRESSURE_DATA_MISSING]

    reasons: list[str] = []

    # HIGH: scoring profile
    if (
        (gf is not None and gf >= HIGH_GF_AVG)
        and (
            (btts is not None and btts >= HIGH_BTTS_RATE)
            or (u25 is not None and u25 <= HIGH_UNDER_25_CAP)
        )
    ):
        reasons.append(RC_HIGH_PRESSURE_PROFILE)
        return HIGH_PRESSURE, reasons

    # MODERATE
    if (
        (gf is not None and gf >= MOD_GF_AVG)
        and (
            (btts is not None and btts >= MOD_BTTS_RATE)
            or (u25 is not None and u25 <= MOD_UNDER_25_CAP)
        )
    ):
        reasons.append(RC_MODERATE_PRESSURE_PROFILE)
        return MODERATE_PRESSURE, reasons

    # LOW: controlled under-profile
    if (
        gf is not None and gf <= LOW_GF_AVG_CAP
        and ga is not None and ga <= LOW_GA_AVG_CAP
        and (
            (u25 is not None and u25 >= LOW_UNDER_25_MIN)
            or (cs is not None and cs >= LOW_CLEAN_SHEET_MIN)
        )
    ):
        reasons.append(RC_LOW_PRESSURE_CONTROLLED)
        return LOW_PRESSURE, reasons

    reasons.append(RC_NEUTRAL_PRESSURE_SIGNAL)
    return NEUTRAL_PRESSURE, reasons


# ─────────────────────────────────────────────────────────────────────
# Public — per team
# ─────────────────────────────────────────────────────────────────────
def calculate_team_goal_pressure(
    team_block: dict | None,
    *,
    live_shots_on_goal: int | float | None = None,
) -> dict:
    """Calculate goal-pressure profile for a single team.

    Args:
        team_block: dict shaped like ``match['home_team']`` (with
            ``context`` sub-dict). Accepts the ``context`` block
            directly as well.
        live_shots_on_goal: optional live count for live override.

    Returns canonical shape::

        {
          "available":     bool,
          "pressure_tier": str,
          "score":         int (0..100),
          "reasons":       [str, ...],
          "inputs":        {... echoed signals ...},
        }
    """
    signals = _extract_team_signals(team_block)
    tier, reasons = _classify_tier(signals)

    # Early-goal risk (additive flag)
    egp = signals.get("early_goal_pct")
    if egp is not None:
        if egp >= EARLY_GOAL_RISK_PCT:
            reasons.append(RC_EARLY_GOAL_RISK)
            # Bump down LOW → NEUTRAL when there's early-goal exposure.
            if tier == LOW_PRESSURE:
                tier = NEUTRAL_PRESSURE
        elif egp <= EARLY_GOAL_PROTECT_PCT:
            reasons.append(RC_EARLY_GOAL_PROTECT)

    # Live override
    live_h = _f(live_shots_on_goal)
    if live_h is not None and live_h >= LIVE_SHOT_PRESSURE_THRESHOLD:
        reasons.append(RC_LIVE_PRESSURE_ACCELERATION)
        if tier in (NEUTRAL_PRESSURE, LOW_PRESSURE):
            tier = MODERATE_PRESSURE
        elif tier == MODERATE_PRESSURE:
            tier = HIGH_PRESSURE

    return {
        "available":     tier != UNAVAILABLE,
        "pressure_tier": tier,
        "score":         _TIER_SCORES.get(tier, 0),
        "reasons":       reasons,
        "inputs": {
            **signals,
            "live_shots_on_goal": live_h,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Public — match-level context
# ─────────────────────────────────────────────────────────────────────
def calculate_match_goal_pressure_context(match: dict | None) -> dict:
    """Build the canonical ``goal_pressure_profile`` payload for a match.

    Accepts:
      * raw football match dict (``home_team``, ``away_team``,
        ``live_stats``), OR
      * a pre-shaped payload with explicit ``home``/``away`` sub-blocks.

    Always returns the canonical shape (never raises).
    """
    if not isinstance(match, dict):
        return _unavailable_match()

    home_block = match.get("home_team") or match.get("home") or {}
    away_block = match.get("away_team") or match.get("away") or {}

    # Live shots on goal (optional)
    live = match.get("live_stats") if isinstance(match.get("live_stats"), dict) else {}
    home_live = None
    away_live = None
    if live:
        hs = live.get("home_stats") if isinstance(live.get("home_stats"), dict) else {}
        as_ = live.get("away_stats") if isinstance(live.get("away_stats"), dict) else {}
        home_live = _f(_pick_first(hs, "shots_on_goal", "shots_on_target"))
        away_live = _f(_pick_first(as_, "shots_on_goal", "shots_on_target"))

    home = calculate_team_goal_pressure(home_block, live_shots_on_goal=home_live)
    away = calculate_team_goal_pressure(away_block, live_shots_on_goal=away_live)

    # Combined tier: most aggressive of the two unless both LOW.
    home_tier = home["pressure_tier"]
    away_tier = away["pressure_tier"]

    combined_tier = _combine_tiers(home_tier, away_tier)

    combined_reasons: list[str] = []
    if combined_tier == HIGH_PRESSURE:
        combined_reasons.append(RC_HIGH_PRESSURE_PROFILE)
    elif combined_tier == MODERATE_PRESSURE:
        combined_reasons.append(RC_MODERATE_PRESSURE_PROFILE)
    elif combined_tier == LOW_PRESSURE:
        combined_reasons.append(RC_LOW_PRESSURE_CONTROLLED)
        combined_reasons.append(RC_PROTECTED_UNDER_3_5_BIAS)
    elif combined_tier == NEUTRAL_PRESSURE:
        combined_reasons.append(RC_NEUTRAL_PRESSURE_SIGNAL)
    else:
        combined_reasons.append(RC_PRESSURE_DATA_MISSING)

    flags = {
        "any_team_high":     home_tier == HIGH_PRESSURE or away_tier == HIGH_PRESSURE,
        "both_teams_high":   home_tier == HIGH_PRESSURE and away_tier == HIGH_PRESSURE,
        "any_team_moderate_or_high": (
            home_tier in (HIGH_PRESSURE, MODERATE_PRESSURE)
            or away_tier in (HIGH_PRESSURE, MODERATE_PRESSURE)
        ),
        "both_teams_low":    home_tier == LOW_PRESSURE and away_tier == LOW_PRESSURE,
        "early_goal_risk_any": (
            RC_EARLY_GOAL_RISK in (home["reasons"] or [])
            or RC_EARLY_GOAL_RISK in (away["reasons"] or [])
        ),
        "live_acceleration": (
            RC_LIVE_PRESSURE_ACCELERATION in (home["reasons"] or [])
            or RC_LIVE_PRESSURE_ACCELERATION in (away["reasons"] or [])
        ),
    }

    # Union reason codes (de-duplicated, ordered).
    all_codes: list[str] = []
    for src in (home["reasons"], away["reasons"], combined_reasons):
        for rc in src:
            if rc not in all_codes:
                all_codes.append(rc)

    # Combined inputs (best-effort sums).
    gf_sum = _safe_sum(home["inputs"].get("goals_for_avg"),
                         away["inputs"].get("goals_for_avg"))
    ga_sum = _safe_sum(home["inputs"].get("goals_against_avg"),
                         away["inputs"].get("goals_against_avg"))
    u25_avg = _safe_mean(home["inputs"].get("under_2_5_rate"),
                            away["inputs"].get("under_2_5_rate"))
    u35_avg = _safe_mean(home["inputs"].get("under_3_5_rate"),
                            away["inputs"].get("under_3_5_rate"))

    return {
        "available": home["available"] or away["available"]
                      or combined_tier != UNAVAILABLE,
        "home":      home,
        "away":      away,
        "combined": {
            "pressure_tier": combined_tier,
            "score":         _TIER_SCORES.get(combined_tier, 0),
            "reasons":       combined_reasons,
            "flags":         flags,
            "inputs": {
                "goals_for_avg_combined":    gf_sum,
                "goals_against_avg_combined": ga_sum,
                "under_2_5_rate_avg":         u25_avg,
                "under_3_5_rate_avg":         u35_avg,
            },
        },
        "reason_codes": all_codes,
    }


def _combine_tiers(home_tier: str, away_tier: str) -> str:
    """Combine two per-team tiers into one match-level tier.

    Rules:
      * any HIGH → HIGH
      * both MODERATE or (HIGH + LOW) → MODERATE
      * any MODERATE → MODERATE (unless other is LOW)
      * both LOW → LOW
      * either UNAVAILABLE and the other not LOW → NEUTRAL
      * else NEUTRAL
    """
    s = {home_tier, away_tier}
    if HIGH_PRESSURE in s:
        if LOW_PRESSURE in s:
            return MODERATE_PRESSURE
        return HIGH_PRESSURE
    if home_tier == LOW_PRESSURE and away_tier == LOW_PRESSURE:
        return LOW_PRESSURE
    if MODERATE_PRESSURE in s:
        return MODERATE_PRESSURE
    if UNAVAILABLE in s and (LOW_PRESSURE not in s and HIGH_PRESSURE not in s):
        # If both unavailable → UNAVAILABLE; if one is, fall to NEUTRAL.
        if home_tier == UNAVAILABLE and away_tier == UNAVAILABLE:
            return UNAVAILABLE
        return NEUTRAL_PRESSURE
    return NEUTRAL_PRESSURE


def _safe_sum(a: Any, b: Any) -> float | None:
    a, b = _f(a), _f(b)
    if a is None and b is None:
        return None
    return round((a or 0.0) + (b or 0.0), 3)


def _safe_mean(a: Any, b: Any) -> float | None:
    a, b = _f(a), _f(b)
    if a is None and b is None:
        return None
    if a is None:
        return round(b, 3) if b is not None else None
    if b is None:
        return round(a, 3) if a is not None else None
    return round((a + b) / 2.0, 3)


def _unavailable_match() -> dict:
    base = {
        "available": False,
        "pressure_tier": UNAVAILABLE,
        "score": 0,
        "reasons": [RC_PRESSURE_DATA_MISSING],
        "inputs": {},
    }
    return {
        "available": False,
        "home":      dict(base),
        "away":      dict(base),
        "combined": {
            "pressure_tier": UNAVAILABLE,
            "score":         0,
            "reasons":       [RC_PRESSURE_DATA_MISSING],
            "flags":         {},
            "inputs":        {
                "goals_for_avg_combined":    None,
                "goals_against_avg_combined": None,
                "under_2_5_rate_avg":         None,
                "under_3_5_rate_avg":         None,
            },
        },
        "reason_codes": [RC_PRESSURE_DATA_MISSING],
    }


# ─────────────────────────────────────────────────────────────────────
# Downstream impact helper — used by football_market_selection
# ─────────────────────────────────────────────────────────────────────
def derive_goal_pressure_impact(
    pressure_context: dict | None,
    *,
    pick_market: str | None = None,
) -> dict:
    """Return capped adjustment hints for the orchestrator.

    Convention: positive ``fragility_delta`` increases fragility (worse
    for Under picks). ``confidence_delta`` is signed.

    Capped at ±10 (more conservative than MLB's ±10 since football has
    more variance).
    """
    market = (pick_market or "").lower()
    is_under = ("under" in market or "menos de" in market) and "team" not in market
    is_over = ("over" in market or "más de" in market or "mas de" in market) and "team" not in market

    out: dict[str, Any] = {
        "applied":          False,
        "fragility_delta":  0,
        "confidence_delta": 0,
        "reason_codes":     [],
    }
    if not isinstance(pressure_context, dict) or not pressure_context.get("available"):
        return out

    combined = pressure_context.get("combined") or {}
    tier = combined.get("pressure_tier")
    flags = combined.get("flags") or {}

    if is_under:
        if tier == HIGH_PRESSURE or flags.get("both_teams_high"):
            out["fragility_delta"]  = 8
            out["confidence_delta"] = -7
            out["reason_codes"].append(RC_UNDER_PICK_HIGH_PRESSURE)
            out["applied"] = True
        elif tier == MODERATE_PRESSURE or flags.get("any_team_high"):
            out["fragility_delta"]  = 5
            out["confidence_delta"] = -4
            out["reason_codes"].append(RC_UNDER_PICK_MODERATE_PRESSURE)
            out["applied"] = True
        elif tier == LOW_PRESSURE and flags.get("both_teams_low"):
            out["fragility_delta"]  = -3
            out["confidence_delta"] = 2
            out["reason_codes"].append(RC_LOW_PRESSURE_CONTROLLED)
            out["applied"] = True
        # Early-goal exposure: flag fragility regardless of market
        if flags.get("early_goal_risk_any"):
            out["fragility_delta"] = min(10, (out["fragility_delta"] or 0) + 3)
            out["reason_codes"].append(RC_EARLY_GOAL_RISK)
            out["applied"] = True
    elif is_over:
        if tier == HIGH_PRESSURE or flags.get("any_team_high"):
            out["confidence_delta"] = 3
            out["reason_codes"].append(RC_HIGH_PRESSURE_PROFILE)
            out["applied"] = True
        elif tier == LOW_PRESSURE and flags.get("both_teams_low"):
            out["confidence_delta"] = -4
            out["reason_codes"].append(RC_LOW_PRESSURE_CONTROLLED)
            out["applied"] = True

    # Clamp
    out["fragility_delta"]  = max(-10, min(10, out["fragility_delta"]))
    out["confidence_delta"] = max(-10, min(10, out["confidence_delta"]))
    return out


__all__ = [
    # Constants
    "HIGH_PRESSURE", "MODERATE_PRESSURE", "LOW_PRESSURE",
    "NEUTRAL_PRESSURE", "UNAVAILABLE",
    "HIGH_GF_AVG", "HIGH_BTTS_RATE", "HIGH_UNDER_25_CAP",
    "MOD_GF_AVG", "MOD_BTTS_RATE", "MOD_UNDER_25_CAP",
    "LOW_GF_AVG_CAP", "LOW_GA_AVG_CAP",
    "LOW_UNDER_25_MIN", "LOW_CLEAN_SHEET_MIN",
    "EARLY_GOAL_RISK_PCT", "EARLY_GOAL_PROTECT_PCT",
    # Reason codes
    "RC_HIGH_PRESSURE_PROFILE", "RC_MODERATE_PRESSURE_PROFILE",
    "RC_LOW_PRESSURE_CONTROLLED", "RC_NEUTRAL_PRESSURE_SIGNAL",
    "RC_PRESSURE_DATA_MISSING",
    "RC_EARLY_GOAL_RISK", "RC_EARLY_GOAL_PROTECT",
    "RC_LIVE_PRESSURE_ACCELERATION",
    "RC_UNDER_PICK_HIGH_PRESSURE", "RC_UNDER_PICK_MODERATE_PRESSURE",
    "RC_PROTECTED_UNDER_3_5_BIAS",
    # API
    "calculate_team_goal_pressure",
    "calculate_match_goal_pressure_context",
    "derive_goal_pressure_impact",
]
