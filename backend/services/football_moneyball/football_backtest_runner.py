"""Football Backtest Runner — populate ``football_market_results`` retroactively.

The Football DC + NB calibration loop in
``football_totals_calibration.compute_football_totals_calibration`` needs
**n ≥ 100** settled docs before it can suggest empirical ``rho`` /
``dispersion_ratio`` values. Waiting for that many real settled picks
takes weeks.

This module short-circuits the wait: it walks a date range against the
API-Sports football fixtures endpoint, reconstructs a *minimum* match
doc (final_score + competition + teams), runs the current
``statsbomb_features.compute_match_features`` with safe defaults
(rho=-0.05, ratio=1.0), and persists each settled fixture into
``football_market_results`` tagged with a separate cohort
``user_id="_slate_backtest"`` so it never pollutes the live calibration.

Why the separate cohort:
  * Backtest rows reconstruct context from finished fixtures, so the
    pregame signals (form, recent stats, injuries) are partially leaked
    by knowledge of the final outcome. Treating those rows as live picks
    would contaminate the calibration.
  * Operators can still query the backtest cohort via the dedicated
    summary endpoint or by passing ``user_id="_slate_backtest"`` to
    ``compute_football_totals_calibration``.

Public API
----------
- :func:`fetch_historical_fixtures(client, start_date, end_date)`
- :func:`build_backtest_match_doc(api_fixture)`  (pure)
- :func:`run_football_backtest(db, start_date, end_date, *, dry_run=False, user_id="_slate_backtest")`

Implementation notes
--------------------
* Reuses ``api_football.fixtures_by_date`` (already rate-limited via
  ``_APISportsLimiter``) so we honor the global API quota.
* Skips fixtures whose ``status.short`` is not in
  ``{"FT", "AET", "PEN"}`` (final results only).
* Deduplicates against ``football_market_results`` using the
  ``match_id`` + cohort ``user_id``.
* Fail-soft per fixture: a single bad fixture never aborts the run.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date as _date_t, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .. import api_football
from ..statsbomb_features import (
    compute_match_features,
    derive_offense_bucket,
    DIXON_COLES_RHO_DEFAULT,
    FOOTBALL_GOALS_DISPERSION_RATIO_DEFAULT,
)
from .football_intelligence_warehouse import (
    persist_football_market_result,
    COLL_MARKET_RESULTS,
)

log = logging.getLogger("football_backtest_runner")

# Final fixture statuses (API-Sports short codes).
_FINAL_STATUSES = {"FT", "AET", "PEN"}

# Cohort key for the backtest namespace. Must NOT collide with live ``_slate``.
DEFAULT_BACKTEST_USER_ID = "_slate_backtest"


# ─────────────────────────────────────────────────────────────────────
# Tier inference (rough, no DB hit) — used to bucket backtest docs.
# ─────────────────────────────────────────────────────────────────────
_TIER1_LEAGUE_IDS = {
    39, 140, 135, 78, 61,           # EPL, LaLiga, Serie A, Bundesliga, Ligue 1
    2, 3, 848, 4, 1,                # UCL, UEL, Conference, Euros, World Cup
}
_TIER2_LEAGUE_IDS = {
    40, 141, 136, 79, 62, 88, 94,  # 2nd divisions + Eredivisie/Primeira
    253, 71, 128,                   # MLS, Brasileirão Serie A, LPF Argentina
}


def _infer_league_tier(league_id: int | None) -> str:
    if league_id is None:
        return "UNKNOWN_LEAGUE"
    try:
        lid = int(league_id)
    except (TypeError, ValueError):
        return "UNKNOWN_LEAGUE"
    if lid in _TIER1_LEAGUE_IDS:
        return "TIER1"
    if lid in _TIER2_LEAGUE_IDS:
        return "TIER2"
    return "TIER3"


# ─────────────────────────────────────────────────────────────────────
# API-Sports fetchers
# ─────────────────────────────────────────────────────────────────────
async def fetch_historical_fixtures(
    client: httpx.AsyncClient,
    start_date: _date_t,
    end_date: _date_t,
) -> list[dict]:
    """Iterate dates in [start_date, end_date] inclusive and return all
    final fixtures. The dates are expected to be in the past (otherwise
    you get NS/TBD entries that this runner skips).

    Returns a list of API-Sports fixture dicts.
    """
    if start_date > end_date:
        return []
    out: list[dict] = []
    d = start_date
    while d <= end_date:
        try:
            res = await api_football.fixtures_by_date(client, d.isoformat())
            for f in res:
                short = ((f.get("fixture") or {}).get("status") or {}).get("short")
                if short in _FINAL_STATUSES:
                    out.append(f)
        except Exception as exc:
            log.debug("fixtures_by_date(%s) failed: %s", d.isoformat(), exc)
        await asyncio.sleep(0.15)  # gentle pacing on top of the limiter
        d += timedelta(days=1)
    return out


# ─────────────────────────────────────────────────────────────────────
# Match-doc reconstruction (pure)
# ─────────────────────────────────────────────────────────────────────
def build_backtest_match_doc(api_fixture: dict | None) -> dict | None:
    """Reconstruct a minimal football match doc from an API-Sports
    fixture so we can run ``compute_match_features`` against it.

    Returns ``None`` when the fixture lacks enough data (no teams or no
    goals object).
    """
    if not isinstance(api_fixture, dict):
        return None
    fixture = api_fixture.get("fixture") or {}
    teams   = api_fixture.get("teams") or {}
    league  = api_fixture.get("league") or {}
    goals   = api_fixture.get("goals") or {}

    home_id = (teams.get("home") or {}).get("id")
    away_id = (teams.get("away") or {}).get("id")
    if not home_id or not away_id:
        return None
    if goals.get("home") is None or goals.get("away") is None:
        return None

    return {
        "match_id":  str(fixture.get("id") or f"{home_id}-{away_id}-{fixture.get('date')}"),
        "league": {
            "id":   league.get("id"),
            "name": league.get("name"),
        },
        "home_team": {
            "id":      home_id,
            "name":    (teams.get("home") or {}).get("name"),
            "context": {},  # no historical context in the backtest path
        },
        "away_team": {
            "id":      away_id,
            "name":    (teams.get("away") or {}).get("name"),
            "context": {},
        },
        "final_score": {
            "home": int(goals.get("home") or 0),
            "away": int(goals.get("away") or 0),
        },
        "_backtest":  True,
    }


# ─────────────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────────────
async def _is_already_persisted(db, *, match_id: str, user_id: str) -> bool:
    if db is None:
        return False
    try:
        existing = await db[COLL_MARKET_RESULTS].find_one({
            "match_id": str(match_id),
            "user_id":  user_id,
        })
        return existing is not None
    except Exception:
        return False


def _decide_outcome(final_score: dict, line: float = 2.5) -> tuple[str, bool]:
    """Return (outcome_label, won_under). Backtest treats Under 2.5 as
    the canonical market because it's the protected baseline used by the
    market selection layer."""
    total = int(final_score.get("home", 0)) + int(final_score.get("away", 0))
    won = total < line
    return ("won" if won else "lost", won)


# ─────────────────────────────────────────────────────────────────────
# Public runner
# ─────────────────────────────────────────────────────────────────────
async def run_football_backtest(
    db,
    start_date: _date_t,
    end_date: _date_t,
    *,
    dry_run: bool = False,
    user_id: str = DEFAULT_BACKTEST_USER_ID,
    line: float = 2.5,
) -> dict:
    """Persist final fixtures from ``start_date..end_date`` into
    ``football_market_results`` for calibration purposes.

    Args:
        db:          Motor handle.
        start_date:  inclusive (UTC date).
        end_date:    inclusive (UTC date).
        dry_run:     if True, no writes are issued; the function only
                     returns the would-be summary.
        user_id:     cohort key (default ``"_slate_backtest"``).
        line:        Under line used to decide outcome (default 2.5).
    """
    audit: dict[str, Any] = {
        "started_at":    datetime.now(timezone.utc).isoformat(),
        "start_date":    start_date.isoformat(),
        "end_date":      end_date.isoformat(),
        "user_id":       user_id,
        "dry_run":       bool(dry_run),
        "fixtures_seen": 0,
        "fixtures_kept": 0,
        "persisted":     0,
        "skipped_dup":   0,
        "errors":        [],
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            fixtures = await fetch_historical_fixtures(client, start_date, end_date)
    except Exception as exc:
        audit["errors"].append(f"fetch_historical_fixtures: {exc}")
        return audit

    audit["fixtures_seen"] = len(fixtures)

    for f in fixtures:
        try:
            match = build_backtest_match_doc(f)
            if not match:
                continue
            audit["fixtures_kept"] += 1

            # Skip dup at cohort level so re-running the script is safe.
            if not dry_run and await _is_already_persisted(
                db, match_id=match["match_id"], user_id=user_id,
            ):
                audit["skipped_dup"] += 1
                continue

            # Inject calibration defaults (we deliberately do NOT use the
            # live calibration here — backtest must measure the engine
            # under its baseline behaviour).
            match["_dc_rho"] = DIXON_COLES_RHO_DEFAULT
            match["_goals_dispersion_ratio"] = FOOTBALL_GOALS_DISPERSION_RATIO_DEFAULT

            features = compute_match_features(match) or {}
            lambda_total = features.get("lambda_total")
            offense_bucket = derive_offense_bucket(lambda_total)

            outcome, won = _decide_outcome(match["final_score"], line=line)
            market = f"Under {line:.1f}"

            if dry_run:
                audit["persisted"] += 1
                continue

            persisted = await persist_football_market_result(
                db,
                match_id=match["match_id"],
                user_id=user_id,
                market=market,
                selection=market,
                odds=None,
                pattern_keys=[],
                stake=1.0,
                won=won,
                payout=0.0,
                result=outcome,
                final_score=match["final_score"],
                snapshot_ref={"source": "backtest"},
                league_tier=_infer_league_tier((match.get("league") or {}).get("id")),
                offense_bucket=offense_bucket,
                lambda_total=lambda_total,
                lambda_home=features.get("lambda_home"),
                lambda_away=features.get("lambda_away"),
                dc_rho_used=features.get("dc_rho_used"),
                goals_dispersion_ratio=features.get("goals_dispersion_ratio"),
                p_under_2_5_poisson=features.get("p_under_2_5_poisson"),
                p_under_3_5_poisson=features.get("p_under_3_5_poisson"),
                p_under_2_5_dc_nb=features.get("p_under_2_5"),
                p_under_3_5_dc_nb=features.get("p_under_3_5"),
                dc_nb_delta_2_5_pts=features.get("dc_nb_delta_2_5_pts"),
                dc_nb_delta_3_5_pts=features.get("dc_nb_delta_3_5_pts"),
            )
            if persisted:
                audit["persisted"] += 1
        except Exception as exc:
            audit["errors"].append(f"{f.get('fixture', {}).get('id')}: {exc}")
            continue

    audit["finished_at"] = datetime.now(timezone.utc).isoformat()
    return audit


__all__ = [
    "fetch_historical_fixtures",
    "build_backtest_match_doc",
    "run_football_backtest",
    "DEFAULT_BACKTEST_USER_ID",
]
