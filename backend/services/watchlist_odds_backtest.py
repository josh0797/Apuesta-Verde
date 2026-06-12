"""Phase F65 — Watchlist Odds-Needed Backtest.

Goal
====
Measure the **economic value** of the structural rescue route introduced
in Phase F64 (the ``watchlist_odds_needed`` bucket). For every pick that
the guardrail diverted to "Watchlist por cuota" we want to answer:

  1. Did the implied probability EVER cross into a positive-edge zone
     before kick-off?  (i.e. did the market eventually meet our model)
  2. If yes, *how long* did that take? (median time-to-positive-edge)
  3. If we had blindly bet at the **best** observed odds, what would the
     hit-rate and ROI look like — per family (CORNERS / GOALS / UNDER /
     OVER) and per league tier?
  4. What share of "rescued" picks NEVER got a positive edge — i.e.
     decisions where the guardrail was *correct* to keep them in
     watchlist rather than discarding them outright?

Strategy
========
- **Snapshots collection** ``watchlist_odds_snapshots`` — populated by a
  scheduler job every hour for each pick currently in the bucket. Each
  row stores the latest observed odds for the *rescued_market*, the
  implied probability, the model-estimated probability, and the edge.
- **Settlement** comes from the existing ``finished_matches`` collection
  (final score, corners totals, etc. ingested by the football settler).
- **Backtest engine** is *pure functional* and works on plain Python
  dicts, so it can run both against MongoDB AND against a synthetic
  fixture (used by the unit tests and the seed dataset shipped with
  this module).

The actual MongoDB I/O lives in two thin wrappers in ``server.py``:
  - ``snapshot_watchlist_odds()`` — called by the scheduler.
  - the ``/api/backtest/watchlist-odds-needed`` endpoint — calls
    ``run_watchlist_backtest(snapshots, settlements)``.

This module never touches Mongo; everything is in-memory and
deterministic so the scorer can be unit-tested in milliseconds.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

log = logging.getLogger("backtest.watchlist_odds_needed")

ENGINE_VERSION = "watchlist_backtest.v1"

# A pick is considered "rescued" (hit) when the rescued_market won
# according to the settled outcome. The mapping from market to outcome
# is deliberately limited to the markets the structural engine emits:
SUPPORTED_FAMILIES = {"CORNERS", "GOALS", "UNDER", "OVER"}


# ─────────────────────────────────────────────────────────────────────
# Public dataclasses (kept as plain dicts to stay Mongo-friendly)
# ─────────────────────────────────────────────────────────────────────
def empty_report() -> dict:
    """Default zeroed report shape — returned when there is no data."""
    return {
        "engine_version":               ENGINE_VERSION,
        "generated_at":                 _now_iso(),
        "n_picks_total":                0,
        "n_picks_settled":              0,
        "n_picks_with_positive_edge":   0,
        "n_picks_won":                  0,
        "n_picks_lost":                 0,
        "n_picks_no_positive_edge":     0,
        "hit_rate_pct":                 None,
        "roi_pct":                      None,
        "avg_edge_at_pick":             None,
        "avg_edge_at_best":             None,
        "median_hours_to_positive_edge": None,
        "per_family":                   {},   # CORNERS / GOALS / UNDER / OVER
        "per_league_tier":              {},   # Tier 1 / Tier 2 / Tier 3 / Other
        "notes":                        [],
    }


# ─────────────────────────────────────────────────────────────────────
# Edge math helpers
# ─────────────────────────────────────────────────────────────────────
def implied_probability(odds: Optional[float]) -> Optional[float]:
    try:
        o = float(odds)
        if o <= 1.0:
            return None
        return 1.0 / o
    except (TypeError, ValueError):
        return None


def edge_pct(estimated_prob: Optional[float], odds: Optional[float]) -> Optional[float]:
    """Returns ``edge%`` = (estimated − implied) × 100.  None when inputs
    are insufficient."""
    imp = implied_probability(odds)
    if imp is None or estimated_prob is None:
        return None
    try:
        return round((float(estimated_prob) - imp) * 100.0, 2)
    except (TypeError, ValueError):
        return None


def _hours_between(a_iso: Optional[str], b_iso: Optional[str]) -> Optional[float]:
    """Return ``(b - a)`` in hours, or None if either timestamp is unusable."""
    da, db = _parse_iso(a_iso), _parse_iso(b_iso)
    if not da or not db:
        return None
    return round((db - da).total_seconds() / 3600.0, 2)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00") if isinstance(ts, str) else None
        if not s:
            return None
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# Snapshot-side helpers
# ─────────────────────────────────────────────────────────────────────
def best_positive_snapshot(snapshots: list[dict]) -> Optional[dict]:
    """Return the snapshot with the **highest positive edge** if any.

    The "best" snapshot is the one that maximises ``estimated_prob -
    implied_prob`` while ``odds`` are sane (>1.0). When no snapshot has
    a positive edge, returns ``None``.
    """
    best = None
    best_edge: float = 0.0
    for s in snapshots or []:
        e = edge_pct(s.get("estimated_prob"), s.get("odds"))
        if e is None or e <= 0:
            continue
        if best is None or e > best_edge:
            best = s
            best_edge = e
    return best


def hours_to_first_positive_edge(snapshots: list[dict]) -> Optional[float]:
    """Time (in hours) between the **pick creation** snapshot and the
    first snapshot whose edge crossed into positive territory.

    Snapshots must include ``captured_at`` (ISO) and ``edge_pct`` or
    enough data (estimated_prob + odds) to derive it.
    """
    if not snapshots:
        return None
    ordered = sorted(snapshots, key=lambda s: _parse_iso(s.get("captured_at"))
                                              or datetime.min.replace(tzinfo=timezone.utc))
    base_ts = ordered[0].get("captured_at")
    for s in ordered:
        e = s.get("edge_pct")
        if e is None:
            e = edge_pct(s.get("estimated_prob"), s.get("odds"))
        if e is not None and e > 0:
            return _hours_between(base_ts, s.get("captured_at"))
    return None


# ─────────────────────────────────────────────────────────────────────
# Settlement helpers — was the rescued_market a winner?
# ─────────────────────────────────────────────────────────────────────
def did_rescued_market_win(
    rescued_market: dict | None,
    settlement: dict | None,
) -> Optional[bool]:
    """Decide if the ``rescued_market`` cashed.

    Both dicts are minimal::

      rescued_market = {"market": "Total corners Over", "family": "CORNERS",
                        "line": 9.5}
      settlement    = {"final_corners_total": 11, "final_goals_total": 2,
                       "home_score": 1, "away_score": 1, ...}

    Returns:
      * ``True``  if the bet wins.
      * ``False`` if it loses.
      * ``None``  if the data needed to decide is missing (push / unknown).
    """
    if not rescued_market or not settlement:
        return None
    fam = (rescued_market.get("family") or "").upper()
    market = (rescued_market.get("market") or "").lower()
    line = rescued_market.get("line")

    # ── Total corners markets ──
    if "corners" in market or fam == "CORNERS":
        total = settlement.get("final_corners_total")
        if total is None:
            return None
        if line is None:
            # Engine emits Over/Under WITHOUT an explicit line for ``Total
            # corners Over`` (canonical 9.5 in the structural engine).
            line = 9.5
        try:
            total = float(total)
            line  = float(line)
        except (TypeError, ValueError):
            return None
        if total == line:
            return None  # push
        if "over" in market:
            return total > line
        if "under" in market:
            return total < line
        if "team corners over" in market:
            # Without per-side corners we can't settle; fail-soft to None.
            home_c = settlement.get("home_corners")
            away_c = settlement.get("away_corners")
            if home_c is None or away_c is None:
                return None
            best = max(float(home_c), float(away_c))
            return best > (line or 5.5)
        return None

    # ── Goals markets ──
    if fam in ("GOALS", "UNDER", "OVER") or "goals" in market:
        total = settlement.get("final_goals_total")
        if total is None:
            return None
        try:
            total = float(total)
            line  = float(line if line is not None else 2.5)
        except (TypeError, ValueError):
            return None
        if total == line:
            return None
        if "over" in market or fam == "OVER":
            return total > line
        if "under" in market or fam == "UNDER":
            return total < line

    return None


# ─────────────────────────────────────────────────────────────────────
# League tier classifier — mirrors the football tier hints elsewhere.
# ─────────────────────────────────────────────────────────────────────
_TIER_1 = {
    "premier league", "la liga", "serie a", "bundesliga", "ligue 1",
    "champions league", "europa league",
}
_TIER_2 = {
    "championship", "primeira liga", "eredivisie", "scottish premiership",
    "mls", "liga mx",
}


def _league_tier(league: Optional[str]) -> str:
    if not league:
        return "Other"
    norm = league.lower()
    if any(t in norm for t in _TIER_1):
        return "Tier 1"
    if any(t in norm for t in _TIER_2):
        return "Tier 2"
    return "Tier 3"


# ─────────────────────────────────────────────────────────────────────
# Main scorer — pure, in-memory, fully deterministic.
# ─────────────────────────────────────────────────────────────────────
def run_watchlist_backtest(
    picks: Iterable[dict],
    settlements_by_match: Optional[dict[str, dict]] = None,
    snapshots_by_match: Optional[dict[str, list[dict]]] = None,
    *,
    flat_stake: float = 1.0,
) -> dict:
    """Evaluate the ``watchlist_odds_needed`` bucket end-to-end.

    Parameters
    ----------
    picks
        Iterable of pick dicts as they were enqueued into the bucket by
        ``market_guardrail``. Each MUST carry::

          {
            "match_id":       "abc",
            "match_label":    "Brazil vs Morocco",
            "league":         "Friendly",          # optional
            "edge_pct":       -18.8,               # at pick time
            "rescued_market": {"market": ..., "family": ..., "line": ...},
            "structural_review": {...},            # optional
            "estimated_prob": 0.55,                # model probability
            "created_at":     "2026-...",          # optional
          }
    settlements_by_match
        Mapping ``match_id → {final_corners_total, final_goals_total, …}``.
        Picks without a settlement are counted as ``unsettled``.
    snapshots_by_match
        Mapping ``match_id → [snapshot, ...]`` — the cron-collected odds
        history. Optional. Drives best-edge + time-to-positive-edge
        statistics.
    flat_stake
        Stake size for ROI math (defaults to 1u flat-stake).

    Returns
    -------
    A report dict shaped like ``empty_report()``. Always safe to JSON-
    serialise. Fail-soft: missing input fields collapse to ``None`` /
    are excluded from averages.
    """
    snapshots_by_match = snapshots_by_match or {}
    settlements_by_match = settlements_by_match or {}
    report = empty_report()

    per_family: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "won": 0, "lost": 0, "stake": 0.0, "returned": 0.0,
    })
    per_tier: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "won": 0, "lost": 0, "stake": 0.0, "returned": 0.0,
    })

    edges_at_pick: list[float] = []
    edges_at_best: list[float] = []
    hours_to_positive: list[float] = []
    n_total = 0
    n_settled = 0
    n_won = 0
    n_lost = 0
    n_with_positive_edge = 0
    n_no_positive_edge = 0
    stake_total = 0.0
    returned_total = 0.0

    for p in picks or []:
        n_total += 1
        match_id = p.get("match_id") or "" if isinstance(p, dict) else ""
        if not isinstance(p, dict):
            continue
        rescued = p.get("rescued_market") or {}
        if not isinstance(rescued, dict):
            rescued = {}
        family = (rescued.get("family") or "OTHER").upper()
        tier   = _league_tier(p.get("league"))

        # Edge at pick time.
        edge0 = p.get("edge_pct")
        if isinstance(edge0, (int, float)):
            edges_at_pick.append(float(edge0))

        # Snapshot stats.
        snaps = snapshots_by_match.get(match_id) or []
        best = best_positive_snapshot(snaps)
        if best:
            n_with_positive_edge += 1
            be = edge_pct(best.get("estimated_prob"), best.get("odds"))
            if be is not None:
                edges_at_best.append(be)
            htp = hours_to_first_positive_edge(snaps)
            if htp is not None:
                hours_to_positive.append(htp)
        else:
            n_no_positive_edge += 1

        # Settlement.
        settlement = settlements_by_match.get(match_id)
        if not settlement:
            # Unsettled — counts in totals but not in win/lose buckets.
            continue
        n_settled += 1
        win = did_rescued_market_win(rescued, settlement)
        if win is None:
            # Push / unknown.
            continue
        # Choose the odds to settle at. Prefer the BEST positive-edge odds
        # (assumes the user took the better line later); fall back to
        # the original pick odds when no positive-edge snapshot.
        settle_odds = (best or {}).get("odds") or p.get("odds")
        try:
            settle_odds = float(settle_odds) if settle_odds else None
        except (TypeError, ValueError):
            settle_odds = None

        stake = flat_stake
        ret   = 0.0
        if win:
            n_won += 1
            if settle_odds:
                ret = stake * settle_odds
        else:
            n_lost += 1
            ret = 0.0

        stake_total    += stake
        returned_total += ret

        per_family[family]["n"]        += 1
        per_family[family]["stake"]    += stake
        per_family[family]["returned"] += ret
        if win:   per_family[family]["won"]  += 1
        else:     per_family[family]["lost"] += 1

        per_tier[tier]["n"]        += 1
        per_tier[tier]["stake"]    += stake
        per_tier[tier]["returned"] += ret
        if win:   per_tier[tier]["won"]  += 1
        else:     per_tier[tier]["lost"] += 1

    # ── Aggregate ──
    report["n_picks_total"]              = n_total
    report["n_picks_settled"]            = n_settled
    report["n_picks_with_positive_edge"] = n_with_positive_edge
    report["n_picks_won"]                = n_won
    report["n_picks_lost"]               = n_lost
    report["n_picks_no_positive_edge"]   = n_no_positive_edge
    settled_with_outcome = n_won + n_lost
    report["hit_rate_pct"] = (
        round(100.0 * n_won / settled_with_outcome, 2)
        if settled_with_outcome else None
    )
    report["roi_pct"] = (
        round(100.0 * (returned_total - stake_total) / stake_total, 2)
        if stake_total else None
    )
    report["avg_edge_at_pick"] = (
        round(statistics.mean(edges_at_pick), 2) if edges_at_pick else None
    )
    report["avg_edge_at_best"] = (
        round(statistics.mean(edges_at_best), 2) if edges_at_best else None
    )
    report["median_hours_to_positive_edge"] = (
        round(statistics.median(hours_to_positive), 2)
        if hours_to_positive else None
    )

    # Finalise per-family / per-tier sub-blocks.
    for bucket in (per_family, per_tier):
        for key, agg in bucket.items():
            agg["hit_rate_pct"] = (
                round(100.0 * agg["won"] / (agg["won"] + agg["lost"]), 2)
                if (agg["won"] + agg["lost"]) else None
            )
            agg["roi_pct"] = (
                round(100.0 * (agg["returned"] - agg["stake"]) / agg["stake"], 2)
                if agg["stake"] else None
            )
    report["per_family"]      = dict(per_family)
    report["per_league_tier"] = dict(per_tier)

    if n_total == 0:
        report["notes"].append("no_picks_in_window")
    elif n_settled == 0:
        report["notes"].append("no_settlements_yet")
    elif not edges_at_best:
        report["notes"].append("no_snapshots_recorded_yet")

    return report


# ─────────────────────────────────────────────────────────────────────
# Synthetic seed dataset — used by tests and the demo endpoint.
# ─────────────────────────────────────────────────────────────────────
def _synthetic_demo_dataset() -> tuple[list[dict], dict, dict]:
    """Returns (picks, settlements, snapshots) for a 5-pick demo run.

    Designed so the resulting report exercises EVERY code path:
      - 1 corner Over win at the best odds
      - 1 corner Under loss
      - 1 goals Over win, settled at pick odds (no snapshots)
      - 1 unsettled pick (still in watchlist, never got positive edge)
      - 1 push (final equals line)
    """
    picks = [
        {
            "match_id": "m1", "match_label": "A vs B", "league": "La Liga",
            "edge_pct": -18.5, "estimated_prob": 0.58, "odds": 1.55,
            "rescued_market": {"market": "Total corners Over", "family": "CORNERS",
                               "line": 9.5},
            "created_at": "2026-06-14T10:00:00Z",
        },
        {
            "match_id": "m2", "match_label": "C vs D", "league": "Bundesliga",
            "edge_pct": -22.0, "estimated_prob": 0.61, "odds": 1.40,
            "rescued_market": {"market": "Total corners Under", "family": "CORNERS",
                               "line": 9.5},
            "created_at": "2026-06-14T11:00:00Z",
        },
        {
            "match_id": "m3", "match_label": "E vs F", "league": "MLS",
            "edge_pct": -15.0, "estimated_prob": 0.55, "odds": 1.70,
            "rescued_market": {"market": "Goals Over", "family": "GOALS", "line": 2.5},
            "created_at": "2026-06-14T12:00:00Z",
        },
        {
            "match_id": "m4", "match_label": "G vs H", "league": "Friendly",
            "edge_pct": -19.0, "estimated_prob": 0.52, "odds": 1.65,
            "rescued_market": {"market": "Total corners Over", "family": "CORNERS",
                               "line": 9.5},
            "created_at": "2026-06-14T13:00:00Z",
        },
        {
            "match_id": "m5", "match_label": "I vs J", "league": "Premier League",
            "edge_pct": -10.0, "estimated_prob": 0.54, "odds": 1.85,
            "rescued_market": {"market": "Total corners Over", "family": "CORNERS",
                               "line": 9.5},
            "created_at": "2026-06-14T14:00:00Z",
        },
    ]
    settlements = {
        "m1": {"final_corners_total": 12},                          # over 9.5 → win
        "m2": {"final_corners_total": 11},                          # under 9.5 fails → loss
        "m3": {"final_goals_total":   3},                           # over 2.5 → win
        # m4 is intentionally unsettled (still upcoming).
        "m5": {"final_corners_total": 9.5},                         # push
    }
    snapshots = {
        "m1": [
            {"captured_at": "2026-06-14T10:00:00Z", "odds": 1.55, "estimated_prob": 0.58},
            {"captured_at": "2026-06-14T16:00:00Z", "odds": 1.65, "estimated_prob": 0.58},
            {"captured_at": "2026-06-14T20:00:00Z", "odds": 1.85, "estimated_prob": 0.58},
        ],
        "m2": [
            {"captured_at": "2026-06-14T11:00:00Z", "odds": 1.40, "estimated_prob": 0.61},
            {"captured_at": "2026-06-14T17:00:00Z", "odds": 1.45, "estimated_prob": 0.61},
            # never crosses → 0.61 vs 1/1.45=0.69 still negative
        ],
        "m4": [
            {"captured_at": "2026-06-14T13:00:00Z", "odds": 1.65, "estimated_prob": 0.52},
            # never crosses positive.
        ],
    }
    return picks, settlements, snapshots


__all__ = [
    "ENGINE_VERSION",
    "SUPPORTED_FAMILIES",
    "empty_report",
    "implied_probability", "edge_pct",
    "best_positive_snapshot", "hours_to_first_positive_edge",
    "did_rescued_market_win",
    "run_watchlist_backtest",
    "_synthetic_demo_dataset",
]
