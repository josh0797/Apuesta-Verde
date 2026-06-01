"""SoccerSTATS scraper — extracts per-team "Goals per time segment" tables.

This scraper feeds `team_context["seasonal_form"]["early_goal_profile"]`
which the football corner pregame engine uses to detect trap signals
(EARLY_SCORING_FAVOURITE). It also surfaces first-half / total split data
that the analyst engine can use for other features.

Data source
-----------
`https://www.soccerstats.com/teamstats.asp?league=<league>&stats=u<id>-<slug>`

Each team page contains 3 tables:

    GOALS PER TIME SEGMENT (total)
    GOALS PER TIME SEGMENT (home)
    GOALS PER TIME SEGMENT (away)

Each table has 6 rows for the intervals 0-15, 16-30, 31-45, 46-60,
61-75, 76-90 — and 2 sub-rows (GF + GA). Bright Data Web Unlocker is
required because the site sits behind Cloudflare.

Output shape (matches user-confirmed spec)
------------------------------------------
    {
        "early_goal_pct":              float,    # 0-1
        "early_concede_pct":           float,    # 0-1
        "early_goal_involvement_pct":  float,    # 0-1
        "first_half_goal_pct":         float,    # 0-1 (intervals 1-3 / 6)
        "team_scored_first_half_pct":  None,     # not derivable from this table
        "team_conceded_first_half_pct":None,     # same
        "goals_for_0_15":              int,
        "goals_against_0_15":          int,
        "total_goals_for":             int,
        "total_goals_against":         int,
        "sample_size":                 int,      # we expose `matches_played`
        "league":                      str,
        "season":                      str,
        "source":                      "soccerstats",
        "source_url":                  str,
        "fetched_at":                  str,      # ISO UTC
        "data_quality":                "strong" | "usable" | "thin" | "missing",
    }

Caching
-------
Results are cached in MongoDB `team_early_goal_stats` with TTL 24h
keyed by (league_slug, team_slug).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..brightdata_client import fetch_unlocked

log = logging.getLogger("external_sources.soccerstats")

NAME = "soccerstats"
APPLICABLE_SPORTS = {"football"}
REQUIRES_UNLOCKER = True

CACHE_COLLECTION = "team_early_goal_stats"
CACHE_TTL_HOURS  = 24
FETCH_TIMEOUT_SEC = 25.0

_BASE   = "https://www.soccerstats.com"
_LEAGUE = _BASE + "/table.asp?league={league}&tid=s4"   # index used to discover team URLs
_TEAM   = _BASE + "/teamstats.asp?league={league}&stats={slug}"


# ── Public surface ──────────────────────────────────────────────────────────
async def fetch_team_early_goal_profile(
    team_name: str,
    league_slug: str,
    *,
    db=None,
    season: Optional[str] = None,
) -> Optional[dict]:
    """Return the `early_goal_profile` payload for one team.

    Fully fail-soft: any network / parsing failure returns
    ``{"data_quality": "missing", ...}`` so the caller can keep going.
    """
    if not team_name or not league_slug:
        return _missing_payload(team_name, league_slug, reason="missing_input")

    cache_key = _cache_key(league_slug, team_name)

    # Read cache
    cached = await _read_cache(db, cache_key)
    if cached:
        return cached

    # Resolve team URL (needs the league index page) → cache the index too
    team_slug = await _resolve_team_slug(league_slug, team_name, db=db)
    if not team_slug:
        log.info("[SOCCERSTATS] team slug not found team=%r league=%r", team_name, league_slug)
        payload = _missing_payload(team_name, league_slug, reason="team_not_found")
        await _write_cache(db, cache_key, payload)
        return payload

    url = _TEAM.format(league=league_slug, slug=team_slug)
    try:
        html = await asyncio.wait_for(fetch_unlocked(url), timeout=FETCH_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        log.warning("[SOCCERSTATS] timeout team=%r url=%s", team_name, url)
        return _missing_payload(team_name, league_slug, reason="timeout")
    except Exception as exc:
        log.warning("[SOCCERSTATS] fetch fail team=%r url=%s: %s", team_name, url, exc)
        return _missing_payload(team_name, league_slug, reason="fetch_error")

    if not html:
        return _missing_payload(team_name, league_slug, reason="empty_body")

    parsed = parse_team_stats_html(html)
    if not parsed:
        log.info("[SOCCERSTATS] parse failed team=%r url=%s", team_name, url)
        payload = _missing_payload(team_name, league_slug, reason="parse_failed")
        await _write_cache(db, cache_key, payload)
        return payload

    parsed.update({
        "league":      league_slug,
        "season":      season or _current_season(),
        "source":      NAME,
        "source_url":  url,
        "fetched_at":  datetime.now(timezone.utc).isoformat(),
    })
    await _write_cache(db, cache_key, parsed)
    return parsed


# ── Team slug resolution ────────────────────────────────────────────────────
_TEAM_LINK_RE = re.compile(
    r"<a\s+href=['\"]teamstats\.asp\?league=[^&]+&stats=(u\d+-[a-z0-9\-]+)['\"]>([^<]+)</a>",
    re.IGNORECASE,
)


async def _resolve_team_slug(
    league_slug: str, team_name: str, *, db=None,
) -> Optional[str]:
    """Look up the `u<id>-<slug>` token for `team_name` from the league index.

    The league index page lists every team with its teamstats URL, so a
    single fetch per league per 24h is enough.
    """
    cache_key = f"_index:{league_slug}"
    cached = await _read_cache(db, cache_key)
    if cached and isinstance(cached, dict) and cached.get("slugs"):
        return _best_match(team_name, cached["slugs"])

    url = _LEAGUE.format(league=league_slug)
    try:
        html = await asyncio.wait_for(fetch_unlocked(url), timeout=FETCH_TIMEOUT_SEC)
    except (asyncio.TimeoutError, Exception) as exc:
        log.debug("[SOCCERSTATS] league index fetch failed %s: %s", league_slug, exc)
        return None
    if not html:
        return None

    slugs: dict[str, str] = {}
    for m in _TEAM_LINK_RE.finditer(html):
        slug, display = m.group(1), m.group(2).strip()
        key = _normalise(display)
        if key and key not in slugs:
            slugs[key] = slug

    if not slugs:
        return None

    await _write_cache(db, cache_key, {"slugs": slugs, "_index": True})
    return _best_match(team_name, slugs)


def _best_match(team_name: str, slugs: dict[str, str]) -> Optional[str]:
    target = _normalise(team_name)
    if not target:
        return None
    # exact
    if target in slugs:
        return slugs[target]
    # startswith
    for k, v in slugs.items():
        if k.startswith(target) or target.startswith(k):
            return v
    # token-overlap (≥ 2 tokens common)
    target_tokens = set(target.split())
    if len(target_tokens) >= 2:
        for k, v in slugs.items():
            if len(target_tokens & set(k.split())) >= 2:
                return v
    return None


def _normalise(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# ── HTML parser ─────────────────────────────────────────────────────────────
_SEGMENT_BLOCK_RE = re.compile(
    r"GOALS PER TIME SEGMENT \((total|home|away)\).*?(?=GOALS PER TIME SEGMENT|</table>\s*</td>\s*</tr>\s*</table>)",
    re.IGNORECASE | re.DOTALL,
)

# Each interval row looks like:
#   <td ... >0-15</td> ... <b>8</b> ... </tr>
#   <tr ...> ... GA</td> ... <b>2</b>
_INTERVAL_RE = re.compile(
    r"<td[^>]*?>\s*(0-15|16-30|31-45|46-60|61-75|76-90)\s*</td>"
    r".*?GF\s*</td>\s*<td[^>]*?>\s*<b>\s*(\d+)\s*</b>"
    r".*?GA\s*</td>\s*<td[^>]*?>\s*<b>\s*(\d+)\s*</b>",
    re.IGNORECASE | re.DOTALL,
)


def parse_team_stats_html(html: str) -> Optional[dict]:
    """Extract Goals-Per-Time-Segment from a teamstats page.

    Returns a dict suitable for `team_context["seasonal_form"]
    ["early_goal_profile"]`, or None when the structure isn't found.
    """
    if not isinstance(html, str) or "GOALS PER TIME SEGMENT" not in html:
        return None

    # Snapshot per-split (total / home / away).
    splits: dict[str, dict] = {}
    for block_match in _SEGMENT_BLOCK_RE.finditer(html):
        split = (block_match.group(1) or "").lower()
        block = block_match.group(0)
        intervals: dict[str, dict[str, int]] = {}
        for m in _INTERVAL_RE.finditer(block):
            interval, gf, ga = m.group(1), int(m.group(2)), int(m.group(3))
            intervals[interval] = {"GF": gf, "GA": ga}
        if intervals:
            splits[split] = intervals

    total = splits.get("total") or {}
    if not total:
        return None

    # Aggregate totals
    gf_0_15 = total.get("0-15", {}).get("GF", 0)
    ga_0_15 = total.get("0-15", {}).get("GA", 0)
    total_gf = sum(d.get("GF", 0) for d in total.values())
    total_ga = sum(d.get("GA", 0) for d in total.values())
    # First half = intervals 0-15 + 16-30 + 31-45
    fh_intervals = ("0-15", "16-30", "31-45")
    fh_gf = sum(total.get(i, {}).get("GF", 0) for i in fh_intervals)
    fh_ga = sum(total.get(i, {}).get("GA", 0) for i in fh_intervals)

    # Rate calculations (fail-soft on zero denominators)
    def _ratio(num: int, den: int) -> Optional[float]:
        if den <= 0:
            return None
        return round(num / den, 3)

    # Sample size proxy — total match goals for the team. We can't get
    # match count from the GOALS PER TIME SEGMENT block alone, so we use
    # total_gf+total_ga as a (rough) reliability indicator.
    match_goal_volume = total_gf + total_ga
    if match_goal_volume >= 40:
        data_quality = "strong"
    elif match_goal_volume >= 20:
        data_quality = "usable"
    elif match_goal_volume >= 10:
        data_quality = "thin"
    else:
        data_quality = "missing"

    return {
        "early_goal_pct":              _ratio(gf_0_15, total_gf),
        "early_concede_pct":           _ratio(ga_0_15, total_ga),
        "early_goal_involvement_pct":  _ratio(gf_0_15 + ga_0_15, total_gf + total_ga),
        "first_half_goal_pct":         _ratio(fh_gf, total_gf),
        # These two require match-level booleans that the segment table
        # does NOT expose. The "derived_early_goal" service backfills
        # them from API-Sports recent_fixtures.
        "team_scored_first_half_pct":  None,
        "team_conceded_first_half_pct":None,
        # Raw counters (useful for combining with other sources later)
        "goals_for_0_15":              gf_0_15,
        "goals_against_0_15":          ga_0_15,
        "goals_for_first_half":        fh_gf,
        "goals_against_first_half":    fh_ga,
        "total_goals_for":             total_gf,
        "total_goals_against":         total_ga,
        "sample_size":                 match_goal_volume,
        "splits": {
            k: v for k, v in splits.items()   # serialise for audit if needed
        },
        "data_quality":                data_quality,
    }


# ── Cache helpers ───────────────────────────────────────────────────────────
def _cache_key(league: str, team: str) -> str:
    h = hashlib.sha1(f"{league}|{_normalise(team)}".encode("utf-8")).hexdigest()[:8]
    return f"soccerstats:{_normalise(team).replace(' ', '_')}:{league}:{h}"


async def _read_cache(db, key: str) -> Optional[dict]:
    if db is None:
        return None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
        doc = await db[CACHE_COLLECTION].find_one(
            {"key": key, "fetched_at": {"$gte": cutoff}}, {"_id": 0, "payload": 1},
        )
        if doc and isinstance(doc.get("payload"), dict):
            return doc["payload"]
    except Exception as exc:
        log.debug("[SOCCERSTATS] cache read failed: %s", exc)
    return None


async def _write_cache(db, key: str, payload: dict) -> None:
    if db is None or not payload:
        return
    try:
        await db[CACHE_COLLECTION].replace_one(
            {"key": key},
            {
                "key":         key,
                "payload":     payload,
                "fetched_at":  datetime.now(timezone.utc).isoformat(),
            },
            upsert=True,
        )
    except Exception as exc:
        log.debug("[SOCCERSTATS] cache write failed: %s", exc)


# ── Helpers ────────────────────────────────────────────────────────────────
def _missing_payload(team_name: str, league: str, *, reason: str) -> dict:
    return {
        "early_goal_pct":              None,
        "early_concede_pct":           None,
        "early_goal_involvement_pct":  None,
        "first_half_goal_pct":         None,
        "team_scored_first_half_pct":  None,
        "team_conceded_first_half_pct":None,
        "goals_for_0_15":              None,
        "goals_against_0_15":          None,
        "total_goals_for":             None,
        "total_goals_against":         None,
        "sample_size":                 0,
        "league":                      league,
        "season":                      _current_season(),
        "source":                      NAME,
        "source_url":                  None,
        "fetched_at":                  datetime.now(timezone.utc).isoformat(),
        "data_quality":                "missing",
        "missing_reason":              reason,
    }


def _current_season() -> str:
    """European football year-pair, e.g. '2025-2026'."""
    now = datetime.now(timezone.utc)
    if now.month >= 7:
        return f"{now.year}-{now.year + 1}"
    return f"{now.year - 1}-{now.year}"


__all__ = [
    "fetch_team_early_goal_profile",
    "parse_team_stats_html",
    "NAME",
    "APPLICABLE_SPORTS",
    "REQUIRES_UNLOCKER",
]
