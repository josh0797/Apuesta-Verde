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

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_LINESCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/linescore"
_SCHEDULE_URL  = "https://statsapi.mlb.com/api/v1/schedule?gamePk={game_pk}&sportId=1"
_BOXSCORE_URL  = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"


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


def _extract_box_score(box_payload: dict) -> dict:
    """Convert MLB Stats API ``/boxscore`` payload to our flat shape.

    The MLB Stats API returns::

        {
          "teams": {
            "home": {
              "teamStats": {
                "batting":  {"hits": 8, "homeRuns": 2, "baseOnBalls": 3, "strikeOuts": 7, ...},
                "pitching": {"baseOnBalls": 2, "strikeOuts": 10, "numberOfPitches": 95, ...},
                "fielding": {"errors": 1, ...},
              }, ...
            },
            "away": {...}
          }
        }

    We expose the offensive (batting) stats per team + errors (fielding)
    + pitches thrown (pitching). The shape mirrors what
    ``services.live_pre_match_comparison`` already expects when reading
    ``live_stats.box_score``::

        {
          "hits":        {"home": int, "away": int},
          "walks":       {"home": int, "away": int},
          "home_runs":   {"home": int, "away": int},
          "errors":      {"home": int, "away": int},
          "strikeouts":  {"home": int, "away": int},
          "pitches_home": int, "pitches_away": int,
        }

    Returns ``{}`` (not None) if no usable data — callers can `.get(k)` safely.
    """
    if not isinstance(box_payload, dict):
        return {}
    teams = box_payload.get("teams") or {}
    home  = teams.get("home") or {}
    away  = teams.get("away") or {}
    if not (isinstance(home, dict) and isinstance(away, dict)):
        return {}

    def _bat(side: dict) -> dict:
        s = ((side.get("teamStats") or {}).get("batting") or {})
        return s if isinstance(s, dict) else {}

    def _pit(side: dict) -> dict:
        s = ((side.get("teamStats") or {}).get("pitching") or {})
        return s if isinstance(s, dict) else {}

    def _fld(side: dict) -> dict:
        s = ((side.get("teamStats") or {}).get("fielding") or {})
        return s if isinstance(s, dict) else {}

    h_bat, a_bat = _bat(home), _bat(away)
    h_pit, a_pit = _pit(home), _pit(away)
    h_fld, a_fld = _fld(home), _fld(away)

    if not (h_bat or a_bat or h_fld or a_fld):
        return {}

    def _get_int(d: dict, *keys: str) -> Optional[int]:
        for k in keys:
            v = d.get(k)
            if v is None or v == "":
                continue
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
        return None

    out: dict[str, Any] = {
        "hits": {
            "home": _get_int(h_bat, "hits"),
            "away": _get_int(a_bat, "hits"),
        },
        "walks": {
            "home": _get_int(h_bat, "baseOnBalls"),
            "away": _get_int(a_bat, "baseOnBalls"),
        },
        "home_runs": {
            "home": _get_int(h_bat, "homeRuns"),
            "away": _get_int(a_bat, "homeRuns"),
        },
        "errors": {
            "home": _get_int(h_fld, "errors"),
            "away": _get_int(a_fld, "errors"),
        },
        "strikeouts": {
            # batting strikeouts = times this team's batters were struck out.
            "home": _get_int(h_bat, "strikeOuts"),
            "away": _get_int(a_bat, "strikeOuts"),
        },
        # `pitches_home` = pitches thrown BY the home pitcher(s).
        "pitches_home": _get_int(h_pit, "numberOfPitches", "pitchesThrown"),
        "pitches_away": _get_int(a_pit, "numberOfPitches", "pitchesThrown"),
        # also useful for downstream consumers
        "at_bats": {
            "home": _get_int(h_bat, "atBats"),
            "away": _get_int(a_bat, "atBats"),
        },
        "left_on_base": {
            "home": _get_int(h_bat, "leftOnBase"),
            "away": _get_int(a_bat, "leftOnBase"),
        },
    }
    # Drop fully-empty nested dicts (both None) so downstream `if box.get("hits"):` works.
    cleaned: dict[str, Any] = {}
    for k, v in out.items():
        if isinstance(v, dict):
            if any(x is not None for x in v.values()):
                cleaned[k] = v
        else:
            if v is not None:
                cleaned[k] = v
    return cleaned


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
        "box_score":      {},   # populated below from /boxscore
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            # BUGFIX MLB-2a — parallel fetch of linescore + schedule +
            # boxscore. The boxscore is needed so the UI can render
            # hits / BB / HR / errors / strikeouts / pitches per side.
            # `live_pre_match_comparison.py` reads these from
            # `live_stats.box_score`. We use `gather` so the third
            # call doesn't add latency (all hit the same MLB Stats API
            # host, HTTP/2 multiplexed by httpx).
            r_line, r_sched, r_box = await asyncio.gather(
                client.get(_LINESCORE_URL.format(game_pk=game_pk)),
                client.get(_SCHEDULE_URL.format(game_pk=game_pk)),
                client.get(_BOXSCORE_URL.format(game_pk=game_pk)),
                return_exceptions=True,
            )
        def _maybe_json(resp):
            if isinstance(resp, Exception):
                return {}
            try:
                return resp.json() if resp.status_code == 200 else {}
            except Exception:
                return {}
        ls    = _maybe_json(r_line)
        sched = _maybe_json(r_sched)
        box   = _maybe_json(r_box)
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

    # BUGFIX MLB-2a — surface the boxscore so the live-pre-match comparison
    # engine can show hits / BB / HR / errors / strikeouts / pitches.
    try:
        out["box_score"] = _extract_box_score(box) or {}
    except Exception as exc:
        log.debug("boxscore extraction failed for %s: %s", game_pk, exc)
        out["box_score"] = {}
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
        # BUGFIX MLB-2b — also persist game_pk at doc root so the
        # pregame-pick lookup can match picks that store it as `game_pk`.
        "game_pk": snap["game_pk"],
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
            # BUGFIX MLB-2a — box_score with hits/BB/HR/errors/etc. so
            # live_pre_match_comparison.py can read it back.
            "box_score":       snap.get("box_score") or {},
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
