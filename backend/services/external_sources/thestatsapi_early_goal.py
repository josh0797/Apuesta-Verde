"""TheStatsAPI Early-Goal Adapter — shotmap-based 0–30 metrics.

Why this exists
---------------
API-Sports gives us goal events with minute timestamps, but the
shotmap from TheStatsAPI (`/football/matches/{id}/shotmap`) carries
minute-stamped **xG** + shots data per event, which lets us derive a
richer 0–30 minute profile (first_30_xg_for / first_30_shots_on_target
etc.) than goals-only metrics can provide.

Design principles (NON-NEGOTIABLE):
  * Fail-soft total — every helper returns None / a neutral dict when
    inputs are missing or the API call fails.
  * Read-only — no IO except the explicit TheStatsAPI request and
    optional Mongo cache.
  * Provenance-aware — every aggregated profile carries
    ``source`` and a flag ``xg_available``.
  * Never used to FORCE Overs. Output feeds into
    ``football_over_support.calculate_football_over_support`` as one
    among several signals.

Public API
----------
  * ``fetch_match_shotmap(client, match_id, db=None)``
  * ``derive_early_goal_0_30_from_shotmap(shotmap, home_team_id, away_team_id)``
  * ``aggregate_team_early_goal_profile_from_matches(per_match_profiles, team_id)``
  * ``merge_early_goal_sources(api_sports_profile, thestatsapi_profile)``
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .thestatsapi_client import _request, is_enabled  # type: ignore

log = logging.getLogger("thestatsapi_early_goal")

_EARLY_30_MAX_MIN = 30


# ─────────────────────────────────────────────────────────────────────
# Shotmap fetch (read-only HTTP + optional Mongo cache key)
# ─────────────────────────────────────────────────────────────────────
async def fetch_match_shotmap(
    client: httpx.AsyncClient,
    match_id: int | str,
    *,
    db=None,
) -> Optional[list[dict]]:
    """Return the shotmap list (minute-stamped shot events) or None.

    Each event SHOULD contain (best-effort): ``minute``, ``team_id``,
    ``is_goal`` (bool), ``xg`` (float), ``on_target`` (bool),
    ``big_chance`` (bool). The adapter is tolerant of missing fields.
    """
    if not is_enabled():
        return None
    try:
        data = await _request(
            client, "GET", f"/football/matches/{match_id}/shotmap",
        )
        if not isinstance(data, dict):
            return None
        if "data" in data and isinstance(data["data"], list):
            return data["data"]
        if "shots" in data and isinstance(data["shots"], list):
            return data["shots"]
        if "shotmap" in data and isinstance(data["shotmap"], list):
            return data["shotmap"]
        return None
    except Exception as exc:
        log.debug("fetch_match_shotmap(%s) failed: %s", match_id, exc)
        return None


# ─────────────────────────────────────────────────────────────────────
# Per-match derivation (pure)
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def _minute_of(ev: dict) -> int | None:
    m = ev.get("minute")
    if m is None:
        m = (ev.get("time") or {}).get("elapsed")
    try:
        return int(m)
    except (TypeError, ValueError):
        return None


def derive_early_goal_0_30_from_shotmap(
    shotmap: list[dict] | None,
    *,
    home_team_id: int | None,
    away_team_id: int | None,
) -> Optional[dict]:
    """Per-match 0–30 digest from a shotmap event list.

    Returns ``None`` when the shotmap is empty or unusable. Otherwise a
    dict with per-side counters that ``aggregate_team_early_goal_profile_from_matches``
    can sum into a team profile.
    """
    if not isinstance(shotmap, list) or not shotmap:
        return None

    home: dict[str, Any] = _empty_match_side()
    away: dict[str, Any] = _empty_match_side()

    for ev in shotmap:
        if not isinstance(ev, dict):
            continue
        minute = _minute_of(ev)
        if minute is None:
            continue
        team_id = ev.get("team_id") or (ev.get("team") or {}).get("id")
        try:
            team_id = int(team_id) if team_id is not None else None
        except (TypeError, ValueError):
            team_id = None
        if team_id is None:
            continue

        if home_team_id is not None and team_id == int(home_team_id):
            target = home
        elif away_team_id is not None and team_id == int(away_team_id):
            target = away
        else:
            continue

        in_window = minute <= _EARLY_30_MAX_MIN
        is_goal = bool(ev.get("is_goal") or ev.get("goal") or (ev.get("type") or "").lower() == "goal")
        on_target = bool(ev.get("on_target") or ev.get("shot_on_target"))
        big_chance = bool(ev.get("big_chance") or ev.get("big_chance_proxy"))
        xg = _f(ev.get("xg") or ev.get("xG"))

        target["shots_total"] += 1
        if on_target:
            target["shots_on_target"] += 1
        if big_chance:
            target["big_chances"] += 1
        if xg is not None:
            target["xg_total"] += xg
        if is_goal:
            target["goals_total"] += 1

        if in_window:
            target["shots_0_30"] += 1
            if on_target:
                target["shots_on_target_0_30"] += 1
            if big_chance:
                target["big_chances_0_30"] += 1
            if xg is not None:
                target["xg_0_30"] += xg
            if is_goal:
                target["goals_0_30"] += 1

    # If everything is zero this match contributes nothing.
    if (home["shots_total"] + away["shots_total"]) == 0:
        return None

    return {
        "home":          home,
        "away":          away,
        "home_team_id":  home_team_id,
        "away_team_id":  away_team_id,
        "xg_available":  (home["xg_total"] + away["xg_total"]) > 0,
    }


def _empty_match_side() -> dict:
    return {
        "shots_total":         0,
        "shots_on_target":     0,
        "big_chances":         0,
        "xg_total":            0.0,
        "goals_total":         0,
        "shots_0_30":          0,
        "shots_on_target_0_30": 0,
        "big_chances_0_30":    0,
        "xg_0_30":             0.0,
        "goals_0_30":          0,
    }


# ─────────────────────────────────────────────────────────────────────
# Aggregate to team profile (pure)
# ─────────────────────────────────────────────────────────────────────
def aggregate_team_early_goal_profile_from_matches(
    per_match: list[dict],
    *,
    team_id: int,
) -> Optional[dict]:
    """Aggregate per-match shotmap derivations into a team profile.

    Returns None when nothing aggregatable was passed. Otherwise a dict
    with the canonical 0–30 metric names so it can be merged with the
    API-Sports derived profile via ``merge_early_goal_sources``.
    """
    if not isinstance(per_match, list) or not per_match or team_id is None:
        return None

    n = 0
    gf_total = ga_total = 0
    gf_0_30 = ga_0_30 = 0
    fixtures_with_scored_0_30   = 0
    fixtures_with_conceded_0_30 = 0
    fixtures_with_any_0_30      = 0
    xg_for_0_30 = xg_against_0_30 = 0.0
    shots_0_30 = sot_0_30 = bc_0_30 = 0
    xg_present_count = 0

    tid = int(team_id)

    for m in per_match:
        if not isinstance(m, dict):
            continue
        home = m.get("home") or {}
        away = m.get("away") or {}
        h_id = m.get("home_team_id")
        a_id = m.get("away_team_id")
        if h_id is not None and int(h_id) == tid:
            mine, opp = home, away
        elif a_id is not None and int(a_id) == tid:
            mine, opp = away, home
        else:
            continue
        n += 1
        gf_total += int(mine.get("goals_total") or 0)
        ga_total += int(opp.get("goals_total") or 0)
        gf_0_30  += int(mine.get("goals_0_30") or 0)
        ga_0_30  += int(opp.get("goals_0_30") or 0)
        if int(mine.get("goals_0_30") or 0) > 0:
            fixtures_with_scored_0_30 += 1
        if int(opp.get("goals_0_30") or 0) > 0:
            fixtures_with_conceded_0_30 += 1
        if (int(mine.get("goals_0_30") or 0) + int(opp.get("goals_0_30") or 0)) > 0:
            fixtures_with_any_0_30 += 1
        xg_for_0_30     += float(mine.get("xg_0_30") or 0.0)
        xg_against_0_30 += float(opp.get("xg_0_30") or 0.0)
        shots_0_30 += int(mine.get("shots_0_30") or 0)
        sot_0_30   += int(mine.get("shots_on_target_0_30") or 0)
        bc_0_30    += int(mine.get("big_chances_0_30") or 0)
        if m.get("xg_available"):
            xg_present_count += 1

    if n == 0 or (gf_total + ga_total) == 0:
        return None

    def _ratio(num, den):
        return round(num / den, 3) if den > 0 else None

    return {
        "early_goal_30_pct":              _ratio(gf_0_30, gf_total),
        "early_concede_30_pct":           _ratio(ga_0_30, ga_total),
        "team_scored_0_30_pct":           _ratio(fixtures_with_scored_0_30, n),
        "team_conceded_0_30_pct":         _ratio(fixtures_with_conceded_0_30, n),
        "first_30_goal_presence_pct":     _ratio(fixtures_with_any_0_30, n),
        "goals_for_0_30":                 gf_0_30,
        "goals_against_0_30":             ga_0_30,
        "first_30_xg_for":                round(xg_for_0_30, 2),
        "first_30_xg_against":            round(xg_against_0_30, 2),
        "first_30_shots":                 shots_0_30,
        "first_30_shots_on_target":       sot_0_30,
        "first_30_big_chance_proxy":      bc_0_30,
        "sample_size":                    n,
        "xg_available":                   xg_present_count >= max(1, n // 2),
        "source":                         "thestatsapi_shotmap",
        "fetched_at":                     datetime.now(timezone.utc).isoformat(),
        "data_quality":                   (
            "strong" if n >= 10 else ("usable" if n >= 7 else ("thin" if n >= 4 else "missing"))
        ),
    }


# ─────────────────────────────────────────────────────────────────────
# Merge with API-Sports profile (pure)
# ─────────────────────────────────────────────────────────────────────
def merge_early_goal_sources(
    api_sports_profile: dict | None,
    thestatsapi_profile: dict | None,
) -> Optional[dict]:
    """Merge a derived_early_goal (API-Sports) profile with a TheStatsAPI
    shotmap-derived one.

    Priority rules:
      * TheStatsAPI WINS when ``xg_available=True`` AND its data_quality
        is at least ``usable`` (i.e. n>=7).
      * Otherwise API-Sports wins (since it usually has a longer
        sample window).
      * Either way, blanks in the winner are filled from the loser.
      * Provenance is preserved in ``source`` and ``secondary_source``.
    """
    if not api_sports_profile and not thestatsapi_profile:
        return None
    if not api_sports_profile:
        return thestatsapi_profile
    if not thestatsapi_profile:
        return api_sports_profile

    rank = {"strong": 3, "usable": 2, "thin": 1, "missing": 0}
    ts_q = rank.get((thestatsapi_profile or {}).get("data_quality"), 0)
    as_q = rank.get((api_sports_profile or {}).get("data_quality"), 0)

    prefer_ts = bool(
        thestatsapi_profile.get("xg_available")
        and ts_q >= 2
    ) or ts_q > as_q

    base, extra = (
        (dict(thestatsapi_profile), api_sports_profile)
        if prefer_ts
        else (dict(api_sports_profile), thestatsapi_profile)
    )
    for k, v in (extra or {}).items():
        if base.get(k) is None and v is not None:
            base[k] = v
    base["secondary_source"] = (extra or {}).get("source")
    base["merged_at"] = datetime.now(timezone.utc).isoformat()
    return base


__all__ = [
    "fetch_match_shotmap",
    "derive_early_goal_0_30_from_shotmap",
    "aggregate_team_early_goal_profile_from_matches",
    "merge_early_goal_sources",
]
