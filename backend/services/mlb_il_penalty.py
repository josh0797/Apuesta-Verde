"""
MLB Injured-List Penalty (GAP #1)
=================================

Today the orchestrator *detects* IL players (via
`mlb_stats_api.get_team_il_players`) but the engine ignores them when
computing `expected_runs` and `confidence_score`.

Real case that motivated this module:
  • Brandon Lowe (2B Pirates, batting #3) was on the 10-day IL.
  • The pick still rated PIT offence as if Lowe were healthy.
  • Result: the Over got way too much confidence (false positive).

This module implements `apply_il_penalty(scoring_ctx)` as a pure
function so it can be tested in isolation and dropped into the
orchestrator as a fail-soft step (any error → no penalty).

Heuristic
---------
Each "key bat" missing from a side hurts that side's offensive output:

  - "Key position" set: 1B, 2B, 3B, SS, LF, CF, RF, DH, C
    (pitchers excluded — losing a SP/RP is a separate signal handled by
    the bullpen/pitcher modules.)
  - **Per-side cap**: the MLB Stats roster-injuries endpoint cumulatively
    returns 10-day + 60-day + minor-league IL without distinguishing
    type. Naively summing them would attribute 13+ "missing key bats"
    per side which destroys the model. We cap at **MAX_KEY_BATS_PER_SIDE
    = 4** which is roughly the top of the order — i.e. the bats that
    materially move `expected_runs`. The rest are surfaced in the
    `over_cap_excluded` counter for the audit trail but not penalised.
  - Each missing key bat reduces `expected_runs` by **0.3** and shaves
    **5 points** off the confidence of offensive picks (Over /
    Run Line / Team Total) for that side.
  - Aggregate caps:
        max ER reduction       = 1.5 carreras (5 key bats global)
        max confidence penalty = 20 pts (4 key bats global)
  - Impact label aggregates both sides (post-cap):
      ≥ 3 missing key bats  → ALTO
      ≥ 1                   → MEDIO
      0                     → BAJO

Returns
-------
::

    {
        "er_adjustment":         -0.6,            # carreras a restar a ER (capado)
        "confidence_penalty":     10,             # puntos a restar al pick ofensivo (capado)
        "home_key_il_count":      1,              # post-cap
        "away_key_il_count":      1,              # post-cap
        "il_impact_label":        "MEDIO",
        "home_missing_key":       ["Brandon Lowe (2B)"],
        "away_missing_key":       ["Carlos Correa (SS)"],
        "applies_to_markets":     ["over", "run_line", "team_total"],
        "over_cap_excluded":      {"home": 9, "away": 7},
    }

The orchestrator decides whether to apply `er_adjustment` to the
`_mlb_script_v2.expectedRuns` and whether the `confidence_penalty`
should be subtracted from `chosen_market.score`. We keep the math
declarative here so the apply-site stays grep-able.
"""

from __future__ import annotations

import logging
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

# Markets considered "offensive" for the purposes of confidence penalty.
OFFENSIVE_MARKETS = ("over", "run_line", "team_total", "f5_over", "nrfi_no")

# Hard caps so a chaotic team with 8+ injuries doesn't destroy the model.
# IMPORTANT: confidence cap of -10 (not -20) was chosen after observing that a
# bigger cap pushed legit chosen_markets (UNDER 9.5 ~ score 50) below the
# orchestrator's 72-threshold, causing every pick to fall to the generic
# rescue "Run Line +1.5 (underdog) score=68" — the user reported 8/8 picks
# collapsing to the same recommendation. Keep this conservative so the
# penalty informs the score without nuking the structural signal.
MAX_KEY_BATS_PER_SIDE  = 4      # ~top-of-order proxy
MAX_ER_REDUCTION       = 1.0    # carreras (~3 key bats global cap)
MAX_CONFIDENCE_PENALTY = 10     # puntos (~2 key bats global cap)
ER_PER_KEY_BAT         = 0.3
CONF_PER_KEY_BAT       = 5

# Position priority: when we have to cap, prefer to KEEP up-the-middle
# spine + corner power positions (those drive most runs).
_POSITION_PRIORITY = {
    "C":  1, "SS": 1, "CF": 1,                    # spine
    "2B": 2, "3B": 2,                              # infield
    "1B": 3, "DH": 3, "LF": 3, "RF": 3,            # power / corner
    "OF": 4, "IF": 4,
}


def _short_pos(raw: Optional[str]) -> str:
    """Normalize an MLB position abbreviation. Returns '' if unknown."""
    if not raw:
        return ""
    return str(raw).strip().upper()


def _is_key_position(pos: Optional[str]) -> bool:
    return _short_pos(pos) in KEY_POSITIONS


def _name_with_pos(p: dict) -> str:
    nm = (p.get("name") or "?").strip()
    pos = _short_pos(p.get("position"))
    return f"{nm} ({pos})" if pos else nm


def _rank_by_position_priority(p: dict) -> tuple[int, str]:
    """Sort key — lower priority value = more impactful position."""
    pos = _short_pos(p.get("position"))
    return (_POSITION_PRIORITY.get(pos, 9), (p.get("name") or ""))


def apply_il_penalty(scoring_ctx: dict) -> dict:
    """Compute the IL-driven adjustment to expected_runs + confidence.

    Reads `home_il_players` / `away_il_players` (list of dicts produced
    by `get_team_il_players`) from `scoring_ctx`. Returns the canonical
    payload described in this module's docstring. Never raises.
    """
    try:
        home_il = scoring_ctx.get("home_il_players") or []
        away_il = scoring_ctx.get("away_il_players") or []

        def _select_key(il_list: list) -> tuple[list, int]:
            """Return (kept up to cap, excluded_count)."""
            key_only = [p for p in il_list if _is_key_position(p.get("position"))]
            key_only.sort(key=_rank_by_position_priority)
            kept     = key_only[:MAX_KEY_BATS_PER_SIDE]
            excluded = max(0, len(key_only) - len(kept))
            return kept, excluded

        home_kept, home_excl = _select_key(home_il)
        away_kept, away_excl = _select_key(away_il)
        home_key_missing = [_name_with_pos(p) for p in home_kept]
        away_key_missing = [_name_with_pos(p) for p in away_kept]
        home_key_il = len(home_key_missing)
        away_key_il = len(away_key_missing)
        total_key   = home_key_il + away_key_il

        er_adjustment      = -round(min(MAX_ER_REDUCTION, total_key * ER_PER_KEY_BAT), 2)
        confidence_penalty = int(min(MAX_CONFIDENCE_PENALTY, total_key * CONF_PER_KEY_BAT))

        if total_key >= 3:
            label = "ALTO"
        elif total_key >= 1:
            label = "MEDIO"
        else:
            label = "BAJO"

        return {
            "er_adjustment":         er_adjustment,
            "confidence_penalty":    confidence_penalty,
            "home_key_il_count":     home_key_il,
            "away_key_il_count":     away_key_il,
            "il_impact_label":       label,
            "home_missing_key":      home_key_missing,
            "away_missing_key":      away_key_missing,
            "applies_to_markets":    list(OFFENSIVE_MARKETS),
            "over_cap_excluded":     {"home": home_excl, "away": away_excl},
        }
    except Exception as exc:  # pragma: no cover — fail-soft by design
        log.debug("apply_il_penalty failed (fail-soft): %s", exc)
        return {
            "er_adjustment":         0.0,
            "confidence_penalty":    0,
            "home_key_il_count":     0,
            "away_key_il_count":     0,
            "il_impact_label":       "BAJO",
            "home_missing_key":      [],
            "away_missing_key":      [],
            "applies_to_markets":    list(OFFENSIVE_MARKETS),
            "over_cap_excluded":     {"home": 0, "away": 0},
            "_error":                str(exc),
        }


def market_is_offensive(market_label: Optional[str]) -> bool:
    """Return True when the chosen market is offensive (Over / RL / TT).

    Used by the orchestrator to decide if `confidence_penalty` should
    apply. Defensive picks (Under, F5 Under, NRFI Yes) are NOT penalized
    when bats are missing — fewer bats = fewer runs = Under-friendly.
    """
    if not market_label:
        return False
    s = str(market_label).lower()
    # Hard filter Under variants — those benefit from missing bats.
    if "under" in s or "nrfi yes" in s:
        return False
    return any(tag in s for tag in ("over", "run line", "team total", "f5 over"))


__all__ = [
    "apply_il_penalty",
    "market_is_offensive",
    "KEY_POSITIONS",
    "OFFENSIVE_MARKETS",
    "MAX_KEY_BATS_PER_SIDE",
    "MAX_ER_REDUCTION",
    "MAX_CONFIDENCE_PENALTY",
]
