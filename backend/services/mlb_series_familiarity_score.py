"""MLB Series Familiarity Score — Priority 3.

When two teams play each other multiple times in a short window
(typically a 3-game series, sometimes back-to-back series within 15
days), their pitchers, bullpens and lineups become "familiar". This
familiarity can subtly raise late-game traffic (hitters time the
arsenal, bullpen pitchers get exposed twice in a series, etc.).

This module computes a continuous 0-100 score that summarises that
familiarity using the **MLB Stats API schedule** as the source of
truth. We deliberately AVOID using ``last_3_totals_average`` (or any
simple H2H average) as the primary signal — that metric is noisy and
gameflow-dependent.

Score components (sum to 100):
    same_teams_last_3_days   : 0-30   (recency dominates)
    same_teams_last_5_days   : 0-25
    same_teams_last_15_days  : 0-20
    bullpen_usage_interaction: 0-15   (only when usage is high)
    starter_repeat_exposure  : 0-10   (back-compat with caller flag)

Buckets:
    0-39   LOW_SERIES_FAMILIARITY
    40-69  MEDIUM_SERIES_FAMILIARITY
    70-100 HIGH_SERIES_FAMILIARITY

The module is fail-soft: missing schedule data, network errors or
caller-side fetch failures simply downgrade the score and add the
``SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT`` reason code.

Caching:
    Results are cached for 6 hours in the optional ``cache_get`` /
    ``cache_set`` callables passed by the caller, keyed by:
        (home_team_id, away_team_id, game_date)

The MLB Stats API client is injected (DI) so the module remains pure
and unit-testable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

ENGINE_VERSION = "mlb_series_familiarity.1"

# ── Buckets ─────────────────────────────────────────────────────────
BUCKET_LOW    = "LOW_SERIES_FAMILIARITY"
BUCKET_MEDIUM = "MEDIUM_SERIES_FAMILIARITY"
BUCKET_HIGH   = "HIGH_SERIES_FAMILIARITY"

# ── Reason codes ────────────────────────────────────────────────────
RC_SERIES_FAMILIARITY_DETECTED          = "SERIES_FAMILIARITY_DETECTED"
RC_RECENT_REPEAT_MATCHUP                = "RECENT_REPEAT_MATCHUP"
RC_SERIES_FAMILIARITY_TRAFFIC_BOOST     = "SERIES_FAMILIARITY_TRAFFIC_BOOST"
RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT = "SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT"
RC_SERIES_FAMILIARITY_CAPPED            = "SERIES_FAMILIARITY_CAPPED"
RC_BULLPEN_FAMILIARITY_TRAFFIC_BOOST    = "BULLPEN_FAMILIARITY_TRAFFIC_BOOST"

# ── Cap on the additive boost to λ7-9 (in expected runs) ────────────
MAX_LAMBDA_BOOST = 0.35


def _unavailable(reason: str) -> dict:
    """Structured fail-soft response."""
    return {
        "available":               False,
        "engine_version":          ENGINE_VERSION,
        "series_familiarity_score": None,
        "bucket":                  None,
        "components":              {},
        "reason":                  reason,
        "reason_codes":            [RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT],
    }


def _bucket_of(score: float) -> str:
    if score >= 70:
        return BUCKET_HIGH
    if score >= 40:
        return BUCKET_MEDIUM
    return BUCKET_LOW


def _parse_iso_date(d: Any) -> Optional[datetime]:
    """Parse to a tz-aware datetime in UTC. Falls back to naive→UTC."""
    if not d:
        return None
    if isinstance(d, datetime):
        return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(d).replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _count_same_opponent_games(
    schedule: list[dict],
    target_date: datetime,
    days_back: int,
    home_team_id: Any,
    away_team_id: Any,
) -> int:
    """Count games where these two teams faced each other within the
    last ``days_back`` days prior to ``target_date``."""
    cutoff = target_date - timedelta(days=days_back)
    count = 0
    target_pair = {str(home_team_id), str(away_team_id)}
    for g in schedule or []:
        gd = _parse_iso_date(g.get("gameDate") or g.get("game_date"))
        if not gd:
            continue
        if not (cutoff <= gd < target_date):
            continue
        h = (g.get("teams") or {}).get("home", {}).get("team", {}).get("id") or g.get("home_team_id")
        a = (g.get("teams") or {}).get("away", {}).get("team", {}).get("id") or g.get("away_team_id")
        if h is None or a is None:
            continue
        if {str(h), str(a)} == target_pair:
            count += 1
    return count


def compute_series_familiarity_score(
    *,
    home_team_id: Any,
    away_team_id: Any,
    game_date: Any,
    schedule: Optional[list[dict]] = None,
    bullpen_usage_5d: Optional[float] = None,
    repeated_starter_exposure: Optional[bool] = None,
    repeated_bullpen_exposure: Optional[bool] = None,
) -> dict:
    """Compute the continuous 0-100 familiarity score.

    Args:
        home_team_id / away_team_id : team identifiers (any type, str-cast).
        game_date : ISO datetime of the upcoming game (or a date string).
        schedule  : a list of dicts from MLB Stats API ``/schedule``.
                    Each item must include ``gameDate`` and ``teams.home /
                    teams.away`` with team IDs. Caller hydrates.
        bullpen_usage_5d : optional 0..1 — bullpen usage ratio over 5 days.
        repeated_starter_exposure : optional flag (back-compat).
        repeated_bullpen_exposure : optional flag (back-compat).

    Returns: dict with ``series_familiarity_score`` (0-100), ``bucket``,
    ``components``, ``reason_codes``, ``available``.
    """
    target = _parse_iso_date(game_date)
    if target is None:
        return _unavailable("invalid_game_date")
    if home_team_id is None or away_team_id is None:
        return _unavailable("missing_team_ids")
    if schedule is None:
        return _unavailable("schedule_not_provided")

    n3  = _count_same_opponent_games(schedule, target, 3,  home_team_id, away_team_id)
    n5  = _count_same_opponent_games(schedule, target, 5,  home_team_id, away_team_id)
    n15 = _count_same_opponent_games(schedule, target, 15, home_team_id, away_team_id)

    # Scoring scale.
    # n3: 0 → 0pts, 1 → 18pts, 2 → 26pts, 3+ → 30pts.
    pts_3  = min(30.0, [0.0, 18.0, 26.0, 30.0][min(3, n3)])
    # n5: 0 → 0pts, 1 → 12pts, 2 → 18pts, 3+ → 25pts.
    pts_5  = min(25.0, [0.0, 12.0, 18.0, 25.0][min(3, n5)])
    # n15: 0 → 0pts, 1 → 8pts, 2 → 12pts, 3 → 16pts, 4+ → 20pts.
    pts_15 = min(20.0, [0.0, 8.0, 12.0, 16.0, 20.0][min(4, n15)])

    # Bullpen usage interaction (0-15 pts). Only meaningful when usage is
    # high (>0.55 ⇒ bullpen has been worked hard recently).
    pts_bp = 0.0
    if bullpen_usage_5d is not None and bullpen_usage_5d > 0.55:
        pts_bp = min(15.0, (bullpen_usage_5d - 0.55) / 0.45 * 15.0)

    # Starter/bullpen repeat exposure flags (0-10 pts, capped at 10).
    pts_repeat = 0.0
    if repeated_starter_exposure:
        pts_repeat += 6.0
    if repeated_bullpen_exposure:
        pts_repeat += 4.0
    pts_repeat = min(10.0, pts_repeat)

    score = round(min(100.0, pts_3 + pts_5 + pts_15 + pts_bp + pts_repeat), 2)
    bucket = _bucket_of(score)

    reason_codes: list[str] = []
    if score >= 40:
        reason_codes.append(RC_SERIES_FAMILIARITY_DETECTED)
    if n3 >= 1 or n5 >= 2:
        reason_codes.append(RC_RECENT_REPEAT_MATCHUP)
    if pts_bp >= 5 and score >= 40:
        reason_codes.append(RC_BULLPEN_FAMILIARITY_TRAFFIC_BOOST)
    if score < 40:
        reason_codes.append(RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT)

    return {
        "available":                True,
        "engine_version":           ENGINE_VERSION,
        "series_familiarity_score": score,
        "bucket":                   bucket,
        "components": {
            "same_teams_last_3_days":    round(pts_3, 2),
            "same_teams_last_5_days":    round(pts_5, 2),
            "same_teams_last_15_days":   round(pts_15, 2),
            "bullpen_usage_interaction": round(pts_bp, 2),
            "starter_repeat_exposure":   round(pts_repeat, 2),
        },
        "counts": {
            "same_teams_last_3_days":  n3,
            "same_teams_last_5_days":  n5,
            "same_teams_last_15_days": n15,
        },
        "reason_codes": reason_codes,
    }


def evaluate_lambda_boost(
    *,
    series_familiarity_score: Optional[float],
    bullpen_fatigue: Optional[float],
    normalized_traffic: Optional[float],
) -> dict:
    """Compute the additive λ7-9 boost (capped at +0.35 runs) given the
    familiarity score and the interaction with bullpen fatigue / traffic.

    Series familiarity ALONE never inflates λ7-9; it only amplifies an
    existing risk. Returns ``{boost, reason_codes}``.
    """
    if series_familiarity_score is None or series_familiarity_score < 40:
        return {"boost": 0.0, "reason_codes": [RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT]}

    norm_series = max(0.0, min(1.0, series_familiarity_score / 100.0))
    norm_fatigue = max(0.0, min(1.0, bullpen_fatigue or 0.0))
    norm_traffic = max(0.0, min(1.0, normalized_traffic or 0.0))
    boost_raw = norm_series * max(norm_fatigue, norm_traffic)
    boost = round(min(MAX_LAMBDA_BOOST, boost_raw), 4)
    reason_codes = [RC_SERIES_FAMILIARITY_DETECTED]
    if boost >= MAX_LAMBDA_BOOST - 0.001:
        reason_codes.append(RC_SERIES_FAMILIARITY_CAPPED)
    if boost > 0.05:
        reason_codes.append(RC_SERIES_FAMILIARITY_TRAFFIC_BOOST)
    return {"boost": boost, "reason_codes": reason_codes}


async def hydrate_series_familiarity(
    *,
    home_team_id: Any,
    away_team_id: Any,
    game_date: Any,
    fetch_schedule: Callable,  # async (start_date, end_date) -> list[dict]
    bullpen_usage_5d: Optional[float] = None,
    cache_get: Optional[Callable] = None,
    cache_set: Optional[Callable] = None,
    cache_ttl_seconds: int = 6 * 3600,
) -> dict:
    """Caching wrapper: hydrate a 15-day schedule window and compute the
    score. Fail-soft on any error.

    Cache key: ``(home_team_id, away_team_id, game_date_iso)``.
    """
    target = _parse_iso_date(game_date)
    if target is None:
        return _unavailable("invalid_game_date")

    cache_key = f"sfam:{home_team_id}:{away_team_id}:{target.date().isoformat()}"
    if cache_get is not None:
        try:
            cached = await cache_get(cache_key)
            if cached is not None:
                return cached
        except Exception:
            pass

    try:
        # Pull 16 days of schedule so the 15-day window has buffer.
        start_d = (target - timedelta(days=16)).date().isoformat()
        end_d   = target.date().isoformat()
        schedule = await fetch_schedule(start_d, end_d)
    except Exception:
        return _unavailable("schedule_fetch_failed")

    out = compute_series_familiarity_score(
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        game_date=target,
        schedule=schedule or [],
        bullpen_usage_5d=bullpen_usage_5d,
    )

    if cache_set is not None:
        try:
            await cache_set(cache_key, out, ttl=cache_ttl_seconds)
        except Exception:
            pass

    return out


__all__ = [
    "ENGINE_VERSION",
    "BUCKET_LOW",
    "BUCKET_MEDIUM",
    "BUCKET_HIGH",
    "MAX_LAMBDA_BOOST",
    "RC_SERIES_FAMILIARITY_DETECTED",
    "RC_RECENT_REPEAT_MATCHUP",
    "RC_SERIES_FAMILIARITY_TRAFFIC_BOOST",
    "RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT",
    "RC_SERIES_FAMILIARITY_CAPPED",
    "RC_BULLPEN_FAMILIARITY_TRAFFIC_BOOST",
    "compute_series_familiarity_score",
    "evaluate_lambda_boost",
    "hydrate_series_familiarity",
]
