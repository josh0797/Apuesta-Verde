"""Football competition allowlist & matching helpers.

Centralizes which competitions the engine considers for ingestion, hydration,
and analysis. The motivation is **speed and cost**: ignoring 95% of the global
football calendar (lower divisions, friendlies, youth, regional leagues) lets
the scraper, odds hydrator and LLM pipeline run an order of magnitude faster.

Design:
    - Tiered allowlist (1/2/3) with priority scores.
    - Aliases per competition to absorb the naming drift of
      API-Sports / ESPN / Sofascore / Flashscore / scraped feeds.
    - Case-insensitive + accent-insensitive + format-flexible matching
      (\"Premier League - England\", \"Liga MX, Clausura - Mexico\", etc.).

Public API:
    FOOTBALL_COMPETITION_TIERS     — declarative tier config
    normalize_competition_name()   — slugify for matching
    get_competition_tier(name)     — 'tier_1' | 'tier_2' | 'tier_3' | None
    is_allowed_competition(name)   — bool
    get_competition_priority(name) — int (0 if disallowed)
    get_competition_meta(name)     — full descriptor dict (canonical name,
                                     tier, priority, type, region, etc.)
"""
from __future__ import annotations

import os
import re
import unicodedata
from functools import lru_cache
from typing import Optional

# Read from env, defaulting to all three tiers enabled.
_ALLOWED_TIERS_ENV = os.environ.get("FOOTBALL_ALLOWED_TIERS", "tier_1,tier_2,tier_3")
_TIER_3_ENABLED = os.environ.get("FOOTBALL_ENABLE_TIER_3", "true").lower() in ("1", "true", "yes")

ALLOWED_TIERS: set[str] = {
    t.strip() for t in _ALLOWED_TIERS_ENV.split(",") if t.strip()
}
if not _TIER_3_ENABLED:
    ALLOWED_TIERS.discard("tier_3")

# Tunable env (consumed by data_ingestion.py)
MAX_MATCHES_TO_HYDRATE = int(os.environ.get("FOOTBALL_MAX_MATCHES_TO_HYDRATE", "30"))
MAX_MATCHES_TO_ANALYZE = int(os.environ.get("FOOTBALL_MAX_MATCHES_TO_ANALYZE", "12"))


# ── Allowlist ────────────────────────────────────────────────────────────────
# Tier priorities are intentionally well spaced so future re-rankings (live
# match boost, kickoff proximity, etc.) can stay within their tier band.
FOOTBALL_COMPETITION_TIERS: dict[str, dict] = {
    "tier_1": {
        "priority": 100,
        "competitions": [
            {
                "name": "FIFA World Cup",
                "aliases": [
                    "FIFA World Cup", "World Cup", "Copa Mundial",
                    "Mundial", "Coupe du Monde", "FIFA WC",
                ],
                "type": "international", "region": "World",
            },
            {
                "name": "UEFA Champions League",
                "aliases": [
                    "UEFA Champions League", "Champions League",
                    "UCL", "Liga de Campeones",
                ],
                "type": "continental", "region": "Europe",
            },
            {
                "name": "Liga MX",
                "aliases": [
                    "Liga MX", "Liga MX, Clausura", "Liga MX, Apertura",
                    "Mexico Liga MX", "Primera Division", "Liga BBVA MX",
                ],
                "type": "league", "region": "Mexico",
            },
            {
                "name": "Premier League",
                "aliases": [
                    "Premier League", "England Premier League", "EPL",
                    "English Premier League",
                ],
                "type": "league", "region": "England",
            },
            {
                "name": "LaLiga",
                "aliases": [
                    "LaLiga", "La Liga", "Spain LaLiga", "Primera Division",
                    "Primera División", "Spain Primera Division",
                    "La Liga Santander", "LaLiga EA Sports",
                ],
                "type": "league", "region": "Spain",
            },
            {
                "name": "Serie A",
                "aliases": [
                    "Serie A", "Italy Serie A", "Italian Serie A",
                    "Lega Serie A", "Serie A TIM",
                ],
                "type": "league", "region": "Italy",
            },
            {
                "name": "Bundesliga",
                "aliases": [
                    "Bundesliga", "Germany Bundesliga", "1. Bundesliga",
                    "German Bundesliga",
                ],
                "type": "league", "region": "Germany",
            },
        ],
    },
    "tier_2": {
        "priority": 70,
        "competitions": [
            {
                "name": "Ligue 1",
                "aliases": [
                    "Ligue 1", "France Ligue 1", "Ligue 1 Uber Eats",
                    "French Ligue 1",
                ],
                "type": "league", "region": "France",
            },
            {
                "name": "UEFA Europa League",
                "aliases": [
                    "UEFA Europa League", "Europa League", "UEL",
                ],
                "type": "continental", "region": "Europe",
            },
            {
                "name": "Copa América",
                "aliases": [
                    "Copa America", "Copa América", "CONMEBOL Copa America",
                ],
                "type": "international", "region": "South America",
            },
            {
                "name": "UEFA European Championship",
                "aliases": [
                    "UEFA European Championship", "Eurocopa", "Euro",
                    "EURO", "UEFA Euro", "European Championship",
                ],
                "type": "international", "region": "Europe",
            },
            {
                "name": "Copa Libertadores",
                "aliases": [
                    "Copa Libertadores", "CONMEBOL Libertadores",
                    "Libertadores",
                ],
                "type": "continental", "region": "South America",
            },
        ],
    },
    "tier_3": {
        "priority": 40,
        "competitions": [
            # Domestic cups for the same regions where we already track the league
            {
                "name": "FA Cup",
                "aliases": ["FA Cup", "Emirates FA Cup", "English FA Cup"],
                "type": "cup", "region": "England",
            },
            {
                "name": "EFL Cup",
                "aliases": ["EFL Cup", "Carabao Cup", "League Cup"],
                "type": "cup", "region": "England",
            },
            {
                "name": "Copa del Rey",
                "aliases": ["Copa del Rey", "Spain Copa del Rey"],
                "type": "cup", "region": "Spain",
            },
            {
                "name": "Coppa Italia",
                "aliases": ["Coppa Italia", "Italy Coppa Italia"],
                "type": "cup", "region": "Italy",
            },
            {
                "name": "DFB-Pokal",
                "aliases": ["DFB-Pokal", "DFB Pokal", "Germany DFB Pokal"],
                "type": "cup", "region": "Germany",
            },
            {
                "name": "Coupe de France",
                "aliases": ["Coupe de France", "France Coupe de France"],
                "type": "cup", "region": "France",
            },
            {
                "name": "UEFA Conference League",
                "aliases": [
                    "UEFA Conference League", "Conference League", "UECL",
                ],
                "type": "continental", "region": "Europe",
            },
            {
                "name": "CONCACAF Gold Cup",
                "aliases": [
                    "Gold Cup", "CONCACAF Gold Cup", "Copa de Oro",
                ],
                "type": "international", "region": "North America",
            },
            {
                "name": "FIFA Club World Cup",
                "aliases": [
                    "FIFA Club World Cup", "Club World Cup",
                    "Mundial de Clubes",
                ],
                "type": "international", "region": "World",
            },
        ],
    },
}


# ── Normalization & flexible matching ────────────────────────────────────────
_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_REGION_TAIL_RE = re.compile(
    r"\s*(?:[-,–]+\s*[a-záéíóúñ ]+|\([a-záéíóúñ ]+\))\s*$",
    flags=re.IGNORECASE,
)


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


def normalize_competition_name(name: Optional[str]) -> str:
    """Lossy slug used purely for fuzzy alias matching.

    Strips accents, punctuation, region tails like ' - England' or ' (Spain)',
    collapses whitespace, lowercases. NEVER use for display.
    """
    if not name:
        return ""
    s = str(name).strip()
    # Drop a trailing region/country tail. Run twice in case the API ships
    # both ' - Spain' AND ', Clausura' patterns.
    s = _REGION_TAIL_RE.sub("", s)
    s = _REGION_TAIL_RE.sub("", s)
    s = _strip_accents(s).lower()
    s = _PUNCT_RE.sub(" ", s).strip()
    return s


# Build a reverse-lookup index alias→(tier, comp_meta) once.
@lru_cache(maxsize=1)
def _alias_index() -> dict[str, tuple[str, dict]]:
    idx: dict[str, tuple[str, dict]] = {}
    for tier_key, tier_cfg in FOOTBALL_COMPETITION_TIERS.items():
        for comp in tier_cfg["competitions"]:
            for alias in [comp["name"], *comp.get("aliases", [])]:
                norm = normalize_competition_name(alias)
                if norm and norm not in idx:
                    idx[norm] = (tier_key, comp)
    return idx


def get_competition_meta(league_name: Optional[str]) -> Optional[dict]:
    """Return full descriptor + tier_key + priority for the given league.

    Tries exact normalized match first; then a substring fallback to absorb
    feeds that ship things like 'UEFA Champions League - Quarter Final'.
    """
    if not league_name:
        return None
    norm = normalize_competition_name(league_name)
    if not norm:
        return None
    idx = _alias_index()
    hit = idx.get(norm)
    if not hit:
        # Substring fallback: ONLY accept when the registered alias appears
        # FULLY inside the input (e.g. 'uefa champions league quarter final'
        # → match 'uefa champions league'). The reverse direction is unsafe:
        # 'championship' must NOT match 'european championship'. Longest
        # alias wins so 'uefa champions league' beats 'champions league' when
        # both are present.
        candidates = [
            (alias, payload) for alias, payload in idx.items()
            if alias and alias in norm
        ]
        candidates.sort(key=lambda kv: -len(kv[0]))
        hit = candidates[0][1] if candidates else None
    if not hit:
        return None
    tier_key, comp = hit
    tier_cfg = FOOTBALL_COMPETITION_TIERS[tier_key]
    return {
        "canonical_name": comp["name"],
        "tier": tier_key,
        "priority": tier_cfg["priority"],
        "type": comp.get("type"),
        "region": comp.get("region"),
    }


# Top-5 European leagues. The single most concentrated source of meaningful
# football data we have (best odds coverage, deepest standings/squads).
# Used for live analysis filtering (`big_five_only=true` flag on analysis/run).
BIG_FIVE_LEAGUES: tuple[str, ...] = (
    "Premier League",
    "LaLiga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
)

# API-Sports league IDs for the Big Five — the AUTHORITATIVE source of truth.
# Name-based matching alone is brittle because "Bundesliga" matches both the
# German top tier AND the Austrian top tier. Use the league_id whenever the
# match payload exposes it.
#   • Premier League (England) → 39
#   • LaLiga (Spain)           → 140
#   • Serie A (Italy)          → 135
#   • Bundesliga (Germany)     → 78
#   • Ligue 1 (France)         → 61
BIG_FIVE_LEAGUE_IDS: set[int] = {39, 140, 135, 78, 61}


def is_big_five(league_name: Optional[str], league_id: Optional[int] = None) -> bool:
    """True iff the league resolves to one of the top-5 European leagues.

    Prefer `league_id` (deterministic) when available; fall back to canonical
    name matching for older code paths that haven't been threaded through with
    the id yet.
    """
    if league_id is not None:
        try:
            return int(league_id) in BIG_FIVE_LEAGUE_IDS
        except (TypeError, ValueError):
            pass
    meta = get_competition_meta(league_name)
    return bool(meta and meta["canonical_name"] in BIG_FIVE_LEAGUES)


def get_competition_tier(league_name: Optional[str]) -> Optional[str]:
    meta = get_competition_meta(league_name)
    return meta["tier"] if meta else None


def is_allowed_competition(league_name: Optional[str]) -> bool:
    """True iff the competition is in an enabled tier (env-controlled)."""
    tier = get_competition_tier(league_name)
    return bool(tier and tier in ALLOWED_TIERS)


def get_competition_priority(league_name: Optional[str]) -> int:
    meta = get_competition_meta(league_name)
    return meta["priority"] if meta else 0


def annotate_match_competition(match_doc: dict, league_name: Optional[str] = None) -> dict:
    """Mutates and returns the match doc with competition_* metadata fields.

    Adds:
        competition_tier            (tier_1|tier_2|tier_3|None)
        competition_priority        (int)
        competition_canonical_name  (str|None)
        competition_type            ('league'|'cup'|'continental'|'international'|None)
        competition_region          (str|None)
        allowed_competition         (bool)
    """
    name = league_name or match_doc.get("league")
    meta = get_competition_meta(name)
    if meta:
        match_doc["competition_tier"] = meta["tier"]
        match_doc["competition_priority"] = meta["priority"]
        match_doc["competition_canonical_name"] = meta["canonical_name"]
        match_doc["competition_type"] = meta["type"]
        match_doc["competition_region"] = meta["region"]
        match_doc["allowed_competition"] = meta["tier"] in ALLOWED_TIERS
    else:
        match_doc["competition_tier"] = None
        match_doc["competition_priority"] = 0
        match_doc["competition_canonical_name"] = None
        match_doc["competition_type"] = None
        match_doc["competition_region"] = None
        match_doc["allowed_competition"] = False
    return match_doc
