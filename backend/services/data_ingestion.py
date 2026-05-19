"""Data ingestion orchestrator.

Flow:
  1) Try API-Football for fixtures/odds/context.
  2) If API fails entirely, fallback to ESPN public scoreboard for basic fixture listing.
  3) Persist normalized docs in MongoDB collections.

Collections:
  matches          (key: match_id)
  odds_snapshots   (history of odds per fixture, indexed by fixture_id + timestamp)
  team_contexts    (cache by team_id+league_id, refreshed every 6h)
  picks            (LLM output)
  pick_tracking    (user marks)
  users            (simple email/jwt local auth)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from . import api_football as af
from . import fallback_scraper as fb
from . import normalizer as nz

log = logging.getLogger("ingestion")

TOP_LEAGUES = {
    39, 140, 135, 78, 61,        # EPL, LaLiga, Serie A, Bundesliga, Ligue 1
    2, 3, 848,                    # UCL, UEL, Conference
    88, 94, 71, 128, 253, 262,    # Eredivisie, Primeira, Brasileirao, Argentina, MLS, LigaMX
    13, 11,                       # Libertadores, Sudamericana
    144, 218, 197,                # Belgium Pro, Austria, Greece
    119, 207, 113,                # Denmark, Switzerland Super, Sweden
    103, 179,                     # Norway, Scotland
}


async def ingest_upcoming(client: httpx.AsyncClient, db, max_per_league: int = 2, max_total: int = 8) -> list[dict]:
    """Ingest upcoming next-48h fixtures (top leagues priority) + odds + context. Returns normalized list."""
    try:
        upcoming_raw = await af.fixtures_next_48h(client)
    except Exception as exc:
        log.error("API-Football fixtures failed: %s -> using fallback", exc)
        upcoming_raw = []

    fallback_used = False
    if not upcoming_raw:
        log.warning("No upcoming from API-Football, attempting ESPN fallback")
        fb_data = await fb.espn_soccer_scoreboard(client)
        fallback_used = True
        # Convert minimal fallback into our shape
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
        # Persist minimal
        for m in minimal:
            await db.matches.update_one({"match_id": m["match_id"]}, {"$set": m}, upsert=True)
        return minimal

    # Prioritize top leagues
    top = [f for f in upcoming_raw if (f.get("league") or {}).get("id") in TOP_LEAGUES]
    others = [f for f in upcoming_raw if (f.get("league") or {}).get("id") not in TOP_LEAGUES]
    # Sort by kickoff_ts ascending within each group
    top.sort(key=lambda f: f.get("fixture", {}).get("timestamp", 0))
    others.sort(key=lambda f: f.get("fixture", {}).get("timestamp", 0))
    # Cap per league
    per_league: dict[int, int] = {}
    selected = []
    for f in top + others:
        lid = (f.get("league") or {}).get("id")
        if per_league.get(lid, 0) >= max_per_league:
            continue
        per_league[lid] = per_league.get(lid, 0) + 1
        selected.append(f)
        if len(selected) >= max_total:
            break

    log.info("Ingesting %d selected fixtures (top-league priority)", len(selected))
    # Serial enrichment to respect 8/min API rate limit (cache will speed up next runs)
    enriched: list[dict] = []
    for fx in selected:
        try:
            res = await enrich_fixture(client, db, fx, False)
            if res:
                enriched.append(res)
        except Exception as exc:
            log.exception("ingest enrich failed: %s", exc)
    return enriched


async def ingest_live(client: httpx.AsyncClient, db, max_total: int = 20) -> list[dict]:
    try:
        live_raw = await af.fixtures_live(client)
    except Exception as exc:
        log.error("API-Football live failed: %s", exc)
        return []
    # Prioritize top leagues
    top = [f for f in live_raw if (f.get("league") or {}).get("id") in TOP_LEAGUES]
    others = [f for f in live_raw if (f.get("league") or {}).get("id") not in TOP_LEAGUES]
    selected = (top + others)[:max_total]
    enriched = await asyncio.gather(*[enrich_fixture(client, db, f, True) for f in selected])
    return [e for e in enriched if e]


async def enrich_fixture(client: httpx.AsyncClient, db, fx_raw: dict, is_live: bool, deep: bool = False) -> dict | None:
    """Enrich a raw fixture into our normalized match doc.

    fast mode (default): odds + standings (cached per league). Marks context as partial.
    deep mode: + team_statistics + h2h + injuries. Use sparingly due to 10 req/min API limit.
    """
    try:
        fid = fx_raw["fixture"]["id"]
        lid = fx_raw["league"]["id"]
        season = fx_raw["league"]["season"]
        home = fx_raw["teams"]["home"]
        away = fx_raw["teams"]["away"]
        kickoff = fx_raw["fixture"]["date"]
        venue = (fx_raw.get("fixture", {}).get("venue") or {}).get("name")

        # Critical: odds
        try:
            odds_resp = await af.odds_for_fixture(client, fid, db=db)
        except Exception as e:
            log.warning("odds failed for %s: %s", fid, e)
            odds_resp = []

        # Standings — shared per league, cached aggressively
        try:
            stand_resp = await af.standings(client, lid, db=db)
        except Exception as e:
            log.warning("standings failed for league %s: %s", lid, e)
            stand_resp = []

        stats_h, stats_a, h2h, inj_h, inj_a = {}, {}, [], [], []
        if deep:
            try:
                stats_h = await af.team_statistics(client, home["id"], lid, db=db)
            except Exception:
                pass
            try:
                stats_a = await af.team_statistics(client, away["id"], lid, db=db)
            except Exception:
                pass
            try:
                h2h = await af.head_to_head(client, home["id"], away["id"], limit=5, db=db)
            except Exception:
                pass
            try:
                inj_h = await af.injuries(client, home["id"], db=db)
            except Exception:
                pass
            try:
                inj_a = await af.injuries(client, away["id"], db=db)
            except Exception:
                pass

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
            "league": fx_raw["league"]["name"],
            "league_id": lid,
            "league_logo": fx_raw["league"].get("logo"),
            "season": season,
            "kickoff_iso": kickoff,
            "kickoff_ts": fx_raw["fixture"]["timestamp"],
            "is_live": is_live,
            "status_short": fx_raw["fixture"]["status"]["short"],
            "venue": venue,
            "home_team": {
                "id": home["id"],
                "name": home["name"],
                "logo": home.get("logo"),
                "context": ctx_home,
            },
            "away_team": {
                "id": away["id"],
                "name": away["name"],
                "logo": away.get("logo"),
                "context": ctx_away,
            },
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
        log.exception("enrich_fixture failed: %s", exc)
        return None
