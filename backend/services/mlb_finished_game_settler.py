"""
MLB Finished-Game Settler
=========================

Runs after a match transitions from live → finished. It persists a
complete, queryable "settlement record" on the match doc so the rest of
the engine (active-series analyzer, learning loops, calibration) can
trust the score & bullpen workload weeks later — without having to
re-hit MLB Stats every time.

What it writes
--------------
    matches.{
      final_score:           {home, away, total},
      total_runs:            int,
      bullpen_usage: {
        home_pitches:   int,
        away_pitches:   int,
        home_innings:   float,
        away_innings:   float,
        home_starter_innings: float,
        away_starter_innings: float,
      },
      settled_at:            iso utc,
      settlement_source:     "mlb_stats_api_boxscore",
    }

Trigger
-------
Invoked from `live_lifecycle.sweep_expired_live_matches()` when a match
moves to `is_live=False`. We don't block the sweep on settlement
failures — the job is fire-and-forget per-match and tolerates partial
data.

Why this matters
----------------
M1 `mlb_active_series_analyzer` looks back 4 days for H2H runs. Today
it finds 1 game out of 2 because the ingestor never wrote the final
score. With this settler, the second the live feed declares a game
over the box-score lands in Mongo → the next pick generated for the
same matchup gets the full series context.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_BASE = "https://statsapi.mlb.com/api/v1"
_TIMEOUT = 12.0


def _ip_to_float(ip) -> float:
    if ip is None:
        return 0.0
    try:
        s = str(ip).strip()
        if "." not in s:
            return float(int(s))
        whole, frac = s.split(".", 1)
        return int(whole) + (int(frac[:1]) / 3.0)
    except (TypeError, ValueError):
        return 0.0


async def fetch_boxscore_summary(game_pk: Any) -> Optional[dict]:
    """Fetch MLB Stats boxscore and return our settlement shape.

    Returns ``None`` on any error or when the game isn't actually Final.
    """
    if not game_pk:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{_BASE}/game/{game_pk}/boxscore")
            r.raise_for_status()
            box = r.json()
            # Linescore for final score + status.
            r2 = await client.get(f"{_BASE}/game/{game_pk}/linescore")
            r2.raise_for_status()
            ls = r2.json()
    except Exception as exc:
        log.debug("settler boxscore %s failed: %s", game_pk, exc)
        return None

    teams = box.get("teams") or {}
    out: dict[str, Any] = {
        "settled_at":        datetime.now(timezone.utc).isoformat(),
        "settlement_source": "mlb_stats_api_boxscore",
    }
    home_score = ((ls.get("teams") or {}).get("home") or {}).get("runs")
    away_score = ((ls.get("teams") or {}).get("away") or {}).get("runs")
    if home_score is not None and away_score is not None:
        try:
            h, a = int(home_score), int(away_score)
            out["final_score"] = {"home": h, "away": a, "total": h + a}
            out["total_runs"]  = h + a
        except (TypeError, ValueError):
            pass

    bullpen_usage: dict[str, Any] = {}
    for side in ("home", "away"):
        side_block = teams.get(side) or {}
        pids = side_block.get("pitchers") or []
        players = side_block.get("players") or {}
        bp_pitches = 0
        bp_innings = 0.0
        starter_ip = 0.0
        for idx, pid in enumerate(pids):
            p = players.get(f"ID{pid}") or {}
            pstats = ((p.get("stats") or {}).get("pitching") or {})
            pitches = pstats.get("pitchesThrown")
            ip = pstats.get("inningsPitched")
            try:
                pitches_i = int(pitches or 0)
            except (TypeError, ValueError):
                pitches_i = 0
            ip_f = _ip_to_float(ip)
            if idx == 0:
                starter_ip = ip_f
            else:
                bp_pitches += pitches_i
                bp_innings += ip_f
        bullpen_usage[f"{side}_pitches"] = bp_pitches
        bullpen_usage[f"{side}_innings"] = round(bp_innings, 1)
        bullpen_usage[f"{side}_starter_innings"] = round(starter_ip, 1)
    if any(bullpen_usage.values()):
        out["bullpen_usage"] = bullpen_usage

    # If we got neither a final score nor box-score data, treat as null.
    if "final_score" not in out and "bullpen_usage" not in out:
        return None
    return out


async def settle_match(db: Any, match_doc: dict) -> Optional[dict]:
    """Settle ONE finished match in `db.matches`.

    Returns the patch dict that was written (or None if nothing).
    Idempotent — if `settled_at` is already set we skip.
    """
    if not match_doc or (match_doc.get("sport") != "baseball"):
        return None
    if match_doc.get("settled_at"):
        return None  # already settled
    game_pk = match_doc.get("game_pk") or match_doc.get("match_id")
    summary = await fetch_boxscore_summary(game_pk)
    if not summary:
        return None
    try:
        await db.matches.update_one(
            {"match_id": match_doc.get("match_id")},
            {"$set": summary},
        )
        # Also stamp on archived_live_matches if the doc was archived first.
        try:
            await db.archived_live_matches.update_one(
                {"match_id": match_doc.get("match_id")},
                {"$set": summary},
            )
        except Exception:
            pass
        log.info(
            "settled match_id=%s final_score=%s bullpen_pitches=H%s/A%s",
            match_doc.get("match_id"),
            summary.get("final_score"),
            (summary.get("bullpen_usage") or {}).get("home_pitches"),
            (summary.get("bullpen_usage") or {}).get("away_pitches"),
        )
        return summary
    except Exception as exc:
        log.warning("settle_match db write failed: %s", exc)
        return None


async def settle_recent_finished(db: Any, *, days_back: int = 2) -> int:
    """Sweep `db.matches` for recently-finished baseball games that
    don't have `settled_at` yet, and settle them. Returns number settled.

    Called from a 15-minute APScheduler tick (see /api/admin or your
    scheduler boot in `server.py`). Safe to call concurrently — the
    update is keyed by `match_id` + idempotent.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    query = {
        "sport":         "baseball",
        "kickoff_iso":   {"$gte": cutoff},
        "settled_at":    {"$exists": False},
        "$or": [
            {"status":            {"$in": ["Final", "FT", "Game Over"]}},
            {"abstractGameState": "Final"},
            {"is_live":           False, "live_stats": {"$exists": True}},
        ],
    }
    n = 0
    try:
        async for doc in db.matches.find(query).limit(50):
            patch = await settle_match(db, doc)
            if patch:
                n += 1
    except Exception as exc:
        log.warning("settle_recent_finished sweep failed: %s", exc)
    if n:
        log.info("settle_recent_finished: settled %d matches.", n)
    return n


__all__ = [
    "fetch_boxscore_summary",
    "settle_match",
    "settle_recent_finished",
]
