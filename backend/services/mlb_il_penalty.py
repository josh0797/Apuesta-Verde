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
# Pattern taxonomy (rewritten 2026-06-02 round 2).
# Each pattern targets a SINGLE category — no shared regex between buckets.
# Ordering matters: most specific first so "15-day" does not get captured
# by a generic "day" regex.
_PATTERN_15DAY  = re.compile(r"15[- ]?day\s+injured\s+list", re.I)
_PATTERN_10DAY  = re.compile(r"10[- ]?day\s+injured\s+list", re.I)
_PATTERN_DTD    = re.compile(r"\bday[- ]to[- ]day\b",         re.I)
_PATTERN_60DAY  = re.compile(r"60[- ]?day\s+injured\s+list",  re.I)
_PATTERN_REST   = re.compile(r"restricted\s+list",            re.I)
_PATTERN_7DAY   = re.compile(r"7[- ]?day\s+injured\s+list",   re.I)
_PATTERN_MINOR  = re.compile(r"\bminor[- ]?league",           re.I)
_PATTERN_BEREAV = re.compile(r"bereavement\s+list",           re.I)


def _classify_status(status_raw: Optional[str]) -> str:
    """Return one of::

        'active_10d'       — 10-day IL (active 26-man impact)
        'pitcher_15d'      — 15-day IL (pitcher-only roster slot)
        'day_to_day'       — DTD (informational; no penalty)
        'long_term_60d'    — 60-day IL or Restricted (long absence)
        'minors'           — 7-day minor-league IL
        'bereavement'      — Bereavement / Family List
        'unknown'          — unrecognized string

    Only ``active_10d`` reaches the offensive penalty. ``pitcher_15d``
    feeds the separate pitcher_depth counter. DTD, 60-day, minors and
    bereavement are surfaced in ``_status_breakdown`` for diagnostics
    but never modify the score.
    """
    if not status_raw:
        return "unknown"
    s = str(status_raw)
    # Order matters: 15-day BEFORE 10-day so "15-Day" never falls through
    # the 10-day regex (which would match "5-day").
    if _PATTERN_15DAY.search(s):
        return "pitcher_15d"
    if _PATTERN_10DAY.search(s):
        return "active_10d"
    if _PATTERN_DTD.search(s):
        return "day_to_day"
    if _PATTERN_60DAY.search(s):
        return "long_term_60d"
    if _PATTERN_REST.search(s):
        return "long_term_60d"
    if _PATTERN_BEREAV.search(s):
        return "bereavement"
    if _PATTERN_7DAY.search(s):
        return "minors"
    if _PATTERN_MINOR.search(s):
        return "minors"
    return "unknown"


# ── PENALTY-ELIGIBLE STATUSES ──────────────────────────────────────────────
# IMPORTANT: only 10-day IL hits the offensive penalty. Day-To-Day is
# informational only — a DTD player frequently plays anyway, and even
# when they don't the lineup card already accounts for it elsewhere.
# Including DTD here would create the same kind of false-positive
# inflation that the original 60-day bug produced.
_PENALTY_ELIGIBLE_STATUSES = {"active_10d"}


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
    """Group IL players by classified status. All known buckets are
    pre-populated so downstream code can read counts safely."""
    out: dict[str, list[dict]] = {
        "active_10d":    [],
        "pitcher_15d":   [],
        "day_to_day":    [],
        "long_term_60d": [],
        "minors":        [],
        "bereavement":   [],
        "unknown":       [],
    }
    for p in il_list:
        cat = _classify_status(p.get("status"))
        out.setdefault(cat, []).append(p)
    return out


def _data_quality_label(
    home_total: int,
    away_total: int,
    home_unknown: int,
    away_unknown: int,
) -> str:
    """Quick heuristic to flag the trustworthiness of the IL signal.

    * ``missing``: zero IL players surfaced on either side — most likely
      the API call failed or we cached an empty roster. Downstream UI
      should show "Datos de IL no disponibles" and NOT penalize.
    * ``thin``: at least one side has >50% players in ``unknown`` status
      → the regex taxonomy didn't recognize their state, so any
      derived penalty would be guesswork.
    * ``strong``: every classified player landed in a known bucket.
    """
    grand_total = home_total + away_total
    if grand_total == 0:
        return "missing"
    grand_unknown = home_unknown + away_unknown
    if grand_total > 0 and (grand_unknown / grand_total) > 0.50:
        return "thin"
    return "strong"


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
            """Return (kept_after_cap, cap_excluded_count, status_breakdown).

            Pipeline:
                1) Split by IL status type.
                2) Keep ONLY ``_PENALTY_ELIGIBLE_STATUSES`` (active 10-day
                   only — 15-day pitcher IL, 60-day, minor-league, DTD,
                   bereavement are surfaced in breakdown but NEVER drive
                   the offensive penalty).
                3) Keep only KEY_POSITIONS (filters pitchers).
                4) Sort by position priority (spine first).
                5) Cap at ``MAX_KEY_BATS_PER_SIDE``.

            ``cap_excluded_count`` is strictly the players we threw away
            BECAUSE of MAX_KEY_BATS_PER_SIDE — never because of status
            filtering. That's why ``cap_applied`` further down does NOT
            count long-term IL as a cap event.
            """
            breakdown = _split_by_status(il_list)
            penalty_pool: list[dict] = []
            for cat in _PENALTY_ELIGIBLE_STATUSES:
                penalty_pool.extend(breakdown.get(cat, []))
            key_only = [p for p in penalty_pool if _is_key_position(p.get("position"))]
            key_only.sort(key=_rank_by_position_priority)
            kept = key_only[:MAX_KEY_BATS_PER_SIDE]
            cap_excluded = max(0, len(key_only) - len(kept))
            counts = {k: len(v) for k, v in breakdown.items()}
            counts["active_eligible_key"] = len(key_only)
            return kept, cap_excluded, counts

        home_kept, home_cap_excl, home_brk = _select_key(home_il)
        away_kept, away_cap_excl, away_brk = _select_key(away_il)
        home_key_missing = [_name_with_pos(p) for p in home_kept]
        away_key_missing = [_name_with_pos(p) for p in away_kept]
        home_key_il = len(home_key_missing)
        away_key_il = len(away_key_missing)
        total_key   = home_key_il + away_key_il

        # Raw counts (all key positions, IGNORING status) — used to show
        # "N aplicados de M detectados" when our filter excluded long-term
        # or unknown-status entries.
        home_raw_key = sum(
            1 for p in home_il if _is_key_position(p.get("position"))
        )
        away_raw_key = sum(
            1 for p in away_il if _is_key_position(p.get("position"))
        )

        # Pitcher IL counters. Includes BOTH active 10-day AND 15-day
        # pitcher IL — those feed bullpen-fatigue / pitcher-depth, not
        # this offensive module. 60-day pitcher IL is again excluded as
        # too distant to model day-of impact.
        _pitcher_eligible = _PENALTY_ELIGIBLE_STATUSES | {"pitcher_15d"}
        home_pitcher_il = sum(
            1 for p in home_il
            if _is_pitcher_position(p.get("position"))
            and _classify_status(p.get("status")) in _pitcher_eligible
        )
        away_pitcher_il = sum(
            1 for p in away_il
            if _is_pitcher_position(p.get("position"))
            and _classify_status(p.get("status")) in _pitcher_eligible
        )

        # status_filter_excluded = key-position players who LOOK like
        # candidates from the roster but were dropped because their
        # status did NOT match _PENALTY_ELIGIBLE_STATUSES (i.e. 60-day,
        # minors, DTD, bereavement, unknown). Reported per-side so the
        # UI can render "5 excluidos por status".
        home_status_excl = max(0, home_raw_key - home_key_il - home_cap_excl)
        away_status_excl = max(0, away_raw_key - away_key_il - away_cap_excl)

        # cap_applied = TRUE only when MAX_KEY_BATS_PER_SIDE actually
        # rejected someone. Status-filtering is NOT a "cap event".
        cap_applied = (home_cap_excl > 0) or (away_cap_excl > 0)

        # Unknown-status counts (any position) — surfaces taxonomy gaps
        # that would otherwise silently swallow real injuries.
        # NOTE: home_brk values are already int counts (see _select_key),
        # so read directly — NEVER call len() on them.
        home_unknown = int(home_brk.get("unknown", 0) or 0)
        away_unknown = int(away_brk.get("unknown", 0) or 0)

        er_adjustment      = -round(min(MAX_ER_REDUCTION, total_key * ER_PER_KEY_BAT), 2)
        confidence_penalty = int(min(MAX_CONFIDENCE_PENALTY, total_key * CONF_PER_KEY_BAT))

        if total_key >= 3:
            label = "ALTO"
        elif total_key >= 1:
            label = "MEDIO"
        else:
            label = "BAJO"

        return {
            "er_adjustment":             er_adjustment,
            "confidence_penalty":        confidence_penalty,
            # Applied (post-filter, post-cap). Aliases for backward compat.
            "home_key_il_count":         home_key_il,
            "away_key_il_count":         away_key_il,
            "home_key_il_count_applied": home_key_il,
            "away_key_il_count_applied": away_key_il,
            # Raw (all key positions, no status filter).
            "home_key_il_count_raw":     home_raw_key,
            "away_key_il_count_raw":     away_raw_key,
            # Cap-only (Patrón D, corrected): only TRUE when MAX_KEY_BATS
            # actually rejected a candidate, NOT when status filter did.
            "cap_applied":               cap_applied,
            "over_cap_excluded":         {
                "home": home_cap_excl,
                "away": away_cap_excl,
            },
            # NEW top-level: how many key-position players were dropped
            # solely because of status (60-day / minors / DTD / unknown).
            "status_filter_excluded":    {
                "home": home_status_excl,
                "away": away_status_excl,
            },
            # NEW top-level: explicit filter contract so consumers know
            # which classifier the numbers come from.
            "il_filter":                 "active_10day_only",
            "il_impact_label":           label,
            "home_missing_key":          home_key_missing,
            "away_missing_key":          away_key_missing,
            "applies_to_markets":        list(OFFENSIVE_MARKETS),
            # Pitcher IL fed to pitcher_depth signal (NOT to this penalty).
            "home_pitcher_il_count":     home_pitcher_il,
            "away_pitcher_il_count":     away_pitcher_il,
            # NEW top-level: unknown-status counts surface taxonomy gaps.
            "home_unknown_il_type_count": home_unknown,
            "away_unknown_il_type_count": away_unknown,
            # NEW top-level: data trustworthiness flag.
            "data_quality": _data_quality_label(
                len(home_il), len(away_il), home_unknown, away_unknown,
            ),
            "_status_breakdown":         {
                "home": home_brk,    # already int counts (see _select_key)
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
            "status_filter_excluded":    {"home": 0, "away": 0},
            "il_filter":                 "active_10day_only",
            "home_pitcher_il_count":     0,
            "away_pitcher_il_count":     0,
            "home_unknown_il_type_count": 0,
            "away_unknown_il_type_count": 0,
            "data_quality":              "missing",
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
