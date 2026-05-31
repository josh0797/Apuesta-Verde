"""
MLB Active-Series Context Analyzer (Module #1)

Avoids the `Twins @ Pirates 2026-05-31 UNDER 9.5` style disaster, where
the engine projected ER=6.9 while the **active series** between the
same two teams had averaged 15 runs over the previous two days.

Reads finished games of the matchup from the past `days_back` days
(default 4) directly from MongoDB. Computes:

  - games_in_series, total_runs_avg, list, over_rate
  - bullpen pitch counts when present in the match doc
  - series_lean (OVER / UNDER / NEUTRAL)
  - series_override flag + reason

Fail-soft
---------
Any exception or empty DB result returns a `available=False` payload
that downstream code can safely ignore.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)


def _normalise(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def _team_match(doc: dict, home: str, away: str) -> bool:
    """Does this doc represent any game between the two teams (home/away interchangeable)?"""
    h = _normalise((doc.get("home_team") or {}).get("name") if isinstance(doc.get("home_team"), dict) else doc.get("home_team"))
    a = _normalise((doc.get("away_team") or {}).get("name") if isinstance(doc.get("away_team"), dict) else doc.get("away_team"))
    home_n, away_n = _normalise(home), _normalise(away)
    if not h or not a:
        return False
    return {h, a} == {home_n, away_n}


def _extract_runs(doc: dict) -> Optional[int]:
    """Best-effort total-runs extractor across the ingestion shapes.

    The upstream pipeline stores the final box-score in a few different
    places depending on which feed produced the doc:
      - `final_score` (rare, when a settlement job ran)
      - `score` (when API-Sports finalised the fixture)
      - `live_stats.score` (when the live ingester captured it last)
      - `live_stats.home_stats.Runs` + `live_stats.away_stats.Runs`
    """
    for path in (doc.get("final_score"), doc.get("score")):
        if isinstance(path, dict):
            try:
                return int(path.get("home", 0)) + int(path.get("away", 0))
            except (TypeError, ValueError):
                continue
    ls = doc.get("live_stats") or {}
    if isinstance(ls, dict):
        sc = ls.get("score")
        if isinstance(sc, dict):
            try:
                return int(sc.get("home", 0)) + int(sc.get("away", 0))
            except (TypeError, ValueError):
                pass
        # Box-score fields per side
        h_runs = ((ls.get("home_stats") or {}).get("Runs")
                   or (ls.get("home_stats") or {}).get("runs"))
        a_runs = ((ls.get("away_stats") or {}).get("Runs")
                   or (ls.get("away_stats") or {}).get("runs"))
        if h_runs is not None and a_runs is not None:
            try:
                return int(h_runs) + int(a_runs)
            except (TypeError, ValueError):
                pass
    return None


def _extract_bullpen_pitches(doc: dict, side: str) -> int:
    """Best-effort: read bullpen pitch counts when the upstream ingestion
    stored them on the match doc. Returns 0 when unknown."""
    bp = doc.get("bullpen_usage") or {}
    if isinstance(bp, dict):
        v = bp.get(f"{side}_pitches") or bp.get(side, {}).get("pitches")
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
    return 0


async def get_active_series_context(
    db: Any,
    home_team: str,
    away_team: str,
    date_str: Optional[str] = None,
    *,
    days_back: int = 4,
    model_expected_runs: Optional[float] = None,
    over_under_line: float = 9.5,
) -> dict:
    """See module docstring.

    `date_str` is the kickoff of the NEXT game (`YYYY-MM-DD`); we look
    back from there. When None, falls back to `datetime.utcnow().date()`.
    """
    empty = {
        "available":           False,
        "games_in_series":     0,
        "total_runs_avg":      None,
        "total_runs_list":     [],
        "over_rate":           None,
        "bullpen_pitches_home": 0,
        "bullpen_pitches_away": 0,
        "series_lean":         "NEUTRAL",
        "series_override":     False,
        "override_reason":     None,
    }
    if db is None or not home_team or not away_team:
        return empty
    try:
        if date_str:
            try:
                ref = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            except ValueError:
                ref = datetime.now(timezone.utc)
        else:
            ref = datetime.now(timezone.utc)
        from_ts = ref - timedelta(days=days_back)

        # We support three collections the ingestion pipeline writes to:
        #   • `finished_games` — settlement job output (when present).
        #   • `matches`         — current-day fixtures with `status=Final`.
        #   • `archived_live_matches` — the live ingester moves a match
        #     here when it finishes; the box-score lives in `live_stats`.
        collections = ["finished_games", "matches", "archived_live_matches"]
        candidates: list[dict] = []
        for coll_name in collections:
            try:
                coll = db[coll_name]
            except Exception:
                continue
            # We accept the dual `kickoff_iso` storage shape (ISO with Z
            # suffix vs +00:00) and we no longer hard-require Final
            # status — many docs are archived as soon as the live feed
            # closes them without re-stamping `status`. We filter for
            # "has a final score" in Python (via _extract_runs) instead.
            window_from = from_ts.isoformat().replace("+00:00", "")
            window_to   = ref.isoformat().replace("+00:00", "")
            query = {
                "sport": "baseball",
                "$or": [
                    {"kickoff_iso": {"$gte": window_from, "$lt": window_to}},
                    {"kickoff_iso": {"$gte": from_ts.isoformat(), "$lt": ref.isoformat()}},
                ],
            }
            try:
                async for d in coll.find(query).limit(40):
                    candidates.append(d)
            except Exception as exc:
                log.debug("active_series query on %s failed: %s", coll_name, exc)
        # Filter to this matchup
        matched = [d for d in candidates if _team_match(d, home_team, away_team)]
        if not matched:
            return empty

        runs_list = [r for r in (_extract_runs(d) for d in matched) if r is not None]
        if not runs_list:
            return empty
        avg = sum(runs_list) / len(runs_list)
        over_rate = sum(1 for r in runs_list if r > over_under_line) / len(runs_list)
        bullpen_home = max((_extract_bullpen_pitches(d, "home") for d in matched), default=0)
        bullpen_away = max((_extract_bullpen_pitches(d, "away") for d in matched), default=0)

        # ── Override rules ──
        override = False
        reason: Optional[str] = None
        lean = "NEUTRAL"
        if len(runs_list) >= 2:
            if avg > over_under_line + 2.0:
                lean = "OVER"
            elif avg < over_under_line - 2.0:
                lean = "UNDER"
            # Override when the series average violently contradicts the model.
            if model_expected_runs and avg > float(model_expected_runs) * 1.4:
                override = True
                lean = "OVER"
                reason = (f"Serie activa promedia {avg:.1f} runs vs ER "
                          f"{float(model_expected_runs):.1f} del modelo.")
            if bullpen_home > 80 or bullpen_away > 80:
                override = True
                reason = ((reason + " " if reason else "")
                          + f"Bullpens agotados (HOME {bullpen_home} pitches, "
                          f"AWAY {bullpen_away} pitches en 2 días).")

        return {
            "available":           True,
            "games_in_series":     len(runs_list),
            "total_runs_avg":      round(avg, 2),
            "total_runs_list":     runs_list,
            "over_rate":           round(over_rate, 2),
            "bullpen_pitches_home": bullpen_home,
            "bullpen_pitches_away": bullpen_away,
            "series_lean":         lean,
            "series_override":     override,
            "override_reason":     reason,
            "days_back":           days_back,
            "reference_line":      over_under_line,
        }
    except Exception as exc:
        log.warning("get_active_series_context failed: %s", exc)
        return empty
