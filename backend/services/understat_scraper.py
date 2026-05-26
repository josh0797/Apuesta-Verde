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


__all__ = [
    "fetch_match",
    "enrich_match_dict",
    "fetch_match_cached",
    "aggregate_team_form",
]
