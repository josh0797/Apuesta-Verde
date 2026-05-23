"""Data ingestion orchestrator (multi-sport).

Flow:
  1) Try API-Sports for fixtures/odds/context (football | basketball | baseball).
  2) Filter early via the football competition allowlist (services.football_competitions)
     to avoid wasting hydration + LLM budget on lower divisions.
  3) Sort retained matches by competition tier priority (Tier 1 → Tier 2 → Tier 3),
     then kickoff time, with a live-boost.
  4) Hydrate odds + context + standings for the top FOOTBALL_MAX_MATCHES_TO_HYDRATE.
  5) If API fails entirely, fallback to ESPN public scoreboard (football only).
  6) Persist normalized docs in MongoDB collections.

Collections:
  matches          (key: match_id) — now also stores `sport` + competition_* fields
  odds_snapshots   (history of odds per fixture)
  picks            (LLM output) — also stores `sport`
  pick_tracking    (user marks) — also stores `sport`
  users
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx

from . import api_football as af  # legacy football-only client (kept for backward compat)
from . import api_sports as aps    # generic multi-sport client
from . import provenance as prov   # Phase P2: per-section source/freshness tagging
from . import fallback_scraper as fb
from . import football_competitions as fc
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
async def discover_priority_fixtures(
    client: httpx.AsyncClient,
    db,
    *,
    window_hours: int = 48,
    season_override: Optional[int] = None,
) -> list[dict]:
    """Phase 8.1 — surgically discover fixtures in top-12 priority leagues.

    Why this exists:
      • The global `/fixtures?date=…` firehose returns 200+ matches per
        day across every confederation, including Côte d'Ivoire U17 and
        Botswana Premier League. Even with the football_quality filter
        downstream, the relevance signal was being diluted.
      • This helper hits `/fixtures?date=YYYY-MM-DD` for today and
        tomorrow (no `season` param — that one is locked to the free-plan
        proxy season 2024 and would miss live 2025/26 fixtures) and then
        client-side filters down to the league_ids of the top-12
        competitions. 2 API calls total instead of 12.

    Returns:
        Raw API-Sports fixture payloads (same shape as `fixtures_next_48h`)
        for the priority leagues that have at least one upcoming match in
        the window. Always sorted by kickoff_ts ascending.

    The caller (server._run_analysis_pipeline) treats a non-empty result as
    the AUTHORITATIVE candidate list — every other source is ignored unless
    `high_volume_mode` is explicitly enabled.
    """
    # Priority ladder per spec: Champions / World Cup → Big Five → Liga MX
    # → secondary continental cups. We keep the human label too so logs
    # tell operators which league actually had matches.
    PRIORITY_LADDER: list[tuple[str, int]] = [
        ("UEFA Champions League",  2),
        ("FIFA World Cup",         1),
        ("Premier League",         39),
        ("LaLiga",                 140),
        ("Serie A",                135),
        ("Bundesliga",             78),
        ("Liga MX",                262),
        ("Ligue 1",                61),
        ("UEFA Europa League",     3),
        ("UEFA Conference League", 848),
        ("Copa Libertadores",      13),
        ("MLS",                    253),
        ("Brasileirão Série A",   71),
        ("UEFA Euro",              4),
        ("Copa América",          9),
    ]
    priority_ids: set[int] = {lid for _, lid in PRIORITY_LADDER}
    id_to_label = {lid: name for name, lid in PRIORITY_LADDER}

    # Pull today + tomorrow via the date endpoint (no season constraint).
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    raw: list[dict] = []
    for d in (today, tomorrow):
        try:
            chunk = await af.fixtures_by_date(client, d.isoformat())
            raw.extend(chunk)
        except Exception as exc:
            log.warning("priority discover: /fixtures?date=%s failed: %s", d, exc)

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=window_hours)
    discovered: list[dict] = []
    counts: dict[str, int] = {}
    for fx in raw:
        try:
            lid = (fx.get("league") or {}).get("id")
            if lid not in priority_ids:
                continue
            ts = fx["fixture"]["timestamp"]
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            status = fx["fixture"]["status"]["short"]
        except Exception:
            continue
        if status not in ("NS", "TBD"):
            continue
        if not (now - timedelta(minutes=10) <= dt <= cutoff):
            continue
        discovered.append(fx)
        label = id_to_label.get(lid, str(lid))
        counts[label] = counts.get(label, 0) + 1

    discovered.sort(key=lambda f: f.get("fixture", {}).get("timestamp") or 0)
    log.info(
        "discover_priority_fixtures: %d fixtures (window=%dh) → %s",
        len(discovered), window_hours,
        {k: v for k, v in counts.items() if v > 0} or "no priority matches",
    )
    return discovered


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
        before_filter = 0
        kept_filter = 0
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
            before_filter += 1
            # Apply the same allowlist on the fallback path
            league_name = (ev.get("league") or "").strip()
            meta = fc.get_competition_meta(league_name)
            if not (meta and meta["tier"] in fc.ALLOWED_TIERS):
                continue
            kept_filter += 1
            doc = {
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
            }
            # Phase P2 — provenance for ESPN fallback path. We only have the
            # fixture itself (no odds, no stats, no h2h, no lineups).
            prov.attach_to_match(
                doc,
                primary_source="espn",
                odds_available=False,
                stats_available=False,
                h2h_available=False,
                lineups_available=False,
                context_available=False,
                live_available=False,
            )
            fc.annotate_match_competition(doc, league_name)
            minimal.append(doc)
        log.info(
            "ESPN fallback: %d events -> %d kept after allowlist filter",
            before_filter, kept_filter,
        )
        # Sort by tier priority then kickoff
        minimal.sort(key=lambda d: (-d.get("competition_priority", 0), d.get("kickoff_iso") or ""))
        # Apply hydration cap on fallback too
        minimal = minimal[:fc.MAX_MATCHES_TO_HYDRATE]
        for m in minimal:
            await db.matches.update_one({"match_id": m["match_id"]}, {"$set": m}, upsert=True)
        return minimal

    # ── Tier-based competition filtering (football only) ──────────────────
    # Drops 95%+ of global lower-division noise BEFORE any hydration/LLM work.
    # Non-football sports keep the legacy top_leagues behavior.
    if sport == "football":
        before = len(upcoming_raw)
        kept: list[dict] = []
        tier_counts = {"tier_1": 0, "tier_2": 0, "tier_3": 0}
        removed_leagues: dict[str, int] = {}
        for f in upcoming_raw:
            league_name = (_fx_league(sport, f).get("name") or "").strip()
            meta = fc.get_competition_meta(league_name)
            if meta and meta["tier"] in fc.ALLOWED_TIERS:
                tier_counts[meta["tier"]] = tier_counts.get(meta["tier"], 0) + 1
                # Attach metadata to the raw payload for later annotation.
                f["_competition_meta"] = meta
                kept.append(f)
            else:
                removed_leagues[league_name or "?"] = removed_leagues.get(league_name or "?", 0) + 1
        log.info(
            "Scraper fetched %d football events. Allowed competition filter kept %d matches. "
            "Removed %d matches from non-priority leagues.",
            before, len(kept), before - len(kept),
        )
        log.info(
            "Tier 1: %d  Tier 2: %d  Tier 3: %d  (allowed_tiers=%s)",
            tier_counts["tier_1"], tier_counts["tier_2"], tier_counts["tier_3"],
            sorted(fc.ALLOWED_TIERS),
        )

        # Sort: tier priority desc → kickoff time asc → (live boost handled in ingest_live)
        kept.sort(key=lambda f: (
            -((f.get("_competition_meta") or {}).get("priority", 0)),
            _fx_timestamp(sport, f) or 0,
        ))

        # Hydrate at most FOOTBALL_MAX_MATCHES_TO_HYDRATE; analyze at most _TO_ANALYZE.
        hydrate_cap = min(fc.MAX_MATCHES_TO_HYDRATE, max_total * 2 if max_total else fc.MAX_MATCHES_TO_HYDRATE)
        analyze_cap = min(fc.MAX_MATCHES_TO_ANALYZE, max_total or fc.MAX_MATCHES_TO_ANALYZE)
        selected = kept[:hydrate_cap]
        # Final analyzable cohort = top-N within the hydrated set.
        # (The LLM stage caps `max_matches` itself; this is just an upper bound.)
        log.info(
            "Hydrating %d / analyzing up to %d football matches",
            len(selected), analyze_cap,
        )
    else:
        # Non-football: keep legacy top_leagues-set behavior.
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
                # Attach tier metadata if computed at filter time
                if sport == "football":
                    fc.annotate_match_competition(res, res.get("league"))
                    # Persist back to DB so picks/today and other endpoints can read it
                    await db.matches.update_one(
                        {"match_id": res["match_id"]},
                        {"$set": {
                            "competition_tier": res.get("competition_tier"),
                            "competition_priority": res.get("competition_priority"),
                            "competition_canonical_name": res.get("competition_canonical_name"),
                            "competition_type": res.get("competition_type"),
                            "competition_region": res.get("competition_region"),
                            "allowed_competition": res.get("allowed_competition"),
                        }},
                    )
                enriched.append(res)
        except Exception as exc:
            log.exception("ingest enrich failed [%s]: %s", sport, exc)
    log.info("Sent %d candidates downstream after enrichment (sport=%s)", len(enriched), sport)
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

    if sport == "football":
        before = len(live_raw)
        kept: list[dict] = []
        for f in live_raw:
            league_name = (_fx_league(sport, f).get("name") or "").strip()
            meta = fc.get_competition_meta(league_name)
            if meta and meta["tier"] in fc.ALLOWED_TIERS:
                f["_competition_meta"] = meta
                kept.append(f)
        kept.sort(key=lambda f: -((f.get("_competition_meta") or {}).get("priority", 0)))
        selected = kept[:max_total]
        log.info(
            "Live scraper: %d events -> %d kept after tier filter (allowed_tiers=%s)",
            before, len(selected), sorted(fc.ALLOWED_TIERS),
        )
    else:
        top_set = _top_leagues_for(sport)
        top = [f for f in live_raw if _fx_league(sport, f).get("id") in top_set]
        others = [f for f in live_raw if _fx_league(sport, f).get("id") not in top_set]
        selected = (top + others)[:max_total]

    # Serial for non-football to respect single shared rate limit
    enriched: list[dict] = []
    if sport == "football":
        enriched_results = await asyncio.gather(*[enrich_fixture(client, db, f, True, sport=sport) for f in selected])
        enriched = [e for e in enriched_results if e]
        for m in enriched:
            fc.annotate_match_competition(m, m.get("league"))
            await db.matches.update_one(
                {"match_id": m["match_id"]},
                {"$set": {
                    "competition_tier": m.get("competition_tier"),
                    "competition_priority": m.get("competition_priority"),
                    "competition_canonical_name": m.get("competition_canonical_name"),
                    "competition_type": m.get("competition_type"),
                    "competition_region": m.get("competition_region"),
                    "allowed_competition": m.get("allowed_competition"),
                }},
            )
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
            "round": fx_raw["league"].get("round"),
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
        # Phase P2 — provenance: API-Sports is authoritative for the football
        # path; every section here was fetched from the same provider.
        prov.attach_to_match(
            match_doc,
            primary_source="api_sports",
            odds_available=bool(norm_odds.get("available")),
            stats_available=bool(stats_h or stats_a),
            h2h_available=bool(h2h_clean),
            lineups_available=False,            # not fetched on this path
            context_available=bool(ctx_home.get("position") or ctx_home.get("form_last_5")),
            live_available=bool(live_stats),
        )
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
        # Phase P2 — provenance: API-Sports authoritative for basket/baseball.
        prov.attach_to_match(
            match_doc,
            primary_source="api_sports",
            odds_available=bool(norm_odds.get("available")),
            stats_available=bool(stats_h or stats_a),
            h2h_available=bool(h2h_clean),
            lineups_available=False,
            context_available=bool(ctx_home.get("position") or ctx_home.get("wins_total")),
            live_available=bool(live_stats),
        )
        await db.matches.update_one({"match_id": fid}, {"$set": match_doc}, upsert=True)
        if norm_odds.get("available"):
            await db.odds_snapshots.insert_one({"match_id": fid, "sport": sport, **norm_odds})
        return match_doc
    except Exception as exc:
        log.exception("enrich_generic[%s] failed: %s", sport, exc)
        return None
