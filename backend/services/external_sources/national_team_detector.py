"""National-team match detection for football.

Used by:
  * ``services.external_sources.thestatsapi_normalizer`` — sets
    ``_is_national_team`` on each TheStatsAPI fixture so the rest of
    the pipeline (UI badges, allowlist) can identify selecciones.
  * ``services.football_live_aggregator`` — country alias map is reused
    during team-name normalization so ``Bélgica`` matches ``Belgium``
    in the dedupe step.
  * ``services.data_ingestion.ingest_live`` — uses
    ``is_international_competition`` to grant synthetic Tier-2 priority
    to TheStatsAPI fixtures that don't have a numeric API-Sports
    league id we recognise.

Design choices:
  * The FIFA national-team list is in English (canonical form used by
    both providers most of the time). Spanish / Portuguese / native
    variants are mapped through ``COUNTRY_ALIASES`` to the English
    canonical, so callers never have to branch on language.
  * Keywords are case-insensitive and accent-insensitive (we strip
    accents before matching).
  * Everything returns plain bools / strings — no exceptions ever.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# ─────────────────────────────────────────────────────────────────────
# FIFA national-team list (canonical English names).
# Covers all ~210 FIFA member associations; this is the universe of
# possible "national-team" home/away names. We compare against the
# normalized form (lowercased, accent-stripped) so spelling variants
# all hit the same entry.
# ─────────────────────────────────────────────────────────────────────
FIFA_NATIONAL_TEAMS: frozenset[str] = frozenset({
    # UEFA (Europe)
    "albania", "andorra", "armenia", "austria", "azerbaijan", "belarus", "belgium",
    "bosnia and herzegovina", "bosnia", "bulgaria", "croatia", "cyprus", "czech republic",
    "czechia", "denmark", "england", "estonia", "faroe islands", "finland", "france",
    "georgia", "germany", "gibraltar", "greece", "hungary", "iceland", "israel", "italy",
    "kazakhstan", "kosovo", "latvia", "liechtenstein", "lithuania", "luxembourg", "malta",
    "moldova", "montenegro", "netherlands", "north macedonia", "macedonia", "northern ireland",
    "norway", "poland", "portugal", "republic of ireland", "ireland", "romania", "russia",
    "san marino", "scotland", "serbia", "slovakia", "slovenia", "spain", "sweden",
    "switzerland", "turkey", "ukraine", "wales",
    # CONMEBOL (South America)
    "argentina", "bolivia", "brazil", "chile", "colombia", "ecuador", "paraguay", "peru",
    "uruguay", "venezuela",
    # CONCACAF (North & Central America + Caribbean)
    "anguilla", "antigua and barbuda", "aruba", "bahamas", "barbados", "belize", "bermuda",
    "british virgin islands", "canada", "cayman islands", "costa rica", "cuba", "curacao",
    "dominica", "dominican republic", "el salvador", "grenada", "guatemala", "guyana",
    "haiti", "honduras", "jamaica", "martinique", "mexico", "montserrat", "nicaragua",
    "panama", "puerto rico", "saint kitts and nevis", "saint lucia",
    "saint vincent and the grenadines", "suriname", "trinidad and tobago",
    "turks and caicos islands", "united states", "usa", "us virgin islands",
    # CAF (Africa)
    "algeria", "angola", "benin", "botswana", "burkina faso", "burundi", "cameroon",
    "cape verde", "central african republic", "chad", "comoros", "congo", "dr congo",
    "democratic republic of congo", "djibouti", "egypt", "equatorial guinea", "eritrea",
    "eswatini", "swaziland", "ethiopia", "gabon", "gambia", "ghana", "guinea",
    "guinea-bissau", "ivory coast", "cote d'ivoire", "kenya", "lesotho", "liberia",
    "libya", "madagascar", "malawi", "mali", "mauritania", "mauritius", "morocco",
    "mozambique", "namibia", "niger", "nigeria", "rwanda", "sao tome and principe",
    "senegal", "seychelles", "sierra leone", "somalia", "south africa", "south sudan",
    "sudan", "tanzania", "togo", "tunisia", "uganda", "zambia", "zimbabwe",
    # AFC (Asia)
    "afghanistan", "australia", "bahrain", "bangladesh", "bhutan", "brunei", "cambodia",
    "china", "guam", "hong kong", "india", "indonesia", "iran", "iraq", "japan", "jordan",
    "kuwait", "kyrgyzstan", "laos", "lebanon", "macau", "malaysia", "maldives", "mongolia",
    "myanmar", "nepal", "north korea", "oman", "pakistan", "palestine", "philippines",
    "qatar", "saudi arabia", "singapore", "south korea", "korea", "sri lanka", "syria",
    "taiwan", "chinese taipei", "tajikistan", "thailand", "timor-leste", "east timor",
    "turkmenistan", "united arab emirates", "uae", "uzbekistan", "vietnam", "yemen",
    # OFC (Oceania)
    "american samoa", "cook islands", "fiji", "new caledonia", "new zealand", "papua new guinea",
    "samoa", "solomon islands", "tahiti", "tonga", "vanuatu",
})

# ─────────────────────────────────────────────────────────────────────
# Country aliases (frequent ES/PT/native → English canonical). Used to
# bridge providers that localize team names. Per user spec: short list
# (~40 entries) covering the most frequent occurrences in logs.
# Keys + values are stored normalized (lowercase, accent-stripped).
# ─────────────────────────────────────────────────────────────────────
COUNTRY_ALIASES: dict[str, str] = {
    # ES → EN
    "alemania":      "germany",
    "argelia":       "algeria",
    "belgica":       "belgium",
    "brasil":        "brazil",
    "camerun":       "cameroon",
    "corea del sur": "south korea",
    "corea del norte": "north korea",
    "costa de marfil": "ivory coast",
    "croacia":       "croatia",
    "dinamarca":     "denmark",
    "ecuador":       "ecuador",
    "egipto":        "egypt",
    "escocia":       "scotland",
    "eslovaquia":    "slovakia",
    "eslovenia":     "slovenia",
    "espana":        "spain",
    "estados unidos": "united states",
    "francia":       "france",
    "gales":         "wales",
    "grecia":        "greece",
    "holanda":       "netherlands",
    "hungria":       "hungary",
    "inglaterra":    "england",
    "irlanda":       "ireland",
    "irlanda del norte": "northern ireland",
    "italia":        "italy",
    "japon":         "japan",
    "marruecos":     "morocco",
    "mexico":        "mexico",
    "noruega":       "norway",
    "nueva zelanda": "new zealand",
    "paises bajos":  "netherlands",
    "polonia":       "poland",
    "portugal":      "portugal",
    "reino unido":   "england",   # imprecise but useful for some feeds
    "republica checa": "czech republic",
    "republica de irlanda": "ireland",
    "rumania":       "romania",
    "rusia":         "russia",
    "serbia":        "serbia",
    "sudafrica":     "south africa",
    "suecia":        "sweden",
    "suiza":         "switzerland",
    "turquia":       "turkey",
    "ucrania":       "ukraine",
    "uruguay":       "uruguay",
    # PT → EN
    "alemanha":      "germany",
    "belgica/pt":    "belgium",        # disambiguator (same as ES)
    "croacia/pt":    "croatia",
    "dinamarca/pt":  "denmark",
    "inglaterra/pt": "england",
    "italia/pt":     "italy",
    "paises baixos": "netherlands",
    "republica tcheca": "czech republic",
    # English variants
    "usa":              "united states",
    "us":               "united states",
    "uae":              "united arab emirates",
    "korea":            "south korea",
    "republic of korea": "south korea",
    "macedonia":        "north macedonia",
    "czechia":          "czech republic",
    "bosnia":           "bosnia and herzegovina",
}

# ─────────────────────────────────────────────────────────────────────
# International / national-team competition keywords (case + accent
# insensitive). Either AS substrings within league/comp name, OR the
# league.country field is one of the listed "region" values.
# ─────────────────────────────────────────────────────────────────────
INTERNATIONAL_COMP_KEYWORDS: frozenset[str] = frozenset({
    # EN
    "world cup", "fifa world cup", "fifa club world cup",
    "euro", "uefa euro", "european championship",
    "nations league", "uefa nations league",
    "copa america", "copa américa",
    "gold cup", "concacaf gold cup",
    "afcon", "africa cup of nations", "african cup of nations",
    "asian cup", "afc asian cup",
    "international friendly", "international friendlies", "club friendlies",
    "world cup qualif", "wc qualif", "euro qualif", "european qualif",
    "concacaf nations league", "concacaf qualif",
    "olympic games", "olympics", "olympic football",
    # ES
    "copa del mundo", "mundial",
    "eurocopa", "campeonato europeo",
    "copa de naciones", "liga de naciones",
    "copa oro", "copa de oro",
    "copa africana", "copa africa",
    "copa asiatica", "copa de asia",
    "eliminatorias", "clasificacion mundial", "clasificación mundial",
    "amistoso", "amistosos", "amistoso internacional",
    "seleccion", "selección", "selecciones",
})

INTERNATIONAL_REGIONS: frozenset[str] = frozenset({
    "world", "international", "europe", "south america", "north america",
    "africa", "asia", "oceania",
})

_NON_WORD_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_country_name(name: str | None) -> str:
    """Return a comparable form (lowercased, accent-stripped, alias-resolved).

    Examples:
        >>> normalize_country_name("Bélgica")
        'belgium'
        >>> normalize_country_name("CROACIA")
        'croatia'
        >>> normalize_country_name("USA")
        'united states'
    """
    if not name:
        return ""
    s = _strip_accents(str(name)).lower().strip()
    s = _NON_WORD_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    # alias lookup
    if s in COUNTRY_ALIASES:
        s = COUNTRY_ALIASES[s]
    return s


def is_national_team_name(name: str | None) -> bool:
    """True if the team name (any language) refers to a FIFA national team."""
    norm = normalize_country_name(name)
    if not norm:
        return False
    return norm in FIFA_NATIONAL_TEAMS


def is_international_competition(
    league_name: str | None = None,
    league_country: str | None = None,
) -> bool:
    """True if the competition is international / for national teams.

    Checked against:
      1. ``league_name`` substring match against ``INTERNATIONAL_COMP_KEYWORDS``
      2. ``league_country`` normalized form ∈ ``INTERNATIONAL_REGIONS``
    """
    if league_name:
        ln = _strip_accents(str(league_name)).lower()
        for kw in INTERNATIONAL_COMP_KEYWORDS:
            if kw in ln:
                return True
    if league_country:
        cn = normalize_country_name(league_country)
        if cn in INTERNATIONAL_REGIONS:
            return True
    return False


def is_national_team_match(
    home_name: str | None,
    away_name: str | None,
    league_name: str | None = None,
    league_country: str | None = None,
) -> bool:
    """True if this looks like a national-team / international fixture.

    Decision rules (any positive signal is enough):
      1. Both teams' names match an entry in ``FIFA_NATIONAL_TEAMS``
         (after alias normalization).
      2. The competition name contains an international keyword.
      3. The competition country is a region (World, Europe, etc.).

    Why "both teams"? A single nat-team name match is too noisy:
    'Belgium U21' style suffixes might still hit, and club teams
    sometimes share a name with a country (e.g. 'Liechtenstein' is
    both country and club). Demanding both ends keeps precision high.
    """
    if is_international_competition(league_name, league_country):
        return True
    if is_national_team_name(home_name) and is_national_team_name(away_name):
        return True
    return False


def country_canonical(name: str | None) -> str | None:
    """Return canonical English form for a country name, or ``None``.

    Distinct from ``normalize_country_name``: only returns when the
    normalized form is actually a recognised FIFA national team.
    Useful for the dedupe step which needs to know if two teams
    can be considered equal.
    """
    norm = normalize_country_name(name)
    if norm and norm in FIFA_NATIONAL_TEAMS:
        return norm
    return None
