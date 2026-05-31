"""
MLB Live State Fetcher (Bug B fix — live match detail hydration)
================================================================

The MatchDetailPage was rendering "Próximo" + empty live data even when the
actual MLB game was 4-3 in the 7th inning. Root cause: `db.matches` is only
refreshed periodically by the live ingestion sweep and the detail endpoint
served whatever was last persisted. The MLB schedule snapshot can lag by
minutes, and the linescore (inning, outs, runners) was never persisted at all.

This module fixes that by exposing a tiny on-demand fetcher backed by MLB
Stats API's `linescore` endpoint, which returns the current scoreboard +
inning state in a single HTTP call. It's designed to be invoked from a new
`/api/matches/{id}/live-refresh` endpoint and by the frontend polling loop.

Design
------
- **Bounded**: 6s timeout, hits 1 URL, no retries (the caller polls every
  30s anyway so a transient miss is OK).
- **Sport-aware**: only works for MLB. The endpoint guards against running
  it on football/basketball matches.
- **Idempotent**: writes back `live_stats` + `is_live` + `status` into
  `db.matches` so the regular detail endpoint also benefits.
- **Honest about state**: returns one of five UI states so the frontend
  can render the appropriate skeleton / banner:
        loading            (frontend-local)
        live-data-ready    score + inning available
        live-data-partial  only score available, missing inning/outs
        final              game is Final / Game Over
        no-live-data       game hasn't started yet
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_LINESCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/linescore"
_SCHEDULE_URL  = "https://statsapi.mlb.com/api/v1/schedule?gamePk={game_pk}&sportId=1"


def _coerce_game_pk(match_id: Any) -> Optional[int]:
    """MLB matches are stored with `match_id` = string of the gamePk."""
    if match_id is None:
        return None
    try:
        return int(str(match_id).strip())
    except (TypeError, ValueError):
        return None


def _shape_state(
    *,
    abstract_state: Optional[str],
    detailed_state: Optional[str],
    linescore: dict,
) -> str:
    """Map MLB's status taxonomy into our 4 backend states."""
    state = (abstract_state or "").strip().lower()
    detail = (detailed_state or "").strip().lower()
    if state == "final" or "final" in detail or "game over" in detail:
        return "final"
    if state == "preview" or detail in ("scheduled", "pre-game", "warmup", "delayed start"):
        return "no-live-data"
    # Live — decide partial vs ready based on what the linescore exposes.
    has_score   = bool(linescore.get("teams"))
    has_inning  = linescore.get("currentInning") is not None
    if has_score and has_inning:
        return "live-data-ready"
    if has_score:
        return "live-data-partial"
    return "no-live-data"


async def fetch_live_state(match_id: Any) -> dict:
    """Return the canonical live-state payload for an MLB match.

    Output keys::

        {
            "state":            "live-data-ready" | "live-data-partial"
                                | "final" | "no-live-data",
            "game_pk":          824832,
            "is_live":          True,
            "status":           "In Progress",
            "abstract_state":   "Live",
            "detailed_state":   "In Progress",
            "score": {"home": 4, "away": 3},
            "inning": {"number": 7, "half": "top", "ordinal": "7th"},
            "outs":             1,
            "balls":            2,
            "strikes":          1,
            "runners_on":       {"first": True, "second": False, "third": False},
            "current_batter":   {"id": 663697, "name": "Bo Bichette"},
            "current_pitcher":  {"id": 593576, "name": "Bryan Bello"},
            "fetched_at":       "2026-05-31T16:35:00+00:00",
        }

    Never raises — returns `state="no-live-data"` on any failure so the
    caller can render a friendly fallback.
    """
    game_pk = _coerce_game_pk(match_id)
    if game_pk is None:
        return {
            "state":      "no-live-data",
            "game_pk":    None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "_reason":    "match_id_not_numeric",
        }
    out: dict = {
        "state":          "no-live-data",
        "game_pk":        game_pk,
        "is_live":        False,
        "status":         None,
        "abstract_state": None,
        "detailed_state": None,
        "score":          {"home": None, "away": None},
        "inning":         None,
        "outs":           None,
        "balls":          None,
        "strikes":        None,
        "runners_on":     {"first": False, "second": False, "third": False},
        "current_batter":  None,
        "current_pitcher": None,
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r_line = await client.get(_LINESCORE_URL.format(game_pk=game_pk))
            r_sched = await client.get(_SCHEDULE_URL.format(game_pk=game_pk))
        ls = r_line.json() if r_line.status_code == 200 else {}
        sched = r_sched.json() if r_sched.status_code == 200 else {}
    except Exception as exc:
        log.debug("MLB live fetch failed for %s: %s", game_pk, exc)
        out["_reason"] = f"http_error: {exc}"
        return out

    # Pull status from the schedule's `games[].status` block.
    try:
        game = (sched.get("dates") or [{}])[0].get("games", [{}])[0] or {}
    except Exception:
        game = {}
    status_block    = (game.get("status") or {}) if isinstance(game, dict) else {}
    abstract_state  = status_block.get("abstractGameState")
    detailed_state  = status_block.get("detailedState") or status_block.get("status")
    out["status"]         = detailed_state
    out["abstract_state"] = abstract_state
    out["detailed_state"] = detailed_state

    # Score from the linescore.
    teams = (ls.get("teams") or {})
    home  = (teams.get("home") or {})
    away  = (teams.get("away") or {})
    if home or away:
        out["score"] = {"home": home.get("runs"), "away": away.get("runs")}

    # Inning + outs/balls/strikes.
    cur_inning = ls.get("currentInning")
    if cur_inning is not None:
        out["inning"] = {
            "number":  cur_inning,
            "half":    (ls.get("inningHalf") or "").lower() or None,
            "ordinal": ls.get("currentInningOrdinal"),
        }
    if ls.get("outs") is not None:
        out["outs"] = ls.get("outs")
    if ls.get("balls") is not None:
        out["balls"] = ls.get("balls")
    if ls.get("strikes") is not None:
        out["strikes"] = ls.get("strikes")

    # Runners — `offense` & `defense` carry IDs of the players on each base.
    offense = (ls.get("offense") or {})
    out["runners_on"] = {
        "first":  bool(offense.get("first")),
        "second": bool(offense.get("second")),
        "third":  bool(offense.get("third")),
    }
    batter = (offense.get("batter") or {})
    if batter.get("fullName"):
        out["current_batter"] = {"id": batter.get("id"), "name": batter.get("fullName")}
    defense = (ls.get("defense") or {})
    pitcher = (defense.get("pitcher") or {})
    if pitcher.get("fullName"):
        out["current_pitcher"] = {"id": pitcher.get("id"), "name": pitcher.get("fullName")}

    out["state"] = _shape_state(
        abstract_state=abstract_state,
        detailed_state=detailed_state,
        linescore=ls,
    )
    out["is_live"] = out["state"] in ("live-data-ready", "live-data-partial")
    return out


async def fetch_and_persist_live_state(db, match_id: Any) -> dict:
    """Fetch live state and write it back to `db.matches` so subsequent
    `/api/matches/{id}` reads return fresh data without another round-trip.

    Updates `live_stats`, `is_live`, `status` fields. Leaves the rest of
    the doc intact (no destructive merge).
    """
    snap = await fetch_live_state(match_id)
    if snap.get("game_pk") is None:
        return snap
    # Candidate match_ids (str + int) — MLB live games can be stored either way.
    candidates: list = [str(snap["game_pk"])]
    try:
        candidates.append(int(snap["game_pk"]))
    except (TypeError, ValueError):
        pass
    update = {
        "is_live": snap["is_live"],
        "status":  snap["status"] or "",
        "live_stats": {
            "score":           snap["score"],
            "inning":          snap.get("inning"),
            "outs":            snap.get("outs"),
            "balls":           snap.get("balls"),
            "strikes":         snap.get("strikes"),
            "runners_on":      snap.get("runners_on"),
            "current_batter":  snap.get("current_batter"),
            "current_pitcher": snap.get("current_pitcher"),
            "abstract_state":  snap.get("abstract_state"),
            "detailed_state":  snap.get("detailed_state"),
            "fetched_at":      snap["fetched_at"],
        },
    }
    try:
        await db.matches.update_one(
            {"match_id": {"$in": candidates}, "sport": "baseball"},
            {"$set": update},
        )
    except Exception as exc:
        log.debug("persist live_state failed (non-fatal): %s", exc)
    return snap


__all__ = ["fetch_live_state", "fetch_and_persist_live_state"]
