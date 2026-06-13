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
from .external_sources import (
    thestatsapi_team_stats_adapter as _ts_team_stats,
    thestatsapi_h2h_adapter as _ts_h2h,
)

log = logging.getLogger("ingestion")

# Phase F84.a — Prioridad-inversa: TheStatsAPI primaria, API-Sports
# fallback. Defaults to ``true`` to preserve the previous behaviour
# (every TheStatsAPI miss gracefully falls back to api_football). Set
# ``ENABLE_API_SPORTS_FALLBACK=false`` to enter "TheStatsAPI-only" mode
# — useful for staging environments that need to surface coverage gaps.
import os as _os  # used only by the F84.a fallback flag helper below
def _api_sports_fallback_enabled() -> bool:
    raw = (_os.environ.get("ENABLE_API_SPORTS_FALLBACK") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")

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
        # Late import to avoid cycles.
        from .api_sports import NATIONAL_TEAM_LEAGUES, is_national_team_league
        before = len(upcoming_raw)
        kept: list[dict] = []
        tier_counts = {"tier_1": 0, "tier_2": 0, "tier_3": 0, "national_team": 0}
        removed_leagues: dict[str, int] = {}
        for f in upcoming_raw:
            league_obj = _fx_league(sport, f)
            league_name = (league_obj.get("name") or "").strip()
            league_id_raw = league_obj.get("id")
            meta = fc.get_competition_meta(league_name)
            # 1) Standard tier allowlist for club competitions.
            if meta and meta["tier"] in fc.ALLOWED_TIERS:
                tier_counts[meta["tier"]] = tier_counts.get(meta["tier"], 0) + 1
                f["_competition_meta"] = meta
                kept.append(f)
                continue
            # 2) National-team leagues that the alias matcher missed
            #    (e.g. "World Cup - Qualification Europe", "International
            #    Friendlies", "UEFA Nations League") get a synthetic
            #    Tier-2 meta so the new "Selecciones Nacionales" button
            #    has something to analyze. They sit BELOW Big-Five but
            #    ABOVE Tier-3 cups for priority sorting.
            if is_national_team_league(league_id_raw):
                synthetic_meta = {
                    "tier":           "tier_2",
                    "priority":       72,   # just below real Tier-2 (70-100)
                    "canonical_name": league_name or "National Team Competition",
                    "type":           "international",
                    "region":         league_obj.get("country") or "World",
                    "_synthetic_national_team": True,
                }
                tier_counts["national_team"] = tier_counts.get("national_team", 0) + 1
                f["_competition_meta"] = synthetic_meta
                kept.append(f)
                continue
            removed_leagues[league_name or "?"] = removed_leagues.get(league_name or "?", 0) + 1
        log.info(
            "Scraper fetched %d football events. Allowed competition filter kept %d matches. "
            "Removed %d matches from non-priority leagues.",
            before, len(kept), before - len(kept),
        )
        log.info(
            "Tier 1: %d  Tier 2: %d  Tier 3: %d  National-team: %d  (allowed_tiers=%s)",
            tier_counts["tier_1"], tier_counts["tier_2"], tier_counts["tier_3"],
            tier_counts.get("national_team", 0),
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
    # Phase F74-post v2 — odds coverage telemetry. Surfaces regressions
    # in odds availability: if "no_odds" / "api_sports_empty" grow day
    # by day, there's a provider gap to investigate. States are
    # mutually exclusive per fixture.
    if sport == "football" and enriched:
        odds_coverage = {
            "api_sports":           sum(1 for m in enriched if m.get("_odds_source") == "api_sports"),
            "thestatsapi_fallback": sum(1 for m in enriched if m.get("_odds_source") == "thestatsapi_fallback"),
            "api_sports_empty":     sum(1 for m in enriched if m.get("_odds_source") == "api_sports_empty"),
            "no_odds":              sum(1 for m in enriched if m.get("_odds_source") == "no_odds"),
            "total":                len(enriched),
        }
        log.info("[odds_coverage] %s", odds_coverage)
    return enriched


async def ingest_live(client: httpx.AsyncClient, db, sport: str = "football", max_total: int = 20) -> list[dict]:
    sport = (sport or "football").lower()
    # ── Sweep stale rows BEFORE we fetch new live ones ──
    # Why before: if the API-Sports live feed has dropped a match (because
    # it just ended), we still have an `is_live=True` ghost row in Mongo.
    # The sweeper flips those to `is_live=False` so the next /matches/live
    # query doesn't surface zombies.
    try:
        from . import live_lifecycle as _ll
        flipped = await _ll.sweep_expired_live(db, sport=sport)
        if flipped:
            log.info("ingest_live: pre-sweep flipped %d stale rows (sport=%s)", flipped, sport)
    except Exception as exc:
        log.warning("ingest_live: pre-sweep failed: %s", exc)

    try:
        if sport == "football":
            # MLB-TS1: Use the football aggregator which transparently merges
            # API-Sports + TheStatsAPI (national teams / internacionales).
            # Fail-soft: if TheStatsAPI is disabled or fails, behaves like
            # the legacy `af.fixtures_live(client)` call.
            try:
                from .football_live_aggregator import fetch_live_football_fixtures
                live_raw, _agg_meta = await fetch_live_football_fixtures(client, db)
                log.info("[ingest_live] aggregator meta: %s", _agg_meta)
            except Exception as exc:
                log.warning("[ingest_live] aggregator failed, falling back to API-Sports: %s", exc)
                live_raw = await af.fixtures_live(client)
        else:
            live_raw = await aps.fixtures_live(sport, client)
    except Exception as exc:
        log.error("API[%s] live failed: %s", sport, exc)
        return []

    if sport == "football":
        # Late import to avoid cycles.
        from .api_sports import NATIONAL_TEAM_LEAGUES, is_national_team_league
        from .external_sources import national_team_detector as ntd
        before = len(live_raw)
        kept: list[dict] = []
        nt_kept = 0
        ts_nt_kept = 0
        for f in live_raw:
            league_obj = _fx_league(sport, f)
            league_name = (league_obj.get("name") or "").strip()
            league_country = league_obj.get("country") or ""
            league_id_raw = league_obj.get("id")
            meta = fc.get_competition_meta(league_name)
            # 1) Standard club-tier allowlist
            if meta and meta["tier"] in fc.ALLOWED_TIERS:
                f["_competition_meta"] = meta
                kept.append(f)
                continue
            # 2) National-team leagues by API-Sports league_id (World Cup,
            #    Euros, Nations League, Copa America, Gold Cup, AFCON,
            #    Asian Cup, WC Qualifying, International Friendlies).
            #    Mirror of the patch in ingest_upcoming: synthetic Tier-2
            #    priority 72 so these live fixtures actually reach the
            #    frontend.
            if is_national_team_league(league_id_raw):
                f["_competition_meta"] = {
                    "tier":           "tier_2",
                    "priority":       72,
                    "canonical_name": league_name or "National Team Competition",
                    "type":           "international",
                    "region":         league_country or "World",
                    "_synthetic_national_team": True,
                }
                kept.append(f)
                nt_kept += 1
                continue
            # 3) MLB-TS1 / Batch 2 — TheStatsAPI national-team detection.
            #    The fixture may not have a league_id in our known set
            #    (TheStatsAPI uses different IDs), or the fixture came
            #    via TheStatsAPI exclusively. Use the language-aware
            #    detector to grant the same synthetic Tier-2 slot.
            home_name = ((f.get("teams") or {}).get("home") or {}).get("name")
            away_name = ((f.get("teams") or {}).get("away") or {}).get("name")
            is_nt = (
                bool(f.get("_is_national_team"))
                or ntd.is_national_team_match(
                    home_name=home_name,
                    away_name=away_name,
                    league_name=league_name,
                    league_country=league_country,
                )
            )
            if is_nt:
                f["_competition_meta"] = {
                    "tier":           "tier_2",
                    "priority":       72,
                    "canonical_name": league_name or "National Team Competition",
                    "type":           "international",
                    "region":         league_country or "World",
                    "_synthetic_national_team": True,
                    "_detector_source": "national_team_detector",
                }
                f.setdefault("_is_national_team", True)
                kept.append(f)
                ts_nt_kept += 1
        kept.sort(key=lambda f: -((f.get("_competition_meta") or {}).get("priority", 0)))
        selected = kept[:max_total]
        log.info(
            "Live scraper: %d events -> %d kept after tier filter (incl. %d API-Sports nat-team + %d TheStatsAPI/detector nat-team; allowed_tiers=%s)",
            before, len(selected), nt_kept, ts_nt_kept, sorted(fc.ALLOWED_TIERS),
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

        # ── Odds fetch — API-Sports primario, TheStatsAPI fallback ───
        # Phase F74-post v2 — cuando API-Sports devuelve [] o estructura
        # inútil (sin bookmakers), intentar rescatar con TheStatsAPI.
        # Mantiene API-Sports como autoridad cuando funciona.
        league_name = (fx_raw.get("league") or {}).get("name")
        odds_source = "no_odds"

        try:
            odds_resp = await af.odds_for_fixture(client, fid, db=db)
        except Exception as e:
            log.warning("odds failed for %s: %s", fid, e)
            odds_resp = []

        # Normalizamos primero (criterio: "available" indica si tiene
        # bookmakers utilizables).
        norm_odds = nz.normalize_odds(odds_resp)
        api_sports_ok = bool(norm_odds.get("available"))

        if api_sports_ok:
            odds_source = "api_sports"
        else:
            odds_source = "api_sports_empty"
            # Fallback — TheStatsAPI.
            try:
                ts_raw_id = fx_raw.get("_thestatsapi_raw_id") or (
                    fx_raw.get("_external_source_id")
                    if fx_raw.get("_external_source") == "thestatsapi"
                    else None
                )
                from .external_sources import thestatsapi_client as _ts_client
                if not ts_raw_id:
                    ts_raw_id = await _ts_client.resolve_thestatsapi_match_id_by_names(
                        client, home=home["name"], away=away["name"],
                        date=kickoff, competition=league_name,
                    )
                if ts_raw_id:
                    from .external_sources import thestatsapi_normalizer as _ts_norm
                    ts_odds_data = await _ts_client.odds_for_fixture(client, ts_raw_id)
                    if ts_odds_data:
                        ts_apisports_shape = _ts_norm.normalize_thestatsapi_odds_to_apisports_shape(
                            ts_odds_data,
                        )
                        if ts_apisports_shape:
                            ts_norm_odds = nz.normalize_odds(ts_apisports_shape)
                            if ts_norm_odds.get("available"):
                                # Preserve opening odds so odds_value_engine
                                # can compute line movement from day one.
                                ts_norm_odds["_opening_odds"] = (
                                    ts_apisports_shape[0].get("_opening_odds") or {}
                                )
                                odds_resp = ts_apisports_shape
                                norm_odds = ts_norm_odds
                                odds_source = "thestatsapi_fallback"
                                log.info(
                                    "[odds_fallback] fixture=%s rescued by TheStatsAPI "
                                    "(API-Sports empty). bookmakers=%d",
                                    fid, len(norm_odds.get("bookmakers") or []),
                                )
            except Exception as exc_ts:
                log.debug("thestatsapi odds fallback failed for %s: %s", fid, exc_ts)

            if not norm_odds.get("available"):
                odds_source = "no_odds"

        # Stamp source en el snapshot para auditoría downstream.
        if isinstance(norm_odds, dict):
            norm_odds["_odds_source"] = odds_source
        try:
            stand_resp = await af.standings(client, lid, db=db)
        except Exception as e:
            log.warning("standings failed for league %s: %s", lid, e)
            stand_resp = []

        stats_h, stats_a, h2h, inj_h, inj_a = {}, {}, [], [], []
        stats_h_source = stats_a_source = "missing"  # F84.a — audit
        recent_h_raw, recent_a_raw = [], []
        if deep:
            # F84.a — Inversión de prioridad para team_statistics:
            # 1) TheStatsAPI primaria (shape compatible con API-Sports gracias
            #    al adapter `thestatsapi_team_stats_adapter`).
            # 2) API-Sports fallback detrás del flag
            #    `ENABLE_API_SPORTS_FALLBACK` (default true).
            # Tanto el éxito como el fallback quedan registrados en
            # ``stats_*_source`` para el bloque _provenance del match_doc.
            _ts_home_team_id = (fx_raw.get("teams", {}).get("home") or {}).get("_thestatsapi_id")
            _ts_away_team_id = (fx_raw.get("teams", {}).get("away") or {}).get("_thestatsapi_id")
            _ts_competition  = (fx_raw.get("league") or {}).get("_thestatsapi_id")
            try:
                stats_h = await _ts_team_stats.fetch_team_season_stats(
                    client,
                    team_id_thestatsapi=_ts_home_team_id,
                    season=season,
                    competition_id=_ts_competition,
                    team_id_internal=home.get("id"),
                )
                if stats_h:
                    stats_h_source = "thestatsapi"
            except Exception as exc:
                log.debug("[F84.a] thestatsapi team_stats home failed: %s", exc)
                stats_h = {}
            if not stats_h and _api_sports_fallback_enabled():
                try:
                    stats_h = await af.team_statistics(client, home["id"], lid, db=db)
                    if stats_h:
                        stats_h_source = "api_sports_fallback"
                except Exception:
                    stats_h = {}
            try:
                stats_a = await _ts_team_stats.fetch_team_season_stats(
                    client,
                    team_id_thestatsapi=_ts_away_team_id,
                    season=season,
                    competition_id=_ts_competition,
                    team_id_internal=away.get("id"),
                )
                if stats_a:
                    stats_a_source = "thestatsapi"
            except Exception as exc:
                log.debug("[F84.a] thestatsapi team_stats away failed: %s", exc)
                stats_a = {}
            if not stats_a and _api_sports_fallback_enabled():
                try:
                    stats_a = await af.team_statistics(client, away["id"], lid, db=db)
                    if stats_a:
                        stats_a_source = "api_sports_fallback"
                except Exception:
                    stats_a = {}
            # F84.b — Inversión de prioridad para head_to_head:
            # 1) TheStatsAPI primaria (lista de matches del home team filtrada
            #    localmente por opponent → shape API-Sports v3 compatible).
            # 2) API-Sports fallback detrás del flag ENABLE_API_SPORTS_FALLBACK.
            # El resultado se almacena en ``h2h`` y la fuente en ``h2h_source``
            # para auditoría en ``_provenance_h2h``.
            h2h_source = "missing"
            try:
                h2h = await _ts_h2h.fetch_head_to_head(
                    client,
                    home_team_id_thestatsapi=_ts_home_team_id,
                    away_team_id_thestatsapi=_ts_away_team_id,
                    limit=5,
                    db=db,
                    home_team_id_internal=home.get("id"),
                    away_team_id_internal=away.get("id"),
                )
                if h2h:
                    h2h_source = "thestatsapi"
            except Exception as exc:
                log.debug("[F84.b] thestatsapi h2h failed: %s", exc)
                h2h = []
            if not h2h and _api_sports_fallback_enabled():
                try:
                    h2h = await af.head_to_head(
                        client, home["id"], away["id"], limit=5, db=db,
                    )
                    if h2h:
                        h2h_source = "api_sports_fallback"
                except Exception:
                    h2h = []
            try: inj_h = await af.injuries(client, home["id"], db=db)
            except Exception: pass
            try: inj_a = await af.injuries(client, away["id"], db=db)
            except Exception: pass
            # P2A — pull last-15 fixtures per team for the historical goal
            # profile (under_3_5_rate, team_exceeded_2_goals_rate, etc.).
            # Cached 12h per (team, season). 15 games gives a robust sample
            # for under-tendency detection (case Atlético-MG style).
            try: recent_h_raw = await af.fixtures_last_n(client, home["id"], n=15, season=season, db=db)
            except Exception: pass
            try: recent_a_raw = await af.fixtures_last_n(client, away["id"], n=15, season=season, db=db)
            except Exception: pass

        # NOTE: ``norm_odds`` was computed earlier (with TheStatsAPI fallback
        # baked in). Do NOT recompute here — that would discard the fallback.
        ctx_home = nz.normalize_team_context(stats_h, stand_resp, inj_h, home["id"])
        ctx_away = nz.normalize_team_context(stats_a, stand_resp, inj_a, away["id"])
        # Attach last-15 goal distributions used by statsbomb_features and the
        # historicalGoalProfile feeder for the Protected Market Rescue Layer.
        if recent_h_raw:
            ctx_home["recent_fixtures"] = nz.normalize_recent_fixtures(recent_h_raw, home["id"], n=15)
        if recent_a_raw:
            ctx_away["recent_fixtures"] = nz.normalize_recent_fixtures(recent_a_raw, away["id"], n=15)
        live_stats = nz.normalize_live_stats(fx_raw) if is_live else None
        # When live, the /fixtures?live=all payload from API-Sports often
        # omits the per-team statistics array on the free tier — meaning
        # home_stats/away_stats end up empty and our xG/threat/pressure
        # engine has nothing to chew on. Fetch the dedicated endpoint and
        # merge so live_xg_proxy can produce real numbers.
        if is_live and live_stats and not (live_stats.get("home_stats") or live_stats.get("away_stats")):
            try:
                fx_stats = await af.fixture_statistics(client, fid)
                if fx_stats:
                    # Re-normalize by injecting the stats array back into fx_raw.
                    fx_raw_copy = dict(fx_raw)
                    fx_raw_copy["statistics"] = fx_stats
                    rehydrated = nz.normalize_live_stats(fx_raw_copy)
                    if rehydrated and (rehydrated.get("home_stats") or rehydrated.get("away_stats")):
                        live_stats = rehydrated
            except Exception as exc:
                log.warning("fixture_statistics fetch failed for %s: %s", fid, exc)

        # MLB-TS1 Batch 2 — TheStatsAPI stats enrichment for national-team /
        # international fixtures. Trigger when:
        #   * the fixture has a TheStatsAPI raw id attached
        #     (`_thestatsapi_raw_id` set by the aggregator when both
        #     providers covered the same fixture, OR `_external_source_id`
        #     if the fixture came from TheStatsAPI exclusively), AND
        #   * we're live, AND
        #   * the API-Sports stats came back empty (no home_stats AND no
        #     away_stats) OR the fixture is TheStatsAPI-only.
        # This is the "Bélgica vs Croacia" case: API-Sports has the fixture
        # but the free tier doesn't ship live stats for national-team games.
        ts_raw_id = fx_raw.get("_thestatsapi_raw_id") or (
            fx_raw.get("_external_source_id")
            if fx_raw.get("_external_source") == "thestatsapi"
            else None
        )
        ts_covered = fx_raw.get("_external_sources_covered") or []
        ts_should_enrich = bool(ts_raw_id) and is_live and (
            (fx_raw.get("_external_source") == "thestatsapi")
            or "thestatsapi" in ts_covered
            or (live_stats is None)
            or (not (live_stats.get("home_stats") or live_stats.get("away_stats")) if live_stats else True)
        )
        if ts_should_enrich:
            try:
                from .external_sources import thestatsapi_client as _ts_client
                from .external_sources import thestatsapi_normalizer as _ts_norm
                if _ts_client.is_enabled():
                    ts_stats_raw = await _ts_client.fetch_match_stats(client, ts_raw_id)
                    if ts_stats_raw:
                        ts_live = _ts_norm.normalize_match_stats(
                            ts_stats_raw,
                            fallback_status=(fx_raw.get("fixture", {}).get("status", {}) or {}).get("short"),
                        )
                        if ts_live:
                            # Merge into existing live_stats (API-Sports payload
                            # wins on non-empty values; TheStatsAPI fills the
                            # gaps for xG / shots / possession).
                            live_stats = _ts_norm.merge_live_stats(live_stats, ts_live)
                            log.info(
                                "[thestatsapi_stats] enriched fixture %s with xG/shots from TheStatsAPI",
                                fid,
                            )
            except Exception as exc:
                log.warning("[thestatsapi_stats] enrichment failed for %s: %s", fid, exc)

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
            "_odds_source":   odds_source,
            "odds_source":    odds_source,   # alias sin prefix para la UI
            "live_stats": live_stats,
            "h2h_recent": h2h_clean,
            "data_complete": norm_odds.get("available") and bool(ctx_home.get("position") or ctx_home.get("form_last_5")),
            "fallback_used": False,
            "updated_at": nz.now_iso(),
        }
        # MLB-TS1: propagate TheStatsAPI provenance + national-team flag onto
        # the match_doc so the frontend can surface badges ("TheStatsAPI" /
        # "Selecciones") next to the match card.
        _ext_src = fx_raw.get("_external_source") or "api_sports"
        _ext_covered = fx_raw.get("_external_sources_covered") or [_ext_src]
        match_doc["external_source"] = _ext_src
        match_doc["external_sources_covered"] = sorted(set(_ext_covered))
        # Phase F74-post v2 — if odds were rescued by TheStatsAPI, mark
        # provenance so the UI can show the badge.
        if odds_source == "thestatsapi_fallback":
            match_doc["external_sources_covered"] = sorted(
                set(match_doc["external_sources_covered"] + ["thestatsapi"])
            )
        # Phase F82 — rich H2H context (renders concrete results, not just count).
        try:
            from .football_h2h_context_builder import build_h2h_context
            match_doc["h2h_context"] = build_h2h_context(match_doc)
            _h2h_ctx = match_doc["h2h_context"]
            if _h2h_ctx.get("available"):
                log.info(
                    "[h2h_context] fixture=%s sample=%d avg_goals=%s under35=%s btts=%s",
                    fid, _h2h_ctx.get("sample_size"),
                    (_h2h_ctx.get("summary") or {}).get("avg_goals"),
                    (_h2h_ctx.get("summary") or {}).get("under_3_5_rate"),
                    (_h2h_ctx.get("summary") or {}).get("btts_rate"),
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("h2h_context build failed for %s: %s", fid, exc)
        # Phase F82 — corners provider (API-Sports → 365Scores → TheStatsAPI).
        # Phase F82.1 — FAST tier only (no HTTP); 365Scores opt-in via env flag.
        try:
            from .football_corners_provider import enrich_match_corners_fast
            await enrich_match_corners_fast(client, db, match_doc)
        except Exception as exc:  # noqa: BLE001
            log.debug("corners_provider failed for %s: %s", fid, exc)
        if fx_raw.get("_is_national_team"):
            match_doc["is_national_team"] = True
        if fx_raw.get("_is_international"):
            match_doc["is_international"] = True

        # MLB-TS1 Batch 3 — Pre-match enrichment via TheStatsAPI.
        # When the fixture has a TheStatsAPI raw id (either originated
        # from TheStatsAPI or matched in the dedupe step) AND we're
        # NOT live (live stats already enriched separately), pull
        # season-level team stats + match details. The block is fully
        # additive — if enrichment returns {}, no field is written.
        ts_raw_id = fx_raw.get("_thestatsapi_raw_id") or (
            fx_raw.get("_external_source_id")
            if fx_raw.get("_external_source") == "thestatsapi"
            else None
        )
        if ts_raw_id and not is_live:
            try:
                from .external_sources import thestatsapi_enrichment as _ts_enrich
                _ts_home_raw = (fx_raw.get("teams", {}).get("home") or {}).get("_thestatsapi_id")
                _ts_away_raw = (fx_raw.get("teams", {}).get("away") or {}).get("_thestatsapi_id")
                ts_payload = await _ts_enrich.enrich_pre_match(
                    client, db,
                    sport="football",
                    match_raw_id=ts_raw_id,
                    home_team_id=_ts_home_raw,
                    away_team_id=_ts_away_raw,
                    season=season,
                    competition_id=fx_raw.get("league", {}).get("_thestatsapi_id"),
                )
                if ts_payload:
                    match_doc["_thestatsapi_enrichment"] = ts_payload
                    log.info(
                        "[ts_enrichment] football fixture %s enriched with TheStatsAPI (%s)",
                        fid, list(ts_payload.keys()),
                    )
            except Exception as exc:
                log.warning("[ts_enrichment] football fixture %s enrichment failed: %s", fid, exc)
        # Phase P2 — provenance: API-Sports is authoritative for the football
        # path; every section here was fetched from the same provider.
        # F84.a — Stamp team_stats audit so the editorial layer can show
        # which source served each side (thestatsapi vs api_sports_fallback
        # vs missing).
        match_doc.setdefault("_provenance_team_stats", {
            "home": stats_h_source,
            "away": stats_a_source,
        })
        # F84.b — Stamp h2h audit (same semantics).
        match_doc.setdefault("_provenance_h2h", {"source": h2h_source})
        prov.attach_to_match(
            match_doc,
            primary_source=_ext_src,
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

        # MLB-TS1 Batch 3.5 — Pre-match enrichment via TheStatsAPI also
        # for basketball + baseball. Symmetrical with the football path
        # in `_enrich_football`. Only triggers when:
        #   * the integration is enabled (env flag + key)
        #   * the fixture has a TheStatsAPI raw id (rare on these sports
        #     for now — typically only when API-Sports failed and we
        #     fell back to a TheStatsAPI-only fixture), OR
        #   * the fixture is pre-game (`is_live=False`) AND we have
        #     team ids that look TheStatsAPI-shaped.
        # Fully additive — failure is logged and discarded.
        try:
            from .external_sources import thestatsapi_client as _ts_client
            from .external_sources import thestatsapi_enrichment as _ts_enrich
            if _ts_client.is_enabled() and not is_live:
                ts_raw_id = fx_raw.get("_thestatsapi_raw_id") or (
                    fx_raw.get("_external_source_id")
                    if fx_raw.get("_external_source") == "thestatsapi"
                    else None
                )
                # For sports where we don't yet have per-fixture mapping
                # we still attempt the team_stats fetches (the enrichment
                # helper safely skips any branch with a missing id).
                _ts_home_id = (fx_raw.get("teams", {}).get("home") or {}).get("_thestatsapi_id")
                _ts_away_id = (fx_raw.get("teams", {}).get("away") or {}).get("_thestatsapi_id")
                if ts_raw_id or _ts_home_id or _ts_away_id:
                    ts_payload = await _ts_enrich.enrich_pre_match(
                        client, db,
                        sport=sport,
                        match_raw_id=ts_raw_id,
                        home_team_id=_ts_home_id,
                        away_team_id=_ts_away_id,
                        season=season,
                        competition_id=fx_raw.get("league", {}).get("_thestatsapi_id"),
                    )
                    if ts_payload:
                        match_doc["_thestatsapi_enrichment"] = ts_payload
                        log.info(
                            "[ts_enrichment] %s fixture %s enriched with TheStatsAPI (%s)",
                            sport, fid, list(ts_payload.keys()),
                        )
        except Exception as exc:
            log.warning("[ts_enrichment] %s fixture %s enrichment failed: %s", sport, fid, exc)

        await db.matches.update_one({"match_id": fid}, {"$set": match_doc}, upsert=True)
        if norm_odds.get("available"):
            await db.odds_snapshots.insert_one({"match_id": fid, "sport": sport, **norm_odds})
        return match_doc
    except Exception as exc:
        log.exception("enrich_generic[%s] failed: %s", sport, exc)
        return None


# ────────────────────────────────────────────────────────────────────────────
# MLB Stats API direct fallback
# ────────────────────────────────────────────────────────────────────────────
# When API-Sports returns 0 baseball games for the day (a recurring symptom
# when the user's API-Sports plan doesn't include MLB or the league is
# misconfigured), we go straight to the official, free MLB Stats API to
# ingest the schedule + probable pitchers. The output is normalized into
# the same `db.matches` shape used by API-Sports so the rest of the
# pipeline (analyst_engine, time_filter, baseball_historical, etc.)
# doesn't have to know about the fallback.

MLB_STATSAPI_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def normalize_mlb_stats_game(raw_game: dict) -> Optional[dict]:
    """Convert a MLB Stats API `dates[].games[]` entry into the internal
    `db.matches` shape.

    Returns ``None`` when the payload is too malformed to be useful.
    The output deliberately mirrors the shape produced by the API-Sports
    baseball normalizer so downstream code (analyst_engine, time_filter,
    baseball_historical) doesn't need a branch.
    """
    if not isinstance(raw_game, dict):
        return None
    game_pk = raw_game.get("gamePk")
    if not game_pk:
        return None

    teams = raw_game.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_team = (home.get("team") or {})
    away_team = (away.get("team") or {})
    if not home_team.get("name") or not away_team.get("name"):
        return None

    status_obj = raw_game.get("status") or {}
    detailed_state    = status_obj.get("detailedState")
    abstract_state    = status_obj.get("abstractGameState")
    is_live           = (abstract_state or "").lower() == "live"
    game_date         = raw_game.get("gameDate") or ""
    venue_name        = ((raw_game.get("venue") or {}).get("name"))

    # Compute kickoff_ts (UNIX) so the existing `kickoff_ts >= now_ts` filter
    # in _run_analysis_pipeline accepts the doc.
    kickoff_ts: Optional[int] = None
    try:
        ts = datetime.fromisoformat((game_date or "").replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        kickoff_ts = int(ts.timestamp())
    except Exception:
        kickoff_ts = None

    home_prob = home.get("probablePitcher") or {}
    away_prob = away.get("probablePitcher") or {}

    doc: dict[str, Any] = {
        # Mandatory identifiers
        "match_id":            str(game_pk),
        "sport":               "baseball",
        "source":              "mlb_stats_api",
        # League info (every MLB game belongs to League ID 1)
        "league":              {"id": 1, "name": "MLB"},
        "league_id":           1,
        "season":              ts.year if kickoff_ts else None,
        # Schedule
        "kickoff_iso":         game_date,
        "kickoff_ts":          kickoff_ts,
        "gameDate":            game_date,    # GAP #0 — keep both fields
        "status":              detailed_state,
        "abstractGameState":   abstract_state,
        "is_live":             is_live,
        "venue":               venue_name,
        # Teams (mirror api_sports baseball normalizer shape)
        "home_team": {
            "id":    home_team.get("id"),
            "name":  home_team.get("name"),
            "context": {"fetched_at": None, "form_last_5": "", "position": None},
        },
        "away_team": {
            "id":    away_team.get("id"),
            "name":  away_team.get("name"),
            "context": {"fetched_at": None, "form_last_5": "", "position": None},
        },
        # Probable pitchers (top-level + nested mirror for compatibility)
        "home_probable_id":    home_prob.get("id"),
        "home_probable_name":  home_prob.get("fullName"),
        "away_probable_id":    away_prob.get("id"),
        "away_probable_name":  away_prob.get("fullName"),
        "home_probable":       {"id": home_prob.get("id"), "name": home_prob.get("fullName")},
        "away_probable":       {"id": away_prob.get("id"), "name": away_prob.get("fullName")},
        # No odds available from MLB Stats API — the user can still see signals/picks
        # but markets without book lines fall back to the engine's own projections.
        "odds_snapshots":      [],
        "live_stats":          None,
        "h2h_recent":          [],
        "data_complete":       False,
        "fallback_used":       True,
        "updated_at":          nz.now_iso(),
    }
    # P2 — provenance
    prov.attach_to_match(
        doc,
        primary_source="mlb_stats_api",
        odds_available=False,
        stats_available=True,
        h2h_available=False,
        lineups_available=bool(home_prob.get("id") and away_prob.get("id")),
        context_available=False,
        live_available=is_live,
    )
    return doc


async def ingest_mlb_direct_fallback(
    db,
    date_str: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict]:
    """Direct ingest from MLB Stats API (no key, no plan) when API-Sports
    returns 0 baseball games for the requested date.

    Always upserts to `db.matches` so the rest of `_run_analysis_pipeline`
    picks them up via the standard candidate query.

    Returns the list of normalized + persisted match docs (may be empty
    if the API itself returned no games).
    """
    if not date_str:
        return []

    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "probablePitcher,team,linescore,venue",
    }
    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        own_client = True

    try:
        try:
            r = await client.get(MLB_STATSAPI_SCHEDULE_URL, params=params)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.warning("MLB Stats API fallback fetch failed for %s: %s", date_str, exc)
            return []
    finally:
        if own_client:
            await client.aclose()

    raw_games: list[dict] = []
    for date_obj in (data.get("dates") or []):
        raw_games.extend(date_obj.get("games") or [])

    log.info("MLB Stats API fallback: %d raw games for %s", len(raw_games), date_str)

    persisted: list[dict] = []
    for rg in raw_games:
        doc = normalize_mlb_stats_game(rg)
        if not doc:
            continue
        try:
            await db.matches.update_one(
                {"match_id": doc["match_id"]},
                {"$set": doc},
                upsert=True,
            )
            persisted.append(doc)
        except Exception as exc:
            log.warning("MLB Stats API fallback upsert failed for %s: %s",
                        doc.get("match_id"), exc)

    log.info(
        "MLB Stats API fallback persisted %d/%d games to db.matches (date=%s)",
        len(persisted), len(raw_games), date_str,
    )
    return persisted



# ────────────────────────────────────────────────────────────────────────────
# ESPN NBA Scoreboard direct fallback (basketball)
# ────────────────────────────────────────────────────────────────────────────
# The user's API-Sports plan doesn't include basketball, so when the
# basketball ingest returns 0 we fall back to ESPN's public JSON API.
# It exposes today's NBA scoreboard + team meta + scheduled tip times
# without any API key. We normalise to the standard `db.matches` shape
# so the rest of the pipeline doesn't need a basketball-specific branch.

ESPN_NBA_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)


def normalize_espn_nba_game(raw_event: dict) -> Optional[dict]:
    """Convert one ESPN scoreboard `events[]` entry to the internal
    `db.matches` shape (basketball)."""
    if not isinstance(raw_event, dict):
        return None
    event_id = raw_event.get("id")
    if not event_id:
        return None

    competition = (raw_event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    if len(competitors) < 2:
        return None
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not (home and away):
        return None

    home_team_obj = home.get("team") or {}
    away_team_obj = away.get("team") or {}
    if not home_team_obj.get("displayName") or not away_team_obj.get("displayName"):
        return None

    status_obj   = (competition.get("status") or {}).get("type") or {}
    state        = (status_obj.get("state") or "").lower()    # 'pre'|'in'|'post'
    detailed     = status_obj.get("description") or status_obj.get("shortDetail") or "Scheduled"
    is_live      = (state == "in")
    is_finished  = (state == "post")
    game_date    = raw_event.get("date") or competition.get("date") or ""

    kickoff_ts: Optional[int] = None
    try:
        ts = datetime.fromisoformat((game_date or "").replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        kickoff_ts = int(ts.timestamp())
    except Exception:
        kickoff_ts = None

    league_obj = (raw_event.get("league") or {})
    league_name = league_obj.get("name") or "NBA"
    season_year = (raw_event.get("season") or {}).get("year") or (
        ts.year if kickoff_ts else None
    )

    doc: dict[str, Any] = {
        "match_id":          str(event_id),
        "sport":             "basketball",
        "source":            "espn_nba",
        "league":            {"id": league_obj.get("id") or 0, "name": league_name},
        "league_id":         league_obj.get("id") or 0,
        "season":            season_year,
        "kickoff_iso":       game_date,
        "kickoff_ts":        kickoff_ts,
        "gameDate":          game_date,
        "status":            detailed,
        "abstractGameState": state,
        "is_live":           is_live,
        "venue":             ((competition.get("venue") or {}).get("fullName")),
        "home_team": {
            "id":      home_team_obj.get("id"),
            "name":    home_team_obj.get("displayName"),
            "abbreviation": home_team_obj.get("abbreviation"),
            "context": {"fetched_at": None, "form_last_5": "", "position": None},
        },
        "away_team": {
            "id":      away_team_obj.get("id"),
            "name":    away_team_obj.get("displayName"),
            "abbreviation": away_team_obj.get("abbreviation"),
            "context": {"fetched_at": None, "form_last_5": "", "position": None},
        },
        "odds_snapshots": [],
        "live_stats":     None,
        "h2h_recent":     [],
        "data_complete":  False,
        "fallback_used":  True,
        "updated_at":     nz.now_iso(),
        "_espn_odds":     competition.get("odds") or [],
        "_espn_event_id": event_id,
    }
    prov.attach_to_match(
        doc,
        primary_source="espn_nba",
        odds_available=bool(competition.get("odds")),
        stats_available=False,
        h2h_available=False,
        lineups_available=False,
        context_available=False,
        live_available=is_live,
    )
    if is_finished and not is_live:
        return None
    return doc


async def ingest_nba_direct_fallback(
    db,
    date_str: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict]:
    """Direct ingest from ESPN's free NBA scoreboard API when API-Sports
    returns 0 basketball games for the requested date.

    `date_str` may be either YYYY-MM-DD or YYYYMMDD. Returns the list of
    normalised+persisted match docs (empty on any failure).
    """
    if not date_str:
        return []
    compact = date_str.replace("-", "")

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        own_client = True

    try:
        try:
            r = await client.get(ESPN_NBA_SCOREBOARD_URL, params={"dates": compact})
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.warning("ESPN NBA fallback fetch failed for %s: %s", date_str, exc)
            return []
    finally:
        if own_client:
            await client.aclose()

    events = data.get("events") or []
    log.info("ESPN NBA fallback: %d raw events for %s", len(events), date_str)

    persisted: list[dict] = []
    for ev in events:
        doc = normalize_espn_nba_game(ev)
        if not doc:
            continue
        try:
            await db.matches.update_one(
                {"match_id": doc["match_id"]},
                {"$set": doc},
                upsert=True,
            )
            persisted.append(doc)
        except Exception as exc:
            log.warning("ESPN NBA fallback upsert failed for %s: %s",
                        doc.get("match_id"), exc)

    log.info(
        "ESPN NBA fallback persisted %d/%d games to db.matches (date=%s)",
        len(persisted), len(events), date_str,
    )
    return persisted



# ────────────────────────────────────────────────────────────────────────────
# SofaScore Basketball direct fallback (tertiary)
# ────────────────────────────────────────────────────────────────────────────
# When **both** api_sports AND ESPN NBA return 0 basketball games, fall
# back to SofaScore's public scheduled-events endpoint. Unlike ESPN it
# covers NBA + EuroLeague + LNB + national-team games, so we restrict
# what we persist to leagues whose name contains "NBA" — keeping the
# downstream LLM/odds pipeline focused on the markets the user actually
# trades.

def normalize_sofascore_basketball_game(raw_event: dict) -> Optional[dict]:
    """Convert one ``sofascore.fetch_matchups()`` entry to the internal
    ``db.matches`` shape (basketball)."""
    if not isinstance(raw_event, dict):
        return None
    home = raw_event.get("home_team")
    away = raw_event.get("away_team")
    league = raw_event.get("league") or ""
    sofa_id = raw_event.get("sofascore_id")
    if not (home and away and sofa_id):
        return None
    # Only persist NBA-style leagues to avoid bloating the analyst with
    # exotic basketball tournaments the platform doesn't model.
    if "nba" not in league.lower():
        return None

    kickoff_ts = raw_event.get("kickoff_ts")
    kickoff_iso = None
    if kickoff_ts:
        try:
            kickoff_iso = datetime.fromtimestamp(int(kickoff_ts), tz=timezone.utc).isoformat()
        except Exception:
            kickoff_iso = None

    status = (raw_event.get("status") or "").lower()
    is_live = status in ("inprogress", "live")
    is_finished = status in ("finished", "ended")

    doc: dict[str, Any] = {
        "match_id":          f"sofascore-{sofa_id}",
        "sport":             "basketball",
        "source":            "sofascore_basketball",
        "league":            {"id": 0, "name": league or "NBA"},
        "league_id":         0,
        "season":            None,
        "kickoff_iso":       kickoff_iso,
        "kickoff_ts":        int(kickoff_ts) if kickoff_ts else None,
        "gameDate":          kickoff_iso,
        "status":            status or "Scheduled",
        "abstractGameState": "in" if is_live else ("post" if is_finished else "pre"),
        "is_live":           is_live,
        "venue":             None,
        "home_team": {
            "id":      None,
            "name":    home,
            "context": {"fetched_at": None, "form_last_5": "", "position": None},
        },
        "away_team": {
            "id":      None,
            "name":    away,
            "context": {"fetched_at": None, "form_last_5": "", "position": None},
        },
        "odds_snapshots": [],
        "live_stats":     None,
        "h2h_recent":     [],
        "data_complete":  False,
        "fallback_used":  True,
        "updated_at":     nz.now_iso(),
        "_sofascore_id":  sofa_id,
    }
    prov.attach_to_match(
        doc,
        primary_source="sofascore_basketball",
        odds_available=False,
        stats_available=False,
        h2h_available=False,
        lineups_available=False,
        context_available=False,
        live_available=is_live,
    )
    if is_finished and not is_live:
        return None
    return doc


async def ingest_basketball_sofascore_fallback(
    db,
    date_str: str,
) -> list[dict]:
    """Direct ingest from SofaScore's public schedule endpoint when BOTH
    api_sports and ESPN NBA returned 0 basketball games for ``date_str``.

    Returns the list of persisted match docs (empty on any failure).
    """
    if not date_str:
        return []

    try:
        from .external_sources import sofascore_basketball as _sofa  # type: ignore
        bundle = await _sofa.fetch_matchups(date_str)
    except Exception as exc:
        log.warning("SofaScore basketball fallback fetch crashed: %s", exc)
        return []

    matchups = bundle.get("matchups") or {}
    log.info("SofaScore basketball fallback: %d raw matchups for %s",
             len(matchups), date_str)

    persisted: list[dict] = []
    for _key, ev in matchups.items():
        doc = normalize_sofascore_basketball_game(ev)
        if not doc:
            continue
        try:
            await db.matches.update_one(
                {"match_id": doc["match_id"]},
                {"$set": doc},
                upsert=True,
            )
            persisted.append(doc)
        except Exception as exc:
            log.warning("SofaScore basketball fallback upsert failed for %s: %s",
                        doc.get("match_id"), exc)

    log.info(
        "SofaScore basketball fallback persisted %d/%d games to db.matches (date=%s)",
        len(persisted), len(matchups), date_str,
    )
    return persisted
