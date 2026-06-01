"""StatBunker scraper — primary early-goal-profile source.

Validated DOM structure (probe_statbunker.py 2026-06-01):

  ✓ https://www.statbunker.com/competitions/GoalsFor?comp_id=<id>
    Headers: ['Goals For', 'Clubs', 'H', 'A', 'FH', 'SH', 'F15', 'L10',
              'HMS', 'AMS', 'Pld', 'Scored goals per match', 'More']
    Example row: ['64','Barcelona','34','30','28','36','12','12','11','13','24','2.67','More']

  ✓ https://www.statbunker.com/competitions/GoalsAgainst?comp_id=<id>
    Headers: ['Goals Against', 'Clubs', 'H', 'A', 'FH', 'SH', 'F15', 'L10',
              'HCS', 'ACS', 'Pld', 'Conceded goals per match', 'More']

Column legend:
  H   = Home goals          A   = Away goals
  FH  = First Half goals    SH  = Second Half goals
  F15 = First 15 mins goals  L10 = Last 10 mins goals
  Pld = Matches played
  HMS / AMS = Home/Away matches scored (count of matches, NOT goals)
  HCS / ACS = Home/Away clean sheets

From these we derive the canonical `early_goal_profile`:

  early_goal_pct        = F15_for     / total_for
  early_concede_pct     = F15_against / total_against
  first_half_goal_pct   = FH_for      / total_for
  first_half_concede_pct= FH_against  / total_against
  goals_for_0_15        = F15_for                       (raw)
  goals_against_0_15    = F15_against                   (raw)
  total_goals_for       = total_for
  total_goals_against   = total_against
  sample_size           = Pld

Cache: MongoDB `team_early_goal_stats` (shared with soccerstats.py),
TTL 24h, keyed by (league_slug, team_name).
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

log = logging.getLogger("external_sources.statbunker")

NAME = "statbunker"
APPLICABLE_SPORTS = {"football"}
REQUIRES_UNLOCKER = True

CACHE_COLLECTION = "team_early_goal_stats"
CACHE_TTL_HOURS  = 24
FETCH_TIMEOUT_SEC = 25.0


# ─────────────────────────────────────────────────────────────────────────────
# Competition resolver
# ─────────────────────────────────────────────────────────────────────────────
# Manual mapping for Tier 1/Tier 2 leagues. The comp_id changes every season.
# Format: { season_key: {league_key: comp_id, ...} }
#
# Confirmed comp_ids from user's exploration (2026-06-01):
#   La Liga 25/26 → 777   |   La Liga 24/25 → 731   |   La Liga 23/24 → 753
#   Premier League 25/26 → 776   |   Premier League 24/25 → 596
#
# When a season isn't mapped, we fall back to "_current" (the active season).
_COMP_IDS: dict[str, dict[str, int]] = {
    "2025-2026": {
        # ── Top 5 European leagues ───────────────────────────────
        "premier league":           776,
        "epl":                      776,
        "la liga":                  777,
        "laliga":                   777,
        "primera division":         777,
        "serie a":                  785,
        "ligue 1":                  787,
        "french ligue":             787,
        "french ligue 1":           787,
        # Bundesliga 25/26 not indexed yet (latest is 24/25)

        # ── UEFA / European competitions ─────────────────────────
        "uefa champions league":    783,
        "champions league":         783,
        "ucl":                      783,
        "uefa europa league":       784,
        "europa league":            784,
        "uefa conference league":   786,
        "uefa europa conference league": 786,
        "conference league":        786,
        "uefa super cup":           781,
        "super cup":                781,

        # ── English lower divisions ──────────────────────────────
        "championship":             779,
        "sky bet championship":     779,
        "league one":               778,
        "sky bet league one":       778,
        "league two":               780,
        "sky bet league two":       780,
        "fa cup":                   788,

        # ── Other top European ───────────────────────────────────
        "mls":                      714,                # latest indexed
        "scottish premiership":     749,
        "scottish premier league":  749,
    },
    "2024-2025": {
        "premier league":           596,
        "epl":                      596,
        "la liga":                  731,
        "laliga":                   731,
        "primera division":         731,
        "bundesliga":               762,
    },
    "2023-2024": {
        "la liga":                  753,
        "laliga":                   753,
        "primera division":         753,
        "bundesliga":               751,
    },
}


# Codes used by StatBunker's /competitions/?comp_type=<X> dropdown.
# These are stable across seasons — only the comp_id changes year-to-year.
# Used by the auto-discovery job to find the latest comp_id for each league.
_STATBUNKER_COMP_CODES: dict[str, str] = {
    "EPL":      "premier league",
    "LL":       "la liga",
    "SA":       "serie a",
    "BL":       "bundesliga",
    "LC":       "ligue 1",
    "EC":       "championship",
    "EL1":      "league one",
    "EL2":      "league two",
    "SPL":      "scottish premiership",
    "MLS":      "mls",
    "DL":       "eredivisie",
    "ABUN":     "austrian bundesliga",
    "SWSL":     "swiss super league",
    "DANSL":    "danish super league",
    "UCL":      "uefa champions league",
    "UCUP":     "uefa europa league",
    "ESC":      "uefa super cup",
    "UEFANL":   "uefa nations league",
    "UECON":    "uefa conference league",
    "FAC":      "fa cup",
    "CC":       "league cup",
    "COPA":     "copa libertadores",
    "FWCC":     "fifa club world cup",
    "WC":       "fifa world cup",
    "ECC":      "euro",
    "copaam":   "copa america",
    "ACON":     "africa cup of nations",
    "Goldc":    "concacaf gold cup",
    "ASCUP":    "afc asian cup",
    "WCQ":      "world cup qualifying",
    "WCQE":     "world cup qualifying europe",
    "WCQSA":    "world cup qualifying south america",
    "INT":      "international friendlies",
}


def _current_season() -> str:
    now = datetime.now(timezone.utc)
    if now.month >= 7:
        return f"{now.year}-{now.year + 1}"
    return f"{now.year - 1}-{now.year}"


def resolve_comp_id(league_name: Optional[str], season: Optional[str] = None) -> Optional[int]:
    """Map (league_name, season) → StatBunker comp_id.

    Returns None when we can't resolve confidently — the scraper will
    then mark the result as missing and the merge will fall back to
    SoccerSTATS / derived API-Sports.
    """
    if not league_name:
        return None
    season = season or _current_season()
    norm = re.sub(r"[^a-z0-9 ]", " ", league_name.lower()).strip()
    norm = re.sub(r"\s+", " ", norm)
    bucket = _COMP_IDS.get(season) or {}
    if norm in bucket:
        return bucket[norm]
    # Strip year qualifiers and retry
    norm2 = re.sub(r"\b(20\d\d|the|men's|women's)\b", "", norm).strip()
    norm2 = re.sub(r"\s+", " ", norm2)
    if norm2 != norm and norm2 in bucket:
        return bucket[norm2]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Auto-discovery
# ─────────────────────────────────────────────────────────────────────────────
# `_COMP_IDS` is a static fallback. The monthly APScheduler job calls
# `discover_comp_ids()` to scan every code in `_STATBUNKER_COMP_CODES`,
# resolve the **active season** comp_id, persist it to MongoDB, and
# update `_COMP_IDS` in-process. This way new seasons go live as soon
# as StatBunker indexes them — no code deploy required.

DISCOVERY_COLLECTION = "statbunker_comp_ids"


_SEASON_RE = re.compile(r"(\d{2})\s*/\s*(\d{2,4})")


def _title_to_season(title: str) -> Optional[str]:
    """Extract the season label from a 'Goals for <League> 25/26' title."""
    if not title:
        return None
    m = _SEASON_RE.search(title)
    if not m:
        return None
    start_short, end_part = m.group(1), m.group(2)
    try:
        start = 2000 + int(start_short)
        if len(end_part) <= 2:
            end = 2000 + int(end_part)
        else:
            end = int(end_part)
        return f"{start}-{end}"
    except ValueError:
        return None


async def _resolve_comp_id_for_code(
    code: str,
) -> Optional[tuple[int, str, str]]:
    """Resolve the active comp_id for a StatBunker code by hitting
    `/competitions/?comp_type=<code>` and pulling the latest comp_id +
    season from the GoalsFor title.

    Returns (comp_id, season, title) or None if discovery failed.
    """
    list_url = f"https://www.statbunker.com/competitions/?comp_type={code}"
    try:
        body = await asyncio.wait_for(fetch_unlocked(list_url), timeout=FETCH_TIMEOUT_SEC)
    except (asyncio.TimeoutError, Exception):
        return None
    if not body or "404" in body[:600]:
        return None

    # The active comp_id is the most recent one in the listing — usually
    # the first `comp_id=NNN` reference inside a teamstats/leagueview
    # link near the top of the body.
    ids = re.findall(r"comp_id=(\d+)", body)
    if not ids:
        return None

    # Fetch the GoalsFor page to confirm + extract season label.
    seen_titles: list[tuple[int, str]] = []
    for cid_str in ids[:6]:
        try:
            cid = int(cid_str)
        except ValueError:
            continue
        url = f"https://www.statbunker.com/competitions/GoalsFor?comp_id={cid}"
        try:
            gbody = await asyncio.wait_for(fetch_unlocked(url), timeout=FETCH_TIMEOUT_SEC)
        except (asyncio.TimeoutError, Exception):
            continue
        if not gbody or "404" in gbody[:600]:
            continue
        title_m = re.search(r"<title[^>]*>([^<]+)</title>", gbody)
        if not title_m:
            continue
        title = title_m.group(1).strip()
        seen_titles.append((cid, title))

    if not seen_titles:
        return None

    # Pick the one with the highest season label (e.g. 25/26 > 24/25).
    def _season_rank(t: tuple[int, str]) -> int:
        m = _SEASON_RE.search(t[1])
        return int(m.group(1)) if m else 0

    seen_titles.sort(key=_season_rank, reverse=True)
    cid, title = seen_titles[0]
    season = _title_to_season(title) or _current_season()
    return cid, season, title


async def discover_comp_ids(db=None, *, codes: Optional[list[str]] = None) -> dict:
    """Scan StatBunker for the active comp_id per league code.

    Persists results to MongoDB and patches the in-process `_COMP_IDS`
    so subsequent fetches benefit immediately. Idempotent and safe to
    re-run.

    Args:
        db:    Motor MongoDB handle (optional).
        codes: Override the default code list — useful for targeted refreshes.

    Returns a dict:
        {
            "discovered": [{"code": "BL", "league": "...", "season":"...",
                             "comp_id": 762, "title": "..."}, ...],
            "failures":   [{"code": "MX", "reason": "..."}, ...],
            "ts":         "<ISO>"
        }
    """
    codes = codes or list(_STATBUNKER_COMP_CODES.keys())
    discovered: list[dict] = []
    failures: list[dict] = []

    # Run in batches of 4 to be polite to Bright Data.
    batch_size = 4
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        results = await asyncio.gather(
            *[_resolve_comp_id_for_code(c) for c in batch],
            return_exceptions=True,
        )
        for code, r in zip(batch, results):
            league = _STATBUNKER_COMP_CODES.get(code)
            if isinstance(r, tuple):
                cid, season, title = r
                discovered.append({
                    "code":    code,
                    "league":  league,
                    "season":  season,
                    "comp_id": cid,
                    "title":   title,
                })
                # Patch in-process _COMP_IDS so the new id is used right away.
                bucket = _COMP_IDS.setdefault(season, {})
                if league and bucket.get(league) != cid:
                    bucket[league] = cid
            else:
                reason = (
                    str(r)[:120] if isinstance(r, Exception)
                    else "no_active_comp_id"
                )
                failures.append({"code": code, "league": league, "reason": reason})

    # Persist to MongoDB
    if db is not None and discovered:
        try:
            now = datetime.now(timezone.utc).isoformat()
            for entry in discovered:
                key = f"{entry['league']}|{entry['season']}"
                await db[DISCOVERY_COLLECTION].replace_one(
                    {"key": key},
                    {
                        "key":         key,
                        "code":        entry["code"],
                        "league":      entry["league"],
                        "season":      entry["season"],
                        "comp_id":     entry["comp_id"],
                        "title":       entry["title"],
                        "discovered_at": now,
                    },
                    upsert=True,
                )
            log.info(
                "[STATBUNKER] discovery persisted %d comp_ids (%d failures)",
                len(discovered), len(failures),
            )
        except Exception as exc:
            log.warning("[STATBUNKER] discovery persist failed: %s", exc)

    return {
        "discovered": discovered,
        "failures":   failures,
        "ts":         datetime.now(timezone.utc).isoformat(),
    }


async def load_discovered_comp_ids(db) -> int:
    """Read previously-discovered comp_ids from MongoDB and merge them into
    `_COMP_IDS`. Call this once at backend startup so the in-process
    table picks up everything persisted by past monthly jobs.

    Returns the number of (league, season) entries loaded.
    """
    if db is None:
        return 0
    try:
        cursor = db[DISCOVERY_COLLECTION].find({}, {"_id": 0})
        loaded = 0
        async for doc in cursor:
            league = doc.get("league")
            season = doc.get("season")
            cid = doc.get("comp_id")
            if not (league and season and cid):
                continue
            bucket = _COMP_IDS.setdefault(season, {})
            if bucket.get(league) != cid:
                bucket[league] = cid
                loaded += 1
        if loaded:
            log.info("[STATBUNKER] loaded %d discovered comp_ids from MongoDB", loaded)
        return loaded
    except Exception as exc:
        log.warning("[STATBUNKER] failed to load discovered comp_ids: %s", exc)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# HTML parser
# ─────────────────────────────────────────────────────────────────────────────
def _normalise(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def _parse_int(s: str) -> Optional[int]:
    try:
        return int(s.strip())
    except (TypeError, ValueError, AttributeError):
        return None


def parse_team_rows(html: str, kind: str) -> dict[str, dict]:
    """Parse the GoalsFor / GoalsAgainst table.

    Returns a dict keyed by the normalised team name with one entry
    per club, e.g.:

        {
            "barcelona":   {"total": 64, "FH": 28, "SH": 36, "F15": 12,
                            "L10": 12, "H": 34, "A": 30, "Pld": 24},
            "real madrid": {...},
        }

    `kind` ∈ {"for", "against"} — only used to validate the header.
    """
    if not isinstance(html, str) or not html:
        return {}

    # Expected header lookup
    title_header = "Goals For" if kind == "for" else "Goals Against"
    if title_header not in html:
        log.debug("[STATBUNKER] header '%s' not present in HTML", title_header)
        return {}

    # Pick the single big table that contains our header
    tbl = re.search(
        r"<table[^>]*>(.*?" + re.escape(title_header) + r".*?)</table>",
        html, re.IGNORECASE | re.DOTALL,
    )
    if not tbl:
        return {}
    raw = tbl.group(1)

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.IGNORECASE | re.DOTALL)
    teams: dict[str, dict] = {}
    for r in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.IGNORECASE | re.DOTALL)
        if len(tds) < 11:
            continue
        cells = [_strip_html(c) for c in tds]
        # Expected layout for GoalsFor:
        #   [0]=total, [1]=team, [2]=H, [3]=A, [4]=FH, [5]=SH, [6]=F15,
        #   [7]=L10, [8]=HMS, [9]=AMS, [10]=Pld, [11]=per_match
        total = _parse_int(cells[0])
        team  = cells[1]
        if total is None or not team:
            continue
        key = _normalise(team)
        if not key:
            continue
        teams[key] = {
            "team_display": team,
            "total":        total,
            "H":            _parse_int(cells[2]),
            "A":            _parse_int(cells[3]),
            "FH":           _parse_int(cells[4]),
            "SH":           _parse_int(cells[5]),
            "F15":          _parse_int(cells[6]),
            "L10":          _parse_int(cells[7]),
            "Pld":          _parse_int(cells[10]) if len(cells) > 10 else None,
        }
    return teams


# ─────────────────────────────────────────────────────────────────────────────
# Build profile from `for` + `against` rows
# ─────────────────────────────────────────────────────────────────────────────
def _ratio(num: Optional[int], den: Optional[int]) -> Optional[float]:
    if num is None or den is None or den <= 0:
        return None
    return round(num / den, 3)


def build_profile_from_rows(
    for_row: Optional[dict],
    against_row: Optional[dict],
    *,
    league: str,
    season: str,
    source_url_for: Optional[str],
    source_url_against: Optional[str],
) -> dict:
    """Compose the canonical `early_goal_profile` payload."""
    total_for  = (for_row or {}).get("total")
    total_ag   = (against_row or {}).get("total")
    f15_for    = (for_row or {}).get("F15")
    f15_ag     = (against_row or {}).get("F15")
    fh_for     = (for_row or {}).get("FH")
    fh_ag      = (against_row or {}).get("FH")
    pld_for    = (for_row or {}).get("Pld")
    pld_ag     = (against_row or {}).get("Pld")
    pld = pld_for or pld_ag or 0

    # Quality calibration based on matches played
    if pld >= 20:
        quality = "strong"
    elif pld >= 12:
        quality = "usable"
    elif pld >= 5:
        quality = "thin"
    else:
        quality = "missing"

    return {
        "early_goal_pct":              _ratio(f15_for, total_for),
        "early_concede_pct":           _ratio(f15_ag,  total_ag),
        "early_goal_involvement_pct":  _ratio(
            (f15_for or 0) + (f15_ag or 0),
            (total_for or 0) + (total_ag or 0),
        ),
        "first_half_goal_pct":         _ratio(fh_for, total_for),
        "first_half_concede_pct":      _ratio(fh_ag,  total_ag),
        # Sample-count flags — StatBunker does NOT expose
        # "team_scored_first_half_pct" directly; left to API-Sports backfill.
        "team_scored_first_half_pct":  None,
        "team_conceded_first_half_pct":None,
        # Raw counters
        "goals_for_0_15":              f15_for,
        "goals_against_0_15":          f15_ag,
        "goals_for_first_half":        fh_for,
        "goals_against_first_half":    fh_ag,
        "total_goals_for":             total_for,
        "total_goals_against":         total_ag,
        "sample_size":                 pld,
        "league":                      league,
        "season":                      season,
        "source":                      NAME,
        "source_url":                  source_url_for or source_url_against,
        "source_url_goals_for":        source_url_for,
        "source_url_goals_against":    source_url_against,
        "fetched_at":                  datetime.now(timezone.utc).isoformat(),
        "data_quality":                quality,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-competition fetch (parses ONCE for all teams, then matches by name)
# ─────────────────────────────────────────────────────────────────────────────
_DOMAINS = ("https://www.statbunker.com", "https://statbunker.com")


async def _fetch_with_fallback(path: str) -> tuple[Optional[str], Optional[str]]:
    """Try www. then bare domain. Return (body, used_url)."""
    last_err = None
    for base in _DOMAINS:
        url = f"{base}{path}"
        try:
            body = await asyncio.wait_for(fetch_unlocked(url), timeout=FETCH_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            last_err = "timeout"
            continue
        except Exception as exc:
            last_err = str(exc)[:80]
            continue
        if body and "404 Page Not Found" not in body[:600]:
            return body, url
    log.debug("[STATBUNKER] all domains failed for %s (last_err=%s)", path, last_err)
    return None, None


async def fetch_competition_rows(comp_id: int) -> dict[str, dict]:
    """Fetch GoalsFor + GoalsAgainst for one competition and return:

        {
            "<team_key>": {
                "for":     {...row...},
                "against": {...row...},
                "source_url_for":     "...",
                "source_url_against": "...",
            },
            ...
        }
    """
    if not comp_id:
        return {}

    body_for, url_for = await _fetch_with_fallback(
        f"/competitions/GoalsFor?comp_id={comp_id}",
    )
    body_ag, url_ag = await _fetch_with_fallback(
        f"/competitions/GoalsAgainst?comp_id={comp_id}",
    )

    rows_for = parse_team_rows(body_for or "", kind="for")
    rows_ag  = parse_team_rows(body_ag or "", kind="against")

    out: dict[str, dict] = {}
    for key in set(rows_for) | set(rows_ag):
        out[key] = {
            "for":                rows_for.get(key),
            "against":            rows_ag.get(key),
            "source_url_for":     url_for,
            "source_url_against": url_ag,
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
async def fetch_team_early_goal_profile(
    team_name: str,
    league: str,
    *,
    db=None,
    season: Optional[str] = None,
) -> Optional[dict]:
    """Return the early-goal profile for one team via StatBunker.

    Fully fail-soft: any failure returns a payload with
    ``data_quality="missing"`` and ``missing_reason``.
    """
    if not team_name or not league:
        return _missing_payload(league, season, reason="missing_input")

    season = season or _current_season()
    cache_key = _cache_key(league, team_name, season)

    cached = await _read_cache(db, cache_key)
    if cached:
        return cached

    comp_id = resolve_comp_id(league, season)
    if not comp_id:
        payload = _missing_payload(league, season, reason="no_comp_id")
        await _write_cache(db, cache_key, payload)
        return payload

    # Comp-level cache: fetch all teams once per competition per 24h.
    comp_cache_key = f"_comp_rows:statbunker:{comp_id}"
    cached_rows = await _read_cache(db, comp_cache_key)
    if cached_rows and isinstance(cached_rows, dict) and cached_rows.get("rows"):
        rows = cached_rows["rows"]
    else:
        rows = await fetch_competition_rows(comp_id)
        if rows:
            await _write_cache(db, comp_cache_key, {"rows": rows})

    if not rows:
        payload = _missing_payload(league, season, reason="empty_competition_rows")
        await _write_cache(db, cache_key, payload)
        return payload

    team_key = _best_match(team_name, list(rows.keys()))
    if not team_key:
        payload = _missing_payload(league, season, reason="team_not_in_table")
        await _write_cache(db, cache_key, payload)
        return payload

    team_data = rows[team_key]
    profile = build_profile_from_rows(
        team_data.get("for"),
        team_data.get("against"),
        league=league,
        season=season,
        source_url_for=team_data.get("source_url_for"),
        source_url_against=team_data.get("source_url_against"),
    )
    await _write_cache(db, cache_key, profile)
    return profile


def _best_match(team_name: str, candidates: list[str]) -> Optional[str]:
    target = _normalise(team_name)
    if not target:
        return None
    if target in candidates:
        return target
    # Startswith / endswith / contains
    for c in candidates:
        if target.startswith(c) or c.startswith(target):
            return c
        if target in c or c in target:
            return c
    # Token overlap
    target_tokens = set(target.split())
    if len(target_tokens) >= 1:
        best = None
        best_score = 0
        for c in candidates:
            overlap = len(target_tokens & set(c.split()))
            if overlap > best_score:
                best, best_score = c, overlap
        if best_score >= 1:
            return best
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────
def _cache_key(league: str, team: str, season: str) -> str:
    h = hashlib.sha1(f"{league}|{_normalise(team)}|{season}".encode()).hexdigest()[:8]
    return f"statbunker:{_normalise(team).replace(' ','_')}:{season}:{h}"


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
        log.debug("[STATBUNKER] cache read failed: %s", exc)
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
        log.debug("[STATBUNKER] cache write failed: %s", exc)


def _missing_payload(league: str, season: Optional[str], *, reason: str) -> dict:
    return {
        "early_goal_pct":              None,
        "early_concede_pct":           None,
        "early_goal_involvement_pct":  None,
        "first_half_goal_pct":         None,
        "first_half_concede_pct":      None,
        "team_scored_first_half_pct":  None,
        "team_conceded_first_half_pct":None,
        "goals_for_0_15":              None,
        "goals_against_0_15":          None,
        "total_goals_for":             None,
        "total_goals_against":         None,
        "sample_size":                 0,
        "league":                      league,
        "season":                      season or _current_season(),
        "source":                      NAME,
        "source_url":                  None,
        "source_url_goals_for":        None,
        "source_url_goals_against":    None,
        "fetched_at":                  datetime.now(timezone.utc).isoformat(),
        "data_quality":                "missing",
        "missing_reason":              reason,
        "source_status":               "failed_url" if reason in (
            "empty_competition_rows", "no_comp_id",
        ) else reason,
    }


__all__ = [
    "fetch_team_early_goal_profile",
    "fetch_competition_rows",
    "parse_team_rows",
    "build_profile_from_rows",
    "resolve_comp_id",
    "discover_comp_ids",
    "load_discovered_comp_ids",
    "NAME",
    "APPLICABLE_SPORTS",
    "REQUIRES_UNLOCKER",
]
