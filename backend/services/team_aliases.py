"""Sprint-D9 Iteration-3 · Aliases canónicos compartidos para nombres de equipos.

Single source of truth para el mapeo de variantes de nombres de equipos
(ES↔EN, oficiales↔abreviados) a la forma canónica usada por el dataset
histórico (Football-Data.co.uk + Understat).

Usado por:
  * ``football_xg_offline_seed`` (xG por equipo)
  * ``football_corners_offline_seed`` (corners por equipo)
  * cualquier otro módulo de "team history" que necesite resolver
    nombres entre proveedores distintos.

Mantenimiento:
  * Añadir aliases nuevos aquí, NO en los módulos consumidores.
  * Cualquier alias debe estar en lowercase ASCII (sin diacríticos en
    la clave PERO sí en la clave hispana original — el dict acepta
    ambas; ``_norm_team`` aplica NFKD antes del lookup).
  * El valor (RHS) debe ser el nombre canónico tal-como-aparece en el
    dataset histórico (``all_leagues_enriched_dataset.json``).

Convención:
  * Para una nueva selección nacional: añadir entradas en ES y EN.
  * Para un club: añadir el nombre largo oficial + el alias corto.
"""
from __future__ import annotations

import unicodedata


def normalize_team_name(name: str) -> str:
    """Lowercase + strip + NFKD-strip-diacritics.

    Convergencia: ``"Atlético Madrid"`` → ``"atletico madrid"`` →
    (alias map) → ``"ath madrid"``.

    Retorna `""` para inputs falsy o no-string.
    """
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKD", name.strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


# Alias canónicos. Clave = nombre normalizado (NFKD); valor = forma
# canónica del dataset histórico. El lookup intenta el match exacto
# primero, luego cae al alias.
TEAM_ALIASES: dict[str, str] = {
    # ──────────────────────────────────────────────────────────
    # Clubs — EPL
    # ──────────────────────────────────────────────────────────
    "manchester city":       "man city",
    "manchester city fc":    "man city",
    "man city fc":           "man city",
    "manchester united":     "man united",
    "manchester united fc":  "man united",
    "man utd":               "man united",
    "tottenham hotspur":     "tottenham",
    "spurs":                 "tottenham",
    "wolverhampton":         "wolves",
    "wolverhampton wanderers": "wolves",
    "newcastle united":      "newcastle",
    "west ham united":       "west ham",
    "leicester city":        "leicester",
    "leeds united":          "leeds",
    "norwich city":          "norwich",
    "nottingham forest":     "nott'm forest",
    "nottm forest":          "nott'm forest",
    "brighton & hove albion": "brighton",
    "brighton and hove albion": "brighton",

    # ──────────────────────────────────────────────────────────
    # Clubs — LaLiga (Football-Data.co.uk usa abreviaciones)
    # ──────────────────────────────────────────────────────────
    "atletico madrid":       "ath madrid",
    "atletico de madrid":    "ath madrid",
    "athletic bilbao":       "ath bilbao",
    "athletic club":         "ath bilbao",
    "athletic":              "ath bilbao",
    "real betis":            "betis",
    "real sociedad":         "sociedad",
    "celta vigo":            "celta",
    "rcd espanyol":          "espanol",
    "espanyol":              "espanol",
    "rayo vallecano":        "vallecano",

    # ──────────────────────────────────────────────────────────
    # Clubs — SerieA
    # ──────────────────────────────────────────────────────────
    "internazionale":        "inter",
    "internazionale milano": "inter",
    "ac milan":              "milan",
    "as roma":               "roma",
    "ssc napoli":            "napoli",

    # ──────────────────────────────────────────────────────────
    # Clubs — Bundesliga
    # ──────────────────────────────────────────────────────────
    "borussia dortmund":     "dortmund",
    "bvb":                   "dortmund",
    "bayer leverkusen":      "leverkusen",
    "borussia mönchengladbach": "m'gladbach",
    "borussia monchengladbach": "m'gladbach",
    "monchengladbach":       "m'gladbach",
    "rb leipzig":            "rb leipzig",
    "eintracht frankfurt":   "ein frankfurt",
    "vfl wolfsburg":         "wolfsburg",
    "1. fc union berlin":    "union berlin",
    "fc bayern":             "bayern munich",
    "bayern münchen":        "bayern munich",
    "bayern munchen":        "bayern munich",

    # ──────────────────────────────────────────────────────────
    # Selecciones nacionales — UEFA
    # ──────────────────────────────────────────────────────────
    "espana":                "spain",
    "españa":                "spain",
    "seleccion de espana":   "spain",
    "selección de españa":   "spain",
    "spain national team":   "spain",
    "paises bajos":          "netherlands",
    "países bajos":          "netherlands",
    "holanda":               "netherlands",
    "holland":               "netherlands",
    "inglaterra":            "england",
    "francia":               "france",
    "alemania":              "germany",
    "deutschland":           "germany",
    "italia":                "italy",
    "belgica":               "belgium",
    "bélgica":               "belgium",
    "croacia":               "croatia",
    "hrvatska":              "croatia",
    "portugal":              "portugal",
    "suiza":                 "switzerland",
    "polonia":               "poland",
    "polska":                "poland",
    "dinamarca":             "denmark",
    "suecia":                "sweden",
    "sverige":               "sweden",
    "noruega":               "norway",
    "norge":                 "norway",
    "finlandia":             "finland",
    "suomi":                 "finland",
    "republica checa":       "czech republic",
    "república checa":       "czech republic",
    "czechia":               "czech republic",
    "turquia":               "turkey",
    "turquía":               "turkey",
    "türkiye":               "turkey",
    "turkiye":               "turkey",
    "ucrania":               "ukraine",
    "austria":               "austria",
    "hungria":               "hungary",
    "hungría":               "hungary",
    "magyarország":          "hungary",
    "magyarorszag":          "hungary",
    "rumania":               "romania",
    "rumanía":               "romania",
    "románia":               "romania",
    "escocia":               "scotland",
    "albania":               "albania",
    "shqipëria":             "albania",
    "shqiperia":             "albania",
    "grecia":                "greece",
    "serbia":                "serbia",
    "srbija":                "serbia",

    # ──────────────────────────────────────────────────────────
    # Selecciones — CONMEBOL
    # ──────────────────────────────────────────────────────────
    "argentina":             "argentina",
    "seleccion argentina":   "argentina",
    "selección argentina":   "argentina",
    "brasil":                "brazil",
    "uruguay":               "uruguay",
    "colombia":              "colombia",
    "chile":                 "chile",
    "peru":                  "peru",
    "perú":                  "peru",
    "ecuador":               "ecuador",
    "paraguay":              "paraguay",
    "bolivia":               "bolivia",
    "venezuela":             "venezuela",

    # ──────────────────────────────────────────────────────────
    # Selecciones — CONCACAF
    # ──────────────────────────────────────────────────────────
    "mexico":                "mexico",
    "méxico":                "mexico",
    "estados unidos":        "usa",
    "united states":         "usa",
    "us":                    "usa",
    "usmnt":                 "usa",
    "canada":                "canada",
    "canadá":                "canada",
    "costa rica":            "costa rica",
    "panama":                "panama",
    "panamá":                "panama",
    "honduras":              "honduras",
    "curacao":               "curacao",
    "curaçao":               "curacao",
    "jamaica":               "jamaica",

    # ──────────────────────────────────────────────────────────
    # Selecciones — CAF (África)
    # ──────────────────────────────────────────────────────────
    "marruecos":             "morocco",
    "túnez":                 "tunisia",
    "tunez":                 "tunisia",
    "senegal":               "senegal",
    "egipto":                "egypt",
    "argelia":               "algeria",
    "nigeria":               "nigeria",
    "ghana":                 "ghana",
    "camerun":               "cameroon",
    "camerún":               "cameroon",
    "costa de marfil":       "ivory coast",
    "côte d'ivoire":         "ivory coast",
    "cote d'ivoire":         "ivory coast",
    "sudafrica":             "south africa",
    "sudáfrica":             "south africa",

    # ──────────────────────────────────────────────────────────
    # Selecciones — AFC (Asia)
    # ──────────────────────────────────────────────────────────
    "arabia saudita":        "saudi arabia",
    "arabia saudi":          "saudi arabia",
    "japon":                 "japan",
    "japón":                 "japan",
    "corea del sur":         "south korea",
    "korea republic":        "south korea",
    "republic of korea":     "south korea",
    "iran":                  "iran",
    "irán":                  "iran",
    "iraq":                  "iraq",
    "qatar":                 "qatar",
    "catar":                 "qatar",
    "australia":             "australia",
    "nueva zelanda":         "new zealand",
    "china":                 "china",
    "china pr":              "china",
    "india":                 "india",
    "tailandia":             "thailand",
    "vietnam":               "vietnam",

    # ──────────────────────────────────────────────────────────
    # Otros
    # ──────────────────────────────────────────────────────────
    "cabo verde":            "cape verde",
}


def canonicalize_team(name: str) -> str:
    """Resuelve un nombre de equipo a su forma canónica.

    Aplica ``normalize_team_name`` (NFKD strip + lowercase) y luego
    consulta ``TEAM_ALIASES``. Si no hay alias, devuelve el norm tal
    cual (caso "Real Madrid" → "real madrid", no necesita alias).
    """
    raw = normalize_team_name(name)
    if not raw:
        return ""
    return TEAM_ALIASES.get(raw, raw)


__all__ = [
    "TEAM_ALIASES",
    "canonicalize_team",
    "normalize_team_name",
]
