"""Generic API-Sports client supporting football, basketball (NBA), baseball (MLB).

All three products share the same API key (same as football). Endpoints differ slightly.
We expose a small per-sport surface used by data_ingestion.
"""
from __future__ import annotations

import asyncio
import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger("api_sports")

API_KEY = os.environ.get("API_FOOTBALL_KEY", "")

SPORT_CONFIG = {
    "football": {
        "base": "https://v3.football.api-sports.io",
        "season": 2024,
        "top_leagues": {39, 140, 135, 78, 61, 2, 3, 848, 88, 94, 71, 128, 253, 262, 13, 11, 144, 218, 197, 119, 207, 113, 103, 179},
    },
    "basketball": {
        "base": "https://v1.basketball.api-sports.io",
        "season": "2024-2025",
        "top_leagues": {12, 120, 117, 117, 194, 110},  # NBA(12), EuroLeague(120), Liga ACB(110), etc.
    },
    "baseball": {
        "base": "https://v1.baseball.api-sports.io",
        "season": 2024,
        "top_leagues": {1, 2, 3, 5},  # MLB(1), NPB(2), KBO(5), CPBL(3)
    },
}

# ── National-team competitions (football only) ──────────────────────────────
# API-Sports football v3 league IDs para torneos exclusivamente de
# selecciones nacionales. Usado por:
#   - POST /api/analysis/run con national_teams_only=true
#   - Filtro server-side en _run_analysis_pipeline para descartar fixtures
#     de clubes incluso cuando los Big-Five están vacíos.
#
# IDs verificados contra https://www.api-football.com/documentation-v3#tag/Leagues
#   1   FIFA World Cup
#   4   Euro Championship (UEFA Euro)
#   5   UEFA Nations League
#   6   Africa Cup of Nations
#   7   AFC Asian Cup
#   9   Copa America
#  10   International Friendlies (amistosos internacionales de selecciones)
#  22   CONCACAF Gold Cup
#  32-37: Qualifying World Cup (Europe/South America/CONCACAF/Africa/Asia/Oceania)
NATIONAL_TEAM_LEAGUES: set[int] = {
    1,   # FIFA World Cup
    4,   # Euro Championship
    5,   # UEFA Nations League
    6,   # Africa Cup of Nations
    7,   # AFC Asian Cup
    9,   # Copa America
    10,  # International Friendlies (selecciones)
    22,  # CONCACAF Gold Cup
    32,  # World Cup - Qualification Europe
    33,  # World Cup - Qualification South America
    34,  # World Cup - Qualification CONCACAF
    36,  # World Cup - Qualification Africa
    37,  # World Cup - Qualification Asia/Oceania (intercontinental)
}


def is_national_team_league(league_id: Any) -> bool:
    """Return True si ``league_id`` corresponde a un torneo de selecciones
    nacionales (no clubes). Acepta int o string numérica.
    """
    try:
        return int(league_id) in NATIONAL_TEAM_LEAGUES
    except (TypeError, ValueError):
        return False


# Sprint-D9-HOTFIX (cascade-reorder) · matching por NOMBRE para fuentes
# que no usan los IDs canónicos de API-Football (TheSportsDB, ESPN,
# Sofascore, etc.). Cuando ``league_id`` es desconocido o no matchea con
# ``NATIONAL_TEAM_LEAGUES``, recurrimos a estos keywords para identificar
# torneos de selecciones nacionales.
_NATIONAL_TEAM_LEAGUE_NAME_KEYWORDS: tuple[str, ...] = (
    "fifa world cup",
    "world cup qualification",
    "world cup qualifier",
    "wc qualifier",
    "uefa nations league",
    "nations league",
    "euro championship",
    "european championship",
    "uefa euro",
    "copa america",
    "copa américa",
    "afc asian cup",
    "asian cup",
    "africa cup of nations",
    "afcon",
    "concacaf gold cup",
    "gold cup",
    "international friendlies",
    "international friendly",
    "club friendlies",       # incluye amistosos internacionales (FIFA dates)
    "world cup",             # genérico — captura "FIFA World Cup", "Women's World Cup", etc.
    "euro qualif",
    "afcon qualif",
)


def _strip_accents_lower(s: str) -> str:
    import unicodedata as _ud
    if not isinstance(s, str):
        return ""
    out = "".join(c for c in _ud.normalize("NFD", s)
                  if _ud.category(c) != "Mn")
    return out.lower().strip()


def is_national_team_league_by_name(league_name: Any) -> bool:
    """Heurística por nombre. Útil cuando la fuente (TheSportsDB, ESPN,
    Sofascore) NO usa el league_id canónico de API-Football y por lo
    tanto :func:`is_national_team_league` retorna ``False`` aunque el
    torneo SÍ sea de selecciones nacionales.

    Fail-soft: si ``league_name`` no es str o está vacío, retorna False.
    """
    if not isinstance(league_name, str) or not league_name.strip():
        return False
    norm = _strip_accents_lower(league_name)
    return any(kw in norm for kw in _NATIONAL_TEAM_LEAGUE_NAME_KEYWORDS)


def is_national_team_match(match_or_fixture: Any) -> bool:
    """Combina chequeo por ID (canónico) + matching por nombre (fallback)
    + flags pre-calculados (Sprint-D9-RootCauseFix).

    Acepta tanto:
      * Un dict con ``league_id`` o ``league.id`` (shape API-Football
        crudo) o ``league`` como string (shape db.matches).
      * Flags pre-calculados ``is_national_team`` / ``is_international``
        / ``competition_type=="international"`` (persistidos por
        ``data_ingestion._classify_competition``).

    Esta función es el GATEKEEPER del filtro ``national_teams_only`` y
    tiene que tolerar TODAS las representaciones que pasan por nuestro
    pipeline (API-Football raw, TheSportsDB raw, ESPN, Sofascore,
    documentos de ``db.matches``).
    """
    if not isinstance(match_or_fixture, dict):
        return False

    # ─── Fast-path 1: flags ya persistidos por la capa de ingest. ───
    # Cuando ``_classify_competition`` ya clasificó el partido como
    # selección nacional / torneo internacional, ese veredicto manda.
    if match_or_fixture.get("is_national_team") is True:
        return True
    if (match_or_fixture.get("competition_type") or "").lower() == "international":
        return True

    # ─── Path 2: chequeo por league_id canónico API-Football. ───
    league_field = match_or_fixture.get("league")
    league_obj = league_field if isinstance(league_field, dict) else {}
    league_id = (
        match_or_fixture.get("league_id")
        or league_obj.get("id")
    )
    if is_national_team_league(league_id):
        return True

    # ─── Path 3: matching por nombre — soporta TRES shapes: ───
    #   * league_name (string directo, p.ej. en db.matches)
    #   * league.name (dict, shape API-Football raw)
    #   * league (string directo, p.ej. en db.matches Sprint-D9 docs)
    #   * competition_canonical_name (post-_classify_competition)
    league_name = (
        match_or_fixture.get("league_name")
        or league_obj.get("name")
        or (league_field if isinstance(league_field, str) else None)
        or match_or_fixture.get("competition_canonical_name")
    )
    return is_national_team_league_by_name(league_name)

# Sentence-friendly labels
SPORT_LABELS = {"football": "Fútbol", "basketball": "NBA / Basket", "baseball": "MLB / Béisbol"}

# Rate limiter shared across all sports (same API key quota)
class _RateLimiter:
    def __init__(self, max_calls: int = 8, period_sec: int = 60):
        self.max_calls = max_calls
        self.period = period_sec
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if now - t < self.period]
            if len(self._calls) >= self.max_calls:
                wait = self.period - (now - self._calls[0]) + 0.05
                if wait > 0:
                    log.info("Rate limit: sleeping %.2fs", wait)
                    await asyncio.sleep(wait)
                    now = time.monotonic()
                    self._calls = [t for t in self._calls if now - t < self.period]
            self._calls.append(time.monotonic())


_LIMITER = _RateLimiter(max_calls=8, period_sec=60)

ODDS_TTL_MIN = 30
CONTEXT_TTL_HOURS = 6


class APISportsError(Exception):
    pass


def get_base(sport: str) -> str:
    cfg = SPORT_CONFIG.get(sport)
    if not cfg:
        raise APISportsError(f"unknown sport {sport}")
    return cfg["base"]


def proxy_season(sport: str):
    return SPORT_CONFIG[sport]["season"]


def top_leagues(sport: str) -> set:
    return SPORT_CONFIG[sport]["top_leagues"]


async def _get(sport: str, client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    if not API_KEY:
        raise APISportsError("API_FOOTBALL_KEY not configured")
    await _LIMITER.acquire()
    url = f"{get_base(sport)}{path}"
    try:
        r = await client.get(url, headers={"x-apisports-key": API_KEY}, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise APISportsError(f"{path}: {exc}") from exc
    errs = data.get("errors")
    if errs and isinstance(errs, dict) and errs:
        if "rateLimit" in errs:
            log.warning("Hit hard rate limit, sleeping 60s and retrying %s", path)
            await asyncio.sleep(60)
            await _LIMITER.acquire()
            r = await client.get(url, headers={"x-apisports-key": API_KEY}, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        else:
            log.warning("API errors for %s [%s]: %s", path, sport, errs)
    return data


# ── Cache helpers ────────────────────────────────────────────────────────────
def _cache_fresh(doc: dict | None, ttl_minutes: int) -> bool:
    if not doc:
        return False
    ts = doc.get("_cached_at")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False
    return (datetime.now(timezone.utc) - cached) < timedelta(minutes=ttl_minutes)


async def _cache_get(db, collection: str, key: dict, ttl_minutes: int):
    if db is None:
        return None
    doc = await db[collection].find_one(key)
    if _cache_fresh(doc, ttl_minutes):
        return doc.get("data")
    return None


async def _cache_set(db, collection: str, key: dict, data: Any) -> None:
    if db is None:
        return
    doc = {**key, "data": data, "_cached_at": datetime.now(timezone.utc).isoformat()}
    await db[collection].update_one(key, {"$set": doc}, upsert=True)


# ── Generic endpoints (football | basketball | baseball) ─────────────────────
async def fixtures_by_date(sport: str, client: httpx.AsyncClient, date_iso: str) -> list[dict]:
    """Fixtures (or games) for a date. Football uses /fixtures, basketball/baseball use /games."""
    path = "/fixtures" if sport == "football" else "/games"
    data = await _get(sport, client, path, {"date": date_iso})
    return data.get("response", []) or []


async def fixtures_next_48h(sport: str, client: httpx.AsyncClient) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    out: list[dict] = []
    for d in (today, tomorrow):
        out.extend(await fixtures_by_date(sport, client, d.isoformat()))
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=48)
    res = []
    for f in out:
        try:
            if sport == "football":
                ts = f["fixture"]["timestamp"]
                status = f["fixture"]["status"]["short"]
                upcoming_statuses = ("NS", "TBD")
            else:
                ts = f.get("timestamp") or (f.get("fixture") or {}).get("timestamp")
                status_obj = f.get("status") or {}
                status = status_obj.get("short") if isinstance(status_obj, dict) else None
                upcoming_statuses = ("NS", "TBD", "SCHED", "AWAITING")
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if status in upcoming_statuses and now - timedelta(minutes=10) <= dt <= cutoff:
                res.append(f)
        except Exception:
            pass
    return res


# Status sets used to identify in-play (live) games on the
# basketball/baseball endpoints. API-Sports v1 for these sports does NOT
# accept `?live=all` (returns `The Live field do not exist.`), so we
# fetch all games for "today" and "yesterday" (to cover late starters
# that cross midnight UTC) and filter client-side by the API's
# `status.short` codes.
_LIVE_STATUS_SHORT: dict[str, set[str]] = {
    "basketball": {
        "Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT",
        "in_play", "IN_PLAY", "LIVE",
    },
    "baseball": {
        "IN1", "IN2", "IN3", "IN4", "IN5", "IN6", "IN7", "IN8", "IN9",
        "IN10", "IN11", "IN12", "IN13", "IN14", "IN15",
        "BT", "MID", "END", "BRK",
        "in_play", "IN_PLAY", "LIVE",
    },
}


async def fixtures_live(sport: str, client: httpx.AsyncClient) -> list[dict]:
    """Return raw fixture objects that are currently in-play.

    Football uses the native `/fixtures?live=all` endpoint, which is reliable.

    Basketball / Baseball **do not** expose a `live` filter on API-Sports v1:
        GET /games?live=all  →  {"errors":{"live":"The Live field do not exist."}}

    Instead, we fetch the day's games (today UTC + yesterday UTC to catch
    late starts) and filter by `status.short` against the per-sport
    in-play set. This is the SAME data the API would return for a working
    live endpoint, just shaped client-side.
    """
    if sport == "football":
        data = await _get(sport, client, "/fixtures", {"live": "all"})
        return data.get("response", []) or []

    # Basketball / Baseball — date-window filter
    from datetime import datetime, timezone, timedelta
    today_utc = datetime.now(timezone.utc).date()
    dates = [today_utc.isoformat(), (today_utc - timedelta(days=1)).isoformat()]
    live_set = _LIVE_STATUS_SHORT.get(sport, set())
    seen_ids: set = set()
    live: list[dict] = []
    for d in dates:
        try:
            data = await _get(sport, client, "/games", {"date": d})
        except Exception as exc:
            log.warning("fixtures_live[%s] date=%s failed: %s", sport, d, exc)
            continue
        for g in data.get("response", []) or []:
            try:
                gid = g.get("id")
                if gid in seen_ids:
                    continue
                short = ((g.get("status") or {}).get("short") or "").strip()
                if short in live_set:
                    live.append(g)
                    seen_ids.add(gid)
            except Exception:
                continue
    log.info("fixtures_live[%s] dates=%s → %d live games", sport, dates, len(live))
    return live


async def fixture_by_id(sport: str, client: httpx.AsyncClient, fixture_id: int) -> dict | None:
    path = "/fixtures" if sport == "football" else "/games"
    data = await _get(sport, client, path, {"id": fixture_id})
    resp = data.get("response", []) or []
    return resp[0] if resp else None


async def odds_for_fixture(sport: str, client: httpx.AsyncClient, fixture_id: int, db=None) -> list[dict]:
    key = {"sport": sport, "fixture_id": fixture_id}
    cached = await _cache_get(db, "cache_odds", key, ODDS_TTL_MIN)
    if cached is not None:
        return cached
    data = await _get(sport, client, "/odds", {"game" if sport != "football" else "fixture": fixture_id})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_odds", key, resp)
    return resp


async def standings(sport: str, client: httpx.AsyncClient, league_id: int, season=None, db=None) -> list[dict]:
    season = season or proxy_season(sport)
    key = {"sport": sport, "league_id": league_id, "season": season}
    cached = await _cache_get(db, "cache_standings", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    data = await _get(sport, client, "/standings", {"league": league_id, "season": season})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_standings", key, resp)
    return resp


async def team_statistics(sport: str, client: httpx.AsyncClient, team_id: int, league_id: int, season=None, db=None) -> dict:
    season = season or proxy_season(sport)
    key = {"sport": sport, "team_id": team_id, "league_id": league_id, "season": season}
    cached = await _cache_get(db, "cache_team_stats", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    # football has /teams/statistics; basketball/baseball use /teams/statistics too with same params
    data = await _get(sport, client, "/teams/statistics", {"team": team_id, "league": league_id, "season": season})
    resp = data.get("response") or {}
    await _cache_set(db, "cache_team_stats", key, resp)
    return resp


async def head_to_head(sport: str, client: httpx.AsyncClient, home_id: int, away_id: int, limit: int = 5, db=None) -> list[dict]:
    key = {"sport": sport, "h2h_key": f"{home_id}-{away_id}"}
    cached = await _cache_get(db, "cache_h2h", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached[:limit]
    if sport == "football":
        data = await _get(sport, client, "/fixtures/headtohead", {"h2h": f"{home_id}-{away_id}"})
    else:
        data = await _get(sport, client, "/games/h2h", {"h2h": f"{home_id}-{away_id}"})
    items = data.get("response", []) or []
    items.sort(key=lambda f: ((f.get("fixture") or {}).get("timestamp") or f.get("timestamp") or 0), reverse=True)
    await _cache_set(db, "cache_h2h", key, items)
    return items[:limit]
