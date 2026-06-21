"""Sprint-D9.2 Block B — Real expected-goals client (multi-source cascade).

Sources (in priority order)
---------------------------
1. **Understat** — HTML JSON-embedded payload, Top-5 leagues since 2014.
   Most accurate xG estimator publicly available. Stable URL contract:
   ``https://understat.com/team/{team_slug}/{year}``.
2. **FBref** — HTML table scrape, broader competition coverage but more
   Cloudflare bumps. URL: ``https://fbref.com/en/squads/{fbref_id}/...``.
3. **footystats** — Public team stats page with xG since 2021. URL:
   ``https://footystats.org/clubs/{team-slug}``.
4. **TheStatsAPI** — JSON endpoint behind the user's existing
   ``THESTATSAPI_KEY``. Coverage is partial for xG (some leagues only)
   but it's our last-resort because it doesn't go through Scrape.do.

Public contract
---------------
:func:`get_team_xg_history`
    ``async fn(team_name, *, league=None, season=None,
              db=None, transport=None) -> dict``
    Returns::

        {
          "available":    bool,
          "source":       "understat" | "fbref" | "footystats" | "thestatsapi" | "cache",
          "team_name":    str,
          "matches":      [{"date": ISO, "opponent": str, "xg_for": float,
                              "xg_against": float, "goals_for": int,
                              "goals_against": int, "competition": str}, ...],
          "fetched_at":   ISO,
          "from_cache":   bool,
          "reason_code":  str,
          "tried_sources": [list of sources we hit],
        }

:func:`compute_xg_features_l15`
    ``fn(history_matches) -> dict`` (pure helper used by the residual model).
    Returns::

        {
          "xg_l15_mean":        float,
          "xg_l15_std":         float,
          "xg_l15_dispersion":  float,    # std / mean  (CV)
          "xga_l15_mean":       float,
          "n_samples":          int,
          "sample_window":      "last_15_all_competitions",
        }

Strictly observe-only. xG features feed the residual model audit; they
never override DC predictions in production.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import unicodedata
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
SOURCE_LABEL                = "football_xg_real"
MONGO_COLLECTION            = "football_team_xg_history"
DEFAULT_CACHE_TTL_SECONDS   = 24 * 60 * 60          # 24h
DEFAULT_HISTORY_WINDOW      = 15

RC_FOUND_UNDERSTAT          = "XG_FOUND_UNDERSTAT"
RC_FOUND_FBREF              = "XG_FOUND_FBREF"
RC_FOUND_FOOTYSTATS         = "XG_FOUND_FOOTYSTATS"
RC_FOUND_THESTATSAPI        = "XG_FOUND_THESTATSAPI"
RC_FROM_CACHE               = "XG_FROM_CACHE"
RC_ALL_SOURCES_FAILED       = "XG_ALL_SOURCES_FAILED"
RC_INSUFFICIENT_SAMPLE      = "XG_INSUFFICIENT_SAMPLE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_name(s: str) -> str:
    if not isinstance(s, str):
        return ""
    nf = unicodedata.normalize("NFD", s)
    out = "".join(c for c in nf if unicodedata.category(c) != "Mn").lower().strip()
    out = re.sub(r"[^a-z0-9 ]+", "", out)
    out = re.sub(r"\s+", " ", out)
    return out


def _team_slug_understat(name: str) -> str:
    """Translate canonical team name into Understat's slug.

    Understat uses a stripped Title-Case slug joined by ``+`` (e.g.
    "Manchester United" → "Manchester_United"). We approximate that
    here; production usage should pass a manually-curated slug for
    edge cases ("Bournemouth" vs "AFC Bournemouth", etc.).
    """
    n = _norm_name(name)
    # Drop common suffixes that Understat omits.
    n = re.sub(r"\b(fc|cf|sc|ac|afc)\b", "", n).strip()
    n = re.sub(r"\s+", "_", n)
    # Capitalise each token to match Understat slug case.
    return "_".join(t.capitalize() for t in n.split("_") if t)


# ─────────────────────────────────────────────────────────────────────
# Understat — HTML JSON-embedded
# ─────────────────────────────────────────────────────────────────────
UNDERSTAT_BASE = "https://understat.com/team"
_RX_UNDERSTAT_DATES = re.compile(
    r"datesData\s*=\s*JSON\.parse\(\s*'((?:[^'\\]|\\.)*)'\s*\)",
    re.DOTALL,
)


def _decode_jsonp(escaped: str) -> Any:
    """Understat embeds JSON like ``\\x22team\\x22:\\x22Arsenal\\x22``.
    Replace the ``\\xHH`` escapes with the literal chars then ``json.loads``.
    """
    decoded = re.sub(
        r"\\x([0-9A-Fa-f]{2})",
        lambda m: chr(int(m.group(1), 16)),
        escaped,
    )
    # Also handle \\u escapes left by Understat.
    decoded = decoded.encode("utf-8").decode("unicode_escape")
    return json.loads(decoded)


def parse_understat_team_page(html: str, *, canonical_team: str) -> list[dict]:
    """Pure parser. Pass an Understat team-page HTML, get back the
    canonical match rows. Returns ``[]`` when the embed isn't found.
    """
    if not isinstance(html, str) or not html:
        return []
    m = _RX_UNDERSTAT_DATES.search(html)
    if not m:
        return []
    try:
        data = _decode_jsonp(m.group(1))
    except (ValueError, TypeError) as exc:
        log.info("[xg.understat] decode failed: %s", exc)
        return []
    if not isinstance(data, list):
        return []
    rows: list[dict] = []
    canon = _norm_name(canonical_team)
    for d in data:
        if not isinstance(d, dict):
            continue
        # Understat per-match block schema:
        # {"datetime":"2024-08-17 14:00:00","h":{"id":..., "title":"Arsenal"},
        #  "a":{"id":..., "title":"Wolves"}, "goals":{"h":"2","a":"0"},
        #  "xG":{"h":"1.51","a":"0.32"}, "league_id":..., "id":...,
        #  "result":"H","forecast":{"w":"...","d":"...","l":"..."} }
        h_block = d.get("h") or {}
        a_block = d.get("a") or {}
        h_name = h_block.get("title") or h_block.get("name") or ""
        a_name = a_block.get("title") or a_block.get("name") or ""
        is_home = _norm_name(h_name) == canon
        is_away = _norm_name(a_name) == canon
        if not (is_home or is_away):
            continue
        xg_block = d.get("xG") or {}
        goals    = d.get("goals") or {}
        xg_for, xg_against, gf, ga, opponent = (None, None, None, None, None)
        if is_home:
            xg_for     = _safe_float(xg_block.get("h"))
            xg_against = _safe_float(xg_block.get("a"))
            gf         = _safe_float(goals.get("h"))
            ga         = _safe_float(goals.get("a"))
            opponent   = a_name
        else:
            xg_for     = _safe_float(xg_block.get("a"))
            xg_against = _safe_float(xg_block.get("h"))
            gf         = _safe_float(goals.get("a"))
            ga         = _safe_float(goals.get("h"))
            opponent   = h_name
        rows.append({
            "date":           d.get("datetime"),
            "opponent":       opponent,
            "venue":          "home" if is_home else "away",
            "xg_for":         xg_for,
            "xg_against":     xg_against,
            "goals_for":      int(gf) if gf is not None else None,
            "goals_against":  int(ga) if ga is not None else None,
            "competition":    f"understat_league_{d.get('league_id')}",
            "source":         "understat",
        })
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows


# ─────────────────────────────────────────────────────────────────────
# FBref — HTML table scrape (xG_for / xG_against columns)
# ─────────────────────────────────────────────────────────────────────
_RX_FBREF_MATCHLOG = re.compile(
    r'<tr[^>]*data-row="\d+"[^>]*>(.*?)</tr>',
    re.DOTALL,
)
_RX_FBREF_CELL = re.compile(
    r'<(?:th|td)[^>]*data-stat="([^"]+)"[^>]*>(.*?)</(?:th|td)>',
    re.DOTALL,
)
_RX_HTML_TAGS = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _RX_HTML_TAGS.sub("", s).strip()


def parse_fbref_matchlog(html: str) -> list[dict]:
    """Pure parser for the FBref "Match Log" table. Expects columns
    ``date``, ``opponent``, ``xg_for``, ``xg_against``, ``goals_for``,
    ``goals_against``, ``venue``."""
    if not isinstance(html, str) or not html:
        return []
    rows: list[dict] = []
    for raw_tr in _RX_FBREF_MATCHLOG.findall(html):
        cells = {}
        for stat, value in _RX_FBREF_CELL.findall(raw_tr):
            cells[stat] = _strip_html(value)
        if not cells.get("date"):
            continue
        xg_for     = _safe_float(cells.get("xg_for"))
        xg_against = _safe_float(cells.get("xg_against"))
        # Skip rows without xG at all (older seasons / non-tracked comps).
        if xg_for is None and xg_against is None:
            continue
        rows.append({
            "date":           cells.get("date"),
            "opponent":       cells.get("opponent"),
            "venue":          (cells.get("venue") or "").lower(),
            "xg_for":         xg_for,
            "xg_against":     xg_against,
            "goals_for":      int(cells["goals_for"]) if cells.get("goals_for", "").isdigit() else None,
            "goals_against":  int(cells["goals_against"]) if cells.get("goals_against", "").isdigit() else None,
            "competition":    cells.get("comp") or "fbref",
            "source":         "fbref",
        })
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows


# ─────────────────────────────────────────────────────────────────────
# Footystats — public team-stats page
# ─────────────────────────────────────────────────────────────────────
_RX_FOOTYSTATS_MATCHES = re.compile(
    r'<div\s+class="match-row[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
    re.DOTALL,
)


def parse_footystats_team_page(html: str, *, canonical_team: str) -> list[dict]:
    """Pure parser for footystats team-stats page.

    Footystats serves an HTML table with per-match xG. The exact CSS
    class names drift; we use a tolerant heuristic that scans for rows
    containing both ``data-xg`` attributes and the opponent name. If
    nothing matches we return ``[]`` (the caller falls back to the next
    source).
    """
    if not isinstance(html, str) or not html:
        return []
    rows: list[dict] = []
    # Footystats publishes ``<td data-xg="1.34">`` cells in the per-match
    # log. We harvest them in pairs (for / against) per row.
    for tr_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL):
        tr = tr_match.group(1)
        xgs = re.findall(r'data-xg="([\d.]+)"', tr)
        if len(xgs) < 2:
            continue
        # Date in the row.
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", tr)
        # Opponent — best-effort.
        opp_match = re.search(r'class="[^"]*team-name[^"]*"[^>]*>([^<]+)</', tr)
        rows.append({
            "date":          date_match.group(1) if date_match else None,
            "opponent":      (opp_match.group(1).strip() if opp_match else None),
            "venue":         None,
            "xg_for":        _safe_float(xgs[0]),
            "xg_against":    _safe_float(xgs[1]),
            "goals_for":     None,
            "goals_against": None,
            "competition":   "footystats",
            "source":        "footystats",
        })
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows


# ─────────────────────────────────────────────────────────────────────
# Pure helpers — feature engineering
# ─────────────────────────────────────────────────────────────────────
def compute_xg_features_l15(matches: list[dict],
                              *, window: int = DEFAULT_HISTORY_WINDOW) -> dict:
    """Pure helper consumed by the residual model. Returns mean / std /
    coefficient-of-variation across the LAST ``window`` matches (most
    recent first). Missing xG values are skipped.
    """
    xgs_for     = [m["xg_for"]     for m in matches[:window]
                    if isinstance(m.get("xg_for"),     (int, float))]
    xgs_against = [m["xg_against"] for m in matches[:window]
                    if isinstance(m.get("xg_against"), (int, float))]
    if not xgs_for:
        return {
            "xg_l15_mean":       None,
            "xg_l15_std":        None,
            "xg_l15_dispersion": None,
            "xga_l15_mean":      None,
            "n_samples":         len(matches[:window]),
            "sample_window":     "last_15_all_competitions",
        }
    mean = statistics.fmean(xgs_for)
    std  = statistics.pstdev(xgs_for) if len(xgs_for) > 1 else 0.0
    cv   = (std / mean) if mean > 0 else None
    return {
        "xg_l15_mean":       round(mean, 4),
        "xg_l15_std":        round(std, 4),
        "xg_l15_dispersion": round(cv, 4) if cv is not None else None,
        "xga_l15_mean":      (round(statistics.fmean(xgs_against), 4)
                                if xgs_against else None),
        "n_samples":         len(xgs_for),
        "sample_window":     "last_15_all_competitions",
    }


# ─────────────────────────────────────────────────────────────────────
# Transports (Scrape.do based, fail-soft)
# ─────────────────────────────────────────────────────────────────────
async def _default_scrape_transport(url: str, *, timeout: float = 35.0) -> Optional[str]:
    """Production transport via Scrape.do. Returns ``html|None``."""
    try:
        from ..scrape_do_client import fetch_via_scrapedo_result, is_enabled
    except Exception:  # noqa: BLE001
        return None
    if not is_enabled():
        return None
    try:
        res = await fetch_via_scrapedo_result(url, timeout=timeout,
                                                render=False, geo="us")
    except Exception as exc:  # noqa: BLE001
        log.info("[xg.transport] raised: %s", exc)
        return None
    if not res.get("ok"):
        return None
    return res.get("html") or ""


# ─────────────────────────────────────────────────────────────────────
# Mongo cache
# ─────────────────────────────────────────────────────────────────────
async def ensure_indexes(db: Any) -> dict:
    if db is None:
        return {"created": [], "skipped": "no_db"}
    coll = getattr(db, MONGO_COLLECTION, None)
    if coll is None:
        try:
            coll = db[MONGO_COLLECTION]
        except Exception:  # noqa: BLE001
            return {"created": [], "skipped": "no_collection"}
    created: list[str] = []
    try:
        await coll.create_index(
            [("team_norm", 1), ("league", 1), ("season", 1)],
            unique=True, name="ix_team_league_season",
        )
        created.append("ix_team_league_season")
    except Exception as exc:  # noqa: BLE001
        log.warning("[xg.cache] ix_team_league_season failed: %s", exc)
    try:
        await coll.create_index(
            "fetched_at", expireAfterSeconds=7 * 24 * 3600,
            name="ttl_fetched_at",
        )
        created.append("ttl_fetched_at")
    except Exception as exc:  # noqa: BLE001
        log.warning("[xg.cache] ttl_fetched_at failed: %s", exc)
    return {"created": created}


async def _get_cached(*, db, team_norm, league, season, ttl_seconds):
    if db is None:
        return None
    coll = getattr(db, MONGO_COLLECTION, None)
    if coll is None:
        try:
            coll = db[MONGO_COLLECTION]
        except Exception:  # noqa: BLE001
            return None
    try:
        doc = await coll.find_one({"team_norm": team_norm,
                                     "league": league, "season": season})
    except Exception as exc:  # noqa: BLE001
        log.info("[xg.cache] lookup failed: %s", exc)
        return None
    if not doc:
        return None
    fa = doc.get("fetched_at")
    if isinstance(fa, str):
        try:
            fa = datetime.fromisoformat(fa.replace("Z", "+00:00"))
        except ValueError:
            fa = None
    if not isinstance(fa, datetime):
        return None
    if fa.tzinfo is None:
        fa = fa.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - fa).total_seconds()
    if age > ttl_seconds:
        return None
    return doc


async def _persist_cache(*, db, doc):
    if db is None:
        return False
    coll = getattr(db, MONGO_COLLECTION, None)
    if coll is None:
        try:
            coll = db[MONGO_COLLECTION]
        except Exception:  # noqa: BLE001
            return False
    try:
        await coll.update_one(
            {"team_norm": doc["team_norm"], "league": doc.get("league"),
              "season": doc.get("season")},
            {"$set": doc}, upsert=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("[xg.cache] persist failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Per-source fetchers (each returns matches list or None)
# ─────────────────────────────────────────────────────────────────────
async def _fetch_understat(team_name: str, *, season: Optional[int],
                             transport: Callable[..., Awaitable[Optional[str]]]) -> list[dict]:
    slug = _team_slug_understat(team_name)
    if not slug:
        return []
    year = season if season is not None else datetime.now(timezone.utc).year
    url = f"{UNDERSTAT_BASE}/{slug}/{int(year)}"
    html = await transport(url)
    if not html:
        # Retry one year back — Understat splits seasons by start year.
        url_prev = f"{UNDERSTAT_BASE}/{slug}/{int(year) - 1}"
        html = await transport(url_prev)
    if not html:
        return []
    return parse_understat_team_page(html, canonical_team=team_name)


async def _fetch_fbref(team_name: str, *, fbref_id: Optional[str],
                        transport: Callable[..., Awaitable[Optional[str]]]) -> list[dict]:
    """FBref needs the team_id slug (e.g. ``18bb7c10/Arsenal``). When
    callers don't have it we skip — FBref's search endpoint is
    Cloudflare-walled and not worth chasing in this sprint.
    """
    if not fbref_id:
        return []
    url = f"https://fbref.com/en/squads/{fbref_id}/matchlogs/all_comps/schedule/"
    html = await transport(url)
    if not html:
        return []
    return parse_fbref_matchlog(html)


async def _fetch_footystats(team_name: str,
                              transport: Callable[..., Awaitable[Optional[str]]]) -> list[dict]:
    slug = _norm_name(team_name).replace(" ", "-")
    if not slug:
        return []
    url = f"https://footystats.org/clubs/{slug}"
    html = await transport(url)
    if not html:
        return []
    return parse_footystats_team_page(html, canonical_team=team_name)


async def _fetch_thestatsapi(team_name: str, *,
                               thestatsapi_id: Optional[str | int]) -> list[dict]:
    """TheStatsAPI fallback. xG coverage is partial — this is best-effort."""
    if not thestatsapi_id:
        return []
    try:
        import os
        import httpx
    except Exception:  # noqa: BLE001
        return []
    key = os.environ.get("THESTATSAPI_KEY")
    if not key:
        return []
    url = ("https://api.thestatsapi.com/v1/teams/"
            f"{thestatsapi_id}/recent-matches")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers={"x-api-key": key})
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.info("[xg.thestatsapi] raised: %s", exc)
        return []
    matches = data.get("matches") if isinstance(data, dict) else None
    if not isinstance(matches, list):
        return []
    rows: list[dict] = []
    canon = _norm_name(team_name)
    for m in matches:
        if not isinstance(m, dict):
            continue
        h_name = m.get("home_team") or m.get("home", {}).get("name") or ""
        a_name = m.get("away_team") or m.get("away", {}).get("name") or ""
        is_home = _norm_name(h_name) == canon
        is_away = _norm_name(a_name) == canon
        if not (is_home or is_away):
            continue
        xg = m.get("xg") or {}
        xg_for     = _safe_float(xg.get("home" if is_home else "away"))
        xg_against = _safe_float(xg.get("away" if is_home else "home"))
        if xg_for is None and xg_against is None:
            continue
        rows.append({
            "date":          m.get("kickoff") or m.get("date"),
            "opponent":      a_name if is_home else h_name,
            "venue":         "home" if is_home else "away",
            "xg_for":        xg_for,
            "xg_against":    xg_against,
            "goals_for":     None,
            "goals_against": None,
            "competition":   m.get("competition") or "thestatsapi",
            "source":        "thestatsapi",
        })
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows


# ─────────────────────────────────────────────────────────────────────
# Public cascade entry point
# ─────────────────────────────────────────────────────────────────────
async def get_team_xg_history(
    team_name: str,
    *,
    league: Optional[str] = None,
    season: Optional[int] = None,
    fbref_id: Optional[str] = None,
    thestatsapi_id: Optional[str | int] = None,
    db: Any = None,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    transport: Optional[Callable[..., Awaitable[Optional[str]]]] = None,
    thestatsapi_fetcher: Optional[Callable[..., Awaitable[list[dict]]]] = None,
    force_refresh: bool = False,
    sources_order: tuple[str, ...] = ("understat", "fbref", "footystats", "thestatsapi"),
    min_samples: int = 5,
) -> dict:
    """Walk the source cascade until we get at least ``min_samples`` matches
    with xG. Always returns a dict; never raises.
    """
    team_norm = _norm_name(team_name)
    if not team_norm:
        return {
            "available": False,
            "source": None,
            "team_name": team_name,
            "matches": [],
            "reason_code": "XG_BAD_TEAM_NAME",
            "fetched_at": _now_iso(),
            "from_cache": False,
            "tried_sources": [],
        }

    # ── Cache lookup ────────────────────────────────────────────────
    if not force_refresh:
        cached = await _get_cached(db=db, team_norm=team_norm,
                                     league=league, season=season,
                                     ttl_seconds=cache_ttl_seconds)
        if cached and cached.get("matches"):
            return {
                "available":     True,
                "source":        "cache",
                "team_name":     cached.get("team_name") or team_name,
                "matches":       cached["matches"],
                "reason_code":   RC_FROM_CACHE,
                "fetched_at":    cached.get("fetched_at"),
                "from_cache":    True,
                "tried_sources": ["cache"],
                "underlying_source": cached.get("underlying_source"),
            }

    # ── Sprint-D9 Fix 2a: offline_seed FIRST (antes que online sources). ──
    # Ahorra rate-limits / créditos Scrape.do cuando el seed ya tiene
    # cobertura suficiente. Esta consulta corre SIEMPRE (incluso con
    # force_refresh=True): la bandera force_refresh solo afecta a la
    # decisión de retornar inmediatamente del seed o no, pero el
    # seed_partial DEBE quedar disponible como fallback si los sources
    # online fallan después.
    seed_partial: list[dict] = []
    try:
        from .football_xg_offline_seed import get_offline_xg_history
        seed_res = await get_offline_xg_history(db, team_name, league=league)
        if seed_res and seed_res.get("matches"):
            ms_seed = seed_res["matches"]
            # Short-circuit: solo si NO se pidió force_refresh y el seed
            # tiene cobertura completa, devolvemos directo (ahorra
            # llamadas online innecesarias).
            if not force_refresh and len(ms_seed) >= min_samples:
                cache_doc = {
                    "team_norm":         team_norm,
                    "team_name":         seed_res.get("team_name") or team_name,
                    "league":            league,
                    "season":            season,
                    "matches":           ms_seed,
                    "fetched_at":        datetime.now(timezone.utc),
                    "underlying_source": "offline_seed",
                    "tried_sources":     ["offline_seed"],
                }
                await _persist_cache(db=db, doc=cache_doc)
                return {
                    "available":         True,
                    "source":            "offline_seed",
                    "team_name":         seed_res.get("team_name") or team_name,
                    "matches":           ms_seed,
                    "reason_code":       seed_res.get("reason_code")
                                            or "XG_OFFLINE_SEED_HIT",
                    "fetched_at":        _now_iso(),
                    "from_cache":        False,
                    "tried_sources":     ["offline_seed"],
                    "underlying_source": "offline_seed",
                }
            # Seed disponible pero (force_refresh OR cobertura parcial)
            # → guardarlo como fallback "best so far".
            seed_partial = ms_seed
    except Exception as _exc_seed:  # noqa: BLE001
        log.info("[xg.cascade] offline_seed primary lookup failed: %s", _exc_seed)

    transport = transport or _default_scrape_transport
    thestatsapi_fetcher = thestatsapi_fetcher or _fetch_thestatsapi

    tried: list[str] = []
    chosen: Optional[str] = None
    matches: list[dict] = []

    for src in sources_order:
        tried.append(src)
        try:
            if src == "understat":
                ms = await _fetch_understat(team_name, season=season,
                                              transport=transport)
            elif src == "fbref":
                ms = await _fetch_fbref(team_name, fbref_id=fbref_id,
                                          transport=transport)
            elif src == "footystats":
                ms = await _fetch_footystats(team_name, transport=transport)
            elif src == "thestatsapi":
                ms = await thestatsapi_fetcher(team_name,
                                                  thestatsapi_id=thestatsapi_id)
            else:
                continue
        except Exception as exc:  # noqa: BLE001
            log.info("[xg.cascade] %s raised: %s", src, exc)
            ms = []
        if ms and len(ms) >= min_samples:
            chosen = src
            matches = ms
            break
        if ms and not matches:
            # Keep best partial as fallback.
            matches = ms
            chosen  = src

    if not matches:
        # ── Sprint-D9 Fix 2a: usar seed_partial guardado al inicio del cascade. ──
        # No re-consultamos offline_seed (ya lo hicimos arriba). Si el
        # seed tenía matches pero menos de min_samples, los servimos
        # ahora como "best available".
        if seed_partial:
            cache_doc = {
                "team_norm":         team_norm,
                "team_name":         team_name,
                "league":            league,
                "season":            season,
                "matches":           seed_partial,
                "fetched_at":        datetime.now(timezone.utc),
                "underlying_source": "offline_seed",
                "tried_sources":     tried + ["offline_seed"],
            }
            await _persist_cache(db=db, doc=cache_doc)
            return {
                "available":         len(seed_partial) >= min_samples,
                "source":            "offline_seed",
                "team_name":         team_name,
                "matches":           seed_partial,
                "reason_code":       (
                    "XG_OFFLINE_SEED_HIT"
                    if len(seed_partial) >= min_samples
                    else RC_INSUFFICIENT_SAMPLE
                ),
                "fetched_at":        _now_iso(),
                "from_cache":        False,
                "tried_sources":     tried + ["offline_seed"],
                "underlying_source": "offline_seed",
            }

        return {
            "available":     False,
            "source":        None,
            "team_name":     team_name,
            "matches":       [],
            "reason_code":   RC_ALL_SOURCES_FAILED,
            "fetched_at":    _now_iso(),
            "from_cache":    False,
            "tried_sources": tried,
        }

    rc = {
        "understat":    RC_FOUND_UNDERSTAT,
        "fbref":        RC_FOUND_FBREF,
        "footystats":   RC_FOUND_FOOTYSTATS,
        "thestatsapi":  RC_FOUND_THESTATSAPI,
    }.get(chosen or "", RC_ALL_SOURCES_FAILED)

    cache_doc = {
        "team_norm":         team_norm,
        "team_name":         team_name,
        "league":            league,
        "season":            season,
        "matches":           matches,
        "fetched_at":        datetime.now(timezone.utc),
        "underlying_source": chosen,
        "tried_sources":     tried,
    }
    await _persist_cache(db=db, doc=cache_doc)

    # ── Sprint-D9 Fix 2b: promover el fetch online al offline_seed con
    # merge inteligente (UNION + dedupe por date+opponent, conserva el
    # conteo más alto). Esto hace que el seed crezca de forma orgánica
    # con datos frescos online — útil especialmente para selecciones
    # nacionales que no están en el dataset histórico inicial.
    try:
        if matches and chosen and chosen != "offline_seed":
            from .football_xg_offline_seed import promote_online_matches_to_seed
            promo = await promote_online_matches_to_seed(
                db, team_name=team_name, league=league,
                matches=matches, underlying_source=chosen,
            )
            log.info("[xg.cascade] promote→seed: %s", promo.get("action"))
    except Exception as _exc_promote:  # noqa: BLE001
        log.info("[xg.cascade] promote→seed failed (non-fatal): %s",
                  _exc_promote)

    return {
        "available":         len(matches) >= min_samples,
        "source":            chosen,
        "team_name":         team_name,
        "matches":           matches,
        "reason_code":       rc if len(matches) >= min_samples else RC_INSUFFICIENT_SAMPLE,
        "fetched_at":        _now_iso(),
        "from_cache":        False,
        "tried_sources":     tried,
        "underlying_source": chosen,
    }


__all__ = [
    "SOURCE_LABEL", "MONGO_COLLECTION", "DEFAULT_CACHE_TTL_SECONDS",
    "DEFAULT_HISTORY_WINDOW",
    "RC_FOUND_UNDERSTAT", "RC_FOUND_FBREF",
    "RC_FOUND_FOOTYSTATS", "RC_FOUND_THESTATSAPI",
    "RC_FROM_CACHE", "RC_ALL_SOURCES_FAILED", "RC_INSUFFICIENT_SAMPLE",
    "parse_understat_team_page", "parse_fbref_matchlog",
    "parse_footystats_team_page",
    "compute_xg_features_l15",
    "ensure_indexes", "get_team_xg_history",
]
