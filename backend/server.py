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
    await db.odds_snapshots.create_index([("match_id", 1), ("snapshot_at", -1)])
    await db.picks.create_index([("user_id", 1), ("generated_at", -1)])
    await db.pick_tracking.create_index([("user_id", 1), ("match_id", 1), ("pick_id", 1)], unique=True)
    await auth_module.seed_demo_user(db)
    scheduler_module.start_scheduler(db)
    log.info("Startup complete")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    scheduler_module.shutdown_scheduler()
    mongo_client.close()


# ── Helpers ──────────────────────────────────────────────────────────────────
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
async def matches_upcoming(refresh: bool = False, user: dict = Depends(get_current_user)):
    """List upcoming matches (next 48h)."""
    if refresh:
        async with httpx.AsyncClient() as client:
            await ingestion.ingest_upcoming(client, db)
    cursor = db.matches.find({"is_live": False}).sort("kickoff_ts", 1).limit(60)
    items = await cursor.to_list(length=60)
    # Filter to future-only
    now_ts = datetime.now(timezone.utc).timestamp()
    items = [i for i in items if (i.get("kickoff_ts") or 0) >= now_ts - 600]
    return {"count": len(items), "items": _clean_list(items)}


@api.get("/matches/live")
async def matches_live(refresh: bool = False, user: dict = Depends(get_current_user)):
    """List current live matches."""
    if refresh:
        async with httpx.AsyncClient() as client:
            await ingestion.ingest_live(client, db)
    cursor = db.matches.find({"is_live": True}).sort("kickoff_ts", 1).limit(50)
    items = await cursor.to_list(length=50)
    return {"count": len(items), "items": _clean_list(items)}


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


@api.post("/analysis/run")
async def analysis_run(payload: AnalysisRunIn, user: dict = Depends(get_current_user)):
    """Run the LLM analyst on currently ingested matches.

    Steps:
      1. (optional) refresh ingestion
      2. Gather candidate matches (upcoming + optionally live)
      3. Build compact LLM payload
      4. Call Claude Sonnet 4.5 via Emergent
      5. Persist picks
    """
    started = datetime.now(timezone.utc)
    async with httpx.AsyncClient() as client:
        if payload.refresh:
            await ingestion.ingest_upcoming(client, db)
            if payload.include_live:
                await ingestion.ingest_live(client, db)

    # Top leagues priority (mirror ingestion list)
    from services.data_ingestion import TOP_LEAGUES, enrich_fixture as enrich_fx
    from services import api_football as af

    # Gather candidates — prioritize WITH odds + TOP leagues
    upcoming = await db.matches.find({"is_live": False}).sort("kickoff_ts", 1).limit(80).to_list(length=80)
    now_ts = datetime.now(timezone.utc).timestamp()
    upcoming = [c for c in upcoming if (c.get("kickoff_ts") or 0) >= now_ts - 600]

    def priority_score(m: dict) -> tuple:
        has_odds = 1 if (m.get("odds_snapshots") or []) else 0
        is_top = 1 if (m.get("league_id") in TOP_LEAGUES) else 0
        return (-has_odds, -is_top, m.get("kickoff_ts") or 0)

    upcoming.sort(key=priority_score)
    candidates = upcoming[: payload.max_matches]

    # Deep-enrich the top candidates (team stats etc) — best effort.
    async with httpx.AsyncClient() as client:
        for c in candidates[:6]:  # cap deep enrichment to first 6 to respect rate limits
            try:
                # Re-fetch raw fixture by id, then enrich deep
                raw = await af.fixture_by_id(client, c["match_id"])
                if raw:
                    enriched = await enrich_fx(client, db, raw, c.get("is_live", False), deep=True)
                    if enriched:
                        # Replace candidate doc with enriched version
                        for k in ["home_team", "away_team", "h2h_recent", "odds_snapshots", "data_complete"]:
                            if k in enriched:
                                c[k] = enriched[k]
            except Exception as exc:
                log.warning("deep enrich failed for %s: %s", c.get("match_id"), exc)

    if payload.include_live:
        live = await db.matches.find({"is_live": True}).limit(10).to_list(length=10)
        candidates.extend(live)

    candidates = candidates[: payload.max_matches]

    if not candidates:
        raise HTTPException(status_code=409, detail="no matches available — try refresh=true")

    # Compact payload for LLM
    llm_payload = [nz.summarize_match_for_llm(_clean(c)) for c in candidates]
    try:
        result = await analyst_engine.analyze_matches(llm_payload)
    except Exception as exc:
        log.exception("LLM analysis failed")
        raise HTTPException(status_code=502, detail=f"analyst engine error: {exc}")

    # Persist run
    pick_id_base = uuid.uuid4().hex[:10]
    record = {
        "id": pick_id_base,
        "user_id": user["id"],
        "generated_at": started.isoformat(),
        "matches_analyzed": len(candidates),
        "payload": result,
    }
    await db.picks.insert_one(record)
    return {"pick_run_id": pick_id_base, "generated_at": record["generated_at"], "result": result}


@api.get("/picks/today")
async def picks_today(user: dict = Depends(get_current_user)):
    """Return the most recent pick run for this user (today's picks)."""
    doc = await db.picks.find_one({"user_id": user["id"]}, sort=[("generated_at", -1)])
    if not doc:
        return {"pick_run": None}
    return {"pick_run": _clean(doc)}


@api.get("/picks/history")
async def picks_history(user: dict = Depends(get_current_user), limit: int = 50):
    docs = await db.picks.find({"user_id": user["id"]}).sort("generated_at", -1).limit(limit).to_list(length=limit)
    # Slim docs for list view
    out = []
    for d in docs:
        d = _clean(d)
        payload = d.get("payload", {})
        out.append({
            "id": d.get("id"),
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
async def stats_dashboard(user: dict = Depends(get_current_user)):
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
        })
    # Accuracy by confidence tier
    tiers = {"Maxima": [0, 0], "Alta": [0, 0], "Media": [0, 0]}
    for d in docs:
        cs = d.get("confidence_score", 0) or 0
        tier = "Maxima" if cs >= 88 else ("Alta" if cs >= 78 else ("Media" if cs >= 68 else "Other"))
        if tier in tiers and d.get("outcome") in ("won", "lost"):
            tiers[tier][1] += 1
            if d.get("outcome") == "won":
                tiers[tier][0] += 1
    accuracy_by_tier = {
        tier: {
            "won": v[0],
            "settled": v[1],
            "rate": round((v[0] / v[1]) * 100, 1) if v[1] else 0.0,
        }
        for tier, v in tiers.items()
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
async def system_fallback_sources(user: dict = Depends(get_current_user)):
    """Run all fallback scrapers and return aggregated public-source data."""
    async with httpx.AsyncClient() as client:
        data = await fallback_module.aggregate_fallback(client)
    summary = {k: (len(v) if isinstance(v, list) else 0) for k, v in data.items() if k != "generated_at"}
    return {"summary": summary, "data": data}


# ── Filters / CSV export ─────────────────────────────────────────────────────
@api.get("/picks/today/filtered")
async def picks_today_filtered(
    user: dict = Depends(get_current_user),
    league: Optional[str] = None,
    market: Optional[str] = None,
    min_confidence: Optional[int] = None,
):
    """Return today's pick_run filtered in-memory by league/market/min_confidence."""
    doc = await db.picks.find_one({"user_id": user["id"]}, sort=[("generated_at", -1)])
    if not doc:
        return {"pick_run": None}
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
    payload2["_filtered"] = {"league": league, "market": market, "min_confidence": min_confidence, "kept": len(filtered), "total": len(picks)}
    doc["payload"] = payload2
    return {"pick_run": doc}


@api.get("/picks/today/export.csv")
async def picks_today_export(user: dict = Depends(get_current_user)):
    """Export today's picks as CSV."""
    from fastapi.responses import Response
    import csv, io
    doc = await db.picks.find_one({"user_id": user["id"]}, sort=[("generated_at", -1)])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["generated_at", "league", "match_label", "kickoff", "market", "selection", "odds_range", "confidence", "confidence_level", "is_live", "reasoning"])
    if doc:
        payload = doc.get("payload", {})
        for p in payload.get("picks", []) or []:
            rec = p.get("recommendation") or {}
            writer.writerow([
                doc.get("generated_at", ""),
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
    return Response(content=buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=picks-today.csv"})


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


@api.get("/meta/leagues")
async def meta_leagues(user: dict = Depends(get_current_user)):
    """Distinct leagues available in the matches collection (for filter UI)."""
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
