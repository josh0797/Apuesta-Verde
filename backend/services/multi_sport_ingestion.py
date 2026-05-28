"""Multi-sport data ingestion + normalization for NBA/MLB.

This module COMPLEMENTS the existing football ingestion. For football we keep using
the original services/data_ingestion.py. For basketball/baseball we use this.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from . import api_sports as aps

log = logging.getLogger("ingest_multi")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_teams(sport: str, fx: dict) -> tuple[dict, dict]:
    """Both basketball and baseball API-Sports payloads have fx['teams']['home']/['away']."""
    teams = fx.get("teams") or {}
    return teams.get("home") or {}, teams.get("away") or {}


def _extract_kickoff(sport: str, fx: dict) -> tuple[str, int]:
    if sport == "football":
        return fx["fixture"]["date"], fx["fixture"]["timestamp"]
    # basketball/baseball top-level fields
    ts = fx.get("timestamp") or 0
    iso = fx.get("date") or datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return iso, ts


def _extract_status(sport: str, fx: dict) -> dict:
    if sport == "football":
        return fx["fixture"]["status"]
    st = fx.get("status") or {}
    return {"short": st.get("short"), "long": st.get("long"), "elapsed": st.get("timer") or st.get("elapsed")}


def _is_live_status(sport: str, short: str | None) -> bool:
    if not short:
        return False
    if sport == "football":
        return short in ("1H", "2H", "HT", "ET", "P", "LIVE", "BT")
    if sport == "basketball":
        return short in ("Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT", "LIVE", "IN_PLAY", "in_play")
    if sport == "baseball":
        # P5 fix (2026-05-28): the previous list was incomplete —
        # API-Sports baseball uses several status codes that were missing,
        # so games already in play (e.g. Detroit Tigers between innings)
        # were classified as upcoming. Now we mirror the (richer)
        # LIVE_STATUSES set used by live_lifecycle.compute_live_state().
        if short in (
            "IN1", "IN2", "IN3", "IN4", "IN5", "IN6", "IN7", "IN8", "IN9",
            "MID1", "MID2", "MID3", "MID4", "MID5", "MID6", "MID7", "MID8", "MID9",
            "BT", "MID", "END", "BRK", "LIVE", "IN_PLAY", "in_play",
        ):
            return True
        # Long-form statuses ("Top 1st" / "Bottom 5th" / "Middle 3rd")
        s_low = short.lower()
        if (s_low.startswith("top ") or s_low.startswith("bottom ") or s_low.startswith("middle ")) \
                and any(o in s_low for o in ("st", "nd", "rd", "th")):
            return True
        # Fallback: anything containing "inning" is live
        if "inning" in s_low:
            return True
        return False
    return False


def normalize_odds_multi(sport: str, odds_response: list[dict]) -> dict:
    """Convert API odds response into our snapshot schema.

    Different sports have different bet names:
      football  → Match Winner (1X2), Over/Under, BTTS, Asian Handicap, Double Chance
      basketball → Home/Away (moneyline), Spread, Total Points (Over/Under)
      baseball   → Home/Away (moneyline), Run Line, Total Runs (Over/Under)
    """
    if not odds_response:
        return {"available": False, "snapshot_at": _now_iso(), "bookmakers": [], "markets": {}, "sport": sport}
    item = odds_response[0]
    bookmakers_data = item.get("bookmakers", []) or []
    markets: dict[str, list[dict]] = {
        "Moneyline": [],   # 1X2 for football OR Home/Away for NBA/MLB
        "Total": [],       # Over/Under points/runs/goals
        "Spread": [],      # Spread (NBA/MLB) or Asian Handicap (football)
        "BTTS": [],
        "Double Chance": [],
    }
    bm_names = []
    for bm in bookmakers_data:
        bm_name = bm.get("name", "Unknown")
        bm_names.append(bm_name)
        for bet in bm.get("bets", []) or []:
            bname = (bet.get("name") or bet.get("label") or "").strip()
            values = bet.get("values", []) or []
            # Football labels
            if bname in ("Match Winner", "Home/Away", "Moneyline", "Match Winner 3Way"):
                row = {"bookmaker": bm_name}
                for v in values:
                    val = (v.get("value") or "").strip()
                    try: odd = float(v.get("odd", 0))
                    except Exception: odd = 0.0
                    if val in ("Home", "1", "Local"): row["home"] = odd
                    elif val in ("Draw", "X"): row["draw"] = odd
                    elif val in ("Away", "2", "Visitante"): row["away"] = odd
                markets["Moneyline"].append(row)
            elif bname in ("Goals Over/Under", "Over/Under", "Total Points", "Total Runs", "Asian Total", "Total"):
                row = {"bookmaker": bm_name, "lines": {}}
                for v in values:
                    try: row["lines"][v.get("value")] = float(v.get("odd", 0))
                    except Exception: pass
                markets["Total"].append(row)
            elif bname in ("Both Teams Score", "Both Teams To Score"):
                row = {"bookmaker": bm_name}
                for v in values:
                    try: row[(v.get("value") or "").lower()] = float(v.get("odd", 0))
                    except Exception: pass
                markets["BTTS"].append(row)
            elif bname in ("Asian Handicap", "Handicap Result", "Spread", "Run Line", "Point Spread"):
                row = {"bookmaker": bm_name, "lines": []}
                for v in values:
                    try: row["lines"].append({"value": v.get("value"), "odd": float(v.get("odd", 0))})
                    except Exception: pass
                markets["Spread"].append(row)
            elif bname == "Double Chance":
                row = {"bookmaker": bm_name}
                for v in values:
                    try: row[(v.get("value") or "")] = float(v.get("odd", 0))
                    except Exception: pass
                markets["Double Chance"].append(row)
    return {
        "available": True,
        "snapshot_at": _now_iso(),
        "bookmakers": bm_names,
        "markets": markets,
        "sport": sport,
    }


def _team_in_standings(sport: str, standings_resp: list[dict], team_id: int) -> dict:
    info = {"position": None, "points": None, "played": None, "wins": None, "losses": None}
    try:
        if not standings_resp:
            return info
        if sport == "football":
            league = standings_resp[0].get("league", {})
            for group in league.get("standings", []) or []:
                for row in group:
                    if (row.get("team") or {}).get("id") == team_id:
                        info["position"] = row.get("rank")
                        info["points"] = row.get("points")
                        info["played"] = (row.get("all") or {}).get("played")
                        return info
        else:
            # basketball/baseball: response is list of group lists or list of rows
            for group in standings_resp:
                rows = group if isinstance(group, list) else [group]
                for row in rows:
                    if (row.get("team") or {}).get("id") == team_id:
                        info["position"] = row.get("position") or row.get("rank")
                        info["points"] = row.get("points") or (row.get("games") or {}).get("win", {}).get("total")
                        games = row.get("games") or {}
                        info["wins"] = (games.get("win") or {}).get("total")
                        info["losses"] = (games.get("lose") or {}).get("total")
                        info["played"] = games.get("played")
                        return info
    except Exception:
        pass
    return info


def normalize_team_context_multi(sport: str, stats: dict, standings_resp: list[dict], team_id: int) -> dict:
    ctx: dict[str, Any] = {
        "sport": sport,
        "fetched_at": _now_iso(),
        "data_source_season": "2024 (proxy)",
        "form_last_5": "",
        "wins": None,
        "losses": None,
        "position": None,
        "points": None,
        "motivation_flags": {},
    }
    if stats:
        form = stats.get("form") or ""
        ctx["form_last_5"] = form[-5:] if form else ""
        games = stats.get("games") or {}
        ctx["wins"] = (games.get("wins") or {}).get("all", {}).get("total") if isinstance(games.get("wins"), dict) else None
    info = _team_in_standings(sport, standings_resp, team_id)
    ctx.update({k: info.get(k) for k in ("position", "points", "wins", "losses")})
    return ctx


def normalize_live_stats_multi(sport: str, fx: dict) -> dict | None:
    status = _extract_status(sport, fx)
    short = status.get("short")
    if not _is_live_status(sport, short):
        return None
    if sport == "football":
        goals = fx.get("goals") or {}
        return {
            "minute": status.get("elapsed"),
            "status": short,
            "score": {"home": goals.get("home"), "away": goals.get("away")},
            "home_stats": {}, "away_stats": {},
            "fetched_at": _now_iso(),
        }
    scores = fx.get("scores") or {}
    return {
        "minute": status.get("elapsed"),
        "status": short,
        "score": {"home": (scores.get("home") or {}).get("total"), "away": (scores.get("away") or {}).get("total")},
        "home_stats": {}, "away_stats": {},
        "fetched_at": _now_iso(),
    }


async def enrich_multi(sport: str, client: httpx.AsyncClient, db, fx_raw: dict, is_live: bool) -> dict | None:
    """Light enrichment for NBA/MLB: odds + standings + basic team info.

    Heavy stats are skipped on first pass (rate-limit budget) — LLM gets enough to work.
    """
    try:
        # Identifiers vary slightly between products
        if sport == "football":
            fid = fx_raw["fixture"]["id"]
            league_obj = fx_raw["league"]
        else:
            fid = fx_raw.get("id")
            league_obj = fx_raw.get("league") or {}
        lid = league_obj.get("id")
        season = league_obj.get("season")
        home, away = _extract_teams(sport, fx_raw)
        kickoff_iso, kickoff_ts = _extract_kickoff(sport, fx_raw)
        status = _extract_status(sport, fx_raw)
        short_status = status.get("short")
        venue = (fx_raw.get("venue") or {}).get("name") or (fx_raw.get("fixture", {}).get("venue") or {}).get("name")

        # Fetch odds + standings
        try:
            odds_resp = await aps.odds_for_fixture(sport, client, fid, db=db)
        except Exception as e:
            log.warning("odds failed [%s/%s]: %s", sport, fid, e)
            odds_resp = []
        try:
            stand_resp = await aps.standings(sport, client, lid, db=db) if lid else []
        except Exception as e:
            log.warning("standings failed [%s/%s]: %s", sport, lid, e)
            stand_resp = []

        norm_odds = normalize_odds_multi(sport, odds_resp)
        ctx_home = normalize_team_context_multi(sport, {}, stand_resp, home.get("id"))
        ctx_away = normalize_team_context_multi(sport, {}, stand_resp, away.get("id"))
        live_stats = normalize_live_stats_multi(sport, fx_raw) if is_live else None

        match_doc = {
            "match_id": f"{sport}-{fid}" if sport != "football" else fid,
            "sport": sport,
            "raw_fixture_id": fid,
            "league": league_obj.get("name"),
            "league_id": lid,
            "league_logo": league_obj.get("logo"),
            "season": season,
            "kickoff_iso": kickoff_iso,
            "kickoff_ts": kickoff_ts,
            "is_live": is_live,
            "status_short": short_status,
            "venue": venue,
            "home_team": {"id": home.get("id"), "name": home.get("name"), "logo": home.get("logo"), "context": ctx_home},
            "away_team": {"id": away.get("id"), "name": away.get("name"), "logo": away.get("logo"), "context": ctx_away},
            "odds_snapshots": [norm_odds] if norm_odds.get("available") else [],
            "live_stats": live_stats,
            "h2h_recent": [],
            "data_complete": norm_odds.get("available"),
            "updated_at": _now_iso(),
        }
        await db.matches.update_one({"match_id": match_doc["match_id"]}, {"$set": match_doc}, upsert=True)
        if norm_odds.get("available"):
            await db.odds_snapshots.insert_one({"match_id": match_doc["match_id"], **norm_odds})
        return match_doc
    except Exception as exc:
        log.exception("enrich_multi failed: %s", exc)
        return None


async def ingest_upcoming_multi(sport: str, client: httpx.AsyncClient, db, max_total: int = 6) -> list[dict]:
    try:
        upcoming = await aps.fixtures_next_48h(sport, client)
    except Exception as exc:
        log.error("%s fixtures failed: %s", sport, exc)
        return []
    top = [f for f in upcoming if ((f.get("league") or {}).get("id")) in aps.top_leagues(sport)]
    others = [f for f in upcoming if ((f.get("league") or {}).get("id")) not in aps.top_leagues(sport)]
    top.sort(key=lambda f: f.get("timestamp") or (f.get("fixture") or {}).get("timestamp") or 0)
    others.sort(key=lambda f: f.get("timestamp") or (f.get("fixture") or {}).get("timestamp") or 0)
    selected = (top + others)[:max_total]
    out = []
    for fx in selected:
        res = await enrich_multi(sport, client, db, fx, False)
        if res:
            out.append(res)
    return out


async def ingest_live_multi(sport: str, client: httpx.AsyncClient, db, max_total: int = 10) -> list[dict]:
    try:
        live = await aps.fixtures_live(sport, client)
    except Exception as exc:
        log.error("%s live failed: %s", sport, exc)
        return []
    top = [f for f in live if ((f.get("league") or {}).get("id")) in aps.top_leagues(sport)]
    others = [f for f in live if ((f.get("league") or {}).get("id")) not in aps.top_leagues(sport)]
    selected = (top + others)[:max_total]
    out = []
    for fx in selected:
        res = await enrich_multi(sport, client, db, fx, True)
        if res:
            out.append(res)
    return out
