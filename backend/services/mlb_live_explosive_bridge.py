"""Bridge layer that converts the MLB live endpoint inputs
(``pregame_pick`` + ``live_state``) into the metrics dict expected by
``mlb_explosive_inning_engine.evaluate_explosive_inning``.

Why a bridge?
-------------
The pregame pick payload and the live-state snapshot have very
different shapes than the per-inning ``metrics`` contract of the v2
engine. Keeping the mapping isolated here:

    1. Makes the live endpoint thin (it just calls one function).
    2. Lets the engine remain pure & sport-agnostic with no coupling to
       the pick payload schema.
    3. Allows unit tests to exercise the mapping independently.

Best-effort mapping
-------------------
The live_state schema in production is minimal (currentInning, half,
score, starter pulled flag, bullpen runs allowed). We can derive:

    * inning, half_inning, outs (when present), score_home/away
    * batting_team (top → away bats, bottom → home bats)
    * times_through_order (heuristic from inning)
    * starter_removed_early (when starter_pulled is True before inning 6)
    * bullpen_fatigue (heuristic from cumulative bullpen workload)
    * lineup_position_due_up (when present)
    * pregame_total_line + live_total_line (from pregame_pick)
    * pitch_count, pitches_this_inning, base_runners, hard_contact (when
      the upstream live_state was hydrated with deeper StatsAPI data)

Unavailable fields default to None / 0 — the engine is fail-soft so the
pressure_score still reflects whatever signals ARE present.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .mlb_explosive_inning_engine import evaluate_explosive_inning

log = logging.getLogger("mlb_live_explosive_bridge")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _b(v: Any) -> bool:
    return bool(v)


def _resolve_batting_team(live_state: dict) -> Optional[str]:
    """Top half → visitor bats → 'away'. Bottom → 'home'."""
    is_top = live_state.get("is_top_half")
    if is_top is True:
        return "away"
    if is_top is False:
        return "home"
    half = (live_state.get("inning_half") or live_state.get("half") or "").lower()
    if half == "top":
        return "away"
    if half == "bottom":
        return "home"
    return None


def _resolve_times_through_order(inning: int) -> int:
    """Heuristic: 1-3 → 1st time; 4-6 → 2nd; 7+ → 3rd."""
    if inning <= 0:
        return 1
    if inning <= 3:
        return 1
    if inning <= 6:
        return 2
    return 3


def _resolve_starter_removed_early(live_state: dict, batting_side: Optional[str]) -> bool:
    """If the OPPOSING (pitching) starter has been pulled before inning 6."""
    if batting_side is None:
        return False
    pitching = "home" if batting_side == "away" else "away"
    pulled = live_state.get(f"{pitching}_starter_pulled")
    if pulled is None:
        pulled = live_state.get("starter_pulled")
    inning = _i(live_state.get("current_inning"))
    return bool(pulled) and 0 < inning < 6


def _resolve_bullpen_fatigue(live_state: dict, batting_side: Optional[str]) -> float:
    """Crude proxy: bullpen runs allowed already in this game ≥ 2 → 0.50
    fatigue band; ≥ 4 → 0.80 high-fatigue band.

    Only counts the runs allowed by the team that is now PITCHING (i.e.,
    NOT the batting team), since those are the relievers under pressure.
    """
    if batting_side is None:
        return 0.0
    pitching = "home" if batting_side == "away" else "away"
    bp_runs = _i(live_state.get(f"bullpen_runs_allowed_{pitching}"))
    if bp_runs >= 4:
        return 0.80
    if bp_runs >= 2:
        return 0.55
    if bp_runs >= 1:
        return 0.35
    return 0.15


def _extract_total_lines(pregame_pick: dict,
                          live_state: Optional[dict] = None) -> tuple[Optional[float], Optional[float]]:
    """Recover pregame + live total lines from the pick payload AND the
    live snapshot.

    Pregame: ``conf.book_total``, ``recommendation.line``, ``_mlb_script_v3.script.total_line``.
    Live  : ``live_state.live_total_line`` first, then ``pregame_pick.live_total_line``.
    """
    pre = None
    live = None
    pp = pregame_pick or {}
    ls = live_state or {}

    # Pregame line — try several common places
    pre = (
        pp.get("book_total")
        or (pp.get("recommendation") or {}).get("line")
        or ((pp.get("_mlb_script_v3") or {}).get("script") or {}).get("total_line")
        or ((pp.get("_mlb_script_v2") or {}).get("script") or {}).get("total_line")
    )

    # Live line — prefer the live snapshot, fall back to the pick payload
    live = (
        ls.get("live_total_line")
        or pp.get("live_total_line")
        or (pp.get("recommendation") or {}).get("live_line")
    )

    return _f(pre) if pre else None, _f(live) if live else None


def _extract_base_runners(live_state: dict) -> dict:
    """Accept multiple shapes for base_runners (StatsAPI uses
    ``offense.first/second/third`` ids; we may also have a precomputed
    ``base_runners`` dict)."""
    br = live_state.get("base_runners")
    if isinstance(br, dict):
        return br
    offense = live_state.get("offense") or {}
    if isinstance(offense, dict):
        return {
            "first":  bool(offense.get("first")),
            "second": bool(offense.get("second")),
            "third":  bool(offense.get("third")),
        }
    return {"first": False, "second": False, "third": False}


def _extract_inning_stats(live_state: dict) -> dict:
    """Per-inning stat block — only meaningful if the live_state was
    hydrated with StatsAPI box data. Returns 0s otherwise."""
    return {
        "pitches_this_inning":    _i(live_state.get("pitches_this_inning")),
        "walks_this_inning":      _i(live_state.get("walks_this_inning")),
        "hits_this_inning":       _i(live_state.get("hits_this_inning")),
        "hard_contact_this_inning":  _i(live_state.get("hard_contact_this_inning")),
        "barrels_this_inning":    _i(live_state.get("barrels_this_inning")),
        "avg_exit_velocity":      _f(live_state.get("avg_exit_velocity")),
        "line_drives_this_inning":_i(live_state.get("line_drives_this_inning")),
        "wild_pitch_or_hbp":      _b(live_state.get("wild_pitch_or_hbp")),
        "falling_behind_count_rate": _f(live_state.get("falling_behind_count_rate")),
    }


def build_live_metrics(pregame_pick: Optional[dict],
                        live_state: Optional[dict]) -> dict:
    """Translate the live endpoint inputs into the v2 engine ``metrics``
    contract. Fail-soft on every field."""
    pp = pregame_pick or {}
    ls = live_state or {}

    batting_side = _resolve_batting_team(ls)
    inning = _i(ls.get("current_inning"))
    score_home = _i(ls.get("home_runs"))
    score_away = _i(ls.get("away_runs"))
    pre_line, live_line = _extract_total_lines(pp, ls)

    # Pitching-side context
    pitching = "home" if batting_side == "away" else "away"
    current_pitcher = ls.get(f"{pitching}_current_pitcher_name") or ls.get("current_pitcher")
    pitch_count = _i(ls.get(f"{pitching}_pitch_count")) or _i(ls.get("pitch_count"))
    pitcher_role = ls.get(f"{pitching}_pitcher_role") or ls.get("pitcher_role") or "starter"

    # Lineup
    due_up = _i(ls.get(f"{batting_side}_lineup_position_due_up")) if batting_side else 0
    due_up = due_up or _i(ls.get("lineup_position_due_up"))

    # Conf identity from pregame
    conf = pp.get("match") or pp.get("conf") or {}
    home_team = conf.get("home_team") or pp.get("home_team")
    away_team = conf.get("away_team") or pp.get("away_team")

    # Pregame side (Under vs Over) — used by engine to decide flip
    rec = (pp.get("recommendation") or {})
    pregame_side = None
    market_text = (rec.get("market") or "").lower()
    if "under" in market_text and "team total" not in market_text:
        pregame_side = "under"
    elif "over" in market_text:
        pregame_side = "over"

    inning_stats = _extract_inning_stats(ls)

    metrics = {
        # ── context ───────────────────────────────────────────────
        "inning":                       inning,
        "half_inning":                  (
            "top" if ls.get("is_top_half") is True else
            "bottom" if ls.get("is_top_half") is False else
            (ls.get("inning_half") or ls.get("half") or None)
        ),
        "home_team":                    home_team,
        "away_team":                    away_team,
        "batting_team":                 batting_side,
        "pitching_team":                pitching if batting_side else None,
        "outs":                         _i(ls.get("outs")),
        "score_home":                   score_home,
        "score_away":                   score_away,
        "current_total_runs":           score_home + score_away,
        "base_runners":                 _extract_base_runners(ls),

        # ── pitching ──────────────────────────────────────────────
        "current_pitcher":              current_pitcher,
        "pitch_count":                  pitch_count,
        "pitch_count_threshold":        _i(ls.get("pitch_count_threshold"), 95),
        "pitcher_role":                 pitcher_role,
        "starter_removed_early":        _resolve_starter_removed_early(ls, batting_side),
        "bullpen_fatigue":              _resolve_bullpen_fatigue(ls, batting_side),
        "next_reliever_quality":        _f(ls.get("next_reliever_quality")),
        "reliever_back_to_back":        _b(ls.get("reliever_back_to_back")),

        # ── lineup ────────────────────────────────────────────────
        "lineup_position_due_up":       due_up,
        "times_through_order":          _i(ls.get("times_through_order"))
                                         or _resolve_times_through_order(inning),
        "handedness_matchup":           ls.get("handedness_matchup") or "neutral",

        # ── markets ───────────────────────────────────────────────
        "pregame_total_line":           pre_line,
        "live_total_line":              live_line,
        "current_odds":                 ls.get("current_odds") or {},

        # ── flip caller hint ──────────────────────────────────────
        "previous_recommendation_side": pregame_side,
    }
    # Merge per-inning stats from live_state (zeroed when not hydrated)
    metrics.update(inning_stats)
    return metrics


def evaluate_live_explosive_v2(pregame_pick: Optional[dict],
                                 live_state: Optional[dict]) -> dict:
    """Top-level helper used by the live endpoint.

    Returns the full ``evaluate_explosive_inning`` payload, with two
    extra keys::

        "input_completeness": float (0..1)   # heuristic
        "data_warning":       str | None     # human-readable cue
    """
    metrics = build_live_metrics(pregame_pick, live_state)
    payload = evaluate_explosive_inning(metrics)

    # Completeness heuristic — how many "rich" inputs were present?
    rich_keys = [
        "pitches_this_inning", "walks_this_inning", "hits_this_inning",
        "hard_contact_this_inning", "barrels_this_inning",
        "avg_exit_velocity",
    ]
    present = sum(1 for k in rich_keys if metrics.get(k))
    completeness = round(present / len(rich_keys), 2)
    payload["input_completeness"] = completeness

    if completeness < 0.30:
        payload["data_warning"] = (
            "Pocos datos por inning disponibles — la evaluación se basa "
            "principalmente en contexto (marcador, line, bullpen). "
            "Conecta StatsAPI box-score para análisis completo."
        )
    else:
        payload["data_warning"] = None

    return payload


__all__ = [
    "build_live_metrics",
    "evaluate_live_explosive_v2",
]
