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
from ._ingestion_helpers.football_odds_cascade import (
    fetch_football_odds_with_fallback as _fetch_football_odds_with_fallback,
)
from .external_sources import (
    thestatsapi_team_stats_adapter as _ts_team_stats,
    thestatsapi_h2h_adapter as _ts_h2h,
    thestatsapi_odds_adapter as _ts_odds,
)

log = logging.getLogger("ingestion")

# Phase F84.a — Prioridad-inversa: TheStatsAPI primaria, API-Sports
# fallback. Defaults to ``true`` to preserve the previous behaviour
# (every TheStatsAPI miss gracefully falls back to api_football). Set
# ``ENABLE_API_SPORTS_FALLBACK=false`` to enter "TheStatsAPI-only" mode
# — useful for staging environments that need to surface coverage gaps.
import os as _os  # used by F84.a + F87 flag helpers
def _api_sports_fallback_enabled() -> bool:
    raw = (_os.environ.get("ENABLE_API_SPORTS_FALLBACK") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


# ─────────────────────────────────────────────────────────────────────
# F87 — Football fixture discovery (TheStatsAPI → API-Football → ESPN
# → Sofascore PW → scrape.do). Designed to be INDEPENDENT from any
# MLB / baseball / basketball module so MLB QCM changes can't break
# football discovery.
# ─────────────────────────────────────────────────────────────────────
_F87_MIN_VIABLE_COUNT = int(_os.environ.get("F87_MIN_VIABLE_COUNT", "5"))
_F87_MERGE_PRIORITY = ("thesportsdb", "thestatsapi", "espn",
                       "sofascore_pw", "api_football", "scrapedo")

# Status válidos para upcoming (cualquier otro se descarta en la cascada).
# Defensa-en-profundidad: aunque cada adapter ya filtra terminados/en vivo,
# este filtro final garantiza que un bug en un adapter futuro no inyecte
# fixtures jugadas / canceladas al pipeline (caso Ecuador vs Curaçao FT).
_F87_VALID_UPCOMING_STATUSES: frozenset[str] = frozenset({"NS", "TBD"})

# Last discovery audit — exposed via /api/football/discovery/debug.
# Reset on every ``_discover_football_fixtures`` invocation. NEVER read
# from outside ``services.data_ingestion`` except by the debug endpoint.
LAST_FOOTBALL_DISCOVERY_AUDIT: dict = {}


def get_last_football_discovery_audit() -> dict:
    """Public read-only accessor for the latest discovery audit dict.

    Returns an empty dict when discovery has not been invoked since
    service startup. A deep copy is returned so callers can mutate the
    result without affecting the module-level cache.
    """
    import copy as _copy
    return _copy.deepcopy(LAST_FOOTBALL_DISCOVERY_AUDIT)


def _f87_flag_enabled(env_var: str, default: str = "true") -> bool:
    raw = (_os.environ.get(env_var) or default).strip().lower()
    return raw not in ("0", "false", "no", "off")


def _normalize_team_for_dedupe(name: str) -> str:
    """Lowercase, strip diacritics, drop common suffixes (FC, CF, SC, U23).
    Pure: never depends on any sport-specific module."""
    import unicodedata as _uc
    import re as _re
    s = _uc.normalize("NFKD", name or "").encode("ASCII", "ignore").decode()
    s = _re.sub(r"\b(fc|cf|sc|sd|ac|u\d+)\b", "", s.lower())
    return _re.sub(r"\s+", " ", s).strip()


def _fixture_dedupe_key(fx: dict) -> tuple:
    home = ((fx.get("teams") or {}).get("home") or {}).get("name") or ""
    away = ((fx.get("teams") or {}).get("away") or {}).get("name") or ""
    ts   = fx.get("timestamp") or (fx.get("fixture") or {}).get("timestamp") or 0
    try:
        date_only = (
            datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
            if ts else ""
        )
    except (TypeError, ValueError, OverflowError, OSError):
        date_only = ""
    return (
        _normalize_team_for_dedupe(home),
        _normalize_team_for_dedupe(away),
        date_only,
    )


def _espn_to_apifootball_shape(ev: dict) -> dict:
    """Convert one ESPN scoreboard event into the API-Football shape so
    it can flow through the same merge step as TheStatsAPI / Sofascore.
    """
    if not isinstance(ev, dict):
        return {}
    iso = ev.get("kickoff_iso")
    ts = None
    if isinstance(iso, str) and iso:
        try:
            ts = int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            ts = None
    home_obj = ev.get("home_team") or {}
    away_obj = ev.get("away_team") or {}
    return {
        "id":        str(ev.get("id") or ""),
        "fixture":   {
            "id":        str(ev.get("id") or ""),
            "date":      iso,
            "timestamp": ts,
            "status":    {"short": "1H" if ev.get("is_live") else "NS"},
            "venue":     {"name": None, "city": None},
        },
        "league":    {
            "id":      None,
            "name":    ev.get("league") or "",
            "country": None,
        },
        "teams":     {
            "home": {"id": home_obj.get("id"), "name": home_obj.get("name", "Home")},
            "away": {"id": away_obj.get("id"), "name": away_obj.get("name", "Away")},
        },
        "_external_source":    "espn",
        "_external_source_id": str(ev.get("id") or ""),
        "date":      iso,
        "timestamp": ts,
        "status":    {"short": "1H" if ev.get("is_live") else "NS"},
    }


def _merge_fixture_buckets(buckets: dict[str, list[dict]]) -> list[dict]:
    """Dedupe fixtures across discovery sources by (home, away, kickoff_date).
    Keeps the entry from the highest-priority bucket and stamps
    ``_discovery_source`` so downstream knows where it came from."""
    seen: dict[tuple, dict] = {}
    for src in _F87_MERGE_PRIORITY:
        for fx in buckets.get(src, []) or []:
            try:
                key = _fixture_dedupe_key(fx)
            except Exception:  # noqa: BLE001
                continue
            if key in seen:
                continue
            fx.setdefault("_discovery_source", src)
            seen[key] = fx
    return list(seen.values())


async def _discover_football_fixtures(
    client: httpx.AsyncClient,
) -> tuple[list[dict], dict]:
    """F87 — Resilient football discovery cascade.

    Order:
      1) TheStatsAPI primary (F87.a)
      2) API-Football fallback (legacy)
      3) ESPN scoreboard
      4) Sofascore via Playwright (F87.b)
      5) Sofascore via scrape.do (F87.b)

    Merge strategy:
      * The first source whose normalised count ≥ ``F87_MIN_VIABLE_COUNT``
        (default 5) short-circuits the cascade.
      * Otherwise every source is merged + deduped by
        (home_norm, away_norm, kickoff_date).

    **F87.1 contract guarantee:** every fixture flowing out of this
    function has been passed through
    :func:`football_fixture_contract.ensure_api_football_fixture_shape`,
    so ``_enrich_football`` ALWAYS receives the nested API-Football
    shape regardless of which adapter discovered the match.

    **Isolated from MLB modules** — never imports
    ``mlb_quality_contact_matchup``, ``mlb_pipeline_payload_contract`` or
    any MLB/baseball adapter. Failure in those modules cannot affect
    football discovery.
    """
    # Lazy import inside the function so the F87.1 contract module
    # cannot become a circular dependency surface for the legacy paths.
    from . import football_fixture_contract as ffc

    log.info("[F87_discovery] sport=football isolated_from_mlb=true")
    audit: dict = {
        "sources_called":   [],
        "counts_per_src":   {},        # raw (pre-normalisation)
        "counts_normalised":{},        # post-shape normalisation
        "shape_audit":      {},        # per-source FFC reason-code counts
        "reason_codes":     {},
        "primary_winner":   None,
        "merged":           False,
        "total":            0,
        "isolated_from_mlb": True,
        "f87_1_contract":   True,
    }
    buckets: dict[str, list[dict]] = {}

    def _publish_audit(final_fixtures: list[dict]) -> None:
        """Publish the audit + sample for /api/football/discovery/debug.
        Called on every exit path (short-circuit or merge)."""
        try:
            LAST_FOOTBALL_DISCOVERY_AUDIT.clear()
            LAST_FOOTBALL_DISCOVERY_AUDIT.update(audit)
            LAST_FOOTBALL_DISCOVERY_AUDIT["sample_fixtures"] = [
                {
                    "source":      f.get("_discovery_source"),
                    "match_id":    (f.get("fixture") or {}).get("id"),
                    "home":        ((f.get("teams") or {}).get("home") or {}).get("name"),
                    "away":        ((f.get("teams") or {}).get("away") or {}).get("name"),
                    "league":      (f.get("league") or {}).get("name"),
                    "kickoff_iso": (f.get("fixture") or {}).get("date"),
                    "status":      ((f.get("fixture") or {}).get("status") or {}).get("short"),
                    "shape_valid": isinstance(f.get("fixture"), dict)
                                    and isinstance(f.get("teams"), dict),
                }
                for f in (final_fixtures or [])[:8]
            ]
        except Exception:  # noqa: BLE001
            pass

    def _normalise_and_record(name: str, raw: list[Any]) -> list[dict]:
        normalised, shape_audit = ffc.normalize_bucket(raw or [], source=name)
        # Defensa-en-profundidad: descartar partidos cuyo status no sea
        # válido como upcoming (NS / TBD). Aunque cada adapter ya debería
        # filtrar terminados / en vivo, este filtro común protege al
        # pipeline ante regresiones en cualquier source futura.
        filtered: list[dict] = []
        dropped = 0
        for fx in normalised:
            status = ((fx.get("fixture") or {}).get("status") or {}).get("short")
            if status and status not in _F87_VALID_UPCOMING_STATUSES:
                dropped += 1
                continue
            filtered.append(fx)
        if dropped > 0:
            log.info(
                "[F87_discovery] %s dropped %d non-upcoming fixtures "
                "(status not in NS/TBD)", name, dropped,
            )
            audit["reason_codes"].setdefault(name, []).append(
                f"DROPPED_NON_UPCOMING={dropped}"
            )
        audit["counts_per_src"][name]    = len(raw or [])
        audit["counts_normalised"][name] = len(filtered)
        audit["shape_audit"][name]       = shape_audit
        return filtered

    # ── 0) TheSportsDB primary (Sprint-D8-Fase2 cascade refactor) ──
    # Decisión del usuario: TheSportsDB es ahora la fuente PRIMARIA
    # de descubrimiento de fixtures de fútbol. The Odds API solo se
    # usa para enrichment de odds, NO de fixtures. API-Sports queda
    # como último fallback.
    if _f87_flag_enabled("ENABLE_THESPORTSDB_FIXTURES_PRIMARY"):
        try:
            from .external_sources import thesportsdb_fixtures_adapter as _tsdb_fx
            tsdb_raw, tsdb_codes = await _tsdb_fx.fetch_fixtures_next_48h(client)
            tsdb_fx = _normalise_and_record("thesportsdb", tsdb_raw)
            buckets["thesportsdb"] = tsdb_fx
            audit["sources_called"].append("thesportsdb")
            audit["reason_codes"]["thesportsdb"] = tsdb_codes
            if len(tsdb_fx) >= _F87_MIN_VIABLE_COUNT:
                audit["primary_winner"] = "thesportsdb"
                audit["total"]          = len(tsdb_fx)
                for f in tsdb_fx:
                    f.setdefault("_discovery_source", "thesportsdb")
                _publish_audit(tsdb_fx)
                return tsdb_fx, audit
        except Exception as exc:  # noqa: BLE001
            log.warning("[F87_discovery] thesportsdb failed: %s", exc)
            audit["reason_codes"]["thesportsdb"] = ["EXCEPTION"]

    # ── 1) TheStatsAPI (legacy primary, now secondary) ──
    if _f87_flag_enabled("ENABLE_THESTATSAPI_FIXTURES_PRIMARY"):
        try:
            from .external_sources import thestatsapi_fixtures_adapter as _tsfx
            ts_raw, ts_codes = await _tsfx.fetch_fixtures_next_48h(client)
            ts_fx = _normalise_and_record("thestatsapi", ts_raw)
            buckets["thestatsapi"] = ts_fx
            audit["sources_called"].append("thestatsapi")
            audit["reason_codes"]["thestatsapi"]   = ts_codes
            if len(ts_fx) >= _F87_MIN_VIABLE_COUNT:
                audit["primary_winner"] = "thestatsapi"
                audit["total"]          = len(ts_fx)
                for f in ts_fx:
                    f.setdefault("_discovery_source", "thestatsapi")
                _publish_audit(ts_fx)
                return ts_fx, audit
        except Exception as exc:  # noqa: BLE001
            log.warning("[F87_discovery] thestatsapi failed: %s", exc)
            audit["reason_codes"]["thestatsapi"] = ["EXCEPTION"]

    # ─────────────────────────────────────────────────────────────────
    # Sprint-D9-cascade-reorder (decisión usuario): el orden ahora es
    # TheSportsDB → TheStatsAPI → ESPN → Sofascore → API-Football.
    # Motivación: API-Sports estaba devolviendo 0 fixtures cuando el
    # crédito se agotaba o el endpoint daba 502, bloqueando el pipeline.
    # Priorizamos primero las fuentes gratuitas confiables (ESPN/Sofa)
    # antes de caer al proveedor de pago.
    # ─────────────────────────────────────────────────────────────────

    # ── 2) ESPN scoreboard (free, primary fallback) ──
    try:
        espn_raw = await fb.espn_soccer_scoreboard(client)
        espn_pre = [_espn_to_apifootball_shape(e) for e in (espn_raw or [])]
        espn_pre = [e for e in espn_pre if e]
        espn_fx  = _normalise_and_record("espn", espn_pre)
        buckets["espn"] = espn_fx
        audit["sources_called"].append("espn")
        if len(espn_fx) >= _F87_MIN_VIABLE_COUNT:
            audit["primary_winner"] = "espn"
            audit["total"]          = len(espn_fx)
            for f in espn_fx:
                f.setdefault("_discovery_source", "espn")
            _publish_audit(espn_fx)
            return espn_fx, audit
    except Exception as exc:  # noqa: BLE001
        log.warning("[F87_discovery] espn failed: %s", exc)
        audit["reason_codes"]["espn"] = ["EXCEPTION"]

    # ── 3) Sofascore via Playwright (free, secondary fallback) ──
    if _f87_flag_enabled("ENABLE_SOFASCORE_PW_FALLBACK"):
        try:
            from .external_sources import sofascore_fixtures_adapter as _sofa
            sofa_raw = await _sofa.fetch_fixtures_today()
            sofa_fx  = _normalise_and_record("sofascore_pw", sofa_raw)
            buckets["sofascore_pw"] = sofa_fx
            audit["sources_called"].append("sofascore_pw")
            if len(sofa_fx) >= _F87_MIN_VIABLE_COUNT:
                audit["primary_winner"] = "sofascore_pw"
                audit["total"]          = len(sofa_fx)
                for f in sofa_fx:
                    f.setdefault("_discovery_source", "sofascore_pw")
                _publish_audit(sofa_fx)
                return sofa_fx, audit
        except Exception as exc:  # noqa: BLE001
            log.warning("[F87_discovery] sofascore_pw failed: %s", exc)
            audit["reason_codes"]["sofascore_pw"] = ["EXCEPTION"]

    # ── 4) API-Football (paid, last-resort fallback) ──
    if _f87_flag_enabled("ENABLE_API_FOOTBALL_FALLBACK"):
        try:
            af_raw = await af.fixtures_next_48h(client) or []
            af_fx = _normalise_and_record("api_football", af_raw)
            buckets["api_football"] = af_fx
            audit["sources_called"].append("api_football")
            if (len(af_fx) >= _F87_MIN_VIABLE_COUNT
                    and not buckets.get("thestatsapi")):
                audit["primary_winner"] = "api_football"
                audit["total"]          = len(af_fx)
                for f in af_fx:
                    f.setdefault("_discovery_source", "api_football")
                _publish_audit(af_fx)
                return af_fx, audit
        except Exception as exc:  # noqa: BLE001
            log.warning("[F87_discovery] api_football failed: %s", exc)
            audit["reason_codes"]["api_football"] = ["EXCEPTION"]

    # ── 5) Sofascore via scrape.do (tertiary network fallback) ──
    if _f87_flag_enabled("ENABLE_SCRAPEDO_FIXTURES_FALLBACK"):
        try:
            from .external_sources import scrapedo_fixtures_adapter as _sdf
            sd_raw = await _sdf.fetch_fixtures_today()
            sd_fx  = _normalise_and_record("scrapedo", sd_raw)
            buckets["scrapedo"] = sd_fx
            audit["sources_called"].append("scrapedo")
        except Exception as exc:  # noqa: BLE001
            log.warning("[F87_discovery] scrapedo failed: %s", exc)
            audit["reason_codes"]["scrapedo"] = ["EXCEPTION"]

    # ── Merge ──
    merged = _merge_fixture_buckets(buckets)
    audit["merged"] = True
    audit["total"]  = len(merged)
    _publish_audit(merged)
    return merged, audit


# Phase F84.a (consolidated above; kept as a no-op anchor for blame).
def _api_sports_fallback_enabled_legacy() -> bool:
    return _api_sports_fallback_enabled()

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

    **Refactor (Sprint-D9 — Ecuador vs Curaçao FT bug):** ahora usa la
    cascada ``_discover_football_fixtures`` (TheSportsDB → TheStatsAPI →
    ESPN → Sofascore → API-Football) en lugar de pegarle directo a
    ``af.fixtures_by_date``. Razones:
      * Cuando los créditos de API-Sports se agotan, esta función
        devolvía 0 fixtures bloqueando todo el pipeline.
      * TheSportsDB ofrece coverage casi total para las priority leagues
        sin coste.
      * La cascada ya descarta partidos terminados / en vivo (filtro
        ``_F87_VALID_UPCOMING_STATUSES``), eliminando el bug histórico
        de partidos FT apareciendo como upcoming.

    Filtros adicionales que esta función aplica:
      * Solo se conservan partidos cuyo nombre de liga matchee
        ``PRIORITY_LADDER`` (matching por nombre normalizado — funciona
        con cualquier source, no solo API-Sports ID-based).
      * Ventana temporal: now - 10min ≤ kickoff ≤ now + window_hours.
      * Status: solo NS / TBD (defensa-en-profundidad).

    Fallback: si la cascada retorna 0 fixtures Y API-Sports está
    habilitado, se ejecuta la lógica legacy `af.fixtures_by_date` como
    último recurso (consume créditos).

    Returns:
        Lista de fixtures normalizadas (shape API-Football) ordenadas
        por kickoff ascendente.
    """
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

    # Normalización de nombres de liga para matching cross-source.
    def _norm_league(name: str) -> str:
        if not name:
            return ""
        s = name.lower().strip()
        # Strip diacríticos comunes y signos
        for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),
                       ("ñ","n"),("ã","a"),("ê","e"),("ô","o"),("ç","c")):
            s = s.replace(a, b)
        for ch in ("-", "_", ".", "'"):
            s = s.replace(ch, " ")
        return " ".join(s.split())

    # Aliases conocidos por source.
    _LEAGUE_ALIASES: dict[str, str] = {
        # Champions League
        "champions league":              "uefa champions league",
        "uefa champions league":         "uefa champions league",
        # Premier League
        "english premier league":        "premier league",
        "premier league":                "premier league",
        # LaLiga
        "spanish la liga":               "laliga",
        "la liga":                       "laliga",
        "laliga":                        "laliga",
        # Serie A
        "italian serie a":               "serie a",
        "serie a":                       "serie a",
        # Bundesliga
        "german bundesliga":             "bundesliga",
        "bundesliga":                    "bundesliga",
        # Ligue 1
        "french ligue 1":                "ligue 1",
        "ligue 1":                       "ligue 1",
        # Mexican Liga MX
        "mexican primera division":      "liga mx",
        "liga mx":                       "liga mx",
        # Europa / Conference
        "uefa europa league":            "uefa europa league",
        "europa league":                 "uefa europa league",
        "uefa europa conference league": "uefa conference league",
        "uefa conference league":        "uefa conference league",
        "conference league":             "uefa conference league",
        # Libertadores
        "conmebol libertadores":         "copa libertadores",
        "copa libertadores":             "copa libertadores",
        # MLS
        "major league soccer":           "mls",
        "mls":                           "mls",
        # Brasileirão
        "brasileirao serie a":           "brasileirao serie a",
        "brazilian serie a":             "brasileirao serie a",
        # FIFA / UEFA seleccionados (también Friendlies internacionales)
        "fifa world cup":                "fifa world cup",
        "world cup":                     "fifa world cup",
        "uefa euro":                     "uefa euro",
        "european championship":         "uefa euro",
        "copa america":                  "copa america",
    }
    priority_norm: set[str] = {_norm_league(n) for n, _ in PRIORITY_LADDER}

    def _matches_priority(league_name: Optional[str]) -> bool:
        if not league_name:
            return False
        norm = _norm_league(league_name)
        canonical = _LEAGUE_ALIASES.get(norm, norm)
        return canonical in priority_norm

    # ── Paso 1: ejecutar la cascada (TheSportsDB primario) ──
    cascade_raw: list[dict] = []
    try:
        cascade_raw, cascade_audit = await _discover_football_fixtures(client)
        log.info(
            "[priority_discover] cascade returned %d fixtures (winner=%s)",
            len(cascade_raw), cascade_audit.get("primary_winner"),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[priority_discover] cascade failed: %s — fallback to API-Sports", exc)

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=window_hours)

    def _kickoff_dt(fx: dict) -> Optional[datetime]:
        # Prefer timestamp epoch; fallback a date ISO.
        try:
            ts = (fx.get("fixture") or {}).get("timestamp")
            if ts:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            pass
        try:
            iso = (fx.get("fixture") or {}).get("date")
            if iso:
                return datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except Exception:
            pass
        return None

    discovered: list[dict] = []
    counts: dict[str, int] = {}
    # Sprint-D9-HOTFIX (priority-id-trust): el reorden de cascada
    # (TheSportsDB primario) introdujo un side-effect: TheSportsDB usa
    # IDs propios (4000-6000) y nombres ambiguos (p.ej. "FIFA World Cup"
    # incluye sub-17/sub-20 friendlies). El filtro por nombre matchea
    # falsos positivos. Para mitigarlo, contamos cuántos fixtures vienen
    # con un league_id válido de API-Football (el universo de IDs que
    # PRIORITY_LADDER realmente usa). Si NO hay matches por ID — solo por
    # nombre — forzamos el fallback API-Football aunque ``discovered`` no
    # esté vacío.
    matched_by_id_count = 0
    for fx in cascade_raw:
        league_name = (fx.get("league") or {}).get("name")
        league_id   = (fx.get("league") or {}).get("id")
        # Match por ID (rápido, solo si la source es API-Sports) O por nombre.
        is_priority_by_id   = isinstance(league_id, int) and league_id in priority_ids
        is_priority_by_name = _matches_priority(league_name)
        is_priority = is_priority_by_id or is_priority_by_name
        if not is_priority:
            continue
        status = ((fx.get("fixture") or {}).get("status") or {}).get("short")
        if status and status not in ("NS", "TBD"):
            continue
        dt = _kickoff_dt(fx)
        if dt is None:
            continue
        if not (now - timedelta(minutes=10) <= dt <= cutoff):
            continue
        discovered.append(fx)
        if is_priority_by_id:
            matched_by_id_count += 1
        label = id_to_label.get(league_id) if isinstance(league_id, int) else league_name
        counts[label or "unknown"] = counts.get(label or "unknown", 0) + 1

    # ── Paso 2: si la cascada NO encontró nada de priority **por ID
    # canónico de API-Football**, fallback al endpoint directo de
    # API-Sports (consume créditos — último recurso). ──
    if not discovered or matched_by_id_count == 0:
        log.info(
            "[priority_discover] cascade priority signal weak "
            "(discovered=%d, matched_by_id=%d) — invoking API-Sports fallback",
            len(discovered), matched_by_id_count,
        )
        # Cuando solo había matches por nombre (no por ID canónico), los
        # consideramos "low confidence" (p.ej. TheSportsDB suele matchear
        # sub-17/sub-20 a "FIFA World Cup"). Descartamos ese ruido y
        # reemplazamos por el resultado autorizado de API-Football.
        if matched_by_id_count == 0:
            discovered = []
            counts = {}
        today = now.date()
        tomorrow = today + timedelta(days=1)
        raw_af: list[dict] = []
        for d in (today, tomorrow):
            try:
                chunk = await af.fixtures_by_date(client, d.isoformat())
                raw_af.extend(chunk)
            except Exception as exc:
                log.warning("[priority_discover] /fixtures?date=%s failed: %s", d, exc)
        # Dedupe por (home, away, kickoff-timestamp) para no duplicar
        # fixtures que ya vinieron por la cascada con ID válido.
        existing_keys = set()
        for ex in discovered:
            t = ex.get("teams") or {}
            h = (t.get("home") or {}).get("name") or ""
            a = (t.get("away") or {}).get("name") or ""
            ts = (ex.get("fixture") or {}).get("timestamp") or 0
            existing_keys.add((h.lower(), a.lower(), int(ts) // 60))
        for fx in raw_af:
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
            t = fx.get("teams") or {}
            h = (t.get("home") or {}).get("name") or ""
            a = (t.get("away") or {}).get("name") or ""
            key = (h.lower(), a.lower(), int(ts) // 60)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            fx.setdefault("_discovery_source", "api_football")
            discovered.append(fx)
            label = id_to_label.get(lid, str(lid))
            counts[label] = counts.get(label, 0) + 1

    discovered.sort(key=lambda f: (
        ((f.get("fixture") or {}).get("timestamp") or 0)
        or (_kickoff_dt(f).timestamp() if _kickoff_dt(f) else 0)
    ))
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
        # F87 — resilient discovery cascade (TheStatsAPI → API-Football →
        # ESPN → Sofascore PW → scrape.do). Replaces the old single-call
        # ``af.fixtures_next_48h``. The cascade is sport-isolated so MLB
        # QCM changes can't break football discovery.
        try:
            upcoming_raw, discovery_audit = await _discover_football_fixtures(client)
            log.info("[F87_discovery] %s", discovery_audit)
        except Exception as exc:
            log.error("[F87_discovery] cascade failed unexpectedly: %s", exc)
            upcoming_raw = []
            discovery_audit = {"sources_called": [], "total": 0,
                                "merged": False, "error": repr(exc)}
    else:
        try:
            upcoming_raw = await aps.fixtures_next_48h(sport, client)
        except Exception as exc:
            log.error("API-Sports[%s] fixtures failed: %s", sport, exc)
            upcoming_raw = []

    # ``fallback_used`` was tracked locally for an older telemetry
    # block (now consumed via dict keys lower down). The dead store
    # was removed in F86-housekeeping to clear the ruff F841 warning.
    if not upcoming_raw:
        if sport != "football":
            log.warning("No upcoming for %s — no fallback available for this sport", sport)
            return []
        log.warning("No upcoming from API-Football, attempting ESPN fallback")
        fb_data = await fb.espn_soccer_scoreboard(client)
        # ``fallback_used=True`` is reserved for the future telemetry
        # block but never consumed today — keep as a comment so the
        # linter doesn't flag a dead assignment.
        # fallback_used = True
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
        tier_counts = {"tier_1": 0, "tier_2": 0, "tier_3": 0,
                        "national_team": 0, "unknown": 0}
        removed_leagues: dict[str, int] = {}
        blocklisted_count = 0
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
            #    get a synthetic Tier-2 meta.
            if is_national_team_league(league_id_raw):
                synthetic_meta = {
                    "tier":           "tier_2",
                    "priority":       72,
                    "canonical_name": league_name or "National Team Competition",
                    "type":           "international",
                    "region":         league_obj.get("country") or "World",
                    "_synthetic_national_team": True,
                }
                tier_counts["national_team"] = tier_counts.get("national_team", 0) + 1
                f["_competition_meta"] = synthetic_meta
                kept.append(f)
                continue
            # 3) F87.c — Unknown competition bucket (inclusive default).
            #    Accept the fixture at low priority instead of discarding
            #    when the league name is not in the registry AND not
            #    blocklisted (youth, reserves, friendly clubs, regional).
            unk = fc.get_unknown_competition_meta(league_name)
            if unk is not None:
                tier_counts["unknown"] = tier_counts.get("unknown", 0) + 1
                f["_competition_meta"] = unk
                kept.append(f)
                continue
            # 4) Final discard (registry miss + blocklist hit, or flag off).
            if fc.is_competition_blocklisted(league_name):
                blocklisted_count += 1
            removed_leagues[league_name or "?"] = removed_leagues.get(league_name or "?", 0) + 1
        log.info(
            "Scraper fetched %d football events. Allowed competition filter kept %d matches. "
            "Removed %d matches from non-priority leagues (blocklisted=%d).",
            before, len(kept), before - len(kept), blocklisted_count,
        )
        log.info(
            "Tier 1: %d  Tier 2: %d  Tier 3: %d  National-team: %d  "
            "Unknown: %d  (allowed_tiers=%s)",
            tier_counts["tier_1"], tier_counts["tier_2"], tier_counts["tier_3"],
            tier_counts.get("national_team", 0),
            tier_counts.get("unknown", 0),
            sorted(fc.get_allowed_tiers()),
        )

        # Sort: tier priority desc → kickoff time asc → (live boost handled in ingest_live)
        kept.sort(key=lambda f: (
            -((f.get("_competition_meta") or {}).get("priority", 0)),
            _fx_timestamp(sport, f) or 0,
        ))

        # F87.c — cap unknown-bucket fixtures so they never crowd out
        # Tier-1/2/3 ligas from the hydration budget. ``known`` is the
        # registry + national-team set; ``unknown`` is the inclusive
        # default bucket.
        unknown_subset = [f for f in kept
                          if (f.get("_competition_meta") or {}).get("_unknown_bucket")]
        known_subset = [f for f in kept
                        if not (f.get("_competition_meta") or {}).get("_unknown_bucket")]
        unknown_capped = unknown_subset[:fc.UNKNOWN_HYDRATE_CAP]
        if len(unknown_subset) > len(unknown_capped):
            log.info(
                "[F87_unknown_bucket] capped unknown competitions: %d → %d "
                "(cap=%d). Dropped names=%s",
                len(unknown_subset), len(unknown_capped), fc.UNKNOWN_HYDRATE_CAP,
                [((f.get("_competition_meta") or {}).get("canonical_name"))
                  for f in unknown_subset[fc.UNKNOWN_HYDRATE_CAP:]][:5],
            )
        kept = known_subset + unknown_capped
        # Preserve the overall priority sort after the cap.
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


# ─────────────────────────────────────────────────────────────────────
# Phase F85 — xG recent-averages background dispatch
# ─────────────────────────────────────────────────────────────────────
# El cómputo de L1/L5/L15 requiere 1–15 HTTP calls a TheStatsAPI por
# equipo (shotmap por partido reciente). Hacerlo inline dentro de
# ``_enrich_football`` agregaría 5–10s al P95 del ingestor por fixture,
# así que lo movemos a un task fire-and-forget que persiste el
# resultado en ``match_doc.xg_recent_averages`` cuando termina.
#
# Contract:
#   * NUNCA levanta excepción al caller.
#   * Persiste en Mongo (db.matches) y muta el ``match_doc`` en memoria
#     para que el editorial endpoint pueda leerlo si todavía no terminó.
#   * Si `compute_xg_recent_averages` devuelve `available=False` lo
#     persistimos igual para que la UI deje de mostrar "PENDING".
_XG_RECENT_BG_TIMEOUT_S = 30.0


async def _ensure_thestatsapi_recent_match_ids(
    match_doc: dict, fid: Any,
) -> None:
    """Pobla ``home_team.thestatsapi_recent_match_ids`` y el simétrico
    para away si faltan. Fail-soft. Requerido por
    ``football_xg_recent_averages.compute_xg_recent_averages``."""
    try:
        from .external_sources import thestatsapi_client as _ts_client
        if not _ts_client.is_enabled():
            return
    except Exception:  # noqa: BLE001
        return

    for side_label in ("home_team", "away_team"):
        side = match_doc.get(side_label) or {}
        if side.get("thestatsapi_recent_match_ids"):
            continue
        ts_team_id = side.get("_thestatsapi_id") or side.get("thestatsapi_id")
        if not ts_team_id:
            continue
        try:
            # Convención del cliente: helper `fetch_recent_match_ids`
            # devuelve [str]. Si el cliente expone otro nombre o no
            # implementa el helper, el AttributeError cae al except
            # de abajo y degradamos a fail-soft sin recent_ids.
            recent_ids = await _ts_client.fetch_recent_match_ids(
                team_id=ts_team_id, n=15,
            )
            if recent_ids:
                side["thestatsapi_recent_match_ids"] = list(recent_ids)
        except Exception as exc:  # noqa: BLE001
            log.debug(
                "[xg_recent_bg] recent_ids fetch failed for fixture=%s side=%s: %s",
                fid, side_label, exc,
            )


async def _schedule_xg_recent_background(
    match_doc: dict, fid: Any, db,
) -> None:
    """Background task — no bloquea ``_enrich_football``.

    1) Pobla `thestatsapi_recent_match_ids` si faltan.
    2) Llama `compute_xg_recent_averages` con timeout de 30s.
    3) Persiste en Mongo y muta el match_doc en memoria.
    """
    try:
        await _ensure_thestatsapi_recent_match_ids(match_doc, fid)
        from .football_xg_recent_averages import compute_xg_recent_averages
        result = await asyncio.wait_for(
            compute_xg_recent_averages(match_doc),
            timeout=_XG_RECENT_BG_TIMEOUT_S,
        )
        if not isinstance(result, dict):
            result = {
                "available":    False,
                "status":       "UNAVAILABLE",
                "reason_codes": ["XG_RECENT_NON_DICT_RESULT"],
            }
        # Stamp status para que la UI deje de mostrar "PENDING".
        result.setdefault(
            "status", "SUCCESS" if result.get("available") else "UNAVAILABLE",
        )
        # Persiste en Mongo (no upsert — si el doc desapareció lo dejamos pasar).
        try:
            await db.matches.update_one(
                {"match_id": fid},
                {"$set": {"xg_recent_averages": result}},
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[xg_recent_bg] mongo persist failed for %s: %s", fid, exc)
        # Muta en memoria por si el editorial endpoint sigue tomando esta ref.
        match_doc["xg_recent_averages"] = result
        if result.get("available"):
            log.info(
                "[xg_recent_bg] fixture=%s computed (partial=%s, source=%s)",
                fid, result.get("partial"), result.get("source"),
            )
        else:
            log.info(
                "[xg_recent_bg] fixture=%s unavailable codes=%s",
                fid, result.get("reason_codes"),
            )
    except asyncio.TimeoutError:
        log.warning("[xg_recent_bg] timeout for fixture=%s", fid)
        try:
            await db.matches.update_one(
                {"match_id": fid},
                {"$set": {"xg_recent_averages": {
                    "available":    False,
                    "status":       "TIMEOUT",
                    "reason_codes": ["XG_RECENT_BACKGROUND_TIMEOUT"],
                }}},
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        log.warning("[xg_recent_bg] crashed for fixture=%s: %s", fid, exc)


# ─────────────────────────────────────────────────────────────────────
# FIX-4 — Background scheduler for the pre-match Corners Profile.
# ─────────────────────────────────────────────────────────────────────
_CORNERS_PROFILE_BG_TIMEOUT_S = 30.0


async def _schedule_corners_profile_background(
    match_doc: dict, fid: Any, db, fx_raw: dict,
) -> None:
    """Background task — fetch each team's last-N corners and compute the
    pre-match Corners Profile (L1/L5/L15 + momentum + expected corners).

    Cache-friendly: uses ``db.team_corners_history`` 24h TTL.
    Fail-soft: any exception is logged at DEBUG and we persist an
    UNAVAILABLE marker so the UI can render the explainer.
    """
    try:
        from .football_corners_history import fetch_team_corners_history
        from .football_corners_profile  import build_corners_profile

        home = match_doc.get("home_team") or {}
        away = match_doc.get("away_team") or {}

        ts_home = home.get("_thestatsapi_id") or home.get("thestatsapi_id")
        ts_away = away.get("_thestatsapi_id") or away.get("thestatsapi_id")
        as_home = home.get("id")
        as_away = away.get("id")
        season  = (fx_raw.get("league") or {}).get("season")

        # Sprint-D9.2 Block A — detect national-team fixtures and ask
        # the corners history layer for a CROSS-tournament window
        # (friendlies + qualifiers + tournaments) instead of just the
        # current tournament season. Without this the L1/L5/L15 window
        # for selecciones never had more than ~7 matches.
        league_block = (fx_raw.get("league") or {})
        league_type  = (league_block.get("type") or "").lower()
        league_id    = league_block.get("id")
        is_national_team_fixture = (
            league_type == "cup"
            and league_id in (
                1,    # FIFA World Cup
                4,    # UEFA European Championship
                9,    # Copa América
                10,   # Friendlies International
                32,   # World Cup Qualifying (Europe)
                34,   # World Cup Qualifying (South America)
                29,   # World Cup Qualifying (Africa)
                30,   # World Cup Qualifying (Asia)
                31,   # World Cup Qualifying (Concacaf)
                5,    # UEFA Nations League
                26,   # International Friendlies — Clubs (some flags)
            )
        )

        async def _one(team_label, ts_id, as_id):
            try:
                return await asyncio.wait_for(
                    fetch_team_corners_history(
                        None, db,
                        team_id_thestatsapi=str(ts_id) if ts_id else None,
                        team_id_apisports=as_id,
                        season=season, n=15, min_sample=5, use_cache=True,
                        include_all_competitions=is_national_team_fixture,
                    ),
                    timeout=_CORNERS_PROFILE_BG_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                log.warning("[corners_profile_bg] timeout %s fixture=%s",
                            team_label, fid)
                return {"history": [], "source": "none",
                        "reason_codes": ["CORNERS_PROFILE_BG_TIMEOUT"]}
            except Exception as exc:
                log.debug("[corners_profile_bg] %s failed fixture=%s: %s",
                          team_label, fid, exc)
                return {"history": [], "source": "none",
                        "reason_codes": [f"CORNERS_PROFILE_BG_{type(exc).__name__}"]}

        home_out, away_out = await asyncio.gather(
            _one("home", ts_home, as_home),
            _one("away", ts_away, as_away),
        )

        # Determine pre-match flag: any non-finished status is pre-match.
        status_short = (
            ((fx_raw.get("fixture") or {}).get("status") or {}).get("short")
            or match_doc.get("status_short")
            or ""
        ).upper()
        is_pre_match = status_short not in ("FT", "AET", "PEN", "1H", "2H", "HT", "ET", "P", "LIVE")

        # The current fixture has its own corners only when the match
        # has been played at least partially. Pre-match this is always
        # ``False`` and the absence MUST NOT be treated as an error.
        current_corners = match_doc.get("corners") or {}
        current_fixture_corners_available = bool(
            isinstance(current_corners, dict) and current_corners.get("available")
        )

        profile = build_corners_profile(
            home_team_id=ts_home or as_home,
            home_team_name=home.get("name"),
            home_history=home_out.get("history") or [],
            away_team_id=ts_away or as_away,
            away_team_name=away.get("name"),
            away_history=away_out.get("history") or [],
            is_pre_match=is_pre_match,
            current_fixture_corners_available=current_fixture_corners_available,
            min_sample=5,
            provider=home_out.get("source") or away_out.get("source") or "thestatsapi",
        )
        # Surface provider-level reason codes too (TS_NO_RECENT_MATCH_IDS, etc.).
        extra_rc = (home_out.get("reason_codes") or []) + (away_out.get("reason_codes") or [])
        if extra_rc:
            seen = set(profile["reason_codes"])
            for code in extra_rc:
                if code and code not in seen:
                    profile["reason_codes"].append(code)
                    seen.add(code)

        # Persist in Mongo and mutate in-memory.
        try:
            await db.matches.update_one(
                {"match_id": fid},
                {"$set": {"corners_profile": profile}},
            )
        except Exception as exc:
            log.debug("[corners_profile_bg] mongo persist failed for %s: %s",
                      fid, exc)
        match_doc["corners_profile"] = profile

        log.info(
            "[corners_profile_bg] fixture=%s status=%s expected=%s home_n=%d away_n=%d blocked=%s",
            fid, profile["status"], profile["expected_corners"],
            profile["home"]["sample_size"], profile["away"]["sample_size"],
            profile["picks_blocked"],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[corners_profile_bg] crashed for fixture=%s: %s", fid, exc)


async def _enrich_football(client: httpx.AsyncClient, db, fx_raw: dict, is_live: bool, deep: bool) -> dict | None:
    try:
        fid = fx_raw["fixture"]["id"]
        lid = fx_raw["league"]["id"]
        season = fx_raw["league"]["season"]
        home = fx_raw["teams"]["home"]
        away = fx_raw["teams"]["away"]
        kickoff = fx_raw["fixture"]["date"]
        venue = (fx_raw.get("fixture", {}).get("venue") or {}).get("name")

        # ── Odds fetch cascade — extracted to _ingestion_helpers ────
        # See `_ingestion_helpers/football_odds_cascade.py` for the
        # full provider order. Behavioural parity is enforced; the
        # helper returns the same tuple shape the inline block used to
        # compute, and stamps ``_odds_source`` on ``norm_odds``.
        league_name = (fx_raw.get("league") or {}).get("name")
        odds_resp, norm_odds, odds_source = await _fetch_football_odds_with_fallback(
            client, db, fx_raw,
            fid=fid, home=home, away=away,
            kickoff=kickoff, league_name=league_name,
        )
        try:
            stand_resp = await af.standings(client, lid, db=db)
        except Exception as e:
            log.warning("standings failed for league %s: %s", lid, e)
            stand_resp = []

        stats_h, stats_a, h2h, inj_h, inj_a = {}, {}, [], [], []
        stats_h_source = stats_a_source = "missing"  # F84.a — audit
        # F94.2 / regression fix — initialise h2h_source OUTSIDE the
        # `if deep:` branch. The previous behaviour ONLY assigned it
        # inside the deep-enrichment path, but the `match_doc.setdefault
        # ("_provenance_h2h", {"source": h2h_source})` line below runs
        # unconditionally. Live ingestion calls `_enrich_football(...,
        # deep=False)` so `h2h_source` stayed unbound → `UnboundLocalError`
        # → enrichment raises → the live match is silently dropped → the
        # "EN CURSO AHORA" counter stays at 0 even when API-Sports returns
        # the fixture (e.g. Iran vs New Zealand / FIFA World Cup).
        h2h_source = "missing"
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
            # para auditoría en ``_provenance_h2h`` (inicializado arriba
            # fuera del `if deep:` para que el camino live también lo
            # tenga definido — ver fix F94.2).
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
            try:
                inj_h = await af.injuries(client, home["id"], db=db)
            except Exception:
                pass
            try:
                inj_a = await af.injuries(client, away["id"], db=db)
            except Exception:
                pass
            # P2A — pull last-15 fixtures per team for the historical goal
            # profile (under_3_5_rate, team_exceeded_2_goals_rate, etc.).
            # Cached 12h per (team, season). 15 games gives a robust sample
            # for under-tendency detection (case Atlético-MG style).
            try:
                recent_h_raw = await af.fixtures_last_n(
                    client, home["id"], n=15, season=season, db=db,
                )
            except Exception:
                pass
            try:
                recent_a_raw = await af.fixtures_last_n(
                    client, away["id"], n=15, season=season, db=db,
                )
            except Exception:
                pass

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
        # Phase F86 — H2H Decision Policy: clasifica si la muestra H2H
        # tiene tamaño suficiente Y partidos en los últimos 12 meses
        # como para influir en la decisión. Si no, queda solo como
        # contexto narrativo con warning visible en la UI.
        try:
            from .football_h2h_decision_policy import build_h2h_decision
            classified, decision = build_h2h_decision(match_doc)
            match_doc["h2h_context"]  = classified
            match_doc["h2h_decision"] = decision
            log.info(
                "[h2h_decision] fixture=%s sample_total=%d sample_recent=%d "
                "decision_useful=%s applied=%s markets=%s",
                fid,
                classified.get("sample_size_total"),
                classified.get("sample_size_recent"),
                classified.get("decision_useful"),
                decision.get("applied"),
                list((decision.get("points_by_market") or {}).keys()),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[h2h_decision] policy failed for %s: %s", fid, exc)
        # Phase F85 — xG recent averages dispatched in background.
        # NEVER blocks the ingestor. On completion, persists into
        # ``match_doc.xg_recent_averages`` (status PENDING until then).
        try:
            match_doc.setdefault("xg_recent_averages", {
                "available":    False,
                "status":       "PENDING_BACKGROUND_ENRICHMENT",
                "reason_codes": ["XG_RECENT_BACKGROUND_DEFERRED"],
            })
            asyncio.create_task(
                _schedule_xg_recent_background(match_doc, fid, db),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[xg_recent_bg] schedule failed for %s: %s", fid, exc)

        # FIX-4 — Pre-match Corners Profile background task.
        # Computes L1/L5/L15 corners-for / corners-against per team
        # plus momentum + expected corners. Runs independently of
        # corners_provider. NEVER blocks the ingestor.
        try:
            match_doc.setdefault("corners_profile", {
                "status":       "PENDING",
                "reason_codes": ["CORNERS_PROFILE_BACKGROUND_DEFERRED"],
            })
            asyncio.create_task(
                _schedule_corners_profile_background(match_doc, fid, db, fx_raw),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[corners_profile_bg] schedule failed for %s: %s", fid, exc)

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
        # F84.e — Stamp odds audit (thestatsapi | api_sports_fallback |
        #         thestatsapi_late | no_odds).
        match_doc.setdefault("_provenance_odds", {"source": odds_source})
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
            try:
                stats_h = await aps.team_statistics(sport, client, home.get("id"), lid, db=db)
            except Exception:
                pass
            try:
                stats_a = await aps.team_statistics(sport, client, away.get("id"), lid, db=db)
            except Exception:
                pass
            try:
                h2h = await aps.head_to_head(
                    sport, client, home.get("id"), away.get("id"), limit=5, db=db,
                )
            except Exception:
                pass

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
