"""Football Structural Value Review — Phase F64.

POST-PROCESSOR for the market_guardrail discard path. When a pick is
about to be rejected for low/negative edge (SOFT_DISCARD_REVIEW), this
module runs a comprehensive STRUCTURAL analysis BEFORE confirming the
discard:

    1. Goal profile cross  (L5 vs L15 goal output)
    2. Corner profile cross (L5 vs L15 corner output)
    3. Under support score
    4. Over support score
    5. Protected market candidates (extracted from existing modules)

It then computes a ``max_structural_support`` score and decides::

    structural_support >= 75 + edge_pct >= 0    → VALUE_CANDIDATE
    structural_support >= 75 + edge_pct < 0     → WATCHLIST_ODDS_NEEDED
    structural_support in [60, 75)              → MOVE_TO_WATCHLIST
    structural_support < 60                     → NO_STRUCTURAL_VALUE

Design contract
---------------
* Pure orchestration — depends only on already-built modules.
* Fail-soft. Any sub-module failure → that signal contributes 0 and the
  others still run. ``available=False`` is only returned when nothing
  computed (truly empty match doc).
* No DB I/O, no LLM calls, no scrapers. All inputs come from the
  ``match`` dict + the pre-computed pick payload.
* Output is enrichment-only — the caller decides whether to mutate the
  pick / move it to a different bucket.

The spec calls this "Step 1 — Structural Match Analysis" + "Step 3 —
Edge validation". This module ONLY handles structural; the edge
validation lives in ``market_guardrail.evaluate_pick`` which already
computes ``edge_pct``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football_structural_value_review")

ENGINE_VERSION = "football_structural_value_review.v1"

# Decision states (mirrors the spec verbatim).
STATE_VALUE_CANDIDATE             = "VALUE_CANDIDATE"
STATE_WATCHLIST_ODDS_NEEDED       = "WATCHLIST_ODDS_NEEDED"
STATE_STRUCTURAL_VALUE_NOT_READY  = "STRUCTURAL_VALUE_BUT_ODDS_NOT_READY"
STATE_MOVE_TO_WATCHLIST           = "MOVE_TO_WATCHLIST"
STATE_NO_STRUCTURAL_VALUE         = "NO_STRUCTURAL_VALUE"

# Reason codes (spec).
RC_EDGE_MOVED_AFTER_STRUCTURAL    = "EDGE_CHECK_MOVED_AFTER_STRUCTURAL_ANALYSIS"
RC_STRUCTURAL_REQUIRED            = "STRUCTURAL_ANALYSIS_REQUIRED_BEFORE_DISCARD"
RC_NEGATIVE_EDGE_SOFT_DISCARD     = "NEGATIVE_EDGE_SOFT_DISCARD_REVIEW"
RC_WATCHLIST_ODDS_NEEDED          = "WATCHLIST_ODDS_NEEDED"
RC_GOAL_PROFILE_ANALYZED          = "GOAL_PROFILE_ANALYZED_BEFORE_DISCARD"
RC_CORNER_PROFILE_ANALYZED        = "CORNER_PROFILE_ANALYZED_BEFORE_DISCARD"
RC_XG_PROFILE_ANALYZED            = "XG_PROFILE_ANALYZED_BEFORE_DISCARD"
RC_SCORES24_REVIEW_REQUIRED       = "SCORES24_REVIEW_REQUIRED_BEFORE_FINAL_DISCARD"
RC_DISCARD_CONFIRMED              = "DISCARD_CONFIRMED_AFTER_FULL_STRUCTURAL_REVIEW"
RC_ALT_MARKET_FOUND               = "ALTERNATIVE_MARKET_FOUND_DESPITE_NEGATIVE_EDGE"

# Thresholds (kept identical to the spec).
STRUCTURAL_SUPPORT_VALUE        = 75
STRUCTURAL_SUPPORT_WATCHLIST    = 60


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _team_side(side: Any) -> dict:
    if not isinstance(side, dict):
        return {}
    ctx = side.get("context") if isinstance(side.get("context"), dict) else None
    return ctx or side


def _corner_profile_label(cross: dict) -> str:
    return str(cross.get("profile") or "UNAVAILABLE")


def _under_score(under: dict) -> int:
    if not isinstance(under, dict) or not under.get("available"):
        return 0
    return _safe_int(under.get("score")) or 0


def _over_score(over: dict) -> int:
    if not isinstance(over, dict) or not over.get("available"):
        return 0
    return _safe_int(over.get("score")) or 0


# ─────────────────────────────────────────────────────────────────────
# Sub-engine runners (each is fail-soft).
# ─────────────────────────────────────────────────────────────────────
def _run_corner_profile_cross(match: dict) -> dict:
    try:
        from services.football_corner_profile_cross import (
            compute_football_corner_profile_cross,
        )
        home_side = _team_side(match.get("home_team") or match.get("home"))
        away_side = _team_side(match.get("away_team") or match.get("away"))
        return compute_football_corner_profile_cross(
            home=home_side, away=away_side, scores24_payload=None,
        ) or {"available": False}
    except Exception as exc:  # noqa: BLE001
        log.debug("[F64] corner_profile_cross failed: %s", exc)
        return {"available": False, "_error": str(exc)}


def _run_team_profile_cross(match: dict) -> dict:
    try:
        from services.football_team_profile_cross import (
            compute_combined_football_profile_cross,
        )
        home_side = _team_side(match.get("home_team") or match.get("home"))
        away_side = _team_side(match.get("away_team") or match.get("away"))
        return compute_combined_football_profile_cross(
            home=home_side, away=away_side,
        ) or {"available": False}
    except Exception as exc:  # noqa: BLE001
        log.debug("[F64] team_profile_cross failed: %s", exc)
        return {"available": False, "_error": str(exc)}


def _run_under_support(match: dict) -> dict:
    try:
        from services.football_moneyball.football_under_support import (
            calculate_football_under_support,
        )
        res = calculate_football_under_support(match) or {}
        return res.get("football_under_support") or {"available": False}
    except Exception as exc:  # noqa: BLE001
        log.debug("[F64] under_support failed: %s", exc)
        return {"available": False, "_error": str(exc)}


def _run_over_support(match: dict) -> dict:
    try:
        from services.football_moneyball.football_over_support import (
            calculate_football_over_support,
        )
        res = calculate_football_over_support(match) or {}
        return res.get("football_over_support") or {"available": False}
    except Exception as exc:  # noqa: BLE001
        log.debug("[F64] over_support failed: %s", exc)
        return {"available": False, "_error": str(exc)}


# ─────────────────────────────────────────────────────────────────────
# Market candidate detection
# ─────────────────────────────────────────────────────────────────────
def _market_candidates_from_signals(
    corner_cross: dict,
    team_cross: dict,
    under_support: dict,
    over_support: dict,
) -> list[dict]:
    """Heuristic candidates list. Each entry has ``structural_support``
    (0-100), the market family, and a short list of contributing reason
    codes. Ordered by support desc.
    """
    candidates: list[dict] = []

    # Under family.
    us = _under_score(under_support)
    if us >= 60:
        candidates.append({
            "market":             "Under 2.5",
            "family":             "GOALS_TOTAL",
            "structural_support": us,
            "fragility":          50 - min(50, max(0, us - 50)),
            "reason_codes":       ["UNDER_SUPPORT_HIGH"],
        })
        candidates.append({
            "market":             "Under 3.5",
            "family":             "GOALS_TOTAL",
            "structural_support": min(100, us + 5),
            "fragility":          max(0, 40 - (us - 60)),
            "reason_codes":       ["UNDER_SUPPORT_HIGH"],
        })

    # Over family.
    os_ = _over_score(over_support)
    if os_ >= 60:
        candidates.append({
            "market":             "Over 1.5",
            "family":             "GOALS_TOTAL",
            "structural_support": min(100, os_ + 5),
            "fragility":          max(0, 40 - (os_ - 60)),
            "reason_codes":       ["OVER_SUPPORT_HIGH"],
        })

    # Corner family.
    if corner_cross.get("available"):
        profile = _corner_profile_label(corner_cross)
        supports = str(corner_cross.get("supports") or "NEUTRAL")
        if supports == "CORNERS_UNDER":
            candidates.append({
                "market":             "Total corners Under",
                "family":             "CORNERS",
                "structural_support": 80,
                "fragility":          22,
                "reason_codes":       [profile, "CORNERS_UNDER_PROFILE"],
            })
        elif supports == "CORNERS_OVER":
            candidates.append({
                "market":             "Total corners Over",
                "family":             "CORNERS",
                "structural_support": 78,
                "fragility":          24,
                "reason_codes":       [profile, "CORNERS_OVER_PROFILE"],
            })
        elif supports == "TEAM_CORNERS":
            candidates.append({
                "market":             "Team corners Over",
                "family":             "CORNERS",
                "structural_support": 72,
                "fragility":          30,
                "reason_codes":       [profile, "TEAM_CORNERS_PROFILE"],
            })

    # Team-strength based (1X2 / DC).
    if team_cross.get("available"):
        profile = str(team_cross.get("profile") or "")
        if profile in ("STRONG_HOME_OVERMATCH_CROSS", "STRONG_AWAY_OVERMATCH_CROSS"):
            candidates.append({
                "market":             "Doble Oportunidad" if "HOME" in profile else "Doble Oportunidad",
                "family":             "MATCH_RESULT",
                "structural_support": 72,
                "fragility":          28,
                "reason_codes":       [profile, "OVERMATCH_DETECTED"],
            })

    # Sort by support desc and dedupe by market label.
    seen: set[str] = set()
    out: list[dict] = []
    for c in sorted(candidates, key=lambda x: x["structural_support"], reverse=True):
        key = c["market"]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def compute_structural_value_review(
    match: dict | None,
    *,
    edge_pct: Optional[float] = None,
    discard_strength: Optional[str] = None,
) -> dict:
    """Run the structural review and return the audit + decision.

    Parameters
    ----------
    match
        The match dict. Should carry ``home_team`` / ``away_team`` with
        their ``context`` / ``recent_fixtures`` blocks.
    edge_pct
        Edge percentage already computed by the market guardrail (signed
        float; -18.8 means -18.8%). Used to gate WATCHLIST_ODDS_NEEDED
        vs VALUE_CANDIDATE.
    discard_strength
        Optional marker from market_guardrail (``HARD_DISCARD`` /
        ``SOFT_DISCARD_REVIEW``). Hard discard short-circuits to
        ``NO_STRUCTURAL_VALUE`` even when structural support is high —
        terminal discards are terminal.

    Returns
    -------
    dict — always non-None, never raises. Schema::

        {
          "available":                    bool,
          "engine_version":               str,
          "structural_analysis_completed": bool,
          "goal_profile_cross":           {...},
          "corner_profile_cross":         {...},
          "under_support":                {...},
          "over_support":                 {...},
          "market_candidates":            list[dict],
          "max_structural_support":       int,
          "edge_pct":                     float | None,
          "discard_strength":             str | None,
          "final_state":                  str,
          "decision":                     str,
          "rescued_market":               dict | None,
          "reason_codes":                 list[str],
          "narrative_es":                 str,
        }
    """
    out: dict[str, Any] = {
        "available":                     False,
        "engine_version":                ENGINE_VERSION,
        "structural_analysis_completed": False,
        "goal_profile_cross":            {"available": False},
        "corner_profile_cross":          {"available": False},
        "under_support":                 {"available": False},
        "over_support":                  {"available": False},
        "market_candidates":             [],
        "max_structural_support":        0,
        "edge_pct":                      edge_pct,
        "discard_strength":              discard_strength,
        "final_state":                   STATE_NO_STRUCTURAL_VALUE,
        "decision":                      "CONFIRM_DISCARD",
        "rescued_market":                None,
        "reason_codes":                  [RC_STRUCTURAL_REQUIRED],
        "narrative_es":                  "Análisis estructural no disponible.",
    }
    if not isinstance(match, dict):
        return out

    # ── Run all 4 sub-engines.
    corner_cross   = _run_corner_profile_cross(match)
    team_cross     = _run_team_profile_cross(match)
    under_support  = _run_under_support(match)
    over_support   = _run_over_support(match)

    out["corner_profile_cross"] = corner_cross
    out["goal_profile_cross"]   = team_cross
    out["under_support"]        = under_support
    out["over_support"]         = over_support

    any_available = (
        corner_cross.get("available")
        or team_cross.get("available")
        or under_support.get("available")
        or over_support.get("available")
    )
    out["available"]                     = bool(any_available)
    out["structural_analysis_completed"] = bool(any_available)

    if any_available:
        out["reason_codes"].extend([
            RC_GOAL_PROFILE_ANALYZED,
            RC_CORNER_PROFILE_ANALYZED,
            RC_XG_PROFILE_ANALYZED,
            RC_EDGE_MOVED_AFTER_STRUCTURAL,
        ])

    # ── Market candidates.
    candidates = _market_candidates_from_signals(
        corner_cross, team_cross, under_support, over_support,
    )
    out["market_candidates"] = candidates
    max_support = max((c["structural_support"] for c in candidates), default=0)
    out["max_structural_support"] = int(max_support)

    # ── Decision matrix.
    e = edge_pct if isinstance(edge_pct, (int, float)) else None

    if discard_strength == "HARD_DISCARD":
        out["final_state"] = STATE_NO_STRUCTURAL_VALUE
        out["decision"]    = "CONFIRM_DISCARD"
        out["reason_codes"].append(RC_DISCARD_CONFIRMED)
    elif max_support >= STRUCTURAL_SUPPORT_VALUE and (e is None or e >= 0):
        out["final_state"] = STATE_VALUE_CANDIDATE
        out["decision"]    = "VALUE_FOUND"
    elif max_support >= STRUCTURAL_SUPPORT_VALUE and e is not None and e < 0:
        out["final_state"] = STATE_WATCHLIST_ODDS_NEEDED
        out["decision"]    = "WATCHLIST_ODDS_NEEDED"
        out["reason_codes"].append(RC_WATCHLIST_ODDS_NEEDED)
        out["reason_codes"].append(RC_NEGATIVE_EDGE_SOFT_DISCARD)
        out["reason_codes"].append(RC_ALT_MARKET_FOUND)
        if candidates:
            out["rescued_market"] = candidates[0]
    elif STRUCTURAL_SUPPORT_WATCHLIST <= max_support < STRUCTURAL_SUPPORT_VALUE:
        out["final_state"] = STATE_MOVE_TO_WATCHLIST
        out["decision"]    = "MOVE_TO_WATCHLIST"
        out["reason_codes"].append(RC_SCORES24_REVIEW_REQUIRED)
    else:
        out["final_state"] = STATE_NO_STRUCTURAL_VALUE
        out["decision"]    = "CONFIRM_DISCARD"
        out["reason_codes"].append(RC_DISCARD_CONFIRMED)

    # ── Narrative (ES).
    if out["final_state"] == STATE_WATCHLIST_ODDS_NEEDED and candidates:
        c = candidates[0]
        out["narrative_es"] = (
            f"Perfil estructural apoya {c['market']} ({c['structural_support']}/100), "
            f"pero la cuota actual no da value (edge {e:+.1f}%). Vigilar si la línea/cuota mejora."
        )
    elif out["final_state"] == STATE_VALUE_CANDIDATE and candidates:
        c = candidates[0]
        out["narrative_es"] = (
            f"Perfil estructural apoya {c['market']} ({c['structural_support']}/100) "
            f"y la cuota tiene value (edge {e:+.1f}%)."
        )
    elif out["final_state"] == STATE_MOVE_TO_WATCHLIST:
        out["narrative_es"] = (
            "Soporte estructural moderado — revisar contexto externo antes de decidir."
        )
    else:
        out["narrative_es"] = (
            "Sin soporte estructural suficiente — descarte confirmado tras revisión completa."
        )

    return out


__all__ = [
    "ENGINE_VERSION",
    "compute_structural_value_review",
    "STATE_VALUE_CANDIDATE",
    "STATE_WATCHLIST_ODDS_NEEDED",
    "STATE_STRUCTURAL_VALUE_NOT_READY",
    "STATE_MOVE_TO_WATCHLIST",
    "STATE_NO_STRUCTURAL_VALUE",
    "RC_EDGE_MOVED_AFTER_STRUCTURAL",
    "RC_STRUCTURAL_REQUIRED",
    "RC_NEGATIVE_EDGE_SOFT_DISCARD",
    "RC_WATCHLIST_ODDS_NEEDED",
    "RC_GOAL_PROFILE_ANALYZED",
    "RC_CORNER_PROFILE_ANALYZED",
    "RC_XG_PROFILE_ANALYZED",
    "RC_SCORES24_REVIEW_REQUIRED",
    "RC_DISCARD_CONFIRMED",
    "RC_ALT_MARKET_FOUND",
    "STRUCTURAL_SUPPORT_VALUE",
    "STRUCTURAL_SUPPORT_WATCHLIST",
]
