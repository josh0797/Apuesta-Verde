"""Football Pattern Memory — derive canonical pattern keys.

Pure module. Given a football match context (or a pregame snapshot) it
returns at most a handful of canonical pattern keys that describe the
structural shape of the match. Pattern keys are then looked up against
``football_pattern_memory`` (warehouse) to retrieve historical hit
rate / ROI / best market.

Key design rules:
  * Pattern keys are conservative — we err on the side of fewer matches
    rather than over-fitting.
  * No automatic pick forcing: pattern memory is a confidence adjuster /
    recommendation hint, never a market override.
  * Football-only signals; never copies MLB pattern semantics.
"""

from __future__ import annotations

from typing import Any

from .football_goal_pressure_profile import (
    HIGH_PRESSURE, MODERATE_PRESSURE, LOW_PRESSURE,
    RC_EARLY_GOAL_RISK,
    RC_LIVE_PRESSURE_ACCELERATION,
)


def _safe_get(d: Any, *path, default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _team_form(pp_or_match: dict, side: str) -> dict:
    """Pick the form-summary block from either a pregame snapshot or a
    raw match payload."""
    # pregame snapshot shape
    form = _safe_get(pp_or_match, "pregame", side, "form")
    if isinstance(form, dict):
        return form
    form = _safe_get(pp_or_match, side, "form")
    if isinstance(form, dict):
        return form
    # Raw match shape
    team_block = pp_or_match.get(f"{side}_team") or pp_or_match.get(side) or {}
    if not isinstance(team_block, dict):
        return {}
    ctx = team_block.get("context") if isinstance(team_block.get("context"), dict) else team_block
    if not isinstance(ctx, dict):
        return {}
    recent = ctx.get("recent_fixtures") if isinstance(ctx.get("recent_fixtures"), dict) else {}
    seasonal = ctx.get("seasonal_form") if isinstance(ctx.get("seasonal_form"), dict) else {}
    early = seasonal.get("early_goal_profile") if isinstance(seasonal.get("early_goal_profile"), dict) else {}
    return {
        "under_2_5_rate":   recent.get("under_2_5_rate"),
        "under_3_5_rate":   recent.get("under_3_5_rate"),
        "btts_rate":        recent.get("btts_rate"),
        "clean_sheet_rate": recent.get("clean_sheet_rate"),
        "goals_for_avg":    ctx.get("goals_for_avg"),
        "goals_against_avg": ctx.get("goals_against_avg"),
        "early_goal_pct":   early.get("early_goal_pct") or ctx.get("early_goal_pct"),
    }


def _pressure(pp_or_match: dict) -> dict:
    """Locate the goal_pressure_profile block."""
    p = _safe_get(pp_or_match, "goal_pressure_profile")
    if isinstance(p, dict):
        return p
    p = _safe_get(pp_or_match, "pregame", "goal_pressure_profile")
    if isinstance(p, dict):
        return p
    return {}


def _football_quality_tier(pp_or_match: dict) -> str | None:
    fq = _safe_get(pp_or_match, "pregame", "football_quality")
    if not isinstance(fq, dict):
        fq = pp_or_match.get("_football_quality")
    if not isinstance(fq, dict):
        return None
    return fq.get("tier") or fq.get("classification")


def _form_guard(pp_or_match: dict) -> dict:
    fg = _safe_get(pp_or_match, "pregame", "form_guard")
    if not isinstance(fg, dict):
        fg = pp_or_match.get("_form_guard")
    if not isinstance(fg, dict):
        return {}
    return fg


def derive_pattern_keys(pp_or_match: dict | None) -> list[str]:
    """Return the canonical pattern keys this match matches.

    Accepts either:
      * a pregame snapshot (output of build_full_intelligence_snapshot)
      * the raw football match dict
      * a pick_payload that already has goal_pressure_profile / context
    """
    if not isinstance(pp_or_match, dict):
        return []

    keys: list[str] = []

    pressure = _pressure(pp_or_match)
    combined = pressure.get("combined") if isinstance(pressure, dict) else {}
    tier = combined.get("pressure_tier") if isinstance(combined, dict) else None
    flags = combined.get("flags") if isinstance(combined, dict) else {}
    pressure_reasons = pressure.get("reason_codes") or []

    home_form = _team_form(pp_or_match, "home")
    away_form = _team_form(pp_or_match, "away")

    # 1. BOTH_TEAMS_LOW_PRESSURE_UNDER_PROFILE
    if (
        tier == LOW_PRESSURE
        and flags.get("both_teams_low")
        and (home_form.get("under_2_5_rate") or 0) >= 0.60
        and (away_form.get("under_2_5_rate") or 0) >= 0.60
    ):
        keys.append("BOTH_TEAMS_LOW_PRESSURE_UNDER_PROFILE")

    # 2. HIGH_PRESSURE_BOTH_SIDES
    if tier == HIGH_PRESSURE or flags.get("both_teams_high"):
        keys.append("HIGH_PRESSURE_BOTH_SIDES")

    # 3. UNDER_PROFILE_STRONG_BOTH (independent of pressure tier)
    if (
        (home_form.get("under_3_5_rate") or 0) >= 0.75
        and (away_form.get("under_3_5_rate") or 0) >= 0.75
    ):
        keys.append("UNDER_PROFILE_STRONG_BOTH")

    # 4. EARLY_GOAL_RISK_HIGH
    if flags.get("early_goal_risk_any") or RC_EARLY_GOAL_RISK in pressure_reasons:
        keys.append("EARLY_GOAL_RISK_HIGH")

    # 5. CLEAN_SHEET_BIAS_BOTH
    if (
        (home_form.get("clean_sheet_rate") or 0) >= 0.35
        and (away_form.get("clean_sheet_rate") or 0) >= 0.35
    ):
        keys.append("CLEAN_SHEET_BIAS_BOTH")

    # 6. BTTS_PROFILE_STRONG
    if (
        (home_form.get("btts_rate") or 0) >= 0.65
        and (away_form.get("btts_rate") or 0) >= 0.65
    ):
        keys.append("BTTS_PROFILE_STRONG")

    # 7. CORNERS_VOLATILE_TRAP
    corner_block = _safe_get(pp_or_match, "pregame", "corner_form")
    if not isinstance(corner_block, dict):
        corner_block = pp_or_match.get("_corner_form")
    if isinstance(corner_block, dict):
        trap_signals = pp_or_match.get("trap_signals")
        if (isinstance(trap_signals, list)
                and any("corner" in (str(t).lower()) for t in trap_signals)):
            keys.append("CORNERS_VOLATILE_TRAP")

    # 8. PROTECTED_UNDER_3_5_OVER_UNDER_2_5
    # When both teams have decent under_2_5 but the volatility profile is
    # NOT extreme, prefer Under 3.5 as protection (matches Pattern "Under 3.5
    # over Under 2.5" from the spec).
    if (
        tier in (LOW_PRESSURE, MODERATE_PRESSURE)
        and (home_form.get("under_3_5_rate") or 0) >= 0.70
        and (away_form.get("under_3_5_rate") or 0) >= 0.70
    ):
        keys.append("PROTECTED_UNDER_3_5_OVER_UNDER_2_5")

    # 9. FORM_GUARD_FRAGILE
    fg = _form_guard(pp_or_match)
    if fg.get("fragile") or (fg.get("verdict") in {"FRAGILE", "FORM_FRAGILE"}):
        keys.append("FORM_GUARD_FRAGILE")

    # 10. LEAGUE_LOW_QUALITY_WARNING
    tier_q = _football_quality_tier(pp_or_match)
    if tier_q in {"EXOTIC_LEAGUE_WARNING", "LOW_DATA_QUALITY",
                    "LOW_MARKET_SUPPORT", "SKIPPED_LOW_RELEVANCE"}:
        keys.append("LEAGUE_LOW_QUALITY_WARNING")

    return keys


__all__ = ["derive_pattern_keys"]
