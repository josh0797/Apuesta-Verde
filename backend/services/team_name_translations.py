"""Team name translations for Scores24 (Phase F63).

The API-Sports / TheSportsDB / OpenFootball stack returns English team
names by default. Scores24 (the editorial site we use for external
context review) uses Spanish slugs for many internationals and a mix
of EN/ES + accented characters for clubs.

This module produces an ordered list of canonical slug variants for a
team name so the URL resolver can try multiple slugs before giving up
(or before falling back to a search engine).

Design contract
---------------
* Pure (no IO). No DB lookups, no network.
* Ordered output: most-likely-correct first.
* Deterministic — same input → same output.
* Fail-soft: empty/None input → empty list.
* The translation dictionary is a curated whitelist for international
  selections (the cases the user explicitly listed in Phase F63). For
  any team NOT in the dictionary, we still emit the ASCII-folded
  canonical slug + the raw-cased slug.

Test coverage lives in
``tests/test_team_name_translations_smoke.py``.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

ENGINE_VERSION = "team_name_translations.v1"


# ─────────────────────────────────────────────────────────────────────
# Curated EN ↔ ES dictionary
# ─────────────────────────────────────────────────────────────────────
# Each key MUST be the lowercase, ASCII-folded English name as it appears
# on api-sports. Values are ordered lists of slugs Scores24 may use
# (English first when in doubt — accented Spanish variants come last
# because the URL resolver also falls back to ASCII-folded slugs).
_TRANSLATIONS: dict[str, list[str]] = {
    # ─── Americas
    "mexico":          ["mexico", "méxico"],
    "united states":   ["usa", "united-states", "estados-unidos"],
    "usa":             ["usa", "united-states", "estados-unidos"],
    "paraguay":        ["paraguay"],
    "canada":          ["canada", "canadá"],
    "brazil":          ["brazil", "brasil"],
    "argentina":       ["argentina"],
    "chile":           ["chile"],
    "colombia":        ["colombia"],
    "uruguay":         ["uruguay"],
    "venezuela":       ["venezuela"],
    "ecuador":         ["ecuador"],
    "peru":            ["peru", "perú"],
    "bolivia":         ["bolivia"],
    "costa rica":      ["costa-rica"],
    "honduras":        ["honduras"],
    "panama":          ["panama", "panamá"],
    "el salvador":     ["el-salvador"],
    "guatemala":       ["guatemala"],
    "jamaica":         ["jamaica"],
    "haiti":           ["haiti", "haití"],

    # ─── Europe
    "spain":           ["spain", "espana", "españa"],
    "france":          ["france", "francia"],
    "germany":         ["germany", "alemania"],
    "italy":           ["italy", "italia"],
    "portugal":        ["portugal"],
    "netherlands":     ["netherlands", "holanda", "paises-bajos"],
    "holland":         ["netherlands", "holanda", "paises-bajos"],
    "belgium":         ["belgium", "belgica", "bélgica"],
    "england":         ["england", "inglaterra"],
    "scotland":        ["scotland", "escocia"],
    "wales":           ["wales", "gales"],
    "ireland":         ["ireland", "irlanda"],
    "northern ireland":["northern-ireland", "irlanda-del-norte"],
    "switzerland":     ["switzerland", "suiza"],
    "austria":         ["austria"],
    "poland":          ["poland", "polonia"],
    "czech republic":  ["czech-republic", "republica-checa", "república-checa", "chequia"],
    "czechia":         ["czech-republic", "republica-checa", "república-checa", "chequia"],
    "slovakia":        ["slovakia", "eslovaquia"],
    "hungary":         ["hungary", "hungria", "hungría"],
    "romania":         ["romania", "rumania", "rumanía"],
    "bulgaria":        ["bulgaria"],
    "greece":          ["greece", "grecia"],
    "croatia":         ["croatia", "croacia"],
    "serbia":          ["serbia"],
    "bosnia & herzegovina": ["bosnia-herzegovina", "bosnia-y-herzegovina", "bosnia"],
    "bosnia and herzegovina": ["bosnia-herzegovina", "bosnia-y-herzegovina", "bosnia"],
    "slovenia":        ["slovenia", "eslovenia"],
    "montenegro":      ["montenegro"],
    "north macedonia": ["north-macedonia", "macedonia-del-norte"],
    "albania":         ["albania"],
    "kosovo":          ["kosovo"],
    "turkey":          ["turkey", "turquia", "turquía"],
    "ukraine":         ["ukraine", "ucrania"],
    "russia":          ["russia", "rusia"],
    "norway":          ["norway", "noruega"],
    "sweden":          ["sweden", "suecia"],
    "denmark":         ["denmark", "dinamarca"],
    "finland":         ["finland", "finlandia"],
    "iceland":         ["iceland", "islandia"],
    "estonia":         ["estonia"],
    "latvia":          ["latvia", "letonia"],
    "lithuania":       ["lithuania", "lituania"],

    # ─── Africa
    "south africa":    ["south-africa", "sudafrica", "sudáfrica"],
    "morocco":         ["morocco", "marruecos"],
    "algeria":         ["algeria", "argelia"],
    "tunisia":         ["tunisia", "tunez", "túnez"],
    "egypt":           ["egypt", "egipto"],
    "nigeria":         ["nigeria"],
    "ghana":           ["ghana"],
    "senegal":         ["senegal"],
    "ivory coast":     ["ivory-coast", "costa-de-marfil"],
    "côte d'ivoire":   ["ivory-coast", "costa-de-marfil"],
    "cote d'ivoire":   ["ivory-coast", "costa-de-marfil"],
    "cameroon":        ["cameroon", "camerun", "camerún"],
    "kenya":           ["kenya", "kenia"],

    # ─── Asia / Oceania
    "japan":           ["japan", "japon", "japón"],
    "south korea":     ["south-korea", "corea-del-sur"],
    "north korea":     ["north-korea", "corea-del-norte"],
    "korea republic":  ["south-korea", "corea-del-sur"],
    "china":           ["china"],
    "iran":            ["iran", "irán"],
    "iraq":            ["iraq", "irak"],
    "saudi arabia":    ["saudi-arabia", "arabia-saudita", "arabia-saudi"],
    "qatar":           ["qatar", "catar"],
    "uae":             ["uae", "emiratos-arabes-unidos"],
    "united arab emirates": ["uae", "emiratos-arabes-unidos"],
    "australia":       ["australia"],
    "new zealand":     ["new-zealand", "nueva-zelanda"],
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _slug(value: str) -> str:
    """Strict slug: lowercase, ASCII, hyphenated, no leading/trailing hyphens."""
    if not value:
        return ""
    s = _strip_accents(str(value)).lower()
    # & → -and-  /  y    (we emit both variants downstream)
    s = re.sub(r"\s*&\s*", "-and-", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def _slug_accented(value: str) -> str:
    """Soft slug that preserves accented characters (Scores24 sometimes uses them)."""
    if not value:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"\s*&\s*", "-y-", s)
    s = re.sub(r"[^a-z0-9áéíóúñü]+", "-", s, flags=re.IGNORECASE)
    s = s.strip("-")
    return s


def _normalize_lookup_key(value: str) -> str:
    """Canonicalise a name for dictionary lookup (lowercased + accent-folded)."""
    return _strip_accents(str(value or "").strip().lower())


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def normalize_team_name_for_scores24(team_name: Optional[str],
                                     lang: str = "es") -> list[str]:
    """Return an ordered list of slug variants for a single team.

    Variants are ordered most-likely-first:
        1. EN ASCII slug (e.g. "south-africa")
        2. ES ASCII slug (e.g. "sudafrica")
        3. ES accented slug (e.g. "sudáfrica")
        4. EN accented slug (same as 1 for ASCII-only languages)
        5. Curated extras from the translation dictionary (if any).

    Empty / None input → empty list.

    The ``lang`` parameter swaps the order between EN-first / ES-first
    output. Default ``"es"`` matches Scores24's primary language.
    """
    if not team_name or not str(team_name).strip():
        return []

    raw = str(team_name).strip()
    lookup = _normalize_lookup_key(raw)

    en_ascii = _slug(raw)
    es_ascii = _slug(raw)  # fallback if not in dict
    es_accent = _slug_accented(raw)

    extras: list[str] = []
    # Look up the dictionary for known international selections.
    if lookup in _TRANSLATIONS:
        for cand in _TRANSLATIONS[lookup]:
            extras.append(_slug(cand))               # ASCII-folded
            if cand != _slug(cand):
                extras.append(_slug_accented(cand))  # preserve accents

    # Stable ordering: EN-ASCII first, then accented EN, then ES variants.
    if lang.lower().startswith("es"):
        ordered = [en_ascii, *extras, es_ascii, es_accent]
    else:
        ordered = [en_ascii, es_ascii, *extras, es_accent]

    # De-dupe preserving order, drop empties.
    seen: set[str] = set()
    out: list[str] = []
    for s in ordered:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def has_translation(team_name: Optional[str]) -> bool:
    """True iff the curated dictionary has an entry for this team."""
    if not team_name:
        return False
    return _normalize_lookup_key(team_name) in _TRANSLATIONS


def slug_pairs(home: Optional[str], away: Optional[str],
               *, lang: str = "es",
               max_pairs: int = 6) -> list[tuple[str, str]]:
    """Cartesian-product of variant slugs for both sides, capped.

    Returns up to ``max_pairs`` (home_slug, away_slug) tuples in the
    same priority order as :func:`normalize_team_name_for_scores24`.
    The first pair is always (EN-ASCII, EN-ASCII) when possible — the
    same slug Scores24 uses for English club competitions.
    """
    homes = normalize_team_name_for_scores24(home, lang=lang)
    aways = normalize_team_name_for_scores24(away, lang=lang)
    if not homes or not aways:
        return []
    out: list[tuple[str, str]] = []
    # Walk the diagonal first (matching priority on both sides), then
    # backfill with off-diagonal combinations.
    pair_count = max(len(homes), len(aways))
    for i in range(pair_count):
        h = homes[i] if i < len(homes) else homes[-1]
        a = aways[i] if i < len(aways) else aways[-1]
        if (h, a) not in out:
            out.append((h, a))
        if len(out) >= max_pairs:
            return out
    # Mixed EN/ES: pair first EN with first ES (and vice versa).
    if len(homes) > 1 and len(aways) > 1:
        for combo in ((homes[0], aways[1]), (homes[1], aways[0])):
            if combo not in out:
                out.append(combo)
                if len(out) >= max_pairs:
                    return out
    return out[:max_pairs]


__all__ = [
    "ENGINE_VERSION",
    "normalize_team_name_for_scores24",
    "has_translation",
    "slug_pairs",
]
