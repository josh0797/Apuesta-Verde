"""Football Quality & Selection Engine.

Stops the engine from wasting LLM cycles on Belarus Reserve, Botswana,
Daguestán, Kazakhstan-lower-tier and similar exotic markets. Sits on top of
`football_competitions.py` (which provides the Tier 1/2/3 allowlist) and adds:

  • leagueQualityScore       (0–100) — derived from tier + match metadata
  • marketLiquidityScore     (0–100) — derived from odds coverage + stability
  • footballSelectionScore   (0–100) — composite used for "deep analysis" cut
  • A 7-state classification:
        PRIORITY_MATCH        — score > 80, Tier 1, complete data
        HIGH_LIQUIDITY        — strong market support regardless of tier
        STANDARD              — score 50–80
        LOW_DATA_QUALITY      — missing key fields (lineup, stats)
        LOW_MARKET_SUPPORT    — odds too thin / not enough books
        EXOTIC_LEAGUE_WARNING — Tier 4 (everything outside the allowlist)
        SKIPPED_LOW_RELEVANCE — score < 35 → never analyzed

  • Dynamic Match Discovery:
        Cascade Tier 1 → Tier 2 → Tier 3 until `target_count` viable matches
        are gathered. Tier 4 is disabled by default; opt-in via env var or
        explicit override.

Public API:
    enrich_football_match(match)          → mutates match with `_football_quality`
    filter_and_prioritize(matches, …)     → returns the curated, ordered list
                                            plus a sidecar of skipped matches
                                            with reasons (for UI rendering)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .football_competitions import (
    get_competition_meta,
    get_competition_tier,
)

log = logging.getLogger("football_quality")

# Quality thresholds (overridable via env for tuning without redeploy)
SCORE_PRIORITY  = int(os.environ.get("FOOTBALL_SCORE_PRIORITY",  "80"))
SCORE_STANDARD  = int(os.environ.get("FOOTBALL_SCORE_STANDARD",  "55"))
SCORE_LOW_POOL  = int(os.environ.get("FOOTBALL_SCORE_LOW_POOL",  "40"))
SCORE_NO_ANALYZE = int(os.environ.get("FOOTBALL_SCORE_NO_ANALYZE", "42"))

# Tier 4 is OFF by default — never analyzed unless ENABLE_TIER_4_FALLBACK=true
ENABLE_TIER_4_FALLBACK = os.environ.get(
    "FOOTBALL_ENABLE_TIER_4_FALLBACK", "false",
).lower() in ("1", "true", "yes")

# Suspicious league name fragments that almost always mean Tier 4 noise.
EXOTIC_FRAGMENTS = (
    "reserve", "reserves",
    "u-15", "u15", "u-16", "u16", "u-17", "u17",
    "u-18", "u18", "u-19", "u19", "u-20", "u20",
    "u-21", "u21", "u-22", "u22", "u-23", "u23",
    "sub-15", "sub-16", "sub-17", "sub-18", "sub-19",
    "sub-20", "sub-21", "sub-22", "sub-23",
    "youth", "academy", "academie", "academia",
    "friendly", "friendlies", "amistoso",
    "regional", "national league",
    "semi-pro", "amateur",
    "second team", "primavera", "ii team", "b team",
)

# Country prefixes / qualifiers that, when paired with names like "Premier
# League" or "Serie A", indicate a NON-Big-Five competition. Used to defeat the
# substring fallback in football_competitions.get_competition_meta which would
# otherwise classify "Botswana Premier League" as Tier 1.
NON_TIER1_COUNTRY_HINTS = (
    "botswana", "belarus", "belarusian", "kazakhstan", "kazakh", "uzbekistan",
    "armenia", "armenian", "azerbaijan", "georgia", "georgian", "moldova",
    "albania", "albanian", "kosovo", "kosovan", "north macedonia", "macedonia",
    "macedonian", "bosnia", "bosnian", "montenegro", "montenegrin", "iceland",
    "icelandic", "faroe", "andorra", "san marino", "gibraltar", "malta",
    "maltese", "cyprus", "cypriot", "luxembourg", "luxembourgish",
    "lithuania", "lithuanian", "latvia", "latvian", "estonia", "estonian",
    "ukraine second", "ucrania segunda", "uruguay segunda", "uruguay second",
    "daguestan", "dagestan", "dagestani", "tajikistan", "kyrgyz", "kyrgyzstan",
    "myanmar", "burma", "nepal", "nepalese", "bangladesh", "bangladeshi",
    "mongolia", "mongolian", "indonesia", "indonesian", "malaysia", "malaysian",
    "singapore", "vietnam", "vietnamese", "cambodia", "cambodian", "laos",
    "thailand second", "thai second",
)

# Big Five league IDs used to short-circuit the highest quality tier.
BIG_FIVE_LEAGUE_IDS = {39, 140, 135, 78, 61}

# Extra well-known API-Sports league IDs that aren't (yet) in the
# football_competitions allowlist by name. Used to lift them out of Tier 4
# when only the id is known.
EXTRA_TIER1_LEAGUE_IDS = {
    1,    # FIFA World Cup
    2,    # UEFA Champions League
    4,    # UEFA Euro
    9,    # Copa América
    262,  # Liga MX
}
EXTRA_TIER2_LEAGUE_IDS = {
    3,    # UEFA Europa League
    13,   # Copa Libertadores
    88,   # Eredivisie (Netherlands)
    94,   # Primeira Liga (Portugal)
    253,  # MLS (USA)
    71,   # Brasileirão Série A
    128,  # Liga Profesional Argentina
}
EXTRA_TIER3_LEAGUE_IDS = {
    848,  # UEFA Conference League
    11,   # Copa Sudamericana
    143,  # Copa del Rey
    45,   # FA Cup
    137,  # Coppa Italia
    81,   # DFB-Pokal
    66,   # Coupe de France
}

# Target picks per analysis run — keeps the LLM cost predictable and prevents
# the engine from forcing weak picks just because matches exist.
DEFAULT_TARGET_COUNT = 8
HARD_PICK_CEILING    = 12   # never analyze more than this even on cascade


# ── Helpers ─────────────────────────────────────────────────────────────────
def _is_exotic_name(league_name: Optional[str]) -> bool:
    if not league_name:
        return False
    lower = league_name.lower()
    return any(frag in lower for frag in EXOTIC_FRAGMENTS)


def _has_non_tier1_country_hint(league_name: Optional[str]) -> bool:
    """Detects "Botswana Premier League", "Belarus Reserve", etc. — names
    where a country prefix indicates the competition is NOT one of the
    canonical Tier 1 competitions, even though the suffix would otherwise
    fuzzy-match (e.g. 'Premier League', 'Serie A')."""
    if not league_name:
        return False
    lower = league_name.lower()
    return any(hint in lower for hint in NON_TIER1_COUNTRY_HINTS)


def _tier_to_int(tier_key: Optional[str]) -> int:
    """tier_1→1, tier_2→2, tier_3→3, None→4."""
    if tier_key == "tier_1": return 1
    if tier_key == "tier_2": return 2
    if tier_key == "tier_3": return 3
    return 4


# ── League Quality Score ────────────────────────────────────────────────────
def compute_league_quality_score(match: dict) -> dict:
    """0–100 score reflecting how seriously this league should be analysed."""
    league_name = match.get("league") or ""
    league_id = match.get("league_id")
    tier_key = get_competition_tier(league_name)
    tier_num = _tier_to_int(tier_key)

    # Defeat the substring fuzzy-matching false positives:
    # "Botswana Premier League" → demote to Tier 4 unless league_id confirms.
    league_id_int: Optional[int] = None
    try:
        if league_id is not None:
            league_id_int = int(league_id)
    except (TypeError, ValueError):
        league_id_int = None

    if _has_non_tier1_country_hint(league_name) and (
        league_id_int is None or league_id_int not in BIG_FIVE_LEAGUE_IDS
    ):
        tier_num = 4
        tier_key = None

    # Big Five short-circuit: even if name matching misses, league_id wins.
    if league_id_int is not None and league_id_int in BIG_FIVE_LEAGUE_IDS:
        tier_num = 1
        tier_key = "tier_1"

    # Extra league-id overrides for competitions not (yet) in the name allowlist
    # — Champions/Europa/Libertadores/Eredivisie/MLS/Brasileirão/etc.
    if tier_num == 4 and league_id_int is not None:
        if league_id_int in EXTRA_TIER1_LEAGUE_IDS:
            tier_num = 1; tier_key = "tier_1"
        elif league_id_int in EXTRA_TIER2_LEAGUE_IDS:
            tier_num = 2; tier_key = "tier_2"
        elif league_id_int in EXTRA_TIER3_LEAGUE_IDS:
            tier_num = 3; tier_key = "tier_3"

    base = {1: 70, 2: 55, 3: 40, 4: 0}[tier_num]
    score = base
    factors: list[str] = [f"Tier {tier_num}"]

    # Statistical coverage
    home_stats = (match.get("home_team") or {}).get("stats") or match.get("home_stats")
    away_stats = (match.get("away_team") or {}).get("stats") or match.get("away_stats")
    if home_stats and away_stats:
        score += 10; factors.append("stats completas")

    # Odds coverage
    odds = match.get("odds") or {}
    if odds and any(odds.get(k) for k in ("moneyline", "spread", "totals", "h2h", "1x2")):
        score += 15; factors.append("odds principales disponibles")

    # Lineup availability (probable XI)
    if match.get("lineups") or (match.get("home_team") or {}).get("lineup") or match.get("probable_xi"):
        score += 10; factors.append("alineaciones probables")

    # xG/xGA presence
    if match.get("xg") or (home_stats or {}).get("xg") or (away_stats or {}).get("xg"):
        score += 5; factors.append("xG disponible")

    # Exotic penalty
    if _is_exotic_name(league_name):
        score -= 30; factors.append("nombre exótico (reserves/youth/friendly)")

    score = max(0, min(100, score))
    return {"score": score, "factors": factors, "tier": tier_num, "tier_key": tier_key}


# ── Market Liquidity Score ──────────────────────────────────────────────────
def compute_market_liquidity_score(match: dict) -> dict:
    """0–100 score for how liquid / well-priced the betting market is.

    Heuristic (we don't have direct volume data, so we infer from breadth):
      • Books offering odds — more books = more liquidity (+up to 40).
      • Markets available — moneyline + spread + totals → broader liquidity (+up to 30).
      • Line movement freshness — recent updates indicate active book (+15).
      • Spreads tight (lowest moneyline odds 1.20+ and not > 30) → +15.
    """
    score = 0
    factors: list[str] = []

    odds = match.get("odds") or {}
    bookmakers = odds.get("bookmakers") or match.get("bookmakers")
    if isinstance(bookmakers, list):
        n = len(bookmakers)
        if n >= 8:
            score += 40; factors.append(f"{n} bookmakers")
        elif n >= 4:
            score += 25; factors.append(f"{n} bookmakers")
        elif n >= 2:
            score += 12; factors.append(f"{n} bookmakers")
        elif n >= 1:
            score += 4

    markets_present = sum(
        1 for k in (
            "moneyline", "spread", "totals", "h2h", "1x2",
            "both_teams_to_score",
            # Corner markets — counted because they unlock the corner
            # rescue layer (attach_pregame_corner_form + corner_market_layer).
            "corners", "total_corners", "team_corners",
        ) if odds.get(k)
    )
    score += min(30, markets_present * 8)
    if markets_present:
        factors.append(f"{markets_present} mercados activos")

    line_mv = (match.get("key_data") or {}).get("line_movement")
    if line_mv:
        score += 15; factors.append("movimiento de línea registrado")

    # Line stability heuristic — if we have a price and it's reasonable
    ml = odds.get("moneyline") or odds.get("h2h") or {}
    if isinstance(ml, dict):
        for side in ("home", "away", "draw"):
            v = ml.get(side)
            try:
                v = float(v) if v else None
            except (TypeError, ValueError):
                v = None
            if v and 1.20 <= v <= 30.0:
                score += 5; factors.append("cuotas en rango sano"); break

    score = max(0, min(100, score))
    label = "alta" if score >= 70 else "media" if score >= 40 else "baja"
    return {"score": score, "label": label, "factors": factors}


# ── Composite Football Selection Score + Classification ─────────────────────
def compute_football_selection_score(match: dict) -> dict:
    league_q = compute_league_quality_score(match)
    market_l = compute_market_liquidity_score(match)

    # Weighted composite (league quality 60%, market liquidity 40%)
    score = round(league_q["score"] * 0.6 + market_l["score"] * 0.4)

    tier_num = league_q["tier"]
    league_name = match.get("league") or ""

    # ── HARD BLOCK: an exotic name (U17, reserves, youth, academy, friendly,
    # second-team, etc.) ALWAYS forces EXOTIC_LEAGUE_WARNING regardless of
    # what the league_id mapping says. This neutralises edge cases like
    # "CAF Cup of Nations - U17" whose competition id isn't (and shouldn't
    # be) in any allowlist but historically slipped through when its
    # liquidity heuristic landed above SCORE_NO_ANALYZE.
    if _is_exotic_name(league_name):
        return {
            "score": min(score, 25),
            "state": "EXOTIC_LEAGUE_WARNING",
            "tier": 4,
            "tier_key": None,
            "league_quality": league_q,
            "market_liquidity": market_l,
            "priority_reason": None,
            "skip_reason": (
                f"Liga exótica detectada por nombre ({league_name}): "
                "reservas, sub-XX, youth, academy, friendly o liga regional."
            ),
            "is_exotic": True,
            "allowed_for_analysis": False,
        }

    # Default state inference
    if score >= SCORE_PRIORITY and tier_num <= 2:
        state = "PRIORITY_MATCH"
        priority_reason = (
            f"Liga top (Tier {tier_num}) + score {score}/100. "
            "Cobertura y liquidez suficientes para análisis profundo."
        )
        skip_reason = None
    elif market_l["score"] >= 75 and league_q["score"] >= 50:
        state = "HIGH_LIQUIDITY"
        priority_reason = (
            f"Mercado muy líquido ({market_l['score']}/100) — "
            "vale la pena aún fuera de Tier 1."
        )
        skip_reason = None
    elif score >= SCORE_STANDARD:
        state = "STANDARD"
        priority_reason = f"Match con datos y liquidez suficientes (score {score})."
        skip_reason = None
    elif tier_num == 4:
        # Tier 4 / unknown competition → exotic warning, skip by default
        state = "EXOTIC_LEAGUE_WARNING"
        priority_reason = None
        skip_reason = (
            f"Liga fuera de la allowlist Tier 1/2/3 ({league_name}). "
            "Datos y liquidez típicamente insuficientes; analizar solo en fallback."
        )
    elif market_l["score"] < 25:
        state = "LOW_MARKET_SUPPORT"
        priority_reason = None
        skip_reason = (
            f"Liquidez baja ({market_l['score']}/100): pocos books / mercados frágiles."
        )
    elif league_q["score"] < SCORE_LOW_POOL:
        state = "LOW_DATA_QUALITY"
        priority_reason = None
        skip_reason = (
            f"Calidad de liga baja ({league_q['score']}/100): falta lineup/xG/stats."
        )
    elif score < SCORE_NO_ANALYZE:
        state = "SKIPPED_LOW_RELEVANCE"
        priority_reason = None
        skip_reason = f"Score {score}/100 < {SCORE_NO_ANALYZE}: irrelevante para análisis."
    else:
        state = "STANDARD"
        priority_reason = None
        skip_reason = None

    # Phase 9 — flag whether this match can fall back to an alternative-market
    # scan (Under 3.5/2.5 + DC combos) when the direct 1X2 has no edge. The
    # flag is informational here; the actual scan + decision is made in
    # services/under_market_scan.py and applied by the analyst engine.
    #
    # Updated: H2H is no longer mandatory when corner data is available
    # (either pre-attached `_corner_form` from football_corner_pregame, raw
    # `corner_stats` enrichment, or a live corner odds market). This lets
    # the corner rescue layer evaluate Tier 1/2 matches that would
    # otherwise be killed for "no H2H".
    has_corner_data = (
        bool(match.get("_corner_form"))
        or bool(match.get("corner_stats"))
    )
    has_corner_market = bool((match.get("odds") or {}).get("corners"))
    pa_eligible = (
        tier_num in (1, 2)
        and bool(market_l["score"] > 0)
        and (
            bool(match.get("h2h_recent"))
            or has_corner_data
            or has_corner_market
        )
    )

    return {
        "score": score,
        "state": state,
        "tier": tier_num,
        "tier_key": league_q.get("tier_key"),
        "league_quality": league_q,
        "market_liquidity": market_l,
        "priority_reason": priority_reason,
        "skip_reason": skip_reason,
        "is_exotic": tier_num == 4 or _is_exotic_name(league_name),
        "allowed_for_analysis": skip_reason is None,
        "protected_alternative_eligible": pa_eligible,
    }


# ── Match enrichment ────────────────────────────────────────────────────────
def enrich_football_match(match: dict) -> dict:
    """Attaches `_football_quality` to the match doc. Idempotent."""
    if not match or match.get("sport") not in (None, "football"):
        return match
    if match.get("_football_quality"):
        return match
    match["_football_quality"] = compute_football_selection_score(match)
    # Mirror a couple of fields at root for back-compat with old UI references
    fq = match["_football_quality"]
    match["football_tier"] = fq["tier"]
    match["football_selection_state"] = fq["state"]
    match["football_selection_score"] = fq["score"]
    return match


# ── Dynamic Match Discovery ─────────────────────────────────────────────────
def filter_and_prioritize(
    matches: list[dict],
    target_count: int = DEFAULT_TARGET_COUNT,
    enable_tier_4: bool = False,
    priority_override: bool = False,
) -> dict:
    """Cascade Tier 1 → Tier 2 → Tier 3 (→ Tier 4 only if explicitly enabled
    AND the upper tiers didn't yield enough viable matches) until we have at
    least `target_count` analysable matches.

    Args:
        priority_override: when True, matches in Tier 1/2/3 that have a
            league_id in the global priority ladder are allowed through
            EVEN IF their market liquidity score is too low (e.g. odds
            not yet hydrated). Exotic-name hard-blocks and Tier 4
            classification still apply. Set this when the caller has
            ALREADY narrowed the candidate set to top-12 priority
            competitions (see `discover_priority_fixtures`) — otherwise
            you'd reject Bologna vs Inter for "low liquidity" when its
            odds simply hadn't been fetched yet.

    Returns:
        {
            "selected": [matches with quality scoring attached, ordered by score desc],
            "skipped":  [{match, reason, state, score}],
            "stats":    {by_tier, by_state, cascade_used},
        }
    """
    target_count = max(3, min(HARD_PICK_CEILING, target_count))
    enable_t4 = enable_tier_4 or ENABLE_TIER_4_FALLBACK

    enriched: list[dict] = []
    skipped: list[dict] = []
    # NOTE: keys are STRINGS, not ints. MongoDB/BSON refuses numeric keys
    # ("documents must have only string keys, key was 1"), and this payload
    # is persisted verbatim into `result._pipeline.football_quality`.
    by_tier: dict[str, int] = {"1": 0, "2": 0, "3": 0, "4": 0}
    by_state: dict[str, int] = {}

    for m in matches:
        enrich_football_match(m)
        fq = m.get("_football_quality") or {}
        tier = fq.get("tier", 4)
        state = fq.get("state", "STANDARD")
        tier_key = str(tier)
        by_tier[tier_key] = by_tier.get(tier_key, 0) + 1
        by_state[state] = by_state.get(state, 0) + 1

        # Phase 8.1 — priority override: rescue a Tier 1/2/3 match that the
        # liquidity heuristic blocked (typically because deep-enrich hasn't
        # populated odds_snapshots yet). We still respect the exotic-name
        # hard-block (U17 / reserves / academy) and the Tier 4 default skip.
        #
        # Phase 9 — protected_alternative_eligible: even WITHOUT priority
        # override, if a Tier 1/2 match has H2H + some odds, we let it
        # through so under_market_scan can offer Under 3.5/2.5 as a
        # fallback. Otherwise we'd kill the next "Alavés vs Rayo" before
        # ever computing its Under profile.
        rescuable = (
            (priority_override and tier in (1, 2, 3))
            or (fq.get("protected_alternative_eligible") and tier in (1, 2))
        )
        if (
            rescuable
            and not fq.get("allowed_for_analysis")
            and not fq.get("is_exotic")
            and state in ("LOW_MARKET_SUPPORT", "LOW_DATA_QUALITY")
        ):
            fq["allowed_for_analysis"] = True
            fq["state"] = (
                "PRIORITY_OVERRIDE" if priority_override else "ALTERNATIVE_MARKET_SCAN"
            )
            fq["priority_reason"] = (
                "Override de prioridad: liga top sin odds completas todavía; "
                "se analiza igual porque entra en el ladder Tier 1/2/3."
                if priority_override else
                "Elegible para escaneo de mercados protegidos (Under 3.5/2.5) "
                "aunque el 1X2 carezca de liquidez."
            )
            fq["skip_reason"] = None
            enriched.append(m)
        elif fq.get("allowed_for_analysis"):
            enriched.append(m)
        else:
            skipped.append({
                "match_id": m.get("match_id"),
                "match_label": m.get("match_label") or f"{(m.get('home_team') or {}).get('name','?')} vs {(m.get('away_team') or {}).get('name','?')}",
                "league": m.get("league"),
                "league_id": m.get("league_id"),
                "tier": tier,
                "state": state,
                "score": fq.get("score"),
                "reason": fq.get("skip_reason"),
            })

    # Cascade: split into tier buckets and keep filling until we hit target_count
    by_tier_buckets: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: []}
    for m in enriched:
        t = (m.get("_football_quality") or {}).get("tier", 4)
        by_tier_buckets[t].append(m)

    # Within each tier, sort by score desc (PRIORITY_MATCH first)
    for tlist in by_tier_buckets.values():
        tlist.sort(key=lambda x: (x.get("_football_quality") or {}).get("score", 0), reverse=True)

    cascade_used: list[int] = []
    selected: list[dict] = []
    for tier_num in (1, 2, 3, 4):
        if tier_num == 4 and not enable_t4:
            break
        bucket = by_tier_buckets.get(tier_num, [])
        if not bucket:
            continue
        cascade_used.append(tier_num)
        for m in bucket:
            selected.append(m)
            if len(selected) >= target_count:
                break
        if len(selected) >= target_count:
            break

    return {
        "selected": selected,
        "skipped": skipped,
        "stats": {
            "ingested_total":  len(matches),
            "analysable_total": len(enriched),
            "selected_total":  len(selected),
            "skipped_total":   len(skipped),
            "by_tier":         by_tier,
            "by_state":        by_state,
            "cascade_used":    cascade_used,
            "target_count":    target_count,
            "tier_4_enabled":  enable_t4,
        },
    }
