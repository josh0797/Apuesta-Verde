"""
StatMuse Recent-Form Scraper (Bright Data Web Unlocker)
========================================================

Acts as a **fallback and cross-validation source** for the MLB Stats API
recent-form module. StatMuse exposes pre-aggregated team tables for any
"last N games" window — exactly what we need to backstop the
schedule+boxscore pipeline.

URLs scraped::

    https://www.statmuse.com/mlb/ask/mlb-team-stats-last-{N}-games
    https://www.statmuse.com/mlb/ask/team-runs-allowed-per-game-last-{N}-games

StatMuse returns an HTML page with a single ranking table. We extract
that table client-side with a minimal HTML parser (``html.parser``,
stdlib) and normalise each row into a per-team dict::

    {
        "team":      "New York Yankees",
        "G":         15,
        "R":         5.1,
        "H":         8.7,
        "BB":        3.2,
        "HBP":       0.2,
        "HR":        1.4,
        "OBP":       0.328,
    }

The scraper is fail-soft: any exception (BD timeout, parse failure,
missing column) results in ``{}`` — the caller already handles empty
payloads gracefully. Cached for 12h in-memory.

Used by:
  - :mod:`services.mlb_recent_form_split` (fallback + cross-check)
  - :mod:`services.mlb_trend_interpreter` (validation hook)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from typing import Optional

from services.external_sources.base import brightdata_fetch, brightdata_available, direct_fetch

log = logging.getLogger(__name__)

_STATMUSE_TABLE_TTL = timedelta(hours=12)
_TABLE_CACHE: dict[str, tuple[datetime, list[dict]]] = {}

# Public slugs we know work — keep the dict small to avoid drift.
SLUG_TEAM_LAST_N        = "mlb-team-stats-last-{n}-games"
SLUG_RUNS_ALLOWED_LAST_N = "team-runs-allowed-per-game-last-{n}-games"


# ── HTML parser ──────────────────────────────────────────────────────────
class _StatMuseTableParser(HTMLParser):
    """Minimal stdlib HTML parser that captures the first ``<table>``
    encountered. Robust enough for StatMuse's rendered output (the team
    ranking table is the only table on the page).
    """
    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._depth = 0
        self._in_row = False
        self._in_cell = False
        self._row: list[str] = []
        self._cell: list[str] = []
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self._is_header_row = False
        self._captured_first = False

    def handle_starttag(self, tag, attrs):
        if tag == "table" and not self._captured_first:
            self._in_table = True
            self._depth += 1
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._row = []
            # First row that contains <th> is header.
            self._is_header_row = False
        elif self._in_table and tag == "th":
            self._in_cell = True
            self._cell = []
            self._is_header_row = True
        elif self._in_table and tag == "td":
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "th" or tag == "td":
            text = "".join(self._cell).strip()
            self._row.append(text)
            self._in_cell = False
            self._cell = []
        elif tag == "tr" and self._in_row:
            if self._row:
                if self._is_header_row and not self.headers:
                    self.headers = [c.strip() for c in self._row]
                else:
                    self.rows.append(list(self._row))
            self._in_row = False
            self._row = []
        elif tag == "table" and self._in_table:
            self._in_table = False
            self._captured_first = True

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


# ── Cell parsing ─────────────────────────────────────────────────────────
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _to_num(s: str) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    # Special: percentage like ".328" or "0.328".
    try:
        return float(s)
    except ValueError:
        m = _NUM_RE.search(s)
        if not m:
            return None
        try:
            return float(m.group(0))
        except ValueError:
            return None


def _normalise_team_name(name: str) -> str:
    """StatMuse renders team names like "1. New York Yankees" — strip
    the rank prefix and trim whitespace.
    """
    if not name:
        return ""
    n = name.strip()
    # Strip leading rank "1.", "12.", etc.
    n = re.sub(r"^\d+\.\s*", "", n)
    # Strip trailing parenthetical W/L records.
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n)
    return n.strip()


def _row_to_team(headers: list[str], row: list[str]) -> Optional[dict]:
    """Map a (headers, row) pair into a per-team dict using canonical keys.

    Recognised column aliases::

        TEAM | Team | Squad      → "team"
        G | GP | Games          → "G"
        R | R/G | Runs           → "R"
        H | H/G | Hits           → "H"
        BB | BB/G | Walks        → "BB"
        HBP                      → "HBP"
        HR | HR/G | HRs          → "HR"
        OBP                      → "OBP"
        OPS                      → "OPS"

    Returns ``None`` when the row has no recognisable team name.
    """
    alias = {
        "TEAM": "team", "Team": "team", "SQUAD": "team", "Squad": "team",
        "G": "G", "GP": "G", "Games": "G",
        "R": "R", "Runs": "R", "RUNS": "R", "R/G": "R", "RPG": "R",
        "H": "H", "Hits": "H", "HITS": "H", "H/G": "H",
        "BB": "BB", "Walks": "BB", "WALKS": "BB", "BB/G": "BB",
        "HBP": "HBP",
        "HR": "HR", "HRs": "HR", "HOME_RUNS": "HR", "HR/G": "HR",
        "OBP": "OBP", "OPS": "OPS",
        "RA": "RA", "Runs Allowed": "RA", "RA/G": "RA",
    }
    out: dict = {}
    for i, h in enumerate(headers):
        canonical = alias.get(h) or alias.get(h.strip()) or alias.get(h.upper())
        if not canonical or i >= len(row):
            continue
        cell = row[i]
        if canonical == "team":
            out["team"] = _normalise_team_name(cell)
        else:
            out[canonical] = _to_num(cell)
    if not out.get("team"):
        return None
    return out


# ── Fetch helpers ────────────────────────────────────────────────────────
def _cache_get(slug: str) -> Optional[list[dict]]:
    hit = _TABLE_CACHE.get(slug)
    if not hit:
        return None
    exp, val = hit
    if datetime.now(timezone.utc) > exp:
        _TABLE_CACHE.pop(slug, None)
        return None
    return val


def _cache_set(slug: str, value: list[dict]) -> None:
    _TABLE_CACHE[slug] = (datetime.now(timezone.utc) + _STATMUSE_TABLE_TTL, value)


async def _fetch_html(slug: str) -> Optional[str]:
    url = f"https://www.statmuse.com/mlb/ask/{slug}"
    # Prefer Bright Data Web Unlocker (handles statmuse's anti-bot).
    if brightdata_available():
        html = await brightdata_fetch(url, country="us", timeout_sec=20.0)
        if html:
            return html
        log.debug("statmuse: brightdata returned empty for %s, falling back to direct", slug)
    # Last-ditch direct fetch.
    return await direct_fetch(
        url,
        headers={"Accept": "text/html,application/xhtml+xml"},
        timeout_sec=12.0,
    )


def _parse_table_html(html: str) -> list[dict]:
    parser = _StatMuseTableParser()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001
        log.debug("statmuse parser failure: %s", exc)
        return []
    if not parser.headers or not parser.rows:
        return []
    out: list[dict] = []
    for row in parser.rows:
        team = _row_to_team(parser.headers, row)
        if team:
            out.append(team)
    return out


# ── Public API ───────────────────────────────────────────────────────────
async def fetch_team_aggregates(window: int) -> list[dict]:
    """Return the per-team aggregated batting table for the last
    ``window`` games (typically 5 or 15). Cached 12h in-memory. Fail-soft
    — returns an empty list if anything fails.
    """
    if window not in (3, 5, 10, 15, 30, 40):
        # StatMuse only honours common windows. Snap to the closest.
        window = min((3, 5, 10, 15, 30, 40), key=lambda w: abs(w - int(window)))
    slug = SLUG_TEAM_LAST_N.format(n=window)
    cached = _cache_get(slug)
    if cached is not None:
        return cached

    html = await _fetch_html(slug)
    if not html:
        _cache_set(slug, [])
        return []
    rows = _parse_table_html(html)
    _cache_set(slug, rows)
    return rows


def find_team_row(rows: list[dict], team_name: str) -> Optional[dict]:
    """Locate the row that matches ``team_name`` using a loose token-set
    match. Returns ``None`` if no row is close enough.

    StatMuse spells teams like "New York Yankees" while our DB / odds
    feeds sometimes use "NY Yankees" or "Yankees". We compare the
    overlap of significant tokens.
    """
    if not rows or not team_name:
        return None
    target_tokens = {t.lower() for t in re.split(r"\s+", team_name) if len(t) > 2}
    if not target_tokens:
        return None

    best = None
    best_score = 0
    for row in rows:
        row_tokens = {t.lower() for t in re.split(r"\s+", (row.get("team") or "")) if len(t) > 2}
        if not row_tokens:
            continue
        # Score by intersection / target_tokens (Jaccard-ish, biased to target).
        score = len(target_tokens & row_tokens)
        if score > best_score:
            best_score = score
            best = row
    if best and best_score >= 1:
        return best
    return None


async def get_team_recent_form_via_statmuse(team_name: str) -> dict:
    """Pull the last-15 and last-5 windows from StatMuse, normalise into
    the same shape used by ``mlb_recent_form_split.get_team_recent_form``.

    Returns ``{}`` on any failure or when the team can't be located in
    the scraped table.
    """
    try:
        rows15 = await fetch_team_aggregates(15)
        rows5  = await fetch_team_aggregates(5)
    except Exception as exc:  # noqa: BLE001
        log.debug("statmuse fetch_team_aggregates failed: %s", exc)
        return {}
    if not rows15 and not rows5:
        return {}

    row15 = find_team_row(rows15, team_name) or {}
    row5  = find_team_row(rows5,  team_name) or {}
    if not row15 and not row5:
        return {}

    def _g(blk: dict, key: str) -> Optional[float]:
        v = blk.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    # StatMuse already returns per-game averages for "last N" queries.
    runs_l5  = _g(row5,  "R")
    runs_l15 = _g(row15, "R")
    hits_l5  = _g(row5,  "H")
    hits_l15 = _g(row15, "H")
    bb_l5    = _g(row5,  "BB")
    bb_l15   = _g(row15, "BB")
    hbp_l5   = _g(row5,  "HBP")
    hbp_l15  = _g(row15, "HBP")
    hr_l5    = _g(row5,  "HR")
    hr_l15   = _g(row15, "HR")
    obp_l5   = _g(row5,  "OBP")
    obp_l15  = _g(row15, "OBP")

    def _tob(h, b, hp):
        parts = [v for v in (h, b, hp) if v is not None]
        if not parts:
            return None
        return round(sum(parts), 3)

    return {
        "team_id":                   None,   # unknown — StatMuse uses names
        "team_name":                 (row15.get("team") or row5.get("team") or team_name),
        "runs_scored_avg_last_5":    runs_l5,
        "runs_scored_avg_last_15":   runs_l15,
        "hits_avg_last_5":           hits_l5,
        "hits_avg_last_15":          hits_l15,
        "walks_avg_last_5":          bb_l5,
        "walks_avg_last_15":         bb_l15,
        "hbp_avg_last_5":            hbp_l5,
        "hbp_avg_last_15":           hbp_l15,
        "home_runs_avg_last_5":      hr_l5,
        "home_runs_avg_last_15":     hr_l15,
        "times_on_base_avg_last_5":  _tob(hits_l5,  bb_l5,  hbp_l5),
        "times_on_base_avg_last_15": _tob(hits_l15, bb_l15, hbp_l15),
        "obp_last_5":                obp_l5,
        "obp_last_15":               obp_l15,
        "games_played_last_5":       int(_g(row5,  "G") or 0),
        "games_played_last_15":      int(_g(row15, "G") or 0),
        "source":                    "statmuse",
    }


def compare_forms(primary: dict, secondary: dict, *, threshold_pct: float = 10.0) -> dict:
    """Compare an MLB-Stats-API form payload (``primary``) against a
    StatMuse one (``secondary``) and return a discrepancy report.

    A discrepancy is flagged when the absolute % delta exceeds
    ``threshold_pct`` for any of the headline metrics.
    """
    metrics = (
        "runs_scored_avg_last_5",  "runs_scored_avg_last_15",
        "hits_avg_last_5",         "hits_avg_last_15",
        "walks_avg_last_5",        "walks_avg_last_15",
        "home_runs_avg_last_5",    "home_runs_avg_last_15",
    )
    issues: list[dict] = []
    for k in metrics:
        a = primary.get(k)
        b = secondary.get(k)
        if a is None or b is None:
            continue
        try:
            af, bf = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if abs(af) < 0.1 and abs(bf) < 0.1:
            continue
        denom = max(abs(af), abs(bf), 0.1)
        pct = abs(af - bf) / denom * 100.0
        if pct >= threshold_pct:
            issues.append({
                "metric": k,
                "primary": round(af, 3),
                "secondary": round(bf, 3),
                "diff_pct": round(pct, 2),
            })
    return {
        "checked_metrics": list(metrics),
        "issues":          issues,
        "match":           len(issues) == 0,
    }


__all__ = [
    "fetch_team_aggregates",
    "find_team_row",
    "get_team_recent_form_via_statmuse",
    "compare_forms",
    "_parse_table_html",
    "_row_to_team",
    "_normalise_team_name",
    "_StatMuseTableParser",
    "_TABLE_CACHE",
]
