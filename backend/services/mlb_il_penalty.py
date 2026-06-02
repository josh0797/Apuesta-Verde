"""
MLB Injured-List Penalty (GAP #1)
=================================

Today the orchestrator *detects* IL players (via
`mlb_stats_api.get_team_il_players`) but the engine needs to translate
that list into a contextual adjustment to ``expected_runs`` and
``confidence_score`` — and only for OFFENSIVE markets.

Real case that motivated this module:
  • Brandon Lowe (2B Pirates, batting #3) was on the 10-day IL.
  • The pick still rated PIT offence as if Lowe were healthy.
  • Result: the Over got way too much confidence (false positive).

Why this module was rewritten (2026-06-02)
------------------------------------------
The MLB Stats roster-injuries endpoint cumulatively returns:
  * 10-day IL
  * 15-day IL (pitchers)
  * 60-day IL
  * 7-day IL (minors)
  * Day-To-Day / Bereavement / Restricted
  * Minor-league injuries

Naively summing all of those produced 7-15+ "key bats" per side, which
saturated the previous ``MAX_KEY_BATS_PER_SIDE=4`` cap. Three different
games would then ALWAYS render the same numbers (HOME 4 · AWAY 4 · ER
-1.00 · Conf -10) — a bug the user reported on 2026-06-02 with three
unrelated screenshots showing the identical IL line.

Fix
---
1. **Filter by IL status**: only ``10-day IL`` (and equivalents that
   affect the active 25-man lineup) count toward the offensive
   penalty. 60-day IL, minor-league IL and Day-To-Day are surfaced as
   informational counters but DO NOT drive the penalty.
2. **Expose raw vs applied** so the UI can render
   "4 aplicados de 7 detectados" instead of pretending the cap is the
   real count.
3. **Contextual integration** is the caller's responsibility — see
   `market_is_offensive()` (defensive picks are not penalized; the
   orchestrator can even flip the narrative to "favorece Under").

Heuristic (unchanged math, fixed input set)
-------------------------------------------
  - "Key position" set: 1B, 2B, 3B, SS, LF, CF, RF, DH, C
    (pitchers excluded — losing a SP/RP is a separate signal handled by
    the bullpen/pitcher modules.)
  - Only ``ACTIVE_IL_STATUSES`` count toward the penalty.
  - Per-side cap: ``MAX_KEY_BATS_PER_SIDE = 4``.
  - Each missing active-IL key bat reduces ``expected_runs`` by **0.3**
    and shaves **5 points** off the confidence of offensive picks for
    that side.
  - Aggregate caps:
        max ER reduction       = 1.0 carreras
        max confidence penalty = 10 pts

Returns
-------
::

    {
        "er_adjustment":         -0.6,
        "confidence_penalty":    10,
        "home_key_il_count":     1,           # = applied count
        "away_key_il_count":     1,
        "home_key_il_count_raw": 7,           # before cap & status filter
        "away_key_il_count_raw": 5,
        "home_key_il_count_applied": 1,
        "away_key_il_count_applied": 1,
        "over_cap_excluded":     {"home": 6, "away": 4},  # filtered out by cap/status
        "cap_applied":           False,       # True when applied < raw
        "il_impact_label":       "MEDIO",     # ALTO / MEDIO / BAJO
        "home_missing_key":      ["Brandon Lowe (2B)"],
        "away_missing_key":      ["Carlos Correa (SS)"],
        "applies_to_markets":    ["over", "run_line", "team_total"],
        "_status_breakdown":     {            # diagnostic, never used downstream
            "active_10d": 5, "long_term_60d": 8, "day_to_day": 1, "minors": 4,
        },
    }
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

# Positions that materially move the offensive needle. Pitchers, when
# they appear on the roster injury list, are intentionally ignored here
# because the pitching analytics module already accounts for them.
KEY_POSITIONS: set[str] = {
    "1B", "2B", "3B", "SS",
    "LF", "CF", "RF",
    "DH", "C",
    "IF",   # generic infielder
    "OF",   # generic outfielder
}

# Pitcher positions — recorded separately for the pitcher_depth signal.
PITCHER_POSITIONS: set[str] = {"P", "SP", "RP"}

# Markets considered "offensive" for the purposes of confidence penalty.
OFFENSIVE_MARKETS = ("over", "run_line", "team_total", "f5_over", "nrfi_no")

MAX_KEY_BATS_PER_SIDE  = 4
MAX_ER_REDUCTION       = 1.0
MAX_CONFIDENCE_PENALTY = 10
ER_PER_KEY_BAT         = 0.3
CONF_PER_KEY_BAT       = 5

# ── IL status taxonomy (per MLB Stats API "status.description") ─────────────
# An "active" IL = the player is on the 25-/26-man IL slot, i.e. NOT
# available for tonight's game. This is what actually moves the lineup.
#
# The endpoint returns free-text strings like:
#   "On the 10-day Injured List"
#   "On the 15-day Injured List" (pitchers)
#   "On the 60-day Injured List"
#   "Day-To-Day"
#   "Bereavement List"
#   "Restricted List"
#   "On the 7-day Injured List" (minors)
#
# We classify them so the UI/audit trail can show the breakdown without
# corrupting the offensive penalty.
_ACTIVE_IL_PATTERNS = (
    re.compile(r"10[- ]?day\s+injured\s+list", re.I),
    re.compile(r"15[- ]?day\s+injured\s+list", re.I),
    re.compile(r"\bday[- ]to[- ]day\b",          re.I),
)
_LONG_TERM_PATTERNS = (
    re.compile(r"60[- ]?day\s+injured\s+list",   re.I),
    re.compile(r"restricted\s+list",             re.I),
)
_MINORS_PATTERNS = (
    re.compile(r"7[- ]?day\s+injured\s+list",    re.I),
    re.compile(r"\bminor[- ]?league",            re.I),
)


def _classify_status(status_raw: Optional[str]) -> str:
    """Return ``'active_10d' | 'long_term_60d' | 'day_to_day' | 'minors' | 'unknown'``.

    Only ``active_10d`` and ``day_to_day`` reach the penalty in this
    refactor — Day-To-Day is included because a player listed as DTD on
    game-day is generally OUT of the lineup as well.
    """
    if not status_raw:
        return "unknown"
    s = str(status_raw)
    for pat in _ACTIVE_IL_PATTERNS:
        if pat.search(s):
            return "day_to_day" if "day-to-day" in s.lower() or "day to day" in s.lower() else "active_10d"
    for pat in _LONG_TERM_PATTERNS:
        if pat.search(s):
            return "long_term_60d"
    for pat in _MINORS_PATTERNS:
        if pat.search(s):
            return "minors"
    return "unknown"


# Statuses that actually remove the player from tonight's 26-man.
_PENALTY_ELIGIBLE_STATUSES = {"active_10d", "day_to_day"}


def _short_pos(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return str(raw).strip().upper()


def _is_key_position(pos: Optional[str]) -> bool:
    return _short_pos(pos) in KEY_POSITIONS


def _is_pitcher_position(pos: Optional[str]) -> bool:
    return _short_pos(pos) in PITCHER_POSITIONS


def _name_with_pos(p: dict) -> str:
    nm = (p.get("name") or "?").strip()
    pos = _short_pos(p.get("position"))
    return f"{nm} ({pos})" if pos else nm


_POSITION_PRIORITY = {
    "C":  1, "SS": 1, "CF": 1,
    "2B": 2, "3B": 2,
    "1B": 3, "DH": 3, "LF": 3, "RF": 3,
    "OF": 4, "IF": 4,
}


def _rank_by_position_priority(p: dict) -> tuple[int, str]:
    pos = _short_pos(p.get("position"))
    return (_POSITION_PRIORITY.get(pos, 9), (p.get("name") or ""))


def _split_by_status(il_list: list[dict]) -> dict[str, list[dict]]:
    """Group IL players by classified status."""
    out: dict[str, list[dict]] = {
        "active_10d":    [],
        "day_to_day":    [],
        "long_term_60d": [],
        "minors":        [],
        "unknown":       [],
    }
    for p in il_list:
        cat = _classify_status(p.get("status"))
        out.setdefault(cat, []).append(p)
    return out


def apply_il_penalty(scoring_ctx: dict) -> dict:
    """Compute the IL-driven adjustment to expected_runs + confidence.

    Reads ``home_il_players`` / ``away_il_players`` (list of dicts
    produced by ``get_team_il_players``) from ``scoring_ctx``. Returns
    the canonical payload described in this module's docstring. Never
    raises.
    """
    try:
        home_il = scoring_ctx.get("home_il_players") or []
        away_il = scoring_ctx.get("away_il_players") or []

        def _select_key(il_list: list) -> tuple[list, int, dict]:
            """Return (kept_after_cap, excluded_count, status_breakdown).

            Pipeline:
                1) Split by IL status type.
                2) Keep ONLY ``_PENALTY_ELIGIBLE_STATUSES`` (active 10-day
                   + Day-To-Day). 60-day IL and minor-league IL are
                   counted in the breakdown but never penalized.
                3) Keep only KEY_POSITIONS (filters pitchers).
                4) Sort by position priority (spine first).
                5) Cap at ``MAX_KEY_BATS_PER_SIDE``.
            """
            breakdown = _split_by_status(il_list)
            penalty_pool: list[dict] = []
            for cat in _PENALTY_ELIGIBLE_STATUSES:
                penalty_pool.extend(breakdown.get(cat, []))
            key_only = [p for p in penalty_pool if _is_key_position(p.get("position"))]
            key_only.sort(key=_rank_by_position_priority)
            kept = key_only[:MAX_KEY_BATS_PER_SIDE]
            excluded = max(0, len(key_only) - len(kept))
            counts = {k: len(v) for k, v in breakdown.items()}
            counts["active_eligible_key"] = len(key_only)
            return kept, excluded, counts

        home_kept, home_excl, home_brk = _select_key(home_il)
        away_kept, away_excl, away_brk = _select_key(away_il)
        home_key_missing = [_name_with_pos(p) for p in home_kept]
        away_key_missing = [_name_with_pos(p) for p in away_kept]
        home_key_il = len(home_key_missing)
        away_key_il = len(away_key_missing)
        total_key   = home_key_il + away_key_il

        # Raw counts (all key positions including those that didn't pass
        # the active-IL filter). Exposes the "detected vs applied" gap.
        home_raw_key = sum(
            1 for p in home_il if _is_key_position(p.get("position"))
        )
        away_raw_key = sum(
            1 for p in away_il if _is_key_position(p.get("position"))
        )

        # Pitcher IL counters — feed pitcher_depth signal, NOT this one.
        home_pitcher_il = sum(
            1 for p in home_il
            if _is_pitcher_position(p.get("position"))
            and _classify_status(p.get("status")) in _PENALTY_ELIGIBLE_STATUSES
        )
        away_pitcher_il = sum(
            1 for p in away_il
            if _is_pitcher_position(p.get("position"))
            and _classify_status(p.get("status")) in _PENALTY_ELIGIBLE_STATUSES
        )

        er_adjustment      = -round(min(MAX_ER_REDUCTION, total_key * ER_PER_KEY_BAT), 2)
        confidence_penalty = int(min(MAX_CONFIDENCE_PENALTY, total_key * CONF_PER_KEY_BAT))

        cap_applied = (home_raw_key > home_key_il) or (away_raw_key > away_key_il)

        if total_key >= 3:
            label = "ALTO"
        elif total_key >= 1:
            label = "MEDIO"
        else:
            label = "BAJO"

        return {
            "er_adjustment":         er_adjustment,
            "confidence_penalty":    confidence_penalty,
            # Applied counts (used for the actual math). Aliases for
            # backwards compat with existing UI/orchestrator code.
            "home_key_il_count":         home_key_il,
            "away_key_il_count":         away_key_il,
            "home_key_il_count_applied": home_key_il,
            "away_key_il_count_applied": away_key_il,
            # Raw counts (Patrón D: separar raw vs applied).
            "home_key_il_count_raw":     home_raw_key,
            "away_key_il_count_raw":     away_raw_key,
            "cap_applied":               cap_applied,
            "il_impact_label":           label,
            "home_missing_key":          home_key_missing,
            "away_missing_key":          away_key_missing,
            "applies_to_markets":        list(OFFENSIVE_MARKETS),
            "over_cap_excluded":         {"home": home_excl, "away": away_excl},
            # Pitcher IL fed to pitcher_depth signal (NOT to this penalty).
            "home_pitcher_il_count":     home_pitcher_il,
            "away_pitcher_il_count":     away_pitcher_il,
            "_status_breakdown":         {
                "home": home_brk,
                "away": away_brk,
            },
        }
    except Exception as exc:  # pragma: no cover — fail-soft by design
        log.debug("apply_il_penalty failed (fail-soft): %s", exc)
        return {
            "er_adjustment":             0.0,
            "confidence_penalty":        0,
            "home_key_il_count":         0,
            "away_key_il_count":         0,
            "home_key_il_count_applied": 0,
            "away_key_il_count_applied": 0,
            "home_key_il_count_raw":     0,
            "away_key_il_count_raw":     0,
            "cap_applied":               False,
            "il_impact_label":           "BAJO",
            "home_missing_key":          [],
            "away_missing_key":          [],
            "applies_to_markets":        list(OFFENSIVE_MARKETS),
            "over_cap_excluded":         {"home": 0, "away": 0},
            "home_pitcher_il_count":     0,
            "away_pitcher_il_count":     0,
            "_error":                    str(exc),
        }


def market_is_offensive(market_label: Optional[str]) -> bool:
    """Return True when the chosen market is offensive (Over / RL / TT).

    Used by the orchestrator to decide if ``confidence_penalty`` should
    apply. Defensive picks (Under, F5 Under, NRFI Yes) are NOT penalized
    when bats are missing — fewer bats = fewer runs = Under-friendly.
    """
    if not market_label:
        return False
    s = str(market_label).lower()
    if "under" in s or "nrfi yes" in s:
        return False
    return any(tag in s for tag in ("over", "run line", "team total", "f5 over"))


def market_is_defensive(market_label: Optional[str]) -> bool:
    """Return True when the chosen market is Under-like (Under / F5
    Under / NRFI Yes). Used by the orchestrator to flip the narrative
    from "penalty" to "favorable signal".
    """
    if not market_label:
        return False
    s = str(market_label).lower()
    return ("under" in s) or ("nrfi yes" in s)


__all__ = [
    "apply_il_penalty",
    "market_is_offensive",
    "market_is_defensive",
    "KEY_POSITIONS",
    "PITCHER_POSITIONS",
    "OFFENSIVE_MARKETS",
    "MAX_KEY_BATS_PER_SIDE",
    "MAX_ER_REDUCTION",
    "MAX_CONFIDENCE_PENALTY",
]
