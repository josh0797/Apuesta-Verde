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
        #
        # Defense-in-depth: refuse to substring-match a Tier-1 alias when the
        # raw league name carries a country-prefix that betrays a non-Big-Five
        # competition (e.g. 'Botswana Premier League', 'Belarus Reserve
        # Premier League'). Without this guard the engine would silently
        # promote those leagues to Tier 1 and waste LLM cycles. The
        # `football_quality.py` layer already corrects for this downstream,
        # but doing it here too keeps every caller (is_big_five,
        # is_allowed_competition, scheduler hydration filters, etc.) honest.
        candidates = [
            (alias, payload) for alias, payload in idx.items()
            if alias and alias in norm
        ]
        candidates.sort(key=lambda kv: -len(kv[0]))
        if candidates and _has_non_tier1_country_hint(norm):
            # Keep only candidates that are NOT in tier_1 — substring matches
            # to tier_2/tier_3 (e.g. a cup) remain valid.
            candidates = [
                kv for kv in candidates
                if kv[1] and kv[1][0] != "tier_1"
            ]
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
        # MLB-TS1 Batch 3.5 / Fix 2 — surface TheStatsAPI raw competition
        # ids (if seeded for this league). The aggregator + enrichment
        # consume this to filter / hydrate per-competition queries
        # without hardcoding ids further down the stack.
        "thestatsapi_ids": get_thestatsapi_competition_ids(comp["name"]),
    }


# ── TheStatsAPI competition-id mapping ───────────────────────────────────────
# Maps each canonical competition name to a list of raw TheStatsAPI ids
# (``comp_NNNN`` strings). Multiple ids per league are allowed because
# TheStatsAPI sometimes splits qualifying campaigns / women / playoffs into
# separate comp documents (e.g. ``comp_6107`` for FIFA World Cup 2026 vs
# ``comp_6201`` for FIFA WC Qualifying CONMEBOL). The list is intentionally
# **seeded conservatively** — only ids we've verified via the live API
# response. Add new mappings as they're observed:
#
#   1. Hit ``GET /football/competitions`` (cached 24h).
#   2. Locate the entry whose ``name`` matches one of our canonical names.
#   3. Append ``raw_id`` to the list below.
#
# Empty list = no mapping known yet (consumers must NOT pass
# ``competition_id`` to TheStatsAPI in that case).
THESTATSAPI_COMPETITION_MAP: dict[str, list[str]] = {
    # ── Tier 1 internationals (verified or high-priority discovery) ──
    "FIFA World Cup":                 ["comp_6107"],   # verified 2026-06-03 live response
    "FIFA Club World Cup":            [],
    "UEFA Champions League":          [],
    "UEFA Europa League":             [],
    "UEFA Conference League":         [],
    "UEFA European Championship":     [],
    "UEFA Nations League":            [],
    "UEFA Super Cup":                 [],
    "UEFA Women's European Championship": [],
    "Copa América":                   [],
    "Copa Libertadores":              [],
    "Copa Sudamericana":              [],
    "CONCACAF Gold Cup":              [],
    "CONCACAF Champions League":      ["comp_8649"],   # CONCACAF Champions Cup
    "CONCACAF Nations League":        [],
    "Africa Cup of Nations":          ["comp_1554"],
    "AFC Asian Cup":                  [],
    "AFC Champions League":           ["comp_5432", "comp_8833"],
    # ── Tier 1 European domestic ─────────────────────────────────────
    "Premier League":                 [],
    "LaLiga":                         [],
    "LaLiga 2":                       [],
    "Serie A":                        [],
    "Serie B":                        [],
    "Bundesliga":                     ["comp_4643"],
    "2. Bundesliga":                  ["comp_0406"],
    "Ligue 1":                        [],
    "Ligue 2":                        [],
    "Eredivisie":                     [],
    "Primeira Liga":                  [],
    "Belgian Pro League":             [],
    "Scottish Premiership":           [],
    "Süper Lig":                      [],
    "Swiss Super League":             [],
    "Austrian Bundesliga":            ["comp_4893"],
    "Russian Premier League":         [],
    "Ukrainian Premier League":       [],
    "Greek Super League":             [],
    "Polish Ekstraklasa":             [],
    # ── Tier 1 American domestic ────────────────────────────────────
    "Major League Soccer":            [],
    "MLS":                            [],
    "Liga MX":                        [],
    "Brasileirão Série A":            ["comp_4795"],
    "Brasileirão Série B":            ["comp_1085"],
    "Argentine Primera División":     [],
    "Liga Profesional Argentina":     [],
    # ── Tier 1 Asia / Oceania ───────────────────────────────────────
    "J1 League":                      [],
    "K League 1":                     [],
    "Saudi Pro League":               [],
    "A-League":                       ["comp_6151"],
    "A-League Men":                   ["comp_6151"],
    "Chinese Super League":           ["comp_7712"],
    # ── Tier 2 cups ─────────────────────────────────────────────────
    "FA Cup":                         [],
    "EFL Cup":                        [],
    "EFL Championship":               [],
    "Copa del Rey":                   [],
    "Coppa Italia":                   [],
    "DFB-Pokal":                      [],
    "Coupe de France":                [],
    "Taça de Portugal":               [],
    "KNVB Beker":                     [],
    "Belgian Cup":                    [],
    "Scottish Cup":                   [],
    "Türkiye Kupası":                 [],
    "DFL-Supercup":                   [],
    "Trophée des Champions":          [],
    "Supercopa de España":            [],
    "Supercoppa Italiana":            [],
    "Community Shield":               [],
    # Add more as they're discovered through live `/football/competitions`.
}


def get_thestatsapi_competition_ids(canonical_name: str | None) -> list[str]:
    """Return the list of TheStatsAPI raw competition ids for a league.

    Returns ``[]`` when:
      * ``canonical_name`` is None / empty
      * the canonical name is not in the mapping
      * the mapping exists but is empty (id unknown yet)

    Callers that need to pass a ``competition_id`` to TheStatsAPI MUST
    branch on a non-empty list — never blindly index the first element.
    """
    if not canonical_name:
        return []
    return list(THESTATSAPI_COMPETITION_MAP.get(canonical_name) or [])


# Country/qualifier hints that, when present in the raw league name, signal
# the competition is NOT one of the canonical Tier-1 European/American
# leagues — even if its suffix ("premier league", "serie a", "bundesliga")
# would otherwise substring-match. Kept in sync with the equivalent list in
# services.football_quality.NON_TIER1_COUNTRY_HINTS.
_NON_TIER1_COUNTRY_HINTS: tuple[str, ...] = (
    "botswana", "belarus", "belarusian", "kazakhstan", "kazakh", "uzbekistan",
    "armenia", "armenian", "azerbaijan", "georgia", "georgian", "moldova",
    "albania", "albanian", "kosovo", "north macedonia", "macedonia", "bosnia",
    "montenegro", "iceland", "icelandic", "faroe", "andorra", "san marino",
    "gibraltar", "malta", "maltese", "cyprus", "cypriot", "luxembourg",
    "lithuania", "lithuanian", "latvia", "latvian", "estonia", "estonian",
    "daguestan", "dagestan", "tajikistan", "kyrgyz", "kyrgyzstan",
    "myanmar", "burma", "nepal", "bangladesh", "mongolia", "mongolian",
    "indonesia", "malaysia", "singapore", "vietnam", "cambodia", "laos",
    "austria", "austrian", "switzerland", "swiss",
    "reserve", "reserves", "u-19", "u19", "u-20", "u20", "u-21", "u21",
    "u-23", "u23", "youth", "primavera", "sub-19", "sub-20", "sub-21",
)


def _has_non_tier1_country_hint(normalized_name: str) -> bool:
    """True iff the (already normalized) league name carries a token that
    delegitimizes a Tier-1 substring match. Token list is intentionally
    conservative: a hint must appear as a whole word fragment of the
    normalized name."""
    if not normalized_name:
        return False
    return any(hint in normalized_name for hint in _NON_TIER1_COUNTRY_HINTS)


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


# ─────────────────────────────────────────────────────────────────────
# F87.c — Unknown-competition bucket (inclusive default filter)
# ─────────────────────────────────────────────────────────────────────
# When a league_name is NOT in :data:`_COMPETITION_REGISTRY` (or its
# aliases) but ALSO is not part of the blocklist (reserve/U18/friendly
# clubs/regional/division 3+), we accept it at a low priority instead
# of silently dropping it. This preserves rare-but-valuable fixtures
# like ``FIFA Club World Cup``, ``CONMEBOL Libertadores``, or one-off
# youth tournaments with TV/odds coverage.
UNKNOWN_TIER_NAME     = "unknown"
UNKNOWN_TIER_PRIORITY = int(os.environ.get("UNKNOWN_COMPETITION_PRIORITY", "10"))
UNKNOWN_HYDRATE_CAP   = int(os.environ.get("UNKNOWN_COMPETITION_HYDRATE_CAP", "3"))

# Hard blocklist patterns — competitions that we DO want to discard.
# Carefully scoped:
#   * Youth tiers U13..U18 (numbers <19).
#   * Reserve teams.
#   * Friendly clubs (NOT international friendlies — those are kept).
#   * Generic ``youth`` / ``amateur`` / ``regional league`` tokens.
#   * Division ≥ 3 nationals (4th, 5th, 6th tier domestic, etc.).
_COMPETITION_BLOCKLIST_PATTERNS = [
    r"\bu1[34567]\b",                        # U13-U17 only — U18+ stays
    r"\bu1[3-7](?:[\s\-_]|$)",                # explicit boundary forms
    r"\breserves?\b",
    r"\bfriendly\s+clubs?\b",
    r"\bclub\s+friendl(?:y|ies)\b",
    r"\byouth\b",
    r"\bwomen.*reserves?\b",
    r"\bamateur\b",
    r"\bregional\s+league\b",
    r"\bdivision\s+[3-9]\b",
    r"\b(3rd|4th|5th|6th)\s+division\b",
    r"\b(tercera|cuarta|quinta)\s+divisi[oó]n\b",
]
_BLOCKLIST_RE = re.compile("|".join(_COMPETITION_BLOCKLIST_PATTERNS), re.IGNORECASE)


def _unknown_bucket_enabled() -> bool:
    raw = (os.environ.get("ENABLE_UNKNOWN_COMPETITION_BUCKET") or "true").lower()
    return raw not in ("0", "false", "no", "off")


def is_competition_blocklisted(league_name: str) -> bool:
    """Return True when ``league_name`` matches any blocklist regex.

    Blocklisted competitions are dropped EVEN with the unknown-bucket
    flag on — they are signal-free / non-analyzable noise.
    """
    if not isinstance(league_name, str):
        return False
    return bool(_BLOCKLIST_RE.search(league_name))


def get_unknown_competition_meta(league_name: str) -> Optional[dict]:
    """Return a synthetic ``unknown``-tier meta dict for a league that
    is not in the registry AND not blocklisted. Returns ``None`` when
    the flag is off or the name is blocklisted (silent discard).
    """
    if not _unknown_bucket_enabled():
        return None
    if is_competition_blocklisted(league_name or ""):
        return None
    return {
        "tier":           UNKNOWN_TIER_NAME,
        "priority":       UNKNOWN_TIER_PRIORITY,
        "canonical_name": (league_name or "").strip() or "Unknown Competition",
        "type":           "unknown",
        "region":         None,
        "_unknown_bucket": True,
    }


def get_allowed_tiers() -> set[str]:
    """Return ``ALLOWED_TIERS`` expanded with ``unknown`` when the flag
    is on. Call-sites that gate on tier membership should use this
    helper instead of touching :data:`ALLOWED_TIERS` directly so the
    unknown bucket participates in routing decisions."""
    base = set(ALLOWED_TIERS)
    if _unknown_bucket_enabled():
        base.add(UNKNOWN_TIER_NAME)
    return base


__all_f87c__ = [
    "UNKNOWN_TIER_NAME", "UNKNOWN_TIER_PRIORITY", "UNKNOWN_HYDRATE_CAP",
    "is_competition_blocklisted", "get_unknown_competition_meta",
    "get_allowed_tiers", "_unknown_bucket_enabled",
]
