"""Understat scraper — pre-match xG / PPDA / shots enrichment.

Strategy
========
Understat changed their site in 2025: `/league/{X}/{year}` and `/team/{name}/{year}`
pages no longer embed JSON datasets in the HTML. The only page that still
embeds rich data is `/match/{match_id}`.

We therefore design the adapter around that single-source-of-truth:

    GET https://understat.com/match/{id}
        →  match_info  : aggregate xG, shots, PPDA, deep completions, probs
        →  shotsData   : per-shot detail with xG, xT, situation, body part…
        →  rostersData : XI + minutes + xG per player

The adapter is intentionally minimal: it does NOT try to do team-level
season aggregation (that would require dozens of GETs per team). Instead
it exposes:

    fetch_match(match_id)         → rich dict ready for enrichment
    enrich_match_dict(match_id)   → normalised payload safe to attach to
                                    `match._understat` in MongoDB
    find_match_id_by_teams(...)   → best-effort fuzzy lookup (web-scoped)

A Mongo-backed TTL cache (12h) keeps us under Understat's polite-use
threshold and protects production from hammering the site.

Provenance
==========
Every enriched payload carries `_provenance = "understat_v1"` so the
analyst engine can score Understat-derived xG higher than the internal
Poisson proxy. When Understat is unreachable or the team isn't in a
covered league (only EPL, La Liga, Bundesliga, Serie A, Ligue 1, RFPL),
the adapter returns `None` and downstream code stays on the Poisson
fallback.

NOTE: This adapter is intentionally LIBRARY-FREE (no `understat` PyPI
package) because that package downgrades aiohttp and breaks litellm.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

log = logging.getLogger("understat")

# ── HTTP layer ────────────────────────────────────────────────────────────────
_BASE = "https://understat.com"
_SESSION: Optional[requests.Session] = None
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT_SEC = 8
_REQUEST_GAP_SEC = 0.6  # polite throttle between sequential GETs
_last_request_ts: float = 0.0


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update(_DEFAULT_HEADERS)
        # Warm cookies (PHPSESSID, UID) with a homepage GET
        try:
            s.get(_BASE + "/", timeout=_TIMEOUT_SEC)
        except Exception as exc:
            log.debug("understat: homepage warm-up failed: %s", exc)
        _SESSION = s
    return _SESSION


def _throttled_get(url: str) -> Optional[requests.Response]:
    """GET with a polite minimum gap. Returns None on network failure."""
    global _last_request_ts
    elapsed = time.time() - _last_request_ts
    if elapsed < _REQUEST_GAP_SEC:
        time.sleep(_REQUEST_GAP_SEC - elapsed)
    try:
        r = _get_session().get(url, timeout=_TIMEOUT_SEC)
        _last_request_ts = time.time()
        return r
    except requests.RequestException as exc:
        log.warning("understat: GET %s failed: %s", url, exc)
        return None


# ── HTML → JSON dataset extraction ────────────────────────────────────────────
# Understat embeds datasets as:
#   var <name> = JSON.parse('\x7B...escaped JSON...\x7D');
# We decode them with a strict regex + unicode_escape codec.
_VAR_JSON_RE = re.compile(
    r"var\s+(\w+)\s*=\s*JSON\.parse\(\s*'([^']+)'\s*\)"
)


def _extract_datasets(html: str) -> dict[str, Any]:
    """Return all named JSON.parse(...) datasets found in the HTML."""
    out: dict[str, Any] = {}
    for name, encoded in _VAR_JSON_RE.findall(html or ""):
        try:
            decoded = encoded.encode("utf-8").decode("unicode_escape")
            out[name] = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log.debug("understat: failed to decode %s: %s", name, exc)
            continue
    return out


# ── Public API ────────────────────────────────────────────────────────────────
def fetch_match(match_id: int | str) -> Optional[dict[str, Any]]:
    """Fetch ALL datasets for a single Understat match page.

    Returns a dict with keys among: match_info, shotsData, rostersData,
    or None if the page does not exist / the request failed.
    """
    try:
        mid = int(match_id)
    except (TypeError, ValueError):
        return None

    r = _throttled_get(f"{_BASE}/match/{mid}")
    if not r or r.status_code != 200 or len(r.text) < 5_000:
        return None

    datasets = _extract_datasets(r.text)
    if not datasets.get("match_info"):
        return None

    return {
        "match_id":     mid,
        "match_info":   datasets.get("match_info") or {},
        "shotsData":    datasets.get("shotsData") or {"h": [], "a": []},
        "rostersData":  datasets.get("rostersData") or {},
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
    }


def enrich_match_dict(match_id: int | str) -> Optional[dict[str, Any]]:
    """Return a normalised payload safe to embed in `match._understat`.

    Schema:
      {
        "provenance":         "understat_v1",
        "understat_match_id": int,
        "league":             str,        # e.g. "EPL"
        "season":             str,        # e.g. "2024"
        "date":               str,        # ISO date
        "teams":              {"home": str, "away": str},
        "score":              {"home": int, "away": int},
        "xg":                 {"home": float, "away": float},
        "shots":              {"home": int, "away": int},
        "shots_on_target":    {"home": int, "away": int},
        "deep_completions":   {"home": int, "away": int},
        "ppda":               {"home": float, "away": float},
        "win_probability":    {"home": float, "draw": float, "away": float},
        "shots_detail_count": int,        # total shots with per-shot xG
        "fetched_at":         iso str,
      }
    """
    raw = fetch_match(match_id)
    if not raw:
        return None
    mi = raw["match_info"] or {}
    shots = raw["shotsData"] or {"h": [], "a": []}

    def _f(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _i(v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    return {
        "provenance":         "understat_v1",
        "understat_match_id": int(raw["match_id"]),
        "league":             mi.get("league"),
        "season":             mi.get("season"),
        "date":               mi.get("date"),
        "teams": {
            "home": mi.get("team_h"),
            "away": mi.get("team_a"),
        },
        "score": {
            "home": _i(mi.get("h_goals")),
            "away": _i(mi.get("a_goals")),
        },
        "xg": {
            "home": _f(mi.get("h_xg")),
            "away": _f(mi.get("a_xg")),
        },
        "shots": {
            "home": _i(mi.get("h_shot")),
            "away": _i(mi.get("a_shot")),
        },
        "shots_on_target": {
            "home": _i(mi.get("h_shotOnTarget")),
            "away": _i(mi.get("a_shotOnTarget")),
        },
        "deep_completions": {
            "home": _i(mi.get("h_deep")),
            "away": _i(mi.get("a_deep")),
        },
        "ppda": {
            "home": _f(mi.get("h_ppda")),
            "away": _f(mi.get("a_ppda")),
        },
        "win_probability": {
            "home": _f(mi.get("h_w")),
            "draw": _f(mi.get("h_d")),
            "away": _f(mi.get("h_l")),
        },
        "shots_detail_count": (len(shots.get("h", [])) + len(shots.get("a", []))),
        "fetched_at":         raw["fetched_at"],
    }


# ── Cache layer (Mongo-backed, 12h TTL) ───────────────────────────────────────
_CACHE_COLL = "understat_cache"
_CACHE_TTL_SEC = 12 * 60 * 60


async def fetch_match_cached(db, match_id: int | str) -> Optional[dict[str, Any]]:
    """Mongo-cached version of `enrich_match_dict`.

    The cache key is the Understat match_id. Entries older than 12h are
    refreshed transparently. Finished matches barely change so this TTL is
    safe; for live matches use `fetch_match` directly.
    """
    try:
        mid = int(match_id)
    except (TypeError, ValueError):
        return None

    coll = db[_CACHE_COLL]
    cached = await coll.find_one({"_id": mid})
    now = time.time()
    if cached and (now - cached.get("_ts", 0)) < _CACHE_TTL_SEC:
        # Drop the bookkeeping fields before returning
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    fresh = enrich_match_dict(mid)
    if fresh is None:
        return None

    doc = {**fresh, "_id": mid, "_ts": now}
    try:
        await coll.replace_one({"_id": mid}, doc, upsert=True)
    except Exception as exc:
        log.warning("understat: cache write failed for %s: %s", mid, exc)
    return fresh


# ── Aggregation helper ────────────────────────────────────────────────────────
def aggregate_team_form(matches: list[dict[str, Any]], team_name: str) -> Optional[dict[str, Any]]:
    """Aggregate enrichment payloads into a team-form summary.

    `matches` is a list of dicts produced by `enrich_match_dict()`.
    Returns avg xG / xGA / shots / PPDA over the matches where `team_name`
    appeared as home OR away.
    """
    if not matches or not team_name:
        return None

    rows: list[dict[str, Any]] = []
    for m in matches:
        teams = m.get("teams") or {}
        is_home = (teams.get("home") == team_name)
        is_away = (teams.get("away") == team_name)
        if not (is_home or is_away):
            continue
        side = "home" if is_home else "away"
        opp_side = "away" if is_home else "home"
        rows.append({
            "xg_for":     (m.get("xg") or {}).get(side),
            "xg_against": (m.get("xg") or {}).get(opp_side),
            "ppda":       (m.get("ppda") or {}).get(side),
            "shots_for":  (m.get("shots") or {}).get(side),
            "shots_against": (m.get("shots") or {}).get(opp_side),
            "goals_for":  (m.get("score") or {}).get(side),
            "goals_against": (m.get("score") or {}).get(opp_side),
            "date":       m.get("date"),
        })
    if not rows:
        return None

    def _avg(key: str) -> Optional[float]:
        vals = [r.get(key) for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "team":             team_name,
        "samples":          len(rows),
        "xg_for_avg":       _avg("xg_for"),
        "xg_against_avg":   _avg("xg_against"),
        "ppda_avg":         _avg("ppda"),
        "shots_for_avg":    _avg("shots_for"),
        "shots_against_avg": _avg("shots_against"),
        "goals_for_avg":    _avg("goals_for"),
        "goals_against_avg": _avg("goals_against"),
        "provenance":       "understat_v1",
        "computed_at":      datetime.now(timezone.utc).isoformat(),
    }


# ── Fuzzy linker — find Understat match_id by team names + date ───────────────
#
# Understat 2025 removed embedded league/team JSON pages, so we cannot
# enumerate matches directly anymore. Workaround: query search engines for
# `site:understat.com/match <home> <away>` and parse the result URLs.
# We try multiple engines in cascade (DDG Lite → Brave → StartPage) so a
# single source going down doesn't break the linker. Then we hydrate each
# candidate via fetch_match() and pick the one whose teams + date match
# the input.
#
# This is best-effort: only EPL / La Liga / Bundesliga / Serie A / Ligue 1
# / RFPL are covered by Understat, so other leagues will yield zero hits
# and the caller must fall back to the Poisson proxy.

_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}
# Search engines tried in order. Each engine returns a query-URL function.
# Order matters: cheaper / more reliable engines first. Bing + Mojeek were
# added in v2 to reduce the dependency on DDG which frequently returns
# HTTP 202 bot challenges from Kubernetes datacenter IPs.
_SEARCH_ENGINES = [
    ("bing",        lambda q: f"https://www.bing.com/search?q={q}"),
    ("brave",       lambda q: f"https://search.brave.com/search?q={q}"),
    ("mojeek",      lambda q: f"https://www.mojeek.com/search?q={q}"),
    ("ddg_lite",    lambda q: f"https://lite.duckduckgo.com/lite/?q={q}"),
    ("ddg_html",    lambda q: f"https://html.duckduckgo.com/html/?q={q}"),
    ("startpage",   lambda q: f"https://www.startpage.com/sp/search?query={q}"),
    ("yandex",      lambda q: f"https://yandex.com/search/?text={q}"),
]
_UNDERSTAT_ID_RE = re.compile(r"understat\.com/match/(\d+)")


# Team-name → Understat slug normalisation.
# Understat slugs use Title_Case_With_Underscores and never include diacritics
# or club suffixes. A manual override map fixes the worst mismatches between
# API-Sports and Understat naming.
_TEAM_SLUG_OVERRIDES = {
    "manchester united":       "Manchester_United",
    "manchester utd":          "Manchester_United",
    "man united":              "Manchester_United",
    "manchester city":         "Manchester_City",
    "man city":                "Manchester_City",
    "tottenham":               "Tottenham",
    "tottenham hotspur":       "Tottenham",
    "spurs":                   "Tottenham",
    "wolverhampton wanderers": "Wolverhampton_Wanderers",
    "wolves":                  "Wolverhampton_Wanderers",
    "newcastle":               "Newcastle_United",
    "newcastle united":        "Newcastle_United",
    "leicester":               "Leicester",
    "atletico madrid":         "Atletico_Madrid",
    "atlético madrid":         "Atletico_Madrid",
    "athletic bilbao":         "Athletic_Club",
    "athletic club":           "Athletic_Club",
    "real betis":              "Real_Betis",
    "rayo vallecano":          "Rayo_Vallecano",
    "real sociedad":           "Real_Sociedad",
    "real madrid":             "Real_Madrid",
    "celta vigo":              "Celta_Vigo",
    "bayern munich":           "Bayern_Munich",
    "bayern münchen":          "Bayern_Munich",
    "borussia dortmund":       "Borussia_Dortmund",
    "bvb":                     "Borussia_Dortmund",
    "borussia monchengladbach":"Borussia_M.Gladbach",
    "borussia mönchengladbach":"Borussia_M.Gladbach",
    "bayer leverkusen":        "Bayer_Leverkusen",
    "eintracht frankfurt":     "Eintracht_Frankfurt",
    "rb leipzig":              "RasenBallsport_Leipzig",
    "psg":                     "Paris_Saint_Germain",
    "paris saint germain":     "Paris_Saint_Germain",
    "paris saint-germain":     "Paris_Saint_Germain",
    "olympique marseille":     "Marseille",
    "olympique de marseille":  "Marseille",
    "olympique lyonnais":      "Lyon",
    "ol":                      "Lyon",
    "inter milan":             "Inter",
    "internazionale":          "Inter",
    "inter":                   "Inter",
    "ac milan":                "AC_Milan",
    "milan":                   "AC_Milan",
    "juventus":                "Juventus",
    "roma":                    "Roma",
    "as roma":                 "Roma",
    "ss lazio":                "Lazio",
    "lazio":                   "Lazio",
    "napoli":                  "Napoli",
    "ssc napoli":              "Napoli",
    "atalanta":                "Atalanta",
    "atalanta bc":             "Atalanta",
}


def _strip_diacritics(s: str) -> str:
    """Remove accents/diacritics for ASCII slug-friendly comparison."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _team_to_understat_slug(name: str) -> str:
    """Convert an API-Sports team name to an Understat URL slug.

    Strategy:
      1. Lookup in `_TEAM_SLUG_OVERRIDES` (canonical naming).
      2. Otherwise strip diacritics → drop sufijo (FC/CF/SC/AC/Calcio/Club) →
         replace spaces with `_` → Title-case each token.
    """
    if not name:
        return ""
    raw = name.strip().lower()
    if raw in _TEAM_SLUG_OVERRIDES:
        return _TEAM_SLUG_OVERRIDES[raw]
    s = _strip_diacritics(raw)
    # Drop common suffixes / prefixes
    for tok in [
        " fc", " cf", " sc", " ac", " bc", " sk", " ks", " ks ",
        " calcio", " club", " athletic", "afc ", "fc ", "ac ",
    ]:
        s = s.replace(tok, " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    parts = [p.capitalize() for p in s.split() if p]
    return "_".join(parts)


def _understat_team_url(team_slug: str, year: int) -> str:
    return f"{_BASE}/team/{team_slug}/{year}"


def _understat_team_data_url(team_slug: str, year: int) -> str:
    """Internal JSON endpoint that team.min.js calls via XHR.

    Returns `{"dates": [{"id": <match_id>, "h": {...}, "a": {...}, "xG":
    {...}, "datetime": "YYYY-MM-DD HH:MM:SS", ...}, ...]}`. Much more
    reliable than `/team/<slug>/<year>` HTML which now requires JS to render.
    """
    return f"{_BASE}/main/getTeamData/{team_slug}/{year}"


def _fetch_team_dates(team_slug: str, year: int) -> list[dict]:
    """GET the internal JSON list of a team's matches for a given season.

    Returns the `dates` array (empty on failure). Each item has at least:
      id, isResult, side, h:{id,title,short_title}, a:{...},
      goals:{h,a}, xG:{h,a}, datetime, forecast:{w,d,l}

    The endpoint `/main/getTeamData/<slug>/<year>` only responds to XHR
    calls (mirrors what `team.min.js` does via jQuery $.ajax). We therefore
    forward `X-Requested-With: XMLHttpRequest` and a season-page Referer.
    Calling without those headers yields a 404.
    """
    if not team_slug:
        return []
    url     = _understat_team_data_url(team_slug, year)
    referer = _understat_team_url(team_slug, year)

    # Throttle and forward XHR headers explicitly.
    global _last_request_ts
    elapsed = time.time() - _last_request_ts
    if elapsed < _REQUEST_GAP_SEC:
        time.sleep(_REQUEST_GAP_SEC - elapsed)
    try:
        r = _get_session().get(
            url,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          referer,
                "Accept":           "application/json, text/javascript, */*; q=0.01",
            },
            timeout=_TIMEOUT_SEC,
        )
        _last_request_ts = time.time()
    except requests.RequestException as exc:
        log.debug("understat: getTeamData %s/%d network err: %s",
                  team_slug, year, exc)
        return []

    if r.status_code != 200:
        log.debug("understat: getTeamData %s/%d → HTTP %s",
                  team_slug, year, r.status_code)
        return []
    try:
        payload = r.json()
    except (ValueError, json.JSONDecodeError) as exc:
        log.debug("understat: getTeamData %s/%d not JSON: %s", team_slug, year, exc)
        return []
    dates = payload.get("dates") or []
    return dates if isinstance(dates, list) else []


def _direct_understat_team_ids(
    team_name: str,
    *,
    years: list[int],
    limit: int,
) -> list[int]:
    """Return match_ids for `team_name` via the internal JSON endpoint.

    Falls back to legacy HTML scraping if the JSON endpoint is unreachable
    (rare — but guards against Understat tightening access in the future).
    """
    slug = _team_to_understat_slug(team_name)
    if not slug:
        return []
    ids: set[int] = set()
    for year in years:
        dates = _fetch_team_dates(slug, year)
        for d in dates:
            try:
                ids.add(int(d.get("id")))
            except (TypeError, ValueError):
                continue
        if len(ids) >= limit * 4:
            break
    # Legacy HTML fallback (only when JSON endpoint returned nothing)
    if not ids:
        for year in years:
            url = _understat_team_url(slug, year)
            r = _throttled_get(url)
            if not r or r.status_code != 200:
                continue
            for mid in _UNDERSTAT_ID_RE.findall(r.text):
                try:
                    ids.add(int(mid))
                except ValueError:
                    continue
    return sorted(ids, reverse=True)[: limit * 4]


def _fetch_team_matches_full(
    team_name: str,
    *,
    years: list[int],
) -> list[dict]:
    """Return enriched match rows for `team_name` from the JSON endpoint.

    Each row includes both teams, goals, xG, kickoff datetime, etc.
    Useful for the fuzzy linker because we can rank candidates by xG/date
    without doing a second hydration request.
    """
    slug = _team_to_understat_slug(team_name)
    if not slug:
        return []
    out: list[dict] = []
    for year in years:
        dates = _fetch_team_dates(slug, year)
        out.extend(dates)
    return out


def _direct_understat_intersection(
    home_team: str,
    away_team: str,
    *,
    years: list[int],
    limit: int,
) -> list[int]:
    """Direct path: intersect home + away team match-list JSON endpoints.

    Significantly more reliable than third-party search engines (which keep
    blocking datacenter IPs) when the teams are in a league Understat covers.
    """
    home_matches = _fetch_team_matches_full(home_team, years=years)
    if not home_matches:
        return []
    home_ids = {int(m["id"]) for m in home_matches if m.get("id")}

    away_matches = _fetch_team_matches_full(away_team, years=years)
    if not away_matches:
        return []
    away_ids = {int(m["id"]) for m in away_matches if m.get("id")}

    common = sorted(home_ids & away_ids, reverse=True)
    return common[:limit]


def _candidate_years(kickoff_iso: str | None) -> list[int]:
    """Decide which Understat season pages to query.

    Understat seasons run roughly Aug→May, so for a fixture in autumn 2025
    we query `/team/<slug>/2025` (current season) and `/team/<slug>/2024`
    (previous season — relevant for the h2h prior).
    """
    from datetime import datetime as _dt
    base_year = None
    if kickoff_iso:
        try:
            base_year = _dt.fromisoformat(kickoff_iso.replace("Z", "+00:00")).year
        except (ValueError, TypeError):
            base_year = None
    if base_year is None:
        base_year = _dt.now(timezone.utc).year
    return [base_year, base_year - 1]


def find_match_ids_by_teams(
    home_team: str,
    away_team: str,
    *,
    limit: int = 8,
    kickoff_iso: str | None = None,
) -> list[int]:
    """Best-effort search for Understat match_ids referencing both teams.

    Strategy (in priority order):
      1. **Direct Understat scraping** of `/team/<slug>/<year>` pages and
         intersect home ∩ away — most reliable, no third-party deps.
      2. **Search-engine cascade** (Bing → Brave → Mojeek → DDG Lite →
         DDG HTML → StartPage → Yandex) for `site:understat.com/match`.

    Returns up to `limit` candidate ids sorted high → low (most recent
    first; Understat ids are monotonic). Empty list when nothing matches.
    """
    import urllib.parse

    home_q = home_team.strip()
    away_q = away_team.strip()
    if not home_q or not away_q:
        return []

    # ── Path 1: direct Understat intersection (no third-party engines) ──
    years = _candidate_years(kickoff_iso)
    direct_ids = _direct_understat_intersection(home_q, away_q, years=years, limit=limit)
    if direct_ids:
        log.debug("understat: direct intersection found %d ids", len(direct_ids))
        return direct_ids

    # ── Path 2: fall back to search engine cascade ──
    raw_query = f'site:understat.com/match "{home_q}" "{away_q}"'
    encoded   = urllib.parse.quote(raw_query)

    for engine_name, url_fn in _SEARCH_ENGINES:
        try:
            r = requests.get(
                url_fn(encoded),
                headers=_SEARCH_HEADERS,
                timeout=_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            log.debug("understat: search %s failed: %s", engine_name, exc)
            continue

        if r.status_code != 200:
            log.debug("understat: search %s → HTTP %s", engine_name, r.status_code)
            continue

        ids = sorted(
            {int(m) for m in _UNDERSTAT_ID_RE.findall(r.text)},
            reverse=True,
        )
        if ids:
            log.debug("understat: %s found %d ids", engine_name, len(ids))
            return ids[:limit]

    return []


def _normalise_team_name(name: str) -> str:
    """Lowercase + strip punctuation for fuzzy team name matching.

    Understat uses slightly different team naming than API-Sports
    (e.g. "Manchester United" vs "Manchester Utd"). We normalise both
    sides before comparing so common variants still match.
    """
    if not name:
        return ""
    s = name.lower().strip()
    # Drop common suffixes / fillers
    for tok in [" fc", " cf", " sc", " ac", " club", " calcio", "afc ", "fc "]:
        s = s.replace(tok, " ")
    # Drop punctuation
    s = re.sub(r"[^a-z0-9áéíóúñ ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _teams_match(api_home: str, api_away: str, u_home: str, u_away: str) -> bool:
    """True if normalised Understat teams overlap with API-Sports teams.

    Accepts both side orderings (Arsenal vs Chelsea matches both home/away
    permutations on Understat — same two clubs are the relevant identity).

    Comparison strategy (in priority order):
      1. Slug equality — normalise both sides via `_team_to_understat_slug`
         which handles abbreviations (PSG → Paris_Saint_Germain, BVB →
         Borussia_Dortmund, etc.) and league suffixes.
      2. Token overlap / tail-match — falls back to substring containment
         on the diacritic-stripped lowercase names.
    """
    # Path 1: slug-based comparison (covers abbreviations + tildes)
    sl_a_h = _team_to_understat_slug(api_home)
    sl_a_a = _team_to_understat_slug(api_away)
    sl_u_h = _team_to_understat_slug(u_home)
    sl_u_a = _team_to_understat_slug(u_away)
    if sl_a_h and sl_a_a and sl_u_h and sl_u_a:
        if (sl_a_h == sl_u_h and sl_a_a == sl_u_a) or \
           (sl_a_h == sl_u_a and sl_a_a == sl_u_h):
            return True

    # Path 2: substring/tail overlap fallback
    a_h = _normalise_team_name(api_home)
    a_a = _normalise_team_name(api_away)
    u_h = _normalise_team_name(u_home)
    u_a = _normalise_team_name(u_away)
    if not (a_h and a_a and u_h and u_a):
        return False
    def _overlap(a: str, b: str) -> bool:
        if not a or not b:
            return False
        if a in b or b in a:
            return True
        # Accept if last token of either matches (e.g. "manchester united" vs "manchester utd")
        tail_a = a.split()[-1][:4]
        tail_b = b.split()[-1][:4]
        return tail_a == tail_b and len(tail_a) >= 3
    same_order = _overlap(a_h, u_h) and _overlap(a_a, u_a)
    reversed_  = _overlap(a_h, u_a) and _overlap(a_a, u_h)
    return same_order or reversed_


def fuzzy_link_match(
    home_team: str,
    away_team: str,
    kickoff_iso: Optional[str] = None,
    *,
    candidates: int = 8,
) -> Optional[dict]:
    """Find the Understat match_id matching the given fixture.

    Pipeline:
      1. Build a DDG query `site:understat.com/match "<home>" "<away>"`.
      2. For each candidate id, fetch the full match page.
      3. Filter by fuzzy team-name overlap (handles Understat vs
         API-Sports naming differences).
      4. If `kickoff_iso` was passed, pick the candidate with the closest
         date (within 7 days). Otherwise pick the highest-id (most recent).

    Returns a dict:
      {
        "understat_match_id": int,
        "enrichment":         <normalised dict>,
        "score":              {"home": int, "away": int},
        "match_date":         str,
        "team_overlap":       True,
        "candidates_checked": int,
      }
    or None when no acceptable candidate is found.
    """
    ids = find_match_ids_by_teams(
        home_team, away_team,
        limit=candidates,
        kickoff_iso=kickoff_iso,
    )
    if not ids:
        return None

    # Parse target kickoff date once (best-effort).
    target_dt = None
    if kickoff_iso:
        try:
            target_dt = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            target_dt = None

    best: Optional[dict] = None
    best_delta = None
    fallback: Optional[dict] = None   # any team-overlap, used when date-filter rejects all
    checked = 0
    for u_id in ids:
        enr = enrich_match_dict(u_id)
        checked += 1
        if not enr:
            continue
        teams = enr.get("teams") or {}
        u_home = teams.get("home") or ""
        u_away = teams.get("away") or ""
        if not _teams_match(home_team, away_team, u_home, u_away):
            continue

        # Compute date distance when possible.
        if target_dt and enr.get("date"):
            try:
                u_dt = datetime.fromisoformat(enr["date"].replace(" ", "T"))
                u_dt = u_dt.replace(tzinfo=timezone.utc) if u_dt.tzinfo is None else u_dt
                delta = abs((u_dt - target_dt).total_seconds())
            except (ValueError, TypeError):
                delta = None
        else:
            delta = None

        cand = {
            "understat_match_id": u_id,
            "enrichment":         enr,
            "score":              enr.get("score"),
            "match_date":         enr.get("date"),
            "team_overlap":       True,
            "candidates_checked": checked,
            "date_delta_seconds": delta,
        }
        # If we have target date: prefer the closest within 7 days.
        if delta is not None:
            if delta <= 7 * 86400 and (best_delta is None or delta < best_delta):
                best = cand
                best_delta = delta
            # Keep team-overlap as a fallback in case nothing falls in the window
            elif fallback is None:
                fallback = cand
        elif best is None:
            # No date filtering — take the first overlap (highest id = newest)
            best = cand

    chosen = best or fallback
    if chosen:
        chosen["candidates_checked"] = checked
    return chosen


__all__ = [
    "fetch_match",
    "enrich_match_dict",
    "fetch_match_cached",
    "aggregate_team_form",
    "find_match_ids_by_teams",
    "fuzzy_link_match",
    "_team_to_understat_slug",
]
