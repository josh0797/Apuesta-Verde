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
from fastapi.responses import JSONResponse
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
from services import job_queue

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
    await db.analysis_jobs.create_index([("user_id", 1), ("created_at", -1)])
    await db.analysis_jobs.create_index("id", unique=True)
    await db.saved_views.create_index([("user_id", 1), ("created_at", -1)])
    await db.saved_views.create_index("id", unique=True)
    await auth_module.seed_demo_user(db)
    # Knowledge Base — seed the user-validated learning cases (idempotent).
    try:
        from services import learning_cases as lc
        inserted = await lc.seed_cases(db)
        if inserted:
            log.info("[LEARNING_CASES] seeded %d new cases", inserted)
    except Exception as exc:
        log.warning("[LEARNING_CASES] seed failed: %s", exc)
    scheduler_module.start_scheduler(db)
    # Mark any jobs orphaned by the previous process as failed.
    try:
        await job_queue.cleanup_stale(db, max_age_minutes=0)  # everything still "running" is now stale
    except Exception as e:
        log.warning("startup cleanup_stale failed: %s", e)
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


def _normalize_keys_for_bson(value):
    """Recursively coerce all dict keys to strings.

    PyMongo / BSON refuses documents with numeric (or any non-string) keys
    with the misleading error: `documents must have only string keys, key
    was 1`. Several upstream payloads can sneak ints in:

      • services.football_quality.filter_and_prioritize → stats.by_tier
        (historically `{1: …, 2: …}`); also patched at the source but kept
        here as defense-in-depth in case a future contributor reintroduces
        the bug.
      • LLM JSON responses that key things by season year (`{2024: …}`).
      • Scraped JSON feeds (Sofascore/Flashscore) that use numeric ids.

    Lists are traversed; primitives are returned untouched. Tuple/set keys
    are stringified via `str()`. This helper is idempotent and cheap (only
    touches dict containers).
    """
    if isinstance(value, dict):
        return {str(k): _normalize_keys_for_bson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_keys_for_bson(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_normalize_keys_for_bson(v) for v in value)
    return value


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
    """List current live matches for the given sport.

    Strict in-play validation via services/live_lifecycle:
      - Only matches with a valid LIVE_STATUSES status pass.
      - Heartbeat age must be < HEARTBEAT_STALE_SEC[sport].
      - Football: minute < 105 and never `2H @ ≥95'`.
      - Per-match `_live_state` (state + minute_label + reason) and
        `_freshness` (0-100 score + label) are attached so the UI can
        render live state badges + filter stale entries client-side.
    Stale matches are auto-flipped to is_live=False (sweeper) and
    archived to `archived_live_matches`.
    """
    from services import live_lifecycle as ll
    from services import live_xg_proxy as lxp
    s = _norm_sport(sport)
    if refresh:
        async with httpx.AsyncClient() as client:
            await ingestion.ingest_live(client, db, sport=s)

    # Run the sweeper first so we never serve known-stale rows.
    try:
        flipped = await ll.sweep_expired_live(db, sport=s)
        if flipped:
            logging.getLogger("live").info("matches_live sweep flipped %d stale rows (sport=%s)", flipped, s)
    except Exception as exc:
        logging.getLogger("live").warning("sweep_expired_live failed: %s", exc)

    query = {**_sport_filter(s), "is_live": True}
    cursor = db.matches.find(query).sort("kickoff_ts", 1).limit(50)
    items_raw = await cursor.to_list(length=50)

    items: list[dict] = []
    archived: list[dict] = []
    for m in items_raw:
        if not ll.is_match_live(m):
            archived.append({
                "match_id": m.get("match_id"),
                "reason": ll.compute_live_state(m).get("reason"),
            })
            continue
        m["_live_state"] = ll.compute_live_state(m)
        m["_freshness"] = ll.compute_freshness(m)
        # P3 — attach the full xG-proxy + threat + pressure + trap analysis
        # so the UI can render the recommendation strip without an extra
        # /api/live/reevaluate roundtrip per card. Football only — basket /
        # baseball get None and the UI skips the strip.
        if s == "football":
            try:
                m["_live_analysis"] = lxp.compute_live_analysis(m)
                # P3.1 — HumanLiveInterpreter: convert raw metrics into a
                # coach-style recommendation (title, action, why, narration,
                # suggested market). Attached separately so callers that only
                # want the raw analysis can ignore it.
                try:
                    from services import human_live_interpreter as hli
                    from services import under_market_scan as ums
                    alt = None
                    try:
                        alt = ums.scan_protected_alternatives(m, live_analysis=m.get("_live_analysis"))
                    except Exception:
                        alt = None
                    m["_live_interpreter"] = hli.interpret_live(
                        m, analysis=m["_live_analysis"], reeval=None, alt_market=alt,
                    )
                except Exception as exc2:
                    logging.getLogger("live").warning("human_live_interpreter failed for %s: %s", m.get("match_id"), exc2)
                    m["_live_interpreter"] = None
            except Exception as exc:
                logging.getLogger("live").warning("live_xg_proxy failed for %s: %s", m.get("match_id"), exc)
                m["_live_analysis"] = None
                m["_live_interpreter"] = None
        elif s == "basketball":
            # P4 — basketball live analytics + copilot. Same shape as
            # football so the UI doesn't branch on sport.
            try:
                from services import live_basketball_analytics as lba
                m["_live_analysis"] = lba.compute_live_analysis(m)
                try:
                    from services import human_live_interpreter as hli
                    m["_live_interpreter"] = hli.interpret_live(
                        m, analysis=m["_live_analysis"], reeval=None, alt_market=None,
                    )
                except Exception as exc2:
                    logging.getLogger("live").warning("basketball interpreter failed for %s: %s", m.get("match_id"), exc2)
                    m["_live_interpreter"] = None
            except Exception as exc:
                logging.getLogger("live").warning("live_basketball_analytics failed for %s: %s", m.get("match_id"), exc)
                m["_live_analysis"] = None
                m["_live_interpreter"] = None
        elif s == "baseball":
            # P5 — baseball live analytics + MLB-language copilot. Same shape
            # as football/basketball so the UI doesn't branch on sport.
            try:
                from services import live_baseball_analytics as lba_bb
                m["_live_analysis"] = lba_bb.compute_live_analysis(m)
                try:
                    from services import human_live_interpreter as hli
                    m["_live_interpreter"] = hli.interpret_live(
                        m, analysis=m["_live_analysis"], reeval=None, alt_market=None,
                    )
                except Exception as exc2:
                    logging.getLogger("live").warning("baseball interpreter failed for %s: %s", m.get("match_id"), exc2)
                    m["_live_interpreter"] = None
            except Exception as exc:
                logging.getLogger("live").warning("live_baseball_analytics failed for %s: %s", m.get("match_id"), exc)
                m["_live_analysis"] = None
                m["_live_interpreter"] = None

        # ── Final defense: sport-vocab firewall on the live payload ────
        # Even with sport-specific interpreters, a future regression could
        # leak football vocabulary into a basketball/baseball card. We scan
        # the interpreter output here and null it out if a leak is detected
        # so the UI never renders "Más de 2.5 goles" on an MLB card.
        try:
            from services import sport_vocab_guard as _svg
            if m.get("_live_interpreter"):
                leaks = _svg.detect_vocab_leaks(
                    {"recommendation": m["_live_interpreter"], "risks": m["_live_interpreter"].get("risks") or []},
                    s,
                )
                if leaks:
                    logging.getLogger("live").warning(
                        "sport_vocab_guard[live %s]: interpreter for %s had leaks=%s — nulled",
                        s, m.get("match_id"), leaks,
                    )
                    m["_live_interpreter"] = {
                        "_blocked_by_sport_vocab_guard": True,
                        "forbidden_terms_found":         leaks,
                        "sport":                         s,
                    }
        except Exception:
            pass

        items.append(m)

    return {
        "count":         len(items),
        "sport":         s,
        "items":         _clean_list(items),
        "archived_count": len(archived),
        "cache_ttl_sec": ll.LIVE_CACHE_TTL_SEC.get(s, 60),
        "computed_at":   datetime.now(timezone.utc).isoformat(),
    }


class LiveReevalRequest(BaseModel):
    """Body for POST /api/live/reevaluate.

    `manual_odds` + `manual_market` are the user-provided cuota route — when
    set, the engine computes edge against the EXACT odds the user sees at
    their bookie instead of the pre-match approximation. Both must be
    supplied together; passing only one is rejected.
    """
    match_id: str | int
    sport: Optional[str] = "football"
    refresh: bool = True
    manual_odds: Optional[float] = None
    manual_market: Optional[str] = None
    expected_goals_total: Optional[float] = None


@api.post("/live/reevaluate")
async def live_reevaluate(req: LiveReevalRequest, user: dict = Depends(get_current_user)):
    """Phase 10 — refresh ESPN/API-Sports for this match, then run
    `live_reevaluation.reevaluate_match()` and return the verdict.

    The output is persisted in `db.live_reevaluations` (last 50 per user) so
    we can later show a per-match history of how the recommendation evolved.
    """
    from services import live_reevaluation as lre

    sport = _norm_sport(req.sport or "football")
    if sport == "baseball":
        # Baseball re-evaluation: implementado vía live_baseball_analytics +
        # _reevaluate_baseball. El dispatcher en reevaluate_match() lo enruta.
        pass

    if (req.manual_odds is not None) ^ (req.manual_market is not None):
        raise HTTPException(
            status_code=400,
            detail="manual_odds and manual_market must be provided TOGETHER (or both omitted).",
        )
    if req.manual_odds is not None and req.manual_odds <= 1.01:
        raise HTTPException(status_code=400, detail="manual_odds must be > 1.01.")

    # Refresh the live doc (best-effort — if API-Sports / ESPN times out we
    # fall back to whatever's cached in db.matches).
    if req.refresh:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await ingestion.ingest_live(client, db, sport=sport)
        except Exception as exc:
            log.warning("live re-eval refresh failed: %s — using cached doc", exc)

    # Resolve match_id flexibly (string OR int — historical inconsistency).
    candidates = []
    try:
        candidates.append(int(req.match_id))
    except (TypeError, ValueError):
        pass
    candidates.append(str(req.match_id))
    match = await db.matches.find_one({"match_id": {"$in": candidates}})
    if not match:
        raise HTTPException(status_code=404, detail=f"match {req.match_id} not found")

    # Strict lifecycle gate — refuse to re-evaluate matches that are no
    # longer in-play. Returns 409 (Conflict) with the lifecycle state so
    # the UI can render a "Live finalizado / stale" message instead of
    # showing a misleading edge calculation.
    from services import live_lifecycle as ll
    live_state = ll.compute_live_state(match)
    if not live_state.get("valid"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "live_match_not_active",
                "message": f"El partido no está en juego ahora ({live_state.get('state')}). {live_state.get('reason') or ''}".strip(),
                "live_state": live_state,
            },
        )

    xg = req.expected_goals_total or 2.5
    result = lre.reevaluate_match(
        match,
        manual_odds=req.manual_odds,
        manual_market=req.manual_market,
        expected_goals_total=xg,
    )
    # P3.1 — El servicio live_reevaluation ya construye result["interpreter"]
    # internamente. Aquí intentamos enriquecerlo con alt_market (Under 3.5/2.5)
    # usando un timeout duro de 3s para no consumir el budget del endpoint.
    # Si scan_protected_alternatives tarda más de 3s o falla, se omite
    # silenciosamente — el interpreter ya construido por el servicio se conserva.
    try:
        from services import human_live_interpreter as hli
        from services import under_market_scan as ums

        # Intentar obtener alt_market con timeout duro.
        # scan_protected_alternatives puede hacer I/O (API-Sports hydration)
        # así que lo envolvemos en asyncio.wait_for con 3s máximo.
        alt = None
        try:
            loop = asyncio.get_event_loop()
            alt = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: ums.scan_protected_alternatives(
                        match, live_analysis=result.get("live_analysis")
                    ),
                ),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            log.info("scan_protected_alternatives timeout (>3s) — skipping alt_market")
        except Exception as exc_alt:
            log.info("scan_protected_alternatives skipped: %s", exc_alt)

        # Solo re-llamar al interpreter si tenemos alt_market nuevo O si el
        # servicio no produjo interpreter (fallback defensivo).
        if alt is not None or result.get("interpreter") is None:
            analysis_block = result.get("live_analysis")
            enriched = hli.interpret_live(
                match,
                analysis=analysis_block,
                reeval=result,
                alt_market=alt,
            )
            # Conservar el interpreter del servicio si alt es None y ya existía
            # uno válido — solo reemplazar si alt enriquece o si era None.
            if alt is not None or result.get("interpreter") is None:
                result["interpreter"] = enriched

    except Exception as exc:
        log.warning("interpreter enrichment failed in reevaluate: %s", exc)
        # Nunca sobreescribir con None — conservar lo que construyó el servicio.
    # Persist (with BSON-safe normalization) — keep latest 50 per user.
    record = _normalize_keys_for_bson({
        "id": f"lre_{user['id']}_{req.match_id}_{datetime.now(timezone.utc).timestamp():.0f}",
        "user_id": user["id"],
        "match_id": req.match_id,
        "sport": sport,
        "manual_odds": req.manual_odds,
        "manual_market": req.manual_market,
        "result": result,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        await db.live_reevaluations.insert_one(record)
        # Trim history per user.
        total = await db.live_reevaluations.count_documents({"user_id": user["id"]})
        if total > 50:
            old = await db.live_reevaluations.find(
                {"user_id": user["id"]}
            ).sort("created_at", 1).limit(total - 50).to_list(length=total)
            await db.live_reevaluations.delete_many({"_id": {"$in": [d["_id"] for d in old]}})
    except Exception as exc:
        log.warning("live reeval persist failed: %s", exc)

    return {"result": result, "match_id": req.match_id, "sport": sport}


@api.get("/matches/{match_id}")
async def match_detail(match_id: str, user: dict = Depends(get_current_user)):
    """Get full detail of a single match (3 layers).

    Robust to ID-shape mismatch: MLB matches are stored with a `str`
    `match_id` (the gamePk as text) while football/basketball matches
    historically used `int`. We probe both shapes so the user never
    lands on a 404 just because of casting.
    """
    candidates: list = []
    # Try the raw string first (preserves leading zeros / hashes).
    candidates.append(match_id)
    # Then try the int form when parseable.
    try:
        mid_int = int(match_id)
        if mid_int not in candidates:
            candidates.append(mid_int)
    except (ValueError, TypeError):
        pass

    doc = await db.matches.find_one({"match_id": {"$in": candidates}})
    # Last-resort: maybe the match was archived to `archived_live_matches`
    # after the live feed closed it. Look there too so the detail page
    # works for recently-finished games.
    if not doc:
        try:
            doc = await db.archived_live_matches.find_one(
                {"match_id": {"$in": candidates}}
            )
        except Exception:
            doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="match not found")

    # Fetch full odds history using whichever ID variant matches docs.
    snapshots: list = []
    try:
        snapshots = await db.odds_snapshots.find(
            {"match_id": {"$in": candidates}}
        ).sort("snapshot_at", -1).limit(10).to_list(length=10)
    except Exception:
        snapshots = []
    doc["odds_history"] = _clean_list(snapshots)
    return _clean(doc)


# ════════════════════════════════════════════════════════════════════════════
# Live Territorial Control + Corner Intelligence (FOOTBALL ONLY)
# ════════════════════════════════════════════════════════════════════════════
class LiveTerritorialControlIn(BaseModel):
    """Body for POST /api/football/live/territorial_control.

    Either provide ``match_id`` (the engine pulls the latest live metrics
    from `db.matches`) OR pass ``metrics`` directly (useful for testing).
    Both are accepted; ``metrics`` overrides any field derived from the
    match document.
    """
    match_id: Optional[str | int] = None
    metrics: Optional[dict] = None
    refresh: bool = False
    surface_threshold: int = 55


def _extract_live_metrics_from_match(doc: dict) -> dict:
    """Build the metrics dict consumed by live_territorial_control out of
    a `db.matches` document. Tolerates the API-Sports / ESPN heterogeneity.
    """
    if not doc:
        return {}
    home = doc.get("home_team") or doc.get("homeTeam") or {}
    away = doc.get("away_team") or doc.get("awayTeam") or {}
    home_name = home.get("name") if isinstance(home, dict) else str(home or "")
    away_name = away.get("name") if isinstance(away, dict) else str(away or "")

    # Score: try multiple paths.
    score = doc.get("score") or doc.get("goals") or {}
    sh = score.get("home") if isinstance(score, dict) else None
    sa = score.get("away") if isinstance(score, dict) else None
    if sh is None or sa is None:
        sh = (doc.get("goals_home") or 0)
        sa = (doc.get("goals_away") or 0)

    # Minute.
    status = doc.get("status") or {}
    minute = status.get("elapsed") if isinstance(status, dict) else doc.get("minute")

    # Stats (API-Sports). Two-element list per match: [home_stats, away_stats].
    stats = doc.get("statistics") or doc.get("stats") or []
    s_home: dict = {}
    s_away: dict = {}
    if isinstance(stats, list) and len(stats) >= 2:
        # API-Sports shape: each item has { team, statistics: [{type, value}] }
        for item in stats:
            team_meta = (item or {}).get("team") or {}
            team_name = team_meta.get("name") if isinstance(team_meta, dict) else None
            bucket = {}
            for s in (item.get("statistics") or []):
                key = (s.get("type") or "").strip().lower()
                val = s.get("value")
                if isinstance(val, str) and val.endswith("%"):
                    try:
                        val = float(val.rstrip("%"))
                    except (TypeError, ValueError):
                        val = 0
                bucket[key] = val
            if team_name and home_name and team_name.lower() == home_name.lower():
                s_home = bucket
            elif team_name and away_name and team_name.lower() == away_name.lower():
                s_away = bucket

    def _stat(bucket: dict, *keys: str, default=0):
        for k in keys:
            if k in bucket and bucket[k] is not None:
                return bucket[k]
        return default

    # Threat index (xT) — already computed by live_xg_proxy if available.
    xt = doc.get("threat_index") or {}
    xt_home = xt.get("home") if isinstance(xt, dict) else 0
    xt_away = xt.get("away") if isinstance(xt, dict) else 0

    # Running xG.
    xg = doc.get("expected_goals") or doc.get("xg") or {}
    xg_home = xg.get("home") if isinstance(xg, dict) else 0
    xg_away = xg.get("away") if isinstance(xg, dict) else 0

    return {
        "minute":                 minute or 0,
        "score_home":             sh,
        "score_away":             sa,
        "home_team":              home_name,
        "away_team":              away_name,
        "possession_home":        _stat(s_home, "ball possession", "possession"),
        "possession_away":        _stat(s_away, "ball possession", "possession"),
        "shots_home":             _stat(s_home, "total shots", "shots"),
        "shots_away":             _stat(s_away, "total shots", "shots"),
        "shots_on_target_home":   _stat(s_home, "shots on goal", "shots on target"),
        "shots_on_target_away":   _stat(s_away, "shots on goal", "shots on target"),
        "corners_home":           _stat(s_home, "corner kicks", "corners"),
        "corners_away":           _stat(s_away, "corner kicks", "corners"),
        "dangerous_attacks_home": _stat(s_home, "dangerous attacks"),
        "dangerous_attacks_away": _stat(s_away, "dangerous attacks"),
        "attacks_home":           _stat(s_home, "attacks"),
        "attacks_away":           _stat(s_away, "attacks"),
        "xt_home":                xt_home or 0,
        "xt_away":                xt_away or 0,
        "xg_home":                xg_home or 0,
        "xg_away":                xg_away or 0,
    }


@api.post("/football/live/territorial_control")
async def football_live_territorial_control(
    payload: LiveTerritorialControlIn,
    user: dict = Depends(get_current_user),
):
    """Evaluate the LIVE Territorial Control + Corner Intelligence engines
    for a football match and return the full payload (territorial state,
    corner pressure, ranked live markets).

    Persists each evaluation into `db.live_territorial_evaluations` for
    later feedback-loop calibration.
    """
    try:
        from services.live_territorial_control import evaluate_live_territorial_control
        from services.live_corner_engine import evaluate_corner_pressure
        from services.live_market_ranker import rank_live_markets
        from services.live_evaluation_storage import store_live_territorial_evaluation

        # Resolve metrics — explicit override wins, else load from db.matches.
        metrics = dict(payload.metrics or {})
        match_doc = None
        if payload.match_id is not None:
            candidates = [str(payload.match_id)]
            try:
                candidates.append(int(payload.match_id))
            except (TypeError, ValueError):
                pass
            match_doc = await db.matches.find_one({"match_id": {"$in": candidates}})
            if not match_doc and not metrics:
                raise HTTPException(
                    status_code=404,
                    detail=f"match {payload.match_id} not found and no explicit metrics provided",
                )
            if match_doc:
                derived = _extract_live_metrics_from_match(match_doc)
                # Explicit `metrics` overrides per-field.
                for k, v in derived.items():
                    metrics.setdefault(k, v)

        if not metrics:
            raise HTTPException(
                status_code=400,
                detail="Either `match_id` or `metrics` must be provided.",
            )

        # Run the three pure-function layers.
        territorial = evaluate_live_territorial_control(metrics)
        corner      = evaluate_corner_pressure(
            metrics, surface_threshold=int(payload.surface_threshold))
        ranked      = rank_live_markets(metrics, territorial, corner)

        # ── V2 — LIVE CORNER MARKET INTELLIGENCE (full recommendation) ──
        # In addition to the raw corner-pressure payload above, compute the
        # rich corner_recommendation object that powers the new "Corner
        # Pressure Intelligence" UI card. This evaluates the 2 sub-states
        # (TERRITORIAL_CONTROL_WITH_CORNER_PRESSURE / CONTROL_WITHOUT_GOAL_DEPTH),
        # the PSG-Arsenal benchmark, the downgrade-when-leading rule, and
        # produces: should_recommend, recommended_market, confidence, risk,
        # reason_codes, human_reasons, explanation, avoid_markets.
        corner_recommendation = None
        corner_eval_id = None
        try:
            from services.live_corner_engine import evaluate_live_corner_market
            from services.live_corner_storage import store_live_corner_evaluation
            corner_recommendation = evaluate_live_corner_market(
                metrics,
                surface_threshold=int(payload.surface_threshold),
            )
            # Persist strong recommendations into the dedicated
            # `live_corner_evaluations` collection.
            corner_eval_id = await store_live_corner_evaluation(
                db,
                user_id=user["id"],
                match_id=payload.match_id if payload.match_id is not None else metrics.get("match_id"),
                corner_recommendation=corner_recommendation,
                metrics=metrics,
                only_strong=True,
            )
        except Exception as _exc:
            log.debug("corner_recommendation pipeline failed (non-fatal): %s", _exc)

        # Persist (fail-soft).
        eval_id = await store_live_territorial_evaluation(
            db,
            user_id=user["id"],
            match_id=payload.match_id if payload.match_id is not None else metrics.get("match_id"),
            sport="football",
            metrics=metrics,
            territorial=territorial,
            corner=corner,
            ranked_markets=ranked,
        )

        return {
            "ok":                  True,
            "evaluation_id":       eval_id,
            "corner_evaluation_id": corner_eval_id,
            "match_id":            payload.match_id,
            "territorial":         territorial,
            "corner":              corner,
            "corner_recommendation": corner_recommendation,
            "ranked_markets":      ranked,
            "metrics":             metrics,
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("football_live_territorial_control failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


@api.get("/football/live/corner_intelligence/history")
async def football_live_corner_intelligence_history(
    user: dict = Depends(get_current_user),
    match_id: Optional[str] = None,
    reference_only: bool = False,
    limit: int = 30,
):
    """Return recent Live Corner Intelligence evaluations for the user.

    Query params:
      • match_id        — optional filter
      • reference_only  — if true, only documents tagged with
                          REFERENCE_LIVE_CORNER_PRESSURE_PROFILE
      • limit           — capped at 100, default 30.
    """
    try:
        from services.live_corner_storage import query_corner_evaluations
        docs = await query_corner_evaluations(
            db,
            user_id=user["id"],
            match_id=match_id,
            reference_only=bool(reference_only),
            limit=max(1, min(100, int(limit or 30))),
        )
        return {
            "ok":     True,
            "count":  len(docs),
            "items":  docs,
        }
    except Exception as exc:
        log.exception("football_live_corner_intelligence_history failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


@api.get("/football/live/territorial_control/history")
async def football_live_territorial_control_history(
    user: dict = Depends(get_current_user),
    match_id: Optional[str] = None,
    limit: int = 30,
):
    """Return recent Live Territorial Control evaluations for the user.

    Optionally filter by ``match_id``. Caps at 100 docs.
    """
    try:
        from services.live_evaluation_storage import query_live_territorial_evaluations
        docs = await query_live_territorial_evaluations(
            db,
            user_id=user["id"],
            match_id=match_id,
            limit=max(1, min(100, int(limit or 30))),
        )
        return {
            "ok":    True,
            "count": len(docs),
            "items": docs,
        }
    except Exception as exc:
        log.exception("football_live_territorial_control_history failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


# ── Analysis ─────────────────────────────────────────────────────────────────
class AnalysisRunIn(BaseModel):
    refresh: bool = True
    include_live: bool = True
    max_matches: int = Field(default=12, ge=1, le=30)
    sport: Optional[str] = "football"
    background: bool = False  # if True, run as async job and return job_id
    # New: focused live analysis for football's Big Five leagues.
    live_only: bool = False         # if True, analyze only live matches (no upcoming)
    big_five_only: bool = False     # if True, keep only Premier/LaLiga/Serie A/Bundesliga/Ligue 1


async def _run_analysis_pipeline(
    user_id: str,
    sport: str,
    refresh: bool,
    include_live: bool,
    max_matches: int,
    progress_cb=None,
    live_only: bool = False,
    big_five_only: bool = False,
) -> dict:
    """Execute the full LLM analysis pipeline.

    `progress_cb(stage, progress, message)` is awaited at each checkpoint so the
    caller can stream status to a polling endpoint. For sync callers, pass a
    no-op (or None).
    """
    from services.data_ingestion import enrich_fixture as enrich_fx
    from services import api_football as af
    from services import api_sports as aps

    async def _emit(stage: str, pct: int, msg: str) -> None:
        if progress_cb is not None:
            try:
                await progress_cb(stage, pct, msg)
            except Exception:
                pass

    started = datetime.now(timezone.utc)
    ingest_error: str | None = None
    top_set = aps.SPORT_CONFIG.get(sport, {}).get("top_leagues", set())
    # Phase 8.1 — declared up here so the no-candidates branch can decide
    # whether to emit a friendly NO_PRIORITY_FIXTURES_FOUND error.
    priority_fixtures: list[dict] = []
    priority_leagues_hit: list[str] = []

    # Pipeline debug (Eastern Time for MLB; UTC for everything else).
    # Built progressively so the final result carries actionable
    # context (cache_status, abort_reason, drop counters) for the UI.
    try:
        from zoneinfo import ZoneInfo as _ZI
        _eastern = _ZI("America/New_York")
    except Exception:
        _eastern = None
    if sport == "baseball" and _eastern is not None:
        _pmeta_date_str   = datetime.now(_eastern).strftime("%Y-%m-%d")
        _pmeta_date_basis = "America/New_York"
    else:
        _pmeta_date_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _pmeta_date_basis = "UTC"
    pipeline_meta: dict = {
        "sport":                  sport,
        "date_str":               _pmeta_date_str,
        "date_basis":             _pmeta_date_basis,
        "started_at":             started.isoformat(),
        "ingested_total":         0,
        "candidates_count":       0,
        "llm_picks_count":        0,
        "moneyball_rescued":      0,
        "blocked_by_time_filter": 0,
        "final_picks_count":      0,
        "final_rescued_count":    0,
        "final_discarded_count":  0,
        "abort_reason":           None,
        "ingest_error":           None,
        "cache_status":           "n/a",
        "source_used":            "api_sports",
    }

    await _emit("ingesting", 5, f"Ingesting upcoming {sport} fixtures…")
    # ── Phase 8.1 — Priority Discovery (football only) ──────────────────────
    # Actively probe API-Sports league-by-league for the top-12 priority
    # competitions BEFORE looking at the global candidate pool. If we find
    # any Tier 1/2 matches in the next 48h they become the AUTHORITATIVE
    # candidate list — exotic leagues are bypassed completely.
    if sport == "football" and not live_only:
        try:
            async with httpx.AsyncClient() as client:
                priority_fixtures = await ingestion.discover_priority_fixtures(
                    client, db, window_hours=48,
                )
                # We discover 50+ priority fixtures (whole weekend of MLS +
                # all Big-Five). Hydrating every single one even shallow-ly
                # is ~4 API calls each × 50 = 200 calls and the free plan
                # caps at 10 req/min. We only NEED the next `max_matches *
                # 2` for the LLM to have something to choose from, so cap
                # the hydration window.
                HYDRATE_CAP = max(12, (max_matches or 12) * 2)
                if priority_fixtures:
                    hydrate_list = priority_fixtures[:HYDRATE_CAP]
                    sem = asyncio.Semaphore(6)

                    async def _hydrate(fx):
                        async with sem:
                            try:
                                return await ingestion.enrich_fixture(
                                    client, db, fx, is_live=False, sport="football", deep=False,
                                )
                            except Exception as exc:
                                log.warning("priority hydrate failed: %s", exc)
                                return None

                    await asyncio.gather(*[_hydrate(fx) for fx in hydrate_list])
                    priority_leagues_hit = sorted({
                        (fx.get("league") or {}).get("name") for fx in priority_fixtures
                        if (fx.get("league") or {}).get("name")
                    })
                    log.info(
                        "priority discovery hydrated %d/%d fixtures (shallow) leagues=%s",
                        len(hydrate_list), len(priority_fixtures), priority_leagues_hit,
                    )
                    # Trim the priority_fixtures list to what we actually
                    # hydrated — the downstream candidate query filters by
                    # match_id and unhydrated fixtures wouldn't be in
                    # db.matches yet.
                    priority_fixtures = hydrate_list
        except Exception as exc:
            log.warning("priority discovery failed: %s", exc)
            priority_fixtures = []

    async with httpx.AsyncClient() as client:
        if refresh and not priority_fixtures:
            # Only fall back to the global firehose if priority discovery
            # didn't surface anything — otherwise we'd be re-ingesting
            # Côte d'Ivoire / Belarus on top of the Tier 1 we just got.
            try:
                await ingestion.ingest_upcoming(client, db, sport=sport)
            except Exception as exc:
                ingest_error = f"upcoming ingest failed: {exc}"
                log.warning(ingest_error)
        if refresh and include_live:
            await _emit("ingesting", 15, "Refreshing live games…")
            try:
                await ingestion.ingest_live(client, db, sport=sport)
            except Exception as exc:
                log.warning("live ingest failed: %s", exc)

    await _emit("enriching", 25, "Selecting candidates by league + odds availability…")
    if live_only:
        # Skip upcoming entirely — only analyze ongoing matches.
        upcoming = []
    elif priority_fixtures:
        # HARD GATE: priority discovery succeeded → load ONLY those fixtures
        # from db.matches by their fixture ids. Everything else (Côte
        # d'Ivoire U17, Botswana Premier, Belarus Reserves, Austrian
        # Bundesliga, etc.) is excluded by construction.
        # Note: db.matches stores match_id as the raw API-Sports fixture id
        # (integer-as-string in some sport adapters, raw int in football),
        # so we look for both forms.
        priority_match_ids = []
        for fx in priority_fixtures:
            fid = (fx.get("fixture") or {}).get("id")
            if fid is None:
                continue
            priority_match_ids.append(fid)
            priority_match_ids.append(str(fid))
        upcoming = await db.matches.find({
            "sport": "football",
            "match_id": {"$in": priority_match_ids},
            "is_live": False,
        }).to_list(length=len(priority_match_ids))
        now_ts = datetime.now(timezone.utc).timestamp()
        upcoming = [c for c in upcoming if (c.get("kickoff_ts") or 0) >= now_ts - 600]
        log.info(
            "priority candidate query: requested=%d found_in_db=%d",
            len(priority_fixtures), len(upcoming),
        )
    else:
        query_upcoming = {**_sport_filter(sport), "is_live": False}
        upcoming = await db.matches.find(query_upcoming).sort("kickoff_ts", 1).limit(80).to_list(length=80)
        now_ts = datetime.now(timezone.utc).timestamp()
        upcoming = [c for c in upcoming if (c.get("kickoff_ts") or 0) >= now_ts - 600]

    # ── MLB Stats API fallback (baseball only) ──────────────────────────
    # If API-Sports returned 0 baseball games for today, hit the official
    # free MLB Stats API directly so the user always gets MLB coverage
    # even when the API-Sports plan doesn't include baseball. Track both
    # sources' counts in pipeline_meta so the UI can explain what happened.
    pipeline_meta["api_sports_games_found"] = (
        len(upcoming) if sport in ("baseball", "basketball") else None
    )
    pipeline_meta["mlb_stats_api_games_found"] = 0
    pipeline_meta["espn_nba_games_found"] = 0
    pipeline_meta["fallback_used"] = False
    pipeline_meta["fallback_reason"] = None
    pipeline_meta["primary_source"] = "api_sports"
    pipeline_meta["external_rescue_count"] = 0
    pipeline_meta["external_sources_consulted"] = []
    if sport == "baseball" and not upcoming and not live_only:
        log.warning(
            "BASEBALL: api_sports returned 0 games for %s — trying MLB Stats API fallback",
            pipeline_meta["date_str"],
        )
        try:
            mlb_games = await ingestion.ingest_mlb_direct_fallback(
                db, pipeline_meta["date_str"],
            )
        except Exception as exc:
            log.warning("MLB Stats API fallback crashed: %s", exc)
            mlb_games = []
        pipeline_meta["mlb_stats_api_games_found"] = len(mlb_games)
        if mlb_games:
            pipeline_meta["source_used"]     = "mlb_stats_api_fallback"
            pipeline_meta["fallback_used"]   = True
            pipeline_meta["fallback_reason"] = "api_sports_zero_games"
            # Re-query db.matches so we pick up the just-persisted MLB
            # games (with their kickoff_ts already populated).
            query_upcoming = {**_sport_filter(sport), "is_live": False}
            upcoming = await db.matches.find(query_upcoming).sort("kickoff_ts", 1).limit(80).to_list(length=80)
            now_ts = datetime.now(timezone.utc).timestamp()
            upcoming = [c for c in upcoming if (c.get("kickoff_ts") or 0) >= now_ts - 600]
            log.info(
                "MLB fallback succeeded: %d games persisted, %d kept after time filter",
                len(mlb_games), len(upcoming),
            )
        else:
            pipeline_meta["source_used"]  = "none"

    # ── F2 — ESPN NBA fallback (basketball) ─────────────────────────────
    # Mirror of the MLB pattern: when api_sports returns 0 basketball
    # games, hit ESPN's free scoreboard JSON to bootstrap the day.
    if sport == "basketball" and not upcoming and not live_only:
        log.warning(
            "BASKETBALL: api_sports returned 0 games for %s — trying ESPN NBA fallback",
            pipeline_meta["date_str"],
        )
        try:
            nba_games = await ingestion.ingest_nba_direct_fallback(
                db, pipeline_meta["date_str"],
            )
        except Exception as exc:
            log.warning("ESPN NBA fallback crashed: %s", exc)
            nba_games = []
        pipeline_meta["espn_nba_games_found"] = len(nba_games)
        if nba_games:
            pipeline_meta["source_used"]     = "espn_nba_fallback"
            pipeline_meta["fallback_used"]   = True
            pipeline_meta["fallback_reason"] = "api_sports_zero_games"
            query_upcoming = {**_sport_filter(sport), "is_live": False}
            upcoming = await db.matches.find(query_upcoming).sort("kickoff_ts", 1).limit(80).to_list(length=80)
            now_ts = datetime.now(timezone.utc).timestamp()
            upcoming = [c for c in upcoming if (c.get("kickoff_ts") or 0) >= now_ts - 600]
            log.info(
                "ESPN NBA fallback succeeded: %d games persisted, %d kept after time filter",
                len(nba_games), len(upcoming),
            )
        else:
            # ── F2b — SofaScore basketball fallback (tertiary) ──────────
            # When BOTH api_sports and ESPN NBA returned 0 games, try
            # SofaScore's public schedule endpoint (NBA-only filter).
            log.warning(
                "BASKETBALL: ESPN NBA fallback also empty for %s — trying SofaScore",
                pipeline_meta["date_str"],
            )
            try:
                sofa_games = await ingestion.ingest_basketball_sofascore_fallback(
                    db, pipeline_meta["date_str"],
                )
            except Exception as exc:
                log.warning("SofaScore basketball fallback crashed: %s", exc)
                sofa_games = []
            pipeline_meta["sofascore_basketball_games_found"] = len(sofa_games)
            if sofa_games:
                pipeline_meta["source_used"]     = "sofascore_basketball_fallback"
                pipeline_meta["fallback_used"]   = True
                pipeline_meta["fallback_reason"] = "api_sports_and_espn_zero_games"
                query_upcoming = {**_sport_filter(sport), "is_live": False}
                upcoming = await db.matches.find(query_upcoming).sort("kickoff_ts", 1).limit(80).to_list(length=80)
                now_ts = datetime.now(timezone.utc).timestamp()
                upcoming = [c for c in upcoming if (c.get("kickoff_ts") or 0) >= now_ts - 600]
                log.info(
                    "SofaScore basketball fallback succeeded: %d games persisted, %d kept after time filter",
                    len(sofa_games), len(upcoming),
                )
            else:
                pipeline_meta["source_used"]  = "none"

    # ── F2c — Basketball cross-source evidence (telemetry) ──────────────
    # Always run the basketball multi-source rescue for the day so the
    # frontend's SourcesConsultedPanel can show which scrapers were
    # queried (sofascore, flashscore) AND attach `_external_evidence`
    # per match — even when ESPN NBA was the primary ingestion source.
    if sport == "basketball" and upcoming:
        try:
            from services.external_sources import basketball_rescue as _bk_rescue
            bk_payload = await _bk_rescue.rescue_basketball_day(
                pipeline_meta["date_str"],
            )
        except Exception as exc:
            log.warning("Basketball cross-source rescue failed: %s", exc)
            bk_payload = {"matchups": {}, "sources_consulted": [], "source_priority": []}
        try:
            corroborated = _bk_rescue.attach_evidence(upcoming, bk_payload)
        except Exception as exc:
            log.warning("Basketball attach_evidence failed: %s", exc)
            corroborated = 0
        # Dedupe sources for pipeline_meta.
        all_sources_seen = bk_payload.get("sources_consulted") or []
        seen_keys = set()
        uniq_sources = []
        for s in all_sources_seen:
            k = (s.get("source"), s.get("url"))
            if k in seen_keys:
                continue
            seen_keys.add(k)
            uniq_sources.append(s)
        # Merge into pipeline_meta.external_sources_consulted (may
        # already contain MLB-rescue entries from another invocation).
        existing = pipeline_meta.get("external_sources_consulted") or []
        pipeline_meta["external_sources_consulted"] = existing + uniq_sources
        pipeline_meta["basketball_external_corroborated"] = corroborated
        log.info(
            "Basketball rescue: %d/%d games corroborated via external sources (%s)",
            corroborated, len(upcoming),
            ", ".join({s.get("source", "?") for s in uniq_sources}),
        )

    # ── F1.6 — MLB lineup rescue layer ─────────────────────────────────
    # For baseball matches whose ingest didn't bring a probable pitcher,
    # consult external sources (RotoWire, MLB.com, FantasyPros, ESPN) to
    # try to recover the pitcher name BEFORE the LLM analyst sees them.
    # This is what flips "Datos incompletos sobre el pitcher de
    # Philadelphia" into a usable pick (or, at worst, into the more
    # honest "DATA_INCOMPLETE_AFTER_ALL_SOURCES" discard).
    if sport == "baseball" and upcoming:
        missing_pitcher = [
            m for m in upcoming
            if not ((m.get("home_probable") or {}).get("id") or m.get("home_probable_id"))
            or not ((m.get("away_probable") or {}).get("id") or m.get("away_probable_id"))
            or not ((m.get("home_probable") or {}).get("name") or m.get("home_probable_name"))
            or not ((m.get("away_probable") or {}).get("name") or m.get("away_probable_name"))
        ]
        if missing_pitcher:
            try:
                from services.external_sources import mlb_lineup_rescue as _rescue
                rescues = await _rescue.rescue_mlb_pitchers(
                    pipeline_meta["date_str"], missing_pitcher,
                )
            except Exception as exc:
                log.warning("baseball external rescue failed: %s", exc)
                rescues = []

            recovered = 0
            all_sources_seen: list[dict] = []
            for game, r in zip(missing_pitcher, rescues or []):
                all_sources_seen.extend(r.get("sources_consulted") or [])
                # Hydrate the game in-place so the analyst sees the names.
                if r.get("home_pitcher_name") and not (
                    (game.get("home_probable") or {}).get("name") or game.get("home_probable_name")
                ):
                    game["home_probable_name"] = r["home_pitcher_name"]
                    game.setdefault("home_probable", {})["name"] = r["home_pitcher_name"]
                if r.get("away_pitcher_name") and not (
                    (game.get("away_probable") or {}).get("name") or game.get("away_probable_name")
                ):
                    game["away_probable_name"] = r["away_pitcher_name"]
                    game.setdefault("away_probable", {})["name"] = r["away_pitcher_name"]
                # Stash the rescue telemetry on the match for downstream
                # (analyst_engine + frontend can read it).
                game["_external_rescue"] = r
                if r.get("home_pitcher_name") and r.get("away_pitcher_name"):
                    recovered += 1
            # De-duplicate sources_consulted by (source, url) for pipeline_meta.
            seen_keys = set()
            uniq_sources = []
            for s in all_sources_seen:
                k = (s.get("source"), s.get("url"))
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                uniq_sources.append(s)
            pipeline_meta["external_rescue_count"] = recovered
            pipeline_meta["external_sources_consulted"] = uniq_sources
            log.info(
                "MLB rescue: %d/%d games recovered via external sources (%s)",
                recovered, len(missing_pitcher),
                ", ".join({s.get("source", "?") for s in uniq_sources}),
            )

    # Big-Five filter (football only) — surfaces only Premier/LaLiga/Serie A/Bundesliga/Ligue 1.
    if big_five_only and sport == "football":
        from services.football_competitions import is_big_five  # local import to avoid cycle
        upcoming = [m for m in upcoming if is_big_five(m.get("league"), m.get("league_id"))]

    # Universal Football Quality Filter (Phase 8 — Dynamic Match Discovery).
    # Filters out Tier 4 / exotic leagues / low-liquidity matches BEFORE the
    # expensive LLM analysis. Cascades Tier 1 → 2 → 3 until we hit the target.
    # This eliminates the Belarus Reserve / Botswana / Daguestán problem AND
    # — thanks to the EXOTIC_FRAGMENTS hard-block — never lets a U17/U20/
    # Reserve match through even when priority_fixtures was empty.
    football_skipped: list[dict] = []
    football_stats: dict = {}
    if sport == "football":
        from services.football_quality import filter_and_prioritize  # local import
        target = max(3, min(20, max_matches if max_matches else 12))
        fq_result = filter_and_prioritize(
            upcoming,
            target_count=target,
            enable_tier_4=False,
            # When the priority hard-gate is engaged, rescue Tier 1/2/3
            # matches that the liquidity heuristic would otherwise skip
            # for "odds not yet hydrated".
            priority_override=bool(priority_fixtures),
        )
        upcoming = fq_result["selected"]
        football_skipped = fq_result["skipped"]
        football_stats = fq_result["stats"]
        football_stats["priority_discovery"] = {
            "fixtures_found": len(priority_fixtures),
            "leagues_hit": priority_leagues_hit,
            "hard_gate_engaged": bool(priority_fixtures),
        }
        if football_stats:
            log.info(
                "football_quality: ingested=%s analysable=%s selected=%s skipped=%s "
                "cascade=%s priority_gate=%s",
                football_stats.get("ingested_total"),
                football_stats.get("analysable_total"),
                football_stats.get("selected_total"),
                football_stats.get("skipped_total"),
                football_stats.get("cascade_used"),
                bool(priority_fixtures),
            )

    def priority_score(m: dict) -> tuple:
        # Phase 8.1 — `is_top` and football_quality SCORE rank ahead of
        # `has_odds`. The previous order (odds first) caused Côte d'Ivoire
        # / Botswana fixtures that happened to have a couple of bookmakers
        # to beat a Serie A whose odds hadn't been hydrated yet.
        is_top = 1 if (m.get("league_id") in top_set) else 0
        fq_score = (m.get("_football_quality") or {}).get("score") or 0
        has_odds = 1 if (m.get("odds_snapshots") or []) else 0
        return (-is_top, -fq_score, -has_odds, m.get("kickoff_ts") or 0)

    upcoming.sort(key=priority_score)
    candidates = upcoming[: max_matches]

    # Deep-enrich
    if sport == "football":
        await _emit("enriching", 40, f"Deep-enriching top {min(6, len(candidates))} football fixtures…")
        async with httpx.AsyncClient() as client:
            for idx, c in enumerate(candidates[:10]):
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
                await _emit("enriching", 40 + int(20 * (idx + 1) / max(1, min(10, len(candidates)))), f"Enriched {idx + 1}/{min(10, len(candidates))}")
    else:
        await _emit("enriching", 40, f"Deep-enriching top {min(4, len(candidates))} {sport} games…")
        async with httpx.AsyncClient() as client:
            for idx, c in enumerate(candidates[:4]):
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
                await _emit("enriching", 40 + int(20 * (idx + 1) / max(1, min(4, len(candidates)))), f"Enriched {idx + 1}/{min(4, len(candidates))}")

    if include_live:
        query_live = {**_sport_filter(sport), "is_live": True}
        live = await db.matches.find(query_live).limit(10).to_list(length=10)
        if big_five_only and sport == "football":
            from services.football_competitions import is_big_five  # noqa: F811
            live = [m for m in live if is_big_five(m.get("league"), m.get("league_id"))]
        candidates.extend(live)

    candidates = candidates[: max_matches]
    pipeline_meta["ingested_total"]   = len(upcoming)
    pipeline_meta["candidates_count"] = len(candidates)
    pipeline_meta["ingest_error"]     = ingest_error

    if not candidates:
        # Phase 8.1 — even the "last-resort" fallback path must pass through
        # filter_and_prioritize so U17/Reserves/Botswana never reach the LLM.
        any_recent = await db.matches.find(_sport_filter(sport)).sort("updated_at", -1).limit(max_matches * 3).to_list(length=max_matches * 3)
        if sport == "football":
            from services.football_quality import filter_and_prioritize  # noqa: F811
            fallback_result = filter_and_prioritize(any_recent, target_count=max_matches, enable_tier_4=False)
            candidates = fallback_result["selected"]
        else:
            candidates = any_recent[:max_matches]
    if not candidates:
        detail = f"no {sport} matches available"
        if priority_fixtures is not None and len(priority_fixtures) == 0:
            detail = (
                f"NO_PRIORITY_FIXTURES_FOUND: no Tier 1/2 {sport} matches in the next 48h. "
                "Enable high-volume mode to analyse lower-tier competitions."
            )
        if ingest_error:
            detail += f" — {ingest_error}"
        if sport == "baseball":
            # We already tried the MLB Stats API fallback above; if we
            # still have zero candidates, both sources failed.
            pipeline_meta["abort_reason"] = "no_games_all_sources"
            pipeline_meta["source_used"]  = "none"
        elif sport == "basketball":
            pipeline_meta["abort_reason"] = "no_games_all_sources"
            pipeline_meta["source_used"]  = "none"
        else:
            pipeline_meta["abort_reason"] = (
                "no_priority_fixtures" if (priority_fixtures is not None and len(priority_fixtures) == 0)
                else "no_candidates_for_sport"
            )
        log.info(
            "[PIPELINE_META] sport=%s date=%s basis=%s ingested=%d candidates=%d abort=%s "
            "api_sports=%s mlb=%s fallback=%s",
            sport, pipeline_meta["date_str"], pipeline_meta["date_basis"],
            pipeline_meta["ingested_total"], pipeline_meta["candidates_count"],
            pipeline_meta["abort_reason"],
            pipeline_meta.get("api_sports_games_found"),
            pipeline_meta.get("mlb_stats_api_games_found"),
            pipeline_meta.get("fallback_used"),
        )
        # For baseball & basketball we return a friendly empty payload (with
        # pipeline_meta) instead of raising 409 so the UI can render the
        # Pipeline Debug panel + humanized abort copy. Other sports keep
        # the legacy 409 contract so existing callers don't break.
        if sport in ("baseball", "basketball"):
            empty_result = {
                "picks":   [],
                "summary": {"no_value_found": True, "blocked_picks": []},
                "pipeline_meta": pipeline_meta,
            }
            empty_record = {
                "id": uuid.uuid4().hex[:10],
                "user_id": user_id,
                "sport": sport,
                "generated_at": started.isoformat(),
                "matches_analyzed": 0,
                "payload": empty_result,
            }
            try:
                await db.picks.insert_one(_normalize_keys_for_bson(empty_record))
            except Exception as exc:
                log.warning("empty baseball pick_run insert failed: %s", exc)
            return {
                "pick_run_id": empty_record["id"],
                "sport": sport,
                "generated_at": empty_record["generated_at"],
                "result": empty_result,
            }
        raise HTTPException(status_code=409, detail=detail)

    await _emit("analyzing", 65, f"LLM analyzing {len(candidates)} {sport} matches…")
    # ── MLB-V5 — Baseball goes through the specialised v2 engine instead of
    # the generic football-centric LLM. The LLM was bucketing MLB games into
    # `discarded_market` with "motivación normal" / "cuotas no disponibles"
    # even when the v2 structural analysis had a clear lean.
    if sport == "baseball":
        try:
            from services.mlb_day_orchestrator import analyze_mlb_day
            mlb_result = await asyncio.wait_for(
                analyze_mlb_day(pipeline_meta["date_str"], db=db),
                timeout=180.0,
            )
        except asyncio.TimeoutError:
            log.error("MLB orchestrator timed out after 180s")
            raise HTTPException(status_code=504,
                detail="El análisis MLB tardó demasiado (>3 min).")
        except Exception as exc:
            log.exception("MLB orchestrator failed")
            raise HTTPException(status_code=502,
                detail=f"mlb engine error: {exc}")

        # Translate MLB engine output → analyst-engine summary shape, keeping
        # the new MLB-V5 buckets at the top level so the frontend can render
        # a dedicated "Revisión manual — falta cuota" section.
        mlb_picks    = mlb_result.get("picks") or []
        mlb_rescued  = mlb_result.get("rescued_picks") or []
        mlb_struct   = mlb_result.get("structural_lean_requires_odds") or []
        mlb_watch    = mlb_result.get("watchlist_manual_odds") or []
        mlb_disc     = mlb_result.get("discarded_after_full_analysis") or []

        # ── BUGFIX (MLB-V5.1) — Counter showed "Recomendados: 4" but the
        # main pick list rendered 0 cards. Root cause: the frontend
        # iterates `data.picks` while `result.picks` only held the direct
        # high-confidence picks (`mlb_picks`). Rescued / structural-lean /
        # watchlist items lived solely in their dedicated buckets and were
        # invisible in the main card list.
        # ───────────────────────────────────────────────────────────────
        # Fix: merge ALL recommended buckets into `result.picks` AND
        # synthesise a `recommendation.confidence_score` on every entry so
        # the existing `filteredPicks.filter(score >= 70 | 60-69)` split in
        # DashboardPage continues to work without changes.

        def _ensure_recommendation_shape(p: dict, *, bucket: str) -> dict:
            """Make sure each visible pick has a `recommendation` dict
            with `confidence_score`, `market`, `selection`. Idempotent —
            existing fields are preserved."""
            rec = p.get("recommendation") or {}
            v2  = p.get("_mlb_script_v2") or {}
            score = (
                rec.get("confidence_score")
                or rec.get("score")
                or (v2.get("runLineScore") if bucket == "structural_lean" else None)
                or (v2.get("lineSafetyScore") if bucket in ("structural_lean", "watchlist") else None)
                or 60   # floor so manual-review picks land in `medium` bucket
            )
            try:
                score = int(score)
            except (TypeError, ValueError):
                score = 60
            score = max(0, min(100, score))
            market    = rec.get("market") or v2.get("recommendedLine") or rec.get("selection") or ""
            selection = rec.get("selection") or v2.get("recommendedLine") or market
            new_rec = {
                **rec,
                "confidence_score": score,
                "score":            score,
                "market":           market,
                "selection":        selection,
            }
            # Floor manual-review picks at 65 so they show up in the
            # `medium` (60-69) panel rather than getting filtered out.
            if bucket in ("structural_lean", "watchlist") and score < 65:
                new_rec["confidence_score"] = 65
                new_rec["score"]            = 65
            p["recommendation"] = new_rec
            p["bucket"]         = bucket
            return p

        mlb_picks   = [_ensure_recommendation_shape(p, bucket="recommended")        for p in mlb_picks]
        mlb_rescued = [_ensure_recommendation_shape(p, bucket="rescued")            for p in mlb_rescued]
        mlb_struct  = [_ensure_recommendation_shape(p, bucket="structural_lean")    for p in mlb_struct]
        mlb_watch   = [_ensure_recommendation_shape(p, bucket="watchlist")          for p in mlb_watch]
        # All four buckets are surfaced in `result.picks` for the main card list.
        # The "Revisión manual" section keeps reading from `summary.structural_*`.
        unified_picks = mlb_picks + mlb_rescued + mlb_struct + mlb_watch

        def _to_summary_row(p: dict, *, with_reason: bool = False) -> dict:
            row = {
                "match_id":         p.get("game_pk") or p.get("match_id"),
                "match_label":      p.get("match_label") or
                                    f"{p.get('away_team') or ''} @ {p.get('home_team') or ''}".strip(" @"),
                "market":           (p.get("recommendation") or {}).get("market") or
                                    (p.get("recommendation") or {}).get("selection") or "",
                "confidence":       int((p.get("recommendation") or {}).get("score") or 0),
                "classification":   p.get("classification"),
            }
            # BUGFIX — Always surface the v2 script payload (incl. Poisson totals
            # probability, edgeVsLine, probabilityDebug) so high_confidence /
            # medium_confidence rows ALSO display the correct cover probability,
            # not just the manual-review bucket.
            if p.get("_mlb_script_v2"):
                row["mlb_script_v2"] = p["_mlb_script_v2"]
            if with_reason:
                row["reason"] = (
                    p.get("manual_review_reason")
                    or p.get("discard_reason")
                    or ""
                )
                if p.get("suggested_markets"):
                    row["suggested_markets"] = p["suggested_markets"]
                if p.get("requires_manual_odds"):
                    row["requires_manual_odds"] = True
                if p.get("_structural_quality"):
                    row["structural_quality"] = p["_structural_quality"]
            return row

        result = {
            "verdict":   ("value_found" if mlb_picks or mlb_rescued or mlb_struct else "no_value"),
            # BUGFIX — main card list now receives ALL visible buckets.
            "picks":     unified_picks,
            "summary": {
                "high_confidence":   [_to_summary_row(p) for p in mlb_picks],
                "medium_confidence": [_to_summary_row(p) for p in mlb_rescued],
                # No "discarded_motivation" for MLB — motivation is neutral.
                "discarded_motivation":          [],
                # Only games discarded after FULL structural analysis.
                "discarded_market":              [_to_summary_row(p, with_reason=True) for p in mlb_disc],
                "incomplete_data":               [],
                # MLB-V5 new buckets exposed in summary so the UI can render them.
                "structural_lean_requires_odds": [_to_summary_row(p, with_reason=True) for p in mlb_struct],
                "watchlist_manual_odds":         [_to_summary_row(p, with_reason=True) for p in mlb_watch],
                "discarded_after_full_analysis": [_to_summary_row(p, with_reason=True) for p in mlb_disc],
                "total_analyzed":    (
                    len(mlb_picks) + len(mlb_rescued) + len(mlb_struct)
                    + len(mlb_watch) + len(mlb_disc)
                ),
                "total_recommended": len(mlb_picks) + len(mlb_rescued),
                "total_discarded":   len(mlb_disc),
                "total_manual_review": len(mlb_struct) + len(mlb_watch),
            },
            # Expose v2 engine artefacts at the top level so the frontend
            # MLBScriptPanel + parlay views can read them without dependency
            # on the analyst LLM payload.
            "parlay_suggested": mlb_result.get("parlay_suggested"),
            "fragility_scores": mlb_result.get("fragility_scores") or {},
            "editorial_context_signals_by_game": mlb_result.get("editorial_context_signals_by_game") or {},
            "pipeline_meta":    {**pipeline_meta, **(mlb_result.get("pipeline_meta") or {})},
            "engine":           "mlb_pregame_analytics_v2",
        }
        log.info(
            "MLB-V5 baseball pipeline result: picks=%d rescued=%d structural=%d watchlist=%d discarded=%d",
            len(mlb_picks), len(mlb_rescued), len(mlb_struct), len(mlb_watch), len(mlb_disc),
        )
    else:
        llm_payload = [nz.summarize_match_for_llm(_clean(c)) for c in candidates]
        try:
            result = await asyncio.wait_for(
                analyst_engine.analyze_matches(llm_payload, sport=sport, db=db),
                timeout=240.0,  # 4 min hard cap — evita jobs zombies indefinidos
            )
        except asyncio.TimeoutError:
            log.error(
                "LLM analysis timed out after 240s for %d %s candidates",
                len(candidates), sport,
            )
            raise HTTPException(
                status_code=504,
                detail=(
                    "El análisis tardó demasiado (>4 min). "
                    "Intenta reducir el número de partidos o reintenta."
                ),
            )
        except Exception as exc:
            log.exception("LLM analysis failed")
            raise HTTPException(status_code=502, detail=f"analyst engine error: {exc}")

    await _emit("analyzing", 90, "Persisting picks…")
    pick_id_base = uuid.uuid4().hex[:10]

    # Attach Phase 8 football quality metadata to the result so the UI can show
    # PRIORITY_MATCH / EXOTIC_LEAGUE_WARNING badges + the "why skipped" list.
    if sport == "football" and (football_skipped or football_stats):
        result.setdefault("summary", {})
        # Skipped matches go to a NEW sidecar list so we don't pollute the
        # existing discarded_motivation / discarded_market sections.
        result["summary"]["skipped_low_relevance"] = football_skipped[:20]
        result.setdefault("_pipeline", {})
        result["_pipeline"]["football_quality"] = football_stats
        # Surface the highest-tier cascade reached so the UI can warn if we
        # had to drop down to Tier 3 (e.g. weekday with no Big Five action).
        cascade = football_stats.get("cascade_used") or []
        result["_pipeline"]["football_tier_reached"] = max(cascade) if cascade else None

    # ── Propagate `_football_quality` from candidate matches into picks ──
    # The analyst engine only echoes match_id/match_label etc.; the badge
    # rendered on each MatchCard reads `m._football_quality`, so we re-attach
    # the quality payload here keyed by match_id. Also mirror the badge into
    # the high_confidence / medium_confidence sidecars so any UI consumer can
    # surface PRIORITY_MATCH / EXOTIC_LEAGUE_WARNING immediately.
    if sport == "football":
        fq_by_match: dict[str, dict] = {}
        prov_by_match: dict[str, dict] = {}
        for c in candidates:
            mid = c.get("match_id")
            if mid is None:
                continue
            sid = str(mid)
            fq = c.get("_football_quality")
            if fq:
                fq_by_match[sid] = fq
            # P2B — propagate provenance (built by services/provenance.py
            # during data_ingestion) so the MatchCard / LiveCard can render
            # "Fuente: API-Sports · hace 12 min" without a second API call.
            pv = c.get("_provenance")
            if pv:
                prov_by_match[sid] = pv
        if fq_by_match or prov_by_match:
            for p in (result.get("picks") or []):
                sid = str(p.get("match_id"))
                if sid in fq_by_match and not p.get("_football_quality"):
                    p["_football_quality"] = fq_by_match[sid]
                if sid in prov_by_match and not p.get("_provenance"):
                    p["_provenance"] = prov_by_match[sid]
            summary = result.setdefault("summary", {})
            for bucket in ("high_confidence", "medium_confidence"):
                for e in (summary.get(bucket) or []):
                    sid = str(e.get("match_id"))
                    if sid in fq_by_match and not e.get("_football_quality"):
                        e["_football_quality"] = fq_by_match[sid]
                    if sid in prov_by_match and not e.get("_provenance"):
                        e["_provenance"] = prov_by_match[sid]
    else:
        # Non-football sports also carry _provenance — propagate it too.
        prov_by_match: dict[str, dict] = {}
        for c in candidates:
            mid = c.get("match_id")
            if mid is None:
                continue
            pv = c.get("_provenance")
            if pv:
                prov_by_match[str(mid)] = pv
        if prov_by_match:
            for p in (result.get("picks") or []):
                sid = str(p.get("match_id"))
                if sid in prov_by_match and not p.get("_provenance"):
                    p["_provenance"] = prov_by_match[sid]

    # Recommendation limit per spec: never expose more than 10 picks.
    picks = (result.get("picks") or [])
    if len(picks) > 10:
        # Keep the highest-confidence 10; demote the rest to a sidecar list
        # rather than dropping them.
        picks_sorted = sorted(
            picks,
            key=lambda p: (p.get("recommendation") or {}).get("confidence_score", 0),
            reverse=True,
        )
        result["picks"] = picks_sorted[:10]
        result.setdefault("summary", {}).setdefault("overflow_picks", []).extend(picks_sorted[10:])

    # NO_VALUE_FOUND signal: when LLM and Moneyball both end empty
    if not (result.get("picks") or []):
        result.setdefault("summary", {})["no_value_found"] = True

    # ── Smart Carry-over (Option A) ─────────────────────────────────────
    # Preserve previously-recommended picks that are still valid (match
    # has NOT kicked off yet, no hard invalidator in the new run). This
    # protects users from the LLM/odds variability that previously made
    # the engine "discard" a pick they trusted just because they re-ran
    # the analysis. See services/carryover_picks.py for the full policy.
    try:
        from services.carryover_picks import apply_carryover
        prior_query = {
            "user_id": user_id,
            **({"sport": sport} if sport != "football"
               else {"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]}),
        }
        prior_run = await db.picks.find_one(prior_query, sort=[("generated_at", -1)])
        result = apply_carryover(result, prior_run, candidates)
    except Exception as exc:
        # NEVER let carry-over logic break the pipeline.
        log.warning("carryover step failed (non-fatal): %s", exc)

    # ── Last-line defence — block any pick whose match is finished/past ─
    # The user reported a recurring regression where finished games
    # (e.g. Cubs vs Pirates already 7-2) appeared as picks. This is the
    # FINAL gate: even if every upstream filter failed, picks that
    # violate is_match_upcoming / is_match_finished are stripped here.
    #
    # BUGFIX MLB-V6 — Skip this generic safety net for baseball: the MLB
    # v2 orchestrator already enforces (a) tz-aware Eastern Time game-date
    # filtering, (b) fragility-based bucket routing (high-fragility picks
    # go to watchlist_manual_odds / discarded_after_full_analysis, not
    # into the recommended bucket), and (c) PITCHER_OVERPERFORMING gating
    # for Under markets via emit_signals. Running the generic filter on
    # top would double-block already-screened picks and hide them from
    # the UI even though the counters showed them as recommended.
    if sport == "baseball":
        log.info("Skipping generic filter_blocked_picks for baseball — MLB-V5 orchestrator handles all safety gates internally.")
    else:
        try:
            from services.time_filter import filter_blocked_picks
            matches_by_id = {str(c.get("match_id")): c for c in (candidates or [])}
            picks_in = result.get("picks") or []
            kept_picks, blocked_picks = filter_blocked_picks(
                picks_in, matches_by_id, buffer_minutes=15,
            )
            if blocked_picks:
                log.warning("validate_pick_before_output: blocked %d picks (%s)",
                            len(blocked_picks),
                            [(p.get("match_id"), p.get("block_reasons")) for p in blocked_picks[:5]])
                result["picks"] = kept_picks
                result.setdefault("summary", {}).setdefault("blocked_picks", [])
                result["summary"]["blocked_picks"].extend(blocked_picks)
                pipeline_meta["blocked_by_time_filter"] = (
                    int(pipeline_meta.get("blocked_by_time_filter") or 0) + len(blocked_picks)
                )
                # Decrement the recommended counters so the UI reflects truth.
                try:
                    result["summary"]["total_recommended"] = (
                        int(result["summary"].get("total_recommended") or 0)
                        - len(blocked_picks))
                except Exception:
                    pass
            # Also run the validator on rescued/watchlist for transparency.
            for bucket_key in ("rescued_picks", "watchlist", "protected_acceptable"):
                bucket = (result.get("summary") or {}).get(bucket_key) or []
                kept, blocked = filter_blocked_picks(bucket, matches_by_id, buffer_minutes=15)
                if blocked:
                    result["summary"][bucket_key] = kept
                    result["summary"].setdefault("blocked_picks", []).extend(blocked)
        except Exception as exc:
            log.warning("last-line validate_pick_before_output failed: %s", exc)

    # ── Finalize pipeline_meta — counters + abort_reason for UI ─────────
    try:
        pipeline_meta["llm_picks_count"]      = len(result.get("picks") or [])
        pipeline_meta["moneyball_rescued"]    = len((result.get("summary") or {}).get("rescued_picks") or [])
        pipeline_meta["final_picks_count"]    = len(result.get("picks") or [])
        pipeline_meta["final_rescued_count"]  = len((result.get("summary") or {}).get("rescued_picks") or [])
        pipeline_meta["final_discarded_count"] = (
            len((result.get("summary") or {}).get("discarded_motivation") or [])
            + len((result.get("summary") or {}).get("discarded_market") or [])
            + len((result.get("summary") or {}).get("incomplete_data") or [])
            + len((result.get("summary") or {}).get("skipped_low_relevance") or [])
        )
        # For baseball: count how many candidates were dropped specifically
        # because they had no probable pitcher (vs general "incomplete_data").
        if sport == "baseball":
            missing_p = 0
            for c in candidates or []:
                if not (c.get("home_probable_id") or (c.get("home_probable") or {}).get("id")):
                    missing_p += 1
                    continue
                if not (c.get("away_probable_id") or (c.get("away_probable") or {}).get("id")):
                    missing_p += 1
            pipeline_meta["games_missing_pitchers"] = missing_p
            pipeline_meta["confirmed_games"]       = max(0, len(candidates or []) - missing_p)
            pipeline_meta["schedule_games_found"]  = max(
                int(pipeline_meta.get("api_sports_games_found") or 0),
                int(pipeline_meta.get("mlb_stats_api_games_found") or 0),
                len(candidates or []),
            )
        if pipeline_meta["final_picks_count"] == 0 and not pipeline_meta.get("abort_reason"):
            # Baseball-specific refinement of "no_value_found":
            #   • games exist but no pitchers       → games_found_but_missing_pitchers
            #   • engine truly ran but nothing of value → no_value_found
            if sport == "baseball":
                schedule_n = int(pipeline_meta.get("schedule_games_found") or 0)
                confirmed_n = int(pipeline_meta.get("confirmed_games") or 0)
                if schedule_n > 0 and confirmed_n == 0:
                    pipeline_meta["abort_reason"] = "games_found_but_missing_pitchers"
                else:
                    pipeline_meta["abort_reason"] = "no_value_found"
            else:
                pipeline_meta["abort_reason"] = "no_value_found"
        pipeline_meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["pipeline_meta"] = pipeline_meta
        log.info(
            "[PIPELINE_META] sport=%s date=%s basis=%s ingested=%d candidates=%d "
            "picks=%d rescued=%d discarded=%d blocked=%d abort=%s "
            "primary=%s source_used=%s fallback=%s api_sports_games=%s mlb_games=%s",
            sport, pipeline_meta["date_str"], pipeline_meta["date_basis"],
            pipeline_meta["ingested_total"], pipeline_meta["candidates_count"],
            pipeline_meta["final_picks_count"], pipeline_meta["final_rescued_count"],
            pipeline_meta["final_discarded_count"], pipeline_meta["blocked_by_time_filter"],
            pipeline_meta.get("abort_reason") or "none",
            pipeline_meta.get("primary_source"),
            pipeline_meta.get("source_used"),
            pipeline_meta.get("fallback_used"),
            pipeline_meta.get("api_sports_games_found"),
            pipeline_meta.get("mlb_stats_api_games_found"),
        )
    except Exception as _exc:
        log.debug("pipeline_meta finalize failed (non-fatal): %s", _exc)

    record = {
        "id": pick_id_base,
        "user_id": user_id,
        "sport": sport,
        "generated_at": started.isoformat(),
        "matches_analyzed": len(candidates),
        "payload": result,
    }
    # Defense-in-depth: any numeric keys sneaking in from football_quality
    # stats / LLM JSON / scraped feeds would crash BSON with
    # "documents must have only string keys, key was 1". Normalize before
    # persistence and before echoing back to the client (the client encodes
    # JSON which silently coerces, but the persisted copy is the one Mongo
    # touches).
    record = _normalize_keys_for_bson(record)
    result = record["payload"]
    await db.picks.insert_one(record)

    # ── Football Market Audit storage (V4) ─────────────────────────────
    # For football runs, persist the per-discarded market_trace into the
    # dedicated `football_market_audit` collection so the user can later
    # query historical audits (e.g. via GET /api/football/market_audit).
    # Fail-soft: any error is logged but never breaks the response.
    if sport == "football":
        try:
            from services.football_market_trace import build_run_audit_payload
            from services.football_audit_storage import store_football_audit
            summary = (result or {}).get("summary") or {}
            audit_payload = build_run_audit_payload(
                summary, sport="football",
                run_id=pick_id_base, user_id=user_id,
            )
            await store_football_audit(
                db,
                user_id=user_id,
                pick_run_id=pick_id_base,
                audit_payload=audit_payload,
                match_date=started.date().isoformat(),
            )
        except Exception as _exc:
            log.debug("football audit storage failed (non-fatal): %s", _exc)

    return {"pick_run_id": pick_id_base, "sport": sport, "generated_at": record["generated_at"], "result": result}


@api.post("/analysis/run")
async def analysis_run(payload: AnalysisRunIn, user: dict = Depends(get_current_user)):
    """Run the LLM analyst on currently ingested matches for the chosen sport.

    Two modes:
      - SYNC (default): blocks until completion, returns full result.
      - BACKGROUND (`background=true`): returns immediately with `job_id`;
        client polls `/api/analysis/jobs/{job_id}` for progress + final result.

    Auto-promotion: SYNC mode with `max_matches > 4` is automatically promoted
    to BACKGROUND because the ingress timeout (~60s) is shorter than the
    analysis duration for larger batches. The response in that case carries the
    same shape as a normal background submission, plus `_auto_promoted: true`
    so any legacy client can detect the switch.
    """
    sport = _norm_sport(payload.sport)

    # ── Auto-promote heavy SYNC requests to BACKGROUND to avoid 502s ────────
    # The Kubernetes ingress cuts requests around 60s; a 6-8 match analysis
    # routinely takes 60-120s. We transparently promote and let the client
    # poll. The frontend already uses background=true, so this only affects
    # external/test callers using SYNC.
    auto_promoted = False
    effective_background = payload.background
    if not effective_background and payload.max_matches > 4:
        effective_background = True
        auto_promoted = True
        log.info(
            "analysis_run: auto-promoting SYNC→background (sport=%s max_matches=%d) to avoid ingress timeout",
            sport, payload.max_matches,
        )

    if effective_background:
        async def _job(job_id: str) -> None:
            async def progress(stage: str, pct: int, msg: str):
                await job_queue.update_progress(db, job_id, stage, pct, msg)
            try:
                result = await _run_analysis_pipeline(
                    user_id=user["id"],
                    sport=sport,
                    refresh=payload.refresh,
                    include_live=payload.include_live,
                    max_matches=payload.max_matches,
                    progress_cb=progress,
                    live_only=payload.live_only,
                    big_five_only=payload.big_five_only,
                )
                await job_queue.finish(db, job_id, result)
            except HTTPException as he:
                await job_queue.fail(db, job_id, f"{he.status_code}: {he.detail}")
            except Exception as exc:
                log.exception("background analysis_run failed")
                await job_queue.fail(db, job_id, str(exc))

        job_id = await job_queue.enqueue_async(
            db,
            _job,
            user_id=user["id"],
            kind="analysis_run",
            params={
                "sport": sport,
                "refresh": payload.refresh,
                "include_live": payload.include_live,
                "max_matches": payload.max_matches,
            },
        )
        response = {
            "job_id": job_id,
            "status": "queued",
            "sport": sport,
            "stage": "queued",
            "progress": 0,
        }
        if auto_promoted:
            response["_auto_promoted"] = True
            response["_auto_promoted_reason"] = (
                "SYNC requests with max_matches > 4 are automatically promoted to "
                "background to avoid ingress timeout. Poll /api/analysis/jobs/{job_id}."
            )
        return response

    # SYNC path (only when max_matches <= 4)
    return await _run_analysis_pipeline(
        user_id=user["id"],
        sport=sport,
        refresh=payload.refresh,
        include_live=payload.include_live,
        max_matches=payload.max_matches,
        progress_cb=None,
        live_only=payload.live_only,
        big_five_only=payload.big_five_only,
    )


# ════════════════════════════════════════════════════════════════════════════
# Lightweight recalibration — re-runs the analyst on matches already in the
# latest pick_run WITHOUT calling external APIs. Lets the user re-score the
# day after a deploy that introduced new features (M1–M5, vetoes, scripts).
# Scope: MLB + Basketball (football comes later).
# ════════════════════════════════════════════════════════════════════════════
class AnalysisRecalibrateIn(BaseModel):
    sport:      str  = "baseball"
    background: bool = True


async def _run_recalibration_pipeline(
    user_id: str,
    sport: str,
    progress_cb=None,
) -> dict:
    """Re-run analyst layers on the matches captured in the latest pick_run.

    Skips ingestion, external API hydration and odds refresh. Relies on:
      • cached match docs in `db.matches` (already enriched on the previous run)
      • the orchestrator's own caches (MLB Stats API, team form, pitcher stats)
      • the auto-recalibrated feedback weights

    Writes a fresh pick_run with `is_recalibration=True` so the UI can label it.
    """
    sport = _norm_sport(sport)
    if sport not in ("baseball", "basketball"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Recalibrar only supports MLB and Basketball in this iteration. "
                "Football recalibration coming later."
            ),
        )

    async def _emit(stage: str, pct: int, msg: str) -> None:
        if progress_cb is not None:
            try:
                await progress_cb(stage, pct, msg)
            except Exception:
                pass

    await _emit("loading_snapshot", 5, "Cargando último pick_run…")
    last_run = await db.picks.find_one(
        {"user_id": user_id, "sport": sport},
        sort=[("generated_at", -1)],
    )
    if not last_run:
        raise HTTPException(
            status_code=404,
            detail=(
                "No hay pick_run previo para recalibrar. "
                "Pulsa 'Generar picks' primero."
            ),
        )

    started = datetime.now(timezone.utc)
    pick_id_base = uuid.uuid4().hex[:10]
    previous_run_id = last_run.get("id")
    previous_generated_at = last_run.get("generated_at")

    # ── Sport-specific recalibration ─────────────────────────────────────
    if sport == "baseball":
        await _emit("analyzing", 25, "Re-ejecutando orquestador MLB…")
        from services.mlb_day_orchestrator import analyze_mlb_day
        from zoneinfo import ZoneInfo as _ZI
        try:
            eastern = _ZI("America/New_York")
            date_str = datetime.now(eastern).strftime("%Y-%m-%d")
        except Exception:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            mlb_result = await asyncio.wait_for(
                analyze_mlb_day(date_str=date_str, db=db),
                timeout=180.0,
            )
        except asyncio.TimeoutError:
            log.error("MLB recalibrate timed out after 180s")
            raise HTTPException(status_code=504, detail="Recalibración MLB tardó demasiado.")
        except Exception as exc:
            log.exception("MLB recalibrate failed")
            raise HTTPException(status_code=502, detail=f"mlb recalibrate error: {exc}")

        await _emit("analyzing", 75, "Aplicando capas v2/v5/v6/M1–M5…")
        # Re-use the same shape as the full pipeline so the UI doesn't branch.
        mlb_picks    = mlb_result.get("picks") or []
        mlb_rescued  = mlb_result.get("rescued_picks") or []
        mlb_struct   = mlb_result.get("structural_lean_requires_odds") or []
        mlb_watch    = mlb_result.get("watchlist_manual_odds") or []
        mlb_disc     = mlb_result.get("discarded_picks") or []
        result = {
            "picks":            mlb_picks + mlb_rescued + mlb_struct + mlb_watch,
            "discarded_picks":  mlb_disc,
            "rescued_picks":    mlb_rescued,
            "structural_lean_requires_odds": mlb_struct,
            "watchlist_manual_odds":         mlb_watch,
            "summary": {
                "no_value_found": (
                    len(mlb_picks) + len(mlb_rescued) + len(mlb_struct) + len(mlb_watch)
                ) == 0,
                "blocked_picks": [],
                "high_confidence":   [p for p in mlb_picks if (p.get("recommendation") or {}).get("confidence_score", 0) >= 75],
                "medium_confidence": [p for p in mlb_picks if 50 <= (p.get("recommendation") or {}).get("confidence_score", 0) < 75],
                "discarded_motivation": [],
                "discarded_market":     mlb_disc,
                "incomplete_data":      [],
                "structural_lean_requires_odds": mlb_struct,
                "watchlist_manual_odds":         mlb_watch,
                "discarded_after_full_analysis": mlb_disc,
                "total_analyzed":      len(mlb_picks) + len(mlb_rescued) + len(mlb_struct) + len(mlb_watch) + len(mlb_disc),
                "total_recommended":   len(mlb_picks) + len(mlb_rescued),
                "total_discarded":     len(mlb_disc),
                "total_manual_review": len(mlb_struct) + len(mlb_watch),
            },
            "parlay_suggested": mlb_result.get("parlay_suggested"),
            "fragility_scores": mlb_result.get("fragility_scores") or {},
            "editorial_context_signals_by_game": mlb_result.get("editorial_context_signals_by_game") or {},
            "pipeline_meta":    mlb_result.get("pipeline_meta") or {},
            "engine":           "mlb_pregame_analytics_v2_recalibrated",
            "is_recalibration": True,
            "recalibrated_from": previous_run_id,
            "recalibrated_from_generated_at": previous_generated_at,
        }
        matches_count = len(mlb_picks) + len(mlb_rescued) + len(mlb_struct) + len(mlb_watch) + len(mlb_disc)

    else:  # basketball
        await _emit("loading_snapshot", 15, "Cargando partidos del pick_run anterior…")
        # Collect match_ids from every visible bucket of the previous run.
        prev_payload = last_run.get("payload") or {}
        match_ids: list = []
        seen: set = set()
        for bucket_key in ("picks", "rescued_picks", "discarded_picks",
                           "structural_lean_requires_odds", "watchlist_manual_odds"):
            for p in (prev_payload.get(bucket_key) or []):
                mid = p.get("match_id") or p.get("matchId")
                if mid and mid not in seen:
                    seen.add(mid)
                    match_ids.append(mid)
        # Also pull from summary if main buckets are empty (rare edge).
        if not match_ids:
            summ = prev_payload.get("summary") or {}
            for k in ("high_confidence", "medium_confidence", "discarded_market",
                      "discarded_motivation", "incomplete_data"):
                for row in (summ.get(k) or []):
                    mid = row.get("match_id") or row.get("matchId")
                    if mid and mid not in seen:
                        seen.add(mid)
                        match_ids.append(mid)
        if not match_ids:
            raise HTTPException(
                status_code=404,
                detail="El pick_run anterior no tiene partidos para recalibrar.",
            )

        await _emit("hydrating", 35, f"Hidratando {len(match_ids)} partidos…")
        cursor = db.matches.find({"match_id": {"$in": match_ids}, "sport": "basketball"})
        candidates = await cursor.to_list(length=len(match_ids))
        if not candidates:
            raise HTTPException(
                status_code=404,
                detail="No quedan datos en cache para esos partidos. Regenera picks.",
            )

        await _emit("analyzing", 55, "Re-ejecutando analista (Basketball)…")
        llm_payload = [nz.summarize_match_for_llm(_clean(c)) for c in candidates]
        try:
            result = await asyncio.wait_for(
                analyst_engine.analyze_matches(llm_payload, sport=sport, db=db),
                timeout=180.0,
            )
        except asyncio.TimeoutError:
            log.error("basketball recalibrate timed out after 180s")
            raise HTTPException(status_code=504, detail="Recalibración Basketball tardó demasiado.")
        except Exception as exc:
            log.exception("basketball recalibrate failed")
            raise HTTPException(status_code=502, detail=f"basketball recalibrate error: {exc}")

        result["engine"]            = (result.get("engine") or "analyst_engine") + "_recalibrated"
        result["is_recalibration"]  = True
        result["recalibrated_from"] = previous_run_id
        result["recalibrated_from_generated_at"] = previous_generated_at
        matches_count = len(candidates)

    await _emit("analyzing", 92, "Guardando snapshot recalibrado…")
    record = {
        "id": pick_id_base,
        "user_id": user_id,
        "sport": sport,
        "generated_at": started.isoformat(),
        "matches_analyzed": matches_count,
        "is_recalibration": True,
        "recalibrated_from": previous_run_id,
        "payload": result,
    }
    record = _normalize_keys_for_bson(record)
    result = record["payload"]
    await db.picks.insert_one(record)
    await _emit("done", 100, "Recalibración lista")
    return {
        "pick_run_id":      pick_id_base,
        "sport":            sport,
        "generated_at":     record["generated_at"],
        "is_recalibration": True,
        "recalibrated_from": previous_run_id,
        "result":           result,
    }


@api.post("/analysis/recalibrate")
async def analysis_recalibrate(
    payload: AnalysisRecalibrateIn,
    user: dict = Depends(get_current_user),
):
    """Re-score the latest pick_run with the current engine without re-ingesting.

    Useful right after a deploy that introduced new features so the picks the
    user is already looking at adapt to the new calculations. MLB + Basketball
    only — football coming later.
    """
    sport = _norm_sport(payload.sport)
    if sport not in ("baseball", "basketball"):
        raise HTTPException(
            status_code=400,
            detail="Recalibrar disponible solo para MLB y Basketball por ahora.",
        )

    if payload.background:
        async def _job(job_id: str) -> None:
            async def progress(stage: str, pct: int, msg: str):
                await job_queue.update_progress(db, job_id, stage, pct, msg)
            try:
                result = await _run_recalibration_pipeline(
                    user_id=user["id"], sport=sport, progress_cb=progress,
                )
                await job_queue.finish(db, job_id, result)
            except HTTPException as he:
                await job_queue.fail(db, job_id, f"{he.status_code}: {he.detail}")
            except Exception as exc:
                log.exception("background recalibrate failed")
                await job_queue.fail(db, job_id, str(exc))

        job_id = await job_queue.enqueue_async(
            db,
            _job,
            user_id=user["id"],
            kind="analysis_recalibrate",
            params={"sport": sport},
        )
        return {
            "job_id":   job_id,
            "status":   "queued",
            "sport":    sport,
            "stage":    "queued",
            "progress": 0,
            "kind":     "recalibrate",
        }

    return await _run_recalibration_pipeline(
        user_id=user["id"], sport=sport, progress_cb=None,
    )



@api.get("/analysis/jobs/{job_id}")
async def analysis_job_status(job_id: str, user: dict = Depends(get_current_user)):
    """Poll the status of a background analysis job."""
    doc = await job_queue.get(db, job_id, user_id=user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="job not found")
    return doc


@api.get("/analysis/jobs")
async def analysis_jobs_recent(user: dict = Depends(get_current_user)):
    """Recent jobs for the authenticated user (active + last few terminal)."""
    # Opportunistic cleanup so the UI never lingers on a dead "running" job.
    try:
        await job_queue.cleanup_stale(db)
    except Exception:
        pass
    jobs = await job_queue.list_active(db, user["id"], limit=10)
    active = [j for j in jobs if j.get("stage") not in job_queue.TERMINAL]
    recent = [j for j in jobs if j.get("stage") in job_queue.TERMINAL][:3]
    return {"active": active, "recent": recent}


@api.get("/picks/today")
async def picks_today(
    sport: Optional[str] = None,
    user: dict = Depends(get_current_user),
    background_tasks: BackgroundTasks = None,
):
    """Return the most recent pick run for this user (today's picks).

    Auto-refresh policy
    -------------------
    The dashboard "Picks del día" used to render whatever snapshot was
    most recently persisted, even when it was hours stale (or from a
    previous calendar day). For users in Latam timezones that meant
    opening the app at 8pm and still seeing a noon snapshot with 0
    picks while the orchestrator was perfectly able to find live and
    upcoming games right now.

    We now classify the existing snapshot as `stale` when either:
      • `generated_at` is older than `STALE_AFTER_MINUTES` (default 120m), OR
      • `generated_at` belongs to a different UTC day than `now`.

    When stale, we still return the current snapshot immediately (so
    the page renders fast and never goes blank), but we kick off a
    background `analysis_run` so the next poll picks up fresh picks.
    The response carries `stale=True` and `refresh_dispatched=True` so
    the client can decide to show a "refreshing…" hint and re-poll
    after a few seconds.
    """
    s = _norm_sport(sport)
    query = {
        "user_id": user["id"],
        **(
            {"sport": s}
            if s != "football"
            else {"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]}
        ),
    }
    doc = await db.picks.find_one(query, sort=[("generated_at", -1)])

    # ── Stale snapshot detection ─────────────────────────────────────
    STALE_AFTER_MINUTES = 120
    now_utc = datetime.now(timezone.utc)
    stale = False
    stale_reason: Optional[str] = None
    if doc:
        gen_at_raw = doc.get("generated_at")
        gen_at: Optional[datetime] = None
        if isinstance(gen_at_raw, datetime):
            gen_at = gen_at_raw if gen_at_raw.tzinfo else gen_at_raw.replace(tzinfo=timezone.utc)
        elif isinstance(gen_at_raw, str):
            try:
                gen_at = datetime.fromisoformat(gen_at_raw.replace("Z", "+00:00"))
                if gen_at.tzinfo is None:
                    gen_at = gen_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                gen_at = None
        if gen_at is None:
            stale = True
            stale_reason = "no_generated_at"
        else:
            age_min = (now_utc - gen_at).total_seconds() / 60.0
            if age_min > STALE_AFTER_MINUTES:
                stale = True
                stale_reason = f"older_than_{STALE_AFTER_MINUTES}m"
            elif gen_at.astimezone(timezone.utc).date() != now_utc.date():
                stale = True
                stale_reason = "different_utc_day"

    # ── Dispatch background refresh if stale ─────────────────────────
    refresh_dispatched = False
    refresh_job_id: Optional[str] = None
    if stale or not doc:
        # Guard against duplicate concurrent refreshes: skip if there is
        # already an active analysis_run job for this user+sport.
        try:
            active_jobs = await job_queue.list_active(db, user["id"], limit=10)
            already_running = any(
                j.get("kind") == "analysis_run"
                and j.get("stage") not in job_queue.TERMINAL
                and (j.get("params") or {}).get("sport") == s
                for j in (active_jobs or [])
            )
        except Exception:
            already_running = False

        if not already_running:
            try:
                async def _refresh_job(job_id: str) -> None:
                    async def _progress(stage: str, pct: int, msg: str):
                        await job_queue.update_progress(db, job_id, stage, pct, msg)
                    try:
                        result = await _run_analysis_pipeline(
                            user_id=user["id"],
                            sport=s,
                            refresh=True,
                            include_live=True,
                            max_matches=10,
                            progress_cb=_progress,
                            live_only=False,
                            big_five_only=False,
                        )
                        await job_queue.finish(db, job_id, result)
                    except Exception as exc:
                        log.exception("auto-refresh analysis_run failed")
                        await job_queue.fail(db, job_id, str(exc))

                refresh_job_id = await job_queue.enqueue_async(
                    db,
                    _refresh_job,
                    user_id=user["id"],
                    kind="analysis_run",
                    params={
                        "sport": s,
                        "refresh": True,
                        "include_live": True,
                        "max_matches": 10,
                        "auto": True,
                    },
                )
                refresh_dispatched = True
                log.info(
                    "/picks/today auto-refresh dispatched user=%s sport=%s reason=%s job=%s",
                    user["id"], s, stale_reason or "no_snapshot", refresh_job_id,
                )
            except Exception as exc:
                log.warning("/picks/today auto-refresh dispatch failed: %s", exc)

    if not doc:
        return {
            "pick_run":           None,
            "sport":              s,
            "stale":              True,
            "stale_reason":       "no_snapshot",
            "refresh_dispatched": refresh_dispatched,
            "refresh_job_id":     refresh_job_id,
        }
    return {
        "pick_run":           _clean(doc),
        "sport":              s,
        "stale":              stale,
        "stale_reason":       stale_reason,
        "refresh_dispatched": refresh_dispatched,
        "refresh_job_id":     refresh_job_id,
    }


@api.get("/picks/today/live")
async def picks_today_live(
    sport: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """Return only the picks from today's snapshot that are tagged as
    `is_live_route=True` — i.e. games already in progress at the time of
    pick generation.

    Why a dedicated endpoint?
      • The dashboard's main `/picks/today` returns the full mixed set
        (pregame + live-route) so the user keeps a single view of the
        day's analysis.
      • Power users / mobile widgets sometimes want a focused "in
        progress" panel that updates faster. This endpoint gives them
        exactly that, with the same stale-detection / auto-refresh
        semantics as `/picks/today`.

    Response shape mirrors `/picks/today` but the `pick_run.payload`
    arrays are pre-filtered to in-progress entries only, plus a
    convenience `counts` block. When the upstream snapshot is stale this
    endpoint also dispatches the background refresh.
    """
    s = _norm_sport(sport)
    query = {
        "user_id": user["id"],
        **(
            {"sport": s}
            if s != "football"
            else {"$or": [{"sport": "football"}, {"sport": {"$exists": False}}]}
        ),
    }
    doc = await db.picks.find_one(query, sort=[("generated_at", -1)])

    # Reuse the exact stale-detection / auto-refresh logic from
    # /picks/today to keep behavior consistent.
    STALE_AFTER_MINUTES = 120
    now_utc = datetime.now(timezone.utc)
    stale = False
    stale_reason: Optional[str] = None
    if doc:
        gen_at_raw = doc.get("generated_at")
        gen_at: Optional[datetime] = None
        if isinstance(gen_at_raw, datetime):
            gen_at = gen_at_raw if gen_at_raw.tzinfo else gen_at_raw.replace(tzinfo=timezone.utc)
        elif isinstance(gen_at_raw, str):
            try:
                gen_at = datetime.fromisoformat(gen_at_raw.replace("Z", "+00:00"))
                if gen_at.tzinfo is None:
                    gen_at = gen_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                gen_at = None
        if gen_at is None:
            stale, stale_reason = True, "no_generated_at"
        else:
            age_min = (now_utc - gen_at).total_seconds() / 60.0
            if age_min > STALE_AFTER_MINUTES:
                stale, stale_reason = True, f"older_than_{STALE_AFTER_MINUTES}m"
            elif gen_at.astimezone(timezone.utc).date() != now_utc.date():
                stale, stale_reason = True, "different_utc_day"

    refresh_dispatched = False
    refresh_job_id: Optional[str] = None
    if stale or not doc:
        try:
            active_jobs = await job_queue.list_active(db, user["id"], limit=10)
            already_running = any(
                j.get("kind") == "analysis_run"
                and j.get("stage") not in job_queue.TERMINAL
                and (j.get("params") or {}).get("sport") == s
                for j in (active_jobs or [])
            )
        except Exception:
            already_running = False
        if not already_running:
            try:
                async def _refresh_job(job_id: str) -> None:
                    async def _progress(stage: str, pct: int, msg: str):
                        await job_queue.update_progress(db, job_id, stage, pct, msg)
                    try:
                        result = await _run_analysis_pipeline(
                            user_id=user["id"],
                            sport=s,
                            refresh=True,
                            include_live=True,
                            max_matches=10,
                            progress_cb=_progress,
                            live_only=False,
                            big_five_only=False,
                        )
                        await job_queue.finish(db, job_id, result)
                    except Exception as exc:
                        log.exception("auto-refresh (live endpoint) failed")
                        await job_queue.fail(db, job_id, str(exc))

                refresh_job_id = await job_queue.enqueue_async(
                    db, _refresh_job, user_id=user["id"], kind="analysis_run",
                    params={"sport": s, "refresh": True, "include_live": True,
                             "max_matches": 10, "auto": True, "source": "live_endpoint"},
                )
                refresh_dispatched = True
            except Exception as exc:
                log.warning("/picks/today/live auto-refresh dispatch failed: %s", exc)

    if not doc:
        return {
            "pick_run":           None,
            "sport":              s,
            "stale":              True,
            "stale_reason":       "no_snapshot",
            "refresh_dispatched": refresh_dispatched,
            "refresh_job_id":     refresh_job_id,
            "counts":             {"picks": 0, "rescued": 0, "discarded": 0, "total": 0},
        }

    cleaned = _clean(doc)
    payload = (cleaned.get("payload") or {}) if isinstance(cleaned.get("payload"), dict) else cleaned
    # /api/mlb/day returns picks at top-level; /api/picks/today wraps them
    # under `payload`. Support both shapes transparently.
    summary = payload.get("summary") or {}

    def _filter_live(entries):
        if not isinstance(entries, list):
            return []
        return [e for e in entries if isinstance(e, dict) and bool(e.get("is_live_route"))]

    picks_live     = _filter_live(payload.get("picks") or summary.get("picks"))
    rescued_live   = _filter_live(payload.get("rescued_picks") or summary.get("rescued_picks"))
    discarded_live = _filter_live(payload.get("discarded_picks")
                                   or summary.get("discarded_market"))

    # Inject the filtered payload back into the response so consumers
    # keep the familiar shape, just with shorter arrays.
    if isinstance(cleaned.get("payload"), dict):
        cleaned["payload"]["picks"]            = picks_live
        cleaned["payload"]["rescued_picks"]    = rescued_live
        cleaned["payload"]["discarded_picks"]  = discarded_live
    else:
        cleaned["picks"]            = picks_live
        cleaned["rescued_picks"]    = rescued_live
        cleaned["discarded_picks"]  = discarded_live

    return {
        "pick_run":           cleaned,
        "sport":              s,
        "stale":              stale,
        "stale_reason":       stale_reason,
        "refresh_dispatched": refresh_dispatched,
        "refresh_job_id":     refresh_job_id,
        "counts": {
            "picks":     len(picks_live),
            "rescued":   len(rescued_live),
            "discarded": len(discarded_live),
            "total":     len(picks_live) + len(rescued_live) + len(discarded_live),
        },
    }


@api.get("/mlb/day")
async def mlb_day(date: Optional[str] = None, user: dict = Depends(get_current_user)):
    """MLB Pre-game Analytics Engine — repeatable-edge focused.

    Returns picks / rescued_picks / discarded_picks plus
    `editorial_context_signals_by_game` and `fragility_scores`. Every
    signal embeds the literal `source_url` that confirmed the pitcher so
    the user can verify independently.

    Query params
    ------------
    date : YYYY-MM-DD. Defaults to today (UTC).
    """
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from services.mlb_day_orchestrator import analyze_mlb_day
        result = await analyze_mlb_day(date, db=db)
    except Exception as exc:
        logger.exception("mlb_day failed")
        raise HTTPException(status_code=500, detail=f"mlb_day_failed: {exc}")
    return {"date": date, "engine": "mlb_pregame_analytics_v1", **result}




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
    sport: Optional[str] = None  # football | basketball | baseball


@api.post("/picks/track")
async def track_pick(payload: TrackIn, user: dict = Depends(get_current_user)):
    pick_uid = f"{payload.run_id}-{payload.match_id}"
    # Validate sport (best-effort — accept anything but default to football)
    sport = (payload.sport or "football").lower()
    if sport not in SUPPORTED_SPORTS:
        sport = "football"
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
        "sport": sport,
        "tracked_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.pick_tracking.update_one(
        {"user_id": user["id"], "match_id": str(payload.match_id), "pick_id": pick_uid},
        {"$set": doc},
        upsert=True,
    )
    return {"ok": True, "pick_id": pick_uid, "outcome": payload.outcome, "sport": sport}


@api.get("/picks/tracked")
async def list_tracked(user: dict = Depends(get_current_user), limit: int = 200):
    docs = await db.pick_tracking.find({"user_id": user["id"]}).sort("tracked_at", -1).limit(limit).to_list(length=limit)
    return {"count": len(docs), "items": _clean_list(docs)}


# ════════════════════════════════════════════════════════════════════════════
# MLB Feedback Loop endpoints (P2) — settle MLB picks + auto-recalibration.
# ════════════════════════════════════════════════════════════════════════════
class MLBSettleIn(BaseModel):
    pick_id:     str                    # e.g. "<run_id>-<match_id>"
    run_id:      str
    match_id:    str
    outcome:     str = Field(pattern="^(won|lost|push|pending)$")
    final_home_runs: Optional[int] = None
    final_away_runs: Optional[int] = None
    v2_snapshot: Optional[dict] = None  # what /api/mlb/day returned for this pick
    pick_doc:    Optional[dict] = None  # selection/market/teams for metric computation


@api.post("/mlb/picks/{pick_id}/settle")
async def settle_mlb_pick(pick_id: str, payload: MLBSettleIn,
                          user: dict = Depends(get_current_user)):
    """Settle an MLB pick. Writes margin/totalRuns/runLineCovered/overHit to
    `pick_tracking` AND a full v2 snapshot to `mlb_pick_feedback`. Triggers
    automatic recalibration of the engine weights every FEEDBACK_BATCH_SIZE
    settled picks (default 50).
    """
    if pick_id != payload.pick_id:
        return JSONResponse(status_code=400,
                            content={"detail": "pick_id path mismatch"})
    try:
        from services.mlb_feedback_loop import record_mlb_pick_outcome
        result = await record_mlb_pick_outcome(
            db,
            pick_id=payload.pick_id,
            run_id=payload.run_id,
            match_id=str(payload.match_id),
            user_id=user["id"],
            outcome=payload.outcome,
            final_home_runs=payload.final_home_runs,
            final_away_runs=payload.final_away_runs,
            v2_snapshot=payload.v2_snapshot,
            pick_doc=payload.pick_doc,
        )
        # F6B — Persist a script-break event for the future learning loop.
        # Fail-soft: a storage error must not block the settle response.
        try:
            from services.mlb_script_breaks_storage import store_script_break_event
            sb_result = await store_script_break_event(
                db,
                pick_id=payload.pick_id,
                run_id=payload.run_id,
                match_id=str(payload.match_id),
                user_id=user["id"],
                outcome=payload.outcome,
                final_home_runs=payload.final_home_runs,
                final_away_runs=payload.final_away_runs,
                pick_doc=payload.pick_doc,
                v2_snapshot=payload.v2_snapshot,
            )
            result["script_break"] = sb_result
        except Exception as sb_exc:
            log.warning("F6B script-break storage failed for pick %s: %s",
                        payload.pick_id, sb_exc)
            result["script_break"] = {"ok": False, "error": str(sb_exc)}
        return {"ok": True, **result}
    except Exception as exc:
        log.exception("mlb settle failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


@api.get("/mlb/engine/weights")
async def mlb_engine_weights(user: dict = Depends(get_current_user)):
    """Returns the currently active v2 engine weights + recalibration status."""
    try:
        from services.mlb_feedback_loop import get_recalibration_status
        return await get_recalibration_status(db)
    except Exception as exc:
        log.exception("mlb engine weights failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


# ════════════════════════════════════════════════════════════════════════════
# F6B — Script Breaks inspection endpoints (read-only).
# Powers the learning-loop dashboard (Phase 7 next iteration).
# ════════════════════════════════════════════════════════════════════════════
@api.get("/mlb/script_breaks")
async def mlb_script_breaks_list(
    user: dict = Depends(get_current_user),
    days: int = 30,
    limit: int = 100,
    only_broken: bool = False,
    script_code: Optional[str] = None,
    scope: str = "me",                   # "me" or "all" (still user-filtered for safety)
):
    """List recent ``mlb_script_breaks`` documents for the current user.

    Filters:
      • days        : look-back window (default 30, max 365)
      • limit       : max docs (default 100, max 500)
      • only_broken : True ⇒ return only docs where script_broken=True
      • script_code : filter by pregame script code (e.g. LOW_SCORING_PITCHERS_DUEL)
    """
    days  = max(1, min(int(days),  365))
    limit = max(1, min(int(limit), 500))
    try:
        from services.mlb_script_breaks_storage import query_recent_script_breaks
        items = await query_recent_script_breaks(
            db,
            user_id=user["id"] if scope == "me" else None,
            days=days,
            limit=limit,
            only_broken=bool(only_broken),
            script_code=script_code,
        )
        return {"ok": True, "count": len(items), "items": items}
    except Exception as exc:
        log.exception("mlb_script_breaks_list failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


@api.get("/mlb/script_breaks/stats")
async def mlb_script_breaks_stats(
    user: dict = Depends(get_current_user),
    days: int = 60,
):
    """Aggregate stats over recent script breaks (window in days, max 365)."""
    days = max(1, min(int(days), 365))
    try:
        from services.mlb_script_breaks_storage import aggregate_break_stats
        stats = await aggregate_break_stats(db, days=days)
        return {"ok": True, **stats}
    except Exception as exc:
        log.exception("mlb_script_breaks_stats failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


# ════════════════════════════════════════════════════════════════════════════
# V6 — Daily Market Audit + Bias Detection + Diversity Score
# ════════════════════════════════════════════════════════════════════════════
@api.get("/mlb/daily_market_audit")
async def mlb_daily_market_audit(
    user: dict = Depends(get_current_user),
    run_id: Optional[str] = None,
):
    """Generate the Daily Market Audit report for the latest (or given) MLB
    pick run. Includes market histogram, bias warnings (UNDER_BIAS_WARNING /
    OVER_BIAS_WARNING / OVER_STARVATION / MARKET_CONCENTRATION_WARNING) and
    market diversity score.

    Query params:
      • run_id : specific MLB pick_run.id to audit (defaults to latest for user).
    """
    try:
        from services.mlb_over_discovery import daily_market_audit
        # Find the relevant pick run document.
        q: dict = {"user_id": user["id"], "sport": "baseball"}
        if run_id:
            q["id"] = run_id
        run_doc = await db.pick_runs.find_one(q, sort=[("generated_at", -1)])
        if not run_doc:
            return {"ok": True, "status": "NO_RUN_FOUND",
                    "detail": "No MLB pick runs found for the current user."}
        # Collect all visible buckets (picks + rescued + structural-lean
        # + watchlist) since each is a "recommendation" the user could act on.
        payload = (run_doc.get("payload") or {})
        picks = (
            (payload.get("picks") or [])
            + (payload.get("rescued_picks") or [])
            + (payload.get("structural_lean_requires_odds") or [])
            + (payload.get("watchlist_manual_odds") or [])
        )
        # Evaluated count = total games considered by the pipeline.
        pipeline_meta = payload.get("pipeline_meta") or {}
        evaluated = (
            pipeline_meta.get("confirmed_games")
            or pipeline_meta.get("schedule_games_found")
            or pipeline_meta.get("scoring_total")
            or len(picks)
        )
        audit = daily_market_audit(picks, evaluated_count=evaluated)
        return {
            "ok":          True,
            "run_id":      run_doc.get("id"),
            "generated_at": run_doc.get("generated_at"),
            "audit":       audit,
        }
    except Exception as exc:
        log.exception("mlb_daily_market_audit failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


# ════════════════════════════════════════════════════════════════════════════
# Football Market Audit — per-discarded explicit market trace history
# ════════════════════════════════════════════════════════════════════════════
@api.get("/football/market_audit")
async def football_market_audit(
    user: dict = Depends(get_current_user),
    date: Optional[str] = None,
    limit: int = 30,
):
    """Return historic football market audits for the authenticated user.

    Each audit document contains the per-discarded ``market_trace`` and
    ``markets_checked`` rows produced by the engine, plus a rejection-code
    histogram so the UI can render an "Auditoría de mercados" timeline.

    Query params:
      • date  : optional `YYYY-MM-DD` filter (matches `match_date`).
      • limit : max number of documents to return (default 30, max 100).
    """
    try:
        from services.football_audit_storage import query_football_audit
        limit = max(1, min(100, int(limit or 30)))
        docs = await query_football_audit(
            db,
            user_id=user["id"],
            date=date,
            limit=limit,
        )
        # Aggregate a high-level histogram across all returned docs so the
        # client can show a "bias detected" indicator without re-computing.
        global_hist: dict[str, int] = {}
        total_discarded = 0
        for d in docs:
            meta = (d or {}).get("summary_meta") or {}
            hist = meta.get("histogram") or {}
            for k, v in hist.items():
                global_hist[k] = global_hist.get(k, 0) + int(v or 0)
            total_discarded += int((d or {}).get("total_discarded") or 0)
        return {
            "ok":              True,
            "count":           len(docs),
            "total_discarded": total_discarded,
            "histogram":       global_hist,
            "audits":          docs,
        }
    except Exception as exc:
        log.exception("football_market_audit failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


@api.post("/mlb/engine/recompute")
async def mlb_engine_recompute(user: dict = Depends(get_current_user)):
    """Force a recalibration cycle (still respects the batch_size_required
    threshold — returns null when not enough feedback rows are pending)."""
    try:
        from services.mlb_feedback_loop import recompute_weights_if_due
        result = await recompute_weights_if_due(db)
        if result is None:
            return {"ok": True, "recalibration": None,
                    "detail": "Not enough pending feedback rows yet."}
        return {"ok": True, "recalibration": result}
    except Exception as exc:
        log.exception("mlb engine recompute failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})


# ════════════════════════════════════════════════════════════════════════════
# MLB-V8 — Live Intelligence (Volatility + Script Break + Cashout)
# ════════════════════════════════════════════════════════════════════════════
class MLBLiveReevalIn(BaseModel):
    match_id:      str
    pregame_pick:  dict          # Full pick payload from /api/picks/today (must
                                  # include _mlb_script_v3 + recommendation).
    live_state:    Optional[dict] = None  # If None, we try to fetch from DB / API.


@api.post("/mlb/live/reevaluate")
async def mlb_live_reevaluate(payload: MLBLiveReevalIn,
                               user: dict = Depends(get_current_user)):
    """MLB-V8 Live Intelligence endpoint.

    Restrictions (per user spec):
      • Only applies to MLB picks that PASSED the pregame filter — the
        endpoint REQUIRES `pregame_pick._mlb_script_v3` to be present.
        Picks that were never processed by the V3 engine are rejected
        with 422, so this is NOT a global "live for every match" feature.
      • Designed to be called from MatchDetailPage when the user clicks on
        a recommended/rescued/manual-review pick.

    Inputs
    ------
    match_id      : MLB Stats API gamePk (string).
    pregame_pick  : The full pick payload from /api/picks/today
                    (carries _mlb_script_v3, _mlb_script_v2, recommendation).
    live_state    : Optional explicit live snapshot:
                    {current_inning, is_top_half, home_runs, away_runs,
                     home_starter_runs_allowed, away_starter_runs_allowed,
                     home_starter_pulled, away_starter_pulled,
                     bullpen_runs_allowed_home, bullpen_runs_allowed_away}.
                    If omitted, we attempt to derive a basic snapshot from the
                    `matches` collection — fail-soft (returns NOT_LIVE_YET when
                    no data available).
    """
    pregame_pick = payload.pregame_pick or {}
    if pregame_pick.get("sport") and pregame_pick.get("sport").lower() != "baseball":
        return JSONResponse(status_code=422,
                            content={"ok": False,
                                      "detail": "Live intelligence only available for baseball picks."})
    if not pregame_pick.get("_mlb_script_v3"):
        return JSONResponse(status_code=422,
                            content={"ok": False,
                                      "detail": ("Pick did not pass pregame filter (no _mlb_script_v3). "
                                                 "Live intelligence only applies to filtered picks.")})

    live_state = payload.live_state or {}
    # Fallback: try to derive from the matches collection if frontend didn't pass it.
    if not live_state:
        try:
            m_doc = await db.matches.find_one({"match_id": str(payload.match_id)})
            if m_doc:
                ls = (m_doc.get("live") or m_doc.get("linescore") or {})
                live_state = {
                    "current_inning":      ls.get("currentInning") or ls.get("current_inning"),
                    "is_top_half":         (ls.get("inningHalf") or ls.get("inning_half") or "top").lower() == "top",
                    "home_runs":           (ls.get("teams") or {}).get("home", {}).get("runs")
                                            or ls.get("home_runs") or 0,
                    "away_runs":           (ls.get("teams") or {}).get("away", {}).get("runs")
                                            or ls.get("away_runs") or 0,
                }
        except Exception as exc:
            log.debug("mlb_live_reevaluate live_state derivation failed: %s", exc)
            live_state = {}

    # If still nothing, surface a graceful NOT_LIVE_YET status so the UI can
    # tell the user "el partido aún no está en vivo".
    if not live_state.get("current_inning"):
        return {"ok": True,
                "status": "NOT_LIVE_YET",
                "detail": "El partido aún no está en vivo; live intelligence se activará al primer inning.",
                "match_id": payload.match_id}

    try:
        from services.mlb_live_intelligence import build_live_intelligence_payload
        intel = build_live_intelligence_payload(pregame_pick, live_state)
        return {"ok": True, "status": "LIVE_INTEL_OK", "match_id": payload.match_id,
                "intelligence": intel}
    except Exception as exc:
        log.exception("mlb_live_reevaluate failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": str(exc)})



@api.get("/stats/dashboard")
async def stats_dashboard(
    user: dict = Depends(get_current_user),
    stake: float = 10.0,
    sport: Optional[str] = None,
    market: Optional[str] = None,
):
    """KPIs for the authenticated user.

    Backward-compatible: the top-level keys (`total`, `won`, `lost`, `win_rate`,
    `roi`, `accuracy_by_tier`, `last10`, etc.) preserve the historical shape
    consumed by HistoryPage / ProfilePage.

    New (Phase P2 — sport-segmented stats):
      • `by_sport[sport]`               → same KPI block computed for one sport
      • `by_sport_market[sport][market]` → (sport, market) cell
      • `cross_sport_comparison`        → compact array for the comparator card
      • Optional `?sport=` / `?market=` filters the TOP-LEVEL block too, so
        the existing UI components automatically narrow when the user
        switches tab in the new SportStatsPanel.
    """
    base_query: dict = {"user_id": user["id"]}
    docs = await db.pick_tracking.find(base_query).sort("tracked_at", -1).to_list(length=2000)

    def _norm(s: Optional[str]) -> str:
        return (s or "football").lower().strip()

    # Optional top-level filter (so old UI consumers can re-render the SAME
    # block scoped to one sport without forking the endpoint).
    filt_sport = _norm_sport(sport) if sport else None
    filt_market = (market or "").strip().lower() or None

    def _docs_for(s: Optional[str], m: Optional[str]) -> list[dict]:
        out = docs
        if s is not None:
            out = [d for d in out if _norm(d.get("sport")) == s]
        if m:
            out = [d for d in out if (d.get("market") or "").lower().find(m) >= 0]
        return out

    def _kpis(subset: list[dict]) -> dict:
        total = len(subset)
        won = sum(1 for d in subset if d.get("outcome") == "won")
        lost = sum(1 for d in subset if d.get("outcome") == "lost")
        push = sum(1 for d in subset if d.get("outcome") == "push")
        pending = sum(1 for d in subset if d.get("outcome") == "pending")
        settled = won + lost
        win_rate = round((won / settled) * 100, 1) if settled else 0.0
        streak = 0
        for d in subset:
            o = d.get("outcome")
            if o == "won":
                streak += 1
            elif o == "lost":
                break
            else:
                continue
        last10 = [{
            "match_label": d.get("match_label"),
            "market": d.get("market"),
            "outcome": d.get("outcome"),
            "confidence_score": d.get("confidence_score"),
            "odds": d.get("odds"),
            "sport": d.get("sport"),
        } for d in subset[:10]]

        # Accuracy + ROI by confidence tier
        tiers = {
            "Maxima": {"won": 0, "lost": 0, "profit": 0.0, "wagered": 0.0},
            "Alta":   {"won": 0, "lost": 0, "profit": 0.0, "wagered": 0.0},
            "Media":  {"won": 0, "lost": 0, "profit": 0.0, "wagered": 0.0},
        }
        total_profit = 0.0
        total_wagered = 0.0
        sum_won_odds = 0.0
        sum_lost_odds = 0.0
        for d in subset:
            cs = d.get("confidence_score", 0) or 0
            tier = "Maxima" if cs >= 80 else ("Alta" if cs >= 70 else ("Media" if cs >= 60 else None))
            outcome = d.get("outcome")
            if outcome not in ("won", "lost"):
                continue
            odds = float(d.get("odds") or 0)
            if odds <= 1.0:
                continue
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
        picks_with_odds = sum(
            1 for d in subset
            if (d.get("odds") or 0) > 1.0 and d.get("outcome") in ("won", "lost")
        )

        accuracy_by_tier = {}
        for tier_name, v in tiers.items():
            settled_tier = v["won"] + v["lost"]
            accuracy_by_tier[tier_name] = {
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

    # ── Top-level block (filtered if `?sport=` or `?market=` provided) ──
    top_subset = _docs_for(filt_sport, filt_market)
    top = _kpis(top_subset)

    # ── Sport-segmented (always computed against the FULL tracking set,
    #    independent of the optional filter — so the SportStatsPanel can show
    #    every sport's KPIs at once even when one tab is active) ────────────
    sports_seen = sorted({_norm(d.get("sport")) for d in docs})
    if not sports_seen:
        sports_seen = ["football"]

    by_sport: dict[str, dict] = {}
    by_sport_market: dict[str, dict] = {}
    for s in sports_seen:
        s_subset = _docs_for(s, None)
        by_sport[s] = _kpis(s_subset)
        # Per-market cell within this sport (top 6 most-tracked markets).
        market_counts: dict[str, int] = {}
        for d in s_subset:
            mk = d.get("market") or "Unknown"
            market_counts[mk] = market_counts.get(mk, 0) + 1
        top_markets = [mk for mk, _ in sorted(market_counts.items(), key=lambda kv: -kv[1])[:6]]
        by_sport_market[s] = {mk: _kpis([d for d in s_subset if (d.get("market") or "Unknown") == mk]) for mk in top_markets}

    # ── Cross-sport compact comparator (for the side-by-side card) ─────────
    cross_sport_comparison = [
        {
            "sport": s,
            "total": by_sport[s]["total"],
            "settled": by_sport[s]["roi"]["settled_total"],
            "win_rate": by_sport[s]["win_rate"],
            "roi_pct": by_sport[s]["roi"]["roi_pct"],
            "total_profit": by_sport[s]["roi"]["total_profit"],
            "streak": by_sport[s]["streak"],
        }
        for s in sports_seen
    ]

    return {
        # Backward-compatible top-level keys (optionally filtered):
        **top,
        # New Phase-P2 segmentation:
        "by_sport": by_sport,
        "by_sport_market": by_sport_market,
        "cross_sport_comparison": cross_sport_comparison,
        "filters_applied": {"sport": filt_sport, "market": filt_market},
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


# ── Bright Data Web Unlocker health ─────────────────────────────────────────
@api.get("/admin/brightdata")
async def admin_brightdata_health(
    probe: bool = False,
    user: dict = Depends(get_current_user),
):
    """Diagnose the Bright Data Web Unlocker integration.

    Returns:
      • Whether the token + zone are configured.
      • A 24h rolling counter of fetches / successes / failures (in-memory).
      • The last fetch's status + URL (for quick "is it still working?" checks).
      • When `probe=true`, runs a tiny live request against
        https://geo.brdtest.com/mygeo.json — useful to confirm the credential
        still authorises a fetch.
    """
    try:
        from services.brightdata_client import get_health_snapshot, healthcheck
    except Exception as exc:
        log.exception("brightdata_client import failed")
        return JSONResponse(status_code=500,
                            content={"ok": False, "detail": f"import failed: {exc}"})

    token_present = bool((os.environ.get("BRIGHTDATA_TOKEN") or "").strip())
    api_key_present = bool((os.environ.get("BRIGHTDATA_API_KEY") or "").strip())
    zone = (
        os.environ.get("BRIGHTDATA_ZONE")
        or "web_unlocker1"
    )
    editorial_enabled = (
        os.environ.get("EDITORIAL_BRIGHTDATA_ENABLED", "true").lower()
        in ("1", "true", "yes")
    )
    snapshot = get_health_snapshot()
    payload: dict = {
        "ok":                   token_present or api_key_present,
        "token_present":        token_present,
        "api_key_present":      api_key_present,
        "zone":                 zone,
        "editorial_enabled":    editorial_enabled,
        "ledger_24h":           snapshot,
        "now":                  datetime.now(timezone.utc).isoformat(),
    }
    if probe:
        try:
            probe_result = await asyncio.wait_for(healthcheck(), timeout=12.0)
        except asyncio.TimeoutError:
            probe_result = {"ok": False, "reason": "timeout"}
        except Exception as exc:
            probe_result = {"ok": False, "reason": str(exc)}
        payload["probe"] = probe_result
    return payload




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


# ── Saved Filter Views ───────────────────────────────────────────────────────
class SavedViewIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    filters: dict = Field(default_factory=dict)
    enginePreset: Optional[str] = None
    sport: Optional[str] = None  # informational; reused on apply


@api.get("/profile/saved-views")
async def saved_views_list(user: dict = Depends(get_current_user)):
    """Return user's saved filter views. Newest first, capped to SAVED_VIEWS_MAX."""
    cursor = db.saved_views.find({"user_id": user["id"]}).sort("created_at", -1).limit(30)
    items = await cursor.to_list(length=30)
    return {"items": [_clean(it) for it in items], "max": 10}


SAVED_VIEWS_MAX = 10


@api.post("/profile/saved-views")
async def saved_views_create(payload: SavedViewIn, user: dict = Depends(get_current_user)):
    """Create a new saved view. Cap of SAVED_VIEWS_MAX per user — oldest gets evicted."""
    if payload.sport and payload.sport not in SUPPORTED_SPORTS:
        raise HTTPException(status_code=400, detail="invalid sport")
    existing = await db.saved_views.count_documents({"user_id": user["id"]})
    evicted_id = None
    if existing >= SAVED_VIEWS_MAX:
        oldest = await db.saved_views.find({"user_id": user["id"]}).sort("created_at", 1).limit(1).to_list(length=1)
        if oldest:
            evicted_id = oldest[0].get("id")
            await db.saved_views.delete_one({"_id": oldest[0]["_id"]})
    doc = {
        "id": uuid.uuid4().hex[:14],
        "user_id": user["id"],
        "name": payload.name.strip()[:60],
        "filters": payload.filters or {},
        "enginePreset": payload.enginePreset or None,
        "sport": payload.sport or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.saved_views.insert_one(doc)
    out = _clean(doc)
    if evicted_id:
        out["_evicted_id"] = evicted_id
    return out


class SavedViewPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    filters: Optional[dict] = None
    enginePreset: Optional[str] = None
    sport: Optional[str] = None


@api.patch("/profile/saved-views/{view_id}")
async def saved_views_update(view_id: str, payload: SavedViewPatch, user: dict = Depends(get_current_user)):
    """Update an existing saved view (name / filters / preset / sport)."""
    existing = await db.saved_views.find_one({"id": view_id, "user_id": user["id"]})
    if not existing:
        raise HTTPException(status_code=404, detail="view not found")
    updates: dict = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip()[:60]
    if payload.filters is not None:
        updates["filters"] = payload.filters
    if payload.enginePreset is not None:
        # Allow empty string to clear preset
        updates["enginePreset"] = payload.enginePreset or None
    if payload.sport is not None:
        if payload.sport and payload.sport not in SUPPORTED_SPORTS:
            raise HTTPException(status_code=400, detail="invalid sport")
        updates["sport"] = payload.sport or None
    if not updates:
        return _clean(existing)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.saved_views.update_one({"id": view_id, "user_id": user["id"]}, {"$set": updates})
    fresh = await db.saved_views.find_one({"id": view_id, "user_id": user["id"]})
    return _clean(fresh)


@api.delete("/profile/saved-views/{view_id}")
async def saved_views_delete(view_id: str, user: dict = Depends(get_current_user)):
    res = await db.saved_views.delete_one({"id": view_id, "user_id": user["id"]})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="view not found")
    return {"ok": True, "id": view_id}


# ── Historical Learning Layer ────────────────────────────────────────────────
#
# Aggregates user's tracked picks into pattern statistics:
#   - winrate per (sport, market, match_state) bucket
#   - sample size
#   - market reliability score (winrate weighted by sample size)
#   - engine_agreement: how often the same combination has been picked vs how
#     many times it was a winning result (proxy for engine confidence in pattern)
#
# We compute on read with a small cache (60s) to keep it simple — there's no
# need to schedule a heavy aggregation job for typical user volumes.
_LEARNING_CACHE: dict[str, tuple[float, dict]] = {}
_LEARNING_TTL = 60.0


def _bucket_match_state(pick_payload: dict) -> str:
    """Best-effort recovery of match_state from the LLM payload."""
    if pick_payload.get("match_state"):
        return pick_payload["match_state"]
    mot = pick_payload.get("motivation") or {}
    h = (mot.get("home") or {}).get("level") or 3
    a = (mot.get("away") or {}).get("level") or 3
    if h >= 4 and a >= 4: return "HIGH_MOTIVATION"
    if h <= 2 and a <= 2: return "LOW_URGENCY"
    return "CONTROLLED_MATCH"


@api.get("/learning/stats")
async def learning_stats(
    sport: Optional[str] = None,
    market: Optional[str] = None,
    match_state: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """Return historical patterns for the authenticated user.

    Optional filters narrow down to a specific (sport, market, match_state).
    The response always includes:
      - `patterns`: list of buckets with winrate, sample size, reliability
      - `summary`: top reliable market per match_state
      - `total_tracked`: total settled tracked picks contributing
    """
    cache_key = f"{user['id']}|{sport or ''}|{market or ''}|{match_state or ''}"
    import time
    now = time.time()
    cached = _LEARNING_CACHE.get(cache_key)
    if cached and (now - cached[0] < _LEARNING_TTL):
        return cached[1]

    # Pull settled tracked picks for this user. `result` field holds 'win'|'lose'|'void'.
    tracked = await db.pick_tracking.find({
        "user_id": user["id"],
        "result": {"$in": ["win", "lose", "void"]},
    }).to_list(length=2000)

    # Index pick_runs to enrich tracking entries with market + state context.
    run_ids = list({t.get("pick_id") for t in tracked if t.get("pick_id")})
    runs = await db.picks.find({"id": {"$in": run_ids}}).to_list(length=2000) if run_ids else []
    run_by_id = {r.get("id"): r for r in runs}

    # Walk tracked entries → derive (sport, market, match_state) bucket → win
    buckets: dict[tuple, dict] = {}
    settled_total = 0
    for t in tracked:
        run = run_by_id.get(t.get("pick_id"))
        if not run:
            continue
        payload = (run.get("payload") or {})
        run_sport = run.get("sport") or payload.get("_sport") or "football"
        match_id = t.get("match_id")
        target_pick = next((p for p in (payload.get("picks") or []) if p.get("match_id") == match_id), None)
        if not target_pick:
            continue
        rec = (target_pick.get("recommendation") or {})
        market_key = rec.get("market") or "Unknown"
        state_key = _bucket_match_state(target_pick)
        if sport and sport.lower() != run_sport.lower():
            continue
        if market and market.lower() not in market_key.lower():
            continue
        if match_state and match_state != state_key:
            continue

        bk = (run_sport, market_key, state_key)
        b = buckets.setdefault(bk, {"sport": run_sport, "market": market_key, "match_state": state_key, "wins": 0, "losses": 0, "voids": 0, "samples": 0})
        b["samples"] += 1
        settled_total += 1
        res = t.get("result")
        if res == "win": b["wins"] += 1
        elif res == "lose": b["losses"] += 1
        elif res == "void": b["voids"] += 1

    # Compute derived fields
    patterns = []
    for b in buckets.values():
        decisive = b["wins"] + b["losses"]
        winrate = (b["wins"] / decisive) if decisive else None
        # Reliability = winrate weighted by sample-size confidence (cap at 30 samples)
        weight = min(1.0, b["samples"] / 30)
        reliability = round(((winrate or 0) * weight) * 100, 1) if winrate is not None else 0.0
        # Engine agreement = how strongly the engine repeatedly picks this combo
        # (samples * winrate gives a notion of consistent successful selection)
        engine_agreement = round(min(100.0, b["samples"] * (winrate or 0) * 4.0), 1)
        patterns.append({
            **b,
            "winrate": round(winrate * 100, 1) if winrate is not None else None,
            "reliability": reliability,
            "engine_agreement": engine_agreement,
        })
    patterns.sort(key=lambda p: (-(p["reliability"]), -p["samples"]))

    # Summary: top market per match_state
    summary_by_state: dict[str, dict] = {}
    for p in patterns:
        ms = p["match_state"]
        if ms not in summary_by_state or p["reliability"] > summary_by_state[ms]["reliability"]:
            summary_by_state[ms] = {
                "match_state": ms,
                "best_market": p["market"],
                "winrate": p["winrate"],
                "samples": p["samples"],
                "reliability": p["reliability"],
            }

    out = {
        "patterns": patterns,
        "summary": list(summary_by_state.values()),
        "total_tracked": settled_total,
        "filters": {"sport": sport, "market": market, "match_state": match_state},
    }
    _LEARNING_CACHE[cache_key] = (now, out)
    return out




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
    """Return chronological winrate evolution per settled pick (for chart).

    Backward-compatible with two schemas in pick_tracking:
      - newer flow:  field `outcome` in {'won','lost','push','pending'}
      - older seed:  field `result`  in {'win','lose','void'}

    Each timeline entry carries (market, selection, odds, confidence, league)
    so the chart tooltip can show full context for any data point.
    """
    docs = await db.pick_tracking.find({"user_id": user["id"]}).sort("tracked_at", 1).to_list(length=limit * 4)
    cumulative_won = 0
    cumulative_settled = 0
    timeline = []
    for d in docs:
        # Normalize outcome across both schemas
        raw_out = d.get("outcome") or d.get("result")
        if raw_out in ("won", "win"):
            norm_out = "won"
        elif raw_out in ("lost", "lose"):
            norm_out = "lost"
        else:
            continue  # skip pending/push/void/None for timeline math
        cumulative_settled += 1
        if norm_out == "won":
            cumulative_won += 1
        rate = round((cumulative_won / cumulative_settled) * 100, 1) if cumulative_settled else 0.0
        timeline.append({
            "tracked_at": d.get("tracked_at"),
            "match_label": d.get("match_label"),
            "league": d.get("league"),
            "market": d.get("market"),
            "selection": d.get("selection"),
            "outcome": norm_out,
            "confidence_score": d.get("confidence_score"),
            "odds": d.get("odds"),
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


# ── Knowledge Base — Learning Cases ──────────────────────────────────────────
class LearningCaseIn(BaseModel):
    """Payload for POST /api/learning/cases.

    `rule_key` is the stable identifier the engine uses to fire the rule
    (e.g. `close_match_moderate_pace_prefer_u35`). When omitted, the case
    is stored as a passive note (not actively applied to the under scan).
    """
    case_id:      Optional[str] = None
    title:        str
    rule_key:     Optional[str] = None
    match_label:  Optional[str] = None
    league:       Optional[str] = None
    date:         Optional[str] = None
    engine_pick:  Optional[str] = None
    user_pick:    Optional[str] = None
    user_odds:    Optional[float] = None
    stake:        Optional[float] = None
    payout:       Optional[float] = None
    final_score:  Optional[str] = None
    outcome:      Optional[str] = None
    trigger_context: Optional[dict] = None
    lesson_es:    Optional[str] = None
    lesson_en:    Optional[str] = None
    tags:         Optional[list[str]] = None


@api.get("/learning/cases")
async def learning_cases_list(
    rule_key: Optional[str] = None,
    limit: int = 50,
    user: dict = Depends(get_current_user),
):
    """Return the active knowledge base entries.

    Cases are seeded at startup (e.g. Pumas-Cruz Azul) and can be appended
    by the user via POST. They are global (not per-user) because they
    encode betting heuristics that benefit every account equally.
    """
    from services import learning_cases as lc
    cases = await lc.list_cases(db, limit=max(1, min(limit, 200)), rule_key=rule_key)
    return {
        "count":         len(cases),
        "rule_keys":     [lc.RULE_CLOSE_MODERATE_PRIORITISE_U35],
        "items":         cases,
        "computed_at":   datetime.now(timezone.utc).isoformat(),
    }


@api.post("/learning/cases")
async def learning_cases_create(
    payload: LearningCaseIn,
    user: dict = Depends(get_current_user),
):
    """Persist a new learning case.

    The case is upserted by `case_id` when provided so the same lesson
    can be refined over time without duplicates. Returns the stored doc.
    """
    from services import learning_cases as lc
    case_dict = payload.model_dump(exclude_none=True)
    if not case_dict.get("case_id"):
        case_dict["case_id"] = f"lc_{uuid.uuid4().hex[:12]}"
    case_dict["_source"] = case_dict.get("_source") or "user_submitted"
    case_dict["created_by"] = user["id"]
    saved = await lc.save_case(db, case_dict)
    # Drop Mongo _id if present (BSON-safe)
    if saved and "_id" in saved:
        saved = {k: v for k, v in saved.items() if k != "_id"}
    return {"case": saved, "computed_at": datetime.now(timezone.utc).isoformat()}


@api.post("/learning/cases/seed")
async def learning_cases_seed(user: dict = Depends(get_current_user)):
    """Re-run the idempotent seed (mostly for debugging / admin)."""
    from services import learning_cases as lc
    inserted = await lc.seed_cases(db)
    return {"inserted": inserted, "computed_at": datetime.now(timezone.utc).isoformat()}


# ── Understat enrichment (admin / debug) ────────────────────────────────────
@api.get("/understat/match/{match_id}")
async def understat_match(match_id: int, user: dict = Depends(get_current_user)):
    """Return normalised Understat enrichment for one match id.

    Uses Mongo TTL cache (12h). Intended for debugging / manual lookup;
    automatic per-fixture enrichment is wired in the ingestion pipeline.
    """
    from services import understat_scraper as us
    payload = await us.fetch_match_cached(db, match_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"Understat match {match_id} not found or unreachable.",
        )
    return {
        "ok":          True,
        "match":       payload,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@api.post("/understat/team-form")
async def understat_team_form(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    """Aggregate rolling xG / PPDA / shots for a team across given match ids.

    Body:
      {
        "team_name":  "Arsenal",
        "match_ids":  [26586, 26604, 26619, ...]
      }

    Returns the aggregate produced by `understat_scraper.aggregate_team_form`.
    """
    from services import understat_scraper as us
    team_name = (payload.get("team_name") or "").strip()
    ids = payload.get("match_ids") or []
    if not team_name or not isinstance(ids, list) or not ids:
        raise HTTPException(
            status_code=400,
            detail="team_name and non-empty match_ids list are required.",
        )
    # Cap at 25 ids to avoid hammering Understat
    ids = [int(i) for i in ids[:25] if str(i).isdigit()]
    matches = []
    for mid in ids:
        m = await us.fetch_match_cached(db, mid)
        if m:
            matches.append(m)
    agg = us.aggregate_team_form(matches, team_name)
    if agg is None:
        raise HTTPException(
            status_code=404,
            detail=f"No Understat matches found for {team_name} in the given ids.",
        )
    return {
        "ok":             True,
        "team_form":      agg,
        "matches_used":   len(matches),
        "matches_asked":  len(ids),
        "computed_at":    datetime.now(timezone.utc).isoformat(),
    }


@api.post("/understat/link")
async def understat_link(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    """Link a Understat match_id to an API-Sports fixture and enrich it.

    Body:
      {
        "match_id":            "<API-Sports match_id>",
        "understat_match_id":  14091
      }

    On success persists the enrichment into the match doc under
    `_understat` (the analyst engine consumes this field in its next run)
    and adds `_provenance.understat = "linked"`. Returns the enrichment.
    """
    from services import understat_scraper as us
    match_id = str(payload.get("match_id") or "").strip()
    u_id_raw = payload.get("understat_match_id")
    try:
        u_id = int(u_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="understat_match_id must be int.")
    if not match_id:
        raise HTTPException(status_code=400, detail="match_id is required.")

    # Ensure the fixture exists in our DB. `match_id` may be stored as int
    # (API-Sports fixtures) or str (manual entries), so try both.
    fixture = await db.matches.find_one({"match_id": match_id})
    if not fixture and match_id.isdigit():
        fixture = await db.matches.find_one({"match_id": int(match_id)})
    if not fixture:
        raise HTTPException(status_code=404, detail=f"match {match_id} not found.")

    enrichment = await us.fetch_match_cached(db, u_id)
    if enrichment is None:
        raise HTTPException(
            status_code=404,
            detail=f"Understat match {u_id} not reachable.",
        )

    # Use the same type the fixture uses for the update query
    query_mid = fixture.get("match_id")
    await db.matches.update_one(
        {"match_id": query_mid},
        {"$set": {
            "_understat":                 enrichment,
            "understat_match_id":         u_id,
            "_provenance.understat":      "linked",
            "_understat_linked_at":       datetime.now(timezone.utc).isoformat(),
            "_understat_linked_by":       user["id"],
        }},
    )
    return {
        "ok":          True,
        "match_id":    query_mid,
        "linked_to":   u_id,
        "enrichment":  enrichment,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@api.post("/understat/auto-link")
async def understat_auto_link(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    """Best-effort automatic linker: given an API-Sports match_id, search
    Understat (via DDG) for the corresponding match and persist it.

    Body:
      { "match_id": "<API-Sports id>" }

    Returns the same shape as `/understat/link` on success, or a 404
    when no Understat coverage is found (out-of-scope league, no match
    found, or team naming too different to fuzzy-match).
    """
    from services import understat_scraper as us
    match_id = str(payload.get("match_id") or "").strip()
    if not match_id:
        raise HTTPException(status_code=400, detail="match_id is required.")

    fixture = await db.matches.find_one({"match_id": match_id})
    if not fixture and match_id.isdigit():
        fixture = await db.matches.find_one({"match_id": int(match_id)})
    if not fixture:
        raise HTTPException(status_code=404, detail=f"match {match_id} not found.")

    home = (fixture.get("home_team") or {}).get("name") or ""
    away = (fixture.get("away_team") or {}).get("name") or ""
    if not home or not away:
        raise HTTPException(status_code=400, detail="fixture has no team names.")

    # Run the DDG-backed fuzzy linker in an executor (blocking I/O).
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: us.fuzzy_link_match(home, away, fixture.get("kickoff_iso")),
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No Understat match found for {home} vs {away}. "
                "Likely out-of-scope league (Understat covers top-5 + RFPL) "
                "or team naming too different."
            ),
        )

    u_id = result["understat_match_id"]
    enrichment = result["enrichment"]
    query_mid = fixture.get("match_id")
    await db.matches.update_one(
        {"match_id": query_mid},
        {"$set": {
            "_understat":             enrichment,
            "understat_match_id":     u_id,
            "_provenance.understat":  "auto_linked",
            "_understat_linked_at":   datetime.now(timezone.utc).isoformat(),
            "_understat_linked_by":   user["id"],
        }},
    )
    return {
        "ok":                  True,
        "match_id":            query_mid,
        "linked_to":           u_id,
        "enrichment":          enrichment,
        "candidates_checked":  result.get("candidates_checked"),
        "fuzzy_score": {
            "home_api":         home,
            "away_api":         away,
            "home_understat":   (enrichment.get("teams") or {}).get("home"),
            "away_understat":   (enrichment.get("teams") or {}).get("away"),
            "match_date":       result.get("match_date"),
        },
        "computed_at":         datetime.now(timezone.utc).isoformat(),
    }


# ── App registration ─────────────────────────────────────────────────────────
app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
