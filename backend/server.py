"""Value Bet Intelligence — FastAPI backend."""
from __future__ import annotations

import os
import logging
import uuid
import httpx
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("server")

# Mongo setup
mongo_url = os.environ["MONGO_URL"]
mongo_client = AsyncIOMotorClient(mongo_url)
db = mongo_client[os.environ.get("DB_NAME", "test_database")]

# Services (lazy imports to keep module light)
from services import data_ingestion as ingestion
from services import analyst_engine
from services import normalizer as nz
from services import auth as auth_module
from services import scheduler as scheduler_module
from services import fallback_scraper as fallback_module

# FastAPI app
app = FastAPI(title="Value Bet Intelligence", version="1.0.0")
api = APIRouter(prefix="/api")

# Build auth router + dependency
auth_router, get_current_user = auth_module.build_router(db)
api.include_router(auth_router)


# ── Lifecycle ────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup() -> None:
    await db.users.create_index("email", unique=True)
    await db.matches.create_index("match_id", unique=True)
    await db.matches.create_index("kickoff_ts")
    await db.matches.create_index("sport")
    await db.odds_snapshots.create_index([("match_id", 1), ("snapshot_at", -1)])
    await db.picks.create_index([("user_id", 1), ("generated_at", -1)])
    await db.picks.create_index([("user_id", 1), ("sport", 1), ("generated_at", -1)])
    await db.pick_tracking.create_index([("user_id", 1), ("match_id", 1), ("pick_id", 1)], unique=True)
    await auth_module.seed_demo_user(db)
    scheduler_module.start_scheduler(db)
    log.info("Startup complete")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    scheduler_module.shutdown_scheduler()
    mongo_client.close()


# ── Helpers ──────────────────────────────────────────────────────────────────
SUPPORTED_SPORTS = {"football", "basketball", "baseball"}


def _norm_sport(sport: Optional[str]) -> str:
    """Normalize/validate sport query param. Defaults to football."""
    s = (sport or "football").lower()
    return s if s in SUPPORTED_SPORTS else "football"


def _sport_filter(sport: Optional[str]) -> dict:
    """Build a Mongo filter that treats records without `sport` as football
    (for backward compatibility with pre-multi-sport data)."""
    s = _norm_sport(sport)
    if s == "football":
        return {"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]}
    return {"sport": s}


def _clean(doc: dict | None) -> dict | None:
    """Strip Mongo's _id (ObjectId) which is not JSON serializable."""
    if not doc:
        return doc
    doc.pop("_id", None)
    return doc


def _clean_list(docs: list[dict]) -> list[dict]:
    return [_clean(d) for d in docs]


# ── Public health ────────────────────────────────────────────────────────────
@api.get("/")
async def root():
    return {"app": "Value Bet Intelligence", "status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


# ── Matches ──────────────────────────────────────────────────────────────────
@api.get("/matches/upcoming")
async def matches_upcoming(refresh: bool = False, sport: Optional[str] = None, user: dict = Depends(get_current_user)):
    """List upcoming matches (next 48h) for the given sport."""
    s = _norm_sport(sport)
    if refresh:
        async with httpx.AsyncClient() as client:
            await ingestion.ingest_upcoming(client, db, sport=s)
    query = {**_sport_filter(s), "is_live": False}
    cursor = db.matches.find(query).sort("kickoff_ts", 1).limit(60)
    items = await cursor.to_list(length=60)
    now_ts = datetime.now(timezone.utc).timestamp()
    items = [i for i in items if (i.get("kickoff_ts") or 0) >= now_ts - 600]
    return {"count": len(items), "sport": s, "items": _clean_list(items)}


@api.get("/matches/live")
async def matches_live(refresh: bool = False, sport: Optional[str] = None, user: dict = Depends(get_current_user)):
    """List current live matches for the given sport."""
    s = _norm_sport(sport)
    if refresh:
        async with httpx.AsyncClient() as client:
            await ingestion.ingest_live(client, db, sport=s)
    query = {**_sport_filter(s), "is_live": True}
    cursor = db.matches.find(query).sort("kickoff_ts", 1).limit(50)
    items = await cursor.to_list(length=50)
    return {"count": len(items), "sport": s, "items": _clean_list(items)}


@api.get("/matches/{match_id}")
async def match_detail(match_id: str, user: dict = Depends(get_current_user)):
    """Get full detail of a single match (3 layers)."""
    try:
        mid: int | str = int(match_id)
    except ValueError:
        mid = match_id
    doc = await db.matches.find_one({"match_id": mid})
    if not doc:
        raise HTTPException(status_code=404, detail="match not found")
    # Fetch full odds history
    snapshots = await db.odds_snapshots.find({"match_id": mid}).sort("snapshot_at", -1).limit(10).to_list(length=10)
    doc["odds_history"] = _clean_list(snapshots)
    return _clean(doc)


# ── Analysis ─────────────────────────────────────────────────────────────────
class AnalysisRunIn(BaseModel):
    refresh: bool = True
    include_live: bool = True
    max_matches: int = Field(default=6, ge=1, le=20)
    sport: Optional[str] = "football"


@api.post("/analysis/run")
async def analysis_run(payload: AnalysisRunIn, user: dict = Depends(get_current_user)):
    """Run the LLM analyst on currently ingested matches for the chosen sport."""
    sport = _norm_sport(payload.sport)
    started = datetime.now(timezone.utc)
    ingest_error: str | None = None
    async with httpx.AsyncClient() as client:
        if payload.refresh:
            try:
                await ingestion.ingest_upcoming(client, db, sport=sport)
            except Exception as exc:
                ingest_error = f"upcoming ingest failed: {exc}"
                log.warning(ingest_error)
            if payload.include_live:
                try:
                    await ingestion.ingest_live(client, db, sport=sport)
                except Exception as exc:
                    log.warning("live ingest failed: %s", exc)

    from services.data_ingestion import enrich_fixture as enrich_fx
    from services import api_football as af
    from services import api_sports as aps
    top_set = aps.SPORT_CONFIG.get(sport, {}).get("top_leagues", set())

    query_upcoming = {**_sport_filter(sport), "is_live": False}
    upcoming = await db.matches.find(query_upcoming).sort("kickoff_ts", 1).limit(80).to_list(length=80)
    now_ts = datetime.now(timezone.utc).timestamp()
    upcoming = [c for c in upcoming if (c.get("kickoff_ts") or 0) >= now_ts - 600]

    def priority_score(m: dict) -> tuple:
        has_odds = 1 if (m.get("odds_snapshots") or []) else 0
        is_top = 1 if (m.get("league_id") in top_set) else 0
        return (-has_odds, -is_top, m.get("kickoff_ts") or 0)

    upcoming.sort(key=priority_score)
    candidates = upcoming[: payload.max_matches]

    # Deep-enrich (only football for now — basketball/baseball not yet supported by deep path's fixture_by_id)
    if sport == "football":
        async with httpx.AsyncClient() as client:
            for c in candidates[:6]:
                try:
                    raw = await af.fixture_by_id(client, c["match_id"])
                    if raw:
                        enriched = await enrich_fx(client, db, raw, c.get("is_live", False), sport=sport, deep=True)
                        if enriched:
                            for k in ["home_team", "away_team", "h2h_recent", "odds_snapshots", "data_complete"]:
                                if k in enriched:
                                    c[k] = enriched[k]
                except Exception as exc:
                    log.warning("deep enrich skipped for %s: %s", c.get("match_id"), exc)
                    continue
    else:
        # For NBA/MLB, do best-effort deep enrich via api_sports
        async with httpx.AsyncClient() as client:
            for c in candidates[:4]:
                try:
                    raw = await aps.fixture_by_id(sport, client, c["match_id"])
                    if raw:
                        enriched = await enrich_fx(client, db, raw, c.get("is_live", False), sport=sport, deep=True)
                        if enriched:
                            for k in ["home_team", "away_team", "h2h_recent", "odds_snapshots", "data_complete"]:
                                if k in enriched:
                                    c[k] = enriched[k]
                except Exception as exc:
                    log.warning("deep enrich skipped for %s [%s]: %s", c.get("match_id"), sport, exc)

    if payload.include_live:
        query_live = {**_sport_filter(sport), "is_live": True}
        live = await db.matches.find(query_live).limit(10).to_list(length=10)
        candidates.extend(live)

    candidates = candidates[: payload.max_matches]

    if not candidates:
        any_recent = await db.matches.find(_sport_filter(sport)).sort("updated_at", -1).limit(payload.max_matches).to_list(length=payload.max_matches)
        candidates = any_recent
    if not candidates:
        detail = f"no {sport} matches available"
        if ingest_error:
            detail += f" — {ingest_error}"
        raise HTTPException(status_code=409, detail=detail)

    llm_payload = [nz.summarize_match_for_llm(_clean(c)) for c in candidates]
    try:
        result = await analyst_engine.analyze_matches(llm_payload, sport=sport)
    except Exception as exc:
        log.exception("LLM analysis failed")
        raise HTTPException(status_code=502, detail=f"analyst engine error: {exc}")

    pick_id_base = uuid.uuid4().hex[:10]
    record = {
        "id": pick_id_base,
        "user_id": user["id"],
        "sport": sport,
        "generated_at": started.isoformat(),
        "matches_analyzed": len(candidates),
        "payload": result,
    }
    await db.picks.insert_one(record)
    return {"pick_run_id": pick_id_base, "sport": sport, "generated_at": record["generated_at"], "result": result}


@api.get("/picks/today")
async def picks_today(sport: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Return the most recent pick run for this user (today's picks)."""
    s = _norm_sport(sport)
    query = {"user_id": user["id"], **({"sport": s} if s != "football" else {"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]})}
    doc = await db.picks.find_one(query, sort=[("generated_at", -1)])
    if not doc:
        return {"pick_run": None, "sport": s}
    return {"pick_run": _clean(doc), "sport": s}


@api.get("/picks/history")
async def picks_history(sport: Optional[str] = None, user: dict = Depends(get_current_user), limit: int = 50):
    s = _norm_sport(sport) if sport else None
    base = {"user_id": user["id"]}
    if s:
        base.update({"sport": s} if s != "football" else {"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]})
    docs = await db.picks.find(base).sort("generated_at", -1).limit(limit).to_list(length=limit)
    out = []
    for d in docs:
        d = _clean(d)
        payload = d.get("payload", {})
        out.append({
            "id": d.get("id"),
            "sport": d.get("sport", "football"),
            "generated_at": d.get("generated_at"),
            "matches_analyzed": d.get("matches_analyzed"),
            "verdict": payload.get("verdict"),
            "total_recommended": (payload.get("summary") or {}).get("total_recommended", 0),
            "total_discarded": (payload.get("summary") or {}).get("total_discarded", 0),
        })
    return {"count": len(out), "items": out}


@api.get("/picks/run/{run_id}")
async def picks_run(run_id: str, user: dict = Depends(get_current_user)):
    doc = await db.picks.find_one({"id": run_id, "user_id": user["id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="run not found")
    return _clean(doc)


# ── Pick tracking ────────────────────────────────────────────────────────────
class TrackIn(BaseModel):
    run_id: str
    match_id: int | str
    market: str
    selection: str
    confidence_score: int
    outcome: str = Field(pattern="^(won|lost|push|pending)$")
    odds: Optional[float] = None
    league: Optional[str] = None
    match_label: Optional[str] = None
    notes: Optional[str] = None


@api.post("/picks/track")
async def track_pick(payload: TrackIn, user: dict = Depends(get_current_user)):
    pick_uid = f"{payload.run_id}-{payload.match_id}"
    doc = {
        "user_id": user["id"],
        "pick_id": pick_uid,
        "run_id": payload.run_id,
        "match_id": str(payload.match_id),
        "match_label": payload.match_label,
        "league": payload.league,
        "market": payload.market,
        "selection": payload.selection,
        "confidence_score": payload.confidence_score,
        "outcome": payload.outcome,
        "odds": payload.odds,
        "notes": payload.notes,
        "tracked_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.pick_tracking.update_one(
        {"user_id": user["id"], "match_id": str(payload.match_id), "pick_id": pick_uid},
        {"$set": doc},
        upsert=True,
    )
    return {"ok": True, "pick_id": pick_uid, "outcome": payload.outcome}


@api.get("/picks/tracked")
async def list_tracked(user: dict = Depends(get_current_user), limit: int = 200):
    docs = await db.pick_tracking.find({"user_id": user["id"]}).sort("tracked_at", -1).limit(limit).to_list(length=limit)
    return {"count": len(docs), "items": _clean_list(docs)}


@api.get("/stats/dashboard")
async def stats_dashboard(user: dict = Depends(get_current_user), stake: float = 10.0):
    docs = await db.pick_tracking.find({"user_id": user["id"]}).sort("tracked_at", -1).to_list(length=500)
    total = len(docs)
    won = sum(1 for d in docs if d.get("outcome") == "won")
    lost = sum(1 for d in docs if d.get("outcome") == "lost")
    push = sum(1 for d in docs if d.get("outcome") == "push")
    pending = sum(1 for d in docs if d.get("outcome") == "pending")
    settled = won + lost
    win_rate = round((won / settled) * 100, 1) if settled else 0.0
    # Streak (consecutive wins on most recent settled)
    streak = 0
    for d in docs:
        if d.get("outcome") == "won":
            streak += 1
        elif d.get("outcome") in ("lost",):
            break
        else:
            continue
    last10 = []
    for d in docs[:10]:
        last10.append({
            "match_label": d.get("match_label"),
            "market": d.get("market"),
            "outcome": d.get("outcome"),
            "confidence_score": d.get("confidence_score"),
            "odds": d.get("odds"),
        })

    # Accuracy + ROI by confidence tier
    tiers = {"Maxima": {"won": 0, "lost": 0, "profit": 0.0, "wagered": 0.0},
             "Alta":   {"won": 0, "lost": 0, "profit": 0.0, "wagered": 0.0},
             "Media":  {"won": 0, "lost": 0, "profit": 0.0, "wagered": 0.0}}
    total_profit = 0.0
    total_wagered = 0.0
    sum_won_odds = 0.0
    sum_lost_odds = 0.0
    for d in docs:
        cs = d.get("confidence_score", 0) or 0
        tier = "Maxima" if cs >= 80 else ("Alta" if cs >= 70 else ("Media" if cs >= 60 else None))
        outcome = d.get("outcome")
        if outcome not in ("won", "lost"):
            continue
        odds = float(d.get("odds") or 0)
        if odds <= 1.0:
            continue  # No valid odds → skip ROI math for this pick
        wagered = stake
        total_wagered += wagered
        if outcome == "won":
            profit = wagered * (odds - 1.0)
            total_profit += profit
            sum_won_odds += odds
            if tier:
                tiers[tier]["won"] += 1
                tiers[tier]["profit"] += profit
                tiers[tier]["wagered"] += wagered
        elif outcome == "lost":
            total_profit -= wagered
            sum_lost_odds += odds
            if tier:
                tiers[tier]["lost"] += 1
                tiers[tier]["profit"] -= wagered
                tiers[tier]["wagered"] += wagered

    roi_pct = round((total_profit / total_wagered) * 100, 2) if total_wagered else 0.0
    avg_won_odds = round(sum_won_odds / won, 2) if won else 0.0
    avg_lost_odds = round(sum_lost_odds / lost, 2) if lost else 0.0
    # Picks with odds (count) to know coverage
    picks_with_odds = sum(1 for d in docs if (d.get("odds") or 0) > 1.0 and d.get("outcome") in ("won", "lost"))

    accuracy_by_tier = {}
    for tier, v in tiers.items():
        settled_tier = v["won"] + v["lost"]
        accuracy_by_tier[tier] = {
            "won": v["won"],
            "lost": v["lost"],
            "settled": settled_tier,
            "rate": round((v["won"] / settled_tier) * 100, 1) if settled_tier else 0.0,
            "profit": round(v["profit"], 2),
            "wagered": round(v["wagered"], 2),
            "roi_pct": round((v["profit"] / v["wagered"]) * 100, 2) if v["wagered"] else 0.0,
        }

    return {
        "total": total,
        "won": won,
        "lost": lost,
        "push": push,
        "pending": pending,
        "win_rate": win_rate,
        "streak": streak,
        "last10": last10,
        "accuracy_by_tier": accuracy_by_tier,
        "roi": {
            "stake_per_pick": stake,
            "total_wagered": round(total_wagered, 2),
            "total_profit": round(total_profit, 2),
            "roi_pct": roi_pct,
            "avg_won_odds": avg_won_odds,
            "avg_lost_odds": avg_lost_odds,
            "settled_with_odds": picks_with_odds,
            "settled_total": settled,
        },
    }


# ── Scheduler & fallback status ──────────────────────────────────────────────
@api.get("/system/status")
async def system_status(user: dict = Depends(get_current_user)):
    return {
        "scheduler": scheduler_module.status(),
        "providers": {
            "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
            "emergent_configured": bool(os.environ.get("EMERGENT_LLM_KEY")),
            "api_football_configured": bool(os.environ.get("API_FOOTBALL_KEY")),
        },
        "now": datetime.now(timezone.utc).isoformat(),
    }


@api.get("/system/fallback-sources")
async def system_fallback_sources(user: dict = Depends(get_current_user), use_playwright: bool = False, use_browser: bool = False):
    """Run all fallback scrapers and return aggregated public-source data.

    Pass `?use_browser=true` (alias: `use_playwright=true`) to enable the
    heavier Crawlee/Playwright-based bypass for Sofascore/Flashscore
    (takes ~5-10s to launch headless Chromium with fingerprint stealth).

    Note: Sofascore's API blocks Kubernetes datacenter IPs at the application
    layer, so it will likely return 0 events even with the browser engine.
    Flashscore works reliably via Crawlee with fingerprinting.
    """
    enable_browser = use_browser or use_playwright
    async with httpx.AsyncClient() as client:
        data = await fallback_module.aggregate_fallback(client, use_playwright=enable_browser)
    summary = {k: (len(v) if isinstance(v, list) else 0) for k, v in data.items() if k not in ("generated_at", "browser_engine")}
    return {
        "summary": summary,
        "data": data,
        "browser_used": enable_browser,
        "browser_engine": data.get("browser_engine"),
    }


# ── Filters / CSV export ─────────────────────────────────────────────────────
@api.get("/picks/today/filtered")
async def picks_today_filtered(
    user: dict = Depends(get_current_user),
    sport: Optional[str] = None,
    league: Optional[str] = None,
    market: Optional[str] = None,
    min_confidence: Optional[int] = None,
):
    """Return today's pick_run filtered in-memory by sport/league/market/min_confidence."""
    s = _norm_sport(sport)
    query = {"user_id": user["id"], **({"sport": s} if s != "football" else {"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]})}
    doc = await db.picks.find_one(query, sort=[("generated_at", -1)])
    if not doc:
        return {"pick_run": None, "sport": s}
    doc = _clean(doc)
    payload = doc.get("payload", {})
    picks = payload.get("picks", []) or []

    def keep(p: dict) -> bool:
        if league and league.lower() not in (p.get("league") or "").lower():
            return False
        if market and market.lower() not in (p.get("recommendation", {}).get("market") or "").lower():
            return False
        if min_confidence is not None and (p.get("recommendation", {}).get("confidence_score") or 0) < min_confidence:
            return False
        return True

    filtered = [p for p in picks if keep(p)]
    payload2 = dict(payload)
    payload2["picks"] = filtered
    payload2["_filtered"] = {"sport": s, "league": league, "market": market, "min_confidence": min_confidence, "kept": len(filtered), "total": len(picks)}
    doc["payload"] = payload2
    return {"pick_run": doc, "sport": s}


@api.get("/picks/today/export.csv")
async def picks_today_export(sport: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Export today's picks as CSV."""
    from fastapi.responses import Response
    import csv, io
    s = _norm_sport(sport)
    query = {"user_id": user["id"], **({"sport": s} if s != "football" else {"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]})}
    doc = await db.picks.find_one(query, sort=[("generated_at", -1)])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["generated_at", "sport", "league", "match_label", "kickoff", "market", "selection", "odds_range", "confidence", "confidence_level", "is_live", "reasoning"])
    if doc:
        payload = doc.get("payload", {})
        for p in payload.get("picks", []) or []:
            rec = p.get("recommendation") or {}
            writer.writerow([
                doc.get("generated_at", ""),
                doc.get("sport", "football"),
                p.get("league", ""),
                p.get("match_label", ""),
                p.get("kickoff_iso", ""),
                rec.get("market", ""),
                rec.get("selection", ""),
                rec.get("odds_range", ""),
                rec.get("confidence_score", ""),
                rec.get("confidence_level", ""),
                p.get("is_live", False),
                (p.get("reasoning") or "").replace("\n", " ")[:500],
            ])
    return Response(content=buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=picks-{s}-today.csv"})


@api.get("/picks/tracked/export.csv")
async def picks_tracked_export(user: dict = Depends(get_current_user)):
    from fastapi.responses import Response
    import csv, io
    docs = await db.pick_tracking.find({"user_id": user["id"]}).sort("tracked_at", -1).to_list(length=2000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["tracked_at", "league", "match_label", "market", "selection", "confidence_score", "odds", "outcome", "notes"])
    for d in docs:
        writer.writerow([
            d.get("tracked_at", ""), d.get("league", ""), d.get("match_label", ""),
            d.get("market", ""), d.get("selection", ""), d.get("confidence_score", ""),
            d.get("odds", ""), d.get("outcome", ""), (d.get("notes") or "")[:300],
        ])
    return Response(content=buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=picks-tracked.csv"})


@api.get("/stats/timeline")
async def stats_timeline(user: dict = Depends(get_current_user), limit: int = 50):
    """Return chronological winrate evolution per settled pick (for chart)."""
    docs = await db.pick_tracking.find({"user_id": user["id"]}).sort("tracked_at", 1).to_list(length=limit * 4)
    cumulative_won = 0
    cumulative_settled = 0
    timeline = []
    for d in docs:
        out = d.get("outcome")
        if out not in ("won", "lost"):
            continue
        cumulative_settled += 1
        if out == "won":
            cumulative_won += 1
        rate = round((cumulative_won / cumulative_settled) * 100, 1) if cumulative_settled else 0.0
        timeline.append({
            "tracked_at": d.get("tracked_at"),
            "match_label": d.get("match_label"),
            "outcome": out,
            "confidence_score": d.get("confidence_score"),
            "cumulative_won": cumulative_won,
            "cumulative_settled": cumulative_settled,
            "win_rate": rate,
        })
    return {"count": len(timeline), "timeline": timeline[-limit:]}


@api.get("/meta/sports")
async def meta_sports(user: dict = Depends(get_current_user)):
    """Available sports + labels. Used by the frontend sport selector."""
    return {
        "sports": [
            {"id": "football", "label": "Fútbol", "label_en": "Football", "icon": "⚽"},
            {"id": "basketball", "label": "NBA / Basket", "label_en": "NBA / Basketball", "icon": "🏀"},
            {"id": "baseball", "label": "MLB / Béisbol", "label_en": "MLB / Baseball", "icon": "⚾"},
        ],
        "default": "football",
    }


@api.get("/meta/leagues")
async def meta_leagues(sport: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Distinct leagues available in the matches collection (for filter UI)."""
    if sport:
        s = _norm_sport(sport)
        flt = _sport_filter(s)
        leagues = await db.matches.distinct("league", flt)
    else:
        leagues = await db.matches.distinct("league")
    return {"leagues": sorted([l for l in leagues if l])}


# ── App registration ─────────────────────────────────────────────────────────
app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
