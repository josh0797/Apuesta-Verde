"""MLB Backtest Runner — populate ``mlb_run_evaluations`` retroactively.

The Negative-Binomial feedback loop in
``mlb_run_evaluations_summary._compute_totals_dispersion_calibration``
needs at least 30 settled evaluations (``result != "pending"``) before
it can suggest an empirical ``dispersion_ratio``. Waiting for that many
real pregame slate runs takes weeks.

This module short-circuits the wait: it walks a date range against the
public MLB Stats API, reconstructs the *minimum* ``scoring_ctx`` needed
by ``over_under_predictor``, runs the **current engine** through
``totals_probability`` (NB by default), and persists each settled game
into ``mlb_run_evaluations`` tagged ``_source: "backtest"``.

Public API
----------
- :func:`fetch_historical_schedule(start_date, end_date)` — async
- :func:`fetch_game_pitchers(game_pk)` — async
- :func:`build_backtest_scoring_ctx(home_pitcher, away_pitcher, home_team_name, away_team_name)`
- :func:`run_backtest(db, start_date, end_date, *, dry_run=False, user_id="_slate_backtest")` — async

Implementation notes
--------------------
* Uses ``httpx`` (already a dependency) with a single shared ``AsyncClient``
  per call so connection pooling halves the wall-clock.
* Rate limiting via ``asyncio.sleep(0.15)`` between boxscore calls — the
  MLB Stats API has no auth but does throttle aggressive bursts.
* Deduplication: before persisting we hit ``query_run_evaluations`` and
  skip when a document with the same ``match_id`` already exists in the
  backtest namespace.
* The line is *approximated* (see :func:`_approximate_book_line`) when
  the real closing line is not available. The doc carries
  ``_line_approximated: True`` so downstream filters can drop those
  rows when calibrating against real closing lines.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date as _date_t, datetime, timezone
from typing import Any, Optional

import httpx

from .mlb_pregame_analytics import over_under_predictor
from .mlb_pregame_analytics_v2 import totals_probability
from .mlb_run_storage import (
    build_run_evaluation_document,
    query_run_evaluations,
    update_run_evaluation_result,
)

log = logging.getLogger("mlb_backtest_runner")

# ─────────────────────────────────────────────────────────────────────
# Constants — MLB Stats API
# ─────────────────────────────────────────────────────────────────────
_STATSAPI_BASE       = "https://statsapi.mlb.com/api/v1"
_SCHEDULE_URL        = f"{_STATSAPI_BASE}/schedule"
_BOXSCORE_URL_TPL    = f"{_STATSAPI_BASE}/game/{{game_pk}}/boxscore"

# HTTP timeouts — generous, the Stats API can be slow on big payloads.
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)

# Rate-limit between boxscore fetches. The schedule call is one-shot so
# only the per-game boxscore loop needs spacing.
_BOXSCORE_DELAY_S = 0.15

# Date-range guardrail. The Stats API will happily return huge ranges but
# we limit the run to keep memory + Mongo footprint reasonable.
_MAX_RANGE_DAYS = 92  # ~3 months

# ETag-style namespace for backtest runs. Picks from the real slate live
# under "_slate" — this keeps the two cohorts queryable independently.
DEFAULT_BACKTEST_USER_ID = "_slate_backtest"
BACKTEST_SOURCE_TAG      = "backtest"


# ─────────────────────────────────────────────────────────────────────
# Public helpers (pure / testable in isolation)
# ─────────────────────────────────────────────────────────────────────
def _pitcher_score_from_era(p: Optional[dict]) -> int:
    """Convert a pitcher dict to a 0..100 quality score using the same
    linear ERA mapping the rest of the engine uses.

    score = 100 − ((ERA − 2.5) / 4.0) × 100, clamped to [0, 100].
    Returns 50 (neutral) when the pitcher dict is missing or ERA is None.
    """
    if not isinstance(p, dict):
        return 50
    era = p.get("era")
    if era is None:
        return 50
    try:
        e = float(era)
    except (TypeError, ValueError):
        return 50
    raw = 100.0 - ((e - 2.5) / 4.0) * 100.0
    return int(max(0, min(100, round(raw))))


def build_backtest_scoring_ctx(
    home_pitcher: Optional[dict],
    away_pitcher: Optional[dict],
    home_team_name: Optional[str],
    away_team_name: Optional[str],
) -> dict:
    """Construct the minimal ``scoring_ctx`` shape consumed by
    ``over_under_predictor`` for a historical replay.

    We intentionally use neutral 50/60 baselines for bullpen / offense /
    park / weather because the boxscore endpoint does not return those
    splits. The pitcher quality is the only signal we can recover with
    confidence from the historical record.
    """
    return {
        "home_pitcher_quality": {"score": _pitcher_score_from_era(home_pitcher)},
        "away_pitcher_quality": {"score": _pitcher_score_from_era(away_pitcher)},
        "bullpen":              {"score": 60},   # neutral default
        "offense_home":         {"score": 50},
        "offense_away":         {"score": 50},
        "park":                 {"park_runs_mult": 1.0, "weather_score": 50},
        "momentum_score":       50,
        "home_team":            home_team_name,
        "away_team":            away_team_name,
        "home_pitcher_name":    (home_pitcher or {}).get("name"),
        "away_pitcher_name":    (away_pitcher or {}).get("name"),
        "home_pitcher_stats":   home_pitcher or {},
        "away_pitcher_stats":   away_pitcher or {},
    }


def _approximate_book_line(expected_runs: float, game_date: Optional[str] = None) -> float:
    """Heuristic book total when the historical closing line is not
    available. Tuned to the 2025-2026 MLB regular season distribution
    (mean ~9.0, ladder 8.0..10.5).

    NOT meant to be a precise closing line — only a defensible anchor
    so the engine has *something* to grade against. Calls that rely on
    a real closing line should filter ``_line_approximated == False``.
    """
    try:
        er = float(expected_runs)
    except (TypeError, ValueError):
        er = 9.0
    if er < 7.5:
        return 8.5
    if er < 8.5:
        return 9.0
    if er < 9.5:
        return 9.5
    return 10.0


# ─────────────────────────────────────────────────────────────────────
# Stats API I/O
# ─────────────────────────────────────────────────────────────────────
async def fetch_historical_schedule(
    start_date: str,
    end_date: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict]:
    """Return a list of finished MLB games between ``start_date`` and
    ``end_date`` (inclusive). Each row::

        {
            "game_pk":    int,
            "home_team":  str,
            "away_team":  str,
            "home_runs":  int,
            "away_runs":  int,
            "game_date":  "YYYY-MM-DD",
        }

    Only games with ``status.abstractGameState == "Final"`` are included.
    Cancelled / postponed / suspended games are silently skipped.
    Returns an empty list on any HTTP / parsing error (fail-soft).
    """
    params = {
        "startDate": start_date,
        "endDate":   end_date,
        "sportId":   1,
        "hydrate":   "linescore,team",
    }
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True)
    try:
        try:
            r = await client.get(_SCHEDULE_URL, params=params)
            r.raise_for_status()
        except Exception as exc:
            log.warning("fetch_historical_schedule HTTP failed (%s..%s): %s",
                         start_date, end_date, exc)
            return []
        try:
            payload = r.json() or {}
        except Exception as exc:
            log.warning("fetch_historical_schedule JSON parse failed: %s", exc)
            return []

        out: list[dict] = []
        for day in (payload.get("dates") or []):
            game_date = day.get("date") or ""
            for game in (day.get("games") or []):
                status = (game.get("status") or {})
                if (status.get("abstractGameState") or "").lower() != "final":
                    continue
                teams = game.get("teams") or {}
                home = teams.get("home") or {}
                away = teams.get("away") or {}
                home_team = ((home.get("team") or {}).get("name")
                              or (home.get("team") or {}).get("teamName")
                              or "")
                away_team = ((away.get("team") or {}).get("name")
                              or (away.get("team") or {}).get("teamName")
                              or "")
                try:
                    home_runs = int(home.get("score") or 0)
                    away_runs = int(away.get("score") or 0)
                except (TypeError, ValueError):
                    continue
                game_pk = game.get("gamePk")
                if not game_pk:
                    continue
                out.append({
                    "game_pk":   int(game_pk),
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_runs": home_runs,
                    "away_runs": away_runs,
                    "game_date": game_date,
                })
        return out
    finally:
        if own_client:
            await client.aclose()


async def fetch_game_pitchers(
    game_pk: int,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> dict:
    """Return the starting pitchers for ``game_pk``::

        {
            "home": {"name": str, "era": float, "whip": float, "ip": float},
            "away": {"name": str, "era": float, "whip": float, "ip": float},
        }

    Empty dict on failure (caller falls back to neutral defaults).
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True)
    try:
        try:
            r = await client.get(_BOXSCORE_URL_TPL.format(game_pk=game_pk))
            r.raise_for_status()
            data = r.json() or {}
        except Exception as exc:
            log.debug("fetch_game_pitchers failed game_pk=%s: %s", game_pk, exc)
            return {}

        out: dict[str, dict] = {}
        for side in ("home", "away"):
            team = (data.get("teams") or {}).get(side) or {}
            pitcher_ids = team.get("pitchers") or []
            players = team.get("players") or {}
            if not pitcher_ids:
                continue
            starter_id = pitcher_ids[0]
            starter_key = f"ID{starter_id}"
            starter = players.get(starter_key) or {}
            person = starter.get("person") or {}
            pitching_stats = (
                ((starter.get("seasonStats") or {}).get("pitching"))
                or ((starter.get("stats") or {}).get("pitching"))
                or {}
            )
            try:
                era = float(pitching_stats.get("era")) if pitching_stats.get("era") not in (None, "-.--", "-") else None
            except (TypeError, ValueError):
                era = None
            try:
                whip = float(pitching_stats.get("whip")) if pitching_stats.get("whip") not in (None, "-.--", "-") else None
            except (TypeError, ValueError):
                whip = None
            try:
                ip = float(pitching_stats.get("inningsPitched")) if pitching_stats.get("inningsPitched") else None
            except (TypeError, ValueError):
                ip = None
            out[side] = {
                "name": person.get("fullName") or "",
                "era":  era,
                "whip": whip,
                "ip":   ip,
            }
        return out
    finally:
        if own_client:
            await client.aclose()


# ─────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────
def _validate_date_range(start_date: str, end_date: str) -> tuple[_date_t, _date_t]:
    """Parse and sanity-check the date range. Raises ValueError on bad
    input — caller decides whether to abort or fall back."""
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid date format (expected YYYY-MM-DD): {exc}") from exc
    if ed < sd:
        raise ValueError(f"end_date {end_date} must be on/after start_date {start_date}")
    if (ed - sd).days > _MAX_RANGE_DAYS:
        raise ValueError(
            f"Range too large ({(ed - sd).days} days). "
            f"Max allowed: {_MAX_RANGE_DAYS} days — split into multiple runs."
        )
    return sd, ed


def _classify_result(final_total: int, book_line: float) -> str:
    """Settle the Under/Over result given the realised total and the
    line used to grade. Push when totals match the integer line."""
    if final_total < book_line:
        return "won"
    if final_total > book_line:
        return "lost"
    return "push"


async def _exists_in_backtest(db, *, user_id: str, game_pk: int) -> bool:
    """Check whether a backtest doc for this game already exists."""
    if db is None:
        return False
    try:
        rows = await query_run_evaluations(
            db, user_id=user_id, match_id=game_pk, limit=1,
        )
        return bool(rows)
    except Exception as exc:
        log.debug("dedup query failed game_pk=%s: %s", game_pk, exc)
        return False


def _build_backtest_run_eval(
    *,
    expected_runs: float,
    book_line: float,
    home_team: str,
    away_team: str,
    home_pitcher: dict,
    away_pitcher: dict,
    game_date: str,
    totals_result: dict,
    final_total: int,
    home_runs: int,
    away_runs: int,
) -> tuple[dict, dict]:
    """Compose ``(run_evaluation, metrics)`` dicts ready for
    :func:`build_run_evaluation_document`. Captures the NB telemetry the
    summary endpoint needs to bucket dispersion."""
    diff = expected_runs - book_line
    if abs(diff) >= 0.8:
        recommended_market = "Full Game Under" if diff < 0 else "Full Game Over"
        recommended_side   = "under" if diff < 0 else "over"
        market_scope       = "full_game"
        should_recommend   = True
        confidence         = min(100, 65 + int(abs(diff) * 10))
        risk_tier          = "MEDIUM"
    else:
        recommended_market = "Watchlist"
        recommended_side   = None
        market_scope       = "full_game"
        should_recommend   = False
        confidence         = 50
        risk_tier          = "LOW"

    totals_model_block = {
        "model_used":                   totals_result.get("model") or "NegativeBinomial",
        "dispersion_ratio":             totals_result.get("dispersion_ratio"),
        "expected_total":               expected_runs,
        "book_total":                   book_line,
        "poisson_prob_under": (
            round((totals_result.get("poisson_prob_under") or 0) * 100.0, 2)
            if totals_result.get("poisson_prob_under") is not None else None
        ),
        "nb_prob_under": (
            round((totals_result.get("prob_under") or 0) * 100.0, 2)
            if totals_result.get("model") == "NegativeBinomial" else None
        ),
        "under_calibration_delta_pts":  totals_result.get("under_calibration_delta_pts"),
    }

    run_evaluation = {
        "explosive_risk_score":   0,
        "risk_tier":              risk_tier,
        "flip_triggered":         False,
        "should_recommend":       should_recommend,
        "recommended_market":     recommended_market,
        "recommended_line":       book_line,
        "recommended_odds":       None,
        "recommended_side":       recommended_side,
        "market_scope":           market_scope,
        "confidence":             confidence,
        "risk":                   risk_tier,
        "reason_codes":           [],
        "human_reasons":          [
            f"Backtest replay: expected_runs={expected_runs:.1f} "
            f"vs approx line {book_line:.1f} (diff {diff:+.1f}).",
        ],
        "explanation":            (
            f"Backtest replay sobre {game_date}: expected_runs={expected_runs:.1f}, "
            f"línea aproximada {book_line:.1f}, total real {final_total}."
        ),
        "avoid_markets":          [],
        "score_contributions":    {
            "ops_score":       0,
            "bullpen_era":     0,
            "park_factor":     0,
            "gap":             0,
            "script_survival": 0,
        },
        "game_state":             "pregame",
        "inning":                 None,
        "half":                   None,
        "score_home":             0,
        "score_away":             0,
        "pregame_total_line":     book_line,
        "live_total_line":        None,
        "game_date":              game_date,
        "home_team":              home_team,
        "away_team":              away_team,
        "starter_home":           (home_pitcher or {}).get("name"),
        "starter_away":           (away_pitcher or {}).get("name"),
        # NB calibration block (read by mlb_run_storage)
        "totals_model":           totals_model_block,
        "expected_total":         expected_runs,
        "book_total":             book_line,
        "pressure_tier":          None,           # not derivable from boxscore
        "fragility_tier":         None,
        "park_runs_mult":         1.0,
        "is_f5_market":           False,
    }
    metrics = {
        "game_date":           game_date,
        "home_team":           home_team,
        "away_team":           away_team,
        "starter_home":        (home_pitcher or {}).get("name"),
        "starter_away":        (away_pitcher or {}).get("name"),
        "expected_runs":       expected_runs,
        "book_total":          book_line,
        "pregame_total_line":  book_line,
        "market_scope":        market_scope,
        "score_home":          home_runs,
        "score_away":          away_runs,
    }
    return run_evaluation, metrics


async def run_backtest(
    db,
    start_date: str,
    end_date: str,
    *,
    dry_run: bool = False,
    user_id: str = DEFAULT_BACKTEST_USER_ID,
) -> dict:
    """Replay the MLB engine over a finished date range.

    See module docstring for the full flow. Returns a summary dict
    suitable for the admin endpoint response::

        {
            "date_range":     "YYYY-MM-DD → YYYY-MM-DD",
            "games_fetched":  int,
            "games_inserted": int,
            "games_skipped":  int,
            "games_failed":   int,
            "won":            int,
            "lost":           int,
            "push":           int,
            "under_hit_rate": float | None,
            "dry_run":        bool,
            "user_id":        str,
        }

    The function never raises — it logs and skips. Caller can rely on
    ``games_failed`` > 0 to know something went wrong.
    """
    summary: dict[str, Any] = {
        "date_range":     f"{start_date} → {end_date}",
        "games_fetched":  0,
        "games_inserted": 0,
        "games_skipped":  0,
        "games_failed":   0,
        "won":            0,
        "lost":           0,
        "push":           0,
        "under_hit_rate": None,
        "dry_run":        dry_run,
        "user_id":        user_id,
    }
    # Validate the range up-front so the caller gets a clean error.
    try:
        _validate_date_range(start_date, end_date)
    except ValueError as exc:
        summary["error"] = str(exc)
        log.warning("run_backtest validation failed: %s", exc)
        return summary

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        schedule = await fetch_historical_schedule(start_date, end_date, client=client)
        summary["games_fetched"] = len(schedule)
        if not schedule:
            return summary

        under_picks = 0
        under_wins  = 0

        for idx, game in enumerate(schedule):
            game_pk    = game["game_pk"]
            home_team  = game["home_team"]
            away_team  = game["away_team"]
            home_runs  = game["home_runs"]
            away_runs  = game["away_runs"]
            game_date  = game["game_date"]
            final_total = int(home_runs + away_runs)

            # ── Dedup ────────────────────────────────────────────────
            if not dry_run and await _exists_in_backtest(
                db, user_id=user_id, game_pk=game_pk,
            ):
                summary["games_skipped"] += 1
                continue

            # ── Fetch pitchers (rate-limited) ────────────────────────
            pitchers = await fetch_game_pitchers(game_pk, client=client)
            await asyncio.sleep(_BOXSCORE_DELAY_S)
            home_pitcher = pitchers.get("home") or {}
            away_pitcher = pitchers.get("away") or {}

            # ── Run the engine ───────────────────────────────────────
            try:
                ctx = build_backtest_scoring_ctx(
                    home_pitcher, away_pitcher, home_team, away_team,
                )
                book_line = _approximate_book_line(
                    expected_runs=9.0, game_date=game_date,
                )
                ou = over_under_predictor(ctx, book_line=book_line)
                expected_runs = float(ou.get("expected_runs") or 9.0)
                # Refine the line now that we know expected_runs.
                book_line = _approximate_book_line(expected_runs, game_date)
                totals_result = totals_probability(expected_runs, book_line)
            except Exception as exc:
                log.warning("Engine failed game_pk=%s: %s", game_pk, exc)
                summary["games_failed"] += 1
                continue

            result = _classify_result(final_total, book_line)
            summary[result] = summary.get(result, 0) + 1

            # Track Under hit-rate ONLY for picks the engine would have
            # actually recommended as an Under (diff <= -0.8).
            if (expected_runs - book_line) <= -0.8:
                under_picks += 1
                if result == "won":
                    under_wins += 1

            if dry_run:
                summary["games_inserted"] += 1  # would-have-been
                continue

            # ── Persist ───────────────────────────────────────────────
            try:
                run_eval, metrics = _build_backtest_run_eval(
                    expected_runs=expected_runs,
                    book_line=book_line,
                    home_team=home_team,
                    away_team=away_team,
                    home_pitcher=home_pitcher,
                    away_pitcher=away_pitcher,
                    game_date=game_date,
                    totals_result=totals_result,
                    final_total=final_total,
                    home_runs=home_runs,
                    away_runs=away_runs,
                )
                doc = build_run_evaluation_document(
                    user_id=user_id,
                    match_id=str(game_pk),
                    run_evaluation=run_eval,
                    metrics=metrics,
                    result="pending",   # update_run_evaluation_result will settle it
                )
                # Backtest provenance tags
                doc["_source"]            = BACKTEST_SOURCE_TAG
                doc["_line_approximated"] = True
                doc["prob_under"]         = totals_result.get("prob_under")
                doc["prob_over"]          = totals_result.get("prob_over")
                doc["dispersion_ratio"]   = totals_result.get("dispersion_ratio")
                doc["under_calibration_delta_pts"] = totals_result.get("under_calibration_delta_pts")
                doc["poisson_prob_under"] = totals_result.get("poisson_prob_under")
                doc["actual_total"]       = final_total
                doc["final_total"]        = final_total
                doc["final_runs_home"]    = home_runs
                doc["final_runs_away"]    = away_runs
                doc["backtest_generated_at"] = datetime.now(timezone.utc).isoformat()

                if db is not None:
                    await db.mlb_run_evaluations.insert_one(doc)
                    # Mirror the settled outcome on the same row so the
                    # summary endpoint counts it under "settled".
                    await update_run_evaluation_result(
                        db,
                        evaluation_id=doc["id"],
                        final_runs_home=home_runs,
                        final_runs_away=away_runs,
                        result=result,
                    )
                summary["games_inserted"] += 1
            except Exception as exc:
                log.warning("Persist failed game_pk=%s: %s", game_pk, exc)
                summary["games_failed"] += 1
                continue

        if under_picks > 0:
            summary["under_hit_rate"] = round(under_wins / under_picks * 100, 2)

    log.info(
        "Backtest done %s..%s fetched=%d inserted=%d skipped=%d failed=%d dry_run=%s",
        start_date, end_date,
        summary["games_fetched"], summary["games_inserted"],
        summary["games_skipped"],  summary["games_failed"],
        dry_run,
    )
    return summary


__all__ = [
    "DEFAULT_BACKTEST_USER_ID",
    "BACKTEST_SOURCE_TAG",
    "fetch_historical_schedule",
    "fetch_game_pitchers",
    "build_backtest_scoring_ctx",
    "run_backtest",
]
