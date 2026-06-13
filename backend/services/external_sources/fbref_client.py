"""Phase F85.1 — FBref public scraper / parser for football xG.

This module is the **primary public source** for advanced football
statistics (xG, npxG, xGA, npxGA, shots, SoT, possession) when our
licensed providers (TheStatsAPI / API-Sports) don't carry them — most
commonly for international friendlies, lower-tier domestic leagues and
fresh competitions where xG is rare.

Two responsibilities live here:

1. ``resolve_fbref_team_url(client, team_name, *, country=None, db=None)``
   maps an internal team name to its FBref ``/en/squads/<id>/<slug>``
   URL. MVP uses a curated in-process mapping table with light alias /
   accent / case normalisation. Phase 2 (out of scope here) will fall
   back to a search-page scrape.

2. ``fetch_fbref_team_match_logs(client, team_url, *, limit=15, db=None)``
   downloads the team page (via :func:`scrape_do_client.fetch_via_scrapedo`
   to dodge Cloudflare) and extracts the most recent ``limit`` match
   rows. **FBref hides many tables inside HTML comments** (``<!-- … -->``)
   to keep its initial DOM lean. The parser uncomments them before
   handing the HTML to :mod:`selectolax`.

Both functions are **fail-soft**: every error path returns a dict with
``available=False`` plus a stable ``reason_codes`` list — they never
raise. Callers (e.g. :mod:`services.football_xg_public_ingestor`) treat
these as misses and gracefully degrade.

No data is invented. If FBref doesn't expose xG for a team (e.g. some
international windows), ``xg_available=false`` is returned and the
upstream layer must not synthesise values from goals.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from selectolax.parser import HTMLParser

log = logging.getLogger("fbref_client")

# ─────────────────────────────────────────────────────────────────────
# Constants & config
# ─────────────────────────────────────────────────────────────────────
FBREF_BASE = "https://fbref.com"
FBREF_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 11_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_FETCH_TIMEOUT = float(os.environ.get("FBREF_FETCH_TIMEOUT_SECONDS", "8"))
CACHE_TTL_HOURS      = int(os.environ.get("FBREF_CACHE_TTL_HOURS", "12"))

# Reason codes — stable strings for downstream log parsing and the
# editorial layer. NEVER rename without updating the test suite.
RC_TEAM_URL_MISSING   = "FBREF_TEAM_URL_MISSING"
RC_UNAVAILABLE        = "FBREF_UNAVAILABLE"
RC_RATE_LIMITED       = "FBREF_RATE_LIMITED"
RC_PARSE_FAILED       = "FBREF_PARSE_FAILED"
RC_TABLE_NOT_FOUND    = "FBREF_TABLE_NOT_FOUND"
RC_XG_COLUMNS_MISSING = "FBREF_XG_COLUMNS_NOT_FOUND"
RC_LOGS_AVAILABLE     = "FBREF_LOGS_AVAILABLE"

# ─────────────────────────────────────────────────────────────────────
# MVP team-name → FBref URL mapping
# ─────────────────────────────────────────────────────────────────────
# The list below is intentionally short — it covers the international
# friendlies / Copa América / Eurocopa axis where licensed feeds tend to
# miss xG. Additional clubs are populated lazily from Mongo at runtime
# via :func:`resolve_fbref_team_url` (see schema in the doc-string).
#
# IMPORTANT: each entry MUST hold at least one alias matching the
# normalised lower-cased ASCII name (no accents). The normaliser handles
# the rest.
_FBREF_TEAM_MAPPING: dict[str, dict[str, Any]] = {
    "united states": {
        "url":     "https://fbref.com/en/squads/6050555d/United-States-Men-Stats",
        "aliases": ["usa", "united states", "usmnt", "estados unidos",
                    "united states men"],
        "team_type": "national_team",
    },
    "paraguay": {
        "url":     "https://fbref.com/en/squads/b8f1bbb1/Paraguay-Men-Stats",
        "aliases": ["paraguay", "paraguay men"],
        "team_type": "national_team",
    },
    "mexico": {
        "url":     "https://fbref.com/en/squads/9c5fbce0/Mexico-Men-Stats",
        "aliases": ["mexico", "mexico men", "mexicо", "méxico"],
        "team_type": "national_team",
    },
    "brazil": {
        "url":     "https://fbref.com/en/squads/12d5cfeb/Brazil-Men-Stats",
        "aliases": ["brazil", "brasil", "brazil men"],
        "team_type": "national_team",
    },
    "argentina": {
        "url":     "https://fbref.com/en/squads/a514f8a8/Argentina-Men-Stats",
        "aliases": ["argentina", "argentina men"],
        "team_type": "national_team",
    },
    "uruguay": {
        "url":     "https://fbref.com/en/squads/a83bd4ab/Uruguay-Men-Stats",
        "aliases": ["uruguay", "uruguay men"],
        "team_type": "national_team",
    },
    "germany": {
        "url":     "https://fbref.com/en/squads/4d224fe8/Germany-Men-Stats",
        "aliases": ["germany", "alemania", "deutschland", "germany men"],
        "team_type": "national_team",
    },
    "france": {
        "url":     "https://fbref.com/en/squads/4f3349a2/France-Men-Stats",
        "aliases": ["france", "francia", "france men"],
        "team_type": "national_team",
    },
    "spain": {
        "url":     "https://fbref.com/en/squads/e2d8892c/Spain-Men-Stats",
        "aliases": ["spain", "espana", "españa", "spain men"],
        "team_type": "national_team",
    },
    "england": {
        "url":     "https://fbref.com/en/squads/0eb73e51/England-Men-Stats",
        "aliases": ["england", "inglaterra", "england men"],
        "team_type": "national_team",
    },
}


def _normalise_name(value: str) -> str:
    """Lower-case, strip accents, drop ``National Team`` suffix and
    collapse whitespace. Used as the lookup key into the mapping table.
    """
    if not isinstance(value, str):
        return ""
    s = unicodedata.normalize("NFKD", value)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    # Drop common trailing modifiers.
    for suffix in (" national team", " national football team", " men"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    # Common aliases that survive accent stripping.
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


async def _load_mongo_mapping(db, team_norm: str) -> Optional[dict]:
    """Look up an FBref team URL in the Mongo ``external_team_mappings``
    collection. Fail-soft: returns ``None`` on any error / miss."""
    if db is None or not team_norm:
        return None
    try:
        doc = await db["external_team_mappings"].find_one({
            "provider": "fbref",
            "$or": [
                {"team_name_norm": team_norm},
                {"aliases_norm":   team_norm},
            ],
        })
    except Exception as exc:  # noqa: BLE001
        log.debug("[fbref] mongo mapping lookup failed for %r: %s",
                  team_norm, exc)
        return None
    if not doc:
        return None
    url = doc.get("fbref_team_url") or doc.get("url")
    if not url:
        return None
    return {
        "url":       url,
        "team_type": doc.get("team_type"),
        "source":    "mongo",
    }


# ─────────────────────────────────────────────────────────────────────
# Phase 2 (F85 Phase 2) — search-page resolver + fuzzy matching
# ─────────────────────────────────────────────────────────────────────
# Minimum SequenceMatcher.ratio() required to accept a search-page hit
# as the canonical match. 0.78 is the sweet spot we hit during empirical
# testing on national-team names where the FBref slug carries "Men"
# suffix (e.g. "Paraguay Men").
SEARCH_FUZZY_THRESHOLD = float(os.environ.get(
    "FBREF_SEARCH_FUZZY_THRESHOLD", "0.78",
))


def _fuzzy_similarity(a: str, b: str) -> float:
    """0.0–1.0 similarity score between two team-name strings, computed
    over their **normalised** forms (no accents, no case, no suffixes)."""
    from difflib import SequenceMatcher
    na, nb = _normalise_name(a), _normalise_name(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _parse_fbref_search_results(html: str) -> list[dict]:
    """Extract club / national-team candidates from FBref's search-page
    HTML. Returns ``[{name, url, team_type, country}]`` in document
    order. Always best-effort; returns ``[]`` on any parse error."""
    if not isinstance(html, str) or not html:
        return []
    try:
        merged = _uncomment_html(html)
        tree = HTMLParser(merged)
    except Exception as exc:  # noqa: BLE001
        log.debug("[fbref] search HTMLParser failed: %s", exc)
        return []

    candidates: list[dict] = []
    # FBref groups search hits under <div class="search-item">.
    # Each item carries a heading + <div class="search-item-name"><a>.
    for item in tree.css(".search-item"):
        name_node = item.css_first(".search-item-name a, a")
        if name_node is None:
            continue
        href = (name_node.attributes or {}).get("href") or ""
        if not href.startswith("/en/squads/"):
            continue
        full_url = FBREF_BASE + href if href.startswith("/") else href
        # The container's class also flags the result type
        # (e.g. ``search-item-club``, ``search-item-national-team``).
        item_classes = (item.attributes or {}).get("class") or ""
        if "national-team" in item_classes:
            team_type = "national_team"
        elif "club" in item_classes:
            team_type = "club"
        else:
            team_type = None
        candidates.append({
            "name":      _normalise_name(name_node.text(strip=True)),
            "display":   (name_node.text(strip=True) or "").strip(),
            "url":       full_url,
            "team_type": team_type,
        })

    # Fallback layout: when only ONE match exists, FBref redirects to
    # the team page directly. Callers detect this via the response
    # status (302 / final URL match) and craft the candidate themselves.
    return candidates


async def _search_fbref_for_team(
    client: httpx.AsyncClient,
    query: str,
    *,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> list[dict]:
    """Issue a search request to FBref and return candidate hits.

    Honours the test-friendly fetch policy from ``_fetch_html`` — when
    a caller-supplied client is present we use it (so MockTransport
    works); otherwise we go through scrape.do.
    """
    if not query or not isinstance(query, str):
        return []
    from urllib.parse import quote_plus
    url = f"{FBREF_BASE}/en/search/search.fcgi?search={quote_plus(query)}"

    # When the search returns exactly one match, FBref usually redirects
    # straight to ``/en/squads/<id>/<slug>``. We detect both possibilities
    # by inspecting the response status AND the candidates the search
    # page would have rendered.
    if client is not None:
        try:
            r = await client.get(
                url, timeout=timeout, follow_redirects=False,
                headers={"User-Agent": FBREF_USER_AGENT},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[fbref] search fetch failed for %r: %s", query, exc)
            return []
        if r.status_code in (301, 302, 303, 307, 308):
            target = r.headers.get("location", "")
            if "/en/squads/" in target:
                # Single-hit redirect → synth one candidate.
                full = target if target.startswith("http") else FBREF_BASE + target
                return [{
                    "name":      _normalise_name(query),
                    "display":   query,
                    "url":       full,
                    "team_type": None,
                }]
            return []
        if r.status_code != 200 or not r.text:
            return []
        return _parse_fbref_search_results(r.text)

    # Production path.
    try:
        from .. import scrape_do_client as sdc
    except Exception:  # noqa: BLE001
        return []
    html = await sdc.fetch_via_scrapedo(url, timeout=timeout)
    if not html:
        return []
    return _parse_fbref_search_results(html)


def _best_fuzzy_hit(
    candidates: list[dict],
    query: str,
    *,
    threshold: float = SEARCH_FUZZY_THRESHOLD,
) -> Optional[dict]:
    """Return the best candidate whose normalised name is ``>= threshold``
    similar to the query, or ``None``."""
    if not candidates:
        return None
    best: Optional[dict] = None
    best_score = 0.0
    for c in candidates:
        score = _fuzzy_similarity(query, c.get("display") or c.get("name") or "")
        if score >= threshold and score > best_score:
            best, best_score = c, score
    if best:
        best = dict(best)
        best["fuzzy_score"] = round(best_score, 3)
    return best


async def _persist_search_hit_to_mongo(
    db, *, team_name: str, team_norm: str, hit: dict,
) -> None:
    """Cache a successful Phase-2 hit into ``external_team_mappings``.
    Fail-soft."""
    if db is None or not hit:
        return
    doc = {
        "provider":        "fbref",
        "team_name":       team_name,
        "team_name_norm":  team_norm,
        "aliases_norm":    [team_norm, _normalise_name(hit.get("display") or "")],
        "fbref_team_url":  hit.get("url"),
        "team_type":       hit.get("team_type"),
        "discovered_via":  "search_fuzzy",
        "fuzzy_score":     hit.get("fuzzy_score"),
        "updated_at":      datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db["external_team_mappings"].update_one(
            {"provider": "fbref", "team_name_norm": team_norm},
            {"$set": doc, "$setOnInsert": {
                "created_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[fbref] mongo cache write failed for %r: %s", team_norm, exc)


async def resolve_fbref_team_url(
    client: Optional[httpx.AsyncClient],
    team_name: str,
    *,
    country: Optional[str] = None,  # noqa: ARG001 — reserved for Phase 2 search
    db=None,
) -> dict:
    """Return ``{url, team_type, source, available, reason_codes}`` for
    ``team_name`` or an ``available=False`` payload when we cannot map
    it. Never raises."""
    if not team_name or not isinstance(team_name, str):
        return {"available": False, "reason_codes": [RC_TEAM_URL_MISSING]}
    norm = _normalise_name(team_name)
    if not norm:
        return {"available": False, "reason_codes": [RC_TEAM_URL_MISSING]}

    # 1) Curated in-process table — fastest path, zero I/O.
    direct = _FBREF_TEAM_MAPPING.get(norm)
    if not direct:
        # Try alias lookup.
        for canonical, entry in _FBREF_TEAM_MAPPING.items():
            aliases = entry.get("aliases") or []
            if norm == canonical or any(_normalise_name(a) == norm for a in aliases):
                direct = entry
                break
    if direct:
        return {
            "available":  True,
            "url":        direct["url"],
            "team_type":  direct.get("team_type"),
            "source":     "static_mapping",
            "team_name":  team_name,
            "team_name_norm": norm,
        }

    # 2) Mongo-cached mapping (extensions populated by ops/manual entry).
    mongo_hit = await _load_mongo_mapping(db, norm)
    if mongo_hit:
        return {
            "available":  True,
            "url":        mongo_hit["url"],
            "team_type":  mongo_hit.get("team_type"),
            "source":     "mongo_mapping",
            "team_name":  team_name,
            "team_name_norm": norm,
        }

    # 3) Phase 2 (F85 Phase 2) — search-page scrape + fuzzy matching.
    #    Only attempted when a real httpx client is available (so we
    #    never accidentally do I/O during unit tests that pass
    #    ``client=None``).
    if client is not None:
        try:
            candidates = await _search_fbref_for_team(client, team_name)
        except Exception as exc:  # noqa: BLE001
            log.debug("[fbref] search Phase 2 crashed: %s", exc)
            candidates = []
        hit = _best_fuzzy_hit(candidates, team_name)
        if hit:
            # Best-effort cache so subsequent lookups skip the network.
            try:
                await _persist_search_hit_to_mongo(
                    db, team_name=team_name, team_norm=norm, hit=hit,
                )
            except Exception:  # noqa: BLE001
                pass
            return {
                "available":      True,
                "url":            hit["url"],
                "team_type":      hit.get("team_type"),
                "source":         "search_fuzzy",
                "team_name":      team_name,
                "team_name_norm": norm,
                "fuzzy_score":    hit.get("fuzzy_score"),
            }

    log.debug("[fbref] team URL not found for %r (norm=%r)", team_name, norm)
    return {
        "available":     False,
        "reason_codes":  [RC_TEAM_URL_MISSING],
        "team_name":     team_name,
        "team_name_norm": norm,
    }


# ─────────────────────────────────────────────────────────────────────
# HTML extraction helpers
# ─────────────────────────────────────────────────────────────────────
# FBref wraps many tables in HTML comments to defer rendering. We need
# the union of "live" DOM + "commented-out" DOM. Regex is sufficient
# here because FBref's comments are well-formed and never nested.
_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def _uncomment_html(html: str) -> str:
    """Strip ``<!-- ... -->`` markers from the HTML payload while
    keeping the inner content. The result remains valid HTML for
    selectolax."""
    if not isinstance(html, str) or "<!--" not in html:
        return html or ""
    return _HTML_COMMENT_RE.sub(lambda m: m.group(1) or "", html)


def _column_index_by_data_stat(table_node) -> dict[str, int]:
    """Build a ``{data-stat: column index}`` map for a ``<table>``. FBref
    tags every header cell with a ``data-stat`` attribute that is stable
    across renderings (e.g. ``xg_for``, ``date``, ``opponent``)."""
    if table_node is None:
        return {}
    mapping: dict[str, int] = {}
    headers = table_node.css("thead tr th")
    for idx, th in enumerate(headers):
        stat = (th.attributes or {}).get("data-stat")
        if stat:
            mapping[stat] = idx
    return mapping


def _row_cells(row_node) -> list:
    """All ``<th>`` + ``<td>`` cells of a row, in document order."""
    return row_node.css("th, td")


def _cell_text(cell) -> str:
    return (cell.text(strip=True) or "").strip()


def _to_float(raw: str) -> Optional[float]:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw in {"-", "–", "—", "N/A"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _to_int(raw: str) -> Optional[int]:
    f = _to_float(raw)
    return int(f) if f is not None else None


# Columns we care about. Each entry maps the canonical key in our
# normalised output to the candidate ``data-stat`` keys FBref may use
# (the schema changes occasionally between tables / seasons).
_COLUMN_CANDIDATES = {
    "date":           ("date",),
    "competition":    ("comp", "comp_level"),
    "venue":          ("venue",),
    "result":         ("result",),
    "score":          ("score", "result"),
    "opponent":       ("opponent",),
    "xg_for":         ("xg_for",),
    "xg_against":     ("xg_against",),
    "npxg_for":       ("npxg_for",),
    "npxg_against":   ("npxg_against",),
    "shots_for":      ("shots_for", "shots"),
    "shots_against":  ("shots_against",),
    "sot_for":        ("shots_on_target_for", "shots_on_target"),
    "sot_against":    ("shots_on_target_against",),
    "possession":     ("possession",),
}


def _extract_row(row, col_idx: dict[str, int], cells: list) -> dict:
    """Build one normalised match-log dict from a single ``<tr>``."""
    def _by_stat(stat_key: str) -> Optional[str]:
        for candidate in _COLUMN_CANDIDATES.get(stat_key, ()):
            idx = col_idx.get(candidate)
            if idx is not None and idx < len(cells):
                txt = _cell_text(cells[idx])
                if txt:
                    return txt
        return None

    return {
        "date":          _by_stat("date"),
        "competition":   _by_stat("competition"),
        "venue":         _by_stat("venue"),
        "result":        _by_stat("result"),
        "score":         _by_stat("score"),
        "opponent":      _by_stat("opponent"),
        "xg_for":        _to_float(_by_stat("xg_for")),
        "xg_against":    _to_float(_by_stat("xg_against")),
        "npxg_for":      _to_float(_by_stat("npxg_for")),
        "npxg_against":  _to_float(_by_stat("npxg_against")),
        "shots_for":     _to_int(_by_stat("shots_for")),
        "shots_against": _to_int(_by_stat("shots_against")),
        "sot_for":       _to_int(_by_stat("sot_for")),
        "sot_against":   _to_int(_by_stat("sot_against")),
        "possession":    _to_int(_by_stat("possession")),
    }


# Order of priority: prefer Scores & Fixtures (carries xG when available),
# fall back to "matchlogs_for" then any table with the right columns.
_PREFERRED_TABLE_IDS = (
    "matchlogs_for",
    "stats_team_matchlogs",
    "matchlogs_all_comps",
    "stats_squads_standard_for",
)


def _find_match_log_table(tree: HTMLParser):
    """Return the best candidate ``<table>`` for match logs."""
    if tree is None:
        return None, None
    # 1) Preferred IDs.
    for tid in _PREFERRED_TABLE_IDS:
        node = tree.css_first(f"table#{tid}")
        if node is not None:
            return node, tid
    # 2) Fallback: ANY table that has both an ``opponent`` and an ``xg_for``
    # data-stat header.
    for node in tree.css("table"):
        idx = _column_index_by_data_stat(node)
        if "opponent" in idx and ("xg_for" in idx or "xg" in idx):
            tid = (node.attributes or {}).get("id") or "anonymous_table"
            return node, tid
    # 3) Last-resort: ANY table with an opponent header (no xG).
    for node in tree.css("table"):
        idx = _column_index_by_data_stat(node)
        if "opponent" in idx:
            tid = (node.attributes or {}).get("id") or "anonymous_table"
            return node, tid
    return None, None


def parse_fbref_team_html(html: str, *, limit: int = 15) -> dict:
    """Parse a FBref squad / team page HTML payload into a list of
    recent match dicts.

    Returns
    -------
    dict
        ``{available, source, source_url, table_id, xg_available,
        logs, reason_codes}``.
    """
    if not isinstance(html, str) or not html:
        return {"available": False, "reason_codes": [RC_UNAVAILABLE]}

    try:
        merged = _uncomment_html(html)
        tree = HTMLParser(merged)
    except Exception as exc:  # noqa: BLE001
        log.warning("[fbref] HTMLParser failed: %s", exc)
        return {"available": False, "reason_codes": [RC_PARSE_FAILED]}

    table, table_id = _find_match_log_table(tree)
    if table is None:
        return {"available": False, "reason_codes": [RC_TABLE_NOT_FOUND]}

    col_idx = _column_index_by_data_stat(table)
    if not col_idx:
        return {"available": False, "reason_codes": [RC_PARSE_FAILED]}

    xg_available = "xg_for" in col_idx and "xg_against" in col_idx
    rows = table.css("tbody tr")
    logs: list[dict] = []
    for row in rows:
        # Skip spacer / sub-header rows ("thead" class FBref injects).
        row_class = (row.attributes or {}).get("class") or ""
        if "thead" in row_class:
            continue
        cells = _row_cells(row)
        if not cells:
            continue
        record = _extract_row(row, col_idx, cells)
        # Require at least an opponent + a date to keep the row.
        if record.get("opponent") and record.get("date"):
            logs.append(record)

    # FBref usually lists oldest → newest. We want newest first.
    logs.reverse()
    if limit:
        logs = logs[:limit]

    reason_codes = [RC_LOGS_AVAILABLE if logs else RC_TABLE_NOT_FOUND]
    if not xg_available:
        reason_codes.append(RC_XG_COLUMNS_MISSING)

    return {
        "available":    bool(logs),
        "source":       "fbref",
        "table_id":     table_id,
        "xg_available": xg_available,
        "logs":         logs,
        "reason_codes": reason_codes,
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# Network fetcher (scrape.do front; httpx fallback for tests)
# ─────────────────────────────────────────────────────────────────────
async def _fetch_html(
    client: Optional[httpx.AsyncClient],
    url: str,
    *,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> Optional[str]:
    """Fetch a FBref page. Prefer scrape.do (Cloudflare-tolerant) but
    fall back to a plain ``httpx`` GET when the caller passes a
    ``MockTransport``-backed client (tests)."""
    # When tests pass a client, use it directly — they bypass scrape.do
    # via their MockTransport. The presence of a non-default client is
    # the signal.
    if client is not None:
        try:
            r = await client.get(url, timeout=timeout,
                                  headers={"User-Agent": FBREF_USER_AGENT})
        except Exception as exc:  # noqa: BLE001
            log.warning("[fbref] httpx fetch failed for %s: %s", url, exc)
            return None
        if r.status_code == 429:
            log.warning("[fbref] rate limited for %s", url)
            return "__RATE_LIMITED__"
        if r.status_code != 200 or not r.text:
            return None
        return r.text

    # Production path: scrape.do.
    try:
        from .. import scrape_do_client as sdc
    except Exception:  # noqa: BLE001
        return None
    return await sdc.fetch_via_scrapedo(url, timeout=timeout)


async def fetch_fbref_team_match_logs(
    client: Optional[httpx.AsyncClient],
    team_url: str,
    *,
    limit: int = 15,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
    db=None,  # noqa: ARG001 — reserved for the per-team cache (next iteration)
) -> dict:
    """Download and parse a FBref team / squad page. Fail-soft."""
    if not team_url or not isinstance(team_url, str):
        return {"available": False, "reason_codes": [RC_TEAM_URL_MISSING]}

    html = await _fetch_html(client, team_url, timeout=timeout)
    if html == "__RATE_LIMITED__":
        return {"available": False, "reason_codes": [RC_RATE_LIMITED],
                "source_url": team_url}
    if not html:
        return {"available": False, "reason_codes": [RC_UNAVAILABLE],
                "source_url": team_url}

    out = parse_fbref_team_html(html, limit=limit)
    out["source_url"] = team_url
    return out


__all__ = [
    # Public API
    "resolve_fbref_team_url", "fetch_fbref_team_match_logs",
    "parse_fbref_team_html",
    # Phase 2 helpers
    "_search_fbref_for_team", "_parse_fbref_search_results",
    "_best_fuzzy_hit", "_fuzzy_similarity", "_persist_search_hit_to_mongo",
    "SEARCH_FUZZY_THRESHOLD",
    # Internal helpers (exported for tests)
    "_uncomment_html", "_normalise_name",
    # Reason codes
    "RC_TEAM_URL_MISSING", "RC_UNAVAILABLE", "RC_RATE_LIMITED",
    "RC_PARSE_FAILED", "RC_TABLE_NOT_FOUND", "RC_XG_COLUMNS_MISSING",
    "RC_LOGS_AVAILABLE",
    # Tunables
    "FBREF_BASE", "DEFAULT_FETCH_TIMEOUT", "CACHE_TTL_HOURS",
]
