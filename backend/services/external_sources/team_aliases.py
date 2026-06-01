"""Shared team-name alias map + best-match algorithm.

Used by StatBunker (canonical league-table lookup) and SoccerSTATS
(league-index slug lookup), and ready for any future scraper that
needs to map "the same team under three different names" to a single
canonical key.

Pure functions, no IO. ``TEAM_ALIASES`` maps every known alias
(normalised, lowercase, ASCII) to its **canonical** name (also
normalised). ``best_match`` resolves the input through this dict
BEFORE falling back to layered matching, eliminating almost every
"team_not_in_table" miss for Tier 1/2 clubs and major national teams.

Strategy of ``best_match``:

    1. **Exact match** on normalised target.
    2. **Alias→canonical**: resolve target via TEAM_ALIASES and retry.
    3. **Reverse alias**: if any candidate has an alias whose canonical
       form matches the target's canonical form, use it.
    4. **Token subset**: all target tokens contained in candidate (or
       vice versa) — eliminates "Manchester City" vs "Manchester
       United" false positives because the longer name's tokens are
       not a subset of the shorter one.
    5. **Token overlap**: ≥ 2 tokens shared (≥ 1 only when the target
       has < 2 tokens).
    6. **Fuzzy fallback**: ``SequenceMatcher.ratio() >= 0.85`` for
       typos and accent variants.

API
---
``normalize(s)``               → normalised string (lowercase ASCII).
``resolve_alias(name)``        → canonical name (via TEAM_ALIASES).
``best_match(target, candidates)`` → best candidate match or ``None``.
    candidates can be either ``list[str]`` or ``dict[str, V]``; the
    return is the matched key (caller does ``dict[key]`` themselves).
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, Optional, Union

FUZZY_THRESHOLD = 0.85

# Each entry maps alias → canonical name (already normalised).
TEAM_ALIASES: dict[str, str] = {
    # ── Premier League ──────────────────────────────────────────────
    "man city":              "manchester city",
    "manchester c":          "manchester city",
    "man c":                 "manchester city",
    "man utd":               "manchester united",
    "manchester u":          "manchester united",
    "manchester utd":        "manchester united",
    "man united":            "manchester united",
    "spurs":                 "tottenham",
    "tottenham hotspur":     "tottenham",
    "newcastle united":      "newcastle",
    "newcastle utd":         "newcastle",
    "west ham united":       "west ham",
    "west ham utd":          "west ham",
    "wolves":                "wolverhampton wanderers",
    "wolverhampton":         "wolverhampton wanderers",
    "brighton hove albion":  "brighton",
    "brighton and hove albion": "brighton",
    "leicester city":        "leicester",
    "leicester c":           "leicester",
    "leeds united":          "leeds utd",
    "leeds":                 "leeds utd",
    "nottingham forest":     "nottm forest",
    "nottm forest":          "nottm forest",
    "afc bournemouth":       "bournemouth",
    "sheffield united":      "sheffield utd",
    "sheffield wednesday":   "sheffield wed",
    # ── La Liga ─────────────────────────────────────────────────────
    "atletico":              "atletico madrid",
    "atletico de madrid":    "atletico madrid",
    "atletico madrid":       "atletico madrid",
    "atletico madrid b":     "atletico madrid b",
    "real betis":            "betis",
    "rcd espanyol":          "espanyol",
    "espanyol":              "espanyol",
    "athletic club":         "athletic bilbao",
    "athletic":              "athletic bilbao",
    "real sociedad":         "real sociedad",
    "fc barcelona":          "barcelona",
    "barca":                 "barcelona",
    "real madrid cf":        "real madrid",
    "real madrid c f":       "real madrid",
    "deportivo alaves":      "alaves",
    "rayo vallecano":        "rayo vallecano",
    "celta vigo":            "celta",
    "celta de vigo":         "celta",
    "real valladolid":       "valladolid",
    "ud almeria":            "almeria",
    "ud las palmas":         "las palmas",
    "girona fc":             "girona",
    "villarreal cf":         "villarreal",
    # ── Serie A ─────────────────────────────────────────────────────
    "inter":                 "internazionale",
    "inter milan":           "internazionale",
    "fc internazionale":     "internazionale",
    "internazionale milano": "internazionale",
    "ac milan":              "milan",
    "milan":                 "milan",
    "as roma":               "roma",
    "ssc napoli":            "napoli",
    "ss lazio":              "lazio",
    "atalanta bc":           "atalanta",
    "juventus turin":        "juventus",
    "juve":                  "juventus",
    "torino fc":             "torino",
    "ac fiorentina":         "fiorentina",
    "us sassuolo":           "sassuolo",
    "udinese calcio":        "udinese",
    "bologna fc":            "bologna",
    "hellas verona":         "verona",
    "genoa cfc":             "genoa",
    # ── Bundesliga ──────────────────────────────────────────────────
    "bayern":                "bayern munich",
    "bayern muenchen":       "bayern munich",
    "bayern munchen":        "bayern munich",
    "fc bayern":             "bayern munich",
    "fc bayern munich":      "bayern munich",
    "bayern munchen ii":     "bayern munich ii",
    "bvb":                   "borussia dortmund",
    "dortmund":              "borussia dortmund",
    "rb leipzig":            "rb leipzig",
    "bayer leverkusen":      "bayer leverkusen",
    "bayer 04 leverkusen":   "bayer leverkusen",
    "borussia monchengladbach": "borussia mgladbach",
    "borussia m gladbach":   "borussia mgladbach",
    "monchengladbach":       "borussia mgladbach",
    "eintracht frankfurt":   "ein frankfurt",
    "eintracht":             "ein frankfurt",
    "wolfsburg":             "vfl wolfsburg",
    "vfl wolfsburg":         "vfl wolfsburg",
    "vfb stuttgart":         "stuttgart",
    "tsg hoffenheim":        "hoffenheim",
    "1899 hoffenheim":       "hoffenheim",
    "fsv mainz":             "mainz",
    "fsv mainz 05":          "mainz",
    "sv werder bremen":      "werder bremen",
    "fc augsburg":           "augsburg",
    "1 fc koln":             "fc koln",
    "fc koln":               "fc koln",
    # ── Ligue 1 ─────────────────────────────────────────────────────
    "psg":                   "paris saint germain",
    "paris sg":              "paris saint germain",
    "paris st germain":      "paris saint germain",
    "paris saint germain":   "paris saint germain",
    "paris":                 "paris saint germain",
    "olympique de marseille":"marseille",
    "om":                    "marseille",
    "olympique marseille":   "marseille",
    "olympique lyonnais":    "lyon",
    "ol":                    "lyon",
    "olympique lyon":        "lyon",
    "lille osc":             "lille",
    "as monaco":             "monaco",
    "stade rennais":         "rennes",
    "stade brestois":        "brest",
    "fc nantes":             "nantes",
    "ogc nice":              "nice",
    "rc strasbourg":         "strasbourg",
    "rc lens":               "lens",
    "stade reims":           "reims",
    # ── Portugal / Netherlands / others ─────────────────────────────
    "fc porto":              "porto",
    "porto fc":              "porto",
    "sporting cp":           "sporting",
    "sporting lisbon":       "sporting",
    "sl benfica":            "benfica",
    "ajax amsterdam":        "ajax",
    "afc ajax":              "ajax",
    "psv eindhoven":         "psv",
    "feyenoord":             "feyenoord",
    "fc utrecht":             "utrecht",
    "az alkmaar":            "az",
    "celtic fc":              "celtic",
    "rangers fc":             "rangers",
    # ── Argentina ───────────────────────────────────────────────────
    "boca juniors":          "boca juniors",
    "boca":                  "boca juniors",
    "club atletico river plate": "river plate",
    "ca river plate":        "river plate",
    "river":                 "river plate",
    "racing club":           "racing club",
    "racing":                "racing club",
    "ca independiente":      "independiente",
    "san lorenzo":           "san lorenzo",
    "ca estudiantes":        "estudiantes",
    "club atletico talleres":"talleres",
    "talleres cordoba":      "talleres",
    "velez sarsfield":       "velez",
    "ca velez sarsfield":    "velez",
    # ── Brazil ──────────────────────────────────────────────────────
    "flamengo":              "flamengo",
    "cr flamengo":            "flamengo",
    "fluminense":             "fluminense",
    "fluminense fc":          "fluminense",
    "palmeiras":              "palmeiras",
    "se palmeiras":           "palmeiras",
    "corinthians":            "corinthians",
    "sc corinthians":         "corinthians",
    "sao paulo":              "sao paulo",
    "sao paulo fc":           "sao paulo",
    "santos fc":              "santos",
    "atletico mineiro":       "atletico mineiro",
    "atletico mg":            "atletico mineiro",
    "internacional":          "internacional",
    "sc internacional":       "internacional",
    "gremio":                 "gremio",
    "cruzeiro":               "cruzeiro",
    "botafogo":               "botafogo",
    "botafogo fr":            "botafogo",
    # ════════════════════════════════════════════════════════════════
    #               NATIONAL TEAMS — World Cup ready
    # ════════════════════════════════════════════════════════════════
    "espana":                "spain",
    "spain national team":   "spain",
    "argentina national team":"argentina",
    "brasil":                "brazil",
    "brazil national team":  "brazil",
    "selecao":               "brazil",
    "seleccion":             "spain",
    "francia":               "france",
    "alemania":              "germany",
    "italia":                "italy",
    "inglaterra":            "england",
    "england national team": "england",
    "paises bajos":          "netherlands",
    "holanda":               "netherlands",
    "holland":               "netherlands",
    "estados unidos":        "united states",
    "usmnt":                 "united states",
    "usa":                   "united states",
    "usa national team":     "united states",
    "mexico national team":  "mexico",
    "el tri":                "mexico",
    "republica de corea":    "south korea",
    "korea republic":        "south korea",
    "republic of korea":     "south korea",
    "south korea national team": "south korea",
    "iran national team":    "iran",
    "ir iran":               "iran",
    "japan national team":   "japan",
    "saudi arabia national team": "saudi arabia",
    "australia national team": "australia",
    "socceroos":             "australia",
    "canada national team":  "canada",
    "uruguay national team": "uruguay",
    "uruguayan":             "uruguay",
    "ecuador national team": "ecuador",
    "marruecos":             "morocco",
    "senegal national team": "senegal",
    "senegales":             "senegal",
    "ghana national team":   "ghana",
    "tunez":                 "tunisia",
    "camerun":               "cameroon",
    "ivory coast":           "cote d ivoire",
    "costa de marfil":       "cote d ivoire",
    "republica democratica del congo": "dr congo",
    "congo dr":              "dr congo",
    "egipto":                "egypt",
    "sudafrica":             "south africa",
    "argelia":               "algeria",
    "nigeria national team": "nigeria",
    "polonia":               "poland",
    "belgica":               "belgium",
    "croacia":               "croatia",
    "serbia national team":  "serbia",
    "republica checa":       "czech republic",
    "czechia":                "czech republic",
    "suiza":                  "switzerland",
    "austria national team":  "austria",
    "dinamarca":              "denmark",
    "noruega":                "norway",
    "suecia":                 "sweden",
    "irlanda":                "republic of ireland",
    "republic of ireland":    "republic of ireland",
    "irlanda del norte":      "northern ireland",
    "gales":                  "wales",
    "escocia":                "scotland",
    "turquia":                "turkey",
    "grecia":                 "greece",
    "ucrania":                "ukraine",
    "hungria":                "hungary",
    "rumania":                "romania",
    "rusia":                  "russia",
    "portugal national team": "portugal",
    "germany national team":  "germany",
    "italy national team":    "italy",
    "france national team":   "france",
    "netherlands national team": "netherlands",
    "qatar national team":    "qatar",
    "panama national team":   "panama",
    "honduras national team": "honduras",
    "el salvador national team": "el salvador",
    "jamaica national team":  "jamaica",
    "colombia national team": "colombia",
    "venezuela national team":"venezuela",
    "chile national team":    "chile",
    "peru national team":     "peru",
    "paraguay national team": "paraguay",
    "bolivia national team":  "bolivia",
    "new zealand national team":"new zealand",
    "all whites":             "new zealand",
}



# ─────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────
def normalize(s: str) -> str:
    """Lowercase, strip diacritics, collapse to ASCII alphanumeric+space."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def resolve_alias(name: str) -> str:
    """Map an arbitrary team-name input to its canonical alias.

    Idempotent: a canonical name maps to itself. Unknown names return
    their normalised form unchanged so callers can use them as-is.
    """
    norm = normalize(name)
    return TEAM_ALIASES.get(norm, norm)


def _ensure_list(candidates: Union[Iterable[str], dict]) -> list[str]:
    if isinstance(candidates, dict):
        return list(candidates.keys())
    return list(candidates)


def best_match(target: str,
                candidates: Union[Iterable[str], dict],
                *,
                fuzzy_threshold: float = FUZZY_THRESHOLD) -> Optional[str]:
    """Find the best candidate for ``target``.

    Returns the matched candidate KEY (or ``None`` if no acceptable
    match was found). For dict-style ``candidates`` the caller does
    ``candidates[returned_key]`` themselves.
    """
    keys = _ensure_list(candidates)
    if not keys:
        return None

    target_norm = normalize(target)
    if not target_norm:
        return None

    keyset = set(keys)

    # ── 1. Exact match ────────────────────────────────────────────
    if target_norm in keyset:
        return target_norm

    # ── 2. Alias → canonical, then retry exact ────────────────────
    canonical_target = TEAM_ALIASES.get(target_norm, target_norm)
    if canonical_target != target_norm and canonical_target in keyset:
        return canonical_target

    # ── 3. Reverse alias resolution ───────────────────────────────
    #    For each candidate, check if its TEAM_ALIASES canonical form
    #    matches the target's canonical form. This handles the case
    #    where the league lists "internazionale" and the user passes
    #    "Inter" (both map to "internazionale").
    for k in keys:
        canonical_candidate = TEAM_ALIASES.get(k, k)
        if canonical_candidate == canonical_target:
            return k

    # The target_norm passed through aliases gives us the canonical
    # name we should actually be hunting for in subsequent layers.
    search_target = canonical_target

    # ── 4. Token subset ───────────────────────────────────────────
    #    "manchester united" tokens ⊆ "manchester united fc" tokens.
    #    Prevents "manchester united" matching "manchester city".
    target_tokens = set(search_target.split())
    if target_tokens:
        for k in keys:
            k_tokens = set(k.split())
            if not k_tokens:
                continue
            if target_tokens.issubset(k_tokens) or k_tokens.issubset(target_tokens):
                # Prefer the smaller candidate when subset matches
                # (more specific). When both sides match, return the
                # one whose token count is closer to target_tokens.
                return k

    # ── 5. Token overlap (≥ 2 unless target has fewer) ────────────
    min_overlap = 2 if len(target_tokens) >= 2 else 1
    best_k = None
    best_overlap = 0
    for k in keys:
        overlap = len(target_tokens & set(k.split()))
        if overlap > best_overlap:
            best_k, best_overlap = k, overlap
    if best_k and best_overlap >= min_overlap:
        return best_k

    # ── 6. Fuzzy fallback ─────────────────────────────────────────
    best_k = None
    best_ratio = 0.0
    for k in keys:
        # Compare both raw target and canonical target for robustness
        r1 = SequenceMatcher(None, target_norm, k).ratio()
        r2 = SequenceMatcher(None, search_target, k).ratio()
        r = max(r1, r2)
        if r > best_ratio:
            best_k, best_ratio = k, r
    if best_k and best_ratio >= fuzzy_threshold:
        return best_k

    return None


__all__ = ["TEAM_ALIASES", "normalize", "resolve_alias", "best_match",
           "FUZZY_THRESHOLD"]
