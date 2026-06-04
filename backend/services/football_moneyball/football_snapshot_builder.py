"""Football Snapshot Builder — pregame + live snapshot digests.

Produces a compact, persistent digest of the most relevant Moneyball
signals for a football match. The digest is intended to be saved into
``football_match_intelligence_snapshots`` (warehouse) so the engine can:

  * Re-use the digest without re-fetching upstream data on subsequent
    runs (e.g. mid-day refreshes or live re-evaluation).
  * Power the pattern matcher (derive_pattern_keys) deterministically.
  * Drive the live vs. pregame comparison panel in the UI.

Pure module — no IO. All helpers tolerate missing data and never raise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# We deliberately import within the package; goal-pressure is pure too.
from .football_goal_pressure_profile import (
    calculate_match_goal_pressure_context,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_get(d: Any, *path, default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _summarize_team_form(team_block: dict | None) -> dict:
    if not isinstance(team_block, dict):
        return {}
    ctx = team_block.get("context") if isinstance(team_block.get("context"), dict) else team_block
    seasonal = ctx.get("seasonal_form") if isinstance(ctx, dict) else {}
    recent = ctx.get("recent_fixtures") if isinstance(ctx, dict) else {}
    early = seasonal.get("early_goal_profile") if isinstance(seasonal, dict) else {}
    if not isinstance(seasonal, dict):
        seasonal = {}
    if not isinstance(recent, dict):
        recent = {}
    if not isinstance(early, dict):
        early = {}
    return {
        "position":           ctx.get("position") if isinstance(ctx, dict) else None,
        "form_last_5":        ctx.get("form_last_5") if isinstance(ctx, dict) else None,
        "goals_for_avg":      ctx.get("goals_for_avg") if isinstance(ctx, dict) else None,
        "goals_against_avg":  ctx.get("goals_against_avg") if isinstance(ctx, dict) else None,
        "under_2_5_rate":     recent.get("under_2_5_rate"),
        "under_3_5_rate":     recent.get("under_3_5_rate"),
        "btts_rate":          recent.get("btts_rate"),
        "clean_sheet_rate":   recent.get("clean_sheet_rate"),
        "early_goal_pct":     early.get("early_goal_pct"),
    }


def _summarize_odds(match: dict) -> dict:
    snaps = match.get("odds_snapshots") or match.get("odds") or []
    if not isinstance(snaps, list) or not snaps:
        return {"available": False, "count": 0}
    # Snapshot last frame compactly
    last = snaps[-1] if isinstance(snaps[-1], dict) else {}
    return {
        "available": True,
        "count":     len(snaps),
        "last":      {
            k: last.get(k) for k in (
                "timestamp", "home", "draw", "away",
                "over_2_5", "under_2_5", "over_3_5", "under_3_5",
                "btts_yes", "btts_no",
            ) if last.get(k) is not None
        },
    }


def _summarize_live(match: dict) -> dict:
    live = match.get("live_stats") or match.get("live") or {}
    if not isinstance(live, dict) or not live:
        return {"available": False}
    h = live.get("home_stats") if isinstance(live.get("home_stats"), dict) else {}
    a = live.get("away_stats") if isinstance(live.get("away_stats"), dict) else {}
    return {
        "available":    True,
        "minute":       live.get("minute"),
        "status":       live.get("status") or match.get("status"),
        "score_home":   live.get("goals_home") or live.get("home_goals"),
        "score_away":   live.get("goals_away") or live.get("away_goals"),
        "home_stats": {
            "shots_total":     h.get("shots_total"),
            "shots_on_goal":   h.get("shots_on_goal") or h.get("shots_on_target"),
            "possession":      h.get("possession"),
            "corners":         h.get("corners"),
            "xg":              h.get("xg"),
        },
        "away_stats": {
            "shots_total":     a.get("shots_total"),
            "shots_on_goal":   a.get("shots_on_goal") or a.get("shots_on_target"),
            "possession":      a.get("possession"),
            "corners":         a.get("corners"),
            "xg":              a.get("xg"),
        },
    }


def _summarize_corner(match: dict) -> dict:
    cf = match.get("_corner_form") if isinstance(match.get("_corner_form"), dict) else {}
    if not cf:
        return {"available": False}
    return {
        "available":              True,
        "mode":                   cf.get("mode"),
        "expected_total_corners": cf.get("expected_total_corners"),
        "data_quality":           cf.get("data_quality"),
    }


def _summarize_quality(match: dict) -> dict:
    fq = match.get("_football_quality") if isinstance(match.get("_football_quality"), dict) else {}
    if not fq:
        return {"available": False}
    return {
        "available":               True,
        "leagueQualityScore":      fq.get("leagueQualityScore"),
        "marketLiquidityScore":    fq.get("marketLiquidityScore"),
        "footballSelectionScore":  fq.get("footballSelectionScore"),
        "classification":          fq.get("classification"),
        "tier":                    fq.get("tier"),
    }


def _summarize_form_guard(match: dict) -> dict:
    fg = match.get("_form_guard") if isinstance(match.get("_form_guard"), dict) else {}
    if not fg:
        return {"available": False}
    return {
        "available":  True,
        "fragile":    fg.get("fragile"),
        "verdict":    fg.get("verdict"),
        "reasons":    fg.get("reasons"),
    }


def build_pregame_snapshot(match: dict | None) -> dict:
    """Build the pregame digest for one football match.

    Pure: takes the raw match dict, returns a compact dict ready for
    persistence in `football_match_intelligence_snapshots`.
    """
    if not isinstance(match, dict):
        return {"available": False, "reason": "no_match"}

    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    league = _safe_get(match, "league", "name") or match.get("league_name") or match.get("competition")

    return {
        "available":              True,
        "generated_at":           _now_iso(),
        "match_id":               match.get("match_id"),
        "league":                 league,
        "home": {
            "id":   home.get("id"),
            "name": home.get("name"),
            "form": _summarize_team_form(home),
        },
        "away": {
            "id":   away.get("id"),
            "name": away.get("name"),
            "form": _summarize_team_form(away),
        },
        "goal_pressure_profile":  calculate_match_goal_pressure_context(match),
        "odds_digest":            _summarize_odds(match),
        "corner_form":            _summarize_corner(match),
        "football_quality":       _summarize_quality(match),
        "form_guard":             _summarize_form_guard(match),
    }


def build_live_snapshot(match: dict | None) -> dict:
    """Build the live digest (state + live-aware pressure recompute)."""
    if not isinstance(match, dict):
        return {"available": False, "reason": "no_match"}
    return {
        "available":             True,
        "generated_at":          _now_iso(),
        "match_id":              match.get("match_id"),
        "live_state":            _summarize_live(match),
        "goal_pressure_profile": calculate_match_goal_pressure_context(match),
    }


def build_full_intelligence_snapshot(
    match: dict | None,
    *,
    selected_market: str | None = None,
    pattern_keys: list[str] | None = None,
    pick_payload: dict | None = None,
) -> dict:
    """Compose pregame + (optional) live snapshot + market selection refs.

    The result is the canonical document persisted by
    ``persist_match_intelligence_snapshot``.
    """
    pregame = build_pregame_snapshot(match)
    live    = build_live_snapshot(match) if (isinstance(match, dict) and match.get("live_stats")) else None
    return {
        "version":          "football_moneyball.snapshot.1",
        "generated_at":     _now_iso(),
        "match_id":         pregame.get("match_id"),
        "league":           pregame.get("league"),
        "selected_market":  selected_market,
        "pattern_keys":     list(pattern_keys or []),
        "pregame":          pregame,
        "live":             live,
        "pick_payload_digest": _digest_pick_payload(pick_payload) if pick_payload else None,
    }


def _digest_pick_payload(pp: dict | None) -> dict:
    if not isinstance(pp, dict):
        return {}
    return {
        "recommendation":         pp.get("recommendation"),
        "market_selection":       pp.get("market_selection"),
        "reason_codes":           pp.get("reason_codes"),
        "goal_pressure_profile":  pp.get("goal_pressure_profile"),
        "historical_pattern_match": pp.get("historical_pattern_match"),
    }


__all__ = [
    "build_pregame_snapshot",
    "build_live_snapshot",
    "build_full_intelligence_snapshot",
]
