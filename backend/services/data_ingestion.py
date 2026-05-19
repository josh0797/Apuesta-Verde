"""Data ingestion orchestrator (multi-sport).

Flow:
  1) Try API-Sports for fixtures/odds/context (football | basketball | baseball).
  2) If API fails entirely, fallback to ESPN public scoreboard (football only).
  3) Persist normalized docs in MongoDB collections.

Collections:
  matches          (key: match_id) — now also stores `sport`
  odds_snapshots   (history of odds per fixture)
  picks            (LLM output) — also stores `sport`
  pick_tracking    (user marks) — also stores `sport`
  users
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from . import api_football as af  # legacy football-only client (kept for backward compat)
from . import api_sports as aps    # generic multi-sport client
from . import fallback_scraper as fb
from . import normalizer as nz

log = logging.getLogger("ingestion")

# Top-league IDs per sport, sourced from api_sports.SPORT_CONFIG
TOP_LEAGUES = aps.SPORT_CONFIG["football"]["top_leagues"]


def _top_leagues_for(sport: str) -> set:
    return aps.SPORT_CONFIG.get(sport, {}).get("top_leagues", set())


# ── Sport-aware field extractors (API-Sports response shapes differ) ─────────
def _fx_id(sport: str, fx: dict):
    return fx["fixture"]["id"] if sport == "football" else fx.get("id")


def _fx_timestamp(sport: str, fx: dict):
    if sport == "football":
        return fx["fixture"]["timestamp"]
    return fx.get("timestamp")


def _fx_status_short(sport: str, fx: dict):
    if sport == "football":
        return fx["fixture"]["status"]["short"]
    return (fx.get("status") or {}).get("short")


def _fx_date(sport: str, fx: dict):
    if sport == "football":
        return fx["fixture"]["date"]
    return fx.get("date")


def _fx_league(sport: str, fx: dict) -> dict:
    return fx.get("league") or {}


def _fx_teams(sport: str, fx: dict) -> tuple[dict, dict]:
    teams = fx.get("teams") or {}
    return teams.get("home") or {}, teams.get("away") or {}


def _fx_venue(sport: str, fx: dict):
    if sport == "football":
        return ((fx.get("fixture") or {}).get("venue") or {}).get("name")
    return (fx.get("venue") or {}).get("name") if isinstance(fx.get("venue"), dict) else fx.get("venue")


# ── Public ingestion API ─────────────────────────────────────────────────────
async def ingest_upcoming(
    client: httpx.AsyncClient,
    db,
    sport: str = "football",
    max_per_league: int = 2,
    max_total: int = 8,
) -> list[dict]:
    """Ingest upcoming next-48h fixtures (top leagues priority) + odds + context."""
    sport = (sport or "football").lower()
    if sport == "football":
        # Use legacy football path (backward compatible)
        try:
            upcoming_raw = await af.fixtures_next_48h(client)
        except Exception as exc:
            log.error("API-Football fixtures failed: %s -> using fallback", exc)
            upcoming_raw = []
    else:
        try:
            upcoming_raw = await aps.fixtures_next_48h(sport, client)
        except Exception as exc:
            log.error("API-Sports[%s] fixtures failed: %s", sport, exc)
            upcoming_raw = []

    fallback_used = False
    if not upcoming_raw:
        if sport != "football":
            log.warning("No upcoming for %s — no fallback available for this sport", sport)
            return []
        log.warning("No upcoming from API-Football, attempting ESPN fallback")
        fb_data = await fb.espn_soccer_scoreboard(client)
        fallback_used = True
        minimal = []
        now = datetime.now(timezone.utc)
        for ev in fb_data:
            if ev.get("is_live"):
                continue
            try:
                ki = ev["kickoff_iso"]
                dt = datetime.fromisoformat(ki.replace("Z", "+00:00"))
                if dt < now:
                    continue
            except Exception:
                pass
            minimal.append({
                "match_id": ev["id"],
                "sport": "football",
                "source": "espn_fallback",
                "league": ev.get("league"),
                "league_id": None,
                "season": None,
                "kickoff_iso": ev.get("kickoff_iso"),
                "is_live": False,
                "venue": None,
                "home_team": {"id": ev["home_team"]["id"], "name": ev["home_team"]["name"], "context": {"fetched_at": None, "form_last_5": "", "position": None}},
                "away_team": {"id": ev["away_team"]["id"], "name": ev["away_team"]["name"], "context": {"fetched_at": None, "form_last_5": "", "position": None}},
                "odds_snapshots": [],
                "live_stats": None,
                "h2h_recent": [],
                "data_complete": False,
                "fallback_used": True,
                "updated_at": nz.now_iso(),
            })
        for m in minimal:
            await db.matches.update_one({"match_id": m["match_id"]}, {"$set": m}, upsert=True)
        return minimal

    top_set = _top_leagues_for(sport)
    top = [f for f in upcoming_raw if _fx_league(sport, f).get("id") in top_set]
    others = [f for f in upcoming_raw if _fx_league(sport, f).get("id") not in top_set]
    top.sort(key=lambda f: _fx_timestamp(sport, f) or 0)
    others.sort(key=lambda f: _fx_timestamp(sport, f) or 0)
    per_league: dict[int, int] = {}
    selected = []
    for f in top + others:
        lid = _fx_league(sport, f).get("id")
        if per_league.get(lid, 0) >= max_per_league:
            continue
        per_league[lid] = per_league.get(lid, 0) + 1
        selected.append(f)
        if len(selected) >= max_total:
            break

    log.info("Ingesting %d selected fixtures for sport=%s (top-league priority)", len(selected), sport)
    enriched: list[dict] = []
    for fx in selected:
        try:
            res = await enrich_fixture(client, db, fx, False, sport=sport)
            if res:
                enriched.append(res)
        except Exception as exc:
            log.exception("ingest enrich failed [%s]: %s", sport, exc)
    return enriched


async def ingest_live(client: httpx.AsyncClient, db, sport: str = "football", max_total: int = 20) -> list[dict]:
    sport = (sport or "football").lower()
    try:
        if sport == "football":
            live_raw = await af.fixtures_live(client)
        else:
            live_raw = await aps.fixtures_live(sport, client)
    except Exception as exc:
        log.error("API[%s] live failed: %s", sport, exc)
        return []
    top_set = _top_leagues_for(sport)
    top = [f for f in live_raw if _fx_league(sport, f).get("id") in top_set]
    others = [f for f in live_raw if _fx_league(sport, f).get("id") not in top_set]
    selected = (top + others)[:max_total]
    # Serial for non-football to respect single shared rate limit
    enriched: list[dict] = []
    if sport == "football":
        enriched_results = await asyncio.gather(*[enrich_fixture(client, db, f, True, sport=sport) for f in selected])
        enriched = [e for e in enriched_results if e]
    else:
        for f in selected:
            try:
                e = await enrich_fixture(client, db, f, True, sport=sport)
                if e:
                    enriched.append(e)
            except Exception as exc:
                log.warning("live enrich failed: %s", exc)
    return enriched


async def enrich_fixture(
    client: httpx.AsyncClient,
    db,
    fx_raw: dict,
    is_live: bool,
    sport: str = "football",
    deep: bool = False,
) -> dict | None:
    """Enrich a raw fixture into our normalized match doc."""
    sport = (sport or "football").lower()
    if sport == "football":
        return await _enrich_football(client, db, fx_raw, is_live, deep)
    return await _enrich_generic(client, db, fx_raw, is_live, sport, deep)


async def _enrich_football(client: httpx.AsyncClient, db, fx_raw: dict, is_live: bool, deep: bool) -> dict | None:
    try:
        fid = fx_raw["fixture"]["id"]
        lid = fx_raw["league"]["id"]
        season = fx_raw["league"]["season"]
        home = fx_raw["teams"]["home"]
        away = fx_raw["teams"]["away"]
        kickoff = fx_raw["fixture"]["date"]
        venue = (fx_raw.get("fixture", {}).get("venue") or {}).get("name")

        try:
            odds_resp = await af.odds_for_fixture(client, fid, db=db)
        except Exception as e:
            log.warning("odds failed for %s: %s", fid, e)
            odds_resp = []
        try:
            stand_resp = await af.standings(client, lid, db=db)
        except Exception as e:
            log.warning("standings failed for league %s: %s", lid, e)
            stand_resp = []

        stats_h, stats_a, h2h, inj_h, inj_a = {}, {}, [], [], []
        if deep:
            try: stats_h = await af.team_statistics(client, home["id"], lid, db=db)
            except Exception: pass
            try: stats_a = await af.team_statistics(client, away["id"], lid, db=db)
            except Exception: pass
            try: h2h = await af.head_to_head(client, home["id"], away["id"], limit=5, db=db)
            except Exception: pass
            try: inj_h = await af.injuries(client, home["id"], db=db)
            except Exception: pass
            try: inj_a = await af.injuries(client, away["id"], db=db)
            except Exception: pass

        norm_odds = nz.normalize_odds(odds_resp)
        ctx_home = nz.normalize_team_context(stats_h, stand_resp, inj_h, home["id"])
        ctx_away = nz.normalize_team_context(stats_a, stand_resp, inj_a, away["id"])
        live_stats = nz.normalize_live_stats(fx_raw) if is_live else None

        h2h_clean = []
        for hf in h2h or []:
            try:
                h2h_clean.append({
                    "date": hf["fixture"]["date"],
                    "home": hf["teams"]["home"]["name"],
                    "away": hf["teams"]["away"]["name"],
                    "score": f"{hf['goals']['home']}-{hf['goals']['away']}",
                    "status": hf["fixture"]["status"]["short"],
                })
            except Exception:
                continue

        match_doc = {
            "match_id": fid,
            "sport": "football",
            "league": fx_raw["league"]["name"],
            "league_id": lid,
            "league_logo": fx_raw["league"].get("logo"),
            "season": season,
            "kickoff_iso": kickoff,
            "kickoff_ts": fx_raw["fixture"]["timestamp"],
            "is_live": is_live,
            "status_short": fx_raw["fixture"]["status"]["short"],
            "venue": venue,
            "home_team": {"id": home["id"], "name": home["name"], "logo": home.get("logo"), "context": ctx_home},
            "away_team": {"id": away["id"], "name": away["name"], "logo": away.get("logo"), "context": ctx_away},
            "odds_snapshots": [norm_odds] if norm_odds.get("available") else [],
            "live_stats": live_stats,
            "h2h_recent": h2h_clean,
            "data_complete": norm_odds.get("available") and bool(ctx_home.get("position") or ctx_home.get("form_last_5")),
            "fallback_used": False,
            "updated_at": nz.now_iso(),
        }
        await db.matches.update_one({"match_id": fid}, {"$set": match_doc}, upsert=True)
        if norm_odds.get("available"):
            await db.odds_snapshots.insert_one({"match_id": fid, **norm_odds})
        return match_doc
    except Exception as exc:
        log.exception("enrich_football failed: %s", exc)
        return None


async def _enrich_generic(client: httpx.AsyncClient, db, fx_raw: dict, is_live: bool, sport: str, deep: bool) -> dict | None:
    """Enrich a basketball or baseball game."""
    try:
        fid = fx_raw.get("id")
        if not fid:
            return None
        league = _fx_league(sport, fx_raw)
        lid = league.get("id")
        league_name = league.get("name")
        season = league.get("season") or aps.proxy_season(sport)
        home, away = _fx_teams(sport, fx_raw)
        kickoff = _fx_date(sport, fx_raw)
        ts = _fx_timestamp(sport, fx_raw)
        venue = _fx_venue(sport, fx_raw)
        status_short = _fx_status_short(sport, fx_raw)

        try:
            odds_resp = await aps.odds_for_fixture(sport, client, fid, db=db)
        except Exception as e:
            log.warning("[%s] odds failed for %s: %s", sport, fid, e)
            odds_resp = []
        try:
            stand_resp = await aps.standings(sport, client, lid, db=db)
        except Exception as e:
            log.warning("[%s] standings failed for league %s: %s", sport, lid, e)
            stand_resp = []

        stats_h, stats_a, h2h = {}, {}, []
        if deep:
            try: stats_h = await aps.team_statistics(sport, client, home.get("id"), lid, db=db)
            except Exception: pass
            try: stats_a = await aps.team_statistics(sport, client, away.get("id"), lid, db=db)
            except Exception: pass
            try: h2h = await aps.head_to_head(sport, client, home.get("id"), away.get("id"), limit=5, db=db)
            except Exception: pass

        norm_odds = nz.normalize_odds_generic(odds_resp, sport)
        ctx_home = nz.normalize_team_context_generic(stats_h, stand_resp, home.get("id"), sport)
        ctx_away = nz.normalize_team_context_generic(stats_a, stand_resp, away.get("id"), sport)
        live_stats = nz.normalize_live_stats_generic(fx_raw, sport) if is_live else None

        h2h_clean = []
        for hf in h2h or []:
            try:
                h_team = (hf.get("teams") or {}).get("home", {})
                a_team = (hf.get("teams") or {}).get("away", {})
                scores = hf.get("scores") or {}
                h_score = (scores.get("home") or {}).get("total")
                a_score = (scores.get("away") or {}).get("total")
                h2h_clean.append({
                    "date": hf.get("date") or (hf.get("fixture") or {}).get("date"),
                    "home": h_team.get("name"),
                    "away": a_team.get("name"),
                    "score": f"{h_score}-{a_score}" if h_score is not None else None,
                    "status": (hf.get("status") or {}).get("short"),
                })
            except Exception:
                continue

        match_doc = {
            "match_id": fid,
            "sport": sport,
            "league": league_name,
            "league_id": lid,
            "league_logo": league.get("logo"),
            "season": season,
            "kickoff_iso": kickoff,
            "kickoff_ts": ts,
            "is_live": is_live,
            "status_short": status_short,
            "venue": venue,
            "home_team": {"id": home.get("id"), "name": home.get("name"), "logo": home.get("logo"), "context": ctx_home},
            "away_team": {"id": away.get("id"), "name": away.get("name"), "logo": away.get("logo"), "context": ctx_away},
            "odds_snapshots": [norm_odds] if norm_odds.get("available") else [],
            "live_stats": live_stats,
            "h2h_recent": h2h_clean,
            "data_complete": norm_odds.get("available") and bool(ctx_home.get("position") or ctx_home.get("wins_total")),
            "fallback_used": False,
            "updated_at": nz.now_iso(),
        }
        await db.matches.update_one({"match_id": fid}, {"$set": match_doc}, upsert=True)
        if norm_odds.get("available"):
            await db.odds_snapshots.insert_one({"match_id": fid, "sport": sport, **norm_odds})
        return match_doc
    except Exception as exc:
        log.exception("enrich_generic[%s] failed: %s", sport, exc)
        return None
